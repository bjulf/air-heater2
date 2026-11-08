"""Padé approximation of the time delay, and the closed loop pole map.

A pole location only exists for a rational transfer function, so the pole map
is the one place where the delay has to be approximated. Everything else in
:mod:`airheater.analysis` uses the exact delay instead - see
:mod:`airheater.analysis.frequency_response`.

Requires numpy.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

from ..simulation.model import AirHeaterParams

__all__ = ["pade", "closed_loop_poles"]


def pade(theta: float, order: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    """Padé approximation of ``exp(-theta*s)``.

    Returns ``(num, den)`` as numpy polynomial coefficient arrays, highest
    power first, matching ``numpy.poly1d`` and ``control.pade`` conventions.
    """
    if theta < 0.0:
        raise ValueError("theta must be >= 0")
    if order < 1:
        raise ValueError("order must be >= 1")
    if theta == 0.0:
        return np.array([1.0]), np.array([1.0])

    n = order
    # c_k = (2n-k)! n! / ((2n)! k! (n-k)!), ascending in powers of (theta*s).
    coeffs = np.array([
        math.factorial(2 * n - k) * math.factorial(n)
        / (math.factorial(2 * n) * math.factorial(k) * math.factorial(n - k))
        * theta ** k
        for k in range(n + 1)
    ])
    num = (coeffs * np.array([(-1.0) ** k for k in range(n + 1)]))[::-1]
    den = coeffs[::-1]
    # Descending order, normalised so the leading denominator coefficient is 1.
    return num / den[0], den / den[0]


def closed_loop_poles(
    Kp: float,
    Ti: Optional[float] = None,
    Td: float = 0.0,
    params: Optional[AirHeaterParams] = None,
    pade_order: int = 5,
) -> np.ndarray:
    """Poles of the tracking transfer function T(s) = L/(1+L).

    The delay is replaced by a Padé approximation of the given order, because
    a pole location only exists for a rational model. Raising the order moves
    the approximation's own poles further out; it does not make the delay
    rational. Treat the pole map as indicative and read the margins from
    :func:`~airheater.analysis.frequency_response.margins`, which uses the
    exact delay.
    """
    p = params or AirHeaterParams()
    pade_num, pade_den = pade(p.theta_d, pade_order)

    # Hp = K_h/(theta_t s + 1) * pade
    hp_num = np.convolve([p.K_h], pade_num)
    hp_den = np.convolve([p.theta_t, 1.0], pade_den)

    # Hc: P -> Kp; PI -> Kp(Ti s + 1)/(Ti s); PID -> Kp(Td Ti s^2 + Ti s + 1)/(Ti s)
    if not Ti:
        hc_num, hc_den = np.array([Kp]), np.array([1.0])
    elif Td:
        hc_num = np.array([Kp * Td * Ti, Kp * Ti, Kp])
        hc_den = np.array([Ti, 0.0])
    else:
        hc_num = np.array([Kp * Ti, Kp])
        hc_den = np.array([Ti, 0.0])

    l_num = np.convolve(hc_num, hp_num)
    l_den = np.convolve(hc_den, hp_den)

    # T = L/(1+L) = l_num / (l_den + l_num); poles are the roots of that sum.
    pad = len(l_den) - len(l_num)
    char = l_den + np.concatenate([np.zeros(pad), l_num]) if pad >= 0 else \
        np.concatenate([np.zeros(-pad), l_den]) + l_num
    return np.roots(char)
