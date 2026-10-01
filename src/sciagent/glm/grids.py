"""Fixed ψ grids (SPEC §2.2): the only values a shape parameter can take.

Times are in mean-rate-1 units (see ``data.py``). Mark-function parameters act
on the standardised mark ``z = (m - location) / scale`` of the channel's
:class:`~sciagent.glm.grammar.ChannelSpec`, except ``pow_exponent``, which acts
on ``m / location``. Every grid is sorted and finite.

These are v2.0 grids. Changing one changes the hypothesis space, so it is a
LOG.md decision and part of the preregistration freeze (SPEC §6.1).
"""

from __future__ import annotations

import math
from typing import Final

PSI_GRIDS: Final[dict[str, tuple[float, ...]]] = {
    # ExpK: β e^{-βt}; mean lag 1/β from ~0.1 to ~4 inter-event times.
    "exp_rate": (0.25, 0.5, 1.0, 2.0, 4.0, 8.0),
    # PowerK (Lomax): ((p-1)/c)(1 + t/c)^{-p}.
    "power_c": (0.05, 0.2, 1.0),
    "power_p": (1.2, 1.5, 2.0, 3.0),
    # GammaK: shape k, mean μ.
    "gamma_shape": (2.0, 3.0, 5.0),
    "gamma_mean": (0.5, 1.0, 2.0, 4.0),
    # Mark functions.
    "pow_exponent": (0.5, 1.0, 1.5, 2.0),
    "exp_coef": (0.5, 1.0, 1.5, 2.0, 2.5),
    "above_z": (0.0, 0.5, 1.0, 1.5),
    # Periodic and PhaseWindow.
    "period": (5.0, 10.0, 25.0, 50.0, 100.0),
    "phase": (0.0, 0.5 * math.pi, math.pi, 1.5 * math.pi),
}


def grid(name: str) -> tuple[float, ...]:
    """The grid for ψ parameter ``name``; KeyError-free lookup with a typed error."""
    from sciagent.glm.grammar import InvalidStructureError

    try:
        return PSI_GRIDS[name]
    except KeyError:
        raise InvalidStructureError(f"no ψ grid named {name!r}") from None
