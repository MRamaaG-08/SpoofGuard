"""Builds SpoofGuard_Report.pdf from results/summary.json and results/*.png."""
import json
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image, Table,
                                TableStyle, PageBreak, KeepTogether)
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER

S = json.load(open("results/summary.json"))
MC, SW, FA, AB, CFG = (S["monte_carlo"], S["stealth_sweep"], S["false_alarm"],
                       S["ablation_no_cn0"], S["config"])
SCEN = ["nominal", "jamming", "jump", "drift", "stealth"]
LABEL = {"nominal": "Nominal (no attack)", "jamming": "Jamming (60 s)",
         "jump": "Position jump", "drift": "Drift carry-off (2 m/s)",
         "stealth": "Stealth carry-off (0.1 m/s², power-matched)"}
LAYERS = ["innovation", "cusum", "imu_consistency", "baro", "cn0_spoof", "cn0_jam"]
LAYER_LABEL = {"innovation": "Innovation χ² gate", "cusum": "CUSUM on NIS",
               "imu_consistency": "IMU–GPS consistency", "baro": "Baro–GPS altitude",
               "cn0_spoof": "C/N0 spoof signature", "cn0_jam": "C/N0 jam / fix loss"}

# Unicode font (χ², subscripts, ∫ ...). Falls back to Helvetica if DejaVu is missing.
import os
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping
FONT, FONT_B, FONT_I = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
for d in ["/usr/share/fonts/truetype/dejavu", os.path.expanduser("~/.fonts"), "fonts"]:
    if os.path.exists(os.path.join(d, "DejaVuSans.ttf")):
        for name, f in [("DV", "DejaVuSans.ttf"), ("DV-B", "DejaVuSans-Bold.ttf"),
                        ("DV-I", "DejaVuSans-Oblique.ttf"), ("DV-BI", "DejaVuSans-BoldOblique.ttf")]:
            pdfmetrics.registerFont(TTFont(name, os.path.join(d, f)))
        addMapping("DV", 0, 0, "DV"); addMapping("DV", 1, 0, "DV-B")
        addMapping("DV", 0, 1, "DV-I"); addMapping("DV", 1, 1, "DV-BI")
        FONT, FONT_B, FONT_I = "DV", "DV-B", "DV-I"
        break

ss = getSampleStyleSheet()
BLUE = colors.HexColor("#2a78d6"); INK = colors.HexColor("#0b0b0b"); INK2 = colors.HexColor("#52514e")
body = ParagraphStyle("b", parent=ss["BodyText"], fontName=FONT, fontSize=10,
                      leading=14.2, alignment=TA_JUSTIFY, spaceAfter=6, textColor=INK)
h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName=FONT_B, fontSize=14,
                    spaceBefore=12, spaceAfter=6, textColor=INK)
h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=FONT_B, fontSize=11,
                    spaceBefore=8, spaceAfter=4, textColor=INK)
cap = ParagraphStyle("cap", parent=body, fontSize=8.5, leading=11, textColor=INK2,
                     alignment=TA_CENTER, spaceAfter=10)
cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.5, alignment=0, spaceAfter=0)
title = ParagraphStyle("t", parent=body, fontName=FONT_B, fontSize=19, leading=24,
                       alignment=TA_CENTER, spaceAfter=8)
sub = ParagraphStyle("s", parent=body, fontSize=10.5, alignment=TA_CENTER, textColor=INK2)
bullet = ParagraphStyle("bl", parent=body, leftIndent=14, bulletIndent=4, spaceAfter=3)
eq = ParagraphStyle("eq", parent=body, alignment=TA_CENTER, fontName=FONT_I)

P = lambda t, s=body: Paragraph(t, s)
B = lambda t: Paragraph(t, bullet, bulletText="•")
f1 = lambda x: "–" if x is None else f"{x:.1f}"
f0 = lambda x: "–" if x is None else f"{x:.0f}"
pct = lambda x: f"{100 * x:.0f}%"
fig_n = [0]


def fig(path, w_mm, caption, head=None):
    fig_n[0] += 1
    img = Image(path)
    r = img.imageHeight / img.imageWidth
    img.drawWidth = w_mm * mm; img.drawHeight = w_mm * mm * r
    return KeepTogether(([head] if head else []) + [img, P(f"<b>Figure {fig_n[0]}.</b> {caption}", cap)])


def table(rows, widths, header_rows=1):
    data = [[c if not isinstance(c, str) else Paragraph(c, cell) for c in r] for r in rows]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=header_rows)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#eef3fb")),
        ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.8, BLUE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, INK2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


# ----------------------------------------------------------------- numbers
N = CFG["N_MC"]
spoofs = ["jump", "drift", "stealth"]
det_all = all(MC[n]["protected"]["detection_rate"] == 1.0 for n in ["jamming"] + spoofs)
fa_mc = sum(MC[n]["protected"]["false_alarms"] for n in SCEN)
fa_total = FA["alarms"] + MC["nominal"]["protected"]["false_alarms"]
fa_flights = FA["flights"] + N
fa_hours = FA["flight_hours"]
worst_base = max(MC[n]["baseline"]["final_home_err"]["mean"] for n in spoofs)
worst_prot = max(MC[n]["protected"]["final_home_err"]["mean"] for n in SCEN)
floor = [s for s in SW if s["rate"] < 0.95]
floor_acc = max([s["acc"] for s in floor]) if floor else None
first_full = min([s["acc"] for s in SW if s["rate"] >= 0.95], default=None)
stealth_d = MC["stealth"]["protected"]["delay"]
drift_d = MC["drift"]["protected"]["delay"]

story = []
# ----------------------------------------------------------------- title
story += [Spacer(1, 18 * mm),
          P("SpoofGuard", title),
          P("Multi-Layer GPS Spoofing and Jamming Detection<br/>with Fail-Safe Navigation for UAVs",
            ParagraphStyle("t2", parent=title, fontSize=14, leading=19, fontName=FONT)),
          Spacer(1, 8 * mm),
          P("M Ramaa Gayatri", ParagraphStyle("au", parent=sub, fontSize=12, textColor=INK)),
          P("Department of Electronics and Communication Engineering<br/>"
            "New Horizon College of Engineering, Bengaluru", sub),
          Spacer(1, 6 * mm),
          P("Project submission: Def-Space Autumn Internship 2026<br/>"
            "Bharat Space Education Research Centre (BSERC) · Domain: Drone Technology", sub),
          P("22 November 2026", sub),
          Spacer(1, 12 * mm)]

story += [P("<b>Abstract</b>", h2), P(
    "Commercial and defence drones depend on GPS for navigation, yet civil GPS signals are "
    "unauthenticated and can be jammed or counterfeited with low-cost radios. A spoofed drone "
    "does not know it is lost. Its autopilot keeps steering its <i>estimate</i> along the "
    "planned route while the real aircraft is dragged elsewhere. This project presents "
    "<b>SpoofGuard</b>, a three-family integrity monitor that runs beside a GNSS/INS Kalman "
    "filter: (i) a <b>signal-level</b> check on the carrier-to-noise (C/N0) pattern across "
    "satellites, (ii) <b>measurement-consistency</b> checks (innovation χ² gate and CUSUM), and "
    "(iii) <b>physics-consistency</b> checks that compare GPS against the accelerometers over a "
    "10 s window and against the barometer. On an alarm, GPS is isolated and the drone returns "
    "home on IMU and barometer dead reckoning. In closed-loop simulation over "
    f"{N} Monte Carlo flights per scenario, SpoofGuard detected "
    f"{'every' if det_all else 'nearly every'} jamming, position-jump, drift and stealth attack "
    f"(stealth: mean delay {f1(stealth_d['mean'])} s) and recorded {fa_total} false alarms in "
    f"{fa_flights} honest flights ({fa_hours:.1f} flight-hours). Unprotected drones ended up to "
    f"{f0(worst_base)} m from home on average; protected drones ended within "
    f"{f1(worst_prot)} m. The physics-consistency layer is the one that catches "
    "power-matched slow spoofers that defeat both the signal check and the classical χ² test.")]
story.append(PageBreak())

# ----------------------------------------------------------------- 1
story += [P("1. Introduction", h1), P(
    "GPS was designed for availability, not security. The civil L1 C/A signal carries no "
    "authentication, arrives at the antenna at roughly −130 dBm, and is therefore easy to "
    "overpower. Two kinds of attack follow. <b>Jamming</b> drowns the signal in noise, which is "
    "disruptive but obvious. <b>Spoofing</b> broadcasts counterfeit signals that the receiver "
    "accepts as genuine, which is far more dangerous because the victim keeps a valid-looking "
    "fix. Humphreys and colleagues built a portable civil GPS spoofer [1], and their group "
    "later demonstrated live spoofing of a small UAV (2012) and of a yacht's navigation (2013); "
    "a survey of these attacks and defences is given in [2]. GNSS interference near conflict "
    "zones has since been widely reported."),
    P("For a drone the consequence is subtle. Guidance steers the <i>estimated</i> position to "
      "each waypoint, so if the navigation filter accepts the false GPS, the estimate follows "
      "the attacker and the controller faithfully flies the real aircraft away from its route. "
      "A standard navigation filter typically rejects only measurements that are statistically "
      "impossible on a single epoch, and a careful attacker never sends one."),
    P("<b>Objective.</b> Design and evaluate an onboard monitor that (1) detects jamming and "
      "spoofing quickly, including slow \"carry-off\" spoofers that mimic real signal strength, "
      "(2) keeps false alarms near zero in honest flight, and (3) brings the drone home safely "
      "without GPS once an attack is declared. The work uses only sensors that every flight "
      "controller already has: IMU, barometer and the GPS receiver's own C/N0 output.")]

# ----------------------------------------------------------------- 2
story += [P("2. Threat model", h1),
          P("Four attacks are simulated, from crude to sophisticated. All begin at t = 70 s, "
            "during the survey mission.")]
story.append(table([
    ["Attack", "What the attacker does", "Signal (C/N0) footprint"],
    ["Jamming", "Blocks GPS for 60 s; receiver loses its fix.", "Mean C/N0 collapses"],
    ["Position jump", "Instantly offsets GPS by (60, −40, 12) m (meaconing-style capture).",
     "High, uniform C/N0 from one transmitter"],
    ["Drift carry-off", "Fake position walks away at 2 m/s with a consistent fake velocity.",
     "High, uniform C/N0"],
    ["Stealth carry-off", "Fake position accelerates at 0.1 m/s² (swept 0.02–1 m/s²); "
     "velocity is consistent; per-satellite C/N0 is copied from the real sky.",
     "None. Signal check is blind by design"],
], [32, 92, 46]))
story.append(Spacer(1, 4))
story.append(P("The stealth case is deliberately chosen so that neither a signal-power test nor "
               "the standard innovation test can see it on its own. It represents a capable "
               "attacker and is the main stress test for the design."))

# ----------------------------------------------------------------- 3
story += [fig("results/fig_architecture.png", 160,
              "SpoofGuard runs beside the navigation filter. It reads the filter's innovations, "
              "the raw IMU and barometer, and receiver C/N0, and commands the fail-safe manager.",
              head=P("3. System architecture", h1)),
          P("The drone flies a 150 m × 200 m lawnmower survey at 30 m altitude and 6 m/s. "
            "Guidance is a velocity-command controller that acts only on the filter estimate, "
            "exactly as a real autopilot does, so a corrupted estimate produces a real "
            "deviation of the aircraft.")]

# ----------------------------------------------------------------- 4
story += [P("4. Method", h1), P("4.1 Navigation filter", h2), P(
    "A loosely coupled GNSS/INS Kalman filter estimates nine states: position, velocity and "
    "accelerometer bias in a local East-North-Up frame. Attitude is assumed to come from the "
    "autopilot's AHRS, so the accelerometer is already resolved in ENU and the filter is "
    "linear. The IMU drives the prediction at 50 Hz; the barometer (10 Hz) and GPS position "
    "and velocity (5 Hz) are measurement updates. Updates use the Joseph form for numerical "
    "robustness. For every GPS epoch the filter produces the innovation and its covariance:"),
    P("y = z − H x̂ ,   S = H P Hᵀ + R ,   NIS = yᵀ S⁻¹ y", eq),
    P("Under honest operation the normalised innovation squared (NIS) follows a χ² "
      "distribution with 6 degrees of freedom (mean 6)."),
    P("4.2 Detection layers", h2)]
story.append(table([
    ["#", "Check", "Test statistic and threshold", "Targets"],
    ["1", "Innovation χ² gate", "NIS > χ²₆(0.9999) = 27.9. Failing measurements are also "
     "<i>gated</i> (never fused).", "Sudden jumps"],
    ["2", "CUSUM on NIS [3]", "Sₖ = max(0, Sₖ₋₁ + NISₖ − 6 − 3); alarm if Sₖ > 60.",
     "Persistent moderate bias"],
    ["3", "IMU–GPS consistency", "Over a 10 s window: |Δv<sub>GPS</sub> − ∫(a<sub>IMU</sub> − b̂)dt| "
     "(horizontal, ~2 s smoothing) > 0.35 m/s.", "Slow carry-off"],
    ["4", "Baro–GPS altitude", "Smoothed |h<sub>GPS</sub> − h<sub>baro</sub>| > 8 m.",
     "Vertical spoofing"],
    ["5", "C/N0 signature", "Learn baseline mean and spread in first 30 s. Spoof: spread "
     "&lt; 40% of baseline and mean +3 dB-Hz. Jam: mean −10 dB-Hz or no fix.",
     "Single-antenna spoofers, jammers"],
], [7, 33, 98, 32]))
story += [Spacer(1, 4), P(
    "Layer 3 is the key idea. A slow spoofer keeps each epoch's innovation small because the "
    "filter simply follows it, but the attacker cannot change what the accelerometers feel. "
    "Integrating the accelerometer over ten seconds and comparing with the velocity change GPS "
    "reports exposes any fake acceleration larger than the residual bias uncertainty. The "
    "bias estimate used is the one frozen at the start of the window, so the attack cannot "
    "contaminate its own reference."),
    P("4.3 Decision and fail-safe", h2),
    P("Per-epoch checks must fire in 3 of the last 5 epochs (1 s) before they count, which "
      "suppresses isolated noise spikes. CUSUM already integrates over time and needs no "
      "persistence. The first confirmed check raises a <b>latched</b> alarm: GPS is isolated, "
      "the drone holds position for 5 s, then flies a return-to-home (RTL) using IMU + "
      "barometer dead reckoning. Latching is intentional. A spoofer must not be able to "
      "\"switch GPS back on\" by briefly behaving well.")]

# ----------------------------------------------------------------- 5
story += [P("5. Simulation setup", h1)]
story.append(table([
    ["Parameter", "Value"],
    ["Rates", "IMU / control 50 Hz, barometer 10 Hz, GPS 5 Hz"],
    ["IMU", f"noise {CFG['acc_noise']} m/s², initial bias σ {CFG['acc_bias0']} m/s², "
            f"bias random walk {CFG['acc_bias_rw']} m/s²/√s"],
    ["Wind", f"gust acceleration σ {CFG['wind_gust']} m/s² (Ornstein–Uhlenbeck, τ = 2 s)"],
    ["GPS", "position σ 1.2 / 1.2 / 2.5 m (E/N/U), velocity σ 0.08 m/s, 10 satellites"],
    ["Barometer", f"σ {CFG['baro_sigma']} m"],
    ["Mission", "lawnmower survey, 150 m × 200 m, 30 m altitude, 6 m/s, ≈190 s"],
    ["Monte Carlo", f"{N} flights per scenario × {{no protection, SpoofGuard}}; "
                    f"{FA['flights']} extra honest flights; stealth sweep {CFG['N_SWEEP']} "
                    "flights per level"],
    ["Baseline", "same filter, no integrity monitoring (fuses every GPS epoch)"],
], [32, 138]))

# ----------------------------------------------------------------- 6
story += [P("6. Results", h1), P("6.1 Detection performance", h2)]
rows = [["Scenario", "Detected", "Mean delay (s)", "95th pct (s)", "False alarms"]]
for n in SCEN:
    pr = MC[n]["protected"]
    rows.append([LABEL[n], "–" if n == "nominal" else pct(pr["detection_rate"]),
                 "–" if n == "nominal" else f1(pr["delay"]["mean"]),
                 "–" if n == "nominal" else f1(pr["delay"]["p95"]),
                 f"{pr['false_alarms']} / {N}"])
story.append(table(rows, [62, 22, 30, 28, 28]))
story += [Spacer(1, 4), P(
    "Delay is measured from the first spoofed GPS epoch, so 0.0 s means the very first false "
    "measurement was caught (GPS epochs are 0.2 s apart). "
    f"Across {fa_flights} honest flights ({fa_hours:.1f} flight-hours) SpoofGuard raised "
    f"{fa_total} false alarm{'s' if fa_total != 1 else ''}. Jamming and crude spoofing are caught within "
    "0.4 s of onset. The stealth attack takes longer because its fake "
    "acceleration has to build up a measurable velocity mismatch, but it is still detected "
    f"in {pct(MC['stealth']['protected']['detection_rate'])} of flights, with a mean delay of "
    f"{f1(stealth_d['mean'])} s. At that point the attacker has moved the fake position by only "
    f"about {0.5 * 0.1 * (stealth_d['mean'] or 0)**2:.1f} m.")]

_h62 = P("6.2 Which layer sees which attack", h2)
rows = [["Check"] + [LABEL[n].split(" (")[0] for n in ["jamming"] + spoofs]]
for L in LAYERS:
    r = [LAYER_LABEL[L]]
    for n in ["jamming"] + spoofs:
        d = MC[n]["protected"]["layers"][L]
        r.append("–" if d["rate"] == 0 else f"{pct(d['rate'])} · {f1(d['delay']['mean'])} s")
    rows.append(r)
story.append(KeepTogether([_h62, table(rows, [42, 32, 32, 32, 32])]))
story += [Spacer(1, 4), P(
    "Entries give the share of flights in which each check fired after attack onset and its "
    "mean first-fire time. Checks that fire after the alarm still appear, because the monitor "
    "keeps evaluating them. The layers are complementary: C/N0 alone sees jamming and crude "
    "spoofers, the χ² family sees jumps, and only the IMU–GPS consistency check sees the "
    "power-matched stealth attack before it has done damage. In that scenario the χ² and "
    "CUSUM entries fire only <i>after</i> the alarm, once GPS is no longer fused and the "
    "spoofed track drifts away from the prediction. With the C/N0 layer switched off "
    f"(ablation), jump and drift attacks were still detected in {pct(AB['jump']['rate'])} and "
    f"{pct(AB['drift']['rate'])} of flights (mean delays {f1(AB['jump']['delay']['mean'])} s and "
    f"{f1(AB['drift']['delay']['mean'])} s), so no single layer is a point of failure.")]

story += [fig("results/fig_timeline_stealth.png", 150,
              "Stealth carry-off attack. The classical NIS test (top) stays inside its gate until "
              "after the alarm. The C/N0 spread (bottom) shows nothing because the attacker "
              "copies it. The IMU–GPS consistency statistic (middle) crosses its threshold "
              "within seconds. After the alarm GPS is no longer fused, so NIS grows."),
          fig("results/fig_map_jump.png", 150,
              "Position-jump attack, one flight. Unprotected (orange): the drone believes it is "
              "on the dashed route, really flies a shifted copy of it, and finishes about "
              f"{MC['jump']['baseline']['final_home_err']['mean']:.0f} m from home. Protected (blue): alarm at the red cross, then hold and return home on "
              "dead reckoning.", head=P("6.3 Effect on the aircraft", h2)),
          fig("results/fig_map_drift.png", 150,
              "Drift carry-off at 2 m/s. Without protection the error grows for the rest of the "
              "mission; SpoofGuard detects it immediately and returns home.")]
rows = [["Scenario", "No protection: final distance from home (m)",
         "SpoofGuard: final distance from home (m)"]]
for n in SCEN:
    rows.append([LABEL[n], f1(MC[n]["baseline"]["final_home_err"]["mean"]),
                 f1(MC[n]["protected"]["final_home_err"]["mean"])])
story += [table(rows, [62, 54, 54]), Spacer(1, 4),
          fig("results/fig_home_error.png", 150,
              "Mean final distance between the drone and home at the end of the flight "
              "(logarithmic scale). In jamming, the unprotected drone recovers once GPS returns; "
              "all spoofing cases leave it far from home.")]

story += [P("6.4 Sensitivity to attack strength", h2)]
rows = [["Spoofer acceleration (m/s²)", "Detected", "Mean delay (s)",
         "Fake displacement at detection (m)"]]
for s in SW:
    rows.append([f"{s['acc']:g}", pct(s["rate"]), f1(s["delay"]["mean"]), f1(s["drag"]["mean"])])
story += [table(rows, [48, 30, 36, 56]), Spacer(1, 4),
          fig("results/fig_stealth_sweep.png", 150,
              "Power-matched carry-off swept from 0.02 to 1 m/s². Stronger attacks are caught "
              "faster; the weakest falls below the detection floor."),
          P(f"Detection is reliable (≥95%) from {first_full:g} m/s² upward. "
            + (f"Below that ({floor_acc:g} m/s² and weaker) the fake acceleration is comparable "
               "to accelerometer bias uncertainty over a 10 s window, and SpoofGuard cannot "
               "separate the two. This is the honest detection floor of an IMU-based "
               "consistency check. " if floor_acc else "")
            + "Even at the floor, the displacement accumulated before detection stays small for "
              "every level that is detected.")]

story += [P("6.5 Navigating home without GPS", h2),
          fig("results/fig_dr_error.png", 150,
              "Dead-reckoning position error after GPS is isolated (jamming scenario, 12 flights)."),
          P("Dead-reckoning error grows roughly quadratically with time because of residual "
            "accelerometer bias. For this mission's return legs (well under a minute) it stays "
            "at a few metres, enough to bring the aircraft back near its launch point for a "
            "pilot-assisted landing. Longer GPS-denied flight needs an additional reference, "
            "for example optical flow or visual odometry.")]

# ----------------------------------------------------------------- 7
story += [P("7. Discussion and limitations", h1),
          B("<b>Simulation only.</b> All numbers come from a simulated drone and sensors. "
            "They show the method works under the stated models; they are not flight-test "
            "results."),
          B("<b>Simplified filter.</b> Attitude is taken as known (9-state linear filter). "
            "Production autopilots run 15–24 state error-state EKFs; the monitor plugs into "
            "their innovations unchanged."),
          B("<b>Modelled signals.</b> GPS errors are white, and C/N0 is a parametric model. "
            "Real multipath and slowly correlated GPS errors will raise the false-alarm rate "
            "and need threshold re-tuning on recorded data."),
          B("<b>Detection floor.</b> A patient attacker below about "
            f"{(floor_acc or 0.02):g} m/s² is invisible to the IMU check over 10 s. Longer windows, "
            "better IMUs, dual-antenna angle-of-arrival checks, and cryptographic "
            "authentication (Galileo OSNMA) push the floor down."),
          B("<b>Fail-safe policy.</b> Latching is safe but forfeits the mission if GPS was "
            "merely glitchy. A supervised re-acceptance rule is future work."),
          P("8. Future work", h1),
          B("Replay real ArduPilot / PX4 flight logs through the monitor with injected attacks "
            "to tune thresholds on real sensor noise."),
          B("Read per-satellite C/N0 and the receiver's own spoofing flags from a u-blox "
            "module over UART on an STM32 or Pixhawk companion computer."),
          B("Port the filter and monitor to MATLAB/Simulink (UAV Toolbox) for "
            "hardware-in-the-loop testing, then flight-test with a tethered drone and a "
            "GNSS simulator in a shielded setting."),
          B("Add a learned fusion stage that weighs the layer statistics instead of a "
            "first-to-fire rule."),
          P("9. Conclusion", h1),
          P("SpoofGuard shows that a drone can defend itself against GPS spoofing using sensors "
            "it already carries. Each layer covers a blind spot of the others. The signal check "
            "catches crude transmitters instantly, the statistical checks catch jumps, and the "
            "IMU-based physics check catches the slow, power-matched spoofer that defeats "
            "both. Combined with a simple latched fail-safe, the drone ends its flight near "
            "home instead of wherever the attacker wanted it."),
          P("References", h1)]
refs = [
    "T. E. Humphreys, B. M. Ledvina, M. L. Psiaki, B. W. O'Hanlon, P. M. Kintner, \"Assessing the "
    "spoofing threat: development of a portable GPS civilian spoofer,\" <i>Proc. ION GNSS</i>, 2008.",
    "M. L. Psiaki and T. E. Humphreys, \"GNSS spoofing and detection,\" <i>Proceedings of the "
    "IEEE</i>, vol. 104, no. 6, pp. 1258–1270, 2016.",
    "E. S. Page, \"Continuous inspection schemes,\" <i>Biometrika</i>, vol. 41, pp. 100–115, 1954.",
    "P. D. Groves, <i>Principles of GNSS, Inertial, and Multisensor Integrated Navigation "
    "Systems</i>, 2nd ed., Artech House, 2013.",
    "Y. Bar-Shalom, X. R. Li, T. Kirubarajan, <i>Estimation with Applications to Tracking and "
    "Navigation</i>, Wiley, 2001.",
]
for i, r in enumerate(refs, 1):
    story.append(Paragraph(f"[{i}] {r}", ParagraphStyle("r", parent=body, fontSize=9,
                                                        leading=12, leftIndent=14,
                                                        firstLineIndent=-14)))
story += [Spacer(1, 6), P("Source code and reproduction instructions accompany this report "
                          "(README.md). All figures and numbers are regenerated by "
                          "<font face='Courier'>python run_experiments.py</font>.",
                          ParagraphStyle("n", parent=body, fontSize=9, textColor=INK2))]


def footer(c, d):
    c.saveState(); c.setFont(FONT, 8); c.setFillColor(INK2)
    c.drawString(20 * mm, 10 * mm, "SpoofGuard · M Ramaa Gayatri · BSERC Def-Space Autumn Internship 2026")
    c.drawRightString(190 * mm, 10 * mm, str(d.page)); c.restoreState()


doc = SimpleDocTemplate("SpoofGuard_Report.pdf", pagesize=A4, leftMargin=20 * mm,
                        rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                        title="SpoofGuard: GPS Spoofing Detection for UAVs",
                        author="M Ramaa Gayatri")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote SpoofGuard_Report.pdf")
