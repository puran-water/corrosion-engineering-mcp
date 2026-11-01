"""Withdrawn galvanic mixed-potential accessor.

The former calculation reversed the cathode/anode area-ratio response and did
not preserve the declared electrode polarity across its stated pH range. The
repository's NRL comparison curves are placeholders, not validation data.
``predict_galvanic_corrosion`` therefore raises
``GalvanicModelWithdrawnError`` before model-specific scope or orientation
checks. Schema validation remains the transport boundary, but every call that
reaches this withdrawn accessor has the same outcome. It emits no rate, current,
polarity, ratio, assessment, recommendation, or validation claim.

Lower-level polarization helpers remain private for forensic unit tests. They
are not an alternative prediction accessor and are not exported by ``__all__``.
"""

import logging
import numpy as np
from scipy.optimize import brentq
from typing import Dict, NoReturn, Tuple, Optional
import warnings

from corrosion_engineering_mcp.core.errors import CorrosionPublicError
from corrosion_engineering_mcp.core.validation import DISSOLVED_OXYGEN_MG_L, require_bounds
from corrosion_engineering_mcp.utils.nrl_constants import C
from corrosion_engineering_mcp.utils.nrl_materials import CorrodingMetal
from corrosion_engineering_mcp.utils.nrl_electrochemical_reactions import CathodicReaction, AnodicReaction, ReactionType


NRL_SCOPE_TEMPERATURE_C = (5.0, 80.0)
NRL_SCOPE_CHLORIDE_MG_L = (5000.0, 30000.0)
NRL_SCOPE_PH = (6.0, 9.0)
NRL_SCOPE_MIN_DO_MG_L = 0.5
HER_CATHODIC_LIMIT_A_CM2 = 1.0e-3

GALVANIC_POTENTIAL_VSCE = {
    "HY80": -0.600,
    "HY100": -0.600,
    "CuNi": -0.250,
    "Ti": -0.100,
    "SS316": -0.050,
    "I625": -0.030,
}


class NRLSeawaterScopeViolation(CorrosionPublicError, ValueError):
    """Raised when a requested chemistry is outside the NRL seawater model."""

    def __init__(self, violations: list[str], alternative: str) -> None:
        super().__init__(
            "OUT_OF_SCOPE",
            "NRL_SEAWATER_SCOPE_VIOLATION: "
            + "; ".join(violations)
            + f". Alternative: {alternative}.",
            details={
                "reason_code": "NRL_SEAWATER_SCOPE_VIOLATION",
                "violations": violations,
                "alternative": alternative,
            },
        )


class GalvanicDirectionError(CorrosionPublicError, ValueError):
    """Raised when the declared anode/cathode direction is reversed."""

    def __init__(self, anode_material: str, cathode_material: str) -> None:
        super().__init__(
            "input_direction_invalid",
            f"input_direction_invalid: declared anode {anode_material} is not less noble "
            f"than declared cathode {cathode_material}. Set allow_auto_orient=True to swap.",
            details={
                "anode_material": anode_material,
                "cathode_material": cathode_material,
                "anode_potential_VSCE": GALVANIC_POTENTIAL_VSCE.get(anode_material),
                "cathode_potential_VSCE": GALVANIC_POTENTIAL_VSCE.get(cathode_material),
            },
        )


class GalvanicModelWithdrawnError(CorrosionPublicError, ValueError):
    """Raised on every prediction call while the mixed-potential model is unsafe."""

    def __init__(self) -> None:
        super().__init__(
            "TOOL_WITHDRAWN",
            "TOOL_WITHDRAWN: corrosion_assess_galvanic is disabled because the "
            "declared electrode polarity is not preserved over its stated pH "
            "scope and the cathode/anode area-ratio response is physically "
            "reversed. The repository's NRL experiment curves are placeholders, "
            "so no numerical output or assessment is validated for release.",
            details={
                "reason_code": "TOOL_WITHDRAWN",
                "unsafe_outputs": [
                    "electrode_polarity",
                    "mixed_potential",
                    "galvanic_current",
                    "anode_corrosion_rate",
                    "area_ratio_response",
                    "galvanic_amplification_assessment",
                    "recommendations",
                ],
                "validation_status": "placeholder_curves_not_validation_data",
                "requires_to_reenable": [
                    "correct and independently review the electrode current signs",
                    "demonstrate monotonic severe geometry response over pH 6-9",
                    "validate rates and polarity against non-placeholder experiments",
                    "derive any assessment thresholds from a named acceptance basis",
                ],
            },
        )


def _named_alternative(
    *,
    pH: float,
    dissolved_oxygen_mg_L: Optional[float],
    sulfide_mg_L: float,
    chloride_mg_L: float,
) -> str:
    if sulfide_mg_L > 0:
        return "use a sour-service model for H2S-containing condensate or brine"
    if pH < NRL_SCOPE_PH[0]:
        return "use a strong-acid/HCl pickle corrosion model"
    if pH > 12:
        return "use a caustic corrosion model for NaOH service"
    if dissolved_oxygen_mg_L is not None and dissolved_oxygen_mg_L <= NRL_SCOPE_MIN_DO_MG_L:
        return "use a deaerated boiler/feedwater model with HER-only kinetics"
    if pH > NRL_SCOPE_PH[1]:
        return "use a model validated for alkaline boiler or process water"
    if chloride_mg_L < NRL_SCOPE_CHLORIDE_MG_L[0]:
        return "use a freshwater/brackish galvanic model"
    if chloride_mg_L > NRL_SCOPE_CHLORIDE_MG_L[1]:
        return "use a concentrated brine galvanic model"
    return "use a chemistry-specific galvanic model with validated coefficients"


def validate_nrl_seawater_scope(
    *,
    temperature_C: float,
    pH: float,
    chloride_mg_L: float,
    dissolved_oxygen_mg_L: Optional[float],
    sulfide_mg_L: float = 0.0,
) -> None:
    """Hard gate for the current NRL aerated seawater coefficient scope."""

    violations: list[str] = []
    if not (NRL_SCOPE_TEMPERATURE_C[0] <= temperature_C <= NRL_SCOPE_TEMPERATURE_C[1]):
        violations.append(
            f"temperature {repr(float(temperature_C))} C out of range "
            f"{repr(float(NRL_SCOPE_TEMPERATURE_C[0]))}-"
            f"{repr(float(NRL_SCOPE_TEMPERATURE_C[1]))} C"
        )
    if not (NRL_SCOPE_CHLORIDE_MG_L[0] <= chloride_mg_L <= NRL_SCOPE_CHLORIDE_MG_L[1]):
        violations.append(
            f"chloride {repr(float(chloride_mg_L))} mg/L out of range "
            f"{repr(float(NRL_SCOPE_CHLORIDE_MG_L[0]))}-"
            f"{repr(float(NRL_SCOPE_CHLORIDE_MG_L[1]))} mg/L"
        )
    if not (NRL_SCOPE_PH[0] <= pH <= NRL_SCOPE_PH[1]):
        violations.append(
            f"pH {repr(float(pH))} out of range "
            f"{repr(float(NRL_SCOPE_PH[0]))}-{repr(float(NRL_SCOPE_PH[1]))}"
        )
    if dissolved_oxygen_mg_L is not None and dissolved_oxygen_mg_L <= NRL_SCOPE_MIN_DO_MG_L:
        violations.append(
            "dissolved oxygen "
            f"{repr(float(dissolved_oxygen_mg_L))} mg/L is not aerated "
            f"(must be > {repr(float(NRL_SCOPE_MIN_DO_MG_L))} mg/L)"
        )
    if sulfide_mg_L > 0:
        violations.append(
            f"sulfide {repr(float(sulfide_mg_L))} mg/L is outside NRL seawater scope"
        )

    if violations:
        raise NRLSeawaterScopeViolation(
            violations,
            _named_alternative(
                pH=pH,
                dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
                sulfide_mg_L=sulfide_mg_L,
                chloride_mg_L=chloride_mg_L,
            ),
        )


def orient_galvanic_couple(
    anode_material: str,
    cathode_material: str,
    *,
    area_ratio_cathode_to_anode: float,
    allow_auto_orient: bool = False,
) -> tuple[str, str, float, bool]:
    """Validate or auto-orient the declared galvanic couple direction."""

    anode_E = GALVANIC_POTENTIAL_VSCE[anode_material]
    cathode_E = GALVANIC_POTENTIAL_VSCE[cathode_material]
    if anode_material == cathode_material or anode_E < cathode_E:
        return anode_material, cathode_material, area_ratio_cathode_to_anode, False
    if not allow_auto_orient:
        raise GalvanicDirectionError(anode_material, cathode_material)
    swapped_ratio = 1.0 / area_ratio_cathode_to_anode
    return cathode_material, anode_material, swapped_ratio, True


def predict_galvanic_corrosion(
    anode_material: str,
    cathode_material: str,
    temperature_C: float,
    pH: float,
    chloride_mg_L: float,
    area_ratio_cathode_to_anode: float = 1.0,
    velocity_m_s: float = 0.0,
    dissolved_oxygen_mg_L: Optional[float] = None,
    allow_auto_orient: bool = False,
    sulfide_mg_L: float = 0.0,
) -> NoReturn:
    """Raise withdrawal before any model-specific scope or orientation check."""
    raise GalvanicModelWithdrawnError()
def _calculate_polarization_curve(
    metal: CorrodingMetal,
    e_applied_VSCE: np.ndarray,
    temperature_C: float,
    c_O2_g_cm3: float,
    c_OH: float,
    a_water: float,
    d_O2_cm2_s: float,
    enable_orr: bool = True,
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

    if enable_orr and c_O2_g_cm3 > 0.0:
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
        orr_current = orr_reaction.i_total
    else:
        orr_current = np.zeros_like(e_applied_VSCE)

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
    her_current = np.clip(her_reaction.i_total, -HER_CATHODIC_LIMIT_A_CM2, 0.0)

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
    cathodic_current = orr_current + her_current
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
        "orr_current_A_cm2": orr_current,
        "her_current_A_cm2": her_current,
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
    - K = 3.15576e8 (conversion factor: cm/s → mm/year)
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
    # FIXED: Previous value 3.27e6 was ~96.5× too small (typo in original)
    # Correct: 365.25 * 24 * 3600 = 31,557,600 s/year; multiply by 10 for cm→mm
    SECONDS_PER_YEAR = 365.25 * 24 * 3600  # 31,557,600 s/year
    K = SECONDS_PER_YEAR * 10.0  # 3.15576e8: converts cm/s → mm/year

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
    dissolved_oxygen_mg_L: Optional[float],
    warnings_list: list
) -> None:
    """Validate input parameters and add warnings."""

    require_bounds("dissolved_oxygen_mg_L", dissolved_oxygen_mg_L, DISSOLVED_OXYGEN_MG_L)

    chloride_M = chloride_mg_L / 35453.0
    if not (0.001 <= chloride_M <= 1.0):
        warnings_list.append(
            f"Chloride {repr(float(chloride_M))} M outside validated range "
            "(0.02-0.6 M). "
            f"Results may be less accurate."
        )

    if area_ratio < 0.01 or area_ratio > 1000:
        raise ValueError(
            f"Area ratio {area_ratio} out of reasonable range (0.01-1000)"
        )


__all__ = [
    "GalvanicDirectionError",
    "GalvanicModelWithdrawnError",
    "NRLSeawaterScopeViolation",
    "orient_galvanic_couple",
    "predict_galvanic_corrosion",
    "validate_nrl_seawater_scope",
]
