"""Discrete PI/PID controller in velocity (incremental) form.

Starting from the continuous PI controller

    u(t) = Kp*e + (Kp/Ti) * integral(e dt)

and differentiating to remove the integral:

    u_dot = Kp*e_dot + (Kp/Ti)*e

the Euler backward approximation x_dot ~ (x[k] - x[k-1]) / Ts gives

    u[k] = u[k-1] + Kp*(e[k] - e[k-1]) + Kp*(Ts/Ti)*e[k]

The optional derivative term extends this to a full velocity-form PID:

    + Kp*(Td/Ts)*(e[k] - 2*e[k-1] + e[k-2])

Note on the Ts factor
---------------------
The course slides state the integral term as ``(Kp/Ti)*e[k]`` without the
sampling time. That drops a factor Ts and makes the integral action Ts times
too strong (10x at Ts = 0.1 s), which is why the slide example needs Kp = 0.1
rather than the Kp = 0.52 the Skogestad tuning produced. This implementation
uses the correct form. Pass ``slide_form=True`` if you need to reproduce the
slide results exactly for comparison in your report.
"""

from __future__ import annotations

__all__ = ["PIController"]


class PIController:
    """Discrete PI (optionally PID) controller with output saturation.

    The velocity form gives anti-windup for free: the saturated output is fed
    back as ``u[k-1]``, so the controller cannot accumulate integral action
    while the actuator is at its limit.

    Parameters
    ----------
    Kp:
        Proportional gain. For the air heater, Skogestad tuning gives ~0.52.
    Ti:
        Integral time [s]. ~18 s for the air heater. Use ``None`` or ``0`` for
        a pure P controller.
    Ts:
        Sampling time [s]. Must match the rate the loop actually runs at.
    Td:
        Derivative time [s]. 0 (the default) gives a PI controller, which is
        enough for this process.
    u_min, u_max:
        Actuator limits [V], matching the heater input range.
    u_init:
        Initial controller output [V]. Set it to the control signal that holds
        the current temperature for a bumpless start.
    slide_form:
        Reproduce the course slides' integral term, which omits Ts. Off by
        default; see the module docstring.
    """

    def __init__(
        self,
        Kp: float,
        Ti: float = None,
        Ts: float = 0.1,
        Td: float = 0.0,
        u_min: float = 0.0,
        u_max: float = 5.0,
        u_init: float = 0.0,
        slide_form: bool = False,
    ) -> None:
        if Ts <= 0.0:
            raise ValueError("Ts must be > 0")
        if Ti is not None and Ti < 0.0:
            raise ValueError("Ti must be >= 0")
        if Td < 0.0:
            raise ValueError("Td must be >= 0")
        if u_max <= u_min:
            raise ValueError("u_max must be > u_min")

        self.Kp = float(Kp)
        self.Ti = None if Ti in (None, 0) else float(Ti)
        self.Td = float(Td)
        self.Ts = float(Ts)
        self.u_min = float(u_min)
        self.u_max = float(u_max)
        self.slide_form = bool(slide_form)

        self._u_init = min(max(float(u_init), self.u_min), self.u_max)
        self.reset()

    def reset(self, u_init: float = None) -> None:
        """Clear the controller state.

        ``u_init`` optionally sets a new starting output, e.g. to resume
        bumplessly from the control signal currently applied to the process.
        """
        if u_init is not None:
            self._u_init = min(max(float(u_init), self.u_min), self.u_max)
        self.u = self._u_init
        self._e_prev = 0.0
        self._e_prev2 = 0.0
        self._primed = False
        self.saturated = False

    def update(self, setpoint: float, measurement: float) -> float:
        """Compute the control signal for one sample.

        Parameters
        ----------
        setpoint:
            Reference value r [degC].
        measurement:
            Filtered process value y [degC].

        Returns
        -------
        float
            Control signal u [V], saturated to the actuator limits.
        """
        e = float(setpoint) - float(measurement)

        # On the first sample there is no previous error; treat e[k-1] = e[k]
        # so the proportional term contributes no step from an arbitrary zero.
        if not self._primed:
            self._e_prev = e
            self._e_prev2 = e
            self._primed = True

        du = self.Kp * (e - self._e_prev)

        if self.Ti is not None:
            gain = self.Kp / self.Ti
            du += gain * e if self.slide_form else gain * self.Ts * e

        if self.Td > 0.0:
            du += self.Kp * (self.Td / self.Ts) * (e - 2.0 * self._e_prev + self._e_prev2)

        u = self.u + du

        # Saturate, and store the saturated value as u[k-1]. This is what stops
        # the integral term from winding up while the heater is at its limit.
        u_sat = min(max(u, self.u_min), self.u_max)
        self.saturated = u_sat != u

        self.u = u_sat
        self._e_prev2 = self._e_prev
        self._e_prev = e
        self.e = e
        return u_sat

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "PIController(Kp={0}, Ti={1}, Td={2}, Ts={3}, u={4:.3f})".format(
            self.Kp, self.Ti, self.Td, self.Ts, self.u
        )
