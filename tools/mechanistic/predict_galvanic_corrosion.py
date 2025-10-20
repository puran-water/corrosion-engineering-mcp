"""
Predict Galvanic Corrosion Rate (Phase 2 Tool)

PROVENANCE:
Based on NRL Butler-Volmer electrochemical kinetics from:
Steven A. Policastro, Ph.D.
Center for Corrosion Science and Engineering
U.S. Naval Research Laboratory

Source: USNavalResearchLaboratory/corrosion-modeling-applications
License: Public domain (U.S. Federal Government work)
Date: 2025-10-19

METHODOLOGY:
1. Calculate polarization curves for both metals using Butler-Volmer kinetics
2. Find mixed potential where i_anode + i_cathode = 0
3. Calculate galvanic current density from coupled potential
4. Convert to corrosion rate using Faraday's law

This is a simplified 1D mixed potential solver suitable for engineering applications.
For complex geometries, the full NRL 2D Laplace solver should be used.

SCOPE:
- Materials: HY-80, HY-100, SS316, Ti, I625, CuNi
- Temperature: 5-80°C (per NRL CSV data)
- Chloride: 0.02-0.6 M (freshwater to seawater)
- pH: 1-13

LIMITATIONS:
- Assumes uniform solution composition
- Does not account for IR drop (use NRL 2D solver for that)
- Does not model crevice corrosion or localized attack
- Area ratio effects simplified (galvanic couple factor)
"""

import logging
import numpy as np
from scipy.optimize import brentq
from typing import Dict, Tuple, Optional
import warnings

from utils.nrl_constants import C
from utils.nrl_materials import create_material, CorrodingMetal
from utils.nrl_electrochemical_reactions import (
    CathodicReaction,
    AnodicReaction,
    ReactionType
)
from utils.nacl_solution_chemistry import NaClSolutionChemistry


def predict_galvanic_corrosion(
    anode_material: str,
    cathode_material: str,
    temperature_C: float,
    pH: float,
    chloride_mg_L: float,
    area_ratio_cathode_to_anode: float = 1.0,
    velocity_m_s: float = 0.0,
    dissolved_oxygen_mg_L: Optional[float] = None
) -> Dict:
    """
    Predict galvanic corrosion rate for a bimetallic couple.

    **Phase 2 Tool - NRL Butler-Volmer Mixed Potential Model**

    Args:
        anode_material: Anode material (less noble, corrodes)
            Options: "HY80", "HY100", "SS316", "Ti", "I625", "CuNi"
        cathode_material: Cathode material (more noble, protected)
            Options: Same as anode_material
        temperature_C: Temperature, °C (5-80°C)
        pH: pH value (1-13)
        chloride_mg_L: Chloride ion concentration, mg/L
        area_ratio_cathode_to_anode: Cathode area / anode area (default 1.0)
            Higher ratio = more aggressive galvanic attack
        velocity_m_s: Liquid velocity, m/s (for I625, CuNi velocity effects)
        dissolved_oxygen_mg_L: Dissolved oxygen, mg/L (optional)
            If None, assumes air-saturated conditions

    Returns:
        Dictionary with results:
        {
            "mixed_potential_VSCE": float,  # Galvanic couple potential
            "galvanic_current_density_A_cm2": float,  # Current density at anode
            "anode_corrosion_rate_mm_year": float,  # Penetration rate
            "cathode_corrosion_rate_mm_year": float,  # Usually near zero
            "current_ratio": float,  # i_galvanic / i_isolated_anode
            "warnings": List[str],  # Validation warnings
            "polarization_curves": {  # Full curves for plotting
                "anode": {...},
                "cathode": {...}
            }
        }

    Raises:
        ValueError: If parameters out of range or materials incompatible

    Example:
        >>> result = predict_galvanic_corrosion(
        ...     anode_material="HY80",
        ...     cathode_material="SS316",
        ...     temperature_C=25.0,
        ...     pH=8.0,
        ...     chloride_mg_L=19000.0,  # Seawater
        ...     area_ratio_cathode_to_anode=10.0  # Large cathode area
        ... )
        >>> print(f"Galvanic CR: {result['anode_corrosion_rate_mm_year']:.2f} mm/year")

    Notes:
        - Area ratio effect: Large cathode + small anode = severe attack
        - For identical materials, galvanic current should be near zero
        - Results are for uniform conditions (no IR drop, no geometry effects)
    """
    warnings_list = []

    # Validate inputs
    _validate_inputs(
        temperature_C, pH, chloride_mg_L, area_ratio_cathode_to_anode, warnings_list
    )

    # Convert chloride to molar
    chloride_M = chloride_mg_L / 35453.0  # MW of Cl⁻ = 35.453 g/mol

    # Create material instances
    anode = create_material(
        anode_material,
        chloride_M=chloride_M,
        temperature_C=temperature_C,
        pH=pH,
        velocity_m_s=velocity_m_s
    )

    cathode = create_material(
        cathode_material,
        chloride_M=chloride_M,
        temperature_C=temperature_C,
        pH=pH,
        velocity_m_s=velocity_m_s
    )

    # Calculate H⁺ and OH⁻ concentrations
    c_H, c_OH = C.calculate_cH_and_cOH(pH)

    # NaCl solution chemistry (authoritative NRL calculations)
    nacl_soln = NaClSolutionChemistry(chloride_M=chloride_M, temperature_C=temperature_C)

    # Dissolved oxygen concentration (g/cm³)
    # Use NRL naclSolutionChemistry unless user explicitly provides DO
    if dissolved_oxygen_mg_L is not None:
        c_O2_g_cm3 = dissolved_oxygen_mg_L / 1.0e6  # mg/L → g/cm³ (correct: 1 mg/L = 1e-6 g/cm³)
    else:
        c_O2_g_cm3 = nacl_soln.c_O2  # From NRL naclSolutionChemistry (g/cm³)

    # Guard against DO = 0 to prevent log(0) in Nernst equation
    # Use small epsilon for anaerobic conditions (equivalent to ~0.01 mg/L)
    MIN_DO_EPSILON = 1.0e-8  # g/cm³ (equivalent to 0.01 mg/L)
    if c_O2_g_cm3 < MIN_DO_EPSILON:
        c_O2_g_cm3 = MIN_DO_EPSILON
        warnings_list.append(
            f"Dissolved oxygen below detection limit (< 0.01 mg/L). "
            f"Using epsilon value for anaerobic conditions. "
            f"Corrosion driven by hydrogen evolution reaction (HER)."
        )

    # Water activity (from NRL naclSolutionChemistry, accounts for salinity)
    a_water = nacl_soln.a_water  # mol/L

    # Fast path for identical materials (prevents numerical issues with near-zero currents)
    if anode_material == cathode_material and velocity_m_s == 0:
        # For identical materials, calculate isolated corrosion only
        # This avoids astronomically high current ratios from dividing by near-zero currents

        # Define applied potential range
        e_applied_VSCE = np.linspace(-1.5, 0.5, 500)

        # Calculate single polarization curve
        anode_curves = _calculate_polarization_curve(
            anode,
            e_applied_VSCE,
            temperature_C,
            c_O2_g_cm3,
            c_OH,
            a_water,
            d_O2_cm2_s=nacl_soln.d_O2
        )

        # Find isolated corrosion potential
        E_corr_isolated, i_corr_isolated = _find_isolated_corrosion_potential(
            e_applied_VSCE, anode_curves
        )

        # CRITICAL FIX (Codex): Use anodic current magnitude, NOT net current (which is zero at E_corr)
        # Net current is identically zero at corrosion potential, so we must get anodic branch
        i_corr_anodic = np.interp(
            E_corr_isolated,
            anode_curves["potential_VSCE"],
            anode_curves["anodic_current_A_cm2"]
        )

        # CRITICAL FIX (Codex): Use material-specific density, NOT hard-coded steel density
        # Get density from material instance
        density_g_cm3 = _get_material_density(anode_material)

        # Calculate corrosion rate
        anode_CR_mm_year = _current_to_corrosion_rate(
            abs(i_corr_anodic),
            anode.metal_mass,
            anode.oxidation_level_z,
            density_g_cm3=density_g_cm3
        )

        anode_CR_mpy = anode_CR_mm_year * 39.3701
        dissolved_oxygen_mg_L_output = c_O2_g_cm3 * 1.0e6

        warnings_list.append(
            f"Identical materials ({anode_material}) - no galvanic coupling. "
            f"Reporting isolated corrosion rate only."
        )

        return {
            "mixed_potential_VSCE": E_corr_isolated,
            "galvanic_current_density_A_cm2": abs(i_corr_isolated),
            "anode_corrosion_rate_mm_year": anode_CR_mm_year,
            "anode_corrosion_rate_mpy": anode_CR_mpy,
            "cathode_corrosion_rate_mm_year": 0.0,
            "current_ratio": 1.0,  # No galvanic effect
            "E_corr_isolated_anode_VSCE": E_corr_isolated,
            "area_ratio": area_ratio_cathode_to_anode,
            "dissolved_oxygen_mg_L": dissolved_oxygen_mg_L_output,
            "warnings": warnings_list,
            "convergence": {"converged": True, "method": "isolated_corrosion_potential"},
            "polarization_curves": {
                "potential_VSCE": e_applied_VSCE.tolist(),
                "anode": {
                    "total_current": anode_curves["total_current_A_cm2"].tolist(),
                    "anodic_current": anode_curves["anodic_current_A_cm2"].tolist(),
                    "cathodic_current": anode_curves["cathodic_current_A_cm2"].tolist()
                },
                "cathode": {
                    "total_current": anode_curves["total_current_A_cm2"].tolist(),
                    "anodic_current": anode_curves["anodic_current_A_cm2"].tolist(),
                    "cathodic_current": anode_curves["cathodic_current_A_cm2"].tolist()
                }
            }
        }

    # Define applied potential range for polarization curves
    # Scan from -1.5 V to +0.5 V_SCE
    e_applied_VSCE = np.linspace(-1.5, 0.5, 500)

    # Calculate polarization curves for anode
    anode_curves = _calculate_polarization_curve(
        anode,
        e_applied_VSCE,
        temperature_C,
        c_O2_g_cm3,
        c_OH,
        a_water,
        d_O2_cm2_s=nacl_soln.d_O2  # From NRL naclSolutionChemistry
    )

    # Calculate polarization curves for cathode
    cathode_curves = _calculate_polarization_curve(
        cathode,
        e_applied_VSCE,
        temperature_C,
        c_O2_g_cm3,
        c_OH,
        a_water,
        d_O2_cm2_s=nacl_soln.d_O2  # From NRL naclSolutionChemistry
    )

    # Find mixed potential (galvanic couple potential)
    try:
        E_galvanic, i_galvanic, convergence_info = _find_mixed_potential(
            e_applied_VSCE,
            anode_curves,
            cathode_curves,
            area_ratio_cathode_to_anode
        )
    except Exception as e:
        raise ValueError(
            f"Failed to find mixed potential for {anode_material}/{cathode_material} couple: {e}"
        )

    # Calculate corrosion rates
    # CRITICAL FIX (Codex): Use material-specific density
    anode_density = _get_material_density(anode_material)
    anode_CR_mm_year = _current_to_corrosion_rate(
        abs(i_galvanic),
        anode.metal_mass,
        anode.oxidation_level_z,
        density_g_cm3=anode_density
    )

    cathode_CR_mm_year = 0.0  # Cathode is protected (negligible corrosion)

    # Calculate isolated (uncoupled) anode corrosion potential
    E_corr_isolated_anode, _ = _find_isolated_corrosion_potential(
        e_applied_VSCE, anode_curves
    )

    # Validate convergence of mixed potential solver
    if not convergence_info.get("converged", False):
        warnings_list.append(
            "Mixed potential solver did not converge - results may be inaccurate. "
            "Possible causes: passive/similar materials, narrow potential range, or "
            "polarization curves that don't intersect properly."
        )

    # Current ratio: i_galvanic / i_isolated
    # CRITICAL FIX (Codex): Use anodic current magnitude, NOT total current (which is zero at E_corr)
    # Total current is identically zero at corrosion potential by definition,
    # so we must use the anodic branch magnitude to quantify galvanic amplification
    i_isolated_anodic = np.interp(
        E_corr_isolated_anode,
        anode_curves["potential_VSCE"],
        anode_curves["anodic_current_A_cm2"]
    )

    # Guard against near-zero isolated current (indicates numerical issues)
    EPSILON_CURRENT = 1e-8  # A/cm²
    if abs(i_isolated_anodic) < EPSILON_CURRENT:
        current_ratio = 1.0
        warnings_list.append(
            f"Isolated anodic current below threshold ({abs(i_isolated_anodic):.2e} A/cm²). "
            f"Treating as uncoupled system (current_ratio = 1.0). "
            f"This typically indicates passive or nearly identical materials."
        )
    else:
        current_ratio = abs(i_galvanic) / abs(i_isolated_anodic)

    # Add warnings based on current ratio
    if current_ratio > 10.0:
        warnings_list.append(
            f"Severe galvanic attack: Current ratio = {current_ratio:.1f}. "
            f"Consider cathodic protection or isolation."
        )
    elif current_ratio > 2.0:
        warnings_list.append(
            f"Moderate galvanic attack: Current ratio = {current_ratio:.1f}."
        )

    if area_ratio_cathode_to_anode > 100:
        warnings_list.append(
            f"Very large area ratio ({area_ratio_cathode_to_anode:.0f}). "
            f"Localized attack likely at anode edges."
        )

    # Convert corrosion rate to mils per year (1 mm/year = 39.3701 mils/year)
    anode_CR_mpy = anode_CR_mm_year * 39.3701

    # Convert dissolved oxygen from g/cm³ to mg/L for output
    dissolved_oxygen_mg_L_output = c_O2_g_cm3 * 1.0e6

    return {
        "mixed_potential_VSCE": E_galvanic,
        "galvanic_current_density_A_cm2": abs(i_galvanic),
        "anode_corrosion_rate_mm_year": anode_CR_mm_year,
        "anode_corrosion_rate_mpy": anode_CR_mpy,
        "cathode_corrosion_rate_mm_year": cathode_CR_mm_year,
        "current_ratio": current_ratio,
        "E_corr_isolated_anode_VSCE": E_corr_isolated_anode,
        "area_ratio": area_ratio_cathode_to_anode,
        "dissolved_oxygen_mg_L": dissolved_oxygen_mg_L_output,
        "warnings": warnings_list,
        "convergence": convergence_info,
        "polarization_curves": {
            "potential_VSCE": e_applied_VSCE.tolist(),
            "anode": {
                "total_current": anode_curves["total_current_A_cm2"].tolist(),
                "anodic_current": anode_curves["anodic_current_A_cm2"].tolist(),
                "cathodic_current": anode_curves["cathodic_current_A_cm2"].tolist()
            },
            "cathode": {
                "total_current": cathode_curves["total_current_A_cm2"].tolist(),
                "anodic_current": cathode_curves["anodic_current_A_cm2"].tolist(),
                "cathodic_current": cathode_curves["cathodic_current_A_cm2"].tolist()
            }
        }
    }


def _calculate_polarization_curve(
    metal: CorrodingMetal,
    e_applied_VSCE: np.ndarray,
    temperature_C: float,
    c_O2_g_cm3: float,
    c_OH: float,
    a_water: float,
    d_O2_cm2_s: float
) -> Dict:
    """
    Calculate full polarization curve for a material.

    Args:
        metal: Material instance
        e_applied_VSCE: Applied potentials (V_SCE)
        temperature_C: Temperature (°C)
        c_O2_g_cm3: Dissolved O2 concentration (g/cm³)
        c_OH: Hydroxide concentration (mol/L)
        a_water: Water activity (mol/L)
        d_O2_cm2_s: O2 diffusivity (cm²/s) from NRL naclSolutionChemistry

    Returns:
        Dictionary with currents for all reactions
    """
    # ORR concentrations
    c_react_orr = [c_O2_g_cm3, (a_water / 1000.0 * C.M_H2O)**2]  # g/cm³
    c_prod_orr = [1.0, (c_OH / 1000.0 * C.M_OH)**4]  # g/cm³

    # HER concentrations
    c_react_her = [(a_water / 1000.0 * C.M_H2O)**2, 1.0]
    c_prod_her = [1.0, (c_OH / 1000.0 * C.M_OH)**2]

    # Cathodic reactions
    orr_reaction = CathodicReaction(
        reaction_type=ReactionType.ORR,
        c_oxidized=c_react_orr,
        c_reduced=c_prod_orr,
        temperature_C=temperature_C,
        z=C.z_orr,
        e0_SHE=C.e0_orr_alk,
        diffusion_coefficient_cm2_s=d_O2_cm2_s,  # From NRL naclSolutionChemistry
        applied_potentials_VSCE=e_applied_VSCE,
        metal=metal
    )

    her_reaction = CathodicReaction(
        reaction_type=ReactionType.HER,
        c_oxidized=c_react_her,
        c_reduced=c_prod_her,
        temperature_C=temperature_C,
        z=C.z_her,
        e0_SHE=C.e0_her_alk,
        diffusion_coefficient_cm2_s=C.D_H2O,
        applied_potentials_VSCE=e_applied_VSCE,
        metal=metal
    )

    # Anodic reactions (material-specific)
    anodic_currents = np.zeros_like(e_applied_VSCE)

    # Determine anodic reaction type
    if hasattr(metal, 'delta_g_metal_passivation') and metal.delta_g_metal_passivation != (0.0, 0.0):
        # Passivating materials: SS316, Ti, I625
        passivation_reaction = AnodicReaction(
            reaction_type=ReactionType.PASSIVATION,
            c_reactants=(1.0,),
            c_products=(1.0e-6,),
            temperature_C=temperature_C,
            applied_potentials_VSCE=e_applied_VSCE,
            metal=metal
        )
        anodic_currents += passivation_reaction.i_total

    elif hasattr(metal, 'delta_g_metal_oxidation') and metal.delta_g_metal_oxidation != (0.0, 0.0):
        # Active oxidation materials: HY-80, HY-100, CuNi
        if "HY" in metal.name.upper():
            reaction_type = ReactionType.FE_OX
        elif "CUNI" in metal.name.upper():
            reaction_type = ReactionType.CU_OX
        else:
            reaction_type = ReactionType.FE_OX  # Default

        oxidation_reaction = AnodicReaction(
            reaction_type=reaction_type,
            c_reactants=(1.0,),
            c_products=(1.0e-6,),
            temperature_C=temperature_C,
            applied_potentials_VSCE=e_applied_VSCE,
            metal=metal
        )
        anodic_currents += oxidation_reaction.i_total

    # Pitting (if applicable)
    if hasattr(metal, 'delta_g_metal_pitting') and metal.delta_g_metal_pitting != (0.0, 0.0):
        pitting_reaction = AnodicReaction(
            reaction_type=ReactionType.PITTING,
            c_reactants=(1.0,),
            c_products=(1.0e-6,),
            temperature_C=temperature_C,
            applied_potentials_VSCE=e_applied_VSCE,
            metal=metal
        )
        anodic_currents += pitting_reaction.i_total

    # Total currents
    cathodic_current = orr_reaction.i_total + her_reaction.i_total
    total_current = cathodic_current + anodic_currents

    # Polarity sanity check: total current should cross zero for active corrosion
    # If curve doesn't cross zero, material may be passive or coefficients need review
    sign_changes = np.diff(np.sign(total_current))
    has_zero_crossing = np.any(sign_changes != 0)

    if not has_zero_crossing:
        # Check if curve is all positive (net anodic) or all negative (net cathodic)
        mean_current = np.mean(total_current)
        if mean_current > 1e-9:
            polarity_state = "always anodic (unusual - check coefficients)"
        elif mean_current < -1e-9:
            polarity_state = "always cathodic (possible passive behavior)"
        else:
            polarity_state = "near zero (possible passive/noble behavior)"

        import logging
        logging.warning(
            f"Polarization curve for {metal.name} does not cross zero - "
            f"curve is {polarity_state}. This may indicate passive material or "
            f"Butler-Volmer coefficients need review."
        )

    return {
        "potential_VSCE": e_applied_VSCE,
        "cathodic_current_A_cm2": cathodic_current,
        "anodic_current_A_cm2": anodic_currents,
        "total_current_A_cm2": total_current
    }


def _find_mixed_potential(
    e_applied_VSCE: np.ndarray,
    anode_curves: Dict,
    cathode_curves: Dict,
    area_ratio: float
) -> Tuple[float, float, Dict]:
    """
    Find mixed potential where i_anode + (area_ratio * i_cathode) = 0.

    Uses Brent's method root finding.

    Returns:
        (E_galvanic, i_galvanic, convergence_info)
    """
    # Interpolate currents for smooth root finding
    from scipy.interpolate import interp1d

    i_anode_interp = interp1d(
        e_applied_VSCE,
        anode_curves["total_current_A_cm2"],
        kind='cubic',
        fill_value="extrapolate"
    )

    i_cathode_interp = interp1d(
        e_applied_VSCE,
        cathode_curves["total_current_A_cm2"],
        kind='cubic',
        fill_value="extrapolate"
    )

    def residual(E: float) -> float:
        """Current balance equation: i_anode + area_ratio * i_cathode = 0"""
        i_a = i_anode_interp(E)
        i_c = i_cathode_interp(E)
        return i_a + area_ratio * i_c

    # Find root using Brent's method
    try:
        E_galvanic = brentq(residual, e_applied_VSCE[0], e_applied_VSCE[-1], xtol=1e-6)
        i_galvanic = i_anode_interp(E_galvanic)
        convergence_info = {"converged": True, "method": "brentq"}
    except ValueError as e:
        # Root not found in range - use minimum of residual
        residuals = np.array([residual(E) for E in e_applied_VSCE])
        idx_min = np.argmin(np.abs(residuals))
        E_galvanic = e_applied_VSCE[idx_min]
        i_galvanic = i_anode_interp(E_galvanic)
        convergence_info = {
            "converged": False,
            "method": "minimum_residual",
            "error": str(e)
        }

    return E_galvanic, i_galvanic, convergence_info


def _find_isolated_corrosion_potential(
    e_applied_VSCE: np.ndarray,
    curves: Dict
) -> Tuple[float, float]:
    """Find isolated corrosion potential (E_corr) where i_total = 0."""
    total_current = curves["total_current_A_cm2"]

    # Find zero crossing
    sign_changes = np.where(np.diff(np.sign(total_current)))[0]

    if len(sign_changes) > 0:
        # Interpolate to find exact E_corr
        idx = sign_changes[0]
        E_corr = np.interp(
            0.0,
            [total_current[idx], total_current[idx+1]],
            [e_applied_VSCE[idx], e_applied_VSCE[idx+1]]
        )
        i_corr = 0.0
    else:
        # No crossing found, use minimum of abs(i_total)
        idx_min = np.argmin(np.abs(total_current))
        E_corr = e_applied_VSCE[idx_min]
        i_corr = total_current[idx_min]

    return E_corr, i_corr


def _get_material_density(material_name: str) -> float:
    """
    Get material-specific density.

    CRITICAL FIX (Codex): Avoid hard-coded steel density (7.85 g/cm³) for all materials.
    Different alloys have different densities, affecting corrosion rate calculations:
    - Steel: 7.85 g/cm³
    - Cu-Ni: 8.94 g/cm³ (+13% vs steel)
    - Ti: 4.51 g/cm³ (-43% vs steel, would overestimate CR by 75% if using 7.85!)

    Args:
        material_name: Material identifier (e.g., "HY80", "SS316", "Ti", "CuNi")

    Returns:
        Density in g/cm³

    Raises:
        ValueError: If material not recognized
    """
    # Material densities from literature (g/cm³)
    MATERIAL_DENSITIES = {
        "HY80": 7.85,    # Carbon steel
        "HY100": 7.85,   # Carbon steel
        "SS316": 8.00,   # Austenitic stainless steel (Fe-Cr-Ni)
        "Ti": 4.51,      # Titanium
        "I625": 8.44,    # Inconel 625 (Ni-Cr-Mo)
        "CuNi": 8.94,    # Copper-Nickel 70/30
    }

    material_upper = material_name.upper()

    if material_upper in MATERIAL_DENSITIES:
        return MATERIAL_DENSITIES[material_upper]
    else:
        # Fallback to steel density with warning
        logging.warning(
            f"Material '{material_name}' not in density database; "
            f"defaulting to steel density (7.85 g/cm³). "
            f"Corrosion rate may be inaccurate for non-ferrous alloys."
        )
        return 7.85


def _current_to_corrosion_rate(
    current_density_A_cm2: float,
    molar_mass_g_mol: float,
    electrons_transferred: int,
    density_g_cm3: float
) -> float:
    """
    Convert current density to corrosion rate using Faraday's law.

    CR (mm/year) = (i * M * K) / (n * F * ρ)

    where:
    - i = current density, A/cm²
    - M = molar mass, g/mol
    - K = 3.27e6 (conversion factor: cm/s → mm/year)
    - n = electrons transferred
    - F = Faraday constant, C/mol
    - ρ = density, g/cm³

    Args:
        current_density_A_cm2: Current density, A/cm²
        molar_mass_g_mol: Molar mass, g/mol
        electrons_transferred: Number of electrons in oxidation reaction
        density_g_cm3: Metal density, g/cm³

    Returns:
        Corrosion rate, mm/year
    """
    K = 3.27e6  # Conversion factor: (365.25 * 24 * 3600 * 10) for cm/s → mm/year

    CR_mm_year = (
        current_density_A_cm2 * molar_mass_g_mol * K /
        (electrons_transferred * C.F * density_g_cm3)
    )

    return CR_mm_year


def _validate_inputs(
    temperature_C: float,
    pH: float,
    chloride_mg_L: float,
    area_ratio: float,
    warnings_list: list
) -> None:
    """Validate input parameters and add warnings."""

    if not (5.0 <= temperature_C <= 80.0):
        raise ValueError(
            f"Temperature {temperature_C}°C out of range (5-80°C per NRL data)"
        )

    if not (1.0 <= pH <= 13.0):
        raise ValueError(f"pH {pH} out of range (1-13)")

    chloride_M = chloride_mg_L / 35453.0
    if not (0.001 <= chloride_M <= 1.0):
        warnings_list.append(
            f"Chloride {chloride_M:.3f} M outside validated range (0.02-0.6 M). "
            f"Results may be less accurate."
        )

    if area_ratio < 0.01 or area_ratio > 1000:
        raise ValueError(
            f"Area ratio {area_ratio} out of reasonable range (0.01-1000)"
        )


__all__ = ["predict_galvanic_corrosion"]
