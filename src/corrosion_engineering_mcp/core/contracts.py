'Input contracts for corrosion assessment: species values and duty states.'
from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import ConfigDict, Field, field_validator, model_validator
from corrosion_engineering_mcp.core.schemas import StrictFiniteBaseModel
SpeciesStatus = Literal['reported', 'converted', 'estimated', 'not_reported', 'below_detection', 'not_applicable', 'not_reported_dose_only']
SpeciesUnits = Literal['mg/L', 'ppmv', 'ppmw', 'mol/L', 'bar', 'mV', 'pH', 'dimensionless']
_KNOWN_UNKNOWN = {'not_reported', 'below_detection', 'not_reported_dose_only'}

class SpeciesValue(StrictFiniteBaseModel):
    """One measured or declared species concentration, with its epistemic status."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    value: Optional[float] = Field(default=None, description='Numeric value in `units`. None whenever status is not reported/converted/estimated. NOT constrained >= 0: ORP in mV is routinely negative in anaerobic service, and a ge=0 bound rejected the single most diagnostic measurement for sulfate-reducing conditions.')
    units: SpeciesUnits = Field(..., description='Explicit, not implied by the field name. Gas-phase H2S is quoted in ppmv and liquid sulfide in mg/L for the same element in the same system; a mg_L-suffixed field forces a silent conversion at the boundary, which is where unit errors enter.')
    status: SpeciesStatus = Field(..., description='Epistemic status. Absent is NOT zero - see the module docstring.')
    source: str = Field(..., min_length=3, max_length=300, description="Where the number came from: 'mADM1 S_Cl', 'lab report 2026-05-12', 'converted from S_IC as C', or the named correlation for an estimate.")
    basis: Optional[str] = Field(default=None, max_length=60, description="Reporting basis where ambiguous: 'as S', 'as N', 'as CaCO3', 'as Cl2'.")
    detection_limit: Optional[float] = Field(default=None, ge=0.0, description='In `units`. Required when status is below_detection.')
    _SIGNED_UNITS = {'mV'}

    @model_validator(mode='after')
    def _value_matches_status(self) -> 'SpeciesValue':
        if self.value is not None and self.value < 0 and (self.units not in self._SIGNED_UNITS):
            raise ValueError(f"NEGATIVE_CONCENTRATION: {self.value} is negative but units are '{self.units}'. Only signed quantities ({', '.join(sorted(self._SIGNED_UNITS))}) may be negative.")
        if self.status in {'reported', 'converted', 'estimated'}:
            if self.value is None:
                raise ValueError(f"SPECIES_VALUE_MISSING: status '{self.status}' requires `value`")
        elif self.value is not None:
            raise ValueError(f"SPECIES_VALUE_UNEXPECTED: status '{self.status}' means there is no value, but value={self.value} was supplied. An unmeasured species must not carry a number - that is how 0.0 becomes 'no risk'.")
        if self.status == 'below_detection' and self.detection_limit is None:
            raise ValueError("DETECTION_LIMIT_REQUIRED: status 'below_detection' requires `detection_limit`; 'below something' is not information without it")
        return self

    @property
    def is_usable(self) -> bool:
        """True when a mechanism can compute from this value."""
        return self.value is not None

    @property
    def blocks_assessment(self) -> bool:
        """True when absence should yield `insufficient_input`, not a verdict.

        `not_applicable` does not block: the species genuinely cannot be present.
        `not_reported_dose_only` DOES block: it means the species is a dosing
        output the stream vocabulary cannot carry and nobody supplied it — which
        is an unknown, not an impossibility.
        `not_reported` and `below_detection` do — for `below_detection` because a
        species at its detection limit can still matter (sulfide at 0.05 mg/L is
        below many methods; the former 0.1 mg/L passivity cutoff is now only an
        internal descriptive grade boundary and never a verdict).
        """
        return self.status in _KNOWN_UNKNOWN
DutyStateKind = Literal['normal', 'cip', 'transient_injection', 'upset', 'shutdown_stagnant', 'hydrotest', 'startup']
Wetness = Literal['dry', 'condensing', 'immersed', 'intermittent', 'unknown']
SpatialScope = Literal['bulk', 'injection_zone', 'wetted_surface', 'vapor_space', 'external']
SCOPE_VISIBLE_TO = {'injection_zone': {'quill', 'injection_nozzle', 'static_mixer', 'spool_first_10D'}, 'vapor_space': {'tank_roof', 'headspace', 'crown', 'vent', 'gas_pipe'}, 'external': {'pipe_external', 'insulated_pipe_external', 'tank_external', 'structure'}}
HOURS_PER_YEAR = 8760.0

class DutyState(StrictFiniteBaseModel):
    """One operating condition a material must survive.

    A material is assessed against the WORST applicable state per mechanism, not
    against an average. See the module docstring for why.
    """
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    state_id: str = Field(..., min_length=1, max_length=60)
    kind: DutyStateKind
    spatial_scope: SpatialScope = 'bulk'
    wetness: Wetness = Field(default='unknown', description="Explicit wetness state. 'unknown' blocks mechanisms that require it.")
    location: Optional[str] = Field(default=None, max_length=80, description="e.g. 'crown', 'splash_zone', 'submerged', 'under_insulation'.")
    temperature_C: float = Field(..., ge=-50.0, le=300.0)
    pH: Optional[float] = Field(default=None, ge=0.0, le=14.0)
    species: dict[str, SpeciesValue] = Field(default_factory=dict)
    duration_h_per_event: Optional[float] = Field(default=None, gt=0.0, le=HOURS_PER_YEAR)
    events_per_year: Optional[float] = Field(default=None, gt=0.0)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator('state_id')
    @classmethod
    def _no_whitespace(cls, v: str) -> str:
        if any((c.isspace() for c in v)):
            raise ValueError('STATE_ID_INVALID: state_id must not contain whitespace')
        return v

    @property
    def fraction_of_time(self) -> Optional[float]:
        """DERIVED, never an input.

        Available only for cumulative-damage channels — uniform mass loss,
        elastomer and thermoplastic ageing. Threshold mechanisms (pitting, crevice
        initiation, SCC) MUST NOT read this: weighting a 6-hour CIP by 0.0014
        discounts to invisibility the state that actually selects the material.
        """
        if self.duration_h_per_event is None or self.events_per_year is None:
            return None
        return min(1.0, self.duration_h_per_event * self.events_per_year / HOURS_PER_YEAR)

    def is_visible_to(self, component_type: str) -> bool:
        """Whether this state's scope reaches the given component."""
        allowed = SCOPE_VISIBLE_TO.get(self.spatial_scope)
        if allowed is None:
            return True
        return component_type in allowed

    def species_gaps(self, required: List[str]) -> List[str]:
        """Required species that are absent or unknown, and therefore block a verdict.

        A missing key blocks: silence about chloride is not a measured zero.
        """
        gaps = []
        for name in required:
            sv = self.species.get(name)
            if sv is None or sv.blocks_assessment:
                gaps.append(name)
        return gaps
