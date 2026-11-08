"""Frequency response and stability analysis of the air heater control system.

Covers the analysis Part 1 of the task asks for:

  * Bode diagram of the loop transfer function with the stability margins
  * gain and phase crossover frequencies, GM, PM and the critical gain Kc
  * closed loop pole map
  * PI parameters from the Ziegler-Nichols frequency response method, compared
    against the Skogestad SIMC rule and the values used in the course material

The time delay is handled exactly (``exp(-j*w*theta_d)``) rather than through a
Padé approximation, so the margins are not limited by an approximation order.
The pole map still uses Padé, because poles only exist for a rational model.

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
from airheater.analysis import (
    Tuning,
    closed_loop_poles,
    loop_frequency_response,
    loop_phase,
    margins,
    skogestad_simc,
    ultimate_point,
    ziegler_nichols,
)
from airheater.simulation.batch import simulate_closed_loop

# The tuning the course material arrives at, and the one analysed in detail.
Kp = 0.52
Ti = 18.0

params = AirHeaterParams()
omega = np.logspace(-3, 1, 2000)


def print_analysis() -> None:
    m = margins(Kp, Ti, params=params)
    print("=" * 62)
    print("Stability analysis, Kp = {0}, Ti = {1} s".format(Kp, Ti))
    print("=" * 62)
    print(m)
    print()
    print("Kc is the gain at which the loop becomes marginally stable:")
    print("  Kp < {0:.2f}  asymptotically stable".format(m.Kc))
    print("  Kp = {0:.2f}  marginally stable".format(m.Kc))
    print("  Kp > {0:.2f}  unstable".format(m.Kc))
    print()

    Ku, Pu = ultimate_point(params)
    print("=" * 62)
    print("Tuning rules")
    print("=" * 62)
    print("Ultimate point of the process: Ku = {0:.3f}, Pu = {1:.2f} s".format(Ku, Pu))
    print()

    candidates = [
        ziegler_nichols(params, "PI"),
        skogestad_simc(params),                 # Tc = theta_d, aggressive
        skogestad_simc(params, Tc=10.0),        # gentler, matches the course values
    ]
    rows = [(t.method, t.Kp, t.Ti) for t in candidates]
    rows.append(("course material", Kp, Ti))

    header = "{0:<32} {1:>6} {2:>7} {3:>6} {4:>8} {5:>7}"
    print(header.format("rule", "Kp", "Ti[s]", "GM", "PM[deg]", "Kc"))
    print("-" * 70)
    for name, kp, ti in rows:
        mm = margins(kp, ti, params=params)
        print("{0:<32} {1:>6.3f} {2:>7.2f} {3:>6.2f} {4:>8.1f} {5:>7.2f}".format(
            name[:32], kp, ti, mm.gm, mm.pm, mm.Kc))
    print()
    print("Rules of thumb: GM 2-6 and PM 30-60 deg is a healthy loop.")
    print("Ziegler-Nichols is the aggressive one here - fast, but it spends")
    print("most of the margin. SIMC with Tc = 10 s reproduces the course values.")
    return candidates


def plot_bode(m) -> None:
    L = loop_frequency_response(omega, Kp, Ti, params=params)
    mag_db = 20.0 * np.log10(np.abs(L))
    phase_deg = np.degrees(loop_phase(omega, Kp, Ti, params=params))

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(8, 7),
                                   num="Bode diagram of L(s)")

    ax1.semilogx(omega, mag_db)
    ax1.axhline(0.0, color="grey", lw=1, ls="--")
    ax1.axvline(m.wc, color="tab:green", lw=1, ls=":")
    ax1.axvline(m.w180, color="tab:red", lw=1, ls=":")
    # Gain margin: the distance from |L| up to 0 dB at the phase crossover.
    ax1.annotate("", xy=(m.w180, 0.0), xytext=(m.w180, -m.gm_db),
                 arrowprops=dict(arrowstyle="<->", color="tab:red"))
    ax1.text(m.w180 * 1.1, -m.gm_db / 2, "GM = {0:.1f} dB".format(m.gm_db),
             color="tab:red", va="center")
    ax1.set_ylabel("|L| [dB]")
    ax1.set_title("Loop transfer function L(s), Kp = {0}, Ti = {1} s".format(Kp, Ti))
    ax1.grid(True, which="both", alpha=0.4)

    ax2.semilogx(omega, phase_deg)
    ax2.axhline(-180.0, color="grey", lw=1, ls="--")
    ax2.axvline(m.wc, color="tab:green", lw=1, ls=":")
    ax2.axvline(m.w180, color="tab:red", lw=1, ls=":")
    ax2.annotate("", xy=(m.wc, -180.0), xytext=(m.wc, -180.0 + m.pm),
                 arrowprops=dict(arrowstyle="<->", color="tab:green"))
    ax2.text(m.wc * 1.1, -180.0 + m.pm / 2, "PM = {0:.0f} deg".format(m.pm),
             color="tab:green", va="center")
    ax2.set_xlabel("frequency [rad/s]")
    ax2.set_ylabel("phase [deg]")
    ax2.set_ylim(-360, 0)
    ax2.grid(True, which="both", alpha=0.4)
    fig.tight_layout()


def plot_poles() -> None:
    fig, ax = plt.subplots(figsize=(7, 6), num="Closed loop poles")
    for kp, colour in [(Kp, "tab:blue"), (2.5, "tab:orange"), (6.0, "tab:red")]:
        poles = closed_loop_poles(kp, Ti, params=params)
        stable = poles.real.max() < 0.0
        ax.scatter(poles.real, poles.imag, marker="x", s=70, color=colour,
                   label="Kp = {0} ({1})".format(kp, "stable" if stable else "unstable"))
    ax.axvline(0.0, color="grey", lw=1)
    ax.axhline(0.0, color="grey", lw=1)
    ax.axvspan(0.0, 1.0, color="tab:red", alpha=0.06)
    ax.text(0.15, ax.get_ylim()[1] * 0.85, "unstable\nhalf-plane",
            color="tab:red", alpha=0.7, ha="left", va="top")
    ax.set_xlim(-6, 1)
    ax.set_xlabel("Re")
    ax.set_ylabel("Im")
    ax.set_title("Closed loop poles (Padé order 5), Ti = {0} s".format(Ti))
    ax.grid(True, alpha=0.4)
    ax.legend(loc="lower left")
    fig.tight_layout()


def plot_tuning_comparison(candidates) -> None:
    """Simulate each candidate tuning so the margins can be seen as behaviour."""
    Ts, duration = 0.1, 300.0
    tunings = list(candidates) + [Tuning(Kp=Kp, Ti=Ti, method="course")]

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(9, 7),
                                   num="Tuning comparison")
    for t in tunings:
        res = simulate_closed_loop(28.0, Ts, duration, Kp=t.Kp, Ti=t.Ti,
                                   T_init=20.0, params=params)
        m = margins(t.Kp, t.Ti, params=params)
        label = "{0}  (PM {1:.0f} deg, IAE {2:.0f})".format(t, m.pm, float(res.iae()))
        ax1.plot(res.t, res.T_out, label=label)
        ax2.plot(res.t, res.u)

    ax1.axhline(28.0, color="grey", ls="--", lw=1)
    ax1.set_ylabel("T_out [degC]")
    ax1.set_title("Step to 28 degC under each tuning")
    ax1.grid(True, alpha=0.4)
    ax1.legend(fontsize=8, loc="lower right")

    ax2.axhline(params.u_max, color="grey", ls="--", lw=1)
    ax2.set_xlabel("t [s]")
    ax2.set_ylabel("u [V]")
    ax2.set_ylim(-0.2, params.u_max + 0.3)
    ax2.grid(True, alpha=0.4)
    fig.tight_layout()


def main() -> None:
    candidates = print_analysis()
    m = margins(Kp, Ti, params=params)
    plot_bode(m)
    plot_poles()
    plot_tuning_comparison(candidates)
    plt.show()


if __name__ == "__main__":
    main()
