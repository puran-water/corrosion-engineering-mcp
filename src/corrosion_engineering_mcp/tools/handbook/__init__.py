"""
Tier 0: Handbook lookup tools using semantic search.

These tools wrap the corrosion_kb MCP server (2,980 vector chunks from
comprehensive corrosion handbooks) to provide fast screening (<0.5 sec) for:
- Material compatibility screening
- Typical corrosion rate ranges
- Mechanism identification and guidance
"""

from .material_screening import material_screening_query
from .typical_rates import typical_rates_query
from .mechanism_guidance import mechanism_guidance_query

__all__ = [
    "material_screening_query",
    "typical_rates_query",
    "mechanism_guidance_query",
]
