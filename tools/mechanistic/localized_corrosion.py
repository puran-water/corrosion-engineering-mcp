"""
Tier 2 Tool: Localized Corrosion Calculator (Pitting & Crevice)

Calculates pitting and crevice corrosion susceptibility using:
- PREN-based CPT (Critical Pitting Temperature) correlations
- Chloride threshold models
- Oldfield-Sutton IR drop for crevice acidification

Inputs:
- Material (e.g., "316L", "2205", "254SMO")
- Temperature (°C)
- Chloride concentration (mg/L, from Phase 1 PHREEQC)
- pH (from Phase 1 PHREEQC)
- Crevice geometry (optional)

Outputs:
- Pitting susceptibility (CPT, PREN, Cl⁻ threshold)
- Crevice susceptibility (CCT, IR drop, acidification)
- Overall risk assessment
- Recommendations

Performance: 1-2 seconds (Tier 2 target)
Accuracy: ±5°C for CPT, ±20% for Cl⁻ threshold

Per Codex guidance:
- Expose PREN calibration coefficients (duplex ±5°C deviation)
- Separate pitting vs crevice outputs
- Share chloride threshold logic
- Simplified Oldfield-Sutton IR drop
"""

from typing import Dict, Optional
import logging

from core.localized_backend import LocalizedBackend, MaterialComposition

logger = logging.getLogger(__name__)


def calculate_localized_corrosion(
    material: str,
    temperature_C: float,
    Cl_mg_L: float,
    pH: float = 7.0,
    crevice_gap_mm: float = 0.1,
    water_chemistry_json: Optional[str] = None,
) -> Dict:
    """
    Calculate pitting and crevice corrosion susceptibility.

    Args:
        material: Material name (e.g., "316L", "2205", "254SMO")
        temperature_C: Operating temperature (°C)
        Cl_mg_L: Chloride concentration (mg/L)
        pH: Solution pH (default 7.0)
        crevice_gap_mm: Crevice gap width in mm (default 0.1 mm)
        water_chemistry_json: Optional JSON with full water chemistry for PHREEQC integration

    Returns:
        Dictionary containing:
        - pitting: Pitting susceptibility results
            - CPT_C: Critical Pitting Temperature (°C)
            - PREN: Pitting Resistance Equivalent Number
            - Cl_threshold_mg_L: Chloride threshold (mg/L)
            - susceptibility: "low", "moderate", "high", "critical"
            - margin_C: Temperature margin to CPT (°C)
            - interpretation: Text summary
        - crevice: Crevice susceptibility results
            - CCT_C: Critical Crevice Temperature (°C)
            - IR_drop_V: IR drop in crevice (V)
            - acidification_factor: pH drop factor
            - susceptibility: "low", "moderate", "high", "critical"
            - margin_C: Temperature margin to CCT (°C)
            - interpretation: Text summary
        - overall_risk: "low", "moderate", "high", "critical"
        - recommendations: List of mitigation strategies

    Example:
        >>> result = calculate_localized_corrosion(
        ...     material="316L",
        ...     temperature_C=60.0,
        ...     Cl_mg_L=500.0,
        ...     pH=6.5,
        ...     crevice_gap_mm=0.1
        ... )
        >>> print(result["pitting"]["CPT_C"])
        14.5
        >>> print(result["overall_risk"])
        "high"

    Material Guidelines:
        - 304: PREN ≈ 18, CPT ≈ 8°C (low resistance)
        - 316/316L: PREN ≈ 24, CPT ≈ 14°C (moderate resistance)
        - 2205 (duplex): PREN ≈ 35, CPT ≈ 25°C (good resistance)
        - 254SMO: PREN ≈ 43, CPT ≈ 38°C (excellent resistance)

    Interpretation Guide:
        - CPT margin > 20°C: Low risk
        - CPT margin 10-20°C: Moderate risk, monitor chlorides
        - CPT margin 0-10°C: High risk, mitigation required
        - CPT margin < 0°C: Critical risk, immediate action required

    Chloride Thresholds (20°C baseline):
        - 304: ~50 mg/L
        - 316L: ~250 mg/L
        - 2205: ~1000 mg/L
        - 254SMO: ~5000 mg/L
        (Decreases exponentially with temperature)

    Raises:
        ValueError: If invalid material or parameters
    """
    # Validate inputs
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
    result = backend.calculate_localized_corrosion(
        material=material,
        temperature_C=temperature_C,
        Cl_mg_L=Cl_mg_L,
        pH=pH,
        crevice_gap_mm=crevice_gap_mm,
    )

    # Format output
    output = {
        "pitting": {
            "CPT_C": round(result.pitting.CPT_C, 1),
            "PREN": round(result.pitting.PREN, 1),
            "Cl_threshold_mg_L": round(result.pitting.Cl_threshold_mg_L, 1),
            "susceptibility": result.pitting.susceptibility,
            "margin_C": round(result.pitting.margin_C, 1),
            "interpretation": result.pitting.interpretation,
        },
        "crevice": {
            "CCT_C": round(result.crevice.CCT_C, 1),
            "IR_drop_V": round(result.crevice.IR_drop_V, 4),
            "acidification_factor": round(result.crevice.acidification_factor, 1),
            "susceptibility": result.crevice.susceptibility,
            "margin_C": round(result.crevice.margin_C, 1),
            "interpretation": result.crevice.interpretation,
        },
        "material": result.material,
        "temperature_C": temperature_C,
        "Cl_mg_L": Cl_mg_L,
        "pH": pH,
        "overall_risk": result.overall_risk,
        "recommendations": [],
    }

    # Generate recommendations
    recommendations = []

    # Overall risk recommendations
    if result.overall_risk == "critical":
        recommendations.append(
            "CRITICAL: Immediate risk of localized corrosion - Material change or process modification required"
        )
        recommendations.append(
            "Consider upgrading to higher PREN alloy (e.g., 316→2205, 2205→254SMO)"
        )

    elif result.overall_risk == "high":
        recommendations.append(
            "HIGH RISK: Localized corrosion likely - Implement mitigation measures"
        )
        recommendations.append(
            "Options: Reduce temperature, lower chlorides, or upgrade material"
        )

    elif result.overall_risk == "moderate":
        recommendations.append(
            "MODERATE RISK: Monitor for pitting/crevice initiation"
        )
        recommendations.append(
            "Establish inspection schedule (quarterly recommended)"
        )

    else:
        recommendations.append(
            "LOW RISK: Material selection appropriate for operating conditions"
        )

    # Pitting-specific recommendations
    if result.pitting.margin_C < 10.0:
        recommendations.append(
            f"Pitting risk: T = {temperature_C}°C near CPT = {result.pitting.CPT_C:.1f}°C"
        )
        if Cl_mg_L > result.pitting.Cl_threshold_mg_L:
            recommendations.append(
                f"Reduce chlorides from {Cl_mg_L:.0f} to <{result.pitting.Cl_threshold_mg_L:.0f} mg/L"
            )

    # Crevice-specific recommendations
    if result.crevice.susceptibility in ["high", "critical"]:
        recommendations.append(
            "Crevice corrosion risk: Eliminate or seal crevices in design"
        )
        recommendations.append(
            "Avoid gasketed joints, threaded connections, or lap joints where possible"
        )
        if result.crevice.acidification_factor > 100:
            recommendations.append(
                f"Severe crevice acidification (pH drop factor {result.crevice.acidification_factor:.0f}x) - Use solid sections or welded construction"
            )

    # pH recommendations
    if pH < 6.0:
        recommendations.append(
            f"Low pH ({pH:.1f}) increases localized corrosion risk - Consider pH control >6.5"
        )

    # Material-specific recommendations
    if "304" in material:
        recommendations.append(
            "304 has low pitting resistance (PREN ≈ 18) - Upgrade to 316L (PREN ≈ 24) if chlorides >100 mg/L"
        )

    if "316" in material and Cl_mg_L > 500:
        recommendations.append(
            "316 moderate for high chlorides - Consider duplex 2205 (PREN ≈ 35) for Cl⁻ >500 mg/L"
        )

    # Temperature control recommendations
    if result.pitting.margin_C < 5.0 and result.pitting.margin_C > 0:
        recommendations.append(
            f"Reduce operating temperature by {5.0 - result.pitting.margin_C:.1f}°C to gain 5°C safety margin"
        )

    output["recommendations"] = recommendations

    # Add note about water chemistry integration (future)
    if water_chemistry_json:
        output["note"] = "Water chemistry integration with PHREEQC pending (Phase 2 enhancement)"

    return output


def calculate_pren(
    Cr_wt_pct: float,
    Mo_wt_pct: float,
    N_wt_pct: float,
    grade_type: str = "austenitic",
) -> Dict:
    """
    Calculate PREN (Pitting Resistance Equivalent Number) from composition.

    Utility function for custom alloys or verification.

    Args:
        Cr_wt_pct: Chromium content (wt%)
        Mo_wt_pct: Molybdenum content (wt%)
        N_wt_pct: Nitrogen content (wt%)
        grade_type: "austenitic", "duplex", or "superaustenitic"

    Returns:
        Dictionary with:
        - PREN: Calculated value
        - CPT_C: Estimated Critical Pitting Temperature (°C)
        - grade_type: Input grade type

    Example:
        >>> result = calculate_pren(Cr_wt_pct=16.5, Mo_wt_pct=2.0, N_wt_pct=0.05)
        >>> print(result["PREN"])
        23.3
        >>> print(result["CPT_C"])
        13.3

    Standard PREN formula (austenitic):
        PREN = %Cr + 3.3×%Mo + 16×%N

    Duplex formula:
        PREN = %Cr + 3.3×%Mo + 30×%N  (higher N weighting)

    CPT correlation (austenitic):
        CPT ≈ PREN - 10 (°C)
    """
    # Validate inputs
    if Cr_wt_pct < 0 or Cr_wt_pct > 30:
        raise ValueError(f"Cr content {Cr_wt_pct}% out of range (0-30%)")

    if Mo_wt_pct < 0 or Mo_wt_pct > 10:
        raise ValueError(f"Mo content {Mo_wt_pct}% out of range (0-10%)")

    if N_wt_pct < 0 or N_wt_pct > 1:
        raise ValueError(f"N content {N_wt_pct}% out of range (0-1%)")

    # Create material composition
    comp = MaterialComposition(
        Cr=Cr_wt_pct,
        Mo=Mo_wt_pct,
        N=N_wt_pct,
        grade_type=grade_type,
    )

    # Calculate PREN
    pren = comp.calculate_pren()

    # Estimate CPT
    from core.localized_backend import CPT_CORRELATIONS
    cpt_corr = CPT_CORRELATIONS.get(grade_type, CPT_CORRELATIONS["austenitic"])
    cpt = cpt_corr["m"] * pren + cpt_corr["b"]

    return {
        "PREN": round(pren, 1),
        "CPT_C": round(cpt, 1),
        "grade_type": grade_type,
        "composition": {
            "Cr_wt_pct": Cr_wt_pct,
            "Mo_wt_pct": Mo_wt_pct,
            "N_wt_pct": N_wt_pct,
        },
    }
