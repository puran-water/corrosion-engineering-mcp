"""
PHREEQC chemistry backend for corrosion engineering calculations.

This module provides a thread-safe wrapper around phreeqpython for:
- Aqueous speciation at a caller-reported pH
- Scaling tendency prediction (LSI, RSI, SDSI)
- Corrosive gas dissolution (CO₂, H₂S, O₂)
- Electrochemical species tracking (Fe²⁺, Fe³⁺, Cl⁻, SO₄²⁻)

Design:
    - Singleton pattern with thread-local PHREEQC instances
    - Charge balance validation and correction
    - Unit conversion helpers (mg/L ↔ mol/L ↔ meq/L)
    - Cross-validation with degasser-design-mcp water chemistry

Thread Safety:
    Per Codex guidance, each thread gets its own PHREEQC instance to avoid
    race conditions in the C++ backend. Use threading.local() to isolate.

Usage:
    >>> backend = PHREEQCBackend()
    >>> result = backend.run_speciation(
    ...     ions={"Na+": 1000.0, "Cl-": 1500.0, "Ca2+": 100.0, "HCO3-": 200.0},
    ...     temperature_C=25.0,
    ...     pH=7.5
    ... )
    >>> print(result.pH)
    7.5
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
from dataclasses import dataclass
from numbers import Real
from typing import Dict, Optional, Tuple, Any
from pathlib import Path

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, UnderflowBelowFloatDomainError, require_representable_nonzero_result, validate_float_domain_inputs
from corrosion_engineering_mcp.core.phreeqc_runtime import PHREEQCBackendUnavailableError, create_phreeqpython, phreeqc_database_record, resolve_phreeqc_database

logger = logging.getLogger(__name__)

_VIPHREEQC_LOCK = threading.Lock()


def _is_phreeqc_nonconvergence(exception: BaseException) -> bool:
    """Identify VIPhreeqc's unusable-solution diagnostics conservatively.

    VIPhreeqc reports magnitude-driven solution failure as an input error rather
    than using the word ``converge``.  Both shapes mean that no aqueous state was
    computed and therefore share the typed fail-closed public contract.
    """

    diagnostic = str(exception).casefold()
    return any(
        marker in diagnostic
        for marker in (
            "failed to converge",
            "has not converged",
            "did not converge",
            "solute mass exceeds solution mass",
            "mass of water is negative",
        )
    )


class WaterChemistrySpeciationNonconvergenceError(
    CorrosionPublicError, RuntimeError
):
    """PHREEQC could not converge for a validated reported-water input region."""

    def __init__(
        self,
        *,
        ions: Dict[str, float],
        temperature_C: float,
        pH: float,
        backend_exception: BaseException,
    ) -> None:
        region_tags = []
        if pH > 12.0:
            region_tags.append("high_pH_above_12")
        elif pH < 2.0:
            region_tags.append("low_pH_below_2")
        else:
            region_tags.append("reported_pH_water_chemistry")
        super().__init__(
            "WATER_CHEMISTRY_SPECIATION_NONCONVERGENCE",
            "WATER_CHEMISTRY_SPECIATION_NONCONVERGENCE: PHREEQC did not "
            "converge for the reported pH, temperature, and ion panel; no "
            "speciation, saturation index, or scaling value is returned.",
            details={
                "reason_code": "WATER_CHEMISTRY_SPECIATION_NONCONVERGENCE",
                "status": "out_of_domain_nonconvergence",
                "calculation_stage": "initial_aqueous_speciation",
                "failing_input_region": {
                    "pH": pH,
                    "temperature_C": temperature_C,
                    "reported_ions_mg_L": dict(ions),
                    "region_tags": region_tags,
                },
                "backend": "PHREEQC",
                "backend_exception_type": type(backend_exception).__name__,
                "result_emitted": False,
            },
        )


class PHREEQCSaturationIndexSentinelError(CorrosionPublicError, RuntimeError):
    """PHREEQC returned its finite missing/non-converged SI sentinel family."""

    def __init__(self, mineral: str, sentinel: float) -> None:
        super().__init__(
            "WATER_CHEMISTRY_SPECIATION_NONCONVERGENCE",
            "WATER_CHEMISTRY_SPECIATION_NONCONVERGENCE: PHREEQC returned its "
            "minus-999 undefined/non-converged saturation-index sentinel; no "
            "saturation index or scaling value is returned.",
            details={
                "reason_code": "WATER_CHEMISTRY_SPECIATION_NONCONVERGENCE",
                "status": "out_of_domain_nonconvergence",
                "calculation_stage": "saturation_index_extraction",
                "mineral": mineral,
                "sentinel_value": sentinel,
                # VIPhreeqc/PHREEQC wrappers use -999 and nearby values in this
                # family for undefined log quantities.  Treat the whole <=-900
                # family as unavailable rather than laundering it as chemistry.
                "sentinel_family": "PHREEQC_MINUS_999",
                "backend": "PHREEQC",
                "result_emitted": False,
            },
        )

# ---------------------------------------------------------------------------
# Unit conversion constants
# ---------------------------------------------------------------------------

# Faraday constant (C/mol) for charge calculations
FARADAY = 96485.3321  # C/mol

# Gas constant (J/mol·K) for thermodynamic calculations
R_GAS = 8.314462618  # J/mol·K

# Standard temperature (K)
T_STD = 298.15  # 25°C


# ---------------------------------------------------------------------------
# Ion definitions (aligned with degasser-design-mcp)
# ---------------------------------------------------------------------------

VALID_IONS: Dict[str, Dict[str, float]] = {
    # Cations
    "Na+": {"charge": 1, "mw": 22.99, "name": "Sodium"},
    "Ca2+": {"charge": 2, "mw": 40.08, "name": "Calcium"},
    "Mg2+": {"charge": 2, "mw": 24.31, "name": "Magnesium"},
    "K+": {"charge": 1, "mw": 39.10, "name": "Potassium"},
    "Fe2+": {"charge": 2, "mw": 55.85, "name": "Iron(II)"},
    "Fe3+": {"charge": 3, "mw": 55.85, "name": "Iron(III)"},
    "Mn2+": {"charge": 2, "mw": 54.94, "name": "Manganese"},
    "Ba2+": {"charge": 2, "mw": 137.33, "name": "Barium"},
    "Sr2+": {"charge": 2, "mw": 87.62, "name": "Strontium"},
    "NH4+": {"charge": 1, "mw": 18.04, "name": "Ammonium"},
    # Anions
    "Cl-": {"charge": -1, "mw": 35.45, "name": "Chloride"},
    "SO4-2": {"charge": -2, "mw": 96.06, "name": "Sulfate"},
    "HCO3-": {"charge": -1, "mw": 61.02, "name": "Bicarbonate"},
    "CO3-2": {"charge": -2, "mw": 60.01, "name": "Carbonate"},
    "NO3-": {"charge": -1, "mw": 62.00, "name": "Nitrate"},
    "F-": {"charge": -1, "mw": 19.00, "name": "Fluoride"},
    "PO4-3": {"charge": -3, "mw": 94.97, "name": "Phosphate"},
    "SiO3-2": {"charge": -2, "mw": 76.08, "name": "Silicate"},
    "Br-": {"charge": -1, "mw": 79.90, "name": "Bromide"},
    "B(OH)4-": {"charge": -1, "mw": 78.84, "name": "Borate"},
}


#: The panel had accepted both while the PHREEQC writer did not map them: they were written raw and PHREEQC ignored
#: them without a word (1 mmol/L of hydroxide changed nothing), while `calculate_charge_balance` counted them, so a
#: panel's stated hydroxide was dropped from the chemistry and kept in its charge balance (closure c13 OQ-C13-4).
PH_SET_ION_RULING_ID = 'PUBLIC-RULE-861b5d0d8643'
#: Every spelling of the two a panel may use (compared case-insensitively): the keys the panel accepted, their PHREEQC
#: charge spellings, hydronium, and the names.
PH_SET_ION_SPELLINGS = frozenset({"h+", "h", "h+1", "h1+", "h3o+", "hydronium", "hydrogen", "oh-", "oh", "oh-1", "oh1-",
                                  "hydroxide"})


def _ph_set_ion_refusal(ion: str) -> str:
    return (f"Ion {ion!r} is refused: H+ and OH- are set by the pH, never by the ion panel; state the water's pH (the "
            "pH argument) instead. PHREEQC ignores an H+ or OH- entry at a stated pH, so the panel carries nothing it "
            f"could have said, while the charge balance would count it (OQ-C13-4 of {PH_SET_ION_RULING_ID}).")

# This is a floating-point execution-domain bound, not a water-quality or
# corrosion decision edge.  Charge-balance arithmetic can sum every supported
# ion after multiplying by abs(charge)/molecular_weight.  Reserving a factor of
# four below DBL_MAX keeps each sign sum, their total and their difference
# finite even when every accepted ion is at the declared maximum.
_MAX_MEQ_PER_MG_L_FACTOR = max(
    abs(properties["charge"]) / properties["mw"]
    for properties in VALID_IONS.values()
)
WATER_ION_MAX_MG_L = (
    sys.float_info.max
    / (4.0 * len(VALID_IONS) * _MAX_MEQ_PER_MG_L_FACTOR)
)
WATER_ION_MAGNITUDE_SCREENING_RULE = {
    "rule_id": "INTERNAL_SCREENING_RULE_WATER_ION_MAGNITUDE_V1",
    "minimum_mg_L": 0.0,
    "maximum_mg_L": WATER_ION_MAX_MG_L,
    "rationale": (
        "Floating-point execution-domain bound derived from DBL_MAX, the "
        "supported-ion count, and the largest charge/molecular-weight factor; "
        "it is not an engineering suitability threshold."
    ),
}


class WaterChemistryInputMagnitudeOutOfDomainError(
    CorrosionPublicError, ValueError
):
    """A finite ion panel cannot be evaluated with finite backend arithmetic."""

    def __init__(self, offending_ions_mg_L: Dict[str, float]) -> None:
        super().__init__(
            "WATER_CHEMISTRY_INPUT_MAGNITUDE_OUT_OF_DOMAIN",
            "WATER_CHEMISTRY_INPUT_MAGNITUDE_OUT_OF_DOMAIN: one or more finite "
            "reported ion magnitudes exceed the declared floating-point "
            "execution domain; no chemistry result is returned.",
            details={
                "offending_ions_mg_L": dict(offending_ions_mg_L),
                "screening_rule": dict(WATER_ION_MAGNITUDE_SCREENING_RULE),
                "result_emitted": False,
            },
        )


#: writes". Each ion's mg/L is written under its PHREEQC name AS ITS OWN COMPOUND, ``<mg/L> as <formula>``, so the
#: database reads it against that formula's weight in its own element weights, whatever basis the database gives the bare
#: name. The map had divided by (compound / element) molar masses and written the ELEMENT mass: phreeqc.dat reads S(6)
#: as SO4, C(4) as HCO3 and Si as SiO2, so 1 mmol of sulfate, carbonate and silicate was read as 0.334, 0.197 and 0.468
#: mmol (enc-e/fork-cde.md, OQ-CDE2); HCO3- went to CaCO3 equivalents at 61.02/50.0 (phreeqc.dat's equivalent is 50.04).
CDE2_RULING_ID = 'PUBLIC-RULE-1ddf4c4cc59b'

# Map ion names to PHREEQC names and the formula each mass is read as
# Format: (PHREEQC name, the formula its mg/L is read as)
ION_TO_PHREEQC: Dict[str, Tuple[str, str]] = {
    # Cations
    "Na+": ("Na", "Na"),
    "Ca2+": ("Ca", "Ca"),
    "Mg2+": ("Mg", "Mg"),
    "K+": ("K", "K"),
    "Fe2+": ("Fe(2)", "Fe"),
    "Fe3+": ("Fe(3)", "Fe"),
    "Mn2+": ("Mn", "Mn"),
    "Ba2+": ("Ba", "Ba"),
    "Sr2+": ("Sr", "Sr"),
    "NH4+": ("N(-3)", "NH4"),
    # Anions
    "Cl-": ("Cl", "Cl"),
    "SO4-2": ("S(6)", "SO4"),
    "HCO3-": ("Alkalinity", "HCO3"),  # one equivalent per mole: the alkalinity of the bicarbonate
    "CO3-2": ("C(4)", "CO3"),
    "NO3-": ("N(5)", "NO3"),
    "F-": ("F", "F"),
    "PO4-3": ("P", "PO4"),
    "Br-": ("Br", "Br"),
    "B(OH)4-": ("B", "B(OH)4"),
    "SiO3-2": ("Si", "SiO3"),
}


#: The minerals every speciation screens (their saturation indices are reported where the database defines them)
SCREENED_MINERALS: Tuple[str, ...] = ("Calcite", "Aragonite", "Dolomite", "Gypsum", "Halite", "Siderite")


def validate_ion_concentrations(ion_dict: Dict[str, float]) -> Dict[str, float]:
    """Return a finite, non-negative, canonical ion panel.

    JSON embedded in a string is outside Pydantic's normal field traversal, so
    every concentration is validated here before charge-balance arithmetic or
    PHREEQC input construction. Unknown ions are rejected because silently
    omitting them can make an invalid analysis appear charge-balanced.
    """

    if not isinstance(ion_dict, dict) or not ion_dict:
        raise ValueError("Ion panel must be a non-empty JSON object")

    validated: Dict[str, float] = {}
    for ion, raw_concentration in ion_dict.items():
        if isinstance(ion, str) and ion.strip().lower() in PH_SET_ION_SPELLINGS:
            raise ValueError(_ph_set_ion_refusal(ion))
        if ion not in VALID_IONS:
            raise ValueError(f"Unsupported ion identifier: {ion!r}")
        if isinstance(raw_concentration, bool) or not isinstance(raw_concentration, Real):
            raise ValueError(f"Concentration for {ion} must be a number in mg/L")
        concentration = float(raw_concentration)
        if not math.isfinite(concentration):
            raise ValueError(f"Concentration for {ion} must be finite")
        if concentration < 0.0:
            raise ValueError(f"Concentration for {ion} must be non-negative")
        validated[ion] = concentration
    offending = {
        ion: concentration
        for ion, concentration in validated.items()
        if concentration > WATER_ION_MAX_MG_L
    }
    if offending:
        raise WaterChemistryInputMagnitudeOutOfDomainError(offending)
    validate_float_domain_inputs(validated)
    return validated


def parse_ions_json(ions_json: str) -> Dict[str, float]:
    """Parse a strict JSON ion panel and reject non-standard NaN/Infinity."""

    def reject_nonfinite_constant(token: str) -> None:
        raise ValueError(f"Non-finite JSON numeric token is not permitted: {token}")

    try:
        parsed = json.loads(ions_json, parse_constant=reject_nonfinite_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for ions: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("ions_json must be a JSON object (dictionary)")
    return validate_ion_concentrations(parsed)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SpeciationResult:
    'Result of PHREEQC aqueous speciation calculation.'
    pH: float
    pe: float
    temperature_C: float
    ionic_strength_M: float
    alkalinity_mg_L_CaCO3: float
    species: Dict[str, float]
    saturation_indices: Dict[str, float]
    charge_balance_percent: float
    raw_solution: Optional[Any] = None  # Disabled by default to prevent memory leaks
    # The database file that answered (name, whose file, sha256), recorded by the run (D5 of
    
    database: Optional[Dict[str, str]] = None
    # Screened minerals the database does not define: named, never silently dropped
    saturation_indices_not_in_database: Tuple[str, ...] = ()
    calcite_root_evidence: Optional[Dict[str, Any]] = None


@dataclass
class ScalingResult:
    """
    Scaling tendency prediction result.

    Attributes:
        lsi: Langelier Saturation Index (pH - pH_s)
        rsi: Ryznar Stability Index (2 × pH_s - pH)
        puckorius_index: Puckorius Scaling Index, or None while its primary
            formula remains unavailable to this implementation
        larson_ratio: Cl⁻ + SO₄²⁻ to HCO₃⁻ ratio; a relative
            corrosivity indicator for iron and steel only
        interpretation: Text interpretation of CaCO₃ saturation/deposition
            tendency and Larson--Skold availability. It is not an absolute
            metal-loss verdict.
        calcite_no_root_sign_classification: Sign classification carried from
            the converged fixed-alkalinity scan when no SI(Calcite)=0 root is
            available. None when a root was resolved.
    """
    lsi: Optional[float]
    rsi: Optional[float]
    puckorius_index: Optional[float]
    larson_ratio: Optional[float]
    interpretation: str
    calcite_no_root_sign_classification: Optional[str] = None
    calcite_root_evidence: Optional[Dict[str, Any]] = None


class CalciteSaturationPHUnavailableError(RuntimeError):
    """The submitted fixed-alkalinity water has no converged SI=0 pH root."""

    def __init__(
        self,
        maximum_si: float,
        minimum_si: Optional[float] = None,
    ) -> None:
        if minimum_si is None:
            minimum_si = maximum_si
        if minimum_si > maximum_si:
            raise ValueError("minimum_si cannot exceed maximum_si")

        self.root_evidence: Optional[Dict[str, Any]] = None
        self.minimum_si = minimum_si
        self.maximum_si = maximum_si
        if minimum_si > 0.0:
            self.converged_si_sign_classification = "all_positive"
        elif maximum_si < 0.0:
            self.converged_si_sign_classification = "all_negative"
        else:
            self.converged_si_sign_classification = "mixed_or_indeterminate"
        super().__init__(
            "Calcite saturation pH is undefined for this fixed-alkalinity "
            "analysis; converged SI(Calcite) range was "
            f"{minimum_si:.6g} to {maximum_si:.6g} "
            f"({self.converged_si_sign_classification})"
        )


def _require_saturation_index_value(mineral: str, raw_value: Any) -> float:
    """Validate one extracted PHREEQC SI across every backend route."""

    value = float(raw_value)
    if not math.isfinite(value):
        raise RuntimeError(
            f"PHREEQC returned a non-finite {mineral} saturation index"
        )
    if value <= -900.0:
        raise PHREEQCSaturationIndexSentinelError(mineral, value)
    return value


def require_saturation_index(
    speciation: SpeciationResult,
    mineral: str,
) -> float:
    """Return one finite PHREEQC saturation index or fail closed."""

    if mineral not in speciation.saturation_indices:
        raise RuntimeError(
            f"PHREEQC did not return the required {mineral} saturation index"
        )
    return _require_saturation_index_value(
        mineral, speciation.saturation_indices[mineral]
    )


# ---------------------------------------------------------------------------
# Unit conversion helpers (per Codex guidance)
# ---------------------------------------------------------------------------

def mg_L_to_mol_L(concentration_mg_L: float, molecular_weight: float) -> float:
    """
    Convert concentration from mg/L to mol/L.

    Args:
        concentration_mg_L: Concentration in mg/L
        molecular_weight: Molecular weight in g/mol

    Returns:
        Concentration in mol/L
    """
    validate_float_domain_inputs(
        {
            "concentration_mg_L": concentration_mg_L,
            "molecular_weight": molecular_weight,
        }
    )
    result = concentration_mg_L / molecular_weight / 1000.0
    return require_representable_nonzero_result(
        "mg_L_to_mol_L",
        result,
        operands={
            "concentration_mg_L": concentration_mg_L,
            "molecular_weight": molecular_weight,
            "mg_per_g": 1000.0,
        },
    )


def mol_L_to_mg_L(concentration_mol_L: float, molecular_weight: float) -> float:
    """
    Convert concentration from mol/L to mg/L.

    Args:
        concentration_mol_L: Concentration in mol/L
        molecular_weight: Molecular weight in g/mol

    Returns:
        Concentration in mg/L
    """
    validate_float_domain_inputs(
        {
            "concentration_mol_L": concentration_mol_L,
            "molecular_weight": molecular_weight,
        }
    )
    result = concentration_mol_L * molecular_weight * 1000.0
    return require_representable_nonzero_result(
        "mol_L_to_mg_L",
        result,
        operands={
            "concentration_mol_L": concentration_mol_L,
            "molecular_weight": molecular_weight,
            "mg_per_g": 1000.0,
        },
    )


def mg_L_to_meq_L(concentration_mg_L: float, molecular_weight: float, charge: int) -> float:
    """
    Convert concentration from mg/L to meq/L (milliequivalents per liter).

    Args:
        concentration_mg_L: Concentration in mg/L
        molecular_weight: Molecular weight in g/mol
        charge: Absolute charge of the ion

    Returns:
        Concentration in meq/L
    """
    validate_float_domain_inputs(
        {
            "concentration_mg_L": concentration_mg_L,
            "molecular_weight": molecular_weight,
            "charge": charge,
        }
    )
    try:
        mol_L = mg_L_to_mol_L(concentration_mg_L, molecular_weight)
    except UnderflowBelowFloatDomainError:
        # With today's 1e-12 input floor this branch is unreachable.  If that
        # contract ever reopens, avoid the cancelling /1000 then *1000 path:
        # mg/L divided by g/mol is already mmol/L, hence meq/L after |charge|.
        result = concentration_mg_L * abs(charge) / molecular_weight
    else:
        # Preserve the established operation order bit-for-bit in-domain.
        result = mol_L * abs(charge) * 1000.0
    return require_representable_nonzero_result(
        "mg_L_to_meq_L",
        result,
        operands={
            "concentration_mg_L": concentration_mg_L,
            "molecular_weight": molecular_weight,
            "absolute_charge": abs(charge),
        },
    )


def calculate_charge_balance(ion_dict: Dict[str, float]) -> float:
    """
    Calculate charge balance error (%) for water chemistry.

    Positive values indicate excess cations, negative values indicate
    excess anions.

    Args:
        ion_dict: Dictionary of ion concentrations (mg/L)

    Returns:
        Charge balance error as percentage
    """
    ion_dict = validate_ion_concentrations(ion_dict)
    cation_meq = 0.0
    anion_meq = 0.0
    cation_sources: Dict[str, float] = {}
    anion_sources: Dict[str, float] = {}

    for ion, conc_mg_L in ion_dict.items():
        props = VALID_IONS[ion]

        meq_L = mg_L_to_meq_L(conc_mg_L, props["mw"], props["charge"])
        if props["charge"] > 0:
            cation_meq += meq_L
            if conc_mg_L != 0.0:
                cation_sources[ion] = conc_mg_L
        else:
            anion_meq += meq_L
            if conc_mg_L != 0.0:
                anion_sources[ion] = conc_mg_L

    if cation_sources:
        require_representable_nonzero_result(
            "sum_cation_charge_equivalents",
            cation_meq,
            operands=cation_sources,
        )
    if anion_sources:
        require_representable_nonzero_result(
            "sum_anion_charge_equivalents",
            anion_meq,
            operands=anion_sources,
        )

    total_meq = cation_meq + anion_meq
    if total_meq == 0:
        return 0.0

    return (cation_meq - anion_meq) / total_meq * 100.0


def _carbonate_alkalinity_mg_caco3(snapshot: Dict[str, Any]) -> float:
    """Alkalinity, mg/L as CaCO3, from PHREEQC's species: [HCO3-] + 2[CO3-2] + [OH-] - [H+], every bicarbonate and
    carbonate ion pair counted with its ion (CaHCO3+ as one HCO3-, CaCO3 and NaCO3- as one CO3-2), in eq per kg of
    water, then per litre of solution on the solution's own kg-water per litre (CLAUDE.md: PHREEQC's amounts are per kg
    WATER). It had summed element totals, whose H is the water's own hydrogen (55.5 mol): -2.8e6 mg/L as CaCO3 for a
    200 mg/L water, and 0.0 on any error. The non-carbonate alkalinity of hydroxo complexes (MgOH+, CaOH+) is not in
    this sum: 0.3 % of a 200 mg/L carbonate water at pH 7.6 (enc-e/runs/fork_cde_probe_corrosion_basis.txt)."""
    molalities = snapshot["molalities"]
    bicarbonate = sum(value for name, value in molalities.items() if "HCO3" in name)
    # dissolved CO2 carries no alkalinity, whichever name a database gives it (CO2, or H2CO3 where a database writes it)
    carbonate = sum(value for name, value in molalities.items()
                    if "CO3" in name and "HCO3" not in name and not name.startswith("H2CO3"))
    eq_per_kgw = bicarbonate + 2.0 * carbonate + molalities.get("OH-", 0.0) - molalities.get("H+", 0.0)
    kgw_per_l = float(snapshot["kgw_per_l"])
    if not (math.isfinite(eq_per_kgw) and math.isfinite(kgw_per_l) and kgw_per_l > 0):
        raise RuntimeError(f"PHREEQC species give no finite alkalinity (eq/kgw {eq_per_kgw}, kgw/L {kgw_per_l})")
    return eq_per_kgw * kgw_per_l * 50043.5


# ---------------------------------------------------------------------------
# PHREEQC backend (thread-safe singleton)
# ---------------------------------------------------------------------------

#: C16B-3 of the owner's ruling: corrosion's species are their moles over the solution's water mass (molality)
SPECIES_MOLALITY_RULING_ID = 'PUBLIC-RULE-5164c01473a4'


def species_molalities_per_kg_water(solution: Any, species_moles: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Each species' molality, mol per kg of WATER: its moles in the solution (phreeqpython's ``Solution.species``,
    VIPhreeqc ``get_species_moles``, or ``species_moles`` already read from it) over the solution's kg of water
    (``Solution.mass``, the mass of water: 0.5 for ``-water 0.5``, 0.82 after a batch took 18 % of it). Water itself is
    left out. Refused by name when the solution reports no water."""
    water = float(solution.mass)
    if not (math.isfinite(water) and water > 0):
        raise ValueError(f"the solution reports {water!r} kg of water: its species moles cannot be put per kg of water "
                         f"({SPECIES_MOLALITY_RULING_ID}, C16B-3)")
    moles = dict(solution.species) if species_moles is None else dict(species_moles)
    return {name: float(value) / water for name, value in moles.items() if name != "H2O"}


class PHREEQCBackend:
    """
    Thread-safe PHREEQC chemistry backend.

    Uses threading.local() to give each thread its own Python PHREEQC wrapper.
    VIPhreeqc result extraction still touches process-global C buffers, so
    run_speciation serializes the C-facing critical section.
    """

    _thread_local = threading.local()
    _database_path: Optional[Path] = None

    def __init__(self, database: str = "phreeqc.dat"):
        """
        Initialize PHREEQC backend.

        Args:
            database: PHREEQC database file ("phreeqc.dat", "pitzer.dat", etc.)
        """
        self.database = database

    def _get_phreeqc(self) -> Any:
        """
        Get thread-local PHREEQC instance.

        Creates a new instance if this is the first call from this thread.

        Returns:
            phreeqpython.PhreeqPython instance for this thread
        """
        if not hasattr(self._thread_local, "pp_by_database"):
            self._thread_local.pp_by_database = {}
        if self.database not in self._thread_local.pp_by_database:
            logger.debug(f"Creating new PHREEQC instance for thread {threading.current_thread().name}")
            self._thread_local.pp_by_database[self.database] = create_phreeqpython(self.database)

        return self._thread_local.pp_by_database[self.database]

    def database_record(self) -> Dict[str, str]:
        """The database this backend loads: name, whose file it is, sha256."""
        return phreeqc_database_record(self.database)

    def _database_phases(self) -> Dict[str, str]:
        """The phases the loaded database file defines (lower-case name: its spelling)."""
        from puran_mcp_support.engineering.phreeqc_phases import database_phase_names

        return database_phase_names(str(resolve_phreeqc_database(self.database)[2]))

    def convert_to_phreeqc_solution(self, ion_dict: Dict[str, float]) -> Dict[str, Any]:
        'Convert ion dictionary to PHREEQC solution format.\n\n        Args:\n            ion_dict: Ion concentrations in mg/L (e.g., {"Na+": 1000.0, "Cl-": 1500.0})'
        solution: Dict[str, Any] = {}

        for ion, conc_mg_L in ion_dict.items():
            if ion not in ION_TO_PHREEQC:
                logger.warning(f"Unknown ion '{ion}'; passing through directly")
                solution[ion] = conc_mg_L
                continue

            phreeqc_name, formula = ION_TO_PHREEQC[ion]
            solution[phreeqc_name] = f"{float(conc_mg_L)!r} as {formula}"

        return solution

    def run_speciation(
        self,
        ions: Dict[str, float],
        temperature_C: float = 25.0,
        pH: Optional[float] = None,
        pe: float = 4.0,
    ) -> SpeciationResult:
        """
        Run PHREEQC aqueous speciation calculation.

        Args:
            ions: Ion concentrations in mg/L (e.g., {"Na+": 1000.0, "Cl-": 1500.0})
            temperature_C: Temperature in °C
            pH: Reported pH. It is required because this solution definition
                does not contain a charge-adjustment directive or acid/base
                inventory from which pH could be solved.
            pe: Redox potential (dimensionless, default 4.0 for oxic)

        Returns:
            SpeciationResult with pH, species, saturation indices
        """
        if pH is None:
            raise ValueError("A reported pH is required for PHREEQC water calculations")
        ions = validate_ion_concentrations(ions)

        # Convert to PHREEQC format
        phreeqc_solution = self.convert_to_phreeqc_solution(ions)

        # Add temperature, pH, pe
        phreeqc_solution["temp"] = temperature_C
        phreeqc_solution["pe"] = pe

        phreeqc_solution["pH"] = pH

        # Add units declaration
        phreeqc_solution["units"] = "mg/L"

        # Calculate charge balance
        charge_balance = calculate_charge_balance(ions)

        with _VIPHREEQC_LOCK:
            sol = None
            try:
                try:
                    pp = self._get_phreeqc()
                    sol = pp.add_solution(phreeqc_solution)
                except PHREEQCBackendUnavailableError:
                    raise
                except Exception as e:
                    logger.error(f"PHREEQC error: {e}")
                    if _is_phreeqc_nonconvergence(e):
                        raise WaterChemistrySpeciationNonconvergenceError(
                            ions=ions,
                            temperature_C=temperature_C,
                            pH=pH,
                            backend_exception=e,
                        ) from e
                    raise RuntimeError(
                        "PHREEQC aqueous speciation failed outside the "
                        "recognized convergence diagnostics"
                    ) from e

                # Extract results while serialized; phreeqpython delegates these
                # reads to VIPhreeqc process-global buffers.
                solution_pH = sol.pH
                calculated_pe = sol.pe
                ionic_strength = sol.I  # mol/L
                species_snapshot = dict(sol.species)

                # Alkalinity from the species, never a substitute: a failure refuses (D5 of
                
                alkalinity = _carbonate_alkalinity_mg_caco3(
                    {"molalities": dict(sol.species_molalities), "kgw_per_l": sol.mass / sol.volume}
                )

                # Major species, PHREEQC's molality (mol per kg of water): phreeqpython's ``sol.species`` is each
                # species' MOLES (``get_species_moles``), equal to the molality only on 1 kg of water (C16B-3 of
                
                species = {}
                for species_name, molality in species_molalities_per_kg_water(sol, species_snapshot).items():
                    if molality > 1e-9:  # Only include significant species
                        species[species_name] = molality

                # Saturation indices of the screened minerals the database defines; one it does not define is
                # named on the answer, and any other failure refuses (a bare except had dropped either silently)
                saturation_indices = {}
                not_in_database = []
                defined = self._database_phases()
                from puran_mcp_support.engineering.phreeqc_saturation import saturation_index

                database_file = str(resolve_phreeqc_database(self.database)[2])
                for mineral in SCREENED_MINERALS:
                    if mineral.lower() not in defined:
                        not_in_database.append(mineral)
                        continue
                    
                    # drops nu_w log a_w; VIPhreeqc's -999 (no component of the phase in the water) passes unchanged
                    saturation_indices[mineral] = saturation_index(sol, mineral, database_path=database_file)
            finally:
                if sol is not None:
                    try:
                        sol.forget()
                    except:
                        pass  # forget() may not be available in all phreeqpython versions

        # Create result (excluding raw_solution to avoid memory retention per Codex)
        result = SpeciationResult(
            pH=solution_pH,
            pe=calculated_pe,
            temperature_C=temperature_C,
            ionic_strength_M=ionic_strength,
            alkalinity_mg_L_CaCO3=alkalinity,
            species=species,
            saturation_indices=saturation_indices,
            charge_balance_percent=charge_balance,
            raw_solution=None,  # Don't keep PHREEQC solution object (memory leak)
            database=self.database_record(),
            saturation_indices_not_in_database=tuple(not_in_database),
        )

        return result

    def calculate_saturation_indices(
        self,
        solution_definition: Dict[str, Any],
        phase_names: tuple[str, ...],
    ) -> Dict[str, float]:
        """Evaluate selected PHREEQC phase saturation indices for one solution.

        ``solution_definition`` uses native PHREEQC solution keys and must carry
        its own units, pH, temperature, and oxidation-state-qualified totals.
        The C-facing calculation and result extraction are serialized for the
        same process-global-buffer reason as :meth:`run_speciation`.
        """
        if not phase_names:
            raise ValueError("phase_names must contain at least one phase")

        with _VIPHREEQC_LOCK:
            sol = None
            try:
                try:
                    pp = self._get_phreeqc()
                    sol = pp.add_solution(dict(solution_definition))
                except PHREEQCBackendUnavailableError:
                    raise
                except Exception as exc:
                    logger.error(f"PHREEQC saturation calculation error: {exc}")
                    raise RuntimeError(
                        f"PHREEQC saturation calculation failed: {exc}"
                    ) from exc

                available = dict(sol.phases)
                missing = [name for name in phase_names if name not in available]
                if missing:
                    raise RuntimeError(
                        "PHREEQC saturation phases unavailable: "
                        + ", ".join(missing)
                    )
                
                # Solution.phases drops nu_w log a_w
                from puran_mcp_support.engineering.phreeqc_saturation import saturation_index

                database_file = str(resolve_phreeqc_database(self.database)[2])
                return {
                    name: _require_saturation_index_value(
                        name, saturation_index(sol, name, database_path=database_file))
                    for name in phase_names
                }
            finally:
                if sol is not None:
                    try:
                        sol.forget()
                    except Exception:
                        pass

    def calculate_langelier_index(
        self,
        ions: Dict[str, float],
        temperature_C: float = 25.0,
        pH: Optional[float] = None,
    ) -> float:
        """
        Calculate Langelier Saturation Index (LSI).

        LSI = pH - pH_s
        where pH_s is the pH at calcite saturation.

        Args:
            ions: Ion concentrations in mg/L
            temperature_C: Temperature in °C
            pH: Reported pH; required by ``run_speciation``

        Returns:
            LSI value (positive = calcite supersaturation, negative = calcite
            undersaturation). LSI is not a metal-corrosion index.
        """
        result = self.run_speciation(ions, temperature_C, pH)
        pH_s = self.calculate_calcite_saturation_pH(
            ions=ions,
            temperature_C=temperature_C,
            reported_pH=result.pH,
            reported_speciation=result,
        )
        return result.pH - pH_s

    def calculate_calcite_saturation_pH(
        self,
        *,
        ions: Dict[str, float],
        temperature_C: float,
        reported_pH: float,
        reported_speciation: Optional[SpeciationResult] = None,
    ) -> float:
        """Solve for the pH at which the submitted water has SI(Calcite)=0.

        Calcite SI is not generally one pH unit per pH unit, so subtracting SI
        from measured pH does not locate the saturation root.  This brackets the
        root with successful PHREEQC states and then bisects it.
        """
        cache: Dict[float, float] = {}
        if reported_speciation is not None:
            cache[float(reported_pH)] = require_saturation_index(
                reported_speciation, "Calcite"
            )

        def calcite_si(candidate_pH: float) -> float:
            key = float(candidate_pH)
            if key not in cache:
                candidate = self.run_speciation(
                    ions=ions,
                    temperature_C=temperature_C,
                    pH=key,
                )
                cache[key] = require_saturation_index(candidate, "Calcite")
            return cache[key]

        # PHREEQC may not converge at extreme pH for a fixed alkalinity. Scan
        # the practical aqueous range and retain only successfully solved states.
        candidates = sorted(
            {float(reported_pH)}
            | {3.0 + 0.5 * index for index in range(19)}  # pH 3.0 through 12.0
        )
        attempted: list[float] = []
        solved: list[tuple[float, float]] = []

        def record(method, termination, root=None, residual=None, bracket=None):
            evidence = {
                "algorithm": "fixed_alkalinity_calcite_scan_bisection_v1",
                "method": method,
                "termination": termination,
                "root_pH": root,
                "residual_si_calcite": residual,
                "scan_si_absolute_tolerance": 1e-8,
                "bisection_si_absolute_tolerance": 1e-7,
                "bisection_pH_interval_tolerance": 1e-7,
                "maximum_bisection_iterations": 60,
                "planned_scan_pH_interval": [min(candidates), max(candidates)],
                "attempted_scan_pH_interval": [min(attempted), max(attempted)],
                "scan_completed": len(attempted) == len(candidates),
                "converged_scan_points": len(solved),
                "sign_bracket_pH": list(bracket) if bracket else None,
                "sign_bracket_si": [cache[bracket[0]], cache[bracket[1]]] if bracket else None,
                "qualification": (
                    "ROOT_ACCEPTED_BY_TOLERANCE_WITHOUT_SIGN_BRACKET"
                    if method == "scan_tolerance" else
                    "NO_ROOT_FOUND_IN_CONVERGED_SCAN_NOT_PROOF_OF_NONEXISTENCE"
                    if method == "not_found" else
                    "ITERATION_LIMIT_NOT_A_CONVERGENCE_CERTIFICATE"
                    if termination == "iteration_limit" else None
                ),
                "standing": "numerical_solver_evidence_not_proof_of_exact_root",
            }
            if reported_speciation is not None:
                reported_speciation.calcite_root_evidence = evidence
            return evidence
        for candidate_pH in candidates:
            attempted.append(candidate_pH)
            try:
                value = calcite_si(candidate_pH)
            except WaterChemistrySpeciationNonconvergenceError:
                # outside the water's own pH domain at this alkalinity: not a bracket end. Any other failure (a
                # missing backend, an unreadable SI) refuses; it had been skipped like a non-converged pH (D5 of
                
                continue
            if abs(value) <= 1e-8:
                solved.append((candidate_pH, value))
                record("scan_tolerance", "scan_si_tolerance", candidate_pH, value)
                return candidate_pH
            solved.append((candidate_pH, value))

        bracket: Optional[tuple[float, float]] = None
        for (left_pH, left_si), (right_pH, right_si) in zip(solved, solved[1:]):
            if left_si * right_si < 0.0:
                bracket = (left_pH, right_pH)
                break
        if bracket is None:
            minimum_si = min(value for _pH, value in solved)
            maximum_si = max(value for _pH, value in solved)
            error = CalciteSaturationPHUnavailableError(
                minimum_si=minimum_si,
                maximum_si=maximum_si,
            )
            error.root_evidence = record("not_found", "no_sign_bracket")
            raise error

        lower, upper = bracket
        lower_si = calcite_si(lower)
        for _ in range(60):
            midpoint = (lower + upper) / 2.0
            midpoint_si = calcite_si(midpoint)
            if abs(midpoint_si) <= 1e-7 or upper - lower <= 1e-7:
                record("bracket_bisection",
                       "bisection_si_tolerance" if abs(midpoint_si) <= 1e-7 else "pH_interval_tolerance",
                       midpoint, midpoint_si, (lower, upper))
                return midpoint
            if lower_si * midpoint_si <= 0.0:
                upper = midpoint
            else:
                lower = midpoint
                lower_si = midpoint_si
        # Preserve the historical return; do not run an extra calculation to
        # fabricate a residual for an unevaluated final midpoint.
        record("bracket_bisection", "iteration_limit", (lower + upper) / 2.0,
               bracket=(lower, upper))
        return (lower + upper) / 2.0

    def predict_scaling_tendency(
        self,
        ions: Dict[str, float],
        temperature_C: float = 25.0,
        pH: Optional[float] = None,
        speciation_result: Optional[SpeciationResult] = None,
    ) -> Tuple[ScalingResult, SpeciationResult]:
        """
        Predict CaCO₃ saturation/deposition tendency and calculate the
        Larson--Skold relative iron/steel corrosivity indicator.

        Args:
            ions: Ion concentrations in mg/L
            temperature_C: Temperature in °C
            pH: Measured pH
            speciation_result: Optional pre-computed speciation (avoids double calculation)

        Returns:
            Tuple of (ScalingResult, SpeciationResult) for reuse
        """
        # Reuse speciation if provided (per Codex: avoid double PHREEQC runs)
        if speciation_result is None:
            speciation_result = self.run_speciation(ions, temperature_C, pH)

        result = speciation_result

        # Langelier/Ryznar use the actual saturation pH. SI(Calcite) is a
        # logarithmic activity ratio, not a pH offset; solve SI=0 explicitly.
        try:
            pH_s = self.calculate_calcite_saturation_pH(
                ions=ions,
                temperature_C=temperature_C,
                reported_pH=result.pH,
                reported_speciation=result,
            )
        except CalciteSaturationPHUnavailableError as exc:
            pH_s = None
            lsi = None
            rsi = None
            no_root_sign_classification = (
                exc.converged_si_sign_classification
            )
        else:
            lsi = result.pH - pH_s
            # Ryznar Stability Index (RSI)
            rsi = 2 * pH_s - result.pH
            no_root_sign_classification = None

        # Puckorius Scaling Index (PSI) is NOT RSI with a different name. PSI
        # uses an equilibrium pH term, whereas RSI uses measured pH. The former
        # implementation set PSI = 2*pH_s - measured_pH, making it numerically
        # identical to RSI. The primary equation has not been content-verified
        # here. Suppress rather than ship an unverified substitute as Puckorius.
        psi = None

        # Larson Ratio (corrosivity indicator)
        # LR = (Cl⁻ + SO₄²⁻) / HCO₃⁻ in meq/L
        # ABSENT IS NOT ZERO. `.get(..., 0.0)` on chloride and sulfate turned an
        # incomplete panel into a proof of zero corrosivity: a lab sheet with only
        # Ca and HCO3 returned larson_ratio = 0.0, the most benign value the index
        # can take, with no indication that its two numerators were never measured.
        # The Larson ratio is a chloride-plus-sulfate index; without them it has no
        # meaning at all.
        _larson_missing = [
            name for name in ("Cl-", "SO4-2", "HCO3-") if name not in ions
        ]
        cl_meq = mg_L_to_meq_L(ions.get("Cl-", 0.0), 35.45, 1)
        so4_meq = mg_L_to_meq_L(ions.get("SO4-2", 0.0), 96.06, 2)
        hco3_meq = mg_L_to_meq_L(ions.get("HCO3-", 0.0), 61.02, 1)

        if _larson_missing:
            larson_ratio = None
        elif hco3_meq > 0:
            larson_ratio = (cl_meq + so4_meq) / hco3_meq
        else:
            larson_ratio = float('inf')

        # LSI reports calcite saturation state. It cannot support a metal-loss
        # verdict. Larson--Skold is the only one of these four indices that
        # speaks to metal corrosion, and only as a relative indicator for iron
        # and steel; no unsourced ratio bands are converted into a verdict.
        if no_root_sign_classification == "all_positive":
            interpretation = (
                "Calcite is supersaturated at the reported state and at every "
                "converged fixed-alkalinity pH scan point; no saturation pH "
                "exists in the scanned range, so Langelier and Ryznar indices "
                "are undefined"
            )
        elif no_root_sign_classification == "all_negative":
            interpretation = (
                "Langelier and Ryznar indices not defined: the fixed-alkalinity "
                "water has no converged pH where SI(Calcite)=0"
            )
        elif lsi is None:
            interpretation = (
                "Calcite saturation classification is indeterminate: converged "
                "fixed-alkalinity scan points had mixed SI(Calcite) signs but no "
                "saturation root was resolved; Langelier and Ryznar indices are "
                "undefined"
            )
        elif lsi > 0.0:
            interpretation = "Calcite supersaturated (LSI > 0)"
        elif lsi < 0.0:
            interpretation = "Calcite undersaturated (LSI < 0)"
        else:
            interpretation = "Calcite at saturation equilibrium (LSI = 0)"

        if larson_ratio is None:
            interpretation += (
                f"; Larson ratio NOT COMPUTED - {', '.join(_larson_missing)} absent "
                f"from the analysis, so relative iron/steel corrosivity is unassessed."
            )
        else:
            interpretation += (
                "; Larson--Skold ratio reported as a relative iron/steel "
                "corrosivity indicator without an absolute verdict"
            )

        scaling_result = ScalingResult(
            lsi=lsi,
            rsi=rsi,
            puckorius_index=psi,
            larson_ratio=larson_ratio,
            interpretation=interpretation,
            calcite_no_root_sign_classification=no_root_sign_classification,
            calcite_root_evidence=speciation_result.calcite_root_evidence,
        )

        return scaling_result, speciation_result


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def validate_water_chemistry(ions: Dict[str, float], max_imbalance: float = 5.0) -> None:
    """
    Validate water chemistry for acceptable charge balance.

    Args:
        ions: Ion concentrations in mg/L
        max_imbalance: Maximum acceptable charge imbalance (%)

    Raises:
        ValueError: If charge imbalance exceeds max_imbalance
    """
    charge_balance = calculate_charge_balance(ions)

    if abs(charge_balance) > max_imbalance:
        raise ValueError(
            f"Charge imbalance {repr(float(charge_balance))}% exceeds threshold "
            f"{repr(float(max_imbalance))}%. "
            "Check ion concentrations or adjust max_imbalance parameter."
        )

    logger.info(f"Charge balance: {charge_balance:.2f}%")
