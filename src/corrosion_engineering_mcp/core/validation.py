"""Shared corrosion tool validation bounds and scope gates.

This module is intentionally small and data-only. Public Pydantic schemas and
runtime model gates both import these constants so the JSON contract cannot
advertise inputs that the calculation layer later rejects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
import inspect
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Callable, Mapping, MutableMapping

from corrosion_engineering_mcp.core.errors import CorrosionPublicError, validate_float_domain_inputs


class ScopeSeverity(str, Enum):
    """Severity for a validation or scope issue."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class NumericBounds:
    """Inclusive numeric bounds for public corrosion tool inputs."""

    name: str
    minimum: float
    maximum: float
    unit: str = ""

    def contains(self, value: float | int | None) -> bool:
        if value is None:
            return True
        return self.minimum <= float(value) <= self.maximum

    def field_kwargs(self) -> dict[str, Any]:
        """Return keyword args suitable for pydantic ``Field``."""

        return {"ge": self.minimum, "le": self.maximum}

    def describe(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        return (
            f"{self.name} must be between {repr(float(self.minimum))} and "
            f"{repr(float(self.maximum))}{unit}"
        )


@dataclass(frozen=True)
class ScopeIssue:
    """A public, structured scope issue."""

    code: str
    message: str
    severity: ScopeSeverity = ScopeSeverity.WARNING
    parameter: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopeGateResult:
    """Result from checking an input against a model validity envelope."""

    model: str
    issues: tuple[ScopeIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == ScopeSeverity.ERROR for issue in self.issues)

    @property
    def warnings(self) -> list[ScopeIssue]:
        return [issue for issue in self.issues if issue.severity == ScopeSeverity.WARNING]

    @property
    def errors(self) -> list[ScopeIssue]:
        return [issue for issue in self.issues if issue.severity == ScopeSeverity.ERROR]


# Generic aqueous public inputs.
PH_AQUEOUS = NumericBounds("pH", 0.0, 14.0)
DISSOLVED_OXYGEN_MG_L = NumericBounds("dissolved oxygen", 0.0, 50.0, "mg/L")
CHLORIDE_MG_L = NumericBounds("chloride", 0.0, 250_000.0, "mg/L")
PRESSURE_BAR = NumericBounds("pressure", 0.0, 1_500.0, "bar")

# Tool-specific envelopes.
GALVANIC_TEMPERATURE_C = NumericBounds("galvanic temperature", 5.0, 80.0, "degC")
GALVANIC_PH = NumericBounds("galvanic pH", 0.0, 14.0)

POURBAIX_TEMPERATURE_C = NumericBounds("Pourbaix temperature", 0.0, 150.0, "degC")
POURBAIX_PH = NumericBounds("Pourbaix pH", 0.0, 14.0)
POURBAIX_POTENTIAL_VSHE = NumericBounds("Pourbaix potential", -3.0, 3.0, "V_SHE")
POURBAIX_GRID_POINTS = NumericBounds("Pourbaix grid points", 10, 200)

HANDBOOK_TEXT_LENGTH = NumericBounds("handbook text length", 3, 500, "characters")

# NRL coefficient provenance notes use chloride molarity; public API uses mg/L.
NRL_CHLORIDE_MOLAR = NumericBounds("NRL chloride", 0.02, 0.6, "mol/L")
CHLORIDE_MG_L_PER_MOLAR = 35_453.0


def chloride_mg_l_to_molar(chloride_mg_L: float) -> float:
    """Convert chloride mg/L to mol/L using Cl atomic mass."""

    return chloride_mg_L / CHLORIDE_MG_L_PER_MOLAR


def require_bounds(parameter: str, value: float | int | None, bounds: NumericBounds) -> None:
    """Raise ``ValueError`` if a public input is outside inclusive bounds."""

    if not bounds.contains(value):
        raise ValueError(
            f"{parameter}={value!r} is outside the supported range: {bounds.describe()}"
        )


def require_ordered_range(
    lower_name: str,
    lower_value: float,
    upper_name: str,
    upper_value: float,
    bounds: NumericBounds,
) -> None:
    """Validate a lower/upper pair against shared bounds."""

    require_bounds(lower_name, lower_value, bounds)
    require_bounds(upper_name, upper_value, bounds)
    if lower_value >= upper_value:
        raise ValueError(f"{lower_name} must be less than {upper_name}")


def _bounds_issue(parameter: str, value: float | int | None, bounds: NumericBounds) -> ScopeIssue | None:
    if bounds.contains(value):
        return None
    return ScopeIssue(
        code="OUT_OF_SCHEMA_BOUNDS",
        message=f"{parameter}={value!r} is outside {bounds.describe()}",
        severity=ScopeSeverity.ERROR,
        parameter=parameter,
        details={
            "reported_value": value,
            "minimum": bounds.minimum,
            "maximum": bounds.maximum,
        },
    )


def gate_galvanic_scope(
    *,
    temperature_C: float,
    pH: float,
    chloride_mg_L: float,
    dissolved_oxygen_mg_L: float | None = None,
    pressure_bar: float | None = None,
) -> ScopeGateResult:
    """Check public galvanic inputs against the current NRL model envelope."""

    issues: list[ScopeIssue] = []
    for parameter, value, bounds in (
        ("temperature_C", temperature_C, GALVANIC_TEMPERATURE_C),
        ("pH", pH, GALVANIC_PH),
        ("chloride_mg_L", chloride_mg_L, CHLORIDE_MG_L),
        ("dissolved_oxygen_mg_L", dissolved_oxygen_mg_L, DISSOLVED_OXYGEN_MG_L),
        ("pressure_bar", pressure_bar, PRESSURE_BAR),
    ):
        issue = _bounds_issue(parameter, value, bounds)
        if issue:
            issues.append(issue)

    chloride_M = chloride_mg_l_to_molar(chloride_mg_L)
    if not NRL_CHLORIDE_MOLAR.contains(chloride_M):
        issues.append(
            ScopeIssue(
                code="OUTSIDE_NRL_CHLORIDE_VALIDATION",
                message=(
                    f"chloride={repr(float(chloride_M))} mol/L is outside the NRL "
                    "validated chloride envelope "
                    f"{repr(float(NRL_CHLORIDE_MOLAR.minimum))}-"
                    f"{repr(float(NRL_CHLORIDE_MOLAR.maximum))} mol/L"
                ),
                parameter="chloride_mg_L",
                details={
                    "reported_chloride_mol_L": chloride_M,
                    "reported_chloride_mg_L": chloride_mg_L,
                    "minimum_mol_L": NRL_CHLORIDE_MOLAR.minimum,
                    "maximum_mol_L": NRL_CHLORIDE_MOLAR.maximum,
                },
            )
        )

    if dissolved_oxygen_mg_L is not None and dissolved_oxygen_mg_L <= 0.01:
        issues.append(
            ScopeIssue(
                code="LOW_OXYGEN_SCOPE_REVIEW",
                message=(
                    "Dissolved oxygen is at or below deaerated conditions; ORR kinetics "
                    "must not be inferred from air-saturated NRL seawater data."
                ),
                parameter="dissolved_oxygen_mg_L",
                details={
                    "reported_dissolved_oxygen_mg_L": dissolved_oxygen_mg_L,
                    "deaerated_upper_boundary_mg_L": 0.01,
                },
            )
        )

    return ScopeGateResult(model="NRL_Butler_Volmer_Mixed_Potential", issues=tuple(issues))


def gate_pourbaix_scope(
    *,
    temperature_C: float,
    pH_range_min: float,
    pH_range_max: float,
    E_range_min_VSHE: float,
    E_range_max_VSHE: float,
    grid_points: int,
) -> ScopeGateResult:
    """Check Pourbaix diagram public inputs against runtime bounds."""

    issues: list[ScopeIssue] = []
    checks = (
        ("temperature_C", temperature_C, POURBAIX_TEMPERATURE_C),
        ("pH_range_min", pH_range_min, POURBAIX_PH),
        ("pH_range_max", pH_range_max, POURBAIX_PH),
        ("E_range_min_VSHE", E_range_min_VSHE, POURBAIX_POTENTIAL_VSHE),
        ("E_range_max_VSHE", E_range_max_VSHE, POURBAIX_POTENTIAL_VSHE),
        ("grid_points", grid_points, POURBAIX_GRID_POINTS),
    )
    for parameter, value, bounds in checks:
        issue = _bounds_issue(parameter, value, bounds)
        if issue:
            issues.append(issue)

    if pH_range_min >= pH_range_max:
        issues.append(
            ScopeIssue(
                code="INVALID_PH_RANGE",
                message="pH_range_min must be less than pH_range_max",
                severity=ScopeSeverity.ERROR,
                details={
                    "pH_range_min": pH_range_min,
                    "pH_range_max": pH_range_max,
                },
            )
        )
    if E_range_min_VSHE >= E_range_max_VSHE:
        issues.append(
            ScopeIssue(
                code="INVALID_POTENTIAL_RANGE",
                message="E_range_min_VSHE must be less than E_range_max_VSHE",
                severity=ScopeSeverity.ERROR,
                details={
                    "E_range_min_VSHE": E_range_min_VSHE,
                    "E_range_max_VSHE": E_range_max_VSHE,
                },
            )
        )
    return ScopeGateResult(model="Simplified_Pourbaix_Nernst", issues=tuple(issues))


# ---------------------------------------------------------------------------
# Registered tool input contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NumericInputConstraint:
    """One numeric field's schema and direct-accessor contract.

    The limits are deliberately independent of the server-wide 1e-12
    floating-point execution floor.  That separate contract is applied to all
    numeric inputs by :func:`core.errors.validate_float_domain_inputs`; this
    declaration owns the engineering/schema range only.
    """

    minimum: float | None = None
    maximum: float | None = None
    minimum_exclusive: bool = False
    maximum_exclusive: bool = False
    integer: bool = False

    def __post_init__(self) -> None:
        if self.minimum is None and self.maximum is None:
            raise ValueError("a numeric input constraint needs at least one bound")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("numeric input constraint minimum exceeds maximum")

    def contains(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, bool) or not isinstance(value, Real):
            return False
        converted = float(value)
        if not math.isfinite(converted):
            return False
        if self.integer and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            return False
        if self.minimum is not None:
            if self.minimum_exclusive and converted <= self.minimum:
                return False
            if not self.minimum_exclusive and converted < self.minimum:
                return False
        if self.maximum is not None:
            if self.maximum_exclusive and converted >= self.maximum:
                return False
            if not self.maximum_exclusive and converted > self.maximum:
                return False
        return True

    def schema_keywords(self) -> dict[str, float]:
        keywords: dict[str, float] = {}
        if self.minimum is not None:
            key = "gt" if self.minimum_exclusive else "ge"
            keywords[key] = self.minimum
        if self.maximum is not None:
            key = "lt" if self.maximum_exclusive else "le"
            keywords[key] = self.maximum
        return keywords

    def accepted_boundary_values(self) -> tuple[int | float, ...]:
        values: list[int | float] = []
        if self.minimum is not None:
            if self.integer:
                lower: int | float = int(self.minimum) + int(
                    self.minimum_exclusive
                )
            else:
                lower = (
                    math.nextafter(self.minimum, math.inf)
                    if self.minimum_exclusive
                    else self.minimum
                )
            values.append(lower)
        if self.maximum is not None:
            if self.integer:
                upper: int | float = int(self.maximum) - int(
                    self.maximum_exclusive
                )
            else:
                upper = (
                    math.nextafter(self.maximum, -math.inf)
                    if self.maximum_exclusive
                    else self.maximum
                )
            if upper not in values:
                values.append(upper)
        return tuple(values)

    def one_step_outside_values(self) -> tuple[int | float, ...]:
        values: list[int | float] = []
        if self.minimum is not None:
            if self.integer:
                lower: int | float = (
                    int(self.minimum)
                    if self.minimum_exclusive
                    else int(self.minimum) - 1
                )
            else:
                lower = (
                    self.minimum
                    if self.minimum_exclusive
                    else math.nextafter(self.minimum, -math.inf)
                )
            values.append(lower)
        if self.maximum is not None:
            if self.integer:
                upper: int | float = (
                    int(self.maximum)
                    if self.maximum_exclusive
                    else int(self.maximum) + 1
                )
            else:
                upper = (
                    self.maximum
                    if self.maximum_exclusive
                    else math.nextafter(self.maximum, math.inf)
                )
            if upper not in values:
                values.append(upper)
        return tuple(values)


@dataclass(frozen=True)
class ToolInputContract:
    """All direct numeric/type constraints for one registered tool."""

    numeric_fields: Mapping[str, NumericInputConstraint] = field(
        default_factory=dict
    )
    strict_boolean_fields: frozenset[str] = field(default_factory=frozenset)
    native_accessor_checks: Mapping[str, frozenset[str]] = field(
        default_factory=dict
    )


def _n(
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    minimum_exclusive: bool = False,
    maximum_exclusive: bool = False,
    integer: bool = False,
) -> NumericInputConstraint:
    return NumericInputConstraint(
        minimum=minimum,
        maximum=maximum,
        minimum_exclusive=minimum_exclusive,
        maximum_exclusive=maximum_exclusive,
        integer=integer,
    )


def _contract(
    numeric_fields: Mapping[str, NumericInputConstraint] | None = None,
    *,
    strict_boolean_fields: tuple[str, ...] = (),
    native_accessor_checks: Mapping[str, tuple[str, ...]] | None = None,
) -> ToolInputContract:
    return ToolInputContract(
        numeric_fields=MappingProxyType(dict(numeric_fields or {})),
        strict_boolean_fields=frozenset(strict_boolean_fields),
        native_accessor_checks=MappingProxyType(
            {
                name: frozenset(checks)
                for name, checks in dict(native_accessor_checks or {}).items()
            }
        ),
    )


def _native_all(*field_names: str) -> dict[str, tuple[str, ...]]:
    """Declare bounds whose established typed error is produced in-tool."""

    return {
        field_name: ("minimum", "maximum", "type")
        for field_name in field_names
    }


# This is the single declaration consumed by discovery metadata, facade
# middleware, and decorated exported Python accessors.  Empty entries are
# intentional: they make the 23-tool registry closed, so a new bounded field on
# an existing tool cannot evade the parity test by lacking a tool entry.
TOOL_INPUT_CONTRACTS: Mapping[str, ToolInputContract] = MappingProxyType(
    {
        "corrosion_screen_materials": _contract(),
        "corrosion_query_typical_rates": _contract(),
        "corrosion_identify_mechanism": _contract(),
        "corrosion_assess_galvanic": _contract(
            {
                "temperature_C": _n(5.0, 80.0),
                "pH": _n(0.0, 14.0),
                "chloride_mg_L": _n(0.0, 250_000.0),
                "area_ratio_cathode_to_anode": _n(
                    0.0, minimum_exclusive=True
                ),
                "velocity_m_s": _n(0.0),
                "dissolved_oxygen_mg_L": _n(0.0, 50.0),
                "sulfide_mg_L": _n(0.0),
            },
            strict_boolean_fields=("allow_auto_orient",),
        ),
        "corrosion_generate_pourbaix": _contract(
            {
                "pH_range_min": _n(0.0, 14.0),
                "pH_range_max": _n(0.0, 14.0),
                "grid_points": _n(10.0, 200.0, integer=True),
            },
            native_accessor_checks={"grid_points": ("maximum",)},
        ),
        "corrosion_resolve_material_identity": _contract(),
        "corrosion_langelier_index": _contract(
            {"pH": _n(0.0, 14.0), "temperature_C": _n(0.0, 100.0)}
        ),
        "corrosion_predict_scaling": _contract(
            {"pH": _n(0.0, 14.0), "temperature_C": _n(0.0, 100.0)}
        ),
        "corrosion_predict_co2": _contract(
            {
                "temperature_C": _n(20.0, 150.0),
                "pressure_bar": _n(0.0, minimum_exclusive=True),
                "co2_fraction": _n(0.0, 1.0),
                "h2s_fraction": _n(0.0, 1.0),
                "pH": _n(3.5, 6.5),
                "bicarbonate_mg_L": _n(0.0),
                "ionic_strength_mg_L": _n(0.0, 175_000.0),
                "superficial_gas_velocity_m_s": _n(0.0),
                "superficial_liquid_velocity_m_s": _n(0.0),
                "gas_mass_flow_kg_hr": _n(0.0, minimum_exclusive=True),
                "liquid_mass_flow_kg_hr": _n(0.0, minimum_exclusive=True),
                "gas_volumetric_flow_m3_hr": _n(
                    0.0, minimum_exclusive=True
                ),
                "liquid_volumetric_flow_m3_hr": _n(
                    0.0, minimum_exclusive=True
                ),
                "liquid_holdup_percent": _n(0.0, 100.0),
                "gas_viscosity_cp": _n(0.0, minimum_exclusive=True),
                "liquid_viscosity_cp": _n(0.0, minimum_exclusive=True),
                "pipe_roughness_m": _n(0.0),
                "pipe_diameter_m": _n(0.0, minimum_exclusive=True),
            },
            native_accessor_checks=_native_all(
                "temperature_C",
                "pressure_bar",
                "co2_fraction",
                "h2s_fraction",
                "pH",
                "bicarbonate_mg_L",
                "ionic_strength_mg_L",
                "superficial_gas_velocity_m_s",
                "superficial_liquid_velocity_m_s",
                "gas_mass_flow_kg_hr",
                "liquid_mass_flow_kg_hr",
                "gas_volumetric_flow_m3_hr",
                "liquid_volumetric_flow_m3_hr",
                "liquid_holdup_percent",
                "gas_viscosity_cp",
                "liquid_viscosity_cp",
                "pipe_roughness_m",
                "pipe_diameter_m",
            ),
        ),
        "corrosion_screen_sour_service": _contract(
            {
                "H2S_ppmv": _n(0.0, 1_000_000.0),
                "pressure_bar_abs": _n(0.0, minimum_exclusive=True),
                "pH2S_bar": _n(0.0),
                "in_situ_pH": _n(0.0, 14.0),
                "dissolved_sulfide_mg_L": _n(0.0),
                "hardness_HRC": _n(0.0, 70.0),
                "temperature_C": _n(-50.0, 300.0),
            },
            strict_boolean_fields=("welded",),
            native_accessor_checks=_native_all(
                "H2S_ppmv", "pressure_bar_abs", "pH2S_bar"
            ),
        ),
        "corrosion_screen_sulfide_exposure": _contract(
            {
                "pH": _n(0.0, 14.0),
                "total_sulfide_mg_L": _n(0.0),
                "H2S_headspace_ppmv": _n(0.0, 1_000_000.0),
                "temperature_C": _n(-5.0, 150.0),
                "pressure_bar_abs": _n(1.0e-12, 300.0),
                "ionic_strength_M": _n(0.0, 10.0),
                "Cl_mg_L": _n(0.0),
                "SO4_mg_L": _n(0.0),
                "measured_ORP_mV": _n(-2_000.0, 2_000.0),
                "measured_ORP_uncertainty_mV": _n(0.0),
                "dissolved_Fe_mg_L": _n(0.0),
            },
            native_accessor_checks=_native_all(
                "pH",
                "total_sulfide_mg_L",
                "H2S_headspace_ppmv",
                "temperature_C",
                "pressure_bar_abs",
                "ionic_strength_M",
                "Cl_mg_L",
                "SO4_mg_L",
                "measured_ORP_mV",
                "measured_ORP_uncertainty_mV",
                "dissolved_Fe_mg_L",
            ),
        ),
        "corrosion_screen_mic": _contract(
            {
                "ORP_mV": _n(-2_000.0, 2_000.0),
                "ORP_uncertainty_mV": _n(0.0),
                "dissolved_O2_mg_L": _n(0.0, 50.0),
                "sulfate_mg_L": _n(0.0),
                "sulfide_mg_L": _n(0.0),
                "VFA_mg_L": _n(0.0),
                "alkalinity_mg_L": _n(0.0),
                "bulk_pH": _n(0.0, 14.0),
                "interface_pH": _n(0.0, 14.0),
                "Mn_mg_L": _n(0.0),
                "Fe_mg_L": _n(0.0),
                "OCP_ennoblement_mV": _n(-2_000.0, 2_000.0),
                "dsrAB_copies_per_cm2": _n(0.0),
                "dsrAB_uncertainty_copies_per_cm2": _n(0.0),
                "headspace_H2S_ppmv": _n(0.0, 1_000_000.0),
                "relative_humidity_pct": _n(0.0, 100.0),
                "surface_pH": _n(0.0, 14.0),
                "observed_corrosion_rate_mm_y": _n(0.0),
                "observed_rate_window_days": _n(
                    0.0, minimum_exclusive=True
                ),
            },
            strict_boolean_fields=(
                "aerobic",
                "manganese_oxidising_biofilm_present",
                "biomineralised_MnO2_present",
                "OCP_ennoblement_trajectory_observed",
                "deposits_present",
                "acid_producing_biofilm_present",
                "sulfur_oxidising_biofilm_present",
            ),
            native_accessor_checks={
                **_native_all(
                    "ORP_uncertainty_mV",
                    "sulfate_mg_L",
                    "sulfide_mg_L",
                    "VFA_mg_L",
                    "alkalinity_mg_L",
                    "bulk_pH",
                    "interface_pH",
                    "Mn_mg_L",
                    "Fe_mg_L",
                    "dsrAB_copies_per_cm2",
                    "dsrAB_uncertainty_copies_per_cm2",
                    "headspace_H2S_ppmv",
                    "relative_humidity_pct",
                    "surface_pH",
                    "observed_corrosion_rate_mm_y",
                ),
                "observed_rate_window_days": ("type",),
            },
        ),
        "corrosion_screen_concrete_exposure": _contract(
            {
                "sulfate_mg_L": _n(0.0),
                "pH": _n(0.0, 14.0),
                "magnesium_mg_L": _n(0.0),
                "ammonium_mg_L": _n(0.0),
                "aggressive_CO2_mg_L": _n(0.0),
                "chloride_mg_L": _n(0.0),
                "temperature_C": _n(-30.0, 120.0),
                "headspace_H2S_ppmv": _n(0.0, 1_000_000.0),
                "relative_humidity_pct": _n(0.0, 100.0),
            },
            strict_boolean_fields=("carbonate_aggregate",),
            native_accessor_checks=_native_all(
                "sulfate_mg_L",
                "pH",
                "magnesium_mg_L",
                "ammonium_mg_L",
                "aggressive_CO2_mg_L",
                "chloride_mg_L",
                "temperature_C",
                "headspace_H2S_ppmv",
                "relative_humidity_pct",
            ),
        ),
        "corrosion_screen_material_suitability": _contract(
            {
                "Cl_mg_L": _n(0.0, 300_000.0),
                "temperature_C": _n(-20.0, 200.0),
                "pH": _n(0.0, 14.0),
                "measured_ORP_mV": _n(-2_000.0, 2_000.0),
                "measured_ORP_uncertainty_mV": _n(0.0),
                "sulfide_mg_L": _n(0.0),
            },
            native_accessor_checks=_native_all("temperature_C", "pH"),
        ),
        "corrosion_screen_galvanic": _contract(
            {
                "area_ratio_cathode_to_anode": _n(
                    0.0, 10_000.0, minimum_exclusive=True
                ),
                "sulfide_mg_L": _n(0.0),
                "sulfide_uncertainty_mg_L": _n(0.0),
                "dissolved_oxygen_mg_L": _n(0.0, 50.0),
                "dissolved_oxygen_uncertainty_mg_L": _n(0.0),
            },
            strict_boolean_fields=("crevice_present",),
            native_accessor_checks={
                "area_ratio_cathode_to_anode": ("minimum", "type"),
            },
        ),
        "corrosion_assess_oxidant_exposure": _contract(
            {
                "pH": _n(0.0, 14.0),
                "temperature_C": _n(-10.0, 150.0),
                # Deliberately valid under every selector: when free_chlorine is
                # not primary, the accessor assesses it as a concurrent supplied
                # exposure rather than rejecting or silently discarding it.
                "free_chlorine_mg_L_as_Cl2": _n(0.0),
                "Cl_mg_L": _n(0.0),
                "measured_ORP_mV": _n(-2_000.0, 2_000.0),
            },
            native_accessor_checks=_native_all(
                "pH",
                "temperature_C",
                "free_chlorine_mg_L_as_Cl2",
                "Cl_mg_L",
                "measured_ORP_mV",
            ),
        ),
        "corrosion_predict_co2_h2s": _contract(
            {
                "co2_fraction": _n(0.0, 1.0),
                "h2s_fraction": _n(0.0, 1.0),
            }
        ),
        "corrosion_predict_aerated_chloride": _contract(
            {
                "temperature_C": _n(0.0, 80.0),
                "chloride_mg_L": _n(0.0),
                "pH": _n(4.0, 10.0),
                "dissolved_oxygen_mg_L": _n(0.0, 50.0),
            }
        ),
        "corrosion_assess_localized": _contract(
            {
                "temperature_C": _n(0.0, 150.0),
                "Cl_mg_L": _n(0.0),
                "pH": _n(0.0, 14.0),
                "crevice_gap_mm": _n(1.0e-12, 10.0),
                "dissolved_oxygen_mg_L": _n(0.0, 50.0),
            },
            native_accessor_checks=_native_all(
                "temperature_C", "Cl_mg_L", "pH", "crevice_gap_mm"
            ),
        ),
        "corrosion_calculate_pren": _contract(
            {
                "Cr_wt_pct": _n(0.0, 30.0),
                "Mo_wt_pct": _n(0.0, 10.0),
                "N_wt_pct": _n(0.0, 1.0),
                "Mn_wt_pct": _n(0.0, 20.0),
            },
            native_accessor_checks=_native_all(
                "Cr_wt_pct", "Mo_wt_pct", "N_wt_pct", "Mn_wt_pct"
            ),
        ),
        "corrosion_estimate_service_life": _contract(
            {
                "thickness_mm": _n(0.0, minimum_exclusive=True),
                "corrosion_rate_mm_y": _n(0.0),
                "minimum_thickness_mm": _n(0.0, minimum_exclusive=True),
                "design_allowance_mm": _n(0.0),
                "safety_factor": _n(1.0, 3.0),
            }
        ),
        "corrosion_get_phreeqc_status": _contract(),
        "corrosion_get_server_info": _contract(),
    }
)


class InputContractViolationError(CorrosionPublicError, ValueError):
    """A direct Python call violates the registered facade contract."""

    def __init__(
        self,
        tool_name: str,
        field_name: str,
        value: Any,
        *,
        constraint: NumericInputConstraint | None = None,
        expected_type: str | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "reason_code": "INPUT_CONTRACT_VIOLATION",
            "tool": tool_name,
            "field": field_name,
            "result_emitted": False,
        }
        if isinstance(value, Real) and not isinstance(value, bool):
            converted = float(value)
            if math.isfinite(converted):
                details["value"] = value
        if constraint is not None:
            details.update(
                {
                    "minimum": constraint.minimum,
                    "maximum": constraint.maximum,
                    "minimum_exclusive": constraint.minimum_exclusive,
                    "maximum_exclusive": constraint.maximum_exclusive,
                    "integer_required": constraint.integer,
                }
            )
        if expected_type is not None:
            details["expected_type"] = expected_type
        super().__init__(
            "INPUT_CONTRACT_VIOLATION",
            "INPUT_CONTRACT_VIOLATION: a direct Python accessor argument did "
            "not satisfy the same registered contract advertised by the MCP "
            "schema; no result was computed.",
            details=details,
        )


def _normalized_contract_values(
    tool_name: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(values)
    params = normalized.get("params")
    model_dump = getattr(params, "model_dump", None)
    if callable(model_dump):
        normalized.update(model_dump(mode="python"))
    if tool_name == "corrosion_generate_pourbaix" and "pH_range" in normalized:
        pH_range = normalized["pH_range"]
        if isinstance(pH_range, (tuple, list)) and len(pH_range) == 2:
            normalized["pH_range_min"] = pH_range[0]
            normalized["pH_range_max"] = pH_range[1]
    return normalized


def validate_tool_input_contract(
    tool_name: str,
    values: Mapping[str, Any],
    *,
    defer_native_accessor_checks: bool = False,
) -> Mapping[str, Any]:
    """Validate supplied values against the one registered tool declaration.

    Decorated Python accessors may defer a declared side to an established
    in-tool validator so its more specific public error family remains stable.
    Discovery and facade validation never defer: the declaration remains the
    complete authority for the public contract.
    """

    try:
        contract = TOOL_INPUT_CONTRACTS[tool_name]
    except KeyError:
        raise KeyError(f"unregistered corrosion input contract: {tool_name}") from None
    normalized = _normalized_contract_values(tool_name, values)
    for field_name, constraint in contract.numeric_fields.items():
        if field_name not in normalized or normalized[field_name] is None:
            continue
        value = normalized[field_name]
        if not constraint.contains(value):
            violation_kinds: set[str] = set()
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
                or (
                    constraint.integer
                    and (not isinstance(value, int) or isinstance(value, bool))
                )
            ):
                violation_kinds.add("type")
            else:
                converted = float(value)
                if constraint.minimum is not None and (
                    converted < constraint.minimum
                    or (
                        constraint.minimum_exclusive
                        and converted == constraint.minimum
                    )
                ):
                    violation_kinds.add("minimum")
                if constraint.maximum is not None and (
                    converted > constraint.maximum
                    or (
                        constraint.maximum_exclusive
                        and converted == constraint.maximum
                    )
                ):
                    violation_kinds.add("maximum")
            native_checks = contract.native_accessor_checks.get(
                field_name, frozenset()
            )
            if (
                defer_native_accessor_checks
                and violation_kinds
                and violation_kinds.issubset(native_checks)
            ):
                continue
            raise InputContractViolationError(
                tool_name,
                field_name,
                value,
                constraint=constraint,
            )
    for field_name in contract.strict_boolean_fields:
        if field_name not in normalized or normalized[field_name] is None:
            continue
        value = normalized[field_name]
        if type(value) is not bool:
            raise InputContractViolationError(
                tool_name,
                field_name,
                value,
                expected_type="strict_boolean",
            )
    return values


REGISTERED_CONTRACT_ACCESSORS: dict[str, list[Callable[..., Any]]] = {}


def _validate_accessor_float_domain(
    func: Callable[..., Any], values: Mapping[str, Any]
) -> None:
    """Apply the accessor's own declared floating-point execution floor.

    Several defense-in-depth tests deliberately reopen a module-local floor to
    exercise downstream underflow handling.  Reading the wrapped function's
    live global keeps that supported probe intact instead of freezing a second
    copy of the floor in this decorator.
    """

    local_floor = func.__globals__.get("MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE")
    if isinstance(local_floor, Real) and not isinstance(local_floor, bool):
        validate_float_domain_inputs(
            values,
            minimum_nonzero_magnitude=float(local_floor),
        )
    else:
        validate_float_domain_inputs(values)


def enforce_tool_input_contract(
    tool_name: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate an exported accessor with the registered facade contract."""

    if tool_name not in TOOL_INPUT_CONTRACTS:
        raise KeyError(f"unregistered corrosion input contract: {tool_name}")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(func)

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                _validate_accessor_float_domain(func, bound.arguments)
                validate_tool_input_contract(
                    tool_name,
                    bound.arguments,
                    defer_native_accessor_checks=True,
                )
                return await func(*args, **kwargs)

            wrapped: Callable[..., Any] = async_wrapper
        else:

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                _validate_accessor_float_domain(func, bound.arguments)
                validate_tool_input_contract(
                    tool_name,
                    bound.arguments,
                    defer_native_accessor_checks=True,
                )
                return func(*args, **kwargs)

            wrapped = sync_wrapper

        setattr(wrapped, "__corrosion_tool_input_contract__", tool_name)
        REGISTERED_CONTRACT_ACCESSORS.setdefault(tool_name, []).append(wrapped)
        return wrapped

    return decorator


def _numeric_schema_branches(schema: MutableMapping[str, Any]):
    if schema.get("type") in {"number", "integer"}:
        yield schema
    for keyword in ("anyOf", "oneOf"):
        children = schema.get(keyword, [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, MutableMapping):
                    yield from _numeric_schema_branches(child)


def _discovered_numeric_bounds(
    schema: MutableMapping[str, Any],
) -> tuple[float | None, float | None, bool, bool]:
    """Read the effective engineering bounds from a Pydantic field schema.

    A zero ``const`` plus a positive branch is the server-wide floating-point
    floor representation; its engineering minimum remains zero.  ``allOf`` is
    deliberately not traversed because the float-domain transform runs after
    this parity check and owns that independent execution-floor annotation.
    """

    branches = list(_numeric_schema_branches(schema))
    lower_values: list[float | None] = []
    upper_values: list[float | None] = []
    for branch in branches:
        constant = branch.get("const")
        if isinstance(constant, Real) and not isinstance(constant, bool):
            lower_values.append(float(constant))
            upper_values.append(float(constant))
            continue
        lower = branch.get("minimum", branch.get("exclusiveMinimum"))
        upper = branch.get("maximum", branch.get("exclusiveMaximum"))
        lower_values.append(
            float(lower)
            if isinstance(lower, Real) and not isinstance(lower, bool)
            else None
        )
        upper_values.append(
            float(upper)
            if isinstance(upper, Real) and not isinstance(upper, bool)
            else None
        )
    minimum = (
        min(value for value in lower_values if value is not None)
        if lower_values and all(value is not None for value in lower_values)
        else None
    )
    maximum = (
        max(value for value in upper_values if value is not None)
        if upper_values and all(value is not None for value in upper_values)
        else None
    )
    return (
        minimum,
        maximum,
        any("exclusiveMinimum" in branch for branch in branches),
        any("exclusiveMaximum" in branch for branch in branches),
    )


def apply_tool_input_contract_to_schema(
    tool_name: str,
    schema: MutableMapping[str, Any],
) -> None:
    """Fail discovery if its rendered bounds drift from the shared declaration.

    The Pydantic model remains the shape renderer.  This transform consumes the
    same table as accessors and middleware but intentionally emits no metadata
    or rewritten branches, preserving established discovery payloads exactly.
    """

    contract = TOOL_INPUT_CONTRACTS[tool_name]
    properties = schema.get("properties", {})
    if not isinstance(properties, MutableMapping):
        return
    for field_name, constraint in contract.numeric_fields.items():
        property_schema = properties.get(field_name)
        if not isinstance(property_schema, MutableMapping):
            raise RuntimeError(
                f"{tool_name}.{field_name} is declared in the input-contract "
                "registry but absent from discovery"
            )
        discovered = _discovered_numeric_bounds(property_schema)
        expected = (
            constraint.minimum,
            constraint.maximum,
            constraint.minimum_exclusive,
            constraint.maximum_exclusive,
        )
        if discovered != expected:
            raise RuntimeError(
                f"{tool_name}.{field_name} discovery/registry bound drift: "
                f"discovered={discovered!r}, registry={expected!r}"
            )
    for field_name in contract.strict_boolean_fields:
        property_schema = properties.get(field_name)
        if not isinstance(property_schema, MutableMapping):
            raise RuntimeError(
                f"{tool_name}.{field_name} strict boolean is declared in the "
                "input-contract registry but absent from discovery"
            )


__all__ = [
    "CHLORIDE_MG_L",
    "CHLORIDE_MG_L_PER_MOLAR",
    "DISSOLVED_OXYGEN_MG_L",
    "GALVANIC_PH",
    "GALVANIC_TEMPERATURE_C",
    "HANDBOOK_TEXT_LENGTH",
    "InputContractViolationError",
    "NRL_CHLORIDE_MOLAR",
    "NumericBounds",
    "NumericInputConstraint",
    "PH_AQUEOUS",
    "POURBAIX_GRID_POINTS",
    "POURBAIX_PH",
    "POURBAIX_POTENTIAL_VSHE",
    "POURBAIX_TEMPERATURE_C",
    "PRESSURE_BAR",
    "ScopeGateResult",
    "ScopeIssue",
    "ScopeSeverity",
    "TOOL_INPUT_CONTRACTS",
    "ToolInputContract",
    "REGISTERED_CONTRACT_ACCESSORS",
    "apply_tool_input_contract_to_schema",
    "chloride_mg_l_to_molar",
    "gate_galvanic_scope",
    "gate_pourbaix_scope",
    "enforce_tool_input_contract",
    "require_bounds",
    "require_ordered_range",
    "validate_tool_input_contract",
]
