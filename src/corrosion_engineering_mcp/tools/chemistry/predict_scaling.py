"""
Tier 1 Tool: Predict Scaling Tendency

Reports four index slots whose scopes are deliberately kept separate:
- LSI (Langelier Saturation Index): CaCO₃ saturation state
- RSI (Ryznar Stability Index): CaCO₃ deposition tendency
- PSI (Puckorius Scaling Index): CaCO₃ deposition tendency; numeric value
  suppressed until the primary formula can be inspected
- Larson--Skold ratio: relative corrosivity of water to iron and steel

LSI, RSI and PSI do not predict metal loss. Larson--Skold is the only one of
the four that addresses metal corrosion, and it is not converted here into an
absolute corrosion rate, risk band or material verdict.

Runtime is dominated by the configured PHREEQC backend.
"""

from typing import Dict, Optional
import logging

from corrosion_engineering_mcp.core.chemistry_backend import PHREEQCBackend, parse_ions_json, validate_water_chemistry
from corrosion_engineering_mcp.core.errors import validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils.index_presentation import classify_lsi, rounded_index_for_display

logger = logging.getLogger(__name__)


@enforce_tool_input_contract("corrosion_predict_scaling")
def predict_scaling_tendency(
    ions_json: str,
    temperature_C: float = 25.0,
    pH: Optional[float] = None,
    validate_charge_balance: bool = True,
    max_imbalance: float = 5.0,
) -> Dict:
    """
    Predict CaCO₃ saturation/deposition tendency and calculate the
    Larson--Skold relative iron/steel corrosivity indicator.

    Args:
        ions_json: JSON string of ion concentrations in mg/L
                   Example: '{"Na+": 1000.0, "Cl-": 1500.0, "Ca2+": 100.0, "HCO3-": 200.0}'
        temperature_C: Water temperature in degrees Celsius (default 25.0)
        pH: Reported pH; required for this calculation
        validate_charge_balance: Check charge balance before running (default True)
        max_imbalance: Maximum acceptable charge imbalance in % (default 5.0)

    Returns:
        Dictionary containing:
        - lsi: Langelier Saturation Index
        - rsi: Ryznar Stability Index
        - puckorius_index: Puckorius Scaling Index, null while its primary
          formula remains unavailable
        - larson_ratio: Larson--Skold relative iron/steel corrosivity ratio
        - index_metadata: What each index predicts and its material scope
        - pH: Reported pH used in the calculation
        - pH_saturation: pH at calcite saturation
        - ionic_strength_M: Ionic strength (mol/L)
        - interpretation: Text summary without an absolute corrosion verdict
        - recommendations: Scale-management observations only

    Example:
        >>> result = predict_scaling_tendency(
        ...     ions_json='{"Ca2+": 80, "Mg2+": 20, "HCO3-": 180, "Cl-": 100, "SO4-2": 60, "Na+": 31.8}',
        ...     temperature_C=25.0,
        ...     pH=7.5
        ... )
        >>> result["charge_balance_valid"]
        True
        >>> result["interpretation_standing"]
        'interpreted_charge_balance_validated'

    Scope:
        LSI reports CaCO₃ saturation. RSI and PSI are empirical CaCO₃
        deposition indices. None is a metal-loss index. Larson--Skold is a
        relative indicator for iron and steel; this accessor reports the ratio
        but does not impose an unsourced decision threshold.

    Raises:
        ValueError: If charge imbalance exceeds max_imbalance
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
    if ions.get("Ca2+", 0.0) <= 0.0:
        raise ValueError("Scaling indices require a positive calcium (Ca2+) concentration")
    if ions.get("HCO3-", 0.0) <= 0.0 and ions.get("CO3-2", 0.0) <= 0.0:
        raise ValueError(
            "Scaling indices require a positive bicarbonate (HCO3-) or carbonate (CO3-2) concentration"
        )
    if pH is None:
        raise ValueError("A reported pH is required for scaling-index calculations")

    # Charge-balance failure is reported to the CALLER, not just to the log.
    #
    # This used to catch the ValueError, log it, and continue. The returned
    # payload carried no warning and no status, so an 18.8%-imbalanced panel
    # produced a clean-looking LSI and a set of confident recommendations. A
    # typed validation error swallowed by its caller is indistinguishable from
    # no error at all, and the log is not where a scripted consumer looks.
    charge_balance_warning = None
    if validate_charge_balance:
        try:
            validate_water_chemistry(ions, max_imbalance=max_imbalance)
        except ValueError as e:
            charge_balance_warning = (
                f"CHARGE_BALANCE_FAILED: {e} Indices below are computed from an "
                f"internally inconsistent analysis: either an ion is missing from "
                f"the panel or one is mis-reported, and both distort the result."
            )
            logger.warning(f"Charge balance validation failed: {e}")

    # Run scaling prediction (returns both scaling result and speciation to avoid double calculation)
    backend = PHREEQCBackend()
    result, speciation = backend.predict_scaling_tendency(
        ions=ions,
        temperature_C=temperature_C,
        pH=pH,
        speciation_result=None,  # Let it calculate once
    )

    # Calculate pH_saturation only when a fixed-alkalinity SI=0 root exists.
    pH_s = speciation.pH - result.lsi if result.lsi is not None else None
    langelier_ryznar_standing = (
        "computed_from_calcite_saturation_ph"
        if pH_s is not None
        else "not_defined_no_calcite_saturation_ph_root"
    )

    if result.larson_ratio is None:
        larson_ratio_standing = "not_computed_missing_inputs"
    elif result.larson_ratio == float("inf"):
        larson_ratio_standing = "undefined_zero_bicarbonate"
    else:
        larson_ratio_standing = "computed_relative_iron_steel_indicator"

    # Each index carries an explicit prediction target and material scope. This
    # prevents callers from treating negative LSI/high RSI as evidence of metal
    # loss or extending Larson--Skold to copper alloys.
    index_metadata = {
        "lsi": {
            "predicts": "calcium_carbonate_saturation_state",
            "material_scope": "aqueous_calcium_carbonate_system",
            "metal_loss_indicator": False,
        },
        "rsi": {
            "predicts": "calcium_carbonate_scale_deposition_tendency",
            "material_scope": "aqueous_calcium_carbonate_system",
            "metal_loss_indicator": False,
        },
        "puckorius_index": {
            "predicts": "calcium_carbonate_scale_deposition_tendency",
            "material_scope": "aqueous_calcium_carbonate_system",
            "metal_loss_indicator": False,
        },
        "larson_ratio": {
            "predicts": "relative_corrosivity_of_water",
            "material_scope": "iron_and_steel",
            "metal_loss_indicator": True,
        },
    }

    root_evidence = getattr(result, "calcite_root_evidence", None)
    # Format output
    output = {
        "calcite_root_evidence": root_evidence,
        "lsi": rounded_index_for_display(result.lsi),
        "lsi_unrounded": result.lsi,
        "phreeqc_database": speciation.database,  
        "rsi": round(result.rsi, 3) if result.rsi is not None else None,
        "rsi_unrounded": result.rsi,
        "langelier_ryznar_standing": langelier_ryznar_standing,
        "puckorius_index": (
            round(result.puckorius_index, 3)
            if result.puckorius_index is not None else None
        ),
        "puckorius_index_standing": (
            "computed_from_primary_source"
            if result.puckorius_index is not None
            else "suppressed_formula_not_verified_from_primary_source"
        ),
        "puckorius_index_unrounded": result.puckorius_index,
        "larson_ratio": (
            result.larson_ratio
            if result.larson_ratio is not None and result.larson_ratio != float("inf")
            else None
        ),
        "larson_ratio_display": (
            round(result.larson_ratio, 3)
            if result.larson_ratio is not None and result.larson_ratio != float("inf")
            else None
        ),
        "larson_ratio_value_standing": (
            "authoritative_unrounded"
            if result.larson_ratio is not None
            and result.larson_ratio != float("inf")
            else larson_ratio_standing
        ),
        "pH": round(speciation.pH, 3),
        "pH_unrounded": speciation.pH,
        "pH_status": "reported",
        "pH_saturation": round(pH_s, 3) if pH_s is not None else None,
        "pH_saturation_unrounded": pH_s,
        "ionic_strength_M": round(speciation.ionic_strength_M, 6),
        "ionic_strength_M_unrounded": speciation.ionic_strength_M,
        "charge_balance_percent": speciation.charge_balance_percent,
        "charge_balance_percent_display": round(
            speciation.charge_balance_percent, 2
        ),
        "charge_balance_value_standing": (
            "exact_primary_authoritative_display_field_rounded"
        ),
        "index_value_standing": (
            "unrounded_values_authoritative_rounded_fields_display_only"
        ),
        "interpretation": result.interpretation,
        "index_metadata": index_metadata,
        "index_sources": {
            "lsi": "Langelier (1936)",
            "rsi": "Ryznar (1944)",
            "puckorius_index": (
                "suppressed: primary PSI equation not content-verified; no "
                "citation or numerical value emitted"
            ),
            "larson_ratio": "Larson and Skold (1958)",
        },
        "metal_loss_indicator_indices": ["larson_ratio"],
        "larson_ratio_standing": larson_ratio_standing,
        "metal_corrosion_recommendations": [],
        "scale_management_recommendations": [],
        "recommendations": [],
    }
    lsi_sign, saturation_state = classify_lsi(
        result.lsi,
        no_root_sign_classification=(
            result.calcite_no_root_sign_classification
        ),
    )
    output["lsi_sign"] = lsi_sign
    output["saturation_state"] = saturation_state

    # A positive LSI supports a calcite-supersaturation observation, not a
    # deposition rate. Keep the action conditional on site evidence and do not
    # turn an undersaturated LSI or an empirical RSI/PSI value into corrosion
    # inhibitor advice.
    scale_management_recommendations = []
    if (
        (
            (result.lsi is not None and result.lsi > 0.0)
            or saturation_state == "supersaturated_no_saturation_ph_root"
        )
        and charge_balance_warning is None
    ):
        scale_management_recommendations.append(
            "Calcite is supersaturated; confirm deposition under site kinetics "
            "and hydraulics before selecting scale control."
        )

    output["scale_management_recommendations"] = scale_management_recommendations
    # Backward-compatible alias, now intentionally limited to scale management.
    output["recommendations"] = list(scale_management_recommendations)

    warnings = list(output.get("warnings", []) or [])
    if root_evidence and root_evidence["method"] == "scan_tolerance":
        warnings.append(root_evidence["qualification"])
    if result.lsi is None:
        warnings.append("CALCITE_SATURATION_PH_UNAVAILABLE")
    if charge_balance_warning:
        warnings.append(charge_balance_warning)
        output["charge_balance_valid"] = False
        output["interpretation_standing"] = "indeterminate_failed_charge_balance"
        output["computed_interpretation_not_used"] = result.interpretation
        output["computed_saturation_state_not_used"] = saturation_state
        output["saturation_state"] = "indeterminate_failed_charge_balance"
        output["interpretation"] = (
            "Indeterminate: the submitted ion panel failed charge balance. "
            "Calculated index values remain visible for audit only and do not "
            "support a saturation interpretation or a scale-management action."
        )
        output["scale_management_recommendations"] = []
        output["recommendations"] = []
    elif validate_charge_balance:
        output["charge_balance_valid"] = True
        output["interpretation_standing"] = "interpreted_charge_balance_validated"
    else:
        output["charge_balance_valid"] = None
        output["interpretation_standing"] = (
            "interpreted_charge_balance_validation_not_requested"
        )
    if result.larson_ratio is None:
        warnings.append(
            "larson_ratio is null because one of Cl-, SO4-2 or HCO3- was absent "
            "from the analysis. It previously reported 0.0 in this case."
        )
    output["warnings"] = warnings

    return output
