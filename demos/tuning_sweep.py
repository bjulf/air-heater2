"""Tuning sweep: simulate many PI tunings at once and score them.

Shows what the vectorised batch simulator is for. The time recursion of a
feedback loop cannot be vectorised, but the *parameter* axis can: every
candidate tuning advances one sample together, as numpy vector operations.
A few hundred tunings cost about the same as a handful.

Two views:

  * a Kp sweep at fixed Ti, with the step responses and their IAE scores,
    against the critical gain Kc from the stability analysis;
  * a Kp/Ti grid as an IAE heat map, with the stability boundary drawn on top.

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
from airheater.analysis import critical_gain, margins, skogestad_simc, ziegler_nichols
from airheater.simulation.batch import simulate_closed_loop

Ts = 0.1
DURATION = 300.0
SETPOINT = 28.0
T_INIT = 20.0

params = AirHeaterParams()


def sweep_kp() -> None:
    """Step responses across a range of Kp at fixed Ti."""
    Ti = 18.0
    Kp_values = np.array([0.2, 0.52, 1.0, 2.0, 3.5, 5.0])
    Kc = critical_gain(Ti, params=params)

    # One call: all six tunings simulated together.
    res = simulate_closed_loop(SETPOINT, Ts, DURATION, Kp=Kp_values, Ti=Ti,
                               T_init=T_INIT, params=params)
    iae = res.iae()
    overshoot = res.overshoot()

    print("Ti = {0} s, critical gain Kc = {1:.2f}".format(Ti, Kc))
    print("{0:>6} {1:>8} {2:>9} {3:>10} {4:>8}".format(
        "Kp", "IAE", "overshoot", "PM[deg]", "stable"))
    for i, kp in enumerate(Kp_values):
        m = margins(float(kp), Ti, params=params)
        print("{0:>6.2f} {1:>8.1f} {2:>8.1f}% {3:>10.1f} {4:>8}".format(
            kp, iae[i], overshoot[i], m.pm, "yes" if kp < Kc else "NO"))

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(9, 7),
                                   num="Kp sweep")
    for i, kp in enumerate(Kp_values):
        style = "--" if kp >= Kc else "-"
        ax1.plot(res.t, res.T_out[:, i], style,
                 label="Kp = {0:.2f} (IAE {1:.0f})".format(kp, iae[i]))
        ax2.plot(res.t, res.u[:, i], style)

    ax1.axhline(SETPOINT, color="grey", ls=":", lw=1)
    ax1.set_ylabel("T_out [degC]")
    ax1.set_title("Kp sweep at Ti = {0} s  (Kc = {1:.2f}, dashed = beyond Kc)".format(
        Ti, Kc))
    ax1.grid(True, alpha=0.4)
    ax1.legend(fontsize=8, loc="lower right", ncol=2)

    ax2.axhline(params.u_max, color="grey", ls=":", lw=1)
    ax2.set_xlabel("t [s]")
    ax2.set_ylabel("u [V]")
    ax2.set_ylim(-0.2, params.u_max + 0.3)
    ax2.grid(True, alpha=0.4)
    fig.tight_layout()


def sweep_grid() -> None:
    """IAE over a Kp/Ti grid, with the stability boundary drawn on top."""
    Kp_axis = np.linspace(0.1, 6.0, 40)
    Ti_axis = np.linspace(4.0, 40.0, 30)
    KP, TI = np.meshgrid(Kp_axis, Ti_axis, indexing="ij")

    # 1200 tunings, flattened onto the parameter axis and run in one call.
    res = simulate_closed_loop(SETPOINT, Ts, DURATION,
                               Kp=KP.ravel(), Ti=TI.ravel(),
                               T_init=T_INIT, params=params)
    iae = res.iae().reshape(KP.shape)

    # Stability boundary: for each Ti, the Kp at which the loop goes marginal.
    Kc_line = np.array([critical_gain(float(ti), params=params) for ti in Ti_axis])

    best = np.unravel_index(np.argmin(iae), iae.shape)
    print("\nGrid sweep over {0} tunings".format(KP.size))
    print("  lowest IAE: Kp = {0:.2f}, Ti = {1:.1f} s, IAE = {2:.1f}".format(
        KP[best], TI[best], iae[best]))
    print("  (lowest IAE is not automatically the best choice - check the")
    print("   margins and the control signal activity before adopting it)")

    fig, ax = plt.subplots(figsize=(8, 6), num="IAE over the tuning grid")
    # Clip the colour scale so the unstable corner does not flatten everything.
    levels = np.linspace(np.nanmin(iae), np.percentile(iae, 80), 30)
    cf = ax.contourf(Kp_axis, Ti_axis, np.clip(iae, None, levels[-1]).T,
                     levels=levels, cmap="viridis_r")
    fig.colorbar(cf, ax=ax, label="IAE [degC*s]  (clipped)")

    ax.plot(Kc_line, Ti_axis, color="tab:red", lw=2, label="stability boundary (Kc)")
    for t, marker in [(ziegler_nichols(params, "PI"), "o"),
                      (skogestad_simc(params), "s"),
                      (skogestad_simc(params, Tc=10.0), "^")]:
        ax.plot(t.Kp, t.Ti, marker, color="white", markeredgecolor="black",
                markersize=9, label=t.method)

    ax.set_xlabel("Kp")
    ax.set_ylabel("Ti [s]")
    ax.set_title("Integral of absolute error over the tuning grid")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()


def main() -> None:
    sweep_kp()
    sweep_grid()
    plt.show()


if __name__ == "__main__":
    main()
