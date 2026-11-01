import math

from corrosion_engineering_mcp.core.errors import require_representable_nonzero_result


# Primary-content record for the exact K1 expression used below. The official
# article abstract was read on 2026-08-10; it prints the complete empirical
# CO2-H2O K1 temperature expression, including the coefficient that differs by
# one zero from the vendored M-506 transcription.
PLUMMER_BUSENBERG_K1_SOURCE = {
    "doi": "10.1016/0016-7037(82)90056-4",
    "authors": ["L. Niel Plummer", "Eurybiades Busenberg"],
    "title": (
        "The solubilities of calcite, aragonite and vaterite in CO2-H2O "
        "solutions between 0 and 90°C, and an evaluation of the aqueous "
        "model for the system CaCO3-CO2-H2O"
    ),
    "section": "Abstract, CO2-H2O equilibria empirical expressions",
    "content_support": (
        "The abstract prints log K1 = -356.3094 - 0.06091964*T + "
        "21834.37/T + 126.8339*log10(T) - 1684915/T^2, with T in kelvin."
    ),
    "content_limit": (
        "This record supports the zero-pressure empirical K1 temperature "
        "expression and its 1684915/T^2 coefficient only; it does not support "
        "the separate M-506 pressure/salting terms or this wrapper's hydration-"
        "basis conversion."
    ),
    "content_verified_from": "official ScienceDirect article abstract",
}


class NorsokM506Error(ValueError):
    """Typed failure raised when the vendored M-506 calculation is undefined."""


class NorsokPHConvergenceError(NorsokM506Error):
    """The Newton solve for M-506 equation (11) or (12) did not converge."""


class NorsokM506EnvelopeError(NorsokM506Error):
    """An input is outside an explicit M-506 table range."""

    def __init__(
        self,
        quantity,
        value,
        minimum,
        maximum,
        source,
        *,
        maximum_inclusive=True,
    ):
        self.quantity = quantity
        self.value = value
        self.minimum = minimum
        self.maximum = maximum
        self.source = source
        self.maximum_inclusive = maximum_inclusive
        upper_relation = "at or below" if maximum_inclusive else "strictly below"
        super().__init__(
            f"{quantity}={value:g} is outside {source}: it must be at or above "
            f"{minimum:g} and {upper_relation} {maximum:g}"
        )

def Shearstress(v_sg, v_sl , mass_g, mass_l,vol_g,vol_l,holdup,vis_g,vis_l,roughness,diameter):
    #v_sg: superficia velocity of gas m/s
    #v_sl: superficia velocity of liquid m/s
    #mass_g: mass flow of gas kg/hr
    #mass_l: mass flow of liquid kg/hr
    #vol_g: volumetric flowrate of gas m3/hr
    #vol_l: volumetric flowrate of liquid m3/hr
    #holdup: %
    #vis_g: viscosity of gas cp
    #vis_l: viscosity of liquid cp
    v_m = v_sg + v_sl
    vis_m = (vis_l * holdup + vis_g * (100 - holdup)) / 100
    # NORSOK M-506:2005 Table 5 gives the equivalent viscosity ranges in cP
    # and N s/m2 (for example 1.1 cP = 0.0011 N s/m2).  Therefore 1 cP is
    # 0.001 Pa s.  This viscosity enters the friction factor in equation (21).
    vis_m = vis_m * 0.001
    #calculate mixyure density, unit kg/m3
    density_g = mass_g / vol_g
    density_l = mass_l / vol_l
    density_mix = (density_l * holdup + density_g * (100 - holdup)) / 100
    friction = 0.001375 * (1 + (20000 * roughness / diameter + 10 ** 6 * vis_m / (v_m * diameter * density_mix)) ** 0.33)
    velocity_squared = v_m ** 2
    if velocity_squared == 0.0 and v_m != 0.0:
        # Stable-order fallback for a squared intermediate that underflows even
        # though the final shear is representable.  Ordinary inputs retain the
        # original operation order and therefore their exact binary64 results.
        tempo = (0.5 * density_mix * friction * v_m) * v_m
    else:
        tempo = 0.5 * density_mix * friction * velocity_squared
    return require_representable_nonzero_result(
        "norsok_m506_equations_20_21_wall_shear",
        tempo,
        operands={
            "mixture_density_kg_m3": density_mix,
            "friction_factor": friction,
            "mixture_velocity_m_s": v_m,
        },
    )


def fpH_FixT(tempe, iph):
    if tempe not in (5.0, 15.0, 20.0, 40.0, 60.0, 80.0, 90.0, 120.0, 150.0):
        raise NorsokM506Error("pH factor requires a tabulated M-506 temperature")
    if not 3.5 <= iph <= 6.5:
        raise NorsokM506Error("pH is outside the M-506 Table 2 range")

    tempo = None
    # All coefficients and branches below implement NORSOK M-506:2005 Table 2.
    if tempe==5.0:
        if (iph>=3.5) and (iph<=4.6): tempo = 2.0676 - 0.2309 * iph
        if (iph>4.6) and (iph<=6.5): tempo = 4.342 - (1.051 * iph) + (0.0708 * iph ** 2)
    if tempe==15.0:
        if (iph>=3.5) and (iph<=4.6): tempo= 2.0676 - (0.2309 * iph)
        if (iph>4.6) and (iph<=6.5): tempo= 4.986 - (1.191 * iph) + (0.0708 * iph ** 2)
    if tempe==20.0:
        if (iph>=3.5) and (iph<=4.6): tempo= 2.0676 - (0.2309 * iph)
        if (iph>4.6) and (iph<=6.5): tempo= 5.1885 - (1.2353 * iph) + (0.0708 * iph ** 2)
    if tempe==40.0:
        if (iph>=3.5) and (iph<=4.6): tempo= 2.0676 - (0.2309 * iph)
        if (iph>4.6) and (iph<=6.5): tempo= 5.1885 - (1.2353 * iph) + (0.0708 * iph ** 2)
    if tempe==60.0:
        if (iph>=3.5) and (iph<=4.6): tempo= 1.836 - (0.1818 * iph)
        if (iph>4.6) and (iph<=6.5): tempo= 15.444 - (6.1291 * iph) + (0.8204 * iph ** 2) - (0.0371 * iph ** 3)
    if tempe==80.0:
        if (iph>=3.5) and (iph<=4.6): tempo= 2.6727 - (0.3636 * iph)
        if (iph>4.6) and (iph<=6.5): tempo= 331.68 * math.exp(-1.2618 * iph)
    if tempe==90.0:
        if (iph>=3.5) and (iph<=4.57): tempo= 3.1355 - (0.4673 * iph)
        if (iph>4.57) and (iph<=5.62): tempo= 21254 * math.exp(-2.1811 * iph)
        if (iph>5.62) and (iph<=6.5): tempo= 0.4014 - (0.0538 * iph)
    if tempe==120.0:
        if (iph>=3.5) and (iph<=4.3): tempo = 1.5375 - (0.125 * iph)
        if (iph>4.3) and (iph<=5.0): tempo= 5.9757 - 1.157 * iph
        if (iph>5.0) and (iph<=6.5): tempo=  0.546125 - (0.071225 * iph)
    if tempe==150.0:
        if (iph>=3.5) and (iph<=3.8): tempo = 1
        if (iph>3.8) and (iph<=5.0): tempo= 17.634 - (7.0945 * iph) + (0.715 * iph ** 2)
        if (iph>5.0) and (iph<=6.5): tempo=  0.037

    if tempo is None:
        raise NorsokM506Error("no M-506 Table 2 pH-factor branch matched")
    return tempo

def fpH_Cal(Tempe,IpH):

    TempRange = [5.0, 15.0, 20.0, 40.0, 60.0, 80.0, 90.0, 120.0, 150.0]

    if not TempRange[0] <= Tempe <= TempRange[-1]:
        raise NorsokM506Error("temperature is outside the M-506 Table 2 range")
    if Tempe in TempRange:
        return fpH_FixT(Tempe, IpH)

    TempUpper = next(value for value in TempRange if value > Tempe)
    upper_index = TempRange.index(TempUpper)
    TempLower = TempRange[upper_index - 1]


    fpHLower = fpH_FixT(TempLower, IpH)
    fpHUpper = fpH_FixT(TempUpper, IpH)


    tempo = (fpHUpper - fpHLower) / (TempUpper - TempLower)

    tempo = fpHLower + (Tempe - TempLower) * tempo

    return tempo

def ionic_strength_molar_from_mg_L(ionic_strength_mg_L):
    """Convert NaCl-equivalent salinity in mg/L to M for M-506 correlations."""
    if ionic_strength_mg_L < 0:
        raise NorsokM506Error("ionic strength cannot be negative")
    # NORSOK M-506:2005 Table 6 specifies ionic strength as g/L or M and the
    # model assumes NaCl (58.44 g/mol).  This port's public input is mg/L, so
    # 58,440 mg/L, not 58.44 mg/L, is one mol/L.
    return ionic_strength_mg_L / 58_440.0


def _pH_equilibrium_constants(ITemp, IPressure, IIonicStrength):
    """Return the constants used by M-506:2005 equations (11)-(18).

    ``K1`` is returned on the hydrated-carbonic-acid basis used in equations
    (11) and (12).  Equation (16)'s underlying Atkinson/Plummer relation is for
    apparent H2CO3* (CO2(aq) + H2CO3), so it is divided by K0 before those
    equations multiply it by K0 again.
    """

    T_Fahrenheit = ITemp * 1.8 + 32.0
    T_Kelvin = ITemp + 273.15
    ionic_strength_molal = ionic_strength_molar_from_mg_L(IIonicStrength)
    pressure_psi = IPressure * 14.503774

    # NORSOK M-506:2005 section 8.2.3, immediately before equation (13).
    K0 = 0.00258

    # NORSOK M-506:2005 equation (13), including both I^0.5 and I terms.
    Ksp_FeCO3 = 10 ** -(
        10.13
        + 0.0182 * ITemp
        - 2.44 * ionic_strength_molal ** 0.5
        + 0.72 * ionic_strength_molal
    )

    # NORSOK M-506:2005 equations (14) and (15).  P is psi and the salting
    # term uses molal ionic strength.  The standard explicitly requires CO2
    # fugacity, rather than partial pressure, when KH is used in equations
    # (11) and (12).
    salting_pressure_factor = 10 ** -(
        1.790e-4 * pressure_psi + 0.107 * ionic_strength_molal
    )
    if ITemp <= 80.0:
        KH = (
            55.5084
            * math.exp(
                -(
                    4.8
                    + 3934.4 / T_Kelvin
                    - 941290.2 / T_Kelvin ** 2
                )
            )
            * salting_pressure_factor
        )
    else:
        KH = (
            55.5084
            * math.exp(
                -(
                    1713.53
                    * (1.0 - T_Kelvin / 647.0) ** (1.0 / 3.0)
                    / T_Kelvin
                    + 3.875
                    + 3680.09 / T_Kelvin
                    - 1198506.1 / T_Kelvin ** 2
                )
            )
            * salting_pressure_factor
        )

    # NORSOK M-506:2005 equation (16), including all pressure and ionic-
    # strength terms.  Rev. 2 prints 168491.5/T_K^2, a one-zero transcription
    # defect: it makes the apparent K1 about 5e10 at 25 C.  The primary article
    # abstract prints 1684915/T_K^2; see PLUMMER_BUSENBERG_K1_SOURCE for author,
    # title, section, exact content and scope. Use that coefficient transparently,
    # then convert apparent H2CO3* K1 to the intrinsic H2CO3 basis because
    # M-506 equations (11)/(12) retain the separate hydration K0 factor.  The
    # exact mass balance is K1 = K1* (1 + K0) / K0; omitting (1 + K0) is a
    # 0.258% approximation at 25 C, not an identity.
    K1_apparent = 10 ** -(
        356.3094
        + 0.06091964 * T_Kelvin
        - 21834.37 / T_Kelvin
        - 126.8339 * math.log10(T_Kelvin)
        + 1684915.0 / T_Kelvin ** 2
        - 2.564e-5 * pressure_psi
        - 0.491 * ionic_strength_molal ** 0.5
        + 0.379 * ionic_strength_molal
        - 0.06506 * ionic_strength_molal ** 1.5
        - 1.458e-3 * ionic_strength_molal * T_Fahrenheit
    )
    K1 = K1_apparent * (1.0 + K0) / K0

    # NORSOK M-506:2005 equation (17), including all pressure and ionic-
    # strength terms.
    K2 = 10 ** -(
        107.8871
        + 0.03252849 * T_Kelvin
        - 5151.79 / T_Kelvin
        - 38.92561 * math.log10(T_Kelvin)
        + 563713.9 / T_Kelvin ** 2
        - 2.118e-5 * pressure_psi
        - 1.255 * ionic_strength_molal ** 0.5
        + 0.867 * ionic_strength_molal
        - 0.174 * ionic_strength_molal ** 1.5
        - 1.588e-3 * T_Fahrenheit * ionic_strength_molal
    )

    # NORSOK M-506:2005 equation (18).
    KW = 10 ** -(
        29.3868
        - 0.0737549 * T_Kelvin
        + 7.47881e-5 * T_Kelvin ** 2
    )
    return KH, K0, K1, K2, Ksp_FeCO3, KW


def pHCalculator(ITemp, IPressure, ICO2Pressure, IBicarb, IIonicStrength, CalcOfpH):

    if CalcOfpH not in (1, 2):
        raise NorsokM506Error("CalcOfpH must select path 1 or 2")
    if IBicarb < 0:
        raise NorsokM506Error("bicarbonate cannot be negative")
    if not 5.0 <= ITemp <= 150.0:
        raise NorsokM506EnvelopeError(
            "temperature_C", ITemp, 5.0, 150.0, "NORSOK M-506 Table 6"
        )
    if not 1.0 <= IPressure <= 1000.0:
        raise NorsokM506EnvelopeError(
            "total_pressure_bar_abs", IPressure, 1.0, 1000.0,
            "NORSOK M-506 Table 6",
        )
    if not 0.0 <= ICO2Pressure <= 10.0:
        raise NorsokM506EnvelopeError(
            "co2_fugacity_bar", ICO2Pressure, 0.0, 10.0,
            "NORSOK M-506 Table 6",
        )
    if IBicarb > 20_000.0:
        raise NorsokM506EnvelopeError(
            "bicarbonate_mg_L", IBicarb, 0.0, 20_000.0,
            "NORSOK M-506 Table 6",
        )
    if not 0.0 <= IIonicStrength <= 175_000.0:
        raise NorsokM506EnvelopeError(
            "ionic_strength_mg_L", IIonicStrength, 0.0, 175_000.0,
            "NORSOK M-506 Table 6",
        )

    # NORSOK M-506:2005 Table 6 specifies HCO3- in mg/L or mM; molecular
    # weight 61.02 g/mol converts the advertised mg/L input to mol/L here.
    Bicarb = IBicarb / (1000 * 61.02)
    KH, K0, k1, k2, Ksp_FeCO3, KW = _pH_equilibrium_constants(
        ITemp, IPressure, IIonicStrength
    )

    #Start calculating pH
    # Path 1 solves unsaturated formation water (equation 11); path 2 repeats
    # with the FeCO3-saturation term from equation (12), section 8.2.2.  The
    # inclusive upper bound is required so both documented paths execute.
    for pHCalcNo in range(1, CalcOfpH + 1):
        if pHCalcNo == 1: SatpHCoeff = 0
        elif ICO2Pressure > 0:
            SatpHCoeff = 2 * Ksp_FeCO3 / (KH * K0 * k1 * k2 * ICO2Pressure)
        else:
            raise NorsokM506Error("FeCO3-saturated pH requires positive CO2 pressure")
        Hion = 10 ** (-3.5)
        if ICO2Pressure > 20: Hion = 10 ** (-2.9)

        converged = False
        for _ in range(100):
            # NORSOK M-506:2005 equations (11) and (12).
            fHion = SatpHCoeff * Hion ** 4 + Hion ** 3 + Bicarb * Hion ** 2 - Hion * (k1 * K0 * KH * ICO2Pressure + KW) - 2 * k1 * k2 * K0 * KH * ICO2Pressure
            fdHion = 4 * SatpHCoeff * Hion ** 3 + 3 * Hion * Hion + 2 * Bicarb * Hion - (k1 * K0 * KH * ICO2Pressure + KW)
            if fdHion == 0 or not math.isfinite(fdHion):
                raise NorsokPHConvergenceError("undefined derivative in pH solve")
            oldHion = Hion
            Hion = oldHion - fHion / fdHion
            # Newton can overshoot from the standard's fixed initial guess.
            # Damping to half the last positive iterate preserves Newton's
            # direction without accepting an unphysical negative concentration.
            if Hion <= 0:
                Hion = oldHion / 2.0
            if not math.isfinite(Hion):
                raise NorsokPHConvergenceError("pH solve left the finite domain")
            if abs(Hion - oldHion) < 10 ** -6 * oldHion:
                converged = True
                break

        if not converged:
            raise NorsokPHConvergenceError("pH solve did not converge in 100 iterations")

        tempo = round(-math.log10(Hion), 2)


    return tempo

def FugacityofCO2(CO2fraction, Pressure, Temp):

    # NORSOK M-506:2005 equations (7) and (8) use absolute temperature in
    # kelvin and cap the pressure in the fugacity coefficient at 250 bar.
    Temp_C = Temp + 273.15
    coefficient_pressure_bar = min(Pressure, 250.0)
    A = 10 ** (coefficient_pressure_bar * (0.0031 - 1.4 / Temp_C))
    CO2Pressure = CO2fraction * Pressure
    tempo = A * CO2Pressure
    return tempo

def Kt(temp):
    temp_table=(5,15,20, 40, 60, 80,90, 120, 150)
    value_table=(0.42,1.59,4.762,8.927,10.695,9.949,6.250,7.770,5.203)
    if not temp_table[0] <= temp <= temp_table[-1]:
        raise NorsokM506Error("temperature is outside the M-506 Table 1 range")
    if temp in temp_table:
        return value_table[temp_table.index(temp)]

    upper_index = next(i for i, temperature in enumerate(temp_table) if temperature > temp)
    temp_lower = temp_table[upper_index - 1]
    temp_upper = temp_table[upper_index]
    value_lower = value_table[upper_index - 1]
    value_upper = value_table[upper_index]
    # NORSOK M-506:2005 Table 1 supplies the nodes.  The missing temperature
    # offset made every value in an interval constant; retain signed linear
    # interpolation so decreasing intervals also reach their upper node.
    return value_lower + (value_upper - value_lower) * (temp - temp_lower) / (temp_upper - temp_lower)


def ResolvedCorrosionRate(CO2Fugacity, ShearStress, Temp, pH):
    """M-506 rate for resolved fugacity, wall shear and in-situ pH."""
    # This is a complete corrosion-rate accessor, not the lower-level Table-2
    # pH-factor primitive. M-506 Table 3 limits the rate model to 20-150 C, so
    # the export must enforce the same typed gate as both guarded wrappers.
    if not 20.0 <= Temp <= 150.0:
        raise NorsokM506EnvelopeError(
            "temperature_C", Temp, 20.0, 150.0,
            "NORSOK M-506 Table 3",
        )
    if not 3.5 <= pH <= 6.5:
        raise NorsokM506Error("pH is outside the M-506 model range")
    if not 0.1 <= CO2Fugacity <= 10.0:
        raise NorsokM506EnvelopeError(
            "co2_fugacity_bar", CO2Fugacity, 0.1, 10.0,
            "NORSOK M-506 Table 3",
        )
    if not 1.0 <= ShearStress <= 150.0:
        raise NorsokM506EnvelopeError(
            "wall_shear_stress_Pa", ShearStress, 1.0, 150.0,
            "NORSOK M-506 Table 3",
        )

    def rate_at_table_temperature(table_temperature):
        # NORSOK M-506:2005 equations (1), (2), and (3), with f(pH) from
        # Table 2 and Kt from Table 1.  Equation (3) at 5 C has no shear term.
        co2_exponent = 0.36 if table_temperature in (5.0, 15.0) else 0.62
        shear_factor = 1.0
        if table_temperature != 5.0:
            shear_exponent = 0.146 + 0.0324 * math.log10(CO2Fugacity)
            shear_factor = (ShearStress / 19.0) ** shear_exponent
        return (
            Kt(table_temperature)
            * CO2Fugacity ** co2_exponent
            * shear_factor
            * fpH_FixT(table_temperature, pH)
        )

    table = (5.0, 15.0, 20.0, 40.0, 60.0, 80.0, 90.0, 120.0, 150.0)
    if Temp in table:
        return rate_at_table_temperature(Temp)

    upper = next(value for value in table if value > Temp)
    lower = table[table.index(upper) - 1]
    lower_rate = rate_at_table_temperature(lower)
    upper_rate = rate_at_table_temperature(upper)
    # Section 5.2, immediately after equation (3), requires linear
    # interpolation of corrosion rate between the tabulated temperatures.
    return lower_rate + (upper_rate - lower_rate) * (Temp - lower) / (upper - lower)

def Cal_Norsok(CO2fraction, Pressure, Temp, v_sg, v_sl , mass_g, mass_l,vol_g,vol_l,holdup,vis_g,vis_l,roughness,diameter, fPH_In,\
                IBicarb, IIonicStrength, CalcOfpH):
    CO2Fugacity= FugacityofCO2(CO2fraction, Pressure, Temp)
    ShearStress=Shearstress(v_sg, v_sl , mass_g, mass_l,vol_g,vol_l,holdup,vis_g,vis_l,roughness,diameter)
    if fPH_In not in (0, 0.0, None):
        raise NorsokM506Error(
            "Cal_Norsok cannot defend a caller-supplied correction factor; "
            "use the resolved-pH wrapper"
        )
    ph=pHCalculator(Temp, Pressure, CO2Fugacity, IBicarb, IIonicStrength, CalcOfpH)
    return ResolvedCorrosionRate(CO2Fugacity, ShearStress, Temp, ph)
