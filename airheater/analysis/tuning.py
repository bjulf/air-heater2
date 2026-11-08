"""PI/PID tuning rules for the air heater.

Two rules, both computed analytically from the model:

``ziegler_nichols``
    The frequency response (ultimate gain) method the task asks for.

``skogestad_simc``
    The SIMC rule for a 1. order process with delay, which is what the course
    material's values came from.

Requires numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ..simulation.model import AirHeaterParams
from .frequency_response import _find_crossing, process_frequency_response

__all__ = ["Tuning", "ultimate_point", "ziegler_nichols", "skogestad_simc"]


@dataclass
class Tuning:
    """A controller tuning."""

    Kp: float
    Ti: Optional[float] = None
    Td: float = 0.0
    method: str = ""

    def __str__(self) -> str:
        parts = ["Kp = {0:.3f}".format(self.Kp)]
        if self.Ti is not None:
            parts.append("Ti = {0:.2f} s".format(self.Ti))
        if self.Td:
            parts.append("Td = {0:.2f} s".format(self.Td))
        label = " ({0})".format(self.method) if self.method else ""
        return ", ".join(parts) + label


def ultimate_point(params: Optional[AirHeaterParams] = None) -> Tuple[float, float]:
    """Ultimate gain Ku and ultimate period Pu [s] of the process.

    The point where pure proportional control makes the loop oscillate with
    constant amplitude: the phase of Hp(jw) reaches -180 deg, and Ku is the
    gain that puts the magnitude at 1 there.
    """
    p = params or AirHeaterParams()

    # Phase of Hp(jw) is -atan(w*theta_t) - w*theta_d; no wrapping to worry
    # about, so solve it directly.
    def phase_plus_pi(w):
        return -np.arctan(w * p.theta_t) - w * p.theta_d + math.pi

    wu = _find_crossing(phase_plus_pi)
    if wu is None:
        raise ValueError("process phase never reaches -180 deg")
    Ku = 1.0 / abs(process_frequency_response(np.array([wu]), p)[0])
    return float(Ku), 2.0 * math.pi / wu


def ziegler_nichols(
    params: Optional[AirHeaterParams] = None, controller: str = "PI"
) -> Tuning:
    """Ziegler-Nichols tuning from the frequency response method.

    Finds the ultimate gain Ku and ultimate period Pu of the process under
    pure proportional control - the point where the loop oscillates with
    constant amplitude - and applies the classic ZN table.

    Note this is found analytically from the model, not by driving the real
    process to the stability limit. Do not run the ZN experiment on the rig
    just to find Ku: sustained oscillation of a heater at its limits is not
    something to provoke deliberately when the model already gives you Ku.

    Parameters
    ----------
    controller:
        ``"P"``, ``"PI"`` or ``"PID"``.
    """
    Ku, Pu = ultimate_point(params)

    kind = controller.upper()
    if kind == "P":
        return Tuning(Kp=0.5 * Ku, method="Ziegler-Nichols P")
    if kind == "PI":
        return Tuning(Kp=0.45 * Ku, Ti=Pu / 1.2, method="Ziegler-Nichols PI")
    if kind == "PID":
        return Tuning(Kp=0.6 * Ku, Ti=Pu / 2.0, Td=Pu / 8.0,
                      method="Ziegler-Nichols PID")
    raise ValueError("controller must be 'P', 'PI' or 'PID', got {0!r}".format(controller))


def skogestad_simc(
    params: Optional[AirHeaterParams] = None, Tc: Optional[float] = None
) -> Tuning:
    """Skogestad SIMC tuning for a 1. order process with delay.

        Kp = theta_t / (K_h * (Tc + theta_d))
        Ti = min(theta_t, 4*(Tc + theta_d))

    Parameters
    ----------
    Tc:
        Desired closed loop time constant [s]. The standard choice is
        ``Tc = theta_d``, which is the default and gives a fast but still
        robust loop. Increase it for a gentler, more robust response.
    """
    p = params or AirHeaterParams()
    Tc = p.theta_d if Tc is None else float(Tc)
    if Tc + p.theta_d <= 0.0:
        raise ValueError("Tc + theta_d must be > 0")
    Kp = p.theta_t / (p.K_h * (Tc + p.theta_d))
    Ti = min(p.theta_t, 4.0 * (Tc + p.theta_d))
    return Tuning(Kp=Kp, Ti=Ti, method="Skogestad SIMC (Tc = {0:.1f} s)".format(Tc))
