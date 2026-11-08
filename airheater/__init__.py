"""Air heater process simulator and control building blocks.

Discrete-time implementation of the USN small-scale air heater, intended to run
as the process stand-in for a Python control application on a Raspberry Pi.

The package is organised by what each part needs:

``airheater.simulation``
    The process model. ``model`` is the per-sample simulator (standard library
    only); ``batch`` is the vectorised version for offline runs and sweeps
    (numpy).

``airheater.control``
    Controller, measurement filter and the real-time loop. Standard library
    only - a loop that handles one sample at a time has nothing to gain from
    numpy, and this way the Pi needs no scientific stack to run the process.

``airheater.interfaces``
    The boundary to the plant: ``AnalogIO``, with ``SimulatedIO`` and
    ``NiDaqIO`` behind it. Swapping the simulator for the rig is a one-line
    change in the control application.

``airheater.analysis``
    Frequency response, stability margins, tuning rules and the pole map
    (numpy).

The names needed to write a control application are re-exported here, and the
numpy-only subpackages are imported on demand so they never load on the Pi.

Typical use - the control application:

    from airheater import AirHeaterSim, PIController, SimulatedIO, run_control_loop

    Ts = 0.1
    io = SimulatedIO(AirHeaterSim(Ts))
    pi = PIController(Kp=0.52, Ti=18.0, Ts=Ts)
    run_control_loop(io, pi, setpoint=28.0, Ts=Ts, duration=200.0)

Typical use - analysis:

    from airheater.analysis import margins, ziegler_nichols
    from airheater.simulation.batch import simulate_closed_loop
"""

from .control import LowPassFilter, PIController, Sample, run_control_loop
from .interfaces import AnalogIO, SimulatedIO
from .simulation import AirHeaterParams, AirHeaterSim

__all__ = [
    "AirHeaterParams",
    "AirHeaterSim",
    "PIController",
    "LowPassFilter",
    "AnalogIO",
    "SimulatedIO",
    "Sample",
    "run_control_loop",
]

__version__ = "2.0.0"
