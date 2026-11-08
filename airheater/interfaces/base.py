"""The interface a control application uses to reach the process.

The control application should not care whether it is driving the simulator or
the real air heater. Both are exposed through the same small interface, so
swapping one for the other is a one-line change - which is what makes building
the simulator first worth the effort.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["AnalogIO"]


class AnalogIO(ABC):
    """Process backend: read the temperature, write the control signal."""

    @abstractmethod
    def read_temperature(self) -> float:
        """Read the outlet temperature [degC]."""

    @abstractmethod
    def write_control(self, u: float) -> None:
        """Write the control signal to the heater [V]."""

    def close(self) -> None:
        """Release the process safely. Override where that means something.

        On real hardware this must leave the heater off. Implementations are
        expected to be idempotent, since the context manager and an explicit
        call may both run.
        """

    def __enter__(self) -> "AnalogIO":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
