"""
Abstract base classes defining plugin contracts for all tiers.

These interfaces enable:
- Swapping chemistry backends (phreeqpython ↔ Reaktoro ↔ IPhreeqcPy)
- Pluggable mechanistic models (NORSOK ↔ MULTICORP ↔ custom)
- Consistent tool orchestration across tiers
- Type safety and IDE support

Design Philosophy (from Codex review):
- Adopt a plug-in contract per tier: Tier 0 (semantic), Tier 1 (chemistry),
  Tier 2 (mechanistic), Tier 3 (UQ) with shared pydantic schemas so you can
  swap models without rewriting orchestration.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


# ============================================================================
# Tier 0: Handbook Lookup Interfaces
# ============================================================================

class HandbookLookup(ABC):
    """
    Abstract base for Tier 0 semantic search tools.

    Implementations wrap corrosion_kb semantic search (2,980 vector chunks)
    to provide fast screening (<0.5 sec) for material compatibility, typical
    rates, and mechanism guidance.
    """

    @abstractmethod
    def query(self, query_text: str, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Query the handbook knowledge base.

        Args:
            query_text: Natural language query
            filters: Optional filters (e.g., material, environment)

        Returns:
            Dictionary with results, sources, and confidence
        """
        pass


# ============================================================================
# Tier 1: Chemistry Backend Interfaces
# ============================================================================

class ChemistryBackend(ABC):
    """
    Abstract base for chemistry engines (phreeqpython, Reaktoro, IPhreeqcPy).

    Design Pattern:
    - Separate PHREEQC I/O from reaction logic
    - Lightweight adapter around phreeqpython
    - Lets you prototype Reaktoro or IPhreeqcPy backends later while keeping
      identical JSON payloads

    Current Implementation: PhreeqcAdapter (phreeqpython backend)
    Future Options: ReaktoroAdapter, IPhreeqcPyAdapter
    """

    @abstractmethod
    def speciate(
        self,
        temperature_C: float,
        pressure_bar: float,
        water: Dict[str, float],
        gases: Optional[Dict[str, float]] = None,
        ions: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate aqueous speciation.

        Args:
            temperature_C: Temperature in Celsius
            pressure_bar: Pressure in bar
            water: Water properties (pH, alkalinity, etc.)
            gases: Partial pressures (pCO2_bar, pH2S_bar, pO2_bar)
            ions: Ion concentrations (Cl_mg_L, SO4_mg_L, etc.)

        Returns:
            Dictionary with:
            - pH: Final pH
            - activities: Species activities {species: value}
            - ionic_strength: Ionic strength (mol/L)
            - saturation_indices: Saturation indices {mineral: SI}
            - concentrations: Species concentrations {species: mg/L}
        """
        pass

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return name of chemistry backend (e.g., 'phreeqpython', 'reaktoro')"""
        pass


# ============================================================================
# Tier 2: Mechanistic Model Interfaces
# ============================================================================

class MechanisticModel(ABC):
    """
    Abstract base for Tier 2 physics-based corrosion models.

    All mechanistic models should:
    - Accept chemistry context (from Tier 1)
    - Return standardized CorrosionResult with uncertainty
    - Include model provenance and validation dataset references
    - Support multiple model backends where applicable (e.g., NORSOK vs MULTICORP)
    """

    @abstractmethod
    def predict_rate(
        self,
        material: str,
        chemistry: Dict[str, Any],
        conditions: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Predict corrosion rate for given material and conditions.

        Args:
            material: Material identifier (e.g., "CS", "316L", "duplex")
            chemistry: Speciation result from Tier 1 chemistry backend
            conditions: Process conditions (T_C, v_m_s, geometry, etc.)

        Returns:
            Dictionary matching CorrosionResult schema:
            - rate_mm_per_y: Corrosion rate (median)
            - rate_p05_mm_per_y: 5th percentile
            - rate_p95_mm_per_y: 95th percentile
            - mechanism: Dominant corrosion mechanism
            - model_name: Model identifier
            - validation_dataset: Reference dataset
            - confidence: "high", "medium", "low"
            - sources: Literature citations
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return model identifier (e.g., 'NORSOK_M506', 'MULTICORP', 'ChiltonColburn')"""
        pass

    @abstractmethod
    def get_validation_dataset(self) -> Optional[str]:
        """Return validation dataset identifier (e.g., 'NORSOK_validation', 'OhioU_FREECORP')"""
        pass


# ============================================================================
# Tier 3: Uncertainty Quantification Interfaces
# ============================================================================

class UncertaintyQuantifier(ABC):
    """
    Abstract base for Tier 3 uncertainty propagation tools.

    Design Pattern:
    - Async Monte Carlo with variance reduction (Latin Hypercube)
    - Wrap any Tier 1-2 tool to propagate input uncertainties
    - Return tornado diagrams and sensitivity analysis
    """

    @abstractmethod
    async def propagate_uncertainty(
        self,
        model_call: str,
        base_inputs: Dict[str, Any],
        distributions: Dict[str, Dict[str, Any]],
        n_samples: int = 1000,
    ) -> Dict[str, Any]:
        """
        Propagate input uncertainties via Monte Carlo.

        Args:
            model_call: Tool name to wrap (e.g., "corrosion.uniform.co2_h2s.rate")
            base_inputs: Nominal input values
            distributions: Input distributions {param: {type, mean, std, ...}}
            n_samples: Number of Monte Carlo samples

        Returns:
            Dictionary with:
            - median: Median output value
            - p05: 5th percentile
            - p95: 95th percentile
            - tornado: Sensitivity ranking {param: variance_contribution}
            - samples: Full sample array (optional)
        """
        pass


# ============================================================================
# Material Database Interfaces
# ============================================================================

class MaterialDatabase(ABC):
    """
    Abstract base for material property databases.

    Enables querying:
    - Alloy compositions
    - PREN values (Pitting Resistance Equivalent Number)
    - CPT (Critical Pitting Temperature)
    - Galvanic series positions
    - Cost factors
    """

    @abstractmethod
    def get_material_properties(self, material_id: str) -> Dict[str, Any]:
        """Get all properties for a material"""
        pass

    @abstractmethod
    def calculate_pren(self, material_id: str) -> float:
        """Calculate PREN = %Cr + 3.3*%Mo + 16*%N"""
        pass

    @abstractmethod
    def estimate_cpt(self, material_id: str) -> float:
        """Estimate Critical Pitting Temperature (°C)"""
        pass
