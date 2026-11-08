"""Measurement filtering.

The real air heater's temperature measurement is noisy, and a derivative-free
PI controller still passes that noise straight to the heater. A first order
lowpass filter on the measurement is the standard remedy, and the task
description asks for one before the rig is connected.

Discrete first order lowpass (Euler backward):

    yf[k] = (1 - a) * yf[k-1] + a * y[k],    a = Ts / (Tf + Ts)

Tf is the filter time constant. A useful starting point is roughly a tenth of
the process time constant, i.e. ~2 s here: enough to suppress noise without
adding phase lag that eats the stability margins.
"""

from __future__ import annotations

__all__ = ["LowPassFilter"]


class LowPassFilter:
    """First order lowpass filter for a sampled measurement.

    Parameters
    ----------
    Tf:
        Filter time constant [s]. 0 disables filtering (pass-through).
    Ts:
        Sampling time [s]. Must match the rate the loop runs at.
    y_init:
        Initial filter state. Leave as ``None`` to initialise from the first
        sample, which avoids a startup transient.
    """

    def __init__(self, Tf: float, Ts: float, y_init: float = None) -> None:
        if Tf < 0.0:
            raise ValueError("Tf must be >= 0")
        if Ts <= 0.0:
            raise ValueError("Ts must be > 0")

        self.Tf = float(Tf)
        self.Ts = float(Ts)
        self.a = 1.0 if self.Tf == 0.0 else self.Ts / (self.Tf + self.Ts)
        self._y_init = y_init
        self.reset()

    def reset(self, y_init: float = None) -> None:
        """Clear the filter state."""
        if y_init is not None:
            self._y_init = float(y_init)
        self.y = self._y_init

    def update(self, measurement: float) -> float:
        """Filter one sample and return the filtered value."""
        y = float(measurement)
        if self.y is None:
            self.y = y  # initialise on the first sample, no startup transient
        else:
            self.y += self.a * (y - self.y)
        return self.y

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "LowPassFilter(Tf={0}, Ts={1}, y={2})".format(self.Tf, self.Ts, self.y)
