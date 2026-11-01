"""Fail-closed public facade for the withdrawn galvanic predictor.

The former facade converted an unvalidated current ratio into categorical
"amplification" bands, recommendations, and a claimed NRL validation dataset.
Those outputs are removed: the underlying solver reverses the cathode/anode
area-ratio trend and does not preserve electrode polarity across pH 6--9.
"""

from __future__ import annotations

from typing import NoReturn

from corrosion_engineering_mcp.core.schemas import AssessGalvanicInput
from corrosion_engineering_mcp.tools.mechanistic.predict_galvanic_corrosion import predict_galvanic_corrosion


AssessGalvanicResult = NoReturn


def assess(input: AssessGalvanicInput) -> NoReturn:
    """Reject every assessment; no numerical or categorical result is licensed."""

    params = (
        input
        if isinstance(input, AssessGalvanicInput)
        else AssessGalvanicInput.model_validate(input)
    )
    predict_galvanic_corrosion(
        anode_material=params.anode_material,
        cathode_material=params.cathode_material,
        temperature_C=params.temperature_C,
        pH=params.pH,
        chloride_mg_L=params.chloride_mg_L,
        area_ratio_cathode_to_anode=params.area_ratio_cathode_to_anode,
        velocity_m_s=params.velocity_m_s,
        dissolved_oxygen_mg_L=params.dissolved_oxygen_mg_L,
        allow_auto_orient=params.allow_auto_orient,
        sulfide_mg_L=params.sulfide_mg_L,
    )
    raise AssertionError("withdrawn predictor returned unexpectedly")  # pragma: no cover


__all__ = ["AssessGalvanicResult", "assess"]
