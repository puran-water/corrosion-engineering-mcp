"""
Tier 2 Mechanistic Tools - Physics-based corrosion models (1-5 seconds).

These tools implement detailed mechanistic models for:
- Galvanic corrosion (mixed-potential theory)
- Localized corrosion (pitting, crevice)
- Flow-accelerated corrosion (FAC)
- Microbiologically influenced corrosion (MIC)
- Coating barrier performance
- Corrosion under insulation (CUI)

All tools integrate with Phase 1 PHREEQC speciation for water chemistry.
"""

from .predict_galvanic_corrosion import predict_galvanic_corrosion
from .localized_corrosion import calculate_localized_corrosion, calculate_pren

__all__ = [
    "predict_galvanic_corrosion",
    "calculate_localized_corrosion",
    "calculate_pren",
]
