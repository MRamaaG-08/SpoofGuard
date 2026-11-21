"""
Builds SpoofGuard_Demo.mp4 (~1.5 min, 1280x720) from the simulation.

    python make_video.py        # needs ffmpeg on PATH

Scenes: title -> the problem (jump attack) -> the stealth attack and the IMU check
-> results -> how it works -> close.
"""
import os, subprocess, shutil
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter
from spoofguard.sim import Config, scenario, run_mission, WAYPOINTS, HOME

FPS = 15
W, H, DPI = 12.8, 7.2, 100
TMP = "video_tmp"
os.makedirs(TMP, exist_ok=True)
C_PROT, C_BASE, C_GPS = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, BG = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
C_ALARM = "#e34948"
plt.rcParams.update({"font.size": 13, "axes.edgecolor": INK2, "text.color": INK,
                     "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": BG, "axes.facecolor": BG})
segments = []


def caption(fig, text):
    fig.text(0.5, 0.035, text, ha="center", va="center", fontsize=15, color="white",
             bbox=dict(boxstyle="round,pad=0.5", fc="#1f1f1e", ec="none", alpha=0.92))


def still(name, seconds, draw):
    fig = plt.figure(figsize=(W, H), dpi=DPI)
    draw(fig)
    png = f"{TMP}/{name}.png"
    fig.savefig(png, dpi=DPI, facecolor=BG); plt.close(fig)
    mp4 = f"{TMP}/{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", png, "-t", str(seconds),
                    "-r", str(FPS), "-vf", "scale=1280:720,format=yuv420p", "-c:v", "libx264",
                    "-preset", "veryfast", mp4], check=True)
    segments.append(mp4)


def title_card(title, sub, foot=""):
    def d(fig):
        fig.text(0.5, 0.58, title, ha="center", fontsize=44, fontweight="bold")
        fig.text(0.5, 0.46, sub, ha="center", fontsize=20, color=INK2)
        fig.text(0.5, 0.25, foot, ha="center", fontsize=14, color=INK2)
    return d


def image_card(path, cap, head):
    def d(fig):
        fig.text(0.05, 0.92, head, fontsize=22, fontweight="bold")
        ax = fig.add_axes([0.08, 0.1, 0.84, 0.78]); ax.axis("off")
        ax.imshow(plt.imread(path))
        caption(fig, cap)
    return d


# --------------------------------------------------------------- animated map scene
def map_scene(name, scen, captions, speed=8.0, with_mismatch=False):
    base = run_mission(scenario(scen), False, seed=7)
    prot = run_mission(scenario(scen), True, seed=7)
    Lb, Lp = base["log"], prot["log"]
    tb, tp = np.array(Lb["t"]), np.array(Lp["t"])
    t_end = max(tb[-1], tp[-1])
    n_frames = int(t_end / speed * FPS) + FPS * 2

    fig = plt.figure(figsize=(W, H), dpi=DPI)
    if with_mismatch:
        ax = fig.add_axes([0.06, 0.34, 0.56, 0.56])
        ax2 = fig.add_axes([0.69, 0.50, 0.28, 0.34])
    else:
        ax = fig.add_axes([0.05, 0.17, 0.55, 0.76])
        ax2 = None
    pts = np.vstack([Lb["p_true"][:, :2], Lp["p_true"][:, :2], WAYPOINTS[:, :2]])
    g = Lb["p_gps"][:, :2]; g = g[~np.isnan(g[:, 0])]
    pts = np.vstack([pts, g])
    lo, hi = pts.min(0) - 15, pts.max(0) + 15
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1])
    if not with_mismatch:
        ax.set_aspect("equal")
    ax.grid(color=GRID, lw=0.6); ax.set_xlabel("East (m)"); ax.set_ylabel("North (m)")
    ax.plot(WAYPOINTS[:, 0], WAYPOINTS[:, 1], "--", color=INK2, lw=1, label="Planned route")
    ax.plot(*HOME[:2], "s", color=INK, ms=10, label="Home")
    gps_dots, = ax.plot([], [], ".", color=C_GPS, ms=3, label="GPS says (spoofed)")
    lb, = ax.plot([], [], color=C_BASE, lw=2.5, label="Real drone, no protection")
    lp, = ax.plot([], [], color=C_PROT, lw=2.5, label="Real drone, SpoofGuard")
    db, = ax.plot([], [], "o", color=C_BASE, ms=10, mec="white", mew=1.5)
    dp, = ax.plot([], [], "o", color=C_PROT, ms=10, mec="white", mew=1.5)
    alarm_mk, = ax.plot([], [], "X", color=C_ALARM, ms=16, mec="white", mew=1.5)
    if with_mismatch:
        ax.legend(loc="upper left", bbox_to_anchor=(0.0, -0.16), ncol=3, fontsize=11, frameon=False)
    else:
        ax.legend(loc="upper left", bbox_to_anchor=(1.03, 0.62), fontsize=12, frameon=False)
    clock = fig.text(0.63 if not with_mismatch else 0.69, 0.88 if not with_mismatch else 0.93,
                     "", fontsize=16, fontweight="bold")
    status = fig.text(0.63 if not with_mismatch else 0.69, 0.80 if not with_mismatch else 0.30,
                      "", fontsize=15)
    if ax2 is not None:
        mm = np.array(Lp["imu_mismatch"], float)
        ax2.set_xlim(0, t_end); ax2.set_ylim(0, max(1.1, np.nanmax(mm) * 1.1))
        ax2.axhline(Config().imu_thresh, color=INK2, ls="--", lw=1)
        ax2.text(2, Config().imu_thresh + 0.04, "alarm threshold", fontsize=11, color=INK2)
        ax2.set_title("IMU vs GPS mismatch (m/s)", loc="left", fontsize=13)
        ax2.set_xlabel("time (s)"); ax2.grid(color=GRID, lw=0.6)
        lm, = ax2.plot([], [], color=C_PROT, lw=2)
    cap_box = fig.text(0.5, 0.035, "", ha="center", va="center", fontsize=15, color="white",
                       bbox=dict(boxstyle="round,pad=0.5", fc="#1f1f1e", ec="none", alpha=0.92))
    t0 = scenario(scen).t0

    def upd(f):
        t = min(f / FPS * speed, t_end)
        ib = np.searchsorted(tb, t, side="right"); ip = np.searchsorted(tp, t, side="right")
        lb.set_data(Lb["p_true"][:ib, 0], Lb["p_true"][:ib, 1])
        lp.set_data(Lp["p_true"][:ip, 0], Lp["p_true"][:ip, 1])
        if ib: db.set_data([Lb["p_true"][ib - 1, 0]], [Lb["p_true"][ib - 1, 1]])
        if ip: dp.set_data([Lp["p_true"][ip - 1, 0]], [Lp["p_true"][ip - 1, 1]])
        gs = Lb["p_gps"][:ib]; mask = np.array(tb[:ib]) >= t0
        gps_dots.set_data(gs[mask, 0], gs[mask, 1])
        clock.set_text(f"t = {t:5.1f} s")
        if t < t0:
            status.set_text("Status: normal flight"); status.set_color(INK)
        elif prot["alarm_time"] is None or t < prot["alarm_time"]:
            status.set_text("Status: ATTACK in progress"); status.set_color(C_BASE)
        else:
            mode = Lp["mode"][max(ip - 1, 0)]
            status.set_text(f"SpoofGuard ALARM at {prot['alarm_time']:.1f} s\n"
                            f"GPS isolated → {'HOLD' if mode == 'HOLD' else 'returning home'}"
                            + (" (done)" if t >= tp[-1] else ""))
            status.set_color(C_ALARM)
            i = np.searchsorted(tp, prot["alarm_time"])
            alarm_mk.set_data([Lp["p_true"][i, 0]], [Lp["p_true"][i, 1]])
        for (ts, txt) in captions:
            if t >= ts: cap_box.set_text(txt)
        if ax2 is not None:
            lm.set_data(tp[:ip], np.array(Lp["imu_mismatch"], float)[:ip])
        return []

    out = f"{TMP}/{name}.mp4"
    FuncAnimation(fig, upd, frames=n_frames).save(
        out, writer=FFMpegWriter(fps=FPS, codec="libx264",
                                 extra_args=["-preset", "veryfast", "-pix_fmt", "yuv420p"]), dpi=DPI)
    plt.close(fig)
    segments.append(out)
    return base, prot


# ------------------------------------------------------------------------ build
if __name__ == "__main__":
    still("00_title", 5, title_card(
        "SpoofGuard", "Detecting GPS spoofing & jamming on drones, and getting home safely",
        "M Ramaa Gayatri · ECE, New Horizon College of Engineering, Bengaluru\n"
        "BSERC Def-Space Autumn Internship 2026 · Drone Technology"))
    still("01_problem", 7, title_card(
        "The problem", "A drone's autopilot trusts GPS. A spoofer can fake GPS signals,\n"
        "and the drone follows the lie while believing it is on course.",
        "SpoofGuard uses sensors every drone already has: IMU, barometer, GPS receiver"))
    map_scene("02_jump", "jump", [
        (0, "Survey mission. Orange and blue are two identical drones; only blue runs SpoofGuard."),
        (70, "t = 70 s: attacker shifts GPS by about 70 m. Orange believes the fake GPS..."),
        (95, "...and flies a shifted copy of the route. Blue caught it on the first fake reading, went home."),
    ])
    map_scene("03_stealth", "stealth", [
        (0, "Harder attack: the spoofer accelerates slowly and copies real signal strength."),
        (70, "Signal-strength and standard filter checks see nothing. But the accelerometer disagrees."),
        (80, "Physics check: GPS velocity change ≠ what the IMU felt. Alarm in ~6 s; drone returns home."),
        (120, "Meanwhile the unprotected drone keeps getting dragged further away (~800 m by the end)."),
    ], speed=8.0, with_mismatch=True)
    still("04_results", 9, image_card(
        "results/fig_home_error.png",
        "50 simulated flights per attack: 100% detected, 0 false alarms in 250 honest flights",
        "Results: where the drone ends up"))
    still("05_sweep", 7, image_card(
        "results/fig_stealth_sweep.png",
        "Weaker spoofers take longer; below ~0.03 m/s² they hide inside normal sensor drift",
        "How gentle can an attacker be?"))
    still("06_arch", 8, image_card(
        "results/fig_architecture.png",
        "Five checks in three families: signal, statistics, physics. Any one → isolate GPS → fly home",
        "How it works"))
    still("07_end", 6, title_card(
        "SpoofGuard", "Next: real ArduPilot/PX4 log replay · u-blox C/N0 on hardware · Simulink HIL",
        "Simulation results. Code, report and figures are in the GitHub repository."))

    with open(f"{TMP}/list.txt", "w") as f:
        for s in segments:
            f.write(f"file '{os.path.basename(s)}'\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                    "list.txt", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-r", str(FPS), "../SpoofGuard_Demo.mp4"],
                   check=True, cwd=TMP)
    shutil.rmtree(TMP)
    print("wrote SpoofGuard_Demo.mp4")
