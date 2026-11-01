"""
NRL Seawater Galvanic Series Data Loader

DIRECT IMPORT from: US Naval Research Laboratory
Source: corrosion-modeling-applications/cma/SeawaterPotentialData.xml
License: MIT (upstream notice included)

Standard Reference:
- ASTM G82-98 (2014): "Standard Guide for Development and Use of a
  Galvanic Series for Predicting Galvanic Corrosion Performance"

All potentials are measured vs. Saturated Calomel Electrode (SCE) reference.
Per ASTM G3 and NACE SP0208: E(SHE) = E(SCE) + 0.241V

Data Source: NRL's corrosion modeling applications XML database
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from corrosion_engineering_mcp.core.errors import AmbiguousGalvanicMaterialError
NRL_XML_PATH = Path(__file__).parent.parent / 'external' / 'nrl_coefficients' / 'SeawaterPotentialData.xml'
E_SCE_TO_SHE = 0.241

@dataclass
class GalvanicSeriesEntry:
    """
    Entry in the galvanic series.

    Attributes:
        name: Material name from ASTM G82 / NRL database
        potential_sce: Corrosion potential vs SCE, V
        potential_she: Corrosion potential vs SHE, V (calculated)
        activity_category: NRL activity category (A-T)
    """
    name: str
    potential_sce: float
    potential_she: float
    activity_category: Optional[str] = None

def load_galvanic_series_xml() -> Dict[str, GalvanicSeriesEntry]:
    """
    Load galvanic series data from NRL XML file.

    Returns:
        Dictionary mapping material name (lowercase, normalized) to GalvanicSeriesEntry

    Raises:
        FileNotFoundError: If XML file not found
        ET.ParseError: If XML is malformed

    Example:
        >>> data = load_galvanic_series_xml()
        >>> carbon_steel = data["carbon steel"]
        >>> carbon_steel.potential_sce
        -0.61
        >>> carbon_steel.potential_she
        -0.369  # -0.61 + 0.241
    """
    if not NRL_XML_PATH.exists():
        raise FileNotFoundError(f'NRL galvanic series XML not found at {NRL_XML_PATH}. Expected file: SeawaterPotentialData.xml')
    tree = ET.parse(NRL_XML_PATH)
    root = tree.getroot()
    galvanic_series = {}
    for data_elem in root.findall('Data'):
        name_elem = data_elem.find('Name')
        potential_elem = data_elem.find('PotentialValue')
        category_elem = data_elem.find('ActivityCategory')
        if name_elem is None or potential_elem is None:
            continue
        name = name_elem.text.strip()
        potential_sce = float(potential_elem.text)
        activity_category = category_elem.text if category_elem is not None else None
        potential_she = potential_sce + E_SCE_TO_SHE
        entry = GalvanicSeriesEntry(name=name, potential_sce=potential_sce, potential_she=potential_she, activity_category=activity_category)
        key_normalized = name.lower().replace('-', ' ').replace(',', '')
        galvanic_series[key_normalized] = entry
    return galvanic_series
_GALVANIC_SERIES_CACHE: Optional[Dict[str, GalvanicSeriesEntry]] = None

def get_galvanic_potential(material: str, reference: str='SHE') -> Optional[float]:
    """
    Get galvanic potential for a material from NRL database.

    Args:
        material: Material name (case-insensitive, fuzzy matching)
        reference: Reference electrode ("SHE" or "SCE")

    Returns:
        Corrosion potential in volts vs specified reference, or None if not found

    Example:
        >>> get_galvanic_potential("Carbon Steel", "SCE")
        -0.61
        >>> get_galvanic_potential("Carbon Steel", "SHE")
        -0.369
        >>> get_galvanic_potential("316L passive")  # Fuzzy match
        -0.05 + 0.241 = 0.191
    """
    global _GALVANIC_SERIES_CACHE
    generic = material.strip().lower().replace('_', ' ').replace('-', ' ')
    if generic in {'al', 'aluminum', 'aluminium'}:
        raise AmbiguousGalvanicMaterialError(material)
    if _GALVANIC_SERIES_CACHE is None:
        _GALVANIC_SERIES_CACHE = load_galvanic_series_xml()
    material_normalized = material.lower().replace('-', ' ').replace(',', '').replace('_', ' ')
    if material_normalized in _GALVANIC_SERIES_CACHE:
        entry = _GALVANIC_SERIES_CACHE[material_normalized]
        return entry.potential_she if reference == 'SHE' else entry.potential_sce
    for key, entry in _GALVANIC_SERIES_CACHE.items():
        if material_normalized in key or key in material_normalized:
            return entry.potential_she if reference == 'SHE' else entry.potential_sce
    keywords_to_try = [('316', 'stainless steel  type 316  passive'), ('304', 'stainless steel  type 304  passive'), ('430', 'stainless steel  type 430  passive'), ('410', 'stainless steel  type 410  active'), ('carbon', 'carbon steel'), ('steel', 'carbon steel'), ('copper', 'copper'), ('brass', 'copper alloy 230 (red brass)'), ('bronze', 'copper alloy 954 (aluminum bronze)'), ('inconel', 'nickel alloy 600 (ni cr)'), ('monel', 'nickel alloy 400 (monel 400)'), ('hastelloy', 'nickel alloy 276 (hastelloy c)'), ('titanium', 'titanium (solid)'), ('ti', 'titanium (solid)'), ('zinc', 'zinc'), ('lead', 'lead'), ('magnesium', 'magnesium')]
    for keyword, target_key in keywords_to_try:
        if keyword in material_normalized:
            entry = _GALVANIC_SERIES_CACHE.get(target_key)
            if entry:
                return entry.potential_she if reference == 'SHE' else entry.potential_sce
    return None

def get_galvanic_series_entry(material: str) -> Optional[GalvanicSeriesEntry]:
    """
    Get full galvanic series entry for a material.

    Args:
        material: Material name (case-insensitive, fuzzy matching)

    Returns:
        GalvanicSeriesEntry with all data, or None if not found
    """
    global _GALVANIC_SERIES_CACHE
    generic = material.strip().lower().replace('_', ' ').replace('-', ' ')
    if generic in {'al', 'aluminum', 'aluminium'}:
        raise AmbiguousGalvanicMaterialError(material)
    if _GALVANIC_SERIES_CACHE is None:
        _GALVANIC_SERIES_CACHE = load_galvanic_series_xml()
    material_normalized = material.lower().replace('-', ' ').replace(',', '').replace('_', ' ')
    if material_normalized in _GALVANIC_SERIES_CACHE:
        return _GALVANIC_SERIES_CACHE[material_normalized]
    for key, entry in _GALVANIC_SERIES_CACHE.items():
        if material_normalized in key or key in material_normalized:
            return entry
    return None

def list_available_materials() -> list[str]:
    """
    List all materials available in the NRL galvanic series database.

    Returns:
        Sorted list of material names
    """
    global _GALVANIC_SERIES_CACHE
    if _GALVANIC_SERIES_CACHE is None:
        _GALVANIC_SERIES_CACHE = load_galvanic_series_xml()
    return sorted([entry.name for entry in _GALVANIC_SERIES_CACHE.values()])
__all__ = ['GalvanicSeriesEntry', 'load_galvanic_series_xml', 'get_galvanic_potential', 'get_galvanic_series_entry', 'list_available_materials', 'E_SCE_TO_SHE']
