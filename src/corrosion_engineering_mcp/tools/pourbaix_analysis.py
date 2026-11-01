"""Public Pourbaix analysis helpers backed by the table-driven engine."""

from __future__ import annotations

from typing import NoReturn

from corrosion_engineering_mcp.tools.pourbaix.contract import PourbaixPointInput
from corrosion_engineering_mcp.tools.pourbaix.engine import compute_stable_phase, generate_pourbaix_diagram


def assess_pourbaix_point(params: PourbaixPointInput) -> NoReturn:
    """Fail closed because point phase/passivity selection is suppressed."""

    return compute_stable_phase(
        element=params.element,
        pH=params.pH,
        E_VSHE=params.E_VSHE,
        T_C=params.T_C,
        activity_M2plus=params.activity_M2plus,
        activity_OHminus=params.activity_OHminus,
    )


__all__ = ["assess_pourbaix_point", "generate_pourbaix_diagram"]
