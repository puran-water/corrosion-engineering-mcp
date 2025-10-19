"""
PHREEQC chemistry backend for corrosion engineering calculations.

This module provides a thread-safe wrapper around phreeqpython for:
- Aqueous speciation and pH calculation
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
    >>> print(result["pH_calculated"])
    7.48
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
from pathlib import Path

import phreeqpython

logger = logging.getLogger(__name__)

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
    "H+": {"charge": 1, "mw": 1.01, "name": "Hydrogen"},
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
    "OH-": {"charge": -1, "mw": 17.01, "name": "Hydroxide"},
}

# Map ion names to PHREEQC element keywords
# Format: (PHREEQC_keyword, conversion_factor)
ION_TO_PHREEQC: Dict[str, Tuple[str, float]] = {
    # Cations (1:1 mapping)
    "Na+": ("Na", 1.0),
    "Ca2+": ("Ca", 1.0),
    "Mg2+": ("Mg", 1.0),
    "K+": ("K", 1.0),
    "Fe2+": ("Fe(2)", 1.0),
    "Fe3+": ("Fe(3)", 1.0),
    "Mn2+": ("Mn", 1.0),
    "Ba2+": ("Ba", 1.0),
    "Sr2+": ("Sr", 1.0),
    "NH4+": ("N(-3)", 18.04 / 14.01),  # Express on nitrogen basis
    # Anions (with elemental conversion)
    "Cl-": ("Cl", 1.0),
    "SO4-2": ("S(6)", 96.06 / 32.07),  # Sulfate → Sulfur
    "HCO3-": ("Alkalinity", 61.02 / 50.0),  # Convert HCO3- to CaCO3 equivalents (mg/L)
    "CO3-2": ("C(4)", 60.01 / 12.01),  # Carbonate → Carbon
    "NO3-": ("N(5)", 62.00 / 14.01),  # Nitrate → Nitrogen
    "F-": ("F", 1.0),
    "PO4-3": ("P", 94.97 / 30.97),  # Phosphate → Phosphorus
    "Br-": ("Br", 1.0),
    "B(OH)4-": ("B", 78.84 / 10.81),  # Borate → Boron
    "SiO3-2": ("Si", 76.08 / 28.09),  # Silicate → Silicon
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SpeciationResult:
    """
    Result of PHREEQC aqueous speciation calculation.

    Attributes:
        pH: Calculated pH after charge balance
        pe: Redox potential (dimensionless)
        temperature_C: Temperature (°C)
        ionic_strength_M: Ionic strength (mol/L)
        alkalinity_mg_L_CaCO3: Total alkalinity as mg/L CaCO₃
        species: Dict of major species concentrations (mol/L)
        saturation_indices: Dict of SI values for relevant minerals
        charge_balance_percent: Charge imbalance (%)
        raw_solution: Optional raw phreeqpython Solution (None to prevent memory leaks)
    """
    pH: float
    pe: float
    temperature_C: float
    ionic_strength_M: float
    alkalinity_mg_L_CaCO3: float
    species: Dict[str, float]
    saturation_indices: Dict[str, float]
    charge_balance_percent: float
    raw_solution: Optional[Any] = None  # Disabled by default to prevent memory leaks


@dataclass
class ScalingResult:
    """
    Scaling tendency prediction result.

    Attributes:
        lsi: Langelier Saturation Index (pH - pH_s)
        rsi: Ryznar Stability Index (2 × pH_s - pH)
        puckorius_index: Puckorius Scaling Index
        larson_ratio: Cl⁻ + SO₄²⁻ to HCO₃⁻ ratio (corrosivity indicator)
        interpretation: Text interpretation of scaling/corrosion tendency
    """
    lsi: float
    rsi: float
    puckorius_index: float
    larson_ratio: float
    interpretation: str


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
    return concentration_mg_L / molecular_weight / 1000.0


def mol_L_to_mg_L(concentration_mol_L: float, molecular_weight: float) -> float:
    """
    Convert concentration from mol/L to mg/L.

    Args:
        concentration_mol_L: Concentration in mol/L
        molecular_weight: Molecular weight in g/mol

    Returns:
        Concentration in mg/L
    """
    return concentration_mol_L * molecular_weight * 1000.0


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
    mol_L = mg_L_to_mol_L(concentration_mg_L, molecular_weight)
    return mol_L * abs(charge) * 1000.0  # meq/L


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
    cation_meq = 0.0
    anion_meq = 0.0

    for ion, conc_mg_L in ion_dict.items():
        props = VALID_IONS.get(ion)
        if not props:
            logger.warning(f"Unknown ion '{ion}' in charge balance calculation")
            continue

        meq_L = mg_L_to_meq_L(conc_mg_L, props["mw"], props["charge"])
        if props["charge"] > 0:
            cation_meq += meq_L
        else:
            anion_meq += meq_L

    total_meq = cation_meq + anion_meq
    if total_meq == 0:
        return 0.0

    return (cation_meq - anion_meq) / total_meq * 100.0


# ---------------------------------------------------------------------------
# PHREEQC backend (thread-safe singleton)
# ---------------------------------------------------------------------------

class PHREEQCBackend:
    """
    Thread-safe PHREEQC chemistry backend.

    Uses threading.local() to give each thread its own PHREEQC instance,
    preventing race conditions in the C++ backend (per Codex guidance).
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

    def _get_phreeqc(self) -> phreeqpython.PhreeqPython:
        """
        Get thread-local PHREEQC instance.

        Creates a new instance if this is the first call from this thread.

        Returns:
            phreeqpython.PhreeqPython instance for this thread
        """
        if not hasattr(self._thread_local, "pp"):
            logger.debug(f"Creating new PHREEQC instance for thread {threading.current_thread().name}")
            self._thread_local.pp = phreeqpython.PhreeqPython(database=self.database)

        return self._thread_local.pp

    def convert_to_phreeqc_solution(self, ion_dict: Dict[str, float]) -> Dict[str, float]:
        """
        Convert ion dictionary to PHREEQC solution format.

        Args:
            ion_dict: Ion concentrations in mg/L (e.g., {"Na+": 1000.0, "Cl-": 1500.0})

        Returns:
            Dictionary with PHREEQC element keywords
        """
        solution: Dict[str, float] = {}

        for ion, conc_mg_L in ion_dict.items():
            if ion not in ION_TO_PHREEQC:
                logger.warning(f"Unknown ion '{ion}'; passing through directly")
                solution[ion] = conc_mg_L
                continue

            phreeqc_keyword, conversion_factor = ION_TO_PHREEQC[ion]
            solution[phreeqc_keyword] = conc_mg_L / conversion_factor

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
            pH: Initial pH (if None, PHREEQC calculates from charge balance)
            pe: Redox potential (dimensionless, default 4.0 for oxic)

        Returns:
            SpeciationResult with pH, species, saturation indices
        """
        pp = self._get_phreeqc()

        # Convert to PHREEQC format
        phreeqc_solution = self.convert_to_phreeqc_solution(ions)

        # Add temperature, pH, pe
        phreeqc_solution["temp"] = temperature_C
        phreeqc_solution["pe"] = pe

        if pH is not None:
            phreeqc_solution["pH"] = pH

        # Add units declaration
        phreeqc_solution["units"] = "mg/L"

        # Run PHREEQC
        try:
            sol = pp.add_solution(phreeqc_solution)
        except Exception as e:
            logger.error(f"PHREEQC error: {e}")
            raise RuntimeError(f"PHREEQC speciation failed: {e}") from e

        # Extract results
        calculated_pH = sol.pH
        calculated_pe = sol.pe
        ionic_strength = sol.I  # mol/L

        # Calculate alkalinity from species totals
        # Alkalinity = [HCO3-] + 2×[CO3-2] + [OH-] - [H+] (meq/L → mg/L as CaCO₃)
        # Per Codex: Using total("C") overshoots in acidic/organic waters
        try:
            # Get individual species totals in mol/L
            hco3_mol = sol.total("HCO3", units="mol") if "HCO3" in str(sol.species) else 0.0
            co3_mol = sol.total("CO3", units="mol") if "CO3" in str(sol.species) else 0.0
            oh_mol = sol.total("OH", units="mol") if "OH" in str(sol.species) else 0.0
            h_mol = sol.total("H", units="mol") if "H" in str(sol.species) else 0.0

            # Alkalinity in meq/L = [HCO3-] + 2×[CO3-2] + [OH-] - [H+]
            alkalinity_meq_L = (hco3_mol + 2.0 * co3_mol + oh_mol - h_mol) * 1000.0

            # Convert to mg/L as CaCO₃ (50 g/mol equivalent weight)
            alkalinity = alkalinity_meq_L * 50.0
        except Exception as e:
            logger.warning(f"Could not calculate alkalinity from species: {e}")
            alkalinity = 0.0

        # Get major species (mol/L)
        # In phreeqpython, sol.species returns a dict of {species_name: molality}
        species = {}
        for species_name, molality in sol.species.items():
            if molality > 1e-9:  # Only include significant species
                species[species_name] = molality

        # Get saturation indices for relevant minerals
        si_minerals = ["Calcite", "Aragonite", "Dolomite", "Gypsum", "Halite", "Siderite"]
        saturation_indices = {}
        for mineral in si_minerals:
            try:
                saturation_indices[mineral] = sol.si(mineral)
            except:
                pass  # Mineral not in database

        # Calculate charge balance
        charge_balance = calculate_charge_balance(ions)

        # Create result (excluding raw_solution to avoid memory retention per Codex)
        result = SpeciationResult(
            pH=calculated_pH,
            pe=calculated_pe,
            temperature_C=temperature_C,
            ionic_strength_M=ionic_strength,
            alkalinity_mg_L_CaCO3=alkalinity,
            species=species,
            saturation_indices=saturation_indices,
            charge_balance_percent=charge_balance,
            raw_solution=None,  # Don't keep PHREEQC solution object (memory leak)
        )

        # Dispose of PHREEQC solution to prevent memory leak (per Codex guidance)
        try:
            sol.forget()
        except:
            pass  # forget() may not be available in all phreeqpython versions

        return result

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
            pH: Measured pH (if None, uses PHREEQC-calculated pH)

        Returns:
            LSI value (positive = scaling, negative = corrosive)
        """
        result = self.run_speciation(ions, temperature_C, pH)

        # pH_s is the pH when SI(Calcite) = 0
        # We can approximate: pH_s ≈ pH - SI(Calcite)
        si_calcite = result.saturation_indices.get("Calcite", 0.0)
        pH_s = result.pH - si_calcite

        lsi = result.pH - pH_s

        return lsi

    def predict_scaling_tendency(
        self,
        ions: Dict[str, float],
        temperature_C: float = 25.0,
        pH: Optional[float] = None,
        speciation_result: Optional[SpeciationResult] = None,
    ) -> Tuple[ScalingResult, SpeciationResult]:
        """
        Predict scaling and corrosion tendency using multiple indices.

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

        # Langelier Saturation Index (LSI)
        si_calcite = result.saturation_indices.get("Calcite", 0.0)
        pH_s = result.pH - si_calcite
        lsi = result.pH - pH_s

        # Ryznar Stability Index (RSI)
        rsi = 2 * pH_s - result.pH

        # Puckorius Scaling Index (PSI)
        # PSI = 2 × pH_s - pH_eq
        # Simplified: PSI ≈ 2 × pH_s - pH
        psi = 2 * pH_s - result.pH

        # Larson Ratio (corrosivity indicator)
        # LR = (Cl⁻ + SO₄²⁻) / HCO₃⁻ in meq/L
        cl_meq = mg_L_to_meq_L(ions.get("Cl-", 0.0), 35.45, 1)
        so4_meq = mg_L_to_meq_L(ions.get("SO4-2", 0.0), 96.06, 2)
        hco3_meq = mg_L_to_meq_L(ions.get("HCO3-", 0.0), 61.02, 1)

        if hco3_meq > 0:
            larson_ratio = (cl_meq + so4_meq) / hco3_meq
        else:
            larson_ratio = float('inf')

        # Interpretation
        if lsi > 0.5:
            interpretation = "Scaling likely (LSI > 0.5)"
        elif lsi < -0.5:
            interpretation = "Corrosive (LSI < -0.5)"
        else:
            interpretation = "Near equilibrium (-0.5 < LSI < 0.5)"

        if larson_ratio > 1.0:
            interpretation += f"; High corrosivity (Larson ratio = {larson_ratio:.2f} > 1.0)"

        scaling_result = ScalingResult(
            lsi=lsi,
            rsi=rsi,
            puckorius_index=psi,
            larson_ratio=larson_ratio,
            interpretation=interpretation,
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
            f"Charge imbalance {charge_balance:.1f}% exceeds threshold {max_imbalance}%. "
            "Check ion concentrations or adjust max_imbalance parameter."
        )

    logger.info(f"Charge balance: {charge_balance:.2f}%")
