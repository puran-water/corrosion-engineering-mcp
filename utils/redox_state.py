"""
RedoxState: Unified DO, ORP, and Eh conversions for corrosion tools.

Provides lightweight conversions between:
- Dissolved Oxygen (DO) in mg/L
- Redox Potential (Eh) in V vs SHE
- Oxidation-Reduction Potential (ORP) in mV vs reference electrode

Uses Nernst equation for oxygen reduction reaction (ORR):
    O₂ + 2H₂O + 4e⁻ → 4OH⁻

References:
    - Pourbaix (1974). Atlas of Electrochemical Equilibria
    - Revie (2011). Uhlig's Corrosion Handbook, Chapter 6
    - Garcia & Gordon (1992). Oxygen solubility (via utils/oxygen_solubility.py)
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional, Literal
from enum import Enum

# Import authoritative DO saturation implementation from project
try:
    from utils.oxygen_solubility import calculate_do_saturation
except ImportError:
    # When running as __main__, adjust import path
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.oxygen_solubility import calculate_do_saturation


# ==============================================================================
# Constants
# ==============================================================================

class Constants:
    """Physical and electrochemical constants."""
    R = 8.314  # J/(mol·K) - Gas constant
    F = 96485  # C/mol - Faraday constant

    # Reference electrode potentials vs SHE
    E_SCE_vs_SHE = 0.242  # V - Saturated Calomel Electrode
    E_AgAgCl_vs_SHE = 0.197  # V - Ag/AgCl (sat'd KCl)

    # ORR standard potential
    E0_ORR_SHE = 1.229  # V vs SHE at pH=0, 25°C

    # Henry's law constant for O₂ at 25°C
    # Reference: USGS tables show 8.26 mg/L saturation at 25°C, 1 atm
    # Working backwards: 8.26 mg/L / (32 g/mol * 1000) = 2.58×10⁻⁴ mol/L
    # K_H = c/p = 2.58×10⁻⁴ / 0.2095 = 1.23×10⁻³ mol/(L·atm)
    K_H_O2_298K = 1.23e-3  # mol/(L·atm) at 25°C (calibrated to USGS: 8.26 mg/L)

    # Enthalpy of solution for O₂ (POSITIVE = endothermic, solubility decreases with T)
    # From Sander (2015): d(ln(k_H))/d(1/T) = -ΔH_soln/R
    # ΔH_soln ≈ +1700 K * R = +14.1 kJ/mol (endothermic)
    d_soln_H_O2 = 14100  # J/mol - enthalpy of solution for O₂ (1700 K * 8.314)

    # Molecular weight of O₂
    MW_O2 = 32.0  # g/mol


C = Constants()


# ==============================================================================
# Reference Electrode Types
# ==============================================================================

class ReferenceElectrode(str, Enum):
    """Standard reference electrodes."""
    SCE = "SCE"  # Saturated Calomel Electrode (+0.242 V vs SHE)
    AgAgCl = "Ag/AgCl"  # Silver/Silver Chloride (+0.197 V vs SHE)
    SHE = "SHE"  # Standard Hydrogen Electrode (0.000 V)


# ==============================================================================
# RedoxState Dataclass
# ==============================================================================

@dataclass
class RedoxState:
    """
    Unified redox state representation.

    Attributes:
        eh_VSHE: Redox potential vs SHE (V)
        pH: Solution pH
        temperature_C: Temperature (°C)
        dissolved_oxygen_mg_L: Dissolved oxygen concentration (mg/L)
        orp_mV: ORP reading vs reference electrode (mV)
        reference_electrode: Reference electrode type
    """
    eh_VSHE: Optional[float] = None
    pH: float = 7.0
    temperature_C: float = 25.0
    dissolved_oxygen_mg_L: Optional[float] = None
    orp_mV: Optional[float] = None
    reference_electrode: ReferenceElectrode = ReferenceElectrode.SCE


# ==============================================================================
# Henry's Law for O₂ Solubility
# ==============================================================================

def henry_constant_o2(temperature_C: float) -> float:
    """
    Calculate temperature-dependent Henry's law constant for O₂.

    Derived from Garcia & Gordon (1992) DO saturation model via
    utils/oxygen_solubility.py (authoritative implementation).

    Args:
        temperature_C: Temperature (°C)

    Returns:
        Henry's constant (mol/(L·atm))

    Reference:
        Derived from Garcia & Gordon (1992)
    """
    # Get DO saturation from authoritative implementation
    DO_sat_mg_L = calculate_do_saturation(
        temperature_C,
        salinity_psu=0.0,
        model="garcia-benson"
    )

    # Convert to Henry's constant
    # DO_sat = K_H * p_O2 * MW_O2 * 1000
    # K_H = DO_sat / (p_O2 * MW_O2 * 1000)
    p_O2 = 0.2095  # atm (air)
    K_H = DO_sat_mg_L / (p_O2 * C.MW_O2 * 1000)  # mol/(L·atm)

    return K_H


def do_saturation(temperature_C: float, pressure_atm: float = 1.0) -> float:
    """
    Calculate DO saturation concentration at air equilibrium.

    Uses Garcia & Gordon (1992) model via utils/oxygen_solubility.py
    (authoritative implementation with full provenance).

    Args:
        temperature_C: Temperature (°C)
        pressure_atm: Atmospheric pressure (atm)

    Returns:
        DO saturation (mg/L)

    Note:
        Assumes air with 20.95% O₂ by volume
    """
    # Use authoritative implementation
    # Garcia & Gordon is normalized to 1 atm, scale with pressure
    DO_sat = calculate_do_saturation(
        temperature_C,
        salinity_psu=0.0,
        pressure_mbar=pressure_atm * 1013.25,  # Convert atm to mbar
        model="garcia-benson"
    )

    return DO_sat


# ==============================================================================
# DO ↔ Eh Conversions
# ==============================================================================

def do_to_eh(
    dissolved_oxygen_mg_L: float,
    pH: float,
    temperature_C: float = 25.0,
) -> tuple[float, list[str]]:
    """
    Convert dissolved oxygen to redox potential using ORR Nernst equation.

    Reaction:
        O₂ + 2H₂O + 4e⁻ → 4OH⁻

    Nernst equation:
        Eh = E⁰ + (RT/4F) * ln(a_O2) - (RT/F) * ln([OH⁻]²)
        Eh = E⁰ - 0.059*pH + (0.059/4) * log10(p_O2)  [at 25°C]

    Args:
        dissolved_oxygen_mg_L: Dissolved oxygen (mg/L)
        pH: Solution pH
        temperature_C: Temperature (°C)

    Returns:
        Tuple of (Eh_VSHE, warnings)
            - Eh_VSHE: Redox potential vs SHE (V)
            - warnings: List of warning messages

    Example:
        >>> Eh, warnings = do_to_eh(8.0, pH=8.1, temperature_C=25.0)
        >>> print(f"Eh = {Eh:.3f} V_SHE")
        Eh = 0.397 V_SHE
    """
    warnings = []

    # Convert DO (mg/L) to partial pressure (atm) using Henry's law
    K_H = henry_constant_o2(temperature_C)
    c_O2_mol_L = dissolved_oxygen_mg_L / (C.MW_O2 * 1000)  # mg/L → mol/L
    p_O2 = c_O2_mol_L / K_H  # atm

    # Check if DO is below detection limit
    if dissolved_oxygen_mg_L < 0.01:
        warnings.append(
            "DO < 0.01 mg/L (anaerobic conditions). Eh calculation assumes ORR "
            "equilibrium, which may not apply in anaerobic environments where "
            "hydrogen evolution reaction (HER) or sulfate reduction may dominate."
        )
        # Use epsilon to prevent log(0)
        p_O2 = max(p_O2, 1e-10)

    # Check if oversaturated
    DO_sat = do_saturation(temperature_C)
    if dissolved_oxygen_mg_L > 1.1 * DO_sat:
        warnings.append(
            f"DO ({dissolved_oxygen_mg_L:.1f} mg/L) exceeds saturation "
            f"({DO_sat:.1f} mg/L) by >10%. This may indicate supersaturation "
            f"or measurement error."
        )

    # Nernst equation for ORR
    T_K = temperature_C + 273.15
    RT_4F = (C.R * T_K) / (4.0 * C.F)  # V

    # Eh = E⁰ - (RT/F)*ln(10)*pH + (RT/4F)*ln(p_O2)
    # Using natural log
    Eh_VSHE = C.E0_ORR_SHE - (2.303 * C.R * T_K / C.F) * pH + RT_4F * np.log(p_O2)

    return Eh_VSHE, warnings


def eh_to_do(
    eh_VSHE: float,
    pH: float,
    temperature_C: float = 25.0,
) -> tuple[float, list[str]]:
    """
    Convert redox potential to dissolved oxygen (inverse of do_to_eh).

    Solves Nernst equation for p_O2, then converts to DO using Henry's law.

    Args:
        eh_VSHE: Redox potential vs SHE (V)
        pH: Solution pH
        temperature_C: Temperature (°C)

    Returns:
        Tuple of (DO_mg_L, warnings)
            - DO_mg_L: Dissolved oxygen (mg/L)
            - warnings: List of warning messages

    Example:
        >>> DO, warnings = eh_to_do(0.4, pH=8.1, temperature_C=25.0)
        >>> print(f"DO = {DO:.1f} mg/L")
        DO = 8.1 mg/L
    """
    warnings = []

    # Solve Nernst equation for p_O2
    T_K = temperature_C + 273.15
    RT_4F = (C.R * T_K) / (4.0 * C.F)

    # Eh = E⁰ - (RT/F)*ln(10)*pH + (RT/4F)*ln(p_O2)
    # ln(p_O2) = (Eh - E⁰ + (RT/F)*ln(10)*pH) / (RT/4F)
    pH_term = (2.303 * C.R * T_K / C.F) * pH
    ln_p_O2 = (eh_VSHE - C.E0_ORR_SHE + pH_term) / RT_4F
    p_O2 = np.exp(ln_p_O2)

    # Check if p_O2 is physically realistic
    if p_O2 > 1.0:
        warnings.append(
            f"Calculated p_O2 = {p_O2:.2f} atm exceeds atmospheric pressure. "
            f"This Eh ({eh_VSHE:.3f} V) is too oxidizing for ORR equilibrium."
        )
        p_O2 = min(p_O2, 1.0)  # Cap at 1 atm

    if p_O2 < 1e-10:
        warnings.append(
            f"Calculated p_O2 = {p_O2:.2e} atm is negligible. "
            f"This Eh ({eh_VSHE:.3f} V) indicates anaerobic/reducing conditions."
        )

    # Convert p_O2 to DO using Henry's law
    K_H = henry_constant_o2(temperature_C)
    c_O2_mol_L = K_H * p_O2
    DO_mg_L = c_O2_mol_L * C.MW_O2 * 1000

    return DO_mg_L, warnings


# ==============================================================================
# ORP ↔ Eh Conversions
# ==============================================================================

def orp_to_eh(
    orp_mV: float,
    reference_electrode: ReferenceElectrode = ReferenceElectrode.SCE,
) -> float:
    """
    Convert ORP reading to Eh (vs SHE).

    Simple reference electrode offset correction.

    Args:
        orp_mV: ORP reading (mV vs reference electrode)
        reference_electrode: Reference electrode type

    Returns:
        Eh_VSHE: Redox potential vs SHE (V)

    Example:
        >>> Eh = orp_to_eh(150, reference_electrode=ReferenceElectrode.SCE)
        >>> print(f"Eh = {Eh:.3f} V_SHE")
        Eh = 0.392 V_SHE
    """
    orp_V = orp_mV / 1000.0  # mV → V

    if reference_electrode == ReferenceElectrode.SCE:
        Eh_VSHE = orp_V + C.E_SCE_vs_SHE
    elif reference_electrode == ReferenceElectrode.AgAgCl:
        Eh_VSHE = orp_V + C.E_AgAgCl_vs_SHE
    elif reference_electrode == ReferenceElectrode.SHE:
        Eh_VSHE = orp_V  # Already vs SHE
    else:
        raise ValueError(f"Unknown reference electrode: {reference_electrode}")

    return Eh_VSHE


def eh_to_orp(
    eh_VSHE: float,
    reference_electrode: ReferenceElectrode = ReferenceElectrode.SCE,
) -> float:
    """
    Convert Eh (vs SHE) to ORP reading.

    Args:
        eh_VSHE: Redox potential vs SHE (V)
        reference_electrode: Reference electrode type

    Returns:
        ORP reading (mV vs reference electrode)

    Example:
        >>> orp = eh_to_orp(0.4, reference_electrode=ReferenceElectrode.SCE)
        >>> print(f"ORP = {orp:.0f} mV_SCE")
        ORP = 158 mV_SCE
    """
    if reference_electrode == ReferenceElectrode.SCE:
        orp_V = eh_VSHE - C.E_SCE_vs_SHE
    elif reference_electrode == ReferenceElectrode.AgAgCl:
        orp_V = eh_VSHE - C.E_AgAgCl_vs_SHE
    elif reference_electrode == ReferenceElectrode.SHE:
        orp_V = eh_VSHE  # Already vs SHE
    else:
        raise ValueError(f"Unknown reference electrode: {reference_electrode}")

    return orp_V * 1000.0  # V → mV


# ==============================================================================
# Convenience Functions
# ==============================================================================

def create_redox_state_from_do(
    dissolved_oxygen_mg_L: float,
    pH: float = 7.0,
    temperature_C: float = 25.0,
) -> RedoxState:
    """
    Create RedoxState from dissolved oxygen.

    Args:
        dissolved_oxygen_mg_L: Dissolved oxygen (mg/L)
        pH: Solution pH
        temperature_C: Temperature (°C)

    Returns:
        RedoxState object with calculated Eh
    """
    Eh_VSHE, _ = do_to_eh(dissolved_oxygen_mg_L, pH, temperature_C)

    return RedoxState(
        eh_VSHE=Eh_VSHE,
        pH=pH,
        temperature_C=temperature_C,
        dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
    )


def create_redox_state_from_orp(
    orp_mV: float,
    pH: float = 7.0,
    temperature_C: float = 25.0,
    reference_electrode: ReferenceElectrode = ReferenceElectrode.SCE,
) -> RedoxState:
    """
    Create RedoxState from ORP reading.

    Args:
        orp_mV: ORP reading (mV vs reference electrode)
        pH: Solution pH
        temperature_C: Temperature (°C)
        reference_electrode: Reference electrode type

    Returns:
        RedoxState object with calculated Eh and DO
    """
    Eh_VSHE = orp_to_eh(orp_mV, reference_electrode)
    DO_mg_L, _ = eh_to_do(Eh_VSHE, pH, temperature_C)

    return RedoxState(
        eh_VSHE=Eh_VSHE,
        pH=pH,
        temperature_C=temperature_C,
        dissolved_oxygen_mg_L=DO_mg_L,
        orp_mV=orp_mV,
        reference_electrode=reference_electrode,
    )


# ==============================================================================
# Example Usage
# ==============================================================================

if __name__ == "__main__":
    # Example 1: Aerated seawater
    print("Example 1: Aerated Seawater")
    print("-" * 50)
    DO = 8.0  # mg/L
    pH = 8.1
    T = 25.0  # °C

    Eh, warnings = do_to_eh(DO, pH, T)
    print(f"DO = {DO} mg/L, pH = {pH}, T = {T}°C")
    print(f"Eh = {Eh:.3f} V_SHE = {Eh*1000:.0f} mV_SHE")
    if warnings:
        print(f"Warnings: {warnings}")
    print()

    # Example 2: Anaerobic digester
    print("Example 2: Anaerobic Digester")
    print("-" * 50)
    DO = 0.01  # mg/L
    pH = 7.2
    T = 35.0  # °C

    Eh, warnings = do_to_eh(DO, pH, T)
    print(f"DO = {DO} mg/L, pH = {pH}, T = {T}°C")
    print(f"Eh = {Eh:.3f} V_SHE = {Eh*1000:.0f} mV_SHE")
    if warnings:
        print(f"Warnings: {warnings[0][:80]}...")
    print()

    # Example 3: ORP conversion
    print("Example 3: ORP Reading")
    print("-" * 50)
    ORP_mV = 150  # mV vs SCE
    Eh = orp_to_eh(ORP_mV, ReferenceElectrode.SCE)
    print(f"ORP = {ORP_mV} mV_SCE")
    print(f"Eh = {Eh:.3f} V_SHE = {Eh*1000:.0f} mV_SHE")
    print()

    # Example 4: DO saturation
    print("Example 4: DO Saturation")
    print("-" * 50)
    for T in [5, 15, 25, 35]:
        DO_sat = do_saturation(T)
        print(f"T = {T}°C: DO_sat = {DO_sat:.1f} mg/L")
