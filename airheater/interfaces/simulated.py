"""Simulator backend - the process stand-in used while the rig is not wired up."""

from __future__ import annotations

from typing import Optional

from ..simulation.model import AirHeaterParams, AirHeaterSim
from .base import AnalogIO

__all__ = ["SimulatedIO"]


class SimulatedIO(AnalogIO):
    """Drive :class:`~airheater.simulation.model.AirHeaterSim` as a process.

    The model advances one sampling interval on each :meth:`write_control`,
    which matches the read -> compute -> write order of a control loop: the
    value read at sample k is the process state produced by the control signal
    written at sample k-1.

    Parameters
    ----------
    sim:
        The process simulator to drive. A default one is created if omitted,
        in which case ``Ts`` must be given.
    Ts:
        Sampling time [s], used only when ``sim`` is not supplied.
    """

    def __init__(self, sim: Optional[AirHeaterSim] = None, Ts: float = 0.1, **sim_kwargs) -> None:
        if sim is None:
            sim = AirHeaterSim(Ts, **sim_kwargs)
        elif sim_kwargs:
            raise TypeError("pass simulator options either via sim or as kwargs, not both")
        self.sim = sim
        self.u = 0.0

    def read_temperature(self) -> float:
        return self.sim.measure()

    def read_temperature_volts(self) -> float:
        """Measurement as a sensor voltage, mirroring what the DAQ returns."""
        return self.sim.measure_volts()

    def write_control(self, u: float) -> None:
        self.u = float(u)
        self.sim.step(self.u)

    def close(self) -> None:
        self.u = 0.0

    @property
    def params(self) -> AirHeaterParams:
        return self.sim.params
