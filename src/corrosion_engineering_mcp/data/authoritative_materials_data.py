"""Material screening data APIs with explicit provenance standing"""
from typing import Dict, Optional, Tuple
from .csv_loaders import MaterialComposition, QuarantinedDataError, load_materials_from_csv, load_cpt_data_from_csv, load_galvanic_series_from_csv, load_chloride_thresholds_from_csv, load_temperature_coefficients_from_csv
MATERIALS_DATABASE = load_materials_from_csv()
ASTM_G48_CPT_DATA = load_cpt_data_from_csv()
CHLORIDE_THRESHOLD_25C = load_chloride_thresholds_from_csv()
CHLORIDE_TEMP_COEFFICIENT = load_temperature_coefficients_from_csv()
GALVANIC_SERIES_SEAWATER = load_galvanic_series_from_csv()

def get_material_data(material_name: str) -> Optional[MaterialComposition]:
    """Get material composition from authoritative database."""
    material_normalized = material_name.upper().replace(' ', '_').replace('-', '_')
    for key, comp in MATERIALS_DATABASE.items():
        key_normalized = key.upper().replace(' ', '_').replace('-', '_')
        if key_normalized == material_normalized or comp.UNS == material_normalized:
            return comp
    for key, comp in MATERIALS_DATABASE.items():
        key_normalized = key.upper().replace(' ', '_').replace('-', '_')
        if key_normalized in material_normalized or material_normalized in key_normalized:
            return comp
    return None

def calculate_pren(comp: MaterialComposition) -> float:
    """Calculate the default PREN16 relative composition index."""
    return comp.Cr_wt_pct + 3.3 * comp.Mo_wt_pct + 16.0 * comp.N_wt_pct

def get_cpt_from_astm(material_name: str) -> Optional[Dict]:
    """Get CPT/CCT from ASTM G48 tabulated data."""
    from corrosion_engineering_mcp.core.materials import MaterialCatalogError, canonical_id
    try:
        resolved = canonical_id(material_name)
    except MaterialCatalogError:
        return None
    for key, data in ASTM_G48_CPT_DATA.items():
        try:
            if canonical_id(key) == resolved:
                return data
        except MaterialCatalogError:
            continue
    return None
_CLASSIFICATION_TO_GRADE_TYPE = {'austenitic_stainless': 'austenitic', 'duplex_stainless': 'duplex', 'super_duplex_stainless': 'super_duplex', 'superaustenitic_stainless': 'superaustenitic', 'nickel_alloy': 'nickel_alloy', 'carbon_steel': 'carbon_steel', 'low_alloy_steel': 'carbon_steel'}

def get_chloride_threshold(material_name: str, temperature_C: float=25.0, pH: float=7.0) -> float:
    """Get an INTERNAL_CL_SCREEN_V1 ranking-only chloride reference coordinate."""
    from corrosion_engineering_mcp.core.localized_backend import ModelNotApplicableError
    from corrosion_engineering_mcp.core.materials import MaterialCatalogError, UnknownMaterialError, canonical_id, get_material
    try:
        resolved = canonical_id(material_name)
    except MaterialCatalogError:
        raise UnknownMaterialError(material_name) from None
    Cl_25C = None
    for key, value in CHLORIDE_THRESHOLD_25C.items():
        try:
            if canonical_id(key) == resolved:
                Cl_25C = value
                break
        except MaterialCatalogError:
            continue
    if Cl_25C is None:
        raise ModelNotApplicableError(resolved, get_material(resolved).classification, 'chloride screening threshold', 'corrosion_resolve_material_identity to confirm the catalog identity')
    grade_type = _CLASSIFICATION_TO_GRADE_TYPE.get(get_material(resolved).classification)
    if grade_type is None:
        raise ModelNotApplicableError(resolved, get_material(resolved).classification, 'chloride threshold temperature correction', 'no temperature coefficient is tabulated for this alloy family')
    k = CHLORIDE_TEMP_COEFFICIENT[grade_type]
    import math
    delta_T = temperature_C - 25.0
    Cl_T = Cl_25C * math.exp(-k * delta_T)
    from corrosion_engineering_mcp.utils.localized_criteria import chloride_threshold_pH_factor
    Cl_T *= chloride_threshold_pH_factor(pH)
    return Cl_T

def get_orr_diffusion_limit(electrolyte: str='seawater', temperature_C: float=25.0) -> float:
    """Refuse every request for the quarantined ORR limiting-current data."""
    del electrolyte, temperature_C
    from corrosion_engineering_mcp.data.csv_loaders import load_provenance_registry
    entry = load_provenance_registry()['sources']['NRL_ORR_LIMITS_V1_SUSPECT']
    raise QuarantinedDataError('NRL_ORR_LIMITS_V1_SUSPECT', entry)

def get_chloride_threshold_record(material_name: str, temperature_C: float=25.0, pH: float=7.0):
    """Chloride threshold WITH its provenance, for scripted consumers."""
    from corrosion_engineering_mcp.data.csv_loaders import provenanced
    value = get_chloride_threshold(material_name, temperature_C, pH)
    rec = provenanced(value, 'INTERNAL_CL_SCREEN_V1', units='mg/L', contributing_source_ids=['INTERNAL_CL_TEMPCOEFF_V1'] if temperature_C != 25.0 else [])
    validity_T = rec.validity.get('temperature_C')
    if validity_T and (not validity_T[0] <= temperature_C <= validity_T[1]):
        rec.assumptions.append(f'OUT OF STATED VALIDITY: {temperature_C} C is outside {validity_T[0]}-{validity_T[1]} C for {rec.source_id}')
    return rec
