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
- fpH_Cal: Temperature-interpolated pH correction factor (Table 2)
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
EXTERNAL_DIR = Path(__file__).parent.parent / 'external' / 'norsokm506'
if str(EXTERNAL_DIR) not in sys.path:
    sys.path.insert(0, str(EXTERNAL_DIR))
try:
    from norsokm506_01 import fpH_Cal, fpH_FixT, Shearstress, pHCalculator, Kt, Cal_Norsok, FugacityofCO2, ResolvedCorrosionRate, NorsokM506EnvelopeError
except ImportError as e:
    raise ImportError(f'Failed to import NORSOK M-506 module from {EXTERNAL_DIR}. Ensure norsokm506_01.py is present in external/norsokm506/. Error: {e}')
NORSOK_VALIDATION_SOURCE = {'doi': '10.1002/prs.12364', 'openalex_id': 'W4224286856', 'title': 'Probabilistic failure assessment of oil pipelines due to internal corrosion', 'authors': ['Sina Khakzad', 'Ming Yang', 'Ali Lohi', 'Nima Khakzad'], 'table': 'Table 2', 'content_location': 'Table 2', 'content_support': 'Table 2 publishes four NORSOK M-506 input/output cases. Rows 2-4 provide rounded independent transcription anchors that this wrapper reproduces within 1.3%.', 'content_limit': 'The cases are rounded transcription checks, not an independent model-accuracy validation dataset. The row-1 discrepancy remains unresolved and is explicitly excluded from a passing reproduction claim.'}
NORSOK_DOGAN_VALIDATION_SOURCE = {'doi': '10.17798/bitlisfen.1191507', 'openalex_id': 'W4353013799', 'title': 'Mathematical Modelling of CO2 corrosion with NORSOK M 506', 'authors': ['B. Dogan', 'Ayla Altınten'], 'content_location': 'Table 5 and accompanying Results text', 'content_support': 'For the selected 20 C, pH 4, 1 bar CO2 and 2 m/s case, the paper reports a NORSOK result of 5.08 mm/y and an experimental result of 1.33 mm/y.', 'content_limit': "The exact NORSOK number is used only as a transcription anchor: pCO2 equals total pressure and lies outside this wrapper's declared Table-3 envelope; plot-read Figures 2-5 are excluded, and experimental agreement is not made a gate.", 'published_output_scope': 'sole exact prose NORSOK rate; raw transcription anchor only because pCO2 equals total pressure and is outside Table 3; Figure 2-5 plot-only values excluded', 'model_uncertainty_context': 'The paper reports 5.08 mm/y from NORSOK against 1.33 mm/y measured for the selected case and says the model was nearly sixfold higher than experiment overall. Experimental agreement is not a pass/fail gate.'}

class NorsokChemistryInputRequiredError(ValueError):
    """The exported wrapper cannot infer an in-situ pH from zero defaults."""

    def __init__(self, missing: list[str]) -> None:
        self.code = 'CHEMISTRY_INPUT_REQUIRED'
        self.missing = missing
        super().__init__(f'CHEMISTRY_INPUT_REQUIRED: supply a positive pH_in, or explicitly supply both bicarbonate_mg_L and ionic_strength_mg_L to calculate in-situ pH; missing {', '.join(missing)}.')

class NorsokChlorideExtensionSuppressedError(RuntimeError):
    """M-506's CO2 pH factor cannot be repurposed as a pitting limit."""

def get_ph_correction_factor(temperature_C: float, pH: float) -> float:
    """
    Get NORSOK M-506 pH correction factor with temperature interpolation.

    Implements Table 2 from NORSOK M-506 Rev. 2, section 5.2.
    Uses linear interpolation between standard temperature points:
    5, 15, 20, 40, 60, 80, 90, 120, 150°C

    Args:
        temperature_C: Temperature in °C (range: 5-150°C)
        pH: pH value (range: 3.5-6.5; values outside raise)

    Returns:
        pH correction factor (dimensionless)

    Example:
        >>> round(get_ph_correction_factor(25.0, 5.0), 4)
        0.782

    Reference:
        NORSOK M-506 Rev. 2, Table 2, "pH correction factor".
    """
    if not 5.0 <= temperature_C <= 150.0:
        raise ValueError(f'Temperature {temperature_C}°C out of NORSOK M-506 range (5-150°C)')
    if not 3.5 <= pH <= 6.5:
        raise ValueError(f'pH {pH} out of NORSOK M-506 correction-factor range (3.5-6.5)')
    return fpH_Cal(temperature_C, pH)

def get_chloride_threshold_norsok(temperature_C: float, pH: float, baseline_threshold_mg_L: float=1000.0) -> float:
    """Fail closed: M-506 defines no stainless chloride-pitting service limit.

    The former implementation inverted the M-506 CO2 pH factor around an
    unsourced 1000 mg/L baseline and clamped the result to another unsourced
    minimum. Retaining the import name avoids a silent API disappearance, but
    no numeric result is available through this accessor.
    """
    del temperature_C, pH, baseline_threshold_mg_L
    raise NorsokChlorideExtensionSuppressedError('NORSOK_M506_CHLORIDE_EXTENSION_SUPPRESSED')

def calculate_shear_stress(v_sg: float, v_sl: float, mass_g: float, mass_l: float, vol_g: float, vol_l: float, holdup: float, vis_g: float, vis_l: float, roughness: float, diameter: float) -> float:
    """
    Calculate wall shear stress for multiphase flow.

    Per NORSOK M-506 Rev. 2 Section 8.4, Equations (20)-(21).

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
        NORSOK M-506 Rev. 2, Section 8.4, Equations (20)-(21)
    """
    return Shearstress(v_sg, v_sl, mass_g, mass_l, vol_g, vol_l, holdup, vis_g, vis_l, roughness, diameter)

def calculate_insitu_pH(temperature_C: float, pressure_bar: float, co2_fugacity_bar: float, bicarbonate_mg_L: float, ionic_strength_mg_L: float, calc_iterations: int=2) -> float:
    """
    Calculate in-situ pH from water chemistry and operating conditions.

    Per NORSOK M-506 Rev. 2 Section 8.2, Equations (11)-(18).

    Args:
        temperature_C: Temperature (°C)
        pressure_bar: Total pressure (bar)
        co2_fugacity_bar: CO₂ fugacity (bar), as required below equations
            (14)-(15); partial pressure is not interchangeable here
        bicarbonate_mg_L: Bicarbonate concentration (mg/L as HCO₃⁻)
        ionic_strength_mg_L: Ionic strength (mg/L)
        calc_iterations: Number of pH calculation iterations (default: 2)
                        1 = unsaturated water, 2 = saturated with FeCO₃

    Returns:
        In-situ pH

    Reference:
        NORSOK M-506 Rev. 2, Sections 8.1-8.2, Equations (4)-(18)

    Note:
        FIXED BUG: CalcOfpH must be integer (iteration count), not boolean.
        Per norsokm506_01.py:105, the parameter is used in range(1, CalcOfpH).
    """
    return pHCalculator(temperature_C, pressure_bar, co2_fugacity_bar, bicarbonate_mg_L, ionic_strength_mg_L, CalcOfpH=calc_iterations)

def calculate_norsok_corrosion_rate(co2_fraction: float, pressure_bar: float, temperature_C: float, v_sg: float, v_sl: float, mass_g: float, mass_l: float, vol_g: float, vol_l: float, holdup: float, vis_g: float, vis_l: float, roughness: float, diameter: float, pH_in: Optional[float]=None, bicarbonate_mg_L: Optional[float]=None, ionic_strength_mg_L: Optional[float]=None, calc_iterations: int=2) -> float:
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
        pH_in: Explicit input pH. Omit (or pass the legacy zero sentinel) only
            when both chemistry inputs below are explicitly supplied.
        bicarbonate_mg_L: Explicit bicarbonate concentration (mg/L as HCO₃⁻)
            used when pH is calculated. There is no zero default.
        ionic_strength_mg_L: Explicit NaCl-equivalent salinity (mg/L) used when
            pH is calculated. There is no zero default.
        calc_iterations: Number of pH calculation iterations (1 or 2)

    Returns:
        Corrosion rate (mm/year)

    Reference:
        NORSOK M-506 Rev. 2, Section 5.2, Equations (1)-(3), with
        fugacity from Section 8.1, Equations (4)-(8), and wall shear from
        Section 8.4, Equations (20)-(21).

    Note:
        FIXED BUG #1: Cal_Norsok requires 18 parameters, not 6.
        FIXED BUG #2: Cal_Norsok ignores fPH_In and recalculates pH at line 168-169.
        This wrapper bypasses Cal_Norsok when pH is user-supplied to honor pH_in.
    """
    if not 1.0 <= pressure_bar <= 1000.0:
        raise NorsokM506EnvelopeError('total_pressure_bar_abs', pressure_bar, 1.0, 1000.0, 'NORSOK M-506 Table 3')
    if not 20.0 <= temperature_C <= 150.0:
        raise NorsokM506EnvelopeError('temperature_C', temperature_C, 20.0, 150.0, 'NORSOK M-506 Table 3')
    if not 0.0 <= co2_fraction <= 1.0:
        raise ValueError('co2_fraction must be between zero and one')
    if co2_fraction == 1.0:
        raise NorsokM506EnvelopeError('co2_partial_pressure_bar', pressure_bar, 0.0, pressure_bar, 'NORSOK M-506 Table 3', maximum_inclusive=False)
    if co2_fraction == 0:
        return 0.0
    if pH_in is not None and pH_in < 0.0:
        raise ValueError('pH_in must be positive, or zero/None to calculate from chemistry')
    calculate_pH = pH_in is None or pH_in == 0.0
    if calculate_pH:
        missing = [name for name, value in (('bicarbonate_mg_L', bicarbonate_mg_L), ('ionic_strength_mg_L', ionic_strength_mg_L)) if value is None]
        if missing:
            raise NorsokChemistryInputRequiredError(missing)
    co2_fugacity_bar = FugacityofCO2(co2_fraction, pressure_bar, temperature_C)
    resolved_pH = pH_in
    if calculate_pH:
        assert bicarbonate_mg_L is not None
        assert ionic_strength_mg_L is not None
        resolved_pH = calculate_insitu_pH(temperature_C=temperature_C, pressure_bar=pressure_bar, co2_fugacity_bar=co2_fugacity_bar, bicarbonate_mg_L=bicarbonate_mg_L, ionic_strength_mg_L=ionic_strength_mg_L, calc_iterations=calc_iterations)
    shear_stress = calculate_shear_stress(v_sg, v_sl, mass_g, mass_l, vol_g, vol_l, holdup, vis_g, vis_l, roughness, diameter)
    return calculate_resolved_norsok_rate(temperature_C=temperature_C, pressure_bar=pressure_bar, co2_partial_pressure_bar=co2_fraction * pressure_bar, shear_stress_Pa=shear_stress, pH=float(resolved_pH))

def calculate_resolved_norsok_rate(*, temperature_C: float, pressure_bar: float, co2_partial_pressure_bar: float, shear_stress_Pa: float, pH: float, inhibitor_efficiency_percent: float=0.0) -> float:
    """Calculate M-506 rate once pH and wall shear have been resolved.

    This narrow entrypoint mirrors the quantities published in independent
    model-comparison tables, so it can be validated without reverse-engineering
    a paper's unreported pipe geometry or fluid-property assumptions.
    """
    if not 1.0 <= pressure_bar <= 1000.0:
        raise NorsokM506EnvelopeError('total_pressure_bar_abs', pressure_bar, 1.0, 1000.0, 'NORSOK M-506 Table 3')
    if not 20.0 <= temperature_C <= 150.0:
        raise NorsokM506EnvelopeError('temperature_C', temperature_C, 20.0, 150.0, 'NORSOK M-506 Table 3')
    if not 0.0 <= co2_partial_pressure_bar < pressure_bar:
        raise NorsokM506EnvelopeError('co2_partial_pressure_bar', co2_partial_pressure_bar, 0.0, pressure_bar, 'NORSOK M-506 Table 3', maximum_inclusive=False)
    if not 0.0 <= inhibitor_efficiency_percent <= 100.0:
        raise ValueError('inhibitor efficiency must lie between 0 and 100 percent')
    co2_fraction = co2_partial_pressure_bar / pressure_bar
    fugacity_bar = FugacityofCO2(co2_fraction, pressure_bar, temperature_C)
    uninhibited_rate = ResolvedCorrosionRate(fugacity_bar, shear_stress_Pa, temperature_C, pH)
    return uninhibited_rate * (1.0 - inhibitor_efficiency_percent / 100.0)
__all__ = ['get_ph_correction_factor', 'get_chloride_threshold_norsok', 'calculate_shear_stress', 'calculate_insitu_pH', 'calculate_norsok_corrosion_rate', 'calculate_resolved_norsok_rate', 'NORSOK_VALIDATION_SOURCE', 'NORSOK_DOGAN_VALIDATION_SOURCE', 'NorsokM506EnvelopeError', 'NorsokChemistryInputRequiredError', 'NorsokChlorideExtensionSuppressedError', 'fpH_Cal', 'fpH_FixT', 'Shearstress', 'pHCalculator', 'Kt', 'Cal_Norsok']
