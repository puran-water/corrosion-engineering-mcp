'Dissolved-sulfide chemistry for corrosion assessment.'

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, require_representable_nonzero_result, validate_float_domain_inputs
from corrosion_engineering_mcp.utils.index_presentation import format_nonzero_fixed

# ---------------------------------------------------------------------------
# First dissociation:  H2S(aq) = H+ + HS-
# ---------------------------------------------------------------------------
# [PQC] gives the association reaction HS- + H+ = H2S with the analytical
# expression log K = A1 + A2*T + A3/T. pKa1 of the acid equals log K of the
# association, so the same coefficients serve directly.
_PKA1_A1: float = -11.17
_PKA1_A2: float = 0.02386
_PKA1_A3: float = 3279.0

# The analytical form is fitted over roughly 0-300 C in [PQC]; we advertise the
# narrower band we have actually cross-checked against PHREEQC (see
# tests/test_sulfide_exposure.py, which asserts agreement to 0.01 pKa units
# after the activity correction below).
PKA1_VERIFIED_T_C = (10.0, 55.0)

# INTERNAL_SCREENING_RULE_SULFIDE_HIGH_PH_SCOPE_V1
# The shipped phreeqc.dat legacy row is ``HS- = S-2 + H+``, ``-log_k 12.918``.
# Under that disputed legacy picture, the S2- share of HS- + S2- at pH 12 is
# 1/(1 + 10**(12.918 - 12)) = 0.107765... (10.8%, rounded upward to 11%).
# It grows above pH 12.  [MAY18]'s readable abstract instead recommends
# expunging S2-(aq).  The pictures therefore diverge materially above this
# explicit internal boundary.  It is a confidence rule, not a species-onset
# threshold and not a pH boundary attributed to May et al.
HIGH_PH_SPECIATION_SCOPE_BOUNDARY = 12.0

MAY_S2_AQUEOUS_CITATION_RECORD = {
    "doi": "10.1039/C8CC00187A",
    "title": "Goodbye to S2- in aqueous solution",
    "publication_year": 2018,
    "authors": [
        "Peter M. May",
        "David Bátka",
        "Glenn Hefter",
        "Erich Königsberger",
        "Darren Rowland",
    ],
    "authors_as_printed_by_publisher": [
        "P. M. May",
        "D. Batka",
        "G. Hefter",
        "E. Königsberger",
        "D. Rowland",
    ],
    "author_name_reconciliation": {
        "publisher_record": "RSC prints the second author as D. Batka.",
        "expanded_metadata": (
            "OpenAlex and Semantic Scholar expand the second author as "
            "David Bátka."
        ),
        "decision": (
            "Retain the expanded full name and separately preserve the "
            "publisher-printed form; neither source representation is silently "
            "substituted for the other."
        ),
    },
    "section": "Abstract",
    "access_status": "closed_access_abstract_only",
    "content_support": (
        "The abstract reports new Raman spectra of Na2S dissolved in "
        "hyper-concentrated NaOH(aq) and CsOH(aq), says they cast serious doubt "
        "on the widely assumed existence of S2-(aq), says S2-(aq) should be "
        "expunged from the chemical literature, and says thermodynamic databases "
        "involving sulfide minerals need careful revision."
    ),
    "content_limit": (
        "Full text is paywalled and was not read. This record makes no "
        "page-anchored claim and does not attribute a numeric pKa2 range, "
        "glass-electrode limitation, or pH decision boundary to May et al. The "
        "pH 12 boundary is the separately labelled internal screening rule."
    ),
}

HIGH_PH_SPECIATION_SCOPE_RULE = {
    "rule_id": "INTERNAL_SCREENING_RULE_SULFIDE_HIGH_PH_SCOPE_V1",
    "rationale": (
        "Legacy and modern treatments of the second dissociation diverge "
        "materially above pH 12; the two-species H2S/HS- display is "
        "reduced-confidence and no S2- fraction is emitted."
    ),
}

# [DAV] Davies constant at 25 C. Weakly temperature dependent; the variation
# across 10-55 C is smaller than the uncertainty this correction removes.
_DAVIES_A: float = 0.509


def pKa1_thermodynamic(temperature_C: float = 25.0) -> float:
    """Zero-ionic-strength pKa1 of H2S. [PQC]

    >>> round(pKa1_thermodynamic(25.0), 3)
    6.942
    >>> round(pKa1_thermodynamic(35.0), 3)
    6.823
    """
    T = temperature_C + 273.15
    return _PKA1_A1 + _PKA1_A2 * T + _PKA1_A3 / T


def davies_log_gamma_monovalent(ionic_strength_M: float) -> float:
    """log10 of the activity coefficient of a singly-charged ion. [DAV]

    Returns a NEGATIVE number: activity is below concentration.
    """
    if ionic_strength_M <= 0:
        return 0.0
    sqrt_I = math.sqrt(ionic_strength_M)
    return -_DAVIES_A * (sqrt_I / (1.0 + sqrt_I) - 0.3 * ionic_strength_M)


def pKa1_apparent(temperature_C: float = 25.0, ionic_strength_M: float = 0.0) -> float:
    """pKa1 on a CONCENTRATION basis, which is what a lab result is.

    H2S is neutral and HS- is singly charged, so ionic strength shifts the
    apparent pKa: the charged product is stabilised, and the acid looks
    stronger. Ignoring this is a real error in brine and digestate service - at
    I = 0.5 M the shift is about 0.15 pKa units, which moves the volatile
    fraction by several percent.

    The correction is what reconciles this module with PHREEQC, which works in
    activities. Without it the two disagree by a constant ~0.05 at I = 0.01 M.
    """
    return pKa1_thermodynamic(temperature_C) + davies_log_gamma_monovalent(ionic_strength_M)


@dataclass(frozen=True)
class SulfideSpeciation:
    """Split of a total dissolved sulfide between H2S(aq) and HS-.

    S2- is deliberately absent. See the module docstring and [MAY18].
    """

    total_sulfide_mg_L_as_S: float
    pH: float
    temperature_C: float
    ionic_strength_M: float
    pKa1: float
    fraction_H2S: float
    fraction_HS: float
    H2S_aq_mg_L_as_S: float
    HS_mg_L_as_S: float
    volatile_fraction: float
    extrapolated_beyond_verified_range: bool
    high_pH_scope_degraded: bool
    speciation_scope_status: str
    S2_minus_reported: bool = False
    S2_minus_standing: str = "not_modelled_no_credible_experimental_evidence"
    S2_minus_note: str = (
        "S2- is NOT reported. The abstract of May et al. (2018), "
        "doi:10.1039/C8CC00187A, reports new Raman spectra in "
        "hyper-concentrated hydroxide that cast serious doubt on S2-(aq) and "
        "recommends expunging it from the chemical literature; this accessor "
        "therefore does not invent a pKa2 or an S2- fraction. Citation standing "
        "is abstract-only; full text was not read. A laboratory 'sulfide as "
        "S2-' result is a TOTAL sulfide measurement named by convention, not a "
        "free-ion concentration - use it as total_sulfide."
    )
    notes: List[str] = field(default_factory=list)


def speciate_dissolved_sulfide(
    total_sulfide_mg_L_as_S: float,
    pH: float,
    temperature_C: float = 25.0,
    ionic_strength_M: float = 0.0,
) -> SulfideSpeciation:
    """Split total dissolved sulfide into H2S(aq) and HS-.

    This is the actionable, high-confidence part of sulfide assessment, and the
    reason two plants reporting "2 mg/L sulfide" can have completely different
    problems:

    >>> round(speciate_dissolved_sulfide(2.0, pH=6.4).fraction_H2S, 2)
    0.78
    >>> round(speciate_dissolved_sulfide(2.0, pH=7.6).fraction_H2S, 2)
    0.18

    At pH 6.4 more than three quarters of it is volatile and headed for the
    headspace; at pH 7.6 more than four fifths stays in the liquid against the
    passive film. Same number on the lab sheet.
    """
    validate_float_domain_inputs(
        {
            "total_sulfide_mg_L_as_S": total_sulfide_mg_L_as_S,
            "pH": pH,
            "temperature_C": temperature_C,
            "ionic_strength_M": ionic_strength_M,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    if total_sulfide_mg_L_as_S < 0:
        raise ValueError("total sulfide cannot be negative")
    if ionic_strength_M < 0:
        raise ValueError("ionic strength cannot be negative")

    pKa1 = pKa1_apparent(temperature_C, ionic_strength_M)
    # H2S / (H2S + HS-) = 1 / (1 + 10^(pH - pKa1))
    f_h2s = 1.0 / (1.0 + 10.0 ** (pH - pKa1))
    f_hs = 1.0 - f_h2s

    temperature_extrapolated = not (
        PKA1_VERIFIED_T_C[0] <= temperature_C <= PKA1_VERIFIED_T_C[1]
    )
    activity_model_extrapolated = ionic_strength_M > 0.5
    extrapolated = temperature_extrapolated or activity_model_extrapolated
    high_pH_scope_degraded = pH > HIGH_PH_SPECIATION_SCOPE_BOUNDARY
    speciation_scope_status = (
        "degraded_high_pH_measurement_and_pka2_controversy"
        if high_pH_scope_degraded
        else "two_species_h2s_hs_scope_supported"
    )
    notes = [
        f"pKa1 {format_nonzero_fixed(pKa1, 3)} at "
        f"{format_nonzero_fixed(temperature_C, 1)} C, "
        f"I = {ionic_strength_M:g} M [phreeqc.dat "
        f"analytical form + Davies activity correction]. Only H2S is volatile."
    ]
    if temperature_extrapolated:
        notes.append(
            "EXTRAPOLATED: "
            f"{repr(float(temperature_C))} C is outside the "
            f"{PKA1_VERIFIED_T_C[0]}-{PKA1_VERIFIED_T_C[1]} C band cross-checked "
            f"against PHREEQC."
        )
    if activity_model_extrapolated:
        notes.append(
            f"Ionic strength {repr(float(ionic_strength_M))} M is beyond the Davies equation's "
            f"reliable range (~0.5 M). Treat the split as indicative; a Pitzer "
            f"model is needed for brine."
        )
    if high_pH_scope_degraded:
        notes.append(
            "REDUCED HIGH-pH SCOPE: "
            "INTERNAL_SCREENING_RULE_SULFIDE_HIGH_PH_SCOPE_V1 applies above "
            "pH 12. Legacy and modern treatments of the second dissociation "
            "diverge materially, while the readable May et al. abstract "
            "recommends expunging S2-(aq). H2S/HS- is displayed without an S2- "
            "fraction and must not be treated as high-confidence speciation."
        )

    return SulfideSpeciation(
        total_sulfide_mg_L_as_S=total_sulfide_mg_L_as_S,
        pH=pH,
        temperature_C=temperature_C,
        ionic_strength_M=ionic_strength_M,
        pKa1=pKa1,
        fraction_H2S=f_h2s,
        fraction_HS=f_hs,
        H2S_aq_mg_L_as_S=total_sulfide_mg_L_as_S * f_h2s,
        HS_mg_L_as_S=total_sulfide_mg_L_as_S * f_hs,
        volatile_fraction=f_h2s,
        extrapolated_beyond_verified_range=extrapolated,
        high_pH_scope_degraded=high_pH_scope_degraded,
        speciation_scope_status=speciation_scope_status,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Gas / liquid partition
# ---------------------------------------------------------------------------
# CORRECTED 2026-08-08. The previous derivation was wrong in its provenance, and
# the "cross-check" that justified it was not a cross-check.
#
# It combined the NOMINAL -log_k values from [PQC]:
#     log K_H = log_k(H2S(g)) + pKa1 = -7.9759 + 6.994 = -0.982 -> 0.1043
# and reported 2% agreement with a literature value as two independent sources
# confirming each other. But PHREEQC does not use the nominal log_k when an
# `-analytic` expression is present, and both of these reactions have one.
# Evaluating what PHREEQC actually evaluates, at 298.15 K:
#     log K_assoc (analytic) = +6.9417   (nominal 6.994)
#     log K_gas   (analytic) = -7.9924   (nominal -7.9759)
#     log K_H                = -1.0507   ->  K_H = 0.0890 mol/(L.atm)
# So the database value is 0.0890, not 0.1043, and the agreement claimed with
# literature was between a number PHREEQC ignores and a compilation.
#
# THE RESOLUTION IS NOT SIMPLY "USE 0.0890". The H2S (CAS 7783-06-4) entries
# in [SAN] Appendix A1.8, machine-read from the publisher on 2026-08-10, give:
#     H^cp = 1.0e-3 mol/(m3.Pa), leading literature-review entry
#                                             ->  0.1013 mol/(L.atm)
# with the two measured anchors retained for this model's existing band
#     8.6e-4 to 1.1e-3 mol/(m3.Pa)           ->  0.0871 to 0.1115
# phreeqc.dat's 0.0890 sits INSIDE that experimental band, near its low end. The
# carried band is 28% wide. Two values 12% apart are therefore not a contradiction
# to be resolved by picking one - they are two points in the band, and quoting
# either to three figures as though it were settled is the actual error.
#
# We anchor on the leading literature-review value in the official [SAN]
# compilation rather than one database's fit. It was machine-verified here and
# is a value a reader can check. The phreeqc.dat figure and the explicitly
# non-exhaustive carried band travel alongside that value.
HENRY_H2S_MOL_PER_L_ATM_25C: float = 0.1013  # [SAN] leading entry, machine-read
HENRY_H2S_PHREEQC_ANALYTIC_25C: float = 0.0890  # [PQC] analytic form
HENRY_H2S_REPORTED_RANGE_25C = (0.0871, 0.1115)  # [SAN] carried measured anchors

# Publisher bibliographic identity and the exact published content used for the
# dual-phase consistency tolerance.  Keeping the content claim with the values
# prevents a resolving DOI from being mistaken for evidence that it does not
# actually contain.
SANDER_H2S_HENRY_CITATION_RECORD = {
    "doi": "10.5194/acp-23-10901-2023",
    "title": (
        "Compilation of Henry's law constants (version 5.0.0) for water as solvent"
    ),
    "publication_year": 2023,
    "authors": ["Rolf Sander"],
    "metadata_source": "Atmospheric Chemistry and Physics publisher",
    "metadata_retrieved_on": "2026-08-10",
    "content_location": "Appendix A1.8, Table A1.8 (Sulfur), hydrogen sulfide entry",
    "content_support": (
        "The H2S entry retains 1.0e-3 mol/(m3.Pa) and 2100 K as the leading "
        "literature-review value in the official compilation. It also retains "
        "measured anchors "
        "8.6e-4 and 1.1e-3 mol/(m3.Pa), which convert to the carried "
        "0.0871-0.1115 mol/(L.atm) band at 298.15 K."
    ),
    "content_limit": (
        "The carried band uses the same two measured anchors as the previous "
        "implementation and is not the full spread of every 2023 table entry. "
        "The compilation does not define a field-measurement tolerance, rule out "
        "ventilation or stripping, or make a reconciled value a corrosion-rate "
        "threshold."
    ),
}

# van't Hoff slope for H2S dissolution, [SAN]: -d ln H / d(1/T) = 2100 K.
# Machine-read alongside the value; the determinations [SAN] compiles for H2S
# cluster at 2000-2300 K, so this is the modal figure, not a single study's.
_HENRY_DLNH_DINVT_K: float = 2100.0

ATM_PER_BAR: float = 1.0 / 1.01325
_M_S: float = 32.06  # g/mol, sulfide reported as S
H2S_GAS_COMPOSITION_MAX_PPMV: float = 1_000_000.0

# INTERNAL_SCREENING_RULE_H2S_AQUEOUS_MOLE_FRACTION_V1
# Henry proportionality is a dilute-solution relation [SAN].  This implementation
# therefore stops at x(total dissolved sulfide) = 0.01: at least 99 mol% of the
# water-plus-sulfide basis remains solvent water.  The 0.01 choice is an explicit
# internal model-applicability rule, not a solubility limit or phase-equilibrium
# datum. Water molarity uses 1000 g/L / 18.01528 g/mol at ambient density.
_WATER_MOLARITY_MOL_L: float = 55.508
HENRY_INVERSE_MAX_TOTAL_SULFIDE_MOLE_FRACTION: float = 0.01
HENRY_INVERSE_DILUTE_RULE = {
    "rule_id": "INTERNAL_SCREENING_RULE_H2S_AQUEOUS_MOLE_FRACTION_V1",
    "rule": "total dissolved sulfide mole fraction <= 0.01",
    "maximum_total_sulfide_mole_fraction": (
        HENRY_INVERSE_MAX_TOTAL_SULFIDE_MOLE_FRACTION
    ),
    "rationale": (
        "Henry proportionality is defined for dilute solution; retain at least "
        "99 mol% solvent water on the water-plus-total-sulfide basis. This is a "
        "model-applicability screen, not a solubility claim."
    ),
    "source_context": (
        "Sander 2023 states Henry's equilibrium ratio is constant for a dilute "
        "solution; the numerical 0.01 criterion is this server's internal rule."
    ),
}


class SulfideInputOutOfRangeError(CorrosionPublicError, ValueError):
    """A sulfide phase measurement violates a physical bound."""

    def __init__(
        self,
        field: str,
        value: float,
        *,
        maximum: float | None = None,
    ) -> None:
        super().__init__(
            "SULFIDE_INPUT_OUT_OF_RANGE",
            f"SULFIDE_INPUT_OUT_OF_RANGE: {field}={value!r} must be finite, "
            "non-negative"
            + (
                f" and no greater than {repr(float(maximum))}"
                if maximum is not None
                else ""
            )
            + ".",
            details={
                "reason_code": "SULFIDE_INPUT_OUT_OF_RANGE",
                "field": field,
                "value": value,
                "minimum": 0.0,
                "maximum": maximum,
            },
        )


def validate_h2s_gas_composition_ppmv(
    value: float | None,
    *,
    field: str,
) -> None:
    """Enforce the shared physical H2S gas-composition contract."""

    if value is not None and (
        not math.isfinite(value)
        or not 0.0 <= value <= H2S_GAS_COMPOSITION_MAX_PPMV
    ):
        raise SulfideInputOutOfRangeError(
            field,
            value,
            maximum=H2S_GAS_COMPOSITION_MAX_PPMV,
        )


class SulfideEquilibriumOutOfPhysicalDomainError(
    CorrosionPublicError, ValueError
):
    """The dilute Henry-law calculation requires more than 100% H2S gas."""

    def __init__(
        self,
        *,
        total_sulfide_mg_L_as_S: float,
        pH: float,
        temperature_C: float,
        total_pressure_bar_abs: float,
    ) -> None:
        super().__init__(
            "SULFIDE_EQUILIBRIUM_OUT_OF_PHYSICAL_DOMAIN",
            "SULFIDE_EQUILIBRIUM_OUT_OF_PHYSICAL_DOMAIN: the declared liquid "
            "condition would require H2S partial pressure above total absolute "
            "pressure. The liquid must DEGAS, and the model's dilute-solution "
            "Henry-law basis is exceeded; no impossible headspace composition "
            "is returned.",
            details={
                "reason_code": "SULFIDE_EQUILIBRIUM_OUT_OF_PHYSICAL_DOMAIN",
                "status": "out_of_physical_domain_degas",
                "total_sulfide_mg_L_as_S": total_sulfide_mg_L_as_S,
                "pH": pH,
                "temperature_C": temperature_C,
                "total_pressure_bar_abs": total_pressure_bar_abs,
                "physical_contract": {
                    "pH2S_bar_maximum": "total_pressure_bar_abs",
                    "H2S_headspace_ppmv_maximum": (
                        H2S_GAS_COMPOSITION_MAX_PPMV
                    ),
                },
                "model_basis_status": "dilute_solution_henry_law_exceeded",
                "phase_response": "degas",
            },
        )


class SulfideHenryOutOfDiluteDomainError(
    CorrosionPublicError, ValueError
):
    """Base error for either direction leaving the shared Henry dilute domain."""


class SulfideHenryInverseOutOfDiluteDomainError(
    SulfideHenryOutOfDiluteDomainError
):
    """A gas-to-liquid Henry inversion would create a non-dilute solution."""

    def __init__(
        self,
        *,
        H2S_ppmv: float,
        pH: float,
        temperature_C: float,
        total_pressure_bar_abs: float,
        total_sulfide_mol_L: float,
        total_sulfide_mole_fraction: float,
        maximum_headspace_H2S_ppmv: float,
    ) -> None:
        super().__init__(
            "SULFIDE_HENRY_INVERSE_OUT_OF_DILUTE_DOMAIN",
            "SULFIDE_HENRY_INVERSE_OUT_OF_DILUTE_DOMAIN: the declared gas "
            "condition implies a non-dilute total dissolved sulfide solution. "
            "Linear Henry inversion is stopped; use a nonideal electrolyte/VLE "
            "model with explicit phase response.",
            details={
                "reason_code": "SULFIDE_HENRY_INVERSE_OUT_OF_DILUTE_DOMAIN",
                "conversion_direction": "gas_to_liquid",
                "status": "out_of_domain_non_dilute_solution",
                "phase_response": "nonideal_vle_required",
                "H2S_headspace_ppmv": H2S_ppmv,
                "pH": pH,
                "temperature_C": temperature_C,
                "total_pressure_bar_abs": total_pressure_bar_abs,
                "computed_total_sulfide_mol_L": total_sulfide_mol_L,
                "total_sulfide_mole_fraction": total_sulfide_mole_fraction,
                "maximum_headspace_H2S_ppmv_for_declared_conditions": (
                    maximum_headspace_H2S_ppmv
                ),
                "screening_rule": dict(HENRY_INVERSE_DILUTE_RULE),
                "result_emitted": False,
            },
        )


class SulfideHenryForwardOutOfDiluteDomainError(
    SulfideHenryOutOfDiluteDomainError
):
    """A liquid-to-gas Henry calculation was requested for a non-dilute liquid."""

    def __init__(
        self,
        *,
        total_sulfide_mg_L_as_S: float,
        pH: float,
        temperature_C: float,
        total_pressure_bar_abs: float,
        total_sulfide_mol_L: float,
        total_sulfide_mole_fraction: float,
    ) -> None:
        super().__init__(
            "SULFIDE_HENRY_FORWARD_OUT_OF_DILUTE_DOMAIN",
            "SULFIDE_HENRY_FORWARD_OUT_OF_DILUTE_DOMAIN: the declared liquid "
            "condition is a non-dilute total dissolved sulfide solution. Linear "
            "Henry partitioning to a gas phase is stopped; use a nonideal "
            "electrolyte/VLE model with explicit phase response.",
            details={
                "reason_code": "SULFIDE_HENRY_FORWARD_OUT_OF_DILUTE_DOMAIN",
                "conversion_direction": "liquid_to_gas",
                "status": "out_of_domain_non_dilute_solution",
                "phase_response": "nonideal_vle_required",
                "total_sulfide_mg_L_as_S": total_sulfide_mg_L_as_S,
                "pH": pH,
                "temperature_C": temperature_C,
                "total_pressure_bar_abs": total_pressure_bar_abs,
                "computed_total_sulfide_mol_L": total_sulfide_mol_L,
                "total_sulfide_mole_fraction": total_sulfide_mole_fraction,
                "screening_rule": dict(HENRY_INVERSE_DILUTE_RULE),
                "result_emitted": False,
            },
        )


def _total_sulfide_mole_fraction(total_sulfide_mol_L: float) -> float:
    """Return x(total sulfide) on the rule's water-plus-sulfide mole basis."""

    return total_sulfide_mol_L / (
        _WATER_MOLARITY_MOL_L + total_sulfide_mol_L
    )


def henry_constant_H2S(temperature_C: float = 25.0) -> float:
    """Henry constant for H2S in mol/(L.atm). Rises as temperature falls. [SAN]

    Anchored on the leading literature-review value in the official [SAN]
    compilation, 0.1013 at 25 C. Note that phreeqc.dat's analytical expression
    gives 0.0890 at the same temperature. This model retains the
    0.0871-0.1115 measured-anchor band unchanged; it is explicitly not the full
    spread of every 2023 table entry. Any headspace figure derived from this
    carries that band; see
    HENRY_H2S_REPORTED_RANGE_25C.
    """
    T = temperature_C + 273.15
    return HENRY_H2S_MOL_PER_L_ATM_25C * math.exp(
        _HENRY_DLNH_DINVT_K * (1.0 / T - 1.0 / 298.15)
    )


def maximum_headspace_H2S_ppmv_for_dilute_domain(
    pH: float,
    temperature_C: float = 25.0,
    total_pressure_bar_abs: float = 1.01325,
    ionic_strength_M: float = 0.0,
) -> float:
    """Maximum gas concentration admitted by the declared internal dilute rule."""

    if not math.isfinite(total_pressure_bar_abs) or total_pressure_bar_abs <= 0.0:
        raise ValueError("total_pressure_bar_abs must be finite and greater than zero")
    pKa1 = pKa1_apparent(temperature_C, ionic_strength_M)
    f_h2s = 1.0 / (1.0 + 10.0 ** (pH - pKa1))
    maximum_total_mol_L = (
        HENRY_INVERSE_MAX_TOTAL_SULFIDE_MOLE_FRACTION
        / (1.0 - HENRY_INVERSE_MAX_TOTAL_SULFIDE_MOLE_FRACTION)
        * _WATER_MOLARITY_MOL_L
    )
    maximum_h2s_mol_L = maximum_total_mol_L * f_h2s
    maximum_p_h2s_atm = maximum_h2s_mol_L / henry_constant_H2S(temperature_C)
    total_atm = total_pressure_bar_abs * ATM_PER_BAR
    return min(
        H2S_GAS_COMPOSITION_MAX_PPMV,
        1.0e6 * maximum_p_h2s_atm / total_atm,
    )


def maximum_total_sulfide_mg_L_for_dilute_domain() -> float:
    """Maximum liquid input admitted by the same declared dilute rule.

    Callers and the forward gate compare against this exact float.  Rebuilding
    the mole fraction from a displayed maximum changes equality at binary64
    seams, which is the same operation-order defect the inverse gate had.
    """

    maximum_total_mol_L = (
        HENRY_INVERSE_MAX_TOTAL_SULFIDE_MOLE_FRACTION
        / (1.0 - HENRY_INVERSE_MAX_TOTAL_SULFIDE_MOLE_FRACTION)
        * _WATER_MOLARITY_MOL_L
    )
    return maximum_total_mol_L * _M_S * 1_000.0


def _equilibrium_headspace_from_validated_dilute_liquid(
    total_sulfide_mg_L_as_S: float,
    pH: float,
    temperature_C: float,
    total_pressure_bar_abs: float,
    ionic_strength_M: float,
) -> tuple[float, SulfideSpeciation]:
    """Convert a liquid already admitted by one strict Henry-domain gate.

    This private path exists for the facade's post-reconciliation display.  A
    gas-derived liquid has already passed the inverse gate; a dual-input liquid
    has passed one gate in each direction.  Reapplying the other gate would make
    binary round-trip noise choose the public error direction.  The physical
    partial-pressure ceiling remains independently enforced here.
    """

    spec = speciate_dissolved_sulfide(
        total_sulfide_mg_L_as_S,
        pH,
        temperature_C,
        ionic_strength_M,
    )
    h2s_mol_L = (spec.H2S_aq_mg_L_as_S / 1000.0) / _M_S
    K_H = henry_constant_H2S(temperature_C)
    p_h2s_atm = h2s_mol_L / K_H
    total_atm = total_pressure_bar_abs * ATM_PER_BAR
    if p_h2s_atm > total_atm:
        # Permit only binary-float noise at the exact phase boundary.  The
        # inverse conversion of the displayed 1,000,000 ppmv bound must
        # round-trip, but a genuinely supersaturated liquid is a phase-behaviour
        # problem and cannot be represented as a gas composition above unity.
        if math.isclose(p_h2s_atm, total_atm, rel_tol=4.0e-15, abs_tol=0.0):
            p_h2s_atm = total_atm
        else:
            raise SulfideEquilibriumOutOfPhysicalDomainError(
                total_sulfide_mg_L_as_S=total_sulfide_mg_L_as_S,
                pH=pH,
                temperature_C=temperature_C,
                total_pressure_bar_abs=total_pressure_bar_abs,
            )
    raw_ppmv = 1e6 * p_h2s_atm / total_atm
    if raw_ppmv == 0.0 and total_sulfide_mg_L_as_S != 0.0:
        # Today's input floor makes this unreachable.  If it is relaxed, fold
        # the reciprocal unit conversions together before the final multiply;
        # this recovers a representable ppmv whose mol/L intermediate is not.
        raw_ppmv = total_sulfide_mg_L_as_S * (
            spec.fraction_H2S
            * 1.0e6
            / (1000.0 * _M_S * K_H * total_atm)
        )
    require_representable_nonzero_result(
        "dissolved_sulfide_to_equilibrium_headspace_ppmv",
        raw_ppmv,
        operands={
            "total_sulfide_mg_L_as_S": total_sulfide_mg_L_as_S,
            "fraction_H2S": spec.fraction_H2S,
            "henry_constant_mol_L_atm": K_H,
            "total_pressure_atm": total_atm,
        },
    )
    ppmv = min(H2S_GAS_COMPOSITION_MAX_PPMV, raw_ppmv)
    return ppmv, spec


def equilibrium_headspace_H2S_ppmv(
    total_sulfide_mg_L_as_S: float,
    pH: float,
    temperature_C: float = 25.0,
    total_pressure_bar_abs: float = 1.01325,
    ionic_strength_M: float = 0.0,
) -> tuple[float, SulfideSpeciation]:
    """Headspace H2S at equilibrium with a sulfide-bearing liquid.

    ONLY the H2S fraction partitions - HS- has no vapour pressure - which is why
    pH controls headspace H2S far more strongly than total sulfide does. This is
    the link between a liquid-phase lab result and the gas-phase number that
    drives crown corrosion and the sour-service screen.

    An equilibrium ceiling, not a prediction: real headspace concentrations
    depend on ventilation, turbulence and residence time, and a well-ventilated
    space sits far below this. Treat it as "what this liquid could support".

    Raises:
        SulfideHenryForwardOutOfDiluteDomainError: If the total dissolved
            sulfide exceeds the shared Henry-law dilute-solution rule.
        SulfideEquilibriumOutOfPhysicalDomainError: If the dilute-solution
            calculation would require H2S partial pressure above total pressure.
    """
    validate_float_domain_inputs(
        {
            "total_sulfide_mg_L_as_S": total_sulfide_mg_L_as_S,
            "pH": pH,
            "temperature_C": temperature_C,
            "total_pressure_bar_abs": total_pressure_bar_abs,
            "ionic_strength_M": ionic_strength_M,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    if not math.isfinite(total_pressure_bar_abs) or total_pressure_bar_abs <= 0.0:
        raise ValueError("total_pressure_bar_abs must be finite and greater than zero")

    # The caller-visible maximum is the comparison authority.  Reconstructing
    # x(total sulfide) here through a different operation order can reject the
    # advertised equality or admit its next representable float.
    total_sulfide_mol_L = (
        total_sulfide_mg_L_as_S / 1000.0
    ) / _M_S
    total_sulfide_mole_fraction = _total_sulfide_mole_fraction(
        total_sulfide_mol_L
    )
    if (
        total_sulfide_mg_L_as_S
        > maximum_total_sulfide_mg_L_for_dilute_domain()
    ):
        raise SulfideHenryForwardOutOfDiluteDomainError(
            total_sulfide_mg_L_as_S=total_sulfide_mg_L_as_S,
            pH=pH,
            temperature_C=temperature_C,
            total_pressure_bar_abs=total_pressure_bar_abs,
            total_sulfide_mol_L=total_sulfide_mol_L,
            total_sulfide_mole_fraction=total_sulfide_mole_fraction,
        )

    return _equilibrium_headspace_from_validated_dilute_liquid(
        total_sulfide_mg_L_as_S,
        pH,
        temperature_C,
        total_pressure_bar_abs,
        ionic_strength_M,
    )


def dissolved_sulfide_from_headspace(
    H2S_ppmv: float,
    pH: float,
    temperature_C: float = 25.0,
    total_pressure_bar_abs: float = 1.01325,
    ionic_strength_M: float = 0.0,
) -> float:
    """Total dissolved sulfide in equilibrium with a measured headspace, mg/L as S.

    The inverse direction, for the common case where the gas is instrumented and
    the liquid is not.
    """
    validate_float_domain_inputs(
        {
            "H2S_ppmv": H2S_ppmv,
            "pH": pH,
            "temperature_C": temperature_C,
            "total_pressure_bar_abs": total_pressure_bar_abs,
            "ionic_strength_M": ionic_strength_M,
        },
        minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    )
    if (
        not math.isfinite(H2S_ppmv)
        or not 0.0 <= H2S_ppmv <= H2S_GAS_COMPOSITION_MAX_PPMV
    ):
        raise ValueError("H2S_ppmv must be finite and within 0 to 1,000,000")
    if not math.isfinite(total_pressure_bar_abs) or total_pressure_bar_abs <= 0.0:
        raise ValueError("total_pressure_bar_abs must be finite and greater than zero")
    maximum_headspace_H2S_ppmv = (
        maximum_headspace_H2S_ppmv_for_dilute_domain(
            pH=pH,
            temperature_C=temperature_C,
            total_pressure_bar_abs=total_pressure_bar_abs,
            ionic_strength_M=ionic_strength_M,
        )
    )
    # This public helper is the single boundary authority.  Comparing a ppmv
    # input against a mole fraction recomputed below used a different floating-
    # point operation order: across the Round-24 grid it rejected 23 advertised
    # equalities and admitted 137 +1-ULP values.
    inverse_outside_dilute_domain = H2S_ppmv > maximum_headspace_H2S_ppmv
    total_atm = total_pressure_bar_abs * ATM_PER_BAR
    p_h2s_atm = (H2S_ppmv * 1e-6) * total_atm
    h2s_mol_L = p_h2s_atm * henry_constant_H2S(temperature_C)
    pKa1 = pKa1_apparent(temperature_C, ionic_strength_M)
    f_h2s = 1.0 / (1.0 + 10.0 ** (pH - pKa1))
    if f_h2s <= 0:
        raise ValueError("degenerate speciation")
    total_sulfide_mol_L = h2s_mol_L / f_h2s
    total_sulfide_mole_fraction = _total_sulfide_mole_fraction(
        total_sulfide_mol_L
    )
    if inverse_outside_dilute_domain:
        raise SulfideHenryInverseOutOfDiluteDomainError(
            H2S_ppmv=H2S_ppmv,
            pH=pH,
            temperature_C=temperature_C,
            total_pressure_bar_abs=total_pressure_bar_abs,
            total_sulfide_mol_L=total_sulfide_mol_L,
            total_sulfide_mole_fraction=total_sulfide_mole_fraction,
            maximum_headspace_H2S_ppmv=maximum_headspace_H2S_ppmv,
        )
    result_mg_L = total_sulfide_mol_L * _M_S * 1000.0
    if result_mg_L == 0.0 and H2S_ppmv != 0.0:
        # Defense in depth behind the public input floor.  Combining all scale
        # factors first can recover a representable final concentration even
        # when the ppmv-to-atmosphere intermediate itself is subnormal.
        result_mg_L = H2S_ppmv * (
            total_atm
            * 1.0e-6
            * henry_constant_H2S(temperature_C)
            / f_h2s
            * _M_S
            * 1000.0
        )
    return require_representable_nonzero_result(
        "equilibrium_headspace_ppmv_to_dissolved_sulfide",
        result_mg_L,
        operands={
            "H2S_ppmv": H2S_ppmv,
            "total_pressure_atm": total_atm,
            "fraction_H2S": f_h2s,
        },
    )


# ---------------------------------------------------------------------------
# Iron sulfide films
# ---------------------------------------------------------------------------
# The mechanism that makes sulfide qualitatively different from chloride: the
# corrosion product is electronically CONDUCTIVE and cathodic to the steel it
# sits on. An FeS film is not a passive layer - it is a distributed cathode
# bonded to the anode, so wherever it breaks the exposed steel corrodes into a
# large cathodic area. That is the classic under-deposit pitting of sour service
# and it is a galvanic problem, not a barrier problem.
#
# NEEDS-PRIMARY-SOURCE: the FeS/steel potential difference is quoted in the
# range 200-400 mV in the sour-service literature; it is NOT tabulated in
# data/astm_g82_galvanic_series.csv and is not machine-verified here. It is
# stated as a mechanism, and no driving potential or current is emitted.
FES_IS_CATHODIC_TO_STEEL: bool = True


@dataclass(frozen=True)
class SulfideFilmAssessment:
    """Whether an iron sulfide film is thermodynamically favoured, and what that means."""

    iron_reported: bool
    saturation_index_mackinawite: Optional[float]
    film_expected: Optional[bool]
    mechanism: str
    caveats: List[str] = field(default_factory=list)
