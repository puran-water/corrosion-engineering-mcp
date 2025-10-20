"""
NaCl Solution Chemistry Module

This module provides temperature and chloride-dependent calculations for:
- Dissolved oxygen concentration (Henry's law with salinity correction)
- Oxygen diffusivity (Stokes model)
- Solution conductivity (Wadsworth 2012 polynomial)
- Water activity (activity coefficient model)

PROVENANCE:
-----------
This is a 1:1 translation of naclSolutionChemistry.m from:

    Repository: USNavalResearchLaboratory/corrosion-modeling-applications
    Path: polarization-curve-modeling/naclSolutionChemistry.m
    Author: Steven A. Policastro, Ph.D., Materials Science
            Center for Corrosion Science and Engineering
            U.S. Naval Research Laboratory
    Email: steven.policastro@nrl.navy.mil
    License: Public domain (U.S. Federal Government work)
    Retrieved: 2025-10-19

REFERENCES:
-----------
1. Oxygen solubility:
   - Acentric factor correlation with Henry's constant
   - Temperature and salinity dependent

2. Oxygen diffusivity:
   - Stokes viscosity model
   - Empirical temperature-dependent parameters

3. Solution conductivity:
   - Wadsworth, J.C. (2012) "The Statistical Description of Precision
     Conductivity Data for Aqueous Sodium Chloride"
     J. Solution Chem. 41:715-729
     https://doi.org/10.1007/s10953-012-9823-6

4. Water activity:
   - Empirical activity coefficient correlation for NaCl solutions

TRANSLATION NOTES:
------------------
- All numerical constants preserved exactly from MATLAB source
- Temperature handling: Supports input in °C or K (auto-detects if >= 273.15)
- Unit conversions match MATLAB implementation exactly
- LinearLinear rational function: (b0 + b1*x) / (1 + b2*x)

NO UNDOCUMENTED HEURISTICS: All equations and constants from authoritative
sources via NRL MATLAB code.
"""

import numpy as np
from typing import Union
from utils.nrl_constants import C


class NaClSolutionChemistry:
    """
    NaCl solution chemistry calculations for corrosion modeling.

    This class calculates temperature and chloride-dependent properties:
    - Dissolved oxygen concentration (g/cm³)
    - Oxygen diffusivity (cm²/s)
    - Solution conductivity (S/m)
    - Water activity (mol/L)

    All methods are 1:1 translations from NRL naclSolutionChemistry.m

    Parameters
    ----------
    chloride_M : float
        Chloride ion concentration (mol/L)
    temperature_C : float
        Temperature (°C or K - auto-detected if >= 273.15)

    Attributes
    ----------
    c_O2 : float
        Dissolved oxygen concentration (g/cm³)
    d_O2 : float
        Oxygen diffusivity (cm²/s)
    rho_NaCl : float
        Solution resistivity (Ohm·m)
    a_water : float
        Water activity (mol/L)
    chloride_M : float
        Chloride concentration (mol/L)
    temperature_C : float
        Temperature (°C)

    Examples
    --------
    >>> # Seawater at 25°C
    >>> soln = NaClSolutionChemistry(chloride_M=0.54, temperature_C=25.0)
    >>> print(f"O2 diffusivity: {soln.d_O2:.2e} cm²/s")
    >>> print(f"O2 concentration: {soln.c_O2:.2e} g/cm³")
    >>> print(f"Water activity: {soln.a_water:.2f} mol/L")
    """

    def __init__(self, chloride_M: float, temperature_C: float):
        """Initialize NaCl solution chemistry with given conditions."""
        self.chloride_M = chloride_M
        self.temperature_C = temperature_C

        # Calculate all properties (matching MATLAB constructor)
        self.c_O2 = self._calc_conc_O2(temperature_C, chloride_M)  # g/cm³
        self.d_O2 = self._calc_diff_O2(temperature_C, chloride_M)  # cm²/s
        conductivity_S_m = self._calc_soln_cond(temperature_C, chloride_M)  # S/m
        self.rho_NaCl = 1.0 / conductivity_S_m  # Ohm·m (resistivity)
        self.a_water = self._water_activity(chloride_M)  # mol/L

    # ============================================================================
    # OXYGEN CONCENTRATION (Henry's Law with Salinity Correction)
    # ============================================================================

    def _calc_conc_O2(
        self,
        temperature: Union[float, np.ndarray],
        chloride_M: Union[float, np.ndarray]
    ) -> Union[float, np.ndarray]:
        """
        Calculate dissolved oxygen concentration in NaCl solution.

        Uses acentric factor correlation with Henry's constant, temperature
        and salinity dependent.

        PROVENANCE: 1:1 translation of naclSolutionChemistry.m::calcConcO2()

        Parameters
        ----------
        temperature : float or array
            Temperature (°C or K - auto-detected)
        chloride_M : float or array
            Chloride concentration (mol/L)

        Returns
        -------
        c_O2 : float or array
            Dissolved oxygen concentration (g/cm³)

        Notes
        -----
        - Acentric factor for O2: 0.022
        - Atmospheric O2 partial pressure from C.cO2
        - All coefficients from NRL MATLAB source
        """
        # Convert Cl⁻ molarity to salinity (mg/L)
        molecular_mass_Cl = C.M_Cl * C.convertGtoKg  # kg/mol

        # Handle temperature units (C or K)
        if np.any(temperature >= C.convertCtoK):
            temperature_K = temperature
        else:
            temperature_K = temperature + C.convertCtoK

        Cl_molality_kg = molecular_mass_Cl * chloride_M
        Cl_molality_mg = Cl_molality_kg * C.convertKgtoMg

        # Henry's constant correlation coefficients
        a1 = 31820.0
        b1 = -229.9
        c1 = -19.12
        d1 = 0.3081

        a2 = -1409.0
        b2 = 10.4
        c2 = 0.8628
        d2 = -0.0005235

        d3 = 0.07464

        acentric_factor_O2 = 0.022

        # Calculate ln(H_s,0) - Henry's constant in pure water
        num1 = (a1 * acentric_factor_O2) + a2
        num2 = (b1 * acentric_factor_O2) + b2
        denom1 = (c1 * acentric_factor_O2) + c2
        denom2 = 1.0 + (denom1 * temperature_K)

        Ln_H_s_0 = (num1 + (num2 * temperature_K)) / denom2

        # Salinity correction to Henry's constant
        num3 = d1 + (d2 * temperature_K)
        denom3 = 1.0 + (d3 * temperature_K)

        salinity_factor = 0.001
        salinity = salinity_factor * Cl_molality_mg
        exp_term = (num3 / denom3) * salinity

        Ln_H_s = Ln_H_s_0 + exp_term

        # Calculate O2 mole fraction from Henry's law
        K_H = np.exp(Ln_H_s)
        x1 = C.cO2 / K_H  # mol/L

        # Convert to g/cm³
        molecular_mass_O2 = C.M_O2 * C.convertGtoKg  # kg/mol
        x1_g_L = x1 * (molecular_mass_O2 / C.convertGtoKg)
        x1_g_cm3 = x1_g_L / C.convertLtoCm3

        return x1_g_cm3  # g/cm³

    # ============================================================================
    # OXYGEN DIFFUSIVITY (Stokes Model)
    # ============================================================================

    def _calc_diff_O2(
        self,
        temperature: Union[float, np.ndarray],
        chloride_M: Union[float, np.ndarray]
    ) -> Union[float, np.ndarray]:
        """
        Calculate oxygen diffusivity in NaCl solution using Stokes model.

        PROVENANCE: 1:1 translation of naclSolutionChemistry.m::calcDiffO2()

        Parameters
        ----------
        temperature : float or array
            Temperature (°C or K - auto-detected)
        chloride_M : float or array
            Chloride concentration (mol/L)

        Returns
        -------
        D_O2 : float or array
            Oxygen diffusivity (cm²/s)

        Notes
        -----
        - Uses Stokes viscosity model with empirical parameters
        - Parameters are temperature-dependent rational functions
        - All 6 parameter sets from NRL MATLAB source
        """
        # Handle scalar vs array inputs
        temperature = np.atleast_1d(temperature)
        chloride_M = np.atleast_1d(chloride_M)

        len_T = len(temperature)
        len_C = len(chloride_M)

        if len_T >= len_C:
            N = len_T
            D_O2 = np.zeros(len_T)
        else:
            N = len_C
            D_O2 = np.zeros(len_C)

        for j in range(N):
            # Get temperature in Kelvin
            T_idx = min(j, len_T - 1)
            if temperature[T_idx] >= C.convertCtoK:
                T_K = temperature[T_idx]
            else:
                T_K = temperature[T_idx] + C.convertCtoK

            # Parameters for Stokes model (6 sets of [b0, b1, b2] for LinearLinear)
            # Each row: [b0, b1, b2] for rational function (b0 + b1*T) / (1 + b2*T)
            params = np.array([
                [0.193015581, -0.000936823, -3738.145703],
                [0.586220598, -0.001982362, -0.003767555],
                [-2058331786, 7380780.538, -725742.0949],
                [-12341118, 7397.380585, -1024619.196],
                [-0.082481761, 8.05605E-06, -0.005230993],
                [-13685.50552, 11.9799009, -0.05822883]
            ])

            num_model_parameters = params.shape[0]
            b = np.zeros(num_model_parameters)

            # Calculate temperature-dependent b parameters
            for i in range(num_model_parameters):
                b[i] = self._linear_linear(params[i, :], T_K)

            # Calculate diffusivity using Stokes model
            C_idx = min(j, len_C - 1)
            D_O2[j] = self._stokes_model2(b, T_K, chloride_M[C_idx])

        # Return scalar if input was scalar
        if len(D_O2) == 1:
            return float(D_O2[0])
        return D_O2

    def _stokes_model2(
        self,
        b: np.ndarray,
        temperature_K: float,
        chloride_M: float
    ) -> float:
        """
        Stokes viscosity model for O2 diffusivity.

        PROVENANCE: 1:1 translation of naclSolutionChemistry.m::StokesModel2()

        Parameters
        ----------
        b : array (6,)
            Temperature-dependent model parameters
        temperature_K : float
            Temperature (K)
        chloride_M : float
            Chloride concentration (mol/L)

        Returns
        -------
        D : float
            Oxygen diffusivity (cm²/s)

        Notes
        -----
        - phi = 2.6 (empirical constant)
        - Uses NRL constants for M_H2O, V_O2
        - Viscosity η = η₀ * (1 + A*√Cl + B*Cl)
        - D ∝ √(M_H2O) * T / η^0.6
        """
        phi = 2.6  # Empirical constant

        # Viscosity of pure water
        eta0 = b[4] * np.exp(b[5] / temperature_K)

        # Temperature-dependent coefficient
        B = b[2] + b[3] * (temperature_K - C.convertCtoK)
        A = b[1]

        # Viscosity correction for NaCl
        eta = eta0 * (1.0 + A * np.sqrt(chloride_M) + B * chloride_M)

        # Stokes diffusivity formula
        D = b[0] * ((np.sqrt(phi * C.M_H2O) * temperature_K) / ((C.VO2 * eta)**0.6))

        return D  # cm²/s

    @staticmethod
    def _linear_linear(b: np.ndarray, x: float) -> float:
        """
        Linear-linear rational function: (b0 + b1*x) / (1 + b2*x)

        PROVENANCE: 1:1 translation of Constants.m::LinearLinear()

        Parameters
        ----------
        b : array (3,)
            Coefficients [b0, b1, b2]
        x : float
            Domain value

        Returns
        -------
        y : float
            Range value
        """
        num = b[0] + b[1] * x
        denom = 1.0 + b[2] * x
        return num / denom

    # ============================================================================
    # SOLUTION CONDUCTIVITY (Wadsworth 2012)
    # ============================================================================

    def _calc_soln_cond(
        self,
        temperature: Union[float, np.ndarray],
        chloride_M: Union[float, np.ndarray]
    ) -> Union[float, np.ndarray]:
        """
        Calculate NaCl solution conductivity.

        PROVENANCE: 1:1 translation of naclSolutionChemistry.m::calcSolnCond()

        REFERENCE:
        Wadsworth, J.C. (2012) "The Statistical Description of Precision
        Conductivity Data for Aqueous Sodium Chloride"
        J. Solution Chem. 41:715-729
        https://doi.org/10.1007/s10953-012-9823-6

        Parameters
        ----------
        temperature : float or array
            Temperature (°C or K - auto-detected, output in °C)
        chloride_M : float or array
            Chloride concentration (mol/L)

        Returns
        -------
        k : float or array
            Solution conductivity (S/m)

        Notes
        -----
        - Polynomial fit with many terms up to Cl^(9/2)
        - All 36 coefficients from Wadsworth (2012)
        - Input T in °C, output conductivity in S/m
        """
        # Ensure temperature is in Celsius
        if np.any(temperature >= C.convertCtoK):
            T_C = temperature - C.convertCtoK
        else:
            T_C = temperature

        # Wadsworth (2012) coefficients
        b0 = -0.014  # μS/cm

        # Lambda0 term (limiting conductivity)
        b10 = 66591.0
        b11 = 2172.2
        b12 = 9.1584
        Lambda0 = b10 + b11 * T_C + b12 * (T_C**2)

        # S term (Debye-Hückel-like)
        b13 = 37515.0
        b14 = -3471.9
        b15 = 69.11
        b16 = -1.0777
        S = b13 + (b14 * T_C) + (b15 * (T_C**2)) + b16 * (T_C**3)

        # E term (logarithmic)
        b17 = -23.47
        E = b17 * (T_C**2)

        # J1 term (quadratic concentration)
        b18 = 46091
        b19 = 8760
        b20 = -352.06
        b21 = 3.8403
        J1 = b18 + (b19 * T_C) + (b20 * (T_C**2)) + b21 * (T_C**3)

        # J2 term (c^(5/2))
        b22 = -77300
        b23 = -10646
        b24 = 481.02
        b25 = -4.9759
        J2 = b22 + (b23 * T_C) + (b24 * (T_C**2)) + b25 * (T_C**3)

        # J3 term (c^3)
        b26 = 98097
        b27 = 5539.6
        b28 = -242.12
        b29 = 2.6452
        J3 = b26 + (b27 * T_C) + (b28 * (T_C**2)) + b29 * (T_C**3)

        # J4 term (c^(7/2))
        b30 = -68419
        b31 = -1014.3
        b32 = 43.97
        b33 = -0.4871
        J4 = b30 + (b31 * T_C) + (b32 * (T_C**2)) + b33 * (T_C**3)

        # J5 term (c^4)
        b34 = 22654
        J5 = b34

        # J6 term (c^(9/2))
        b35 = -2799.6
        J6 = b35

        # Wadsworth polynomial (output in μS/cm)
        k1 = (b0 + (Lambda0 * chloride_M) - (S * (chloride_M**(3/2))) +
              (E * (chloride_M**2) * np.log(chloride_M)) + (J1 * (chloride_M**2)) +
              (J2 * (chloride_M**(5/2))) + (J3 * (chloride_M**3)) +
              (J4 * (chloride_M**(7/2))) + (J5 * (chloride_M**4)) +
              (J6 * (chloride_M**(9/2))))

        # Convert μS/cm to S/m
        k = k1 * (1.0e-6 / 0.01)  # S/m

        return k

    # ============================================================================
    # WATER ACTIVITY (Activity Coefficient Model)
    # ============================================================================

    def _water_activity(self, chloride_M: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """
        Calculate water activity in NaCl solution.

        PROVENANCE: 1:1 translation of naclSolutionChemistry.m::waterActivity()

        Parameters
        ----------
        chloride_M : float or array
            Chloride concentration (mol/L)

        Returns
        -------
        a_water : float or array
            Water activity (mol/L)

        Notes
        -----
        - Empirical activity coefficient correlation
        - γ = (c1 + c2*m) / (1 + c3*m) where m is molality
        - a_water = 55.55 mol/L * γ
        - Accounts for NaCl density correction
        """
        # Molar masses
        m_NaCl = C.M_NaCl * C.convertGtoKg  # kg/mol
        m_H2O = C.M_H2O * C.convertGtoKg  # kg/mol
        molarity_of_water = 55.55  # mol/L
        test_solution_volume = 1.0  # L

        # Mass of NaCl and water per liter
        mass_NaCl_per_vol = m_NaCl * chloride_M  # kg/L
        mass_H2O_per_vol = m_H2O * C.cH2O  # kg/L
        total_mass_per_vol = mass_NaCl_per_vol + mass_H2O_per_vol

        # Mass percent NaCl
        mass_percent_NaCl_in_sol = (mass_NaCl_per_vol / total_mass_per_vol) * 100

        # NaCl solution density correction
        d1 = 1.0001
        d2 = -0.0064603
        density_NaCl_sol = (d1 / (1.0 + (d2 * mass_percent_NaCl_in_sol)) *
                           (C.convertGtoKg * C.convertLtoCm3))  # kg/L

        mass_solution = density_NaCl_sol * test_solution_volume  # kg
        mass_solvent = mass_solution - (mass_NaCl_per_vol * test_solution_volume)  # kg
        mol_Cl = chloride_M * test_solution_volume

        # Molality (mol/kg solvent)
        conc_cl_molality = mol_Cl / mass_solvent

        # Activity coefficient correlation
        c1 = 1.0001
        c2 = -0.065634
        c3 = -0.033533

        activity_coefficient = (c1 + (c2 * conc_cl_molality)) / (1.0 + (c3 * conc_cl_molality))

        a_water = molarity_of_water * activity_coefficient

        return a_water  # mol/L


# ==============================================================================
# CONVENIENCE FUNCTIONS (for use in other modules)
# ==============================================================================

def calculate_oxygen_properties(
    temperature_C: float,
    chloride_M: float
) -> dict:
    """
    Calculate oxygen concentration and diffusivity for given conditions.

    Parameters
    ----------
    temperature_C : float
        Temperature (°C)
    chloride_M : float
        Chloride concentration (mol/L)

    Returns
    -------
    dict with keys:
        'c_O2_g_cm3' : float
            Dissolved oxygen concentration (g/cm³)
        'd_O2_cm2_s' : float
            Oxygen diffusivity (cm²/s)

    Examples
    --------
    >>> props = calculate_oxygen_properties(temperature_C=25.0, chloride_M=0.54)
    >>> print(f"O2 diffusivity: {props['d_O2_cm2_s']:.2e} cm²/s")
    """
    soln = NaClSolutionChemistry(chloride_M=chloride_M, temperature_C=temperature_C)
    return {
        'c_O2_g_cm3': soln.c_O2,
        'd_O2_cm2_s': soln.d_O2
    }


def calculate_all_properties(
    temperature_C: float,
    chloride_M: float
) -> dict:
    """
    Calculate all NaCl solution properties for given conditions.

    Parameters
    ----------
    temperature_C : float
        Temperature (°C)
    chloride_M : float
        Chloride concentration (mol/L)

    Returns
    -------
    dict with keys:
        'c_O2_g_cm3' : float
            Dissolved oxygen concentration (g/cm³)
        'd_O2_cm2_s' : float
            Oxygen diffusivity (cm²/s)
        'conductivity_S_m' : float
            Solution conductivity (S/m)
        'resistivity_Ohm_m' : float
            Solution resistivity (Ohm·m)
        'a_water_mol_L' : float
            Water activity (mol/L)
        'temperature_C' : float
            Temperature (°C)
        'chloride_M' : float
            Chloride concentration (mol/L)

    Examples
    --------
    >>> props = calculate_all_properties(temperature_C=25.0, chloride_M=0.54)
    >>> print(f"Conductivity: {props['conductivity_S_m']:.2f} S/m")
    >>> print(f"Water activity: {props['a_water_mol_L']:.2f} mol/L")
    """
    soln = NaClSolutionChemistry(chloride_M=chloride_M, temperature_C=temperature_C)
    return {
        'c_O2_g_cm3': soln.c_O2,
        'd_O2_cm2_s': soln.d_O2,
        'conductivity_S_m': 1.0 / soln.rho_NaCl,
        'resistivity_Ohm_m': soln.rho_NaCl,
        'a_water_mol_L': soln.a_water,
        'temperature_C': soln.temperature_C,
        'chloride_M': soln.chloride_M
    }
