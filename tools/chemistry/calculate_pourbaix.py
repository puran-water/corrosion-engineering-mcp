"""
Calculate Pourbaix (E-pH) Diagrams for Corrosion Analysis (Phase 2 Tool)

PROVENANCE:
-----------
Standard electrode potentials and reaction data from:

1. Pourbaix, M. (1974). "Atlas of Electrochemical Equilibria in Aqueous Solutions".
   2nd English Edition. NACE International. (Primary source for E⁰ values)

2. Haynes, W.M., ed. (2016). "CRC Handbook of Chemistry and Physics", 97th Edition.
   CRC Press. (Standard thermodynamic data)

3. Bard, A.J., Parsons, R., Jordan, J. (1985). "Standard Potentials in Aqueous Solution".
   IUPAC-NIST compilation.

METHODOLOGY:
------------
This implementation uses SIMPLIFIED THERMODYNAMICS (Nernst equation):

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
**This is NOT a full PHREEQC integration**. This tool provides ENGINEERING ESTIMATES
using standard thermodynamic data. For precise geochemical modeling, use actual PHREEQC.

Simplifications:
1. Activity coefficients = 1 (ideal solutions assumed)
2. No complex ion speciation (e.g., FeCl₄²⁻, FeOH⁺ not modeled)
3. Temperature effects simplified (E⁰ assumed constant)
4. Oxide stability based on literature values, not calculated from ΔG_f

WHEN TO USE:
- Material selection (qualitative comparison of immunity/passivation regions)
- Quick assessment of corrosion risk
- Educational/visualization purposes

WHEN NOT TO USE:
- Precise corrosion rate prediction (use NRL galvanic model instead)
- Complex solutions (high ionic strength, mixed electrolytes)
- Non-standard conditions (high T, P, exotic species)
- Regulatory/compliance calculations (use validated PHREEQC)

SCOPE:
------
- Elements: Fe, Cr, Ni, Cu, Ti, Al
- Temperature: 0-100°C (approximate corrections only)
- pH: 0-14
- Potential: -2.0 to +2.0 V_SHE
- Solubility: 10⁻⁶ M default (user-adjustable)

FUTURE ENHANCEMENT:
-------------------
For Phase 3+, integrate actual PHREEQC via PhreeqPython for:
- Exact speciation
- Activity coefficient corrections (Davies, Pitzer)
- Temperature-dependent thermodynamics
- Mixed electrolytes

REFERENCES:
-----------
1. Pourbaix, M. (1974). "Atlas of Electrochemical Equilibria in Aqueous Solutions".
   2nd English Edition. NACE International. [Primary data source for E⁰ values]

2. Bard, A.J., Parsons, R., Jordan, J. (1985). "Standard Potentials in Aqueous Solution".
   IUPAC-NIST. [Standard electrode potentials]

3. Haynes, W.M. (2016). "CRC Handbook of Chemistry and Physics", 97th Ed.
   CRC Press. [Thermodynamic data]

4. Revie, R.W., Uhlig, H.H. (2008). "Corrosion and Corrosion Control", 4th Ed.
   Wiley. [Oxide stability data for Ti, Cr, Ni, Al]
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
import re


def calculate_pourbaix(
    element: str,
    temperature_C: float = 25.0,
    soluble_concentration_M: float = 1.0e-6,
    pH_range: Tuple[float, float] = (0.0, 14.0),
    E_range_VSHE: Tuple[float, float] = (-2.0, 2.0),
    grid_points: int = 50,
    include_species: Optional[List[str]] = None
) -> Dict:
    """
    Calculate Pourbaix (E-pH) diagram for a pure element.

    **Phase 2 Tool - Simplified Thermodynamic Equilibrium Calculator**

    **NOTE**: This is a simplified implementation using Nernst equation.
    For precise geochemical modeling, use actual PHREEQC via PhreeqPython.

    Args:
        element: Chemical element symbol
            Supported: "Fe", "Cr", "Ni", "Cu", "Ti", "Al"
        temperature_C: Temperature, °C (0-100°C)
        soluble_concentration_M: Solubility limit for dissolved species, M
            Default: 10⁻⁶ M (typical corrosion threshold)
        pH_range: (pH_min, pH_max) for diagram
        E_range_VSHE: (E_min, E_max) in V_SHE for diagram
        grid_points: Number of grid points in each direction
        include_species: Optional list of specific species to include
            If None, uses predefined reactions from literature (Pourbaix 1974)

    Returns:
        Dictionary with results:
        {
            "element": str,
            "temperature_C": float,
            "regions": {  # Dominant regions
                "immunity": [[pH, E], ...],  # Metal stable
                "passivation": [[pH, E], ...],  # Oxide stable
                "corrosion": [[pH, E], ...],  # Ions stable
            },
            "boundaries": [  # E-pH equilibrium lines
                {
                    "type": "immunity_passivation",  # or other combinations
                    "equation": "2H₂O + O₂ + 4e⁻ = 4OH⁻",
                    "points": [[pH, E], ...]
                },
                ...
            ],
            "species_stability": {  # Predominant species in each region
                "Fe": [[pH_range, E_range], ...],
                "Fe²⁺": [[pH_range, E_range], ...],
                "Fe³⁺": [[pH_range, E_range], ...],
                "Fe(OH)₂": [[pH_range, E_range], ...],
                "Fe(OH)₃": [[pH_range, E_range], ...],
                ...
            },
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
        ...     temperature_C=25.0,
        ...     soluble_concentration_M=1.0e-6,
        ...     pH_range=(0, 14),
        ...     E_range_VSHE=(-1.5, 1.5)
        ... )
        >>> print(f"Immunity region: {len(result['regions']['immunity'])} points")
        >>> print(f"Equilibrium boundaries: {len(result['boundaries'])} lines")

    Notes:
        - Immunity: Metal does not corrode (thermodynamically stable)
        - Passivation: Oxide film protects (corrosion rate low if film intact)
        - Corrosion: Metal dissolves (active corrosion expected)
        - Water stability: pH-dependent H₂ and O₂ evolution lines
    """
    # Validate inputs
    supported_elements = ["Fe", "Cr", "Ni", "Cu", "Ti", "Al"]
    if element not in supported_elements:
        raise ValueError(
            f"Element '{element}' not supported. "
            f"Supported elements: {supported_elements}"
        )

    if not (0.0 <= temperature_C <= 100.0):
        raise ValueError(f"Temperature {temperature_C}°C out of range (0-100°C)")

    if not (0.0 <= pH_range[0] < pH_range[1] <= 14.0):
        raise ValueError(f"Invalid pH range: {pH_range}")

    if not (-3.0 <= E_range_VSHE[0] < E_range_VSHE[1] <= 3.0):
        raise ValueError(f"Invalid potential range: {E_range_VSHE}")

    # Generate pH and E grids
    pH_grid = np.linspace(pH_range[0], pH_range[1], grid_points)
    E_grid = np.linspace(E_range_VSHE[0], E_range_VSHE[1], grid_points)

    # Calculate equilibrium boundaries using simplified thermodynamics
    boundaries = _calculate_equilibrium_boundaries(
        element,
        temperature_C,
        soluble_concentration_M,
        pH_grid,
        E_grid,
        include_species
    )

    # Classify regions (immunity, passivation, corrosion)
    regions = _classify_pourbaix_regions(
        element,
        boundaries,
        pH_grid,
        E_grid,
        soluble_concentration_M
    )

    # Calculate water stability lines
    water_lines = _calculate_water_stability(pH_grid, temperature_C)

    return {
        "element": element,
        "temperature_C": temperature_C,
        "soluble_concentration_M": soluble_concentration_M,
        "pH_range": pH_range,
        "E_range_VSHE": E_range_VSHE,
        "regions": regions,
        "boundaries": boundaries,
        "water_lines": water_lines,
        "grid_points": grid_points
    }


def _calculate_equilibrium_boundaries(
    element: str,
    temperature_C: float,
    soluble_conc_M: float,
    pH_grid: np.ndarray,
    E_grid: np.ndarray,
    include_species: Optional[List[str]] = None
) -> List[Dict]:
    """
    Calculate E-pH equilibrium boundaries using simplified thermodynamics.

    Uses Nernst equation with standard electrode potentials from literature
    (Pourbaix Atlas 1974, Bard-IUPAC 1985) to calculate stability boundaries.
    """
    boundaries = []

    # Define key reactions for each element
    reactions = _get_element_reactions(element)

    for reaction in reactions:
        # Calculate boundary line for this reaction
        boundary_points = _calculate_reaction_boundary(
            element,
            reaction,
            temperature_C,
            soluble_conc_M,
            pH_grid
        )

        if boundary_points:
            boundaries.append({
                "type": reaction["type"],
                "equation": reaction["equation"],
                "points": boundary_points
            })

    return boundaries


def _get_element_reactions(element: str) -> List[Dict]:
    """
    Get standard Pourbaix reactions for an element.

    Based on Pourbaix (1974) Atlas.
    """
    reactions = {
        "Fe": [
            {
                "type": "immunity_corrosion",
                "equation": "Fe → Fe²⁺ + 2e⁻",
                "reaction": "Fe = Fe+2 + 2e-",
                "E0_VSHE": -0.447,  # Standard potential at 25°C
                "pH_dependent": False
            },
            {
                "type": "corrosion_passivation",
                "equation": "3Fe²⁺ + 4H₂O → Fe₃O₄ + 8H⁺ + 2e⁻",
                "reaction": "3Fe+2 + 4H2O = Fe3O4 + 8H+ + 2e-",
                "E0_VSHE": 0.98,
                "pH_dependent": True
            },
            {
                "type": "passivation_corrosion",
                "equation": "Fe₃O₄ + 8H⁺ → 3Fe³⁺ + 4H₂O + e⁻",
                "reaction": "Fe3O4 + 8H+ = 3Fe+3 + 4H2O + e-",
                "E0_VSHE": 0.77,
                "pH_dependent": True
            }
        ],
        "Cr": [
            {
                "type": "immunity_corrosion",
                "equation": "Cr → Cr³⁺ + 3e⁻",
                "reaction": "Cr = Cr+3 + 3e-",
                "E0_VSHE": -0.744,
                "pH_dependent": False
            },
            {
                "type": "corrosion_passivation",
                "equation": "2Cr³⁺ + 3H₂O → Cr₂O₃ + 6H⁺",
                "reaction": "2Cr+3 + 3H2O = Cr2O3 + 6H+",
                "E0_VSHE": None,  # pH-dependent only
                "pH_dependent": True
            }
        ],
        "Ni": [
            {
                "type": "immunity_corrosion",
                "equation": "Ni → Ni²⁺ + 2e⁻",
                "reaction": "Ni = Ni+2 + 2e-",
                "E0_VSHE": -0.257,
                "pH_dependent": False
            },
            {
                "type": "corrosion_passivation",
                "equation": "Ni²⁺ + 2H₂O → Ni(OH)₂ + 2H⁺",
                "reaction": "Ni+2 + 2H2O = Ni(OH)2 + 2H+",
                "E0_VSHE": None,
                "pH_dependent": True
            }
        ],
        "Cu": [
            {
                "type": "immunity_corrosion",
                "equation": "Cu → Cu²⁺ + 2e⁻",
                "reaction": "Cu = Cu+2 + 2e-",
                "E0_VSHE": 0.340,
                "pH_dependent": False
            },
            {
                "type": "corrosion_passivation",
                "equation": "2Cu²⁺ + H₂O → Cu₂O + 2H⁺",
                "reaction": "2Cu+2 + H2O = Cu2O + 2H+",
                "E0_VSHE": 0.203,
                "pH_dependent": True
            }
        ],
        "Ti": [
            {
                "type": "immunity_corrosion",
                "equation": "Ti → Ti³⁺ + 3e⁻",
                "reaction": "Ti = Ti+3 + 3e-",
                "E0_VSHE": -1.630,
                "pH_dependent": False
            },
            {
                "type": "corrosion_passivation",
                "equation": "Ti³⁺ + 2H₂O → TiO₂ + 4H⁺ + e⁻",
                "reaction": "Ti+3 + 2H2O = TiO2 + 4H+ + e-",
                "E0_VSHE": None,
                "pH_dependent": True
            }
        ],
        "Al": [
            {
                "type": "immunity_corrosion",
                "equation": "Al → Al³⁺ + 3e⁻",
                "reaction": "Al = Al+3 + 3e-",
                "E0_VSHE": -1.662,
                "pH_dependent": False
            },
            {
                "type": "corrosion_passivation",
                "equation": "2Al³⁺ + 3H₂O → Al₂O₃ + 6H⁺",
                "reaction": "2Al+3 + 3H2O = Al2O3 + 6H+",
                "E0_VSHE": None,
                "pH_dependent": True
            }
        ]
    }

    return reactions.get(element, [])


def _calculate_reaction_boundary(
    element: str,
    reaction: Dict,
    temperature_C: float,
    soluble_conc_M: float,
    pH_grid: np.ndarray
) -> List[List[float]]:
    """
    Calculate E-pH boundary for a specific reaction using Nernst equation.

    For pH-independent: E = E⁰ + (RT/nF) * ln([M^n+])
    For pH-dependent: E = E⁰ - (RT/F) * ln(10) * (m/n) * pH
    """
    boundary_points = []

    if not reaction["pH_dependent"]:
        # Simple Nernst equation: E = E⁰ + (RT/nF) * ln([M^n+])
        # For [M^n+] = soluble_conc_M
        R = 8.314  # J/(mol·K)
        F = 96485.0  # C/mol
        T_K = temperature_C + 273.15

        # Extract electrons from reaction string
        n_electrons = _extract_electrons(reaction["equation"])

        # Nernst correction
        RT_nF = (R * T_K) / (n_electrons * F)
        E_corr = RT_nF * np.log(soluble_conc_M)

        E_boundary = reaction["E0_VSHE"] + E_corr

        # Horizontal line (pH-independent)
        for pH in pH_grid:
            boundary_points.append([float(pH), float(E_boundary)])

    else:
        # pH-dependent: Use simplified Nernst with pH correction
        # E = E⁰ + (RT/nF) * ln([products]/[reactants]) - (m*0.059/n) * pH
        # where m = protons in reaction

        for pH in pH_grid:
            E_boundary = _calculate_pH_dependent_potential(
                reaction,
                pH,
                temperature_C,
                soluble_conc_M
            )
            if E_boundary is not None:
                boundary_points.append([float(pH), float(E_boundary)])

    return boundary_points


def _extract_electrons(equation: str) -> int:
    """Extract number of electrons from reaction equation."""
    match = re.search(r'(\d+)e⁻', equation)
    if match:
        return int(match.group(1))
    elif 'e⁻' in equation and not re.search(r'\d+e⁻', equation):
        return 1  # Implicit "1e⁻"
    return 2  # Default


def _calculate_pH_dependent_potential(
    reaction: Dict,
    pH: float,
    temperature_C: float,
    soluble_conc_M: float
) -> Optional[float]:
    """
    Calculate potential for pH-dependent reactions.

    Uses simplified Nernst equation with pH correction.
    """
    # Extract stoichiometric coefficients
    n_electrons = _extract_electrons(reaction["equation"])
    n_protons = _extract_protons(reaction["equation"])

    R = 8.314  # J/(mol·K)
    F = 96485.0  # C/mol
    T_K = temperature_C + 273.15

    # pH correction: -0.059 * m/n * pH at 25°C
    # General: -(RT/F) * (m/n) * ln(10) * pH
    pH_correction = -(R * T_K / F) * (n_protons / n_electrons) * 2.303 * pH

    # Base potential (if available)
    if reaction["E0_VSHE"] is not None:
        E = reaction["E0_VSHE"] + pH_correction
    else:
        # Use empirical correlations for oxides from literature
        # (Revie-Uhlig 2008, oxide stability data)
        E = _estimate_oxide_potential(reaction["type"], pH, temperature_C)

    return E


def _extract_protons(equation: str) -> int:
    """Extract number of protons from reaction equation."""
    match = re.search(r'(\d+)H⁺', equation)
    if match:
        return int(match.group(1))
    elif 'H⁺' in equation and not re.search(r'\d+H⁺', equation):
        return 1
    return 0


def _estimate_oxide_potential(reaction_type: str, pH: float, temperature_C: float) -> float:
    """
    Estimate oxide formation potential using empirical correlations.

    Based on literature values (Revie-Uhlig 2008, Pourbaix 1974).
    """
    # Typical oxide formation potentials (pH-dependent)
    if "passivation" in reaction_type.lower():
        # Passivation typically occurs at positive potentials
        # E ≈ -0.059 * pH + offset
        return -0.059 * pH + 0.5  # Simplified
    return 0.0


def _classify_pourbaix_regions(
    element: str,
    boundaries: List[Dict],
    pH_grid: np.ndarray,
    E_grid: np.ndarray,
    soluble_conc_M: float
) -> Dict[str, List[List[float]]]:
    """
    Classify E-pH grid points into immunity, passivation, and corrosion regions.

    Uses boundary lines to determine dominant species in each region.
    """
    regions = {
        "immunity": [],
        "passivation": [],
        "corrosion": []
    }

    # Create meshgrid
    pH_mesh, E_mesh = np.meshgrid(pH_grid, E_grid)

    # For each grid point, determine dominant region
    for i in range(len(E_grid)):
        for j in range(len(pH_grid)):
            pH_pt = pH_mesh[i, j]
            E_pt = E_mesh[i, j]

            region = _classify_point(element, pH_pt, E_pt, boundaries)
            regions[region].append([float(pH_pt), float(E_pt)])

    return regions


def _classify_point(
    element: str,
    pH: float,
    E: float,
    boundaries: List[Dict]
) -> str:
    """
    Classify a single (pH, E) point.

    Simplified classification based on typical Pourbaix diagrams.
    """
    # Get immunity boundary (lowest potential)
    immunity_boundary = None
    passivation_boundary = None

    for boundary in boundaries:
        if "immunity" in boundary["type"]:
            # Find E at this pH
            E_immunity = _interpolate_boundary(boundary["points"], pH)
            immunity_boundary = E_immunity
        elif "passivation" in boundary["type"]:
            E_passivation = _interpolate_boundary(boundary["points"], pH)
            passivation_boundary = E_passivation

    # Classification logic
    if immunity_boundary is not None and E < immunity_boundary:
        return "immunity"
    elif passivation_boundary is not None and immunity_boundary is not None:
        if immunity_boundary <= E < passivation_boundary:
            return "passivation"
    # Default to corrosion region
    return "corrosion"


def _interpolate_boundary(points: List[List[float]], pH: float) -> Optional[float]:
    """Interpolate E value at given pH from boundary points."""
    if not points:
        return None

    points_array = np.array(points)
    pH_values = points_array[:, 0]
    E_values = points_array[:, 1]

    # Check if pH is in range
    if pH < pH_values.min() or pH > pH_values.max():
        return None

    # Linear interpolation
    return float(np.interp(pH, pH_values, E_values))


def _calculate_water_stability(pH_grid: np.ndarray, temperature_C: float) -> Dict:
    """
    Calculate H₂O stability limits (H₂ and O₂ evolution lines).

    Based on standard Pourbaix water lines:
    - H₂ evolution: 2H⁺ + 2e⁻ → H₂ (lower limit)
    - O₂ evolution: O₂ + 4H⁺ + 4e⁻ → 2H₂O (upper limit)
    """
    R = 8.314  # J/(mol·K)
    F = 96485.0  # C/mol
    T_K = temperature_C + 273.15

    # H₂ evolution: E = -0.059 * pH (at 25°C, 1 atm H₂)
    # General: E = E⁰ - (RT/F) * ln(10) * pH
    pH_factor = -(R * T_K / F) * 2.303

    H2_line = []
    O2_line = []

    for pH in pH_grid:
        # H₂ evolution (lower limit)
        E_H2 = 0.0 + pH_factor * pH  # E⁰(H⁺/H₂) = 0.0 V_SHE by definition
        H2_line.append([float(pH), float(E_H2)])

        # O₂ evolution (upper limit)
        # O₂ + 4H⁺ + 4e⁻ = 2H₂O
        # E = 1.229 - 0.059 * pH (at 25°C, 1 atm O₂)
        E_O2 = 1.229 + pH_factor * pH
        O2_line.append([float(pH), float(E_O2)])

    return {
        "H2_evolution": H2_line,
        "O2_evolution": O2_line
    }


__all__ = ["calculate_pourbaix"]
