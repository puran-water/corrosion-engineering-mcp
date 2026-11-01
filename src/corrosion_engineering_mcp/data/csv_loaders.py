"""CSV Data Loaders for Authoritative Corrosion Data"""
import csv
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from functools import lru_cache
import logging
logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent

def _parse_declared_integer(value: str, field_name: str) -> int:
    """Parse a base-10 integer token without permitting numeric truncation."""
    token = value.strip()
    if not token:
        raise ValueError(f'{field_name} must be a base-10 integer')
    sign = -1 if token.startswith('-') else 1
    digits = token[1:] if token[:1] in {'+', '-'} else token
    if not digits or not digits.isdecimal():
        raise ValueError(f'{field_name} must be a base-10 integer')
    magnitude = 0
    for digit in digits:
        magnitude = magnitude * 10 + (ord(digit) - ord('0'))
    return sign * magnitude

class QuarantinedDataError(RuntimeError):
    """Raised whenever a public API requests a quarantined data source."""

    def __init__(self, source_id: str, entry: dict) -> None:
        super().__init__(f'QUARANTINED_DATA_SOURCE: {source_id} is quarantined and usable for nothing. Reason: {(entry.get('quarantine_reason') or '').strip()} Supersede when: {'; '.join(entry.get('supersede_when', []) or [])}')
        self.source_id = source_id

@dataclass
class MaterialComposition:
    """Material composition from CSV file."""
    UNS: str
    common_name: str
    Cr_wt_pct: float
    Mo_wt_pct: float
    N_wt_pct: float
    Ni_wt_pct: float
    Fe_bal: bool = True
    density_kg_m3: float = 8000.0
    grade_type: str = 'austenitic'
    n_electrons: int = 2
    source: str = 'ASTM'
_MATERIALS_CACHE: Optional[Dict[str, MaterialComposition]] = None
_CPT_DATA_CACHE: Optional[Dict[str, Dict]] = None
_GALVANIC_SERIES_CACHE: Optional[Dict[str, float]] = None
_CHLORIDE_THRESHOLD_CACHE: Optional[Dict[str, float]] = None
_TEMP_COEFFICIENT_CACHE: Optional[Dict[str, float]] = None

def load_materials_from_csv() -> Dict[str, MaterialComposition]:
    """Load material compositions from CSV file."""
    global _MATERIALS_CACHE
    if _MATERIALS_CACHE is not None:
        return _MATERIALS_CACHE
    csv_file = DATA_DIR / 'materials_compositions.csv'
    if not csv_file.exists():
        raise FileNotFoundError(f'Materials CSV not found: {csv_file}. Expected file: materials_compositions.csv')
    materials = {}
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                mat = MaterialComposition(UNS=row['UNS'], common_name=row['common_name'], Cr_wt_pct=float(row['Cr_wt_pct']), Ni_wt_pct=float(row['Ni_wt_pct']), Mo_wt_pct=float(row['Mo_wt_pct']), N_wt_pct=float(row['N_wt_pct']), density_kg_m3=float(row['density_kg_m3']), grade_type=row['grade_type'], n_electrons=_parse_declared_integer(row['n_electrons'], 'n_electrons'), Fe_bal=row['Fe_bal'].lower() == 'true', source=row['source'])
                materials[row['common_name']] = mat
            except (KeyError, ValueError) as e:
                logger.warning(f'Failed to parse material row: {row}. Error: {e}')
                continue
    logger.info(f'Loaded {len(materials)} materials from {csv_file}')
    _MATERIALS_CACHE = materials
    return materials

def load_cpt_data_from_csv() -> Dict[str, Dict]:
    """Load Critical Pitting Temperature (CPT) data from CSV file."""
    global _CPT_DATA_CACHE
    if _CPT_DATA_CACHE is not None:
        return _CPT_DATA_CACHE
    csv_file = DATA_DIR / 'astm_g48_cpt_data.csv'
    if not csv_file.exists():
        raise FileNotFoundError(f'CPT data CSV not found: {csv_file}. Expected file: astm_g48_cpt_data.csv')
    cpt_data = {}
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                material = row['material']
                cpt_data[material] = {'CPT_C': _parse_declared_integer(row['CPT_C'], 'CPT_C'), 'CCT_C': _parse_declared_integer(row['CCT_C'], 'CCT_C'), 'test_solution': row['test_solution'], 'source': row['source']}
            except (KeyError, ValueError) as e:
                logger.warning(f'Failed to parse CPT row: {row}. Error: {e}')
                continue
    logger.info(f'Loaded CPT data for {len(cpt_data)} materials from {csv_file}')
    _CPT_DATA_CACHE = cpt_data
    return cpt_data

def load_galvanic_series_from_csv() -> Dict[str, float]:
    """Load galvanic series potentials from CSV file."""
    global _GALVANIC_SERIES_CACHE
    if _GALVANIC_SERIES_CACHE is not None:
        return _GALVANIC_SERIES_CACHE
    csv_file = DATA_DIR / 'astm_g82_galvanic_series.csv'
    if not csv_file.exists():
        raise FileNotFoundError(f'Galvanic series CSV not found: {csv_file}. Expected file: astm_g82_galvanic_series.csv')
    galvanic_series = {}
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                material = row['material']
                potential_sce = float(row['potential_sce_V'])
                galvanic_series[material] = potential_sce
            except (KeyError, ValueError) as e:
                logger.warning(f'Failed to parse galvanic series row: {row}. Error: {e}')
                continue
    logger.info(f'Loaded galvanic series for {len(galvanic_series)} materials from {csv_file}')
    _GALVANIC_SERIES_CACHE = galvanic_series
    return galvanic_series

def load_orr_diffusion_limits_from_csv() -> Dict[str, float]:
    """Refuse public access to the quarantined ORR limiting-current table."""
    entry = load_provenance_registry()['sources']['NRL_ORR_LIMITS_V1_SUSPECT']
    raise QuarantinedDataError('NRL_ORR_LIMITS_V1_SUSPECT', entry)

def load_chloride_thresholds_from_csv() -> Dict[str, float]:
    """Load chloride thresholds from CSV file."""
    global _CHLORIDE_THRESHOLD_CACHE
    if _CHLORIDE_THRESHOLD_CACHE is not None:
        return _CHLORIDE_THRESHOLD_CACHE
    csv_file = DATA_DIR / 'iso18070_chloride_thresholds.csv'
    if not csv_file.exists():
        raise FileNotFoundError(f'Chloride thresholds CSV not found: {csv_file}. Expected file: iso18070_chloride_thresholds.csv')
    thresholds = {}
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                material = row['material']
                threshold = float(row['threshold_25C_mg_L'])
                thresholds[material] = threshold
            except (KeyError, ValueError) as e:
                logger.warning(f'Failed to parse chloride threshold row: {row}. Error: {e}')
                continue
    logger.info(f'Loaded chloride thresholds for {len(thresholds)} materials from {csv_file}')
    _CHLORIDE_THRESHOLD_CACHE = thresholds
    return thresholds

def load_temperature_coefficients_from_csv() -> Dict[str, float]:
    """Load temperature coefficients from CSV file."""
    global _TEMP_COEFFICIENT_CACHE
    if _TEMP_COEFFICIENT_CACHE is not None:
        return _TEMP_COEFFICIENT_CACHE
    csv_file = DATA_DIR / 'iso18070_temperature_coefficients.csv'
    if not csv_file.exists():
        raise FileNotFoundError(f'Temperature coefficients CSV not found: {csv_file}. Expected file: iso18070_temperature_coefficients.csv')
    coefficients = {}
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                grade_type = row['grade_type']
                coefficient = float(row['temp_coefficient_per_C'])
                coefficients[grade_type] = coefficient
            except (KeyError, ValueError) as e:
                logger.warning(f'Failed to parse temperature coefficient row: {row}. Error: {e}')
                continue
    logger.info(f'Loaded temperature coefficients for {len(coefficients)} grade types from {csv_file}')
    _TEMP_COEFFICIENT_CACHE = coefficients
    return coefficients

def clear_caches():
    """Clear all cached CSV data (useful for testing or reloading)."""
    global _MATERIALS_CACHE, _CPT_DATA_CACHE, _GALVANIC_SERIES_CACHE
    global _CHLORIDE_THRESHOLD_CACHE, _TEMP_COEFFICIENT_CACHE
    _MATERIALS_CACHE = None
    _CPT_DATA_CACHE = None
    _GALVANIC_SERIES_CACHE = None
    _CHLORIDE_THRESHOLD_CACHE = None
    _TEMP_COEFFICIENT_CACHE = None
    logger.info('Cleared all CSV data caches')
__all__ = ['MaterialComposition', 'QuarantinedDataError', 'load_materials_from_csv', 'load_cpt_data_from_csv', 'load_galvanic_series_from_csv', 'load_orr_diffusion_limits_from_csv', 'load_chloride_thresholds_from_csv', 'load_temperature_coefficients_from_csv', 'clear_caches']
_PROVENANCE_PATH = DATA_DIR / 'provenance.yaml'

@lru_cache(maxsize=1)
def load_provenance_registry() -> Dict[str, Any]:
    """Load data/provenance.yaml. Authoritative; PROVENANCE.md is the narrative."""
    import yaml
    if not _PROVENANCE_PATH.exists():
        raise FileNotFoundError(f'PROVENANCE_REGISTRY_MISSING: {_PROVENANCE_PATH.name} is required; numeric data cannot be loaded without it')
    with open(_PROVENANCE_PATH, encoding='utf-8') as fh:
        registry = yaml.safe_load(fh)
    if not registry or 'sources' not in registry:
        raise ValueError("PROVENANCE_REGISTRY_MALFORMED: missing 'sources' key")
    return registry

def registered_source_ids() -> frozenset:
    return frozenset(load_provenance_registry()['sources'].keys())

@dataclass(frozen=True)
class ProvenancedValue:
    """A number with everything a consumer needs to decide whether to trust it."""
    value: float
    units: str
    source_id: str
    evidence_class: str
    derivation: str
    assumptions: List[str]
    validity: Dict[str, Any]
    usable_for: List[str]
    not_usable_for: List[str]
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_qualification_grade(self) -> bool:
        """True only when this value may decide whether a material is acceptable."""
        return 'qualification' in self.usable_for

    @property
    def composite_source_ids(self) -> List[str]:
        """Every source that contributed to this value, not just the headline one."""
        return [self.source_id, *self.extras.get('contributing_source_ids', [])]

    @property
    def supports_negative_screening(self) -> bool:
        """True when the registry explicitly licenses a bounded NEGATIVE verdict."""
        return all(('conservative_negative_screening' in _usable_for_of(sid) for sid in self.composite_source_ids))

    @property
    def negative_screening_min_ratio(self) -> Optional[float]:
        """Exceedance factor below which even a negative verdict is unlicensed."""
        v = self.extras.get('negative_screening_min_ratio')
        return float(v) if v is not None else None

    def require_qualification_grade(self) -> 'ProvenancedValue':
        """Fail closed when a caller tries to qualify on a ranking-only value."""
        if not self.is_qualification_grade:
            raise ValueError(f'NOT_QUALIFICATION_GRADE: source {self.source_id} is {self.evidence_class} and is usable only for {self.usable_for}. It must not be used to qualify a material. Reason: {(self.derivation.strip().splitlines()[0] if self.derivation else 'see provenance.yaml')}')
        return self

def _usable_for_of(source_id: str) -> List[str]:
    """usable_for of a registry entry, for composite checks."""
    reg = load_provenance_registry()['sources']
    return list((reg.get(source_id) or {}).get('usable_for', []) or [])

def provenanced(value: float, source_id: str, units: str='', contributing_source_ids: Optional[List[str]]=None) -> ProvenancedValue:
    """Wrap a raw number with its registry entry."""
    reg = load_provenance_registry()['sources']
    if source_id not in reg:
        raise ValueError(f'UNREGISTERED_DATA_SOURCE: {source_id!r} is not in provenance.yaml')
    e = reg[source_id]
    return ProvenancedValue(value=value, units=units or e.get('units', ''), source_id=source_id, evidence_class=e['evidence_class'], derivation=e.get('derivation', ''), assumptions=list(e.get('assumptions', []) or []), validity=dict(e.get('validity', {}) or {}), usable_for=list(e.get('usable_for', []) or []), not_usable_for=list(e.get('NOT_usable_for', []) or []), extras={k: e[k] for k in ('negative_screening_min_ratio', 'negative_screening_note', 'known_conflicts', 'supersede_when', 'independent_validation', 'cross_family_ordering') if k in e} | ({'contributing_source_ids': list(contributing_source_ids)} if contributing_source_ids else {}))
_PROVENANCE_GATED_FILES = ('iso18070_chloride_thresholds.csv', 'iso18070_temperature_coefficients.csv', 'astm_g48_cpt_data.csv', 'astm_g82_galvanic_series.csv', 'materials_compositions.csv', 'orr_diffusion_limits.csv')

def assert_source_ids_resolve() -> None:
    """Fail closed if any gated CSV row lacks a registered source_id."""
    import csv as _csv
    problems = []
    for filename in _PROVENANCE_GATED_FILES:
        path = DATA_DIR / filename
        if not path.exists():
            problems.append(f'{filename}: missing')
            continue
        with open(path, newline='', encoding='utf-8') as fh:
            for i, row in enumerate(_csv.DictReader(fh), start=2):
                sid = (row.get('source_id') or '').strip()
                if not sid:
                    problems.append(f'{filename}:{i} has no source_id')
                elif sid not in registered_source_ids():
                    problems.append(f'{filename}:{i} unregistered source_id {sid!r}')
    if problems:
        raise ValueError('UNREGISTERED_DATA_SOURCE: every numeric row must resolve to an entry in data/PROVENANCE.md. Problems: ' + '; '.join(problems[:10]))
assert_source_ids_resolve()
