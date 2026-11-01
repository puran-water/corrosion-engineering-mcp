"""Tier 2 Tool: anaerobic / sulfide exposure screen.

WHAT THIS ANSWERS
-----------------
"There is sulfide in this stream. Do the chloride and PREN numbers I have still
mean anything, and what else is now in play?"

That framing matters. The failure this tool exists to prevent is not an
under-estimated rate - it is a chloride/PREN assessment being quoted for a
service the chloride/PREN models do not describe. Sulfide does not simply add
another aggressive anion to an oxide film; it REPLACES the film. Once the
surface is FeS/NiS rather than Cr2O3, PREN is a number about a layer that is no
longer there.

The load-bearing output is therefore an evidence grade with its source standing
and uncertainty. It is deliberately not a yes/no gate: the on-point 316L study
reports continuous degradation and identifies no critical concentration.

THE THREE OUTPUTS, WHICH DO NOT HAVE EQUAL STANDING
---------------------------------------------------
Deliberately kept apart in the payload, as in the R2.1 oxidant tool:

  1. speciation                 HIGH confidence only within its temperature,
                                ionic-strength and pH scope. Above pH 12 it is
                                explicitly reduced by a disclosed internal rule
                                because the shipped legacy S2- model and May et
                                al.'s abstract-backed recommendation diverge.
  2. passivity_standing         QUALITATIVE. <0.1 / 0.1-48 / >=48 mg/L grades,
                                source status and uncertainty; no verdict.
  3. sulfate_inhibition         QUALITATIVE CONTEXT. The measured bulk molar
                                ratio is reported beside Galvele's one mixed-
                                electrolyte calculation; no threshold or credit.

WHAT IT NEVER EMITS
-------------------
A corrosion rate. There is no open validated sulfide rate model for these
conditions - the same reason corrosion_predict_co2 rejects any declared H2S and
the aerated-chloride tool was withdrawn entirely.

FeS FILM PROPERTY STANDING
--------------------------
The payload can report whether the declared dissolved iron and sulfide clear its
explicit film-assertion reporting floor. No returned, content-verified source
establishes an electrical, galvanic, or protective property for that film in the
scoped steel service, so those properties and any resulting attack prediction
are not assessed.

SOURCES
-------
[G76]  J. R. Galvele, J. Electrochem. Soc. 123(4) 464 (1976).
       doi:10.1149/1.2132857 - sulfate inhibition of chloride pitting.
[P21]  Parangusan, Bhadra & Al-Thani, Emergent Materials 4, 1187 (2021).
       doi:10.1007/s42247-021-00194-6 (open access) - sulfide destruction of the
       passive film; FeS / NiS corrosion products.
[PQC]  phreeqc.dat thermodynamics, machine-read. See utils/sulfide_chemistry.
"""

from __future__ import annotations

import logging
import math
from numbers import Real
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.chemistry_backend import PHREEQCBackend, PHREEQCSaturationIndexSentinelError
from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract
from corrosion_engineering_mcp.core.phreeqc_runtime import PHREEQCBackendUnavailableError
from corrosion_engineering_mcp.utils.localized_criteria import PARANGUSAN_SULFIDE_REVIEW_CITATION_RECORD, SULFIDE_316L_CITATION_RECORD, sulfate_chloride_molar_ratio, sulfide_standing
from corrosion_engineering_mcp.utils.sulfide_chemistry import HIGH_PH_SPECIATION_SCOPE_RULE, HENRY_H2S_MOL_PER_L_ATM_25C, HENRY_H2S_PHREEQC_ANALYTIC_25C, HENRY_H2S_REPORTED_RANGE_25C, MAY_S2_AQUEOUS_CITATION_RECORD, SANDER_H2S_HENRY_CITATION_RECORD, SulfideInputOutOfRangeError, _equilibrium_headspace_from_validated_dilute_liquid, dissolved_sulfide_from_headspace, equilibrium_headspace_H2S_ppmv, speciate_dissolved_sulfide, validate_h2s_gas_composition_ppmv

logger = logging.getLogger(__name__)

# Stainless families for which the internal sulfide convention was originally
# framed.  It is not extended to titanium or nickel alloys: those oxide systems
# need class-specific primary evidence that this server does not hold.
_CHROMIUM_OXIDE_PASSIVE_CLASSES = frozenset({
    "austenitic_stainless",
    "duplex_stainless",
    "super_duplex_stainless",
    "superaustenitic_stainless",
    "ferritic_stainless",
    "martensitic_stainless",
})

_OTHER_PASSIVE_CLASSES = frozenset({"nickel_alloy", "titanium"})

# Alloys that never had a passive film to lose. Sulfide still matters, by a
# different mechanism, and saying "the gate does not apply" must not read as
# "no problem".
_ACTIVE_CLASSES = frozenset({"carbon_steel", "low_alloy_steel", "cast_iron"})

# A concentration that rounds to zero in this payload cannot support a positive
# film assertion.  This is an output-resolution floor, not a claimed laboratory
# detection limit or a thermodynamic solubility threshold.
FILM_ASSERTION_FLOOR_MG_L = 1.0e-4

DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION = (
    "DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION"
)

_FES_SATURATION_SOURCE = {
    "authors": ["U.S. Geological Survey PHREEQC database maintainers"],
    "title": "phreeqc.dat thermodynamic database",
    "section": "PHASES entries FeS(ppt) and Mackinawite",
    "content_support": (
        "Both entries define FeS + H+ = Fe+2 + HS-; phreeqc.dat gives "
        "log_k -3.915 for FeS(ppt) and -4.648 for Mackinawite. This accessor "
        "uses the resulting saturation indices, with SI >= 0 treated as "
        "thermodynamic FeS precipitation tendency."
    ),
    "content_limit": (
        "A saturation index is an equilibrium thermodynamic result. It does not "
        "establish that an adherent film was observed, its formation kinetics, "
        "or any electrical, galvanic, protective, or local-attack property."
    ),
    "content_verified_from": "bundled phreeqpython phreeqc.dat PHASES section",
}

_GALVELE_SULFATE_CONTEXT_SOURCE = {
    "authors": ["José R. Galvele"],
    "title": "Transport Processes and the Mechanism of Pitting of Metals",
    "doi": "10.1149/1.2132857",
    "section": "mixed Na2SO4/NaCl calculation, p. 473",
    "content_support": (
        "The calculation starts with bulk 0.1 M Na2SO4 and 0.01 M NaCl "
        "(10:1), predicts sulfate enrichment inside a growing pit to about "
        "100:1 or higher, and describes eventual repassivation."
    ),
    "content_limit": (
        "It supplies no bulk 100:1 boundary, interpolation rule, or "
        "chloride-threshold multiplier."
    ),
}


class SulfideInputUnderdeterminedError(CorrosionPublicError, ValueError):
    """Neither a liquid sulfide nor a headspace H2S measurement was supplied.

    Absent is not zero. A stream with no sulfide measurement is not a stream
    with no sulfide, and for anaerobic service the difference decides which
    models apply at all.
    """

    def __init__(self) -> None:
        super().__init__(
            "SULFIDE_INPUT_REQUIRED",
            "SULFIDE_INPUT_REQUIRED: supply total_sulfide_mg_L (liquid) or "
            "H2S_headspace_ppmv (gas). Absent is not zero - an unmeasured "
            "anaerobic stream is the case this screen exists for, and "
            "defaulting it to zero would return a clean verdict for exactly "
            "the service that needs one least.",
            details={
                "reason_code": "SULFIDE_INPUT_REQUIRED",
                "accepted": ["total_sulfide_mg_L", "H2S_headspace_ppmv"],
            },
        )


def _henry_uncertainty_ranges(
    *,
    liquid_implied_headspace_ppmv: float,
    headspace_implied_total_sulfide_mg_L: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Propagate the carried Sander-2023 H2S measured-anchor band both ways."""

    low_henry, high_henry = HENRY_H2S_REPORTED_RANGE_25C
    liquid_to_gas = (
        liquid_implied_headspace_ppmv
        * HENRY_H2S_MOL_PER_L_ATM_25C
        / high_henry,
        liquid_implied_headspace_ppmv
        * HENRY_H2S_MOL_PER_L_ATM_25C
        / low_henry,
    )
    gas_to_liquid = (
        headspace_implied_total_sulfide_mg_L
        * low_henry
        / HENRY_H2S_MOL_PER_L_ATM_25C,
        headspace_implied_total_sulfide_mg_L
        * high_henry
        / HENRY_H2S_MOL_PER_L_ATM_25C,
    )
    return liquid_to_gas, gas_to_liquid


def _displayed_headspace_range(
    limits: tuple[float, float],
) -> tuple[float, float]:
    """Expose the exact executable bounds using JSON-round-trippable floats.

    Python/JSON float serialization preserves enough significant digits to
    round-trip a binary float. Fixed-decimal outward rounding created a 0.1 ppmv
    acceptance dead zone when the physical upper bound was merely trace-sized.
    """

    low, high = limits
    return (
        min(1_000_000.0, max(0.0, low)),
        min(1_000_000.0, max(0.0, high)),
    )


def _inside_closed_range(value: float, limits: tuple[float, float]) -> bool:
    """Use the exact JSON-round-trippable floats exposed in the payload."""

    low, high = limits
    return low <= value <= high


def _format_signed_nonzero(value: float, decimal_places: int) -> str:
    """Use scientific notation when fixed-point would invent numeric zero."""

    if value != 0.0 and round(value, decimal_places) == 0.0:
        return f"{value:+.3e}"
    return f"{value:+.{decimal_places}f}"


def _calculate_fes_saturation(
    *,
    dissolved_Fe_mg_L: float,
    total_sulfide_mg_L: float,
    pH: float,
    temperature_C: float,
    ionic_strength_M: float,
) -> Dict[str, float]:
    """Return phreeqc.dat FeS saturation indices for the declared solution.

    The public ionic-strength input has no full ion analysis behind it. When it
    is nonzero, represent it explicitly as an equimolar NaCl background so the
    PHREEQC activity correction has the same disclosed 1:1-electrolyte basis as
    the sulfide speciation screen.
    """
    solution: Dict[str, Any] = {
        "units": "mg/L",
        "temp": temperature_C,
        "pH": pH,
        "Fe(2)": dissolved_Fe_mg_L,
        "S(-2)": total_sulfide_mg_L,
    }
    if ionic_strength_M > 0.0:
        solution["Na"] = ionic_strength_M * 22.98976928 * 1000.0
        solution["Cl"] = ionic_strength_M * 35.453 * 1000.0

    return PHREEQCBackend(database="phreeqc.dat").calculate_saturation_indices(
        solution,
        ("Mackinawite", "FeS(ppt)"),
    )


@enforce_tool_input_contract("corrosion_screen_sulfide_exposure")
def screen_sulfide_exposure(
    material: str,
    pH: float,
    total_sulfide_mg_L: Optional[float] = None,
    H2S_headspace_ppmv: Optional[float] = None,
    temperature_C: float = 25.0,
    pressure_bar_abs: float = 1.01325,
    ionic_strength_M: float = 0.0,
    Cl_mg_L: Optional[float] = None,
    SO4_mg_L: Optional[float] = None,
    measured_ORP_mV: Optional[float] = None,
    measured_ORP_uncertainty_mV: Optional[float] = None,
    reference_electrode: str = "SHE",
    dissolved_Fe_mg_L: Optional[float] = None,
) -> Dict[str, Any]:
    'Screen an anaerobic / sulfide-bearing service.'
    from corrosion_engineering_mcp.core.materials import MaterialCatalogError, UnknownMaterialError, canonical_id, get_material

    try:
        resolved = canonical_id(material)
        record = get_material(resolved)
    except MaterialCatalogError:
        raise UnknownMaterialError(material) from None

    # Mirror the public MCP bounds on the exported Python accessor. NaN is
    # especially dangerous here because it bypasses comparisons and propagates
    # into fractions, ratios, and positive material assertions.
    numeric_inputs = {
        "pH": pH,
        "total_sulfide_mg_L": total_sulfide_mg_L,
        "H2S_headspace_ppmv": H2S_headspace_ppmv,
        "temperature_C": temperature_C,
        "pressure_bar_abs": pressure_bar_abs,
        "ionic_strength_M": ionic_strength_M,
        "Cl_mg_L": Cl_mg_L,
        "SO4_mg_L": SO4_mg_L,
        "measured_ORP_mV": measured_ORP_mV,
        "measured_ORP_uncertainty_mV": measured_ORP_uncertainty_mV,
        "dissolved_Fe_mg_L": dissolved_Fe_mg_L,
    }
    for field_name, value in numeric_inputs.items():
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{field_name} must be a finite real number")
    validate_float_domain_inputs(
        numeric_inputs,
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    bounds = {
        "pH": (0.0, 14.0),
        "temperature_C": (-5.0, 150.0),
        "pressure_bar_abs": (0.0, 300.0),
        "ionic_strength_M": (0.0, 10.0),
        "Cl_mg_L": (0.0, None),
        "SO4_mg_L": (0.0, None),
        "measured_ORP_mV": (-2000.0, 2000.0),
        "measured_ORP_uncertainty_mV": (0.0, None),
        "dissolved_Fe_mg_L": (0.0, None),
    }
    for field_name, (minimum, maximum) in bounds.items():
        value = numeric_inputs[field_name]
        if value is None:
            continue
        if value < minimum or (maximum is not None and value > maximum):
            raise ValueError(
                f"{field_name} must be within {repr(float(minimum))} to "
                f"{maximum if maximum is not None else 'unbounded'}"
            )
    if pressure_bar_abs == 0.0:
        raise ValueError("pressure_bar_abs must be greater than zero")
    if total_sulfide_mg_L is None and H2S_headspace_ppmv is None:
        raise SulfideInputUnderdeterminedError()
    if total_sulfide_mg_L is not None and (
        not math.isfinite(total_sulfide_mg_L) or total_sulfide_mg_L < 0.0
    ):
        raise SulfideInputOutOfRangeError(
            "total_sulfide_mg_L", total_sulfide_mg_L
        )
    validate_h2s_gas_composition_ppmv(
        H2S_headspace_ppmv,
        field="H2S_headspace_ppmv",
    )

    warnings: List[str] = []
    gates: List[str] = []

    # ---- resolve sulfide across the two phases -----------------------------
    liquid_measurement_supplied = total_sulfide_mg_L is not None
    headspace_measurement_supplied = H2S_headspace_ppmv is not None
    gas_derived_liquid_sulfide = not liquid_measurement_supplied
    reported_liquid_sulfide_mg_L = total_sulfide_mg_L
    dual_measurement_decision_standing: Optional[Dict[str, Any]] = None
    above_equilibrium_dual_measurements = False

    if liquid_measurement_supplied and headspace_measurement_supplied:
        liquid_implied_ppmv, _ = equilibrium_headspace_H2S_ppmv(
            total_sulfide_mg_L,
            pH,
            temperature_C,
            pressure_bar_abs,
            ionic_strength_M,
        )
        gas_implied_liquid_mg_L = dissolved_sulfide_from_headspace(
            H2S_headspace_ppmv,
            pH,
            temperature_C,
            pressure_bar_abs,
            ionic_strength_M,
        )
        liquid_to_gas_range, gas_to_liquid_range = _henry_uncertainty_ranges(
            liquid_implied_headspace_ppmv=liquid_implied_ppmv,
            headspace_implied_total_sulfide_mg_L=gas_implied_liquid_mg_L,
        )
        displayed_liquid_to_gas_range = _displayed_headspace_range(
            liquid_to_gas_range
        )
        # Henry's law describes an equilibrium partition. These inputs declare
        # neither equilibrium, co-location nor timing, so the displayed range is
        # a reconciliation reference rather than a physical-possibility boundary.
        # A lower headspace remains consistent with stripping/ventilation. A
        # higher headspace may instead reflect an external gas source or transient
        # gas-to-liquid transfer and is evaluated fail-closed on both measurements.
        above_equilibrium_dual_measurements = (
            H2S_headspace_ppmv > displayed_liquid_to_gas_range[1]
        )
        if above_equilibrium_dual_measurements:
            headspace_relation = (
                "above_liquid_equilibrium_external_gas_or_transient_transfer_possible"
            )
            selected_liquid_mg_L = max(
                total_sulfide_mg_L, gas_implied_liquid_mg_L
            )
            selected_headspace_ppmv = max(
                H2S_headspace_ppmv, liquid_implied_ppmv
            )
            liquid_basis_source = (
                "headspace_implied_liquid_equivalent"
                if gas_implied_liquid_mg_L > total_sulfide_mg_L
                else "measured_liquid"
            )
            headspace_basis_source = (
                "measured_headspace"
                if H2S_headspace_ppmv >= liquid_implied_ppmv
                else "liquid_implied_equilibrium_headspace"
            )
            dual_measurement_decision_standing = {
                "standing_code": (
                    DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION
                ),
                "standing": (
                    "dual_measurements_exceed_equilibrium_reconciliation"
                ),
                "decision_policy": (
                    "more_severe_measurement_basis_per_decision_path"
                ),
                "interpretation_limit": (
                    "Conservative decision equivalents are not an assertion that "
                    "the two measurements describe one equilibrium state."
                ),
            }
            sulfide_input_reconciliation = {
                "status": (
                    "dual_measurements_exceed_equilibrium_reconciliation"
                ),
                "standing_code": (
                    DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION
                ),
                "total_sulfide_mg_L_supplied": total_sulfide_mg_L,
                "H2S_headspace_ppmv_supplied": H2S_headspace_ppmv,
                "liquid_implied_headspace_ppmv": liquid_implied_ppmv,
                "liquid_implied_headspace_ppmv_range": list(
                    displayed_liquid_to_gas_range
                ),
                "headspace_implied_total_sulfide_mg_L": gas_implied_liquid_mg_L,
                "headspace_implied_total_sulfide_mg_L_range": list(
                    gas_to_liquid_range
                ),
                "headspace_relation": headspace_relation,
                "equilibrium_assumption": {
                    "declared_by_caller": False,
                    "model_role": "reconciliation_reference_only",
                    "henry_law_scope": "gas_liquid_equilibrium_partition",
                    "co_location_declared": False,
                    "measurement_timing_declared": False,
                },
                "caller_evidence_to_restore_reconciliation": [
                    "declare_that_the_phases_were_at_gas_liquid_equilibrium",
                    "confirm_measurements_were_co_located",
                    "confirm_measurements_were_contemporaneous",
                    "otherwise_identify_external_gas_source_or_transient_transfer",
                ],
                "decision_policy": (
                    "more_severe_measurement_basis_per_decision_path"
                ),
                "decision_bases": {
                    "liquid_concentration_path": {
                        "source": liquid_basis_source,
                        "selected_total_sulfide_mg_L_as_S": (
                            selected_liquid_mg_L
                        ),
                        "measured_liquid_mg_L_as_S": total_sulfide_mg_L,
                        "headspace_implied_liquid_equivalent_mg_L_as_S": (
                            gas_implied_liquid_mg_L
                        ),
                    },
                    "headspace_exposure_path": {
                        "source": headspace_basis_source,
                        "selected_H2S_headspace_ppmv": selected_headspace_ppmv,
                        "measured_headspace_ppmv": H2S_headspace_ppmv,
                        "liquid_implied_equilibrium_headspace_ppmv": (
                            liquid_implied_ppmv
                        ),
                    },
                },
                "decision_rule": (
                    "equilibrium_range_is_a_reconciliation_reference_not_an_"
                    "impossibility_boundary"
                ),
                "tolerance_basis": (
                    "Sander_2023_H2S_Henry_carried_measured_anchor_band_"
                    "0.0871_to_0.1115_mol_per_L_atm_at_25_C; exact_display_"
                    "bounds_are_reconciliation_references"
                ),
                "tolerance_source": {
                    **SANDER_H2S_HENRY_CITATION_RECORD,
                    "authors": list(SANDER_H2S_HENRY_CITATION_RECORD["authors"]),
                },
            }
            total_sulfide_mg_L = selected_liquid_mg_L
            gates.append(
                DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION
            )
            warnings.append(
                f"{DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION}: "
                "Henry's law is an equilibrium reconciliation model, but the "
                "caller did not declare equilibrium, co-location or measurement "
                "timing. Both measurements were accepted and the more severe "
                "basis is used independently for liquid-concentration and "
                "headspace-exposure decisions."
            )
        elif _inside_closed_range(
            H2S_headspace_ppmv, displayed_liquid_to_gas_range
        ):
            headspace_relation = "within_displayed_liquid_equilibrium_range"
        else:
            headspace_relation = (
                "below_liquid_equilibrium_consistent_with_ventilation_or_stripping"
            )
        if not above_equilibrium_dual_measurements:
            sulfide_input_reconciliation = {
                "status": "physically_consistent_dual_measurement",
                "total_sulfide_mg_L_supplied": total_sulfide_mg_L,
                "H2S_headspace_ppmv_supplied": H2S_headspace_ppmv,
                "liquid_implied_headspace_ppmv": liquid_implied_ppmv,
                "liquid_implied_headspace_ppmv_range": list(
                    displayed_liquid_to_gas_range
                ),
                "headspace_implied_total_sulfide_mg_L": gas_implied_liquid_mg_L,
                "headspace_implied_total_sulfide_mg_L_range": list(
                    gas_to_liquid_range
                ),
                "headspace_relation": headspace_relation,
                "decision_rule": (
                    "equilibrium_range_is_a_reconciliation_reference_not_an_"
                    "impossibility_boundary"
                ),
                "lower_bound_is_not_a_rejection_boundary": True,
                "tolerance_basis": (
                    "Sander_2023_H2S_Henry_carried_measured_anchor_band_"
                    "0.0871_to_0.1115_mol_per_L_atm_at_25_C; exact_display_"
                    "bounds_are_reconciliation_references"
                ),
                "tolerance_source": {
                    **SANDER_H2S_HENRY_CITATION_RECORD,
                    "authors": list(
                        SANDER_H2S_HENRY_CITATION_RECORD["authors"]
                    ),
                },
            }
        if above_equilibrium_dual_measurements:
            sulfide_basis = (
                f"{reported_liquid_sulfide_mg_L:g} mg/L as S measured in the "
                f"liquid and {H2S_headspace_ppmv:g} ppmv H2S measured in the "
                f"headspace; both retained ({headspace_relation}). Henry's law "
                f"is used only as an equilibrium reconciliation reference. The "
                f"more severe measurement equivalent is used separately for "
                f"each decision path."
            )
        else:
            sulfide_basis = (
                f"{total_sulfide_mg_L:g} mg/L as S measured in the liquid and "
                f"{H2S_headspace_ppmv:g} ppmv H2S measured in the headspace; both "
                f"retained ({headspace_relation}). The measured liquid value is "
                f"the speciation basis after reconciliation."
            )
    elif gas_derived_liquid_sulfide:
        total_sulfide_mg_L = dissolved_sulfide_from_headspace(
            H2S_headspace_ppmv, pH, temperature_C, pressure_bar_abs, ionic_strength_M
        )
        sulfide_input_reconciliation = {
            "status": "single_headspace_measurement",
            "total_sulfide_mg_L_supplied": None,
            "H2S_headspace_ppmv_supplied": H2S_headspace_ppmv,
            "headspace_implied_total_sulfide_mg_L": total_sulfide_mg_L,
        }
        sulfide_basis = (
            f"Derived from {H2S_headspace_ppmv:g} ppmv headspace H2S at "
            f"{pressure_bar_abs:g} bar(a), {temperature_C:g} C, pH {pH:g}. This "
            f"assumes gas/liquid EQUILIBRIUM; a ventilated or stripped headspace "
            f"sits below equilibrium, so the true liquid sulfide is higher than "
            f"this."
        )
        warnings.append(
            "Liquid sulfide was inferred from a gas measurement assuming "
            "equilibrium. It is a LOWER BOUND, not a measurement of the liquid, and "
            "cannot establish the liquid concentration grade."
        )
    else:
        sulfide_input_reconciliation = {
            "status": "single_liquid_measurement",
            "total_sulfide_mg_L_supplied": total_sulfide_mg_L,
            "H2S_headspace_ppmv_supplied": None,
        }
        sulfide_basis = f"{total_sulfide_mg_L:g} mg/L as S measured in the liquid"

    spec = speciate_dissolved_sulfide(
        total_sulfide_mg_L, pH, temperature_C, ionic_strength_M
    )
    if spec.high_pH_scope_degraded:
        warnings.append(
            "SULFIDE_SPECIATION_HIGH_PH_SCOPE_REDUCED: "
            "INTERNAL_SCREENING_RULE_SULFIDE_HIGH_PH_SCOPE_V1 applies above "
            "pH 12 because the shipped legacy S2- row and the May et al. "
            "abstract-backed recommendation diverge; no S2- fraction is emitted."
        )
    if headspace_measurement_supplied:
        # Both source values have already passed their strict Henry-domain gate.
        # Preserve the established reconstructed display without applying the
        # opposite-direction gate a second time.
        equilibrium_ppmv, _ = (
            _equilibrium_headspace_from_validated_dilute_liquid(
                total_sulfide_mg_L,
                pH,
                temperature_C,
                pressure_bar_abs,
                ionic_strength_M,
            )
        )
    else:
        equilibrium_ppmv, _ = equilibrium_headspace_H2S_ppmv(
            total_sulfide_mg_L,
            pH,
            temperature_C,
            pressure_bar_abs,
            ionic_strength_M,
        )
    equilibrium_headspace_display_range = _displayed_headspace_range(
        (
            equilibrium_ppmv
            * HENRY_H2S_MOL_PER_L_ATM_25C
            / HENRY_H2S_REPORTED_RANGE_25C[1],
            equilibrium_ppmv
            * HENRY_H2S_MOL_PER_L_ATM_25C
            / HENRY_H2S_REPORTED_RANGE_25C[0],
        )
    )
    headspace_decision_ppmv = (
        sulfide_input_reconciliation["decision_bases"][
            "headspace_exposure_path"
        ]["selected_H2S_headspace_ppmv"]
        if above_equilibrium_dual_measurements
        else equilibrium_ppmv
    )

    # ---- evidence standing: concentration is not a decision threshold ------
    cls = record.classification
    passivity_standing = sulfide_standing(total_sulfide_mg_L)
    passivity_standing["input_value_standing"] = (
        "canonical_more_severe_dual_measurement_basis"
        if above_equilibrium_dual_measurements
        else (
            "lower_bound_from_gas_liquid_equilibrium"
            if gas_derived_liquid_sulfide
            else "measured_liquid_concentration"
        )
    )
    if dual_measurement_decision_standing is not None:
        passivity_standing["dual_measurement_decision_standing"] = dict(
            dual_measurement_decision_standing
        )
        passivity_standing["uncertainty"] += (
            " The reported phases were outside the equilibrium reconciliation "
            "range. This grade uses the more severe liquid-concentration "
            "equivalent as a conservative decision screen, not as a reconciled "
            "concentration measurement."
        )
    if gas_derived_liquid_sulfide:
        passivity_standing["uncertainty"] += (
            " The concentration was inferred from headspace H2S and is only a "
            "lower bound; the true liquid may occupy a higher grade."
        )
    if cls in _CHROMIUM_OXIDE_PASSIVE_CLASSES:
        passivity_standing["material_scope"] = (
            "chromium_oxide_passive_family_direct_concentration_data_for_316L"
        )
        passivity_standing["material_note"] = (
            f"{resolved} relies on a chromium-rich oxide film. Lee et al. directly "
            "studied 316L and found continuous degradation with HS- concentration; "
            "transfer to other stainless families is qualitative. This standing "
            "does not decide whether chloride/PREN output is applicable."
        )
    elif cls in _OTHER_PASSIVE_CLASSES:
        passivity_standing["material_scope"] = "no_class_specific_source"
        passivity_standing["material_note"] = (
            f"{resolved} is {cls}; the 316L evidence is not a class-specific basis "
            "for this oxide system. The concentration grade is descriptive only."
        )
        gates.append("SULFIDE_EFFECT_NEEDS_CLASS_SPECIFIC_SOURCE")
    elif cls in _ACTIVE_CLASSES:
        passivity_standing["material_scope"] = (
            "different_mechanism_no_chromium_oxide_passive_film"
        )
        passivity_standing["material_note"] = (
            f"{resolved} is {cls} and carries no chromium-oxide passive film, so "
            f"the 316L passivity study is not a material rule. That is NOT "
            f"reassurance: this accessor has no class-specific sulfide attack "
            f"model for active ferrous alloys. See iron_sulfide_film and the "
            f"uncleared residual mechanisms below."
        )
        gates.append("SULFIDE_PASSIVITY_STUDY_NOT_APPLICABLE_ACTIVE_ALLOY")
    else:
        passivity_standing["material_scope"] = "unclassified_source_transfer"
        passivity_standing["material_note"] = (
            f"{resolved} is classified {cls}, which is neither a chromium-oxide "
            f"passive alloy nor a plain ferrous alloy in this server's taxonomy. "
            f"No material inference is made from the concentration grade."
        )
        gates.append("SULFIDE_EFFECT_UNDEFINED_FOR_CLASS")

    # ---- sulfate/chloride context; no quantitative credit -------------------
    sulfate_block: Optional[Dict[str, Any]] = None
    if Cl_mg_L is not None and SO4_mg_L is not None:
        ratio = sulfate_chloride_molar_ratio(SO4_mg_L, Cl_mg_L)
        sulfate_block = {
            "SO4_Cl_molar_ratio": round(ratio, 3) if ratio != float("inf") else None,
            "SO4_Cl_molar_ratio_unrounded": (
                ratio if ratio != float("inf") else None
            ),
            "SO4_Cl_molar_ratio_value_standing": (
                "unrounded_authoritative" if ratio != float("inf") else None
            ),
            "reported_bulk_concentrations_mg_L": {
                "SO4_mg_L": SO4_mg_L,
                "Cl_mg_L": Cl_mg_L,
            },
            "qualitative_finding": (
                "Galvele's one mixed-electrolyte calculation predicts sulfate "
                "enrichment inside a growing pit and eventual repassivation. "
                "That establishes qualitative context, not a transferable bulk "
                "ratio boundary or chloride credit."
            ),
            "confidence": "qualitative_source_context",
            "application_status": "not_applied_to_material_or_model_verdict",
            "decision_effect": "qualitative_context_only_no_credit",
            "decision_threshold_applied": False,
            "source": {
                **_GALVELE_SULFATE_CONTEXT_SOURCE,
                "authors": list(_GALVELE_SULFATE_CONTEXT_SOURCE["authors"]),
            },
        }
    elif Cl_mg_L is not None or SO4_mg_L is not None:
        warnings.append(
            "Sulfate/chloride context needs BOTH measured ions to report a molar "
            "ratio. Supplied only one; no ratio or decision was produced."
        )

    # ---- iron sulfide film -------------------------------------------------
    fes_saturation_indices: Optional[Dict[str, float]] = None
    if (
        dissolved_Fe_mg_L is not None
        and dissolved_Fe_mg_L >= FILM_ASSERTION_FLOOR_MG_L
        and total_sulfide_mg_L >= FILM_ASSERTION_FLOOR_MG_L
    ):
        try:
            fes_saturation_indices = _calculate_fes_saturation(
                dissolved_Fe_mg_L=dissolved_Fe_mg_L,
                total_sulfide_mg_L=total_sulfide_mg_L,
                pH=pH,
                temperature_C=temperature_C,
                ionic_strength_M=ionic_strength_M,
            )
        except PHREEQCSaturationIndexSentinelError:
            # A finite PHREEQC missing/non-converged sentinel is a typed
            # backend failure, not an ordinary calculation failure that this
            # facade may soften into an indeterminate film screen.
            raise
        except PHREEQCBackendUnavailableError:
            film_expected = None
            saturation_standing = "not_assessed_phreeqc_unavailable"
            gates.append("FES_SATURATION_UNAVAILABLE")
            warnings.append(
                "FeS saturation was not assessed because phreeqc.dat could not "
                "be loaded; reactant presence alone does not establish a film."
            )
            film_note = (
                "Dissolved iron and sulfide clear the reporting floor, but the "
                "required PHREEQC saturation calculation is unavailable. Film "
                "expectation is indeterminate."
            )
        except RuntimeError:
            film_expected = None
            saturation_standing = "not_assessed_phreeqc_calculation_failed"
            gates.append("FES_SATURATION_CALCULATION_FAILED")
            warnings.append(
                "FeS saturation calculation failed; reactant presence alone does "
                "not establish a film."
            )
            film_note = (
                "Dissolved iron and sulfide clear the reporting floor, but the "
                "PHREEQC saturation calculation did not complete. Film expectation "
                "is indeterminate."
            )
        else:
            maximum_fes_si = max(fes_saturation_indices.values())
            if maximum_fes_si >= 0.0:
                film_expected = True
                saturation_standing = "phreeqc_saturation_evaluated"
                film_note = (
                    "phreeqc.dat FeS phase maximum SI is "
                    f"{_format_signed_nonzero(maximum_fes_si, 4)} "
                    f"(supersaturated_or_at_saturation). film_expected is a "
                    f"thermodynamic precipitation expectation only, not an "
                    f"observed adherent film. Electrical, galvanic, protective, "
                    f"kinetic, and local-attack properties are not assessed."
                )
            elif gas_derived_liquid_sulfide:
                film_expected = None
                saturation_standing = (
                    "indeterminate_lower_bound_cannot_exclude_saturation"
                )
                film_note = (
                    "phreeqc.dat FeS phase maximum SI is "
                    f"{_format_signed_nonzero(maximum_fes_si, 4)} "
                    f"at the headspace-derived LOWER-BOUND liquid sulfide. "
                    f"Undersaturation at a lower bound cannot exclude saturation "
                    f"at the unknown true liquid concentration, so film expectation "
                    f"remains indeterminate."
                )
            elif dual_measurement_decision_standing is not None:
                film_expected = False
                saturation_standing = "phreeqc_saturation_evaluated"
                film_note = (
                    "phreeqc.dat FeS phase maximum SI is "
                    f"{_format_signed_nonzero(maximum_fes_si, 4)} "
                    "(undersaturated) at the canonical more-severe liquid-"
                    "concentration decision equivalent. This is a conservative "
                    "dual-measurement screen, not an assertion that the phases "
                    "were at equilibrium. Electrical, galvanic, protective, "
                    "kinetic, and local-attack properties are not assessed."
                )
            else:
                film_expected = False
                saturation_standing = "phreeqc_saturation_evaluated"
                film_note = (
                    "phreeqc.dat FeS phase maximum SI is "
                    f"{_format_signed_nonzero(maximum_fes_si, 4)} "
                    f"(undersaturated) for the measured liquid concentrations; "
                    f"the thermodynamic film expectation is false. Electrical, "
                    f"galvanic, protective, kinetic, and local-attack properties "
                    f"are not assessed."
                )
    elif dissolved_Fe_mg_L is not None and (
        dissolved_Fe_mg_L == 0.0 or total_sulfide_mg_L == 0.0
    ):
        film_expected = None
        saturation_standing = "not_assessed_reported_reactant_absent"
        film_note = (
            "At least one required reactant was explicitly reported zero. The "
            "input is not missing, but an FeS saturation calculation is not "
            "applicable to the reported zero-reactant condition."
        )
    elif (
        (dissolved_Fe_mg_L is not None and dissolved_Fe_mg_L > 0.0)
        or total_sulfide_mg_L > 0.0
    ) and (
        total_sulfide_mg_L < FILM_ASSERTION_FLOOR_MG_L
        or (
            dissolved_Fe_mg_L is not None
            and dissolved_Fe_mg_L < FILM_ASSERTION_FLOOR_MG_L
        )
    ):
        film_expected = None
        saturation_standing = "not_assessed_below_assertion_floor"
        film_note = (
            f"At least one reported reactant is below {FILM_ASSERTION_FLOOR_MG_L:g} "
            "mg/L, the smallest nonzero concentration displayed by this payload. "
            "The value therefore prints as zero and cannot support a positive film "
            "assertion. This output-resolution floor is not an analytical LOD or "
            "an FeS solubility threshold."
        )
    elif total_sulfide_mg_L > 0 and cls in _ACTIVE_CLASSES:
        film_expected = None
        saturation_standing = "not_assessed_missing_dissolved_iron"
        film_note = (
            "Dissolved iron not reported, but a ferrous alloy in sulfide service "
            "can supply iron as it corrodes. FeS formation is plausible, but is "
            "not asserted without enough reported chemistry to assess it. Film "
            "properties are also not assessed."
        )
    else:
        film_expected = None
        saturation_standing = "not_assessed_inputs_insufficient"
        film_note = (
            "Dissolved iron not reported. FeS saturation is not assessed. Absent "
            "is not zero."
        )

    # ---- ORP, reported not judged ------------------------------------------
    orp_block: Optional[Dict[str, Any]] = None
    if measured_ORP_mV is not None:
        from corrosion_engineering_mcp.utils.redox_state import orp_to_eh

        eh_v = orp_to_eh(measured_ORP_mV, reference_electrode)
        orp_block = {
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
            "interpretation_standing": "reported_not_judged",
            "note": (
                "Reported after reference-electrode conversion, not classified. "
                "There is no sourced Eh threshold on this server for turning this "
                "measurement into a sulfide-consistency or corrosion verdict."
            ),
        }

    # ---- what this screen does not clear -----------------------------------
    residual: List[Dict[str, Any]] = []

    residual.append({
        "mechanism": "sulfide_stress_cracking",
        "status": "NOT_SCREENED_HERE",
        "why": (
            "Cracking is decided by H2S PARTIAL PRESSURE against ISO 15156-2, not "
            "by dissolved concentration. Use corrosion_screen_sour_service - the "
            "same gas is out of scope at 1 bar and in scope at 15 bar."
        ),
    })

    if headspace_decision_ppmv > 0:
        biogenic_residual: Dict[str, Any] = {
            "mechanism": "biogenic_sulfuric_acid_on_concrete",
            "status": "UNASSESSED",
            "why": (
                (
                    f"The canonical more-severe headspace decision basis is "
                    f"{headspace_decision_ppmv:g} ppmv H2S. "
                    if above_equilibrium_dual_measurements
                    else (
                        f"This liquid can support {equilibrium_ppmv:g} ppmv H2S "
                        f"in an unventilated headspace. "
                    )
                )
                + "Where that headspace is concrete and condensing, the "
                "governing mechanism is biogenic sulfuric acid on the crown, not "
                "metal loss. Out of scope for a metals screen."
            ),
        }
        if dual_measurement_decision_standing is not None:
            biogenic_residual["dual_measurement_decision_standing"] = dict(
                dual_measurement_decision_standing
            )
        residual.append(biogenic_residual)

    residual.append({
        "mechanism": "microbially_influenced_corrosion",
        "status": "UNASSESSED",
        "why": (
            "Reported sulfide concentration is chemical context only; it cannot "
            "establish biogenic origin or an active population. Resolve MIC from "
            "categorical surface/biofilm evidence in corrosion_screen_mic."
        ),
    })

    if cls == "cast_iron":
        residual.append({
            "mechanism": "graphitic_corrosion",
            "status": "UNASSESSED_NO_CONTENT_VERIFIED_SOURCE",
            "why": (
                "Cast iron was selected, but this accessor returns no content-"
                "verified graphitic-corrosion mechanism, strength, or inspection-"
                "method claim. Obtain a scoped cast-iron assessment separately."
            ),
        })

    return {
        "material": resolved,
        "material_classification": cls,
        "sulfide_basis": sulfide_basis,
        "speciation": {
            "total_sulfide_mg_L_as_S": round(total_sulfide_mg_L, 4),
            "total_sulfide_mg_L_as_S_unrounded": total_sulfide_mg_L,
            "total_sulfide_numeric_value_standing": (
                "unrounded_companion_authoritative_primary_is_display_rounded"
            ),
            "total_sulfide_value_standing": (
                "canonical_more_severe_dual_measurement_basis"
                if above_equilibrium_dual_measurements
                else (
                    "lower_bound"
                    if gas_derived_liquid_sulfide
                    else (
                        "measured_reconciled_with_headspace"
                        if headspace_measurement_supplied
                        else "measured"
                    )
                )
            ),
            **(
                {
                    "dual_measurement_decision_standing": dict(
                        dual_measurement_decision_standing
                    )
                }
                if dual_measurement_decision_standing is not None
                else {}
            ),
            "pH": pH,
            "temperature_C": temperature_C,
            "pKa1": spec.pKa1,
            "pKa1_display": round(spec.pKa1, 3),
            "pKa1_value_standing": (
                "exact_primary_authoritative_display_field_rounded"
            ),
            "fraction_H2S": round(spec.fraction_H2S, 4),
            "fraction_HS_minus": round(spec.fraction_HS, 4),
            "H2S_aq_mg_L_as_S": round(spec.H2S_aq_mg_L_as_S, 4),
            "HS_minus_mg_L_as_S": round(spec.HS_mg_L_as_S, 4),
            # Preserve the four-decimal compatibility fields above, but carry
            # the actual equilibrium values so a small nonzero H2S fraction is
            # never contradicted by a displayed zero alongside nonzero gas.
            "fraction_H2S_unrounded": spec.fraction_H2S,
            "fraction_HS_minus_unrounded": spec.fraction_HS,
            "H2S_aq_mg_L_as_S_unrounded": spec.H2S_aq_mg_L_as_S,
            "HS_minus_mg_L_as_S_unrounded": spec.HS_mg_L_as_S,
            "display_precision_note": (
                "Unrounded equilibrium values are authoritative; rounded "
                "compatibility fields are not chemical zeroes."
            ),
            "equilibrium_headspace_H2S_ppmv": equilibrium_ppmv,
            "equilibrium_headspace_H2S_ppmv_range": list(
                equilibrium_headspace_display_range
            ),
            "henry_note": (
                "Partition uses the Sander 2023 leading literature-review value "
                "in the official compilation, "
                f"{HENRY_H2S_MOL_PER_L_ATM_25C} mol/(L.atm) at 25 C. The "
                f"two measured anchors retained for the carried band are "
                f"{HENRY_H2S_REPORTED_RANGE_25C[0]}-{HENRY_H2S_REPORTED_RANGE_25C[1]}, "
                f"and phreeqc.dat's analytical form sits at "
                f"{HENRY_H2S_PHREEQC_ANALYTIC_25C} inside it. The ppmv range above "
                f"is that spread propagated. The exact JSON-round-trippable "
                f"bounds shown are also the executable reconciliation bounds."
            ),
            "S2_minus_reported": False,
            "S2_minus_standing": spec.S2_minus_standing,
            "S2_minus_note": spec.S2_minus_note,
            "S2_minus_source": {
                **MAY_S2_AQUEOUS_CITATION_RECORD,
                "authors": list(MAY_S2_AQUEOUS_CITATION_RECORD["authors"]),
                "authors_as_printed_by_publisher": list(
                    MAY_S2_AQUEOUS_CITATION_RECORD[
                        "authors_as_printed_by_publisher"
                    ]
                ),
                "author_name_reconciliation": dict(
                    MAY_S2_AQUEOUS_CITATION_RECORD[
                        "author_name_reconciliation"
                    ]
                ),
            },
            "speciation_scope_status": spec.speciation_scope_status,
            "high_pH_scope_boundary": dict(HIGH_PH_SPECIATION_SCOPE_RULE),
            "confidence": (
                "reduced_non_equilibrium_dual_measurement_screen"
                if above_equilibrium_dual_measurements
                else (
                    "reduced_high_pH_scope"
                    if spec.high_pH_scope_degraded
                    else (
                        "high"
                        if not spec.extrapolated_beyond_verified_range
                        else "reduced_extrapolated"
                    )
                )
            ),
            "notes": spec.notes,
        },
        "sulfide_input_reconciliation": sulfide_input_reconciliation,
        "passivity_standing": passivity_standing,
        "sulfate_inhibition": sulfate_block,
        "iron_sulfide_film": {
            "film_expected": film_expected,
            "reported_reactants_mg_L": {
                **(
                    {"total_sulfide_mg_L": reported_liquid_sulfide_mg_L}
                    if liquid_measurement_supplied
                    else {}
                ),
                **(
                    {"dissolved_Fe_mg_L": dissolved_Fe_mg_L}
                    if dissolved_Fe_mg_L is not None
                    else {}
                ),
            },
            **(
                {
                    "decision_reactants_mg_L": {
                        "total_sulfide_mg_L_as_S": total_sulfide_mg_L,
                        "dissolved_Fe_mg_L": dissolved_Fe_mg_L,
                    },
                    "dual_measurement_decision_standing": dict(
                        dual_measurement_decision_standing
                    ),
                }
                if dual_measurement_decision_standing is not None
                else {}
            ),
            "film_expectation_scope": (
                "thermodynamic_precipitation_tendency_not_observed_film"
            ),
            "saturation_standing": saturation_standing,
            "saturation_indices": (
                {
                    name: round(value, 6)
                    for name, value in fes_saturation_indices.items()
                }
                if fes_saturation_indices is not None
                else None
            ),
            "saturation_indices_unrounded": (
                dict(fes_saturation_indices)
                if fes_saturation_indices is not None
                else None
            ),
            "saturation_indices_value_standing": (
                "unrounded_values_authoritative_rounded_fields_display_only"
                if fes_saturation_indices is not None
                else None
            ),
            "saturation_source": {
                **_FES_SATURATION_SOURCE,
                "authors": list(_FES_SATURATION_SOURCE["authors"]),
            },
            "ionic_strength_basis": (
                "equimolar_NaCl_background_from_reported_ionic_strength_M"
                if ionic_strength_M > 0.0
                else "zero_added_background_electrolyte"
            ),
            "property_standing": "not_assessed_no_content_verified_source",
            "assertion_floor_mg_L": FILM_ASSERTION_FLOOR_MG_L,
            "floor_basis": (
                "smallest_nonzero_concentration_displayed_by_this_payload_"
                "not_an_analytical_LOD"
            ),
            "note": film_note,
        },
        "redox": orp_block,
        "corrosion_rate_mm_y": None,
        "why_no_rate": (
            "No open validated sulfide corrosion rate model exists for these "
            "conditions. corrosion_predict_co2 rejects any declared H2S for the "
            "same reason. This is a screen, not a model."
        ),
        "residual_mechanisms_not_cleared": residual,
        "gates": gates,
        "warnings": warnings,
        "provenance": {
            "model": "sulfide_exposure_screen",
            "version": "1.0",
            "validation_dataset": None,
            "confidence": (
                "reduced_non_equilibrium_dual_measurement_screen_graded_"
                "uncertain_for_passivity"
                if above_equilibrium_dual_measurements
                else (
                    "reduced_speciation_high_pH_scope_graded_uncertain_for_passivity"
                    if spec.high_pH_scope_degraded
                    else (
                        "high_for_speciation_graded_uncertain_for_passivity"
                        if not spec.extrapolated_beyond_verified_range
                        else (
                            "reduced_speciation_extrapolated_activity_or_temperature"
                        )
                    )
                )
            ),
            "speciation_confidence": (
                "reduced_non_equilibrium_dual_measurement_screen"
                if above_equilibrium_dual_measurements
                else (
                    "reduced_high_pH_scope"
                    if spec.high_pH_scope_degraded
                    else (
                        "high"
                        if not spec.extrapolated_beyond_verified_range
                        else "reduced_extrapolated"
                    )
                )
            ),
            "sources": [
                "phreeqc.dat thermodynamics (pKa1 machine-read 2026-08-07; "
                "legacy second-dissociation artifact machine-read 2026-08-10 "
                "for the qualitative internal high-pH scope rule only)",
                "May et al. 2018, doi:10.1039/C8CC00187A (Abstract only: new "
                "Raman spectra in hyper-concentrated hydroxide cast serious "
                "doubt on S2-(aq), which the abstract says should be expunged; "
                "full text is paywalled and was not read)",
                "Sander 2023, doi:10.5194/acp-23-10901-2023 (Appendix A1.8, "
                "Table A1.8 H2S Henry entries)",
                "Galvele 1976, doi:10.1149/1.2132857 (one mixed-electrolyte "
                "sulfate-enrichment calculation; qualitative context only, no "
                "bulk threshold or chloride credit)",
                "Parangusan et al. 2021, doi:10.1007/s42247-021-00194-6 (sulfide "
                "destruction of the passive film)",
            ],
            "citation_records": [
                {
                    **MAY_S2_AQUEOUS_CITATION_RECORD,
                    "authors": list(MAY_S2_AQUEOUS_CITATION_RECORD["authors"]),
                    "authors_as_printed_by_publisher": list(
                        MAY_S2_AQUEOUS_CITATION_RECORD[
                            "authors_as_printed_by_publisher"
                        ]
                    ),
                    "author_name_reconciliation": dict(
                        MAY_S2_AQUEOUS_CITATION_RECORD[
                            "author_name_reconciliation"
                        ]
                    ),
                },
                {
                    **SANDER_H2S_HENRY_CITATION_RECORD,
                    "authors": list(SANDER_H2S_HENRY_CITATION_RECORD["authors"]),
                },
                {
                    **_GALVELE_SULFATE_CONTEXT_SOURCE,
                    "authors": list(_GALVELE_SULFATE_CONTEXT_SOURCE["authors"]),
                },
                {
                    **SULFIDE_316L_CITATION_RECORD,
                    "authors": list(SULFIDE_316L_CITATION_RECORD["authors"]),
                },
                {
                    **PARANGUSAN_SULFIDE_REVIEW_CITATION_RECORD,
                    "authors": list(
                        PARANGUSAN_SULFIDE_REVIEW_CITATION_RECORD["authors"]
                    ),
                },
            ],
            "citation_metadata_source": "OpenAlex",
            "citation_verification_scope": (
                "doi_author_title_and_abstract_content_plus_machine_read_local_"
                "database_artifact"
            ),
            "assumptions": [
                "Speciation uses the phreeqc.dat analytical pKa1 with a Davies "
                "activity correction; agreement with PHREEQC is within 0.005 pKa "
                "units over 10-55 C. Davies confidence is reduced above its "
                "stated approximate 0.5 M reliable range.",
                "Gas/liquid conversions assume equilibrium.",
                "A liquid concentration derived from headspace H2S is a lower "
                "bound and may occupy a higher concentration grade.",
                "The 0.1 mg/L grade boundary is an internal convention, not a "
                "decision threshold; no passivity or material verdict is emitted.",
                "S2- is never reported. The readable May et al. (2018) abstract "
                "reports new Raman spectra in hyper-concentrated hydroxide, "
                "casts serious doubt on S2-(aq), recommends expunging it from "
                "the chemical literature, and calls for careful revision of "
                "sulfide-mineral thermodynamic databases. Full text is paywalled "
                "and was not read.",
                "Above pH 12, the H2S/HS- display is explicitly reduced-confidence "
                "under INTERNAL_SCREENING_RULE_SULFIDE_HIGH_PH_SCOPE_V1. The "
                "legacy and modern treatments of the second dissociation diverge "
                "materially, while the May abstract recommends expunging "
                "S2-(aq). pH 12 is an internal confidence boundary, not an S2- "
                "formation threshold or May-derived decision edge.",
            ],
            "warnings": warnings,
        },
    }
