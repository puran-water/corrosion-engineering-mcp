"""
Core plugin architecture for corrosion engineering MCP server.

This module provides the foundational interfaces and infrastructure for:
- Plugin contracts (abstract base classes for all tiers)
- State management (context persistence across tool chains)
- Chemistry backend abstraction (PHREEQC adapter layer)
- Standardized response schemas (median/p05/p95 + provenance)
"""

from .engineering_utils_bridge import ensure_engineering_utils_importable

ensure_engineering_utils_importable()

from .interfaces import (
    ChemistryBackend,
    MechanisticModel,
    HandbookLookup,
    UncertaintyQuantifier,
)
from .schemas import (
    AssessGalvanicInput,
    CorrosionResult,
    GeneratePourbaixInput,
    ResolveMaterialIdentityInput,
    GetServerInfoInput,
    IdentifyMechanismInput,
    SpeciationResult,
    MaterialCompatibility,
    ProvenanceMetadata,
    QueryTypicalRatesInput,
    ScreenMaterialsInput,
    ServerInfoResult,
)
from .state_container import CorrosionContext
from .phreeqc_runtime import PHREEQCBackendUnavailableError, get_phreeqc_engine_status

__all__ = [
    "ChemistryBackend",
    "MechanisticModel",
    "HandbookLookup",
    "UncertaintyQuantifier",
    "AssessGalvanicInput",
    "CorrosionResult",
    "GeneratePourbaixInput",
    "ResolveMaterialIdentityInput",
    "GetServerInfoInput",
    "IdentifyMechanismInput",
    "SpeciationResult",
    "MaterialCompatibility",
    "ProvenanceMetadata",
    "QueryTypicalRatesInput",
    "ScreenMaterialsInput",
    "ServerInfoResult",
    "CorrosionContext",
    "PHREEQCBackendUnavailableError",
    "get_phreeqc_engine_status",
]
