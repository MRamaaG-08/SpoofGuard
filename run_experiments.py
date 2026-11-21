"""
Runs every experiment in the report and writes figures + numbers to results/.

    python run_experiments.py            # full run (~5-8 min on 2 cores)
    python run_experiments.py --quick    # fewer Monte Carlo runs, for testing
    python run_experiments.py --figures-only   # redraw figures from results/summary.json
"""
import json, sys, os
from multiprocessing import Pool
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from spoofguard.sim import Config, scenario, run_mission, WAYPOINTS, HOME

QUICK = "--quick" in sys.argv
N_MC = 10 if QUICK else 50          # Monte Carlo runs per scenario
N_NOM = 40 if QUICK else 200        # extra nominal flights for false-alarm rate
N_SWEEP = 8 if QUICK else 30
OUT = "results"
os.makedirs(OUT, exist_ok=True)

# Reference categorical palette (fixed order), text inks, alarm colour
C_PROT, C_BASE, C_GPS = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
C_ALARM = "#e34948"
plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "lines.linewidth": 2, "legend.frameon": False, "figure.dpi": 150,
    "savefig.bbox": "tight",
})
SCEN = ["nominal", "jamming", "jump", "drift", "stealth"]
LABEL = {"nominal": "Nominal", "jamming": "Jamming", "jump": "Position jump",
         "drift": "Drift carry-off", "stealth": "Stealth carry-off"}
LAYERS = ["innovation", "cusum", "imu_consistency", "baro", "cn0_spoof", "cn0_jam"]
LAYER_LABEL = {"innovation": "Innovation χ² gate", "cusum": "CUSUM on NIS",
               "imu_consistency": "IMU–GPS consistency", "baro": "Baro–GPS altitude",
               "cn0_spoof": "C/N0 spoof signature", "cn0_jam": "C/N0 jam / fix loss"}


def _job(args):
    name, protected, seed, kw, cfg_kw = args
    r = run_mission(scenario(name, **kw), protected, seed=seed,
                    cfg=Config(**cfg_kw), log=False)
    r.pop("dr_error")
    return (name, protected, seed, kw, cfg_kw, r)


def run_jobs(jobs):
    with Pool() as pool:
        return pool.map(_job, jobs, chunksize=4)


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return dict(n=0, mean=None, p95=None, max=None)
    a = np.array(xs, float)
    return dict(n=len(a), mean=float(a.mean()), p95=float(np.percentile(a, 95)),
                max=float(a.max()))


def main():
    summary = {"config": vars(Config()) | {"N_MC": N_MC, "N_NOM": N_NOM, "N_SWEEP": N_SWEEP}}

    # ---------------------------------------------------------- 1. Monte Carlo
    print("Monte Carlo scenarios...")
    jobs = [(n, p, 1000 + s, {}, {}) for n in SCEN for p in (False, True) for s in range(N_MC)]
    res = run_jobs(jobs)
    mc = {}
    for n in SCEN:
        row = {}
        for p in (False, True):
            rs = [r for (nn, pp, _, _, _, r) in res if nn == n and pp == p]
            d = dict(final_home_err=stats([r["final_home_err"] for r in rs]),
                     max_nav_error=stats([r["max_nav_error"] for r in rs]))
            if p:
                d["detection_rate"] = float(np.mean([r["delay"] is not None for r in rs]))
                d["false_alarms"] = int(sum(r["false_alarm"] for r in rs))
                d["delay"] = stats([r["delay"] for r in rs])
                t0 = scenario(n).t0
                d["layers"] = {}
                for L in LAYERS:
                    fl = [r["first_flag"][L] - t0 for r in rs
                          if r["first_flag"][L] is not None and r["first_flag"][L] >= t0]
                    d["layers"][L] = dict(rate=len(fl) / len(rs), delay=stats(fl))
            row["protected" if p else "baseline"] = d
        mc[n] = row
    summary["monte_carlo"] = mc

    # ---------------------------------------------------- 2. False-alarm rate
    print("Nominal flights for false-alarm rate...")
    res = run_jobs([("nominal", True, 5000 + s, {}, {}) for s in range(N_NOM)])
    fa = sum(r["false_alarm"] for *_, r in res)
    hours = sum(r["end_time"] for *_, r in res) / 3600
    summary["false_alarm"] = dict(flights=N_NOM, alarms=int(fa), flight_hours=hours)

    # --------------------------------------------- 3. Stealth sensitivity sweep
    print("Stealth sweep...")
    accs = [0.02, 0.03, 0.05, 0.1, 0.2, 0.5, 1.0]
    res = run_jobs([("stealth", True, 2000 + s, {"stealth_acc": a}, {})
                    for a in accs for s in range(N_SWEEP)])
    sweep = []
    for a in accs:
        rs = [r for (_, _, _, kw, _, r) in res if kw["stealth_acc"] == a]
        dl = [r["delay"] for r in rs if r["delay"] is not None]
        drag = [0.5 * a * d**2 for d in dl]
        sweep.append(dict(acc=a, rate=len(dl) / len(rs), delay=stats(dl), drag=stats(drag),
                          home_err=stats([r["final_home_err"] for r in rs])))
    summary["stealth_sweep"] = sweep

    # ------------------------------------------------ 4. Layer ablation (no C/N0)
    print("Ablation without the C/N0 layer...")
    res = run_jobs([(n, True, 3000 + s, {}, {"use_cn0_layer": False})
                    for n in ("jump", "drift") for s in range(N_SWEEP)])
    summary["ablation_no_cn0"] = {
        n: dict(rate=float(np.mean([r["delay"] is not None for (nn, *_, r) in res if nn == n])),
                delay=stats([r["delay"] for (nn, *_, r) in res if nn == n]))
        for n in ("jump", "drift")}

    with open(f"{OUT}/summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=float)
    print("Figures...")
    figures(summary)
    print("Done. See results/")


# ======================================================================== figures
def _map(ax, name, title, note_offset=(-10, 40)):
    base = run_mission(scenario(name), False, seed=7)
    prot = run_mission(scenario(name), True, seed=7)
    ax.plot(WAYPOINTS[:, 0], WAYPOINTS[:, 1], color=INK2, lw=1, ls="--", label="Planned route")
    g = base["log"]["p_gps"]
    ax.plot(g[:, 0], g[:, 1], ".", ms=1.5, color=C_GPS, label="GPS reported (spoofed)")
    ax.plot(base["log"]["p_true"][:, 0], base["log"]["p_true"][:, 1], color=C_BASE,
            label="True path – no protection")
    ax.plot(prot["log"]["p_true"][:, 0], prot["log"]["p_true"][:, 1], color=C_PROT,
            label="True path – SpoofGuard")
    i = np.searchsorted(prot["log"]["t"], prot["alarm_time"])
    pa = prot["log"]["p_true"][i]
    ax.plot(*pa[:2], marker="X", ms=9, color=C_ALARM, mec="white", mew=1.5, ls="none",
            label="Alarm → GPS isolated")
    ax.plot(*HOME[:2], marker="s", ms=8, color=INK, mec="white", ls="none", label="Home")
    pe = base["log"]["p_true"][-1]
    if note_offset is not None:
      ax.annotate(f"Unprotected drone ends\n{base['final_home_err']:.0f} m from home",
                xy=pe[:2], xytext=note_offset, textcoords="offset points", fontsize=8,
                color=INK2, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
    ax.set_aspect("equal")
    ax.set_xlabel("East (m)"); ax.set_ylabel("North (m)"); ax.set_title(title, loc="left")
    return base, prot


def figures(S):
    # Fig 1-2: maps
    for name, fn, title, off in [
            ("jump", "fig_map_jump.png", "Position-jump spoofing attack at t = 70 s", None),
            ("drift", "fig_map_drift.png", "Drift carry-off attack (2 m/s) at t = 70 s", (-10, 40))]:
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        _map(ax, name, title, off)
        ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
        fig.savefig(f"{OUT}/{fn}"); plt.close(fig)

    # Fig 3: detector timeline, stealth (power-matched) attack
    r = run_mission(scenario("stealth"), True, seed=7)
    L = r["log"]; t = np.array(L["t"])
    fig, axs = plt.subplots(3, 1, figsize=(7.2, 6.2), sharex=True)
    axs[0].plot(t, L["nis"], color=C_PROT, lw=1)
    axs[0].axhline(27.86, color=INK2, lw=1, ls="--")
    axs[0].text(2, 30, "χ² gate (p = 0.9999)", fontsize=8, color=INK2)
    axs[0].set_ylabel("NIS (6-DOF)"); axs[0].set_ylim(0, 45)
    axs[0].set_title("Stealth carry-off (0.1 m/s², power-matched): which check sees it?", loc="left")
    axs[1].plot(t, L["imu_mismatch"], color=C_PROT, lw=1.5)
    axs[1].axhline(Config().imu_thresh, color=INK2, lw=1, ls="--")
    axs[1].text(2, Config().imu_thresh + 0.05, "threshold", fontsize=8, color=INK2)
    axs[1].set_ylabel("IMU–GPS mismatch\n(m/s)")
    axs[2].plot(t, L["cn0_std"], color=C_PROT, lw=1)
    axs[2].set_ylabel("C/N0 spread\n(dB-Hz)"); axs[2].set_xlabel("Time (s)")
    for ax in axs:
        ax.axvline(70, color=INK2, lw=1, ls=":")
        if r["alarm_time"]:
            ax.axvline(r["alarm_time"], color=C_ALARM, lw=1.5)
    axs[0].text(71, 38, "attack starts", fontsize=8, color=INK2)
    axs[1].text(r["alarm_time"] + 1, 0.9 * np.nanmax(L["imu_mismatch"]),
                f"alarm (+{r['delay']:.1f} s)", fontsize=8, color=INK)
    fig.savefig(f"{OUT}/fig_timeline_stealth.png"); plt.close(fig)

    # Fig 4: detector timeline, jump attack (signal + measurement layers)
    r = run_mission(scenario("jump"), True, seed=7)
    rb = run_mission(scenario("jump"), False, seed=7)
    L = r["log"]; t = np.array(L["t"])
    fig, axs = plt.subplots(2, 1, figsize=(7.2, 4.4), sharex=True)
    axs[0].plot(t, L["cn0_mean"], color=C_PROT, label="mean across satellites")
    axs[0].plot(t, L["cn0_std"], color=C_BASE, label="spread across satellites")
    axs[0].set_ylabel("C/N0 (dB-Hz)"); axs[0].legend(fontsize=8, loc="center left")
    axs[0].set_title("Position-jump attack: signal-level signature and navigation error", loc="left")
    eb = np.linalg.norm((rb["log"]["p_true"] - rb["log"]["p_est"])[:, :2], axis=1)
    ep = np.linalg.norm((L["p_true"] - L["p_est"])[:, :2], axis=1)
    axs[1].plot(rb["log"]["t"], eb, color=C_BASE, label="No protection")
    axs[1].plot(t, ep, color=C_PROT, label="SpoofGuard")
    axs[1].set_ylabel("Navigation error (m)"); axs[1].set_xlabel("Time (s)")
    axs[1].legend(fontsize=8, loc="upper left")
    for ax in axs:
        ax.axvline(70, color=INK2, lw=1, ls=":")
    fig.savefig(f"{OUT}/fig_timeline_jump.png"); plt.close(fig)

    # Fig 5: stealth sweep
    sw = S["stealth_sweep"]
    a = np.array([s["acc"] for s in sw])
    fig, axs = plt.subplots(1, 2, figsize=(7.2, 3.0))
    det = [(s["acc"], s["delay"]["mean"]) for s in sw if s["delay"]["mean"] is not None]
    axs[0].plot([d[0] for d in det], [d[1] for d in det], "o-", color=C_PROT, ms=6)
    axs[0].set_xscale("log"); axs[0].set_xlabel("Spoofer acceleration (m/s²)")
    axs[0].set_ylabel("Mean detection delay (s)"); axs[0].set_title("Time to detect", loc="left")
    rate = [s["rate"] * 100 for s in sw]
    axs[1].bar(range(len(a)), rate, color=C_PROT, width=0.6)
    axs[1].set_xticks(range(len(a)), [f"{x:g}" for x in a])
    axs[1].set_xlabel("Spoofer acceleration (m/s²)"); axs[1].set_ylabel("Detected (%)")
    axs[1].set_title("Detection rate", loc="left"); axs[1].set_ylim(0, 105)
    for i, v in enumerate(rate):
        axs[1].text(i, v + 2, f"{v:.0f}", ha="center", fontsize=7, color=INK2)
    fig.tight_layout(); fig.savefig(f"{OUT}/fig_stealth_sweep.png"); plt.close(fig)

    # Fig 6: final distance from home, baseline vs protected
    mc = S["monte_carlo"]
    names = SCEN
    x = np.arange(len(names)); w = 0.36
    fb = [mc[n]["baseline"]["final_home_err"]["mean"] for n in names]
    fp = [mc[n]["protected"]["final_home_err"]["mean"] for n in names]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.bar(x - w / 2 - 0.01, fb, w, color=C_BASE, label="No protection")
    ax.bar(x + w / 2 + 0.01, fp, w, color=C_PROT, label="SpoofGuard")
    ax.set_yscale("log"); ax.set_xticks(x, [LABEL[n] for n in names])
    ax.set_ylabel("Final distance from home (m, log)")
    ax.set_title(f"Where the drone actually ends up (mean of {S['config']['N_MC']} flights)", loc="left")
    for i in range(len(names)):
        ax.text(x[i] - w / 2, fb[i] * 1.12, f"{fb[i]:.0f}", ha="center", fontsize=7, color=INK2)
        ax.text(x[i] + w / 2, fp[i] * 1.12, f"{fp[i]:.1f}", ha="center", fontsize=7, color=INK2)
    ax.legend(fontsize=8, loc="upper left")
    fig.savefig(f"{OUT}/fig_home_error.png"); plt.close(fig)

    # Fig 7: dead-reckoning error growth after GPS isolation (jamming)
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    for s in range(12):
        r = run_mission(scenario("jamming"), True, seed=300 + s, log=False)
        e = np.array(r["dr_error"])
        if len(e):
            ax.plot(e[:, 0], e[:, 1], color=C_PROT, lw=1, alpha=0.6)
    ax.set_xlabel("Time since GPS isolated (s)"); ax.set_ylabel("Position error (m)")
    ax.set_title("Dead-reckoning (IMU + baro) error during return-to-home, 12 flights", loc="left")
    fig.savefig(f"{OUT}/fig_dr_error.png"); plt.close(fig)

    # Fig 0: architecture block diagram
    fig, ax = plt.subplots(figsize=(7.2, 3.4)); ax.axis("off")
    ax.set_xlim(0, 100); ax.set_ylim(0, 50)
    def box(x, y, w, h, txt, fc="#f3f2ef", ec=INK2, bold=False):
        ax.add_patch(plt.Rectangle((x, y), w, h, fc=fc, ec=ec, lw=1))
        ax.text(x + w / 2, y + h / 2, txt, ha="center", va="center", fontsize=7.5,
                fontweight="bold" if bold else "normal")
    def arr(x1, y1, x2, y2):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="->", color=INK2, lw=1))
    box(1, 38, 17, 8, "IMU 50 Hz\n(accelerometer)"); box(1, 26, 17, 8, "Barometer 10 Hz")
    box(1, 14, 17, 8, "GPS 5 Hz\n(position, velocity)"); box(1, 2, 17, 8, "Receiver C/N0\n(per satellite)")
    box(26, 30, 26, 14, "GNSS/INS Kalman filter\n(9 states: position,\nvelocity, accel bias)")
    box(28, 2, 22, 22, "SpoofGuard monitor\n\n1  Innovation χ² gate\n2  CUSUM on NIS\n3  IMU–GPS consistency\n4  Baro–GPS altitude\n5  C/N0 signature",
        fc="#e8f0fb", ec=C_PROT, bold=False)
    box(60, 30, 18, 14, "Guidance &\ncontrol", bold=True)
    box(60, 6, 18, 14, "Fail-safe manager\nisolate GPS → hold\n→ RTL on IMU+baro", fc="#fbeaea", ec=C_ALARM)
    box(86, 30, 13, 14, "Drone\ndynamics")
    arr(18, 42, 28, 39); arr(18, 30, 28, 35); arr(18, 18, 28, 33)
    arr(18, 18, 28, 16); arr(18, 6, 28, 10); arr(18, 30, 28, 20)
    arr(39, 30, 39, 24)
    arr(50, 37, 60, 37); arr(50, 13, 60, 13); arr(69, 20, 69, 30); arr(78, 37, 86, 37)
    ax.text(51, 39, "estimate", fontsize=7, color=INK2)
    ax.text(51, 15, "alarm", fontsize=7, color=C_ALARM)
    ax.text(40, 26.2, "innovations", fontsize=7, color=INK2)
    fig.savefig(f"{OUT}/fig_architecture.png"); plt.close(fig)


if __name__ == "__main__":
    if "--figures-only" in sys.argv:          # redraw from saved numbers
        figures(json.load(open(f"{OUT}/summary.json")))
    else:
        main()
