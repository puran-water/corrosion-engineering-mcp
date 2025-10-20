"""
MCP tool implementations for corrosion engineering.

Tools are organized by tier:
- Tier 0: Handbook lookup (semantic search)
- Tier 1: Chemistry (speciation, Pourbaix)
- Tier 2: Mechanistic physics (uniform, galvanic, barriers, CUI, MIC, FAC)
- Tier 3: Uncertainty quantification (Monte Carlo)
"""

# Phase 1 Tools (Tier 1 & 2 - Chemistry and Mechanistic)
try:
    from tools.mechanistic.aerated_chloride_corrosion import predict_aerated_chloride_corrosion
except ImportError:
    predict_aerated_chloride_corrosion = None

try:
    from tools.chemistry.co2_corrosion import predict_co2_corrosion
except ImportError:
    predict_co2_corrosion = None

try:
    from tools.chemistry.water_speciation import calculate_water_speciation
except ImportError:
    calculate_water_speciation = None

# Phase 2 Tools (Tier 2 - Galvanic Corrosion and Pourbaix)
from tools.mechanistic.predict_galvanic_corrosion import predict_galvanic_corrosion
from tools.chemistry.calculate_pourbaix import calculate_pourbaix

__all__ = [
    # Phase 2 (always available)
    "predict_galvanic_corrosion",
    "calculate_pourbaix",
]

# Add Phase 1 tools if available
if predict_aerated_chloride_corrosion is not None:
    __all__.append("predict_aerated_chloride_corrosion")
if predict_co2_corrosion is not None:
    __all__.append("predict_co2_corrosion")
if calculate_water_speciation is not None:
    __all__.append("calculate_water_speciation")
