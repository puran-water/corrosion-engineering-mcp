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
NRL_DATA_DIR = Path(__file__).parent.parent / 'external' / 'nrl_coefficients'
R_GAS = 8.314
F_FARADAY = 96485.3
E_SHE_TO_SCE = 0.244
CONVERT_C_TO_K = 273.15
K_BOLTZMANN = 1.38e-23
H_PLANCK = 6.626e-34

@dataclass
class ResponseSurfaceCoeffs:
    """Polynomial coefficients for ΔG response surface model."""
    p00: float
    p10: float
    p01: float
    p20: float
    p11: float
    p02: float

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
        temp_K = temp_C + CONVERT_C_TO_K
        return self.p00 + self.p10 * c_cl_mol_L + self.p01 * temp_K + self.p20 * c_cl_mol_L ** 2 + self.p11 * c_cl_mol_L * temp_K + self.p02 * temp_K ** 2

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
    i0: float
    beta: float
    b_tafel: float
    delta_G: float
    reaction_type: str
    material: str

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
            data = next(reader)
            if len(data) != 6:
                raise ValueError(f'Expected 6 coefficients in {csv_filename}, got {len(data)}')
            return ResponseSurfaceCoeffs(p00=float(data[0]), p10=float(data[1]), p01=float(data[2]), p20=float(data[3]), p11=float(data[4]), p02=float(data[5]))
    except Exception as e:
        raise RuntimeError(f'Failed to load {csv_filename}: {e}')

def calculate_tafel_from_activation_energy(delta_G: float, beta: float, n_electrons: int, temp_C: float, reaction_type: str) -> Tuple[float, float]:
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
    if reaction_type == 'cathodic':
        alpha = beta
    else:
        alpha = 1.0 - beta
    b_tafel = 2.303 * R_GAS * T_K / (alpha * n_electrons * F_FARADAY)
    lambda_0 = K_BOLTZMANN * T_K / H_PLANCK
    pF = n_electrons * F_FARADAY * lambda_0
    i0 = pF * math.exp(-delta_G / (R_GAS * T_K))
    return (i0, b_tafel)

def get_orr_parameters(material: str, c_cl_mol_L: float, temp_C: float, pH: float=7.0) -> Optional[TafelParameters]:
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
    material_map = {'SS316': 'SS316ORRCoeffs.csv', '316L': 'SS316ORRCoeffs.csv', 'HY80': 'HY80ORRCoeffs.csv', 'HY100': 'HY100ORRCoeffs.csv', 'I625': 'I625ORRCoeffs.csv', 'Ti': 'TiORRCoeffs.csv', 'CuNi': 'cuniORRCoeffs.csv'}
    csv_file = material_map.get(material)
    if not csv_file:
        return None
    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)
    pH_min, pH_max = (1.0, 13.0)
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min
    beta = 0.89
    n_electrons = 4
    i0, b_tafel = calculate_tafel_from_activation_energy(delta_G, beta, n_electrons, temp_C, 'cathodic')
    return TafelParameters(i0=i0, beta=beta, b_tafel=b_tafel, delta_G=delta_G, reaction_type='ORR', material=material)

def get_her_parameters(material: str, c_cl_mol_L: float, temp_C: float, pH: float=7.0) -> Optional[TafelParameters]:
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
    material_map = {'SS316': 'SS316HERCoeffs.csv', '316L': 'SS316HERCoeffs.csv', 'HY80': 'HY80HERCoeffs.csv', 'HY100': 'HY100HERCoeffs.csv', 'I625': 'I625HERCoeffs.csv', 'Ti': 'TiHERCoeffs.csv', 'CuNi': 'cuniHERCoeffs.csv'}
    csv_file = material_map.get(material)
    if not csv_file:
        return None
    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)
    pH_min, pH_max = (1.0, 13.0)
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min
    beta = 0.8
    n_electrons = 2
    i0, b_tafel = calculate_tafel_from_activation_energy(delta_G, beta, n_electrons, temp_C, 'cathodic')
    return TafelParameters(i0=i0, beta=beta, b_tafel=b_tafel, delta_G=delta_G, reaction_type='HER', material=material)

def get_passivation_parameters(material: str, c_cl_mol_L: float, temp_C: float, pH: float=7.0) -> Optional[TafelParameters]:
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
    material_map = {'SS316': 'SS316PassCoeffs.csv', '316L': 'SS316PassCoeffs.csv', 'I625': 'I625PassCoeffs.csv', 'Ti': 'TiPassCoeffs.csv'}
    csv_file = material_map.get(material)
    if not csv_file:
        return None
    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)
    pH_min, pH_max = (1.0, 13.0)
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min
    beta = 0.6
    n_electrons = 3
    i0, b_tafel = calculate_tafel_from_activation_energy(delta_G, beta, n_electrons, temp_C, 'anodic')
    return TafelParameters(i0=i0, beta=beta, b_tafel=b_tafel, delta_G=delta_G, reaction_type='Passivation', material=material)

def get_metal_oxidation_parameters(material: str, c_cl_mol_L: float, temp_C: float, pH: float=7.0) -> Optional[TafelParameters]:
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
    material_map = {'HY80': 'HY80FeOxCoeffs.csv', 'HY100': 'HY100FeOxCoeffs.csv', 'CuNi': 'cuniCuOxCoeffs.csv'}
    csv_file = material_map.get(material)
    if not csv_file:
        return None
    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)
    pH_min, pH_max = (1.0, 13.0)
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_min - dG_max) / (pH_max - pH_min)
    delta_G = m * (pH - pH_max) + dG_min
    if material in ['HY80', 'HY100']:
        beta = 0.5
        n_electrons = 2
    else:
        beta = 0.5
        n_electrons = 1
    i0, b_tafel = calculate_tafel_from_activation_energy(delta_G, beta, n_electrons, temp_C, 'anodic')
    return TafelParameters(i0=i0, beta=beta, b_tafel=b_tafel, delta_G=delta_G, reaction_type='Metal Oxidation', material=material)

def get_pitting_parameters(material: str, c_cl_mol_L: float, temp_C: float, pH: float=7.0) -> Optional[TafelParameters]:
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
    material_map = {'SS316': 'SS316PitCoeffs.csv', '316L': 'SS316PitCoeffs.csv', 'HY80': 'HY80PitCoeffs.csv', 'HY100': 'HY100PitCoeffs.csv'}
    csv_file = material_map.get(material)
    if not csv_file:
        return None
    coeffs = _load_csv_coefficients(csv_file)
    if not coeffs:
        return None
    delta_G_base = coeffs.evaluate(c_cl_mol_L, temp_C)
    pH_min, pH_max = (1.0, 13.0)
    dG_max = 1.1 * delta_G_base
    dG_min = 0.9 * delta_G_base
    m = (dG_max - dG_min) / (pH_max - pH_min)
    delta_G = m * (pH - pH_min) + dG_min
    beta = 0.9999
    n_electrons = 3
    i0, b_tafel = calculate_tafel_from_activation_energy(delta_G, beta, n_electrons, temp_C, 'anodic')
    return TafelParameters(i0=i0, beta=beta, b_tafel=b_tafel, delta_G=delta_G, reaction_type='Pitting', material=material)

def get_all_parameters(material: str, c_cl_mol_L: float, temp_C: float, pH: float=7.0) -> Dict[str, Optional[TafelParameters]]:
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
    return {'ORR': get_orr_parameters(material, c_cl_mol_L, temp_C, pH), 'HER': get_her_parameters(material, c_cl_mol_L, temp_C, pH), 'Passivation': get_passivation_parameters(material, c_cl_mol_L, temp_C, pH), 'Metal_Oxidation': get_metal_oxidation_parameters(material, c_cl_mol_L, temp_C, pH), 'Pitting': get_pitting_parameters(material, c_cl_mol_L, temp_C, pH)}
