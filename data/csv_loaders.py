"""
CSV Data Loaders for Authoritative Corrosion Data

This module loads all corrosion engineering data from CSV files instead of
hardcoded dictionaries. All CSV files contain full citations to authoritative
sources (ASTM standards, NORSOK, NRL, etc.).

NO hardcoded data - everything loaded from version-controlled CSV files.

CSV Files:
- materials_compositions.csv - Material compositions (ASTM A240, B443, etc.)
- astm_g48_cpt_data.csv - Critical Pitting Temperature data (ASTM G48-11)
- astm_g82_galvanic_series.csv - Galvanic series (ASTM G82-98 via NRL)

All loaders are lazy (data loaded on first access) for performance.
"""

import csv
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# Location of CSV data files
DATA_DIR = Path(__file__).parent


@dataclass
class MaterialComposition:
    """
    Material composition from CSV file.

    Source: materials_compositions.csv (ASTM A240, B443, B152, etc.)
    """
    UNS: str
    common_name: str
    Cr_wt_pct: float
    Mo_wt_pct: float
    N_wt_pct: float
    Ni_wt_pct: float
    Fe_bal: bool = True
    density_kg_m3: float = 8000.0
    grade_type: str = "austenitic"
    n_electrons: int = 2
    source: str = "ASTM"


# Cache for loaded data (lazy loading)
_MATERIALS_CACHE: Optional[Dict[str, MaterialComposition]] = None
_CPT_DATA_CACHE: Optional[Dict[str, Dict]] = None
_GALVANIC_SERIES_CACHE: Optional[Dict[str, float]] = None
_ORR_LIMITS_CACHE: Optional[Dict[str, float]] = None
_CHLORIDE_THRESHOLD_CACHE: Optional[Dict[str, float]] = None
_TEMP_COEFFICIENT_CACHE: Optional[Dict[str, float]] = None


def load_materials_from_csv() -> Dict[str, MaterialComposition]:
    """
    Load material compositions from CSV file.

    Returns:
        Dictionary mapping material name to MaterialComposition

    Example:
        >>> materials = load_materials_from_csv()
        >>> ss316 = materials["316L"]
        >>> ss316.Cr_wt_pct
        16.5
        >>> ss316.source
        'ASTM A240'
    """
    global _MATERIALS_CACHE

    if _MATERIALS_CACHE is not None:
        return _MATERIALS_CACHE

    csv_file = DATA_DIR / "materials_compositions.csv"

    if not csv_file.exists():
        raise FileNotFoundError(
            f"Materials CSV not found: {csv_file}. "
            f"Expected file: materials_compositions.csv"
        )

    materials = {}

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                mat = MaterialComposition(
                    UNS=row['UNS'],
                    common_name=row['common_name'],
                    Cr_wt_pct=float(row['Cr_wt_pct']),
                    Ni_wt_pct=float(row['Ni_wt_pct']),
                    Mo_wt_pct=float(row['Mo_wt_pct']),
                    N_wt_pct=float(row['N_wt_pct']),
                    density_kg_m3=float(row['density_kg_m3']),
                    grade_type=row['grade_type'],
                    n_electrons=int(row['n_electrons']),
                    Fe_bal=row['Fe_bal'].lower() == 'true',
                    source=row['source'],
                )

                # Store by common name (primary key)
                materials[row['common_name']] = mat

            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse material row: {row}. Error: {e}")
                continue

    logger.info(f"Loaded {len(materials)} materials from {csv_file}")

    _MATERIALS_CACHE = materials
    return materials


def load_cpt_data_from_csv() -> Dict[str, Dict]:
    """
    Load Critical Pitting Temperature (CPT) data from CSV file.

    Returns:
        Dictionary mapping material name to CPT data dict

    Example:
        >>> cpt_data = load_cpt_data_from_csv()
        >>> cpt_data["316L"]
        {'CPT_C': 15, 'CCT_C': 5, 'test_solution': '6% FeCl3', 'source': 'ASTM G48-11'}
    """
    global _CPT_DATA_CACHE

    if _CPT_DATA_CACHE is not None:
        return _CPT_DATA_CACHE

    csv_file = DATA_DIR / "astm_g48_cpt_data.csv"

    if not csv_file.exists():
        raise FileNotFoundError(
            f"CPT data CSV not found: {csv_file}. "
            f"Expected file: astm_g48_cpt_data.csv"
        )

    cpt_data = {}

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                material = row['material']
                cpt_data[material] = {
                    'CPT_C': int(row['CPT_C']),
                    'CCT_C': int(row['CCT_C']),
                    'test_solution': row['test_solution'],
                    'source': row['source'],
                }
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse CPT row: {row}. Error: {e}")
                continue

    logger.info(f"Loaded CPT data for {len(cpt_data)} materials from {csv_file}")

    _CPT_DATA_CACHE = cpt_data
    return cpt_data


def load_galvanic_series_from_csv() -> Dict[str, float]:
    """
    Load galvanic series potentials from CSV file.

    Returns:
        Dictionary mapping material name to potential (V vs SCE)

    Note:
        Potentials are in SCE reference. To convert to SHE, add 0.241V.

    Example:
        >>> galv_series = load_galvanic_series_from_csv()
        >>> galv_series["Carbon Steel"]
        -0.610  # V vs SCE
    """
    global _GALVANIC_SERIES_CACHE

    if _GALVANIC_SERIES_CACHE is not None:
        return _GALVANIC_SERIES_CACHE

    csv_file = DATA_DIR / "astm_g82_galvanic_series.csv"

    if not csv_file.exists():
        raise FileNotFoundError(
            f"Galvanic series CSV not found: {csv_file}. "
            f"Expected file: astm_g82_galvanic_series.csv"
        )

    galvanic_series = {}

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                material = row['material']
                # Use SCE values (original ASTM G82 reference)
                potential_sce = float(row['potential_sce_V'])
                galvanic_series[material] = potential_sce
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse galvanic series row: {row}. Error: {e}")
                continue

    logger.info(f"Loaded galvanic series for {len(galvanic_series)} materials from {csv_file}")

    _GALVANIC_SERIES_CACHE = galvanic_series
    return galvanic_series


def load_orr_diffusion_limits_from_csv() -> Dict[str, float]:
    """
    Load ORR diffusion limits from CSV file.

    Returns:
        Dictionary mapping condition name to diffusion-limited current density (A/m²)

    Example:
        >>> orr_limits = load_orr_diffusion_limits_from_csv()
        >>> orr_limits["seawater_25C"]
        5.0  # A/m²
    """
    global _ORR_LIMITS_CACHE

    if _ORR_LIMITS_CACHE is not None:
        return _ORR_LIMITS_CACHE

    csv_file = DATA_DIR / "orr_diffusion_limits.csv"

    if not csv_file.exists():
        raise FileNotFoundError(
            f"ORR diffusion limits CSV not found: {csv_file}. "
            f"Expected file: orr_diffusion_limits.csv"
        )

    orr_limits = {}

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                condition = row['condition']
                i_lim = float(row['i_lim_A_m2'])
                orr_limits[condition] = i_lim
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse ORR limit row: {row}. Error: {e}")
                continue

    logger.info(f"Loaded ORR diffusion limits for {len(orr_limits)} conditions from {csv_file}")

    _ORR_LIMITS_CACHE = orr_limits
    return orr_limits


def load_chloride_thresholds_from_csv() -> Dict[str, float]:
    """
    Load chloride thresholds from CSV file.

    Returns:
        Dictionary mapping material name to chloride threshold (mg/L) at 25°C, pH 7.0

    Example:
        >>> thresholds = load_chloride_thresholds_from_csv()
        >>> thresholds["316L"]
        250.0  # mg/L
    """
    global _CHLORIDE_THRESHOLD_CACHE

    if _CHLORIDE_THRESHOLD_CACHE is not None:
        return _CHLORIDE_THRESHOLD_CACHE

    csv_file = DATA_DIR / "iso18070_chloride_thresholds.csv"

    if not csv_file.exists():
        raise FileNotFoundError(
            f"Chloride thresholds CSV not found: {csv_file}. "
            f"Expected file: iso18070_chloride_thresholds.csv"
        )

    thresholds = {}

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                material = row['material']
                threshold = float(row['threshold_25C_mg_L'])
                thresholds[material] = threshold
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse chloride threshold row: {row}. Error: {e}")
                continue

    logger.info(f"Loaded chloride thresholds for {len(thresholds)} materials from {csv_file}")

    _CHLORIDE_THRESHOLD_CACHE = thresholds
    return thresholds


def load_temperature_coefficients_from_csv() -> Dict[str, float]:
    """
    Load temperature coefficients from CSV file.

    Returns:
        Dictionary mapping grade_type to temperature coefficient (/°C)

    Example:
        >>> coeffs = load_temperature_coefficients_from_csv()
        >>> coeffs["austenitic"]
        0.05  # /°C
    """
    global _TEMP_COEFFICIENT_CACHE

    if _TEMP_COEFFICIENT_CACHE is not None:
        return _TEMP_COEFFICIENT_CACHE

    csv_file = DATA_DIR / "iso18070_temperature_coefficients.csv"

    if not csv_file.exists():
        raise FileNotFoundError(
            f"Temperature coefficients CSV not found: {csv_file}. "
            f"Expected file: iso18070_temperature_coefficients.csv"
        )

    coefficients = {}

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                grade_type = row['grade_type']
                coefficient = float(row['temp_coefficient_per_C'])
                coefficients[grade_type] = coefficient
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse temperature coefficient row: {row}. Error: {e}")
                continue

    logger.info(f"Loaded temperature coefficients for {len(coefficients)} grade types from {csv_file}")

    _TEMP_COEFFICIENT_CACHE = coefficients
    return coefficients


def clear_caches():
    """Clear all cached CSV data (useful for testing or reloading)."""
    global _MATERIALS_CACHE, _CPT_DATA_CACHE, _GALVANIC_SERIES_CACHE
    global _ORR_LIMITS_CACHE, _CHLORIDE_THRESHOLD_CACHE, _TEMP_COEFFICIENT_CACHE
    _MATERIALS_CACHE = None
    _CPT_DATA_CACHE = None
    _GALVANIC_SERIES_CACHE = None
    _ORR_LIMITS_CACHE = None
    _CHLORIDE_THRESHOLD_CACHE = None
    _TEMP_COEFFICIENT_CACHE = None
    logger.info("Cleared all CSV data caches")


__all__ = [
    "MaterialComposition",
    "load_materials_from_csv",
    "load_cpt_data_from_csv",
    "load_galvanic_series_from_csv",
    "load_orr_diffusion_limits_from_csv",
    "load_chloride_thresholds_from_csv",
    "load_temperature_coefficients_from_csv",
    "clear_caches",
]
