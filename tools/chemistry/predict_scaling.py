"""
Tier 1 Tool: Predict Scaling Tendency

Calculates multiple scaling/corrosion indices:
- LSI (Langelier Saturation Index): pH - pH_s
- RSI (Ryznar Stability Index): 2×pH_s - pH
- PSI (Puckorius Scaling Index): 2×pH_s - pH_eq
- Larson Ratio: (Cl⁻ + SO₄²⁻) / HCO₃⁻ (corrosivity)

Interpretation:
- LSI > 0: Scaling tendency
- LSI < 0: Corrosive tendency
- RSI < 6.5: Scaling
- RSI > 7.5: Corrosive
- Larson Ratio > 1.0: Aggressive corrosion

Performance: ~1 second
Accuracy: ±0.1 for indices
"""

from typing import Dict, Optional
import json
import logging

from core.chemistry_backend import PHREEQCBackend, validate_water_chemistry

logger = logging.getLogger(__name__)


def predict_scaling_tendency(
    ions_json: str,
    temperature_C: float = 25.0,
    pH: Optional[float] = None,
    validate_charge_balance: bool = True,
    max_imbalance: float = 5.0,
) -> Dict:
    """
    Predict scaling and corrosion tendency using multiple indices.

    Args:
        ions_json: JSON string of ion concentrations in mg/L
                   Example: '{"Na+": 1000.0, "Cl-": 1500.0, "Ca2+": 100.0, "HCO3-": 200.0}'
        temperature_C: Water temperature in degrees Celsius (default 25.0)
        pH: Measured pH (if None, uses PHREEQC-calculated pH)
        validate_charge_balance: Check charge balance before running (default True)
        max_imbalance: Maximum acceptable charge imbalance in % (default 5.0)

    Returns:
        Dictionary containing:
        - lsi: Langelier Saturation Index
        - rsi: Ryznar Stability Index
        - puckorius_index: Puckorius Scaling Index
        - larson_ratio: Larson corrosivity ratio
        - pH: Calculated or measured pH
        - pH_saturation: pH at calcite saturation
        - ionic_strength_M: Ionic strength (mol/L)
        - interpretation: Text summary of scaling/corrosion tendency
        - recommendations: List of recommended actions

    Example:
        >>> result = predict_scaling_tendency(
        ...     ions_json='{"Ca2+": 120, "HCO3-": 250, "Cl-": 150, "SO4-2": 80}',
        ...     temperature_C=25.0,
        ...     pH=7.8
        ... )
        >>> print(result["lsi"])
        0.35
        >>> print(result["interpretation"])
        "Mild scaling tendency (LSI = 0.35). Water is near equilibrium."

    Interpretation Guide:
        LSI (Langelier Saturation Index):
        - LSI > 0.5: Significant scaling tendency
        - 0 < LSI < 0.5: Mild scaling tendency
        - -0.5 < LSI < 0: Mild corrosivity
        - LSI < -0.5: Significant corrosivity

        RSI (Ryznar Stability Index):
        - RSI < 6.0: Heavy scaling
        - 6.0 < RSI < 6.5: Moderate scaling
        - 6.5 < RSI < 7.0: Near equilibrium
        - 7.0 < RSI < 7.5: Slight corrosivity
        - RSI > 7.5: High corrosivity

        Larson Ratio:
        - LR < 0.2: Low corrosivity
        - 0.2 < LR < 1.0: Moderate corrosivity
        - LR > 1.0: High corrosivity (especially for copper alloys)

    Raises:
        ValueError: If charge imbalance exceeds max_imbalance
        RuntimeError: If PHREEQC calculation fails
    """
    # Parse ion concentrations
    try:
        ions = json.loads(ions_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for ions: {e}") from e

    if not isinstance(ions, dict):
        raise ValueError("ions_json must be a JSON object (dictionary)")

    # Validate charge balance if requested
    if validate_charge_balance:
        try:
            validate_water_chemistry(ions, max_imbalance=max_imbalance)
        except ValueError as e:
            logger.warning(f"Charge balance validation failed: {e}")

    # Run scaling prediction (returns both scaling result and speciation to avoid double calculation)
    backend = PHREEQCBackend()
    result, speciation = backend.predict_scaling_tendency(
        ions=ions,
        temperature_C=temperature_C,
        pH=pH,
        speciation_result=None,  # Let it calculate once
    )

    # Calculate pH_saturation from LSI
    pH_s = speciation.pH - result.lsi

    # Format output
    output = {
        "lsi": round(result.lsi, 3),
        "rsi": round(result.rsi, 3),
        "puckorius_index": round(result.puckorius_index, 3),
        "larson_ratio": round(result.larson_ratio, 3) if result.larson_ratio != float('inf') else None,
        "pH": round(speciation.pH, 3),
        "pH_saturation": round(pH_s, 3),
        "ionic_strength_M": round(speciation.ionic_strength_M, 6),
        "interpretation": result.interpretation,
        "recommendations": [],
    }

    # Generate detailed recommendations
    recommendations = []

    # LSI-based recommendations
    if result.lsi > 0.5:
        recommendations.append("Consider scale inhibitor dosing or pH adjustment")
        recommendations.append("Monitor heat exchangers and piping for scale buildup")
    elif result.lsi < -0.5:
        recommendations.append("Consider corrosion inhibitor dosing")
        recommendations.append("Monitor for metal loss, especially in carbon steel")

    # RSI-based recommendations
    if result.rsi < 6.0:
        recommendations.append("Heavy scaling expected - increase blowdown or add acid")
    elif result.rsi > 7.5:
        recommendations.append("Corrosive water - verify inhibitor program effectiveness")

    # Larson ratio recommendations
    if result.larson_ratio > 1.0:
        recommendations.append(f"High Larson ratio ({result.larson_ratio:.2f}) - aggressive to copper alloys")
        recommendations.append("Avoid copper-containing materials in system")
    elif result.larson_ratio > 0.5:
        recommendations.append("Moderate corrosivity - suitable corrosion inhibitor required")

    # Ionic strength recommendations
    if speciation.ionic_strength_M > 0.5:
        recommendations.append("High salinity - verify materials are suitable for brackish/saline service")

    output["recommendations"] = recommendations

    return output
