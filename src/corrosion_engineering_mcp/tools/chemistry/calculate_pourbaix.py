"""
Calculate Pourbaix (E-pH) Diagrams for Corrosion Analysis (Phase 2 Tool)

PROVENANCE:
-----------
The calculation reads local species/reaction tables. Their individual E0 rows do
not carry primary-source traceability, so every E0 and derived reaction boundary
is suppressed. General references in this module do not upgrade table rows into
verified data. The surviving numerical output is the 25 C H2/O2 water-stability
pair.

METHODOLOGY:
------------
The surviving water lines use the Nernst equation at 25 C:

    E = E⁰ + (RT/nF) * ln([oxidized]/[reduced])

For pH-dependent reactions:
    E = E⁰ - (RT/F) * ln(10) * (m/n) * pH

Where:
- E⁰ = Standard electrode potential (V_SHE)
- R = 8.314 J/(mol·K)
- T = Temperature (K)
- n = Electrons transferred
- F = 96485 C/mol
- m = Protons involved in reaction

IMPORTANT LIMITATIONS:
----------------------
This accessor returns the sourced H2/O2 water-stability lines only. It does not
select a stable phase or emit metal-reaction boundaries, immunity, corrosion,
or passivity regions.

Simplifications:
1. Unit H2/O2 fugacity and unit water activity
2. Ideal proton activity represented by pH
3. Fixed 25 C output
4. Element reaction lines remain suppressed until individually sourced

WHEN TO USE:
- Inspecting the 25 C water-stability lines and the reaction-row suppression ledger

WHEN NOT TO USE:
- Corrosion-rate prediction (this is thermodynamic boundary construction only)
- Complex solutions (high ionic strength, mixed electrolytes)
- Non-standard conditions (high T, P, exotic species)
- Regulatory/compliance decisions without a separately validated method and data

SCOPE:
------
- Elements: Fe, Cr, Ni, Cu, Ti, Al, Zn, Mg, Pb, Sn, Mo, W, Co, Mn
- Temperature: fixed at 25°C for the retained water-line output
- pH: 0-14 table range; values to 16 are flagged as extrapolated
- Potential range is determined by the sourced water lines over the requested pH range
- No dissolved-metal activity is accepted or used while metal-reaction rows are suppressed

FUTURE ENHANCEMENT:
-------------------
For Phase 3+, integrate actual PHREEQC via PhreeqPython for:
- Exact speciation
- Activity coefficient corrections (Davies, Pitzer)
- Temperature-dependent thermodynamics
- Mixed electrolytes

GENERAL REFERENCES (NOT ROW-LEVEL E0 TRACEABILITY):
--------------------------------------------------
1. Pourbaix, M. (1974). "Atlas of Electrochemical Equilibria in Aqueous Solutions".
   2nd English Edition. NACE International.

2. Bratsch, S.G. (1989). "Standard Electrode Potentials and Temperature
   Coefficients in Water at 298.15 K", DOI 10.1063/1.555839, Eq. 15 and
   Table 1. [O2/H2O E° = 1.2291 V]

3. Haynes, W.M. (2016). "CRC Handbook of Chemistry and Physics", 97th Ed.
   CRC Press. [Thermodynamic data]

4. Revie, R.W., Uhlig, H.H. (2008). "Corrosion and Corrosion Control", 4th Ed.
   Wiley. [General background]
"""

from typing import Dict, Tuple

from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.tools.pourbaix.engine import generate_pourbaix_diagram, supported_elements


@enforce_tool_input_contract("corrosion_generate_pourbaix")
def calculate_pourbaix(
    element: str,
    pH_range: Tuple[float, float] = (0.0, 14.0),
    grid_points: int = 50,
) -> Dict:
    """
    Calculate Pourbaix (E-pH) diagram for a pure element.

    **Phase 2 Tool - Boundary-Only Nernst Construction**

    **NOTE**: Phase/passivity selection is suppressed. The table does not
    implement the Gibbs-energy minimization required for an equilibrium verdict.

    Args:
        element: Chemical element symbol
            Supported: Fe, Cr, Ni, Cu, Ti, Al, Zn, Mg, Pb, Sn, Mo, W, Co, Mn
        pH_range: (pH_min, pH_max) for diagram
        grid_points: Number of grid points in each direction
    Returns:
        Dictionary with results:
        {
            "element": str,
            "temperature_C": 25.0,
            "boundaries": [],  # no current row has a verified E0 source
            "suppressed_reaction_rows": [...],
            "water_lines": {  # H₂O stability limits
                "H₂_evolution": [[pH, E], ...],  # Lower limit
                "O₂_evolution": [[pH, E], ...],  # Upper limit
            }
        }

    Raises:
        ValueError: If element not supported or parameters out of range

    Example:
        >>> result = calculate_pourbaix(
        ...     element="Fe",
        ...     pH_range=(0, 14),
        ... )
        >>> print(f"Equilibrium boundaries: {len(result['boundaries'])} lines")

    Notes:
        - Water stability: pH-dependent H2 and O2 evolution lines
        - Returned reaction lines do not establish which phase is stable
    """
    supported = supported_elements()
    canonical = element.strip().title()
    if canonical not in supported:
        raise ValueError(
            f"Element '{element}' not supported. Supported elements: {list(supported)}"
        )

    if pH_range[0] >= pH_range[1]:
        raise ValueError(f"Invalid pH range: {pH_range}")
    result = generate_pourbaix_diagram(
        element=canonical,
        pH_range=pH_range,
        grid_points=grid_points,
    )
    return result
__all__ = ["calculate_pourbaix"]
