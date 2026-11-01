"""
Tier 2 Tool: Oxidant exposure assessment.

Answers "does the oxidant loading in this service change the material decision?"
for free chlorine, chlorine dioxide, ozone, peracetic acid, hydrogen peroxide and
ferric iron.

WHAT THIS RETURNS, AND WHY IT IS SHAPED THIS WAY
------------------------------------------------
Three outputs of very different standing, deliberately kept apart so a caller
cannot mistake one for another:

1. `speciation` - HIGH confidence only within Morris's measured 5-35 °C range
   and this model's declared dilute-solution domain. Confidence is explicitly
   reduced for temperature extrapolation or concentrated hypochlorite. The
   HOCl/OCl- split is set by pH around pKa 7.54, so a 1 mg/L residual is ~97%
   HOCl at pH 6 and ~3% at pH 9. Judging a service on "free chlorine" alone
   averages that away.

2. Numeric redox index - SUPPRESSED. The vendored E0 values were not verified
   against primary sources, the OCl branch omitted pH dependence, absolute
   E_pit is suppressed, and a reversible couple potential is not E_corr.

3. `house_rule` - DECISIVE where it applies. Chemical-feed context explicitly
   scopes a concentrated oxidant wetted path; the tool does not extend that rule
   to treated water or infer it when context is missing.

The tool will NOT produce a physics-derived material verdict. It gates instead:
when an oxidant is present, chloride-only screening is declared invalid for that
service, which is the honest statement given the screening tables carry no
oxidant term at all.

CONCURRENT-EXPOSURE CONTRACT
----------------------------
`oxidant` selects the primary reporting lens; it does not assert that every
other accepted exposure is physically absent.  In particular, the public API
and the StreamState adapter can both supply a measured free-chlorine residual
while another oxidant is selected.  Rejecting that combination would break a
valid adapter record, while ignoring it erases a potentially decisive house
rule.  Therefore this module uses the assess-each-and-aggregate contract: a
separately supplied free-chlorine residual is screened as a concurrent exposure,
both assessments are retained, and `disqualified` controls `indeterminate`.
The selected primary wins a tie.  No concentration is inferred, and calls with
only their selected exposure retain their historical payload bit-for-bit.

THE FERRIC-IRON CASE, WHICH IS THE WHOLE ARGUMENT IN ONE EXAMPLE
----------------------------------------------------------------
Fe3+/Fe2+ sits at +0.771 V_SHE, *below* the pH-corrected O2/H2O line at pH 7. On
the thermodynamic index alone, ferric chloride looks less forcing than aerated
water. It is not: 6% FeCl3 is the ASTM G48 test reagent precisely because ferric
iron is a fast, kinetically facile cathodic depolarizer while oxygen reduction on
passive stainless is sluggish. A thermodynamic index cannot see that.

No numeric redox index is emitted. A measured ORP or E_corr is preserved as a
measurement, but it does not lift model confidence and is not load-bearing:
this server has no sourced alloy-specific threshold against which to judge it.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import math
from numbers import Real
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils.oxidant_chemistry import MORRIS_HOCL_CITATION, speciate_free_chlorine

logger = logging.getLogger(__name__)

# Oxidants for which the thermodynamic index is known to misrank because the
# governing behaviour is kinetic, not thermodynamic. Named explicitly rather than
# left for the reader to notice.
_KINETICALLY_MISRANKED = {
    "ferric_iron": (
        "Fe3+/Fe2+ sits BELOW the O2/H2O line thermodynamically, but 6% FeCl3 is "
        "the ASTM G48 test reagent: ferric iron is a fast cathodic depolarizer "
        "where oxygen reduction on passive stainless is sluggish. The index "
        "understates it. Treat a declared ferric chemical-feed wetted path as "
        "aggressive regardless of the index value."
    ),
}

# Alloy families for which this tool has an explicitly supported oxidant/passive-
# film assessment path. Omission says only that the family is unsupported here;
# ferritic/martensitic stainless and aluminium can be passive and must not be
# relabelled as non-passive.
# NOTE the values here are materials.yaml `classification` strings, which are
# NOT the same vocabulary as the `grade_type` column in materials_compositions.csv
# ("austenitic_stainless" vs "austenitic"). Two catalogs, two vocabularies - the
# first version of this tool silently matched neither and returned no house rule
# at all, which is the quiet-failure mode this server keeps producing.
_SUPPORTED_PASSIVE_FILM_FAMILIES = {
    "austenitic_stainless", "duplex_stainless", "super_duplex_stainless",
    "superaustenitic_stainless", "nickel_alloy", "nickel_copper_alloy", "titanium",
}

_STAINLESS_FAMILIES = {
    "austenitic_stainless", "duplex_stainless", "super_duplex_stainless",
    "superaustenitic_stainless",
}

OXIDANT_CHOICES = (
    "free_chlorine",
    "chlorine_dioxide",
    "ozone",
    "hydrogen_peroxide",
    "peracetic_acid",
    "ferric_iron",
    "dissolved_oxygen",
)
SERVICE_CONTEXT_CHOICES = ("chemical_feed", "treated_water")

# Closed enumeration of fields that identify a particular oxidant exposure.
# All other accepted fields describe the shared material/water/measurement
# context.  A non-native selector cannot make one of these measurements vanish.
OXIDANT_SPECIFIC_INPUT_FIELDS = {
    "free_chlorine_mg_L_as_Cl2": {
        "native_oxidant": "free_chlorine",
        "other_selector_behavior": "assess_as_concurrent_exposure",
    }
}

_VERDICT_CONTROL_PRIORITY = {
    "indeterminate": 0,
    "disqualified": 1,
}

# INTERNAL_SCREENING_RULE_ORP_MAGNITUDE_V1
# A deliberately broad input-sanity envelope for aqueous field ORP measurements.
# It is not a material threshold and has no verdict effect. Values beyond +/-2 V
# are treated as unit/reference/instrument errors until independently reconciled.
MEASURED_ORP_MIN_MV = -2_000.0
MEASURED_ORP_MAX_MV = 2_000.0
MEASURED_ORP_SCREENING_RULE = {
    "rule_id": "INTERNAL_SCREENING_RULE_ORP_MAGNITUDE_V1",
    "rule": "-2000 mV <= measured ORP <= +2000 mV",
    "minimum_mV": MEASURED_ORP_MIN_MV,
    "maximum_mV": MEASURED_ORP_MAX_MV,
    "rationale": (
        "Broad aqueous-measurement sanity envelope used to catch unit, reference-"
        "electrode, sign, and instrument-entry errors; not an alloy decision edge."
    ),
    "decision_effect": "input_validation_only_no_material_verdict",
}


class OxidantORPOutOfScreeningDomainError(CorrosionPublicError, ValueError):
    """A reported ORP magnitude is outside the declared input-sanity envelope."""

    def __init__(self, measured_ORP_mV: float) -> None:
        super().__init__(
            "OXIDANT_ORP_OUT_OF_SCREENING_DOMAIN",
            "OXIDANT_ORP_OUT_OF_SCREENING_DOMAIN: measured ORP is outside the "
            "declared broad aqueous input-sanity envelope; reconcile units, sign, "
            "and reference electrode before assessment.",
            details={
                "reason_code": "OXIDANT_ORP_OUT_OF_SCREENING_DOMAIN",
                "status": "out_of_screening_domain",
                "measured_ORP_mV": measured_ORP_mV,
                "screening_rule": dict(MEASURED_ORP_SCREENING_RULE),
                "result_emitted": False,
            },
        )


@enforce_tool_input_contract("corrosion_assess_oxidant_exposure")
def assess_oxidant_exposure(
    material: str,
    oxidant: str,
    pH: float,
    temperature_C: float = 25.0,
    free_chlorine_mg_L_as_Cl2: Optional[float] = None,
    Cl_mg_L: Optional[float] = None,
    measured_ORP_mV: Optional[float] = None,
    reference_electrode: str = "SHE",
    service_context: Optional[str] = None,
) -> Dict[str, Any]:
    """Assess an oxidising service against a material.

    Args:
        material: alloy designation, resolved through the canonical catalog.
        oxidant: one of free_chlorine, chlorine_dioxide, ozone,
            hydrogen_peroxide, peracetic_acid, ferric_iron, dissolved_oxygen.
        pH: bulk pH. Governs free-chlorine speciation.
        temperature_C: bulk temperature.
        free_chlorine_mg_L_as_Cl2: required when oxidant is free_chlorine. If
            supplied with another selected oxidant, it is assessed as a
            concurrent exposure and participates in the controlling verdict.
        Cl_mg_L: chloride, for context on the gate.
        measured_ORP_mV: a MEASURED redox potential. Supplying it is what moves
            this assessment above low confidence; it is never inferred.
        reference_electrode: SHE, SCE or Ag/AgCl for measured_ORP_mV. Passed
            straight to the shared converter, which raises on anything else.
        service_context: "chemical_feed" (concentrated oxidant wetted path:
            dosing skid, quill, carrier) or "treated_water" (downstream water).
            The NaOCl and ferric-chloride house rules apply only to
            chemical_feed. Scope is not inferred from concentration, and ferric
            concentration is otherwise unavailable to this API.

    Returns:
        dict with speciation, redox-index suppression status, house_rule,
        assessment_status and gates. When a concurrent free-chlorine exposure is
        supplied, ``exposure_assessments`` retains both results and
        ``controlling_exposure`` explains the aggregate compatibility fields.
        `assessment_status` is "indeterminate" unless a decisive house rule
        applies; a measured potential alone is never judged.
    """
    from corrosion_engineering_mcp.core.materials import canonical_id, get_material, MaterialCatalogError
    from corrosion_engineering_mcp.core.localized_backend import ModelNotApplicableError

    try:
        resolved = canonical_id(material)
        record = get_material(resolved)
    except MaterialCatalogError:
        from corrosion_engineering_mcp.core.materials import UnknownMaterialError

        raise UnknownMaterialError(material) from None

    if oxidant not in OXIDANT_CHOICES:
        raise ValueError(
            f"oxidant must be one of {', '.join(OXIDANT_CHOICES)}; got {oxidant!r}"
        )
    if service_context is not None and service_context not in SERVICE_CONTEXT_CHOICES:
        raise ValueError(
            "service_context must be 'chemical_feed', 'treated_water', or omitted"
        )
    for field_name, value in (
        ("pH", pH),
        ("temperature_C", temperature_C),
        ("free_chlorine_mg_L_as_Cl2", free_chlorine_mg_L_as_Cl2),
        ("Cl_mg_L", Cl_mg_L),
        ("measured_ORP_mV", measured_ORP_mV),
    ):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{field_name} must be a finite real number")
    validate_float_domain_inputs(
        {
            "pH": pH,
            "temperature_C": temperature_C,
            "free_chlorine_mg_L_as_Cl2": free_chlorine_mg_L_as_Cl2,
            "Cl_mg_L": Cl_mg_L,
            "measured_ORP_mV": measured_ORP_mV,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    if not 0.0 <= pH <= 14.0:
        raise ValueError("pH must be between 0 and 14")
    if not -10.0 <= temperature_C <= 150.0:
        raise ValueError("temperature_C must be between -10 and 150")
    for field_name, value in (
        ("free_chlorine_mg_L_as_Cl2", free_chlorine_mg_L_as_Cl2),
        ("Cl_mg_L", Cl_mg_L),
    ):
        if value is not None and value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
    if measured_ORP_mV is not None and not (
        MEASURED_ORP_MIN_MV <= measured_ORP_mV <= MEASURED_ORP_MAX_MV
    ):
        raise OxidantORPOutOfScreeningDomainError(measured_ORP_mV)

    out: Dict[str, Any] = {
        "material": resolved,
        "oxidant": oxidant,
        "pH": pH,
        "temperature_C": temperature_C,
        "Cl_mg_L": Cl_mg_L,
    }
    gates: List[str] = []
    warnings: List[str] = []

    # ---- 1. Speciation. Confidence is bounded by Morris's 5-35 C data. -------
    if oxidant == "free_chlorine":
        if free_chlorine_mg_L_as_Cl2 is None:
            raise ValueError(
                "FREE_CHLORINE_CONCENTRATION_REQUIRED: the HOCl/OCl- split is what "
                "makes this assessment useful and it cannot be inferred"
            )
        spec = speciate_free_chlorine(free_chlorine_mg_L_as_Cl2, pH, temperature_C)
        if (
            spec.extrapolated_beyond_measured_range
            and spec.concentrated_beyond_dilute_model
        ):
            speciation_confidence = "reduced_extrapolated_and_concentrated"
        elif spec.extrapolated_beyond_measured_range:
            speciation_confidence = "reduced_extrapolated"
        elif spec.concentrated_beyond_dilute_model:
            speciation_confidence = "reduced_concentrated_nonideal"
        else:
            speciation_confidence = "high"
        out["speciation"] = {
            "reported_free_chlorine_mg_L_as_Cl2": (
                spec.total_free_chlorine_mg_L_as_Cl2
            ),
            "confidence": speciation_confidence,
            "extrapolated_beyond_measured_range": (
                spec.extrapolated_beyond_measured_range
            ),
            "measured_temperature_range_C": [5.0, 35.0],
            "pKa": round(spec.pKa, 3),
            "pKa_unrounded": spec.pKa,
            "fraction_HOCl": round(spec.fraction_HOCl, 4),
            "fraction_HOCl_unrounded": spec.fraction_HOCl,
            "fraction_OCl": round(spec.fraction_OCl, 4),
            "fraction_OCl_unrounded": spec.fraction_OCl,
            "HOCl_mg_L_as_Cl2": round(spec.HOCl_mg_L_as_Cl2, 4),
            "HOCl_mg_L_as_Cl2_unrounded": spec.HOCl_mg_L_as_Cl2,
            "OCl_mg_L_as_Cl2": round(spec.OCl_mg_L_as_Cl2, 4),
            "OCl_mg_L_as_Cl2_unrounded": spec.OCl_mg_L_as_Cl2,
            "speciation_value_standing": (
                "unrounded_values_authoritative_rounded_fields_display_only"
            ),
            "concentration_domain": {
                "standing": (
                    "outside_declared_dilute_solution_domain"
                    if spec.concentrated_beyond_dilute_model
                    else "within_declared_dilute_solution_domain"
                ),
                "free_chlorine_mol_L_as_Cl2": spec.free_chlorine_mol_L,
                "dilute_model_max_mol_L_as_Cl2": spec.dilute_model_max_mol_L,
                "boundary_basis": (
                    "Conservative model-applicability boundary for replacing "
                    "activities with concentrations; not a Morris threshold."
                ),
            },
            "source": dict(MORRIS_HOCL_CITATION),
            "note": spec.note,
        }
        if spec.extrapolated_beyond_measured_range:
            gates.append("SPECIATION_TEMPERATURE_EXTRAPOLATION")
        if spec.concentrated_beyond_dilute_model:
            gates.append("SPECIATION_CONCENTRATION_OUT_OF_DOMAIN")
    else:
        out["speciation"] = None

    # A DECLARED ZERO IS NOT AN OXIDISING SERVICE.
    #
    # The previous round stopped the house rule firing at zero free chlorine but
    # left the rest of the response behaving as though chlorine were present: it
    # still reported an HOCl/Cl- forcing index at +1.27 V_SHE and still raised
    # CHLORIDE_SCREENING_INVALID, which suppresses an otherwise applicable
    # chloride screen. Fixing the headline verdict and leaving the reasoning that
    # produced it is how a defect survives its own fix.
    oxidant_absent = (
        oxidant == "free_chlorine"
        and free_chlorine_mg_L_as_Cl2 is not None
        and free_chlorine_mg_L_as_Cl2 <= 0.0
    )

    # ---- 2. Numeric redox index is suppressed, not merely caveated. --------
    out["redox_index_status"] = {
        "standing": "suppressed_unverified_standard_potentials",
        "re_enable_conditions": [
            "primary-source standard potentials and complete Nernst reactions",
            "validation against measured solution potentials",
            "a sourced alloy-specific threshold before any decision use",
        ],
    }
    out["suppressed_outputs"] = ["redox_forcing_index"]

    if oxidant in _KINETICALLY_MISRANKED:
        warnings.append(_KINETICALLY_MISRANKED[oxidant])

    # ---- 3. House rule. Decisive where it applies. --------------------------
    out["house_rule"] = _house_rule(
        resolved, record, oxidant, service_context, free_chlorine_mg_L_as_Cl2
    )

    # ---- 4. Gates ----------------------------------------------------------
    if record.classification not in _SUPPORTED_PASSIVE_FILM_FAMILIES:
        out["material_family_support"] = {
            "status": "unsupported",
            "reason_code": "UNSUPPORTED_MATERIAL_FAMILY",
            "material_classification": record.classification,
            "decision_effect": "fail_closed_indeterminate",
        }
        gates.append(
            f"UNSUPPORTED_MATERIAL_FAMILY: {resolved} is classified "
            f"{record.classification}, which is outside this tool's supported "
            f"material-family set. This is not a claim that the material is "
            f"non-passive; the assessment fails closed as indeterminate."
        )
    else:
        out["material_family_support"] = {
            "status": "supported",
            "reason_code": None,
            "material_classification": record.classification,
            "decision_effect": "continue_supported_family_screen",
        }

    if oxidant != "dissolved_oxygen" and not oxidant_absent:
        gates.append(
            "CHLORIDE_SCREENING_INVALID: an oxidant beyond dissolved oxygen is "
            "present. The chloride screening tables carry NO oxidant term "
            "(get_chloride_threshold takes only material, temperature and pH), so "
            "a chloride-only verdict does not describe this service."
        )

    if measured_ORP_mV is None:
        gates.append(
            "NO_MEASURED_POTENTIAL: no measured ORP or E_corr was supplied. The "
            "former computed redox index is suppressed and cannot substitute for "
            "a measurement."
        )
        measured = None
    else:
        from corrosion_engineering_mcp.utils.redox_state import orp_to_eh

        # The string is passed STRAIGHT THROUGH to the shared converter, which
        # knows SHE, SCE and Ag/AgCl and raises on anything else.
        #
        # This previously went via a local two-entry dict that mapped only SHE
        # and SCE and silently defaulted everything else to SHE. Ag/AgCl - the
        # electrode most field ORP meters actually use - was therefore read as
        # SHE and came back 197 mV low, and a typo was accepted in silence. A
        # correct shared utility wrapped in a lossy local adapter is worse than
        # no adapter.
        #
        # orp_to_eh takes mV, not volts. Dividing by 1000 first turned 620 mV
        # into 0.6 mV and produced Eh = 0.0006 V. Caught by the test that
        # asserted the round-trip rather than merely that a number came back.
        eh = orp_to_eh(measured_ORP_mV, reference_electrode=reference_electrode)
        measured = {
            "measured_ORP_mV": measured_ORP_mV,
            "reference_electrode": reference_electrode,
            "Eh_VSHE": round(eh, 4),
            "Eh_VSHE_unrounded": eh,
            "Eh_VSHE_value_standing": "unrounded_authoritative",
            "measurement_standing": "reported_measured_input_not_judged",
            "input_domain": dict(MEASURED_ORP_SCREENING_RULE),
        }
        gates.append(
            "NO_SOURCED_THRESHOLD: a measured potential is available, but this "
            "server holds no validated E_pit or sourced service threshold to "
            "compare it against. Absolute E_pit is suppressed (see "
            "TOOL_SANITY_LOG finding 5). The measurement is reported, not judged."
        )
    out["measured_potential"] = measured

    # ---- 5. Status ---------------------------------------------------------
    hr = out["house_rule"]
    if hr and hr.get("verdict") == "disqualified":
        out["assessment_status"] = "ok"
        out["verdict"] = "disqualified"
        out["verdict_basis"] = "house_standard"
    else:
        out["assessment_status"] = "indeterminate"
        out["verdict"] = "indeterminate"
        out["verdict_basis"] = None

    out["gates"] = gates
    out["warnings"] = warnings
    out["provenance"] = {
        "model": "oxidant_exposure",
        "version": "1.0",
        "validation_dataset": None,
        "confidence": "low" if out["assessment_status"] != "ok" else "medium",
        "sources": [
            dict(MORRIS_HOCL_CITATION),
            "chemical-metering-spec chemical-service-selection.yaml (house rule)",
        ],
        "assumptions": [
            "Unverified standard reduction potentials are not used in any output.",
            "A measured ORP is reported but not judged without a sourced "
            "alloy-specific threshold.",
            "The ideal concentration-based HOCl/OCl- split is not high-confidence "
            "above the declared dilute-solution model domain.",
        ],
        "warnings": warnings,
    }

    if oxidant != "free_chlorine" and free_chlorine_mg_L_as_Cl2 is not None:
        return _aggregate_concurrent_free_chlorine(
            primary_result=out,
            material=material,
            pH=pH,
            temperature_C=temperature_C,
            free_chlorine_mg_L_as_Cl2=free_chlorine_mg_L_as_Cl2,
            Cl_mg_L=Cl_mg_L,
            measured_ORP_mV=measured_ORP_mV,
            reference_electrode=reference_electrode,
            service_context=service_context,
        )
    return out


def _aggregate_concurrent_free_chlorine(
    *,
    primary_result: Dict[str, Any],
    material: str,
    pH: float,
    temperature_C: float,
    free_chlorine_mg_L_as_Cl2: float,
    Cl_mg_L: Optional[float],
    measured_ORP_mV: Optional[float],
    reference_electrode: str,
    service_context: Optional[str],
) -> Dict[str, Any]:
    """Compose a selected lens with an explicitly supplied chlorine exposure.

    The recursive call is deliberately narrow: selecting ``free_chlorine``
    makes the aggregation condition false, so the established single-exposure
    implementation remains the one source of speciation, gates, and house-rule
    behaviour.  Deep copies keep the per-exposure records immutable while the
    top-level compatibility fields are replaced by the controlling result.
    """

    primary_snapshot = deepcopy(primary_result)
    chlorine_result = assess_oxidant_exposure(
        material=material,
        oxidant="free_chlorine",
        pH=pH,
        temperature_C=temperature_C,
        free_chlorine_mg_L_as_Cl2=free_chlorine_mg_L_as_Cl2,
        Cl_mg_L=Cl_mg_L,
        measured_ORP_mV=measured_ORP_mV,
        reference_electrode=reference_electrode,
        service_context=service_context,
    )

    exposures = [
        {
            "role": "selected_primary",
            "oxidant": primary_snapshot["oxidant"],
            "result": primary_snapshot,
        },
        {
            "role": "concurrent_supplied_field",
            "oxidant": "free_chlorine",
            "source_field": "free_chlorine_mg_L_as_Cl2",
            "result": chlorine_result,
        },
    ]

    controlling = exposures[0]
    for exposure in exposures[1:]:
        try:
            candidate_priority = _VERDICT_CONTROL_PRIORITY[
                exposure["result"]["verdict"]
            ]
            controlling_priority = _VERDICT_CONTROL_PRIORITY[
                controlling["result"]["verdict"]
            ]
        except KeyError as exc:
            raise RuntimeError(
                "oxidant aggregate encountered an unregistered verdict; add an "
                "explicit control priority before emitting it"
            ) from exc
        if candidate_priority > controlling_priority:
            controlling = exposure

    controlling_result = controlling["result"]
    primary_result["exposure_assessments"] = exposures
    primary_result["controlling_exposure"] = {
        "role": controlling["role"],
        "oxidant": controlling["oxidant"],
        "source_field": controlling.get("source_field"),
        "verdict": controlling_result["verdict"],
        "verdict_basis": controlling_result["verdict_basis"],
        "selection_rule": (
            "disqualified_controls_indeterminate_selected_primary_wins_ties"
        ),
    }

    for field_name in (
        "house_rule",
        "assessment_status",
        "verdict",
        "verdict_basis",
    ):
        primary_result[field_name] = deepcopy(controlling_result[field_name])

    primary_result["gates"] = list(
        dict.fromkeys(primary_snapshot["gates"] + chlorine_result["gates"])
    )
    primary_result["warnings"] = list(
        dict.fromkeys(primary_snapshot["warnings"] + chlorine_result["warnings"])
    )
    primary_result["provenance"]["confidence"] = controlling_result[
        "provenance"
    ]["confidence"]
    primary_result["provenance"]["warnings"] = primary_result["warnings"]
    primary_result["provenance"]["aggregation"] = {
        "standing": "deterministic_policy_composition",
        "assessed_oxidants": [exposure["oxidant"] for exposure in exposures],
        "selection_rule": primary_result["controlling_exposure"]["selection_rule"],
        "no_exposure_inferred": True,
    }
    return primary_result


def _house_rule(
    resolved: str,
    record,
    oxidant: str,
    service_context: Optional[str] = None,
    free_chlorine_mg_L_as_Cl2: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Surface the existing house standard, which is a sourced policy verdict.

    `chemical-service-selection.yaml` declares `stainless_wetted_ok: false` for
    hypochlorite, and `moc_catalog.py` scopes it to the ("injection.quill",
    "wetted") path. That rule predates this tool and is the reason SS316 should
    never have reached tag 401-SM-01.

    SCOPE IS PART OF THE RULE. It is a CHEMICAL-SERVICE rule about the neat
    hypochlorite wetted path, not a statement about any stainless that ever sees
    free chlorine. Applied unscoped it disqualified 316L in treated-water service
    and even when free chlorine was explicitly zero; those are different service
    questions from the chemical-feed wetted path.

    `service_context` is therefore required rather than inferred from a
    concentration boundary, because any boundary picked here would be invented.
    """
    stainless = record.classification in _STAINLESS_FAMILIES

    if oxidant == "free_chlorine" and stainless:
        if free_chlorine_mg_L_as_Cl2 is not None and free_chlorine_mg_L_as_Cl2 <= 0.0:
            return {
                "verdict": "not_applicable_no_oxidant_present",
                "rule": "stainless_wetted_ok: false for NaOCl",
                "evidence_class": "house_standard",
                "rationale": (
                    "Free chlorine is zero, so there is no oxidant for the rule to "
                    "act on. A rule about hypochlorite service cannot disqualify a "
                    "material in the absence of hypochlorite."
                ),
            }
        if service_context != "chemical_feed":
            return {
                "verdict": "not_applied_out_of_declared_scope",
                "rule": "stainless_wetted_ok: false for NaOCl",
                "evidence_class": "house_standard",
                "source": (
                    "skills/chemical-metering-spec/references/"
                    "chemical-service-selection.yaml; scoped in "
                    "engineering_utils.house_standards.moc_catalog to "
                    '("injection.quill", "wetted")'
                ),
                "rationale": (
                    f"service_context is {service_context!r}. The house rule covers "
                    f"the NEAT hypochlorite wetted path (dosing skid, quill, "
                    f"carrier to the point of injection). It does not speak to "
                    f"stainless carrying a treated-water residual downstream. "
                    f"Declare service_context='chemical_feed' to apply it."
                ),
            }
        return {
            "verdict": "disqualified",
            "rule": "stainless_wetted_ok: false for NaOCl",
            "evidence_class": "house_standard",
            "source": (
                "skills/chemical-metering-spec/references/"
                "chemical-service-selection.yaml"
            ),
            "rationale": (
                "Chloride pitting under an oxidising residual. The house standard "
                "excludes stainless from the hypochlorite wetted path outright; it "
                "does not depend on this server's physics being validated."
            ),
        }
    if oxidant == "ferric_iron" and stainless:
        if service_context != "chemical_feed":
            return {
                "verdict": "not_applied_out_of_declared_scope",
                "rule": "concentrated ferric chloride on a stainless wetted path",
                "evidence_class": "house_standard",
                "source_id": "CHEMICAL_SERVICE_SELECTION_FERRIC",
                "source": (
                    "skills/chemical-metering-spec/references/"
                    "chemical-service-selection.yaml"
                ),
                "rationale": (
                    f"service_context is {service_context!r}. No ferric-iron "
                    f"concentration is supplied, so naming ferric iron alone "
                    f"cannot establish a concentrated FeCl3 service. Declare "
                    f"service_context='chemical_feed' only for its dosing skid, "
                    f"quill or concentrated carrier path."
                ),
            }
        return {
            "verdict": "disqualified",
            "rule": "concentrated ferric chloride on a stainless wetted path",
            "evidence_class": "house_standard",
            "source_id": "CHEMICAL_SERVICE_SELECTION_FERRIC",
            "source": (
                "skills/chemical-metering-spec/references/"
                "chemical-service-selection.yaml; wetted-material-limits.md"
            ),
            "rationale": (
                "service_context='chemical_feed' declares the concentrated "
                "ferric-feed wetted path. The chemical-service standard marks "
                "stainless_wetted_ok false for ferric and the wetted-material "
                "table specifies polymer liquid ends. This sourced house rule "
                "does not extend to downstream treated water."
            ),
        }
    return None
