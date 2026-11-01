"""Water-stability-line-only Pourbaix helpers.

The local reaction table contains E0 numbers but no row-level verifiable source
records. Unsupported numbers accompanied by warning prose are still unsupported,
so those E0 values and every derived reaction line are suppressed at every
accessor. The registered tool retains the independently defined H2/O2 water
stability lines at 25 C. Phase, immunity, corrosion, and passivity selection are
also suppressed because no complete Gibbs-minimization dataset exists.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from math import log
from pathlib import Path
import re
from typing import Any, NoReturn
import numpy as np
import yaml
from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, validate_float_domain_inputs
R_J_MOL_K = 8.314462618
F_C_MOL = 96485.33212
DATA_DIR = Path(__file__).resolve().parents[2] / 'data' / 'pourbaix'
SPECIES_PATH = DATA_DIR / 'species.yaml'
REACTIONS_PATH = DATA_DIR / 'reactions.yaml'
DEFAULT_ACTIVITY_M2PLUS = 1e-06
DEFAULT_ACTIVITY_OHMINUS = 1e-07
TABLE_PH_RANGE = (0.0, 14.0)
TABLE_T_C_RANGE = (0.0, 100.0)
O2_H2O_STANDARD_POTENTIAL_VSHE_25C = 1.2291
MAX_POURBAIX_GRID_POINTS = 200
PERSSON_SOLID_AQUEOUS_METHOD_SOURCE = {'doi': '10.1103/physrevb.85.235438', 'authors': ['Kristin A. Persson', 'Bryn Waldwick', 'Predrag Lazic', 'Gerbrand Ceder'], 'title': 'Prediction of solid-aqueous equilibria: Scheme to combine first-principles calculations of solids with experimental aqueous states', 'section': 'Sections II and III', 'content_support': 'Section II constructs consistent Gibbs reference energies for solids, water, gases and experimental aqueous ions; Section III compares those energies to construct equilibrium solid-aqueous Pourbaix diagrams.', 'content_limit': 'It does not license stable-phase or passivity selection from isolated Nernst boundaries whose reaction energies lack row-level sources.', 'role': 'required solid-aqueous Gibbs-energy phase-selection method'}
PATEL_MANY_ELEMENT_METHOD_SOURCE = {'doi': '10.1039/c9cp04799a', 'authors': ['Anjli M. Patel', 'Jens K. Nørskov', 'Kristin A. Persson', 'Joseph H. Montoya'], 'title': 'Efficient Pourbaix diagrams of many-element compounds', 'section': 'Section 2, Pourbaix Algorithm', 'content_support': 'Section 2 filters candidate species with a convex hull in proton, electron and composition space, retains minimum-free-energy compounds, and obtains stable regions by half-space intersection.', 'content_limit': "The algorithm assumes suitable thermodynamic entries and ideal mixing; it does not validate this server's unsourced reaction-E0 rows.", 'role': 'required many-element stable-phase construction method'}

class UnsupportedElementError(ValueError):
    """Raised when an element has no table-driven Pourbaix data."""

    def __init__(self, element: str, supported: tuple[str, ...]) -> None:
        super().__init__(f"Unsupported Pourbaix element '{element}' is not supported. Use a tabled alternative: {', '.join(supported)}")
        self.element = element
        self.supported = supported

class PourbaixPhaseSelectionSuppressedError(CorrosionPublicError, RuntimeError):
    """Raised whenever a caller requests the withdrawn phase/passivity verdict."""

    def __init__(self, element: str) -> None:
        super().__init__('POURBAIX_PHASE_SELECTION_SUPPRESSED', 'Pourbaix phase/passivity selection is suppressed because this server does not implement Gibbs-energy minimization over a complete thermodynamic dataset. Use the boundary-only diagram output.', details={'element': element, 'phase_selection_available': False, 'boundary_tool': 'corrosion_generate_pourbaix', 'method_sources': [{**PERSSON_SOLID_AQUEOUS_METHOD_SOURCE, 'authors': list(PERSSON_SOLID_AQUEOUS_METHOD_SOURCE['authors'])}, {**PATEL_MANY_ELEMENT_METHOD_SOURCE, 'authors': list(PATEL_MANY_ELEMENT_METHOD_SOURCE['authors'])}]})

class PourbaixGridPointsOutOfRangeError(CorrosionPublicError, ValueError):
    """The requested inclusive pH grid exceeds the public computation cap."""

    def __init__(self, requested_grid_points: int) -> None:
        super().__init__('POURBAIX_GRID_POINTS_OUT_OF_RANGE', 'POURBAIX_GRID_POINTS_OUT_OF_RANGE: grid_points exceeds the documented inclusive-grid computation cap of 200; no diagram is computed or silently clamped.', details={'requested_grid_points': requested_grid_points, 'maximum_grid_points': MAX_POURBAIX_GRID_POINTS, 'result_emitted': False})

class PourbaixGridResolutionUnavailableError(CorrosionPublicError, ValueError):
    """Requested pH samples are not distinct at binary64 resolution."""

    def __init__(self, *, pH_range: tuple[float, float], requested_grid_points: int, unique_abscissae: int) -> None:
        super().__init__('POURBAIX_GRID_RESOLUTION_UNAVAILABLE', 'POURBAIX_GRID_RESOLUTION_UNAVAILABLE: the requested pH span is too narrow for every requested binary64 grid abscissa to be distinct; no duplicate-coordinate diagram is returned.', details={'pH_range': list(pH_range), 'requested_grid_points': requested_grid_points, 'unique_abscissae': unique_abscissae, 'grid_numeric_domain': 'IEEE_754_binary64', 'result_emitted': False})

@dataclass(frozen=True)
class Species:
    """Species properties needed to construct reaction boundaries."""
    species_id: str
    formula: str
    delta_g_f_kj_mol: float
    z: int
    is_solid: bool
    source: str

@dataclass(frozen=True)
class Reaction:
    """One half-cell table row from data/pourbaix/reactions.yaml."""
    reaction_id: str
    element: str
    reactants: tuple[dict[str, Any], ...]
    products: tuple[dict[str, Any], ...]
    n_electrons: int
    E0_VSHE: float
    protons: float
    favored_below: str
    favored_above: str
    validity_pH_range: tuple[float, float]
    validity_T_C_range: tuple[float, float]
    notes: str
    E0_source: dict[str, Any] | None

def _canonical_element(element: str) -> str:
    token = element.strip()
    return token[:1].upper() + token[1:].lower()

def _range(value: list[float] | tuple[float, float], field_name: str) -> tuple[float, float]:
    if len(value) != 2:
        raise ValueError(f'{field_name} must be a two-item range')
    minimum, maximum = (float(value[0]), float(value[1]))
    if minimum > maximum:
        raise ValueError(f'{field_name} minimum exceeds maximum')
    return (minimum, maximum)

def _declared_integer(value: Any, field_name: str) -> int:
    """Accept only YAML integers; never truncate a fractional table value."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{field_name} must be declared as an integer')
    return value

@lru_cache(maxsize=1)
def _load_species_payload() -> dict[str, Any]:
    with SPECIES_PATH.open('r', encoding='utf-8') as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get('elements'), dict):
        raise ValueError('data/pourbaix/species.yaml must contain elements mapping')
    return payload

def _strip_formula_charge(formula: str, charge: int) -> str:
    if charge > 0:
        suffix = f'{charge}+' if charge > 1 else '+'
        if formula.endswith(suffix):
            return formula[:-len(suffix)]
        return formula.rstrip('+')
    if charge < 0:
        return formula.rstrip('-')
    return formula

def _parse_formula(formula: str, charge: int) -> dict[str, float]:
    token = _strip_formula_charge(formula, charge)

    def parse_segment(index: int=0) -> tuple[dict[str, float], int]:
        counts: dict[str, float] = {}
        while index < len(token):
            if token[index] == '(':
                inner, index = parse_segment(index + 1)
                multiplier_match = re.match('\\d+', token[index:])
                multiplier = float(multiplier_match.group(0)) if multiplier_match else 1.0
                if multiplier_match:
                    index += len(multiplier_match.group(0))
                for element, count in inner.items():
                    counts[element] = counts.get(element, 0.0) + count * multiplier
            elif token[index] == ')':
                return (counts, index + 1)
            else:
                atom_match = re.match('[A-Z][a-z]?(\\d*)', token[index:])
                if not atom_match:
                    raise ValueError(f'cannot parse formula {formula!r} at offset {index}')
                atom_token = atom_match.group(0)
                element = re.match('[A-Z][a-z]?', atom_token).group(0)
                count_text = atom_token[len(element):]
                count = float(count_text) if count_text else 1.0
                counts[element] = counts.get(element, 0.0) + count
                index += len(atom_token)
        return (counts, index)
    parsed, _ = parse_segment()
    return parsed

def _species_index(species_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    species_by_id: dict[str, dict[str, Any]] = {}
    for element, entry in species_payload['elements'].items():
        for species_id, raw in entry.get('species', {}).items():
            charge = _declared_integer(raw['z'], f'{species_id}.z')
            species_by_id[species_id] = {'element': element, 'charge': charge, 'atoms': _parse_formula(str(raw['formula']), charge)}
    return species_by_id

def _side_balance(species_by_id: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> tuple[dict[str, float], float]:
    atoms: dict[str, float] = {}
    charge = 0.0
    for item in items:
        species_id = str(item['species_id'])
        species = species_by_id[species_id]
        stoichiometry = float(item.get('stoichiometry', 1.0))
        for element, count in species['atoms'].items():
            atoms[element] = atoms.get(element, 0.0) + count * stoichiometry
        charge += float(species['charge']) * stoichiometry
    return (atoms, charge)

def reaction_balance_error(species_by_id: dict[str, dict[str, Any]], reaction: dict[str, Any]) -> str | None:
    """Return a balance error for one reaction row, or ``None`` if balanced."""
    reaction_id = str(reaction.get('reaction_id', '<unknown>'))
    n_electrons = float(reaction.get('n_electrons', 0.0))
    if n_electrons < 0:
        return f'{reaction_id}: n_electrons must be non-negative'
    lhs_atoms, lhs_charge = _side_balance(species_by_id, reaction.get('reactants', []))
    rhs_atoms, rhs_charge = _side_balance(species_by_id, reaction.get('products', []))
    protons = float(reaction.get('protons', 0.0))
    lhs_atoms['H'] = lhs_atoms.get('H', 0.0) + protons
    lhs_charge += protons - n_electrons
    water_rhs = max(lhs_atoms.get('O', 0.0) - rhs_atoms.get('O', 0.0), 0.0)
    water_lhs = max(rhs_atoms.get('O', 0.0) - lhs_atoms.get('O', 0.0), 0.0)
    if water_rhs:
        rhs_atoms['O'] = rhs_atoms.get('O', 0.0) + water_rhs
        rhs_atoms['H'] = rhs_atoms.get('H', 0.0) + 2.0 * water_rhs
    if water_lhs:
        lhs_atoms['O'] = lhs_atoms.get('O', 0.0) + water_lhs
        lhs_atoms['H'] = lhs_atoms.get('H', 0.0) + 2.0 * water_lhs
    atom_delta = {element: lhs_atoms.get(element, 0.0) - rhs_atoms.get(element, 0.0) for element in sorted(set(lhs_atoms) | set(rhs_atoms)) if abs(lhs_atoms.get(element, 0.0) - rhs_atoms.get(element, 0.0)) > 1e-08}
    charge_delta = lhs_charge - rhs_charge
    if atom_delta or abs(charge_delta) > 1e-08:
        return f'{reaction_id}: atom_delta={atom_delta or {}}, charge_delta={repr(float(charge_delta))}'
    return None

def reaction_balance_errors() -> list[str]:
    """Return all Pourbaix reaction balance errors from the YAML tables."""
    species_payload = _load_species_payload()
    reactions_payload = _load_reactions_payload()
    species_by_id = _species_index(species_payload)
    return [error for reaction in reactions_payload['reactions'] if (error := reaction_balance_error(species_by_id, reaction)) is not None]

def _assert_reactions_balanced(species_payload: dict[str, Any], reactions_payload: dict[str, Any]) -> None:
    species_by_id = _species_index(species_payload)
    errors = [error for reaction in reactions_payload['reactions'] if (error := reaction_balance_error(species_by_id, reaction)) is not None]
    if errors:
        raise RuntimeError('data/pourbaix/reactions.yaml contains unbalanced reactions: ' + '; '.join(errors))

@lru_cache(maxsize=1)
def _load_reactions_payload() -> dict[str, Any]:
    if not REACTIONS_PATH.is_file() or not SPECIES_PATH.is_file():
        raise RuntimeError('POURBAIX_DATA_UNAVAILABLE: cleared reaction/species tables are not bundled in this release')
    with REACTIONS_PATH.open('r', encoding='utf-8') as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get('reactions'), list):
        raise ValueError('data/pourbaix/reactions.yaml must contain reactions list')
    _assert_reactions_balanced(_load_species_payload(), payload)
    return payload

def supported_elements() -> tuple[str, ...]:
    """Return elements with table-backed Pourbaix data."""
    if not REACTIONS_PATH.is_file() or not SPECIES_PATH.is_file():
        return ()
    return tuple(_load_species_payload()['elements'].keys())

def _coerce_species(species_id: str, raw: dict[str, Any]) -> Species:
    return Species(species_id=species_id, formula=str(raw['formula']), delta_g_f_kj_mol=float(raw['delta_g_f_kj_mol']), z=_declared_integer(raw['z'], f'{species_id}.z'), is_solid=bool(raw['is_solid']), source=str(raw['source']))

def _coerce_reaction(raw: dict[str, Any]) -> Reaction:
    return Reaction(reaction_id=str(raw['reaction_id']), element=str(raw['element']), reactants=tuple(raw.get('reactants', [])), products=tuple(raw.get('products', [])), n_electrons=_declared_integer(raw['n_electrons'], f'{raw['reaction_id']}.n_electrons'), E0_VSHE=float(raw['E0_VSHE']), protons=float(raw.get('protons', 0.0)), favored_below=str(raw['favored_below']), favored_above=str(raw['favored_above']), validity_pH_range=_range(raw['validity_pH_range'], f'{raw['reaction_id']}.pH'), validity_T_C_range=_range(raw['validity_T_C_range'], f'{raw['reaction_id']}.T_C'), notes=str(raw.get('notes', '')), E0_source=dict(raw['E0_source']) if raw.get('E0_source') else None)

def _load_pourbaix_data(element: str) -> dict[str, Any]:
    """Load boundary-construction data for one element."""
    if not REACTIONS_PATH.is_file() or not SPECIES_PATH.is_file():
        raise RuntimeError('POURBAIX_DATA_UNAVAILABLE: cleared reaction/species tables are not bundled in this release')
    canonical = _canonical_element(element)
    payload = _load_species_payload()
    elements = payload['elements']
    if canonical not in elements:
        raise UnsupportedElementError(element, supported_elements())
    raw_species = elements[canonical].get('species', {})
    species = {species_id: _coerce_species(species_id, raw) for species_id, raw in raw_species.items()}
    reactions = [_coerce_reaction(raw) for raw in _load_reactions_payload()['reactions'] if raw.get('element') == canonical]
    for reaction in reactions:
        for species_id in (reaction.favored_below, reaction.favored_above):
            if species_id not in species:
                raise ValueError(f'{reaction.reaction_id} references unknown species {species_id}')
    return {'element': canonical, 'species': species, 'reactions': reactions}

def _distance_outside(value: float, bounds: tuple[float, float]) -> float:
    if value < bounds[0]:
        return bounds[0] - value
    if value > bounds[1]:
        return value - bounds[1]
    return 0.0

def _reaction_boundary_E(reaction: Reaction, *, species: dict[str, Species], pH: float, T_C: float, activity_M2plus: float) -> float:
    """Calculate a Nernst-shifted equilibrium line for a table reaction."""
    T_K = T_C + 273.15
    n = reaction.n_electrons
    if n <= 0:
        raise ValueError(f'{reaction.reaction_id} is not an E-pH redox boundary')
    pH_term = 2.303 * R_J_MOL_K * T_K / F_C_MOL * (reaction.protons / n) * pH
    dissolved_activity_exponent = _dissolved_activity_exponent(reaction, species)
    activity = max(activity_M2plus, 1e-30)
    aqueous_term = R_J_MOL_K * T_K / (n * F_C_MOL) * dissolved_activity_exponent * log(activity)
    return reaction.E0_VSHE - pH_term + aqueous_term

def _dissolved_activity_exponent(reaction: Reaction, species: dict[str, Species]) -> float:
    """Return the signed aqueous stoichiometry for the Nernst activity term."""
    reactant_exponent = sum((float(item.get('stoichiometry', 1.0)) for item in reaction.reactants if not species[str(item['species_id'])].is_solid))
    product_exponent = sum((float(item.get('stoichiometry', 1.0)) for item in reaction.products if not species[str(item['species_id'])].is_solid))
    return reactant_exponent - product_exponent

def compute_stable_phase(element: str, pH: float, E_VSHE: float, T_C: float=25.0, activity_M2plus: float=DEFAULT_ACTIVITY_M2PLUS, activity_OHminus: float=DEFAULT_ACTIVITY_OHMINUS) -> NoReturn:
    """Fail closed: phase/passivity selection is not implemented defensibly."""
    canonical = _load_pourbaix_data(element)['element']
    raise PourbaixPhaseSelectionSuppressedError(canonical)

def _water_lines(pH_grid: np.ndarray, temperature_C: float) -> dict[str, list[tuple[float, float]]]:
    T_K = temperature_C + 273.15
    pH_factor = -(R_J_MOL_K * T_K / F_C_MOL) * 2.303
    return {'H2_evolution': [(float(pH), float(pH_factor * pH)) for pH in pH_grid], 'O2_evolution': [(float(pH), float(O2_H2O_STANDARD_POTENTIAL_VSHE_25C + pH_factor * pH)) for pH in pH_grid]}

def _has_verified_row_level_e0_source(reaction: Reaction) -> bool:
    """Whether a row carries the complete source contract needed to emit E0."""
    source = reaction.E0_source
    return bool(source and source.get('verification_status') == 'verified_title_author_and_value' and source.get('title') and source.get('authors') and source.get('locator') and source.get('value_locator'))

def generate_pourbaix_diagram(element: str, pH_range: tuple[float, float]=(0.0, 14.0), grid_points: int=50) -> dict[str, Any]:
    """Return sourced 25 C water lines and an E0-row suppression ledger."""
    validate_float_domain_inputs({'pH_range': pH_range, 'grid_points': grid_points}, minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE)
    if pH_range[0] >= pH_range[1]:
        raise ValueError(f'Invalid pH range: {pH_range}')
    if grid_points < 2:
        raise ValueError('grid_points must be at least 2')
    if grid_points > MAX_POURBAIX_GRID_POINTS:
        raise PourbaixGridPointsOutOfRangeError(grid_points)
    pH_grid = np.linspace(pH_range[0], pH_range[1], grid_points)
    unique_abscissae = len({float(value) for value in pH_grid})
    if unique_abscissae != grid_points:
        raise PourbaixGridResolutionUnavailableError(pH_range=pH_range, requested_grid_points=grid_points, unique_abscissae=unique_abscissae)
    data = _load_pourbaix_data(element)
    temperature_C = 25.0
    soluble_concentration_M = DEFAULT_ACTIVITY_M2PLUS
    warnings: list[str] = []
    if pH_range[0] < TABLE_PH_RANGE[0] or pH_range[1] > TABLE_PH_RANGE[1]:
        warnings.append('WATER_LINES_OUTSIDE_ADVERTISED_PH_RANGE')
    boundaries: list[dict[str, Any]] = []
    suppressed_nonredox_reaction_ids: list[str] = []
    suppressed_reaction_rows: list[dict[str, str]] = []
    for reaction in data['reactions']:
        if not _has_verified_row_level_e0_source(reaction):
            suppressed_reaction_rows.append({'reaction_id': reaction.reaction_id, 'reason_code': 'E0_ROW_SOURCE_UNVERIFIED', 'source_status': 'absent' if reaction.E0_source is None else 'incomplete_or_unverified'})
            if reaction.n_electrons <= 0:
                suppressed_nonredox_reaction_ids.append(reaction.reaction_id)
            continue
        if reaction.n_electrons <= 0:
            suppressed_nonredox_reaction_ids.append(reaction.reaction_id)
            continue
        points = [(float(pH), float(_reaction_boundary_E(reaction, species=data['species'], pH=float(pH), T_C=temperature_C, activity_M2plus=soluble_concentration_M))) for pH in pH_grid]
        boundaries.append({'reaction_id': reaction.reaction_id, 'reaction_pair': [reaction.favored_below, reaction.favored_above], 'notes': reaction.notes, 'E0_VSHE': reaction.E0_VSHE, 'n_electrons': reaction.n_electrons, 'points': points, 'basis': {'method': 'Nernst line from tabulated E0 and stoichiometry', 'source': reaction.E0_source, 'source_standing': 'ROW_LEVEL_VERIFIED', 'validation_standing': 'boundary_only_no_phase_or_passivity_verdict', 'validity_pH_range': reaction.validity_pH_range, 'validity_T_C_range': reaction.validity_T_C_range, 'dissolved_metal_activity_M': soluble_concentration_M, 'dissolved_activity_applied': _dissolved_activity_exponent(reaction, data['species']) != 0.0, 'dissolved_activity_exponent': _dissolved_activity_exponent(reaction, data['species']), 'activity_coefficient': 1.0}})
    return {'element': data['element'], 'temperature_C': temperature_C, 'pH_range': pH_range, 'boundaries': boundaries, 'water_lines': _water_lines(pH_grid, temperature_C), 'grid_points': grid_points, 'phase_selection_status': 'suppressed_no_gibbs_minimization_and_unverified_reaction_e0_rows', 'suppressed_reaction_rows': suppressed_reaction_rows, 'suppressed_nonredox_reaction_ids': suppressed_nonredox_reaction_ids, 'method_sources': [{**PERSSON_SOLID_AQUEOUS_METHOD_SOURCE, 'authors': list(PERSSON_SOLID_AQUEOUS_METHOD_SOURCE['authors'])}, {**PATEL_MANY_ELEMENT_METHOD_SOURCE, 'authors': list(PATEL_MANY_ELEMENT_METHOD_SOURCE['authors'])}], 'water_line_basis': {'method': 'Nernst equation for H2/H+ and O2/H2O', 'potential_reference': 'SHE', 'temperature_C': 25.0, 'standard_potential_Hplus_H2_VSHE': 0.0, 'standard_potential_O2_H2O_VSHE': O2_H2O_STANDARD_POTENTIAL_VSHE_25C, 'sources': [{'doi': '10.1351/goldbook.S05917', 'openalex_id': 'W4255507444', 'title': 'standard hydrogen electrode', 'authors': [], 'publisher_organization': 'International Union of Pure and Applied Chemistry', 'section': 'Gold Book entry S05917, definition', 'value_locator': 'definition: H+/H2 standard potential is zero', 'content_support': 'The IUPAC Gold Book definition assigns the standard hydrogen electrode a standard potential of zero at all temperatures, providing the reference for the H+/H2 line.', 'content_limit': "The definition fixes the reference electrode; it does not supply this tool's reaction-table energies, phase selection, activities, or corrosion/passivity verdicts.", 'verification_status': 'official_iupac_definition_openalex_title_no_authorships'}, {'doi': '10.1063/1.555839', 'openalex_id': 'W2092766164', 'title': 'Standard Electrode Potentials and Temperature Coefficients in Water at 298.15 K', 'authors': ['Steven G. Bratsch'], 'section': 'Equation 15 and Table 1', 'value_locator': 'Equation 15 and Table 1: E°298 = 1.2291 V', 'content_support': 'Equation 15 and Table 1 give 1.2291 V at 298.15 K for the O2/H2O standard electrode reaction, which anchors the upper water-stability line.', 'content_limit': "The tabulated standard potential does not validate the server's unsourced reaction rows, activity assumptions, stable-phase selection, or service verdicts.", 'verification_status': 'verified_title_author_and_value'}], 'conditions': 'unit H2/O2 fugacity, unit water activity, ideal proton activity'}, 'warnings': [*warnings, 'UNSOURCED_REACTION_E0_AND_BOUNDARIES_SUPPRESSED']}
__all__ = ['MAX_POURBAIX_GRID_POINTS', 'PourbaixGridPointsOutOfRangeError', 'PourbaixGridResolutionUnavailableError', 'UnsupportedElementError', 'PourbaixPhaseSelectionSuppressedError', 'compute_stable_phase', 'generate_pourbaix_diagram', 'reaction_balance_error', 'reaction_balance_errors', 'supported_elements']
