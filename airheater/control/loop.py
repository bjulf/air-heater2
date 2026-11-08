"""Fixed-step control loop with wall-clock pacing.

This is the part that runs on the Raspberry Pi. It executes the standard
sequence once per sampling interval:

    read measurement -> filter -> controller -> write control signal -> publish

``on_sample`` is the integration point for the rest of the Industry 4.0 system:
hand it a callback that publishes the sample over MQTT or OPC UA, writes it to
a database, or updates an HMI. It is called once per sample with a
:class:`Sample`; exceptions raised inside it are reported but do not stop the
loop, because losing the network should not stop the process control.

Timing note: this is soft real time on a general-purpose OS. Jitter of a few
milliseconds is normal on a Raspberry Pi running Linux and is harmless for a
process with a 22 s time constant. ``Sample.jitter`` reports it so you can show
in your report that the loop actually held its rate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Union

from ..interfaces.base import AnalogIO
from .controller import PIController
from .filters import LowPassFilter

__all__ = ["Sample", "run_control_loop"]

SetpointLike = Union[float, Callable[[float], float]]


@dataclass
class Sample:
    """One sample of the control loop."""

    k: int                  # sample number
    t: float                # loop time [s]
    setpoint: float         # reference r [degC]
    measured: float         # raw measurement y [degC]
    filtered: float         # filtered measurement yf [degC]
    error: float            # e = r - yf [degC]
    u: float                # control signal [V]
    saturated: bool         # controller output hit an actuator limit
    jitter: float = 0.0     # how late this sample started vs. its slot [s]

    def as_dict(self) -> Dict[str, float]:
        """Flat dict, ready for json.dumps() to an MQTT topic or a database."""
        return {
            "k": self.k,
            "t": round(self.t, 3),
            "setpoint": round(self.setpoint, 3),
            "measured": round(self.measured, 3),
            "filtered": round(self.filtered, 3),
            "error": round(self.error, 3),
            "u": round(self.u, 4),
            "saturated": self.saturated,
            "jitter": round(self.jitter, 4),
        }


def _as_setpoint_fn(setpoint: SetpointLike) -> Callable[[float], float]:
    if callable(setpoint):
        return setpoint
    value = float(setpoint)
    return lambda t: value


def run_control_loop(
    io: AnalogIO,
    controller: PIController,
    setpoint: SetpointLike,
    Ts: float,
    duration: Optional[float] = None,
    measurement_filter: Optional[LowPassFilter] = None,
    on_sample: Optional[Callable[[Sample], None]] = None,
    realtime: bool = True,
    stop: Optional[Callable[[], bool]] = None,
    history: bool = True,
) -> List[Sample]:
    """Run the control loop.

    Parameters
    ----------
    io:
        Process backend: :class:`~airheater.io_backend.SimulatedIO` for the
        simulator, a hardware backend for the real rig.
    controller:
        Controller instance. Its ``Ts`` must match the ``Ts`` given here.
    setpoint:
        Reference temperature [degC], either a constant or a callable
        ``f(t) -> degC`` for setpoint profiles.
    Ts:
        Sampling time [s].
    duration:
        Run length [s]. ``None`` runs until ``stop()`` returns True or the
        loop is interrupted with Ctrl-C.
    measurement_filter:
        Optional measurement lowpass filter. Strongly recommended on the real
        rig, where the temperature signal is noisy.
    on_sample:
        Called once per sample with the :class:`Sample`. This is where you
        publish to MQTT/OPC UA or write to a database. Exceptions inside it
        are caught and reported, never allowed to stop the loop.
    realtime:
        True paces the loop against the wall clock, which is what you want on
        the Pi. False runs as fast as possible, for offline simulation.
    stop:
        Optional callable polled each sample; returning True ends the loop.
    history:
        Collect every sample in the returned list. Set False for long or
        indefinite runs so memory does not grow without bound.

    Returns
    -------
    list of Sample
        The recorded samples, or an empty list when ``history`` is False.
    """
    if Ts <= 0.0:
        raise ValueError("Ts must be > 0")
    if abs(controller.Ts - Ts) > 1e-12:
        raise ValueError(
            "controller.Ts={0} does not match loop Ts={1}; the integral and "
            "derivative terms would be scaled wrongly".format(controller.Ts, Ts)
        )
    if measurement_filter is not None and abs(measurement_filter.Ts - Ts) > 1e-12:
        raise ValueError(
            "measurement_filter.Ts={0} does not match loop Ts={1}".format(
                measurement_filter.Ts, Ts
            )
        )

    setpoint_fn = _as_setpoint_fn(setpoint)
    n_samples = None if duration is None else int(round(duration / Ts))
    samples: List[Sample] = []

    t0 = time.perf_counter()
    k = 0
    try:
        while True:
            if n_samples is not None and k >= n_samples:
                break
            if stop is not None and stop():
                break

            # Wait for this sample's slot. Slots are computed from t0 rather
            # than accumulated, so the loop does not drift over a long run.
            slot = k * Ts
            if realtime:
                remaining = slot - (time.perf_counter() - t0)
                if remaining > 0:
                    time.sleep(remaining)
                jitter = (time.perf_counter() - t0) - slot
                t = time.perf_counter() - t0
            else:
                jitter = 0.0
                t = slot

            r = float(setpoint_fn(t))
            y = io.read_temperature()
            yf = y if measurement_filter is None else measurement_filter.update(y)
            u = controller.update(r, yf)
            io.write_control(u)

            sample = Sample(
                k=k,
                t=t,
                setpoint=r,
                measured=y,
                filtered=yf,
                error=r - yf,
                u=u,
                saturated=controller.saturated,
                jitter=jitter,
            )
            if history:
                samples.append(sample)
            if on_sample is not None:
                try:
                    on_sample(sample)
                except Exception as exc:  # keep controlling even if publishing fails
                    print("on_sample callback failed at k={0}: {1!r}".format(k, exc))

            k += 1
    except KeyboardInterrupt:
        print("\nControl loop interrupted at t={0:.1f} s".format(k * Ts))

    return samples
