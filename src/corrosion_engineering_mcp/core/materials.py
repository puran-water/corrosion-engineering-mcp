"""YAML-backed material catalog and alias resolver."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
import yaml
CATALOG_PATH = Path(__file__).resolve().parents[1] / 'data' / 'materials.yaml'

class MaterialCatalogError(ValueError):
    """Raised when the material catalog is malformed."""

class UnknownMaterialError(MaterialCatalogError):
    """Raised when a material alias cannot be resolved."""

    def __init__(self, material: str) -> None:
        super().__init__(f"UNKNOWN_MATERIAL: Material '{material}' is not in data/materials.yaml. Use one of: {', '.join(all_canonical_ids())}")
        self.material = material

@dataclass(frozen=True)
class MaterialRecord:
    """Canonical material catalog record."""
    canonical_id: str
    aliases: tuple[str, ...]
    composition: dict[str, float]
    PREN: float | None
    galvanic_potential_v_sce_seawater: float
    density_kg_m3: float
    classification: str
    validity_range_pH: tuple[float, float]
    validity_range_temp_C: tuple[float, float]
    uns: str | None = None

def _normalization_key(value: str) -> str:
    """Normalize aliases across case, hyphen, spaces, slashes, dots, and UNS prefix."""
    key = re.sub('[^A-Z0-9]', '', value.upper())
    if key.startswith('UNS'):
        return key[3:]
    return key

def _coerce_range(value: Any, field_name: str, material_id: str) -> tuple[float, float]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise MaterialCatalogError(f'{material_id}.{field_name} must be a two-item range')
    minimum, maximum = (float(value[0]), float(value[1]))
    if minimum > maximum:
        raise MaterialCatalogError(f'{material_id}.{field_name} minimum exceeds maximum')
    return (minimum, maximum)

def _coerce_record(raw: dict[str, Any]) -> MaterialRecord:
    canonical = str(raw['canonical_id'])
    aliases = tuple((str(alias) for alias in raw.get('aliases', [])))
    if not aliases:
        raise MaterialCatalogError(f'{canonical}.aliases must not be empty')
    composition_raw = raw.get('composition', {})
    if not isinstance(composition_raw, dict) or not composition_raw:
        raise MaterialCatalogError(f'{canonical}.composition must be a non-empty mapping')
    composition = {str(element): float(mass_pct) for element, mass_pct in composition_raw.items()}
    pren = raw.get('PREN')
    return MaterialRecord(canonical_id=canonical, aliases=aliases, uns=str(raw['uns']) if raw.get('uns') is not None else None, composition=composition, PREN=float(pren) if pren is not None else None, galvanic_potential_v_sce_seawater=float(raw['galvanic_potential_v_sce_seawater']), density_kg_m3=float(raw['density_kg_m3']), classification=str(raw['classification']), validity_range_pH=_coerce_range(raw['validity_range_pH'], 'validity_range_pH', canonical), validity_range_temp_C=_coerce_range(raw['validity_range_temp_C'], 'validity_range_temp_C', canonical))

def _load_catalog() -> dict[str, MaterialRecord]:
    with CATALOG_PATH.open('r', encoding='utf-8') as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get('materials'), list):
        raise MaterialCatalogError('data/materials.yaml must contain a top-level materials list')
    catalog: dict[str, MaterialRecord] = {}
    for raw_record in payload['materials']:
        if not isinstance(raw_record, dict):
            raise MaterialCatalogError('material catalog entries must be mappings')
        record = _coerce_record(raw_record)
        if record.canonical_id in catalog:
            raise MaterialCatalogError(f'Duplicate material canonical_id: {record.canonical_id}')
        catalog[record.canonical_id] = record
    return catalog

def _build_alias_index(catalog: dict[str, MaterialRecord]) -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, record in catalog.items():
        tokens = [canonical, *record.aliases]
        if record.uns:
            tokens.extend([record.uns, f'UNS {record.uns}'])
        for token in tokens:
            key = _normalization_key(token)
            existing = index.get(key)
            if existing and existing != canonical:
                raise MaterialCatalogError(f"Alias collision for '{token}': {existing} and {canonical}")
            index[key] = canonical
    return index
MATERIALS_CATALOG = _load_catalog()
_ALIAS_INDEX = _build_alias_index(MATERIALS_CATALOG)
_ALIASES_BY_LENGTH = sorted(_ALIAS_INDEX.items(), key=lambda item: len(item[0]), reverse=True)

def all_canonical_ids() -> list[str]:
    """Return canonical material IDs in catalog order."""
    return list(MATERIALS_CATALOG)

def advertised_materials() -> list[str]:
    """Return the public material IDs advertised by server metadata."""
    return all_canonical_ids()

def all_materials() -> list[MaterialRecord]:
    """Return all catalog records."""
    return list(MATERIALS_CATALOG.values())

def canonical_id(material: str) -> str:
    """Resolve a user material string to a catalog canonical ID."""
    key = _normalization_key(material)
    resolved = _ALIAS_INDEX.get(key)
    if resolved is None:
        raise UnknownMaterialError(material)
    return resolved

def try_canonical_id(material: str) -> str | None:
    """Resolve a material alias, returning None if it is unknown."""
    try:
        return canonical_id(material)
    except UnknownMaterialError:
        return None

def get_material(material: str) -> MaterialRecord:
    """Return the catalog record for a material alias or canonical ID."""
    return MATERIALS_CATALOG[canonical_id(material)]

def find_material_ids(text: str) -> list[str]:
    """Find catalog materials mentioned in free text."""
    normalized_text = _normalization_key(text)
    seen: set[str] = set()
    matches: list[str] = []
    for alias_key, canonical in _ALIASES_BY_LENGTH:
        if alias_key and alias_key in normalized_text and (canonical not in seen):
            matches.append(canonical)
            seen.add(canonical)
    return matches

def format_composition(record: MaterialRecord) -> str:
    """Format key alloying elements as a compact composition string."""
    parts = [f'{repr(float(mass_pct))}{element}' for element, mass_pct in record.composition.items()]
    composition = '-'.join(parts)
    if record.uns:
        return f'{composition} (UNS {record.uns})'
    return composition
__all__ = ['CATALOG_PATH', 'MATERIALS_CATALOG', 'MaterialCatalogError', 'MaterialRecord', 'UnknownMaterialError', 'advertised_materials', 'all_canonical_ids', 'all_materials', 'canonical_id', 'find_material_ids', 'format_composition', 'get_material', 'try_canonical_id']
