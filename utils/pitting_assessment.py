"""
Electrochemical Pitting Assessment - E_pit Calculator

Calculates pitting initiation potential (E_pit) from NRL Butler-Volmer pitting kinetics.

Phase 3 enhancement:
- Integrates NRL pitting coefficients with RedoxState DO-aware E_mix calculation
- Provides ΔE = E_mix - E_pit driving force assessment
- Complements existing PREN/CPT Tier 1 heuristics with Tier 2 mechanistic model

Theory:
-------
Pitting initiates when the corrosion potential E_mix exceeds the pitting potential E_pit.
E_pit is defined as the potential where anodic pitting current reaches a threshold (typically 1 µA/cm²).

Butler-Volmer Pitting Current (Anodic Only):
    i_pit = i0_anodic * exp((alpha * z * F * eta) / (R * T))

where:
    eta = E_applied - E_N (overpotential)
    i0_anodic = z * F * lambda_0 * exp(-dG_anodic / (R * T))
    lambda_0 = (k_B * T) / h  (Eyring rate constant)

Solving for E_pit at threshold current i_threshold:
    E_pit = E_N + (R * T / (alpha * z * F)) * ln(i_threshold / i0_anodic)

Integrated with RedoxState:
    E_mix = do_to_eh(DO_mg_L, pH, T)  # From RedoxState module
    ΔE = E_mix - E_pit  # Driving force for pitting

Risk Assessment:
    ΔE > +0.05 V: CRITICAL (E_mix >> E_pit, pitting highly likely)
    ΔE > 0 V: HIGH (E_mix > E_pit, pitting thermodynamically favorable)
    -0.1 V < ΔE < 0 V: MODERATE (small margin, monitor)
    ΔE < -0.1 V: LOW (large margin, pitting unlikely)

Supported Materials (NRL database):
- HY80 (z=2, Fe oxidation)
- HY100 (z=2, Fe oxidation)
- SS316 (z=3, Cr oxidation)

Author: Codex AI + Claude Code
Date: 2025-10-19
"""

import numpy as np
from typing import Tuple, Optional
from utils.nrl_constants import C
from utils.nrl_materials import HY80, HY100, SS316


def calculate_pitting_potential(
    material_name: str,
    temperature_C: float,
    chloride_mg_L: float,
    pH: float,
    i_threshold_A_cm2: float = 1e-6,
) -> Tuple[float, dict]:
    """
    Calculate pitting initiation potential E_pit using NRL Butler-Volmer kinetics.

    Args:
        material_name: Material name ("HY80", "HY100", "SS316")
        temperature_C: Temperature (°C)
        chloride_mg_L: Chloride concentration (mg/L)
        pH: Solution pH (1-13)
        i_threshold_A_cm2: Pitting current threshold (A/cm²), default 1 µA/cm²

    Returns:
        E_pit_VSCE: Pitting initiation potential (V vs SCE)
        details: Dictionary with {
            "i0_anodic_A_cm2": Exchange current density,
            "dG_anodic_J_mol": Activation energy for pitting,
            "E_N_VSCE": Nernst potential,
            "alpha": Transfer coefficient,
            "z": Oxidation level
        }

    Raises:
        ValueError: If material not supported or parameters out of range

    Example:
        >>> E_pit, details = calculate_pitting_potential(
        ...     "SS316", temperature_C=25.0, chloride_mg_L=1000.0, pH=7.0
        ... )
        >>> print(f"E_pit = {E_pit:.3f} V_SCE")
        E_pit = 0.450 V_SCE  # Example value
    """
    # Validate inputs
    if temperature_C < 0 or temperature_C > 150:
        raise ValueError(f"Temperature {temperature_C}°C out of valid range (0-150°C)")
    if pH < 1 or pH > 13:
        raise ValueError(f"pH {pH} out of valid range (1-13)")
    if chloride_mg_L < 0:
        raise ValueError(f"Chloride concentration cannot be negative: {chloride_mg_L}")

    # Convert chloride mg/L to M (molar)
    # Cl⁻ molar mass = 35.453 g/mol
    chloride_M = (chloride_mg_L / 1000.0) / 35.453

    # Initialize material instance
    if material_name.upper() == "HY80":
        metal = HY80("HY80", chloride_M, temperature_C, pH)
    elif material_name.upper() == "HY100":
        metal = HY100("HY100", chloride_M, temperature_C, pH)
    elif material_name.upper() == "SS316":
        metal = SS316("SS316", chloride_M, temperature_C, pH)
    else:
        raise ValueError(
            f"Material '{material_name}' not supported for pitting assessment. "
            f"Supported: HY80, HY100, SS316"
        )

    # Get pitting activation energies from material
    dG_cathodic, dG_anodic = metal.delta_g_metal_pitting
    alpha = metal.beta_metal_pitting
    z = metal.oxidation_level_z

    # Calculate temperature in Kelvin
    T_K = temperature_C + C.convertCtoK

    # Eyring rate constant
    lambda_0 = (C.kb * T_K) / C.planck_h

    # Exchange current density for pitting (anodic)
    RT = C.R * T_K
    pF = z * C.F * lambda_0
    i0_anodic = pF * np.exp(-dG_anodic / RT)

    # Nernst potential for pitting reaction
    # For HY80/HY100: Fe → Fe²⁺ + 2e⁻
    # For SS316: Cr → Cr³⁺ + 3e⁻
    if material_name.upper() in ["HY80", "HY100"]:
        # Fe oxidation
        c_reactants = 1.0  # Pure metal (activity = 1)
        c_products = 1e-6  # Typical Fe²⁺ concentration at surface (M)
        c_g_cm3 = c_products * C.M_Fe / 1000.0
        EN_log = np.log(c_reactants / c_g_cm3)
        E_N_SHE = C.e0_Fe_ox + (RT / (z * C.F)) * EN_log
    elif material_name.upper() == "SS316":
        # Cr oxidation
        c_reactants = 1.0
        c_products = 1e-6  # Typical Cr³⁺ concentration
        c_g_cm3 = c_products * C.M_Cr / 1000.0
        EN_log = np.log(c_reactants / c_g_cm3)
        E_N_SHE = C.e0_Cr_ox + (RT / (z * C.F)) * EN_log
    else:
        raise ValueError(f"Unknown material: {material_name}")

    # Convert to SCE reference
    E_N_VSCE = E_N_SHE - C.E_SHE_to_SCE

    # Calculate E_pit where i_pit = i_threshold
    # From Butler-Volmer: i_pit = i0_anodic * exp((alpha * z * F * eta) / RT)
    # Solving for eta: eta = (RT / (alpha * z * F)) * ln(i_pit / i0_anodic)
    # E_pit = E_N + eta

    eta_pit = (RT / (alpha * z * C.F)) * np.log(i_threshold_A_cm2 / i0_anodic)
    E_pit_VSCE = E_N_VSCE + eta_pit

    # Assemble details
    details = {
        "i0_anodic_A_cm2": float(i0_anodic),
        "dG_anodic_J_mol": float(dG_anodic),
        "E_N_VSCE": float(E_N_VSCE),
        "alpha": float(alpha),
        "z": int(z),
        "material": material_name,
        "temperature_C": temperature_C,
        "chloride_mg_L": chloride_mg_L,
        "pH": pH,
        "i_threshold_A_cm2": i_threshold_A_cm2,
    }

    return float(E_pit_VSCE), details


def assess_pitting_risk_electrochemical(
    E_mix_VSCE: float,
    E_pit_VSCE: float,
) -> Tuple[str, str, float]:
    """
    Assess pitting risk from electrochemical driving force ΔE = E_mix - E_pit.

    Args:
        E_mix_VSCE: Mixed/corrosion potential (V vs SCE)
        E_pit_VSCE: Pitting initiation potential (V vs SCE)

    Returns:
        risk_level: "critical", "high", "moderate", "low"
        interpretation: Text summary
        margin_V: ΔE = E_mix - E_pit (V)

    Risk Criteria:
        ΔE > +0.05 V: CRITICAL (pitting highly likely)
        ΔE > 0 V: HIGH (pitting thermodynamically favorable)
        -0.1 V < ΔE < 0 V: MODERATE (small margin)
        ΔE < -0.1 V: LOW (large margin)

    Example:
        >>> risk, interp, dE = assess_pitting_risk_electrochemical(
        ...     E_mix_VSCE=0.5, E_pit_VSCE=0.4
        ... )
        >>> print(risk)
        'critical'
    """
    margin_V = E_mix_VSCE - E_pit_VSCE

    if margin_V > 0.05:
        risk_level = "critical"
        interpretation = (
            f"CRITICAL: E_mix ({E_mix_VSCE:.3f} V) >> E_pit ({E_pit_VSCE:.3f} V) by "
            f"{margin_V*1000:.0f} mV. Pitting is thermodynamically highly favorable and "
            f"likely to initiate. Immediate action required: upgrade material, reduce Cl⁻, "
            f"or apply cathodic protection."
        )
    elif margin_V > 0:
        risk_level = "high"
        interpretation = (
            f"HIGH RISK: E_mix ({E_mix_VSCE:.3f} V) > E_pit ({E_pit_VSCE:.3f} V) by "
            f"{margin_V*1000:.0f} mV. Pitting is thermodynamically favorable. "
            f"Recommend: monitor for pitting initiation, consider material upgrade or "
            f"chloride reduction."
        )
    elif margin_V > -0.1:
        risk_level = "moderate"
        interpretation = (
            f"MODERATE: E_mix ({E_mix_VSCE:.3f} V) is {abs(margin_V)*1000:.0f} mV below "
            f"E_pit ({E_pit_VSCE:.3f} V). Small safety margin. Pitting unlikely under "
            f"steady-state conditions, but transients (DO spikes, temperature rise) could "
            f"trigger initiation. Monitor for environmental changes."
        )
    else:
        risk_level = "low"
        interpretation = (
            f"LOW RISK: E_mix ({E_mix_VSCE:.3f} V) is {abs(margin_V)*1000:.0f} mV below "
            f"E_pit ({E_pit_VSCE:.3f} V). Large safety margin. Pitting is thermodynamically "
            f"unfavorable under current conditions. Material selection is appropriate."
        )

    return risk_level, interpretation, margin_V


if __name__ == "__main__":
    # Test E_pit calculation
    print("="*70)
    print("Pitting Potential Calculation Test")
    print("="*70)

    # Test case: SS316 in seawater conditions
    E_pit, details = calculate_pitting_potential(
        material_name="SS316",
        temperature_C=25.0,
        chloride_mg_L=19000.0,  # Seawater
        pH=8.1,
        i_threshold_A_cm2=1e-6,  # 1 µA/cm²
    )

    print(f"\nMaterial: {details['material']}")
    print(f"Conditions: T={details['temperature_C']}C, Cl-={details['chloride_mg_L']} mg/L, pH={details['pH']}")
    print(f"\nElectrochemical Properties:")
    print(f"  E_N (Nernst potential): {details['E_N_VSCE']:.3f} V_SCE")
    print(f"  i0_anodic: {details['i0_anodic_A_cm2']:.3e} A/cm2")
    print(f"  dG_anodic: {details['dG_anodic_J_mol']:.2e} J/mol")
    print(f"  alpha (transfer coeff): {details['alpha']:.4f}")
    print(f"  z (oxidation level): {details['z']}")
    print(f"\nPitting Potential:")
    print(f"  E_pit = {E_pit:.3f} V_SCE (at i_threshold = {details['i_threshold_A_cm2']:.1e} A/cm2)")

    # Test risk assessment
    print(f"\n{'='*70}")
    print("Risk Assessment Test")
    print("="*70)

    # Scenario: Aerated seawater (E_mix ≈ 0.5 V_SCE)
    E_mix_seawater = 0.5  # V_SCE (typical for aerated seawater)
    risk, interpretation, dE = assess_pitting_risk_electrochemical(E_mix_seawater, E_pit)

    print(f"\nScenario: Aerated seawater")
    print(f"  E_mix = {E_mix_seawater:.3f} V_SCE")
    print(f"  E_pit = {E_pit:.3f} V_SCE")
    print(f"  dE = {dE:.3f} V ({dE*1000:.0f} mV)")
    print(f"\nRisk Level: {risk.upper()}")
    print(f"\n{interpretation}")
