"""
Pydantic models for standardized request/response schemas.

All tools return results with:
- Central estimate (median or nominal)
- Uncertainty bounds (p05, p95)
- Provenance metadata (model, validation dataset, sources, confidence)

Design Philosophy (from Codex review):
- Document JSON response schema early (median/p05/p95, metadata, provenance)
  so downstream agent frameworks can introspect uncertainty and move between
  tiers cleanly.
"""

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, validator
from enum import Enum


# ============================================================================
# Confidence Levels
# ============================================================================

class ConfidenceLevel(str, Enum):
    """Confidence in prediction quality"""
    HIGH = "high"          # Validated against >10 benchmarks, error <±30%
    MEDIUM = "medium"      # Validated against <10 benchmarks, error <±factor of 2
    LOW = "low"            # Extrapolated beyond validation range, error >±factor of 2
    UNKNOWN = "unknown"    # No validation data available


# ============================================================================
# Provenance Metadata
# ============================================================================

class ProvenanceMetadata(BaseModel):
    """
    Provenance tracking for all results.

    Enables AI agents to:
    - Assess result reliability
    - Trace predictions to source models/handbooks
    - Understand validation basis
    """
    model: str = Field(..., description="Model or tool identifier (e.g., 'NORSOK_M506', 'kb.material_screening')")
    version: Optional[str] = Field(None, description="Model version or git commit")
    validation_dataset: Optional[str] = Field(None, description="Benchmark dataset identifier (e.g., 'OhioU_FREECORP', 'NORSOK_validation')")
    confidence: ConfidenceLevel = Field(..., description="Confidence level in prediction")
    sources: List[str] = Field(default_factory=list, description="Literature citations or handbook page references")
    assumptions: List[str] = Field(default_factory=list, description="Key modeling assumptions")
    warnings: List[str] = Field(default_factory=list, description="Warnings or extrapolation notices")


# ============================================================================
# Tier 0: Handbook Lookup Results
# ============================================================================

class MaterialCompatibility(BaseModel):
    """Material compatibility screening result"""
    material: str = Field(..., description="Material identifier (e.g., 'CS', '316L')")
    environment: str = Field(..., description="Environment description")
    compatibility: Literal["acceptable", "marginal", "not_recommended"] = Field(..., description="Compatibility rating")
    typical_rate_range: Optional[tuple[float, float]] = Field(None, description="Typical corrosion rate range (mm/y)")
    notes: str = Field(..., description="Detailed notes from handbook")
    provenance: ProvenanceMetadata = Field(..., description="Source tracking")


class TypicalRateResult(BaseModel):
    """Typical corrosion rate from handbook"""
    material: str
    environment: str
    rate_min_mm_per_y: float = Field(..., description="Minimum reported rate (mm/y)")
    rate_max_mm_per_y: float = Field(..., description="Maximum reported rate (mm/y)")
    rate_typical_mm_per_y: float = Field(..., description="Typical/median rate (mm/y)")
    conditions: str = Field(..., description="Conditions for reported rates")
    provenance: ProvenanceMetadata


class MechanismGuidance(BaseModel):
    """Corrosion mechanism identification and guidance"""
    probable_mechanisms: List[str] = Field(..., description="Likely corrosion mechanisms")
    symptoms: List[str] = Field(..., description="Observed or expected symptoms")
    recommendations: List[str] = Field(..., description="Mitigation recommendations")
    tests_recommended: List[str] = Field(..., description="Recommended diagnostic tests")
    provenance: ProvenanceMetadata


# ============================================================================
# Tier 1: Chemistry Results
# ============================================================================

class SpeciationResult(BaseModel):
    """
    Aqueous chemistry speciation result.

    Output from PHREEQC/Reaktoro chemistry backends.
    """
    pH: float = Field(..., description="Final pH")
    temperature_C: float = Field(..., description="Temperature (°C)")
    ionic_strength: float = Field(..., description="Ionic strength (mol/L)")
    activities: Dict[str, float] = Field(default_factory=dict, description="Species activities {species: activity}")
    concentrations_mg_L: Dict[str, float] = Field(default_factory=dict, description="Species concentrations {species: mg/L}")
    saturation_indices: Dict[str, float] = Field(default_factory=dict, description="Saturation indices {mineral: SI}")
    pe: Optional[float] = Field(None, description="pe (electron activity)")
    Eh_V: Optional[float] = Field(None, description="Redox potential vs SHE (V)")
    provenance: ProvenanceMetadata


class PourbaixResult(BaseModel):
    """Pourbaix (potential-pH) stability result"""
    material: str
    pH: float
    potential_V_SHE: float = Field(..., description="Potential vs SHE (V)")
    stable_phase: str = Field(..., description="Thermodynamically stable phase")
    corrosion_region: Literal["immunity", "passivation", "corrosion"] = Field(..., description="Pourbaix region")
    risk_assessment: str = Field(..., description="Stability assessment")
    provenance: ProvenanceMetadata


# ============================================================================
# Tier 2: Mechanistic Corrosion Results
# ============================================================================

class CorrosionResult(BaseModel):
    """
    Standardized corrosion rate prediction result.

    Always includes uncertainty (median + p05/p95) to support
    uncertainty-first philosophy.
    """
    material: str
    mechanism: str = Field(..., description="Dominant corrosion mechanism (e.g., 'uniform_CO2', 'galvanic', 'pitting')")

    # Central estimate
    rate_mm_per_y: float = Field(..., description="Median corrosion rate (mm/y)", gt=0)

    # Uncertainty bounds
    rate_p05_mm_per_y: float = Field(..., description="5th percentile rate (mm/y)", gt=0)
    rate_p95_mm_per_y: float = Field(..., description="95th percentile rate (mm/y)", gt=0)

    # Alternative units
    rate_mpy: Optional[float] = Field(None, description="Rate in mils per year (mpy)")

    # Component contributions (if applicable)
    rate_components: Optional[Dict[str, float]] = Field(None, description="Rate contributions {component: mm/y}")

    # Process conditions
    temperature_C: float
    environment_summary: str = Field(..., description="Brief environment description")

    # Provenance
    provenance: ProvenanceMetadata

    @validator('rate_p95_mm_per_y')
    def p95_greater_than_median(cls, v, values):
        if 'rate_mm_per_y' in values and v < values['rate_mm_per_y']:
            raise ValueError('rate_p95_mm_per_y must be >= rate_mm_per_y')
        return v

    @validator('rate_p05_mm_per_y')
    def p05_less_than_median(cls, v, values):
        if 'rate_mm_per_y' in values and v > values['rate_mm_per_y']:
            raise ValueError('rate_p05_mm_per_y must be <= rate_mm_per_y')
        return v


class GalvanicResult(BaseModel):
    """Galvanic corrosion prediction result"""
    anode_material: str
    cathode_material: str
    mixed_potential_V_SCE: float = Field(..., description="Mixed potential vs SCE (V)")
    galvanic_current_A_m2: float = Field(..., description="Galvanic current density (A/m²)")

    # Anode corrosion rate
    anode_rate_mm_per_y: float = Field(..., description="Anode corrosion rate (mm/y)", gt=0)
    anode_rate_p05_mm_per_y: float = Field(..., description="5th percentile", gt=0)
    anode_rate_p95_mm_per_y: float = Field(..., description="95th percentile", gt=0)

    # Geometry factors
    area_ratio: float = Field(..., description="Cathode/anode area ratio")
    sensitivity: Dict[str, str] = Field(default_factory=dict, description="Sensitivity notes")

    provenance: ProvenanceMetadata


class CoatingBarrierResult(BaseModel):
    """Coating barrier transport result"""
    coating_type: str
    thickness_um: float = Field(..., description="Coating thickness (μm)")

    # Oxygen flux
    J_O2_mol_m2_s: float = Field(..., description="Oxygen flux (mol/m²/s)")
    wet_blocking_factor: float = Field(..., description="Blocking factor (0-1)", ge=0, le=1)

    # Effective current limit
    effective_i_lim_A_m2: float = Field(..., description="Effective limiting current (A/m²)")

    # Environmental conditions
    temperature_C: float
    relative_humidity_pct: float

    notes: str = Field(..., description="Model notes (e.g., plasticization effects)")
    provenance: ProvenanceMetadata


class CUIResult(BaseModel):
    """Corrosion Under Insulation (CUI) prediction"""
    probability_of_failure_class: Literal["High", "Medium", "Low", "Very Low"] = Field(..., description="DNV-RP-G109 PoF class")

    # Rate estimate
    rate_estimate_mm_per_y: float = Field(..., description="Estimated CUI rate (mm/y)")
    rate_p05_mm_per_y: float = Field(..., description="5th percentile")
    rate_p95_mm_per_y: float = Field(..., description="95th percentile")

    # Drivers
    wetness_fraction: float = Field(..., description="Fraction of time wet (0-1)", ge=0, le=1)
    salt_load_mg_m2: float = Field(..., description="Salt contamination (mg/m²)")

    # Barrier assessment
    barrier_scores: Dict[str, str] = Field(..., description="DNV 4-barrier scores")

    method: str = Field(..., description="Methodology description")
    provenance: ProvenanceMetadata


class PittingScreenResult(BaseModel):
    """Stainless steel pitting resistance screening"""
    material: str
    PREN: float = Field(..., description="Pitting Resistance Equivalent Number")
    CPT_estimate_C: float = Field(..., description="Estimated Critical Pitting Temperature (°C)")

    # Environment
    chloride_g_L: float
    temperature_C: float

    # Risk assessment
    risk_band: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(..., description="Pitting risk classification")
    risk_notes: str = Field(..., description="Detailed risk assessment")

    # Recommendations
    recommendations: List[str] = Field(default_factory=list, description="Material upgrade recommendations")

    provenance: ProvenanceMetadata


# ============================================================================
# Tier 3: Uncertainty Quantification Results
# ============================================================================

class MonteCarloResult(BaseModel):
    """Monte Carlo uncertainty propagation result"""
    model_called: str = Field(..., description="Tool that was wrapped")
    n_samples: int = Field(..., description="Number of Monte Carlo samples")

    # Output statistics
    output_median: float
    output_p05: float
    output_p95: float
    output_mean: float
    output_std: float

    # Sensitivity analysis
    tornado: Dict[str, float] = Field(..., description="Sensitivity ranking {param: variance_contribution}")

    # Input distributions used
    input_distributions: Dict[str, Dict[str, Any]] = Field(..., description="Input uncertainty definitions")

    # Optional: full sample array
    samples: Optional[List[float]] = Field(None, description="Full output sample array")

    provenance: ProvenanceMetadata


# ============================================================================
# Service Life & Economics
# ============================================================================

class ServiceLifeResult(BaseModel):
    """Equipment service life prediction"""
    material: str
    initial_thickness_mm: float
    corrosion_allowance_mm: float

    # Corrosion rate (with uncertainty)
    rate_mm_per_y: float
    rate_p05_mm_per_y: float
    rate_p95_mm_per_y: float

    # Life prediction
    service_life_years: float = Field(..., description="Median service life (years)")
    service_life_p05_years: float = Field(..., description="Conservative estimate (5th percentile)")
    service_life_p95_years: float = Field(..., description="Optimistic estimate (95th percentile)")

    # Inspection intervals
    recommended_inspection_interval_years: float

    # Safety factors applied
    safety_factor: float = Field(default=2.0, description="Safety factor applied to corrosion rate")

    provenance: ProvenanceMetadata
