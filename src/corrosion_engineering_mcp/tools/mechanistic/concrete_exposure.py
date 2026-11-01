"""Tier 2 Tool: concrete exposure classification and biogenic acid screening.

WHY THIS IS WORTH BUILDING AT ALL
----------------------------------
Exposure classes are prescriptive standards, not models. They are cheap, they are
what a structural engineer will actually be handed, and this practice had
**zero** of them: a repo-wide search for XA/S-class/IS-456 exposure logic returns
nothing, while concrete tankage is specified as bare `material="RCC"` with no
recorded selection basis anywhere.

THE MISTAKE THIS TOOL EXISTS TO CATCH
--------------------------------------
EN 206 XA, ACI 318 S and IS 456 sulfate classes all describe the same thing:
**chemical attack on buried or submerged concrete by aggressive ground water or
soil**. Their severity axis is dissolved sulfate, with secondary limits on
magnesium, ammonium, aggressive CO2 and pH — and the most aggressive class, XA3,
bottoms out at **pH 4.0**.

Biogenic sulfuric acid attack in a sewer crown or a digester headspace drives the
concrete SURFACE below **pH 1**. It is a different mechanism (H2S transfers to the
gas phase, sulfur-oxidising bacteria colonise the wetted crown and oxidise it to
H2SO4 in situ), it acts on a surface the ground water never touches, and it is
three orders of magnitude beyond where the classification stops.

**There is no EN 206 exposure class for biogenic sulfuric acid attack.** So
writing "XA3" on a sewer crown or a digester headspace is not a conservative
specification — it is a specification for the wrong mechanism, and it reads as
diligence. This tool returns the exposure class AND refuses to let it stand as
the answer where the governing mechanism is biogenic.

ZONES IN ONE STRUCTURE ARE NOT ONE EXPOSURE
--------------------------------------------
A digester wall is submerged at the bottom, splash-zone at the working level and
headspace at the crown.  A splash zone is intermittently in direct liquid
contact, so it follows the water-chemistry classification path while retaining a
separate gas-side H2S/biogenic screen.  The two results are never merged.  The
zone taxonomy is supported by [JSKB15]: its abstract distinguishes gas-phase
coupons from partially submerged coupons and states that direct sewage contact
provided the latter with sufficient moisture for microbial processes.  A single
exposure class for "the tank" is therefore always wrong somewhere. `zone` is
required for that reason and is never inferred.

PRESCRIPTIVE MEANS COMPLETE
---------------------------
The EN 206 duty block is prescriptive only when the applicability basis is
inside the verified envelope and fully declared, all five governing groundwater
chemistry inputs are assessed, an EN 206 class exists, and the result remains
inside Table 2.  A class calculated from only the supplied parameters is retained
as a labelled partial screen so its edge and promotion arithmetic stays visible;
it is not an overall classification and emits no Boolean duty.  A displayed XA3
outside Table 2 is likewise not a valid specification: it carries only a
conditional advisory and the standard's specific-study requirement.

NO DETERIORATION RATE IS EMITTED
---------------------------------
No content-verified, executable deterioration model with a transferable
validation artifact is loaded. A sewer-headspace model could not in any event be
silently transferred to submerged RCC or a digester headspace.

SOURCES
-------
[EN206]  ILNAS EN 206:2013+A2:2021/prDNA-LU:2022, pp. 36-37, Section
         4.1(3) and Table 2. The official ILNAS publication was content-read for
         every interval edge, bounded ceiling, "up to saturation" row and the
         same-class promotion rule.  The verified applicability envelope is
         natural groundwater/surface water or qualifying effluent, 5-25 C
         inclusive, at static or sufficiently low flow. Screening only; the
         standard remains licensed.
[ACI]    ACI 318-19 Table 19.3.1.1, sulfate exposure classes S0-S3, as
         reproduced in official ACI-hosted training material at slide 69. The
         dissolved-sulfate edge semantics were content-read there.
[IS456]  IS 456:2000 Table 4, sulfate classes for Indian practice - carried
         because it is the code that actually governs our Indian projects.
         LICENSED, NOT machine-verified.
[JSKB15] Jiang, Sun, Keller & Bond, "Identification of controlling factors for
         the initiation of corrosion of fresh concrete sewers", Water Research
         80, 30 (2015), doi:10.1016/j.watres.2015.04.015. Its abstract reports
         a 4.5-year, 36-condition chamber study and says humidity affected
         gas-phase coupon initiation but not partially submerged coupons. It
         supplies no universal threshold or local-condensation state.
"""

from __future__ import annotations

import logging
import math
from numbers import Real
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils.sulfide_chemistry import H2S_GAS_COMPOSITION_MAX_PPMV, validate_h2s_gas_composition_ppmv

logger = logging.getLogger(__name__)

# [EN206] Official ILNAS publication, pp. 36-37, Section 4.1(3), Table 2:
# https://portail-qualite.public.lu/dam-assets/fr/normes-normalisation/
# achat-consultation-normes/normes-nationales/
# ilnas-en-206-2013a2-2021prdna-lu-2022.pdf
#
# Concentration tuples are (lower edge, upper edge, class). The XA1 lower edge
# is inclusive; shared edges remain in the lower class, so later lower edges are
# exclusive and every printed upper edge is inclusive. ``None`` is the table's
# "up to saturation" upper end, not a fabricated numeric ceiling.
_EN206_SOURCE_URL = (
    "https://portail-qualite.public.lu/dam-assets/fr/normes-normalisation/"
    "achat-consultation-normes/normes-nationales/"
    "ilnas-en-206-2013a2-2021prdna-lu-2022.pdf"
)
_EN206_SO4_WATER = (
    (200.0, 600.0, "XA1"),
    (600.0, 3000.0, "XA2"),
    (3000.0, 6000.0, "XA3"),
)
_EN206_SO4_LIMIT_XA3 = 6000.0
_EN206_PH_XA1_CEILING = 6.5
_EN206_PH_XA1_FLOOR = 5.5
_EN206_PH_XA2_FLOOR = 4.5
_EN206_PH_FLOOR = 4.0
_EN206_CO2_AGGRESSIVE = (
    (15.0, 40.0, "XA1"),
    (40.0, 100.0, "XA2"),
    (100.0, None, "XA3"),
)
_EN206_NH4 = (
    (15.0, 30.0, "XA1"),
    (30.0, 60.0, "XA2"),
    (60.0, 100.0, "XA3"),
)
_EN206_NH4_LIMIT_XA3 = 100.0
_EN206_MG = (
    (300.0, 1000.0, "XA1"),
    (1000.0, 3000.0, "XA2"),
    (3000.0, None, "XA3"),
)

_EN206_TEMPERATURE_MIN_C = 5.0
_EN206_TEMPERATURE_MAX_C = 25.0
VALID_WATER_ORIGINS = (
    "natural_groundwater",
    "natural_surface_water",
    "non_industrial_effluent",
    "industrial_effluent",
    "other",
)
_EN206_INSIDE_WATER_ORIGINS = (
    "natural_groundwater",
    "natural_surface_water",
    "non_industrial_effluent",
)
VALID_FLOW_REGIMES = ("static_or_low_flow", "high_flow")

# F2 option (b): keep this accessor's established mg/L chemistry classifier as
# a GROUNDWATER classifier and explicitly withhold any soil clearance.  Table 2
# has independent total-soil-sulfate (mg/kg) and Baumann-Gully acidity (ml/kg)
# rows, including sampling/extraction and permeability qualifications.  Adding
# only the two headline numbers would create another incomplete licensed-table
# implementation.  Buried results therefore retain their groundwater classes
# but always expose the two soil rows as unassessed behind a typed gate.
_UNASSESSED_SOIL_PARAMETERS = (
    "total_sulfate_mg_kg",
    "baumann_gully_ml_kg",
)

# [ACI] ACI 318-19 Table 19.3.1.1, dissolved sulfate in water, as reproduced
# in official ACI-hosted material at slide 69:
# https://www.concrete.org/portals/0/files/pdf/webinars/ws_S23_Hover.pdf
# S0 <150; S1 >=150 and <1500; S2 >=1500 and <=10000; S3 >10000 ppm.
_ACI_S1_FLOOR = 150.0
_ACI_S2_FLOOR = 1500.0
_ACI_S2_CEILING = 10000.0

# [IS456] Table 4, sulfate as SO4 in ground water, mg/L. The official BIS
# preview available during this remediation did not expose the numeric table
# body. Preserve the existing values and edge behavior rather than guessing.
_IS456 = ((300.0, "Class 2"), (1200.0, "Class 3"), (2500.0, "Class 4"), (5000.0, "Class 5"))

_CLASS_ORDER = {"X0": 0, "XA1": 1, "XA2": 2, "XA3": 3}

VALID_ZONES = ("submerged", "buried", "splash", "headspace", "external_atmospheric")

_BIOGENIC_HUMIDITY_CITATION = {
    "doi": "10.1016/j.watres.2015.04.015",
    "openalex_id": "https://openalex.org/W348609139",
    "title": (
        "Identification of controlling factors for the initiation of corrosion "
        "of fresh concrete sewers"
    ),
    "authors": ["Guangming Jiang", "Xiaoyan Sun", "Jürg Keller", "Philip L. Bond"],
    "section": "Abstract",
    "content_support": (
        "The abstract reports a 4.5-year purpose-built chamber study with 36 "
        "exposure conditions and says relative humidity affected corrosion "
        "initiation for gas-phase coupons but not partially submerged coupons; "
        "the latter were in direct sewage contact and had sufficient moisture "
        "for microbial processes."
    ),
    "content_limit": (
        "The abstract does not supply specific relative-humidity values, a "
        "universal activity threshold, local surface condensation from bulk RH, "
        "or transfer from a sewer chamber to a digester headspace."
    ),
}


class ZoneRequiredError(CorrosionPublicError, ValueError):
    """The exposure zone was not declared, and it cannot be inferred.

    One structure holds several zones with different governing mechanisms, so a
    single class for "the tank" is always wrong somewhere.
    """

    def __init__(self, given: Optional[str]) -> None:
        super().__init__(
            "CONCRETE_ZONE_REQUIRED",
            f"CONCRETE_ZONE_REQUIRED: zone {given!r} is not one of "
            f"{', '.join(VALID_ZONES)}. A digester wall is submerged at the "
            f"bottom, splash at the working level and headspace at the crown, and "
            f"those are three different mechanisms at three different rates. One "
            f"class for the whole tank is wrong somewhere by construction.",
            details={"reason_code": "CONCRETE_ZONE_REQUIRED", "accepted": list(VALID_ZONES)},
        )


def _class_from_unverified_ascending_lower_bounds(
    value: Optional[float], bands
) -> Optional[str]:
    if value is None:
        return None
    result = "X0"
    for threshold, name in bands:
        if value >= threshold:
            result = name
    return result


def _class_from_en206_concentration(value: Optional[float], bands) -> Optional[str]:
    """Apply Table 2's inclusive upper and shared-edge semantics exactly."""

    if value is None:
        return None
    if value < bands[0][0]:
        return "X0"
    for index, (lower, upper, name) in enumerate(bands):
        lower_matches = value >= lower if index == 0 else value > lower
        upper_matches = upper is None or value <= upper
        if lower_matches and upper_matches:
            return name
    # Bounded Table 2 rows stop at XA3. Retain the highest visible class only
    # alongside the mandatory beyond-classification gate added by the caller.
    return bands[-1][2]


def _class_from_descending_pH(pH: Optional[float]) -> Optional[str]:
    if pH is None:
        return None
    if pH > _EN206_PH_XA1_CEILING:
        return "X0"
    if pH >= _EN206_PH_XA1_FLOOR:
        return "XA1"
    if pH >= _EN206_PH_XA2_FLOOR:
        return "XA2"
    # As for bounded concentration rows, a value below the printed XA3 floor
    # remains visibly XA3 only in combination with a gate declaring it invalid.
    return "XA3"


def _class_from_aci_sulfate(sulfate_mg_L: Optional[float]) -> Optional[str]:
    """Apply the content-read ACI 318-19 water-sulfate interval edges."""

    if sulfate_mg_L is None:
        return None
    if sulfate_mg_L < _ACI_S1_FLOOR:
        return "S0"
    if sulfate_mg_L < _ACI_S2_FLOOR:
        return "S1"
    if sulfate_mg_L <= _ACI_S2_CEILING:
        return "S2"
    return "S3"


@enforce_tool_input_contract("corrosion_screen_concrete_exposure")
def screen_concrete_exposure(
    zone: str,
    sulfate_mg_L: Optional[float] = None,
    pH: Optional[float] = None,
    magnesium_mg_L: Optional[float] = None,
    ammonium_mg_L: Optional[float] = None,
    aggressive_CO2_mg_L: Optional[float] = None,
    chloride_mg_L: Optional[float] = None,
    temperature_C: Optional[float] = None,
    carbonate_aggregate: Optional[bool] = None,
    headspace_H2S_ppmv: Optional[float] = None,
    relative_humidity_pct: Optional[float] = None,
    cement_type: Optional[str] = None,
    lining: Optional[str] = None,
    water_origin: Optional[str] = None,
    flow_regime: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify a concrete exposure and screen for biogenic acid attack.

    Returns the EN 206 / ACI 318 / IS 456 classes, the governing parameter, and —
    where the zone is a headspace or splash zone carrying H2S — an explicit
    statement that the classification does NOT cover the governing mechanism.

    No deterioration rate is emitted.
    """
    validate_float_domain_inputs(locals())

    if zone not in VALID_ZONES:
        raise ZoneRequiredError(zone)

    validate_h2s_gas_composition_ppmv(
        headspace_H2S_ppmv,
        field="headspace_H2S_ppmv",
    )

    # The exported Python accessor is a public route too.  Mirror the MCP
    # schema so NaN cannot fall through every comparison and become a benign
    # X0 classification, or be echoed as if it were a reported measurement.
    numeric_bounds = {
        "sulfate_mg_L": (sulfate_mg_L, 0.0, None),
        "pH": (pH, 0.0, 14.0),
        "magnesium_mg_L": (magnesium_mg_L, 0.0, None),
        "ammonium_mg_L": (ammonium_mg_L, 0.0, None),
        "aggressive_CO2_mg_L": (aggressive_CO2_mg_L, 0.0, None),
        "chloride_mg_L": (chloride_mg_L, 0.0, None),
        "temperature_C": (temperature_C, -30.0, 120.0),
        "headspace_H2S_ppmv": (
            headspace_H2S_ppmv,
            0.0,
            H2S_GAS_COMPOSITION_MAX_PPMV,
        ),
        "relative_humidity_pct": (relative_humidity_pct, 0.0, 100.0),
    }
    for field_name, (value, lower, upper) in numeric_bounds.items():
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{field_name} must be a finite real number")
        if value < lower or (upper is not None and value > upper):
            upper_text = "unbounded" if upper is None else repr(float(upper))
            raise ValueError(
                f"{field_name} must be between {repr(float(lower))} and {upper_text}"
            )
    if carbonate_aggregate is not None and not isinstance(
        carbonate_aggregate, bool
    ):
        raise ValueError("carbonate_aggregate must be true, false, or omitted")
    if water_origin is not None and water_origin not in VALID_WATER_ORIGINS:
        raise ValueError(
            "water_origin must be one of "
            f"{', '.join(VALID_WATER_ORIGINS)}; got {water_origin!r}"
        )
    if flow_regime is not None and flow_regime not in VALID_FLOW_REGIMES:
        raise ValueError(
            "flow_regime must be one of "
            f"{', '.join(VALID_FLOW_REGIMES)}; got {flow_regime!r}"
        )
    for field_name, value, maximum in (
        ("cement_type", cement_type, 60),
        ("lining", lining, 80),
    ):
        if value is not None and (
            not isinstance(value, str) or len(value) > maximum
        ):
            raise ValueError(
                f"{field_name} must be a string of at most {maximum} characters"
            )

    warnings: List[str] = []
    gates: List[str] = []
    external_atmospheric = zone == "external_atmospheric"
    water_contact_zone = zone in {"submerged", "buried", "splash"}
    gas_phase_biogenic_zone = zone in {"headspace", "splash"}

    # EN 206 Section 4.1(3), Table 2 and the Luxembourg DNA complement limit
    # the tabulated classifier to natural groundwater/surface water or
    # qualifying effluent at 5-25 C inclusive and static/sufficiently low flow.
    # The arithmetic remains useful outside that envelope only as an explicitly
    # labelled extrapolated screen; it is never prescriptive there.
    outside_en206_reasons: List[str] = []
    unconfirmed_en206_fields: List[str] = []
    if water_contact_zone:
        if temperature_C is None:
            unconfirmed_en206_fields.append("temperature_C")
        elif temperature_C < _EN206_TEMPERATURE_MIN_C:
            outside_en206_reasons.append(
                f"temperature_C={repr(float(temperature_C))}_below_5.0_C"
            )
        elif temperature_C > _EN206_TEMPERATURE_MAX_C:
            outside_en206_reasons.append(
                f"temperature_C={repr(float(temperature_C))}_above_25.0_C"
            )

        if water_origin is None:
            unconfirmed_en206_fields.append("water_origin")
        elif water_origin not in _EN206_INSIDE_WATER_ORIGINS:
            outside_en206_reasons.append(
                f"water_origin={water_origin}_requires_specific_study"
            )

        if flow_regime is None:
            unconfirmed_en206_fields.append("flow_regime")
        elif flow_regime == "high_flow":
            outside_en206_reasons.append(
                "flow_regime=high_flow_requires_specific_study"
            )

    if water_contact_zone and outside_en206_reasons:
        gates.append("OUTSIDE_EN206_TABLE2_APPLICABILITY_ENVELOPE")
    if water_contact_zone and unconfirmed_en206_fields:
        gates.append("EN206_TABLE2_APPLICABILITY_BASIS_UNCONFIRMED")

    en206_applicability: Optional[Dict[str, Any]] = (
        {
            "standing": (
                "outside_verified_envelope"
                if outside_en206_reasons
                else (
                    "assumed_basis_not_confirmed"
                    if unconfirmed_en206_fields
                    else "verified_inside_envelope"
                )
            ),
            "classification_use": (
                "extrapolated_screen_not_prescriptive"
                if outside_en206_reasons
                else (
                    "conditional_on_unconfirmed_table2_basis"
                    if unconfirmed_en206_fields
                    else "prescriptive_classification_screen"
                )
            ),
            "declared": {
                "temperature_C": temperature_C,
                "water_origin": water_origin,
                "flow_regime": flow_regime,
            },
            "assumed_when_omitted": {
                **(
                    {"water_origin": "natural_groundwater"}
                    if water_origin is None
                    else {}
                ),
                **(
                    {"flow_regime": "static_or_low_flow"}
                    if flow_regime is None
                    else {}
                ),
            },
            "unconfirmed_fields": unconfirmed_en206_fields,
            "outside_reasons": outside_en206_reasons,
            "verified_envelope": {
                "temperature_C": {
                    "minimum": _EN206_TEMPERATURE_MIN_C,
                    "maximum": _EN206_TEMPERATURE_MAX_C,
                    "boundary_policy": "inclusive",
                },
                "water_origins": list(_EN206_INSIDE_WATER_ORIGINS),
                "flow_regimes": ["static_or_low_flow"],
            },
            "source_location": (
                "ILNAS EN 206:2013+A2:2021/prDNA-LU:2022 pp. 36-37, "
                "Section 4.1(3), Table 2 and DNA complement"
            ),
        }
        if water_contact_zone
        else None
    )

    # ---- classification ----------------------------------------------------
    contributions: Dict[str, Optional[str]] = {
        "sulfate": _class_from_en206_concentration(
            sulfate_mg_L, _EN206_SO4_WATER
        ),
        "pH": _class_from_descending_pH(pH),
        "aggressive_CO2": _class_from_en206_concentration(
            aggressive_CO2_mg_L, _EN206_CO2_AGGRESSIVE
        ),
        "ammonium": _class_from_en206_concentration(ammonium_mg_L, _EN206_NH4),
        "magnesium": _class_from_en206_concentration(magnesium_mg_L, _EN206_MG),
    }
    assessed = (
        {k: v for k, v in contributions.items() if v is not None}
        if water_contact_zone
        else {}
    )
    missing_classification_params = (
        [k for k, v in contributions.items() if v is None]
        if water_contact_zone
        else []
    )
    missing_params = list(missing_classification_params)
    if missing_classification_params:
        gates.append("EN206_CLASSIFICATION_INPUTS_UNASSESSED")
    if zone == "buried":
        missing_params.extend(_UNASSESSED_SOIL_PARAMETERS)
        gates.append("SOIL_CHEMICAL_ATTACK_NOT_ASSESSED")

    soil_chemical_attack: Optional[Dict[str, Any]] = (
        {
            "standing": "out_of_scope_groundwater_only_option_b",
            "decision_effect": "no_soil_clearance",
            "unassessed_parameters": list(_UNASSESSED_SOIL_PARAMETERS),
            "scope_reason": (
                "This accessor implements only the established groundwater mg/L "
                "classifier. EN 206 Table 2 soil sulfate and Baumann-Gully rows, "
                "their extraction method, clay-permeability allowance and sulfate-"
                "accumulation rule require a separate soil assessment."
            ),
            "source_location": (
                "ILNAS EN 206:2013+A2:2021/prDNA-LU:2022 Table 2, "
                "pp. 36 and 41-42, soil rows and footnotes a-c"
            ),
            "aci_soil_sulfate_standing": "not_assessed_independent_soil_row",
        }
        if zone == "buried"
        else None
    )

    if assessed:
        governing_param = max(assessed, key=lambda k: _CLASS_ORDER[assessed[k]])
        en206_class = assessed[governing_param]
    else:
        governing_param = None
        en206_class = None

    # EN 206 Section 4.1(3): "Where two or more aggressive characteristics
    # lead to the same class, the environment shall be classified into the next
    # higher class." The following clause allows a specific study to show that
    # escalation is unnecessary. This accessor accepts no study artifact, so it
    # must apply promotion unconditionally. Only XA1-XA3 are aggressive classes;
    # multiple X0 measurements cannot promote a benign environment.
    # A bounded-band exceedance is not an XA3 characteristic: it is already
    # outside Table 2. Keep its visible top class plus gate, but do not let that
    # display convention manufacture a same-class pair for Section 4.1(3).
    promotion_eligible_assessed = {
        parameter: parameter_class
        for parameter, parameter_class in assessed.items()
        if not (
            (parameter == "sulfate" and sulfate_mg_L > _EN206_SO4_LIMIT_XA3)
            or (parameter == "pH" and pH < _EN206_PH_FLOOR)
            or (
                parameter == "ammonium"
                and ammonium_mg_L > _EN206_NH4_LIMIT_XA3
            )
        )
    }
    same_class_groups = {
        class_name: [
            parameter
            for parameter, parameter_class in promotion_eligible_assessed.items()
            if parameter_class == class_name
        ]
        for class_name in ("XA1", "XA2", "XA3")
    }
    duplicated_classes = [
        class_name
        for class_name, parameters in same_class_groups.items()
        if len(parameters) >= 2
    ]
    promotion_source = (
        max(duplicated_classes, key=_CLASS_ORDER.__getitem__)
        if duplicated_classes
        else None
    )
    promotion_target = {"XA1": "XA2", "XA2": "XA3"}.get(promotion_source)
    promotion = {
        "triggered": promotion_source is not None,
        "source_class": promotion_source,
        "matching_parameters": (
            same_class_groups[promotion_source] if promotion_source else []
        ),
        "result_class": promotion_target,
        "standing": (
            "beyond_table_2_no_higher_class_defined"
            if promotion_source == "XA3"
            else (
                "promoted_to_next_higher_class"
                if promotion_source is not None
                else "not_triggered"
            )
        ),
        "application": "unconditional_no_specific_study_input",
        "escape_clause": (
            "A specific study may prove that promotion is unnecessary; this "
            "tool accepts no such study input, so promotion is unconditional."
        ),
    }
    if (
        promotion_target is not None
        and en206_class is not None
        and _CLASS_ORDER[promotion_target] > _CLASS_ORDER[en206_class]
    ):
        en206_class = promotion_target
        governing_param = "combined_same_class_characteristics"

    # EN 206 stops. Say so rather than clamping to the top class.
    beyond_classification: List[str] = []
    if (water_contact_zone and sulfate_mg_L is not None
            and sulfate_mg_L > _EN206_SO4_LIMIT_XA3):
        beyond_classification.append(
            f"Sulfate {repr(float(sulfate_mg_L))} mg/L exceeds the "
            f"{repr(float(_EN206_SO4_LIMIT_XA3))} "
            f"mg/L ceiling of XA3. EN 206 requires a special study above this; XA3 "
            f"is not a valid specification for it."
        )
    if water_contact_zone and pH is not None and pH < _EN206_PH_FLOOR:
        beyond_classification.append(
            f"pH {repr(float(pH))} is below the XA3 floor of "
            f"{repr(float(_EN206_PH_FLOOR))}. The "
            f"classification does not extend here."
        )
    if (
        water_contact_zone
        and ammonium_mg_L is not None
        and ammonium_mg_L > _EN206_NH4_LIMIT_XA3
    ):
        beyond_classification.append(
            f"Ammonium {repr(float(ammonium_mg_L))} mg/L exceeds the "
            f"{repr(float(_EN206_NH4_LIMIT_XA3))} mg/L ceiling of XA3. "
            "EN 206 Table 2 defines no class for this concentration; XA3 is "
            "not a valid specification for it."
        )
    if water_contact_zone and promotion_source == "XA3":
        governing_param = "combined_same_class_characteristics"
        beyond_classification.append(
            "Two or more aggressive characteristics individually lead to XA3. "
            "EN 206 Section 4.1(3) requires the next higher class unless a "
            "specific study proves promotion unnecessary, but Table 2 defines "
            "no class above XA3. This combination is beyond the table rather "
            "than an ungated XA3 specification."
        )
    if beyond_classification:
        gates.append("BEYOND_EN206_CLASSIFICATION")

    beyond_en206_classification = "BEYOND_EN206_CLASSIFICATION" in gates
    if en206_applicability is not None and not outside_en206_reasons:
        if unconfirmed_en206_fields:
            # Preserve the round-18 applicability-basis standing: it is already
            # explicitly non-prescriptive and dominates chemistry completeness.
            pass
        elif beyond_en206_classification:
            en206_applicability["classification_use"] = (
                "beyond_table2_screen_specific_study_required_not_prescriptive"
            )
        elif missing_classification_params:
            en206_applicability["classification_use"] = (
                "partial_screen_governing_inputs_unassessed_not_prescriptive"
            )

    aci_class = _class_from_aci_sulfate(sulfate_mg_L) if water_contact_zone else None
    is456_class = (
        _class_from_unverified_ascending_lower_bounds(sulfate_mg_L, _IS456)
        or None
        if water_contact_zone
        else None
    )
    if is456_class == "X0":
        is456_class = "Class 1"

    # ---- the central check: is the classification even the right question? --
    h2s_present = headspace_H2S_ppmv is not None and headspace_H2S_ppmv > 0.0
    if zone == "headspace":
        gates.append("WATER_SOIL_CLASSES_NOT_APPLICABLE")
    if gas_phase_biogenic_zone and headspace_H2S_ppmv is None:
        gates.append("HEADSPACE_H2S_NOT_SUPPLIED")

    if external_atmospheric:
        gates.append("WATER_SOIL_CLASSES_NOT_APPLICABLE")
        classification_standing = "not_applicable_external_atmospheric"
        classification_note = (
            "The declared zone is external_atmospheric. EN 206 XA, ACI 318 S "
            "and IS 456 sulfate classes describe attack by contacting water or "
            "soil, so this accessor suppresses them. Atmospheric carbonation, "
            "freeze/thaw and airborne-chloride exposure are not classified by "
            "this tool."
        )
    elif zone == "headspace" and headspace_H2S_ppmv is None:
        classification_standing = "incomplete"
        classification_note = (
            "Zone is headspace but headspace H2S was not supplied. For a gas-phase "
            f"concrete surface that is the measurement that decides whether the "
            f"exposure class is relevant at all. Absent is not zero."
        )
    elif zone == "headspace" and h2s_present:
        # The read Jiang abstract says RH affected gas-phase-coupon initiation,
        # but supplies no exact RH values or universal onset threshold. Bulk RH
        # also does not tell us whether a cooler concrete surface is condensing.
        if relative_humidity_pct is None:
            gates.append("HEADSPACE_HUMIDITY_NOT_SUPPLIED")
        gates.append("BIOGENIC_SURFACE_MOISTURE_UNRESOLVED")
        gates.append("EXPOSURE_CLASS_DOES_NOT_COVER_POSSIBLE_MECHANISM")
        classification_standing = "incomplete_biogenic_moisture_state"
        rh_text = (
            f"{relative_humidity_pct:g}%"
            if relative_humidity_pct is not None
            else "not supplied"
        )
        classification_note = (
            f"Zone is {zone} with {headspace_H2S_ppmv:g} ppmv H2S. Reported bulk "
            f"RH is {rh_text}. "
            "Jiang et al.'s abstract says humidity affected gas-phase coupon "
            "corrosion initiation but supplies no exact RH values or universal "
            "activity cutoff; bulk RH does not establish local surface "
            "condensation. Biogenic sulfuric-acid activity is "
            "therefore UNKNOWN pending direct surface-wetness/condensation "
            "evidence. EN 206 XA, ACI S and IS 456 classes remain inapplicable "
            "to this gas-phase surface whether the mechanism is active or not."
        )
    elif zone == "headspace":
        classification_standing = "not_applicable_gas_phase_surface"
        classification_note = (
            f"Zone is {zone} and H2S was explicitly declared absent. Biogenic "
            f"sulfuric-acid attack is inactive on that declaration, but EN 206 "
            f"XA, ACI S and IS 456 sulfate classes still describe contacting "
            f"water or soil and are not exposed for this gas-phase surface."
        )
    else:
        # Final caller-facing standing is assigned only after the complete
        # prescriptive predicate has been evaluated below.
        classification_standing = "pending_final_water_contact_predicate"
        if zone == "splash":
            classification_note = (
                "Zone is splash: intermittent direct liquid contact makes EN 206 "
                "XA, ACI 318 S and IS 456 classes applicable to the reported liquid "
                "chemistry. The gas-side H2S/biogenic screen is reported separately "
                "and does not replace or merge with those classes. Jiang et al.'s "
                "abstract distinguishes gas-phase from partially submerged coupons "
                "and reports sufficient microbial moisture from direct sewage contact."
            )
        elif zone == "buried":
            classification_note = (
                "Zone is buried. The returned EN 206 XA, ACI 318 S and IS 456 "
                "classes describe only the reported groundwater chemistry. Soil "
                "chemical attack is a separate path and is not assessed by this "
                "accessor; the groundwater classes are not a soil clearance."
            )
        else:
            classification_note = (
                f"Zone is {zone}, so chemical attack from the contacting water "
                f"is the mechanism the classification was written for."
            )

    if water_contact_zone:
        if outside_en206_reasons:
            classification_standing = (
                "outside_en206_table2_applicability_envelope_extrapolated"
            )
            classification_note += (
                " The declared service is outside the content-verified EN 206 "
                "Table 2 applicability envelope. Numeric classes are retained only "
                "as an extrapolated screen; a specific study and protection review "
                "are needed before any prescriptive classification or duty can be "
                "asserted."
            )
        elif zone == "buried":
            classification_standing = (
                "groundwater_classification_only_soil_attack_unassessed"
            )
        if unconfirmed_en206_fields:
            classification_note += (
                " Table 2 applicability is conditional because "
                f"{', '.join(unconfirmed_en206_fields)} was not declared. The "
                "payload exposes origin/flow assumptions when those fields are "
                "omitted; no temperature value is assumed."
            )
        if missing_classification_params:
            classification_note += (
                " Governing chemistry inputs remain unassessed: "
                f"{', '.join(missing_classification_params)}. Any displayed EN "
                "206 class is only a partial screen from supplied parameters, not "
                "an overall classification, and it creates no prescriptive duty."
            )
        if beyond_en206_classification:
            classification_note += (
                " The reported environment is beyond EN 206 Table 2. Any displayed "
                "XA3 is not a valid specification; a specific study must establish "
                "the classification and protection basis."
            )

    # ---- biogenic acid block ------------------------------------------------
    biogenic: Optional[Dict[str, Any]] = None
    if gas_phase_biogenic_zone:
        if headspace_H2S_ppmv is None:
            mechanism_active = None
        elif not h2s_present:
            mechanism_active = False
        else:
            mechanism_active = None
        if zone == "splash":
            surface_moisture_standing = (
                "present_during_declared_intermittent_liquid_contact"
            )
            humidity_decision_effect = (
                "no_humidity_gate_surface_wet_by_zone_activity_still_unassessed"
            )
        else:
            surface_moisture_standing = (
                "unknown_local_condensation_not_established_by_bulk_RH"
            )
            humidity_decision_effect = "mechanism_activity_remains_unknown"
        biogenic = {
            "mechanism_active": mechanism_active,
            "headspace_H2S_ppmv": headspace_H2S_ppmv,
            "relative_humidity_pct": relative_humidity_pct,
            "surface_moisture_standing": surface_moisture_standing,
            "screen_merged_with_water_classes": False,
            "humidity_evidence": {
                "interpretation_standing": "reported_not_thresholded",
                "decision_threshold_applied": False,
                "decision_effect": humidity_decision_effect,
                "study_scope": "fresh_sewer_concrete_corrosion_initiation",
                "finding": "humidity_affected_gas_phase_initiation",
                "not_established": [
                    "universal_activity_threshold",
                    "local_surface_condensation_from_bulk_RH",
                    "transfer_from_sewer_to_digester_headspace",
                ],
                **_BIOGENIC_HUMIDITY_CITATION,
            },
            "deterioration_rate_mm_y": None,
            "why_no_rate": (
                "No content-verified executable deterioration model with a "
                "transferable validation artifact is loaded. A sewer-headspace "
                "model may not be transferred to a digester or submerged RCC."
            ),
            "reported_configuration": {
                "cement_type": cement_type,
                "lining": lining,
            },
            "material_selection_standing": (
                "not_assessed_no_content_verified_source"
            ),
            "material_recommendations": [],
        }

    # ---- thaumasite ----------------------------------------------------------
    # The prior implementation made a three-way yes/no decision using <15 C,
    # sulfate >200 mg/L and carbonate aggregate.  No content-verified source in
    # this server licensed those exact gates, so preserve the reported inputs
    # without allowing the unsourced boundary to decide mechanism activity.
    tsa: Optional[Dict[str, Any]] = None
    if zone in {"submerged", "buried"}:
        tsa = {
            "thaumasite_form_possible": None,
            "interpretation_standing": (
                "reported_not_decisive_no_content_verified_source"
            ),
            "decision_threshold_applied": False,
            "decision_effect": "no_mechanism_or_material_verdict",
            "reported_inputs": {
                "temperature_C": temperature_C,
                "sulfate_mg_L": sulfate_mg_L,
                "carbonate_aggregate": carbonate_aggregate,
            },
            "note": (
                "Temperature, dissolved sulfate and carbonate-aggregate state are "
                "reported for a later thaumasite assessment. No content-verified "
                "source loaded by this tool establishes the former <15 C, >200 "
                "mg/L and carbonate Boolean gate, so these inputs do not produce "
                "a mechanism verdict."
            ),
            "missing": [
                p for p, v in (
                    ("temperature_C", temperature_C),
                    ("sulfate_mg_L", sulfate_mg_L),
                    ("carbonate_aggregate", carbonate_aggregate),
                )
                if v is None
            ],
        }

    reinforcement_chloride_context = (
        None
        if chloride_mg_L is None
        else {
            "reported_chloride_mg_L": chloride_mg_L,
            "interpretation_standing": "reported_not_thresholded",
            "decision_threshold_applied": False,
            "decision_effect": "no_reinforcement_corrosion_classification",
        }
    )

    if not water_contact_zone:
        en206_class_value_standing = (
            "not_applicable_no_water_contact_classification"
        )
        en206_classification_completeness: Optional[Dict[str, Any]] = None
    else:
        # Determine the final public state exactly once.  Every downstream
        # contract consumes this state; no second predicate may independently
        # promote a typed screen into a prescriptive result.
        if outside_en206_reasons:
            classification_standing = (
                "outside_en206_table2_applicability_envelope_extrapolated"
            )
        elif zone == "buried":
            classification_standing = (
                "groundwater_classification_only_soil_attack_unassessed"
            )
        elif beyond_en206_classification:
            classification_standing = (
                "beyond_table2_screen_not_a_valid_specification"
            )
        elif missing_classification_params:
            classification_standing = (
                "partial_screen_not_an_overall_classification"
            )
        elif unconfirmed_en206_fields:
            classification_standing = (
                "basis_unconfirmed_screen_not_prescriptive"
            )
        elif en206_class is not None:
            classification_standing = "applicable"
        else:
            # Fail closed if a future state is added without an explicit branch.
            classification_standing = "table2_screen_not_prescriptive"

        if beyond_en206_classification:
            en206_class_value_standing = (
                "beyond_table2_display_not_valid_specification"
            )
        elif missing_classification_params:
            en206_class_value_standing = (
                "not_established_governing_inputs_unassessed"
                if en206_class is None
                else (
                    "partial_screen_from_supplied_parameters_"
                    "not_overall_classification"
                )
            )
        elif (
            outside_en206_reasons
            or unconfirmed_en206_fields
            or classification_standing
            == "groundwater_classification_only_soil_attack_unassessed"
        ):
            en206_class_value_standing = (
                "complete_chemistry_screen_applicability_basis_not_prescriptive"
            )
        elif classification_standing == "applicable":
            en206_class_value_standing = "prescriptive_classification"
        else:
            en206_class_value_standing = "table2_screen_not_prescriptive"

    # This is the sole prescriptive switch for companions, duties, warnings and
    # provenance.  A future standing is non-prescriptive until it is explicitly
    # made the literal ``applicable`` state above.
    classification_is_prescriptive = classification_standing == "applicable"

    if water_contact_zone:
        en206_classification_completeness = {
            "all_governing_chemistry_assessed": (
                not missing_classification_params
            ),
            "unassessed_governing_inputs": list(
                missing_classification_params
            ),
            "prescriptive_classification_established": (
                classification_is_prescriptive
            ),
        }

        en206_class_value_contract = {"en206_class": en206_class}
        if not classification_is_prescriptive:
            en206_class_value_contract.update({
                "en206_class_value_standing": en206_class_value_standing,
                "en206_classification_completeness": (
                    en206_classification_completeness
                ),
            })

    if not water_contact_zone:
        en206_dna_requirements: Optional[Dict[str, Any]] = None
    elif classification_is_prescriptive:
        # Preserve the established in-envelope payload exactly. The conditional
        # shape below must not leak into a genuinely prescriptive classification.
        en206_dna_requirements = {
            "standing": "documented_requirements_not_a_protection_system_design",
            "source_location": (
                "ILNAS EN 206:2013+A2:2021/prDNA-LU:2022 DNA Table 1 "
                "pp. 40-41 and Table 2 footnotes d-e pp. 41-42"
            ),
            "xa3_surface_protection": {
                "triggered_by_reported_class": en206_class == "XA3",
                "required_unless_specific_study": en206_class == "XA3",
                "system_standard": "EN 1504",
                "decision_effect": (
                    "requirement_reported_no_system_selected"
                    if en206_class == "XA3"
                    else "not_triggered_by_reported_class"
                ),
                "requirement": (
                    "For XA3, a surface-protection system providing sufficient "
                    "chemical-attack protection over the design life is required "
                    "unless a specific study demonstrates it is unnecessary."
                ),
            },
            "specification_reporting": {
                "water_sulfate": {
                    "required": (
                        sulfate_mg_L is not None and sulfate_mg_L > 600.0
                    ),
                    "reported_value_mg_L": sulfate_mg_L,
                    "threshold_mg_L": 600.0,
                    "trigger_semantics": "strictly_greater_than",
                    "footnote": "d",
                },
                "soil_sulfate": {
                    "required": None,
                    "reported_value_mg_kg": None,
                    "threshold_mg_kg": 3000.0,
                    "trigger_semantics": "strictly_greater_than",
                    "footnote": "d",
                    "standing": "unassessed_soil_input_not_supported",
                },
                "pH": {
                    "required": pH is not None and pH < 5.5,
                    "reported_value": pH,
                    "threshold": 5.5,
                    "trigger_semantics": "strictly_less_than",
                    "footnote": "e",
                },
            },
            "decision_limit": (
                "Requirements are surfaced for specification review; this tool "
                "does not select or qualify a protection system."
            ),
        }
    else:
        # A partial XA3 can only remain XA3 or move beyond Table 2 when the
        # missing inputs arrive, so retaining a positive advisory is safe. A
        # partial lower class cannot support a negative XA3 advisory because an
        # unassessed characteristic may raise it.
        if en206_class is None:
            xa3_would_apply: Optional[bool] = None
        elif missing_classification_params and en206_class != "XA3":
            xa3_would_apply = None
        else:
            xa3_would_apply = en206_class == "XA3"

        water_sulfate_would_apply = (
            None if sulfate_mg_L is None else sulfate_mg_L > 600.0
        )
        ph_would_apply = None if pH is None else pH < 5.5
        conditional_basis_gates = [
            *(
                ["soil_chemical_attack_not_assessed"]
                if classification_standing
                == "groundwater_classification_only_soil_attack_unassessed"
                else []
            ),
            *(
                ["outside_en206_table2_applicability_envelope_extrapolated"]
                if outside_en206_reasons
                else []
            ),
            *(
                ["en206_table2_applicability_basis_unconfirmed"]
                if unconfirmed_en206_fields
                else []
            ),
            *(
                ["en206_classification_inputs_unassessed"]
                if missing_classification_params
                else []
            ),
            *(
                ["en206_class_not_established"]
                if en206_class is None
                else []
            ),
            *(
                ["beyond_en206_classification_specific_study_required"]
                if beyond_en206_classification
                else []
            ),
        ]

        if beyond_en206_classification:
            protection_decision_effect = (
                "conditional_advisory_specific_study_required_"
                "no_prescriptive_obligation_asserted"
            )
            protection_requirement = (
                "The displayed XA3 is not a valid specification for this "
                "beyond-Table-2 environment. The DNA's EN 1504 XA3 surface-"
                "protection duty is retained only as a design-review advisory; "
                "EN 206 requires a specific study before a classification, "
                "protection duty or sufficiency claim can be prescriptive."
            )
            decision_limit = (
                "The DNA duties are advisory conditionals only. This environment "
                "is beyond Table 2, so the displayed class is not a valid "
                "specification and a specific study must establish the design "
                "basis before any duty is asserted."
            )
            specific_study_requirement: Optional[Dict[str, Any]] = {
                "required": True,
                "standing": "required_for_beyond_table2_environment",
                "source_location": (
                    "ILNAS EN 206:2013+A2:2021/prDNA-LU:2022 pp. 36-37, "
                    "Section 4.1(3) and Table 2 applicability text"
                ),
                "decision_effect": (
                    "study_required_before_classification_or_duty_can_be_"
                    "prescriptive"
                ),
                "requirement": (
                    "A specific study is required because the reported "
                    "environment lies outside the classification Table 2 "
                    "defines."
                ),
            }
        elif missing_classification_params:
            protection_decision_effect = (
                "conditional_advisory_no_prescriptive_obligation_asserted"
            )
            protection_requirement = (
                "The supplied parameters show a partial XA3 screen, so the DNA "
                "EN 1504 duty is retained only as an advisory pending all "
                "governing chemistry inputs. No overall classification or duty "
                "is asserted from incomplete evidence."
                if xa3_would_apply is True
                else (
                    "The supplied parameters do not establish an overall XA3 "
                    "classification. Unassessed governing chemistry can raise "
                    "the class or trigger promotion, so no negative or positive "
                    "surface-protection duty is inferred."
                )
            )
            decision_limit = (
                "The DNA duties are advisory conditionals only. All governing "
                "chemistry inputs must be assessed before the displayed partial "
                "screen can become an overall prescriptive classification."
            )
            specific_study_requirement = None
        else:
            protection_decision_effect = (
                "conditional_advisory_no_prescriptive_obligation_asserted"
            )
            protection_requirement = (
                "For a prescriptive XA3 classification, the DNA duty would call "
                "for a surface-protection system providing sufficient chemical-"
                "attack protection over the design life unless a specific study "
                "demonstrated it unnecessary. The returned XA3 is only an "
                "extrapolated or basis-unconfirmed screen, so that duty is not "
                "asserted by this result."
                if xa3_would_apply is True
                else (
                    "The conditional screen does not reach the XA3 trigger. No "
                    "prescriptive duty is inferred from a non-prescriptive "
                    "Table 2 basis."
                )
            )
            decision_limit = (
                "The DNA duties are advisory conditionals only. They attach "
                "only after the service has a prescriptive Table 2 "
                "classification; this extrapolated or basis-unconfirmed screen "
                "establishes none."
            )
            specific_study_requirement = None

        en206_dna_requirements = {
            "standing": "conditional_advisory_table2_basis_not_prescriptive",
            "basis_gate": conditional_basis_gates[0],
            "basis_gates": conditional_basis_gates,
            "source_location": (
                "ILNAS EN 206:2013+A2:2021/prDNA-LU:2022 DNA Table 1 "
                "pp. 40-41 and Table 2 footnotes d-e pp. 41-42"
            ),
            "xa3_surface_protection": {
                "triggered_by_reported_class": None,
                "required_unless_specific_study": None,
                "would_apply_if_table2_class_were_prescriptive": xa3_would_apply,
                "system_standard": "EN 1504",
                "decision_effect": protection_decision_effect,
                "requirement": protection_requirement,
            },
            "specification_reporting": {
                "water_sulfate": {
                    "required": None,
                    "would_apply_if_table2_class_were_prescriptive": (
                        water_sulfate_would_apply
                    ),
                    "reported_value_mg_L": sulfate_mg_L,
                    "threshold_mg_L": 600.0,
                    "trigger_semantics": "strictly_greater_than",
                    "footnote": "d",
                },
                "soil_sulfate": {
                    "required": None,
                    "would_apply_if_table2_class_were_prescriptive": None,
                    "reported_value_mg_kg": None,
                    "threshold_mg_kg": 3000.0,
                    "trigger_semantics": "strictly_greater_than",
                    "footnote": "d",
                    "standing": "unassessed_soil_input_not_supported",
                },
                "pH": {
                    "required": None,
                    "would_apply_if_table2_class_were_prescriptive": ph_would_apply,
                    "reported_value": pH,
                    "threshold": 5.5,
                    "trigger_semantics": "strictly_less_than",
                    "footnote": "e",
                },
            },
            "decision_limit": decision_limit,
        }
        if specific_study_requirement is not None:
            en206_dna_requirements["specific_study_requirement"] = (
                specific_study_requirement
            )

    if outside_en206_reasons:
        warnings.append(
            "OUTSIDE_EN206_TABLE2_APPLICABILITY_ENVELOPE: "
            + "; ".join(outside_en206_reasons)
            + ". Returned classes are extrapolated arithmetic, not a prescriptive "
            "classification."
        )
    if unconfirmed_en206_fields:
        warnings.append(
            "EN206_TABLE2_APPLICABILITY_BASIS_UNCONFIRMED: "
            f"{', '.join(unconfirmed_en206_fields)} not declared. The arithmetic "
            "shows any origin/flow assumption explicitly, assumes no temperature, "
            "and does not establish that the service is inside Table 2."
        )
    if beyond_en206_classification:
        warnings.append(
            "BEYOND_EN206_CLASSIFICATION: the displayed top class is not a valid "
            "specification for this environment. EN 206 requires a specific study "
            "before a classification, protection duty or sufficiency claim can be "
            "prescriptive."
        )
    if soil_chemical_attack is not None:
        warnings.append(
            "SOIL_CHEMICAL_ATTACK_NOT_ASSESSED: buried-zone classes are "
            "groundwater-only. Total soil sulfate, Baumann-Gully acidity and "
            "their Table 2 qualifications require a separate assessment."
        )
    if (
        en206_dna_requirements is not None
        and classification_is_prescriptive
        and en206_dna_requirements["xa3_surface_protection"][
            "required_unless_specific_study"
        ]
    ):
        warnings.append(
            "DNA XA3 SURFACE PROTECTION: an EN 1504-conforming protection system "
            "is required unless a specific study demonstrates it is unnecessary."
        )
    elif (
        en206_dna_requirements is not None
        and not classification_is_prescriptive
        and en206_dna_requirements["xa3_surface_protection"][
            "would_apply_if_table2_class_were_prescriptive"
        ]
    ):
        if beyond_en206_classification:
            warnings.append(
                "DNA XA3 CONDITIONAL ADVISORY: the EN 1504 XA3 duty is retained "
                "only as design-review context. The displayed XA3 is not a valid "
                "specification beyond Table 2; a specific study is required and "
                "this result asserts no obligation or protection sufficiency."
            )
        elif missing_classification_params:
            warnings.append(
                "DNA XA3 CONDITIONAL ADVISORY: supplied parameters produce a "
                "partial XA3 screen, but unassessed governing chemistry prevents "
                "an overall prescriptive classification; this result asserts no "
                "obligation."
            )
        else:
            warnings.append(
                "DNA XA3 CONDITIONAL ADVISORY: an EN 1504 surface-protection duty "
                "would apply only if a prescriptive Table 2 classification is "
                "established; the current extrapolated or basis-unconfirmed XA3 "
                "screen asserts no obligation."
            )
    if en206_dna_requirements is not None and classification_is_prescriptive:
        reporting = en206_dna_requirements["specification_reporting"]
        reporting_duties = [
            name
            for name in ("water_sulfate", "pH")
            if reporting[name]["required"] is True
        ]
        if reporting_duties:
            warnings.append(
                "DNA TABLE 2 SPECIFICATION REPORTING: "
                f"{', '.join(reporting_duties)} must be stated in the concrete "
                "specification under footnotes d/e."
            )
    elif en206_dna_requirements is not None:
        reporting = en206_dna_requirements["specification_reporting"]
        conditional_reporting_duties = [
            name
            for name in ("water_sulfate", "pH")
            if reporting[name][
                "would_apply_if_table2_class_were_prescriptive"
            ] is True
        ]
        if conditional_reporting_duties:
            warnings.append(
                "DNA TABLE 2 CONDITIONAL ADVISORY: "
                f"{', '.join(conditional_reporting_duties)} would carry footnote "
                "d/e reporting duties only if a prescriptive Table 2 "
                "classification is established; this result asserts none."
            )

    if missing_classification_params:
        warnings.append(
            "EN206_CLASSIFICATION_INPUTS_UNASSESSED: Not assessed for "
            f"{', '.join(missing_classification_params)}. Each is an independent "
            f"route to a higher class in EN 206. The most severe individual "
            f"parameter governs, and two or more aggressive characteristics in "
            f"the same class trigger the Section 4.1(3) promotion rule, so an "
            f"unassessed one can only raise the result or trigger its gate. Any "
            f"displayed class is a partial screen and no prescriptive duty is "
            f"asserted."
        )

    if not external_atmospheric:
        warnings.append(
            "EN 206, ACI 318 and IS 456 are LICENSED standards. EN 206 Table 2 "
            "and Section 4.1(3) were content-read in the official ILNAS "
            "publication, and the ACI 318-19 sulfate edges were content-read in "
            "official ACI-hosted material. IS 456 Table 4 remains not "
            "machine-verified. This screening does not substitute for the codes."
        )

    if external_atmospheric:
        en206_confidence = "not_applicable_external_atmospheric"
    elif not water_contact_zone:
        en206_confidence = "water_soil_classification_not_applicable"
    elif outside_en206_reasons:
        en206_confidence = "extrapolated_table2_screen_not_prescriptive"
    elif unconfirmed_en206_fields:
        en206_confidence = (
            "conditional_groundwater_classification_soil_unassessed_"
            "and_table2_basis_unconfirmed_not_prescriptive"
            if zone == "buried"
            else "table2_applicability_basis_unconfirmed_not_prescriptive"
        )
    elif beyond_en206_classification:
        en206_confidence = (
            "beyond_table2_specific_study_required_not_prescriptive"
        )
    elif missing_classification_params:
        en206_confidence = (
            "classification_inputs_unassessed_partial_screen_not_prescriptive"
        )
    elif zone == "buried":
        en206_confidence = (
            "groundwater_classification_soil_unassessed_not_prescriptive"
        )
    elif classification_is_prescriptive:
        en206_confidence = "prescriptive_classification"
    else:
        en206_confidence = "table2_screen_not_prescriptive"

    result = {
        "zone": zone,
        "reported_inputs": {
            "sulfate_mg_L": sulfate_mg_L,
            "pH": pH,
            "magnesium_mg_L": magnesium_mg_L,
            "ammonium_mg_L": ammonium_mg_L,
            "aggressive_CO2_mg_L": aggressive_CO2_mg_L,
            "chloride_mg_L": chloride_mg_L,
            "temperature_C": temperature_C,
            "carbonate_aggregate": carbonate_aggregate,
            "headspace_H2S_ppmv": headspace_H2S_ppmv,
            "relative_humidity_pct": relative_humidity_pct,
            "cement_type": cement_type,
            "lining": lining,
            "water_origin": water_origin,
            "flow_regime": flow_regime,
        },
        "classification_standing": classification_standing,
        "classification_note": classification_note,
        "en206_applicability": en206_applicability,
        "zone_exposure_paths": {
            "water_contact_classification": {
                "applicable": water_contact_zone,
                "basis": (
                    "declared_intermittent_direct_liquid_contact"
                    if zone == "splash"
                    else (
                        "groundwater_chemistry_only_soil_attack_out_of_scope"
                        if zone == "buried"
                        else (
                            "declared_continuous_water_contact"
                            if water_contact_zone
                            else "no_declared_water_or_soil_contact"
                        )
                    )
                ),
            },
            "gas_phase_biogenic_screen": {
                "applicable": gas_phase_biogenic_zone,
                "basis": (
                    "declared_gas_exposure_with_intermittent_liquid_wetting"
                    if zone == "splash"
                    else (
                        "declared_gas_phase_surface"
                        if zone == "headspace"
                        else "not_a_gas_phase_biogenic_zone"
                    )
                ),
                "merged_with_water_classes": False,
            },
        },
        "beyond_classification": beyond_classification,
        "biogenic_sulfuric_acid": biogenic,
        "soil_chemical_attack": soil_chemical_attack,
        "thaumasite": tsa,
        "reinforcement_chloride_context": reinforcement_chloride_context,
        "en206_dna_requirements": en206_dna_requirements,
        "deterioration_rate_mm_y": None,
        "unassessed_parameters": missing_params,
        "gates": gates,
        "warnings": warnings,
        "provenance": {
            "model": "concrete_exposure_class_screen",
            "version": "1.0",
            "validation_dataset": None,
            "confidence": en206_confidence,
            "sources": [
                "ILNAS EN 206:2013+A2:2021/prDNA-LU:2022 pp. 36-37, "
                f"Section 4.1(3) and Table 2 (content-read; {_EN206_SOURCE_URL})",
                "ACI 318-19 Table 19.3.1.1 at official ACI-hosted slide 69 "
                "(content-read; https://www.concrete.org/portals/0/files/pdf/"
                "webinars/ws_S23_Hover.pdf)",
                "IS 456:2000 Table 4 (licensed; not machine-verified)",
                "Jiang, Sun, Keller & Bond humidity evidence is structured in "
                "citation_records with its read location and content limit",
            ],
            "citation_records": [
                {
                    **_BIOGENIC_HUMIDITY_CITATION,
                    "authors": list(_BIOGENIC_HUMIDITY_CITATION["authors"]),
                }
            ],
            "citation_metadata_source": "OpenAlex",
            "citation_verification_scope": (
                "official_ilnas_sections_4_1_3_and_table_2;"
                "official_ilnas_dna_table1_and_table2_footnotes_d_e;"
                "official_ilnas_table2_applicability_envelope;"
                "official_aci_hosted_table_19_3_1_1;"
                "jiang_doi_author_title_and_read_abstract_content;"
                "is456_numeric_table_not_content_verified"
            ),
            "assumptions": [
                "The most severe individual characteristic governs; an average is "
                "never used.",
                "Where two or more aggressive characteristics lead to the same "
                "class, the environment is classified into the next higher class.",
                "The specific-study escape is not accepted as an input, so "
                "same-class promotion is unconditional.",
                "Table 2 arithmetic is prescriptive only for natural groundwater, "
                "natural surface water or qualifying effluent at 5-25 C inclusive "
                "and static or sufficiently low flow.",
                *(
                    [
                        "A class derived from only supplied parameters is a labelled "
                        "partial screen, not an overall classification; its duty "
                        "Booleans are null."
                    ]
                    if missing_classification_params
                    else []
                ),
                *(
                    [
                        "A displayed class beyond Table 2 is not a valid "
                        "specification and requires a specific study before any "
                        "duty is prescriptive."
                    ]
                    if beyond_en206_classification
                    else []
                ),
                "Buried-zone outputs are groundwater-only under implementation "
                "option (b); independent soil sulfate and Baumann-Gully rows are "
                "explicitly out of scope and never cleared.",
                "No deterioration rate is emitted in any zone.",
                "No thaumasite or reinforcement-chloride boundary is applied "
                "without content-verified support for that decision rule.",
            ],
            "warnings": warnings,
        },
    }

    # Suppression is enforced at the result accessor: an external atmosphere
    # never receives water/soil class fields, even when sulfate or pH were sent.
    if water_contact_zone:
        result.update({
            **en206_class_value_contract,
            "governing_parameter": governing_param,
            "per_parameter_class": assessed,
            "en206_same_class_promotion": promotion,
            "aci318_sulfate_class": aci_class,
            "is456_sulfate_class": is456_class,
        })

    return result
