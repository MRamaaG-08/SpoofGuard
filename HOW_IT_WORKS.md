# How SpoofGuard works — a walkthrough for Ramaa

Read this after exams, with `spoofguard/sim.py` open beside it. Each section names the
function to look at. Questions an evaluator is likely to ask are at the end.

---

## 1. The big picture (one paragraph)

The drone flies a lawnmower survey pattern. Its autopilot never sees the "true" position,
only the **estimate** from a Kalman filter that fuses the IMU, barometer and GPS. Guidance
steers the *estimate* to each waypoint. So if GPS lies and the filter believes it, the
estimate moves, guidance "corrects", and the **real drone** gets dragged away. That is
the whole danger of spoofing, and the simulation reproduces it: look at
`fig_map_jump.png`. The orange line is a drone that thinks it is flying the dashed route.

## 2. The drone and sensors — `run_mission()`

* **True dynamics**: position and velocity integrate the commanded acceleration plus a
  wind gust (a random process that wanders, called Ornstein–Uhlenbeck) and light drag.
* **IMU (50 Hz)**: measures true acceleration + a slowly wandering **bias** + noise.
  The bias is why pure dead reckoning drifts.
* **Barometer (10 Hz)**: altitude + noise. Independent of GPS, so it can expose
  altitude spoofing.
* **GPS (5 Hz)**: position + velocity + noise, **plus whatever the attacker adds**.
* **C/N0**: carrier-to-noise density for each of 10 satellites. Real satellites are at
  different elevations, so their strengths differ (spread ~4–5 dB-Hz). A spoofer with one
  antenna transmits every satellite from one box, so the strengths become high and nearly
  identical. That is the signature we detect.

## 3. Attacks — `class Attack`, `scenario()`

| Name | What the attacker does | Why it matters |
|---|---|---|
| `jamming` | Blocks GPS for 60 s | Simplest attack; drone loses fix |
| `jump` | Instantly shifts GPS by (60, −40, 12) m | Crude spoofer; easy to see |
| `drift` | Moves the fake position at 2 m/s, with matching fake velocity | Classic "carry-off" |
| `stealth` | Accelerates the fake position gently (0.1 m/s²) **and** copies a realistic C/N0 pattern | Sophisticated attacker: signal check is blind |

## 4. The navigation filter — `class GnssInsFilter`

State `x = [position(3), velocity(3), accelerometer bias(3)]`.

* `predict()`: integrate IMU acceleration minus estimated bias (this is the INS part).
* `gps_innovation()`: innovation `y = z − Hx` = "what GPS says minus what I expected".
  Its covariance is `S = HPHᵀ + R`. **NIS** = `yᵀ S⁻¹ y` is a single number saying how
  surprising the measurement is. If everything is honest, NIS follows a χ² distribution
  with 6 degrees of freedom (mean 6).
* `_update()`: standard Kalman update in Joseph form, which keeps P symmetric and
  positive even with rounding errors.

## 5. The detector — `class IntegrityMonitor` (+ the IMU window in `run_mission`)

1. **Innovation χ² gate**: NIS above the 99.99% χ²(6) point (27.9) is "impossible".
   Also used for *gating*: such a measurement is never fed into the filter.
2. **CUSUM on NIS**: adds up how much NIS keeps exceeding its normal level.
   Catches a moderate bias that is never big enough for the gate on any single epoch.
3. **IMU–GPS consistency**: over a 10 s window, compare the velocity change GPS reports
   with the velocity change the accelerometers measured. Physics says they must match.
   A slow spoofer can fool the filter epoch-by-epoch (the filter just follows it), but it
   cannot fake the accelerometer. **This layer is what catches the stealth attack.**
4. **Baro–GPS altitude**: smoothed difference above 8 m means GPS altitude is wrong.
5. **C/N0 signature**: during the first 30 s (arming period) the monitor learns this sky's
   normal mean and spread. Spoof = spread collapses below 40% of normal *and* mean rises
   > 3 dB-Hz. Jam = mean drops > 10 dB-Hz or fix lost.

Each per-epoch check must be true in **3 of the last 5 epochs** (persistence) before it
counts. One noisy epoch can't trigger an alarm.

## 6. Fail-safe — mode machine in `run_mission`

`MISSION → (alarm) → HOLD 5 s → RTL → DONE`. GPS is isolated permanently (latched).
RTL flies home on IMU + baro only. `fig_dr_error.png` shows how that error grows.

## 7. Experiments — `run_experiments.py`

* 50 Monte Carlo flights per scenario, with and without SpoofGuard.
* 200 extra honest flights to measure the false-alarm rate.
* A sweep of stealth attack strength (0.02 → 1 m/s²) to find the detection floor.
* An ablation: turn off the C/N0 layer and check the other layers still catch jump/drift.

## 8. Questions you might get (and the honest answers)

* **Why not just use the χ² gate?** Slow attacks never exceed it. The filter follows the
  lie. See the top panel of `fig_timeline_stealth.png`: NIS stays normal until after the
  physics layer has already raised the alarm.
* **What can't it catch?** Very gentle attacks (0.02 m/s² was never detected, 0.03 m/s² only
  40% of the time; from 0.05 m/s² upward, 100%). That
  is the same size as an accelerometer bias, so over 10 s it is indistinguishable from
  sensor error. Fixes: longer windows, better IMU, multi-antenna angle-of-arrival checks,
  or cryptographic signal authentication (Galileo OSNMA).
* **Is attitude really known?** We assume the autopilot's AHRS provides it, to keep the
  filter linear. A full 15/16-state error-state EKF (like ArduPilot EKF3 / PX4 EKF2) is
  the natural upgrade.
* **Is C/N0 real?** It's modelled. Real receivers (e.g. u-blox M8/M9/F9) report per-satellite
  C/N0 and their own spoofing flags, so this layer can be tested on hardware.

## 9. How to make it yours (later)

1. Port the filter + monitor to MATLAB/Simulink and run it inside the UAV Toolbox
   (your strongest tool).
2. Replay a real ArduPilot/PX4 flight log (`.bin`/`.ulg`) through the monitor and
   inject attacks offline. No drone needed.
3. Add the full attitude EKF and a second spoofer direction (vertical).
4. Put it on GitHub under your own account, with your own commits.
