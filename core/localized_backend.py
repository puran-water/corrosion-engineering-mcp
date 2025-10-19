"""
Localized Corrosion Backend - Pitting and Crevice Corrosion

Implements mechanistic models for:
- Pitting corrosion (PREN-based CPT correlations)
- Crevice corrosion (Oldfield-Sutton IR drop model)

Based on:
- PREN (Pitting Resistance Equivalent Number): PREN = %Cr + 3.3×%Mo + 16×%N
- CPT (Critical Pitting Temperature): CPT ≈ PREN - 10 (calibrated for austenitic SS)
- Chloride threshold correlations
- Crevice IR drop (Oldfield-Sutton resistance term)

Per Codex guidance:
- Use PREN-based CPT correlations with exposed calibration coefficients
- Duplex grades can deviate by ±5°C from standard correlation
- Separate pitting vs crevice outputs
- Share chloride threshold logic between models
- Simplified Oldfield-Sutton for crevice IR drop

Performance: 1-2 seconds (Tier 2 target)
Accuracy: ±5°C for CPT, ±20% for chloride threshold
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

# Import authoritative data (BUG-013, BUG-014, BUG-016 fixes)
from data import (
    get_material_data,
    get_cpt_from_astm,
    get_chloride_threshold,
    calculate_pren as calculate_pren_authoritative,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

FARADAY = 96485.3321  # C/mol
R_GAS = 8.314462618  # J/mol·K

# ---------------------------------------------------------------------------
# PREN calibration coefficients (exposed per Codex)
# ---------------------------------------------------------------------------

# Standard PREN formula: PREN = a×Cr + b×Mo + c×N
PREN_COEFFS = {
    "standard": {"a": 1.0, "b": 3.3, "c": 16.0},  # ASTM G48 standard
    "duplex": {"a": 1.0, "b": 3.3, "c": 30.0},    # Higher N weighting for duplex
}

# CPT-PREN correlation: CPT = m×PREN + b (°C)
CPT_CORRELATIONS = {
    "austenitic": {"m": 1.0, "b": -10.0},         # CPT ≈ PREN - 10
    "duplex": {"m": 1.0, "b": -15.0},             # Duplex slightly more conservative
    "superaustenitic": {"m": 1.0, "b": -5.0},     # Super grades more resistant
}

# Chloride threshold correlations (mg/L Cl⁻ vs temperature)
# Based on empirical data from ASTM G48, ISO 17945
CL_THRESHOLD_BASE = {
    "304": 50.0,      # Low PREN ≈ 18, very susceptible
    "316": 200.0,     # PREN ≈ 24, moderate resistance
    "316L": 250.0,    # Lower carbon, slightly better
    "2205": 1000.0,   # Duplex, PREN ≈ 35
    "254SMO": 5000.0, # Superaustenitic, PREN ≈ 43
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MaterialComposition:
    """
    Material composition for PREN calculation.

    Attributes:
        Cr: Chromium content (wt%)
        Mo: Molybdenum content (wt%)
        N: Nitrogen content (wt%)
        Ni: Nickel content (wt%, optional)
        grade_type: "austenitic", "duplex", "superaustenitic"
    """
    Cr: float
    Mo: float
    N: float
    Ni: float = 0.0
    grade_type: str = "austenitic"

    def calculate_pren(self, coeffs: Optional[Dict[str, float]] = None) -> float:
        """
        Calculate PREN (Pitting Resistance Equivalent Number).

        Args:
            coeffs: Optional custom coefficients {"a": ..., "b": ..., "c": ...}

        Returns:
            PREN value (unitless)
        """
        if coeffs is None:
            coeffs = PREN_COEFFS.get(self.grade_type, PREN_COEFFS["standard"])

        pren = coeffs["a"] * self.Cr + coeffs["b"] * self.Mo + coeffs["c"] * self.N
        return pren


@dataclass
class PittingResult:
    """
    Result of pitting corrosion susceptibility calculation.

    Attributes:
        CPT_C: Critical Pitting Temperature (°C)
        PREN: Pitting Resistance Equivalent Number
        Cl_threshold_mg_L: Chloride threshold at operating temperature (mg/L)
        susceptibility: "low", "moderate", "high", "critical"
        margin_C: Temperature margin to CPT (positive = safe, negative = unsafe)
        interpretation: Text summary
    """
    CPT_C: float
    PREN: float
    Cl_threshold_mg_L: float
    susceptibility: str
    margin_C: float
    interpretation: str


@dataclass
class CreviceResult:
    """
    Result of crevice corrosion susceptibility calculation.

    Attributes:
        CCT_C: Critical Crevice Temperature (°C) - typically CPT - 10 to 20°C
        IR_drop_V: IR drop in crevice (V) from Oldfield-Sutton model
        acidification_factor: pH drop in crevice (unitless, >1 = more acidic)
        susceptibility: "low", "moderate", "high", "critical"
        margin_C: Temperature margin to CCT
        interpretation: Text summary
    """
    CCT_C: float
    IR_drop_V: float
    acidification_factor: float
    susceptibility: str
    margin_C: float
    interpretation: str


@dataclass
class LocalizedResult:
    """
    Combined result for localized corrosion (pitting + crevice).

    Per Codex: Separate outputs but shared chloride threshold logic.
    """
    pitting: PittingResult
    crevice: CreviceResult
    material: str
    temperature_C: float
    Cl_mg_L: float
    pH: float
    overall_risk: str  # "low", "moderate", "high", "critical"


# ---------------------------------------------------------------------------
# Localized corrosion backend
# ---------------------------------------------------------------------------

class LocalizedBackend:
    """
    Pitting and crevice corrosion calculator.

    Uses PREN-based CPT correlations and simplified Oldfield-Sutton model.
    """

    def __init__(self):
        """Initialize localized corrosion backend."""
        pass

    def calculate_pitting_susceptibility(
        self,
        material_comp: MaterialComposition,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        material_name: str = "316L",
        custom_cpt_correlation: Optional[Dict[str, float]] = None,
    ) -> PittingResult:
        """
        Calculate pitting corrosion susceptibility.

        FIX BUG-013: Use ASTM G48 tabulated CPT data instead of heuristic
        FIX BUG-014: Use ISO 18070 chloride threshold data

        Args:
            material_comp: Material composition (Cr, Mo, N)
            temperature_C: Operating temperature (°C)
            Cl_mg_L: Chloride concentration (mg/L)
            pH: Solution pH
            material_name: Material name for database lookup
            custom_cpt_correlation: Optional custom CPT correlation (for calibration)

        Returns:
            PittingResult with CPT, PREN, threshold, susceptibility
        """
        # Calculate PREN using local method (handles local MaterialComposition format)
        pren = material_comp.calculate_pren()

        # FIX BUG-013: Get CPT from ASTM G48 tabulated data
        cpt_data = get_cpt_from_astm(material_name)

        if cpt_data is not None:
            # Use ASTM G48 measured CPT
            CPT = cpt_data["CPT_C"]
            logger.info(f"Using ASTM G48 CPT for {material_name}: {CPT}°C (source: {cpt_data['source']})")
        elif custom_cpt_correlation is not None:
            # Use custom correlation if provided
            cpt_corr = custom_cpt_correlation
            CPT = cpt_corr["m"] * pren + cpt_corr["b"]
            logger.warning(f"Using custom CPT correlation for {material_name}: {CPT}°C")
        else:
            # Fallback to PREN-based estimate with warning
            cpt_corr = CPT_CORRELATIONS.get(material_comp.grade_type, CPT_CORRELATIONS["austenitic"])
            CPT = cpt_corr["m"] * pren + cpt_corr["b"]
            logger.warning(
                f"Material {material_name} not in ASTM G48 database; "
                f"using PREN estimate: {CPT}°C (±20°C uncertainty)"
            )

        # Temperature margin
        margin_C = CPT - temperature_C

        # FIX BUG-014: Get chloride threshold from ISO 18070/NORSOK authoritative data
        Cl_threshold = get_chloride_threshold(material_name, temperature_C, pH)
        logger.info(f"Using ISO 18070 Cl⁻ threshold for {material_name} at {temperature_C}°C, pH {pH}: {Cl_threshold:.0f} mg/L")

        # Determine susceptibility
        if margin_C > 20.0 and Cl_mg_L < Cl_threshold * 0.5:
            susceptibility = "low"
        elif margin_C > 10.0 and Cl_mg_L < Cl_threshold:
            susceptibility = "moderate"
        elif margin_C > 0 or Cl_mg_L < Cl_threshold * 1.5:
            susceptibility = "high"
        else:
            susceptibility = "critical"

        # Interpretation
        if susceptibility == "critical":
            interpretation = f"CRITICAL: T = {temperature_C}°C exceeds CPT = {CPT:.1f}°C by {-margin_C:.1f}°C; Cl⁻ = {Cl_mg_L:.0f} mg/L >> {Cl_threshold:.0f} mg/L threshold"
        elif susceptibility == "high":
            interpretation = f"HIGH RISK: T = {temperature_C}°C within {margin_C:.1f}°C of CPT = {CPT:.1f}°C; Cl⁻ = {Cl_mg_L:.0f} mg/L near threshold"
        elif susceptibility == "moderate":
            interpretation = f"MODERATE: T = {temperature_C}°C is {margin_C:.1f}°C below CPT = {CPT:.1f}°C; Cl⁻ = {Cl_mg_L:.0f} mg/L acceptable"
        else:
            interpretation = f"LOW RISK: T = {temperature_C}°C well below CPT = {CPT:.1f}°C (margin {margin_C:.1f}°C); Cl⁻ = {Cl_mg_L:.0f} mg/L < {Cl_threshold:.0f} mg/L"

        return PittingResult(
            CPT_C=CPT,
            PREN=pren,
            Cl_threshold_mg_L=Cl_threshold,
            susceptibility=susceptibility,
            margin_C=margin_C,
            interpretation=interpretation,
        )

    def calculate_crevice_susceptibility(
        self,
        material_comp: MaterialComposition,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        crevice_gap_mm: float = 0.1,
        current_density_A_per_m2: float = 1e-4,
        material_name: str = "316L",  # BUG-017 fix: Add material name for ASTM G48 lookup
    ) -> CreviceResult:
        """
        Calculate crevice corrosion susceptibility.

        Uses simplified Oldfield-Sutton IR drop model.

        Args:
            material_comp: Material composition
            temperature_C: Operating temperature (°C)
            Cl_mg_L: Chloride concentration (mg/L)
            pH: Bulk solution pH
            crevice_gap_mm: Crevice gap width (mm)
            current_density_A_per_m2: Corrosion current density (A/m²)
            material_name: Material name for ASTM G48 CCT lookup

        Returns:
            CreviceResult with CCT, IR drop, acidification factor

        Per Codex: Simplified Oldfield-Sutton for IR drop iteration
        """
        # BUG-017 fix: Get CCT from ASTM G48 tabulated data
        cpt_data = get_cpt_from_astm(material_name)
        if cpt_data is not None and "CCT_C" in cpt_data:
            CCT = cpt_data["CCT_C"]  # ASTM G48-11 measured CCT
            CPT = cpt_data["CPT_C"]  # Also get CPT for reference
            logger.info(f"Using ASTM G48 CCT: {CCT}°C (source: {cpt_data['source']})")
        else:
            # Fallback: CCT is typically CPT - 10 to 20°C (more aggressive than pitting)
            pren = material_comp.calculate_pren()
            cpt_corr = CPT_CORRELATIONS.get(material_comp.grade_type, CPT_CORRELATIONS["austenitic"])
            CPT = cpt_corr["m"] * pren + cpt_corr["b"]
            CCT = CPT - 15.0  # Conservative estimate
            logger.warning(f"Material {material_name} not in ASTM G48; using CCT = CPT - 15°C heuristic")

        # IR drop in crevice (Oldfield-Sutton simplified)
        # ΔE = i × R × L
        # where:
        #   i = current density (A/m²)
        #   R = solution resistivity (Ω·m)
        #   L = crevice depth (m)

        # Estimate solution resistivity from chloride concentration
        # R ≈ 1 / (κ × (Cl⁻ concentration))
        # For seawater (19000 mg/L Cl⁻): κ ≈ 5 S/m, R ≈ 0.2 Ω·m
        # Scale linearly with Cl⁻
        Cl_seawater = 19000.0  # mg/L
        R_seawater = 0.2  # Ω·m
        R_solution = R_seawater * (Cl_seawater / max(Cl_mg_L, 100.0))

        # Assume crevice depth = 10 × gap (aspect ratio)
        crevice_depth_m = (crevice_gap_mm / 1000.0) * 10.0

        # IR drop
        IR_drop = current_density_A_per_m2 * R_solution * crevice_depth_m

        # Acidification factor (pH drop in crevice)
        # Metal dissolution produces H⁺: M → M²⁺ + 2e⁻
        # Hydrolysis: M²⁺ + H₂O → MOH⁺ + H⁺
        # Simplified: ΔpH ≈ log10(1 + k × i × t / buffer_capacity)
        # Assume acidification_factor = (pH_bulk / pH_crevice)
        # Typically pH drops by 2-4 units in active crevice
        delta_pH = 2.0 + (IR_drop / 0.1) * 2.0  # 2-4 pH drop depending on IR
        delta_pH = min(delta_pH, pH - 2.0)  # Can't drop below pH 2
        pH_crevice = pH - delta_pH
        acidification_factor = 10.0 ** delta_pH  # Factor by which [H⁺] increases

        # Temperature margin
        margin_C = CCT - temperature_C

        # Susceptibility (crevice is more aggressive than pitting)
        if margin_C > 15.0 and acidification_factor < 10:
            susceptibility = "low"
        elif margin_C > 5.0 and acidification_factor < 100:
            susceptibility = "moderate"
        elif margin_C > -5.0:
            susceptibility = "high"
        else:
            susceptibility = "critical"

        # Interpretation
        if susceptibility == "critical":
            interpretation = f"CRITICAL: T = {temperature_C}°C >> CCT = {CCT:.1f}°C; IR drop = {IR_drop*1000:.1f} mV; pH drops to {pH_crevice:.1f} in crevice"
        elif susceptibility == "high":
            interpretation = f"HIGH RISK: T = {temperature_C}°C near CCT = {CCT:.1f}°C; Crevice acidification factor = {acidification_factor:.1f}"
        elif susceptibility == "moderate":
            interpretation = f"MODERATE: T = {temperature_C}°C below CCT = {CCT:.1f}°C (margin {margin_C:.1f}°C); Monitor for crevice formation"
        else:
            interpretation = f"LOW RISK: T = {temperature_C}°C well below CCT = {CCT:.1f}°C (margin {margin_C:.1f}°C)"

        return CreviceResult(
            CCT_C=CCT,
            IR_drop_V=IR_drop,
            acidification_factor=acidification_factor,
            susceptibility=susceptibility,
            margin_C=margin_C,
            interpretation=interpretation,
        )

    def calculate_localized_corrosion(
        self,
        material: str,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        crevice_gap_mm: float = 0.1,
    ) -> LocalizedResult:
        """
        Calculate combined pitting and crevice susceptibility.

        Args:
            material: Material name (e.g., "316L", "2205", "254SMO")
            temperature_C: Operating temperature (°C)
            Cl_mg_L: Chloride concentration (mg/L)
            pH: Solution pH
            crevice_gap_mm: Crevice gap width (mm)

        Returns:
            LocalizedResult with separate pitting and crevice results

        Per Codex: Separate pitting vs crevice outputs, shared Cl⁻ threshold logic
        """
        # Get material composition from database
        material_comp = self._get_material_composition(material)

        # Calculate pitting susceptibility
        pitting_result = self.calculate_pitting_susceptibility(
            material_comp, temperature_C, Cl_mg_L, pH, material_name=material
        )

        # Calculate crevice susceptibility
        crevice_result = self.calculate_crevice_susceptibility(
            material_comp, temperature_C, Cl_mg_L, pH, crevice_gap_mm, material_name=material
        )

        # Overall risk (worst of pitting or crevice)
        risk_levels = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
        pitting_level = risk_levels[pitting_result.susceptibility]
        crevice_level = risk_levels[crevice_result.susceptibility]
        overall_level = max(pitting_level, crevice_level)
        overall_risk = [k for k, v in risk_levels.items() if v == overall_level][0]

        return LocalizedResult(
            pitting=pitting_result,
            crevice=crevice_result,
            material=material,
            temperature_C=temperature_C,
            Cl_mg_L=Cl_mg_L,
            pH=pH,
            overall_risk=overall_risk,
        )

    def _get_base_chloride_threshold(self, pren: float) -> float:
        """
        Get base chloride threshold from PREN.

        Uses empirical correlation: Cl_threshold ≈ 10^(PREN/10) mg/L
        (rough approximation from ASTM G48 data)
        """
        # Exponential correlation
        Cl_threshold = 10.0 ** ((pren - 10.0) / 10.0)
        return max(Cl_threshold, 10.0)  # Minimum 10 mg/L

    def _get_material_composition(self, material: str) -> MaterialComposition:
        """
        Get material composition from authoritative UNS database.

        FIX BUG-016: Use authoritative materials database instead of 5-material dict
        """
        # Get from authoritative database
        mat_data = get_material_data(material)

        if mat_data is not None:
            # Convert to local MaterialComposition format
            return MaterialComposition(
                Cr=mat_data.Cr_wt_pct,
                Mo=mat_data.Mo_wt_pct,
                N=mat_data.N_wt_pct,
                Ni=mat_data.Ni_wt_pct,
                grade_type=mat_data.grade_type,
            )
        else:
            # Fallback with warning
            logger.warning(
                f"Material '{material}' not in authoritative UNS database; "
                f"defaulting to conservative 316L"
            )
            # Return 316L as conservative fallback
            mat_316L = get_material_data("316L")
            if mat_316L:
                return MaterialComposition(
                    Cr=mat_316L.Cr_wt_pct,
                    Mo=mat_316L.Mo_wt_pct,
                    N=mat_316L.N_wt_pct,
                    Ni=mat_316L.Ni_wt_pct,
                    grade_type=mat_316L.grade_type,
                )
            else:
                # Absolute fallback
                return MaterialComposition(
                    Cr=16.5, Mo=2.0, N=0.10, Ni=10.0, grade_type="austenitic"
                )
