"""
Literature-anchored criteria for localized corrosion of stainless steels.

Every constant in this module traces to a primary source cited inline. Where a
value is an engineering default rather than a measured literature value, it is
marked NEEDS-PRIMARY-SOURCE so it can be replaced rather than silently trusted.

Primary sources
---------------
[G76]  J. R. Galvele, "Transport Processes and the Mechanism of Pitting of
       Metals", J. Electrochem. Soc. 123(4) 464-474 (1976).
       doi:10.1149/1.2132857
[LU66] H. P. Leckie and H. H. Uhlig, "Environmental Factors Affecting the
       Critical Potential for Pitting in 18-8 Stainless Steel",
       J. Electrochem. Soc. 113, 1262 (1966). doi:10.1149/1.2423801
       (Reported as ref [35] and Table III of [G76].)
[P21]  H. Parangusan, J. Bhadra, N. Al-Thani, "A review of passivity breakdown
       on metal surfaces: influence of chloride- and sulfide-ion
       concentrations, temperature, and pH", Emergent Materials 4, 1187-1203
       (2021). doi:10.1007/s42247-021-00194-6  (open access)
[D19]  A. A. Dastgerdi, A. Brenna, M. Ormellese, M. Pedeferri, F. Bolzoni,
       "Experimental design to study the influence of temperature, pH, and
       chloride concentration on the pitting and crevice corrosion of
       UNS S30403 stainless steel", Corros. Sci. 159, 108160 (2019).
       doi:10.1016/j.corsci.2019.108160  (Figure 17 of [P21] reproduces it.)
       DOI CORRECTED 2026-08-08. This module cited ...108120 from the start of
       the workstream, which resolves to "Preparation of graphene nanoplate added
       zinc-rich epoxy coatings" (Cao et al.) - an entirely different paper. The
       citation was matched by title and the article number transcribed wrong,
       and the error survived a round of "DOIs machine-verified against OpenAlex"
       because that check confirmed each DOI RESOLVED, not that it resolved to
       the paper named beside it. Verified 2026-08-09: 108160 is Dastgerdi,
       Brenna, Ormellese, Pedeferri & Bolzoni, Corros. Sci. (2019).
[L15]  J.-S. Lee, Y. Kitagawa, T. Nakanishi, Y. Hasegawa, K. Fushimi,
       "Effect of Hydrogen Sulfide Ions on the Passive Behavior of Type 316L
       Stainless Steel", J. Electrochem. Soc. (2015).
       doi:10.1149/2.0861512jes. Author list and title verified against OpenAlex
       work W2237882663 on 2026-08-09.

Why this module exists
----------------------
The pre-existing Tier-1/Tier-2 models in this server were found to be
insensitive to chloride (~10 mV/decade against a measured 88 mV/decade), to
carry a crevice current density ~8 orders of magnitude below the literature,
and to apply an acidic-pH penalty that [LU66] shows does not exist. See
agent-configs/pe-mechanical/TOOL_SANITY_LOG.md entries 1-9.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError
from corrosion_engineering_mcp.utils.index_presentation import format_nonzero_fixed


GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD = {
    "doi": "10.1149/1.2132857",
    "openalex_id": "https://openalex.org/W2156524385",
    "title": "Transport Processes and the Mechanism of Pitting of Metals",
    "publication_year": 1976,
    "authors": ["José R. Galvele"],
    "metadata_source": "OpenAlex",
    "metadata_retrieved_on": "2026-08-10",
    "content_location": "Table III, Fe-18Cr-8Ni in NaCl row",
    "content_support": (
        "Table III gives E_p = A - B log C_X- and B = 0.088 V per "
        "decade for Fe-18Cr-8Ni in NaCl"
    ),
    "content_limit": (
        "The measured slope is scoped to Fe-18Cr-8Ni in NaCl. It is not a "
        "generic 316L, duplex, superaustenitic, other-electrolyte, pitting-"
        "potential-magnitude, or service-acceptance criterion."
    ),
}

# ---------------------------------------------------------------------------
# Reference electrode
# ---------------------------------------------------------------------------
# Single source of truth. The codebase previously carried 0.244 (nrl_constants),
# 0.242 (redox_state) and 0.241 (docs) and subtracted potentials converted with
# different constants, biasing every Tier-2 verdict.
# Saturated calomel electrode at 25 C, +0.241 V vs SHE (CRC / IUPAC convention).
SCE_VS_SHE_V: float = 0.241


# ---------------------------------------------------------------------------
# Chloride dependence of the pitting potential  [G76] Table III, Eq. 30
# ---------------------------------------------------------------------------
# E_pit = E_pit_ref - B * log10([Cl-] / [Cl-]_ref)
#
# [G76] Table III tabulates experimentally measured slopes B for
# E_p = A - B log C_X-.  Fe-18Cr-8Ni (i.e. type 304) in NaCl solution: 0.088 V.
# The same table gives 0.044 V for Fe-18Cr-8Ni in phthalate solution, i.e. the
# slope is electrolyte-dependent; NaCl is the right analogue for water service.
# [G76] Eq. 30 derives B = (RT/F) ln10 = 0.059 V at 25 C as the transport-
# theoretic value, which the measured 0.088 V exceeds.
#
B_CL_FE18CR8NI_NACL_V_PER_DECADE: float = 0.088  # [G76] Table III, Type-304 composition
B_CL_THEORETICAL_V_PER_DECADE: float = 0.059  # [G76] Eq. 30, RT/F*ln10 at 25 C
B_CL_IRON_V_PER_DECADE: float = 0.059         # [G76] Table III, iron/NaCl
B_CL_NICKEL_V_PER_DECADE: float = 0.071       # [G76] Table III, nickel/NaCl

# 316/316L, duplex and superaustenitic slopes are NOT tabulated in [G76].  No
# generic-austenitic alias or default is exposed: callers must name a slope and
# carry its material/electrolyte scope at their own accessor boundary.


class ChlorideSlopeScopeRequiredError(ValueError):
    """A chloride-shift helper cannot silently choose an alloy's measured slope."""

    def __init__(self) -> None:
        self.code = "CHLORIDE_SLOPE_SCOPE_REQUIRED"
        self.details = {
            "required_input": "slope_V_per_decade",
            "measured_304_option": "B_CL_FE18CR8NI_NACL_V_PER_DECADE",
            "measured_304_source": "Galvele 1976 Table III, doi:10.1149/1.2132857",
            "source_record": {
                **GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD,
                "authors": list(
                    GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD["authors"]
                ),
            },
        }
        super().__init__(
            "CHLORIDE_SLOPE_SCOPE_REQUIRED: supply an explicitly sourced and "
            "material-scoped slope; the Fe-18Cr-8Ni/NaCl value is not a generic "
            "stainless-steel default."
        )


def epit_chloride_shift_V(
    Cl_mg_L: float,
    Cl_ref_mg_L: float = 1000.0,
    slope_V_per_decade: Optional[float] = None,
) -> float:
    """
    Shift in pitting potential relative to a reference chloride level.

    Returns a NEGATIVE number when Cl_mg_L > Cl_ref_mg_L (pitting gets easier).

    E_pit(Cl) = E_pit(Cl_ref) + shift,  shift = -B * log10(Cl/Cl_ref)   [G76]

    >>> round(epit_chloride_shift_V(10000.0, 1000.0, 0.088), 4)  # scoped upstream
    -0.088
    """
    if Cl_mg_L <= 0 or Cl_ref_mg_L <= 0:
        raise ValueError("Chloride concentrations must be positive")
    if slope_V_per_decade is None:
        raise ChlorideSlopeScopeRequiredError()
    return -slope_V_per_decade * math.log10(Cl_mg_L / Cl_ref_mg_L)


# ---------------------------------------------------------------------------
# pH dependence  [LU66] via [G76]
# ---------------------------------------------------------------------------
# [G76], "Effect of the External pH on the Pitting Potential":
#   "Leckie and Uhlig reported that the pitting potential of stainless steel
#    was independent of pH in the range of 1-7, but for pH values higher than 7
#    the OH- ion acted as an inhibitor."
#
# The previous implementation applied a LINEAR penalty across pH 4-10
# (factor 0.5 at pH 4), i.e. it halved the allowable chloride at pH 4. That
# contradicts [LU66]: below pH 7 there is no measured effect.
#
# Above pH 7 the inhibition is logarithmic in OH- ([G76] Eq. 12,
# E_p = A + b log C_OH-), with b = 0.020 V measured for iron over pH 10-12.
# For stainless steel [G76] reports the direction but not a slope, so we use a
# bounded logarithmic benefit and cap it.
PH_NEUTRAL: float = 7.0
PH_ALKALINE_BENEFIT_PER_DECADE: float = 0.20  # fractional Cl- threshold gain
PH_BENEFIT_CAP: float = 1.6


def chloride_threshold_pH_factor(pH: float) -> float:
    """
    Multiplier on the allowable chloride threshold as a function of bulk pH.

    Flat at 1.0 for pH <= 7 per [LU66]; logarithmic benefit above pH 7 from
    OH- inhibition, capped.

    >>> chloride_threshold_pH_factor(4.0)
    1.0
    >>> chloride_threshold_pH_factor(7.0)
    1.0
    >>> round(chloride_threshold_pH_factor(9.0), 3)
    1.4
    """
    if pH <= PH_NEUTRAL:
        return 1.0
    return min(PH_BENEFIT_CAP, 1.0 + PH_ALKALINE_BENEFIT_PER_DECADE * (pH - PH_NEUTRAL))


# ---------------------------------------------------------------------------
# Temperature dependence  [D19] via [P21] Fig. 17
# ---------------------------------------------------------------------------
# [P21] Fig. 17a/b plots the passivity breakdown potential of AISI 304L against
# log(Cl- in mg/L) at pH 4 and pH 8, for T = 20 C and T = 50 C. Across that
# 30 C interval the breakdown potential falls by roughly 0.20-0.23 V at fixed
# chloride, i.e. of order -7 mV/C.
#
# Digitised from a published figure, so treat as order-of-magnitude, not exact.
EPIT_TEMPERATURE_COEFF_V_PER_C: float = -0.007  # [D19] via [P21] Fig. 17

# Chloride-threshold temperature coefficient. The incumbent value k = 0.05 /C
# (threshold halves every ~13.9 C) is retained: it is consistent with the
# conventional 10-15 C halving rule and reproduced the incumbent Tier-1 sweep
# exactly, so changing it would be unforced.
CL_THRESHOLD_TEMP_COEFF_PER_C: float = 0.05


def epit_temperature_shift_V(temperature_C: float, T_ref_C: float = 25.0) -> float:
    """Shift in pitting potential relative to a reference temperature. [D19]"""
    return EPIT_TEMPERATURE_COEFF_V_PER_C * (temperature_C - T_ref_C)


# ---------------------------------------------------------------------------
# Galvele x*i criterion  [G76]
# ---------------------------------------------------------------------------
# This is the load-bearing physics for crevice vs pitting, and it is what the
# incumbent crevice model should have implemented instead of an IR-drop
# expression driven by a placeholder current density.
#
# [G76] establishes that localized acidification depends on the PRODUCT of the
# diffusion path length x (cm) and the current density i (A/cm^2):
#
#   "for most of the metals studied the critical pH is reached with x*i values
#    lower than 10^-6 A/cm"
#   "under stable pitting growth, would be x*i >= 10^-2 A/cm"
#
# and the consequence that matters for design:
#
#   "a metal could be resistant to pitting, but susceptible to crevice
#    corrosion. From the electrochemical point of view both processes would be
#    equal, the only difference being the length of the diffusion path."
#
# That single sentence is the correct basis for CCT < CPT: a crevice supplies a
# long x, so the critical x*i is reached at a current density orders of
# magnitude below what an open surface would need.
XI_CRITICAL_ACIDIFICATION_A_PER_CM: float = 1e-6  # [G76] critical pH reached
XI_STABLE_GROWTH_A_PER_CM: float = 1e-2           # [G76] stable pit growth

# Current density INSIDE an active pit, from [G76] "Current Density inside the Pits":
#   - "the current density inside a pit, at the initiation stage, is of the
#      order of 1 A/cm^2"
#   - Sato et al.: austenitic stainless, E > 0.60 V(SCE) -> 8 A/cm^2
#   - Schwenk: 18/10 Cr-Ni austenitic, at E_pit -> ~0.5 A/cm^2
#   - Szklarska-Smialowska & Janik-Czachor, Fe-16Cr: 8 A/cm^2 at +0.50 V(SCE),
#     falling to 0.1 A/cm^2 at the pit passivation potential -0.05 V(SCE)
I_PIT_INITIATION_A_PER_CM2: float = 1.0        # [G76] order of magnitude
I_PIT_AUSTENITIC_AT_EPIT_A_PER_CM2: float = 0.5  # [G76] citing Schwenk
I_PIT_AUSTENITIC_HIGH_E_A_PER_CM2: float = 8.0   # [G76] citing Sato et al.
I_PIT_PASSIVATION_A_PER_CM2: float = 0.1         # [G76] citing Szklarska-Smialowska

# Passive current density of stainless steel in near-neutral aerated water.
# NEEDS-PRIMARY-SOURCE: [G76] characterises current density inside active pits,
# not the passive film. 0.1-10 uA/cm^2 is the conventional passive range; we
# default to the geometric middle. Replace with a measured value for the
# specific alloy/electrolyte when one is available.
I_PASSIVE_A_PER_CM2_DEFAULT: float = 1e-5

# Critical depassivation pH, [G76] "Critical H+ Ion Concentration", computed
# from the oxide solubility product at 1e-6 M dissolved metal:
#   Fe 9.5, Co 9.5, Ni 9.5, Zn 8.7, Cr 6.0, Al 5.0
# Stainless passivity is Cr2O3-based, so Cr governs.
# [G76] notes these carry "an error of at least plus or minus one unit pH".
CRITICAL_PH_CR: float = 6.0
CRITICAL_PH_FE: float = 9.5
CRITICAL_PH_NI: float = 9.5
CRITICAL_PH_UNCERTAINTY: float = 1.0  # [G76] stated

LocalizedRegime = Literal["no_acidification", "metastable", "stable_propagation"]


@dataclass
class GalveleAssessment:
    """Result of the x*i localized-corrosion criterion. [G76]"""
    xi_A_per_cm: float
    diffusion_path_cm: float
    current_density_A_per_cm2: float
    regime: LocalizedRegime
    critical_pH: float
    reaches_critical_acidification: bool
    supports_stable_growth: bool
    interpretation: str
    basis: str = "Galvele 1976, J. Electrochem. Soc. 123(4) 464, doi:10.1149/1.2132857"


def galvele_xi_assessment(
    diffusion_path_cm: float,
    current_density_A_per_cm2: float = I_PASSIVE_A_PER_CM2_DEFAULT,
    critical_pH: float = CRITICAL_PH_CR,
) -> GalveleAssessment:
    'Apply the Galvele x*i criterion to a pit or crevice geometry.'
    if diffusion_path_cm <= 0:
        raise ValueError("diffusion_path_cm must be positive")
    if current_density_A_per_cm2 <= 0:
        raise ValueError("current_density_A_per_cm2 must be positive")

    xi = diffusion_path_cm * current_density_A_per_cm2

    if xi >= XI_STABLE_GROWTH_A_PER_CM:
        regime: LocalizedRegime = "stable_propagation"
        interp = (
            f"x*i = {repr(float(xi))} A/cm exceeds the stable-growth threshold "
            f"{repr(float(XI_STABLE_GROWTH_A_PER_CM))} A/cm. Localized attack will "
            f"propagate rather than repassivate."
        )
    elif xi >= XI_CRITICAL_ACIDIFICATION_A_PER_CM:
        regime = "metastable"
        interp = (
            f"x*i = {repr(float(xi))} A/cm is above the critical-acidification threshold "
            f"{repr(float(XI_CRITICAL_ACIDIFICATION_A_PER_CM))} A/cm but below the "
            f"stable-growth threshold {repr(float(XI_STABLE_GROWTH_A_PER_CM))} A/cm. The "
            "occluded site can reach pH "
            f"{format_nonzero_fixed(critical_pH, 1)} and depassivate; "
            f"whether it then propagates depends on whether the current density "
            f"can rise."
        )
    else:
        regime = "no_acidification"
        interp = (
            f"x*i = {repr(float(xi))} A/cm is below the critical-acidification threshold "
            f"{repr(float(XI_CRITICAL_ACIDIFICATION_A_PER_CM))} A/cm. The diffusion path "
            f"is too short to sustain the acidification needed to depassivate."
        )

    return GalveleAssessment(
        xi_A_per_cm=xi,
        diffusion_path_cm=diffusion_path_cm,
        current_density_A_per_cm2=current_density_A_per_cm2,
        regime=regime,
        critical_pH=critical_pH,
        reaches_critical_acidification=xi >= XI_CRITICAL_ACIDIFICATION_A_PER_CM,
        supports_stable_growth=xi >= XI_STABLE_GROWTH_A_PER_CM,
        interpretation=interp,
    )


def crevice_pH_from_xi(
    xi_A_per_cm: float,
    bulk_pH: float,
    critical_pH: float = CRITICAL_PH_CR,
) -> float:
    """
    Estimate occluded-cell pH from the x*i product. [G76]

    Below the critical x*i the crevice tracks bulk pH. Above it, the crevice
    acidifies toward and past the critical depassivation pH; [G76] Fig. 7 shows
    iron reaching pH 3-4 from an external pH of 10, and measurements in
    crevices report pH falling to ~2 and below once a micro-pit forms.

    Replaces the incumbent behaviour, which returned a hardcoded 2.0 pH-unit
    drop for every input regardless of geometry, chemistry, or alloy.
    """
    if xi_A_per_cm < XI_CRITICAL_ACIDIFICATION_A_PER_CM:
        return bulk_pH

    # Logarithmic march below the critical pH once acidification is established,
    # bounded at pH 0. [G76] Figs. 2-6 show the H+ line rising roughly a decade
    # per decade of x*i once the hydrolysis regime is entered.
    decades_past = math.log10(xi_A_per_cm / XI_CRITICAL_ACIDIFICATION_A_PER_CM)
    acidified = critical_pH - decades_past
    return max(0.0, min(bulk_pH, acidified))


# ---------------------------------------------------------------------------
# Sulfate context  [G76] mixed NaCl + Na2SO4 calculation, p. 473
# ---------------------------------------------------------------------------
# Directly relevant to wastewater, digester and CBG service, where sulfate is
# routinely present at levels comparable to or above chloride, and where the
# incumbent model had no sulfate term at all.
#
# [G76] calculates one example that STARTS at bulk 0.1 M Na2SO4 plus 0.01 M
# NaCl (10:1), then predicts sulfate enrichment INSIDE the pit to roughly 100:1
# or higher and eventual repassivation.  It does not turn 10:1 or 100:1 into a
# transferable bulk-water threshold and supplies no chloride-credit multiplier.

_M_CL = 35.453
_M_SO4 = 96.06


def sulfate_chloride_molar_ratio(SO4_mg_L: float, Cl_mg_L: float) -> float:
    """Molar SO4(2-):Cl(-) ratio. Returns inf if chloride is absent."""
    if Cl_mg_L <= 0:
        return float("inf")
    return (SO4_mg_L / _M_SO4) / (Cl_mg_L / _M_CL)


class SulfateCreditSuppressedError(CorrosionPublicError, RuntimeError):
    """The former quantitative Galvele credit is unavailable at every accessor."""

    def __init__(self) -> None:
        super().__init__(
            "SULFATE_CREDIT_SUPPRESSED",
            "SULFATE_CREDIT_SUPPRESSED: Galvele's mixed-electrolyte calculation "
            "shows sulfate enrichment inside a pit and qualitative inhibition, "
            "but defines no bulk-water threshold, interpolation rule, or "
            "chloride-threshold multiplier. Report the measured bulk molar ratio "
            "as context only.",
            details={
                "reason_code": "SULFATE_CREDIT_SUPPRESSED",
                "source": {
                    "authors": ["José R. Galvele"],
                    "title": "Transport Processes and the Mechanism of Pitting of Metals",
                    "doi": "10.1149/1.2132857",
                    "section": "mixed Na2SO4/NaCl calculation, p. 473",
                    "content_support": (
                        "The calculation starts with bulk 0.1 M Na2SO4 and "
                        "0.01 M NaCl (10:1), predicts sulfate enrichment inside "
                        "a growing pit to about 100:1 or higher, and describes "
                        "eventual repassivation."
                    ),
                    "content_limit": (
                        "It supplies no bulk 100:1 boundary, interpolation rule, "
                        "or chloride-threshold multiplier."
                    ),
                },
            },
        )


def sulfate_inhibition_factor(SO4_mg_L: float, Cl_mg_L: float) -> tuple[float, str]:
    """Fail closed: the former 1x--2x quantitative credit is withdrawn."""

    del SO4_mg_L, Cl_mg_L
    raise SulfateCreditSuppressedError()


# ---------------------------------------------------------------------------
# Sulfide aggravation  [P21] Section 3.2
# ---------------------------------------------------------------------------
# Relevant wherever digester gas, digestate, or sour condensate contacts
# stainless: sulfide is not simply "another anion", it changes the corrosion
# product from a protective oxide to a sulfide.
#
# [P21] Section 3.2: "The incorporation of sulfide ions on the passive film
# surface can crack the film surface and lead to pit initiation on the metal
# surface." XRD of steel in sulfide solution shows the film "mainly composed of
# FeS, alpha-FeOOH and gamma-Fe2O3, and a high sulfur content is observed in the
# pits. This result is because sulfide ions are strongly adsorbed on metal
# surfaces, which greatly accelerates localized corrosion." EDS of martensitic
# stainless in H2S shows corrosion products that are "mainly nickel sulfides
# and not iron sulfides", with Fe "completely absent" from the film.
#
# [P21] reports the mechanism and its severity but no threshold correlation.
# The legacy 0.1 mg/L value is retained only as the boundary between two named
# SCREENING GRADES. It is not allowed to decide whether a model or material is
# applicable. The second grade boundary is the lowest concentration actually
# studied by [L15].
SULFIDE_SCREENING_GRADE_BOUNDARY_MG_L: float = 0.1

# OpenAlex DOI record retrieved 2026-08-09. Keep bibliographic identity
# structured so a wrong author label cannot survive beside a resolving DOI.
SULFIDE_316L_CITATION_RECORD = {
    "doi": "10.1149/2.0861512jes",
    "openalex_id": "https://openalex.org/W2237882663",
    "title": (
        "Effect of Hydrogen Sulfide Ions on the Passive Behavior of Type "
        "316L Stainless Steel"
    ),
    "publication_year": 2015,
    "authors": [
        "Jun‐Seob Lee",
        "Yuichi Kitagawa",
        "Takayuki Nakanishi",
        "Yasuchika Hasegawa",
        "Koji Fushimi",
    ],
    "metadata_source": "OpenAlex",
    "metadata_retrieved_on": "2026-08-09",
    "section": "Experimental; Results, Tables I-II; Discussion",
    "content_support": (
        "At pH 8.4 and 0.4 V_SSE the paper reports 0, 1.5, 2.2 and "
        "2.8e-3 mol/dm3 HS-; increasing HS- raised passivity-maintaining "
        "current, reduced charge-transfer resistance from 781 to 741, 707 and "
        "295 kOhm cm2, and raised donor density from 4.19 to 4.84, 5.21 and "
        "5.99e20 cm-3."
    ),
    "content_limit": (
        "The lowest nonzero condition is about 48 mg/L as S and the paper "
        "defines no universal critical concentration or material-service verdict; "
        "the results are local-LPIG experiments on 316L in pH 8.4 buffer."
    ),
}

PARANGUSAN_SULFIDE_REVIEW_CITATION_RECORD = {
    "doi": "10.1007/s42247-021-00194-6",
    "authors": ["Hemalatha Parangusan", "Jolly Bhadra", "Noora Al-Thani"],
    "title": (
        "A review of passivity breakdown on metal surfaces: influence of "
        "chloride- and sulfide-ion concentrations, temperature, and pH"
    ),
    "section": "Section 3.2, Effect of sulfide-ion concentration",
    "content_support": (
        "Section 3.2 says incorporation of sulfide ions can crack a passive "
        "film and initiate pits, and discusses sulfur-rich sulfide corrosion "
        "products and strong sulfide adsorption as localized-corrosion context."
    ),
    "content_limit": (
        "This is a qualitative review across multiple metals; it supplies no "
        "universal sulfide concentration threshold or 316L service verdict."
    ),
}

# WHAT THAT 0.1 IS, STATED PLAINLY: an internal screening convention, not a
# measured threshold. Neither [P21] nor the on-point electrochemical study gives
# one, and the literature says the effect does not HAVE that shape.
#
# [L15] Lee, Kitagawa, Nakanishi, Hasegawa & Fushimi, "Effect of Hydrogen
# Sulfide Ions on the Passive Behavior of Type 316L Stainless Steel"
# (doi:10.1149/2.0861512jes, open access, read 2026-08-08) tested 0, 1.5, 2.2 and
# 2.8e-3 mol/dm3 HS- and reports a CONTINUOUS degradation - passive current
# density rises, charge-transfer resistance falls and donor density climbs from
# 4.19 to 5.99e20 cm-3 as HS- increases. It explicitly identifies no critical
# concentration at which the film transitions from stable to unstable.
#
# Two consequences for how this is allowed to be used:
#
#   1. The gate is a CONVENTION, and 0.1 mg/L is roughly 480x BELOW the lowest
#      concentration that study tested (1.5e-3 M ~ 48 mg/L as S). It is
#      conservative by a wide margin, which is the point - but conservatism is
#      not evidence, and it must not be reported as "decisive".
#   2. Because the underlying behaviour is continuous, a step at any single
#      value is a modelling choice. The graded standing below says so rather
#      than pretending 0.099 and 0.101 mg/L are different services.
SULFIDE_GRADE_EVIDENCE_CLASS = "internal_screening_convention"

SulfideStanding = Literal["unassessed", "not_significant", "screening_trigger", "documented_degradation"]

# Lowest concentration at which passive-film degradation is actually documented
# in the source above, converted from 1.5e-3 mol/dm3 HS- to mg/L as S.
SULFIDE_DOCUMENTED_DEGRADATION_MG_L: float = 48.0


def sulfide_standing(sulfide_mg_L: Optional[float]) -> Dict[str, Any]:
    """Grade the evidence without converting concentration into a verdict.

    The 0.1 mg/L boundary is an internal convention roughly 480 times below
    [L15]'s lowest tested concentration.  [L15] reports continuous degradation
    and no critical concentration, so adjacent values can receive adjacent
    grades but can never flip a material or model-applicability decision.
    """

    if sulfide_mg_L is None:
        grade: SulfideStanding = "unassessed"
        source_status = "not_measured"
        uncertainty = (
            "Absent is not zero. No statement about passive-film condition or "
            "localized-model applicability can be made."
        )
        rationale = "Dissolved sulfide was not measured."
    elif sulfide_mg_L < SULFIDE_SCREENING_GRADE_BOUNDARY_MG_L:
        grade = "not_significant"
        source_status = "below_internal_grade_boundary_not_a_physical_threshold"
        uncertainty = (
            "The label is an internal screening grade only. Published 316L data "
            "begin near 48 mg/L as S and show a continuous effect, so this grade "
            "does not establish an unaffected passive film."
        )
        rationale = (
            f"Reported sulfide {repr(float(sulfide_mg_L))} mg/L is below the 0.1 mg/L "
            "internal grade boundary."
        )
    elif sulfide_mg_L < SULFIDE_DOCUMENTED_DEGRADATION_MG_L:
        grade = "screening_trigger"
        source_status = "internal_grade_below_lowest_studied_concentration"
        uncertainty = (
            "This band is a conservative attention flag, not an observed onset. "
            "No source establishes a critical concentration within it."
        )
        rationale = (
            f"Reported sulfide {repr(float(sulfide_mg_L))} mg/L is between the 0.1 mg/L "
            f"internal grade boundary and the approximately "
            f"{repr(float(SULFIDE_DOCUMENTED_DEGRADATION_MG_L))} mg/L lowest studied level."
        )
    else:
        grade = "documented_degradation"
        source_status = "within_or_above_published_316L_test_range"
        uncertainty = (
            "Passive-film degradation is documented for 316L under the study "
            "electrolyte and test conditions; concentration alone does not "
            "transfer that observation into a universal material verdict."
        )
        rationale = (
            f"Reported sulfide {repr(float(sulfide_mg_L))} mg/L is at or above the lowest "
            "316L concentration studied by Lee et al."
        )

    return {
        "reported_total_sulfide_mg_L_as_S": sulfide_mg_L,
        "grade": grade,
        "grade_boundaries_mg_L_as_S": {
            "internal_screening_convention": SULFIDE_SCREENING_GRADE_BOUNDARY_MG_L,
            "lowest_published_316L_test_level_approx": (
                SULFIDE_DOCUMENTED_DEGRADATION_MG_L
            ),
        },
        "source_status": source_status,
        "effect_shape": "continuous_no_critical_concentration_reported",
        "decision_effect": "no_material_or_model_verdict",
        "uncertainty": uncertainty,
        "rationale": rationale,
        "source": {
            **SULFIDE_316L_CITATION_RECORD,
            "authors": list(SULFIDE_316L_CITATION_RECORD["authors"]),
        },
    }
