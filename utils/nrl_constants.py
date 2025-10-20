"""
NRL Physical Constants and Conversion Factors

PROVENANCE:
Direct 1:1 translation from MATLAB code by:
Steven A. Policastro, Ph.D.
Center for Corrosion Science and Engineering
U.S. Naval Research Laboratory
4555 Overlook Avenue SW, Washington, DC 20375

Source: USNavalResearchLaboratory/corrosion-modeling-applications
File: polarization-curve-modeling/Constants.m
License: Public domain (U.S. Federal Government work)
Date: 2025-10-19
"""

import numpy as np


class NRLConstants:
    """
    Physical constants and conversion factors for electrochemical calculations.

    All values are exact transcriptions from the NRL MATLAB Constants class.
    """

    # Fundamental Physical Constants
    R = 8.314  # Ideal gas constant, J/(mol·K)
    F = 96485.3  # Faraday constant, C/mol
    kb = 1.38e-23  # Boltzmann's constant, m²·kg/(s²·K)
    planck_h = 6.626e-34  # Planck's constant, m²·kg/s
    eps0 = 8.85418782e-12  # Permittivity of free space, m⁻³·kg⁻¹·s⁴·A²
    e = 1.6e-19  # Charge on electron, C

    # Reference Electrode Conversion
    E_SHE_to_SCE = 0.244  # Convert V_SHE to V_SCE

    # Unit Conversions
    convertCtoK = 273.15  # Celsius to Kelvin
    convertGtoKg = 1.0 / 1000.0  # Grams to kilograms
    convertKgtoMg = 1000.0 * 1000.0  # Kilograms to milligrams
    convertLtoCm3 = 1000  # Liters to cm³ (or mL)
    convertMtoCm = 100  # Meters to cm
    convertCm2toM2 = 1.0e-4  # cm² to m²

    # Solution Concentrations
    cH2O = 55.55  # Concentration of water, mol/L
    cO2 = 0.209476  # Concentration of oxygen in air, mole fraction

    # Molar Masses (g/mol)
    M_H2 = 2.016
    M_OH = 17.008
    M_O2 = 32.0
    M_H2O = 18.01528
    M_Cl = 35.5
    M_NaCl = 58.4
    M_Cr = 51.9961
    M_Fe = 55.845
    M_Ni = 58.6934
    M_Cu = 63.546

    # Oxide Properties
    MCr2O3 = 151.99  # Molar mass of Cr₂O₃, g/mol
    densityCr2O3 = 5.22  # Density of Cr₂O₃, g/cm³
    rhoCr2O3_0 = 5000.0e9  # Resistivity of Cr₂O₃, Ω/cm
    iPassCr2O3 = 1.0e-6  # Passive current density for Cr₂O₃, A/cm²
    tCr2O3film = 2.5e-7  # Cr₂O₃ film thickness, cm

    # Diffusion Coefficients (cm²/s)
    D_H = 9.311e-5  # Diffusivity of H₃O⁺
    D_H2O = 2.299e-5  # Diffusivity of H₂O
    D_Fe = 2.5e-12  # Diffusivity of Fe in oxide

    # Dielectric Constants
    epsH2O = 80.1  # Water
    epsPolyurethane = 6.19  # Polyurethane
    epsEpoxy = 3.6  # Epoxy

    # Standard Electrode Potentials (V_SHE)
    e0_orr_acid = 1.223  # ORR in acidic solution
    e0_orr_2e_alk = -0.065  # ORR 2-electron in alkaline
    e0_orr_alk = 0.401  # ORR in neutral/alkaline solution
    e0_her_alk = -0.83  # HER in neutral/alkaline solution
    e0_her_acid = 0.0  # HER in acidic solution
    e0_me_ox = 0.0  # Generic metal oxidation
    e0_Cr_ox = -0.74  # Cr oxidation
    e0_Fe_ox = -0.501  # Fe oxidation
    e0_Ni_ox = -0.23  # Ni oxidation
    e0_Cu_ox = 0.52  # Cu oxidation

    # Electrons Transferred
    z_orr = 4  # ORR: O₂ + 2H₂O + 4e⁻ → 4OH⁻
    z_her = 2  # HER: 2H₂O + 2e⁻ → H₂ + 2OH⁻
    z_Cr_ox = 3  # Cr → Cr³⁺ + 3e⁻
    z_Fe_ox = 2  # Fe → Fe²⁺ + 2e⁻
    z_Cu_ox = 1  # Cu → Cu⁺ + e⁻
    z_Fe_red = 1  # Fe reduction (1 electron)
    z_Ni_ox = 2  # Ni → Ni²⁺ + 2e⁻

    # Molar Volumes (L/mol)
    VO2 = 22.414  # O₂
    VNaCl = 16.6  # NaCl

    @staticmethod
    def calculate_cH_and_cOH(pH: float) -> tuple[float, float]:
        """
        Calculate H⁺ and OH⁻ concentrations from pH.

        Args:
            pH: pH value

        Returns:
            Tuple of (cH, cOH) concentrations in mol/L

        Example:
            >>> cH, cOH = NRLConstants.calculate_cH_and_cOH(7.0)
            >>> print(f"cH = {cH:.2e} M, cOH = {cOH:.2e} M")
            cH = 1.00e-07 M, cOH = 1.00e-07 M
        """
        cH = 10.0 ** (-pH)  # mol/L
        cOH = 10.0 ** (-(14.0 - pH))  # mol/L
        return cH, cOH

    @staticmethod
    def linear_linear_rational(b: np.ndarray, x: np.ndarray) -> np.ndarray:
        """
        Linear-linear rational function.

        Args:
            b: Array of 3 parameters [b1, b2, b3]
            x: Domain values

        Returns:
            Range values y = (b1 + b2*x) / (1 + b3*x)

        Note:
            This function is used in the NRL MATLAB code but may not be
            actively used in the polarization curve calculations.
        """
        numerator = b[0] + b[1] * x
        denominator = 1.0 + b[2] * x
        return numerator / denominator


# Create module-level instance for easy access
C = NRLConstants()

__all__ = ['NRLConstants', 'C']
