"""
Oxidant chemistry for corrosion assessment: speciation and redox forcing.

WHY THIS EXISTS
---------------
The chloride screening tables resolve temperature and pH but carry no oxidant
term, and until now no tool on this server accepted ORP or free chlorine. This
module adds free-chlorine speciation without turning an oxidant concentration or
an ideal reversible potential into a material-suitability verdict.

WHAT THIS MODULE MAY AND MAY NOT CONCLUDE
-----------------------------------------
It may speciate. Numeric thermodynamic potentials are suppressed for three
independent reasons:

1. There is nothing to compare against. Absolute E_pit is suppressed on this
   server as unvalidated (see PittingResult in core/localized_backend.py), so
   dE = E_corr - E_pit cannot be formed.

2. Even with an E_pit, a computed Nernst potential is not the alloy's measured
   corrosion potential. Electrode kinetics and oxidant-driven changes in passive-
   film composition, local pH, chloride activity, and repassivation prevent a
   reversible-couple value from being used as a transferable E_corr shift.

3. The standard potentials previously vendored here were labelled
   NEEDS-PRIMARY-SOURCE and the OCl branch omitted its pH dependence. A
   low-confidence label did not make those derived numbers defensible.

`thermodynamic_redox_forcing_index` therefore raises a typed suppression error
at every direct accessor. Any load-bearing material call requires a measured
ORP or E_corr and a sourced alloy-specific threshold; the public tool reports a
measurement but does not judge it.

WHAT IS ACTUALLY ACTIONABLE HERE
--------------------------------
The speciation. Wahman and Alexander's readable author manuscript reproduces
the Morris temperature relation and attributes it to Morris, setting the
HOCl/OCl- ratio as a function of pH and temperature. The returned fractions are
computable inside the declared dilute-solution domain; this module does not
attach an unsourced material warning or suitability boundary to either fraction.

SOURCES
-------
[M66] J. Carrell Morris, "The Acid Ionization Constant of HOCl from 5 to 35\u00b0",
      J. Phys. Chem. 70(12) 3798-3805 (1966). doi:10.1021/j100884a007
      Closed access; only the free first page was read. The equation page was
      not read and is not the content basis for the fit emitted here.
[WA19] David G. Wahman and Matthew T. Alexander, "A Drinking Water Relevant
       Water Chemistry Model for the Free Chlorine and Cyanuric Acid System
       from 5 \u00b0C to 35 \u00b0C", Environmental Engineering Science 36(3),
       283-294 (2019). doi:10.1089/ees.2018.0387; PMCID: PMC6446240.
       Free full-text author manuscript read through Water Chemistry Model,
       Equation (1), which reproduces the full temperature fit and attributes
       the established temperature dependence to Morris.
[SP]  Standard reduction potentials as tabulated in the CRC Handbook of Chemistry
      and Physics / Bard, Parsons & Jordan, "Standard Potentials in Aqueous
      Solution" (IUPAC, 1985). NOT machine-verified here - see
      NEEDS-PRIMARY-SOURCE below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Literal, Optional

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, require_representable_nonzero_result, validate_float_domain_inputs
from corrosion_engineering_mcp.utils.index_presentation import format_nonzero_fixed

# ---------------------------------------------------------------------------
# Free chlorine speciation
# ---------------------------------------------------------------------------

# Wahman and Alexander [WA19], Water Chemistry Model, Equation (1), reproduces
# the Morris temperature relation below in a readable author manuscript. T is
# absolute temperature in kelvin. Outside 5-35 C it is an extrapolation and is
# flagged as such by speciate_free_chlorine().
HOCL_PKA_VALID_T_C = (5.0, 35.0)

MORRIS_HOCL_CITATION = {
    "authors": ["J. Carrell Morris"],
    "title": "The Acid Ionization Constant of HOCl from 5 to 35\u00b0",
    "journal": "The Journal of Physical Chemistry 70(12), 3798-3805 (1966)",
    "doi": "10.1021/j100884a007",
    "section": "Free first page only",
    "access_status": "closed_access_first_page_only",
    "content_support": (
        "The readable first page identifies the measured acid-ionization study "
        "and its 5-35 \u00b0C temperature scope."
    ),
    "content_limit": (
        "Equation (13) was not read because its page is closed access; no "
        "equation- or page-anchored content claim is made from the original."
    ),
    "read_corroboration": {
        "authors": ["David G. Wahman", "Matthew T. Alexander"],
        "title": (
            "A Drinking Water Relevant Water Chemistry Model for the Free "
            "Chlorine and Cyanuric Acid System from 5 \u00b0C to 35 \u00b0C"
        ),
        "journal": "Environmental Engineering Science 36(3), 283-294 (2019)",
        "doi": "10.1089/ees.2018.0387",
        "pmcid": "PMC6446240",
        "section": "Water Chemistry Model, Equation (1)",
        "access_status": "free_full_text_author_manuscript",
        "content_support": (
            "Equation (1) gives pK = 3000/T_K - 10.0686 + 0.0253*T_K "
            "at zero ionic strength and attributes the established temperature "
            "dependence to Morris."
        ),
        "content_limit": (
            "The model covers 5-35 \u00b0C and does not validate ideal "
            "concentration-based speciation for concentrated commercial "
            "hypochlorite solutions."
        ),
        "content_read_on": "2026-08-10",
    },
}

# The Henderson-Hasselbalch calculation below uses concentrations in place of
# activities. Declare a conservative dilute-solution applicability ceiling so
# commercial bleach cannot be presented as high-confidence ideal speciation.
# This is a model-domain boundary, not a concentration threshold from Morris.
FREE_CHLORINE_MOLAR_MASS_AS_CL2_G_MOL = 70.906
DILUTE_FREE_CHLORINE_MAX_MOL_L = 0.01


def hocl_pka(temperature_C: float = 25.0) -> float:
    """HOCl pKa from the Morris fit as reproduced by Wahman/Alexander [WA19]."""
    if (
        isinstance(temperature_C, bool)
        or not isinstance(temperature_C, Real)
        or not math.isfinite(float(temperature_C))
        or temperature_C <= -273.15
    ):
        raise ValueError("temperature_C must be finite and above absolute zero")
    temperature_K = temperature_C + 273.15
    return 3000.0 / temperature_K - 10.0686 + 0.0253 * temperature_K


@dataclass(frozen=True)
class ChlorineSpeciation:
    """Split of a free-chlorine residual between HOCl and OCl-."""

    total_free_chlorine_mg_L_as_Cl2: float
    pH: float
    temperature_C: float
    pKa: float
    fraction_HOCl: float
    fraction_OCl: float
    HOCl_mg_L_as_Cl2: float
    OCl_mg_L_as_Cl2: float
    extrapolated_beyond_measured_range: bool
    free_chlorine_mol_L: float
    dilute_model_max_mol_L: float
    concentrated_beyond_dilute_model: bool
    note: str


def speciate_free_chlorine(
    free_chlorine_mg_L_as_Cl2: float,
    pH: float,
    temperature_C: float = 25.0,
) -> ChlorineSpeciation:
    """Split a free-chlorine residual into HOCl and OCl-.

    This is the actionable part of oxidant assessment. HOCl is by far the
    stronger oxidant, and its share collapses across the pH range plants
    actually run:

    >>> round(speciate_free_chlorine(1.0, 6.5).fraction_HOCl, 2)
    0.92
    >>> round(speciate_free_chlorine(1.0, 8.5).fraction_HOCl, 2)
    0.1

    So a 1 mg/L residual is roughly nine times more HOCl at pH 6.5 than at 8.5.
    Any material judgement made on "free chlorine" alone has averaged that away.
    """
    for field_name, value in (
        ("free_chlorine_mg_L_as_Cl2", free_chlorine_mg_L_as_Cl2),
        ("pH", pH),
        ("temperature_C", temperature_C),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"{field_name} must be a finite real number")
    validate_float_domain_inputs(
        {
            "free_chlorine_mg_L_as_Cl2": free_chlorine_mg_L_as_Cl2,
            "pH": pH,
            "temperature_C": temperature_C,
        },
    )
    if free_chlorine_mg_L_as_Cl2 < 0:
        raise ValueError("free chlorine cannot be negative")
    if not 0.0 <= pH <= 14.0:
        raise ValueError("pH must be between 0 and 14")

    pKa = hocl_pka(temperature_C)
    free_chlorine_mol_L = (
        free_chlorine_mg_L_as_Cl2
        / 1000.0
        / FREE_CHLORINE_MOLAR_MASS_AS_CL2_G_MOL
    )
    require_representable_nonzero_result(
        "free_chlorine_mg_L_as_Cl2_to_mol_L",
        free_chlorine_mol_L,
        operands={
            "free_chlorine_mg_L_as_Cl2": free_chlorine_mg_L_as_Cl2,
            "mg_per_g": 1000.0,
            "molar_mass_as_Cl2_g_mol": (
                FREE_CHLORINE_MOLAR_MASS_AS_CL2_G_MOL
            ),
        },
    )
    # HOCl / (HOCl + OCl-) = 1 / (1 + 10^(pH - pKa))
    f_hocl = 1.0 / (1.0 + 10.0 ** (pH - pKa))
    f_ocl = 1.0 - f_hocl

    extrapolated = not (HOCL_PKA_VALID_T_C[0] <= temperature_C <= HOCL_PKA_VALID_T_C[1])
    concentrated = free_chlorine_mol_L > DILUTE_FREE_CHLORINE_MAX_MOL_L
    note = (
        f"pKa {format_nonzero_fixed(pKa, 2)} at "
        f"{format_nonzero_fixed(temperature_C, 1)} C "
        "[Morris 1966, doi:10.1021/j100884a007]. "
        f"HOCl is the stronger oxidant; its share falls sharply above the pKa."
    )
    if extrapolated:
        note += (
            " EXTRAPOLATED: "
            f"{repr(float(temperature_C))} C is outside the measured "
            f"{HOCL_PKA_VALID_T_C[0]}-{HOCL_PKA_VALID_T_C[1]} C range."
        )
    if concentrated:
        note += (
            " CONCENTRATION OUT OF DOMAIN: the concentration-based split "
            "assumes dilute-solution activities; concentrated hypochlorite "
            "requires an activity model."
        )

    return ChlorineSpeciation(
        total_free_chlorine_mg_L_as_Cl2=free_chlorine_mg_L_as_Cl2,
        pH=pH,
        temperature_C=temperature_C,
        pKa=pKa,
        fraction_HOCl=f_hocl,
        fraction_OCl=f_ocl,
        HOCl_mg_L_as_Cl2=free_chlorine_mg_L_as_Cl2 * f_hocl,
        OCl_mg_L_as_Cl2=free_chlorine_mg_L_as_Cl2 * f_ocl,
        extrapolated_beyond_measured_range=extrapolated,
        free_chlorine_mol_L=free_chlorine_mol_L,
        dilute_model_max_mol_L=DILUTE_FREE_CHLORINE_MAX_MOL_L,
        concentrated_beyond_dilute_model=concentrated,
        note=note,
    )


# ---------------------------------------------------------------------------
# Withdrawn numeric redox index
# ---------------------------------------------------------------------------

OxidantSpecies = Literal[
    "free_chlorine", "chlorine_dioxide", "ozone", "hydrogen_peroxide",
    "peracetic_acid", "ferric_iron", "dissolved_oxygen",
]


class RedoxIndexSuppressedError(CorrosionPublicError, RuntimeError):
    """The former E0-derived index is unavailable at every Python accessor."""

    def __init__(self) -> None:
        super().__init__(
            "REDOX_INDEX_SUPPRESSED",
            "REDOX_INDEX_SUPPRESSED: the vendored standard potentials lack "
            "verified primary sources, the former OCl branch omitted its pH "
            "dependence, and a reversible couple potential is not an alloy "
            "corrosion potential. Only chlorine speciation is retained.",
            details={
                "reason_code": "REDOX_INDEX_SUPPRESSED",
                "re_enable_conditions": [
                    "primary-source standard potentials and full Nernst reactions",
                    "validation against measured solution potentials",
                    "a sourced alloy-specific decision threshold for any verdict",
                ],
            },
        )


def thermodynamic_redox_forcing_index(
    oxidant: OxidantSpecies,
    pH: float,
    temperature_C: float = 25.0,
    free_chlorine_mg_L_as_Cl2: Optional[float] = None,
) -> None:
    """Fail closed: the former numerical index is withdrawn, not renamed."""
    del oxidant, pH, temperature_C, free_chlorine_mg_L_as_Cl2
    raise RedoxIndexSuppressedError()
