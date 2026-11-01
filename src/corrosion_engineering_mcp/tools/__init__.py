"""Corrosion helper package.

Registered capability and suppression status are reported by
``corrosion_get_server_info``. Importing a legacy helper name here does not
override its runtime gate; notably, galvanic prediction is withdrawn and the
Pourbaix helper returns boundaries without phase/passivity selection.
"""

from corrosion_engineering_mcp.core.engineering_utils_bridge import ensure_engineering_utils_importable

ensure_engineering_utils_importable()

# Phase 1 Tools (Tier 1 & 2 - Chemistry and Mechanistic)
try:
    from corrosion_engineering_mcp.tools.mechanistic.aerated_chloride_corrosion import predict_aerated_chloride_corrosion
except ImportError:
    predict_aerated_chloride_corrosion = None

try:
    from corrosion_engineering_mcp.tools.chemistry.co2_corrosion import predict_co2_corrosion
except ImportError:
    predict_co2_corrosion = None

try:
    from corrosion_engineering_mcp.tools.chemistry.water_speciation import calculate_water_speciation
except ImportError:
    calculate_water_speciation = None

# Phase 2 Tools (Tier 2 - Galvanic Corrosion and Pourbaix)
from corrosion_engineering_mcp.tools.mechanistic.predict_galvanic_corrosion import predict_galvanic_corrosion
from corrosion_engineering_mcp.tools.chemistry.calculate_pourbaix import calculate_pourbaix

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
