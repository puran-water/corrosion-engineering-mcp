"""
NRL Material Property Classes for Galvanic Corrosion Modeling

PROVENANCE:
Direct 1:1 translation from MATLAB code by:
Steven A. Policastro, Ph.D.
Center for Corrosion Science and Engineering
U.S. Naval Research Laboratory
4555 Overlook Avenue SW, Washington, DC 20375

Source: USNavalResearchLaboratory/corrosion-modeling-applications
Directory: polarization-curve-modeling/
Files: HY80.m, HY100.m, SS316.m, Ti.m, I625.m, CuNi.m
License: Public domain (U.S. Federal Government work)
Date: 2025-10-19

This module provides material-specific electrochemical properties for:
- HY-80 Steel (UNS K31820)
- HY-100 Steel (UNS K32045)
- SS 316 Stainless Steel (UNS S31600)
- Titanium (UNS R50700)
- Inconel 625 (UNS N06625)
- CuNi 70-30 (UNS C71500)

Each material class encapsulates:
1. Physical properties (molar mass, oxidation state)
2. Electrochemical reaction parameters (ΔG, β, diffusion layer thickness)
3. CSV coefficient loading for temperature/chloride-dependent activation energies
4. pH correction for activation energies
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Tuple, Literal, Optional
from functools import lru_cache


# Path to vendored NRL coefficient CSV files
NRL_COEFFICIENTS_DIR = Path(__file__).parent.parent / "external" / "nrl_coefficients"


# Module-level CSV cache to avoid redundant pandas reads
@lru_cache(maxsize=32)
def _load_csv_coefficients_cached(csv_path_str: str) -> np.ndarray:
    """
    Load polynomial coefficients from CSV file with caching.

    Args:
        csv_path_str: Full path to CSV file as string (for hashability)

    Returns:
        Array of 6 coefficients: [p00, p10, p01, p20, p11, p02]

    Raises:
        FileNotFoundError: If CSV file not found
        ValueError: If CSV format is incorrect
    """
    csv_path = Path(csv_path_str)

    if not csv_path.exists():
        raise FileNotFoundError(
            f"NRL coefficient file not found: {csv_path}\n"
            f"Expected in: {NRL_COEFFICIENTS_DIR}"
        )

    # Read CSV (single row, 6 columns, no header)
    data = pd.read_csv(csv_path, header=None).values[0]

    if len(data) != 6:
        raise ValueError(
            f"Expected 6 coefficients in {csv_path.name}, got {len(data)}"
        )

    return data


class CorrodingMetal:
    """
    Base class for corroding metals in NRL galvanic corrosion model.

    All material classes inherit from this base class and implement
    the calculate_delta_g() method with material-specific CSV files
    and pH correction logic.
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        """
        Initialize a corroding metal instance.

        Args:
            name: Material name (e.g., "HY-80", "SS316")
            chloride_M: Chloride ion concentration, M (mol/L)
            temperature_C: Temperature, °C
            pH: pH value (1-13)
            velocity_m_s: Liquid velocity, m/s (default 0.0)
        """
        self.name = name
        self.chloride_M = chloride_M
        self.temperature_C = temperature_C
        self.pH = pH
        self.velocity_m_s = velocity_m_s

        # Material properties (set by subclasses)
        self.metal_mass: float = 0.0  # Molar mass, g/mol
        self.oxidation_level_z: int = 0  # Electrons transferred in oxidation

        # Electrochemical reaction properties (set by subclasses)
        # Format: (delta_g_cathodic, delta_g_anodic) in J/mol
        self.delta_g_orr: Tuple[float, float] = (0.0, 0.0)
        self.delta_g_her: Tuple[float, float] = (0.0, 0.0)
        self.delta_g_metal_oxidation: Tuple[float, float] = (0.0, 0.0)
        self.delta_g_metal_passivation: Tuple[float, float] = (0.0, 0.0)
        self.delta_g_metal_pitting: Tuple[float, float] = (0.0, 0.0)

        # Transfer coefficients (β = symmetry factor)
        self.beta_orr: float = 0.0
        self.beta_her: float = 0.0
        self.beta_metal_oxidation: float = 0.0
        self.beta_metal_passivation: float = 0.0
        self.beta_metal_pitting: float = 0.0

        # Diffusion layer thicknesses, cm
        self.del_orr: float = 0.0
        self.del_her: float = 0.0

        # Oxide film properties
        self.oxide_mass: float = 0.0  # Molar mass, g/mol
        self.oxide_density: float = 0.0  # Density, g/cm³
        self.resistivity_of_oxide: float = 0.0  # Resistivity, Ω·cm
        self.passive_current_density: float = 0.0  # A/cm²
        self.passive_film_thickness: float = 0.0  # cm

        # Pitting properties (for materials that pit)
        self.pit_potential: Optional[float] = None  # V_SCE

    @staticmethod
    def _validate_activation_energy(
        dg_cathodic: float,
        dg_anodic: float,
        reaction_type: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
    ) -> Tuple[float, float]:
        """
        Validate activation energies and raise error if negative.

        CRITICAL SANITY CHECK (Codex recommendation v2):
        Activation energies must be positive (energy barrier for reaction).
        Negative dG produces exp(+value) → astronomical exchange currents.

        Known issue: HY80ORRCoeffs.csv produces negative dG_cathodic at
        (Cl=0.54 M, T=25C, pH=8), causing i0_cathodic ~ 1e+97 A/cm².

        Codex guidance: "Clamping to 10 kJ/mol fabricates kinetics with no
        physical basis. Treat as error path - refuse to return result so
        consumers know coefficients are out of range."

        If negative dG detected, raise ValueError with detailed context.

        Args:
            dg_cathodic: Cathodic activation energy (J/mol)
            dg_anodic: Anodic activation energy (J/mol)
            reaction_type: Reaction name (for error message)
            chloride_M: Chloride concentration (M)
            temperature_C: Temperature (°C)
            pH: pH value

        Returns:
            Tuple of (validated_dg_cathodic, validated_dg_anodic)

        Raises:
            ValueError: If either activation energy is negative
        """
        errors = []

        if dg_cathodic < 0:
            errors.append(
                f"Negative cathodic activation energy for {reaction_type}: "
                f"dG_cathodic = {dg_cathodic:.2e} J/mol"
            )

        if dg_anodic < 0:
            errors.append(
                f"Negative anodic activation energy for {reaction_type}: "
                f"dG_anodic = {dg_anodic:.2e} J/mol"
            )

        if errors:
            error_msg = (
                f"Invalid activation energies detected - polynomial coefficients "
                f"are out of valid range at (Cl={chloride_M:.3f} M, T={temperature_C:.1f}°C, "
                f"pH={pH:.1f}).\n\n"
                f"Errors:\n  - " + "\n  - ".join(errors) + "\n\n"
                f"This indicates the NRL polynomial fit is being extrapolated beyond its "
                f"calibrated range. The resulting exchange currents would be physically "
                f"meaningless (clamping to arbitrary values would fabricate kinetics with "
                f"no physical basis).\n\n"
                f"Recommendations:\n"
                f"1. Use different material (e.g., SS316, HY100) with valid coefficient sets\n"
                f"2. Adjust conditions to stay within valid parameter ranges\n"
                f"3. Contact NRL for revised coefficients or valid range documentation\n\n"
                f"Known issue: HY80 ORR at seawater conditions (Cl≈0.5 M, T=25°C, pH=8) "
                f"produces negative dG_cathodic."
            )
            raise ValueError(error_msg)

        return (dg_cathodic, dg_anodic)

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """
        Calculate activation energies from CSV polynomial coefficients.

        This method must be implemented by each material subclass.

        Args:
            reaction_type: Type of electrochemical reaction
            chloride_M: Chloride ion concentration, M
            pH: pH value
            temperature_C: Temperature, °C

        Returns:
            Tuple of (delta_g_cathodic, delta_g_anodic) in J/mol
        """
        raise NotImplementedError("Subclasses must implement calculate_delta_g()")

    def _load_csv_coefficients(self, csv_filename: str) -> np.ndarray:
        """
        Load polynomial coefficients from CSV file (with caching).

        Args:
            csv_filename: Name of CSV file (e.g., "HY80ORRCoeffs.csv")

        Returns:
            Array of 6 coefficients: [p00, p10, p01, p20, p11, p02]

        Raises:
            FileNotFoundError: If CSV file not found in external/nrl_coefficients/

        Note:
            Uses @lru_cache wrapper to avoid redundant pandas CSV reads.
            Cache hit rate should be high since files are static.
        """
        csv_path = NRL_COEFFICIENTS_DIR / csv_filename
        # Use cached loader (requires string path for hashability)
        return _load_csv_coefficients_cached(str(csv_path))

    def _apply_polynomial_response_surface(
        self,
        coeffs: np.ndarray,
        chloride_M: float,
        temperature_C: float
    ) -> float:
        """
        Apply quadratic response surface polynomial.

        ΔG(T, Cl⁻) = p00 + p10*Cl⁻ + p01*T + p20*Cl⁻² + p11*Cl⁻*T + p02*T²

        Args:
            coeffs: Array of [p00, p10, p01, p20, p11, p02]
            chloride_M: Chloride concentration, M
            temperature_C: Temperature, °C

        Returns:
            Activation energy without pH correction, J/mol
        """
        p00, p10, p01, p20, p11, p02 = coeffs

        delta_g_no_pH = (
            p00 +
            p10 * chloride_M +
            p01 * temperature_C +
            p20 * chloride_M**2 +
            p11 * chloride_M * temperature_C +
            p02 * temperature_C**2
        )

        return delta_g_no_pH


class HY80(CorrodingMetal):
    """
    HY-80 High-Yield Steel (UNS K31820)

    Low-alloy, high-strength steel used in naval applications.
    Active corrosion material (pitting, not passivating).

    Reactions:
    - Cathodic: ORR, HER
    - Anodic: Fe oxidation (Fe → Fe²⁺ + 2e⁻), Pitting
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        super().__init__(name, chloride_M, temperature_C, pH, velocity_m_s)

        # Material properties
        self.metal_mass = 55.845  # Molar mass of Fe, g/mol
        self.oxidation_level_z = 2  # Fe → Fe²⁺ + 2e⁻

        # Pitting properties
        self.pit_potential = -0.2  # V_SCE
        self.delta_g_metal_pitting = self.calculate_delta_g(
            "Pitting", chloride_M, pH, temperature_C
        )
        self.beta_metal_pitting = 0.9999

        # Metal oxidation
        self.delta_g_metal_oxidation = self.calculate_delta_g(
            "Oxidation", chloride_M, pH, temperature_C
        )
        self.beta_metal_oxidation = 0.3

        # Oxygen reduction reaction (cathodic)
        self.delta_g_orr = self.calculate_delta_g(
            "ORR", chloride_M, pH, temperature_C
        )
        self.beta_orr = 0.89
        self.del_orr = 0.085  # cm

        # Hydrogen evolution reaction (cathodic)
        self.delta_g_her = self.calculate_delta_g(
            "HER", chloride_M, pH, temperature_C
        )
        self.beta_her = 0.7
        self.del_her = 0.15  # cm

        # Oxide film properties (Cr₂O₃ - default values)
        self.oxide_mass = 151.99  # g/mol
        self.oxide_density = 5.22  # g/cm³
        self.resistivity_of_oxide = 5000.0e9  # Ω·cm
        self.passive_current_density = 1.0e-6  # A/cm²
        self.passive_film_thickness = 2.5e-7  # cm

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """Calculate activation energies for HY-80 steel."""

        if reaction_type == "ORR":
            # Load ORR coefficients
            coeffs = self._load_csv_coefficients("HY80ORRCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            # Apply pH correction (linear interpolation from pH 1 to 13)
            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 800.0e4  # J/mol (high barrier for reverse reaction)

        elif reaction_type == "HER":
            # Load HER coefficients
            coeffs = self._load_csv_coefficients("HY80HERCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            # Apply pH correction
            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 1000.0e4  # J/mol

        elif reaction_type == "Oxidation":
            # Load Fe oxidation coefficients
            coeffs = self._load_csv_coefficients("HY80FeOxCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            # Apply pH correction
            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_min - dg_a_max) / (13.0 - 1.0)
            dg_anodic = m * (pH - 13.0) + dg_a_min

            dg_cathodic = 80.0e4  # J/mol

        elif reaction_type == "Pitting":
            # Load pitting coefficients
            coeffs = self._load_csv_coefficients("HY80PitCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            # Apply pH correction (note: different slope direction for pitting)
            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_max - dg_a_min) / (13.0 - 1.0)
            dg_anodic = m * (pH - 1.0) + dg_a_min

            dg_cathodic = 20.0e4  # J/mol

        else:
            raise ValueError(
                f"Reaction type '{reaction_type}' not supported for HY-80"
            )

        # Validate and clamp negative activation energies (Codex recommendation)
        return self._validate_activation_energy(
            dg_cathodic, dg_anodic, reaction_type, chloride_M, temperature_C, pH
        )


class HY100(CorrodingMetal):
    """
    HY-100 High-Yield Steel (UNS K32045)

    Similar to HY-80 but higher strength.
    Active corrosion material (pitting, not passivating).

    Reactions:
    - Cathodic: ORR, HER
    - Anodic: Fe oxidation, Pitting
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        super().__init__(name, chloride_M, temperature_C, pH, velocity_m_s)

        # Material properties
        self.metal_mass = 55.845  # Molar mass of Fe, g/mol
        self.oxidation_level_z = 2  # Fe → Fe²⁺ + 2e⁻

        # Pitting properties
        self.pit_potential = -0.2  # V_SCE
        self.delta_g_metal_pitting = self.calculate_delta_g(
            "Pitting", chloride_M, pH, temperature_C
        )
        self.beta_metal_pitting = 0.9999

        # Metal oxidation
        self.delta_g_metal_oxidation = self.calculate_delta_g(
            "Oxidation", chloride_M, pH, temperature_C
        )
        self.beta_metal_oxidation = 0.3

        # ORR
        self.delta_g_orr = self.calculate_delta_g(
            "ORR", chloride_M, pH, temperature_C
        )
        self.beta_orr = 0.89
        self.del_orr = 0.085  # cm

        # HER
        self.delta_g_her = self.calculate_delta_g(
            "HER", chloride_M, pH, temperature_C
        )
        self.beta_her = 0.72  # Slightly different from HY-80
        self.del_her = 0.15  # cm

        # Oxide film properties
        self.oxide_mass = 151.99  # g/mol
        self.oxide_density = 5.22  # g/cm³
        self.resistivity_of_oxide = 5000.0e9  # Ω·cm
        self.passive_current_density = 1.0e-6  # A/cm²
        self.passive_film_thickness = 2.5e-7  # cm

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """Calculate activation energies for HY-100 steel."""

        if reaction_type == "ORR":
            coeffs = self._load_csv_coefficients("HY100ORRCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 800.0e4

        elif reaction_type == "HER":
            coeffs = self._load_csv_coefficients("HY100HERCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 1000.0e4

        elif reaction_type == "Oxidation":
            coeffs = self._load_csv_coefficients("HY100FeOxCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_min - dg_a_max) / (13.0 - 1.0)
            dg_anodic = m * (pH - 13.0) + dg_a_min

            dg_cathodic = 80.0e4

        elif reaction_type == "Pitting":
            coeffs = self._load_csv_coefficients("HY100PitCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_max - dg_a_min) / (13.0 - 1.0)
            dg_anodic = m * (pH - 1.0) + dg_a_min

            dg_cathodic = 20.0e4

        else:
            raise ValueError(
                f"Reaction type '{reaction_type}' not supported for HY-100"
            )

        # Validate and clamp negative activation energies (Codex recommendation)
        return self._validate_activation_energy(
            dg_cathodic, dg_anodic, reaction_type, chloride_M, temperature_C, pH
        )


class SS316(CorrodingMetal):
    """
    SS 316 Stainless Steel (UNS S31600)

    Austenitic chromium-nickel stainless steel.
    Passivating material with pitting susceptibility.

    Reactions:
    - Cathodic: ORR, HER
    - Anodic: Passivation (Cr₂O₃ film), Pitting
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        super().__init__(name, chloride_M, temperature_C, pH, velocity_m_s)

        # Material properties
        self.metal_mass = 51.9961  # Molar mass of Cr, g/mol (rate-limiting element)
        self.oxidation_level_z = 3  # Cr → Cr³⁺ + 3e⁻

        # Pitting properties
        self.pit_potential = -0.2  # V_SCE
        self.delta_g_metal_pitting = self.calculate_delta_g(
            "Pitting", chloride_M, pH, temperature_C
        )
        self.beta_metal_pitting = 0.9999

        # Passivation
        self.delta_g_metal_passivation = self.calculate_delta_g(
            "Passivation", chloride_M, pH, temperature_C
        )
        self.beta_metal_passivation = 0.6

        # ORR
        self.delta_g_orr = self.calculate_delta_g(
            "ORR", chloride_M, pH, temperature_C
        )
        self.beta_orr = 0.89
        self.del_orr = 0.085  # cm

        # HER
        self.delta_g_her = self.calculate_delta_g(
            "HER", chloride_M, pH, temperature_C
        )
        self.beta_her = 0.8
        self.del_her = 0.15  # cm

        # Oxide film properties (Cr₂O₃)
        self.oxide_mass = 151.99  # g/mol
        self.oxide_density = 5.22  # g/cm³
        self.resistivity_of_oxide = 5000.0e9  # Ω·cm
        self.passive_current_density = 1.0e-3  # A/cm² (higher than Ti/I625)
        self.passive_film_thickness = 2.5e-7  # cm

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """Calculate activation energies for SS 316."""

        pH_max = 13.0
        pH_min = 1.0

        if reaction_type == "ORR":
            coeffs = self._load_csv_coefficients("SS316ORRCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (pH_max - pH_min)
            dg_cathodic = m * (pH - pH_max) + dg_c_min

            dg_anodic = 800.0e4

        elif reaction_type == "HER":
            coeffs = self._load_csv_coefficients("SS316HERCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (pH_max - pH_min)
            dg_cathodic = m * (pH - pH_max) + dg_c_min

            dg_anodic = 1000.0e4

        elif reaction_type == "Oxidation":
            # SS316 does not have active oxidation (only passivation)
            dg_anodic = 0.0
            dg_cathodic = 0.0

        elif reaction_type == "Passivation":
            coeffs = self._load_csv_coefficients("SS316PassCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_min - dg_a_max) / (pH_max - pH_min)
            dg_anodic = m * (pH - pH_max) + dg_a_min

            dg_cathodic = 100.0e4

        elif reaction_type == "Pitting":
            coeffs = self._load_csv_coefficients("SS316PitCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_max - dg_a_min) / (pH_max - pH_min)
            dg_anodic = m * (pH - pH_min) + dg_a_min

            dg_cathodic = 20.0e4

        else:
            raise ValueError(
                f"Reaction type '{reaction_type}' not supported for SS 316"
            )

        # Validate and clamp negative activation energies (Codex recommendation)
        return self._validate_activation_energy(
            dg_cathodic, dg_anodic, reaction_type, chloride_M, temperature_C, pH
        )


class Ti(CorrodingMetal):
    """
    Titanium (UNS R50700)

    Highly corrosion-resistant passivating metal.
    Forms protective TiO₂ film.

    Reactions:
    - Cathodic: ORR, HER
    - Anodic: Passivation (TiO₂ film)
    - Does NOT pit or actively corrode in seawater
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        super().__init__(name, chloride_M, temperature_C, pH, velocity_m_s)

        # Material properties
        self.metal_mass = 47.88  # Molar mass of Ti, g/mol
        self.oxidation_level_z = 3  # Ti → Ti³⁺ + 3e⁻

        # Passivation
        self.delta_g_metal_passivation = self.calculate_delta_g(
            "Passivation", chloride_M, pH, temperature_C
        )
        self.beta_metal_passivation = 0.3

        # ORR
        self.delta_g_orr = self.calculate_delta_g(
            "ORR", chloride_M, pH, temperature_C
        )
        self.beta_orr = 0.65
        self.del_orr = 0.085  # cm

        # HER
        self.delta_g_her = self.calculate_delta_g(
            "HER", chloride_M, pH, temperature_C
        )
        self.beta_her = 0.75
        self.del_her = 0.15  # cm

        # Oxide film properties (TiO₂)
        self.oxide_mass = 143.76  # g/mol
        self.oxide_density = 4.49  # g/cm³
        self.resistivity_of_oxide = 50000.0e9  # Ω·cm (very resistive)
        self.passive_current_density = 1.0e-6  # A/cm²
        self.passive_film_thickness = 2.5e-7  # cm

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """Calculate activation energies for titanium."""

        if reaction_type == "ORR":
            coeffs = self._load_csv_coefficients("TiORRCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 800.0e4

        elif reaction_type == "HER":
            coeffs = self._load_csv_coefficients("TiHERCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 1000.0e4

        elif reaction_type == "Oxidation":
            # Ti does not have active oxidation (only passivation)
            dg_anodic = 0.0
            dg_cathodic = 0.0

        elif reaction_type == "Passivation":
            coeffs = self._load_csv_coefficients("TiPassCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_min - dg_a_max) / (13.0 - 1.0)
            dg_anodic = m * (pH - 13.0) + dg_a_min

            dg_cathodic = 80.0e4

        else:
            raise ValueError(
                f"Reaction type '{reaction_type}' not supported for titanium"
            )

        # Validate and clamp negative activation energies (Codex recommendation)
        return self._validate_activation_energy(
            dg_cathodic, dg_anodic, reaction_type, chloride_M, temperature_C, pH
        )


class I625(CorrodingMetal):
    """
    Inconel 625 (UNS N06625)

    Nickel-chromium-molybdenum superalloy.
    Highly corrosion-resistant passivating material.

    Reactions:
    - Cathodic: ORR, HER
    - Anodic: Passivation (Cr₂O₃ + NiO film)
    - Does NOT pit in seawater

    Note: Includes velocity-dependent diffusion layer for ORR.
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        super().__init__(name, chloride_M, temperature_C, pH, velocity_m_s)

        # Material properties
        self.metal_mass = 58.6934  # Molar mass of Ni, g/mol (primary element)
        self.oxidation_level_z = 3  # Ni → Ni³⁺ + 3e⁻ (in passive film)

        # Passivation
        self.delta_g_metal_passivation = self.calculate_delta_g(
            "Passivation", chloride_M, pH, temperature_C
        )
        self.beta_metal_passivation = 0.21

        # ORR
        self.delta_g_orr = self.calculate_delta_g(
            "ORR", chloride_M, pH, temperature_C
        )
        self.beta_orr = 0.89

        # Velocity-dependent diffusion layer thickness
        liq_v0 = 50.0  # Reference velocity, m/s
        self.del_orr = 0.085 * (1.0 - (velocity_m_s / liq_v0))  # cm

        # HER
        self.delta_g_her = self.calculate_delta_g(
            "HER", chloride_M, pH, temperature_C
        )
        self.beta_her = 0.7
        self.del_her = 0.15  # cm

        # Oxide film properties (Cr₂O₃ + NiO)
        self.oxide_mass = 165.39  # g/mol
        self.oxide_density = 4.84  # g/cm³
        self.resistivity_of_oxide = 50000.0e9  # Ω·cm
        self.passive_current_density = 1.0e-6  # A/cm²
        self.passive_film_thickness = 2.5e-7  # cm

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """Calculate activation energies for Inconel 625."""

        if reaction_type == "ORR":
            coeffs = self._load_csv_coefficients("I625ORRCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 800.0e4

        elif reaction_type == "HER":
            coeffs = self._load_csv_coefficients("I625HERCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 1000.0e4

        elif reaction_type == "Oxidation":
            # I625 does not have active oxidation (only passivation)
            dg_anodic = 0.0
            dg_cathodic = 0.0

        elif reaction_type == "Passivation":
            coeffs = self._load_csv_coefficients("I625PassCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_min - dg_a_max) / (13.0 - 1.0)
            dg_anodic = m * (pH - 13.0) + dg_a_min

            dg_cathodic = 80.0e4

        else:
            raise ValueError(
                f"Reaction type '{reaction_type}' not supported for Inconel 625"
            )

        # Validate and clamp negative activation energies (Codex recommendation)
        return self._validate_activation_energy(
            dg_cathodic, dg_anodic, reaction_type, chloride_M, temperature_C, pH
        )


class CuNi(CorrodingMetal):
    """
    CuNi 70-30 (Copper-Nickel Alloy, UNS C71500)

    70% Cu, 30% Ni alloy used in marine applications.
    Active corrosion material (Cu oxidation).

    Reactions:
    - Cathodic: ORR, HER
    - Anodic: Cu oxidation (Cu → Cu⁺ + e⁻)
    - Does NOT form passive film

    Note: Includes velocity-dependent diffusion layer for ORR.
    """

    def __init__(
        self,
        name: str,
        chloride_M: float,
        temperature_C: float,
        pH: float,
        velocity_m_s: float = 0.0
    ):
        super().__init__(name, chloride_M, temperature_C, pH, velocity_m_s)

        # Material properties
        self.metal_mass = 63.546  # Molar mass of Cu, g/mol
        self.oxidation_level_z = 1  # Cu → Cu⁺ + e⁻

        # Metal oxidation (Cu)
        self.delta_g_metal_oxidation = self.calculate_delta_g(
            "Oxidation", chloride_M, pH, temperature_C
        )
        self.beta_metal_oxidation = 0.7

        # ORR
        self.delta_g_orr = self.calculate_delta_g(
            "ORR", chloride_M, pH, temperature_C
        )
        self.beta_orr = 0.72

        # Velocity-dependent diffusion layer thickness
        liq_v0 = 7.5  # Reference velocity, m/s (lower than I625)
        self.del_orr = 0.085 * (1.0 - (velocity_m_s / liq_v0))  # cm

        # HER
        self.delta_g_her = self.calculate_delta_g(
            "HER", chloride_M, pH, temperature_C
        )
        self.beta_her = 0.6
        self.del_her = 0.15  # cm

        # Oxide film properties (not used for CuNi, but set for consistency)
        self.oxide_mass = 151.99  # g/mol
        self.oxide_density = 5.22  # g/cm³
        self.resistivity_of_oxide = 5000.0e9  # Ω·cm
        self.passive_current_density = 1.0e-6  # A/cm²
        self.passive_film_thickness = 2.5e-7  # cm

    def calculate_delta_g(
        self,
        reaction_type: Literal["ORR", "HER", "Oxidation", "Passivation", "Pitting"],
        chloride_M: float,
        pH: float,
        temperature_C: float
    ) -> Tuple[float, float]:
        """Calculate activation energies for CuNi 70-30."""

        if reaction_type == "ORR":
            coeffs = self._load_csv_coefficients("cuniORRCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 800.0e4

        elif reaction_type == "HER":
            coeffs = self._load_csv_coefficients("cuniHERCoeffs.csv")
            dg_cathodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_c_max = 1.1 * dg_cathodic_no_pH
            dg_c_min = 0.9 * dg_cathodic_no_pH
            m = (dg_c_min - dg_c_max) / (13.0 - 1.0)
            dg_cathodic = m * (pH - 13.0) + dg_c_min

            dg_anodic = 1000.0e4

        elif reaction_type == "Oxidation":
            coeffs = self._load_csv_coefficients("cuniCuOxCoeffs.csv")
            dg_anodic_no_pH = self._apply_polynomial_response_surface(
                coeffs, chloride_M, temperature_C
            )

            dg_a_max = 1.1 * dg_anodic_no_pH
            dg_a_min = 0.9 * dg_anodic_no_pH
            m = (dg_a_min - dg_a_max) / (13.0 - 1.0)
            dg_anodic = m * (pH - 13.0) + dg_a_min

            dg_cathodic = 25.0e4

        else:
            raise ValueError(
                f"Reaction type '{reaction_type}' not supported for CuNi 70-30"
            )

        # Validate and clamp negative activation energies (Codex recommendation)
        return self._validate_activation_energy(
            dg_cathodic, dg_anodic, reaction_type, chloride_M, temperature_C, pH
        )


# Material factory function
def create_material(
    material_name: str,
    chloride_M: float,
    temperature_C: float,
    pH: float,
    velocity_m_s: float = 0.0
) -> CorrodingMetal:
    """
    Factory function to create material instances by name.

    Args:
        material_name: Material identifier (case-insensitive)
        chloride_M: Chloride ion concentration, M
        temperature_C: Temperature, °C
        pH: pH value (1-13)
        velocity_m_s: Liquid velocity, m/s (default 0.0)

    Returns:
        Instance of appropriate material class

    Raises:
        ValueError: If material_name not recognized

    Supported Materials:
    - "HY80", "HY-80", "HY_80" → HY80
    - "HY100", "HY-100", "HY_100" → HY100
    - "SS316", "316", "SS_316", "316L" → SS316
    - "Ti", "Titanium" → Ti
    - "I625", "Inconel625", "Inconel_625" → I625
    - "CuNi", "CuNi7030", "CuNi_70_30" → CuNi
    """
    material_map = {
        "HY80": HY80,
        "HY-80": HY80,
        "HY_80": HY80,
        "HY100": HY100,
        "HY-100": HY100,
        "HY_100": HY100,
        "SS316": SS316,
        "316": SS316,
        "SS_316": SS316,
        "316L": SS316,
        "TI": Ti,
        "TITANIUM": Ti,
        "I625": I625,
        "INCONEL625": I625,
        "INCONEL_625": I625,
        "CUNI": CuNi,
        "CUNI7030": CuNi,
        "CUNI_70_30": CuNi,
    }

    material_key = material_name.upper().replace(" ", "_")

    if material_key not in material_map:
        supported = list(set([k.split("_")[0] for k in material_map.keys()]))
        raise ValueError(
            f"Unknown material: '{material_name}'\n"
            f"Supported materials: {supported}"
        )

    material_class = material_map[material_key]

    return material_class(
        name=material_name,
        chloride_M=chloride_M,
        temperature_C=temperature_C,
        pH=pH,
        velocity_m_s=velocity_m_s
    )


__all__ = [
    "CorrodingMetal",
    "HY80",
    "HY100",
    "SS316",
    "Ti",
    "I625",
    "CuNi",
    "create_material",
]
