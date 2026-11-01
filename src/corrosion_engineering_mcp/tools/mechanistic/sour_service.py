'Tier 2 Tool: wet-H2S sour service screening (SSC only).'

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, NonFiniteArithmeticError, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils.localized_criteria import sulfide_standing
from corrosion_engineering_mcp.utils import sour_service_basis

logger = logging.getLogger(__name__)

# [15156] ISO 15156-2 Figure 1 starts at 0.3 kPa.  Keep the standard's SI
# boundary as the executable value.  The customary shorthand "0.05 psi"
# (approximately 0.044 psi after exact conversion) is commentary only.
SOUR_THRESHOLD_KPA = sour_service_basis.SOUR_SERVICE_PH2S_KPA
SOUR_THRESHOLD_BAR = sour_service_basis.SOUR_SERVICE_PH2S_BAR

# ISO 15156-2 scope: carbon steels, low-alloy steels, cast irons.
# Everything else is ISO 15156-3 (CRAs and other alloys).
_PART_2_CLASSES = frozenset({"carbon_steel", "low_alloy_steel", "cast_iron"})

# [15156] ISO 15156-2:2020 Figure 1 vertices.  Between 1 and 100 kPa,
# the two diagonal boundaries are exactly one pH unit apart on the log-pressure
# axis.  Figure 1 continues the lower boundary horizontally below 1 kPa and
# both boundaries horizontally above 100 kPa.  Equality is assigned to the
# more severe adjacent region so floating-point rounding cannot under-classify.
_FIGURE_1_MIN_PH2S_KPA = 0.3
_FIGURE_1_MAX_PH2S_KPA = 1000.0

_ISO15156_PART2_2020_PREVIEW = {
    "standard": "ISO 15156-2:2020",
    "section": "7.1.1 and Figure 1",
    "access_status": "content_read_in_readable_iso_2020_preview",
    "url": (
        "https://cdn.standards.iteh.ai/samples/79659/"
        "4dd6188bfa6e45baac32183b9eb0fb36/ISO-15156-2-2020.pdf"
    ),
    "content_support": (
        "The 0.3 kPa Figure-1 boundary is where Part-2 precautions normally "
        "apply. Section 7.1.1 preserves that highly susceptible steels can "
        "crack in less severe environments, so the boundary is not an SSC "
        "impossibility line."
    ),
    "content_limit": (
        "The readable preview supports the normal-applicability caveat and "
        "Figure-1 geometry only; no broader equivalence between the 2015 and "
        "2020 editions is claimed."
    ),
}

_ISO15156_PART3_2020_PREVIEW = {
    "standard": "ISO 15156-3:2020",
    "section": "5 and 6.1",
    "access_status": "content_read_in_readable_iso_2020_preview",
    "url": (
        "https://cdn.standards.iteh.ai/samples/79660/"
        "5601e597f63e40a6a008c1a3d45cb3a2/ISO-15156-3-2020.pdf"
    ),
    "content_support": (
        "Section 5 identifies environmental factors affecting cracking, including "
        "H2S partial pressure, in-situ pH, chloride concentration, temperature "
        "and elemental sulfur. Section 6.1 directs material selection to Annex A "
        "limits or qualification by laboratory testing or documented field "
        "experience."
    ),
    "content_limit": (
        "The readable preview supports the governing factors and qualification "
        "routes. This tool does not encode or reproduce the licensed Annex A "
        "alloy-family environmental-limit tables."
    ),
}


def _figure_1_region_boundaries(pH2S_kPa: float) -> tuple[float, float]:
    """Return (Region 1/2, Region 2/3) pH lines from Figure 1."""

    log_pressure = math.log10(pH2S_kPa)
    region_1_2_pH = min(6.5, max(3.5, 4.5 + log_pressure))
    region_2_3_pH = min(5.5, max(3.5, 3.5 + log_pressure))
    return region_1_2_pH, region_2_3_pH

# Carbon-steel hardness ceiling for sour service. Widely cited as 22 HRC.
# NEEDS-PRIMARY-SOURCE: not machine-verified; used as a flag, not a pass.
HARDNESS_LIMIT_HRC = 22.0


VALID_WETNESS = ("dry", "condensing", "immersed", "intermittent")


def _format_exact_comparison(value: float, decimal_places: int) -> str:
    """Keep comparison prose exact, with labelled scientific trace values."""

    fixed = format(value, f".{decimal_places}f")
    if value != 0.0 and float(fixed) == 0.0:
        return format(value, ".3e")
    return repr(float(value))


# This tolerance is only for the different binary-float representations of the
# same supplied pressure.  It is deliberately far tighter than an engineering
# measurement tolerance: two independently measured values are evidence, not
# interchangeable estimates that this screen is entitled to reconcile.
_PH2S_RECONCILIATION_REL_TOL = 1.0e-9
_PH2S_RECONCILIATION_ABS_TOL_BAR = 1.0e-12


def _symmetric_relative_difference(first: float, second: float) -> float:
    """Return a swap-invariant relative difference using the pair mean."""

    scale = (abs(first) + abs(second)) / 2.0
    if scale == 0.0:
        return 0.0
    return abs(first - second) / scale


def _compare_pH2S_bases_in_ppmv(
    *,
    pH2S_bar: float,
    H2S_ppmv: float,
    pressure_bar_abs: float,
) -> tuple[bool, float]:
    """Compare both bases in the supplied composition representation.

    Converting ppmv to bar and then reversing which value takes that route adds
    a different final multiplication rounding.  Convert the direct partial
    pressure to ppmv instead: on reversal the same two ppmv floats are compared
    in the opposite order, making both the absolute difference and symmetric
    relative difference exactly orientation-independent.
    """

    direct_as_ppmv = (pH2S_bar / pressure_bar_abs) * 1.0e6
    absolute_difference_ppmv = abs(direct_as_ppmv - H2S_ppmv)
    absolute_tolerance_ppmv = (
        _PH2S_RECONCILIATION_ABS_TOL_BAR / pressure_bar_abs
    ) * 1.0e6
    symmetric_relative_difference = _symmetric_relative_difference(
        direct_as_ppmv, H2S_ppmv
    )
    consistent = (
        absolute_difference_ppmv
        <= math.nextafter(absolute_tolerance_ppmv, math.inf)
        or symmetric_relative_difference
        <= math.nextafter(_PH2S_RECONCILIATION_REL_TOL, math.inf)
    )
    return consistent, symmetric_relative_difference


class WetnessUnknownError(CorrosionPublicError, ValueError):
    'Wetness was not declared or was not a recognised value.'

    def __init__(self, given: str = "unknown") -> None:
        super().__init__(
            "WETNESS_REQUIRED",
            f"WETNESS_REQUIRED: wetness {given!r} is not one of "
            f"{' | '.join(VALID_WETNESS)}. Sulfide stress cracking requires liquid "
            f"water, so wetness decides whether this screen applies at all. It is "
            f"never inferred and never defaulted: assuming dry is unsafe, assuming "
            f"wet is useless, and treating a typo as dry is worse than both.",
            details={
                "reason_code": "WETNESS_REQUIRED",
                "given": given,
                "accepted": list(VALID_WETNESS),
            },
        )


class ContradictorySourInputError(CorrosionPublicError, ValueError):
    """Direct and composition-derived H2S partial pressures disagree."""

    def __init__(
        self,
        *,
        pH2S_bar_supplied: float,
        pH2S_bar_derived: float,
        H2S_ppmv_supplied: float,
        pressure_bar_abs_supplied: float,
    ) -> None:
        super().__init__(
            "CONTRADICTORY_INPUT",
            "CONTRADICTORY_INPUT: supplied pH2S_bar and the H2S_ppmv plus "
            "pressure_bar_abs pair describe different H2S partial pressures. "
            "Neither supplied basis was discarded.",
            details={
                "reason_code": "CONTRADICTORY_INPUT",
                "pH2S_bar_supplied": pH2S_bar_supplied,
                "pH2S_bar_derived": pH2S_bar_derived,
                "H2S_ppmv_supplied": H2S_ppmv_supplied,
                "pressure_bar_abs_supplied": pressure_bar_abs_supplied,
                "relative_tolerance": _PH2S_RECONCILIATION_REL_TOL,
                "absolute_tolerance_bar": _PH2S_RECONCILIATION_ABS_TOL_BAR,
            },
        )


class SourInputOutOfRangeError(CorrosionPublicError, ValueError):
    """A sour-service input violates a physical bound."""

    def __init__(
        self,
        field: str,
        value: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        explanation: str,
    ) -> None:
        bounds = []
        if minimum is not None:
            bounds.append(f"minimum {repr(float(minimum))}")
        if maximum is not None:
            bounds.append(f"maximum {repr(float(maximum))}")
        super().__init__(
            "SOUR_INPUT_OUT_OF_RANGE",
            f"SOUR_INPUT_OUT_OF_RANGE: {field}={value!r} violates "
            f"{', '.join(bounds)}. {explanation}",
            details={
                "reason_code": "SOUR_INPUT_OUT_OF_RANGE",
                "field": field,
                "value": value,
                "minimum": minimum,
                "maximum": maximum,
                "explanation": explanation,
            },
        )


class SourPartialPressureRequiredError(CorrosionPublicError, ValueError):
    """The direct basis is absent and the composition basis is incomplete."""

    def __init__(self, supplied_fields: list[str]) -> None:
        super().__init__(
            "H2S_PARTIAL_PRESSURE_REQUIRED",
            "H2S_PARTIAL_PRESSURE_REQUIRED: supply pH2S_bar, or H2S_ppmv "
            "together with pressure_bar_abs. A partial second basis is not "
            "silently discarded.",
            details={
                "reason_code": "H2S_PARTIAL_PRESSURE_REQUIRED",
                "supplied_fields": supplied_fields,
                "required_alternatives": [
                    ["pH2S_bar"],
                    ["H2S_ppmv", "pressure_bar_abs"],
                ],
            },
        )


@enforce_tool_input_contract("corrosion_screen_sour_service")
def screen_sour_service(
    material: str,
    H2S_ppmv: Optional[float] = None,
    pressure_bar_abs: Optional[float] = None,
    pH2S_bar: Optional[float] = None,
    in_situ_pH: Optional[float] = None,
    wetness: str = "unknown",
    dissolved_sulfide_mg_L: Optional[float] = None,
    hardness_HRC: Optional[float] = None,
    welded: Optional[bool] = None,
    temperature_C: Optional[float] = None,
) -> Dict[str, Any]:
    'Screen a service for sulfide stress cracking and related damage.'
    from corrosion_engineering_mcp.core.materials import canonical_id, get_material, MaterialCatalogError, UnknownMaterialError

    try:
        resolved = canonical_id(material)
        record = get_material(resolved)
    except MaterialCatalogError:
        raise UnknownMaterialError(material) from None

    # Validate against the ENUM, not against the single string "unknown".
    #
    # Checking only `wetness == "unknown"` meant every other unrecognised value
    # fell through to the `is_wet` membership test below, which is a whitelist,
    # and came back FALSE - i.e. dry. So `wetness="condesing"` on a wet 15 mbar
    # sour line returned `not_applicable_dry`: a spelling mistake silently
    # cleared the exact service this tool exists to catch. A gate that fails
    # open on a typo is not a gate.
    if wetness not in VALID_WETNESS:
        raise WetnessUnknownError(wetness)

    validate_float_domain_inputs(
        {
            "H2S_ppmv": H2S_ppmv,
            "pressure_bar_abs": pressure_bar_abs,
            "pH2S_bar": pH2S_bar,
            "in_situ_pH": in_situ_pH,
            "dissolved_sulfide_mg_L": dissolved_sulfide_mg_L,
            "hardness_HRC": hardness_HRC,
            "temperature_C": temperature_C,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )

    # ---- partial pressure --------------------------------------------------
    # Validate here as well as in the MCP schema: this function is exported and
    # used directly by tests and Python callers.
    if H2S_ppmv is not None and (
        not math.isfinite(H2S_ppmv) or not 0.0 <= H2S_ppmv <= 1_000_000.0
    ):
        raise SourInputOutOfRangeError(
            "H2S_ppmv",
            H2S_ppmv,
            minimum=0.0,
            maximum=1_000_000.0,
            explanation="A gas composition cannot contain more than 1,000,000 ppmv H2S.",
        )
    if pressure_bar_abs is not None and (
        not math.isfinite(pressure_bar_abs) or pressure_bar_abs <= 0.0
    ):
        raise SourInputOutOfRangeError(
            "pressure_bar_abs",
            pressure_bar_abs,
            minimum=0.0,
            explanation="Absolute pressure must be finite and greater than zero.",
        )
    if pH2S_bar is not None and (
        not math.isfinite(pH2S_bar) or pH2S_bar < 0.0
    ):
        raise SourInputOutOfRangeError(
            "pH2S_bar",
            pH2S_bar,
            minimum=0.0,
            explanation="H2S partial pressure must be finite and non-negative.",
        )
    pair_is_complete = H2S_ppmv is not None and pressure_bar_abs is not None
    # H2S_ppmv is meaningful only with total pressure. Total pressure by itself,
    # however, is useful alongside a direct partial pressure because it supplies
    # the physical pH2S <= P bound; do not mislabel that as a broken ppmv pair.
    if H2S_ppmv is not None and pressure_bar_abs is None:
        supplied = [
            name
            for name, value in (
                ("pH2S_bar", pH2S_bar),
                ("H2S_ppmv", H2S_ppmv),
                ("pressure_bar_abs", pressure_bar_abs),
            )
            if value is not None
        ]
        raise SourPartialPressureRequiredError(supplied)
    if pH2S_bar is None and not pair_is_complete:
        supplied = [
            name
            for name, value in (
                ("H2S_ppmv", H2S_ppmv),
                ("pressure_bar_abs", pressure_bar_abs),
            )
            if value is not None
        ]
        raise SourPartialPressureRequiredError(supplied)

    derived_pH2S_bar = None
    if pair_is_complete:
        h2s_fraction = H2S_ppmv * 1.0e-6
        if h2s_fraction == 0.0 and H2S_ppmv != 0.0:
            # The declared 1e-12 input floor makes this branch unreachable today.
            # Keep the stable-order fallback so a future validation edit cannot
            # restore the finite-zero defect at this exported Python accessor.
            derived_pH2S_bar = H2S_ppmv * (pressure_bar_abs * 1.0e-6)
        else:
            # Retain the established operation order for ordinary inputs so their
            # binary64 results remain bit-identical.
            derived_pH2S_bar = h2s_fraction * pressure_bar_abs

        if (
            derived_pH2S_bar == 0.0
            and H2S_ppmv != 0.0
            and pressure_bar_abs != 0.0
        ):
            raise NonFiniteArithmeticError(
                "derive_h2s_partial_pressure_bar_underflow",
                operands={
                    "H2S_ppmv": H2S_ppmv,
                    "pressure_bar_abs": pressure_bar_abs,
                },
            )
    if derived_pH2S_bar is not None and not math.isfinite(derived_pH2S_bar):
        raise NonFiniteArithmeticError(
            "derive_h2s_partial_pressure_bar",
            operands={
                "H2S_ppmv": H2S_ppmv,
                "pressure_bar_abs": pressure_bar_abs,
            },
            non_finite_quantities=["pH2S_bar_derived"],
        )
    if (
        derived_pH2S_bar is not None
        and pressure_bar_abs is not None
        and derived_pH2S_bar > pressure_bar_abs
    ):
        raise SourInputOutOfRangeError(
            "pH2S_bar_derived",
            derived_pH2S_bar,
            minimum=0.0,
            maximum=pressure_bar_abs,
            explanation="A component partial pressure cannot exceed total absolute pressure.",
        )
    if (
        pH2S_bar is not None
        and pressure_bar_abs is not None
        and pH2S_bar > pressure_bar_abs
    ):
        raise SourInputOutOfRangeError(
            "pH2S_bar",
            pH2S_bar,
            minimum=0.0,
            maximum=pressure_bar_abs,
            explanation="A component partial pressure cannot exceed total absolute pressure.",
        )

    if pH2S_bar is not None and derived_pH2S_bar is not None:
        pair_is_consistent, symmetric_relative_difference = (
            _compare_pH2S_bases_in_ppmv(
                pH2S_bar=pH2S_bar,
                H2S_ppmv=H2S_ppmv,
                pressure_bar_abs=pressure_bar_abs,
            )
        )
        if not pair_is_consistent:
            raise ContradictorySourInputError(
                pH2S_bar_supplied=pH2S_bar,
                pH2S_bar_derived=derived_pH2S_bar,
                H2S_ppmv_supplied=H2S_ppmv,
                pressure_bar_abs_supplied=pressure_bar_abs,
            )
        # Both values describe the same reported condition within the narrowly
        # declared representation tolerance, but they can still straddle an
        # executable threshold.  Reconciliation must therefore produce one
        # canonical decision value.  The more severe (larger) value is used for
        # every downstream comparison; retaining the direct field as the
        # decision value made a sub-tolerance ordering difference flip the SSC
        # verdict.
        supplied_pH2S_bar = pH2S_bar
        decision_pH2S_bar = max(supplied_pH2S_bar, derived_pH2S_bar)
        if derived_pH2S_bar > supplied_pH2S_bar:
            decision_source = "composition_derived"
        elif supplied_pH2S_bar > derived_pH2S_bar:
            decision_source = "direct"
        else:
            decision_source = "identical_both_bases"
        input_reconciliation = {
            "status": "consistent",
            "pH2S_bar_supplied": supplied_pH2S_bar,
            "pH2S_bar_derived": derived_pH2S_bar,
            "H2S_ppmv_supplied": H2S_ppmv,
            "pressure_bar_abs_supplied": pressure_bar_abs,
            "relative_tolerance": _PH2S_RECONCILIATION_REL_TOL,
            "absolute_tolerance_bar": _PH2S_RECONCILIATION_ABS_TOL_BAR,
            "symmetric_relative_difference": symmetric_relative_difference,
            "relative_difference_denominator": "mean_absolute_pair_value",
            "comparison_representation": "H2S_ppmv",
            "decision_pH2S_bar": decision_pH2S_bar,
            "decision_policy": "more_severe_reconciled_value",
            "decision_source": decision_source,
        }
        basis = (
            f"pH2S {supplied_pH2S_bar:g} bar supplied directly and reconciled "
            f"with {H2S_ppmv:g} ppmv at {pressure_bar_abs:g} bar(a); the more "
            f"severe reconciled value {decision_pH2S_bar:g} bar "
            f"({decision_source}) is canonical for every decision"
        )
        pH2S_bar = decision_pH2S_bar
    elif derived_pH2S_bar is not None:
        pH2S_bar = derived_pH2S_bar
        input_reconciliation = {
            "status": "single_composition_basis",
            "pH2S_bar_supplied": None,
            "pH2S_bar_derived": derived_pH2S_bar,
            "H2S_ppmv_supplied": H2S_ppmv,
            "pressure_bar_abs_supplied": pressure_bar_abs,
        }
        basis = f"{H2S_ppmv:g} ppmv at {pressure_bar_abs:g} bar(a)"
    else:
        input_reconciliation = {
            "status": (
                "single_direct_basis_with_total_pressure_bound"
                if pressure_bar_abs is not None
                else "single_direct_basis"
            ),
            "pH2S_bar_supplied": pH2S_bar,
            "pH2S_bar_derived": None,
            "H2S_ppmv_supplied": None,
            "pressure_bar_abs_supplied": pressure_bar_abs,
        }
        basis = f"pH2S {pH2S_bar:g} bar supplied directly"
        if pressure_bar_abs is not None:
            basis += f" and checked against {pressure_bar_abs:g} bar(a) total pressure"

    # Resolve every wire-visible pressure unit once, check it, and reuse it.
    # A finite accepted bar value can overflow when multiplied into kPa/mbar;
    # the result must fail before a region or cracking verdict is constructed.
    pH2S_kPa = pH2S_bar * 100.0
    pH2S_mbar = pH2S_bar * 1000.0
    non_finite_conversions = [
        quantity
        for quantity, value in (
            ("pH2S_kPa", pH2S_kPa),
            ("pH2S_mbar", pH2S_mbar),
        )
        if not math.isfinite(value)
    ]
    if non_finite_conversions:
        operands = {"pH2S_bar": pH2S_bar}
        if H2S_ppmv is not None:
            operands["H2S_ppmv"] = H2S_ppmv
        if pressure_bar_abs is not None:
            operands["pressure_bar_abs"] = pressure_bar_abs
        raise NonFiniteArithmeticError(
            "convert_h2s_partial_pressure_units",
            operands=operands,
            non_finite_quantities=non_finite_conversions,
        )

    warnings: List[str] = []
    residual: List[Dict[str, Any]] = []

    is_wet = wetness in {"condensing", "immersed", "intermittent"}
    # This comparison is executable only for Part-2 materials. It is calculated
    # once here but is neither exposed nor used as a Part-3 decision boundary.
    above_threshold = pH2S_bar >= sour_service_basis.SOUR_SERVICE_PH2S_BAR

    # ---- which PART of the standard applies --------------------------------
    # ISO 15156-2 covers carbon steels, low-alloy steels and cast irons.
    # ISO 15156-3 covers corrosion-resistant alloys and other alloys.
    #
    # This tool previously directed EVERY material to Part 2, including 316L.
    # That is not a conservative simplification: the SSC region map is a Part 2
    # construct built for carbon and low-alloy steel, and Part 3 does not use it
    # at all - it sets per-alloy-family environmental limits on pH2S, chloride,
    # temperature and elemental sulfur. Emitting "Region 2" for any Part-3
    # material points the reader at the wrong tables.
    part = 2 if record.classification in _PART_2_CLASSES else 3

    # ---- region (Part 2 only) ----------------------------------------------
    region_1_2_pH: Optional[float] = None
    region_2_3_pH: Optional[float] = None
    if part == 3:
        region = None
        region_note = (
            f"{resolved} resolves to material family {record.classification}. ISO "
            f"15156-3 covers CRAs and other alloys, so Part 3 applies, NOT Part 2. "
            f"The numbered SSC region map is a "
            f"Part 2 construct for carbon and low-alloy steels and is not defined "
            f"here. Part 3 sets per-alloy-family environmental limits on H2S partial "
            f"pressure, in-situ pH, chloride, temperature and elemental sulfur, or "
            f"requires qualification. Those Annex A tables are not encoded here."
        )
        warnings.append(
            "ISO 15156-3 alloy-family specific environmental limits are not encoded "
            "on this "
            "server. H2S partial pressure, temperature, chloride, elemental sulfur "
            "and the other Section-5 factors must be checked against Annex A or the "
            "alloy must be qualified under Section 6.1; this screen cannot clear or "
            "condemn this Part-3 material."
        )
    elif not above_threshold:
        region = 0
        region_note = (
            f"pH2S {_format_exact_comparison(pH2S_mbar, 3)} mbar is below the "
            "ISO 15156-2:2020 normal Part-2 applicability boundary of "
            f"{repr(float(sour_service_basis.SOUR_SERVICE_PH2S_BAR * 1000.0))} mbar "
            f"({repr(float(sour_service_basis.SOUR_SERVICE_PH2S_KPA))} kPa, "
            "the Figure 1 SI "
            "boundary). Routine Part-2 qualification requirements do not normally "
            "apply below this boundary; SSC is not thereby excluded, particularly "
            "for highly susceptible steels under Section 7.1.1."
        )
    elif in_situ_pH is None:
        region = None
        region_note = (
            f"pH2S {_format_exact_comparison(pH2S_mbar, 3)} mbar is above the sour threshold, but the "
            f"region cannot be assigned without in-situ pH. Supply it - the "
            f"ISO 15156-2 Figure 1 boundaries vary diagonally with log10(pH2S)."
        )
        warnings.append("IN_SITU_PH_REQUIRED to assign a region")
    elif pH2S_kPa > _FIGURE_1_MAX_PH2S_KPA:
        region = None
        region_note = (
            f"pH2S {repr(float(pH2S_kPa))} kPa is above the "
            f"{repr(float(_FIGURE_1_MAX_PH2S_KPA))} kPa upper extent "
            "of ISO 15156-2 Figure 1. The standard notes uncertainty outside "
            "that extent, so this tool does not extrapolate a numbered region."
        )
        warnings.append("PH2S_ABOVE_ISO15156_FIGURE_1_EXTENT")
    else:
        region_1_2_pH, region_2_3_pH = _figure_1_region_boundaries(pH2S_kPa)
        if in_situ_pH <= region_2_3_pH:
            region = 3
        elif in_situ_pH <= region_1_2_pH:
            region = 2
        else:
            region = 1
        region_note = (
            f"ISO 15156-2 Region {region} at pH2S {_format_exact_comparison(pH2S_mbar, 3)} mbar, in-situ "
            f"pH {_format_exact_comparison(in_situ_pH, 2)}; Figure 1 diagonal "
            f"boundaries at this pressure are pH "
            f"{repr(float(region_1_2_pH))} (Region 1/2) and "
            f"{repr(float(region_2_3_pH))} (Region 2/3). Equality snaps to the more "
            f"severe adjacent region."
        )

    # ---- wetness gate ------------------------------------------------------
    if not is_wet:
        cracking_verdict = "not_applicable_dry"
        cracking_note = (
            f"Declared {wetness}. SSC requires liquid water; with none present the "
            f"cracking question does not arise. This depends entirely on the "
            f"declaration being true at every point in the system - a stream that "
            f"cools below its dew point anywhere is condensing there."
        )
    elif part == 3:
        cracking_verdict = (
            "part3_alloy_specific_limits_or_qualification_required"
        )
        cracking_note = (
            f"{region_note} ISO 15156-3:2020 Sections 5 and 6.1 require the "
            "material-family-specific Annex A environmental limits or a permitted "
            "qualification route to be checked. This tool encodes neither the "
            "Annex A tables for CRAs and other alloys nor a qualification artifact, "
            "so it makes no "
            "material clearance or disqualification at this H2S pressure."
        )
    elif not above_threshold:
        cracking_verdict = (
            "below_part2_normal_applicability_boundary_ssc_not_excluded"
        )
        cracking_note = region_note
    else:
        cracking_verdict = "sour_service_qualification_required"
        cracking_note = (
            f"{region_note} Material must be qualified to ISO 15156-{part} / NACE "
            f"MR0175. That is a materials qualification with hardness, "
            f"heat-treatment and welding requirements - not something "
            f"this tool can discharge."
        )

    # ---- mechanisms this SSC screen does not clear -------------------------
    # The 0.3-kPa line starts normal Part-2 precautions; it is not a physical
    # no-cracking boundary.  Residual corrosion mechanisms remain separate.
    if is_wet and (H2S_ppmv or pH2S_bar):
        residual.append({
            "mechanism": "wet_H2S_general_and_localized_corrosion",
            "status": "NOT_CLEARED",
            "why": (
                (
                    "Proceeds below the ISO 15156 Part-2 applicability boundary. "
                    if part == 2
                    else (
                        "Is not cleared by the unresolved ISO 15156-3 alloy-specific "
                        "qualification standing. "
                    )
                )
                + "This screen addresses cracking only and this server has no "
                "validated sour rate model - corrosion_predict_co2 rejects any "
                "declared H2S."
            ),
        })

    if dissolved_sulfide_mg_L is not None and dissolved_sulfide_mg_L > 0.0:
        standing = sulfide_standing(dissolved_sulfide_mg_L)
        residual.append({
            "mechanism": "sulfide_attack_on_passive_film",
            "status": "NOT_CLEARED",
            "standing": standing,
            "why": (
                f"Dissolved sulfide {dissolved_sulfide_mg_L:g} mg/L receives the "
                f"descriptive grade {standing['grade']}. Published 316L evidence "
                "shows continuous degradation and no critical concentration; "
                "this residual mechanism therefore remains not cleared on both "
                "sides of the internal 0.1 mg/L grade boundary. The grade makes "
                "no material or localized-model verdict."
            ),
        })
    elif dissolved_sulfide_mg_L == 0.0:
        residual.append({
            "mechanism": "sulfide_attack_on_passive_film",
            "status": "REPORTED_ZERO_AT_MEASUREMENT",
            "standing": sulfide_standing(0.0),
            "why": (
                "Reported dissolved sulfide is zero. This is a measurement state, "
                "not a concentration cutoff or a general material verdict."
            ),
        })
    elif dissolved_sulfide_mg_L is None and is_wet:
        residual.append({
            "mechanism": "sulfide_attack_on_passive_film",
            "status": "UNASSESSED",
            "standing": sulfide_standing(None),
            "why": (
                "Dissolved sulfide not supplied. Absent is not zero; the continuous "
                "passive-film effect is unassessed."
            ),
        })

    if wetness in {"condensing", "intermittent"}:
        residual.append({
            "mechanism": "biogenic_sulfuric_acid_on_concrete",
            "status": "UNASSESSED",
            "why": (
                "A condensing H2S headspace over concrete is the crown-corrosion "
                "case. Out of scope for a metals screen; assess separately."
            ),
        })

    # ---- material-specific flags -------------------------------------------
    cls = record.classification
    hardness_screen = {
        "applies": part == 2,
        "reported_hardness_HRC": hardness_HRC,
        "part_2_limit_HRC": HARDNESS_LIMIT_HRC if part == 2 else None,
        "exceeds_part_2_limit": (
            hardness_HRC > HARDNESS_LIMIT_HRC
            if part == 2 and hardness_HRC is not None
            else None
        ),
    }
    weld_screen = {
        "applies": part == 2,
        "reported_welded": welded,
        "wet_service": is_wet,
        "qualification_attention_required": (
            bool(welded) and is_wet if part == 2 else None
        ),
    }
    below_boundary_ssc_standing = (
        {
            "standing": (
                "ssc_not_excluded_below_part2_normal_applicability_boundary"
            ),
            "routine_part2_qualification_requirements_normally_apply": False,
            "ssc_excluded": False,
            "susceptibility_caveat_required": True,
            "reported_susceptibility_factors": {
                "hardness_exceeds_part2_limit": hardness_screen[
                    "exceeds_part_2_limit"
                ],
                "welded": welded,
                "in_situ_pH": in_situ_pH,
                # Section 7.1.1 names environmental severity but the readable
                # preview supplies no executable low-pH cutoff below Figure 1.
                "pH_decision_threshold_applied": False,
            },
            "source": {
                **_ISO15156_PART2_2020_PREVIEW,
                "section": "7.1.1",
            },
        }
        if part == 2 and is_wet and not above_threshold
        else None
    )

    part3_qualification_standing = (
        {
            "standing": (
                "part3_annex_a_limits_not_encoded_qualification_unresolved"
            ),
            "verdict": (
                "part3_alloy_specific_limits_or_qualification_required"
            ),
            "decision_effect": "no_material_clearance_or_disqualification",
            "wetness_gate": (
                "applicable_wet_service"
                if is_wet
                else "not_applicable_while_declared_dry"
            ),
            "annex_a_environmental_limit_tables_encoded": False,
            "governing_factors": [
                "H2S_partial_pressure",
                "temperature",
                "chloride_concentration",
                "elemental_sulfur",
                "in_situ_pH_and_other_environmental_factors",
            ],
            "qualification_routes": [
                "alloy_specific_Annex_A_environmental_limits",
                "laboratory_qualification_under_section_6_1",
                "documented_field_experience_under_section_6_1",
            ],
            "reported_environment": {
                "pH2S_bar": pH2S_bar,
                "temperature_C": temperature_C,
                "in_situ_pH": in_situ_pH,
                "wetness": wetness,
                "chloride_concentration": None,
                "elemental_sulfur": None,
            },
            "source": {
                **_ISO15156_PART3_2020_PREVIEW,
                "authors": ["International Organization for Standardization"],
            },
        }
        if part == 3
        else None
    )
    if (
        hardness_screen["exceeds_part_2_limit"] is True
        and is_wet
    ):
        warnings.append(
            f"Hardness {repr(float(hardness_HRC))} HRC exceeds the "
            f"{repr(float(HARDNESS_LIMIT_HRC))} HRC "
            f"ceiling commonly applied to carbon and low-alloy steel in sour "
            f"service. Hard weld heat-affected zones are the usual offender."
        )
    if welded and is_wet and part == 2:
        warnings.append(
            "Welded construction in sour service: the HAZ, not the parent metal, "
            "normally governs. Post-weld heat treatment and hardness survey are "
            "the qualification route."
        )
    if below_boundary_ssc_standing is not None:
        factor_notes: List[str] = []
        if hardness_screen["exceeds_part_2_limit"] is True:
            factor_notes.append("hardness above the reported Part-2 limit")
        if welded:
            factor_notes.append("welded construction")
        if in_situ_pH is not None:
            factor_notes.append(
                f"reported in-situ pH {repr(float(in_situ_pH))}"
            )
        factor_text = (
            "; reported factors: " + ", ".join(factor_notes)
            if factor_notes
            else "; no susceptibility inputs were supplied to reduce uncertainty"
        )
        warnings.append(
            "ISO 15156-2:2020 Section 7.1.1: routine Part-2 qualification "
            "requirements do not normally apply below 0.3 kPa pH2S, but highly "
            "susceptible steels can crack in less severe environments; SSC is not "
            f"excluded{factor_text}."
        )
    if cls in {"cast_iron"}:
        warnings.append(
            "Cast iron in wet H2S: graphitic corrosion is a distinct mechanism "
            "from SSC and is not screened here. This accessor has no content-"
            "verified graphitic-corrosion, residual-strength, or inspection-method "
            "claim."
        )

    warnings.append(
        f"ISO 15156-{part} is a LICENSED standard. The cited preview sections "
        f"were content-read at the stated access level, but this screening result "
        f"does not reproduce the licensed qualification tables. Confirm against "
        f"the licensed copy before using it to qualify material."
    )

    result = {
        "material": resolved,
        "material_classification": cls,
        # This is itself an accepted input field. Preserve the canonical decision
        # value rather than rounding it across the executable 0.003-bar boundary;
        # copying the returned value into a later call must not flip the result.
        "pH2S_bar": pH2S_bar,
        "pH2S_mbar": pH2S_mbar,
        "pH2S_kPa": pH2S_kPa,
        "pH2S_mbar_display": round(pH2S_mbar, 4),
        "pH2S_mbar_value_standing": "authoritative_unrounded",
        "pH2S_basis": basis,
        "pH2S_input_reconciliation": input_reconciliation,
        "wetness": wetness,
        "in_situ_pH": in_situ_pH,
        "iso15156_part": part,
        "iso15156_region": region,
        "iso15156_region_basis": (
            {
                "source": "ISO 15156-2:2020 Figure 1",
                "edition_continuity": {
                    "from_edition": "2015",
                    "to_edition": "2020",
                    "standing": (
                        "figure_geometry_rechecked_in_readable_2020_preview"
                    ),
                    "claim_limit": "no_broader_edition_equivalence_claimed",
                },
                "pressure_axis": "pH2S_kPa_log10",
                "region_1_2_diagonal": "pH = 4.5 + log10(pH2S_kPa)",
                "region_2_3_diagonal": "pH = 3.5 + log10(pH2S_kPa)",
                "horizontal_caps": {
                    "region_1_2_pH_max": 6.5,
                    "region_2_3_pH_min": 3.5,
                    "region_2_3_pH_max": 5.5,
                },
                "computed_boundaries_pH": (
                    {
                        "region_1_2": region_1_2_pH,
                        "region_2_3": region_2_3_pH,
                    }
                    if region_1_2_pH is not None
                    and region_2_3_pH is not None
                    else None
                ),
                "boundary_policy": "equality_assigned_to_more_severe_adjacent_region",
                "figure_extent_kPa": [
                    _FIGURE_1_MIN_PH2S_KPA,
                    _FIGURE_1_MAX_PH2S_KPA,
                ],
            }
            if part == 2
            else None
        ),
        "region_note": region_note,
        "cracking_verdict": cracking_verdict,
        "cracking_note": cracking_note,
        "damage_modes_screened": ["SSC"],
        "damage_modes_NOT_screened": {
            "HIC": (
                "Hydrogen-induced cracking is governed by steel cleanliness, "
                "elongated MnS inclusions, centreline segregation and product form "
                "- none of which is an input here. A pH2S/pH screen cannot address "
                "it; HIC-resistant plate is specified and tested (NACE TM0284), not "
                "screened."
            ),
            "SOHIC": (
                "Stress-oriented HIC needs the same cleanliness data plus applied "
                "and residual stress. Also not screened here."
            ),
        },
        "hardness_screen": hardness_screen,
        "weld_screen": weld_screen,
        "residual_mechanisms_not_cleared": residual,
        "corrosion_rate_mm_y": None,
        "why_no_rate": (
            "No open validated sour corrosion rate model exists for these "
            "conditions. This is a domain screen against a standard, not a model."
        ),
        "warnings": warnings,
        "provenance": {
            "model": "ISO15156_sour_service_screen",
            "version": "1.0",
            "validation_dataset": None,
            "confidence": "medium",
            "sources": [
                (
                    "NACE MR0175 / ISO 15156-2:2020 "
                    "(licensed; readable preview content-read)"
                    if part == 2
                    else "NACE MR0175 / ISO 15156-3:2020 Sections 5 and 6.1 "
                    "(licensed; readable preview content-read; Annex A tables "
                    "not encoded)"
                )
            ],
            "citation_records": (
                [{**_ISO15156_PART2_2020_PREVIEW}]
                if part == 2
                else [{**_ISO15156_PART3_2020_PREVIEW}]
            ),
            "assumptions": (
                [
                    "Figure 1 begins at 0.3 kPa pH2S; that SI value is the "
                    "normal Part-2 applicability boundary, not an SSC-"
                    "impossibility line. Its customary shorthand is often "
                    "rounded to 0.05 psi but is not used as the gate.",
                    f"Part-2 hardness ceiling {HARDNESS_LIMIT_HRC} HRC is a "
                    "flag, not a pass.",
                ]
                if part == 2
                else [
                    "Part 3 has no general 0.3 kPa clearance or qualification "
                    "boundary; alloy-specific limits or qualification govern.",
                    "The licensed Annex A environmental-limit tables for CRAs and "
                    "other alloys are not encoded, so no material clearance or "
                    "disqualification is made.",
                ]
            ),
            "warnings": warnings,
        },
    }

    if part == 2:
        result.update({
            "sour_threshold_mbar": (
                sour_service_basis.SOUR_SERVICE_PH2S_BAR * 1000.0
            ),
            "sour_threshold_mbar_display": round(
                sour_service_basis.SOUR_SERVICE_PH2S_BAR * 1000.0, 4
            ),
            "sour_threshold_mbar_value_standing": (
                "exact_primary_authoritative_display_field_rounded"
            ),
            "above_sour_threshold": above_threshold,
            "above_sour_threshold_meaning": (
                "at_or_above_part2_normal_applicability_boundary;"
                "not_a_physical_ssc_impossibility_boundary"
            ),
            "below_boundary_ssc_standing": below_boundary_ssc_standing,
        })
        if temperature_C is not None:
            result.update({
                "temperature_C": temperature_C,
                "temperature_standing": (
                    "reported_context_only_encoded_part2_screen_"
                    "temperature_independent"
                ),
            })
    else:
        result["part3_qualification_standing"] = part3_qualification_standing

    return result
