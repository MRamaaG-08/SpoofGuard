"""
SpoofGuard - simulation core.

A single quadcopter flies a survey mission using a GNSS/INS Kalman filter for
navigation. GPS can be jammed or spoofed. A three-layer integrity monitor
decides when GPS is lying, isolates it, and triggers a dead-reckoning
return-to-home.

Frame: local ENU (x = East, y = North, z = Up), metres, seconds.
Simplification: attitude is assumed known from the autopilot's AHRS, so the
IMU acceleration is already rotated into the ENU frame. This keeps the filter
linear (9 states) and lets us focus on GPS integrity.
"""
from dataclasses import dataclass, field
import numpy as np
from scipy.stats import chi2

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class Config:
    dt: float = 0.02                 # IMU / control rate 50 Hz
    gps_every: int = 10              # GPS at 5 Hz
    baro_every: int = 5              # Baro at 10 Hz
    t_max: float = 320.0
    cruise_speed: float = 6.0        # m/s
    alt: float = 30.0                # survey altitude (m)
    # IMU (consumer MEMS)
    acc_noise: float = 0.08          # m/s^2 per sample
    acc_bias0: float = 0.03          # initial bias std (m/s^2)
    acc_bias_rw: float = 5e-4        # bias random walk (m/s^2/sqrt(s))
    wind_gust: float = 0.35          # gust acceleration std (m/s^2)
    # GPS
    gps_pos_sigma: tuple = (1.2, 1.2, 2.5)
    gps_vel_sigma: float = 0.08
    n_sats: int = 10
    # Baro
    baro_sigma: float = 0.4
    # Detector
    nis_gate_p: float = 0.9999       # per-epoch chi-square gate probability
    cusum_drift: float = 3.0         # CUSUM allowance (above mean of 6)
    cusum_h: float = 60.0            # CUSUM alarm threshold
    baro_thresh: float = 8.0         # |GPS alt - baro alt| (m), smoothed
    cn0_std_ratio: float = 0.4       # spoof: spread collapses below 40% of baseline...
    cn0_rise: float = 3.0            # ...and mean C/N0 rises by > 3 dB-Hz
    cn0_jam_drop: float = 10.0       # jam: mean C/N0 drops by > 10 dB-Hz
    persist_k: int = 3               # k-of-n persistence for epoch flags
    persist_n: int = 5
    arm_time: float = 30.0           # filter convergence time before the IMU layer arms
    imu_window: float = 10.0         # IMU-vs-GPS consistency window (s)
    imu_thresh: float = 0.35         # horizontal velocity-change mismatch (m/s)
    use_cn0_layer: bool = True       # can be disabled for ablation
    hold_time: float = 5.0           # seconds of hover before RTL


WAYPOINTS = np.array([
    [0, 0, 30], [0, 200, 30], [50, 200, 30], [50, 0, 30],
    [100, 0, 30], [100, 200, 30], [150, 200, 30], [150, 0, 30],
    [0, 0, 30],
], dtype=float)
HOME = np.array([0.0, 0.0, 30.0])


# --------------------------------------------------------------------------
# Attack models
# --------------------------------------------------------------------------
@dataclass
class Attack:
    """kind: none | jam | jump | drift | stealth"""
    kind: str = "none"
    t0: float = 70.0
    duration: float = 1e9
    offset: tuple = (60.0, -40.0, 12.0)   # jump: instant position offset
    drift_vel: tuple = (2.0, 0.0, 0.0)    # drift: spoofed velocity ramp (m/s)
    stealth_acc: float = 0.1              # stealth: spoof acceleration (m/s^2)
    stealth_dir: tuple = (1.0, 0.0, 0.0)
    power_matched: bool = False           # True = spoofer mimics C/N0 pattern


    def active(self, t):
        return self.kind != "none" and self.t0 <= t < self.t0 + self.duration

    def gps_corruption(self, t):
        """Return (position offset, velocity offset, fix_available)."""
        zero = np.zeros(3)
        if not self.active(t):
            return zero, zero, True
        tau = t - self.t0
        if self.kind == "jam":
            return zero, zero, False
        if self.kind == "jump":
            return np.array(self.offset), zero, True
        if self.kind == "drift":
            v = np.array(self.drift_vel)
            return v * tau, v, True
        if self.kind == "stealth":
            d = np.array(self.stealth_dir) / np.linalg.norm(self.stealth_dir)
            a = self.stealth_acc
            return 0.5 * a * tau**2 * d, a * tau * d, True
        raise ValueError(self.kind)


def scenario(name, **kw):
    """The five named test scenarios used throughout the report."""
    presets = {
        "nominal": dict(kind="none"),
        "jamming": dict(kind="jam", duration=60.0),
        "jump":    dict(kind="jump"),                    # meaconing-style position jump
        "drift":   dict(kind="drift"),                   # velocity-consistent carry-off
        "stealth": dict(kind="stealth", power_matched=True),  # slow, power-matched carry-off
    }
    d = dict(presets[name])
    d.update(kw)
    return Attack(**d)


# --------------------------------------------------------------------------
# Kalman filter: state x = [p(3), v(3), accel_bias(3)]
# --------------------------------------------------------------------------
class GnssInsFilter:
    def __init__(self, cfg: Config, p0):
        self.cfg = cfg
        dt = cfg.dt
        self.x = np.zeros(9)
        self.x[0:3] = p0
        self.P = np.diag([2, 2, 2, 0.3, 0.3, 0.3, 0.05, 0.05, 0.05]) ** 2
        I3 = np.eye(3)
        F = np.eye(9)
        F[0:3, 3:6] = I3 * dt
        F[0:3, 6:9] = -0.5 * I3 * dt**2
        F[3:6, 6:9] = -I3 * dt
        self.F = F
        qa = (2.0 * cfg.acc_noise * dt) ** 2          # inflated for unmodelled effects
        qb = cfg.acc_bias_rw**2 * dt
        self.Q = np.diag([1e-6] * 3 + [qa] * 3 + [qb] * 3)
        sp = np.array(cfg.gps_pos_sigma) * 1.25
        self.R_gps = np.diag(list(sp**2) + [(cfg.gps_vel_sigma * 1.5) ** 2] * 3)
        self.H_gps = np.zeros((6, 9))
        self.H_gps[:, 0:6] = np.eye(6)
        self.H_baro = np.zeros((1, 9))
        self.H_baro[0, 2] = 1.0
        self.R_baro = np.array([[(cfg.baro_sigma * 1.5) ** 2]])

    def predict(self, acc_meas):
        dt = self.cfg.dt
        a = acc_meas - self.x[6:9]
        self.x[0:3] += self.x[3:6] * dt + 0.5 * a * dt**2
        self.x[3:6] += a * dt
        self.P = self.F @ self.P @ self.F.T + self.Q

    def gps_innovation(self, z):
        y = z - self.H_gps @ self.x
        S = self.H_gps @ self.P @ self.H_gps.T + self.R_gps
        nis = float(y @ np.linalg.solve(S, y))
        return y, S, nis

    def _update(self, y, S, H):
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        I_KH = np.eye(9) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ (S - H @ self.P @ H.T) @ K.T  # Joseph form
        self.P = 0.5 * (self.P + self.P.T)

    def update_gps(self, y, S):
        self._update(y, S, self.H_gps)

    def update_baro(self, z_alt):
        y = np.array([z_alt - self.x[2]])
        S = self.H_baro @ self.P @ self.H_baro.T + self.R_baro
        self._update(y, S, self.H_baro)


# --------------------------------------------------------------------------
# Integrity monitor (the "SpoofGuard" detector)
# --------------------------------------------------------------------------
class IntegrityMonitor:
    LAYERS = ("innovation", "cusum", "imu_consistency", "baro", "cn0_spoof", "cn0_jam")

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.gate = chi2.ppf(cfg.nis_gate_p, 6)
        self.cusum = 0.0
        self.baro_diff = 0.0
        self.history = {k: [] for k in ("innovation", "imu_consistency", "baro",
                                         "cn0_spoof", "cn0_jam")}
        self.imu_mismatch = 0.0
        self.cn0_base = []
        self.first_flag = {k: None for k in self.LAYERS}
        self.alarm = False
        self.alarm_time = None
        self.alarm_reason = None

    def _persist(self, key, flag):
        h = self.history[key]
        h.append(bool(flag))
        if len(h) > self.cfg.persist_n:
            h.pop(0)
        return sum(h) >= self.cfg.persist_k

    def step(self, t, nis, gps_alt, baro_alt, cn0, fix, imu_mismatch=None):
        """Called once per GPS epoch. Returns dict of layer flags."""
        c = self.cfg
        flags = {k: False for k in self.LAYERS}
        if c.use_cn0_layer and cn0 is not None:
            mean, std = float(np.mean(cn0)), float(np.std(cn0))
            if t < c.arm_time and fix:
                # learn this sky's normal C/N0 level and spread before arming
                self.cn0_base.append((mean, std))
                flags["cn0_jam"] = self._persist("cn0_jam", not fix)
            else:
                bm, bs = np.mean(self.cn0_base, axis=0) if self.cn0_base else (40.0, 5.0)
                jam = (not fix) or mean < bm - c.cn0_jam_drop
                spoof = fix and std < c.cn0_std_ratio * bs and mean > bm + c.cn0_rise
                flags["cn0_jam"] = self._persist("cn0_jam", jam)
                flags["cn0_spoof"] = self._persist("cn0_spoof", spoof)
        elif not fix:
            flags["cn0_jam"] = self._persist("cn0_jam", True)   # loss of fix always visible
        if fix:
            flags["innovation"] = self._persist("innovation", nis > self.gate)
            self.cusum = max(0.0, self.cusum + (min(nis, 200.0) - 6.0) - c.cusum_drift)
            flags["cusum"] = self.cusum > c.cusum_h
            alpha = c.dt * c.gps_every / 2.0                      # ~2 s smoothing
            self.baro_diff += alpha * ((gps_alt - baro_alt) - self.baro_diff)
            flags["baro"] = self._persist("baro", abs(self.baro_diff) > c.baro_thresh)
            if imu_mismatch is not None:
                self.imu_mismatch = imu_mismatch
                flags["imu_consistency"] = self._persist("imu_consistency",
                                                         imu_mismatch > c.imu_thresh)
        for k, f in flags.items():
            if f and self.first_flag[k] is None:
                self.first_flag[k] = t
        if not self.alarm and any(flags.values()):
            self.alarm = True
            self.alarm_time = t
            self.alarm_reason = [k for k, f in flags.items() if f]
        return flags


# --------------------------------------------------------------------------
# Signal-level model: per-satellite carrier-to-noise density (C/N0)
# --------------------------------------------------------------------------
def make_sky(rng, n):
    return rng.uniform(10, 85, n)     # satellite elevations (deg)


def cn0_sample(rng, elev, attack: Attack, t):
    authentic = 30 + 18 * np.sin(np.radians(elev)) + rng.normal(0, 1.5, len(elev))
    if not attack.active(t):
        return authentic
    if attack.kind == "jam":
        return authentic - 25 + rng.normal(0, 1.0, len(elev))
    if attack.power_matched:
        return authentic                  # sophisticated spoofer mimics geometry
    # Single-antenna spoofer: every satellite arrives from one transmitter,
    # so C/N0 is high and nearly identical across satellites.
    return 47 + rng.normal(0, 0.6, len(elev))


# --------------------------------------------------------------------------
# Closed-loop mission
# --------------------------------------------------------------------------
def guidance(p_est, v_est, target, speed):
    d = target - p_est
    dist = np.linalg.norm(d)
    v_des = np.zeros(3) if dist < 1e-6 else d / dist * min(speed, 0.6 * dist)
    a_cmd = 1.6 * (v_des - v_est)
    n = np.linalg.norm(a_cmd)
    if n > 4.0:
        a_cmd *= 4.0 / n
    return a_cmd, dist


def run_mission(attack: Attack, protected: bool, seed: int = 0, cfg: Config = None,
                log: bool = True):
    cfg = cfg or Config()
    rng = np.random.default_rng(seed)
    dt = cfg.dt
    n_steps = int(cfg.t_max / dt)

    p = np.array([0.0, 0.0, 30.0])        # start hovering at survey altitude over home
    v = np.zeros(3)
    bias = rng.normal(0, cfg.acc_bias0, 3)
    gust = np.zeros(3)
    elev = make_sky(rng, cfg.n_sats)

    kf = GnssInsFilter(cfg, p + rng.normal(0, 1.0, 3))
    mon = IntegrityMonitor(cfg)

    wp_idx = 1
    mode = "MISSION"           # MISSION -> HOLD -> RTL -> DONE
    hold_start = None
    gps_ok = True              # GPS used by the filter

    L = {k: [] for k in ("t", "p_true", "p_est", "p_gps", "nis", "cusum",
                          "cn0_std", "cn0_mean", "baro_diff", "mode")} if log else None
    est_err_after_alarm = []
    acc_int = np.zeros(3)      # running integral of raw IMU acceleration
    win = []                   # (t, v_gps, acc_int, bias_est) per GPS epoch
    mis_vec = np.zeros(2)      # smoothed horizontal mismatch vector
    max_dev = 0.0

    for k in range(n_steps):
        t = k * dt
        # ---------------- guidance & control (uses the ESTIMATE) -------------
        p_est, v_est = kf.x[0:3], kf.x[3:6]
        # mode transitions first, then guidance toward the current target
        if mode == "MISSION" and np.linalg.norm(WAYPOINTS[wp_idx] - p_est) < 3.0:
            wp_idx += 1
            if wp_idx >= len(WAYPOINTS):
                mode = "DONE"
        if mode == "HOLD" and t - hold_start >= cfg.hold_time:
            mode = "RTL"
        if mode == "RTL" and np.linalg.norm(HOME - p_est) < 2.0:
            mode = "DONE"
        if mode == "DONE":
            break
        target = {"MISSION": WAYPOINTS[wp_idx] if mode == "MISSION" else None,
                  "HOLD": p_hold if mode == "HOLD" else None,
                  "RTL": HOME}[mode]
        a_cmd, dist = guidance(p_est, v_est, target, cfg.cruise_speed)

        # ---------------- true dynamics ------------------------------------
        gust += -gust * dt / 2.0 + rng.normal(0, cfg.wind_gust * np.sqrt(dt))
        a_true = a_cmd + gust - 0.05 * v           # light drag
        p = p + v * dt + 0.5 * a_true * dt**2
        v = v + a_true * dt
        bias += rng.normal(0, cfg.acc_bias_rw * np.sqrt(dt), 3)

        # ---------------- IMU + prediction ---------------------------------
        acc_meas = a_true + bias + rng.normal(0, cfg.acc_noise, 3)
        kf.predict(acc_meas)
        acc_int += acc_meas * dt

        # ---------------- barometer ----------------------------------------
        baro_alt = p[2] + rng.normal(0, cfg.baro_sigma)
        if k % cfg.baro_every == 0:
            kf.update_baro(baro_alt)

        # ---------------- GPS epoch ----------------------------------------
        if k % cfg.gps_every == 0:
            dp, dv, fix = attack.gps_corruption(t)
            cn0 = cn0_sample(rng, elev, attack, t)
            z = None
            nis = np.nan
            if fix:
                z = np.concatenate([
                    p + dp + rng.normal(0, 1, 3) * np.array(cfg.gps_pos_sigma),
                    v + dv + rng.normal(0, cfg.gps_vel_sigma, 3)])
                y, S, nis = kf.gps_innovation(z)
            # Layer: IMU-vs-GPS consistency over a long window. The velocity change
            # GPS reports must match what the accelerometers measured. Slow
            # carry-off attacks hide inside per-epoch noise but not here.
            imu_mismatch = None
            if fix:
                win.append((t, z[3:6].copy(), acc_int.copy(), kf.x[6:9].copy()))
                while len(win) > 1 and win[1][0] <= t - cfg.imu_window:
                    win.pop(0)
                t_a, v_a, I_a, b_a = win[0]
                if t - t_a >= cfg.imu_window - 1e-9:
                    W = t - t_a
                    dv_imu = (acc_int - I_a) - b_a * W
                    dv_gps = z[3:6] - v_a
                    alpha = cfg.dt * cfg.gps_every / 2.0          # ~2 s smoothing
                    mis_vec += alpha * ((dv_gps - dv_imu)[0:2] - mis_vec)
                    if t >= cfg.arm_time:
                        imu_mismatch = float(np.linalg.norm(mis_vec))
            else:
                win.clear()
            if protected:
                mon.step(t, nis, z[2] if fix else np.nan, baro_alt, cn0, fix, imu_mismatch)
                if mon.alarm and gps_ok:
                    gps_ok = False                 # isolate GPS (latched)
                    if mode == "MISSION":
                        mode, hold_start, p_hold = "HOLD", t, kf.x[0:3].copy()
                if fix and gps_ok and nis <= mon.gate:   # innovation gating
                    kf.update_gps(y, S)
            else:
                if fix:
                    kf.update_gps(y, S)            # baseline: trusts GPS blindly
            if log:
                L["t"].append(t)
                L["p_true"].append(p.copy())
                L["p_est"].append(kf.x[0:3].copy())
                L["p_gps"].append(z[0:3].copy() if fix else np.full(3, np.nan))
                L["nis"].append(nis)
                L["cusum"].append(mon.cusum)
                L["cn0_std"].append(float(np.std(cn0)))
                L["cn0_mean"].append(float(np.mean(cn0)))
                L["baro_diff"].append(mon.baro_diff)
                L.setdefault("imu_mismatch", []).append(np.nan if imu_mismatch is None
                                                         else imu_mismatch)
                L["mode"].append(mode)

        # ---------------- metrics ------------------------------------------
        if attack.active(t) or (attack.kind != "none" and t >= attack.t0):
            # how far the true drone is from where the autopilot thinks it is
            max_dev = max(max_dev, float(np.linalg.norm((p - kf.x[0:3])[0:2])))
        if protected and mon.alarm:
            est_err_after_alarm.append((t - mon.alarm_time,
                                        float(np.linalg.norm((p - kf.x[0:3])[0:2]))))

    final_home_err = float(np.linalg.norm((p - HOME)[0:2]))
    out = dict(
        detected=mon.alarm if protected else None,
        alarm_time=mon.alarm_time,
        delay=(mon.alarm_time - attack.t0) if (mon.alarm and attack.kind != "none"
                                               and mon.alarm_time >= attack.t0) else None,
        false_alarm=bool(protected and mon.alarm and
                         (attack.kind == "none" or mon.alarm_time < attack.t0)),
        reason=mon.alarm_reason,
        first_flag=dict(mon.first_flag),
        max_nav_error=max_dev,
        final_home_err=final_home_err,
        end_mode=mode,
        end_time=t,
        dr_error=est_err_after_alarm,
    )
    if log:
        for key in ("p_true", "p_est", "p_gps"):
            L[key] = np.array(L[key])
        out["log"] = L
    return out
