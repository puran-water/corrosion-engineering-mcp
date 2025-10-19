"""
Tier 2 Tool: Galvanic Corrosion Calculator

Calculates galvanic corrosion rate for bimetallic couples using
mixed-potential theory (Evans diagrams).

Inputs:
- Anode material (less noble, e.g., "carbon steel")
- Cathode material (more noble, e.g., "316L")
- Area ratio (A_cathode / A_anode)
- Water chemistry (optional, from Phase 1)
- Temperature

Outputs:
- Coupled potential (E_couple)
- Galvanic current density (i_galv)
- Corrosion rate (mm/year)
- Interpretation and recommendations

Performance: 1-2 seconds (Tier 2 target)
Accuracy: ±30% (typical for Tafel approximations)

Per Codex guidance:
- Use Tafel approximations (valid for |η| > 50-100 mV)
- Weight multiple cathodes by area
- Return E_couple and i_galv for downstream tools
"""

from typing import Dict, Optional
import json
import logging

from core.galvanic_backend import GalvanicBackend

logger = logging.getLogger(__name__)


def calculate_galvanic_corrosion(
    anode_material: str,
    cathode_material: str,
    area_ratio: float,
    temperature_C: float = 25.0,
    electrolyte: str = "seawater",
    water_chemistry_json: Optional[str] = None,
) -> Dict:
    """
    Calculate galvanic corrosion rate for a bimetallic couple.

    Args:
        anode_material: Less noble material (e.g., "carbon steel", "aluminum")
        cathode_material: More noble material (e.g., "316L", "copper", "titanium")
        area_ratio: Cathode area / Anode area (unitless)
        temperature_C: Temperature in degrees Celsius (default 25.0)
        electrolyte: Electrolyte type ("seawater", "freshwater", "acid", etc.)
        water_chemistry_json: Optional JSON string with ion concentrations for PHREEQC integration

    Returns:
        Dictionary containing:
        - E_couple: Coupled potential vs SHE (V)
        - i_galv: Galvanic current density on anode (A/m²)
        - corrosion_rate_mm_per_year: Corrosion rate (mm/year)
        - anode_material: Anode material name
        - cathode_material: Cathode material name
        - area_ratio: A_cathode / A_anode
        - interpretation: Text summary
        - recommendations: List of mitigation strategies

    Example:
        >>> result = calculate_galvanic_corrosion(
        ...     anode_material="carbon steel",
        ...     cathode_material="316L",
        ...     area_ratio=10.0,
        ...     temperature_C=25.0,
        ...     electrolyte="seawater"
        ... )
        >>> print(result["corrosion_rate_mm_per_year"])
        2.45
        >>> print(result["interpretation"])
        "Severe galvanic corrosion (CR = 2.45 mm/year); Large cathode/anode ratio (10.0:1) accelerates attack"

    Galvanic Series (Seawater, Most Noble to Least Noble):
        - Titanium (most noble)
        - 316L stainless steel
        - 304 stainless steel
        - Copper, Bronze
        - Brass
        - Carbon steel
        - Aluminum alloys
        - Zinc
        - Magnesium (least noble, best sacrificial anode)

    Interpretation Guide:
        - CR < 0.1 mm/year: Low risk, acceptable for most applications
        - 0.1 < CR < 1.0 mm/year: Moderate risk, monitor and consider mitigation
        - CR > 1.0 mm/year: High risk, galvanic isolation or coatings required

    Area Ratio Effects:
        - Large cathode + small anode (ratio > 10): Severe attack on anode
        - Equal areas (ratio ≈ 1): Moderate attack
        - Small cathode + large anode (ratio < 0.1): Low risk

    Raises:
        ValueError: If invalid materials or area ratio
    """
    # Validate inputs
    if area_ratio <= 0:
        raise ValueError(f"Area ratio must be positive, got {area_ratio}")

    if area_ratio > 1000:
        logger.warning(f"Very large area ratio ({area_ratio}) may indicate input error")

    # Run galvanic calculation
    backend = GalvanicBackend()
    result = backend.calculate_galvanic_corrosion(
        anode_material=anode_material,
        cathode_material=cathode_material,
        area_ratio=area_ratio,
        temperature_C=temperature_C,
        electrolyte=electrolyte,
    )

    # Format output
    output = {
        "E_couple_V": round(result.E_couple, 4),
        "i_galv_A_per_m2": round(result.i_galv, 6),
        "corrosion_rate_mm_per_year": round(result.corrosion_rate_mm_per_year, 3),
        "anode_material": result.anode_material,
        "cathode_material": result.cathode_material,
        "area_ratio": area_ratio,
        "interpretation": result.interpretation,
        "recommendations": [],
    }

    # Generate recommendations
    recommendations = []

    # Area ratio recommendations
    if area_ratio > 10.0:
        recommendations.append(
            f"CRITICAL: Large cathode/anode ratio ({area_ratio:.1f}:1) - "
            "Minimize cathode area or increase anode area"
        )
        recommendations.append("Consider galvanic isolation (insulating gaskets, coatings)")

    elif area_ratio > 3.0:
        recommendations.append(
            f"WARNING: Moderate cathode/anode ratio ({area_ratio:.1f}:1) - Monitor for accelerated corrosion"
        )

    # Corrosion rate recommendations
    if result.corrosion_rate_mm_per_year > 1.0:
        recommendations.append(
            "HIGH RISK: Apply protective coating to anode or use sacrificial anode system"
        )
        recommendations.append("Consider cathodic protection if economically feasible")

    elif result.corrosion_rate_mm_per_year > 0.1:
        recommendations.append("MODERATE RISK: Monitor corrosion rate and inspect regularly")
        recommendations.append("Consider coating or periodic replacement schedule")

    else:
        recommendations.append("LOW RISK: Corrosion rate acceptable for most applications")

    # Material-specific recommendations
    if "aluminum" in anode_material.lower():
        recommendations.append(
            "Aluminum anode: Avoid direct contact with copper alloys (severe galvanic attack)"
        )

    if "316" in cathode_material or "304" in cathode_material:
        recommendations.append(
            "Stainless steel cathode: Ensure sufficient chloride removal to prevent crevice corrosion"
        )

    # Electrolyte recommendations
    if electrolyte.lower() == "seawater":
        recommendations.append("Seawater environment: High conductivity accelerates galvanic corrosion")
        recommendations.append("Consider marine-grade coatings and regular inspection")

    output["recommendations"] = recommendations

    # Add note about water chemistry integration (future)
    if water_chemistry_json:
        output["note"] = "Water chemistry integration with PHREEQC pending (Phase 2 enhancement)"

    return output
