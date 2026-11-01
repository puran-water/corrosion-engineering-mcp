"""
Tier 2 Tool: Predict Sweet CO₂ Corrosion

PROVENANCE:
This tool is a direct wrapper around the vendored NORSOK M-506 implementation from:
- Repository: https://github.com/dungnguyen2/norsokm506 (MIT License)
- Original author: Dung Nguyen
- Vendored location: external/norsokm506/norsokm506_01.py
- Wrapper location: data/norsok_internal_corrosion.py

The vendored implementation targets the official NORSOK Standard:
- Standard: NORSOK M-506 "CO₂ Corrosion Rate Calculation Model" Rev. 2 (2005)
- Publisher: Standards Norway
- Authority: Norwegian oil & gas industry consortium
- URL: https://www.standard.no/en/sectors/energi-og-klima/petroleum/norsok-standard-categories/m-material/m-5061/

Model equations per NORSOK M-506:
- Equations (1)-(3): temperature-specific corrosion-rate equations
- pH correction: Table 2
- Temperature constants: Table 1; rate interpolation per section 5.2

SCOPE — READ THIS BEFORE USING:
This implements NORSOK M-506, which is a **CO₂ corrosion model for carbon steel**.
It does NOT model H₂S. The vendored implementation
(external/norsokm506/norsokm506_01.py) contains no H2S, sulfide, FeS, or sour-
corrosion equation.

Any declared `h2s_fraction > 0` is therefore rejected with a typed
`H2SNotModelledError` (OUT_OF_SCOPE). This applies to mixed CO₂/H₂S streams as
well as pure H₂S — a CO₂-only rate understates a sour stream just as badly as it
understates a pure-H₂S one.

The module filename retains "h2s" for import compatibility; the registered MCP
tool is already named `corrosion_predict_co2`.

Covers:
- CO₂ corrosion (sweet corrosion) on carbon steel — the model's actual domain

Does NOT cover (use a dedicated screen):
- H₂S / sour corrosion rate
- Sulfide stress cracking, HIC, SOHIC
- Biogenic sulfuric acid attack (sewer and digester headspace)

Handles:
- pH calculation from water chemistry
- Multiphase flow (gas-liquid)
- Temperature effects inside the Table-3 service envelope (20-150°C)
- Scaling and protective film formation

Performance: ~0.5 seconds
Validation standing: Khakzad et al. Table-2 rows 2-4 reproduce within 1.3%.
Row 1 remains an unresolved -5.86% published-output mismatch that an explicit
rounding-box sweep cannot explain.  Dogan & Altinten's sole exact prose value
reproduces as a raw equation-transcription anchor, but pCO2 equals total
pressure and is outside Table 3.  These are not evidence that M-506 predicts
asset or laboratory corrosion accurately.

MODIFICATIONS FROM ORIGINAL:
- Dual-path pH calculation (user-supplied vs calculated from chemistry)
- Bug fix: Honor user-supplied pH parameter (vendored Cal_Norsok always recalculates pH)
- Complete 18-parameter signature for all flow parameters
"""

from typing import Dict, Optional
import logging
import math

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, NonFiniteArithmeticError, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils import sour_service_basis
from corrosion_engineering_mcp.utils.index_presentation import format_nonzero_fixed

from corrosion_engineering_mcp.data.norsok_internal_corrosion import calculate_shear_stress, calculate_resolved_norsok_rate, calculate_insitu_pH, FugacityofCO2, get_ph_correction_factor, NORSOK_VALIDATION_SOURCE, NORSOK_DOGAN_VALIDATION_SOURCE

logger = logging.getLogger(__name__)

# ISO 15156-2 Figure 1 begins at 0.3 kPa H2S partial pressure. Retained for
# reporting only - the gate below rejects ANY declared H2S, not merely values
# at or above the normal Part-2 applicability boundary.
SOUR_SERVICE_PH2S_BAR = sour_service_basis.SOUR_SERVICE_PH2S_BAR


class GasCompositionRequiredError(CorrosionPublicError, ValueError):
    """The acid-gas composition was not declared.

    It used to default to zero, so a caller who simply had not described the gas
    received 0.0000 mm/y at confidence "high" with mechanism "none" and severity
    "negligible". That is the most dangerous possible shape for a missing input:
    a confident, specific, benign number.
    """

    def __init__(self, missing: list) -> None:
        super().__init__(
            "GAS_COMPOSITION_REQUIRED",
            f"GAS_COMPOSITION_REQUIRED: {', '.join(missing)} not declared. Absent "
            f"is not zero - an undeclared acid-gas composition previously returned "
            f"a high-confidence 0.0 mm/y. State the mole fractions explicitly; "
            f"0.0 is accepted as a declaration that the gas is absent.",
            details={"reason_code": "GAS_COMPOSITION_REQUIRED", "missing": missing},
        )


class H2SNotModelledError(CorrosionPublicError, ValueError):
    """Raised when H2S is declared. This model contains no H2S terms whatsoever.

    The vendored NORSOK M-506 implementation
    (external/norsokm506/norsokm506_01.py) has no H2S, sulfide, FeS, or sour-
    corrosion equation. `h2s_fraction` never entered the rate calculation - it
    only selected a `mechanism` string and appended an advisory.

    Before this gate, `co2_fraction=0, h2s_fraction=0.005` at 35 C returned
    0.0 mm/y, mechanism "none", severity "negligible", confidence "high" - i.e.
    the tool reported *no corrosion* for digester gas and sewer headspace, which
    are among the most aggressive services this practice runs.
    """

    def __init__(self, h2s_fraction: float, pH2S_bar: float, co2_fraction: float) -> None:
        sour = pH2S_bar >= sour_service_basis.SOUR_SERVICE_PH2S_BAR
        super().__init__(
            "OUT_OF_SCOPE",
            "H2S_NOT_MODELLED: this model is NORSOK M-506, which is a CO2 corrosion "
            "model and contains no H2S terms. Declared H2S fraction "
            f"{repr(float(h2s_fraction))} (pH2S {repr(float(pH2S_bar))} bar"
            + (
                ", at or above the ISO 15156-2 normal Part-2 applicability boundary"
                if sour
                else ", below the normal Part-2 boundary; SSC is not thereby excluded"
            )
            + "). Alternative: use a wet-H2S / sour-service screen for cracking "
            "(SSC/HIC/SOHIC) and a biogenic-acid model for sewer or digester "
            "headspace. Do not infer a rate from the CO2-only result.",
            details={
                "reason_code": "H2S_NOT_MODELLED",
                "h2s_fraction": h2s_fraction,
                "h2s_partial_pressure_bar": pH2S_bar,
                "co2_fraction": co2_fraction,
                "sour_service_by_iso15156": sour,
                "sour_service_by_iso15156_meaning": (
                    "at_or_above_part2_normal_applicability_boundary;"
                    "false_does_not_exclude_ssc"
                ),
                "sour_service_threshold_bar": sour_service_basis.SOUR_SERVICE_PH2S_BAR,
                "sour_service_threshold_kPa": sour_service_basis.SOUR_SERVICE_PH2S_KPA,
                "alternative": "wet-H2S/sour-service cracking screen; biogenic acid model",
            },
        )


class BicarbonateRequiredError(CorrosionPublicError, ValueError):
    """Calculated-pH mode cannot infer an undeclared bicarbonate concentration."""

    def __init__(self) -> None:
        super().__init__(
            "BICARBONATE_REQUIRED",
            "BICARBONATE_REQUIRED: bicarbonate_mg_L must be declared when pH is "
            "not supplied. Absent bicarbonate is not zero.",
            details={
                "reason_code": "BICARBONATE_REQUIRED",
                "missing": ["bicarbonate_mg_L"],
                "applies_when": "pH is not supplied and co2_fraction > 0",
            },
        )


class PHOutsideModelRangeError(CorrosionPublicError, ValueError):
    """The pH cannot be used without substituting a different model input."""

    def __init__(self, pH: float, source: str) -> None:
        super().__init__(
            "PH_OUTSIDE_MODEL_RANGE",
            f"PH_OUTSIDE_MODEL_RANGE: {source} pH {repr(float(pH))} is outside the "
            "NORSOK M-506 correction-factor range 3.5-6.5.",
            details={
                "reason_code": "PH_OUTSIDE_MODEL_RANGE",
                "pH": pH,
                "source": source,
                "model_range": {"minimum": 3.5, "maximum": 6.5},
            },
        )


class NorsokOutOfEnvelopeError(CorrosionPublicError, ValueError):
    """A Table 3/6 input cannot be silently extrapolated."""

    def __init__(
        self,
        quantity: str,
        value: float,
        minimum: float,
        maximum: float,
        source: str,
        *,
        maximum_inclusive: bool = True,
    ) -> None:
        interval = (
            f"[{repr(float(minimum))}, {repr(float(maximum))}]"
            if maximum_inclusive
            else f"[{repr(float(minimum))}, {repr(float(maximum))})"
        )
        super().__init__(
            "NORSOK_OUT_OF_ENVELOPE",
            f"NORSOK_OUT_OF_ENVELOPE: {quantity}={repr(float(value))} is outside "
            f"{source} {interval}.",
            details={
                "reason_code": "NORSOK_OUT_OF_ENVELOPE",
                "quantity": quantity,
                "value": value,
                "minimum": minimum,
                "maximum": maximum,
                "maximum_inclusive": maximum_inclusive,
                "source": source,
            },
        )


class HydraulicInputRequiredError(CorrosionPublicError, ValueError):
    """Nonzero-CO2 rate calculation requires a defensible wall shear."""

    def __init__(self, missing: list[str] | None = None) -> None:
        super().__init__(
            "HYDRAULIC_INPUT_REQUIRED",
            "HYDRAULIC_INPUT_REQUIRED: declare wall_shear_stress_Pa or every "
            "hydraulic input needed by M-506 equations (20)-(21); hidden "
            "hydraulic defaults have been removed.",
            details={
                "reason_code": "HYDRAULIC_INPUT_REQUIRED",
                "alternatives": [
                    "wall_shear_stress_Pa",
                    "complete_hydraulic_input_set",
                ],
                "missing_hydraulic_inputs": missing or [],
            },
        )


class HydraulicInputConflictError(CorrosionPublicError, ValueError):
    """A caller must choose reported shear or calculated shear, not both."""

    def __init__(self) -> None:
        super().__init__(
            "HYDRAULIC_INPUT_CONFLICT",
            "HYDRAULIC_INPUT_CONFLICT: provide either wall_shear_stress_Pa "
            "or the complete hydraulic input set, not both.",
            details={"reason_code": "HYDRAULIC_INPUT_CONFLICT"},
        )


_HYDRAULIC_INPUT_NAMES = (
    "superficial_gas_velocity_m_s",
    "superficial_liquid_velocity_m_s",
    "gas_mass_flow_kg_hr",
    "liquid_mass_flow_kg_hr",
    "gas_volumetric_flow_m3_hr",
    "liquid_volumetric_flow_m3_hr",
    "liquid_holdup_percent",
    "gas_viscosity_cp",
    "liquid_viscosity_cp",
    "pipe_roughness_m",
    "pipe_diameter_m",
)


def _resolve_wall_shear(
    wall_shear_stress_Pa: Optional[float],
    hydraulic_inputs: dict[str, Optional[float]],
) -> tuple[float, str]:
    supplied_hydraulics = {
        name: value for name, value in hydraulic_inputs.items() if value is not None
    }
    validate_float_domain_inputs(
        {
            "wall_shear_stress_Pa": wall_shear_stress_Pa,
            **supplied_hydraulics,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    if wall_shear_stress_Pa is not None and supplied_hydraulics:
        raise HydraulicInputConflictError()
    if wall_shear_stress_Pa is not None:
        if not math.isfinite(wall_shear_stress_Pa):
            raise NonFiniteArithmeticError(
                "validate_reported_wall_shear",
                operands={},
                non_finite_quantities=["wall_shear_stress_Pa"],
            )
        return wall_shear_stress_Pa, "reported"

    missing = [name for name in _HYDRAULIC_INPUT_NAMES if hydraulic_inputs[name] is None]
    if missing:
        raise HydraulicInputRequiredError(missing)

    values = {name: float(hydraulic_inputs[name]) for name in _HYDRAULIC_INPUT_NAMES}
    if (
        values["superficial_gas_velocity_m_s"] < 0.0
        or values["superficial_liquid_velocity_m_s"] < 0.0
        or values["superficial_gas_velocity_m_s"]
        + values["superficial_liquid_velocity_m_s"]
        <= 0.0
        or values["gas_mass_flow_kg_hr"] <= 0.0
        or values["liquid_mass_flow_kg_hr"] <= 0.0
        or values["gas_volumetric_flow_m3_hr"] <= 0.0
        or values["liquid_volumetric_flow_m3_hr"] <= 0.0
        or not 0.0 <= values["liquid_holdup_percent"] <= 100.0
        or values["gas_viscosity_cp"] <= 0.0
        or values["liquid_viscosity_cp"] <= 0.0
        or values["pipe_roughness_m"] < 0.0
        or values["pipe_diameter_m"] <= 0.0
    ):
        raise CorrosionPublicError(
            "INVALID_HYDRAULIC_INPUT",
            "INVALID_HYDRAULIC_INPUT: velocities must be non-negative with a "
            "positive sum; flows, viscosities and diameter must be positive; "
            "roughness must be non-negative; holdup must be 0-100 percent.",
            details={"reason_code": "INVALID_HYDRAULIC_INPUT"},
        )

    try:
        resolved_shear_Pa = calculate_shear_stress(
            values["superficial_gas_velocity_m_s"],
            values["superficial_liquid_velocity_m_s"],
            values["gas_mass_flow_kg_hr"],
            values["liquid_mass_flow_kg_hr"],
            values["gas_volumetric_flow_m3_hr"],
            values["liquid_volumetric_flow_m3_hr"],
            values["liquid_holdup_percent"],
            values["gas_viscosity_cp"],
            values["liquid_viscosity_cp"],
            values["pipe_roughness_m"],
            values["pipe_diameter_m"],
        )
    except (OverflowError, ZeroDivisionError, FloatingPointError) as exc:
        raise NonFiniteArithmeticError(
            "norsok_m506_equations_20_21_wall_shear",
            operands=values,
            non_finite_quantities=["wall_shear_stress_Pa"],
        ) from exc
    if not math.isfinite(resolved_shear_Pa):
        raise NonFiniteArithmeticError(
            "norsok_m506_equations_20_21_wall_shear",
            operands=values,
            non_finite_quantities=["wall_shear_stress_Pa"],
        )
    return (
        resolved_shear_Pa,
        "calculated_from_reported_hydraulics_m506_equations_20_21",
    )


@enforce_tool_input_contract("corrosion_predict_co2")
def predict_co2_h2s_corrosion(
    # Environmental conditions
    temperature_C: float,
    pressure_bar: float,
    # ABSENT IS NOT ZERO. These defaulted to 0.0, so a call that simply did not
    # state the gas composition returned 0.0000 mm/y, mechanism "none", severity
    # "negligible" at confidence "high" — a confident clean bill of health for a
    # stream nobody had described. None now means "not declared" and raises;
    # an explicit 0.0 is a declaration and is honoured.
    co2_fraction: Optional[float] = None,
    h2s_fraction: Optional[float] = None,

    # Water chemistry
    pH: Optional[float] = None,
    bicarbonate_mg_L: Optional[float] = None,
    ionic_strength_mg_L: Optional[float] = None,

    # A caller may report wall shear directly, or provide every hydraulic input
    # below so equations (20)-(21) can calculate it.  Nothing is defaulted.
    wall_shear_stress_Pa: Optional[float] = None,

    # Multiphase flow parameters
    superficial_gas_velocity_m_s: Optional[float] = None,
    superficial_liquid_velocity_m_s: Optional[float] = None,
    gas_mass_flow_kg_hr: Optional[float] = None,
    liquid_mass_flow_kg_hr: Optional[float] = None,
    gas_volumetric_flow_m3_hr: Optional[float] = None,
    liquid_volumetric_flow_m3_hr: Optional[float] = None,
    liquid_holdup_percent: Optional[float] = None,

    # Fluid properties
    gas_viscosity_cp: Optional[float] = None,
    liquid_viscosity_cp: Optional[float] = None,

    # Pipe geometry
    pipe_roughness_m: Optional[float] = None,
    pipe_diameter_m: Optional[float] = None,

    # Calculation options
    calc_iterations: int = 2,
) -> Dict:
    """
    Predict sweet CO₂ corrosion rate using the NORSOK M-506 model.

    Args:
        temperature_C: Temperature in degrees Celsius (20-150°C, Table 3)
        pressure_bar: Total pressure in bar absolute
        co2_fraction: CO₂ mole fraction in gas phase (0-1)
        h2s_fraction: H₂S mole fraction in gas phase; must be declared zero
        pH: Water pH. For nonzero CO₂, either this or bicarbonate is required.
            When supplied, it is the rate-model pH and the chemistry path is not run.
        bicarbonate_mg_L: Bicarbonate used only to calculate pH when pH is absent.
            If supplied with reported pH, it is retained and labelled ignored.
        ionic_strength_mg_L: NaCl-equivalent salinity in mg/L, used only to
            calculate pH. In calculated-pH mode an omitted value receives Table
            6's disclosed 50 g/L formation-water default. With reported pH, no
            default is created and a supplied value is retained as ignored.
        wall_shear_stress_Pa: Reported wall shear stress (1-150 Pa), mutually
            exclusive with the complete hydraulic input set.
        superficial_gas_velocity_m_s: Superficial gas velocity (m/s)
        superficial_liquid_velocity_m_s: Superficial liquid velocity (m/s)
        gas_mass_flow_kg_hr: Gas mass flow rate (kg/hr)
        liquid_mass_flow_kg_hr: Liquid mass flow rate (kg/hr)
        gas_volumetric_flow_m3_hr: Gas volumetric flow rate (m³/hr)
        liquid_volumetric_flow_m3_hr: Liquid volumetric flow rate (m³/hr)
        liquid_holdup_percent: Liquid holdup percentage (0-100)
        gas_viscosity_cp: Gas viscosity (cp)
        liquid_viscosity_cp: Liquid viscosity (cp)
        pipe_roughness_m: Pipe wall roughness (m)
        pipe_diameter_m: Pipe internal diameter (m)
        calc_iterations: NORSOK in-situ-pH calculation path (1 or 2)
            1 = unsaturated formation-water path (equation 11)
            2 = FeCO₃-saturated path (equation 12)

    Returns:
        For nonzero CO₂, a NORSOK rate payload including the reported or
        calculated pH. For declared zero CO₂, a narrow zero-CO₂-corrosion
        identity with optional ``pH_reported`` and no mechanism or severity.

    Example:
        >>> result = predict_co2_h2s_corrosion(
        ...     temperature_C=60.0,
        ...     pressure_bar=50.0,
        ...     co2_fraction=0.05,
        ...     h2s_fraction=0.0,
        ...     pH=5.0,
        ...     wall_shear_stress_Pa=19.0,
        ... )
        >>> print(f"Corrosion rate: {result['corrosion_rate_mm_y']:.2f} mm/y")
        Corrosion rate: 11.71 mm/y

    Raises:
        ValueError: If parameters are outside NORSOK M-506 validity range
        RuntimeError: If NORSOK calculation fails
    """
    validate_float_domain_inputs(locals())

    # Gas composition must be DECLARED, not defaulted — checked FIRST, before any
    # range validation, because a None cannot be range-checked.
    if co2_fraction is None or h2s_fraction is None:
        missing = [
            n for n, v in (("co2_fraction", co2_fraction), ("h2s_fraction", h2s_fraction))
            if v is None
        ]
        raise GasCompositionRequiredError(missing)

    # Validate the standard's hard pressure envelope before any short-circuit.
    if not 1.0 <= pressure_bar <= 1000.0:
        raise NorsokOutOfEnvelopeError(
            "total_pressure_bar_abs", pressure_bar, 1.0, 1000.0,
            "NORSOK M-506 Table 3",
        )

    if not 20.0 <= temperature_C <= 150.0:
        raise NorsokOutOfEnvelopeError(
            "temperature_C", temperature_C, 20.0, 150.0,
            "NORSOK M-506 Table 3",
        )

    if not (0.0 <= co2_fraction <= 1.0):
        raise ValueError(f"CO₂ fraction {co2_fraction} must be between 0 and 1")

    # M-506 Table 3 states pCO2 must be strictly below total pressure.  A pure
    # CO2 gas (co2_fraction == 1) is therefore outside the model envelope even
    # when the resulting fugacity happens to lie in the 0.1-10 bar band.
    if co2_fraction == 1.0:
        raise NorsokOutOfEnvelopeError(
            "co2_partial_pressure_bar",
            pressure_bar,
            0.0,
            pressure_bar,
            "NORSOK M-506 Table 3",
            maximum_inclusive=False,
        )

    if not (0.0 <= h2s_fraction <= 1.0):
        raise ValueError(f"H₂S fraction {h2s_fraction} must be between 0 and 1")

    # Gate ANY declared H2S before doing anything else. Not just pure H2S: a
    # mixed CO2/H2S stream is equally unmodelled here, and returning the CO2-only
    # rate for it understates the service just as badly.
    if h2s_fraction > 0.0:
        raise H2SNotModelledError(
            h2s_fraction=h2s_fraction,
            pH2S_bar=h2s_fraction * pressure_bar,
            co2_fraction=co2_fraction,
        )

    # A supplied pH is part of the registered public contract even on the
    # zero-CO2 identity path.  Validate it before that early return so direct
    # and facade calls cannot disagree about the advertised 3.5-6.5 range.
    if pH is not None and not (3.5 <= pH <= 6.5):
        raise PHOutsideModelRangeError(pH, "supplied")

    # Short-circuit: zero CO₂ means zero *CO₂ corrosion*. It establishes neither
    # water pH nor freedom from oxygen, MIC, chloride, caustic, acid, or other
    # mechanisms, so the narrow result has no generic mechanism/severity field.
    if co2_fraction == 0.0:
        logger.info("CO₂ fraction is zero - returning zero CO₂ corrosion rate")
        return {
            "corrosion_rate_mm_y": 0.0,
            "corrosion_rate_mm_y_display": 0.0,
            "corrosion_rate_mpy": 0.0,
            "corrosion_rate_mpy_display": 0.0,
            "corrosion_rate_value_standing": (
                "exact_primaries_authoritative_display_fields_rounded"
            ),
            "pH_reported": pH,
            "pH_status": (
                "reported_not_used_for_zero_co2_rate"
                if pH is not None
                else "not_reported_not_required_for_zero_co2_rate"
            ),
            "co2_partial_pressure_bar": 0.0,
            "co2_partial_pressure_bar_display": 0.0,
            "co2_fugacity_bar": 0.0,
            "h2s_partial_pressure_bar": h2s_fraction * pressure_bar,
            "h2s_partial_pressure_bar_display": round(
                h2s_fraction * pressure_bar, 6
            ),
            "partial_pressure_value_standing": (
                "exact_primaries_authoritative_display_fields_rounded"
            ),
            "output_scope": "co2_corrosion_only",
            "co2_corrosion_basis": "co2_absent",
            "not_assessed": [
                "overall_water_corrosivity",
                "non_co2_corrosion_mechanisms",
                "material_suitability",
            ],
            "interpretation": "No CO₂ corrosion: CO₂ fraction is zero",
            "provenance": {
                "model": "NORSOK M-506 (short-circuit)",
                "version": "vendored Rev. 2 (2005) implementation",
                "implementation_basis": "vendored_norsok_m506_rev2_2005",
                "tool_tier": 2,
                "standards": ["NORSOK M-506:2005"],
                "confidence": "not_applicable_stoichiometric_identity",
                "validated_outputs": ["co2_corrosion_rate"],
                "assumptions": ["Zero CO₂ fraction implies zero CO₂ corrosion"],
                "warnings": [],
            },
        }

    if pH is None and bicarbonate_mg_L is None:
        # Bicarbonate sets calculated in-situ pH.  Substituting 0 mg/L here used
        # to turn an unknown water chemistry into a specific corrosion rate.
        raise BicarbonateRequiredError()

    # Calculate partial pressures and M-506 fugacity (equations 4-8).  The
    # equation-8 coefficient pressure is capped at 250 bar inside the helper.
    co2_partial_pressure_bar = co2_fraction * pressure_bar
    h2s_partial_pressure_bar = h2s_fraction * pressure_bar
    co2_fugacity_bar = FugacityofCO2(
        co2_fraction, pressure_bar, temperature_C
    )
    if not 0.1 <= co2_fugacity_bar <= 10.0:
        raise NorsokOutOfEnvelopeError(
            "co2_fugacity_bar", co2_fugacity_bar, 0.1, 10.0,
            "NORSOK M-506 Table 3",
        )

    hydraulic_inputs = {
        "superficial_gas_velocity_m_s": superficial_gas_velocity_m_s,
        "superficial_liquid_velocity_m_s": superficial_liquid_velocity_m_s,
        "gas_mass_flow_kg_hr": gas_mass_flow_kg_hr,
        "liquid_mass_flow_kg_hr": liquid_mass_flow_kg_hr,
        "gas_volumetric_flow_m3_hr": gas_volumetric_flow_m3_hr,
        "liquid_volumetric_flow_m3_hr": liquid_volumetric_flow_m3_hr,
        "liquid_holdup_percent": liquid_holdup_percent,
        "gas_viscosity_cp": gas_viscosity_cp,
        "liquid_viscosity_cp": liquid_viscosity_cp,
        "pipe_roughness_m": pipe_roughness_m,
        "pipe_diameter_m": pipe_diameter_m,
    }
    resolved_shear_Pa, wall_shear_source = _resolve_wall_shear(
        wall_shear_stress_Pa, hydraulic_inputs
    )
    if not 1.0 <= resolved_shear_Pa <= 150.0:
        raise NorsokOutOfEnvelopeError(
            "wall_shear_stress_Pa", resolved_shear_Pa, 1.0, 150.0,
            "NORSOK M-506 Table 3",
        )

    # Resolve chemistry only for the path that actually uses it. A reported pH
    # bypasses equations (11)/(12); echoing a Table-6 default or a supplied
    # salinity as "used" on that path created a false audit trail.
    if pH is None:
        # Table 6's formation-water default is 50 g/L, not the former facade's
        # hidden 5 g/L. Preserve whether the value was reported in the payload.
        if ionic_strength_mg_L is None:
            resolved_ionic_strength_mg_L = 50_000.0
            ionic_strength_input_status = (
                "defaulted_per_norsok_m506_table_6_formation_water_"
                "used_for_calculated_pH"
            )
        else:
            resolved_ionic_strength_mg_L = ionic_strength_mg_L
            ionic_strength_input_status = "reported_used_for_calculated_pH"
        if not 0.0 <= resolved_ionic_strength_mg_L <= 175_000.0:
            raise NorsokOutOfEnvelopeError(
                "ionic_strength_mg_L",
                resolved_ionic_strength_mg_L,
                0.0,
                175_000.0,
                "NORSOK M-506 Table 6",
            )
        resolved_bicarbonate_mg_L = bicarbonate_mg_L
        bicarbonate_input_status = "reported_used_for_calculated_pH"

        # Calculate pH with the M-506 equation (11)/(12) path selected above.
        pH_calculated = calculate_insitu_pH(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            co2_fugacity_bar=co2_fugacity_bar,
            bicarbonate_mg_L=resolved_bicarbonate_mg_L,
            ionic_strength_mg_L=resolved_ionic_strength_mg_L,
            calc_iterations=calc_iterations,
        )
        pH_used = pH_calculated
        pH_source = "calculated_from_chemistry"
        logger.info(f"Calculated pH from chemistry: {pH_calculated:.2f}")
    else:
        pH_calculated = None
        pH_used = pH
        pH_source = "reported"
        resolved_ionic_strength_mg_L = None
        resolved_bicarbonate_mg_L = None
        ionic_strength_input_status = (
            "ignored_reported_pH_used"
            if ionic_strength_mg_L is not None
            else "not_supplied_reported_pH_used"
        )
        bicarbonate_input_status = (
            "ignored_reported_pH_used"
            if bicarbonate_mg_L is not None
            else "not_supplied_reported_pH_used"
        )
        logger.info(f"Using user-supplied pH: {pH_used:.2f}")

    if not (3.5 <= pH_used <= 6.5):
        raise PHOutsideModelRangeError(
            pH_used,
            "calculated" if pH is None else "supplied",
        )

    # Calculate CO₂ corrosion rate using NORSOK M-506
    rate_operands = {
        "temperature_C": temperature_C,
        "pressure_bar": pressure_bar,
        "co2_partial_pressure_bar": co2_partial_pressure_bar,
        "wall_shear_stress_Pa": resolved_shear_Pa,
        "pH_used": pH_used,
    }
    try:
        corrosion_rate_mm_y = calculate_resolved_norsok_rate(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            co2_partial_pressure_bar=co2_partial_pressure_bar,
            shear_stress_Pa=resolved_shear_Pa,
            pH=pH_used,
        )
    except (OverflowError, ZeroDivisionError, FloatingPointError) as exc:
        raise NonFiniteArithmeticError(
            "vendored_norsok_m506_corrosion_rate",
            operands=rate_operands,
            non_finite_quantities=["corrosion_rate_mm_y"],
        ) from exc
    except Exception as e:
        logger.error(f"NORSOK M-506 calculation failed: {e}")
        raise RuntimeError(f"CO₂ corrosion calculation failed: {e}")
    if not math.isfinite(corrosion_rate_mm_y):
        raise NonFiniteArithmeticError(
            "vendored_norsok_m506_corrosion_rate",
            operands=rate_operands,
            non_finite_quantities=["corrosion_rate_mm_y"],
        )

    # Convert to mpy (mils per year)
    corrosion_rate_mpy = corrosion_rate_mm_y * 39.37
    if not math.isfinite(corrosion_rate_mpy):
        raise NonFiniteArithmeticError(
            "convert_corrosion_rate_mm_y_to_mpy",
            operands={"corrosion_rate_mm_y": corrosion_rate_mm_y},
            non_finite_quantities=["corrosion_rate_mpy"],
        )

    # Positive H2S and zero CO2 have already failed or returned, respectively.
    mechanism = "CO₂ sweet corrosion"

    # Build result dictionary
    result = {
        "corrosion_rate_mm_y": corrosion_rate_mm_y,
        "corrosion_rate_mm_y_display": round(corrosion_rate_mm_y, 3),
        "corrosion_rate_mpy": corrosion_rate_mpy,
        "corrosion_rate_mpy_display": round(corrosion_rate_mpy, 2),
        "corrosion_rate_value_standing": (
            "exact_primaries_authoritative_display_fields_rounded"
        ),
        # These fields distinguish the exact value used from its provenance.
        # A reported pH is never relabelled as calculated.
        "pH_used": pH_used,
        "pH_source": pH_source,
        "pH_reported": pH,
        "pH_calculated": pH_calculated,
        "co2_partial_pressure_bar": co2_partial_pressure_bar,
        "co2_partial_pressure_bar_display": round(co2_partial_pressure_bar, 4),
        "co2_fugacity_bar": co2_fugacity_bar,
        "h2s_partial_pressure_bar": h2s_partial_pressure_bar,
        "h2s_partial_pressure_bar_display": round(h2s_partial_pressure_bar, 6),
        "partial_pressure_value_standing": (
            "exact_primaries_authoritative_display_fields_rounded"
        ),
        "wall_shear_stress_Pa": resolved_shear_Pa,
        "wall_shear_source": wall_shear_source,
        "bicarbonate_mg_L_reported": bicarbonate_mg_L,
        "bicarbonate_mg_L_used": resolved_bicarbonate_mg_L,
        "bicarbonate_input_status": bicarbonate_input_status,
        "ionic_strength_mg_L_reported": ionic_strength_mg_L,
        "ionic_strength_mg_L_used": resolved_ionic_strength_mg_L,
        "ionic_strength_input_status": ionic_strength_input_status,
        "mechanism": mechanism,
        "acceptance_assessment_performed": False,
        "provenance": {
            "model": "NORSOK M-506 Rev. 2 (2005) repaired vendored implementation",
            "implementation_basis": "repaired_vendored_norsok_m506_rev2_2005",
            "standard": "NORSOK M-506 CO₂ Corrosion Rate Calculation Model",
            "material": "Carbon steel (unspecified grade)",
            "validation_datasets": [
                NORSOK_VALIDATION_SOURCE,
                NORSOK_DOGAN_VALIDATION_SOURCE,
            ],
            "validation_status": (
                "transcription_partially_verified_with_unresolved_published_mismatch"
            ),
            "validation_scope": (
                "Khakzad Table-2 rows 2-4 are in-envelope transcription checks; "
                "row 1 remains unresolved at -5.86%. Dogan & Altinten's sole "
                "exact prose value is a raw transcription anchor outside the "
                "strict Table-3 pCO2 < total-pressure envelope. This is not "
                "model-vs-reality validation."
            ),
            "unresolved_validation_cases": ["khakzad_table2_row1"],
            "outside_envelope_transcription_anchors": [
                "dogan_altinten_exact_prose_case1"
            ],
            "confidence": "partial_transcription_verification_not_asset_accuracy",
            "implementation_repairs": [
                "Table 1 temperature interpolation",
                "centipoise to Pa s conversion",
                "mg/L NaCl-equivalent salinity to mol/L conversion",
                "equation (11)/(12) pH path iteration",
                "equation (8) 250-bar fugacity-coefficient pressure cap",
                "equations (13)-(17) pressure and ionic-strength terms",
                "equations (14)-(15) CO2 fugacity input to pH",
                "Table 2 5 C coefficients",
                "150 C endpoint handling",
            ],
            "assumptions": (
                ["Equilibrium thermodynamics for pH calculation"]
                if pH_source == "calculated_from_chemistry"
                else []
            ) + [
                "Semi-empirical mass transfer correlations",
                "No protective scale formation (conservative)",
                (
                    f"NaCl-equivalent salinity used for calculated pH: "
                    f"{resolved_ionic_strength_mg_L:g} mg/L "
                    f"({ionic_strength_input_status})"
                    if pH_source == "calculated_from_chemistry"
                    else "Reported pH used directly; bicarbonate and ionic "
                    "strength did not enter the pH or rate calculation"
                ),
                (
                    f"Calculation iterations: {calc_iterations} "
                    f"({'saturated with FeCO₃' if calc_iterations == 2 else 'unsaturated'})"
                    if pH_source == "calculated_from_chemistry"
                    else "pH calculation iterations not used because pH was reported"
                ),
            ],
            "warnings": [],
        },
    }

    # Generate interpretation
    interpretation_parts = []

    interpretation_parts.append(
        "Predicted corrosion rate: "
        f"{format_nonzero_fixed(corrosion_rate_mm_y, 2)} mm/y "
        f"({format_nonzero_fixed(corrosion_rate_mpy, 1)} mpy)"
    )
    interpretation_parts.append(f"Mechanism: {mechanism}")

    if pH_used < 4.5:
        interpretation_parts.append(
            f"Low pH ({format_nonzero_fixed(pH_used, 2)}) - corrosion rate "
            "may be underestimated"
        )

    result["interpretation"] = ". ".join(interpretation_parts)

    return result
