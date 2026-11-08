"""Frequency response and stability margins of the air heater control system.

Transfer functions:

    process     Hp(s) = K_h / (theta_t*s + 1) * exp(-theta_d*s)
    PI          Hc(s) = Kp * (Ti*s + 1) / (Ti*s)
    loop        L(s)  = Hc(s) * Hp(s)
    tracking    T(s)  = L(s) / (1 + L(s))

A note on the time delay. The course material replaces ``exp(-theta_d*s)`` with
a Padé approximation before computing margins. That is necessary only if you
need a *rational* transfer function. For a frequency response you do not: on
the jw axis the delay is exactly

    exp(-j*w*theta_d),  magnitude 1, phase -w*theta_d radians

so this module computes margins from the exact delay. The results are exact
rather than approximation-limited, and there is no Padé order to justify.

Padé lives in :mod:`airheater.analysis.pade`, and is used only for the pole
map, where a rational model is unavoidable.

Requires numpy. No scipy and no `control` package.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..simulation.model import AirHeaterParams

__all__ = [
    "Margins",
    "process_frequency_response",
    "controller_frequency_response",
    "loop_frequency_response",
    "loop_phase",
    "margins",
    "critical_gain",
]


@dataclass
class Margins:
    """Stability margins of the loop transfer function."""

    gm: float        # gain margin, linear factor
    gm_db: float     # gain margin [dB]
    pm: float        # phase margin [deg]
    wc: float        # gain crossover frequency [rad/s]
    w180: float      # phase crossover frequency [rad/s]
    Kc: float        # critical gain: Kp at which the loop is marginally stable

    @property
    def stable(self) -> bool:
        """Asymptotically stable, by the Bode-Nyquist criterion (wc < w180)."""
        return self.wc < self.w180

    def __str__(self) -> str:
        return (
            "wc    = {0:.4f} rad/s\n"
            "w180  = {1:.4f} rad/s\n"
            "GM    = {2:.2f} ({3:.2f} dB)\n"
            "PM    = {4:.1f} deg\n"
            "Kc    = {5:.2f}\n"
            "{6}".format(
                self.wc, self.w180, self.gm, self.gm_db, self.pm, self.Kc,
                "asymptotically stable (wc < w180)" if self.stable
                else "UNSTABLE (wc > w180)",
            )
        )


# -- frequency response ---------------------------------------------------


def process_frequency_response(
    omega: np.ndarray, params: Optional[AirHeaterParams] = None
) -> np.ndarray:
    """Hp(jw) for the air heater, with the exact time delay."""
    p = params or AirHeaterParams()
    w = np.asarray(omega, dtype=float)
    jw = 1j * w
    return p.K_h / (p.theta_t * jw + 1.0) * np.exp(-jw * p.theta_d)


def controller_frequency_response(
    omega: np.ndarray, Kp: float, Ti: Optional[float] = None, Td: float = 0.0
) -> np.ndarray:
    """Hc(jw) for a P, PI or PID controller."""
    w = np.asarray(omega, dtype=float)
    jw = 1j * w
    h = np.full(w.shape, complex(Kp))
    if Ti:
        h = h + Kp / (Ti * jw)
    if Td:
        h = h + Kp * Td * jw
    return h


def loop_frequency_response(
    omega: np.ndarray,
    Kp: float,
    Ti: Optional[float] = None,
    Td: float = 0.0,
    params: Optional[AirHeaterParams] = None,
) -> np.ndarray:
    """L(jw) = Hc(jw) * Hp(jw)."""
    return controller_frequency_response(omega, Kp, Ti, Td) * process_frequency_response(
        omega, params
    )


def loop_phase(
    omega: np.ndarray,
    Kp: float,
    Ti: Optional[float] = None,
    Td: float = 0.0,
    params: Optional[AirHeaterParams] = None,
) -> np.ndarray:
    """Continuous (unwrapped) phase of L(jw) in radians.

    ``numpy.angle`` wraps into (-pi, pi], which hides exactly the crossing
    this analysis is looking for: the delay drives the phase steadily past
    -180 deg. So the phase is assembled from its parts instead, each of which
    is naturally unwrapped:

      * process: ``-atan(w*theta_t) - w*theta_d``, evaluated directly;
      * controller: P, PI and PID all have ``Re(Hc) = Kp > 0``, so their phase
        stays within +/- 90 deg and never wraps.
    """
    p = params or AirHeaterParams()
    if Kp <= 0.0:
        raise ValueError("Kp must be > 0 for this phase construction")
    w = np.asarray(omega, dtype=float)
    phase_process = -np.arctan(w * p.theta_t) - w * p.theta_d
    phase_controller = np.angle(controller_frequency_response(w, Kp, Ti, Td))
    return phase_controller + phase_process


# -- root finding ---------------------------------------------------------


def _bisect(f, lo: float, hi: float, tol: float = 1e-12, max_iter: int = 200) -> float:
    """Bisection on a bracketed sign change. Robust, and fast enough here."""
    f_lo = f(lo)
    for _ in range(max_iter):
        mid = math.sqrt(lo * hi)          # geometric midpoint: frequencies are log-scaled
        f_mid = f(mid)
        if f_mid == 0.0 or (hi - lo) / mid < tol:
            return mid
        if (f_lo < 0.0) != (f_mid < 0.0):
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return math.sqrt(lo * hi)


def _find_crossing(f, w_min: float = 1e-4, w_max: float = 1e3, n: int = 20000) -> Optional[float]:
    """Find the lowest frequency where f changes sign, or None."""
    grid = np.logspace(math.log10(w_min), math.log10(w_max), n)
    vals = f(grid)
    sign_change = np.where(np.diff(np.signbit(vals)))[0]
    if sign_change.size == 0:
        return None
    i = int(sign_change[0])
    return _bisect(lambda w: float(f(np.array([w]))[0]), float(grid[i]), float(grid[i + 1]))


# -- margins --------------------------------------------------------------


def margins(
    Kp: float,
    Ti: Optional[float] = None,
    Td: float = 0.0,
    params: Optional[AirHeaterParams] = None,
) -> Margins:
    """Gain and phase margins of the loop, from the exact frequency response.

    Returns
    -------
    Margins
        Crossover frequencies, gain margin, phase margin and the critical gain
        at which the loop becomes marginally stable.
    """

    def gain_minus_one(w):
        return np.abs(loop_frequency_response(w, Kp, Ti, Td, params)) - 1.0

    def phase_plus_pi(w):
        return loop_phase(w, Kp, Ti, Td, params) + math.pi

    wc = _find_crossing(gain_minus_one)
    w180 = _find_crossing(phase_plus_pi)

    if wc is None:
        raise ValueError("no gain crossover found: |L| never reaches 1")
    if w180 is None:
        raise ValueError("no phase crossover found: phase never reaches -180 deg")

    gm = 1.0 / abs(loop_frequency_response(np.array([w180]), Kp, Ti, Td, params)[0])
    pm = math.degrees(math.pi + float(loop_phase(np.array([wc]), Kp, Ti, Td, params)[0]))

    return Margins(
        gm=gm,
        gm_db=20.0 * math.log10(gm),
        pm=pm,
        wc=wc,
        w180=w180,
        Kc=Kp * gm,
    )


def critical_gain(
    Ti: Optional[float] = None,
    Td: float = 0.0,
    params: Optional[AirHeaterParams] = None,
) -> float:
    """Proportional gain at which the loop becomes marginally stable.

    The loop gain scales linearly with Kp, so this is independent of the Kp you
    evaluate it at.
    """
    return margins(1.0, Ti, Td, params).Kc
