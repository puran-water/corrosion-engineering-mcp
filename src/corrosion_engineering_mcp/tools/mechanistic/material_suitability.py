'Tier 2 Tool: relative chloride coordinate and ordinal alloy ranking.'

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract

logger = logging.getLogger(__name__)

_STAINLESS_SULFIDE_EVIDENCE_CLASSES = frozenset({
    "austenitic_stainless",
    "duplex_stainless",
    "super_duplex_stainless",
    "superaustenitic_stainless",
    "ferritic_stainless",
    "martensitic_stainless",
})

class OxidantHandoffRequired(CorrosionPublicError, ValueError):
    """An oxidant was declared, so a chloride-based verdict is not the answer."""

    def __init__(self, oxidant: str) -> None:
        super().__init__(
            "OXIDANT_HANDOFF_REQUIRED",
            f"OXIDANT_HANDOFF_REQUIRED: {oxidant!r} was declared. The chloride "
            f"screening tables resolve temperature and pH but carry NO oxidant "
            f"term, so a chloride-based verdict here would be answering a "
            f"different question than the one asked. This is not hypothetical: a "
            f"hypochlorite injection quill sits at ~40 mg/L chloride, which any "
            f"chloride-only screen calls benign while omitting the controlling "
            f"oxidant variable. Use corrosion_assess_oxidant_exposure.",
            details={
                "reason_code": "OXIDANT_HANDOFF_REQUIRED",
                "oxidant": oxidant,
                "use_instead": "corrosion_assess_oxidant_exposure",
            },
        )


class MaterialSuitabilityOutOfValidityError(CorrosionPublicError, ValueError):
    """A ranking request lies outside its provenance's declared domain."""

    def __init__(
        self,
        quantity: str,
        value: float,
        minimum: float,
        maximum: float,
    ) -> None:
        super().__init__(
            "MATERIAL_SUITABILITY_OUT_OF_VALIDITY",
            "MATERIAL_SUITABILITY_OUT_OF_VALIDITY: "
            f"{quantity}={repr(float(value))} is outside INTERNAL_CL_SCREEN_V1 "
            f"validity [{repr(float(minimum))}, {repr(float(maximum))}]. "
            "The ranking convention supplies no extrapolation rule; a prose "
            "warning cannot make an out-of-domain coordinate usable.",
            details={
                "reason_code": "MATERIAL_SUITABILITY_OUT_OF_VALIDITY",
                "quantity": quantity,
                "value": value,
                "minimum": minimum,
                "maximum": maximum,
                "source_id": "INTERNAL_CL_SCREEN_V1",
            },
        )


@enforce_tool_input_contract("corrosion_screen_material_suitability")
def screen_material_suitability(
    material: str,
    Cl_mg_L: float,
    temperature_C: float = 25.0,
    pH: float = 7.0,
    oxidant: Optional[str] = None,
    measured_ORP_mV: Optional[float] = None,
    measured_ORP_uncertainty_mV: Optional[float] = None,
    reference_electrode: str = "SHE",
    sulfide_mg_L: Optional[float] = None,
    candidates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Screen a material against a chloride service, and rank alternatives.

    Returns a chloride margin with no absolute material verdict, the G48 ranking
    index, and - when `candidates` is supplied - a dense-ranked ordering of
    alternatives at the same conditions. Equal service/reference ratios receive
    equal ranks; canonical material ID is only a deterministic display-order
    tiebreaker for resolved rows. Unresolved rows use casefolded then exact caller
    text because no canonical ID exists. At exactly zero service chloride every
    candidate ratio is zero, so ranks are null and the ordering status is typed
    as indeterminate.
    """
    from corrosion_engineering_mcp.core.materials import MaterialCatalogError, UnknownMaterialError, canonical_id, get_material
    from corrosion_engineering_mcp.data.authoritative_materials_data import get_chloride_threshold, get_chloride_threshold_record

    validate_float_domain_inputs(
        {
            "Cl_mg_L": Cl_mg_L,
            "temperature_C": temperature_C,
            "pH": pH,
            "measured_ORP_mV": measured_ORP_mV,
            "measured_ORP_uncertainty_mV": measured_ORP_uncertainty_mV,
            "sulfide_mg_L": sulfide_mg_L,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )

    if oxidant:
        raise OxidantHandoffRequired(oxidant)

    # These are the machine-readable bounds declared by the ranking source in
    # data/provenance.yaml.  Enforce them before calculating a coordinate so an
    # out-of-domain number cannot escape alongside a warning.
    if not 0.0 <= temperature_C <= 60.0:
        raise MaterialSuitabilityOutOfValidityError(
            "temperature_C", temperature_C, 0.0, 60.0
        )
    if not 1.0 <= pH <= 10.0:
        raise MaterialSuitabilityOutOfValidityError("pH", pH, 1.0, 10.0)

    try:
        resolved = canonical_id(material)
        record = get_material(resolved)
    except MaterialCatalogError:
        raise UnknownMaterialError(material) from None

    warnings: List[str] = []
    gates: List[str] = []

    threshold_record = get_chloride_threshold_record(resolved, temperature_C, pH)
    threshold = threshold_record.value
    independent_validation = dict(
        threshold_record.extras["independent_validation"]
    )
    cross_family_contract = dict(
        threshold_record.extras["cross_family_ordering"]
    )

    # The gate. It is proven at the call site, not asserted in a comment.
    qualification_blocked_because: Optional[str] = None
    try:
        threshold_record.require_qualification_grade()
        qualification_available = True
    except ValueError as exc:
        qualification_available = False
        qualification_blocked_because = str(exc)
        gates.append("NOT_QUALIFICATION_GRADE")

    ratio = Cl_mg_L / threshold if threshold > 0 else float("inf")

    # The ratio is a continuous ranking coordinate only. Former 0.5x, 1x and 5x
    # bands turned an unvalidated two-anchor ladder into graded verdicts. No
    # threshold in the source licenses that transformation, so no band remains.
    chloride_basis_status = "ranking_available"
    if not qualification_available:
        warnings.append(
            "This result cannot be quoted as material approval. Obtaining IGN "
            "4-25-02 Issue 2 or the Outokumpu chloride-vs-temperature charts "
            "is what would change that; see data/PROVENANCE.md."
        )

    # ---- sulfide standing, never a concentration-to-verdict boolean --------
    from corrosion_engineering_mcp.utils.localized_criteria import sulfide_standing as grade_sulfide

    sulfide_evidence = grade_sulfide(sulfide_mg_L)
    if record.classification in _STAINLESS_SULFIDE_EVIDENCE_CLASSES:
        sulfide_evidence["material_scope"] = (
            "chromium_oxide_passive_family_direct_concentration_data_for_316L"
        )
        if sulfide_mg_L is None:
            chloride_basis_status = "conditional_sulfide_not_measured"
            gates.append("SULFIDE_NOT_MEASURED")
            warnings.append(
                f"{sulfide_evidence['uncertainty']} The chloride coordinate "
                "describes a chromium-oxide film and is only a conditional ranking."
            )
        elif sulfide_mg_L > 0.0:
            # The chloride-only relation contains no sulfide term. Withhold the
            # coordinate for every positive reported concentration, irrespective
            # of which descriptive grade it occupies. This is a model-scope
            # statement, not a hidden threshold or a claim that 0+ is an onset.
            chloride_basis_status = "conditional_sulfide_effect_not_quantified"
            gates.append("SULFIDE_EFFECT_NOT_QUANTIFIED_IN_CHLORIDE_MODEL")
            warnings.append(
                f"Sulfide grade {sulfide_evidence['grade']}: "
                f"{sulfide_evidence['rationale']} {sulfide_evidence['uncertainty']} "
                "The chloride coordinate is withheld because the model has no "
                "sulfide term; the grade itself makes no applicability verdict."
            )
    elif sulfide_mg_L is None:
        chloride_basis_status = "conditional_sulfide_not_measured"
        gates.append("SULFIDE_NOT_MEASURED")
    elif sulfide_mg_L > 0.0:
        chloride_basis_status = (
            "not_evaluated_sulfide_effect_no_class_specific_source"
        )
        sulfide_evidence["material_scope"] = "no_class_specific_source"
        gates.append("SULFIDE_EFFECT_NEEDS_CLASS_SPECIFIC_SOURCE")
        warnings.append(
            "Sulfide was reported for a non-stainless passive alloy, but this "
            "server has no class-specific source for transferring the 316L "
            "evidence. The chloride coordinate and ranking are withheld."
        )

    # ---- measured ORP is reported, not used as an unsourced ranking gate ---
    redox: Optional[Dict[str, Any]] = None
    if measured_ORP_mV is not None:
        from corrosion_engineering_mcp.utils.redox_state import orp_to_eh

        eh_v = orp_to_eh(measured_ORP_mV, reference_electrode)
        redox = {
            "measured_ORP_mV": measured_ORP_mV,
            "measurement_uncertainty_mV": measured_ORP_uncertainty_mV,
            "uncertainty_standing": (
                "reported"
                if measured_ORP_uncertainty_mV is not None
                else "not_reported"
            ),
            "reference_electrode": reference_electrode,
            "Eh_VSHE": round(eh_v, 4),
            "Eh_VSHE_unrounded": eh_v,
            "Eh_VSHE_value_standing": "unrounded_authoritative",
            "decision_threshold_applied": False,
            "interpretation_standing": "reported_not_evaluated",
        }

    # ---- G48 stays a ranking index, explicitly ------------------------------
    from corrosion_engineering_mcp.data.authoritative_materials_data import get_cpt_from_astm

    g48 = get_cpt_from_astm(resolved)
    g48_block = {
        "CPT_G48_C": (g48 or {}).get("CPT_C"),
        "CCT_G48_C": (g48 or {}).get("CCT_C"),
        "test_basis": (
            "ASTM G48 Section 6.1 Methods A/B 6% FeCl3 recipe stoichiometry: "
            "100 g / 270.285 g/mol = 0.36998 mol FeCl3.6H2O; "
            "3 x 0.36998 = 1.10994 mol chloride, or "
            "about 1.11 M chloride at ~1.0 L final solution "
            "(1.23 M on the initial 0.9 L water volume). "
            "Methods C-F use a separate acidified solution. "
            "A RANKING index only. It orders alloys reliably and "
            "says nothing about a service limit at 50-500 mg/L chloride and "
            "open-circuit potential - comparing those conditions is what floored "
            "the incumbent classifier at 'critical' for every input."
        ),
        "usable_for": ["ranking"],
        "not_usable_for": ["service_temperature_limit", "qualification"],
    }

    # ---- ordering alternatives at the same conditions ----------------------
    ranking: Optional[List[Dict[str, Any]]] = None
    ranking_status: Optional[Dict[str, Any]] = None
    ranking_basis_available = chloride_basis_status in {
        "ranking_available",
        "conditional_sulfide_not_measured",
    }
    if candidates and ranking_basis_available:
        rankable: List[tuple[float, str]] = []
        unresolved: List[Dict[str, Any]] = []
        for cand in candidates:
            try:
                cid = canonical_id(cand)
                cand_threshold = get_chloride_threshold(cid, temperature_C, pH)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                unresolved.append({
                    "material": cand, "rank": None,
                    "error": type(exc).__name__,
                })
                continue
            rankable.append((Cl_mg_L / cand_threshold, cid))
        # The ratio is the ranking metric. Canonical ID is only a deterministic
        # display-order key; it must never manufacture distinct ranks for equal
        # metrics. Unresolved rows have no canonical ID, so casefolded text and
        # then exact text provide a total order without leaking caller order.
        rankable.sort(key=lambda item: (item[0], item[1]))
        unresolved.sort(
            key=lambda item: (
                str(item["material"]).casefold(),
                str(item["material"]),
                str(item["error"]),
            )
        )

        if Cl_mg_L == 0.0:
            ranking = [
                {"material": cid, "rank": None}
                for _ratio, cid in rankable
            ] + unresolved
        else:
            dense_rank = 0
            previous_metric: Optional[float] = None
            ranked_rows: List[Dict[str, Any]] = []
            for metric, cid in rankable:
                if previous_metric is None or metric != previous_metric:
                    dense_rank += 1
                    previous_metric = metric
                ranked_rows.append({"material": cid, "rank": dense_rank})
            ranking = ranked_rows + unresolved

        # Cross-family inversions cannot be explained from PREN alone. The
        # previous round called them a PREN limitation rather than a data error,
        # but had no matched-condition dataset to distinguish those hypotheses.
        families = {
            get_material(cid).classification
            for _ratio, cid in rankable
        }
        cross_family = len(families) > 1
        ranking_status = {
            "available": Cl_mg_L != 0.0,
            "reason_code": (
                None
                if Cl_mg_L != 0.0
                else "INDETERMINATE_ZERO_SERVICE_CHLORIDE"
            ),
            "ordering_status": (
                "ranked_by_service_to_reference_ratio"
                if Cl_mg_L != 0.0
                else "all_candidate_metrics_tied"
            ),
            "rank_style": "dense",
            "display_order_tiebreaker": (
                "canonical_material_id_for_resolved_rows;"
                "casefold_then_exact_input_for_unresolved_rows"
            ),
            "independent_validation": independent_validation,
            "cross_family": cross_family,
            "cross_family_ordering": (
                cross_family_contract["status"]
                if cross_family else "not_applicable"
            ),
            "evidence_needed": (
                list(cross_family_contract.get("evidence_needed", []))
                if cross_family else []
            ),
        }
        if len(families) > 1:
            warnings.append(
                f"CROSS-FAMILY RANKING: candidates span {len(families)} grade "
                f"families ({', '.join(sorted(families))}). Whether an inversion "
                f"is genuine alloy behaviour or a construction error is "
                f"UNRESOLVED without matched-condition data. Treat positions as "
                f"this table's convention, not as a physical ordering."
            )
        if Cl_mg_L == 0.0:
            warnings.append(
                "CANDIDATE ORDERING INDETERMINATE: service chloride is exactly "
                "zero, so every service/reference ratio is zero. Candidate rows "
                "are displayed by canonical material ID, with casefolded then "
                "exact caller text for unresolved rows, and with null ranks; "
                "that display order is not an engineering ordering."
            )
        warnings.append(
            "The candidate ordering is an INTERNAL RANKING with no independent "
            "validation dataset. It is not a physical service ordering or an "
            "absolute material verdict."
        )
    elif candidates:
        ranking_status = {
            "available": False,
            "reason_code": chloride_basis_status,
            "independent_validation": independent_validation,
            "cross_family": None,
            "cross_family_ordering": "not_assessed",
            "evidence_needed": [],
        }

    warnings.append(
        "Restructured off the ASTM G48 temperature margin onto the chloride "
        "margin. The G48 margin returned 'critical' for every service above "
        "5 C for 316L regardless of chloride, which is a constant rather than "
        "a conservative result."
    )

    return {
        "material": resolved,
        "material_classification": record.classification,
        "service": {
            "Cl_mg_L": Cl_mg_L, "temperature_C": temperature_C, "pH": pH,
        },
        "assessment_scope": "relative_ranking_only",
        "absolute_verdict_available": False,
        "chloride_basis_status": chloride_basis_status,
        "ranking_reference_chloride_mg_L": (
            threshold if ranking_basis_available else None
        ),
        "ranking_reference_chloride_mg_L_display": (
            round(threshold, 1) if ranking_basis_available else None
        ),
        "ranking_reference_chloride_mg_L_value_standing": (
            "authoritative_unrounded" if ranking_basis_available else None
        ),
        "service_to_ranking_reference_ratio": (
            ratio if ranking_basis_available else None
        ),
        "service_to_ranking_reference_ratio_display": (
            round(ratio, 3) if ranking_basis_available else None
        ),
        "service_to_ranking_reference_ratio_value_standing": (
            "authoritative_unrounded" if ranking_basis_available else None
        ),
        "qualification_grade_available": qualification_available,
        "qualification_blocked_because": qualification_blocked_because,
        "threshold_provenance": {
            "source_id": threshold_record.source_id,
            "evidence_class": threshold_record.evidence_class,
            "derivation": threshold_record.derivation,
            "assumptions": threshold_record.assumptions,
            "validity": threshold_record.validity,
            "composite_source_ids": threshold_record.composite_source_ids,
            "usable_for": threshold_record.usable_for,
            "not_usable_for": threshold_record.not_usable_for,
            "independent_validation": independent_validation,
            "cross_family_ordering": cross_family_contract,
        },
        "g48_ranking_index": g48_block,
        "sulfide_standing": sulfide_evidence,
        "redox": redox,
        "candidate_ranking": ranking,
        "candidate_ranking_status": ranking_status,
        "gates": gates,
        "warnings": warnings,
        "provenance": {
            "model": "material_suitability_screen",
            "version": "1.0",
            "validation_dataset": None,
            "confidence": "ranking_only",
            "usable_for": ["relative_ranking"],
            "not_usable_for": [
                "qualification", "disqualification", "corrosion_rate",
                "risk_level",
            ],
            "sources": [
                f"{threshold_record.source_id} via data/provenance.yaml",
                "INTERNAL_G48_RANKING_V1 via data/provenance.yaml",
            ],
            "assumptions": [
                "The internal thresholds support relative ordering only; no "
                "absolute verdict is available in either direction.",
                "No corrosion rate is emitted.",
            ],
            "warnings": warnings,
        },
    }
