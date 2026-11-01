"""Pydantic request and response contracts used by released server paths.

Different tools expose different evidence types. A schema is retained only when
an active or explicitly gated path uses it; suppressed verdict/rate shapes are
not kept as sibling contracts.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel

from .materials import UnknownMaterialError, canonical_id as material_catalog_id
from .validation import (
    CHLORIDE_MG_L,
    DISSOLVED_OXYGEN_MG_L,
    GALVANIC_PH,
    GALVANIC_TEMPERATURE_C,
    HANDBOOK_TEXT_LENGTH,
    POURBAIX_GRID_POINTS,
    POURBAIX_PH,
)


SUPPORTED_GALVANIC_MATERIALS = ("HY80", "HY100", "SS316", "Ti", "I625", "CuNi")
SUPPORTED_POURBAIX_ELEMENTS = (
    "Fe",
    "Cr",
    "Ni",
    "Cu",
    "Ti",
    "Al",
    "Zn",
    "Mg",
    "Pb",
    "Sn",
    "Mo",
    "W",
    "Co",
    "Mn",
)

_GALVANIC_CATALOG_TO_NRL = {
    "HY80": "HY80",
    "HY100": "HY100",
    "316": "SS316",
    "316L": "SS316",
    "Ti_Gr2": "Ti",
    "Inconel_625": "I625",
    "CuNi_90_10": "CuNi",
    "CuNi_70_30": "CuNi",
}


def canonicalize_galvanic_material(value: str) -> str:
    """Return the current NRL canonical material ID for galvanic calculations."""

    try:
        catalog_id = material_catalog_id(value)
    except UnknownMaterialError as exc:
        raise ValueError(
            f"INVALID_MATERIAL: Material '{value}' not supported. "
            f"Options: {list(SUPPORTED_GALVANIC_MATERIALS)}"
        ) from exc
    nrl_id = _GALVANIC_CATALOG_TO_NRL.get(catalog_id)
    if nrl_id is None:
        raise ValueError(
            f"INVALID_GALVANIC_MATERIAL: Material '{value}' resolves to '{catalog_id}', "
            f"which is not supported by the current NRL galvanic model. "
            f"Options: {list(SUPPORTED_GALVANIC_MATERIALS)}"
        )
    return nrl_id


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

class ProvenanceMetadata(StrictFiniteBaseModel):
    """
    Provenance tracking for all results.

    Enables AI agents to:
    - Assess result reliability
    - Trace predictions to source models/handbooks
    - Understand validation basis
    """
    model: str = Field(..., description="Model or tool identifier (e.g., 'NORSOK_M506', 'kb.material_screening')")
    version: Optional[str] = Field(..., description="Model version or git commit")
    validation_dataset: Optional[str] = Field(..., description="Benchmark dataset identifier (e.g., 'OhioU_FREECORP', 'NORSOK_validation')")
    confidence: ConfidenceLevel = Field(..., description="Confidence level in prediction")
    sources: List[str] = Field(default_factory=list, description="Literature citations or handbook page references")
    assumptions: List[str] = Field(default_factory=list, description="Key modeling assumptions")
    warnings: List[str] = Field(default_factory=list, description="Warnings or extrapolation notices")


# ============================================================================
# Public Tool Input Schemas (flat MCP argument contract)
# ============================================================================

class ScreenMaterialsInput(StrictFiniteBaseModel):
    """Input for ``corrosion_screen_materials``."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    environment: str = Field(
        ...,
        description="Environment description (e.g., 'seawater, 35 g/L Cl, 25°C')",
        min_length=HANDBOOK_TEXT_LENGTH.minimum,
        max_length=HANDBOOK_TEXT_LENGTH.maximum,
    )
    candidates: List[str] = Field(
        ...,
        description="Material identifiers to screen (e.g., ['CS', '316L', 'duplex'])",
        min_length=1,
        max_length=20,
    )
    application: Optional[str] = Field(
        default=None,
        description="Application context (e.g., 'heat_exchanger_tubes', 'piping')",
        max_length=200,
    )


class QueryTypicalRatesInput(StrictFiniteBaseModel):
    """Input for ``corrosion_query_typical_rates``."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    material: str = Field(
        ...,
        description="Material identifier (e.g., 'CS', '316L', 'duplex')",
        min_length=1,
        max_length=50,
    )
    environment_summary: str = Field(
        ...,
        description="Brief environment description (e.g., 'seawater, 25°C, stagnant')",
        min_length=HANDBOOK_TEXT_LENGTH.minimum,
        max_length=HANDBOOK_TEXT_LENGTH.maximum,
    )


class IdentifyMechanismInput(StrictFiniteBaseModel):
    """Input for ``corrosion_identify_mechanism``."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    material: str = Field(
        ...,
        description="Material identifier (e.g., '304_SS', 'CS')",
        min_length=1,
        max_length=50,
    )
    symptoms: List[str] = Field(
        ...,
        description="Observed symptoms (e.g., ['localized_attack', 'pits', 'crevices'])",
        min_length=1,
        max_length=20,
    )
    environment: Optional[str] = Field(
        default=None,
        description="Optional environment description for context",
        max_length=HANDBOOK_TEXT_LENGTH.maximum,
    )


class AssessGalvanicInput(StrictFiniteBaseModel):
    """Validated input accepted only to return the galvanic withdrawal error."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    anode_material: str = Field(
        ...,
        description=f"Less noble metal. Options: {', '.join(SUPPORTED_GALVANIC_MATERIALS)}",
    )
    cathode_material: str = Field(
        ...,
        description=f"More noble metal. Options: {', '.join(SUPPORTED_GALVANIC_MATERIALS)}",
    )
    temperature_C: float = Field(
        ...,
        description="Temperature in Celsius; the registered model is withdrawn",
        **GALVANIC_TEMPERATURE_C.field_kwargs(),
    )
    pH: float = Field(
        ...,
        description="Solution pH; the registered model is withdrawn",
        **GALVANIC_PH.field_kwargs(),
    )
    chloride_mg_L: float = Field(
        ...,
        description="Chloride concentration in mg/L",
        **CHLORIDE_MG_L.field_kwargs(),
    )
    area_ratio_cathode_to_anode: float = Field(
        default=1.0,
        description="Cathode area / anode area",
        gt=0.0,
    )
    velocity_m_s: float = Field(
        default=0.0,
        description="Liquid velocity in m/s",
        ge=0.0,
    )
    dissolved_oxygen_mg_L: Optional[float] = Field(
        default=None,
        description="Dissolved oxygen in mg/L; no value is estimated by the withdrawn accessor",
        **DISSOLVED_OXYGEN_MG_L.field_kwargs(),
    )
    sulfide_mg_L: float = Field(
        default=0.0,
        description="Dissolved sulfide/H2S indicator in mg/L; the accessor is withdrawn",
        ge=0.0,
    )
    allow_auto_orient: bool = Field(
        default=False,
        description="If true, swap reversed anode/cathode inputs based on seawater galvanic series.",
    )

    @field_validator("anode_material", "cathode_material")
    @classmethod
    def validate_material(cls, value: str) -> str:
        return canonicalize_galvanic_material(value)


class GeneratePourbaixInput(StrictFiniteBaseModel):
    """Input for ``corrosion_generate_pourbaix``."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    element: str = Field(
        ...,
        description=f"Chemical element. Options: {', '.join(SUPPORTED_POURBAIX_ELEMENTS)}",
    )
    pH_range_min: float = Field(
        default=POURBAIX_PH.minimum,
        description="Minimum pH for diagram",
        **POURBAIX_PH.field_kwargs(),
    )
    pH_range_max: float = Field(
        default=POURBAIX_PH.maximum,
        description="Maximum pH for diagram",
        **POURBAIX_PH.field_kwargs(),
    )
    grid_points: int = Field(
        default=50,
        description="Number of pH samples in each retained water-stability line",
        ge=POURBAIX_GRID_POINTS.minimum,
        le=POURBAIX_GRID_POINTS.maximum,
    )

    @field_validator("element")
    @classmethod
    def validate_element(cls, value: str) -> str:
        canonical = value.title() if len(value) > 1 else value.upper()
        if canonical not in SUPPORTED_POURBAIX_ELEMENTS:
            raise ValueError(
                f"Element '{value}' not supported. Options: {list(SUPPORTED_POURBAIX_ELEMENTS)}"
            )
        return canonical

    @model_validator(mode="after")
    def validate_ranges(self) -> "GeneratePourbaixInput":
        if self.pH_range_min >= self.pH_range_max:
            raise ValueError("pH_range_min must be less than pH_range_max")
        return self


class ResolveMaterialIdentityInput(StrictFiniteBaseModel):
    """Input for ``corrosion_resolve_material_identity``."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    material: str = Field(
        ...,
        description="Material identifier or alias (e.g., 'SS316', '316L', '2205')",
        min_length=1,
        max_length=80,
    )


class GetServerInfoInput(StrictFiniteBaseModel):
    """Input for ``corrosion_get_server_info``."""

    model_config = ConfigDict(extra="forbid")


class GetPhreeqcStatusInput(StrictFiniteBaseModel):
    """Input for ``corrosion_get_phreeqc_status``."""

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Tier 0: Handbook Lookup Results
# ============================================================================

class MaterialCompatibility(StrictFiniteBaseModel):
    """Material compatibility screening result"""
    material: str = Field(..., description="Material identifier (e.g., 'CS', '316L')")
    environment: str = Field(..., description="Environment description")
    compatibility: Literal["acceptable", "marginal", "not_recommended"] = Field(..., description="Compatibility rating")
    typical_rate_range: Optional[tuple[float, float]] = Field(None, description="Typical corrosion rate range (mm/y)")
    notes: str = Field(..., description="Detailed notes from handbook")
    provenance: ProvenanceMetadata = Field(..., description="Source tracking")


class TypicalRateResult(StrictFiniteBaseModel):
    """Typical corrosion rate from handbook"""
    material: str
    environment: str
    rate_min_mm_per_y: float = Field(..., description="Minimum reported rate (mm/y)")
    rate_max_mm_per_y: float = Field(..., description="Maximum reported rate (mm/y)")
    rate_typical_mm_per_y: float = Field(..., description="Typical/median rate (mm/y)")
    conditions: str = Field(..., description="Conditions for reported rates")
    provenance: ProvenanceMetadata


class MechanismGuidance(StrictFiniteBaseModel):
    """Corrosion mechanism identification and guidance"""
    probable_mechanisms: List[str] = Field(..., description="Likely corrosion mechanisms")
    symptoms: List[str] = Field(..., description="Observed or expected symptoms")
    recommendations: List[str] = Field(..., description="Mitigation recommendations")
    tests_recommended: List[str] = Field(..., description="Recommended diagnostic tests")
    provenance: ProvenanceMetadata


# ============================================================================
# Tier 1: Chemistry Results
# ============================================================================

class SpeciationResult(StrictFiniteBaseModel):
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


# ============================================================================
# Tier 2: Mechanistic Corrosion Results
# ============================================================================

class CorrosionResult(StrictFiniteBaseModel):
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

    @field_validator('rate_p95_mm_per_y')
    @classmethod
    def p95_greater_than_median(cls, v, info):
        if 'rate_mm_per_y' in info.data and v < info.data['rate_mm_per_y']:
            raise ValueError('rate_p95_mm_per_y must be >= rate_mm_per_y')
        return v

    @field_validator('rate_p05_mm_per_y')
    @classmethod
    def p05_less_than_median(cls, v, info):
        if 'rate_mm_per_y' in info.data and v > info.data['rate_mm_per_y']:
            raise ValueError('rate_p05_mm_per_y must be <= rate_mm_per_y')
        return v


class CoatingBarrierResult(StrictFiniteBaseModel):
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


class CUIResult(StrictFiniteBaseModel):
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


# ============================================================================
# Phase 2: Advanced Mechanistic Results
# ============================================================================

class PourbaixDiagramResult(StrictFiniteBaseModel):
    """
    Boundary-only E-pH construction.

    Phase/passivity fields are intentionally absent until a complete Gibbs-energy
    minimization implementation is available.
    """
    element: str = Field(..., description="Element analyzed")
    temperature_C: float = Field(..., description="Temperature (°C)")
    boundaries: List[Dict[str, Any]] = Field(
        ...,
        description="Only row-level sourced reaction boundaries; currently empty",
    )

    # Water stability lines
    water_lines: Dict[str, List[tuple]] = Field(..., description="H2/O2 evolution lines {line_name: [(pH, E), ...]}")

    # Grid metadata
    pH_range: tuple[float, float] = Field(..., description="pH range (min, max)")
    grid_points: int = Field(..., description="Number of grid points")

    phase_selection_status: str = Field(..., description="Typed phase-selection suppression status")
    suppressed_reaction_rows: List[Dict[str, str]] = Field(default_factory=list)
    suppressed_nonredox_reaction_ids: List[str] = Field(default_factory=list)
    method_sources: List[Dict[str, Any]] = Field(default_factory=list)
    water_line_basis: Dict[str, Any] = Field(default_factory=dict)

    # Provenance
    provenance: ProvenanceMetadata


class MaterialIdentityResult(StrictFiniteBaseModel):
    """Canonical identity resolved from the local alias catalog."""

    material: str = Field(..., description="Canonical material identifier")
    uns_number: Optional[str] = Field(..., description="Catalog UNS designation")
    material_classification: str = Field(..., description="Catalog alloy class")
    identity_source: str = Field(..., description="Alias/identity catalog used")
    properties_returned: Literal[False] = Field(
        default=False,
        description="Always false: this accessor resolves identity only",
    )
    provenance: ProvenanceMetadata


class ToolRegistryEntry(StrictFiniteBaseModel):
    """Server tool registry entry."""

    name: str
    tier: str
    phase: str
    description: str
    typical_latency_sec: float
    availability_status: str = "available"
    requires_configuration: List[str] = Field(default_factory=list)
    output_scope: List[str] = Field(default_factory=list)
    suppressed_outputs: List[str] = Field(default_factory=list)


class ServerInfoResult(StrictFiniteBaseModel):
    """Output for ``corrosion_get_server_info``."""

    name: str
    version: str
    phase: str
    tool_count: int
    architecture: str
    total_tools_planned: int
    knowledge_base: str
    tool_registry: List[ToolRegistryEntry]
    implemented_tools: Dict[str, List[str]]
    supported_materials: List[str]
    advertised_materials: List[str]
    supported_elements_pourbaix: List[str]
    nrl_provenance: str
    github: str
    documentation: str


class PhreeqcDatabaseStatus(StrictFiniteBaseModel):
    """Loadability status for one PHREEQC database."""

    name: str
    ok: bool
    error: Optional[str] = Field(...)


class PhreeqcStatusResult(StrictFiniteBaseModel):
    """Output for ``corrosion_get_phreeqc_status``."""

    ready: bool
    viphreeqc_lib_path: Optional[str] = Field(...)
    viphreeqc_arch: Optional[str] = Field(...)
    databases_loadable: List[PhreeqcDatabaseStatus]
    error: Optional[str] = Field(...)


# ============================================================================
# Tier 3: Uncertainty Quantification Results
# ============================================================================

class MonteCarloResult(StrictFiniteBaseModel):
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
