"""Presentation helpers for saturation indices.

Classification remains a function of the unrounded thermodynamic value.  A
display value that rounds to zero is normalised to positive zero so JSON callers
do not receive accessor-dependent ``-0.0`` spellings.
"""

from __future__ import annotations

from typing import Optional


def format_nonzero_fixed(value: float, digits: int) -> str:
    """Format ordinary values fixed-width without spelling nonzero as zero.

    Fixed precision remains useful for prose, but it is not allowed to erase a
    nonzero diagnostic. Values below that fixed-point resolution switch to
    scientific notation. Callers must still expose the exact numeric value in
    a structured field; this helper is presentation-only.
    """

    displayed = format(value, f".{digits}f")
    if value != 0.0 and float(displayed) == 0.0:
        return format(value, ".3e")
    return displayed


def rounded_index_for_display(
    value: Optional[float], digits: int = 3
) -> Optional[float]:
    """Round an index and normalise either spelling of zero to ``0.0``."""

    if value is None:
        return None
    displayed = round(value, digits)
    return 0.0 if displayed == 0.0 else displayed


def classify_lsi(
    value: Optional[float],
    *,
    no_root_sign_classification: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """Return (sign, saturation state) from the unrounded LSI value."""

    if value is None:
        if no_root_sign_classification == "all_positive":
            return None, "supersaturated_no_saturation_ph_root"
        if no_root_sign_classification == "all_negative":
            return None, "undersaturated_no_saturation_ph_root"
        if no_root_sign_classification == "mixed_or_indeterminate":
            return None, "indeterminate_no_saturation_ph_root_mixed_si_signs"
        if no_root_sign_classification is None:
            return None, "indeterminate_no_saturation_ph_root_sign_unavailable"
        raise ValueError(
            "Unknown calcite no-root sign classification: "
            f"{no_root_sign_classification!r}"
        )
    if value > 0.0:
        return "positive", "supersaturated"
    if value < 0.0:
        return "negative", "undersaturated"
    return "zero", "saturated"
