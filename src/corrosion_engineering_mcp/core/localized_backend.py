'Localized Corrosion Backend - Pitting and Crevice Corrosion'

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from numbers import Real
from typing import Any, Dict, Literal, Optional, Tuple, List

from corrosion_engineering_mcp.core.errors import CorrosionPublicError
from corrosion_engineering_mcp.core.materials import MaterialCatalogError, UnknownMaterialError, canonical_id, get_material
from corrosion_engineering_mcp.utils.index_presentation import format_nonzero_fixed

# Alloy families for which PREN / CPT is a meaningful construct: passive,
# Cr-bearing. Carbon steel, aluminium, copper alloys, titanium and zinc all
# resist chloride by mechanisms PREN does not describe, so a PREN-derived CPT
# for them is a number without meaning rather than a conservative estimate.
_PREN_APPLICABLE_GRADE_TYPES = frozenset(
    {"austenitic", "duplex", "super_duplex", "superaustenitic", "nickel_alloy"}
)

# NRL response-surface training domain. The upstream generator
# (corrosion-modeling-applications/polarization-curve-modeling/
# ModelsForResponseSurfaces/createmultilinearmodels.m) fits the pitting surface
# at 5/25/45 C and 0/3.1/6.2 M chloride. Outside that box the polynomial is
# extrapolating with no stated behaviour, so say so rather than returning a
# confident number.
NRL_TRAIN_T_C = (5.0, 45.0)
NRL_TRAIN_CL_M = (0.0, 6.2)


class ModelNotApplicableError(CorrosionPublicError, ValueError):
    """Raised when a material resolves but the requested model does not apply to it.

    Distinct from UnknownMaterialError: the material IS known, and returning a
    number for it would be worse than not recognising it at all, because the
    number looks authoritative. Carbon steel through the PREN path yields
    PREN = 0 -> CPT = -10 C, which reads as a real result.
    """

    def __init__(self, material: str, grade_type: str, model: str, alternative: str) -> None:
        super().__init__(
            "MODEL_NOT_APPLICABLE",
            f"MODEL_NOT_APPLICABLE: '{material}' is a {grade_type} alloy; the "
            f"{model} model applies only to passive Cr-bearing alloys "
            f"({', '.join(sorted(_PREN_APPLICABLE_GRADE_TYPES))}). "
            f"Alternative: {alternative}.",
            details={
                "reason_code": "MODEL_NOT_APPLICABLE",
                "material": material,
                "grade_type": grade_type,
                "model": model,
                "applicable_grade_types": sorted(_PREN_APPLICABLE_GRADE_TYPES),
                "alternative": alternative,
            },
        )


class LocalizedBackendServiceAssessmentSuppressedError(
    CorrosionPublicError, RuntimeError
):
    """The direct aggregate backend formerly leaked every suppressed verdict."""

    def __init__(self) -> None:
        super().__init__(
            "LOCALIZED_BACKEND_SERVICE_ASSESSMENT_SUPPRESSED",
            "LOCALIZED_BACKEND_SERVICE_ASSESSMENT_SUPPRESSED: the aggregate "
            "LocalizedBackend service assessment contains internal CPT/CCT, "
            "chloride limits, crevice calculations and risk verdicts that have "
            "no sourced service model. Use corrosion_assess_localized, whose "
            "facade exposes only the defensible composition/electrochemical subset.",
            details={
                "reason_code": "LOCALIZED_BACKEND_SERVICE_ASSESSMENT_SUPPRESSED",
                "use_instead": "corrosion_assess_localized",
            },
        )


class PRENFormulaScopeMismatchError(CorrosionPublicError, ValueError):
    """A named PREN convention was requested outside its cited alloy scope."""

    def __init__(
        self,
        formula_variant: str,
        grade_type: str,
        accepted_grade_types: list[str],
    ) -> None:
        super().__init__(
            "PREN_FORMULA_SCOPE_MISMATCH",
            f"PREN_FORMULA_SCOPE_MISMATCH: {formula_variant} is sourced only for "
            f"{', '.join(accepted_grade_types)} compositions, not {grade_type!r}. "
            "Select the ASTM_A511_PREN16 default or supply a grade label inside "
            "the cited scope.",
            details={
                "reason_code": "PREN_FORMULA_SCOPE_MISMATCH",
                "formula_variant": formula_variant,
                "grade_type": grade_type,
                "accepted_grade_types": accepted_grade_types,
            },
        )


class PRENFormulaCoefficientMismatchError(CorrosionPublicError, ValueError):
    """Explicit coefficients disagree with the selected named convention."""

    def __init__(
        self,
        formula_variant: str,
        supplied_coefficients: Dict[str, float],
        expected_coefficients: Dict[str, float],
    ) -> None:
        super().__init__(
            "PREN_FORMULA_COEFFICIENT_MISMATCH",
            "PREN_FORMULA_COEFFICIENT_MISMATCH: explicit coefficients must "
            f"match the selected named formula_variant {formula_variant!r}. "
            "A named citation and a different executable equation cannot travel "
            "in the same payload.",
            details={
                "reason_code": "PREN_FORMULA_COEFFICIENT_MISMATCH",
                "formula_variant": formula_variant,
                "supplied_coefficients": dict(supplied_coefficients),
                "expected_coefficients": dict(expected_coefficients),
            },
        )


class PRENFormulaCompositionBasisMismatchError(CorrosionPublicError, ValueError):
    """A phase-specific equation was given a bulk-alloy composition."""

    def __init__(
        self,
        formula_variant: str,
        composition_basis: str,
        accepted_composition_bases: list[str],
    ) -> None:
        super().__init__(
            "PREN_FORMULA_COMPOSITION_BASIS_MISMATCH",
            "PREN_FORMULA_COMPOSITION_BASIS_MISMATCH: "
            f"{formula_variant} is a phase-specific equation and cannot be "
            f"evaluated from {composition_basis!r}. Supply one of "
            f"{accepted_composition_bases!r} with phase-specific composition.",
            details={
                "reason_code": "PREN_FORMULA_COMPOSITION_BASIS_MISMATCH",
                "formula_variant": formula_variant,
                "composition_basis": composition_basis,
                "accepted_composition_bases": accepted_composition_bases,
            },
        )


class PRENFormulaInputRequiredError(CorrosionPublicError, ValueError):
    """A named equation is missing an element that it explicitly contains."""

    def __init__(self, formula_variant: str, field: str) -> None:
        super().__init__(
            "PREN_FORMULA_INPUT_REQUIRED",
            "PREN_FORMULA_INPUT_REQUIRED: "
            f"{field} is required by {formula_variant}; it may not be assumed zero.",
            details={
                "reason_code": "PREN_FORMULA_INPUT_REQUIRED",
                "formula_variant": formula_variant,
                "field": field,
            },
        )


class PRENCompositionInvalidError(CorrosionPublicError, ValueError):
    """Composition values must be real and finite on every Python accessor."""

    def __init__(self, field: str, value: Any) -> None:
        super().__init__(
            "PREN_COMPOSITION_INVALID",
            f"PREN_COMPOSITION_INVALID: {field}={value!r} must be a finite real number.",
            details={
                "reason_code": "PREN_COMPOSITION_INVALID",
                "field": field,
                "value": value,
            },
        )


# Import provenance-gated catalog and internal ranking data.
from corrosion_engineering_mcp.data import get_material_data, get_cpt_from_astm, get_chloride_threshold, calculate_pren as calculate_pren_authoritative

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

FARADAY = 96485.3321  # C/mol
R_GAS = 8.314462618  # J/mol·K

# ---------------------------------------------------------------------------
# PREN calibration coefficients (exposed per Codex)
# ---------------------------------------------------------------------------

# PREN is not a uniquely defined material property.  The public default is the
# PREN16 equation actually printed in ASTM A511/A511M-16, chemical-composition
# table note B for UNS S32760. A PREN30 coefficient reported in super-duplex
# literature is available only by explicit selection for a duplex composition.
DEFAULT_PREN_FORMULA_VARIANT = "ASTM_A511_PREN16"
PREN_FORMULA_VARIANTS: Dict[str, Dict[str, Any]] = {
    "ASTM_A511_PREN16": {
        "coefficients": {"a": 1.0, "b": 3.3, "c": 16.0},
        "equation": "PREN = Cr + 3.3*Mo + 16*N",
        "source": {
            "authors": ["ASTM International"],
            "identifier": "ASTM A511/A511M-16",
            "title": (
                "Standard Specification for Seamless Stainless Steel Mechanical "
                "Tubing and Hollow Bar"
            ),
            "doi": "10.1520/A0511_A0511M-16",
            "location": "chemical-composition table note B for UNS S32760",
            "section": "Table 3, chemical-composition note B for UNS S32760",
            "content_support": (
                "Chemical-composition Table 3 note B defines PREN as %Cr + "
                "3.3*%Mo + 16*%N for UNS S32760"
            ),
            "content_limit": (
                "The note is a composition requirement for UNS S32760. It does "
                "not establish a universal PREN convention, a CPT correlation, "
                "or a material-service acceptance threshold."
            ),
            "scope": (
                "PREN16 composition requirement for S32760; used here as the "
                "default relative-index convention"
            ),
        },
    },
    "LIU_2020_DUPLEX_PREN30": {
        "coefficients": {"a": 1.0, "b": 3.3, "c": 30.0},
        "manganese_coefficient": -1.0,
        "equation": (
            "PREN_gamma30 = Cr_gamma + 3.3*Mo_gamma + 30*N_gamma - Mn_gamma"
        ),
        "accepted_grade_types": ["duplex"],
        "accepted_composition_bases": ["austenite_phase"],
        "required_inputs": ["Mn"],
        "source": {
            "authors": [
                "Shuang Liu",
                "Chaohua Yue",
                "Xi Chen",
                "Qiu-hua Zhu",
                "Yiyou Tu",
            ],
            "title": (
                "Pitting Corrosion Resistance on Annealing Treated Super Duplex "
                "Stainless Steel S32750"
            ),
            "doi": "10.3390/cryst10040294",
            "openalex_id": "https://openalex.org/W3015827073",
            "metadata_source": "OpenAlex",
            "metadata_retrieved_on": "2026-08-10",
            "scope": (
                "reported alternative austenite-phase nitrogen coefficient in "
                "super-duplex PREN literature; explicit duplex-only selection, "
                "not the ASTM A511 default"
            ),
            "section": "4. Discussion",
            "content_support": (
                "Section 4 gives phase-specific PREN equations: ferrite uses Cr + "
                "3.3Mo; austenite uses Cr + 3.3Mo + 16N - Mn or Cr + 3.3Mo + "
                "30N - Mn. It says the austenite nitrogen coefficient is usually "
                "16 or 30, and this paper used 16."
            ),
            "content_limit": (
                "The paper did not use PREN30 for its results and did not define a "
                "whole-duplex PREN30 service verdict."
            ),
        },
    },
}


def get_pren_formula_basis(
    formula_variant: str = DEFAULT_PREN_FORMULA_VARIANT,
) -> Dict[str, Any]:
    """Return a caller-safe description of one named PREN convention."""
    try:
        definition = PREN_FORMULA_VARIANTS[formula_variant]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported PREN formula_variant {formula_variant!r}; choose one of "
            f"{', '.join(PREN_FORMULA_VARIANTS)}"
        ) from exc
    coefficients = definition["coefficients"]
    source = definition["source"]
    return {
        "variant": formula_variant,
        "equation": definition["equation"],
        "nitrogen_coefficient": coefficients["c"],
        "manganese_coefficient": definition.get("manganese_coefficient", 0.0),
        "accepted_grade_types": (
            list(definition["accepted_grade_types"])
            if "accepted_grade_types" in definition
            else None
        ),
        "accepted_composition_bases": (
            list(definition["accepted_composition_bases"])
            if "accepted_composition_bases" in definition
            else None
        ),
        "required_inputs": list(definition.get("required_inputs", [])),
        "source": {
            key: list(value) if isinstance(value, list) else value
            for key, value in source.items()
        },
        "output_scope": "relative_pitting_resistance_composition_index",
    }


# Compatibility names for internal consumers.  Critically, ``duplex`` no
# longer hides a switch to 30N: every implicit/default route is PREN16.
PREN_COEFFS = {
    "standard": PREN_FORMULA_VARIANTS[DEFAULT_PREN_FORMULA_VARIANT]["coefficients"],
    "duplex": PREN_FORMULA_VARIANTS[DEFAULT_PREN_FORMULA_VARIANT]["coefficients"],
    **{
        name: definition["coefficients"]
        for name, definition in PREN_FORMULA_VARIANTS.items()
    },
}

# CPT-PREN correlation: CPT = m×PREN + b (°C)
CPT_CORRELATIONS = {
    "austenitic": {"m": 1.0, "b": -10.0},         # CPT ≈ PREN - 10
    "duplex": {"m": 1.0, "b": -15.0},             # Duplex slightly more conservative
    "superaustenitic": {"m": 1.0, "b": -5.0},     # Super grades more resistant
}

# Chloride threshold correlations (mg/L Cl⁻ vs temperature)
# Based on empirical data from ASTM G48, ISO 17945
CL_THRESHOLD_BASE = {
    "304": 50.0,      # Low PREN ≈ 18, very susceptible
    "316": 200.0,     # PREN ≈ 24, moderate resistance
    "316L": 250.0,    # Lower carbon, slightly better
    "2205": 1000.0,   # Duplex, PREN ≈ 35
    "254SMO": 5000.0, # Superaustenitic, PREN ≈ 43
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MaterialComposition:
    """
    Material composition for PREN calculation.

    Attributes:
        Cr: Chromium content (wt%)
        Mo: Molybdenum content (wt%)
        N: Nitrogen content (wt%)
        Ni: Nickel content (wt%, optional)
        Mn: Manganese content (wt%, required for the Liu austenite-phase equation)
        grade_type: "austenitic", "duplex", "superaustenitic"
        composition_basis: ``bulk_alloy`` or ``austenite_phase``
    """
    Cr: float
    Mo: float
    N: float
    Ni: float = 0.0
    grade_type: str = "austenitic"
    Mn: Optional[float] = None
    composition_basis: str = "bulk_alloy"

    def calculate_pren(
        self,
        coeffs: Optional[Dict[str, float]] = None,
        formula_variant: str = DEFAULT_PREN_FORMULA_VARIANT,
    ) -> float:
        """
        Calculate PREN (Pitting Resistance Equivalent Number).

        Args:
            coeffs: Optional custom coefficients {"a": ..., "b": ..., "c": ...}.
                Internal research use only; public payloads use named variants.
            formula_variant: Named sourced convention used when ``coeffs`` is
                omitted. Defaults to ASTM_A511_PREN16 for every grade family.

        Returns:
            PREN value (unitless)
        """
        # Resolve and scope the named convention before considering explicit
        # coefficients.  The previous order let ``coeffs=...`` bypass both the
        # variant lookup and its duplex-only scope while the caller still named
        # the Liu citation.
        try:
            definition = PREN_FORMULA_VARIANTS[formula_variant]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported PREN formula_variant {formula_variant!r}"
            ) from exc

        for field_name in ("Cr", "Mo", "N", "Ni"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                raise PRENCompositionInvalidError(field_name, value)
        if self.Mn is not None and (
            isinstance(self.Mn, bool)
            or not isinstance(self.Mn, Real)
            or not math.isfinite(float(self.Mn))
        ):
            raise PRENCompositionInvalidError("Mn", self.Mn)

        accepted_grade_types = definition.get("accepted_grade_types")
        if (
            accepted_grade_types is not None
            and self.grade_type not in accepted_grade_types
        ):
            raise PRENFormulaScopeMismatchError(
                formula_variant,
                self.grade_type,
                list(accepted_grade_types),
            )
        accepted_composition_bases = definition.get("accepted_composition_bases")
        if (
            accepted_composition_bases is not None
            and self.composition_basis not in accepted_composition_bases
        ):
            raise PRENFormulaCompositionBasisMismatchError(
                formula_variant,
                self.composition_basis,
                list(accepted_composition_bases),
            )
        for field_name in definition.get("required_inputs", []):
            if getattr(self, field_name) is None:
                raise PRENFormulaInputRequiredError(formula_variant, field_name)

        expected_coeffs = definition["coefficients"]
        if coeffs is None:
            coeffs = expected_coeffs
        else:
            keys_match = set(coeffs) == set(expected_coeffs)
            values_match = keys_match and all(
                not isinstance(coeffs[name], bool)
                and isinstance(coeffs[name], Real)
                and math.isfinite(float(coeffs[name]))
                and math.isclose(
                    float(coeffs[name]),
                    expected_coeffs[name],
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
                for name in expected_coeffs
            )
            if not values_match:
                raise PRENFormulaCoefficientMismatchError(
                    formula_variant,
                    coeffs,
                    expected_coeffs,
                )

        pren = (
            coeffs["a"] * self.Cr
            + coeffs["b"] * self.Mo
            + coeffs["c"] * self.N
            + definition.get("manganese_coefficient", 0.0) * (self.Mn or 0.0)
        )
        return pren


@dataclass
class PittingResult:
    """
    Result of pitting corrosion susceptibility calculation.

    Tier 1 (PREN/CPT - always available):
        CPT_C: Critical Pitting Temperature (°C)
        PREN: Pitting Resistance Equivalent Number
        Cl_threshold_mg_L: Chloride threshold at operating temperature (mg/L)
        susceptibility: "low", "moderate", "high", "critical"
        margin_C: Temperature margin to CPT (positive = safe, negative = unsafe)
        interpretation: Text summary

    Electrochemical outputs when DO is supplied:
        E_pit_VSCE: Pitting initiation potential (V vs SCE), None if not calculated
        E_redox_equilibrium_VSCE: Reversible O2/H2O Nernst potential (V vs SCE) -
            a material-independent thermodynamic bound, NOT the alloy's mixed
            corrosion potential; null at/below the stated oxygen-activity basis
            or outside the verified 0-40 C freshwater source domain
        electrochemical_margin_V: ΔE = E_mix - E_pit (V), None if not calculated
        electrochemical_risk: "critical", "high", "moderate", "low", None if not calculated
        electrochemical_interpretation: Text summary for Tier 2, None if not calculated
    """
    # Tier 1: PREN/CPT (always present)
    CPT_C: float
    PREN: float
    Cl_threshold_mg_L: float
    susceptibility: str
    margin_C: float
    interpretation: str

    # Electrochemical outputs. Redox is material-independent; E_pit is suppressed.
    #
    # E_pit_VSCE is SUPPRESSED and always None. It is retained as a field so the
    # payload shape is stable, but the absolute pitting potential this server can
    # currently compute is not defensible:
    #
    #   * The Python is not a faithful port of the NRL MATLAB. It evaluates the
    #     response surface at a fixed reference chloride, applies a separate
    #     Galvele chloride shift, alters the Nernst concentration convention, and
    #     invents a 1 uA/cm^2 threshold current. The MATLAB produces a
    #     polarization curve and does none of that.
    #   * There is no anchor to validate it against. NRL declares
    #     PitPotential = -0.2 V_SCE *identically* for HY80, HY100 and SS316 - a
    #     naval steel and an austenitic stainless - so it is a shared model
    #     constant, not a per-alloy pitting potential, and can neither confirm nor
    #     refute a computed value.
    #
    # What IS defensible is reported instead: the equilibrium redox potential
    # (correctly named), and the chloride response slope, which is anchored to
    # Galvele 1976 Table III at 88 mV/decade for Fe-18Cr-8Ni in NaCl.
    E_pit_VSCE: Optional[float] = None
    E_pit_status: Optional[str] = None

    # Renamed from E_mix_VSCE. This is the reversible O2/H2O Nernst potential from
    # dissolved oxygen - a thermodynamic bound - NOT the alloy's mixed corrosion
    # potential. Measured E_corr for passive stainless in aerated neutral water
    # sits 400-500 mV below this because oxygen reduction is kinetically hindered.
    # Calling it "mixed potential" overstated the driving force in every result.
    E_redox_equilibrium_VSCE: Optional[float] = None
    E_redox_equilibrium_status: str = "not_requested_no_dissolved_oxygen"
    E_redox_equilibrium_warnings: Optional[List[str]] = None
    E_redox_detection_basis_mg_L: float = 0.01

    # Anchored: Galvele 1976 J. Electrochem. Soc. 123(4) 464 Table III.
    chloride_response_mV_per_decade: Optional[float] = None
    chloride_response_scope: Optional[Dict[str, Any]] = None

    electrochemical_margin_V: Optional[float] = None
    electrochemical_risk: Optional[str] = None
    electrochemical_interpretation: Optional[str] = None


@dataclass
class CreviceResult:
    """
    Result of crevice corrosion susceptibility calculation.

    Attributes:
        CCT_C: Critical Crevice Temperature (°C) - typically CPT - 10 to 20°C
        IR_drop_V: IR drop in crevice (V) from Oldfield-Sutton model
        acidification_factor: pH drop in crevice (unitless, >1 = more acidic)
        susceptibility: "low", "moderate", "high", "critical"
        margin_C: Temperature margin to CCT
        interpretation: Text summary
    """
    CCT_C: float
    IR_drop_V: float
    acidification_factor: float
    susceptibility: str
    margin_C: float
    interpretation: str
    xi_A_per_cm: float
    pH_crevice: float


@dataclass
class LocalizedResult:
    """
    Combined result for localized corrosion (pitting + crevice).

    Per Codex: Separate outputs but shared chloride threshold logic.
    """
    pitting: PittingResult
    crevice: CreviceResult
    material: str
    temperature_C: float
    Cl_mg_L: float
    pH: float
    dissolved_oxygen_mg_L: Optional[float]
    overall_risk: str  # "low", "moderate", "high", "critical"

    # A bare risk string cannot express "I could not assess this", so a caller has
    # no way to distinguish a genuine verdict from one produced on an incomplete or
    # unvalidated basis — and a downstream consumer sorting or comparing strings
    # silently treats the second as if it were the first.
    #
    # assessment_status is the discriminator:
    #   "ok"            - overall_risk is a verdict the server stands behind
    #   "indeterminate" - overall_risk is NOT usable for a material decision;
    #                     read assessment_caveats for what is missing
    #
    # Consumers MUST branch on assessment_status before reading overall_risk.
    assessment_status: Literal["ok", "indeterminate"] = "ok"
    assessment_caveats: Optional[List[str]] = None

    # Separated from risk deliberately. Risk is about this service; suitability is
    # about this alloy in this service. R2.1 replaces the classifier behind this
    # field without changing its contract.
    material_suitability: Optional[str] = None


# ---------------------------------------------------------------------------
# Localized corrosion backend
# ---------------------------------------------------------------------------

class LocalizedBackend:
    """
    Pitting and crevice corrosion calculator.

    Uses PREN-based CPT correlations and simplified Oldfield-Sutton model.
    """

    def __init__(self):
        """Initialize localized corrosion backend."""
        pass

    def calculate_pitting_susceptibility(
        self,
        material_comp: MaterialComposition,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        material_name: str = "316L",
        custom_cpt_correlation: Optional[Dict[str, float]] = None,
        dissolved_oxygen_mg_L: Optional[float] = None,
    ) -> PittingResult:
        'Calculate pitting corrosion susceptibility.'
        # Calculate PREN using local method (handles local MaterialComposition format)
        pren = material_comp.calculate_pren()

        # Historical G48-test ranking row; not a value tabulated by ASTM G48.
        cpt_data = get_cpt_from_astm(material_name)

        if cpt_data is not None:
            # Use the internal G48-test ranking coordinate.
            CPT = cpt_data["CPT_C"]
            logger.info(
                "Using internal G48-test CPT ranking coordinate for %s: %s°C",
                material_name,
                CPT,
            )
        elif custom_cpt_correlation is not None:
            # Use custom correlation if provided
            cpt_corr = custom_cpt_correlation
            CPT = cpt_corr["m"] * pren + cpt_corr["b"]
            logger.warning(f"Using custom CPT correlation for {material_name}: {CPT}°C")
        else:
            # Fallback to PREN-based estimate with warning
            cpt_corr = CPT_CORRELATIONS.get(material_comp.grade_type, CPT_CORRELATIONS["austenitic"])
            CPT = cpt_corr["m"] * pren + cpt_corr["b"]
            logger.warning(
                f"Material {material_name} not in ASTM G48 database; "
                f"using PREN estimate: {CPT}°C (±20°C uncertainty)"
            )

        # Temperature margin
        margin_C = CPT - temperature_C

        # Internal ranking coordinate only; the public facade suppresses this
        # value because neither ISO 18070 nor NORSOK M-506 supplies it.
        Cl_threshold = get_chloride_threshold(material_name, temperature_C, pH)
        logger.info(
            "Using internal chloride ranking coordinate for %s at %s°C, pH %s: %.0f mg/L",
            material_name,
            temperature_C,
            pH,
            Cl_threshold,
        )

        # Determine susceptibility
        if margin_C > 20.0 and Cl_mg_L < Cl_threshold * 0.5:
            susceptibility = "low"
        elif margin_C > 10.0 and Cl_mg_L < Cl_threshold:
            susceptibility = "moderate"
        elif margin_C > 0 or Cl_mg_L < Cl_threshold * 1.5:
            susceptibility = "high"
        else:
            susceptibility = "critical"

        # Interpretation
        if susceptibility == "critical":
            interpretation = (
                f"CRITICAL: T = {temperature_C}°C exceeds CPT = "
                f"{format_nonzero_fixed(CPT, 1)}°C by "
                f"{format_nonzero_fixed(-margin_C, 1)}°C; Cl⁻ = "
                f"{format_nonzero_fixed(Cl_mg_L, 0)} mg/L >> "
                f"{format_nonzero_fixed(Cl_threshold, 0)} mg/L threshold"
            )
        elif susceptibility == "high":
            interpretation = (
                f"HIGH RISK: T = {temperature_C}°C within "
                f"{format_nonzero_fixed(margin_C, 1)}°C of CPT = "
                f"{format_nonzero_fixed(CPT, 1)}°C; Cl⁻ = "
                f"{format_nonzero_fixed(Cl_mg_L, 0)} mg/L near threshold"
            )
        elif susceptibility == "moderate":
            interpretation = (
                f"MODERATE: T = {temperature_C}°C is "
                f"{format_nonzero_fixed(margin_C, 1)}°C below CPT = "
                f"{format_nonzero_fixed(CPT, 1)}°C; Cl⁻ = "
                f"{format_nonzero_fixed(Cl_mg_L, 0)} mg/L acceptable"
            )
        else:
            interpretation = (
                f"LOW RISK: T = {temperature_C}°C well below CPT = "
                f"{format_nonzero_fixed(CPT, 1)}°C (margin "
                f"{format_nonzero_fixed(margin_C, 1)}°C); Cl⁻ = "
                f"{format_nonzero_fixed(Cl_mg_L, 0)} mg/L < "
                f"{format_nonzero_fixed(Cl_threshold, 0)} mg/L"
            )

        # PHASE 3 electrochemical outputs. The water-chemistry redox quantity is
        # material-independent; only the NRL E_pit machinery is alloy-scoped.
        E_pit_VSCE = None
        # Always populated, not only on the Tier-2 path. A null status alongside a
        # null value reads as "not requested"; the caller cannot tell that from
        # "computed but withheld as unvalidated", and the difference matters.
        E_pit_status = (
            "SUPPRESSED_UNVALIDATED: absolute pitting potential is not reported by "
            "this server under any input. See PittingResult in core/localized_backend.py."
        )
        E_redox_equilibrium_VSCE = None
        E_redox_equilibrium_status = "not_requested_no_dissolved_oxygen"
        E_redox_equilibrium_warnings: List[str] = []

        # This is a water-chemistry equilibrium quantity, not an alloy response.
        # Calculate it for every material whenever DO is supplied; only E_pit and
        # the NRL response-surface machinery are material-scoped.  At/below the
        # stated DO basis, do_to_eh returns a typed null rather than an epsilon-
        # fabricated oxygen activity.
        from corrosion_engineering_mcp.utils.redox_state import DO_NERNST_DETECTION_BASIS_MG_L, REDOX_OUTSIDE_SOURCE_DOMAIN_STATUS, do_to_eh, redox_calculation_status
        from corrosion_engineering_mcp.utils.localized_criteria import SCE_VS_SHE_V

        if dissolved_oxygen_mg_L is not None:
            Eh_VSHE, E_redox_equilibrium_warnings = do_to_eh(
                dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
                pH=pH,
                temperature_C=temperature_C,
            )
            E_redox_equilibrium_status = redox_calculation_status(
                Eh_VSHE,
                E_redox_equilibrium_warnings,
            )
            if Eh_VSHE is not None:
                E_redox_equilibrium_VSCE = Eh_VSHE - SCE_VS_SHE_V
        # Galvele Table III measured Fe-18Cr-8Ni, a Type-304 composition.  The
        # 88 mV/decade response is not a generic austenitic coefficient.  Report
        # it only for the exact 304 composition family; 316/316L and every other
        # queried alloy receive an explicit material-scope suppression.
        from corrosion_engineering_mcp.utils.localized_criteria import B_CL_FE18CR8NI_NACL_V_PER_DECADE, GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD

        resolved_slope_material = canonical_id(material_name)
        if resolved_slope_material == "304":
            chloride_response_mV_per_decade = (
                B_CL_FE18CR8NI_NACL_V_PER_DECADE * 1000.0
            )
            chloride_response_scope = {
                "status": "reported_matching_source_composition",
                "queried_material": resolved_slope_material,
                "measured_material": "Fe-18Cr-8Ni (Type 304 composition)",
                "source": "Galvele 1976 Table III, doi:10.1149/1.2132857",
                "source_record": {
                    **GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD,
                    "authors": list(
                        GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD["authors"]
                    ),
                },
                "transfer_to_other_alloys": "not_permitted",
            }
        else:
            chloride_response_mV_per_decade = None
            chloride_response_scope = {
                "status": "suppressed_material_scope_mismatch",
                "queried_material": resolved_slope_material,
                "measured_material": "Fe-18Cr-8Ni (Type 304 composition)",
                "source": "Galvele 1976 Table III, doi:10.1149/1.2132857",
                "source_record": {
                    **GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD,
                    "authors": list(
                        GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD["authors"]
                    ),
                },
                "transfer_to_other_alloys": "not_permitted",
            }
        electrochemical_margin_V = None
        electrochemical_risk = None
        electrochemical_interpretation = None

        if dissolved_oxygen_mg_L is not None:
            # Check if material is in NRL database (HY80, HY100, SS316)
            # Also support common aliases (316L, UNS codes)
            nrl_materials = ["HY80", "HY100", "SS316"]

            # Material alias mapping (per Codex recommendation)
            material_aliases = {
                "316L": "SS316",
                "316": "SS316",
                "UNS S31600": "SS316",
                "UNS S31603": "SS316",  # 316L UNS
                "HY-80": "HY80",
                "HY-100": "HY100",
            }

            material_upper = material_name.upper()
            # Map alias to canonical NRL name
            material_nrl = material_aliases.get(material_upper, material_upper)

            if material_nrl in nrl_materials:
                try:
                    # Import Tier 2 modules
                    # Enforce the NRL response-surface training domain. The
                    # upstream generator fits the pitting surface over 5/25/45 C
                    # and 0/3.1/6.2 M chloride; outside that the polynomial is
                    # extrapolating with no stated behaviour.
                    domain_violations = []
                    if not (NRL_TRAIN_T_C[0] <= temperature_C <= NRL_TRAIN_T_C[1]):
                        domain_violations.append(
                            "temperature "
                            f"{repr(float(temperature_C))} C outside "
                            "NRL training range "
                            f"{NRL_TRAIN_T_C[0]}-{NRL_TRAIN_T_C[1]} C"
                        )
                    cl_M = (Cl_mg_L / 1000.0) / 35.453
                    if cl_M > NRL_TRAIN_CL_M[1]:
                        domain_violations.append(
                            "chloride "
                            f"{repr(float(cl_M))} M exceeds NRL "
                            "training range "
                            f"{NRL_TRAIN_CL_M[0]}-{NRL_TRAIN_CL_M[1]} M"
                        )

                    # Absolute E_pit is deliberately NOT reported - see the field
                    # comment on PittingResult. The chloride *response* is reported
                    # because it is anchored to a measurement.
                    E_pit_VSCE = None
                    electrochemical_margin_V = None
                    electrochemical_risk = None
                    E_pit_status = (
                        "SUPPRESSED_UNVALIDATED: the absolute pitting potential is not "
                        "reported. The implementation is a hybrid of the NRL response "
                        "surface and a Galvele chloride shift that does not correspond to "
                        "the NRL polarization model, and NRL's declared PitPotential "
                        "(-0.2 V_SCE) is identical for HY80, HY100 and SS316, so it cannot "
                        "validate a per-alloy value. Chloride response and the equilibrium "
                        "redox potential are reported instead."
                    )
                    if domain_violations:
                        E_pit_status += " Also out of NRL training domain: " + "; ".join(
                            domain_violations
                        ) + "."

                    chloride_response_note = (
                        "Chloride response: "
                        f"{format_nonzero_fixed(chloride_response_mV_per_decade, 0)} "
                        "mV/decade [Galvele 1976 Table III, Fe-18Cr-8Ni in NaCl]."
                        if chloride_response_mV_per_decade is not None
                        else (
                            "The Galvele 88 mV/decade chloride response is "
                            f"suppressed for {resolved_slope_material}: Table III "
                            "measured Fe-18Cr-8Ni (Type 304 composition)."
                        )
                    )
                    if E_redox_equilibrium_VSCE is None:
                        if (
                            E_redox_equilibrium_status
                            == REDOX_OUTSIDE_SOURCE_DOMAIN_STATUS
                        ):
                            redox_note = (
                                "Equilibrium redox potential is null because "
                                "temperature "
                                f"{repr(float(temperature_C))} C is outside the "
                                "verified source domain; the reported DO is not "
                                "relabelled as anaerobic."
                            )
                        else:
                            redox_note = (
                                "Equilibrium redox potential is undefined because "
                                "DO "
                                f"{repr(float(dissolved_oxygen_mg_L))} "
                                "mg/L is at or below "
                                f"the {repr(float(DO_NERNST_DETECTION_BASIS_MG_L))} "
                                "mg/L oxygen-activity detection basis."
                            )
                    else:
                        redox_note = (
                            f"Equilibrium redox potential "
                            f"{format_nonzero_fixed(E_redox_equilibrium_VSCE, 3)} "
                            "V_SCE from DO "
                            f"{format_nonzero_fixed(dissolved_oxygen_mg_L, 2)} "
                            "mg/L. This is a "
                            "thermodynamic bound, not the alloy's corrosion "
                            "potential - measured E_corr for passive stainless "
                            "in aerated neutral water sits 400-500 mV below it."
                        )
                    electrochemical_interpretation = (
                        f"{redox_note} E_pit is suppressed (see E_pit_status), "
                        "so no pitting driving force is computed. "
                        f"{chloride_response_note}"
                    )
                    if E_redox_equilibrium_warnings:
                        electrochemical_interpretation += (
                            " [RedoxState: "
                            f"{E_redox_equilibrium_warnings[0]}]"
                        )

                    logger.info(
                        "Tier 2 for %s: E_pit suppressed (unvalidated); "
                        "E_redox_eq=%s; status=%s; domain_violations=%s",
                        material_name,
                        (
                            f"{format_nonzero_fixed(E_redox_equilibrium_VSCE, 3)} "
                            "V_SCE"
                            if E_redox_equilibrium_VSCE is not None
                            else "null"
                        ),
                        E_redox_equilibrium_status,
                        len(domain_violations),
                    )

                except ValueError as e:
                    # Activation energy out of range (e.g., HY80 at seawater)
                    logger.warning(
                        f"Tier 2 electrochemical assessment failed for {material_name}: {str(e)}\n"
                        f"Falling back to Tier 1 PREN/CPT only."
                    )
                    # Per Codex: Add explanation to electrochemical_interpretation
                    electrochemical_interpretation = (
                        f"Tier 2 unavailable: NRL coefficients out of valid range at "
                        f"(Cl={format_nonzero_fixed(Cl_mg_L, 0)} mg/L, "
                        f"T={format_nonzero_fixed(temperature_C, 0)}°C, "
                        f"pH={format_nonzero_fixed(pH, 1)}). "
                        f"Use Tier 1 PREN/CPT assessment only."
                    )
                except Exception as e:
                    logger.error(
                        f"Unexpected error in Tier 2 electrochemical assessment for {material_name}: {str(e)}\n"
                        f"Falling back to Tier 1 PREN/CPT only."
                    )
                    # Per Codex: Add explanation to electrochemical_interpretation
                    electrochemical_interpretation = (
                        f"Tier 2 unavailable: Unexpected error during calculation. "
                        f"Use Tier 1 PREN/CPT assessment only."
                    )
            else:
                # Per Codex: Add explanation when material not in NRL database
                logger.info(
                    f"Material '{material_name}' not in NRL database (HY80, HY100, SS316). "
                    f"Tier 2 electrochemical assessment not available. Using Tier 1 PREN/CPT only."
                )
                electrochemical_interpretation = (
                    f"Tier 2 unavailable: Material '{material_name}' not in NRL database "
                    f"(supported: HY80, HY100, SS316, and aliases 316/316L/UNS S31600). "
                    f"Use Tier 1 PREN/CPT assessment only."
                )

        return PittingResult(
            # Tier 1: PREN/CPT (always present)
            CPT_C=CPT,
            PREN=pren,
            Cl_threshold_mg_L=Cl_threshold,
            susceptibility=susceptibility,
            margin_C=margin_C,
            interpretation=interpretation,
            # Tier 2: Electrochemical (optional)
            E_pit_VSCE=E_pit_VSCE,
            E_pit_status=E_pit_status,
            E_redox_equilibrium_VSCE=E_redox_equilibrium_VSCE,
            E_redox_equilibrium_status=E_redox_equilibrium_status,
            E_redox_equilibrium_warnings=list(E_redox_equilibrium_warnings),
            E_redox_detection_basis_mg_L=DO_NERNST_DETECTION_BASIS_MG_L,
            chloride_response_mV_per_decade=chloride_response_mV_per_decade,
            chloride_response_scope=chloride_response_scope,
            electrochemical_margin_V=electrochemical_margin_V,
            electrochemical_risk=electrochemical_risk,
            electrochemical_interpretation=electrochemical_interpretation,
        )

    def calculate_crevice_susceptibility(
        self,
        material_comp: MaterialComposition,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        crevice_gap_mm: float = 0.1,
        current_density_A_per_m2: float = 1e-4,
        material_name: str = "316L",  # BUG-017 fix: Add material name for ASTM G48 lookup
    ) -> CreviceResult:
        """
        Calculate crevice corrosion susceptibility.

        Uses simplified Oldfield-Sutton IR drop model.

        Args:
            material_comp: Material composition
            temperature_C: Operating temperature (°C)
            Cl_mg_L: Chloride concentration (mg/L)
            pH: Bulk solution pH
            crevice_gap_mm: Crevice gap width (mm)
            current_density_A_per_m2: Corrosion current density (A/m²)
            material_name: Material name for ASTM G48 CCT lookup

        Returns:
            CreviceResult with CCT, IR drop, acidification factor

        Per Codex: Simplified Oldfield-Sutton for IR drop iteration
        """
        # Historical G48-test ranking row; suppressed by the public facade.
        cpt_data = get_cpt_from_astm(material_name)
        if cpt_data is not None and "CCT_C" in cpt_data:
            CCT = cpt_data["CCT_C"]
            CPT = cpt_data["CPT_C"]  # Also get CPT for reference
            logger.info("Using internal G48-test CCT ranking coordinate: %s°C", CCT)
        else:
            # Fallback: CCT is typically CPT - 10 to 20°C (more aggressive than pitting)
            pren = material_comp.calculate_pren()
            cpt_corr = CPT_CORRELATIONS.get(material_comp.grade_type, CPT_CORRELATIONS["austenitic"])
            CPT = cpt_corr["m"] * pren + cpt_corr["b"]
            CCT = CPT - 15.0  # Conservative estimate
            logger.warning(f"Material {material_name} not in ASTM G48; using CCT = CPT - 15°C heuristic")

        # Galvele x*i criterion (J. Electrochem. Soc. 123(4) 464, 1976,
        # doi:10.1149/1.2132857) replaces the previous Oldfield-Sutton IR-drop
        # expression, which was driven by a placeholder current density of
        # 1e-4 A/m^2 -- some eight orders of magnitude below the 1 A/cm^2 that
        # Galvele reports inside an active occluded site. That placeholder made
        # IR_drop ~1e-7 V for every input, pinned the acidification factor at
        # exactly 100, and rendered crevice_gap_mm inert.
        #
        # Galvele's result is that localized acidification is governed by the
        # PRODUCT of diffusion path length x and current density i, and that
        # crevice and pitting differ only in x. That is the correct basis for
        # CCT < CPT.
        from corrosion_engineering_mcp.utils.localized_criteria import CRITICAL_PH_CR, I_PASSIVE_A_PER_CM2_DEFAULT, crevice_pH_from_xi, galvele_xi_assessment

        # Diffusion path length. Crevice depth is the governing dimension, not
        # the gap; the 10:1 depth-to-gap aspect ratio is retained as the default
        # geometric assumption from the incumbent model.
        crevice_depth_cm = (crevice_gap_mm / 10.0) * 10.0  # mm -> cm, then x10 aspect

        # Current density inside the crevice. The question a crevice assessment
        # answers is whether attack can INITIATE, so the passive current density
        # is the correct input; an active-pit current density would presuppose
        # the answer.
        i_crevice_A_per_cm2 = (
            current_density_A_per_m2 / 1e4
            if current_density_A_per_m2 and current_density_A_per_m2 > 1e-3
            else I_PASSIVE_A_PER_CM2_DEFAULT
        )

        galvele = galvele_xi_assessment(
            diffusion_path_cm=crevice_depth_cm,
            current_density_A_per_cm2=i_crevice_A_per_cm2,
            critical_pH=CRITICAL_PH_CR,
        )

        pH_crevice = crevice_pH_from_xi(galvele.xi_A_per_cm, pH, CRITICAL_PH_CR)
        delta_pH = max(0.0, pH - pH_crevice)
        acidification_factor = 10.0 ** delta_pH

        # IR drop retained for reporting continuity, now computed from a
        # physically meaningful current density.
        Cl_seawater = 19000.0  # mg/L
        R_seawater = 0.2  # Ω·m
        R_solution = R_seawater * (Cl_seawater / max(Cl_mg_L, 100.0))
        IR_drop = (i_crevice_A_per_cm2 * 1e4) * R_solution * (crevice_depth_cm / 100.0)

        # Temperature margin
        margin_C = CCT - temperature_C

        # Susceptibility. Both the thermal margin and the Galvele regime must be
        # satisfied for a low/moderate call, so the geometry now actually moves
        # the verdict instead of the acidification factor being a constant.
        if margin_C > 15.0 and galvele.regime == "no_acidification":
            susceptibility = "low"
        elif margin_C > 5.0 and not galvele.supports_stable_growth:
            susceptibility = "moderate"
        elif margin_C > -5.0:
            susceptibility = "high"
        else:
            susceptibility = "critical"

        # Interpretation
        if susceptibility == "critical":
            interpretation = (
                f"CRITICAL: T = {temperature_C}°C >> CCT = "
                f"{format_nonzero_fixed(CCT, 1)}°C; "
                f"x·i = {repr(float(galvele.xi_A_per_cm))} A/cm ({galvele.regime}); "
                f"pH drops to {format_nonzero_fixed(pH_crevice, 1)} in crevice"
            )
        elif susceptibility == "high":
            interpretation = (
                f"HIGH RISK: T = {temperature_C}°C near CCT = "
                f"{format_nonzero_fixed(CCT, 1)}°C; "
                f"x·i = {repr(float(galvele.xi_A_per_cm))} A/cm ({galvele.regime}); "
                "crevice acidification factor = "
                f"{format_nonzero_fixed(acidification_factor, 1)}"
            )
        elif susceptibility == "moderate":
            interpretation = (
                f"MODERATE: T = {temperature_C}°C below CCT = "
                f"{format_nonzero_fixed(CCT, 1)}°C (margin "
                f"{format_nonzero_fixed(margin_C, 1)}°C); x·i = "
                f"{repr(float(galvele.xi_A_per_cm))} A/cm "
                f"({galvele.regime}); monitor for crevice formation"
            )
        else:
            interpretation = (
                f"LOW RISK: T = {temperature_C}°C well below CCT = "
                f"{format_nonzero_fixed(CCT, 1)}°C (margin "
                f"{format_nonzero_fixed(margin_C, 1)}°C)"
            )

        return CreviceResult(
            CCT_C=CCT,
            IR_drop_V=IR_drop,
            acidification_factor=acidification_factor,
            susceptibility=susceptibility,
            margin_C=margin_C,
            interpretation=interpretation,
            xi_A_per_cm=galvele.xi_A_per_cm,
            pH_crevice=pH_crevice,
        )

    def calculate_localized_corrosion(
        self,
        material: str,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        crevice_gap_mm: float = 0.1,
        dissolved_oxygen_mg_L: Optional[float] = None,
    ) -> LocalizedResult:
        """Suppress the unsupported aggregate at the direct public accessor."""

        del material, temperature_C, Cl_mg_L, pH, crevice_gap_mm, dissolved_oxygen_mg_L
        raise LocalizedBackendServiceAssessmentSuppressedError()

    def _calculate_localized_corrosion_research(
        self,
        material: str,
        temperature_C: float,
        Cl_mg_L: float,
        pH: float = 7.0,
        crevice_gap_mm: float = 0.1,
        dissolved_oxygen_mg_L: Optional[float] = None,
    ) -> LocalizedResult:
        """Calculate the internal aggregate retained for facade filtering.

        Args:
            material: Material name (e.g., "316L", "2205", "254SMO")
            temperature_C: Operating temperature (°C)
            Cl_mg_L: Chloride concentration (mg/L)
            pH: Solution pH
            crevice_gap_mm: Crevice gap width (mm)
            dissolved_oxygen_mg_L: Dissolved oxygen (mg/L). If provided, enables
                Tier 2 electrochemical pitting assessment for NRL materials.

        Returns:
            LocalizedResult with separate pitting and crevice results

        Per Codex: Separate pitting vs crevice outputs, shared Cl⁻ threshold logic
        Phase 3: Dual-Tier pitting (Tier 1 PREN/CPT + optional Tier 2 E_pit/E_mix)
        """
        # Get material composition from database
        material_comp = self._get_material_composition(material)

        # Calculate pitting susceptibility (Tier 1 + optional Tier 2)
        pitting_result = self.calculate_pitting_susceptibility(
            material_comp, temperature_C, Cl_mg_L, pH,
            material_name=material,
            dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
        )

        # Calculate crevice susceptibility
        crevice_result = self.calculate_crevice_susceptibility(
            material_comp, temperature_C, Cl_mg_L, pH, crevice_gap_mm, material_name=material
        )

        # Overall risk (worst of pitting or crevice)
        risk_levels = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
        pitting_level = risk_levels[pitting_result.susceptibility]
        crevice_level = risk_levels[crevice_result.susceptibility]
        overall_level = max(pitting_level, crevice_level)
        overall_risk = [k for k, v in risk_levels.items() if v == overall_level][0]

        # Everything this verdict currently rests on that we cannot yet defend.
        # While any of these hold, overall_risk is not a material-selection basis
        # and assessment_status says so rather than leaving the caller to infer it.
        caveats: List[str] = [
            "PROVENANCE: the CPT/CCT and chloride-threshold tables carry "
            "INTERNAL_SCREENING_RULE rows that are not derived from a primary "
            "source. See data/PROVENANCE.md.",
            "CLASSIFIER: the risk ladder keys off the ASTM G48 temperature margin, "
            "while ASTM G48 Section 6.1 Methods A/B recipe stoichiometry is "
            "100 g / 270.285 g/mol = 0.36998 mol FeCl3.6H2O; "
            "3 x 0.36998 = 1.10994 mol chloride, or "
            "about 1.11 M chloride at ~1.0 L final solution "
            "(1.23 M on the initial 0.9 L water volume). "
            "Methods C-F use a separate acidified solution. It is "
            "not calibrated to service conditions and floored the common 304/316 "
            "cases at 'critical'. Superseded by corrosion_screen_material_suitability "
            "(R2.5), which keys off the chloride margin and keeps G48 as a ranking "
            "index only.",
            "SCOPE: crevice geometry, deposits and stagnation, weld heat tint, "
            "surface finish, oxidant loading and wetness are not represented.",
        ]

        return LocalizedResult(
            pitting=pitting_result,
            crevice=crevice_result,
            material=material,
            temperature_C=temperature_C,
            Cl_mg_L=Cl_mg_L,
            pH=pH,
            dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
            overall_risk=overall_risk,
            assessment_status="indeterminate",
            assessment_caveats=caveats,
            # Relative ranking survives even where absolute qualification does not:
            # PREN ordering (304 < 316 < 2205 < 254SMO) matches both the G48 ranking
            # and field practice. That is what this field carries until R2.1.
            material_suitability=(
                "RANKING_ONLY: PREN "
                f"{format_nonzero_fixed(pitting_result.PREN, 1)}. Use to compare "
                f"alloys against each other at fixed conditions, not to qualify "
                f"this one."
            ),
        )

    def _get_base_chloride_threshold(self, pren: float) -> float:
        """
        Get base chloride threshold from PREN.

        Internal unsourced heuristic: Cl_coordinate ≈ 10^(PREN/10) mg/L.

        It is retained only inside the research backend; the registered facade
        suppresses this value and everything derived from it.
        """
        # Exponential correlation
        Cl_threshold = 10.0 ** ((pren - 10.0) / 10.0)
        return max(Cl_threshold, 10.0)  # Minimum 10 mg/L

    def _get_material_composition(self, material: str) -> MaterialComposition:
        """
        Resolve material composition, failing closed on anything unrecognised.

        This previously fell back to 316L with only a log warning when the material
        was unknown. The caller saw nothing. Measured before the fix, at 500 mg/L
        chloride and 25 C:

            material="PVC"       -> PREN 24.70, risk "critical"
            material="HDPE"      -> PREN 24.70, risk "critical"
            material="concrete"  -> PREN 24.70, risk "critical"
            material="banana"    -> PREN 24.70, risk "critical"

        Every one of those is 316L's PREN. A pitting verdict on a thermoplastic is
        not conservative, it is meaningless, and it violated this server's own
        fail-closed rule while `corrosion_resolve_material_identity` failed closed on
        the same input.

        Raises:
            UnknownMaterialError: material is not in the composition database.
            ModelNotApplicableError: material resolves, but PREN/CPT does not apply
                to its alloy family (e.g. carbon steel, where PREN = 0 -> CPT = -10 C
                is a number without meaning).
        """
        # Resolve through the canonical alias index FIRST. get_material_data()
        # does bidirectional SUBSTRING matching
        # (data/authoritative_materials_data.py:117), so calling it first left the
        # fail-closed gate wide open: "banana316L", "316L pipe", "XXX316LXXX" and
        # "my 304 thing" all resolved silently to a real alloy. Plain "banana"
        # raised, which is why the first version of this fix looked correct.
        #
        # canonical_id() is exact-or-alias only, so an arbitrary string containing
        # an alloy name no longer smuggles itself through.
        try:
            resolved = canonical_id(material)
        except MaterialCatalogError:
            raise UnknownMaterialError(material) from None

        mat_data = get_material_data(resolved)

        if mat_data is None:
            # Two catalogs coexist and disagree: data/materials.yaml carries 25
            # records, data/materials_compositions.csv carries 18. A material can
            # be perfectly well known and still have no PREN inputs here — HY80,
            # for instance, is in the YAML with an explicit `PREN: null`, which is
            # the catalog stating that PREN does not apply to a low-alloy naval
            # steel. Distinguish that from a genuinely unrecognised string, so the
            # error tells the caller which problem they have.
            known = get_material(resolved)
            raise ModelNotApplicableError(
                material=material,
                grade_type=known.classification,
                model="PREN/CPT localized corrosion",
                # Deliberately does NOT name corrosion_predict_aerated_chloride.
                # That tool is currently withdrawn (~1000x low, and it silently
                # coerces any unrecognised material to carbon steel), so routing
                # Ti/Cu/HY80 callers into it would have replaced one wrong answer
                # with a more dangerous one. Restore the pointer when that tool is
                # corrected and re-enabled.
                alternative=(
                    "no substitute model is currently available on this server for "
                    "this alloy family; assess by hand or by alloy-specific standard"
                ),
            )

        # PREN/CPT is a pitting-resistance construct for passive Cr-bearing alloys.
        # Returning PREN 0 for carbon steel is the same silent-substitution defect
        # in a different costume: a real-looking number from an inapplicable model.
        if mat_data.grade_type not in _PREN_APPLICABLE_GRADE_TYPES:
            raise ModelNotApplicableError(
                material=material,
                grade_type=mat_data.grade_type,
                model="PREN/CPT localized corrosion",
                # Same reason as the branch above: corrosion_predict_aerated_chloride
                # is withdrawn. This branch was missed on the first pass, so
                # carbon_steel and Copper were still being redirected into it.
                alternative=(
                    "no substitute model is currently available on this server for "
                    "this alloy family; assess by hand or by alloy-specific standard"
                ),
            )

        return MaterialComposition(
            Cr=mat_data.Cr_wt_pct,
            Mo=mat_data.Mo_wt_pct,
            N=mat_data.N_wt_pct,
            Ni=mat_data.Ni_wt_pct,
            grade_type=mat_data.grade_type,
        )
