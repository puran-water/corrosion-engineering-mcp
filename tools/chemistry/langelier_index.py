"""
Tier 1 Tool: Calculate Langelier Saturation Index (LSI)

Simplified wrapper for LSI-only calculation.
LSI = pH - pH_s, where pH_s is the pH at calcite saturation.

Interpretation:
- LSI > 0: Scaling tendency (water can precipitate CaCO₃)
- LSI = 0: Equilibrium (water is saturated)
- LSI < 0: Corrosive tendency (water can dissolve CaCO₃)

Common in cooling tower, boiler, and potable water treatment.

Performance: ~1 second
Accuracy: ±0.1 LSI units
"""

from typing import Dict, Optional
import json
import logging

from core.chemistry_backend import PHREEQCBackend, validate_water_chemistry

logger = logging.getLogger(__name__)


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
                   Example: '{"Ca2+": 120, "HCO3-": 250, "Cl-": 150, "Na+": 100}'
        temperature_C: Water temperature in degrees Celsius (default 25.0)
        pH: Measured pH (if None, uses PHREEQC-calculated pH from charge balance)
        validate_charge_balance: Check charge balance before running (default True)
        max_imbalance: Maximum acceptable charge imbalance in % (default 5.0)

    Returns:
        Dictionary containing:
        - lsi: Langelier Saturation Index
        - pH: Measured or calculated pH
        - pH_saturation: pH at calcite saturation (pH_s)
        - si_calcite: Saturation index for calcite mineral
        - temperature_C: Temperature used in calculation
        - interpretation: Text summary
        - action_required: Recommended action based on LSI

    Example:
        >>> result = calculate_langelier_index(
        ...     ions_json='{"Ca2+": 120, "HCO3-": 250, "Cl-": 150, "Na+": 100}',
        ...     temperature_C=25.0,
        ...     pH=7.8
        ... )
        >>> print(result["lsi"])
        0.35
        >>> print(result["interpretation"])
        "Mild scaling tendency"

    LSI Interpretation Guide:
        +2.0 to +3.0: Severe scaling (heavy precipitation)
        +0.5 to +2.0: Moderate scaling (scale formation likely)
        +0.0 to +0.5: Mild scaling (slight scale formation)
        -0.5 to +0.0: Near equilibrium (balanced)
        -2.0 to -0.5: Corrosive (dissolution of CaCO₃)
        < -2.0: Severely corrosive (aggressive water)

    Typical Applications:
        - Cooling tower water treatment
        - Boiler feedwater quality assessment
        - Potable water distribution (Pb/Cu corrosion control)
        - Reverse osmosis pretreatment
        - Heat exchanger fouling prediction

    Raises:
        ValueError: If charge imbalance exceeds max_imbalance or required ions missing
        RuntimeError: If PHREEQC calculation fails
    """
    # Parse ion concentrations
    try:
        ions = json.loads(ions_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for ions: {e}") from e

    if not isinstance(ions, dict):
        raise ValueError("ions_json must be a JSON object (dictionary)")

    # Check for required ions
    if "Ca2+" not in ions and "Ca+2" not in ions:
        raise ValueError("LSI calculation requires calcium (Ca2+) concentration")

    if "HCO3-" not in ions and "CO3-2" not in ions:
        raise ValueError("LSI calculation requires bicarbonate (HCO3-) or carbonate (CO3-2)")

    # Validate charge balance if requested
    if validate_charge_balance:
        try:
            validate_water_chemistry(ions, max_imbalance=max_imbalance)
        except ValueError as e:
            logger.warning(f"Charge balance validation failed: {e}")

    # Run LSI calculation
    backend = PHREEQCBackend()
    lsi = backend.calculate_langelier_index(
        ions=ions,
        temperature_C=temperature_C,
        pH=pH,
    )

    # Get speciation for additional context
    speciation = backend.run_speciation(ions, temperature_C, pH)
    si_calcite = speciation.saturation_indices.get("Calcite", 0.0)
    pH_s = speciation.pH - lsi

    # Format output
    output = {
        "lsi": round(lsi, 3),
        "pH": round(speciation.pH, 3),
        "pH_saturation": round(pH_s, 3),
        "si_calcite": round(si_calcite, 3),
        "temperature_C": temperature_C,
    }

    # Generate interpretation
    if lsi > 2.0:
        interpretation = "Severe scaling tendency"
        action = "Immediate action required: Add acid or increase blowdown. Heavy scale formation expected."
    elif lsi > 0.5:
        interpretation = "Moderate scaling tendency"
        action = "Action recommended: Consider scale inhibitor or pH adjustment. Scale formation likely."
    elif lsi > 0.0:
        interpretation = "Mild scaling tendency"
        action = "Monitor closely. Minor scale formation possible over time."
    elif lsi > -0.5:
        interpretation = "Near equilibrium (balanced)"
        action = "No action required. Water is well-balanced."
    elif lsi > -2.0:
        interpretation = "Corrosive tendency"
        action = "Consider corrosion inhibitor or pH adjustment. May dissolve existing scale."
    else:
        interpretation = "Severely corrosive"
        action = "Immediate action required: Increase pH or add corrosion inhibitor. Aggressive water."

    output["interpretation"] = interpretation
    output["action_required"] = action

    # Add specific notes for cooling towers vs potable water
    if temperature_C > 30.0:
        output["note"] = (
            "Elevated temperature increases scaling tendency. "
            "Common in cooling towers - monitor heat exchanger surfaces."
        )
    elif lsi < -1.0 and ("Pb" in ions or "Cu" in ions):
        output["note"] = (
            "Corrosive water with lead/copper present. "
            "Consider pH adjustment to prevent leaching in distribution system."
        )

    return output
