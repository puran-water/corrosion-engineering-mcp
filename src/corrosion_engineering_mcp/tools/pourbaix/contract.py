"""Input contract for the suppressed Pourbaix point-classification accessor."""

from __future__ import annotations

from pydantic import ConfigDict, Field
from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel

class PourbaixPointInput(StrictFiniteBaseModel):
    """Legacy point request retained for the typed suppression accessor."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    element: str = Field(..., description="Element symbol, e.g. Fe or Cu")
    pH: float = Field(..., description="Solution pH")
    E_VSHE: float = Field(..., description="Potential vs SHE, V")
    T_C: float = Field(default=25.0, description="Temperature, degC")
    activity_M2plus: float = Field(
        default=1.0e-6,
        gt=0.0,
        description="Activity of representative dissolved metal cation",
    )
    activity_OHminus: float = Field(
        default=1.0e-7,
        gt=0.0,
        description="Activity of hydroxide ion used for alkaline extrapolation notes",
    )
