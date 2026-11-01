'Python-library bridge from the R2.4 stream adapter to R2.1/R2.3 screens.'

from __future__ import annotations

from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError


API_SURFACE = "python_library"


class StreamGapsBlockAssessmentError(CorrosionPublicError, ValueError):
    """The stream does not carry species the requested screen needs."""

    def __init__(self, screen: str, gaps: List[str]) -> None:
        super().__init__(
            "STREAM_GAPS_BLOCK_ASSESSMENT",
            f"STREAM_GAPS_BLOCK_ASSESSMENT: {screen} needs {', '.join(gaps)}, "
            f"which this stream does not report. They are UNKNOWN, not zero - "
            f"supply them explicitly or measure them. This converter will not "
            f"substitute a default.",
            details={
                "reason_code": "STREAM_GAPS_BLOCK_ASSESSMENT",
                "screen": screen,
                "gaps": gaps,
            },
        )


def _value(rec, name: str) -> Optional[float]:
    entry = rec.species.get(name) or {}
    return entry.get("value")


def screen_sulfide_from_stream(
    state: Any,
    material: str,
    pH: float,
    temperature_C: float = 25.0,
    dose_overlay: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """R2.4 -> R2.3. Sulfide screen driven by a StreamState.

    `total_sulfide` is passed as a TOTAL, which is the identity the adapter
    established: mADM1 `S_IS` is total inorganic sulfur, and splitting it needs
    the pH the screen already has.
    """
    from puran_mcp_support.engineering.converters.stream_to_corrosion import stream_state_to_corrosion_water

    from corrosion_engineering_mcp.tools.mechanistic.sulfide_exposure import screen_sulfide_exposure

    rec = stream_state_to_corrosion_water(state, dose_overlay=dose_overlay)
    gaps = rec.gaps(["total_sulfide"])
    if gaps:
        raise StreamGapsBlockAssessmentError("corrosion_screen_sulfide_exposure", gaps)

    return screen_sulfide_exposure(
        material=material,
        pH=pH,
        temperature_C=temperature_C,
        total_sulfide_mg_L=_value(rec, "total_sulfide"),
        Cl_mg_L=_value(rec, "Cl-"),
        SO4_mg_L=_value(rec, "SO4-2"),
        dissolved_Fe_mg_L=_value(rec, "Fe2+"),
    )


def assess_oxidant_from_stream(
    state: Any,
    material: str,
    pH: float,
    oxidant: str = "free_chlorine",
    temperature_C: float = 25.0,
    service_context: Optional[str] = None,
    dose_overlay: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """R2.4 -> R2.1. Oxidant screen driven by a StreamState plus its dose overlay.

    Free chlorine is never carried by a stream vocabulary, so it arrives only
    through `dose_overlay`. Without one the adapter marks it
    `not_reported_dose_only`, `gaps()` reports it, and this refuses — which is the
    behaviour that stops an undosed stream reading as a zero residual. When the
    caller selects another primary oxidant, a reported NaOCl residual is still
    forwarded as a concurrent physical exposure; the downstream accessor keeps
    both lenses and aggregates any decisive house-rule verdict.
    """
    from puran_mcp_support.engineering.converters.stream_to_corrosion import stream_state_to_corrosion_water

    from corrosion_engineering_mcp.tools.mechanistic.oxidant_exposure import assess_oxidant_exposure

    rec = stream_state_to_corrosion_water(state, dose_overlay=dose_overlay)
    required = ["free_chlorine"] if oxidant == "free_chlorine" else []
    gaps = rec.gaps(required)
    if gaps:
        raise StreamGapsBlockAssessmentError("corrosion_assess_oxidant_exposure", gaps)

    return assess_oxidant_exposure(
        material=material,
        oxidant=oxidant,
        pH=pH,
        temperature_C=temperature_C,
        free_chlorine_mg_L_as_Cl2=_value(rec, "free_chlorine"),
        Cl_mg_L=_value(rec, "Cl-"),
        service_context=service_context,
    )


__all__ = [
    "API_SURFACE",
    "StreamGapsBlockAssessmentError",
    "assess_oxidant_from_stream",
    "screen_sulfide_from_stream",
]
