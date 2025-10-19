"""
NORSOK M-506 CO₂ Corrosion Rate Calculation Model

Vendored from: https://github.com/dungnguyen2/norsokm506
License: MIT
Author: Dung Nguyen
Standard: NORSOK M-506 Rev. 2 (June 2005)

See LICENSE file in this directory for full license text.
"""

from .norsokm506_01 import (
    fpH_Cal,
    fpH_FixT,
    Shearstress,
    pHCalculator,
    Kt,
    Cal_Norsok,
)

__all__ = [
    "fpH_Cal",
    "fpH_FixT",
    "Shearstress",
    "pHCalculator",
    "Kt",
    "Cal_Norsok",
]
