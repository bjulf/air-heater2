"""Linear analysis of the implemented Euler plant, backward PI and filter.

The loop reads y[k], filters it, computes u[k], then advances the plant.
Actuator saturation and measurement noise are excluded from this local analysis.
"""
import math

import numpy as np

from .frequency_response import Margins, _find_crossing
from ..simulation.model import AirHeaterParams


def components(omega, Kp, Ti, Ts=0.1, Tf=2.0, params=None):
    """Return P, C, F on z=exp(j*omega*Ts), including the update delay."""
    p = params or AirHeaterParams()
    q = np.exp(-1j * np.asarray(omega) * Ts)
    a = 1 - Ts / p.theta_t
    nd = round(p.theta_d / Ts)
    alpha = Ts / (Tf + Ts)
    plant = p.K_h * (1-a) * q**(nd+1) / (1-a*q)
    controller = Kp * (1 + Ts / (Ti * (1-q)))
    filt = alpha / (1-(1-alpha)*q)
    return plant, controller, filt


def response(omega, Kp, Ti, Ts=0.1, Tf=2.0, params=None):
    p, c, f = components(omega, Kp, Ti, Ts, Tf, params)
    return p*c*f


def phase(omega, Kp, Ti, Ts=0.1, Tf=2.0, params=None):
    p = params or AirHeaterParams()
    w = np.asarray(omega)
    q = np.exp(-1j*w*Ts)
    a = 1-Ts/p.theta_t
    alpha = Ts/(Tf+Ts)
    c = Kp*(1+Ts/(Ti*(1-q)))
    return (np.angle(c) - (round(p.theta_d/Ts)+1)*w*Ts
            - np.angle(1-a*q) - np.angle(1-(1-alpha)*q))


def sampled_margins(Kp, Ti, Ts=0.1, Tf=2.0, params=None):
    """First gain and -180 degree crossings below Nyquist for this PI loop."""
    kw = dict(Ts=Ts, Tf=Tf, params=params)
    hi = math.pi/Ts * (1-1e-9)
    wc = _find_crossing(lambda w: abs(response(w, Kp, Ti, **kw))-1,
                        w_min=1e-6, w_max=hi)
    wp = _find_crossing(lambda w: phase(w, Kp, Ti, **kw)+math.pi,
                        w_min=1e-6, w_max=hi)
    if wc is None or wp is None:
        raise ValueError('No crossover below Nyquist')
    gm = float(1/abs(response(wp, Kp, Ti, **kw)))
    pm = float(np.degrees(math.pi+phase(wc, Kp, Ti, **kw)))
    return Margins(gm, 20*math.log10(gm), pm, wc, wp, Kp*gm)


def poles(Kp, Ti, Ts=0.1, Tf=2.0, params=None):
    """Roots of 1+PCF in z, no Pade approximation. Stable iff |z|<1."""
    p = params or AirHeaterParams()
    a = 1-Ts/p.theta_t
    alpha = Ts/(Tf+Ts)
    nd = round(p.theta_d/Ts)
    # Polynomials in q=z^-1, stored in ascending powers.
    denominator = np.convolve(np.convolve([1, -a], [1, -1]),
                              [1, -(1-alpha)])
    numerator = np.r_[np.zeros(nd+1),
                      Kp*p.K_h*(1-a)*alpha*np.array([1+Ts/Ti, -1])]
    n = max(len(denominator), len(numerator))
    characteristic = (np.pad(denominator, (0, n-len(denominator)))
                      + np.pad(numerator, (0, n-len(numerator))))
    # Multiply by z^(n-1): these same coefficients are descending in z.
    return np.roots(characteristic)
