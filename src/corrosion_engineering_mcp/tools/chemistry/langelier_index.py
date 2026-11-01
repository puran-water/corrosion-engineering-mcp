"""
Tier 1 Tool: Calculate Langelier Saturation Index (LSI)

Simplified wrapper for LSI-only calculation.
LSI = pH - pH_s, where pH_s is the pH at calcite saturation.

Interpretation:
- LSI > 0: Supersaturated with respect to CaCO₃
- LSI = 0: Saturated with respect to CaCO₃
- LSI < 0: Undersaturated with respect to CaCO₃

LSI is not a metal-corrosion index and this accessor emits no metal-corrosion
verdict or chemical-dosing recommendation.

Common in cooling tower, boiler, and potable water treatment.

Runtime is dominated by the configured PHREEQC backend.
"""

from typing import Dict, Optional
import logging

from corrosion_engineering_mcp.core.chemistry_backend import CalciteSaturationPHUnavailableError, PHREEQCBackend, parse_ions_json, require_saturation_index, validate_water_chemistry
from corrosion_engineering_mcp.core.errors import validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils.index_presentation import classify_lsi, format_nonzero_fixed, rounded_index_for_display

logger = logging.getLogger(__name__)


@enforce_tool_input_contract("corrosion_langelier_index")
def calculate_langelier_index(
    ions_json: str,
    temperature_C: float = 25.0,
    pH: Optional[float] = None,
    validate_charge_balance: bool = True,
    max_imbalance: float = 5.0,
) -> Dict:
    """
    Calculate Langelier Saturation Index (LSI) for calcium carbonate scaling.

    The LSI is the most widely used index for predicting CaCO₃ scaling
    tendency in water systems. It is defined as:

        LSI = pH - pH_s

    where pH_s is the pH at which water is saturated with calcium carbonate.

    Args:
        ions_json: JSON string of ion concentrations in mg/L
                   Must include at least: Ca2+, HCO3- (or CO3-2)
                   Example: '{"Ca2+": 80, "Mg2+": 20, "HCO3-": 180, "Cl-": 100, "SO4-2": 60, "Na+": 31.8}'
        temperature_C: Water temperature in degrees Celsius (default 25.0)
        pH: Reported pH; required for this calculation
        validate_charge_balance: Check charge balance before running (default True)
        max_imbalance: Maximum acceptable charge imbalance in % (default 5.0)

    Returns:
        Dictionary containing:
        - lsi: Langelier Saturation Index
        - pH: Reported pH used in the calculation
        - pH_saturation: pH at calcite saturation (pH_s)
        - si_calcite: Saturation index for calcite mineral
        - temperature_C: Temperature used in calculation
        - interpretation: Text summary
        - saturation_state: Machine-readable CaCO3 saturation state
        - charge_balance_valid: Whether the submitted analysis passed validation

    Example:
        >>> result = calculate_langelier_index(
        ...     ions_json='{"Ca2+": 80, "Mg2+": 20, "HCO3-": 180, "Cl-": 100, "SO4-2": 60, "Na+": 31.8}',
        ...     temperature_C=25.0,
        ...     pH=7.5
        ... )
        >>> result["charge_balance_valid"]
        True
        >>> result["saturation_state"]
        'supersaturated'

    LSI Interpretation Guide:
        Positive: supersaturated with respect to CaCO3
        Zero: saturated with respect to CaCO3
        Negative: undersaturated with respect to CaCO3

    Typical Applications:
        - Cooling tower water treatment
        - Boiler feedwater quality assessment
        - Potable-water CaCO3 deposition assessment
        - Reverse osmosis pretreatment
        - Heat exchanger fouling prediction

    Raises:
        ValueError: If charge imbalance exceeds max_imbalance or required ions missing
        RuntimeError: If PHREEQC calculation fails
    """
    validate_float_domain_inputs(
        {
            "temperature_C": temperature_C,
            "pH": pH,
            "max_imbalance": max_imbalance,
        }
    )
    ions = parse_ions_json(ions_json)

    # Check for required ions
    if ions.get("Ca2+", 0.0) <= 0.0:
        raise ValueError("LSI calculation requires a positive calcium (Ca2+) concentration")

    if ions.get("HCO3-", 0.0) <= 0.0 and ions.get("CO3-2", 0.0) <= 0.0:
        raise ValueError(
            "LSI calculation requires a positive bicarbonate (HCO3-) or carbonate (CO3-2) concentration"
        )
    if pH is None:
        raise ValueError("A reported pH is required for LSI calculation")

    # Validate charge balance if requested
    charge_balance_valid: Optional[bool] = None
    warnings: list[str] = []
    if validate_charge_balance:
        try:
            validate_water_chemistry(ions, max_imbalance=max_imbalance)
        except ValueError as e:
            logger.warning(f"Charge balance validation failed: {e}")
            charge_balance_valid = False
            warnings.append("CHARGE_BALANCE_FAILED")
        else:
            charge_balance_valid = True

    # Run speciation once, then solve the true fixed-alkalinity saturation pH.
    backend = PHREEQCBackend()
    speciation = backend.run_speciation(ions, temperature_C, pH)
    si_calcite = require_saturation_index(speciation, "Calcite")
    try:
        pH_s = backend.calculate_calcite_saturation_pH(
            ions=ions,
            temperature_C=temperature_C,
            reported_pH=speciation.pH,
            reported_speciation=speciation,
        )
    except CalciteSaturationPHUnavailableError as exc:
        pH_s = None
        lsi = None
        index_status = "not_defined_no_calcite_saturation_ph_root"
        warnings.append("CALCITE_SATURATION_PH_UNAVAILABLE")
        minimum_converged_si = exc.minimum_si
        maximum_converged_si = exc.maximum_si
        no_root_sign_classification = exc.converged_si_sign_classification
    else:
        lsi = speciation.pH - pH_s
        index_status = "computed_from_calcite_saturation_ph"
        minimum_converged_si = None
        maximum_converged_si = None
        no_root_sign_classification = None

    root_evidence = getattr(speciation, "calcite_root_evidence", None)
    if root_evidence and root_evidence["method"] == "scan_tolerance":
        warnings.append(root_evidence["qualification"])
    # Format output
    output = {
        "calcite_root_evidence": root_evidence,
        "lsi": rounded_index_for_display(lsi),
        "lsi_unrounded": lsi,
        "phreeqc_database": speciation.database,  
        "pH": round(speciation.pH, 3),
        "pH_unrounded": speciation.pH,
        "pH_status": "reported",
        "pH_saturation": round(pH_s, 3) if pH_s is not None else None,
        "pH_saturation_unrounded": pH_s,
        "si_calcite": round(si_calcite, 3),
        "si_calcite_unrounded": si_calcite,
        "index_value_standing": (
            "unrounded_values_authoritative_rounded_fields_display_only"
        ),
        "temperature_C": temperature_C,
        "index_scope": "calcium_carbonate_saturation_only",
        "index_status": index_status,
        "maximum_converged_si_calcite": maximum_converged_si,
        "maximum_converged_si_calcite_display": (
            round(maximum_converged_si, 6)
            if maximum_converged_si is not None
            else None
        ),
        "maximum_converged_si_calcite_value_standing": (
            "exact_primary_authoritative_display_field_rounded"
            if maximum_converged_si is not None
            else None
        ),
        "charge_balance_percent": speciation.charge_balance_percent,
        "charge_balance_percent_display": round(
            speciation.charge_balance_percent, 2
        ),
        "charge_balance_value_standing": (
            "exact_primary_authoritative_display_field_rounded"
        ),
        "charge_balance_valid": charge_balance_valid,
        "warnings": warnings,
    }

    # Report only the thermodynamic sign licensed by LSI = pH - pH_s.  The former
    # -2/-0.5/+0.5/+2 severity ladder had no source and is deliberately absent.
    # A negative LSI says that calcite is undersaturated; it cannot establish a
    # metal corrosion rate or support dosing.
    lsi_sign, saturation_state = classify_lsi(
        lsi,
        no_root_sign_classification=no_root_sign_classification,
    )
    if no_root_sign_classification == "all_positive":
        interpretation = (
            "Calcite is supersaturated at the reported state and at every "
            "converged fixed-alkalinity pH scan point; no saturation pH exists "
            "in the scanned range, so LSI and pH_s are undefined for this analysis"
        )
        output["minimum_converged_si_calcite"] = minimum_converged_si
        output["minimum_converged_si_calcite_display"] = round(
            minimum_converged_si, 6
        )
        output["minimum_converged_si_calcite_value_standing"] = (
            "exact_primary_authoritative_display_field_rounded"
        )
    elif no_root_sign_classification == "all_negative":
        interpretation = (
            "Calcite remained undersaturated at every converged fixed-alkalinity "
            "pH; LSI and pH_s are undefined for this analysis"
        )
    elif lsi is None:
        interpretation = (
            "Indeterminate calcite saturation classification: converged "
            "fixed-alkalinity scan points had mixed SI(Calcite) signs but no "
            "saturation root was resolved; LSI and pH_s are undefined."
        )
        output["minimum_converged_si_calcite"] = minimum_converged_si
        output["minimum_converged_si_calcite_display"] = round(
            minimum_converged_si, 6
        )
        output["minimum_converged_si_calcite_value_standing"] = (
            "exact_primary_authoritative_display_field_rounded"
        )
    elif lsi > 0.0:
        interpretation = (
            "Supersaturated with respect to calcite because LSI is positive "
            f"(+{format_nonzero_fixed(lsi, 3)})."
        )
    elif lsi < 0.0:
        interpretation = (
            "Undersaturated with respect to calcite because LSI is negative "
            f"({format_nonzero_fixed(lsi, 3)})."
        )
    else:
        interpretation = (
            "At saturation equilibrium with respect to calcite because LSI is "
            f"zero (+{format_nonzero_fixed(lsi, 3)})."
        )

    if charge_balance_valid is False:
        output["computed_saturation_state_not_used"] = saturation_state
        saturation_state = "indeterminate_failed_charge_balance"
        if lsi is None:
            interpretation = (
                "Indeterminate: the reported analysis failed charge balance; "
                "LSI and its sign are null because no calcite saturation-pH "
                "root was found."
            )
        else:
            interpretation = (
                "Indeterminate: the reported analysis failed charge balance; the "
                "computed LSI and its sign are visible but not interpreted."
            )

    output["lsi_sign"] = lsi_sign
    output["saturation_state"] = saturation_state
    output["interpretation"] = interpretation

    return output
