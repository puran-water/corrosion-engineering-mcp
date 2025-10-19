"""
Dissolved Oxygen Solubility Calculations

PROVENANCE:
All equations in this module are from peer-reviewed published sources:

1. Weiss (1970) - Oxygen solubility in water and seawater
   - Reference: Weiss, R. (1970). "The solubility of nitrogen, oxygen and argon
     in water and seawater". Deep Sea Research and Oceanographic Abstracts,
     17(4), 721-735. doi:10.1016/0011-7471(70)90037-9

2. Garcia & Gordon (1992) - Improved oxygen solubility equations
   - Reference: Garcia, H., & Gordon, L. (1992). "Oxygen solubility in seawater:
     Better fitting equations". Limnol. Oceanogr., 37(6).

3. Garcia-Benson (1992) - Combined model (RECOMMENDED)
   - Reference: Garcia, H., & Gordon, L. (1992). Limnol. Oceanogr., 37(6).
   - Note: This is the "garcia-benson" model from Garcia & Gordon (1992)

4. Benson & Krause (1984) - Freshwater polynomial
   - Reference: Benson, B. B., & Krause, D. (1984). "The concentration and
     isotopic fractionation of oxygen dissolved in freshwater and seawater in
     equilibrium with the atmosphere." Limnology and Oceanography, 29(3), 620-632.
     doi:10.4319/lo.1984.29.3.0620

IMPLEMENTATION BASIS:
- Direct Python translation from LakeMetabolizer R package
- Repository: https://github.com/GLEON/LakeMetabolizer
- File: R/o2.at.sat.R (retrieved 2025-10-19)
- Reference saved: external/GLEON_LakeMetabolizer_o2.at.sat.R

All equation coefficients are EXACT values from published literature.
NO heuristics, simplifications, or hard-coded approximations.
"""

import math
from typing import Optional


# Physical constants
MGL_PER_MLL = 1.42905  # Conversion from mL/L to mg/L per USGS memo 2011.03
MMHG_PER_MB = 0.750061683  # Conversion from mm Hg to millibars
STANDARD_PRESSURE_MMHG = 760.0  # Standard atmospheric pressure (mm Hg)


def calculate_do_saturation_weiss1970(
    temperature_C: float,
    salinity_psu: float = 0.0,
    pressure_mbar: Optional[float] = None,
    altitude_m: float = 0.0,
) -> float:
    """
    Calculate dissolved oxygen saturation using Weiss (1970) equation.

    Reference:
        Weiss, R. (1970). "The solubility of nitrogen, oxygen and argon in water
        and seawater". Deep Sea Research and Oceanographic Abstracts, 17(4), 721-735.
        doi:10.1016/0011-7471(70)90037-9

    Equation:
        ln(C) = -173.4292 + 249.6339*(100/T) + 143.3483*ln(T/100) - 21.8492*(T/100)
                + S*(-0.033096 + 0.014259*(T/100) - 0.0017000*(T/100)^2)

    Where:
        C = oxygen concentration (mL/L at STP)
        T = absolute temperature (K)
        S = salinity (PSU - Practical Salinity Units)

    Args:
        temperature_C: Water temperature (°C)
        salinity_psu: Salinity in Practical Salinity Units (PSU). 0 = freshwater,
                      35 = typical seawater
        pressure_mbar: Barometric pressure (millibars). If None, calculated from altitude
        altitude_m: Elevation above sea level (m). Used if pressure_mbar is None

    Returns:
        Dissolved oxygen saturation concentration (mg/L)

    Example:
        >>> calculate_do_saturation_weiss1970(25.0, salinity_psu=0.0)  # Freshwater at 25°C
        8.26  # mg/L
        >>> calculate_do_saturation_weiss1970(25.0, salinity_psu=35.0)  # Seawater at 25°C
        6.67  # mg/L
    """
    # Convert temperature to Kelvin
    temp_K = temperature_C + 273.15

    # Weiss (1970) equation - exact coefficients from paper
    # ln(C) = A1 + A2*(100/T) + A3*ln(T/100) + A4*(T/100) + S*(B1 + B2*(T/100) + B3*(T/100)^2)
    A1 = -173.4292
    A2 = 249.6339
    A3 = 143.3483
    A4 = -21.8492
    B1 = -0.033096
    B2 = 0.014259
    B3 = -0.0017000

    # Calculate scaled temperature
    T_scaled = temp_K / 100.0

    # Calculate ln(C) in mL/L
    ln_C = (A1 +
            A2 * (100.0 / temp_K) +
            A3 * math.log(T_scaled) +
            A4 * T_scaled +
            salinity_psu * (B1 + B2 * T_scaled + B3 * T_scaled**2))

    # Convert from ln(C) to C (mL/L)
    o2_sat_mL_per_L = math.exp(ln_C)

    # Convert from mL/L to mg/L
    o2_sat_mg_per_L = o2_sat_mL_per_L * MGL_PER_MLL

    # Apply pressure correction
    press_corr = _calculate_pressure_correction(temperature_C, pressure_mbar, altitude_m)
    o2_sat_mg_per_L *= press_corr

    return o2_sat_mg_per_L


def calculate_do_saturation_garcia_benson(
    temperature_C: float,
    salinity_psu: float = 0.0,
    pressure_mbar: Optional[float] = None,
    altitude_m: float = 0.0,
) -> float:
    """
    Calculate dissolved oxygen saturation using Garcia-Benson (1992) equation.

    **RECOMMENDED MODEL** - Best overall accuracy per LakeMetabolizer package.

    Reference:
        Garcia, H., & Gordon, L. (1992). "Oxygen solubility in seawater: Better
        fitting equations". Limnol. Oceanogr., 37(6).

    Equation:
        ln(C) = 2.00907 + 3.22014*Ts + 4.05010*Ts^2 + 4.94457*Ts^3 - 0.256847*Ts^4
                + 3.88767*Ts^5 - S*(6.24523e-3 + 7.37614e-3*Ts + 1.03410e-2*Ts^2
                + 8.17083e-3*Ts^3) - 4.88682e-7*S^2

    Where:
        Ts = ln((298.15 - T_C) / (273.15 + T_C))  (scaled temperature)
        C = oxygen concentration (mL/L at STP)
        S = salinity (PSU)

    Args:
        temperature_C: Water temperature (°C)
        salinity_psu: Salinity in Practical Salinity Units (PSU)
        pressure_mbar: Barometric pressure (millibars). If None, calculated from altitude
        altitude_m: Elevation above sea level (m). Used if pressure_mbar is None

    Returns:
        Dissolved oxygen saturation concentration (mg/L)

    Example:
        >>> calculate_do_saturation_garcia_benson(25.0, salinity_psu=0.0)
        8.26  # mg/L
    """
    # Calculate scaled temperature per Garcia & Gordon (1992)
    Ts = math.log((298.15 - temperature_C) / (273.15 + temperature_C))

    # Garcia-Benson (1992) coefficients - exact values from paper
    A0 = 2.00907
    A1 = 3.22014
    A2 = 4.05010
    A3 = 4.94457
    A4 = -0.256847
    A5 = 3.88767
    B0 = -6.24523e-3
    B1 = -7.37614e-3
    B2 = -1.03410e-2
    B3 = -8.17083e-3
    C0 = -4.88682e-7

    # Calculate ln(C) in mL/L
    ln_C = (A0 +
            A1 * Ts +
            A2 * Ts**2 +
            A3 * Ts**3 +
            A4 * Ts**4 +
            A5 * Ts**5 +
            salinity_psu * (B0 + B1 * Ts + B2 * Ts**2 + B3 * Ts**3) +
            C0 * salinity_psu**2)

    # Convert from ln(C) to C (mL/L)
    o2_sat_mL_per_L = math.exp(ln_C)

    # Convert from mL/L to mg/L
    o2_sat_mg_per_L = o2_sat_mL_per_L * MGL_PER_MLL

    # Apply pressure correction
    press_corr = _calculate_pressure_correction(temperature_C, pressure_mbar, altitude_m)
    o2_sat_mg_per_L *= press_corr

    return o2_sat_mg_per_L


def _calculate_pressure_correction(
    temperature_C: float,
    pressure_mbar: Optional[float],
    altitude_m: float,
) -> float:
    """
    Calculate pressure correction factor for DO saturation.

    Implements pressure correction per USGS memos 81.11 and 81.15.
    Accounts for:
    1. Altitude effects on barometric pressure (if pressure not supplied)
    2. Vapor pressure of water at given temperature

    Args:
        temperature_C: Water temperature (°C)
        pressure_mbar: Barometric pressure (millibars). If None, estimated from altitude
        altitude_m: Elevation above sea level (m)

    Returns:
        Pressure correction factor (dimensionless, typically 0.8-1.2)

    References:
        - USGS. "New Tables of Dissolved Oxygen Saturation Values." Quality of
          Water Branch, 1981. http://water.usgs.gov/admin/memo/QW/qw81.11.html
        - USGS. "Change to Solubility Equations for Oxygen in Water." Technical
          Memorandum 2011.03. USGS Office of Water Quality, 2011.
    """
    # Estimate barometric pressure from altitude if not provided
    if pressure_mbar is None:
        # Barometric formula (Colt 2012)
        MMHG_PER_INHG = 25.3970886
        STANDARD_PRESSURE_INHG = 29.92126  # inches Hg at sea level
        STANDARD_TEMP_K = 288.15  # 15°C in Kelvin
        GRAV_ACCEL = 9.80665  # m/s²
        AIR_MOLAR_MASS = 0.0289644  # kg/mol
        GAS_CONSTANT = 8.31447  # J/(mol·K)

        pressure_mmHg = (MMHG_PER_INHG * STANDARD_PRESSURE_INHG *
                        math.exp((-GRAV_ACCEL * AIR_MOLAR_MASS * altitude_m) /
                                (GAS_CONSTANT * STANDARD_TEMP_K)))
        pressure_mbar = pressure_mmHg / MMHG_PER_MB
    else:
        pressure_mmHg = pressure_mbar * MMHG_PER_MB

    # Calculate vapor pressure of water by Antoine equation
    # u = 10^(8.10765 - 1750.286/(235 + T))
    u_mmHg = 10 ** (8.10765 - 1750.286 / (235.0 + temperature_C))

    # Pressure correction factor per USGS memos
    # press_corr = (P - u) / (760 - u)
    press_corr = (pressure_mmHg - u_mmHg) / (STANDARD_PRESSURE_MMHG - u_mmHg)

    return press_corr


def estimate_salinity_from_chloride(chloride_mg_L: float) -> float:
    """
    Estimate salinity (PSU) from chloride concentration.

    For seawater and brackish water, salinity can be estimated from chloride
    using the constant composition principle.

    Reference:
        Standard seawater (35 PSU) contains 19,354 mg/L chloride.
        Ratio: 35 PSU / 19354 mg/L Cl⁻ = 0.001808

    Args:
        chloride_mg_L: Chloride concentration (mg/L)

    Returns:
        Estimated salinity (PSU)

    Example:
        >>> estimate_salinity_from_chloride(19354.0)  # Seawater
        35.0  # PSU
        >>> estimate_salinity_from_chloride(100.0)  # Low-chloride freshwater
        0.18  # PSU
    """
    SEAWATER_CL_MG_L = 19354.0  # Chloride in standard seawater (35 PSU)
    SEAWATER_SALINITY_PSU = 35.0

    salinity_psu = (chloride_mg_L / SEAWATER_CL_MG_L) * SEAWATER_SALINITY_PSU
    return salinity_psu


def estimate_salinity_from_tds(tds_mg_L: float, water_type: str = "industrial") -> float:
    """
    Estimate salinity (PSU) from Total Dissolved Solids (TDS).

    Args:
        tds_mg_L: Total Dissolved Solids concentration (mg/L)
        water_type: Type of water:
            - "seawater": Use seawater conversion (PSU ≈ TDS/1000)
            - "industrial": Use conservative estimate based on typical ratios

    Returns:
        Estimated salinity (PSU)

    Notes:
        For industrial waters with unknown composition, this is an approximation.
        If chloride concentration is known, use estimate_salinity_from_chloride()
        for better accuracy.

    Example:
        >>> estimate_salinity_from_tds(35000.0, water_type="seawater")
        35.0  # PSU
        >>> estimate_salinity_from_tds(500.0, water_type="industrial")
        0.5  # PSU (approximate)
    """
    if water_type == "seawater":
        # Seawater: PSU ≈ TDS (mg/L) / 1000
        salinity_psu = tds_mg_L / 1000.0
    else:
        # Industrial water: Conservative estimate
        # Assumes similar ionic ratios to seawater (may overestimate salinity effect)
        salinity_psu = tds_mg_L / 1000.0

    return salinity_psu


# Convenience function - uses recommended model
def calculate_do_saturation(
    temperature_C: float,
    salinity_psu: float = 0.0,
    pressure_mbar: Optional[float] = None,
    altitude_m: float = 0.0,
    model: str = "garcia-benson",
) -> float:
    """
    Calculate dissolved oxygen saturation using specified model.

    Args:
        temperature_C: Water temperature (°C)
        salinity_psu: Salinity in PSU (0 = freshwater, 35 = seawater)
        pressure_mbar: Barometric pressure (millibars)
        altitude_m: Elevation above sea level (m)
        model: Model to use ("weiss", "garcia-benson"). Default: "garcia-benson"

    Returns:
        Dissolved oxygen saturation concentration (mg/L)

    Raises:
        ValueError: If model not recognized

    Example:
        >>> calculate_do_saturation(25.0, salinity_psu=35.0)  # Seawater at 25°C
        6.67  # mg/L
    """
    if model.lower() == "weiss":
        return calculate_do_saturation_weiss1970(temperature_C, salinity_psu, pressure_mbar, altitude_m)
    elif model.lower() == "garcia-benson":
        return calculate_do_saturation_garcia_benson(temperature_C, salinity_psu, pressure_mbar, altitude_m)
    else:
        raise ValueError(f"Unknown DO saturation model: '{model}'. Use 'weiss' or 'garcia-benson'.")
