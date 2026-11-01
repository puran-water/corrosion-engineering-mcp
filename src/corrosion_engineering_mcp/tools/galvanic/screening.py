"""Screening galvanic tier over the ASTM G82 series.

WHY A SECOND TIER
-----------------
The former `corrosion_assess_galvanic` mixed-potential accessor is withdrawn: it
reversed the cathode/anode area-ratio response, lost the declared electrode
polarity over part of its pH range, and had only placeholder comparison curves.
This server therefore has no validated galvanic-current or rate model.

Every couple this practice actually specifies falls outside it on both counts.
Counted across project MOC fields: `CI_body_SS316_impeller` and its variants
appear in the hundreds; `CI + SS304 + SS410`, aluminum/CI, `CS + SS316`, and
`SS304 + SS316` all recur. ASTM G82 has two distinct aluminum rows (AA 3003-H
and AA 6061-T), so a generic aluminum request is rejected until the caller
chooses the grade. None of those materials are in the NRL kinetic set, and
wastewater chloride is an order of magnitude below its floor.

So this tier answers the couples we build, using data already in the repo, and
deliberately answers a smaller question.

WHAT IT WILL NOT DO
-------------------
**It emits no corrosion rate and no galvanic current.** ASTM G82 potentials are
open-circuit values in flowing seawater; they support *ranking* a couple, not
computing kinetics. A current would need validated polarization behaviour for
both members in the actual electrolyte, which this tier does not have. Emitting
one would be a fabrication.

THE ACTIVE/PASSIVE DISTINCTION IS RETAINED AS SCENARIOS
-------------------------------------------------------
G82 tabulates stainless twice, active and passive, and the difference dominates
the answer:

    Cast Iron (-0.610) vs SS304 PASSIVE  (-0.080)  ->  530 mV
    Cast Iron (-0.610) vs SS304 ACTIVE   (-0.530)  ->   80 mV

A stainless surface can occupy either tabulated state depending on the local
surface chemistry.  This accessor returns both available rows; it does not use
house cutoffs on bulk sulfide, dissolved oxygen, or a crevice declaration to
choose one. No service acceptability inference follows from either difference.

NOT ALL STATES ARE TABULATED. G82 lists SS316 passive but not SS316 active. When
a caller asks for a state that is not in the table, this tool says so rather than
substituting a neighbouring alloy.

SOURCES
-------
[G82] ASTM G82 potentials as vendored in data/astm_g82_galvanic_series.csv
      (48 rows, SCE and SHE, with G82 activity categories A-T).
[MIL] No primary-source copy of MIL-STD-889 was obtained for the former
      compatibility thresholds. Release 4 therefore suppresses all band labels
      and verdicts; only the ASTM G82 potential difference and the caller's raw
      area ratio are returned.
"""

from __future__ import annotations

import logging
import math
from numbers import Real
from typing import Any, Dict, List, Optional

from corrosion_engineering_mcp.core.errors import AmbiguousGalvanicMaterialError, CorrosionPublicError, validate_float_domain_inputs
from corrosion_engineering_mcp.core.validation import enforce_tool_input_contract

logger = logging.getLogger(__name__)

# Aliases onto the G82 `material` column, which uses long descriptive names.
_G82_ALIASES: Dict[str, str] = {
    "cast_iron": "Cast Iron",
    "ci": "Cast Iron",
    "ductile_iron": "Cast Iron",          # see note in _resolve
    "di": "Cast Iron",
    "carbon_steel": "Carbon Steel",
    "cs": "Carbon Steel",
    "zinc": "Zinc",
    "galvanized_steel": "Zinc",
    "aa_3003_h": "AA 3003-H",
    "aa3003_h": "AA 3003-H",
    "aluminum_3003_h": "AA 3003-H",
    "aluminium_3003_h": "AA 3003-H",
    "aa_6061_t": "AA 6061-T",
    "aa6061_t": "AA 6061-T",
    "aluminum_6061_t": "AA 6061-T",
    "aluminium_6061_t": "AA 6061-T",
    "austenitic_cast_iron": "Austenitic Cast Iron",
    "ni_resist": "Austenitic Cast Iron",
}

# Stainless grades carry a state suffix in G82.
_STAINLESS_BASE = {
    "304": "Stainless Steel, Type 304",
    "304l": "Stainless Steel, Type 304",
    "ss304": "Stainless Steel, Type 304",
    "316": "Stainless Steel, Type 316",
    "316l": "Stainless Steel, Type 316",
    "ss316": "Stainless Steel, Type 316",
    "410": "Stainless Steel, Type 410",
    "ss410": "Stainless Steel, Type 410",
    "430": "Stainless Steel, Type 430",
    "ss430": "Stainless Steel, Type 430",
}


class GalvanicStateNotTabulatedError(CorrosionPublicError, ValueError):
    """The alloy is in G82 but not in the requested passivity state.

    Substituting a neighbouring alloy's potential here would be quiet
    fabrication, and the active/passive gap is hundreds of millivolts — larger
    than most of the couples being assessed.
    """

    def __init__(self, material: str, state: str, available: List[str]) -> None:
        super().__init__(
            "GALVANIC_STATE_NOT_TABULATED",
            f"GALVANIC_STATE_NOT_TABULATED: ASTM G82 does not tabulate "
            f"'{material}' in the {state} state. Available: {', '.join(available)}. "
            f"The active/passive difference is several hundred millivolts, so no "
            f"substitution is made.",
            details={
                "reason_code": "GALVANIC_STATE_NOT_TABULATED",
                "material": material,
                "requested_state": state,
                "available_states": available,
            },
        )


def _series() -> Dict[str, float]:
    from corrosion_engineering_mcp.data.csv_loaders import load_galvanic_series_from_csv

    return load_galvanic_series_from_csv()


def _resolve(material: str, state: str) -> Dict[str, Any]:
    """Map a house material name onto a G82 row, honouring passivity state."""
    series = _series()
    key = material.strip().lower().replace(" ", "_").replace("-", "_")

    notes: List[str] = []

    if key in {"al", "aluminum", "aluminium"}:
        raise AmbiguousGalvanicMaterialError(material)

    if key in _STAINLESS_BASE:
        base = _STAINLESS_BASE[key]
        want = f"{base}, {state}"
        if want in series:
            return {"g82_name": want, "E_VSCE": series[want], "state": state, "notes": notes}
        available = [
            s.rsplit(", ", 1)[-1] for s in series if s.startswith(base + ",")
        ]
        raise GalvanicStateNotTabulatedError(material, state, available or ["none"])

    if key in _G82_ALIASES:
        name = _G82_ALIASES[key]
        if key in {"ductile_iron", "di"}:
            notes.append(
                "Ductile iron is assessed on the Cast Iron potential (-0.610 V_SCE). "
                "G82 tabulates 'Cast Iron' and 'Austenitic Cast Iron' but not ductile "
                "specifically; the substitution is stated rather than silent."
            )
        if name in series:
            return {"g82_name": name, "E_VSCE": series[name], "state": "as_tabulated", "notes": notes}

    # Last resort: exact match against the raw G82 column.
    for name, e in series.items():
        if name.lower() == material.strip().lower():
            return {"g82_name": name, "E_VSCE": e, "state": "as_tabulated", "notes": notes}

    raise CorrosionPublicError(
        "UNKNOWN_GALVANIC_MATERIAL",
        f"UNKNOWN_GALVANIC_MATERIAL: '{material}' has no ASTM G82 entry. "
        f"Known aliases: {', '.join(sorted(set(_G82_ALIASES) | set(_STAINLESS_BASE)))}.",
        details={"reason_code": "UNKNOWN_GALVANIC_MATERIAL", "material": material},
    )


def infer_passivity_state(
    sulfide_mg_L: Optional[float] = None,
    dissolved_oxygen_mg_L: Optional[float] = None,
    crevice_present: Optional[bool] = None,
    sulfide_uncertainty_mg_L: Optional[float] = None,
    dissolved_oxygen_uncertainty_mg_L: Optional[float] = None,
) -> Dict[str, Any]:
    """Return the measurements without inferring an ASTM G82 table state.

    The former selector switched state for any sulfide above zero, dissolved
    oxygen below 1 mg/L, or a crevice declaration.  Those were unsourced house
    cutoffs.  Keeping this compatibility helper makes the withdrawal of that
    inference visible at every Python accessor.
    """
    numeric_inputs = {
        "sulfide_mg_L": sulfide_mg_L,
        "dissolved_oxygen_mg_L": dissolved_oxygen_mg_L,
        "sulfide_uncertainty_mg_L": sulfide_uncertainty_mg_L,
        "dissolved_oxygen_uncertainty_mg_L": (
            dissolved_oxygen_uncertainty_mg_L
        ),
    }
    for field_name, value in numeric_inputs.items():
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{field_name} must be a finite real number")
        if value is not None and value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
    if crevice_present is not None and not isinstance(crevice_present, bool):
        raise ValueError("crevice_present must be true, false, or omitted")

    reported_environment: Dict[str, Any] = {}
    if sulfide_mg_L is not None:
        reported_environment["sulfide_mg_L"] = sulfide_mg_L
    if dissolved_oxygen_mg_L is not None:
        reported_environment["dissolved_oxygen_mg_L"] = dissolved_oxygen_mg_L
    if crevice_present is not None:
        reported_environment["crevice_present"] = crevice_present

    reported_uncertainty: Dict[str, float] = {}
    if sulfide_uncertainty_mg_L is not None:
        reported_uncertainty["sulfide_mg_L"] = sulfide_uncertainty_mg_L
    if dissolved_oxygen_uncertainty_mg_L is not None:
        reported_uncertainty["dissolved_oxygen_mg_L"] = (
            dissolved_oxygen_uncertainty_mg_L
        )
    if reported_uncertainty:
        reported_environment["measurement_uncertainty"] = reported_uncertainty

    return {
        "state": None,
        "state_selection_status": "not_inferred_no_sourced_service_rule",
        "decision_threshold_applied": False,
        "reported_environment": reported_environment,
    }


def _uses_state_table(material: str) -> bool:
    key = material.strip().lower().replace(" ", "_").replace("-", "_")
    return key in _STAINLESS_BASE


def _state_scenario(material_a: str, material_b: str, state: str) -> Dict[str, Any]:
    """Resolve one caller-visible G82 table scenario without judging service."""
    try:
        a = _resolve(material_a, state)
        b = _resolve(material_b, state)
    except GalvanicStateNotTabulatedError as exc:
        return {
            "availability": "not_tabulated",
            "requested_state": state,
            "material": exc.details["material"],
            "available_states": exc.details["available_states"],
        }

    if a["E_VSCE"] == b["E_VSCE"]:
        equal_potential_materials = sorted(
            (
                {
                    "material": material_a,
                    "g82_name": a["g82_name"],
                    "E_VSCE": a["E_VSCE"],
                },
                {
                    "material": material_b,
                    "g82_name": b["g82_name"],
                    "E_VSCE": b["E_VSCE"],
                },
            ),
            key=lambda item: (
                str(item["g82_name"]).casefold(),
                str(item["material"]).casefold(),
                str(item["material"]),
            ),
        )
        return {
            "availability": "tabulated",
            "table_state": state,
            "polarity_status": "indeterminate_equal_tabulated_potential",
            "anode": None,
            "cathode": None,
            "equal_potential_materials": equal_potential_materials,
            "driving_force_V": 0.0,
            "driving_force_V_display": 0.0,
            "driving_force_mV": 0.0,
            "driving_force_mV_display": 0.0,
            "driving_force_value_standing": (
                "exact_primaries_authoritative_display_fields_rounded"
            ),
            "notes": sorted(list(a["notes"]) + list(b["notes"])),
        }

    if a["E_VSCE"] < b["E_VSCE"]:
        anode, cathode = a, b
        anode_name, cathode_name = material_a, material_b
    else:
        anode, cathode = b, a
        anode_name, cathode_name = material_b, material_a

    delta_e = cathode["E_VSCE"] - anode["E_VSCE"]
    return {
        "availability": "tabulated",
        "table_state": state,
        "polarity_status": "resolved_by_relative_tabulated_potential",
        "anode": {
            "material": anode_name,
            "g82_name": anode["g82_name"],
            "E_VSCE": anode["E_VSCE"],
        },
        "cathode": {
            "material": cathode_name,
            "g82_name": cathode["g82_name"],
            "E_VSCE": cathode["E_VSCE"],
        },
        "driving_force_V": delta_e,
        "driving_force_V_display": round(delta_e, 4),
        "driving_force_mV": delta_e * 1000.0,
        "driving_force_mV_display": round(delta_e * 1000.0, 1),
        "driving_force_value_standing": (
            "exact_primaries_authoritative_display_fields_rounded"
        ),
        "notes": list(a["notes"]) + list(b["notes"]),
    }


@enforce_tool_input_contract("corrosion_screen_galvanic")
def screen_galvanic_couple(
    material_a: str,
    material_b: str,
    area_ratio_cathode_to_anode: Optional[float] = None,
    sulfide_mg_L: Optional[float] = None,
    sulfide_uncertainty_mg_L: Optional[float] = None,
    dissolved_oxygen_mg_L: Optional[float] = None,
    dissolved_oxygen_uncertainty_mg_L: Optional[float] = None,
    crevice_present: Optional[bool] = None,
    electrolyte_note: Optional[str] = None,
) -> Dict[str, Any]:
    """Rank a bimetallic couple by ASTM G82 driving force. Emits no current.

    Example — the couple this practice specifies most often, both ways:

        >>> screen_galvanic_couple("cast_iron", "316L")["state_scenarios"]["passive"]["driving_force_mV"]
        560.0
        >>> screen_galvanic_couple("cast_iron", "304")["state_scenarios"]["active"]["driving_force_mV"]
        80.0
    """
    validate_float_domain_inputs(locals())

    if area_ratio_cathode_to_anode is not None:
        if (
            isinstance(area_ratio_cathode_to_anode, bool)
            or not isinstance(area_ratio_cathode_to_anode, Real)
            or not math.isfinite(float(area_ratio_cathode_to_anode))
            or area_ratio_cathode_to_anode <= 0.0
        ):
            raise ValueError(
                "area_ratio_cathode_to_anode must be a finite positive number "
                "when reported"
            )

    state_report = infer_passivity_state(
        sulfide_mg_L,
        dissolved_oxygen_mg_L,
        crevice_present,
        sulfide_uncertainty_mg_L,
        dissolved_oxygen_uncertainty_mg_L,
    )
    if electrolyte_note is not None:
        state_report["reported_environment"]["electrolyte_note"] = electrolyte_note
    states = (
        ("active", "passive")
        if _uses_state_table(material_a) or _uses_state_table(material_b)
        else ("as_tabulated",)
    )
    scenarios = {
        state: _state_scenario(material_a, material_b, state)
        for state in states
    }

    warnings: List[str] = []
    for scenario in scenarios.values():
        warnings.extend(scenario.get("notes", []))
    warnings.append(
        "ASTM G82 potentials are open-circuit values in flowing seawater. They "
        "rank a couple; they do not describe kinetics in this electrolyte."
    )
    if electrolyte_note:
        warnings.append(f"Electrolyte: {electrolyte_note}")

    area_text = (
        "The reported area ratio is preserved without categorical cutoffs"
        if area_ratio_cathode_to_anode is not None
        else "No area ratio was reported or assumed"
    )
    warnings.append(
        "No compatibility band or recommendation is emitted. The former "
        "MIL-STD-889 thresholds were not verified against a primary source and "
        f"are suppressed. {area_text} because this tool has no polarization "
        "current."
    )

    return {
        "state_selection_status": state_report["state_selection_status"],
        "decision_threshold_applied": state_report["decision_threshold_applied"],
        "reported_environment": state_report["reported_environment"],
        "state_scenarios": scenarios,
        "area_ratio_cathode_to_anode": area_ratio_cathode_to_anode,
        "decision_status": "suppressed_no_sourced_compatibility_threshold",
        "validation_standing": {
            "driving_force": "ASTM_G82_open_circuit_ranking_only",
            "area_ratio": (
                "reported_not_evaluated"
                if area_ratio_cathode_to_anode is not None
                else "not_reported"
            ),
            "compatibility_verdict": "suppressed_no_primary_source",
        },
        # Stated explicitly so nobody looks for one.
        "corrosion_rate_mm_y": None,
        "galvanic_current": None,
        "why_no_rate": (
            "This is a screening tier. G82 open-circuit potentials rank a couple "
            "but carry no kinetics; a current would require polarization behaviour "
            "for both members in this electrolyte. The server's former kinetic "
            "accessor is withdrawn and no galvanic rate is available here."
        ),
        "warnings": warnings,
        "provenance": {
            "model": "ASTM_G82_screening",
            "version": "1.0",
            "validation_dataset": None,
            "confidence": "screening_data_only",
            "sources": [
                "data/astm_g82_galvanic_series.csv (ASTM G82 potentials)",
            ],
            "assumptions": [
                "Open-circuit potentials are seawater values and are not shifted "
                "for this electrolyte; they inform ranking only.",
                "No passivity state is inferred from bulk sulfide, dissolved "
                "oxygen, or a crevice declaration; all available G82 table "
                "states are returned as separate scenarios.",
            ],
            "warnings": warnings,
        },
    }
