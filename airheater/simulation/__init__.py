"""The process model.

``model`` is the per-sample simulator the control application drives, and
depends on the standard library only. ``batch`` is the vectorised version for
offline runs and tuning sweeps, and needs numpy - so it is imported on demand
rather than here.

    from airheater.simulation import AirHeaterSim, AirHeaterParams
    from airheater.simulation.batch import simulate_closed_loop
"""

from .model import AirHeaterParams, AirHeaterSim

__all__ = ["AirHeaterParams", "AirHeaterSim"]
