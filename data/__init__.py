"""
Authoritative Corrosion Data Module

Contains data from:
- ASTM standards (G48, G82, A240, etc.)
- ISO standards (18070)
- NORSOK M-001, M-506
- UNS material designations
- NRL corrosion-modeling-applications (DIRECT IMPORT via CSV files and XML)

All data is from published authoritative sources, NOT simplified heuristics.
"""

from .authoritative_materials_data import (
    MATERIALS_DATABASE,
    ASTM_G48_CPT_DATA,
    CHLORIDE_THRESHOLD_25C,
    CHLORIDE_TEMP_COEFFICIENT,
    ORR_DIFFUSION_LIMITS,
    GALVANIC_SERIES_SEAWATER,
    MaterialComposition,
    get_material_data,
    calculate_pren,
    get_cpt_from_astm,
    get_chloride_threshold,
    get_orr_diffusion_limit,
)

# DIRECT IMPORT: NRL polarization curve data
from .nrl_polarization_curves import (
    TafelParameters,
    ResponseSurfaceCoeffs,
    get_orr_parameters,
    get_her_parameters,
    get_passivation_parameters,
    get_metal_oxidation_parameters,
    get_pitting_parameters,
    get_all_parameters,
    R_GAS,
    F_FARADAY,
    E_SHE_TO_SCE,
)

# DIRECT IMPORT: NRL galvanic series (XML parser)
from .nrl_galvanic_series import (
    GalvanicSeriesEntry,
    load_galvanic_series_xml,
    get_galvanic_potential,
    get_galvanic_series_entry,
    list_available_materials as list_galvanic_materials,
)

# DIRECT IMPORT: NORSOK M-506 internal corrosion
from .norsok_internal_corrosion import (
    get_ph_correction_factor,
    get_chloride_threshold_norsok,
    calculate_shear_stress,
    calculate_insitu_pH,
    calculate_norsok_corrosion_rate,
)

__all__ = [
    # Material database (CSV-backed from ASTM standards - 100% authoritative data)
    "MATERIALS_DATABASE",
    "ASTM_G48_CPT_DATA",
    "CHLORIDE_THRESHOLD_25C",
    "CHLORIDE_TEMP_COEFFICIENT",
    "ORR_DIFFUSION_LIMITS",
    "GALVANIC_SERIES_SEAWATER",
    "MaterialComposition",
    "get_material_data",
    "calculate_pren",
    "get_cpt_from_astm",
    "get_chloride_threshold",
    "get_orr_diffusion_limit",
    # NRL polarization curves (DIRECT IMPORT - CSV files)
    "TafelParameters",
    "ResponseSurfaceCoeffs",
    "get_orr_parameters",
    "get_her_parameters",
    "get_passivation_parameters",
    "get_metal_oxidation_parameters",
    "get_pitting_parameters",
    "get_all_parameters",
    "R_GAS",
    "F_FARADAY",
    "E_SHE_TO_SCE",
    # NRL galvanic series (DIRECT IMPORT - XML parser)
    "GalvanicSeriesEntry",
    "load_galvanic_series_xml",
    "get_galvanic_potential",
    "get_galvanic_series_entry",
    "list_galvanic_materials",
    # NORSOK M-506 (DIRECT IMPORT - vendored module)
    "get_ph_correction_factor",
    "get_chloride_threshold_norsok",
    "calculate_shear_stress",
    "calculate_insitu_pH",
    "calculate_norsok_corrosion_rate",
]
