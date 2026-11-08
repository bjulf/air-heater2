"""Hardware backend for an NI USB-6008 (or similar NI-DAQmx device).

Kept in its own module because it is the one part of the package that has not
been tested against hardware, and because importing it pulls in the nidaqmx
driver. Nothing else imports it, so the simulator runs on a machine that has
no NI driver installed at all.

Before trusting this on the rig, check:

  * the device and channel names against NI MAX;
  * the wiring - analog input on the 1-5 V temperature transmitter, analog
    output on the 0-5 V heater input;
  * the sensor scaling in :class:`~airheater.simulation.model.AirHeaterParams`
    against the transmitter's own datasheet.

Always use it as a context manager, or call :meth:`close` from a ``finally``
block. Closing drives the heater back to 0 V; leaving the process without that
means an aborted script can leave the heater running.
"""

from __future__ import annotations

from typing import Optional

from ..simulation.model import AirHeaterParams
from .base import AnalogIO

__all__ = ["NiDaqIO"]


class NiDaqIO(AnalogIO):
    """Analog I/O through NI-DAQmx.

    Parameters
    ----------
    device:
        DAQmx device name, e.g. ``"Dev1"``.
    ai_channel, ao_channel:
        Analog input channel carrying the 1-5 V temperature signal, and analog
        output channel driving the 0-5 V heater input.
    params:
        Scaling and limits. Uses :class:`AirHeaterParams` defaults otherwise.
    """

    def __init__(
        self,
        device: str = "Dev1",
        ai_channel: str = "ai0",
        ao_channel: str = "ao0",
        params: Optional[AirHeaterParams] = None,
    ) -> None:
        try:
            from nidaqmx import Task
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise ImportError(
                "nidaqmx is required for NiDaqIO: pip install nidaqmx "
                "(and install the NI-DAQmx driver). Use SimulatedIO to work "
                "without hardware."
            ) from exc

        self.params = params or AirHeaterParams()
        self._closed = False

        self._ai_task = Task()
        self._ai_task.ai_channels.add_ai_voltage_chan(
            "{0}/{1}".format(device, ai_channel),
            min_val=self.params.V_sensor_min,
            max_val=self.params.V_sensor_max,
        )
        self._ao_task = Task()
        self._ao_task.ao_channels.add_ao_voltage_chan(
            "{0}/{1}".format(device, ao_channel),
            min_val=self.params.u_min,
            max_val=self.params.u_max,
        )

    def read_temperature(self) -> float:
        volts = float(self._ai_task.read())
        return self.params.volt_to_degc(volts)

    def write_control(self, u: float) -> None:
        u = min(max(float(u), self.params.u_min), self.params.u_max)
        self._ao_task.write(u)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Leave the heater off, then release the tasks. The finally block
        # matters: the tasks must be released even if the write fails.
        try:
            self._ao_task.write(self.params.u_min)
        finally:
            self._ao_task.close()
            self._ai_task.close()
