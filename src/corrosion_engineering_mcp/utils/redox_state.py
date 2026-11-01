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

import math

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Literal
from enum import Enum

# Import authoritative DO saturation implementation from project
try:
    from corrosion_engineering_mcp.utils.oxygen_solubility import GARCIA_GORDON_FIT_SALINITY_PSU, GARCIA_GORDON_FRESHWATER_FIT_TEMPERATURE_C, calculate_do_saturation
except ImportError:
    # When running as __main__, adjust import path
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from corrosion_engineering_mcp.utils.oxygen_solubility import GARCIA_GORDON_FIT_SALINITY_PSU, GARCIA_GORDON_FRESHWATER_FIT_TEMPERATURE_C, calculate_do_saturation


# ==============================================================================
# Constants
# ==============================================================================

class Constants:
    """Physical and electrochemical constants."""
    R = 8.314  # J/(mol·K) - Gas constant
    F = 96485  # C/mol - Faraday constant

    # Reference electrode potentials vs SHE
    # Unified with utils.localized_criteria.SCE_VS_SHE_V. The codebase carried
    # 0.244, 0.242 and 0.241 in three places and subtracted potentials converted
    # with different ones (TOOL_SANITY_LOG finding 7).
    E_SCE_vs_SHE = 0.241  # V - Saturated Calomel Electrode (IUPAC, 25 C)
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

# Below this reported concentration the oxygen activity is not resolved well
# enough to evaluate the logarithmic ORR Nernst term.  This is an explicit
# calculation/detection basis, not a claim about the actual absence of oxygen.
DO_NERNST_DETECTION_BASIS_MG_L = 0.01

REDOX_OUTSIDE_SOURCE_DOMAIN_STATUS = "outside_source_domain_temperature"
REDOX_OUTSIDE_SOURCE_DOMAIN_WARNING_PREFIX = (
    "REDOX_OUTSIDE_SOURCE_DOMAIN_TEMPERATURE:"
)
REDOX_UNDEFINED_ANAEROBIC_STATUS = (
    "undefined_anaerobic_at_or_below_detection_basis"
)
REDOX_UNDEFINED_ANAEROBIC_WARNING_PREFIX = (
    "ANAEROBIC_ORR_EQUILIBRIUM_UNDEFINED:"
)
REDOX_DEFINED_ORR_STATUS = "defined_orr_equilibrium"
REDOX_TYPED_NULL_STATUS_PRECEDENCE = (
    REDOX_OUTSIDE_SOURCE_DOMAIN_STATUS,
    REDOX_UNDEFINED_ANAEROBIC_STATUS,
)

# The public redox quantity combines Garcia & Gordon's freshwater oxygen-
# solubility fit with Bratsch's standard-potential anchor. Garcia & Gordon state
# the Eq. (8) fit domain as t_F <= t <= 40 C and 0 <= S <= 42 per mille. This
# implementation fixes salinity at 0 PSU, so t_F is 0 C. Bratsch reports the
# standard potential and its temperature coefficient at 298.15 K. The existing
# numerical contract uses the rounded 1.229 V anchor plus temperature-dependent
# RT/F Nernst terms; it does not apply Bratsch's E0 temperature coefficient.
# Therefore the result is explicitly a fixed-anchor screen, bounded to the
# narrower 0-40 C Garcia/Gordon freshwater fit, and is not represented as a
# temperature-corrected standard potential.
REDOX_EQUILIBRIUM_SOURCE_DOMAIN = {
    "temperature_C": {
        "minimum": GARCIA_GORDON_FRESHWATER_FIT_TEMPERATURE_C[0],
        "maximum": GARCIA_GORDON_FRESHWATER_FIT_TEMPERATURE_C[1],
        "boundary_policy": "inclusive",
        "lower_bound_basis": (
            "freshwater_freezing_point_at_implemented_salinity_0_psu"
        ),
    },
    "salinity_psu": {
        "minimum": GARCIA_GORDON_FIT_SALINITY_PSU[0],
        "maximum": GARCIA_GORDON_FIT_SALINITY_PSU[1],
        "boundary_policy": "inclusive",
        "implemented_value": 0.0,
    },
    "standard_potential_basis": {
        "temperature_K": 298.15,
        "published_E0_VSHE": 1.2291,
        "implemented_rounded_E0_VSHE": 1.229,
        "published_temperature_coefficient_V_per_K": -0.0008456,
        "temperature_coefficient_applied": False,
        "interpretation": (
            "fixed_298_15_K_anchor_with_temperature_dependent_nernst_terms;"
            "not_a_temperature_corrected_standard_potential"
        ),
    },
}

GARCIA_GORDON_O2_SOLUBILITY_CITATION_RECORD = {
    "doi": "10.4319/lo.1992.37.6.1307",
    "title": "Oxygen solubility in seawater: Better fitting equations",
    "publication_year": 1992,
    "authors": ["Hernan E. Garcia", "Louis I. Gordon"],
    "publisher_url": (
        "https://aslopubs.onlinelibrary.wiley.com/doi/10.4319/"
        "lo.1992.37.6.1307"
    ),
    "content_read_url": (
        "https://calcofi.org/downloads/underway/2024/2411/ctd/docs/"
        "Misc/O2Sol.pdf"
    ),
    "access_status": "full_text_read_open_copy",
    "content_location": "Equation (8) and fitted-range statement, pp. 1310-1312",
    "content_support": (
        "The fitted oxygen-solubility equation states t_F <= t <= 40 C and "
        "0 <= S <= 42 per_mille. At the implemented salinity S=0, t_F=0 C."
    ),
    "content_limit": (
        "The source-domain statement supports oxygen solubility only; it does "
        "not by itself validate a corrosion potential or material verdict."
    ),
}

BRATSCH_ORR_STANDARD_POTENTIAL_CITATION_RECORD = {
    "doi": "10.1063/1.555839",
    "title": (
        "Standard Electrode Potentials and Temperature Coefficients in Water "
        "at 298.15 K"
    ),
    "publication_year": 1989,
    "authors": ["Steven G. Bratsch"],
    "publisher_url": "https://doi.org/10.1063/1.555839",
    "content_read_url": "https://srd.nist.gov/jpcrdreprint/1.555839.pdf",
    "access_status": "full_text_read_official_nist_reprint",
    "content_location": (
        "standard-potential table, O2/H2O reaction row at 298.15 K"
    ),
    "content_support": (
        "Bratsch reports E0=1.2291 V and dE0/dT=-0.8456 mV/K for the "
        "O2/H2O couple at 298.15 K."
    ),
    "content_limit": (
        "The established executable value uses the rounded 1.229 V anchor and "
        "does not apply the tabulated E0 temperature coefficient. It is "
        "therefore disclosed as a fixed-anchor Nernst screen, not a "
        "temperature-corrected standard potential."
    ),
}

REDOX_EQUILIBRIUM_CITATION_RECORDS = (
    GARCIA_GORDON_O2_SOLUBILITY_CITATION_RECORD,
    BRATSCH_ORR_STANDARD_POTENTIAL_CITATION_RECORD,
)


# ==============================================================================
# Reference Electrode Types
# ==============================================================================

class ReferenceElectrode(str, Enum):
    """Standard reference electrodes."""
    SCE = "SCE"  # Saturated Calomel Electrode (+0.241 V vs SHE)
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
    calculation_status: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def calculation_statuses(self) -> list[str]:
        """All applicable statuses, ordered by the public precedence contract.

        This is a property rather than a dataclass field so established
        in-domain dataclass serialization and equality remain unchanged.
        """

        if self.calculation_status is None:
            return []
        if self.calculation_status == REDOX_DEFINED_ORR_STATUS:
            return [REDOX_DEFINED_ORR_STATUS]
        statuses = redox_calculation_statuses(None, self.warnings)
        if self.calculation_status not in statuses:
            statuses.insert(0, self.calculation_status)
        return statuses

    @property
    def status_precedence(self) -> list[str]:
        """Machine-readable typed-null precedence, highest priority first."""

        return list(REDOX_TYPED_NULL_STATUS_PRECEDENCE)


# ==============================================================================
# Henry's Law for O₂ Solubility
# ==============================================================================

def henry_constant_o2(temperature_C: float) -> Optional[float]:
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
    if not math.isfinite(temperature_C):
        raise ValueError("temperature_C must be finite")
    if not redox_temperature_in_source_domain(temperature_C):
        return None

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


def do_saturation(
    temperature_C: float,
    pressure_atm: float = 1.0,
) -> Optional[float]:
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
    if not math.isfinite(temperature_C):
        raise ValueError("temperature_C must be finite")
    if not redox_temperature_in_source_domain(temperature_C):
        return None

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

def redox_temperature_in_source_domain(temperature_C: float) -> bool:
    """Return whether the redox calculation is inside its inclusive source gate."""
    minimum_C, maximum_C = GARCIA_GORDON_FRESHWATER_FIT_TEMPERATURE_C
    return minimum_C <= temperature_C <= maximum_C


def redox_outside_source_domain_warning(
    temperature_C: float,
    *,
    suppressed_quantity: str = "E_redox",
) -> str:
    """Return the shared machine-recognisable source-domain warning."""

    minimum_C, maximum_C = GARCIA_GORDON_FRESHWATER_FIT_TEMPERATURE_C
    if suppressed_quantity == "E_redox":
        consequence = (
            "E_redox is null; the public localized-corrosion temperature schema "
            "remains 0-150 C for its other outputs."
        )
    else:
        consequence = (
            f"{suppressed_quantity} is null; no Garcia-Gordon extrapolation is "
            "emitted."
        )
    return (
        f"{REDOX_OUTSIDE_SOURCE_DOMAIN_WARNING_PREFIX} temperature_C="
        f"{repr(float(temperature_C))} is outside the inclusive "
        f"{minimum_C:g}-{maximum_C:g} C Garcia-Gordon freshwater fit used by "
        f"this redox calculation. {consequence}"
    )


def redox_calculation_statuses(
    value: Optional[float],
    warnings: list[str],
) -> list[str]:
    """Return every applicable status in documented precedence order."""

    if value is not None:
        return [REDOX_DEFINED_ORR_STATUS]

    statuses: list[str] = []
    if any(
        warning.startswith(REDOX_OUTSIDE_SOURCE_DOMAIN_WARNING_PREFIX)
        for warning in warnings
    ):
        statuses.append(REDOX_OUTSIDE_SOURCE_DOMAIN_STATUS)
    if any(
        warning.startswith(REDOX_UNDEFINED_ANAEROBIC_WARNING_PREFIX)
        for warning in warnings
    ):
        statuses.append(REDOX_UNDEFINED_ANAEROBIC_STATUS)
    if not statuses:
        statuses.append(REDOX_UNDEFINED_ANAEROBIC_STATUS)
    return statuses


def redox_calculation_status(
    eh_VSHE: Optional[float],
    warnings: list[str],
) -> str:
    """Map a computed or typed-null redox result to its public status."""
    return redox_calculation_statuses(eh_VSHE, warnings)[0]

def do_to_eh(
    dissolved_oxygen_mg_L: float,
    pH: float,
    temperature_C: float = 25.0,
) -> tuple[Optional[float], list[str]]:
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
            - Eh_VSHE: Redox potential vs SHE (V), or None when oxygen
              activity is unresolved at/below the stated detection basis or
              temperature is outside the verified source domain
            - warnings: List of warning messages

    Example:
        >>> Eh, warnings = do_to_eh(8.0, pH=8.1, temperature_C=25.0)
        >>> print(f"Eh = {Eh:.3f} V_SHE")
        Eh = 0.397 V_SHE
    """
    warnings = []

    if not math.isfinite(dissolved_oxygen_mg_L) or dissolved_oxygen_mg_L < 0.0:
        raise ValueError(
            "dissolved_oxygen_mg_L must be finite and non-negative"
        )

    if not math.isfinite(temperature_C):
        raise ValueError("temperature_C must be finite")

    # Evaluate every typed-null condition before returning. Source-domain status
    # remains primary, but a simultaneous unresolved oxygen activity must stay
    # visible to machine consumers rather than disappearing by check order.
    outside_source_domain = not redox_temperature_in_source_domain(temperature_C)
    unresolved_oxygen_activity = (
        dissolved_oxygen_mg_L <= DO_NERNST_DETECTION_BASIS_MG_L
    )
    if outside_source_domain:
        warnings.append(redox_outside_source_domain_warning(temperature_C))
    if unresolved_oxygen_activity:
        warnings.append(
            f"{REDOX_UNDEFINED_ANAEROBIC_WARNING_PREFIX} dissolved oxygen is at or "
            f"below the {DO_NERNST_DETECTION_BASIS_MG_L:g} mg/L detection "
            "basis, so oxygen activity and the logarithmic O2/H2O Nernst term "
            "are unresolved. No epsilon activity is substituted; measured ORP "
            "or an applicable reducing couple such as HER/sulfate reduction is "
            "needed for anaerobic conditions."
        )
    if outside_source_domain or unresolved_oxygen_activity:
        return None, warnings

    # Convert DO (mg/L) to partial pressure (atm) using Henry's law
    K_H = henry_constant_o2(temperature_C)
    c_O2_mol_L = dissolved_oxygen_mg_L / (C.MW_O2 * 1000)  # mg/L → mol/L
    p_O2 = c_O2_mol_L / K_H  # atm

    # Check if oversaturated
    DO_sat = do_saturation(temperature_C)
    if dissolved_oxygen_mg_L > 1.1 * DO_sat:
        warnings.append(
            "Reported dissolved oxygen exceeds the temperature-specific "
            "saturation calculation by >10%. This may indicate supersaturation "
            "or measurement error; the exact reported DO remains in the "
            "structured result."
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
) -> tuple[Optional[float], list[str]]:
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

    if not math.isfinite(temperature_C):
        raise ValueError("temperature_C must be finite")
    if not redox_temperature_in_source_domain(temperature_C):
        warnings.append(
            redox_outside_source_domain_warning(
                temperature_C,
                suppressed_quantity="dissolved_oxygen_mg_L",
            )
        )
        return None, warnings

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
            "Calculated oxygen partial pressure exceeds 1 atm. The reported Eh "
            "is too oxidizing for ORR equilibrium."
        )
        p_O2 = min(p_O2, 1.0)  # Cap at 1 atm

    if p_O2 < 1e-10:
        warnings.append(
            "Calculated oxygen partial pressure is below 1e-10 atm. The "
            "reported Eh indicates anaerobic/reducing conditions."
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
    Eh_VSHE, warnings = do_to_eh(dissolved_oxygen_mg_L, pH, temperature_C)

    return RedoxState(
        eh_VSHE=Eh_VSHE,
        pH=pH,
        temperature_C=temperature_C,
        dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
        calculation_status=redox_calculation_status(Eh_VSHE, warnings),
        warnings=list(warnings),
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
    DO_mg_L, warnings = eh_to_do(Eh_VSHE, pH, temperature_C)

    return RedoxState(
        eh_VSHE=Eh_VSHE,
        pH=pH,
        temperature_C=temperature_C,
        dissolved_oxygen_mg_L=DO_mg_L,
        orp_mV=orp_mV,
        reference_electrode=reference_electrode,
        calculation_status=redox_calculation_status(DO_mg_L, warnings),
        warnings=list(warnings),
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
    if Eh is None:
        print("Eh = undefined at/below the dissolved-oxygen detection basis")
    else:
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
    if Eh is None:
        print("Eh = undefined at/below the dissolved-oxygen detection basis")
    else:
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
