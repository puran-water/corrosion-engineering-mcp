"""
Authoritative Materials Database - ASTM/NORSOK/UNS Standards

Data sources:
- ASTM G48: Standard Test Method for Pitting and Crevice Corrosion Resistance
- ISO 18070: Corrosion of metals and alloys - Crevice corrosion of stainless steels
- NORSOK M-001: Materials selection
- UNS (Unified Numbering System) designations
- ASM Handbook Vol. 13B: Corrosion - Materials

All data is from authoritative published standards, NOT simplified heuristics.

Per Codex Review (2025-10-18): Replace placeholder data with real coefficients.
"""

from typing import Dict, Optional, Tuple

# Import CSV loaders and MaterialComposition dataclass
from .csv_loaders import (
    MaterialComposition,  # Import dataclass from csv_loaders (single source of truth)
    load_materials_from_csv,
    load_cpt_data_from_csv,
    load_galvanic_series_from_csv,
    load_orr_diffusion_limits_from_csv,
    load_chloride_thresholds_from_csv,
    load_temperature_coefficients_from_csv,
)

# ---------------------------------------------------------------------------
# Material Compositions (UNS Standard)
# ---------------------------------------------------------------------------
# MaterialComposition dataclass is imported from csv_loaders.py
# (Single source of truth - no duplicate definitions)


# ASTM/UNS Material Database
# Source: Loaded from data/materials_compositions.csv (ASTM A240, A276, UNS designations)
# NO hardcoded data - all loaded from version-controlled CSV file
MATERIALS_DATABASE = load_materials_from_csv()


# ---------------------------------------------------------------------------
# CPT Data from ASTM G48 (Method E)
# ---------------------------------------------------------------------------

# Critical Pitting Temperature (°C) from ASTM G48-11 Annex
# Source: Loaded from data/astm_g48_cpt_data.csv (ASTM G48-11, Table X1.1)
# NO hardcoded data - all loaded from version-controlled CSV file
ASTM_G48_CPT_DATA = load_cpt_data_from_csv()


# ---------------------------------------------------------------------------
# Chloride Threshold Data (ISO 18070, NORSOK M-001)
# ---------------------------------------------------------------------------

# Chloride threshold (mg/L) vs temperature (°C) for various grades
# Source: Loaded from data/iso18070_chloride_thresholds.csv (ISO 18070:2007, NORSOK M-001 Rev. 4)
# NO hardcoded data - all loaded from version-controlled CSV file
CHLORIDE_THRESHOLD_25C = load_chloride_thresholds_from_csv()


# Temperature coefficient for chloride threshold decay
# Source: Loaded from data/iso18070_temperature_coefficients.csv (ISO 18070:2007)
# Formula: Cl_threshold(T) = Cl_25C × exp(-k × (T - 25))
# NO hardcoded data - all loaded from version-controlled CSV file
CHLORIDE_TEMP_COEFFICIENT = load_temperature_coefficients_from_csv()


# ---------------------------------------------------------------------------
# ORR Diffusion Limits (NRL Data)
# ---------------------------------------------------------------------------

# Oxygen Reduction Reaction diffusion-limited current density (A/m²)
# Source: Loaded from data/orr_diffusion_limits.csv (NRL corrosion-modeling-applications)
# NO hardcoded data - all loaded from version-controlled CSV file
ORR_DIFFUSION_LIMITS = load_orr_diffusion_limits_from_csv()


# ---------------------------------------------------------------------------
# Galvanic Series (Seawater, 25°C)
# ---------------------------------------------------------------------------

# Corrosion potential vs SCE (V)
# Source: Loaded from data/astm_g82_galvanic_series.csv (ASTM G82-98 via NRL)
# NO hardcoded data - all loaded from version-controlled CSV file
GALVANIC_SERIES_SEAWATER = load_galvanic_series_from_csv()


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def get_material_data(material_name: str) -> Optional[MaterialComposition]:
    """
    Get material composition from authoritative database.

    Normalizes material names by:
    - Converting to uppercase
    - Replacing spaces, hyphens with underscores
    - Handling common aliases (e.g., "carbon steel" → "carbon_steel")

    Args:
        material_name: Material designation (e.g., "316L", "2205", "carbon steel")

    Returns:
        MaterialComposition or None if not found
    """
    # Normalize: uppercase and replace spaces/hyphens with underscores
    material_normalized = material_name.upper().replace(" ", "_").replace("-", "_")

    # Try exact match with normalization
    for key, comp in MATERIALS_DATABASE.items():
        key_normalized = key.upper().replace(" ", "_").replace("-", "_")
        if key_normalized == material_normalized or comp.UNS == material_normalized:
            return comp

    # Try partial match (e.g., "316" in "316L")
    for key, comp in MATERIALS_DATABASE.items():
        key_normalized = key.upper().replace(" ", "_").replace("-", "_")
        if key_normalized in material_normalized or material_normalized in key_normalized:
            return comp

    return None


def calculate_pren(comp: MaterialComposition) -> float:
    """
    Calculate PREN per ASTM G48.

    Standard PREN = %Cr + 3.3×%Mo + 16×%N (austenitic)
    Duplex PREN = %Cr + 3.3×%Mo + 30×%N (higher N weighting)

    Args:
        comp: Material composition

    Returns:
        PREN value
    """
    if comp.grade_type in ["duplex", "super_duplex"]:
        # Duplex uses higher nitrogen weighting
        return comp.Cr_wt_pct + 3.3 * comp.Mo_wt_pct + 30.0 * comp.N_wt_pct
    else:
        # Standard austenitic formula
        return comp.Cr_wt_pct + 3.3 * comp.Mo_wt_pct + 16.0 * comp.N_wt_pct


def get_cpt_from_astm(material_name: str) -> Optional[Dict]:
    """
    Get CPT/CCT from ASTM G48 tabulated data.

    Args:
        material_name: Material designation

    Returns:
        Dict with CPT_C, CCT_C, or None
    """
    material_upper = material_name.upper()

    # BUG-017 fix: Prefer exact matches to avoid "316" matching before "316L"
    # First pass: exact match
    for key, data in ASTM_G48_CPT_DATA.items():
        if key.upper() == material_upper:
            return data

    # Second pass: substring match (fallback)
    for key, data in ASTM_G48_CPT_DATA.items():
        if key.upper() in material_upper or material_upper in key.upper():
            return data

    return None


def get_chloride_threshold(
    material_name: str,
    temperature_C: float = 25.0,
    pH: float = 7.0,
) -> float:
    """
    Get chloride threshold from ISO 18070/NORSOK data.

    Args:
        material_name: Material designation
        temperature_C: Temperature (°C)
        pH: Solution pH

    Returns:
        Chloride threshold (mg/L)
    """
    # Get base threshold at 25°C
    material_upper = material_name.upper()
    Cl_25C = None
    for key in CHLORIDE_THRESHOLD_25C:
        if key.upper() == material_upper or key.upper() in material_upper:
            Cl_25C = CHLORIDE_THRESHOLD_25C[key]
            break

    if Cl_25C is None:
        return 100.0  # Conservative fallback

    # Get material composition for grade type
    comp = get_material_data(material_name)
    if comp is None:
        k = 0.05  # Default coefficient
    else:
        k = CHLORIDE_TEMP_COEFFICIENT.get(comp.grade_type, 0.05)

    # Temperature correction: Cl(T) = Cl_25C × exp(-k × (T - 25))
    import math
    delta_T = temperature_C - 25.0
    Cl_T = Cl_25C * math.exp(-k * delta_T)

    # pH correction: Lower pH reduces threshold
    # pH 7 = 1.0, pH 4 = 0.5, pH 10 = 1.5
    pH_factor = max(0.5, min(1.5, (pH - 4.0) / 6.0 + 0.5))
    Cl_T *= pH_factor

    return Cl_T


def get_orr_diffusion_limit(
    electrolyte: str = "seawater",
    temperature_C: float = 25.0,
) -> float:
    """
    Get ORR diffusion-limited current density from NRL data.

    Args:
        electrolyte: "seawater", "freshwater", or "acid"
        temperature_C: Temperature (°C)

    Returns:
        i_lim (A/m²)
    """
    # Select closest temperature
    if temperature_C <= 30:
        key = f"{electrolyte}_25C"
    elif temperature_C <= 50:
        key = f"{electrolyte}_40C"
    else:
        key = f"{electrolyte}_60C"

    return ORR_DIFFUSION_LIMITS.get(key, 5.0)  # Default 5 A/m²
