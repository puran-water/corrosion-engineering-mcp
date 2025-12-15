"""
NORSOK M-506 Internal Corrosion Rate Calculation Model

This module provides a wrapper around the authoritative NORSOK M-506 implementation
for CO₂ corrosion calculations in oil and gas pipelines.

DIRECT IMPORT from: https://github.com/dungnguyen2/norsokm506
License: MIT
Vendored in: external/norsokm506/

Standard Reference:
- NORSOK M-506 Rev. 2 (June 2005): "CO₂ Corrosion Rate Calculation Model"
- Published by: Standards Norway

Key Functions:
- fpH_Cal: Temperature-interpolated pH correction factor (Table A.1)
- fpH_FixT: pH factor at fixed temperature points
- Shearstress: Shear stress calculation for multiphase flow
- pHCalculator: In-situ pH calculation
- Kt: Temperature correction factor
- Cal_Norsok: Overall corrosion rate calculation per M-506

Author: Dung Nguyen (original), Vendored for corrosion-engineering-mcp
"""

import sys
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Add vendored NORSOK module to path
EXTERNAL_DIR = Path(__file__).parent.parent / "external" / "norsokm506"
if str(EXTERNAL_DIR) not in sys.path:
    sys.path.insert(0, str(EXTERNAL_DIR))

# Import NORSOK functions directly
try:
    from norsokm506_01 import (
        fpH_Cal,
        fpH_FixT,
        Shearstress,
        pHCalculator,
        Kt,
        Cal_Norsok,
    )
except ImportError as e:
    raise ImportError(
        f"Failed to import NORSOK M-506 module from {EXTERNAL_DIR}. "
        f"Ensure norsokm506_01.py is present in external/norsokm506/. "
        f"Error: {e}"
    )


def get_ph_correction_factor(temperature_C: float, pH: float) -> float:
    """
    Get NORSOK M-506 pH correction factor with temperature interpolation.

    Implements Table A.1 from NORSOK M-506 Rev. 2, Section 4.3.
    Uses linear interpolation between standard temperature points:
    5, 15, 20, 40, 60, 80, 90, 120, 150°C

    Args:
        temperature_C: Temperature in °C (range: 5-150°C)
        pH: pH value (range: 3.5-6.5, clamped with warning if outside)

    Returns:
        pH correction factor (dimensionless)

    Note:
        pH values outside 3.5-6.5 are clamped to the nearest bound with a warning,
        rather than raising ValueError. This allows upstream chemistry (e.g., PHREEQC)
        that produces out-of-range pH to still work with the NORSOK model.

    Example:
        >>> get_ph_correction_factor(25.0, 5.0)
        1.234  # Example value

    Reference:
        NORSOK M-506 Rev. 2, Annex A, Table A.1
        "Effect of pH on CO₂ Corrosion"
    """
    if not (5.0 <= temperature_C <= 150.0):
        raise ValueError(
            f"Temperature {temperature_C}°C out of NORSOK M-506 range (5-150°C)"
        )

    # Clamp pH to valid NORSOK range with warning (instead of raising)
    # This allows upstream chemistry that produces out-of-range pH to still work
    if pH < 3.5:
        logger.warning(
            f"pH {pH:.2f} below NORSOK M-506 minimum (3.5), clamping to 3.5"
        )
        pH = 3.5
    elif pH > 6.5:
        logger.warning(
            f"pH {pH:.2f} above NORSOK M-506 maximum (6.5), clamping to 6.5"
        )
        pH = 6.5

    return fpH_Cal(temperature_C, pH)


def get_chloride_threshold_norsok(
    temperature_C: float,
    pH: float,
    baseline_threshold_mg_L: float = 1000.0,
) -> float:
    """
    Calculate chloride threshold for pitting using NORSOK pH factor.

    **WARNING (Codex Review 2025-10-19): YELLOW FLAG**
    This function is NOT part of Phase 1 and is NOT used by any Phase 1 tools.

    **TODO for Phase 2 (Stainless Pitting Tools):**
    - Replace `baseline_threshold_mg_L=1000.0` with authoritative source
    - Options: ASTM G48, ISO 18070, or NACE standards for Cl⁻ thresholds
    - Document provenance for baseline value
    - Current value is placeholder only (engineering judgment)

    This replaces exponential heuristics in authoritative_materials_data.py
    with a calculation based on NORSOK M-506 standard pH correction factors.

    The pH factor represents how pH affects corrosion aggressiveness. We use
    it inversely: higher pH factor → lower threshold (more aggressive).

    Args:
        temperature_C: Temperature in °C
        pH: pH value (3.5-6.5 per NORSOK M-506 Table A.1)
        baseline_threshold_mg_L: Baseline Cl⁻ threshold at pH 6.0, 25°C (mg/L)
            **PLACEHOLDER VALUE** - No authoritative source (Codex Review flag)

    Returns:
        Chloride threshold in mg/L

    Note:
        This is an EXTENSION of NORSOK M-506 (which focuses on CO₂ corrosion)
        to chloride pitting. The pH factor is used as a relative scale.

        NORSOK M-506 Table A.1 only covers pH 3.5-6.5. For pH > 6.5, use
        the value at pH 6.5 (conservative assumption - corrosion decreases
        with higher pH).
    """
    # Clamp pH to NORSOK valid range (3.5-6.5)
    pH_clamped = max(3.5, min(pH, 6.5))

    fpH = get_ph_correction_factor(temperature_C, pH_clamped)

    # Normalize to pH 6.0 at 25°C (near-neutral in NORSOK range)
    fpH_ref = get_ph_correction_factor(25.0, 6.0)

    threshold = baseline_threshold_mg_L * (fpH_ref / fpH)

    return max(threshold, 10.0)  # Minimum 10 mg/L (always some risk)


def calculate_shear_stress(
    v_sg: float,
    v_sl: float,
    mass_g: float,
    mass_l: float,
    vol_g: float,
    vol_l: float,
    holdup: float,
    vis_g: float,
    vis_l: float,
    roughness: float,
    diameter: float,
) -> float:
    """
    Calculate wall shear stress for multiphase flow.

    Per NORSOK M-506 Section 4.4: "Shear Stress Effect"

    Args:
        v_sg: Superficial velocity of gas (m/s)
        v_sl: Superficial velocity of liquid (m/s)
        mass_g: Mass flow of gas (kg/hr)
        mass_l: Mass flow of liquid (kg/hr)
        vol_g: Volumetric flowrate of gas (m³/hr)
        vol_l: Volumetric flowrate of liquid (m³/hr)
        holdup: Liquid holdup (%)
        vis_g: Viscosity of gas (cp)
        vis_l: Viscosity of liquid (cp)
        roughness: Pipe roughness (m)
        diameter: Pipe diameter (m)

    Returns:
        Wall shear stress (Pa)

    Reference:
        NORSOK M-506 Rev. 2, Section 4.4, Equation (8)
    """
    return Shearstress(
        v_sg, v_sl, mass_g, mass_l, vol_g, vol_l,
        holdup, vis_g, vis_l, roughness, diameter
    )


def calculate_insitu_pH(
    temperature_C: float,
    pressure_bar: float,
    co2_partial_pressure_bar: float,
    bicarbonate_mg_L: float,
    ionic_strength_mg_L: float,
    calc_iterations: int = 2,
) -> float:
    """
    Calculate in-situ pH from water chemistry and operating conditions.

    Per NORSOK M-506 Section 3.2: "pH Calculation"

    Args:
        temperature_C: Temperature (°C)
        pressure_bar: Total pressure (bar)
        co2_partial_pressure_bar: CO₂ partial pressure (bar)
        bicarbonate_mg_L: Bicarbonate concentration (mg/L as HCO₃⁻)
        ionic_strength_mg_L: Ionic strength (mg/L)
        calc_iterations: Number of pH calculation iterations (default: 2)
                        1 = unsaturated water, 2 = saturated with FeCO₃

    Returns:
        In-situ pH

    Reference:
        NORSOK M-506 Rev. 2, Section 3.2, Equations (1)-(3)

    Note:
        FIXED BUG: CalcOfpH must be integer (iteration count), not boolean.
        Per norsokm506_01.py:105, the parameter is used in range(1, CalcOfpH).
    """
    return pHCalculator(
        temperature_C,
        pressure_bar,
        co2_partial_pressure_bar,
        bicarbonate_mg_L,
        ionic_strength_mg_L,
        CalcOfpH=calc_iterations  # FIXED: Integer, not boolean
    )


def calculate_norsok_corrosion_rate(
    co2_fraction: float,
    pressure_bar: float,
    temperature_C: float,
    v_sg: float,
    v_sl: float,
    mass_g: float,
    mass_l: float,
    vol_g: float,
    vol_l: float,
    holdup: float,
    vis_g: float,
    vis_l: float,
    roughness: float,
    diameter: float,
    pH_in: float = 0.0,
    bicarbonate_mg_L: float = 0.0,
    ionic_strength_mg_L: float = 0.0,
    calc_iterations: int = 2,
) -> float:
    """
    Calculate CO₂ corrosion rate per NORSOK M-506 complete model.

    This wrapper handles pH correctly: if pH_in > 0, uses it directly; if pH_in = 0,
    calculates from chemistry. The vendored Cal_Norsok ALWAYS recalculates pH, so we
    bypass it when user supplies pH.

    Args:
        co2_fraction: CO₂ mole fraction in gas phase (dimensionless, 0-1)
        pressure_bar: Total pressure (bar)
        temperature_C: Temperature (°C)
        v_sg: Superficial velocity of gas (m/s)
        v_sl: Superficial velocity of liquid (m/s)
        mass_g: Mass flow of gas (kg/hr)
        mass_l: Mass flow of liquid (kg/hr)
        vol_g: Volumetric flowrate of gas (m³/hr)
        vol_l: Volumetric flowrate of liquid (m³/hr)
        holdup: Liquid holdup (%)
        vis_g: Viscosity of gas (cp)
        vis_l: Viscosity of liquid (cp)
        roughness: Pipe roughness (m)
        diameter: Pipe diameter (m)
        pH_in: Input pH (default 0 = calculate from chemistry)
        bicarbonate_mg_L: Bicarbonate concentration (mg/L as HCO₃⁻)
        ionic_strength_mg_L: Ionic strength (mg/L)
        calc_iterations: Number of pH calculation iterations (1 or 2)

    Returns:
        Corrosion rate (mm/year)

    Reference:
        NORSOK M-506 Rev. 2, Section 4, Equation (4)

    Note:
        FIXED BUG #1: Cal_Norsok requires 18 parameters, not 6.
        FIXED BUG #2: Cal_Norsok ignores fPH_In and recalculates pH at line 168-169.
        This wrapper bypasses Cal_Norsok when pH is user-supplied to honor pH_in.
    """
    import math
    from norsokm506_01 import FugacityofCO2

    # If user provides pH, calculate corrosion rate directly without vendored function
    # (vendored Cal_Norsok always recalculates pH, ignoring fPH_In parameter)
    if pH_in > 0:
        # Calculate components directly per NORSOK M-506 Eq. 4
        co2_fugacity = FugacityofCO2(co2_fraction, pressure_bar, temperature_C)
        shear_stress = calculate_shear_stress(
            v_sg, v_sl, mass_g, mass_l, vol_g, vol_l,
            holdup, vis_g, vis_l, roughness, diameter
        )
        fpH = get_ph_correction_factor(temperature_C, pH_in)
        kt = Kt(temperature_C)

        if co2_fraction == 0:
            return 0.0

        # NORSOK M-506 Equation (4):
        # CR = Kt × fCO₂^0.62 × (τ/19)^(0.146 + 0.0324×log₁₀(fCO₂)) × fpH
        corrosion_rate = (
            kt
            * co2_fugacity ** 0.62
            * (shear_stress / 19) ** (0.146 + 0.0324 * math.log10(co2_fugacity))
            * fpH
        )
        return corrosion_rate

    else:
        # pH not provided - use vendored function which calculates pH from chemistry
        # Note: fPH_In=0 signals Cal_Norsok to calculate pH
        return Cal_Norsok(
            co2_fraction,
            pressure_bar,
            temperature_C,
            v_sg,
            v_sl,
            mass_g,
            mass_l,
            vol_g,
            vol_l,
            holdup,
            vis_g,
            vis_l,
            roughness,
            diameter,
            0.0,  # fPH_In=0 signals: calculate pH from chemistry
            bicarbonate_mg_L,
            ionic_strength_mg_L,
            calc_iterations,
        )


# Expose all NORSOK functions for advanced use
__all__ = [
    # Wrapper functions (recommended)
    "get_ph_correction_factor",
    "get_chloride_threshold_norsok",
    "calculate_shear_stress",
    "calculate_insitu_pH",
    "calculate_norsok_corrosion_rate",
    # Direct NORSOK functions (advanced use)
    "fpH_Cal",
    "fpH_FixT",
    "Shearstress",
    "pHCalculator",
    "Kt",
    "Cal_Norsok",
]
