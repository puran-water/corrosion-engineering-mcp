"""
NRL Electrochemical Reaction Classes for Butler-Volmer Kinetics

PROVENANCE:
Direct 1:1 translation from MATLAB code by:
Steven A. Policastro, Ph.D.
Center for Corrosion Science and Engineering
U.S. Naval Research Laboratory
4555 Overlook Avenue SW, Washington, DC 20375

Source: USNavalResearchLaboratory/corrosion-modeling-applications
Directory: polarization-curve-modeling/
Files:
- ElectrochemicalReductionReaction.m (cathodic reactions: ORR, HER)
- ElectrochemicalOxidationReaction.m (anodic reactions: oxidation, passivation, pitting)
- reactionNames.m (reaction enumeration)
License: Public domain (U.S. Federal Government work)
Date: 2025-10-19

This module implements Butler-Volmer electrochemical kinetics with:
- Activation-controlled current (Butler-Volmer equation)
- Diffusion-limited current (mass transport limit)
- Koutecky-Levich combined kinetics: i_total = (i_lim * i_act) / (i_act + i_lim)
- Passive film resistance correction (for passivation reactions)

Classes:
- ReactionType: Enumeration of reaction types
- CathodicReaction: ORR, HER (reduction reactions)
- AnodicReaction: Metal oxidation, passivation, pitting (oxidation reactions)
"""

from enum import Enum, auto
import numpy as np
from typing import Tuple, Optional
from .nrl_constants import C
from .nrl_materials import CorrodingMetal


class ReactionType(Enum):
    """
    Enumeration of electrochemical reaction types.

    Based on NRL MATLAB reactionNames enum.
    """
    HER = "HER"  # Hydrogen evolution reaction (cathodic)
    ORR = "ORR"  # Oxygen reduction reaction (cathodic)
    FE_OX = "Fe_Ox"  # Iron oxidation (anodic)
    FE_RED = "Fe_Red"  # Iron reduction (cathodic, rarely used)
    CR_OX = "Cr_Ox"  # Chromium oxidation (anodic)
    NI_OX = "Ni_Ox"  # Nickel oxidation (anodic)
    CU_OX = "Cu_Ox"  # Copper oxidation (anodic)
    PASSIVATION = "Passivation"  # Passive film formation (anodic)
    PITTING = "Pitting"  # Pitting corrosion (anodic)
    NONE = "None"  # No reaction


class CathodicReaction:
    """
    Cathodic (reduction) electrochemical reaction.

    Implements Butler-Volmer kinetics with diffusion limits for:
    - ORR: O₂ + 2H₂O + 4e⁻ → 4OH⁻
    - HER: 2H₂O + 2e⁻ → H₂ + 2OH⁻

    Key Equations:
    1. Butler-Volmer activation current:
       i_act = i0_anode * exp(α*z*F*η/RT) - i0_cathode * exp(-(1-α)*z*F*η/RT)

    2. Exchange current densities:
       i0 = z * F * λ₀ * exp(-ΔG / RT)
       where λ₀ = kB*T / h (Eyring rate constant)

    3. Diffusion-limited current:
       i_lim = -z * F * D * C_ox / (δ * M)

    4. Combined current (Koutecky-Levich):
       i_total = (i_lim * i_act) / (i_act + i_lim)
    """

    def __init__(
        self,
        reaction_type: ReactionType,
        c_oxidized: Tuple[float, float],
        c_reduced: Tuple[float, float],
        temperature_C: float,
        z: int,
        e0_SHE: float,
        diffusion_coefficient_cm2_s: float,
        applied_potentials_VSCE: np.ndarray,
        metal: CorrodingMetal
    ):
        """
        Initialize cathodic reaction.

        Args:
            reaction_type: Type of cathodic reaction (ORR or HER)
            c_oxidized: Concentrations of oxidized species (reactants), [c1, c2]
            c_reduced: Concentrations of reduced species (products), [c1, c2]
            temperature_C: Temperature, °C
            z: Number of electrons transferred
            e0_SHE: Standard electrode potential, V_SHE
            diffusion_coefficient_cm2_s: Diffusion coefficient, cm²/s
            applied_potentials_VSCE: Array of applied potentials, V_SCE
            metal: Material instance with electrochemical properties
        """
        self.reaction_type = reaction_type
        self.temperature_C = temperature_C
        self.temperature_K = temperature_C + C.convertCtoK
        self.z = z
        self.e0_SHE = e0_SHE

        # Eyring rate constant: λ₀ = kB*T / h
        self.lambda_0 = (C.kb * self.temperature_K) / C.planck_h  # s⁻¹

        # Concentrations
        self.c_oxidized = c_oxidized
        self.c_reduced = c_reduced

        # Diffusion properties
        self.diffusion_coefficient = diffusion_coefficient_cm2_s
        self.diffusion_length = 0.0  # Set by reaction type

        # Activation energies and transfer coefficient from material
        if reaction_type == ReactionType.ORR:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_orr
            self.alpha = metal.beta_orr  # Transfer coefficient (β in MATLAB)
            self.diffusion_length = metal.del_orr  # cm
        elif reaction_type == ReactionType.HER:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_her
            self.alpha = metal.beta_her
            self.diffusion_length = metal.del_her
        elif reaction_type == ReactionType.NONE:
            self.delta_g_cathodic = 0.0
            self.delta_g_anodic = 0.0
            self.alpha = 0.5
            self.diffusion_length = 1.0
        else:
            raise ValueError(
                f"Cathodic reaction type must be ORR, HER, or NONE, got {reaction_type}"
            )

        # Nernst potential calculation
        RT = C.R * self.temperature_K
        pre_factor = RT / (self.z * C.F)
        EN_log = np.log((c_reduced[0] * c_reduced[1]) / (c_oxidized[0] * c_oxidized[1]))
        self.EN_SHE = self.e0_SHE + (pre_factor * EN_log)  # V_SHE

        # Overpotential: η = E_applied - E_Nernst
        self.applied_potentials_VSCE = applied_potentials_VSCE
        self.eta = applied_potentials_VSCE - (self.EN_SHE - C.E_SHE_to_SCE)  # V

        # Exchange current densities
        RT = C.R * self.temperature_K
        pF = self.z * C.F * self.lambda_0

        self.i0_cathodic = pF * np.exp(-self.delta_g_cathodic / RT)  # A/cm²
        self.i0_anodic = pF * np.exp(-self.delta_g_anodic / RT)  # A/cm²

        # Butler-Volmer activation current
        if reaction_type == ReactionType.NONE:
            self.i_cathodic = np.zeros_like(self.eta)
            self.i_anodic = np.zeros_like(self.eta)
            self.i_act = np.zeros_like(self.eta)
        else:
            exp_val_cathodic = -(((1.0 - self.alpha) * self.z * C.F) * self.eta) / RT
            exp_val_anodic = ((self.alpha * self.z * C.F) * self.eta) / RT

            self.i_cathodic = self.i0_cathodic * np.exp(exp_val_cathodic)
            self.i_anodic = self.i0_anodic * np.exp(exp_val_anodic)
            self.i_act = self.i_anodic - self.i_cathodic  # Net activation current

        # Diffusion-limited current
        self.i_lim = self._calculate_diffusion_limit()

        # Combined current (Koutecky-Levich)
        if reaction_type == ReactionType.NONE:
            self.i_total = np.zeros_like(self.eta)
        else:
            self.i_total = (self.i_lim * self.i_act) / (self.i_act + self.i_lim)

    def _calculate_diffusion_limit(self) -> np.ndarray:
        """
        Calculate diffusion-limited current.

        i_lim = -z * F * D * C_ox / (δ * M)

        where:
        - z = electrons transferred
        - F = Faraday constant (C/mol)
        - D = diffusion coefficient (cm²/s)
        - C_ox = concentration of oxidized species (g/L)
        - δ = diffusion layer thickness (cm)
        - M = molar mass (g/mol)

        Returns:
            Diffusion-limited current density, A/cm² (negative for cathodic)
        """
        if self.reaction_type == ReactionType.HER:
            numerator = (
                self.z * C.F * self.diffusion_coefficient *
                (self.c_oxidized[0] / C.M_H2)
            )
        elif self.reaction_type == ReactionType.ORR:
            numerator = (
                self.z * C.F * self.diffusion_coefficient *
                (self.c_oxidized[0] / C.M_O2)
            )
        elif self.reaction_type == ReactionType.FE_RED:
            numerator = (
                self.z * C.F * self.diffusion_coefficient *
                (self.c_oxidized[0] / C.M_Fe)
            )
        elif self.reaction_type == ReactionType.NONE:
            numerator = 0.0
        else:
            raise ValueError(f"No diffusion limit for {self.reaction_type}")

        i_lim = np.ones_like(self.eta) * (-numerator / self.diffusion_length)
        return i_lim

    @staticmethod
    def get_koutecky_levich_current(i_lim: float, i_act: float) -> float:
        """
        Calculate combined activation + diffusion limited current.

        i_total = (i_lim * i_act) / (i_act + i_lim)

        Args:
            i_lim: Diffusion-limited current, A/cm²
            i_act: Activation-controlled current, A/cm²

        Returns:
            Combined current density, A/cm²
        """
        return (i_lim * i_act) / (i_act + i_lim)


class AnodicReaction:
    """
    Anodic (oxidation) electrochemical reaction.

    Implements Butler-Volmer kinetics for:
    - Metal oxidation: M → M^n+ + ne⁻
    - Passivation: Formation of protective oxide film
    - Pitting: Localized breakdown of passive film

    Key Features:
    1. Butler-Volmer activation current (same as cathodic)
    2. Passive film resistance correction (for passivation reactions)
    3. Film thickness growth kinetics (time-dependent)

    Passive Film Resistance Correction:
    For passivation, the current is reduced by oxide film resistance:
    i_corrected = i0_anode * exp(α*z*F*(η - i*R_film) / RT)

    This is solved iteratively using Newton-Raphson.
    """

    def __init__(
        self,
        reaction_type: ReactionType,
        c_reactants: Tuple[float, ...],
        c_products: Tuple[float, ...],
        temperature_C: float,
        applied_potentials_VSCE: np.ndarray,
        metal: CorrodingMetal
    ):
        """
        Initialize anodic reaction.

        Args:
            reaction_type: Type of anodic reaction (oxidation, passivation, pitting)
            c_reactants: Concentrations of reactants (metal)
            c_products: Concentrations of products (metal ions)
            temperature_C: Temperature, °C
            applied_potentials_VSCE: Array of applied potentials, V_SCE
            metal: Material instance with electrochemical properties
        """
        self.reaction_type = reaction_type
        self.temperature_C = temperature_C
        self.temperature_K = temperature_C + C.convertCtoK

        # Eyring rate constant
        self.lambda_0 = (C.kb * self.temperature_K) / C.planck_h

        # Get activation energies and transfer coefficient from material
        if reaction_type == ReactionType.CR_OX:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_metal_oxidation
            self.alpha = metal.beta_metal_oxidation
        elif reaction_type == ReactionType.FE_OX:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_metal_oxidation
            self.alpha = metal.beta_metal_oxidation
        elif reaction_type == ReactionType.CU_OX:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_metal_oxidation
            self.alpha = metal.beta_metal_oxidation
        elif reaction_type == ReactionType.NI_OX:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_metal_oxidation
            self.alpha = metal.beta_metal_oxidation
        elif reaction_type == ReactionType.PASSIVATION:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_metal_passivation
            self.alpha = metal.beta_metal_passivation
        elif reaction_type == ReactionType.PITTING:
            self.delta_g_cathodic, self.delta_g_anodic = metal.delta_g_metal_pitting
            self.alpha = metal.beta_metal_pitting
        else:
            raise ValueError(
                f"Anodic reaction type must be metal oxidation, passivation, or pitting, got {reaction_type}"
            )

        self.z = metal.oxidation_level_z  # Electrons transferred
        self.diffusion_length = -1.0  # Not used for anodic reactions

        # Nernst potential calculation
        RT = C.R * self.temperature_K
        pre_factor = RT / (self.z * C.F)

        # Calculate Nernst potential based on reaction type
        if reaction_type == ReactionType.CR_OX:
            c_g_cm3 = c_products[0] * C.M_Cr / 1000.0  # g/cm³
            EN_log = np.log(c_reactants[0] / c_g_cm3)
            self.EN_SHE = C.e0_Cr_ox + (pre_factor * EN_log)
        elif reaction_type == ReactionType.FE_OX:
            c_g_cm3 = c_products[0] * C.M_Fe / 1000.0
            EN_log = np.log(c_reactants[0] / c_g_cm3)
            self.EN_SHE = C.e0_Fe_ox + (pre_factor * EN_log)
        elif reaction_type == ReactionType.CU_OX:
            c_g_cm3 = c_products[0] * C.M_Cu / 1000.0
            EN_log = np.log(c_reactants[0] / c_g_cm3)
            self.EN_SHE = C.e0_Cu_ox + (pre_factor * EN_log)
        elif reaction_type == ReactionType.PASSIVATION:
            c_g_cm3 = c_products[0] * C.M_Cr / 1000.0
            EN_log = np.log(c_reactants[0] / c_g_cm3)
            self.EN_SHE = C.e0_Cr_ox + (pre_factor * EN_log)
        elif reaction_type == ReactionType.PITTING:
            c_g_cm3 = c_products[0] * C.M_Cr / 1000.0
            EN_log = np.log(c_reactants[0] / c_g_cm3)
            self.EN_SHE = C.e0_Cr_ox + (pre_factor * EN_log)
        else:
            raise ValueError(f"Unknown reaction type: {reaction_type}")

        # Overpotential
        self.applied_potentials_VSCE = applied_potentials_VSCE
        self.eta = applied_potentials_VSCE - (self.EN_SHE - C.E_SHE_to_SCE)

        # Exchange current densities
        RT = C.R * self.temperature_K
        pF = self.z * C.F * self.lambda_0

        self.i0_cathodic = pF * np.exp(-self.delta_g_cathodic / RT)
        self.i0_anodic = pF * np.exp(-self.delta_g_anodic / RT)

        # Butler-Volmer activation current
        i_act_cathodic = -self.i0_cathodic * np.exp(
            (-(1 - self.alpha) * self.z * C.F * self.eta) / RT
        )
        i_act_anodic = self.i0_anodic * np.exp(
            (self.alpha * self.z * C.F * self.eta) / RT
        )

        # Apply passive film resistance correction for passivation
        if reaction_type == ReactionType.PASSIVATION:
            # Calculate oxide film thickness
            h_oxide = self._calculate_film_thickness(metal, applied_potentials_VSCE)
            resistance = metal.resistivity_of_oxide * h_oxide  # Ω·cm²

            # Iteratively correct current for film resistance
            i_act_anodic = self._apply_film_resistance_correction(
                i_act_anodic,
                resistance,
                self.eta,
                self.i0_anodic,
                self.alpha,
                self.z,
                RT
            )

        self.i_cathodic = i_act_cathodic
        self.i_anodic = i_act_anodic
        self.i_total = self.i_cathodic + self.i_anodic

    def _calculate_film_thickness(
        self,
        metal: CorrodingMetal,
        applied_potentials_VSCE: np.ndarray
    ) -> np.ndarray:
        """
        Calculate passive oxide film thickness as function of time.

        Based on high-field conduction model with exponential film growth:
        h(t) = (1/A) * ln(1 + B*t)

        where:
        A = ε₂ * γ_ox
        B = A * (k_f / r) * i_pass * exp(γ_ox * ε₂_f * h_film)

        Args:
            metal: Material instance
            applied_potentials_VSCE: Applied potentials, V_SCE

        Returns:
            Film thickness array, cm
        """
        # Calculate time array from potential scan
        # Assuming linear potential scan at 0.167 mV/s
        total_time_s = ((applied_potentials_VSCE[-1] - applied_potentials_VSCE[0]) * 1000.0) / 0.167
        delta_t = total_time_s / len(applied_potentials_VSCE)
        t_oxide = np.arange(len(applied_potentials_VSCE)) * delta_t  # seconds

        # High-field conduction parameters
        a_ox = self.alpha
        RT = C.R * self.temperature_K

        # Field strength at film-solution interface
        eps2_f = (
            (RT / (a_ox * self.z * C.F * metal.passive_film_thickness)) *
            np.log(metal.passive_current_density / self.i0_anodic)
        )

        # Field coefficient
        gamma_ox = (a_ox * C.F) / RT

        # Film growth rate constant
        k_f = metal.oxide_mass / (self.z * C.F * metal.oxide_density)  # cm³/C
        r = 1.0  # Stoichiometric ratio

        # Empirical scaling factor (from NRL MATLAB code)
        multi = 1.40e2
        eps2 = multi * eps2_f

        A = eps2 * gamma_ox
        B = A * (k_f / r) * metal.passive_current_density * np.exp(
            gamma_ox * eps2_f * metal.passive_film_thickness
        )

        # Film thickness growth
        h_oxide = np.zeros_like(applied_potentials_VSCE)
        for j in range(len(applied_potentials_VSCE)):
            C1 = B * t_oxide[j]
            C2 = 1.0 + C1
            h_oxide[j] = (1.0 / A) * np.log(C2)

        return h_oxide

    def _apply_film_resistance_correction(
        self,
        i_act_anodic: np.ndarray,
        resistance: np.ndarray,
        eta: np.ndarray,
        i0_anodic: float,
        alpha: float,
        z: int,
        RT: float
    ) -> np.ndarray:
        """
        Apply passive film resistance correction using Newton-Raphson.

        Solve for corrected current i such that:
        i = i0_anode * exp(α*z*F*(η - i*R_film) / RT)

        This is rearranged to:
        f(i) = i - C2 * exp(-C1 * R_film * i) = 0

        where:
        C1 = α*z*F / RT
        C2 = i0_anode * exp(C1 * η)

        Args:
            i_act_anodic: Uncorrected anodic current, A/cm²
            resistance: Film resistance array, Ω·cm²
            eta: Overpotential array, V
            i0_anodic: Anodic exchange current density, A/cm²
            alpha: Transfer coefficient
            z: Electrons transferred
            RT: Gas constant * temperature, J/mol

        Returns:
            Corrected anodic current, A/cm²
        """
        n_reps = 50  # Max Newton-Raphson iterations
        tol = 1.0e-6  # Convergence tolerance

        i_corrected = np.zeros_like(i_act_anodic)

        for j in range(len(i_act_anodic)):
            i_old = i_act_anodic[j]
            R_film = resistance[j]
            eta_j = eta[j]

            for k in range(n_reps):
                if R_film > 0:
                    # Calculate f(i) and df/di
                    f_i, df_i = self._calculate_film_correction_residual(
                        i_old, i0_anodic, alpha, eta_j, z, RT, R_film
                    )

                    # Newton-Raphson update
                    i_new = i_old - (f_i / df_i)
                else:
                    i_new = i_old

                # Check convergence
                if i_old != 0:
                    err = abs((i_new - i_old) / i_old)
                else:
                    err = abs(i_new - i_old)

                if err <= tol:
                    i_corrected[j] = i_new
                    break
                elif err > tol and k == n_reps - 1:
                    # Did not converge, apply small correction
                    if j > 0 and i_new < i_corrected[j-1]:
                        i_corrected[j] = 1.001 * i_new
                    else:
                        i_corrected[j] = i_new
                else:
                    i_old = i_new

        return i_corrected

    @staticmethod
    def _calculate_film_correction_residual(
        i: float,
        i0: float,
        beta: float,
        eta: float,
        z: int,
        RT: float,
        R_film: float
    ) -> Tuple[float, float]:
        """
        Calculate residual and derivative for film resistance correction.

        f(i) = i - C2 * exp(-C1 * R_film * i)
        df/di = 1 + C2 * C1 * R_film * exp(-C1 * R_film * i)

        Args:
            i: Current density, A/cm²
            i0: Exchange current density, A/cm²
            beta: Transfer coefficient
            eta: Overpotential, V
            z: Electrons transferred
            RT: Gas constant * temperature, J/mol
            R_film: Film resistance, Ω·cm²

        Returns:
            Tuple of (f_i, df_i)
        """
        C1 = (beta * z * C.F) / RT
        C2 = i0 * np.exp(C1 * eta)
        R_correct = np.exp(-C1 * R_film * i)

        f_i = i - C2 * R_correct
        df_i = 1.0 + C2 * C1 * R_film * R_correct

        return f_i, df_i


__all__ = [
    "ReactionType",
    "CathodicReaction",
    "AnodicReaction",
]
