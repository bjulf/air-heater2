"""Vectorised batch simulation with numpy.

The real-time loop in :mod:`airheater.scheduler` works one sample at a time,
which is what a control application does and what numpy cannot speed up. Batch
simulation is the opposite case: you already know the whole input sequence, you
want the whole response at once, and you want it as arrays ready for plotting
or for a tuning sweep. That is what this module is for.

Two functions:

``simulate_open_loop``
    Exact closed-form response to a known control signal sequence. The model is
    linear and first order, so the forced response is a convolution and the
    whole run is computed in one step rather than iterated.

``simulate_closed_loop``
    Feedback, so the time recursion has to be iterated. What *is* vectorised is
    the parameter axis: pass arrays of Kp and Ti and every candidate tuning is
    simulated simultaneously. That is what makes a tuning sweep cheap.

Both return :class:`SimResult`, which holds numpy arrays.

Requires numpy. The rest of the package does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np

from .model import AirHeaterParams

__all__ = ["SimResult", "simulate_open_loop", "simulate_closed_loop"]

ArrayLike = Union[float, np.ndarray]


@dataclass
class SimResult:
    """Result of a batch simulation.

    For a closed loop sweep over M tunings, the signal arrays have shape
    ``(N, M)``; for a single tuning they are 1-D of length ``N``.
    """

    t: np.ndarray                      # time [s], shape (N,)
    T_out: np.ndarray                  # true outlet temperature [degC]
    u: Optional[np.ndarray] = None     # control signal [V]
    measured: Optional[np.ndarray] = None   # noisy measurement [degC]
    filtered: Optional[np.ndarray] = None   # filtered measurement [degC]
    setpoint: Optional[np.ndarray] = None   # reference [degC]

    @property
    def error(self) -> np.ndarray:
        """Control error r - T_out, using the true temperature."""
        if self.setpoint is None:
            raise ValueError("no setpoint recorded for this run")
        return self.setpoint - self.T_out

    def iae(self) -> np.ndarray:
        """Integral of absolute error [degC*s], the usual tuning score.

        Lower is better. Returns one value per tuning in a sweep.
        """
        dt = float(self.t[1] - self.t[0])
        return np.abs(self.error).sum(axis=0) * dt

    def overshoot(self) -> np.ndarray:
        """Peak overshoot past the final setpoint, in percent of the step."""
        if self.setpoint is None:
            raise ValueError("no setpoint recorded for this run")
        r_final = np.atleast_1d(self.setpoint[-1])
        start = np.atleast_1d(np.asarray(self.T_out)[0])
        span = r_final - start
        peak = np.max(self.T_out, axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            pct = 100.0 * (peak - r_final) / span
        return np.where(np.abs(span) < 1e-12, 0.0, pct)


def _pole(Ts: float, theta_t: float, method: str) -> float:
    """Discrete pole for the chosen discretisation."""
    if method == "zoh":
        return math.exp(-Ts / theta_t)
    if method == "euler":
        return 1.0 - Ts / theta_t
    raise ValueError("method must be 'euler' or 'zoh', got {0!r}".format(method))


def simulate_open_loop(
    u: ArrayLike,
    Ts: float,
    duration: Optional[float] = None,
    params: Optional[AirHeaterParams] = None,
    T_init: Optional[float] = None,
    method: str = "euler",
) -> SimResult:
    """Response to a known control signal sequence, computed in one shot.

    Parameters
    ----------
    u:
        Control signal [V]: a scalar for a step, or an array of one value per
        sample. Values are saturated to the actuator range.
    Ts:
        Sampling time [s].
    duration:
        Run length [s]. Required when ``u`` is a scalar; ignored otherwise.
    params:
        Process parameters.
    T_init:
        Initial temperature [degC]. Defaults to ambient.
    method:
        ``"euler"`` or ``"zoh"``; see :mod:`airheater.model`.

    Returns
    -------
    SimResult
        ``t`` and ``T_out`` of length N+1 (the initial state plus N steps).
    """
    p = params or AirHeaterParams()

    u_arr = np.atleast_1d(np.asarray(u, dtype=float))
    if u_arr.size == 1:
        if duration is None:
            raise ValueError("duration is required when u is a scalar")
        n = int(round(duration / Ts))
        u_arr = np.full(n, float(u_arr[0]))
    else:
        n = u_arr.size
    u_arr = np.clip(u_arr, p.u_min, p.u_max)

    a = _pole(Ts, p.theta_t, method)
    T0 = p.T_env if T_init is None else float(T_init)
    n_delay = int(round(p.theta_d / Ts))

    # Apply the transport delay by shifting the control signal; samples before
    # t = 0 are assumed to have seen u = 0.
    if n_delay > 0:
        u_delayed = np.concatenate([np.zeros(n_delay), u_arr[:-n_delay]]) if n_delay < n \
            else np.zeros(n)
    else:
        u_delayed = u_arr

    # Steady state the process is heading for at each sample.
    x = p.K_h * u_delayed + p.T_env

    # T[k] = a^k * T0 + (1-a) * sum_{j<k} a^(k-1-j) * x[j].
    # The sum is a convolution of x with the impulse response a^m, so the whole
    # run is one call rather than an N-step Python loop.
    powers = a ** np.arange(n, dtype=float)
    conv = np.convolve(x, powers)[:n]
    T = np.empty(n + 1)
    T[0] = T0
    T[1:] = a ** np.arange(1, n + 1, dtype=float) * T0 + (1.0 - a) * conv

    t = np.arange(n + 1, dtype=float) * Ts
    u_out = np.concatenate([u_arr, u_arr[-1:]])   # pad to match t
    return SimResult(t=t, T_out=T, u=u_out)


def simulate_closed_loop(
    setpoint: ArrayLike,
    Ts: float,
    duration: float,
    Kp: ArrayLike,
    Ti: Optional[ArrayLike] = None,
    Td: ArrayLike = 0.0,
    params: Optional[AirHeaterParams] = None,
    T_init: Optional[float] = None,
    Tf: float = 0.0,
    noise_std: float = 0.0,
    seed: Optional[int] = None,
    method: str = "euler",
    slide_form: bool = False,
) -> SimResult:
    """PI/PID control of the process, vectorised over the tuning parameters.

    ``Kp``, ``Ti`` and ``Td`` may be scalars or arrays. Arrays are broadcast
    against each other, and every resulting tuning is simulated at the same
    time, giving signal arrays of shape ``(N+1, M)``.

    This mirrors :class:`~airheater.controller.PIController` exactly - same
    velocity form, same saturation-based anti-windup - so a tuning that looks
    good here behaves the same way in the real-time loop.

    Parameters
    ----------
    setpoint:
        Reference [degC]: a scalar, or an array with one value per sample.
    Ts, duration:
        Sampling time and run length [s].
    Kp, Ti, Td:
        Controller tuning. ``Ti=None`` gives pure P control.
    Tf:
        Measurement filter time constant [s]; 0 disables filtering.
    noise_std, seed:
        Measurement noise [degC] and its seed.
    method:
        Process discretisation, ``"euler"`` or ``"zoh"``.
    slide_form:
        Reproduce the course slides' integral term, which omits Ts.

    Returns
    -------
    SimResult
        With ``T_out``, ``u``, ``measured``, ``filtered`` and ``setpoint``.
    """
    p = params or AirHeaterParams()
    n = int(round(duration / Ts))

    # Broadcast the tuning parameters to a common 1-D parameter axis.
    scalar_run = np.isscalar(Kp) and (Ti is None or np.isscalar(Ti)) and np.isscalar(Td)
    kp = np.atleast_1d(np.asarray(Kp, dtype=float))
    td = np.atleast_1d(np.asarray(Td, dtype=float))
    if Ti is None:
        ti = None
        kp, td = np.broadcast_arrays(kp, td)
    else:
        ti = np.atleast_1d(np.asarray(Ti, dtype=float))
        kp, ti, td = np.broadcast_arrays(kp, ti, td)
        ti = np.array(ti, dtype=float)
    kp = np.array(kp, dtype=float)
    td = np.array(td, dtype=float)
    m = kp.size

    r = np.atleast_1d(np.asarray(setpoint, dtype=float))
    if r.size == 1:
        r = np.full(n, float(r[0]))
    elif r.size != n:
        raise ValueError("setpoint array must have {0} samples, got {1}".format(n, r.size))

    a = _pole(Ts, p.theta_t, method)
    n_delay = int(round(p.theta_d / Ts))
    T0 = p.T_env if T_init is None else float(T_init)

    rng = np.random.default_rng(seed)
    alpha = 1.0 if Tf <= 0.0 else Ts / (Tf + Ts)

    T = np.full(m, T0)
    u = np.zeros(m)
    e_prev = np.zeros(m)
    e_prev2 = np.zeros(m)
    yf = None
    primed = False
    # Transport delay pipeline, oldest first.
    pipeline = np.zeros((max(n_delay, 1), m))

    T_hist = np.empty((n + 1, m))
    u_hist = np.empty((n + 1, m))
    y_hist = np.empty((n + 1, m))
    yf_hist = np.empty((n + 1, m))
    T_hist[0] = T

    for k in range(n):
        y = T + (rng.normal(0.0, noise_std, m) if noise_std > 0.0 else 0.0)
        yf = y if yf is None else yf + alpha * (y - yf)

        e = r[k] - yf
        if not primed:
            e_prev = e.copy()
            e_prev2 = e.copy()
            primed = True

        du = kp * (e - e_prev)
        if ti is not None:
            gain = kp / ti
            du += gain * e if slide_form else gain * Ts * e
        nz = td > 0.0
        if np.any(nz):
            du = du + np.where(nz, kp * (td / Ts) * (e - 2.0 * e_prev + e_prev2), 0.0)

        # Saturate and store the saturated value: this is the anti-windup.
        u = np.clip(u + du, p.u_min, p.u_max)
        e_prev2, e_prev = e_prev, e

        if n_delay > 0:
            u_delayed = pipeline[0].copy()
            pipeline[:-1] = pipeline[1:]
            pipeline[-1] = u
        else:
            u_delayed = u

        T = a * T + (1.0 - a) * (p.K_h * u_delayed + p.T_env)

        u_hist[k] = u
        y_hist[k] = y
        yf_hist[k] = yf
        T_hist[k + 1] = T

    # Pad the last sample so every array matches the time vector.
    u_hist[n] = u_hist[n - 1]
    y_hist[n] = T
    yf_hist[n] = yf_hist[n - 1]

    t = np.arange(n + 1, dtype=float) * Ts
    r_hist = np.concatenate([r, r[-1:]])

    if scalar_run:
        T_hist, u_hist = T_hist[:, 0], u_hist[:, 0]
        y_hist, yf_hist = y_hist[:, 0], yf_hist[:, 0]
        setpoint_out = r_hist
    else:
        setpoint_out = r_hist[:, None]

    return SimResult(
        t=t,
        T_out=T_hist,
        u=u_hist,
        measured=y_hist,
        filtered=yf_hist,
        setpoint=setpoint_out,
    )
