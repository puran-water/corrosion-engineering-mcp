'Tier 2 Tool: microbially influenced corrosion (MIC) mechanism screen.'

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.utils.localized_criteria import sulfide_standing
from corrosion_engineering_mcp.utils.sulfide_chemistry import validate_h2s_gas_composition_ppmv

logger = logging.getLogger(__name__)


# Bibliographic identity returned by OpenAlex's DOI work endpoint on 2026-08-09.
# Keep this structured and user-visible so a wrong author/title cannot hide behind
# a prose claim that the DOI was "machine verified".
_MIC_CITATION_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "doi": "10.1021/acsomega.1c01762",
        "openalex_id": "https://openalex.org/W3161841580",
        "title": (
            "Testing the Test: A Comparative Study of Marine Microbial Corrosion "
            "under Laboratory and Field Conditions"
        ),
        "publication_year": 2021,
        "authors": [
            "Camila Canales", "Carlos Galarce", "Francisca Rubio", "Fabiola Pineda",
            "Javiera Anguita", "Ramón Barros", "Mirtala Parragué", "Leslie K. Daille",
            "Javiera Aguirre", "Francisco Armijo", "Gonzalo Pizarro",
            "Magdalena Walczak", "Rodrigo De la Iglesia", "Sérgio A. Navarrete",
            "Ignacio T. Vargas",
        ],
        "section": "Abstract; Conclusions",
        "content_support": (
            "AISI 316L coupons were exposed for 70 days in parallel running-"
            "seawater laboratory and offshore field systems. The conclusions "
            "state that the laboratory system informs early biofilm development "
            "over days but does not predict stainless-steel deterioration or "
            "biofouling under natural field conditions."
        ),
        "content_limit": (
            "This comparative study supports non-transfer of that laboratory "
            "setup to the field; it does not prove that every MIC experiment or "
            "mechanistic model is non-transferable, nor does it supply a rate "
            "equation for this tool."
        ),
        "content_verified_from": "ACS Omega open full text",
    },
    {
        "doi": "10.1128/aem.02848-13",
        "openalex_id": "https://openalex.org/W2080732846",
        "title": "Corrosion of Iron by Sulfate-Reducing Bacteria: New Views of an Old Problem",
        "publication_year": 2013,
        "authors": ["Dennis Enning", "Julia Garrelfs"],
        "section": "Who's Who in SRB-Mediated Corrosion; EMIC versus CMIC",
        "content_support": (
            "The review distinguishes chemical MIC, in which organotrophic SRB "
            "use organic electron donors and corrosive sulfide is the principal "
            "agent, from electrical MIC, in which specialized SRB take electrons "
            "directly from metallic iron and can corrode without organic donors."
        ),
        "content_limit": (
            "The review establishes mechanism classes, not a transferable VFA, "
            "sulfate, sulfide, ORP, gene-abundance, or corrosion-rate threshold."
        ),
        "content_verified_from": "Applied and Environmental Microbiology open full text",
    },
    {
        "doi": "10.1016/S0010-938X(99)00071-2",
        "openalex_id": "https://openalex.org/W2049488807",
        "title": "Manganese dioxide as a potential cathodic reactant in corrosion of stainless steels",
        "publication_year": 2000,
        "authors": ["B.H Olesen", "Recep Avci", "Zbigniew Lewandowski"],
        "section": "Abstract; 3.1 Laboratory ennobled samples; 5. Conclusion",
        "content_support": (
            "Leptothrix discophora SP-6 biofilms grown on 316L ennobled OCP "
            "to about +410 mV_SCE; XPS identified the biologically deposited "
            "mineral as MnO2; the conclusion says biomineralised MnO2 as a "
            "cathodic reactant may increase corrosion rate or active-corrosion "
            "probability."
        ),
        "content_limit": (
            "The study does not make bulk aerobicity, dissolved manganese, "
            "dissolved oxygen, or one OCP reading a diagnostic threshold; its "
            "load-bearing evidence is the observed biofilm/MnO2/OCP chain."
        ),
        "content_verified_from": (
            "Montana State University ScholarWorks open full-text paper"
        ),
    },
)

# Accepted provenance for an OBSERVED rate. A rate with no method and no window
# is a rumour: "0.3 mm/y" from an unrecorded technique over an unrecorded period
# cannot be compared with anything.
ACCEPTED_RATE_METHODS = frozenset({
    "coupon_mass_loss", "ER_probe", "LPR", "UT_thickness", "pit_depth_gauge",
})

# Alloys with no passive film and no barrier, for which an unresolved MIC screen
# in a supporting environment is a disqualification rather than an open question.
_UNPROTECTED_FERROUS = frozenset({"carbon_steel", "low_alloy_steel", "cast_iron"})

# Strings that DECLARE the absence of a lining. Treating these as a lining is
# how a disqualification gets bypassed by saying so plainly.
_NO_PROTECTION_STRINGS = frozenset({
    "none", "no", "nil", "n/a", "na", "bare", "uncoated", "unlined", "-",
})


class ObservedRateProvenanceError(CorrosionPublicError, ValueError):
    """An observed corrosion rate was supplied without a method and a window."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            "OBSERVED_RATE_PROVENANCE_REQUIRED",
            f"OBSERVED_RATE_PROVENANCE_REQUIRED: {detail} An observed rate "
            f"without a measurement method and an exposure window cannot be "
            f"compared with anything - a coupon average over three years and a "
            f"pit-depth maximum over three months are different quantities that "
            f"share a unit.",
            details={
                "reason_code": "OBSERVED_RATE_PROVENANCE_REQUIRED",
                "accepted_methods": sorted(ACCEPTED_RATE_METHODS),
            },
        )


class DsrABMeasurementContextError(CorrosionPublicError, ValueError):
    """A qPCR abundance was supplied without its metrology."""

    def __init__(self, missing: List[str]) -> None:
        super().__init__(
            "DSRAB_MEASUREMENT_CONTEXT_REQUIRED",
            "DSRAB_MEASUREMENT_CONTEXT_REQUIRED: a sessile dsrAB abundance is "
            "reported only as a measurement. Supply the assay/laboratory method "
            "reference and the quantitative uncertainty; no abundance threshold "
            "is used to diagnose MIC.",
            details={
                "reason_code": "DSRAB_MEASUREMENT_CONTEXT_REQUIRED",
                "missing": missing,
            },
        )


@dataclass
class MechanismVerdict:
    """One MIC mechanism, its standing, and what would settle it."""

    mechanism: str
    standing: str  # possible | unsupported | indeterminate
    supporting_evidence: List[str] = field(default_factory=list)
    # Evidence that removes an argument AGAINST a mechanism without being an
    # argument for it. Kept separate so it cannot promote a mechanism to
    # "possible" on its own.
    non_exonerating_evidence: List[str] = field(default_factory=list)
    contrary_evidence: List[str] = field(default_factory=list)
    missing_measurements: List[str] = field(default_factory=list)
    reported_measurements: Dict[str, Any] = field(default_factory=dict)
    basis: str = ""


def _reported_numeric(
    value: float,
    units: str,
    interpretation: str,
    *,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a numeric MIC measurement without turning it into a decision."""

    return {
        "value": value,
        "units": units,
        "interpretation_standing": "reported_not_thresholded",
        "interpretation": interpretation,
        "source": source,
        "decision_threshold_applied": False,
        "decision_effect": "no_mechanism_or_material_verdict",
    }


def _finalize_standing(verdict: MechanismVerdict, missing: List[str]) -> None:
    """Apply the three-state categorical model; numeric context never enters.

    ``possible`` requires supporting categorical evidence. ``unsupported`` is
    contrary-only. ``indeterminate`` has neither. A future conflict containing
    both sides remains possible (the mechanism has affirmative evidence), but
    cannot be promoted beyond that. ``supported`` is absent by design because
    the current interface accepts no two independent, source-verified diagnostic
    families for any one mechanism.
    """

    verdict.missing_measurements = missing
    if verdict.supporting_evidence:
        verdict.standing = "possible"
    elif verdict.contrary_evidence:
        verdict.standing = "unsupported"
    else:
        verdict.standing = "indeterminate"


def _srb(
    ORP_mV: Optional[float],
    ORP_reference_electrode: Optional[str],
    ORP_uncertainty_mV: Optional[float],
    sulfate_mg_L: Optional[float],
    sulfide_mg_L: Optional[float],
    VFA_mg_L: Optional[float],
    aerobic: Optional[bool],
    deposits_present: Optional[bool],
    dsrAB_copies_per_cm2: Optional[float],
    dsrAB_measurement_reference: Optional[str],
    dsrAB_uncertainty_copies_per_cm2: Optional[float],
) -> MechanismVerdict:
    """Sulfate-reducing bacteria, covering both the classical (CMIC) and the
    iron-consuming (EMIC) routes. [EG13]"""
    v = MechanismVerdict(
        mechanism="sulfate_reduction_SRB",
        standing="indeterminate",
        basis="Enning & Garrelfs 2014, doi:10.1128/aem.02848-13",
    )
    missing: List[str] = []

    anoxic_signal = False
    if ORP_mV is not None:
        # No decision threshold is applied.  Bulk ORP depends on the reference
        # electrode and does not establish the redox state inside a deposit or
        # biofilm.  The previous +/-100 mV house cutoffs had no primary-source
        # basis and could flip the mechanism standing on a 2 mV change.
        v.reported_measurements.update({
            "bulk_ORP_mV": ORP_mV,
            "reference_electrode": ORP_reference_electrode,
            "measurement_uncertainty_mV": ORP_uncertainty_mV,
            "uncertainty_standing": (
                "reported" if ORP_uncertainty_mV is not None else "not_reported"
            ),
            "decision_threshold_applied": False,
        })
    else:
        missing.append("bulk ORP (mV, with reference electrode)")

    if deposits_present:
        v.supporting_evidence.append(
            "Deposits/biofilm reported. The relevant environment is under the "
            "deposit, not in the bulk - an aerobic bulk stream routinely carries "
            "anoxic sites capable of supporting SRB."
        )
        anoxic_signal = True
    elif deposits_present is None:
        missing.append("presence of deposits, biofilm, scale or sludge on the surface")

    # Sulfate is the substrate and sulfide is a possible product, but no
    # validated concentration boundary on this server turns their reported
    # values into an MIC diagnosis.  The former 0.1 mg/L sulfide and 5/20 mg/L
    # sulfate house cutoffs changed the mechanism and material verdicts.  Keep
    # both measurements and the shared continuous-effect sulfide grade visible,
    # but let neither cross a decision edge.
    if sulfate_mg_L is None:
        missing.append("sulfate (mg/L) - the SRB substrate, not the product")
    if sulfide_mg_L is None:
        missing.append("dissolved sulfide (mg/L) - the SRB product")
    else:
        v.reported_measurements["sulfate_sulfide_context"] = {
            "sulfate_mg_L": sulfate_mg_L,
            "dissolved_sulfide_mg_L": sulfide_mg_L,
            "sulfide_standing": sulfide_standing(sulfide_mg_L),
            "interpretation": (
                "substrate_and_product_context_reported"
                if sulfate_mg_L is not None
                else "product_reported_substrate_unmeasured"
            ),
            "decision_threshold_applied": False,
            "decision_effect": "no_mechanism_or_material_verdict",
        }

    # The discriminator that is routinely inverted.
    #
    # Note what low VFA is and is not. It is a NON-EXONERATION: it removes an
    # argument against SRB. It is not by itself evidence FOR them, because SRB
    # still need sulfate and an anoxic site, and low organic carbon is the
    # ordinary condition of most clean water. Filing it as supporting evidence
    # let `screen_mic(material="carbon_steel", VFA_mg_L=0)` return SRB
    # "supported" and disqualify the material on a single absent prerequisite,
    # with no ORP, sulfate, sulfide, deposit or dsrAB data at all.
    if VFA_mg_L is not None:
        v.reported_measurements["VFA"] = _reported_numeric(
            VFA_mg_L,
            "mg/L",
            "Organic donor context. Enning & Garrelfs establish CMIC and EMIC "
            "routes, but no transferable VFA concentration cutoff diagnoses "
            "either route. A low value does not exonerate SRB and a high value "
            "does not establish an active population.",
            source="Enning & Garrelfs 2014, doi:10.1128/aem.02848-13",
        )
        v.non_exonerating_evidence.append(
            f"Reported VFA {VFA_mg_L:g} mg/L neither establishes nor excludes "
            "CMIC/EMIC; no concentration threshold is applied."
        )
    else:
        missing.append("VFA or readily-degradable organic carbon (mg/L)")

    if dsrAB_copies_per_cm2 is not None:
        # No sourced qPCR abundance threshold was found for diagnosing MIC.
        # The measurement therefore travels with its assay reference and
        # uncertainty but contributes to neither evidence side and cannot alter
        # mechanism or material standing by crossing an arbitrary copy count.
        v.reported_measurements["sessile_dsrAB_qpcr"] = {
            "copies_per_cm2": dsrAB_copies_per_cm2,
            "measurement_reference": dsrAB_measurement_reference,
            "measurement_uncertainty_copies_per_cm2": (
                dsrAB_uncertainty_copies_per_cm2
            ),
            "interpretation_standing": "reported_not_thresholded",
            "decision_threshold_applied": False,
        }
    else:
        missing.append(
            "sessile dsrAB gene copies per cm2 (surface, not bulk water) - the "
            "single most discriminating measurement available"
        )

    # Omitted is not false.  Only an explicit declaration that the surface is
    # deposit-free can oppose the under-deposit/anoxic SRB route.
    if aerobic is True and deposits_present is False and not anoxic_signal:
        v.contrary_evidence.append(
            "Declared fully aerobic with no deposits reported."
        )

    _finalize_standing(v, missing)
    return v


def _ennoblement(
    dissolved_O2_mg_L: Optional[float],
    Mn_mg_L: Optional[float],
    Fe_mg_L: Optional[float],
    OCP_ennoblement_mV: Optional[float],
    aerobic: Optional[bool],
    manganese_oxidising_biofilm_present: Optional[bool],
    biomineralised_MnO2_present: Optional[bool],
    OCP_ennoblement_trajectory_observed: Optional[bool],
) -> MechanismVerdict:
    """Manganese/iron-oxidising ennoblement of passive alloys. [OAL00]"""
    v = MechanismVerdict(
        mechanism="biomineralised_oxide_ennoblement",
        standing="indeterminate",
        basis="Olesen, Avci & Lewandowski 2000, doi:10.1016/S0010-938X(99)00071-2",
    )
    missing: List[str] = []

    if dissolved_O2_mg_L is not None:
        v.reported_measurements["dissolved_oxygen"] = _reported_numeric(
            dissolved_O2_mg_L,
            "mg/L",
            "Reported oxygen concentration; no universal concentration cutoff "
            "establishes the oxygen state inside a biofilm or deposit.",
        )
    else:
        missing.append("dissolved oxygen at the surface (mg/L)")

    if aerobic is False:
        v.contrary_evidence.append(
            "Surface state was explicitly declared anoxic. The mechanism requires "
            "oxygen to biomineralise Mn(II) to MnO2. This does not clear SRB."
        )
    elif aerobic is True:
        v.non_exonerating_evidence.append(
            "Surface state was explicitly declared aerobic. Oxygen is a prerequisite "
            "for manganese oxidation, not evidence that an ennobling biofilm, MnO2, "
            "or an OCP trajectory is present."
        )
    else:
        missing.append("declared aerobic/anoxic state at the surface")

    if Mn_mg_L is not None:
        v.reported_measurements["dissolved_manganese"] = _reported_numeric(
            Mn_mg_L,
            "mg/L",
            "Olesen, Avci & Lewandowski identify biomineralised MnO2 as a "
            "cathodic reactant on stainless, but do not provide a transferable "
            "dissolved-Mn diagnostic cutoff.",
            source="Olesen, Avci & Lewandowski 2000, doi:10.1016/S0010-938X(99)00071-2",
        )
    elif Mn_mg_L is None:
        missing.append("dissolved manganese (mg/L)")

    if Fe_mg_L is not None:
        v.reported_measurements["dissolved_iron"] = _reported_numeric(
            Fe_mg_L,
            "mg/L",
            "Reported iron context; this server has no sourced concentration "
            "cutoff for diagnosing an iron-oxidising tubercle mechanism.",
        )
    elif Fe_mg_L is None:
        missing.append("dissolved iron (mg/L)")

    if OCP_ennoblement_mV is not None:
        v.reported_measurements["OCP_shift"] = _reported_numeric(
            OCP_ennoblement_mV,
            "mV",
            "Reported open-circuit-potential shift. A trajectory and matched "
            "surface evidence are needed; no sourced single-shift cutoff is used.",
        )
    else:
        missing.append(
            "open-circuit potential trend over weeks (mV) - ennoblement is "
            "defined by the trajectory, so a single reading cannot show it"
        )

    # Olesen, Avci & Lewandowski's evidence chain is specific: a
    # manganese-oxidising Leptothrix biofilm, biomineralised MnO2 identified on
    # the stainless surface, and an OCP trajectory.  Bulk aerobicity is only the
    # prerequisite for that chain.  Keep the categorical observations separate
    # from the optional single numeric OCP shift above.
    categorical = {
        "manganese_oxidising_biofilm_present": (
            manganese_oxidising_biofilm_present
        ),
        "biomineralised_MnO2_present": biomineralised_MnO2_present,
        "OCP_ennoblement_trajectory_observed": (
            OCP_ennoblement_trajectory_observed
        ),
    }
    reported_categorical = {
        name: observed
        for name, observed in categorical.items()
        if observed is not None
    }
    if reported_categorical:
        v.reported_measurements["olesen_categorical_evidence"] = {
            **reported_categorical,
            "standing": "reported_categorical",
            "evidence_required_for_possible": "all_three_true",
            "source": (
                "Olesen, Avci & Lewandowski 2000, "
                "doi:10.1016/S0010-938X(99)00071-2"
            ),
        }
    for name, observed in categorical.items():
        if observed is None:
            missing.append(name)
        elif observed is False:
            v.contrary_evidence.append(f"{name} was explicitly reported false.")

    if all(observed is True for observed in categorical.values()):
        v.supporting_evidence.append(
            "A manganese-oxidising biofilm, biomineralised MnO2, and an OCP "
            "ennoblement trajectory were all explicitly observed; this is the "
            "categorical evidence chain used by Olesen, Avci & Lewandowski."
        )
    elif any(observed is True for observed in categorical.values()):
        v.non_exonerating_evidence.append(
            "Part of the Olesen categorical evidence chain was reported, but the "
            "biofilm, MnO2, and OCP trajectory were not all observed."
        )

    _finalize_standing(v, missing)
    return v


def _apb(
    VFA_mg_L: Optional[float],
    interface_pH: Optional[float],
    bulk_pH: Optional[float],
    alkalinity_mg_L: Optional[float],
    acid_producing_biofilm_present: Optional[bool],
) -> MechanismVerdict:
    """Acid-producing-biofilm categorical evidence screen."""
    v = MechanismVerdict(
        mechanism="acid_producing_bacteria",
        standing="indeterminate",
        basis=(
            "Categorical surface observation contract; numeric APB diagnostic "
            "thresholds and rate prediction are intentionally not claimed"
        ),
    )
    missing: List[str] = []

    if interface_pH is not None and bulk_pH is not None:
        drop = bulk_pH - interface_pH
        v.reported_measurements["interface_bulk_pH"] = {
            "interface_pH": interface_pH,
            "bulk_pH": bulk_pH,
            "observed_difference_pH_units": drop,
            "interpretation_standing": "reported_not_thresholded",
            "decision_threshold_applied": False,
            "decision_effect": "no_mechanism_or_material_verdict",
        }
    elif interface_pH is not None:
        v.reported_measurements["interface_pH"] = {
            "value": interface_pH,
            "units": "pH",
            "interpretation_standing": (
                "reported_incomplete_differential_requires_bulk_pH"
            ),
            "interpretation": (
                "The supplied interface pH is retained, but an interface-to-bulk "
                "pH differential requires both interface_pH and bulk_pH."
            ),
            "decision_threshold_applied": False,
            "decision_effect": "no_mechanism_or_material_verdict",
        }
        missing.append(
            "bulk pH paired with the supplied interface pH; both are required "
            "to calculate the interface-to-bulk pH differential"
        )
    elif bulk_pH is not None:
        v.reported_measurements["bulk_pH"] = {
            "value": bulk_pH,
            "units": "pH",
            "interpretation_standing": (
                "reported_incomplete_differential_requires_interface_pH"
            ),
            "interpretation": (
                "The supplied bulk pH is retained, but an interface-to-bulk pH "
                "differential requires both interface_pH and bulk_pH."
            ),
            "decision_threshold_applied": False,
            "decision_effect": "no_mechanism_or_material_verdict",
        }
        missing.append(
            "pH AT THE INTERFACE, by microelectrode or pH-sensitive film. Bulk pH "
            "does not resolve this mechanism"
        )
    else:
        missing.append(
            "pH AT THE INTERFACE, by microelectrode or pH-sensitive film. Bulk pH "
            "does not resolve this mechanism"
        )

    if VFA_mg_L is not None:
        v.reported_measurements["VFA"] = _reported_numeric(
            VFA_mg_L,
            "mg/L",
            "Reported substrate context; no sourced APB diagnostic cutoff is used.",
        )
    else:
        missing.append("VFA (mg/L)")

    if alkalinity_mg_L is not None:
        v.reported_measurements["alkalinity"] = _reported_numeric(
            alkalinity_mg_L,
            "mg/L as CaCO3",
            "Reported buffer capacity; no sourced single-value APB cutoff is used.",
        )
    else:
        missing.append("alkalinity (mg/L as CaCO3)")

    if acid_producing_biofilm_present is not None:
        v.reported_measurements["acid_producing_biofilm_present"] = {
            "value": acid_producing_biofilm_present,
            "standing": "reported_categorical",
        }
    if acid_producing_biofilm_present is True:
        v.supporting_evidence.append(
            "An acid-producing biofilm was explicitly identified on the surface."
        )
    elif acid_producing_biofilm_present is False:
        v.contrary_evidence.append(
            "An acid-producing biofilm was explicitly ruled out on the surface."
        )
    else:
        missing.append("categorical surface identification of an acid-producing biofilm")

    _finalize_standing(v, missing)
    return v


def _sob(
    headspace_H2S_ppmv: Optional[float],
    relative_humidity_pct: Optional[float],
    surface_pH: Optional[float],
    sulfur_oxidising_biofilm_present: Optional[bool],
) -> MechanismVerdict:
    """Sulfur-oxidising bacteria in a condensing H2S headspace."""
    v = MechanismVerdict(
        mechanism="sulfur_oxidising_bacteria_headspace",
        standing="indeterminate",
        basis="Biogenic sulfuric acid route; concrete quantification is out of scope",
    )
    missing: List[str] = []

    if headspace_H2S_ppmv is not None:
        v.reported_measurements["headspace_H2S"] = _reported_numeric(
            headspace_H2S_ppmv,
            "ppmv",
            "Reported sulfur-substrate context; no universal diagnostic cutoff is used.",
        )
    else:
        missing.append("headspace H2S (ppmv)")

    if relative_humidity_pct is not None:
        v.reported_measurements["relative_humidity"] = _reported_numeric(
            relative_humidity_pct,
            "%",
            "Reported bulk headspace RH. It does not establish local surface "
            "condensation and no universal activity cutoff is applied.",
        )
    else:
        missing.append("headspace relative humidity (%)")

    if surface_pH is not None:
        v.reported_measurements["surface_pH"] = _reported_numeric(
            surface_pH,
            "pH",
            "Reported surface measurement. A trajectory plus organism/oxidation "
            "evidence is needed; no single-pH diagnostic cutoff is used.",
        )
    else:
        missing.append(
            "surface pH trajectory over time - the succession from neutrophilic to "
            "acidophilic oxidisers is what the trajectory shows"
        )

    if sulfur_oxidising_biofilm_present is not None:
        v.reported_measurements["sulfur_oxidising_biofilm_present"] = {
            "value": sulfur_oxidising_biofilm_present,
            "standing": "reported_categorical",
        }
    if sulfur_oxidising_biofilm_present is True:
        v.supporting_evidence.append(
            "A sulfur-oxidising biofilm was explicitly identified on the surface."
        )
    elif sulfur_oxidising_biofilm_present is False:
        v.contrary_evidence.append(
            "A sulfur-oxidising biofilm was explicitly ruled out on the surface."
        )
    else:
        missing.append("categorical surface identification of a sulfur-oxidising biofilm")

    _finalize_standing(v, missing)
    return v


@enforce_tool_input_contract("corrosion_screen_mic")
def screen_mic(
    material: str,
    aerobic: Optional[bool] = None,
    ORP_mV: Optional[float] = None,
    ORP_reference_electrode: Optional[str] = None,
    ORP_uncertainty_mV: Optional[float] = None,
    dissolved_O2_mg_L: Optional[float] = None,
    sulfate_mg_L: Optional[float] = None,
    sulfide_mg_L: Optional[float] = None,
    VFA_mg_L: Optional[float] = None,
    alkalinity_mg_L: Optional[float] = None,
    bulk_pH: Optional[float] = None,
    interface_pH: Optional[float] = None,
    Mn_mg_L: Optional[float] = None,
    Fe_mg_L: Optional[float] = None,
    OCP_ennoblement_mV: Optional[float] = None,
    manganese_oxidising_biofilm_present: Optional[bool] = None,
    biomineralised_MnO2_present: Optional[bool] = None,
    OCP_ennoblement_trajectory_observed: Optional[bool] = None,
    dsrAB_copies_per_cm2: Optional[float] = None,
    dsrAB_measurement_reference: Optional[str] = None,
    dsrAB_uncertainty_copies_per_cm2: Optional[float] = None,
    deposits_present: Optional[bool] = None,
    headspace_H2S_ppmv: Optional[float] = None,
    relative_humidity_pct: Optional[float] = None,
    surface_pH: Optional[float] = None,
    acid_producing_biofilm_present: Optional[bool] = None,
    sulfur_oxidising_biofilm_present: Optional[bool] = None,
    coating_or_lining: Optional[str] = None,
    observed_corrosion_rate_mm_y: Optional[float] = None,
    observed_rate_method: Optional[str] = None,
    observed_rate_window_days: Optional[float] = None,
) -> Dict[str, Any]:
    """Identify which MIC mechanisms the evidence can keep in play. No rate.

    The current interface emits exactly ``possible``, ``unsupported`` and
    ``indeterminate``. Possible needs supporting categorical evidence;
    unsupported is contrary-only; indeterminate has neither. ``supported`` is
    absent by design because no mechanism accepts two independent, source-
    verified non-numeric diagnostic families. Numeric measurements never select
    one of these states. `predicted_mic_rate_mm_y` is null unconditionally.
    """
    validate_float_domain_inputs(locals())

    from corrosion_engineering_mcp.core.materials import MaterialCatalogError, UnknownMaterialError, canonical_id, get_material

    try:
        resolved = canonical_id(material)
        record = get_material(resolved)
    except MaterialCatalogError:
        raise UnknownMaterialError(material) from None

    # The direct Python accessor must be as strict as the MCP schema. NaN
    # otherwise bypasses ordinary comparisons and is then reported as if it
    # were a measurement. Numeric context is non-decisive, but must be real.
    numeric_inputs = {
        "ORP_mV": ORP_mV,
        "ORP_uncertainty_mV": ORP_uncertainty_mV,
        "dissolved_O2_mg_L": dissolved_O2_mg_L,
        "sulfate_mg_L": sulfate_mg_L,
        "sulfide_mg_L": sulfide_mg_L,
        "VFA_mg_L": VFA_mg_L,
        "alkalinity_mg_L": alkalinity_mg_L,
        "bulk_pH": bulk_pH,
        "interface_pH": interface_pH,
        "Mn_mg_L": Mn_mg_L,
        "Fe_mg_L": Fe_mg_L,
        "OCP_ennoblement_mV": OCP_ennoblement_mV,
        "dsrAB_copies_per_cm2": dsrAB_copies_per_cm2,
        "dsrAB_uncertainty_copies_per_cm2": dsrAB_uncertainty_copies_per_cm2,
        "headspace_H2S_ppmv": headspace_H2S_ppmv,
        "relative_humidity_pct": relative_humidity_pct,
        "surface_pH": surface_pH,
        "observed_corrosion_rate_mm_y": observed_corrosion_rate_mm_y,
        "observed_rate_window_days": observed_rate_window_days,
    }
    for field_name, value in numeric_inputs.items():
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{field_name} must be a finite real number")

    validate_h2s_gas_composition_ppmv(
        headspace_H2S_ppmv,
        field="headspace_H2S_ppmv",
    )

    for field_name in (
        "ORP_uncertainty_mV",
        "dissolved_O2_mg_L",
        "sulfate_mg_L",
        "sulfide_mg_L",
        "VFA_mg_L",
        "alkalinity_mg_L",
        "Mn_mg_L",
        "Fe_mg_L",
        "dsrAB_copies_per_cm2",
        "dsrAB_uncertainty_copies_per_cm2",
        "headspace_H2S_ppmv",
        "observed_corrosion_rate_mm_y",
    ):
        value = numeric_inputs[field_name]
        if value is not None and value < 0:
            raise ValueError(f"{field_name} must be non-negative")
    for field_name in ("bulk_pH", "interface_pH", "surface_pH"):
        value = numeric_inputs[field_name]
        if value is not None and not 0.0 <= value <= 14.0:
            raise ValueError(f"{field_name} must be between 0 and 14")
    if (
        relative_humidity_pct is not None
        and not 0.0 <= relative_humidity_pct <= 100.0
    ):
        raise ValueError("relative_humidity_pct must be between 0 and 100")

    categorical_inputs = {
        "aerobic": aerobic,
        "manganese_oxidising_biofilm_present": manganese_oxidising_biofilm_present,
        "biomineralised_MnO2_present": biomineralised_MnO2_present,
        "OCP_ennoblement_trajectory_observed": OCP_ennoblement_trajectory_observed,
        "deposits_present": deposits_present,
        "acid_producing_biofilm_present": acid_producing_biofilm_present,
        "sulfur_oxidising_biofilm_present": sulfur_oxidising_biofilm_present,
    }
    for field_name, value in categorical_inputs.items():
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{field_name} must be true, false, or omitted")

    # An observed rate is admissible only with its provenance.
    if observed_corrosion_rate_mm_y is not None:
        if observed_rate_method is None:
            raise ObservedRateProvenanceError("observed_rate_method was not given.")
        if observed_rate_method not in ACCEPTED_RATE_METHODS:
            raise ObservedRateProvenanceError(
                f"observed_rate_method {observed_rate_method!r} is not a "
                f"recognised measurement technique."
            )
        if observed_rate_window_days is None or observed_rate_window_days <= 0:
            raise ObservedRateProvenanceError(
                "observed_rate_window_days was not given."
            )

    if dsrAB_copies_per_cm2 is not None:
        missing_dsrab_context = []
        if not (dsrAB_measurement_reference or "").strip():
            missing_dsrab_context.append("dsrAB_measurement_reference")
        if dsrAB_uncertainty_copies_per_cm2 is None:
            missing_dsrab_context.append("dsrAB_uncertainty_copies_per_cm2")
        if missing_dsrab_context:
            raise DsrABMeasurementContextError(missing_dsrab_context)

    mechanisms = [
        _srb(
            ORP_mV,
            ORP_reference_electrode,
            ORP_uncertainty_mV,
            sulfate_mg_L,
            sulfide_mg_L,
            VFA_mg_L,
            aerobic,
            deposits_present,
            dsrAB_copies_per_cm2,
            dsrAB_measurement_reference,
            dsrAB_uncertainty_copies_per_cm2,
        ),
        _ennoblement(
            dissolved_O2_mg_L,
            Mn_mg_L,
            Fe_mg_L,
            OCP_ennoblement_mV,
            aerobic,
            manganese_oxidising_biofilm_present,
            biomineralised_MnO2_present,
            OCP_ennoblement_trajectory_observed,
        ),
        _apb(
            VFA_mg_L,
            interface_pH,
            bulk_pH,
            alkalinity_mg_L,
            acid_producing_biofilm_present,
        ),
        _sob(
            headspace_H2S_ppmv,
            relative_humidity_pct,
            surface_pH,
            sulfur_oxidising_biofilm_present,
        ),
    ]

    possible = [m.mechanism for m in mechanisms if m.standing == "possible"]
    unsupported = [m.mechanism for m in mechanisms if m.standing == "unsupported"]
    indeterminate = [m.mechanism for m in mechanisms if m.standing == "indeterminate"]

    warnings: List[str] = []
    cls = record.classification
    # Three states, not a boolean: absent protection data is neither a lining nor
    # a factual declaration that the metal is bare.
    normalized_protection = (coating_or_lining or "").strip()
    if not normalized_protection:
        protection_standing = "not_reported"
        is_protected: Optional[bool] = None
    elif normalized_protection.lower() in _NO_PROTECTION_STRINGS:
        protection_standing = "reported_absent"
        is_protected = False
    else:
        protection_standing = "reported_present"
        is_protected = True

    known_unprotected = cls in _UNPROTECTED_FERROUS and is_protected is False
    protection_unknown = cls in _UNPROTECTED_FERROUS and is_protected is None

    # The rule that stops "possible" from reading as "probably fine".
    if possible:
        if known_unprotected:
            material_verdict = "disqualified_pending_evidence"
            verdict_note = (
                f"{resolved} is unprotected {cls} in a service where MIC is "
                "possible from supporting categorical evidence. That is a "
                f"DISQUALIFICATION pending evidence, not an open question: an "
                f"unresolved MIC screen on bare ferrous metal must not leave the "
                f"specification standing. Clear it by supplying the missing "
                f"measurements below, by lining or coating, or by changing alloy."
            )
        elif protection_unknown:
            material_verdict = "hold_pending_mic_and_protection_evidence"
            verdict_note = (
                f"MIC is possible for {resolved} from supporting categorical evidence, "
                "but coating_or_lining was not reported. The material is NOT "
                "labelled bare and receives no protection credit. Hold the "
                "specification until both the protection state and the missing "
                "MIC evidence are resolved."
            )
        else:
            material_verdict = "elevated_risk_requires_evidence"
            verdict_note = (
                f"MIC mechanisms are in play for {resolved} "
                f"({cls}{', ' + normalized_protection if is_protected else ''}). "
                f"This is not a clearance - resolve the missing measurements."
            )
    elif unsupported and not indeterminate and len(unsupported) == len(mechanisms):
        material_verdict = "no_mechanism_possible_from_the_evidence_supplied"
        verdict_note = (
            "Every screened mechanism is affirmatively contradicted by supplied "
            "categorical evidence. This mechanism-level result reflects only the "
            "reported evidence and service state; it is not a general immunity or "
            "future-service guarantee."
        )
        warnings.append(
            "EVIDENCE-SCOPED RESULT: every mechanism was individually unsupported "
            "by affirmative contrary evidence; changing service or surface evidence "
            "requires a new screen."
        )
    else:
        material_verdict = "indeterminate_insufficient_evidence"
        verdict_note = (
            "No mechanism has supporting categorical evidence, but one or more "
            "mechanisms remain indeterminate because the discriminating evidence "
            "is absent. Insufficient evidence is machine-distinct from affirmative "
            "contradiction; absence is never benign."
        )
        warnings.append(
            "NOT A CLEARANCE: INDETERMINATE_INSUFFICIENT_EVIDENCE. At least one "
            "mechanism lacks affirmative supporting or contrary evidence; absent "
            "measurements cannot establish that MIC is not possible."
        )

    all_missing = sorted({m for mech in mechanisms for m in mech.missing_measurements})
    if len(all_missing) >= 6:
        warnings.append(
            f"{len(all_missing)} discriminating measurements are absent. With this "
            f"little surface data the screen distinguishes mechanisms weakly; the "
            f"output is a list of what to measure more than a diagnosis."
        )

    in_play = set(possible)
    if {"sulfate_reduction_SRB", "biomineralised_oxide_ennoblement"} <= in_play:
        warnings.append(
            "SRB and ennoblement both in play. They cannot coexist at one spot - "
            "one needs oxygen, the other its absence - but they routinely coexist "
            "in one SYSTEM, which is a differential aeration cell with an anoxic "
            "anode under a deposit and a large oxic cathode around it."
        )

    return {
        "material": resolved,
        "material_classification": cls,
        "coating_or_lining": coating_or_lining,
        "protection": {
            "standing": protection_standing,
            "is_protected": is_protected,
            "declared_value": coating_or_lining,
            "credit_applied": is_protected is True,
        },
        "predicted_mic_rate_mm_y": None,
        "evidence_model": {
            "name": "categorical_observation_screen_v2",
            "numeric_measurements_decide_standing": False,
            "numeric_measurement_standing": "reported_not_thresholded",
            "emitted_standings": ["possible", "unsupported", "indeterminate"],
            "possible_requires": (
                "mechanism_specific_categorical_evidence; see mechanism_requirements"
            ),
            "unsupported_requires": (
                "mechanism_specific_contrary_categorical_evidence_without_the_"
                "complete_possible_requirement; see mechanism_requirements"
            ),
            "indeterminate_when": "no_supporting_or_contrary_categorical_observation",
            "supported_state": "not_emitted_by_design",
            "supported_state_reason": (
                "current_interface_accepts_no_two_independent_source_verified_"
                "non_numeric_diagnostic_families_for_any_one_mechanism"
            ),
            "mechanism_reachability": {
                "sulfate_reduction_SRB": [
                    "possible", "unsupported", "indeterminate"
                ],
                "biomineralised_oxide_ennoblement": [
                    "possible", "unsupported", "indeterminate"
                ],
                "acid_producing_bacteria": [
                    "possible", "unsupported", "indeterminate"
                ],
                "sulfur_oxidising_bacteria_headspace": [
                    "possible", "unsupported", "indeterminate"
                ],
            },
            "mechanism_requirements": {
                "sulfate_reduction_SRB": {
                    "possible_requires": "deposits_present=true",
                    "unsupported_requires": (
                        "aerobic=true AND deposits_present=false"
                    ),
                },
                "biomineralised_oxide_ennoblement": {
                    "possible_requires": (
                        "all_three_true: manganese_oxidising_biofilm_present, "
                        "biomineralised_MnO2_present, "
                        "OCP_ennoblement_trajectory_observed"
                    ),
                    "unsupported_requires": (
                        "at_least_one_of_the_three_explicitly_false_without_all_"
                        "three_true"
                    ),
                    "aerobic": "precondition_context_not_evidence",
                },
                "acid_producing_bacteria": {
                    "possible_requires": "acid_producing_biofilm_present=true",
                    "unsupported_requires": "acid_producing_biofilm_present=false",
                },
                "sulfur_oxidising_bacteria_headspace": {
                    "possible_requires": "sulfur_oxidising_biofilm_present=true",
                    "unsupported_requires": "sulfur_oxidising_biofilm_present=false",
                },
            },
        },
        "why_no_predicted_rate": (
            "No model converts routinely measurable conditions into independently "
            "validated metal loss with calibrated uncertainty. Canales et al. 2021 "
            "(doi:10.1021/acsomega.1c01762) ran 316L in parallel in the laboratory "
            "and the field and found the laboratory data 'does not predict "
            "deterioration or biofouling of SS surfaces exposed to natural "
            "conditions in the field'. Every candidate model was validated only "
            "against its own calibration set."
        ),
        "material_verdict": material_verdict,
        "verdict_note": verdict_note,
        "mechanisms_possible": possible,
        "mechanisms_unsupported": unsupported,
        "mechanisms_indeterminate": indeterminate,
        "mechanisms": [
            {
                "mechanism": m.mechanism,
                "standing": m.standing,
                "supporting_evidence": m.supporting_evidence,
                "non_exonerating_evidence": m.non_exonerating_evidence,
                "contrary_evidence": m.contrary_evidence,
                "missing_measurements": m.missing_measurements,
                "reported_measurements": m.reported_measurements,
                "basis": m.basis,
            }
            for m in mechanisms
        ],
        "missing_measurements": all_missing,
        "observed_corrosion_rate": (
            {
                "rate_mm_y": observed_corrosion_rate_mm_y,
                "method": observed_rate_method,
                "window_days": observed_rate_window_days,
                "note": (
                    "An OBSERVED rate. It is not attributed to MIC by this tool - "
                    "attribution needs the surface evidence above, and abiotic "
                    "mechanisms produce the same number."
                ),
            }
            if observed_corrosion_rate_mm_y is not None
            else None
        ),
        "warnings": warnings,
        "provenance": {
            "model": "mic_mechanism_screen",
            "version": "1.0",
            "validation_dataset": None,
            "confidence": "mechanism_identification_only",
            "sources": [
                f"{record['title']}, doi:{record['doi']}"
                for record in _MIC_CITATION_RECORDS
            ],
            "citation_records": [
                {
                    **record,
                    "authors": list(record["authors"]),
                }
                for record in _MIC_CITATION_RECORDS
            ],
            "citation_metadata_source": "OpenAlex",
            "citation_metadata_retrieved_on": "2026-08-09",
            "citation_verification_scope": (
                "author_title_and_load-bearing_sections_read_from_open_full_text_"
                "for_Canales_Enning_and_Olesen"
            ),
            "assumptions": [
                "Mechanism standing is derived from evidence supplied, so it "
                "describes the evidence and not the service.",
                "No rate is predicted under any input.",
            ],
            "warnings": warnings,
        },
    }
