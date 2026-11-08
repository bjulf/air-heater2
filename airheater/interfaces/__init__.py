"""Process backends: the boundary between the control application and the plant.

``AnalogIO`` is the interface; ``SimulatedIO`` is the simulator behind it, and
``NiDaqIO`` the real rig.

``NiDaqIO`` is deliberately *not* re-exported here, because importing it pulls
in the nidaqmx driver. Import it explicitly when you need it:

    from airheater.interfaces.nidaq import NiDaqIO
"""

from .base import AnalogIO
from .simulated import SimulatedIO

__all__ = ["AnalogIO", "SimulatedIO"]
