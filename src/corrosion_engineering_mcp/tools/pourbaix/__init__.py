"""Table-driven Pourbaix boundary engine with phase selection suppressed."""

from .contract import PourbaixPointInput
from .engine import (
    PourbaixPhaseSelectionSuppressedError,
    UnsupportedElementError,
    compute_stable_phase,
    generate_pourbaix_diagram,
)

__all__ = [
    "PourbaixPointInput",
    "PourbaixPhaseSelectionSuppressedError",
    "UnsupportedElementError",
    "compute_stable_phase",
    "generate_pourbaix_diagram",
]
