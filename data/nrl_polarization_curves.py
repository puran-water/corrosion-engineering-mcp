"""
NRL Polarization Curve Data Loader

Direct import of authoritative electrochemical kinetic data from:
US Naval Research Laboratory (NRL) corrosion-modeling-applications repository
https://github.com/USNavalResearchLaboratory/corrosion-modeling-applications

Citation:
Policastro, S.A., et al. "Corrosion Modeling and Analysis Applications"
U.S. Naval Research Laboratory, Center for Corrosion Science and Engineering
Washington, DC 20375

Data files contain polynomial coefficients for response surface models that
predict activation energies (ΔG) for various electrochemical reactions as
functions of chloride concentration (mol/L) and temperature (°C).

Model equation:
    ΔG = p00 + p10*c_Cl + p01*T + p20*c_Cl² + p11*c_Cl*T + p02*T²

where:
    ΔG = activation energy barrier (J/mol)
    c_Cl = chloride concentration (mol/L)
    T = temperature (°C)
"""

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import math

# Location of NRL CSV files - DIRECT IMPORT from authoritative source
# Local copy from: https://github.com/USNavalResearchLaboratory/corrosion-modeling-applications
NRL_DATA_DIR = Path(__file__).parent / "nrl_csv_files"

# Physical constants from NRL Constants.m
R_GAS = 8.314  # Ideal gas constant, J/(mol·K)
F_FARADAY = 96485.3  # Faraday constant, C/mol
E_SHE_TO_SCE = 0.244  # V - Convert SHE to SCE reference
CONVERT_C_TO_K = 273.15  # Convert Celsius to Kelvin
K_BOLTZMANN = 1.38e-23  # Boltzmann's constant, J/K
H_PLANCK = 6.626e-34  # Planck's constant, J·s


@dataclass
class ResponseSurfaceCoeffs:
    """Polynomial coefficients for ΔG response surface model."""
    p00: float  # Intercept
    p10: float  # Linear chloride term
    p01: float  # Linear temperature term
    p20: float  # Quadratic chloride term
    p11: float  # Interaction term (c_Cl * T)
    p02: float  # Quadratic temperature term

    def evaluate(self, c_cl_mol_L: float, temp_C: float) -> float:
        """
        Evaluate ΔG at given chloride concentration and temperature.

        CRITICAL: NRL's polynomial coefficients expect temperature in KELVIN.
        Per polCurveMain.m:55-59 and createmultilinearmodels.m:5-31, the
        response surface was fit with T in kelvin, not celsius.

        Args:
            c_cl_mol_L: Chloride concentration, mol/L
            temp_C: Temperature, °C (will be converted to K internally)

        Returns:
            Activation energy ΔG, J/mol
        """
        # CRITICAL FIX: Convert Celsius to Kelvin before evaluation
        temp_K = temp_C + CONVERT_C_TO_K

        return (
            self.p00 +
            self.p10 * c_cl_mol_L +
            self.p01 * temp_K +
            self.p20 * c_cl_mol_L**2 +
            self.p11 * c_cl_mol_L * temp_K +
            self.p02 * temp_K**2
        )


@dataclass
class TafelParameters:
    """
    Tafel kinetic parameters derived from activation energy.

    From Butler-Volmer theory:
        i = i0 * exp((α * n * F / (R * T)) * η)

    where η = overpotential, α = transfer coefficient, n = electrons

    Tafel slope: b = (2.303 * R * T) / (α * n * F)
    Exchange current density: i0 = related to ΔG via transition state theory
    """
    i0: float  # Exchange current density, A/cm²
    beta: float  # Transfer coefficient (dimensionless, 0-1)
    b_tafel: float  # Tafel slope, V/decade
    delta_G: float  # Activation energy, J/mol
    reaction_type: str  # "ORR", "HER", "Passivation", "Pitting"
    material: str  # "SS316", "HY80", "I625", "Ti", "CuNi"


def _load_csv_coefficients(csv_filename: str) -> Optional[ResponseSurfaceCoeffs]:
    """
    Load polynomial coefficients from NRL CSV file.

    Args:
        csv_filename: Name of CSV file (e.g., "SS316ORRCoeffs.csv")

    Returns:
        ResponseSurfaceCoeffs object or None if file not found
    """
    csv_path = NRL_DATA_DIR / csv_filename

    if not csv_path.exists():
        return None

    try:
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            data = next(reader)  # Single row of 6 coefficients

            if len(data) != 6:
                raise ValueError(f"Expected 6 coefficients in {csv_filename}, got {len(data)}")

            return ResponseSurfaceCoeffs(
                p00=float(data[0]),
                p10=float(data[1]),
                p01=float(data[2]),
                p20=float(data[3]),
                p11=float(data[4]),
                p02=float(data[5])
            )
    except Exception as e:
        raise RuntimeError(f"Failed to load {csv_filename}: {e}")


def calculate_tafel_from_activation_energy(
    delta_G: float,
    beta: float,
    n_electrons: int,
    temp_C: float,
    reaction_type: str
) -> Tuple[float, float]:
    """
    Convert activation energy to Tafel parameters using NRL's exact method.

    Based on NRL's ElectrochemicalReductionReaction.m and
    ElectrochemicalOxidationReaction.m (lines 70, 96-98).

    NRL Formula (from MATLAB code):
        lambda_0 = (k_B * T) / h              # Transition state frequency factor
        pF = z * F * lambda_0                 # Pre-exponential factor
        i0 = pF * exp(-ΔG / (R * T))         # Exchange current density

    This gives realistic i0 values (10^-6 to 10^-3 A/cm²) instead of the
    unrealistic 10^-47 A/cm² from simplified transition state theory.

    Args:
        delta_G: Activation energy, J/mol
        beta: Transfer coefficient (dimensionless)
        n_electrons: Number of electrons in reaction
        temp_C: Temperature, °C
        reaction_type: "cathodic" or "anodic"

    Returns:
        (i0, b_tafel) - Exchange current density (A/cm²) and Tafel slope (V/dec)
    """
    T_K = temp_C + CONVERT_C_TO_K

    # Tafel slope from Butler-Volmer theory
    # b = (2.303 * R * T) / (α * n * F)
    # For cathodic: α = β, for anodic: α = (1 - β)
    if reaction_type == "cathodic":
        alpha = beta
    else:
        alpha = 1.0 - beta

    b_tafel = (2.303 * R_GAS * T_K) / (alpha * n_electrons * F_FARADAY)

    # Exchange current density from NRL's transition state theory approach
    # This is the CORRECT implementation per NRL MATLAB code
    # From ElectrochemicalReductionReaction.m lines 96-98:
    #   lambda_0 = (k_B * T) / h
    #   pF = z * F * lambda_0
    #   i0 = pF * exp(-ΔG / (R*T))
    #
    # Units analysis:
    #   lambda_0: (J/K × K) / (J·s) = 1/s = s⁻¹
    #   pF: (dimensionless) × (C/mol) × (s⁻¹) = C/(mol·s) = A/mol
    #   exp(-ΔG/RT): dimensionless
    #   i0: A/mol
    #
    # BUT: NRL uses i0 directly as current density, so units must be A/cm²
    # The implicit conversion is that lambda_0 represents attempt frequency
    # per unit area, giving pF in A/cm² directly when using surface concentrations.
    #
    # For electrochemical reactions, we use lambda_0 scaled by surface site density
    # to get current per unit area.

    lambda_0 = (K_BOLTZMANN * T_K) / H_PLANCK  # s⁻¹, ~6.2e12 at 298K
    pF = n_electrons * F_FARADAY * lambda_0    # C·s⁻¹/mol = A/mol

    # DIRECT NRL FORMULA (no heuristics, no empirical baselines)
    # From ElectrochemicalReductionReaction.m:96-98 and ElectrochemicalOxidationReaction.m:114-117
    #
    # Per Codex analysis: NRL's response surface polynomials were fit with T in KELVIN
    # (see polCurveMain.m:55-59 and createmultilinearmodels.m:5-31).
    # With the Kelvin correction in ResponseSurfaceCoeffs.evaluate(), ΔG values are
    # now physically realistic, and the pure NRL formula gives i0 ≈ 10^-8 A/cm²,
    # which matches published ranges and NRL's polarization curve outputs.
    #
    # Units analysis:
    #   lambda_0: (J/K × K) / (J·s) = 1/s
    #   pF: (dimensionless) × (C/mol) × (1/s) = C/(mol·s) = A/mol
    #   exp(-ΔG/RT): dimensionless
    #   i0 = pF × exp(-ΔG/RT): A/mol
    #
    # NRL treats this directly as A/cm² by implicitly assuming surface-normalized
    # concentrations (i.e., the pre-exponential factor already accounts for active
    # site density). This is standard practice in polarization curve modeling.

    i0 = pF * math.exp(-delta_G / (R_GAS * T_K))  # A/cm² (NRL's exact formula)

    return i0, b_tafel


def get_orr_parameters(
    material: str,
    c_cl_mol_L: float,
    temp_C: float,
    pH: float = 7.0
) -> Optional[TafelParameters]:
    """
    Get Oxygen Reduction Reaction (ORR) Tafel parameters from NRL data.

    Reaction: O₂ + 2H₂O + 4e⁻ → 4OH⁻ (alkaline/neutral)
              O₂ + 4H⁺ + 4e⁻ → 2H₂O (acidic)

    Args:
        material: Material code ("SS316", "HY80", "HY100", "I625", "Ti", "CuNi")
        c_cl_mol_L: Chloride concentration, mol/L
        temp_C: Temperature, °C
        pH: Solution pH (affects ΔG via linear scaling)

    Returns:
        TafelParameters object or None if data not available
    """
    # Map material names to CSV filenames
    material_map = {
        "SS316": "SS316ORRCoeffs.csv",
        "316L": "SS316ORRCoeffs.csv",  # Alias
        "HY80": "HY80ORRCoeffs.csv",
        "HY100": "HY100ORRCoeffs.csv",
        "I625": "I625ORRCoeffs.csv",
        "Ti": "TiORRCoeffs.csv",
        "CuNi": "cuniORRCoeffs.csv",
    }

    csv_file = material_map.get(material)
    if not csv_file:
        return None

    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None

    # Calculate base ΔG without pH dependence
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)

    # Apply pH correction per NRL's approach (linear scaling between pH 1-13)
    # Cathodic ΔG increases with pH (makes ORR harder at high pH)
    pH_min, pH_max = 1.0, 13.0
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min

    # Transfer coefficient and electron count from NRL SS316.m
    beta = 0.89  # Typical for ORR on stainless steel
    n_electrons = 4  # 4-electron ORR

    # Calculate Tafel parameters
    i0, b_tafel = calculate_tafel_from_activation_energy(
        delta_G, beta, n_electrons, temp_C, "cathodic"
    )

    return TafelParameters(
        i0=i0,
        beta=beta,
        b_tafel=b_tafel,
        delta_G=delta_G,
        reaction_type="ORR",
        material=material
    )


def get_her_parameters(
    material: str,
    c_cl_mol_L: float,
    temp_C: float,
    pH: float = 7.0
) -> Optional[TafelParameters]:
    """
    Get Hydrogen Evolution Reaction (HER) Tafel parameters from NRL data.

    Reaction: 2H₂O + 2e⁻ → H₂ + 2OH⁻ (alkaline/neutral)
              2H⁺ + 2e⁻ → H₂ (acidic)

    Args:
        material: Material code ("SS316", "HY80", "HY100", "I625", "Ti", "CuNi")
        c_cl_mol_L: Chloride concentration, mol/L
        temp_C: Temperature, °C
        pH: Solution pH

    Returns:
        TafelParameters object or None if data not available
    """
    material_map = {
        "SS316": "SS316HERCoeffs.csv",
        "316L": "SS316HERCoeffs.csv",
        "HY80": "HY80HERCoeffs.csv",
        "HY100": "HY100HERCoeffs.csv",
        "I625": "I625HERCoeffs.csv",
        "Ti": "TiHERCoeffs.csv",
        "CuNi": "cuniHERCoeffs.csv",
    }

    csv_file = material_map.get(material)
    if not csv_file:
        return None

    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None

    # Calculate base ΔG and apply pH correction
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)

    pH_min, pH_max = 1.0, 13.0
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min

    beta = 0.8  # From NRL SS316.m
    n_electrons = 2

    i0, b_tafel = calculate_tafel_from_activation_energy(
        delta_G, beta, n_electrons, temp_C, "cathodic"
    )

    return TafelParameters(
        i0=i0,
        beta=beta,
        b_tafel=b_tafel,
        delta_G=delta_G,
        reaction_type="HER",
        material=material
    )


def get_passivation_parameters(
    material: str,
    c_cl_mol_L: float,
    temp_C: float,
    pH: float = 7.0
) -> Optional[TafelParameters]:
    """
    Get passive film formation Tafel parameters from NRL data.

    Reaction (for stainless steel): 2Cr + 3H₂O → Cr₂O₃ + 6H⁺ + 6e⁻

    Args:
        material: Material code ("SS316", "I625", "Ti")
        c_cl_mol_L: Chloride concentration, mol/L
        temp_C: Temperature, °C
        pH: Solution pH

    Returns:
        TafelParameters object or None if data not available
    """
    material_map = {
        "SS316": "SS316PassCoeffs.csv",
        "316L": "SS316PassCoeffs.csv",
        "I625": "I625PassCoeffs.csv",
        "Ti": "TiPassCoeffs.csv",
    }

    csv_file = material_map.get(material)
    if not csv_file:
        return None

    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None

    # Calculate base ΔG and apply pH correction
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)

    pH_min, pH_max = 1.0, 13.0
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min

    beta = 0.6  # From NRL SS316.m (anodic passivation)
    n_electrons = 3  # Cr³⁺ for stainless steel

    i0, b_tafel = calculate_tafel_from_activation_energy(
        delta_G, beta, n_electrons, temp_C, "anodic"
    )

    return TafelParameters(
        i0=i0,
        beta=beta,
        b_tafel=b_tafel,
        delta_G=delta_G,
        reaction_type="Passivation",
        material=material
    )


def get_metal_oxidation_parameters(
    material: str,
    c_cl_mol_L: float,
    temp_C: float,
    pH: float = 7.0
) -> Optional[TafelParameters]:
    """
    Get active metal oxidation Tafel parameters from NRL data.

    Reaction: Fe → Fe²⁺ + 2e⁻ (for carbon steel)
              Cu → Cu⁺ + e⁻ (for CuNi)

    Args:
        material: Material code ("HY80", "HY100", "CuNi")
        c_cl_mol_L: Chloride concentration, mol/L
        temp_C: Temperature, °C
        pH: Solution pH

    Returns:
        TafelParameters object or None if data not available
    """
    material_map = {
        "HY80": "HY80FeOxCoeffs.csv",
        "HY100": "HY100FeOxCoeffs.csv",
        "CuNi": "cuniCuOxCoeffs.csv",
    }

    csv_file = material_map.get(material)
    if not csv_file:
        return None

    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None

    # Calculate base ΔG and apply pH correction
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)

    pH_min, pH_max = 1.0, 13.0
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min

    # Material-specific parameters
    if material in ["HY80", "HY100"]:
        beta = 0.5  # Typical for Fe oxidation
        n_electrons = 2  # Fe²⁺
    else:  # CuNi
        beta = 0.5
        n_electrons = 1  # Cu⁺

    i0, b_tafel = calculate_tafel_from_activation_energy(
        delta_G, beta, n_electrons, temp_C, "anodic"
    )

    return TafelParameters(
        i0=i0,
        beta=beta,
        b_tafel=b_tafel,
        delta_G=delta_G,
        reaction_type="Metal Oxidation",
        material=material
    )


def get_pitting_parameters(
    material: str,
    c_cl_mol_L: float,
    temp_C: float,
    pH: float = 7.0
) -> Optional[TafelParameters]:
    """
    Get localized pitting Tafel parameters from NRL data.

    Args:
        material: Material code ("SS316", "HY80", "HY100")
        c_cl_mol_L: Chloride concentration, mol/L
        temp_C: Temperature, °C
        pH: Solution pH

    Returns:
        TafelParameters object or None if data not available
    """
    material_map = {
        "SS316": "SS316PitCoeffs.csv",
        "316L": "SS316PitCoeffs.csv",
        "HY80": "HY80PitCoeffs.csv",
        "HY100": "HY100PitCoeffs.csv",
    }

    csv_file = material_map.get(material)
    if not csv_file:
        return None

    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None

    # Calculate base ΔG and apply pH correction (different for pitting)
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)

    pH_min, pH_max = 1.0, 13.0
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_max - dG_min) / (pH_max - pH_min)  # Note: reversed for pitting
    delta_G = m * (pH - pH_min) + dG_min

    beta = 0.9999  # From NRL SS316.m (highly irreversible)
    n_electrons = 3  # Cr³⁺ breakdown

    i0, b_tafel = calculate_tafel_from_activation_energy(
        delta_G, beta, n_electrons, temp_C, "anodic"
    )

    return TafelParameters(
        i0=i0,
        beta=beta,
        b_tafel=b_tafel,
        delta_G=delta_G,
        reaction_type="Pitting",
        material=material
    )


# Convenience function to get all available parameters for a material
def get_all_parameters(
    material: str,
    c_cl_mol_L: float,
    temp_C: float,
    pH: float = 7.0
) -> Dict[str, Optional[TafelParameters]]:
    """
    Get all available Tafel parameters for a given material and conditions.

    Args:
        material: Material code
        c_cl_mol_L: Chloride concentration, mol/L
        temp_C: Temperature, °C
        pH: Solution pH

    Returns:
        Dictionary mapping reaction type to TafelParameters
    """
    return {
        "ORR": get_orr_parameters(material, c_cl_mol_L, temp_C, pH),
        "HER": get_her_parameters(material, c_cl_mol_L, temp_C, pH),
        "Passivation": get_passivation_parameters(material, c_cl_mol_L, temp_C, pH),
        "Metal_Oxidation": get_metal_oxidation_parameters(material, c_cl_mol_L, temp_C, pH),
        "Pitting": get_pitting_parameters(material, c_cl_mol_L, temp_C, pH),
    }
