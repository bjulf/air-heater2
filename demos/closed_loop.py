"""Closed loop PI control of the simulated air heater, run offline.

Uses the vectorised batch simulator, so 400 s of process time finishes
instantly. Tune Kp and Ti here and produce the plots for the report; use
demo_realtime.py when you want the loop paced by the clock.

The setpoint steps part way through, and measurement noise plus a lowpass
filter are enabled, so the plot shows the controller on something closer to
the real rig than a clean step.

Requires numpy and matplotlib.
"""

# Make the airheater package importable when this script is run directly,
# from any working directory, without installing it first.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt
import numpy as np

from airheater import AirHeaterParams
from airheater.analysis import margins
from airheater.simulation.batch import simulate_closed_loop

Ts = 0.1          # sampling time [s]
T_STOP = 400.0    # simulation length [s]

Kp = 0.52         # Skogestad tuning for this process
Ti = 18.0         # [s]
Tf = 2.0          # measurement filter time constant [s]

T_INIT = 20.0     # initial outlet temperature [degC]
NOISE_STD = 0.1   # measurement noise [degC]; set 0 for a clean run

params = AirHeaterParams()


def setpoint_profile(n: int) -> np.ndarray:
    """28 degC, stepping to 34 degC halfway through."""
    r = np.full(n, 28.0)
    r[n // 2:] = 34.0
    return r


def main() -> None:
    n = int(round(T_STOP / Ts))
    r = setpoint_profile(n)

    res = simulate_closed_loop(
        r, Ts, T_STOP,
        Kp=Kp, Ti=Ti, Tf=Tf,
        T_init=T_INIT, noise_std=NOISE_STD, seed=1,
        params=params,
    )

    m = margins(Kp, Ti, params=params)

    # Steady state error over the last 20 s of each setpoint plateau.
    def settled_error(at: float) -> float:
        window = (res.t >= at - 20.0) & (res.t < at)
        return float(np.mean(res.setpoint[window] - res.filtered[window]))

    print("Kp = {0}, Ti = {1} s, Ts = {2} s, Tf = {3} s".format(Kp, Ti, Ts, Tf))
    print("  GM = {0:.2f}, PM = {1:.1f} deg, wc = {2:.3f} rad/s".format(
        m.gm, m.pm, m.wc))
    print("  mean error before setpoint step : {0:+.3f} degC".format(
        settled_error(T_STOP / 2)))
    print("  mean error at end of run        : {0:+.3f} degC".format(
        settled_error(T_STOP)))
    print("  control signal range            : {0:.2f} - {1:.2f} V".format(
        res.u.min(), res.u.max()))
    print("  IAE                             : {0:.1f} degC*s".format(float(res.iae())))
    print("  samples at the actuator limit   : {0}".format(
        int(np.sum(res.u >= params.u_max - 1e-9))))

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(9, 7),
                                   num="Air heater - closed loop")

    ax1.plot(res.t, res.measured, color="0.8", lw=0.8, label="measured")
    ax1.plot(res.t, res.filtered, color="tab:blue", label="filtered")
    ax1.plot(res.t, res.setpoint, color="tab:red", ls="--", label="setpoint")
    ax1.set_ylabel("T_out [degC]")
    ax1.set_title("PI control (Kp = {0}, Ti = {1} s, GM = {2:.1f}, PM = {3:.0f} deg)".format(
        Kp, Ti, m.gm, m.pm))
    ax1.grid(True, alpha=0.4)
    ax1.legend(loc="lower right")

    ax2.plot(res.t, res.u, color="tab:green")
    ax2.axhline(params.u_max, color="grey", ls=":", lw=1)
    ax2.set_xlabel("t [s]")
    ax2.set_ylabel("u [V]")
    ax2.set_ylim(params.u_min - 0.2, params.u_max + 0.3)
    ax2.grid(True, alpha=0.4)

    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
