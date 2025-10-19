"""
Tier 1 Chemistry Tools - PHREEQC-based aqueous chemistry calculations.

These tools provide fast (~1 second) chemistry calculations for:
- Aqueous speciation and pH calculation
- Scaling tendency prediction (LSI, RSI, Puckorius)
- Langelier index for calcium carbonate scaling

All tools use the PHREEQCBackend for thread-safe PHREEQC integration.
"""

from .run_speciation import run_phreeqc_speciation
from .predict_scaling import predict_scaling_tendency
from .langelier_index import calculate_langelier_index

__all__ = [
    "run_phreeqc_speciation",
    "predict_scaling_tendency",
    "calculate_langelier_index",
]
