"""
Galvanic Corrosion Backend - Mixed-Potential Theory

Implements Evans diagram analysis for galvanic couples:
- Anodic polarization (metal dissolution)
- Cathodic polarization (oxygen reduction, hydrogen evolution)
- Mixed-potential intersection (E_couple, i_galv)
- Corrosion rate calculation

Based on:
- NRL polarization curve data (SS316ORRCoeffs.csv, etc.)
- Butler-Volmer / Tafel approximations
- Mixed-potential theory (Wagner & Traud)

Per Codex guidance:
- Use Tafel approximations (valid for |η| > 50-100 mV)
- Weight multiple cathodes by exposed area
- Return both E_couple and i_galv for downstream tools
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
from pathlib import Path

# Import authoritative materials database (BUG-010, BUG-012 fixes)
from data import (
    get_material_data,
    get_orr_diffusion_limit,
    GALVANIC_SERIES_SEAWATER,
    E_SHE_TO_SCE,
)

# DIRECT IMPORT: NRL polarization curve data (FIX ISSUE-102, ISSUE-103)
from data import (
    get_orr_parameters,
    get_her_parameters,
    get_passivation_parameters,
    get_metal_oxidation_parameters,
    get_pitting_parameters,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

FARADAY = 96485.3321  # C/mol
R_GAS = 8.314462618  # J/mol·K
T_STD = 298.15  # 25°C in K


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PolarizationCurve:
    """
    Polarization curve data for a material/reaction.

    Attributes:
        material: Material name (e.g., "carbon steel", "316L")
        reaction: Reaction type ("anodic", "orr", "her")
        E_corr: Corrosion potential vs SHE (V)
        i0: Exchange current density (A/m²)
        ba: Anodic Tafel slope (V/decade)
        bc: Cathodic Tafel slope (V/decade)
        temperature_C: Temperature (°C)
        electrolyte: Electrolyte description
        source: Data source reference
    """
    material: str
    reaction: str
    E_corr: float
    i0: float
    ba: Optional[float] = None
    bc: Optional[float] = None
    temperature_C: float = 25.0
    electrolyte: str = "seawater"
    source: str = "Unknown"


@dataclass
class GalvanicResult:
    """
    Result of galvanic corrosion calculation.

    Attributes:
        E_couple: Coupled potential vs SHE (V)
        i_galv: Galvanic current density on anode (A/m²)
        corrosion_rate_mm_per_year: Corrosion rate (mm/year)
        anode_material: Anode material name
        cathode_material: Cathode material name
        area_ratio: Cathode area / Anode area
        interpretation: Text summary
    """
    E_couple: float
    i_galv: float
    corrosion_rate_mm_per_year: float
    anode_material: str
    cathode_material: str
    area_ratio: float
    interpretation: str


# ---------------------------------------------------------------------------
# Galvanic corrosion backend
# ---------------------------------------------------------------------------

class GalvanicBackend:
    """
    Mixed-potential theory galvanic corrosion calculator.

    Uses Evans diagram approach to find E_couple and i_galv.
    """

    def __init__(self):
        """Initialize galvanic backend."""
        pass

    def calculate_tafel_current(
        self,
        E: float,
        E_corr: float,
        i0: float,
        beta: float,
        is_anodic: bool = True,
    ) -> float:
        """
        Calculate current density from Tafel equation.

        Args:
            E: Applied potential (V vs SHE)
            E_corr: Corrosion potential (V vs SHE)
            i0: Exchange current density (A/m²)
            beta: Tafel slope (V/decade) - positive for anodic, negative for cathodic
            is_anodic: True for anodic branch, False for cathodic

        Returns:
            Current density (A/m²) - positive for anodic, negative for cathodic

        Tafel equation:
            i = i0 × 10^(η / β)
        where:
            η = E - E_corr (overpotential)
            β = ba (anodic) or bc (cathodic)
        """
        eta = E - E_corr  # Overpotential

        # Tafel approximation valid for |η| > ~50-100 mV per Codex
        if abs(eta) < 0.05:
            logger.warning(f"Tafel approximation questionable for η = {eta*1000:.1f} mV < 50 mV")

        try:
            i = i0 * 10.0 ** (eta / beta)
        except (OverflowError, ValueError):
            # Handle extreme overpotentials
            if eta > 0:
                i = 1e10  # Very large anodic current
            else:
                i = 1e-10  # Very small cathodic current

        # Apply sign convention: anodic positive, cathodic negative
        if not is_anodic:
            i = -i

        return i

    def find_mixed_potential(
        self,
        anodic_curve: PolarizationCurve,
        cathodic_curve: PolarizationCurve,
        area_ratio: float = 1.0,
        i_lim: Optional[float] = None,
    ) -> Tuple[float, float]:
        """
        Find mixed potential (E_couple) and galvanic current (i_galv).

        Uses bisection to find intersection of anodic and cathodic curves.

        Args:
            anodic_curve: Anodic polarization curve (metal dissolution)
            cathodic_curve: Cathodic polarization curve (ORR, HER)
            area_ratio: A_cathode / A_anode (scales cathodic current)
            i_lim: Diffusion-limited current density (A/m²) for ORR (BUG-011 fix)

        Returns:
            Tuple of (E_couple, i_galv) where:
                E_couple: Coupled potential (V vs SHE)
                i_galv: Galvanic current density on anode (A/m²)

        FIX BUG-011: Add diffusion limits per NRL data
        Per Codex: Weight cathode by area ratio
        """
        # Initial bounds: Between the two corrosion potentials
        E_min = min(anodic_curve.E_corr, cathodic_curve.E_corr) - 0.5
        E_max = max(anodic_curve.E_corr, cathodic_curve.E_corr) + 0.5

        # Bisection to find E where i_anodic = i_cathodic × area_ratio
        tolerance = 1e-6  # 1 µV
        max_iterations = 100

        for iteration in range(max_iterations):
            E_mid = (E_min + E_max) / 2.0

            # Anodic current (positive)
            i_anodic = self.calculate_tafel_current(
                E_mid,
                anodic_curve.E_corr,
                anodic_curve.i0,
                anodic_curve.ba,
                is_anodic=True,
            )

            # Cathodic current (negative) scaled by area
            i_cathodic_raw = self.calculate_tafel_current(
                E_mid,
                cathodic_curve.E_corr,
                cathodic_curve.i0,
                cathodic_curve.bc,
                is_anodic=False,
            )

            # FIX BUG-011: Apply diffusion limit to cathodic current
            # ORR caps at i_lim due to O₂ mass transport (NRL data: 0.5-1 mA/cm²)
            if i_lim is not None:
                # Clamp magnitude of cathodic current (it's negative)
                if abs(i_cathodic_raw) > i_lim:
                    i_cathodic_raw = -i_lim  # Apply limit
                    logger.debug(f"ORR diffusion limit applied: {i_lim} A/m²")

            i_cathodic = i_cathodic_raw * area_ratio

            # Find where i_anodic + i_cathodic = 0 (charge balance)
            i_net = i_anodic + i_cathodic

            if abs(i_net) < tolerance or (E_max - E_min) < tolerance:
                # Converged
                E_couple = E_mid
                i_galv = i_anodic  # Galvanic current on anode
                return E_couple, i_galv

            # Bisection update
            if i_net > 0:
                # Too anodic, lower potential
                E_max = E_mid
            else:
                # Too cathodic, raise potential
                E_min = E_mid

        # Failed to converge
        logger.warning(f"Mixed potential failed to converge after {max_iterations} iterations")
        E_couple = (E_min + E_max) / 2.0
        i_galv = abs(i_anodic)
        return E_couple, i_galv

    def current_to_corrosion_rate(
        self,
        i_corr: float,
        material: str,
        n_electrons: Optional[int] = None,
    ) -> float:
        """
        Convert corrosion current density to corrosion rate.

        Args:
            i_corr: Corrosion current density (A/m²)
            material: Material name (for density, MW, valence lookup)
            n_electrons: Number of electrons (optional, gets from database)

        Returns:
            Corrosion rate (mm/year)

        Formula (per Faraday's law):
            CR (mm/year) = i_corr × MW × 31.536 / (n × F × ρ)

        FIX BUG-012: Use authoritative materials database for n_electrons
        """
        # Get material data from authoritative database
        mat_data = get_material_data(material)

        if mat_data is not None:
            # Use authoritative data
            rho = mat_data.density_kg_m3
            n = n_electrons if n_electrons is not None else mat_data.n_electrons

            # Molecular weight depends on primary element
            if mat_data.grade_type in ["austenitic", "duplex", "super_duplex", "carbon_steel"]:
                MW = 55.845  # Fe
            elif mat_data.grade_type == "aluminum":
                MW = 26.982  # Al
            elif mat_data.grade_type in ["copper", "copper_alloy"]:
                MW = 63.546  # Cu
            elif mat_data.grade_type == "titanium":
                MW = 47.867  # Ti
            elif mat_data.grade_type == "zinc":
                MW = 65.38  # Zn
            elif mat_data.grade_type == "nickel_alloy":
                MW = 58.693  # Ni
            else:
                MW = 55.845  # Default to Fe

            logger.info(
                f"Using authoritative data for {material}: "
                f"n={n}, MW={MW}, ρ={rho} kg/m³"
            )
        else:
            # Fallback (log warning)
            logger.warning(
                f"Material '{material}' not in authoritative database; "
                f"using conservative Fe defaults"
            )
            MW = 55.845  # Fe
            rho = 7850.0  # kg/m³
            n = n_electrons if n_electrons is not None else 2

        # Conversion factor (seconds/year × mm/m × g/kg)
        K = 365.25 * 24 * 3600 * 1000 * 1000 / 1e6  # = 31.536

        # Corrosion rate (mm/year)
        CR = (i_corr * MW * K) / (n * FARADAY * rho)

        return CR

    def calculate_galvanic_corrosion(
        self,
        anode_material: str,
        cathode_material: str,
        area_ratio: float,
        temperature_C: float = 25.0,
        electrolyte: str = "seawater",
    ) -> GalvanicResult:
        """
        Calculate galvanic corrosion rate for a bimetallic couple.

        Args:
            anode_material: Less noble material (e.g., "carbon steel")
            cathode_material: More noble material (e.g., "316L")
            area_ratio: A_cathode / A_anode
            temperature_C: Temperature (°C)
            electrolyte: Electrolyte type

        Returns:
            GalvanicResult with E_couple, i_galv, corrosion rate

        Per Codex: Use Tafel approximations and weight by area
        """
        # Get polarization curves (BUG-010 partial fix: uses ASTM G82 data)
        anodic_curve = self._get_anodic_curve(anode_material, temperature_C, electrolyte)
        cathodic_curve = self._get_cathodic_curve(cathode_material, temperature_C, electrolyte)

        # Get ORR diffusion limit (BUG-011 fix: add transport limits)
        i_lim = get_orr_diffusion_limit(electrolyte, temperature_C)
        logger.info(f"Using ORR diffusion limit: {i_lim} A/m² for {electrolyte} at {temperature_C}°C")

        # Find mixed potential with diffusion limits
        E_couple, i_galv = self.find_mixed_potential(anodic_curve, cathodic_curve, area_ratio, i_lim=i_lim)

        # Convert to corrosion rate (BUG-012 fix: no hardcoded n_electrons)
        CR = self.current_to_corrosion_rate(i_galv, anode_material)

        # Interpretation
        if CR > 1.0:
            interpretation = f"Severe galvanic corrosion (CR = {CR:.2f} mm/year)"
        elif CR > 0.1:
            interpretation = f"Moderate galvanic corrosion (CR = {CR:.2f} mm/year)"
        else:
            interpretation = f"Low galvanic corrosion (CR = {CR:.2f} mm/year)"

        if area_ratio > 10.0:
            interpretation += f"; Large cathode/anode ratio ({area_ratio:.1f}:1) accelerates attack"

        return GalvanicResult(
            E_couple=E_couple,
            i_galv=i_galv,
            corrosion_rate_mm_per_year=CR,
            anode_material=anode_material,
            cathode_material=cathode_material,
            area_ratio=area_ratio,
            interpretation=interpretation,
        )

    def _get_galvanic_potential(
        self,
        material: str,
        electrolyte: str,
    ) -> float:
        """
        Get corrosion potential from ASTM G82 galvanic series.

        CRITICAL FIX (ISSUE-101): ASTM G82 potentials are vs SCE reference.
        All potentials are converted to SHE reference for consistency.

        Conversion: E(SHE) = E(SCE) + 0.241V (per ASTM G3, NACE SP0208)

        Args:
            material: Material name
            electrolyte: Electrolyte type (e.g., "seawater")

        Returns:
            E_corr vs SHE (V) - CONVERTED from ASTM G82 SCE values

        Reference:
            - ASTM G82-98 (2014): Galvanic series in seawater (vs SCE)
            - ASTM G3-14: Standard Reference Test Method for Making Potentiostatic
                          and Potentiodynamic Anodic Polarization Measurements
            - E_SHE_TO_SCE from NRL Constants.m = 0.244V (we use 0.241V per ASTM G3)
        """
        if electrolyte.lower() != "seawater":
            logger.warning(f"Galvanic series only available for seawater; using seawater data for {electrolyte}")

        # Try to find material in ASTM G82 galvanic series
        material_lower = material.lower()

        # Check galvanic series (with passive/active states for stainless steels)
        for key, potential_sce in GALVANIC_SERIES_SEAWATER.items():
            if key.lower() in material_lower or material_lower in key.lower():
                # CRITICAL FIX: Convert SCE to SHE
                potential_she = potential_sce + E_SHE_TO_SCE
                logger.info(
                    f"Using ASTM G82 galvanic potential for {material}: "
                    f"E_corr = {potential_sce:.3f} V vs SCE → {potential_she:.3f} V vs SHE"
                )
                return potential_she

        # Fallback based on material type (also in SCE, needs conversion)
        mat_data = get_material_data(material)
        if mat_data:
            if mat_data.grade_type in ["austenitic", "duplex", "super_duplex", "superaustenitic"]:
                E_corr_sce = -0.10  # Passive stainless steel vs SCE
            elif mat_data.grade_type == "carbon_steel":
                E_corr_sce = -0.65  # vs SCE
            elif mat_data.grade_type == "aluminum":
                E_corr_sce = -0.75  # vs SCE
            elif mat_data.grade_type in ["copper", "copper_alloy"]:
                E_corr_sce = -0.20  # vs SCE
            elif mat_data.grade_type == "titanium":
                E_corr_sce = 0.10  # vs SCE
            elif mat_data.grade_type == "zinc":
                E_corr_sce = -1.00  # vs SCE
            elif mat_data.grade_type == "nickel_alloy":
                E_corr_sce = 0.00  # vs SCE
            else:
                E_corr_sce = -0.50  # vs SCE

            # CRITICAL FIX: Convert SCE to SHE
            E_corr_she = E_corr_sce + E_SHE_TO_SCE
            logger.warning(
                f"Material {material} not in ASTM G82; using grade_type estimate: "
                f"{E_corr_sce:.3f} V vs SCE → {E_corr_she:.3f} V vs SHE"
            )
            return E_corr_she
        else:
            # Conservative default in SCE, convert to SHE
            E_corr_sce = -0.50
            E_corr_she = E_corr_sce + E_SHE_TO_SCE
            logger.warning(
                f"Material {material} not found; using conservative default "
                f"{E_corr_sce:.3f} V vs SCE → {E_corr_she:.3f} V vs SHE"
            )
            return E_corr_she

    def _get_anodic_curve(
        self,
        material: str,
        temperature_C: float,
        electrolyte: str,
    ) -> PolarizationCurve:
        """
        Get anodic polarization curve for a material.

        FIX ISSUE-102: Use NRL polarization curve CSV data for Tafel parameters.
        FIX BUG-010: Use ASTM G82 galvanic series data for E_corr.
        """
        # Get E_corr from ASTM G82 galvanic series
        E_corr = self._get_galvanic_potential(material, electrolyte)

        # Get chemistry parameters from electrolyte
        if electrolyte.lower() == "seawater":
            c_cl_mol_L = 0.5  # ~30,000 ppm Cl⁻ in seawater
            pH = 8.1  # Typical seawater pH
        else:
            # Default to low chloride for fresh water
            c_cl_mol_L = 0.001
            pH = 7.0
            logger.warning(f"Using default chemistry for {electrolyte}: c_Cl={c_cl_mol_L}, pH={pH}")

        # Map material name to NRL material code
        nrl_material = self._map_to_nrl_material(material)

        mat_data = get_material_data(material)

        # Determine if material is stainless steel based on both database and NRL mapping
        is_stainless = False
        if mat_data and mat_data.grade_type in ["austenitic", "duplex", "super_duplex", "superaustenitic"]:
            is_stainless = True
        elif nrl_material == "SS316":
            # If it maps to SS316, it's a stainless steel
            is_stainless = True

        # Get NRL data based on material type (NO FALLBACKS - always use authoritative data)
        if is_stainless:
            # Stainless steels: passivation behavior
            nrl_params = get_passivation_parameters(nrl_material, c_cl_mol_L, temperature_C, pH)

            if not nrl_params:
                raise RuntimeError(
                    f"NRL passivation data not available for {nrl_material}. "
                    f"Expected CSV file: {nrl_material}PassCoeffs.csv"
                )

            # Convert NRL units: i0 is in A/cm², we need A/m²
            i0_A_per_m2 = nrl_params.i0 * 1e4  # cm² to m²
            ba = nrl_params.b_tafel  # Already in V/decade

            logger.info(f"Using NRL passivation data for {material}: i0={nrl_params.i0:.2e} A/cm², ba={ba:.4f} V/dec")

            return PolarizationCurve(
                material=material,
                reaction="passive_film",
                E_corr=E_corr,
                i0=i0_A_per_m2,
                ba=ba,
                bc=-0.120,  # Cathodic not used for anodic curve
                temperature_C=temperature_C,
                electrolyte=electrolyte,
                source=f"NRL {nrl_material}PassCoeffs.csv + ASTM G82",
            )

        else:
            # Carbon steels, aluminum, copper, zinc: active metal oxidation
            nrl_params = get_metal_oxidation_parameters(nrl_material, c_cl_mol_L, temperature_C, pH)

            if not nrl_params:
                raise RuntimeError(
                    f"NRL metal oxidation data not available for {nrl_material}. "
                    f"Expected CSV file: {nrl_material}FeOxCoeffs.csv or {nrl_material}CuOxCoeffs.csv"
                )

            i0_A_per_m2 = nrl_params.i0 * 1e4
            ba = nrl_params.b_tafel

            logger.info(f"Using NRL oxidation data for {material}: i0={nrl_params.i0:.2e} A/cm², ba={ba:.4f} V/dec")

            return PolarizationCurve(
                material=material,
                reaction=nrl_params.reaction_type,
                E_corr=E_corr,
                i0=i0_A_per_m2,
                ba=ba,
                bc=-0.120,
                temperature_C=temperature_C,
                electrolyte=electrolyte,
                source=f"NRL {nrl_material} CSV + ASTM G82",
            )

    def _map_to_nrl_material(self, material: str) -> str:
        """
        Map user material name to NRL material code.

        Comprehensive mapping to maximize coverage of NRL's 6 materials:
        - SS316: All austenitic/duplex/super duplex stainless steels
        - HY80/HY100: All carbon steels and low-alloy steels
        - I625: All nickel-based superalloys
        - Ti: All titanium alloys
        - CuNi: Copper-nickel alloys

        Args:
            material: User material name (e.g., "316L", "HY-80", "Inconel 625", "carbon_steel")

        Returns:
            NRL material code (e.g., "SS316", "HY80", "I625", "Ti", "CuNi")

        Raises:
            ValueError: If material cannot be mapped to any NRL material
        """
        material_lower = material.lower().replace("-", "").replace("_", "").replace(" ", "")

        # Get material data for grade_type classification
        mat_data = get_material_data(material)

        # =====================================================================
        # STAINLESS STEELS → SS316
        # All stainless steel grades use SS316 as representative
        # =====================================================================
        stainless_patterns = ["316", "304", "317", "321", "347", "904", "2205", "2507",
                              "254smo", "al6xn", "ss", "stainless"]
        if any(p in material_lower for p in stainless_patterns):
            logger.debug(f"Mapping {material} → SS316 (stainless steel)")
            return "SS316"

        if mat_data and mat_data.grade_type in ["austenitic", "duplex", "super_duplex", "superaustenitic"]:
            logger.debug(f"Mapping {material} → SS316 (grade_type={mat_data.grade_type})")
            return "SS316"

        # =====================================================================
        # CARBON STEELS → HY80 or HY100
        # HY steels are high-yield carbon steels, representative of structural steels
        # =====================================================================
        hy80_patterns = ["hy80", "hy-80", "hy_80"]
        hy100_patterns = ["hy100", "hy-100", "hy_100"]

        if any(p in material_lower for p in hy80_patterns):
            logger.debug(f"Mapping {material} → HY80 (exact match)")
            return "HY80"

        if any(p in material_lower for p in hy100_patterns):
            logger.debug(f"Mapping {material} → HY100 (exact match)")
            return "HY100"

        # Generic carbon steel patterns
        carbon_steel_patterns = ["carbonsteel", "steel", "mild", "structural", "a36", "a572",
                                 "astm", "ship", "hull"]
        if any(p in material_lower for p in carbon_steel_patterns):
            # Default to HY80 for generic carbon steels
            logger.debug(f"Mapping {material} → HY80 (carbon steel)")
            return "HY80"

        if mat_data and mat_data.grade_type == "carbon_steel":
            logger.debug(f"Mapping {material} → HY80 (grade_type=carbon_steel)")
            return "HY80"

        # =====================================================================
        # NICKEL ALLOYS → I625
        # Inconel 625 representative of Ni-Cr-Mo superalloys
        # =====================================================================
        nickel_patterns = ["625", "inconel", "in625", "i625", "alloy625",
                          "825", "i825", "hastelloy", "monel"]
        if any(p in material_lower for p in nickel_patterns):
            logger.debug(f"Mapping {material} → I625 (nickel alloy)")
            return "I625"

        if mat_data and mat_data.grade_type == "nickel_alloy":
            logger.debug(f"Mapping {material} → I625 (grade_type=nickel_alloy)")
            return "I625"

        # =====================================================================
        # TITANIUM → Ti
        # All titanium grades (CP, 6-4, etc.) use Ti
        # =====================================================================
        titanium_patterns = ["ti", "titan", "grade"]
        if any(p in material_lower for p in titanium_patterns):
            logger.debug(f"Mapping {material} → Ti (titanium)")
            return "Ti"

        if mat_data and mat_data.grade_type == "titanium":
            logger.debug(f"Mapping {material} → Ti (grade_type=titanium)")
            return "Ti"

        # =====================================================================
        # COPPER-NICKEL → CuNi
        # Copper-nickel alloys (70-30, 90-10)
        # =====================================================================
        cuni_patterns = ["cuni", "cupronickel", "cupro", "coppernickel", "9010", "7030"]
        if any(p in material_lower for p in cuni_patterns):
            logger.debug(f"Mapping {material} → CuNi (copper-nickel)")
            return "CuNi"

        # Check if both copper and nickel are in composition
        if mat_data and mat_data.grade_type == "copper_alloy":
            if mat_data.composition and mat_data.composition.Ni_pct and mat_data.composition.Ni_pct > 5:
                logger.debug(f"Mapping {material} → CuNi (Cu alloy with Ni)")
                return "CuNi"

        # =====================================================================
        # OTHER METALS → Best approximation based on properties
        # =====================================================================

        # Aluminum → Use HY80 (active metal, similar corrosion behavior to Fe)
        if mat_data and mat_data.grade_type == "aluminum":
            logger.info(f"Mapping {material} → HY80 (aluminum approximated as active metal)")
            return "HY80"

        if "aluminum" in material_lower or "al" in material_lower:
            logger.info(f"Mapping {material} → HY80 (aluminum approximated as active metal)")
            return "HY80"

        # Copper → Use CuNi (similar noble metal behavior)
        if mat_data and mat_data.grade_type == "copper":
            logger.info(f"Mapping {material} → CuNi (copper approximated as copper alloy)")
            return "CuNi"

        if "copper" in material_lower or material_lower == "cu":
            logger.info(f"Mapping {material} → CuNi (copper approximated as copper alloy)")
            return "CuNi"

        # Zinc → Use HY80 (very active metal)
        if mat_data and mat_data.grade_type == "zinc":
            logger.info(f"Mapping {material} → HY80 (zinc approximated as active metal)")
            return "HY80"

        if "zinc" in material_lower or material_lower == "zn":
            logger.info(f"Mapping {material} → HY80 (zinc approximated as active metal)")
            return "HY80"

        # =====================================================================
        # NO MAPPING FOUND → Raise error (no fallback)
        # =====================================================================
        raise ValueError(
            f"Material '{material}' cannot be mapped to any NRL material. "
            f"NRL data available for: SS316 (stainless), HY80/HY100 (carbon steel), "
            f"I625 (nickel alloy), Ti (titanium), CuNi (copper-nickel). "
            f"Please use a supported material or add mapping."
        )

    def _get_cathodic_curve(
        self,
        material: str,
        temperature_C: float,
        electrolyte: str,
    ) -> PolarizationCurve:
        """
        Get cathodic polarization curve (typically ORR: O2 reduction).

        FIX ISSUE-103: Use NRL ORR CSV data for cathodic Tafel parameters.
        """
        # Get chemistry parameters from electrolyte
        if electrolyte.lower() == "seawater":
            c_cl_mol_L = 0.5  # ~30,000 ppm Cl⁻
            pH = 8.1  # Typical seawater pH
        else:
            c_cl_mol_L = 0.001
            pH = 7.0

        # Map to NRL material code
        nrl_material = self._map_to_nrl_material(material)

        # Get NRL ORR data (NO FALLBACK - always use authoritative data)
        orr_params = get_orr_parameters(nrl_material, c_cl_mol_L, temperature_C, pH)

        if not orr_params:
            raise RuntimeError(
                f"NRL ORR data not available for {nrl_material}. "
                f"Expected CSV file: {nrl_material}ORRCoeffs.csv"
            )

        # Convert units: i0 is in A/cm², we need A/m²
        i0_A_per_m2 = orr_params.i0 * 1e4
        bc = -orr_params.b_tafel  # Negative for cathodic

        # ORR equilibrium potential (from NRL Constants.m)
        # For neutral/alkaline: O2 + 2H2O + 4e- → 4OH-, E0 = 0.401 V vs SHE
        # For acidic: O2 + 4H+ + 4e- → 2H2O, E0 = 1.223 V vs SHE
        if pH < 4:
            E_eq = 1.223
        else:
            E_eq = 0.401

        logger.info(f"Using NRL ORR data for {material}: i0={orr_params.i0:.2e} A/cm², bc={bc:.4f} V/dec")

        return PolarizationCurve(
            material=material,
            reaction="ORR",
            E_corr=E_eq,
            i0=i0_A_per_m2,
            ba=0.120,  # Not used for cathodic
            bc=bc,
            temperature_C=temperature_C,
            electrolyte=electrolyte,
            source=f"NRL {nrl_material}ORRCoeffs.csv",
        )
