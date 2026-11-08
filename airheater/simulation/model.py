"""Discrete-time model of the air heater process.

Continuous model (1. order with transport delay):

    dTout/dt = (1/theta_t) * (-Tout + K_h * u(t - theta_d) + T_env)

Two discretisations are available, selected with ``method``:

``"euler"`` (default)
    Euler forward, x_dot ~ (x[k+1] - x[k]) / Ts:

        Tout[k+1] = Tout[k] + (Ts/theta_t) * (-Tout[k] + K_h * u[k - Nd] + T_env)

    This is the discretisation the course asks you to derive, so it is the
    default and the one to show in the report. It is an approximation, and it
    requires Ts well below theta_t.

``"zoh"``
    Exact solution over one sampling interval, assuming u is held constant
    across the interval (which it is - the controller writes once per sample):

        a = exp(-Ts/theta_t)
        Tout[k+1] = a*Tout[k] + (1 - a)*(K_h * u[k - Nd] + T_env)

    Exact for any Ts, so it stays accurate at the 1 s sampling you would
    actually run on a Raspberry Pi, and has no stability limit on Ts.

In both cases Nd = round(theta_d / Ts) is the transport delay in samples.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Optional

__all__ = ["AirHeaterParams", "AirHeaterSim"]


@dataclass
class AirHeaterParams:
    """Process parameters for the air heater.

    Defaults are the nominal values given in the USN air heater documentation.
    They describe the laboratory rig; a real rig should be identified from its
    own step response before a controller is tuned against it.
    """

    K_h: float = 3.5        # heater gain [degC/V]
    theta_t: float = 22.0   # time constant [s]
    theta_d: float = 2.0    # transport delay [s]
    T_env: float = 21.5     # ambient/inlet air temperature [degC]

    u_min: float = 0.0      # control signal range to the heater [V]
    u_max: float = 5.0

    # Temperature sensor scaling on the outlet: 20-50 degC maps to 1-5 V.
    T_sensor_min: float = 20.0
    T_sensor_max: float = 50.0
    V_sensor_min: float = 1.0
    V_sensor_max: float = 5.0

    def __post_init__(self) -> None:
        if self.theta_t <= 0.0:
            raise ValueError("theta_t must be > 0")
        if self.theta_d < 0.0:
            raise ValueError("theta_d must be >= 0")
        if self.u_max <= self.u_min:
            raise ValueError("u_max must be > u_min")
        if self.T_sensor_max <= self.T_sensor_min:
            raise ValueError("T_sensor_max must be > T_sensor_min")
        if self.V_sensor_max <= self.V_sensor_min:
            raise ValueError("V_sensor_max must be > V_sensor_min")

    def steady_state(self, u: float) -> float:
        """Outlet temperature the process settles at for a constant u [degC]."""
        return self.K_h * u + self.T_env

    def degc_to_volt(self, temperature: float) -> float:
        """Convert a temperature to the sensor voltage a DAQ would read."""
        span_t = self.T_sensor_max - self.T_sensor_min
        span_v = self.V_sensor_max - self.V_sensor_min
        volt = self.V_sensor_min + (temperature - self.T_sensor_min) * span_v / span_t
        return min(max(volt, self.V_sensor_min), self.V_sensor_max)

    def volt_to_degc(self, volt: float) -> float:
        """Convert a sensor voltage from the DAQ to a temperature."""
        span_t = self.T_sensor_max - self.T_sensor_min
        span_v = self.V_sensor_max - self.V_sensor_min
        return self.T_sensor_min + (volt - self.V_sensor_min) * span_t / span_v


class AirHeaterSim:
    """Fixed-step simulator for the air heater process.

    One instance holds the full process state: the outlet temperature plus the
    pipeline of control signals still travelling through the transport delay.
    Call :meth:`step` once per sampling interval with the control signal that
    applies to that interval.

    Parameters
    ----------
    Ts:
        Sampling time [s]. Must stay well below the time constant for the Euler
        approximation to remain accurate; 0.1 s is a good default here.
    params:
        Process parameters. Defaults to :class:`AirHeaterParams`.
    T_init:
        Initial outlet temperature [degC]. Defaults to the ambient temperature.
    noise_std:
        Standard deviation of additive Gaussian measurement noise [degC],
        applied in :meth:`measure` only, so the process state stays clean. Use
        it to develop the measurement lowpass filter before touching the rig.
    seed:
        Seed for the measurement-noise generator, for reproducible runs.
    method:
        ``"euler"`` (default) or ``"zoh"``. See the module docstring.
    """

    def __init__(
        self,
        Ts: float,
        params: Optional[AirHeaterParams] = None,
        T_init: Optional[float] = None,
        noise_std: float = 0.0,
        seed: Optional[int] = None,
        method: str = "euler",
    ) -> None:
        if Ts <= 0.0:
            raise ValueError("Ts must be > 0")
        if method not in ("euler", "zoh"):
            raise ValueError("method must be 'euler' or 'zoh', got {0!r}".format(method))

        self.params = params or AirHeaterParams()
        self.Ts = float(Ts)
        self.noise_std = float(noise_std)
        self.method = method
        self._rng = random.Random(seed)

        # Euler forward is stable for Ts < 2*theta_t, but the approximation is
        # only accurate well below that. Reject a sampling time that is too
        # slow. The exact ZOH solution has no such limit.
        if method == "euler" and self.Ts > self.params.theta_t / 5.0:
            raise ValueError(
                "Ts={0} s is too slow for theta_t={1} s with method='euler'; "
                "use Ts <= {2:.2f} s or method='zoh'".format(
                    self.Ts, self.params.theta_t, self.params.theta_t / 5.0
                )
            )

        # Pole of the discrete model, precomputed: both methods are of the form
        # T[k+1] = a*T[k] + (1-a)*T_steady_state.
        if method == "zoh":
            self._a = math.exp(-self.Ts / self.params.theta_t)
        else:
            self._a = 1.0 - self.Ts / self.params.theta_t

        self.delay_samples = int(round(self.params.theta_d / self.Ts))
        exact = self.params.theta_d / self.Ts
        # The delay is quantised to the sample grid; report how far off that is.
        self.delay_quantisation_error = (self.delay_samples - exact) * self.Ts

        self._T_init = self.params.T_env if T_init is None else float(T_init)
        self.reset()

    # -- state -----------------------------------------------------------

    def reset(self, T_init: Optional[float] = None, u_init: float = 0.0) -> None:
        """Return the simulator to its initial state.

        ``u_init`` pre-fills the transport-delay pipeline, i.e. it is the
        control signal the process is assumed to have seen before t = 0.
        """
        if T_init is not None:
            self._T_init = float(T_init)
        self.T_out = self._T_init
        self.t = 0.0
        self.k = 0
        self._u_pipeline = deque(
            [float(u_init)] * self.delay_samples, maxlen=max(self.delay_samples, 1)
        )

    # -- simulation ------------------------------------------------------

    def step(self, u: float) -> float:
        """Advance the process one sampling interval.

        Parameters
        ----------
        u:
            Control signal to the heater [V]. Saturated to the physical range
            of the actuator, so the simulated process cannot be driven beyond
            what the real heater can do.

        Returns
        -------
        float
            The new, noise-free outlet temperature [degC].
        """
        p = self.params
        u = min(max(float(u), p.u_min), p.u_max)

        if self.delay_samples == 0:
            u_delayed = u
        else:
            u_delayed = self._u_pipeline[0]
            self._u_pipeline.append(u)

        # Both discretisations are a first order lag towards the steady state
        # the delayed control signal commands; only the pole differs.
        T_ss = p.K_h * u_delayed + p.T_env
        self.T_out = self._a * self.T_out + (1.0 - self._a) * T_ss
        self.t += self.Ts
        self.k += 1
        return self.T_out

    # -- measurement -----------------------------------------------------

    def measure(self) -> float:
        """Outlet temperature as the sensor reports it [degC].

        Includes measurement noise when ``noise_std`` is set. This is what a
        control application should consume; :attr:`T_out` is the true process
        state and has no counterpart on the real rig.
        """
        if self.noise_std <= 0.0:
            return self.T_out
        return self.T_out + self._rng.gauss(0.0, self.noise_std)

    def measure_volts(self) -> float:
        """Measurement expressed as the sensor voltage a DAQ would read [V]."""
        return self.params.degc_to_volt(self.measure())

    # -- helpers ---------------------------------------------------------

    def steady_state(self, u: float) -> float:
        """Outlet temperature the process settles at for a constant u [degC]."""
        return self.params.steady_state(u)

    def required_u(self, temperature: float) -> float:
        """Constant control signal that holds a given outlet temperature [V].

        Useful as a bumpless starting value for the controller output.
        """
        p = self.params
        u = (temperature - p.T_env) / p.K_h
        return min(max(u, p.u_min), p.u_max)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            "AirHeaterSim(Ts={0}, t={1:.1f}s, T_out={2:.2f}degC, "
            "delay={3} samples)".format(self.Ts, self.t, self.T_out, self.delay_samples)
        )
