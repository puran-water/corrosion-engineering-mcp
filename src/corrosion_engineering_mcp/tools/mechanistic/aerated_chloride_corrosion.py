"""Withdrawn aerated-chloride endpoint.

The public predictor raises :class:`ToolWithdrawnError` before calculation. The
former ORR limiting-current table is quarantined, is not imported at module
scope, and cannot be read through the retained private helper. The obsolete
calculation body was removed so there is no dormant branch to re-enable by
moving or deleting a guard.
"""

from __future__ import annotations

from typing import Dict, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError


class ToolWithdrawnError(CorrosionPublicError, ValueError):
    """Raised on every call because the former model was not defensible."""

    def __init__(self) -> None:
        super().__init__(
            "TOOL_WITHDRAWN",
            "TOOL_WITHDRAWN: corrosion_predict_aerated_chloride is disabled. It "
            "under-reported by ~1000x (units error in the Faraday conversion) on "
            "top of an ORR limiting-current table ~24x high, and classified every "
            "realistic input - including seawater - as 'Very low, carbon steel "
            "acceptable for long-term service'. It also silently treated "
            "unrecognised materials as carbon steel. No substitute is available "
            "on this server; assess uniform aqueous corrosion by hand until the "
            "model is corrected and validated.",
            details={
                "reason_code": "TOOL_WITHDRAWN",
                "faraday_factor_error": "1000x low",
                "orr_table_error": "~24x high",
                "why_not_a_units_only_fix": (
                    "the two errors partially cancel; correcting units alone gives "
                    "5.56 mm/y for seawater against ~0.227 mm/y with both corrected"
                ),
                "requires_to_reenable": [
                    "correct Faraday conversion and ORR limiting current atomically",
                    "accept velocity, hydraulic diameter, flow regime and roughness",
                    "validate i_lim against DNV-RP-B401 CP design current densities",
                    "remove the silent coercion of unknown materials to carbon steel",
                ],
            },
        )


# Retained for import compatibility only; no calculation uses these constants.
FARADAY = 96485.0
STEEL_DENSITY = 7850.0
FE_MW = 55.845


def predict_aerated_chloride_corrosion(
    temperature_C: float,
    chloride_mg_L: float = 0.0,
    dissolved_oxygen_mg_L: Optional[float] = None,
    pH: float = 7.0,
    material: str = "carbon_steel",
) -> Dict:
    """Fail closed for every input; the former calculation is withdrawn."""
    del temperature_C, chloride_mg_L, dissolved_oxygen_mg_L, pH, material
    raise ToolWithdrawnError()


def _get_orr_limit_from_csv(temperature_C: float, chloride_mg_L: float) -> float:
    """Route the retired helper through the same enforced data quarantine.

    The import is intentionally local: importing this withdrawn module must not
    load or expose the ORR table. Every input raises while the source remains
    quarantined.
    """
    from corrosion_engineering_mcp.data.authoritative_materials_data import get_orr_diffusion_limit

    electrolyte = "seawater" if chloride_mg_L > 10000 else "freshwater"
    return get_orr_diffusion_limit(electrolyte, temperature_C)


__all__ = ["ToolWithdrawnError", "predict_aerated_chloride_corrosion"]
