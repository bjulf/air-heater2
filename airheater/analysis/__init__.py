"""Frequency response, stability margins, tuning rules and the pole map.

Everything here is offline analysis and uses numpy. The control application
does not import it.

    from airheater.analysis import margins, ziegler_nichols
    print(margins(Kp=0.52, Ti=18.0))
"""

from .frequency_response import (
    Margins,
    controller_frequency_response,
    critical_gain,
    loop_frequency_response,
    loop_phase,
    margins,
    process_frequency_response,
)
from .pade import closed_loop_poles, pade
from .tuning import Tuning, skogestad_simc, ultimate_point, ziegler_nichols

__all__ = [
    "Margins",
    "Tuning",
    "process_frequency_response",
    "controller_frequency_response",
    "loop_frequency_response",
    "loop_phase",
    "margins",
    "critical_gain",
    "ultimate_point",
    "ziegler_nichols",
    "skogestad_simc",
    "pade",
    "closed_loop_poles",
]
