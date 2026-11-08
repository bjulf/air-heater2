"""Open loop step response of the air heater simulator.

Run this first: it shows the process on its own, with no controller, and is how
you verify the model before you tune anything against it. The step response
also gives the three numbers used for tuning - gain, time constant and delay -
and the script marks each of them on the plot.

The second figure compares the two discretisations at a coarse sampling time,
which is the case where the choice actually matters.

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
from airheater.simulation.batch import simulate_open_loop

Ts = 0.1          # sampling time [s]
T_STOP = 200.0    # simulation length [s]
U_STEP = 1.0      # control signal applied at t = 0 [V]

# The step starts from the steady state the process sits at with u = 0, i.e.
# ambient. Starting anywhere else mixes the step response with a settling
# transient, and the 63 % reading no longer gives the time constant.
params = AirHeaterParams()
T_START = params.T_env


def plot_step() -> None:
    res = simulate_open_loop(U_STEP, Ts, duration=T_STOP, params=params)

    T_final = params.steady_state(U_STEP)
    span = T_final - T_START
    T_63 = T_START + 0.632 * span
    t_63 = params.theta_d + params.theta_t

    print("Step u = {0} V applied at t = 0, from steady state".format(U_STEP))
    print("  steady state : {0:.2f} degC  (K_h*u + T_env)".format(T_final))
    print("  reached      : {0:.2f} degC after {1:.0f} s".format(res.T_out[-1], T_STOP))
    print("  63 % level   : {0:.2f} degC at t = theta_d + theta_t = {1:.0f} s".format(
        T_63, t_63))
    print("  delay        : {0:.0f} s".format(params.theta_d))
    print()
    print("Reading the three parameters back off this curve is exactly how you")
    print("identify the real rig: gain from the final value, delay from where")
    print("the curve leaves the baseline, time constant from the 63 % point.")

    fig, ax = plt.subplots(figsize=(9, 5.5), num="Open loop step response")
    ax.plot(res.t, res.T_out, lw=2, label="T_out")

    ax.axhline(T_final, color="grey", ls="--", lw=1)
    ax.axhline(T_63, color="grey", ls=":", lw=1)
    ax.axvline(params.theta_d, color="tab:orange", ls=":", lw=1)
    ax.axvline(t_63, color="tab:green", ls=":", lw=1)

    ax.annotate("K_h*u = {0:.1f} degC".format(params.K_h * U_STEP),
                xy=(T_STOP * 0.75, T_final), xytext=(T_STOP * 0.55, T_final - 0.9),
                arrowprops=dict(arrowstyle="->", color="grey"))
    ax.annotate("theta_d = {0:.0f} s".format(params.theta_d),
                xy=(params.theta_d, T_START + 0.1 * span),
                xytext=(params.theta_d + 14, T_START + 0.25 * span),
                arrowprops=dict(arrowstyle="->", color="tab:orange"), color="tab:orange")
    ax.annotate("63 % at theta_d + theta_t = {0:.0f} s".format(t_63),
                xy=(t_63, T_63), xytext=(t_63 + 18, T_63 - 0.8),
                arrowprops=dict(arrowstyle="->", color="tab:green"), color="tab:green")

    ax.set_title("Air heater - open loop step response (u = {0} V)".format(U_STEP))
    ax.set_xlabel("t [s]")
    ax.set_ylabel("T_out [degC]")
    ax.grid(True, alpha=0.4)
    fig.tight_layout()


def plot_discretisation_comparison() -> None:
    """Euler vs exact ZOH at a coarse Ts, against the analytic solution.

    The delay is set to zero here on purpose. At a coarse Ts the delay is
    quantised to the sample grid (2 s rounds to nothing at Ts = 4 s), and that
    error would swamp the difference between the two discretisations, which is
    what this comparison is about.
    """
    Ts_coarse = 4.0     # at the limit of what Euler tolerates here
    no_delay = AirHeaterParams(theta_d=0.0)
    t = np.arange(0.0, T_STOP + Ts_coarse, Ts_coarse)

    euler = simulate_open_loop(U_STEP, Ts_coarse, duration=T_STOP,
                               params=no_delay, method="euler")
    zoh = simulate_open_loop(U_STEP, Ts_coarse, duration=T_STOP,
                             params=no_delay, method="zoh")

    # Analytic continuous solution.
    T_final = no_delay.steady_state(U_STEP)
    exact = T_final + (T_START - T_final) * np.exp(-t / no_delay.theta_t)

    err_euler = np.max(np.abs(euler.T_out - exact))
    err_zoh = np.max(np.abs(zoh.T_out - exact))
    print("\nAt Ts = {0} s (no delay), worst deviation from the analytic solution:".format(
        Ts_coarse))
    print("  euler : {0:.4f} degC".format(err_euler))
    print("  zoh   : {0:.4f} degC   (exact by construction)".format(err_zoh))
    print("Euler is fine at Ts = 0.1 s; this is what it costs when you sample")
    print("slowly, which is tempting on a Pi. Use method='zoh' if you do.")

    fig, ax = plt.subplots(figsize=(9, 5), num="Discretisation comparison")
    ax.plot(t, exact, color="black", lw=2, label="analytic")
    ax.plot(euler.t, euler.T_out, "o--", ms=4,
            label="euler (err {0:.3f} degC)".format(err_euler))
    ax.plot(zoh.t, zoh.T_out, "s--", ms=4,
            label="zoh (err {0:.1e} degC)".format(err_zoh))
    ax.set_title("Euler vs exact ZOH at Ts = {0} s, theta_d = 0".format(Ts_coarse))
    ax.set_xlabel("t [s]")
    ax.set_ylabel("T_out [degC]")
    ax.grid(True, alpha=0.4)
    ax.legend()
    fig.tight_layout()


def main() -> None:
    plot_step()
    plot_discretisation_comparison()
    plt.show()


if __name__ == "__main__":
    main()
