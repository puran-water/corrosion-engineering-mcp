"""Localized-corrosion facade with unsupported service outputs suppressed.

The internal backend is retained for research and future validation.  The
registered facade exposes only PREN as a relative composition index and the
separately sourced electrochemical quantities.  It does not expose CPT, CCT,
chloride limits, crevice calculations, risk ranks, suitability verdicts, or
mitigation recommendations because those backend outputs lack a primary-source
service model.
"""

import math
from copy import deepcopy
from numbers import Real
from typing import Dict, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.core.localized_backend import DEFAULT_PREN_FORMULA_VARIANT, PREN_FORMULA_VARIANTS, PRENCompositionInvalidError, LocalizedBackend, MaterialComposition, get_pren_formula_basis
from corrosion_engineering_mcp.utils.redox_state import REDOX_EQUILIBRIUM_CITATION_RECORDS, REDOX_EQUILIBRIUM_SOURCE_DOMAIN, REDOX_TYPED_NULL_STATUS_PRECEDENCE, redox_calculation_statuses


class UnsupportedPRENFormulaVariantError(CorrosionPublicError, ValueError):
    """A caller requested an unnamed/unproven PREN equation."""

    def __init__(self, formula_variant: str) -> None:
        choices = list(PREN_FORMULA_VARIANTS)
        super().__init__(
            "UNSUPPORTED_PREN_FORMULA_VARIANT",
            f"UNSUPPORTED_PREN_FORMULA_VARIANT: {formula_variant!r} is not a "
            f"named sourced convention. Choose one of: {', '.join(choices)}.",
            details={
                "reason_code": "UNSUPPORTED_PREN_FORMULA_VARIANT",
                "formula_variant": formula_variant,
                "choices": choices,
            },
        )


def _detect_tier_disagreement(*_args, **_kwargs):
    """REMOVED. Retained as a stub only so any stale import fails loudly.

    This previously compared Tier 1 and Tier 2 risk bands and, on disagreement,
    told the caller to "Trust Tier 2 (mechanistic)". Tier 2 no longer produces a
    risk band — absolute E_pit is suppressed as unvalidated — so the comparison
    cannot fire, and the guidance it emitted pointed at the very output that was
    withdrawn. Leaving a callable that returns that string is a live hazard even
    when nothing calls it.
    """
    raise NotImplementedError(
        "TIER_DISAGREEMENT_REMOVED: Tier 2 reports no risk verdict, so no tier "
        "comparison exists. The former 'trust Tier 2 on disagreement' guidance is "
        "withdrawn - it directed callers to a suppressed output."
    )


@enforce_tool_input_contract("corrosion_assess_localized")
def calculate_localized_corrosion(
    material: str,
    temperature_C: float,
    Cl_mg_L: float,
    pH: float = 7.0,
    crevice_gap_mm: float = 0.1,
    water_chemistry_json: Optional[str] = None,
    dissolved_oxygen_mg_L: Optional[float] = None,
) -> Dict:
    """Return the defensible subset of the localized-corrosion calculation.

    Args:
        material: Material name (e.g., "316L", "2205", "254SMO")
        temperature_C: Operating temperature (°C)
        Cl_mg_L: Chloride concentration (mg/L)
        pH: Solution pH (default 7.0)
        crevice_gap_mm: Crevice gap width in mm (default 0.1 mm)
        water_chemistry_json: Accepted for API compatibility; it does not enable
            additional outputs.
        dissolved_oxygen_mg_L: Dissolved oxygen concentration (mg/L). If provided,
            enables the sourced equilibrium redox potential for every material
            when oxygen activity is defined and temperature is inside the
            verified 0-40 C freshwater source domain. Outside that domain only
            the redox quantity is a typed null; the public 0-150 C temperature
            schema and other retained outputs remain available. The chloride-
            response slope remains material-scoped. Absolute E_pit remains
            suppressed.

    Returns:
        Dictionary containing the PREN relative index, optional sourced
        electrochemical quantities, explicit suppression standing, and no
        service verdict or recommendation.

    Example:
        >>> result = calculate_localized_corrosion(
        ...     material="316L",
        ...     temperature_C=60.0,
        ...     Cl_mg_L=500.0,
        ...     pH=6.5,
        ...     crevice_gap_mm=0.1
        ... )
        >>> result["pitting"]["PREN"]
        23.9
        >>> result["assessment_status"]
        'suppressed_unsupported_service_assessment'

    Raises:
        ValueError: If invalid material or parameters
    """
    # Validate inputs
    validate_float_domain_inputs(
        {
            "temperature_C": temperature_C,
            "Cl_mg_L": Cl_mg_L,
            "pH": pH,
            "crevice_gap_mm": crevice_gap_mm,
            "dissolved_oxygen_mg_L": dissolved_oxygen_mg_L,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    if temperature_C < 0 or temperature_C > 150:
        raise ValueError(f"Temperature {temperature_C}°C out of range (0-150°C)")

    if Cl_mg_L < 0:
        raise ValueError(f"Chloride concentration {Cl_mg_L} mg/L must be positive")

    if pH < 0 or pH > 14:
        raise ValueError(f"pH {pH} out of range (0-14)")

    if crevice_gap_mm <= 0 or crevice_gap_mm > 10:
        raise ValueError(f"Crevice gap {crevice_gap_mm} mm out of range (0-10 mm)")

    # Run localized corrosion calculation
    backend = LocalizedBackend()
    result = backend._calculate_localized_corrosion_research(
        material=material,
        temperature_C=temperature_C,
        Cl_mg_L=Cl_mg_L,
        pH=pH,
        crevice_gap_mm=crevice_gap_mm,
        dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
    )

    redox_warnings = list(result.pitting.E_redox_equilibrium_warnings or [])
    redox_statuses = redox_calculation_statuses(
        result.pitting.E_redox_equilibrium_VSCE,
        redox_warnings,
    )
    # Preserve every established single-status payload bit-for-bit. The additive
    # contract appears only where multiple typed-null conditions simultaneously
    # apply and the former scalar status necessarily hid one of them.
    compound_redox_status_contract = (
        {
            "E_redox_equilibrium_statuses": redox_statuses,
            "E_redox_equilibrium_status_precedence": list(
                REDOX_TYPED_NULL_STATUS_PRECEDENCE
            ),
        }
        if len(redox_statuses) > 1
        else {}
    )

    # The backend's CPT/CCT, chloride limit, temperature margins, crevice IR
    # drop, acidification factor and 1-4 ranks are internal screening rules, not
    # sourced service limits.  Suppression must remove the values themselves;
    # nesting them under ``screening_only`` merely left every number reachable.
    # Retain only the composition index and separately sourced electrochemical
    # quantities.  No service suitability or mitigation decision is made.
    return {
        "pitting": {
            "PREN": round(result.pitting.PREN, 1),
            "PREN_unrounded": result.pitting.PREN,
            "PREN_value_standing": "unrounded_authoritative",
            "PREN_scope": "relative_composition_index_only",
            "PREN_formula_basis": get_pren_formula_basis(),
            "E_pit_VSCE": None,
            "E_pit_status": result.pitting.E_pit_status,
            "E_redox_equilibrium_VSCE": result.pitting.E_redox_equilibrium_VSCE,
            "E_redox_equilibrium_VSCE_display": (
                round(result.pitting.E_redox_equilibrium_VSCE, 3)
                if result.pitting.E_redox_equilibrium_VSCE is not None
                else None
            ),
            "E_redox_equilibrium_VSCE_value_standing": (
                "exact_primary_authoritative_display_field_rounded"
                if result.pitting.E_redox_equilibrium_VSCE is not None
                else None
            ),
            "E_redox_equilibrium_status": (
                result.pitting.E_redox_equilibrium_status
            ),
            "E_redox_equilibrium_warnings": redox_warnings,
            **compound_redox_status_contract,
            "E_redox_equilibrium_source_domain": deepcopy(
                REDOX_EQUILIBRIUM_SOURCE_DOMAIN
            ),
            "E_redox_equilibrium_citation_records": deepcopy(
                list(REDOX_EQUILIBRIUM_CITATION_RECORDS)
            ),
            "E_redox_detection_basis_mg_L": (
                result.pitting.E_redox_detection_basis_mg_L
            ),
            "chloride_response_mV_per_decade": (
                float(result.pitting.chloride_response_mV_per_decade)
                if result.pitting.chloride_response_mV_per_decade is not None
                else None
            ),
            "chloride_response_scope": dict(
                result.pitting.chloride_response_scope or {}
            ),
            "electrochemical_margin_V": None,
            "electrochemical_risk": None,
        },
        "crevice": {
            "assessment_status": "suppressed_no_sourced_service_model",
        },
        "material": result.material,
        "temperature_C": temperature_C,
        "Cl_mg_L": Cl_mg_L,
        "pH": pH,
        "crevice_gap_mm": crevice_gap_mm,
        "crevice_gap_standing": (
            "reported_context_only_crevice_model_suppressed"
        ),
        "dissolved_oxygen_mg_L": dissolved_oxygen_mg_L,
        "assessment_status": "suppressed_unsupported_service_assessment",
        "assessment_caveats": list(result.assessment_caveats or []),
        "recommendations": [],
        "suppressed_outputs": [
            "CPT_C",
            "CCT_C",
            "chloride_service_threshold",
            "temperature_margins",
            "crevice_IR_drop",
            "acidification_factor",
            "all_service_ranks_and_verdicts",
            "material_suitability",
            "mitigation_recommendations",
        ],
        "retained_outputs": [
            "PREN_relative_composition_index",
            "named_PREN_formula_basis",
            "equilibrium_redox_potential_when_DO_above_detection_basis",
            "typed_redox_null_status_when_DO_at_or_below_detection_basis",
            "typed_redox_null_status_outside_source_temperature_domain",
            "redox_source_domain_and_citation_records",
            "sourced_chloride_response_slope",
        ],
    }


@enforce_tool_input_contract("corrosion_calculate_pren")
def calculate_pren(
    Cr_wt_pct: float,
    Mo_wt_pct: float,
    N_wt_pct: float,
    Mn_wt_pct: Optional[float] = None,
    grade_type: str = "austenitic",
    composition_basis: str = "bulk_alloy",
    formula_variant: str = DEFAULT_PREN_FORMULA_VARIANT,
) -> Dict:
    """
    Calculate PREN (Pitting Resistance Equivalent Number) from composition.

    Utility function for custom alloys or verification.

    Args:
        Cr_wt_pct: Chromium content (wt%)
        Mo_wt_pct: Molybdenum content (wt%)
        N_wt_pct: Nitrogen content (wt%)
        Mn_wt_pct: Manganese content (wt%). Required by the explicit Liu
            austenite-phase equation; not used by the ASTM default.
        grade_type: "austenitic", "duplex", or "superaustenitic"
        composition_basis: ``bulk_alloy`` (default) or ``austenite_phase``.
        formula_variant: ``ASTM_A511_PREN16`` (default) or the explicitly
            selected duplex-only ``LIU_2020_DUPLEX_PREN30`` literature convention.

    Returns:
        Dictionary with the PREN composition index, its exact formula basis and
        validation standing.  No CPT is inferred from PREN.

    Example:
        >>> result = calculate_pren(Cr_wt_pct=16.5, Mo_wt_pct=2.0, N_wt_pct=0.05)
        >>> print(result["PREN"])
        23.9

    Default named formula (all grade labels):
        ASTM_A511_PREN16 = %Cr + 3.3×%Mo + 16×%N

    Explicit duplex-literature variant:
        LIU_2020_DUPLEX_PREN30 evaluates the cited austenite-phase alternative:
        PREN_gamma30 = Cr_gamma + 3.3×Mo_gamma + 30×N_gamma - Mn_gamma.

        Liu et al. say 16 or 30 is usually used for austenite and use 16 in
        their own work. The explicit 30 variant is available only for a duplex
        grade with a declared austenite-phase composition including Mn; it is a
        relative-index convention, not a recommendation or service model.

    PREN is a relative composition index, not a critical-pitting-temperature
    measurement or a service suitability verdict.
    """
    # The exported Python accessor must fail closed just like the Pydantic MCP
    # boundary. NaN otherwise bypasses every ordinary range comparison.
    validate_float_domain_inputs(
        {
            "Cr_wt_pct": Cr_wt_pct,
            "Mo_wt_pct": Mo_wt_pct,
            "N_wt_pct": N_wt_pct,
            "Mn_wt_pct": Mn_wt_pct,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    for field_name, value in (
        ("Cr_wt_pct", Cr_wt_pct),
        ("Mo_wt_pct", Mo_wt_pct),
        ("N_wt_pct", N_wt_pct),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise PRENCompositionInvalidError(field_name, value)
    if Mn_wt_pct is not None and (
        isinstance(Mn_wt_pct, bool)
        or not isinstance(Mn_wt_pct, Real)
        or not math.isfinite(float(Mn_wt_pct))
    ):
        raise PRENCompositionInvalidError("Mn_wt_pct", Mn_wt_pct)

    # Validate ranges after finiteness so invalid floats get one typed route.
    if Cr_wt_pct < 0 or Cr_wt_pct > 30:
        raise ValueError(f"Cr content {Cr_wt_pct}% out of range (0-30%)")

    if Mo_wt_pct < 0 or Mo_wt_pct > 10:
        raise ValueError(f"Mo content {Mo_wt_pct}% out of range (0-10%)")

    if N_wt_pct < 0 or N_wt_pct > 1:
        raise ValueError(f"N content {N_wt_pct}% out of range (0-1%)")

    if Mn_wt_pct is not None and (Mn_wt_pct < 0 or Mn_wt_pct > 20):
        raise ValueError(f"Mn content {Mn_wt_pct}% out of range (0-20%)")

    if grade_type not in {"austenitic", "duplex", "superaustenitic"}:
        raise ValueError("Unsupported grade_type")

    if formula_variant not in PREN_FORMULA_VARIANTS:
        raise UnsupportedPRENFormulaVariantError(formula_variant)

    # Create material composition
    comp = MaterialComposition(
        Cr=Cr_wt_pct,
        Mo=Mo_wt_pct,
        N=N_wt_pct,
        grade_type=grade_type,
        Mn=Mn_wt_pct,
        composition_basis=composition_basis,
    )

    # Calculate PREN
    pren = comp.calculate_pren(formula_variant=formula_variant)

    return {
        "PREN": round(pren, 1),
        "PREN_unrounded": pren,
        "PREN_value_standing": "unrounded_authoritative",
        "grade_type": grade_type,
        "composition": {
            "Cr_wt_pct": Cr_wt_pct,
            "Mo_wt_pct": Mo_wt_pct,
            "N_wt_pct": N_wt_pct,
            "Mn_wt_pct": Mn_wt_pct,
        },
        "composition_basis": composition_basis,
        "formula_basis": get_pren_formula_basis(formula_variant),
        "validation_standing": {
            "PREN": "composition_index_only",
            "CPT": "suppressed_no_primary_source",
            "service_verdict": "not_supported",
        },
    }
