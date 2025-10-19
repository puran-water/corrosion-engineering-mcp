"""
Core plugin architecture for corrosion engineering MCP server.

This module provides the foundational interfaces and infrastructure for:
- Plugin contracts (abstract base classes for all tiers)
- State management (context persistence across tool chains)
- Chemistry backend abstraction (PHREEQC adapter layer)
- Standardized response schemas (median/p05/p95 + provenance)
"""

from .interfaces import (
    ChemistryBackend,
    MechanisticModel,
    HandbookLookup,
    UncertaintyQuantifier,
)
from .schemas import (
    CorrosionResult,
    SpeciationResult,
    MaterialCompatibility,
    ProvenanceMetadata,
)
from .state_container import CorrosionContext
from .phreeqc_adapter import PhreeqcAdapter

__all__ = [
    "ChemistryBackend",
    "MechanisticModel",
    "HandbookLookup",
    "UncertaintyQuantifier",
    "CorrosionResult",
    "SpeciationResult",
    "MaterialCompatibility",
    "ProvenanceMetadata",
    "CorrosionContext",
    "PhreeqcAdapter",
]
