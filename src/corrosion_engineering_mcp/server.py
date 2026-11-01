"""Corrosion Engineering FastMCP server.

The registry contains 23 tools. Three remain registered as non-computing safety
endpoints (the renamed CO2/H2S endpoint and the withdrawn aerated-chloride and
galvanic models), and the three handbook tools require an external corpus.
Individual tool descriptions state those limits for MCP discovery clients.
"""
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.transforms import Transform
from mcp.types import ToolAnnotations
import anyio
from copy import deepcopy
from collections.abc import Sequence
from functools import wraps
import inspect
import json
import logging
import math
import sys
from typing import Annotated, Any, List, Literal, Optional
from corrosion_engineering_mcp.tools.handbook.material_screening import material_screening_query
from corrosion_engineering_mcp.tools.handbook.typical_rates import typical_rates_query
from corrosion_engineering_mcp.tools.handbook.mechanism_guidance import mechanism_guidance_query
from corrosion_engineering_mcp.tools.chemistry.langelier_index import calculate_langelier_index
from corrosion_engineering_mcp.tools.chemistry.predict_scaling import predict_scaling_tendency
from corrosion_engineering_mcp.tools.chemistry.calculate_pourbaix import calculate_pourbaix
from corrosion_engineering_mcp.tools.galvanic.engine import assess as assess_galvanic
from corrosion_engineering_mcp.tools.mechanistic.co2_h2s_corrosion import predict_co2_h2s_corrosion
from corrosion_engineering_mcp.tools.mechanistic.aerated_chloride_corrosion import predict_aerated_chloride_corrosion
from corrosion_engineering_mcp.tools.mechanistic.sour_service import screen_sour_service
from corrosion_engineering_mcp.tools.mechanistic.sulfide_exposure import screen_sulfide_exposure
from corrosion_engineering_mcp.tools.mechanistic.mic_screen import screen_mic
from corrosion_engineering_mcp.tools.mechanistic.concrete_exposure import screen_concrete_exposure
from corrosion_engineering_mcp.tools.mechanistic.material_suitability import screen_material_suitability
from corrosion_engineering_mcp.tools.galvanic.screening import screen_galvanic_couple
from corrosion_engineering_mcp.tools.mechanistic.oxidant_exposure import assess_oxidant_exposure
from corrosion_engineering_mcp.tools.mechanistic.localized_corrosion import calculate_localized_corrosion, calculate_pren
from corrosion_engineering_mcp.core.schemas import AssessGalvanicInput, GeneratePourbaixInput, ResolveMaterialIdentityInput, GetPhreeqcStatusInput, GetServerInfoInput, IdentifyMechanismInput, MaterialCompatibility, TypicalRateResult, MechanismGuidance, PourbaixDiagramResult, PhreeqcDatabaseStatus, PhreeqcStatusResult, MaterialIdentityResult, ProvenanceMetadata, QueryTypicalRatesInput, ScreenMaterialsInput, ServerInfoResult, ConfidenceLevel, SUPPORTED_GALVANIC_MATERIALS, SUPPORTED_POURBAIX_ELEMENTS
from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel
from corrosion_engineering_mcp.core.materials import advertised_materials, get_material as get_catalog_material
from corrosion_engineering_mcp.core.phreeqc_runtime import get_phreeqc_engine_status
from corrosion_engineering_mcp.core.tool_runtime import build_tool_input
from corrosion_engineering_mcp.utils.sulfide_chemistry import H2S_GAS_COMPOSITION_MAX_PPMV
from corrosion_engineering_mcp.core.errors import CorrosionPublicError, MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, NonFiniteArithmeticError, NonFiniteErrorDetailError, annotate_float_domain_schema, find_non_finite_paths, public_error, redact_paths, require_representable_nonzero_result, validate_float_domain_inputs
from pydantic import Field, ConfigDict, ValidationError
from corrosion_engineering_mcp.core.validation import CHLORIDE_MG_L, DISSOLVED_OXYGEN_MG_L, GALVANIC_PH, GALVANIC_TEMPERATURE_C, HANDBOOK_TEXT_LENGTH, POURBAIX_GRID_POINTS, POURBAIX_PH, InputContractViolationError, apply_tool_input_contract_to_schema, validate_tool_input_contract
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
ERROR_ENVELOPE_KEYS = {'code', 'message', 'details', 'correlation_id'}

def _is_error_envelope(value: Any) -> bool:
    return isinstance(value, dict) and set(value.keys()) == ERROR_ENVELOPE_KEYS

class CorrosionMCPToolError(ToolError):
    """Protocol-visible tool error retaining the machine-readable domain payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        non_finite_paths = find_non_finite_paths(payload)
        if non_finite_paths:
            unsafe_code = str(payload.get('code', 'UNKNOWN_ERROR'))
            payload = public_error(NonFiniteErrorDetailError(unsafe_code, non_finite_paths), correlation_id=payload.get('correlation_id'))
        self.payload = payload
        super().__init__(json.dumps(payload, separators=(',', ':'), sort_keys=True, allow_nan=False))

class FacadeValidationError(CorrosionPublicError, ValueError):
    """Stable public representation of pre-wrapper argument validation."""

    def __init__(self, tool_name: str, exc: BaseException) -> None:
        violations: list[dict[str, Any]] = []
        errors = getattr(exc, 'errors', None)
        if callable(errors):
            for item in errors(include_url=False, include_context=False, include_input=False):
                violations.append({'field': '.'.join((str(part) for part in item.get('loc', ()))), 'type': str(item.get('type', 'validation_error'))})
        elif isinstance(exc, InputContractViolationError):
            violations.append({'field': str(exc.details.get('field', '')), 'type': exc.code})
        super().__init__('VALIDATION_ERROR', 'VALIDATION_ERROR: tool arguments did not satisfy the registered input contract; no tool result was computed.', details={'reason_code': 'VALIDATION_ERROR', 'tool': tool_name, 'violations': violations, 'result_emitted': False})

class PublicValidationErrorMiddleware(Middleware):
    """Shape contract and Pydantic failures before FastMCP serializes them."""

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        tool_name = context.message.name
        arguments = context.message.arguments or {}
        try:
            validate_tool_input_contract(tool_name, arguments)
            return await call_next(context)
        except CorrosionMCPToolError:
            raise
        except FastMCPValidationError as exc:
            if not isinstance(exc.__cause__, ValidationError):
                raise
            shaped = FacadeValidationError(tool_name, exc.__cause__)
            raise CorrosionMCPToolError(public_error(shaped)) from exc
        except (InputContractViolationError, ValidationError) as exc:
            shaped = FacadeValidationError(tool_name, exc)
            raise CorrosionMCPToolError(public_error(shaped)) from exc

def public_tool_errors(func):
    """Raise one public, protocol-visible error for every tool failure."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            bound = inspect.signature(func).bind_partial(*args, **kwargs)
            validate_float_domain_inputs(bound.arguments, minimum_nonzero_magnitude=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE)
            result = await func(*args, **kwargs)
            non_finite_paths = find_non_finite_paths(result)
            if non_finite_paths:
                raise NonFiniteArithmeticError('tool_result_payload_emission', non_finite_paths=non_finite_paths)
            return result
        except CorrosionMCPToolError:
            raise
        except Exception as exc:
            if len(exc.args) == 1 and _is_error_envelope(exc.args[0]):
                payload = exc.args[0]
            else:
                payload = public_error(exc)
            raise CorrosionMCPToolError(payload) from exc
    wrapper.__signature__ = inspect.signature(func).replace(return_annotation=Any)
    return wrapper

class CalculateLangelierInput(StrictFiniteBaseModel):
    """Input for Langelier Saturation Index calculation."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    ions_json: str = Field(..., description='JSON ion concentrations in mg/L. Must include Ca2+ and either HCO3- or CO3-2; every value must be finite and non-negative. Nonzero ion magnitudes must be at least 1e-12 mg/L under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1.', min_length=10)
    temperature_C: float = Field(default=25.0, description='Water temperature in Celsius', ge=0.0, le=100.0)
    pH: float = Field(..., description='Reported pH; required because this calculation does not solve pH from charge balance.', ge=0.0, le=14.0)

class PredictScalingInput(StrictFiniteBaseModel):
    """Input for scaling tendency prediction."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    ions_json: str = Field(..., description='JSON ion concentrations in mg/L. Must include Ca2+ and either HCO3- or CO3-2; every value must be finite and non-negative. Nonzero ion magnitudes must be at least 1e-12 mg/L under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1.', min_length=10)
    temperature_C: float = Field(default=25.0, description='Water temperature in Celsius', ge=0.0, le=100.0)
    pH: float = Field(..., description='Reported pH; required because this calculation does not solve pH from charge balance.', ge=0.0, le=14.0)

class PredictCO2H2SInput(StrictFiniteBaseModel):
    """Input contract for the NORSOK M-506 CO2-only prediction."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    temperature_C: float = Field(..., description='Temperature in Celsius (NORSOK M-506 Table 3: 20-150°C)', ge=20.0, le=150.0)
    pressure_bar: float = Field(..., description='Total pressure in bar absolute', gt=0.0)
    co2_fraction: Optional[float] = Field(default=None, description='CO2 mole fraction in gas phase (0-1). REQUIRED — absent is not zero.', ge=0.0, le=1.0)
    h2s_fraction: Optional[float] = Field(default=None, description='H2S mole fraction in gas phase (0-1). REQUIRED — absent is not zero.', ge=0.0, le=1.0)
    pH: Optional[float] = Field(default=None, description='Water pH (3.5-6.5). If None, calculated from chemistry.', ge=3.5, le=6.5)
    bicarbonate_mg_L: Optional[float] = Field(default=None, description='Bicarbonate concentration in mg/L. Required when pH is not supplied; absent is not zero. If pH is supplied, this value is retained but labelled ignored_reported_pH_used.', ge=0.0)
    ionic_strength_mg_L: Optional[float] = Field(default=None, description='NaCl-equivalent salinity in mg/L (0-175000). In calculated-pH mode, an omitted value receives the disclosed NORSOK M-506 Table 6 default of 50000 mg/L. With reported pH no default is created, and a supplied value is retained as ignored_reported_pH_used.', ge=0.0, le=175000.0)
    wall_shear_stress_Pa: Optional[float] = Field(default=None, description='Reported wall shear stress in Pa (NORSOK M-506 Table 3: 1-150). Provide this or every hydraulic field below, never both. Values outside Table 3 are returned as NORSOK_OUT_OF_ENVELOPE.')
    superficial_gas_velocity_m_s: Optional[float] = Field(default=None, description='Reported superficial gas velocity in m/s', ge=0.0)
    superficial_liquid_velocity_m_s: Optional[float] = Field(default=None, description='Reported superficial liquid velocity in m/s', ge=0.0)
    gas_mass_flow_kg_hr: Optional[float] = Field(default=None, description='Reported gas mass flow in kg/h', gt=0.0)
    liquid_mass_flow_kg_hr: Optional[float] = Field(default=None, description='Reported liquid mass flow in kg/h', gt=0.0)
    gas_volumetric_flow_m3_hr: Optional[float] = Field(default=None, description='Reported gas volumetric flow in m3/h', gt=0.0)
    liquid_volumetric_flow_m3_hr: Optional[float] = Field(default=None, description='Reported liquid volumetric flow in m3/h', gt=0.0)
    liquid_holdup_percent: Optional[float] = Field(default=None, description='Reported liquid holdup in percent', ge=0.0, le=100.0)
    gas_viscosity_cp: Optional[float] = Field(default=None, description='Reported gas dynamic viscosity in cP', gt=0.0)
    liquid_viscosity_cp: Optional[float] = Field(default=None, description='Reported liquid dynamic viscosity in cP', gt=0.0)
    pipe_roughness_m: Optional[float] = Field(default=None, description='Reported absolute pipe roughness in metres', ge=0.0)
    pipe_diameter_m: Optional[float] = Field(default=None, description='Reported pipe internal diameter in metres', gt=0.0)

class PredictAeratedChlorideInput(StrictFiniteBaseModel):
    """Validation contract retained for the withdrawn fail-closed endpoint."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    temperature_C: float = Field(..., description='Temperature in Celsius (0-80°C)', ge=0.0, le=80.0)
    chloride_mg_L: float = Field(..., description='Chloride concentration in mg/L', ge=0.0)
    pH: float = Field(default=7.0, description='Solution pH (6.0-9.0 validated range)', ge=4.0, le=10.0)
    dissolved_oxygen_mg_L: Optional[float] = Field(default=None, description='Dissolved oxygen in mg/L; the withdrawn endpoint does not calculate with it.', **DISSOLVED_OXYGEN_MG_L.field_kwargs())
    material: str = Field(default='carbon_steel', description="Material type: 'carbon_steel' or 'low_alloy'")

class AssessLocalizedInput(StrictFiniteBaseModel):
    """Input for localized (pitting/crevice) corrosion assessment."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    material: str = Field(..., description="Material name (e.g., '316L', '2205', '254SMO', 'SS316', 'HY80')", min_length=1)
    temperature_C: float = Field(..., description='Operating temperature in Celsius', ge=0.0, le=150.0)
    Cl_mg_L: float = Field(..., description='Chloride concentration in mg/L', ge=0.0)
    pH: float = Field(default=7.0, description='Solution pH', ge=0.0, le=14.0)
    crevice_gap_mm: float = Field(default=0.1, description='Crevice gap width in mm; minimum is the float-domain bound INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1', ge=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, le=10.0)
    dissolved_oxygen_mg_L: Optional[float] = Field(default=None, description='Dissolved oxygen in mg/L. Enables Tier 2 electrochemical assessment for NRL materials.', **DISSOLVED_OXYGEN_MG_L.field_kwargs())

class CalculatePRENInput(StrictFiniteBaseModel):
    """Input for PREN (Pitting Resistance Equivalent Number) calculation."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    Cr_wt_pct: float = Field(..., description='Chromium content in weight percent', ge=0.0, le=30.0)
    Mo_wt_pct: float = Field(..., description='Molybdenum content in weight percent', ge=0.0, le=10.0)
    N_wt_pct: float = Field(..., description='Nitrogen content in weight percent', ge=0.0, le=1.0)
    Mn_wt_pct: Optional[float] = Field(default=None, description='Manganese content in weight percent. Required for the explicit Liu austenite-phase equation; unused by the ASTM default.', ge=0.0, le=20.0)
    grade_type: str = Field(default='austenitic', description="Grade type: 'austenitic', 'duplex', or 'superaustenitic'")
    composition_basis: Literal['bulk_alloy', 'austenite_phase'] = Field(default='bulk_alloy', description='Composition sampling basis. The Liu phase-specific variant requires austenite_phase; the ASTM default accepts bulk_alloy.')
    formula_variant: Literal['ASTM_A511_PREN16', 'LIU_2020_DUPLEX_PREN30'] = Field(default='ASTM_A511_PREN16', description="Named PREN equation. ASTM_A511_PREN16 is the default for every grade; LIU_2020_DUPLEX_PREN30 must be selected explicitly and is valid only with grade_type='duplex'.")

class EstimateServiceLifeInput(StrictFiniteBaseModel):
    """Input for service life estimation."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    thickness_mm: float = Field(..., description='Current wall thickness in mm', gt=0.0)
    corrosion_rate_mm_y: float = Field(..., description='Corrosion rate in mm/year', ge=0.0)
    design_allowance_mm: float = Field(default=0.0, description='Original design corrosion allowance in mm, retained for context only; it is not subtracted from a current measured wall', ge=0.0)
    minimum_thickness_mm: float = Field(..., description='Minimum required/retirement wall thickness in mm', gt=0.0)
    safety_factor: float = Field(default=1.0, description='Safety factor to apply to corrosion rate', ge=1.0, le=3.0)

class NonFiniteServiceLifeArithmeticError(CorrosionPublicError, ArithmeticError):
    """A finite service-life input set overflowed or underflowed to non-finite."""

    def __init__(self, *, operation: str, operands: dict[str, float], computed_value: float) -> None:
        if math.isnan(computed_value):
            nonfinite_kind = 'nan'
        elif computed_value > 0.0:
            nonfinite_kind = 'positive_infinity'
        else:
            nonfinite_kind = 'negative_infinity'
        super().__init__('NON_FINITE_ARITHMETIC', 'NON_FINITE_ARITHMETIC: service-life arithmetic produced a non-finite intermediate or result from finite inputs; no service life or finite-calculation label is returned.', details={'reason_code': 'NON_FINITE_ARITHMETIC', 'calculation_status': 'out_of_domain_non_finite_arithmetic', 'operation': operation, 'operands': operands, 'nonfinite_kind': nonfinite_kind, 'result_emitted': False})

class FloatDomainDiscoveryTransform(Transform):
    """Declare shared field bounds and the server-wide numeric input floor."""

    async def list_tools(self, tools: Sequence[Any]) -> Sequence[Any]:
        transformed = []
        for tool in tools:
            parameters = deepcopy(tool.parameters)
            apply_tool_input_contract_to_schema(tool.name, parameters)
            annotate_float_domain_schema(parameters)
            transformed.append(tool.model_copy(update={'parameters': parameters}))
        return transformed
mcp = FastMCP('Corrosion Engineering')
mcp.add_middleware(PublicValidationErrorMiddleware())
mcp.add_transform(FloatDomainDiscoveryTransform())

def engineering_tool(**options):
    """Keep full reviewed engineering qualifications in MCP discovery.

    FastMCP 4 parses docstrings and omits sections such as Examples/Returns.
    An explicit description preserves the existing client contract on both
    supported framework generations while Field descriptions retain precedence.
    """

    def register(func):
        return mcp.tool(description=inspect.getdoc(func), **options)(func)
    return register

@engineering_tool(name='corrosion_screen_materials', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
@public_tool_errors
async def screen_materials(environment: Annotated[str, Field(min_length=HANDBOOK_TEXT_LENGTH.minimum, max_length=HANDBOOK_TEXT_LENGTH.maximum, description='Environment description')], candidates: Annotated[List[str], Field(min_length=1, max_length=20, description='Material identifiers to screen')], application: Annotated[Optional[str], Field(max_length=200, description='Application context')]=None) -> MaterialCompatibility:
    """
    Screen materials for compatibility with specified environment.

    CORPUS REQUIREMENT: unless CORROSION_CORPUS_PATH points to an available
    handbook corpus, every call fails closed with typed CORPUS_UNAVAILABLE. No
    fabricated or built-in fallback data are used. When configured, the tool
    searches that corpus for material-environment evidence.

    Args:
        params (ScreenMaterialsInput): Validated input parameters containing:
            - environment (str): Environment description (e.g., "seawater, 35 g/L Cl, 25°C")
            - candidates (List[str]): Material identifiers (e.g., ["CS", "316L", "duplex"])
            - application (Optional[str]): Application context (e.g., "heat_exchanger_tubes")

    Returns:
        MaterialCompatibility with schema:
        {
            "material": str,           # Best candidate material
            "compatibility": str,      # "acceptable", "marginal", "not_recommended"
            "typical_rate_range": Tuple[float, float],  # (min, max) mm/year
            "sources": List[str],      # Handbook references
            "provenance": ProvenanceMetadata
        }

    Example:
        result = await screen_materials(ScreenMaterialsInput(
            environment="CO2-rich brine, 60°C, pCO2=0.5 bar",
            candidates=["CS", "316L"],
            application="piping"
        ))
        print(result.compatibility)  # "acceptable", "marginal", "not_recommended"
    """
    params = build_tool_input(ScreenMaterialsInput, environment=environment, candidates=candidates, application=application)
    logger.info(f'Screening materials {params.candidates} for environment: {params.environment}')
    result = await anyio.to_thread.run_sync(lambda: material_screening_query(environment=params.environment, candidates=params.candidates, application=params.application, mcp_search_function=None))
    logger.info(f'Screening complete: {result.material} → {result.compatibility}')
    return result

@engineering_tool(name='corrosion_query_typical_rates', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
@public_tool_errors
async def query_typical_rates(material: Annotated[str, Field(min_length=1, max_length=50, description='Material identifier')], environment_summary: Annotated[str, Field(min_length=HANDBOOK_TEXT_LENGTH.minimum, max_length=HANDBOOK_TEXT_LENGTH.maximum, description='Brief environment description')]) -> TypicalRateResult:
    """
    Query handbook for typical corrosion rates.

    CORPUS REQUIREMENT: unless CORROSION_CORPUS_PATH points to an available
    handbook corpus, every call fails closed with typed CORPUS_UNAVAILABLE. No
    placeholder rate or fabricated fallback is returned. When configured, the
    tool searches that corpus for empirical rate data.

    Args:
        params (QueryTypicalRatesInput): Validated input parameters containing:
            - material (str): Material identifier (e.g., "CS", "316L", "duplex")
            - environment_summary (str): Brief description (e.g., "seawater, 25°C, stagnant")

    Returns:
        TypicalRateResult with schema:
        {
            "material": str,
            "rate_min_mm_per_y": float,
            "rate_max_mm_per_y": float,
            "rate_typical_mm_per_y": float,
            "sources": List[str],
            "provenance": ProvenanceMetadata
        }

    Example:
        result = await query_typical_rates(QueryTypicalRatesInput(
            material="CS",
            environment_summary="CO2-rich brine, 60°C, pH 6.8"
        ))
        print(f"Typical: {result.rate_typical_mm_per_y:.3f} mm/y")
    """
    params = build_tool_input(QueryTypicalRatesInput, material=material, environment_summary=environment_summary)
    logger.info(f'Querying typical rates for {params.material} in {params.environment_summary}')
    result = await anyio.to_thread.run_sync(lambda: typical_rates_query(material=params.material, environment_summary=params.environment_summary, mcp_search_function=None))
    logger.info(f'Typical rate: {result.rate_typical_mm_per_y:.3f} mm/y')
    return result

@engineering_tool(name='corrosion_identify_mechanism', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
@public_tool_errors
async def identify_mechanism(material: Annotated[str, Field(min_length=1, max_length=50, description='Material identifier')], symptoms: Annotated[List[str], Field(min_length=1, max_length=20, description='Observed symptoms')], environment: Annotated[Optional[str], Field(max_length=HANDBOOK_TEXT_LENGTH.maximum)]=None) -> MechanismGuidance:
    """
    Identify probable corrosion mechanisms and get mitigation guidance.

    CORPUS REQUIREMENT: unless CORROSION_CORPUS_PATH points to an available
    handbook corpus, every call fails closed with typed CORPUS_UNAVAILABLE. No
    fabricated mechanism fallback is used. When configured, the tool searches
    that corpus to match symptoms to mechanisms and mitigation evidence.

    Args:
        params (IdentifyMechanismInput): Validated input parameters containing:
            - material (str): Material identifier (e.g., "304_SS", "CS")
            - symptoms (List[str]): Observed symptoms (e.g., ["localized_attack", "pits"])
            - environment (Optional[str]): Environment description for context

    Returns:
        MechanismGuidance with schema:
        {
            "material": str,
            "probable_mechanisms": List[str],  # ["pitting", "crevice", ...]
            "recommendations": List[str],       # Mitigation guidance
            "tests_recommended": List[str],     # Diagnostic tests
            "provenance": ProvenanceMetadata
        }

    Example:
        result = await identify_mechanism(IdentifyMechanismInput(
            material="304_SS",
            symptoms=["localized_attack", "pits"],
            environment="cooling water, 500 mg/L Cl"
        ))
        print(result.probable_mechanisms)  # ["pitting", "crevice"]
    """
    params = build_tool_input(IdentifyMechanismInput, material=material, symptoms=symptoms, environment=environment)
    logger.info(f'Identifying mechanism for {params.material} with symptoms: {params.symptoms}')
    result = await anyio.to_thread.run_sync(lambda: mechanism_guidance_query(material=params.material, symptoms=params.symptoms, environment=params.environment, mcp_search_function=None))
    logger.info(f'Probable mechanisms: {result.probable_mechanisms}')
    return result

@engineering_tool(name='corrosion_assess_galvanic', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def assess_galvanic_corrosion(anode_material: Annotated[str, Field(description=f'Less noble metal. Options: {', '.join(SUPPORTED_GALVANIC_MATERIALS)}')], cathode_material: Annotated[str, Field(description=f'More noble metal. Options: {', '.join(SUPPORTED_GALVANIC_MATERIALS)}')], temperature_C: Annotated[float, Field(ge=GALVANIC_TEMPERATURE_C.minimum, le=GALVANIC_TEMPERATURE_C.maximum, description='Temperature in Celsius')], pH: Annotated[float, Field(ge=GALVANIC_PH.minimum, le=GALVANIC_PH.maximum, description='Solution pH')], chloride_mg_L: Annotated[float, Field(ge=CHLORIDE_MG_L.minimum, le=CHLORIDE_MG_L.maximum, description='Chloride concentration in mg/L')], area_ratio_cathode_to_anode: Annotated[float, Field(gt=0.0, description='Cathode area / anode area')]=1.0, velocity_m_s: Annotated[float, Field(ge=0.0, description='Liquid velocity in m/s')]=0.0, dissolved_oxygen_mg_L: Annotated[Optional[float], Field(ge=DISSOLVED_OXYGEN_MG_L.minimum, le=DISSOLVED_OXYGEN_MG_L.maximum, description='Dissolved oxygen in mg/L')]=None, sulfide_mg_L: Annotated[float, Field(ge=0.0, description='Dissolved sulfide/H2S indicator in mg/L')]=0.0, allow_auto_orient: Annotated[bool, Field(description='Swap reversed anode/cathode inputs based on seawater galvanic series')]=False) -> Any:
    """WITHDRAWN: accept no galvanic rate, current, polarity, or verdict.

    Valid inputs fail with ``TOOL_WITHDRAWN`` because the former model reversed
    the cathode/anode area-ratio response and did not preserve electrode polarity
    across its stated pH range. Its vendored NRL comparison curves are placeholders,
    not a validation dataset. ``corrosion_screen_galvanic`` remains available for
    ASTM G82 open-circuit ranking without a kinetic result.
    """
    params = build_tool_input(AssessGalvanicInput, anode_material=anode_material, cathode_material=cathode_material, temperature_C=temperature_C, pH=pH, chloride_mg_L=chloride_mg_L, area_ratio_cathode_to_anode=area_ratio_cathode_to_anode, velocity_m_s=velocity_m_s, dissolved_oxygen_mg_L=dissolved_oxygen_mg_L, sulfide_mg_L=sulfide_mg_L, allow_auto_orient=allow_auto_orient)
    return await anyio.to_thread.run_sync(lambda: assess_galvanic(params))

@engineering_tool(name='corrosion_generate_pourbaix', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def generate_pourbaix_diagram(element: Annotated[str, Field(description=f'Chemical element. Options: {', '.join(SUPPORTED_POURBAIX_ELEMENTS)}')], pH_range_min: Annotated[float, Field(ge=POURBAIX_PH.minimum, le=POURBAIX_PH.maximum, description='Minimum pH for diagram')]=POURBAIX_PH.minimum, pH_range_max: Annotated[float, Field(ge=POURBAIX_PH.minimum, le=POURBAIX_PH.maximum, description='Maximum pH for diagram')]=POURBAIX_PH.maximum, grid_points: Annotated[int, Field(ge=POURBAIX_GRID_POINTS.minimum, le=POURBAIX_GRID_POINTS.maximum, description='Number of grid points in each direction')]=50) -> PourbaixDiagramResult:
    """
    Generate sourced 25 C water-stability lines for a Pourbaix plot.

    **Phase 2 Tool - Water-Stability Lines Only**

    Returns H2/H+ and O2/H2O lines plus a suppression ledger for every local
    reaction row. The table's E0 values lack row-level source records, so no
    element-reaction E0 or derived boundary is emitted. It also emits no
    equilibrium phase, immunity, corrosion, or passivity region. Those verdicts
    require Gibbs-energy phase selection over a complete thermodynamic dataset.
    The returned ``method_sources`` records identify the Persson and Patel
    methods, the sections read, the exact content relied on, and their limits;
    this implementation performs neither method.

    The tool remains registered because the 25 C water-stability lines are
    independently defined and useful, while the ledger makes the withheld
    element rows explicit. Temperature, dissolved-metal activity and display-E
    range inputs were removed because they no longer affect the retained output.

    Supported elements: Fe, Cr, Ni, Cu, Ti, Al, Zn, Mg, Pb, Sn, Mo, W, Co, Mn

    Args:
        element: Chemical element.
        pH_range_min/max: pH range for the water lines.
        grid_points: Number of pH samples per water line.

    Returns:
        PourbaixDiagramResult with schema:
        {
            "element": str,
            "temperature_C": float,
            "boundaries": [],  # all current reaction E0 rows are unsourced
            "suppressed_reaction_rows": List[Dict],
            "water_lines": Dict[str, List[Tuple[float, float]]],  # H2/O2 lines
            "pH_range": Tuple[float, float],
            "provenance": ProvenanceMetadata
        }

    Example:
        diagram = await generate_pourbaix_diagram(
            element="Fe",
            pH_range_min=6.0,
            pH_range_max=8.0
        )
        # Inspect water lines and the withheld reaction-row ledger.
    """
    params = build_tool_input(GeneratePourbaixInput, element=element, pH_range_min=pH_range_min, pH_range_max=pH_range_max, grid_points=grid_points)
    logger.info(f'Generating 25 C Pourbaix water lines for {params.element}, pH {params.pH_range_min}-{params.pH_range_max}')
    raw_result = await anyio.to_thread.run_sync(lambda: calculate_pourbaix(element=params.element, pH_range=(params.pH_range_min, params.pH_range_max), grid_points=params.grid_points))
    logger.info(f'Pourbaix water-line set generated; {len(raw_result.get('suppressed_reaction_rows', []))} reaction rows suppressed')
    point_sets = list(raw_result['water_lines'].values()) + [boundary['points'] for boundary in raw_result['boundaries'] if isinstance(boundary.get('points'), list)]
    counts = {len(points) for points in point_sets}
    endpoints_complete = all((points and points[0][0] == params.pH_range_min and (points[-1][0] == params.pH_range_max) for points in point_sets))
    if counts != {params.grid_points} or not endpoints_complete:
        raise CorrosionPublicError('POURBAIX_GRID_CONTRACT_VIOLATION', 'POURBAIX_GRID_CONTRACT_VIOLATION: computed lines do not match the requested inclusive pH grid; no partial diagram is returned.', details={'requested_grid_points': params.grid_points, 'actual_point_counts': sorted(counts), 'requested_pH_range': [params.pH_range_min, params.pH_range_max], 'endpoints_complete': endpoints_complete, 'result_emitted': False})
    actual_grid_points = next(iter(counts))
    logger.info('Returning %s inclusive pH samples per Pourbaix water/boundary line', actual_grid_points)
    result = PourbaixDiagramResult(element=params.element, temperature_C=25.0, boundaries=raw_result['boundaries'], water_lines=raw_result['water_lines'], pH_range=(params.pH_range_min, params.pH_range_max), grid_points=actual_grid_points, phase_selection_status=raw_result['phase_selection_status'], suppressed_reaction_rows=raw_result['suppressed_reaction_rows'], suppressed_nonredox_reaction_ids=raw_result['suppressed_nonredox_reaction_ids'], method_sources=raw_result['method_sources'], water_line_basis=raw_result['water_line_basis'], provenance=ProvenanceMetadata(model='Water_Line_Only_Pourbaix_Nernst', version='0.5.0', validation_dataset=None, confidence=ConfidenceLevel.LOW, sources=['Persson and Patel method citations: see structured method_sources', 'Bard, A.J., et al. (1985). Standard Potentials in Aqueous Solution (IUPAC).'], assumptions=['Water stability lines only, fixed at 25 C', 'Every current reaction E0 row is suppressed for absent row-level sourcing', 'No Gibbs-energy minimization and no phase/passivity selection', 'Unit gas fugacities and water activity; ideal proton activity'], warnings=[*raw_result.get('warnings', []), 'REACTION E0 ROWS AND DERIVED BOUNDARIES SUPPRESSED', 'Chloride, sulfide, carbonate, and alloy-specific passivation are not fully modeled', 'No corrosion or material-suitability verdict is emitted']))
    return result

@engineering_tool(name='corrosion_resolve_material_identity', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def resolve_material_identity(material: Annotated[str, Field(min_length=1, max_length=80, description='Material identifier or alias')]) -> MaterialIdentityResult:
    """Resolve a material identifier or alias to its local catalog identity.

    This is an identity/alias resolver, not a material-property database. It
    returns the canonical identifier, UNS designation and internal alloy class.
    It returns no composition, density, potential, PREN, kinetics, passivation,
    validity range, or service/material-suitability guidance.

    Args:
        material: Material identifier or alias ("316L", "Ti", "I625", "CuNi", etc.)

    Returns:
        MaterialIdentityResult containing identity fields only

    Example:
        identity = await resolve_material_identity(material="SS316")
        print(identity.material, identity.uns_number)
    """
    params = build_tool_input(ResolveMaterialIdentityInput, material=material)
    logger.info(f'Resolving material identity for {params.material}')
    record = get_catalog_material(params.material)
    result = MaterialIdentityResult(material=record.canonical_id, uns_number=record.uns, material_classification=record.classification, identity_source='data/materials.yaml', properties_returned=False, provenance=ProvenanceMetadata(model='YAML_Materials_Catalog', version='0.5.0', validation_dataset=None, confidence=ConfidenceLevel.LOW, sources=['data/materials.yaml'], assumptions=['Catalog spellings and aliases are used for identity resolution only.'], warnings=['This resolver returns no material properties or service assessment']))
    logger.info(f'Material catalog identity resolved: {record.canonical_id} ({record.uns or 'no UNS'})')
    return result

@engineering_tool(name='corrosion_langelier_index', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def langelier_index(ions_json: Annotated[str, Field(min_length=10, description='JSON ion concentrations in mg/L. Must include Ca2+ and either HCO3- or CO3-2; values must be finite and non-negative. Nonzero ion magnitudes must be at least 1e-12 mg/L under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1.')], pH: Annotated[float, Field(ge=0.0, le=14.0, description='Reported pH; required for the calculation.')], temperature_C: Annotated[float, Field(ge=0.0, le=100.0, description='Water temperature in Celsius')]=25.0) -> dict:
    """
    Calculate Langelier Saturation Index (LSI) for calcium carbonate scaling.

    LSI is a CaCO₃ saturation index, not a metal-corrosion index:
    - LSI > 0: supersaturated with respect to CaCO₃
    - LSI = 0: saturated with respect to CaCO₃
    - LSI < 0: undersaturated with respect to CaCO₃

    This tool emits no metal-corrosion verdict and no chemical-dosing
    recommendation. Charge-balance validation status is returned in the payload.

    Args:
        ions_json: JSON ion concentrations in mg/L, including Ca2+ and either
            HCO3- or CO3-2. Values must be finite and non-negative.
        temperature_C: Water temperature in Celsius.
        pH: Caller-reported pH; required and never inferred from charge balance.

    Returns:
        Dictionary with LSI value, CaCO3 saturation state, and validation status.

    Example:
        result = await langelier_index(
            ions_json=(
                '{"Ca2+": 80, "Mg2+": 20, "HCO3-": 180, "Cl-": 100, '
                '"SO4-2": 60, "Na+": 31.8}'
            ),
            temperature_C=25.0,
            pH=7.5
        )
        print(result["lsi"], result["saturation_state"])
    """
    params = build_tool_input(CalculateLangelierInput, ions_json=ions_json, temperature_C=temperature_C, pH=pH)
    return calculate_langelier_index(ions_json=params.ions_json, temperature_C=params.temperature_C, pH=params.pH)

@engineering_tool(name='corrosion_predict_scaling', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def predict_scaling(ions_json: Annotated[str, Field(min_length=10, description='JSON ion concentrations in mg/L. Must include Ca2+ and either HCO3- or CO3-2; values must be finite and non-negative. Nonzero ion magnitudes must be at least 1e-12 mg/L under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1.')], pH: Annotated[float, Field(ge=0.0, le=14.0, description='Reported pH; required for the calculation.')], temperature_C: Annotated[float, Field(ge=0.0, le=100.0, description='Water temperature in Celsius')]=25.0) -> dict:
    """
    Report CaCO₃ saturation/deposition indices and the Larson--Skold relative
    iron/steel corrosivity ratio, with each index's scope labelled in payload.

    Calculates:
    - LSI (Langelier Saturation Index): CaCO₃ saturation state
    - RSI (Ryznar Stability Index): CaCO₃ deposition tendency
    - PSI (Puckorius Scaling Index): CaCO₃ deposition tendency; value is
      suppressed because the verified primary-paper DOI is closed-access and
      the former implementation merely duplicated RSI
    - Larson--Skold ratio: Relative corrosivity of water to iron and steel

    LSI, RSI and PSI are not metal-loss indices. Larson--Skold is the only one
    of the four that speaks to metal corrosion, and this tool does not convert
    it into an absolute corrosion rate, risk band or material verdict.

    Args:
        ions_json: JSON ion concentrations in mg/L, including Ca2+ and either
            HCO3- or CO3-2. Values must be finite and non-negative.
        temperature_C: Water temperature in Celsius.
        pH: Caller-reported pH; required and never inferred from charge balance.

    Returns:
        Dictionary with all indices, explicit prediction/material scopes, and
        scale-management observations. No absolute corrosion verdict is emitted.

    Example:
        result = await predict_scaling(
            ions_json=(
                '{"Ca2+": 80, "Mg2+": 20, "HCO3-": 180, "Cl-": 100, '
                '"SO4-2": 60, "Na+": 31.8}'
            ),
            temperature_C=25.0,
            pH=7.5,
        )
        print(result["lsi"], result["larson_ratio"])
    """
    params = build_tool_input(PredictScalingInput, ions_json=ions_json, temperature_C=temperature_C, pH=pH)
    return predict_scaling_tendency(ions_json=params.ions_json, temperature_C=params.temperature_C, pH=params.pH)

@engineering_tool(name='corrosion_predict_co2', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def predict_co2(temperature_C: Annotated[float, Field(ge=20.0, le=150.0, description='Temperature in Celsius (NORSOK M-506 Table 3: 20-150)')], pressure_bar: Annotated[float, Field(gt=0.0, description='Total pressure in bar absolute')], co2_fraction: Annotated[Optional[float], Field(default=None, ge=0.0, le=1.0, description='CO2 mole fraction in gas phase. REQUIRED — absent is not zero. An undeclared composition previously returned a high-confidence 0.0 mm/y. 0.0 is accepted as a declaration that CO2 is absent.')]=None, h2s_fraction: Annotated[Optional[float], Field(default=None, ge=0.0, le=1.0, description='H2S mole fraction in gas phase. REQUIRED — absent is not zero. Any value above zero is rejected: no validated sour model exists here.')]=None, pH: Annotated[Optional[float], Field(ge=3.5, le=6.5, description='Water pH. If None, calculated from chemistry.')]=None, bicarbonate_mg_L: Annotated[Optional[float], Field(ge=0.0, description='Bicarbonate concentration in mg/L. Required when pH is not supplied; absent is not zero. With reported pH it is retained but labelled ignored_reported_pH_used.')]=None, ionic_strength_mg_L: Annotated[Optional[float], Field(ge=0.0, le=175000.0, description='NaCl-equivalent salinity in mg/L. The disclosed 50000 mg/L Table-6 default applies only in calculated-pH mode. With reported pH no default is created and a supplied value is labelled ignored_reported_pH_used.')]=None, wall_shear_stress_Pa: Annotated[Optional[float], Field(description='Reported wall shear in Pa. Supply this or every hydraulic input, never both; hidden hydraulic defaults are not used. Table-3 breaches return NORSOK_OUT_OF_ENVELOPE.')]=None, superficial_gas_velocity_m_s: Annotated[Optional[float], Field(ge=0.0, description='Reported superficial gas velocity in m/s')]=None, superficial_liquid_velocity_m_s: Annotated[Optional[float], Field(ge=0.0, description='Reported superficial liquid velocity in m/s')]=None, gas_mass_flow_kg_hr: Annotated[Optional[float], Field(gt=0.0, description='Reported gas mass flow in kg/h')]=None, liquid_mass_flow_kg_hr: Annotated[Optional[float], Field(gt=0.0, description='Reported liquid mass flow in kg/h')]=None, gas_volumetric_flow_m3_hr: Annotated[Optional[float], Field(gt=0.0, description='Reported gas volumetric flow in m3/h')]=None, liquid_volumetric_flow_m3_hr: Annotated[Optional[float], Field(gt=0.0, description='Reported liquid volumetric flow in m3/h')]=None, liquid_holdup_percent: Annotated[Optional[float], Field(ge=0.0, le=100.0, description='Reported liquid holdup in percent')]=None, gas_viscosity_cp: Annotated[Optional[float], Field(gt=0.0, description='Reported gas dynamic viscosity in cP')]=None, liquid_viscosity_cp: Annotated[Optional[float], Field(gt=0.0, description='Reported liquid dynamic viscosity in cP')]=None, pipe_roughness_m: Annotated[Optional[float], Field(ge=0.0, description='Reported absolute pipe roughness in metres')]=None, pipe_diameter_m: Annotated[Optional[float], Field(gt=0.0, description='Reported pipe internal diameter in metres')]=None) -> dict:
    """Predict sweet CO₂ corrosion rate using the repaired NORSOK M-506 model.

    NORSOK M-506 is the Norwegian oil & gas industry standard for internal
    CO₂ corrosion prediction in carbon steel pipelines carrying wet gas/oil.
    The transcription is checked against the official Rev. 2 equations/tables.
    Khakzad Table-2 rows 2-4 reproduce within 1.3%; row 1 remains an unresolved
    -5.86% mismatch that source rounding cannot explain. The sole exact Dogan
    prose value is retained only as a raw transcription anchor because its
    pCO2 equals total pressure and is outside Table 3. These checks are not
    evidence that M-506 matches laboratory or asset corrosion.
    This implementation has no sour-service model: ``h2s_fraction`` is required
    to distinguish unknown from zero, and every value above zero is rejected.
    Bicarbonate must likewise be declared when it is needed to calculate pH.

    Args:
        params (PredictCO2H2SInput): Validated input parameters containing:
            - temperature_C (float): Temperature (20-150°C, Table 3)
            - pressure_bar (float): Total pressure in bar
            - co2_fraction (float): CO₂ mole fraction (0-1)
            - h2s_fraction (float): H₂S mole fraction (0-1)
            - pH (Optional[float]): Water pH (if None, calculated)
            - bicarbonate_mg_L (float): Bicarbonate concentration
            - ionic_strength_mg_L (Optional[float]): NaCl-equivalent salinity;
              the 50000 mg/L Table-6 default applies only when pH is absent.
              With reported pH, a supplied value is retained as ignored and no
              default is created
            - wall_shear_stress_Pa (Optional[float]): Reported shear, or supply
              the complete velocity/flow/property/geometry input set

    Returns:
        Dictionary with corrosion rate (mm/y), mechanism, and pH.

    Example:
        result = await predict_co2(
            temperature_C=60.0,
            pressure_bar=10.0,
            co2_fraction=0.02,
            h2s_fraction=0.0,
            pH=5.0,
            wall_shear_stress_Pa=19.0,
        )
        print(result["corrosion_rate_mm_y"])
    """
    params = build_tool_input(PredictCO2H2SInput, temperature_C=temperature_C, pressure_bar=pressure_bar, co2_fraction=co2_fraction, h2s_fraction=h2s_fraction, pH=pH, bicarbonate_mg_L=bicarbonate_mg_L, ionic_strength_mg_L=ionic_strength_mg_L, wall_shear_stress_Pa=wall_shear_stress_Pa, superficial_gas_velocity_m_s=superficial_gas_velocity_m_s, superficial_liquid_velocity_m_s=superficial_liquid_velocity_m_s, gas_mass_flow_kg_hr=gas_mass_flow_kg_hr, liquid_mass_flow_kg_hr=liquid_mass_flow_kg_hr, gas_volumetric_flow_m3_hr=gas_volumetric_flow_m3_hr, liquid_volumetric_flow_m3_hr=liquid_volumetric_flow_m3_hr, liquid_holdup_percent=liquid_holdup_percent, gas_viscosity_cp=gas_viscosity_cp, liquid_viscosity_cp=liquid_viscosity_cp, pipe_roughness_m=pipe_roughness_m, pipe_diameter_m=pipe_diameter_m)
    return predict_co2_h2s_corrosion(temperature_C=params.temperature_C, pressure_bar=params.pressure_bar, co2_fraction=params.co2_fraction, h2s_fraction=params.h2s_fraction, pH=params.pH, bicarbonate_mg_L=params.bicarbonate_mg_L, ionic_strength_mg_L=params.ionic_strength_mg_L, wall_shear_stress_Pa=params.wall_shear_stress_Pa, superficial_gas_velocity_m_s=params.superficial_gas_velocity_m_s, superficial_liquid_velocity_m_s=params.superficial_liquid_velocity_m_s, gas_mass_flow_kg_hr=params.gas_mass_flow_kg_hr, liquid_mass_flow_kg_hr=params.liquid_mass_flow_kg_hr, gas_volumetric_flow_m3_hr=params.gas_volumetric_flow_m3_hr, liquid_volumetric_flow_m3_hr=params.liquid_volumetric_flow_m3_hr, liquid_holdup_percent=params.liquid_holdup_percent, gas_viscosity_cp=params.gas_viscosity_cp, liquid_viscosity_cp=params.liquid_viscosity_cp, pipe_roughness_m=params.pipe_roughness_m, pipe_diameter_m=params.pipe_diameter_m)

@engineering_tool(name='corrosion_screen_sour_service', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def screen_sour_service_tool(material: Annotated[str, Field(min_length=1, max_length=80)], wetness: Annotated[str, Field(description='dry | condensing | immersed | intermittent. Never inferred.')], H2S_ppmv: Annotated[Optional[float], Field(default=None, ge=0.0, le=H2S_GAS_COMPOSITION_MAX_PPMV, description='H2S concentration in ppmv: zero or at least 1e-12 ppmv under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1', json_schema_extra={'anyOf': [{'const': 0.0, 'type': 'number'}, {'minimum': MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, 'maximum': H2S_GAS_COMPOSITION_MAX_PPMV, 'type': 'number'}, {'type': 'null'}]})]=None, pressure_bar_abs: Annotated[Optional[float], Field(default=None, gt=0.0, description='Absolute pressure in bar; minimum is 1e-12 bar under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1', json_schema_extra={'minimum': MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE})]=None, pH2S_bar: Annotated[Optional[float], Field(default=None, ge=0.0, description='H2S partial pressure in bar: zero or at least 1e-12 bar under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1', json_schema_extra={'anyOf': [{'const': 0.0, 'type': 'number'}, {'minimum': MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, 'type': 'number'}, {'type': 'null'}]})]=None, in_situ_pH: Annotated[Optional[float], Field(default=None, ge=0.0, le=14.0)]=None, dissolved_sulfide_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, hardness_HRC: Annotated[Optional[float], Field(default=None, ge=0.0, le=70.0)]=None, welded: Annotated[Optional[bool], Field(default=None)]=None, temperature_C: Annotated[Optional[float], Field(default=None, ge=-50.0, le=300.0)]=None) -> Any:
    """Screen for sulfide stress cracking (SSC) against ISO 15156.

    WHICH PART APPLIES IS PART OF THE ANSWER. Part 2 covers carbon steels,
    low-alloy steels and cast irons and defines the numbered SSC regions; Part 3
    covers CRAs and other alloys and does not use those regions, setting
    per-material-family limits on pH2S, chloride, temperature and elemental sulfur
    instead. The part is selected from the resolved material family and a Part-3
    material gets no region.
    Part 3 has no general 0.3 kPa boundary. Because this server does not encode
    the licensed Annex A tables for CRAs and other alloys, every Part-3 pressure
    returns the typed
    `part3_alloy_specific_limits_or_qualification_required` standing: check the
    alloy-specific limits or qualify under ISO 15156-3; this tool cannot clear or
    condemn the alloy.

    HIC and SOHIC are NOT screened — they need steel cleanliness, inclusion
    morphology and product form, and HIC-resistant plate is specified and tested
    (NACE TM0284) rather than screened from a pH2S/pH pair.

    A domain screen against a standard, not a model. Returns a region and a
    qualification requirement, never a corrosion rate — no open validated sour
    rate model exists for these conditions.

    FOR PART-2 MATERIALS ONLY, PRESSURE, NOT CONCENTRATION, DECIDES FIGURE-1
    SCOPE. The Figure 1 SI boundary is
    0.3 kPa (3.0 mbar, about 0.0435 psi) H2S partial pressure, with equality on
    the sour side. The customary 0.05 psi shorthand is rounded commentary and
    is never the executable gate:

        250 ppmv at 1.013 bar  ->  0.253 mbar   below Part-2 boundary
        250 ppmv at 14.98 bar  ->  3.745 mbar   Part-2 scope

    The same Part-2 service is below the normal-applicability boundary at the
    blower and above it after compression; neither state is an SSC clearance.
    Screening on ppmv alone gets that backwards, so supply H2S_ppmv WITH
    pressure_bar_abs rather than a bare concentration.

    FOR PART-2 MATERIALS ONLY, BELOW 0.3 kPa DOES NOT MEAN SSC-CLEARED.
    Routine Part-2 qualification
    requirements do not normally apply below that boundary, but ISO
    15156-2:2020 Section 7.1.1 preserves susceptible-steel cracking in less
    severe environments: SSC is not excluded. Hardness, welding and reported pH
    remain visible on both sides. Wet-H2S corrosion, passive-film degradation and
    biogenic crown attack remain separately uncleared.

    Wetness is a hard gate and is never inferred: SSC requires liquid water, so
    assuming dry is unsafe and assuming wet is useless.
    """
    return await anyio.to_thread.run_sync(lambda: screen_sour_service(material=material, H2S_ppmv=H2S_ppmv, pressure_bar_abs=pressure_bar_abs, pH2S_bar=pH2S_bar, in_situ_pH=in_situ_pH, wetness=wetness, dissolved_sulfide_mg_L=dissolved_sulfide_mg_L, hardness_HRC=hardness_HRC, welded=welded, temperature_C=temperature_C))

@engineering_tool(name='corrosion_screen_sulfide_exposure', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def screen_sulfide_exposure_tool(material: Annotated[str, Field(min_length=1, max_length=80)], pH: Annotated[float, Field(ge=0.0, le=14.0)], total_sulfide_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, H2S_headspace_ppmv: Annotated[Optional[float], Field(default=None, ge=0.0, le=H2S_GAS_COMPOSITION_MAX_PPMV)]=None, temperature_C: Annotated[float, Field(ge=-5.0, le=150.0)]=25.0, pressure_bar_abs: Annotated[float, Field(ge=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, le=300.0, description='Absolute pressure in bar; minimum is the float-domain bound INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1')]=1.01325, ionic_strength_M: Annotated[float, Field(ge=0.0, le=10.0)]=0.0, Cl_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0, description='Chloride context: zero or at least 1e-12 mg/L under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1')]=None, SO4_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0, description='Sulfate context: zero or at least 1e-12 mg/L under INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1')]=None, measured_ORP_mV: Annotated[Optional[float], Field(default=None, ge=-2000.0, le=2000.0)]=None, measured_ORP_uncertainty_mV: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, reference_electrode: Annotated[str, Field(default='SHE', max_length=10)]='SHE', dissolved_Fe_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None) -> Any:
    """Screen anaerobic / sulfide service. Emits evidence grades, never a rate.

    Answers "there is sulfide here — do my chloride and PREN numbers still mean
    anything?" Sulfide does not add another aggressive anion to an oxide film, it
    can replace the film: once the surface is FeS/NiS rather than Cr2O3, PREN
    describes a different surface state. The on-point 316L study reports a
    CONTINUOUS concentration effect and no critical value. This tool therefore
    reports <0.1, 0.1-48 and >=48 mg/L descriptive grades with source status and
    uncertainty; it emits no passivity, model-applicability or material verdict.

    Supply the sulfide from whichever phase is instrumented — total_sulfide_mg_L
    for the liquid, H2S_headspace_ppmv for the gas — and it converts through the
    pH-dependent speciation. If both are supplied, both conversion directions
    are retained. Henry's law defines an equilibrium partition, not a universal
    possibility bound. A headspace above the liquid-implied equilibrium range is
    accepted with typed standing
    `DUAL_MEASUREMENTS_EXCEED_EQUILIBRIUM_RECONCILIATION`: external gas sources
    or transient gas-to-liquid transfer remain possible because equilibrium,
    co-location and timing were not declared. The tool evaluates both values and
    uses the more severe measurement equivalent independently for each liquid-
    concentration and headspace-exposure decision path. The standing identifies
    the equilibrium/co-location/timing evidence needed to restore reconciliation.
    A lower headspace remains admissible as ventilation or stripping. A
    gas-derived liquid value is a LOWER BOUND: it may
    invalidate the passivity basis but cannot clear it. That conversion is the
    reason to use it: pKa1 sits near the operating band, so the returned volatile
    fraction and headspace-equilibrium range can change materially with pH.

    S2- is never reported. The abstract of May, David Bátka (printed by RSC as
    D. Batka), Hefter, Koenigsberger & Rowland,
    "Goodbye to S2- in aqueous solution" (2018),
    reports new Raman spectra in hyper-concentrated hydroxide that cast serious
    doubt on S2-(aq), recommends expunging it from the chemical literature, and
    calls for careful revision of sulfide-mineral thermodynamic databases. The
    full text was not read. Above pH 12 the tool applies the disclosed
    INTERNAL_SCREENING_RULE_SULFIDE_HIGH_PH_SCOPE_V1, derived from the shipped
    legacy phreeqc.dat S-2 row and explicit arithmetic, and returns a typed
    reduced-confidence H2S/HS- scope result instead of inventing an S2-
    fraction. A lab "sulfide as S2-" result is a TOTAL sulfide measurement
    named by convention; the structured payload carries the DOI record.

    FeS film expectation requires both declared reactants to clear the payload's
    explicit assertion floor and a non-negative phreeqc.dat FeS PHREEQC saturation
    index. Measured-liquid undersaturation returns false; lower-bound
    undersaturation is indeterminate because a higher true liquid concentration
    may still be saturated. An unavailable calculation is also indeterminate.
    The result is a thermodynamic precipitation tendency, not an observed
    adherent film. Electrical, galvanic, protective, kinetic, and local-attack
    properties are not assessed without scoped content evidence.

    Cracking is NOT screened here — that is decided by H2S partial pressure
    against ISO 15156-2; use corrosion_screen_sour_service.
    """
    return await anyio.to_thread.run_sync(lambda: screen_sulfide_exposure(material=material, pH=pH, total_sulfide_mg_L=total_sulfide_mg_L, H2S_headspace_ppmv=H2S_headspace_ppmv, temperature_C=temperature_C, pressure_bar_abs=pressure_bar_abs, ionic_strength_M=ionic_strength_M, Cl_mg_L=Cl_mg_L, SO4_mg_L=SO4_mg_L, measured_ORP_mV=measured_ORP_mV, measured_ORP_uncertainty_mV=measured_ORP_uncertainty_mV, reference_electrode=reference_electrode, dissolved_Fe_mg_L=dissolved_Fe_mg_L))

@engineering_tool(name='corrosion_screen_mic', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def screen_mic_tool(material: Annotated[str, Field(min_length=1, max_length=80)], aerobic: Annotated[Optional[bool], Field(default=None)]=None, ORP_mV: Annotated[Optional[float], Field(default=None, ge=-2000.0, le=2000.0)]=None, ORP_reference_electrode: Annotated[Optional[str], Field(default=None, max_length=40)]=None, ORP_uncertainty_mV: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, dissolved_O2_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0, le=50.0)]=None, sulfate_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, sulfide_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, VFA_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, alkalinity_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, bulk_pH: Annotated[Optional[float], Field(default=None, ge=0.0, le=14.0)]=None, interface_pH: Annotated[Optional[float], Field(default=None, ge=0.0, le=14.0)]=None, Mn_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, Fe_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, OCP_ennoblement_mV: Annotated[Optional[float], Field(default=None, ge=-2000.0, le=2000.0)]=None, manganese_oxidising_biofilm_present: Annotated[Optional[bool], Field(default=None, description='Categorical surface observation; omitted is unknown. Used with biomineralised_MnO2_present and an observed OCP trajectory.')]=None, biomineralised_MnO2_present: Annotated[Optional[bool], Field(default=None, description='Categorical surface-analysis result; omitted is unknown.')]=None, OCP_ennoblement_trajectory_observed: Annotated[Optional[bool], Field(default=None, description='Categorical observation of an OCP trajectory, not a single OCP value.')]=None, dsrAB_copies_per_cm2: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, dsrAB_measurement_reference: Annotated[Optional[str], Field(default=None, min_length=1, max_length=240, description='qPCR assay, primer-set citation, or laboratory SOP reference. Required with dsrAB_copies_per_cm2.')]=None, dsrAB_uncertainty_copies_per_cm2: Annotated[Optional[float], Field(default=None, ge=0.0, description='Quantitative uncertainty of the reported sessile dsrAB abundance in copies/cm2. Required with dsrAB_copies_per_cm2.')]=None, deposits_present: Annotated[Optional[bool], Field(default=None)]=None, headspace_H2S_ppmv: Annotated[Optional[float], Field(default=None, ge=0.0, le=H2S_GAS_COMPOSITION_MAX_PPMV)]=None, relative_humidity_pct: Annotated[Optional[float], Field(default=None, ge=0.0, le=100.0)]=None, surface_pH: Annotated[Optional[float], Field(default=None, ge=0.0, le=14.0)]=None, acid_producing_biofilm_present: Annotated[Optional[bool], Field(default=None, description='Categorical APB surface observation; omitted is unknown.')]=None, sulfur_oxidising_biofilm_present: Annotated[Optional[bool], Field(default=None, description='Categorical SOB surface observation; omitted is unknown.')]=None, coating_or_lining: Annotated[Optional[str], Field(default=None, max_length=80)]=None, observed_corrosion_rate_mm_y: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, observed_rate_method: Annotated[Optional[str], Field(default=None, max_length=40)]=None, observed_rate_window_days: Annotated[Optional[float], Field(default=None, gt=0.0)]=None) -> Any:
    'Identify which MIC mechanisms the evidence supports. NEVER predicts a rate.'
    return await anyio.to_thread.run_sync(lambda: screen_mic(material=material, aerobic=aerobic, ORP_mV=ORP_mV, ORP_reference_electrode=ORP_reference_electrode, ORP_uncertainty_mV=ORP_uncertainty_mV, dissolved_O2_mg_L=dissolved_O2_mg_L, sulfate_mg_L=sulfate_mg_L, sulfide_mg_L=sulfide_mg_L, VFA_mg_L=VFA_mg_L, alkalinity_mg_L=alkalinity_mg_L, bulk_pH=bulk_pH, interface_pH=interface_pH, Mn_mg_L=Mn_mg_L, Fe_mg_L=Fe_mg_L, OCP_ennoblement_mV=OCP_ennoblement_mV, manganese_oxidising_biofilm_present=manganese_oxidising_biofilm_present, biomineralised_MnO2_present=biomineralised_MnO2_present, OCP_ennoblement_trajectory_observed=OCP_ennoblement_trajectory_observed, dsrAB_copies_per_cm2=dsrAB_copies_per_cm2, dsrAB_measurement_reference=dsrAB_measurement_reference, dsrAB_uncertainty_copies_per_cm2=dsrAB_uncertainty_copies_per_cm2, deposits_present=deposits_present, headspace_H2S_ppmv=headspace_H2S_ppmv, relative_humidity_pct=relative_humidity_pct, surface_pH=surface_pH, acid_producing_biofilm_present=acid_producing_biofilm_present, sulfur_oxidising_biofilm_present=sulfur_oxidising_biofilm_present, coating_or_lining=coating_or_lining, observed_corrosion_rate_mm_y=observed_corrosion_rate_mm_y, observed_rate_method=observed_rate_method, observed_rate_window_days=observed_rate_window_days))

@engineering_tool(name='corrosion_screen_concrete_exposure', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def screen_concrete_exposure_tool(zone: Annotated[str, Field(description='submerged | buried | splash | headspace | external_atmospheric. Never inferred.')], sulfate_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, pH: Annotated[Optional[float], Field(default=None, ge=0.0, le=14.0)]=None, magnesium_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, ammonium_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, aggressive_CO2_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, chloride_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, temperature_C: Annotated[Optional[float], Field(default=None, ge=-30.0, le=120.0)]=None, carbonate_aggregate: Annotated[Optional[bool], Field(default=None)]=None, headspace_H2S_ppmv: Annotated[Optional[float], Field(default=None, ge=0.0, le=H2S_GAS_COMPOSITION_MAX_PPMV)]=None, relative_humidity_pct: Annotated[Optional[float], Field(default=None, ge=0.0, le=100.0)]=None, cement_type: Annotated[Optional[str], Field(default=None, max_length=60)]=None, lining: Annotated[Optional[str], Field(default=None, max_length=80)]=None, water_origin: Annotated[Optional[Literal['natural_groundwater', 'natural_surface_water', 'non_industrial_effluent', 'industrial_effluent', 'other']], Field(default=None, description='Origin used to establish the EN 206 Table-2 applicability basis. Industrial effluent and other origins require a specific study; omitted origin is exposed as an unconfirmed assumption.')]=None, flow_regime: Annotated[Optional[Literal['static_or_low_flow', 'high_flow']], Field(default=None, description='Water-flow declaration for EN 206 Table 2. High flow is outside the verified envelope; omission is exposed as an unconfirmed static/low-flow assumption.')]=None) -> Any:
    'Classify a concrete exposure (EN 206 XA / ACI 318 S / IS 456) and screen'
    return await anyio.to_thread.run_sync(lambda: screen_concrete_exposure(zone=zone, sulfate_mg_L=sulfate_mg_L, pH=pH, magnesium_mg_L=magnesium_mg_L, ammonium_mg_L=ammonium_mg_L, aggressive_CO2_mg_L=aggressive_CO2_mg_L, chloride_mg_L=chloride_mg_L, temperature_C=temperature_C, carbonate_aggregate=carbonate_aggregate, headspace_H2S_ppmv=headspace_H2S_ppmv, relative_humidity_pct=relative_humidity_pct, cement_type=cement_type, lining=lining, water_origin=water_origin, flow_regime=flow_regime))

@engineering_tool(name='corrosion_screen_material_suitability', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def screen_material_suitability_tool(material: Annotated[str, Field(min_length=1, max_length=80)], Cl_mg_L: Annotated[float, Field(ge=0.0, le=300000.0)], temperature_C: Annotated[float, Field(ge=-20.0, le=200.0, description='Temperature in C. INTERNAL_CL_SCREEN_V1 is valid only from 0 to 60 C; out-of-validity values return a typed domain error.')]=25.0, pH: Annotated[float, Field(ge=0.0, le=14.0, description='pH. INTERNAL_CL_SCREEN_V1 is valid only from 1 to 10; out-of-validity values return a typed domain error.')]=7.0, oxidant: Annotated[Optional[str], Field(default=None, max_length=40)]=None, measured_ORP_mV: Annotated[Optional[float], Field(default=None, ge=-2000.0, le=2000.0)]=None, measured_ORP_uncertainty_mV: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, reference_electrode: Annotated[str, Field(default='SHE', max_length=10)]='SHE', sulfide_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, candidates: Annotated[Optional[List[str]], Field(default=None)]=None) -> Any:
    """Return a chloride screening margin and ordinal alloy ranking; no absolute verdict.

    Replaces the ASTM G48 temperature-margin ladder, which returned "critical"
    for any 316L service above 5 °C regardless of chloride, geometry or oxidant —
    a constant, not a conservative result. This keys off the CHLORIDE margin,
    which is the axis service actually varies along, and keeps G48 as a ranking
    index with `usable_for: [ranking]` stated in the payload.

    The `INTERNAL_SCREENING_RULE` rows are usable only as ranking coordinates.
    Neither qualification nor disqualification is licensed. The tool returns the
    continuous service/reference chloride ratio and ordinal candidate ranks, with
    no threshold bands from which a pass/fail branch can be reconstructed.

    An explicit oxidant declaration hands off to the oxidant-specific tool.
    Measured ORP is reference-corrected and reported without a decision cutoff;
    the former 0.400 V_SHE ranking gate had no primary-source basis.

    Supply `candidates` to dense-rank alternatives at identical conditions.
    Equal service/reference ratios receive equal ranks; canonical material ID
    controls display order only. At exactly zero service chloride every ratio is
    zero, so ranks are null and ordering is typed indeterminate. For
    stainless, every positive reported sulfide concentration makes the
    chloride-only coordinate conditional because that model has no sulfide term;
    0.099 and 0.101 mg/L do not select different verdicts. The concentration is
    reported as a continuous-effect evidence grade with source status and
    uncertainty. The 316L evidence is not extended to nickel alloys.
    """
    return await anyio.to_thread.run_sync(lambda: screen_material_suitability(material=material, Cl_mg_L=Cl_mg_L, temperature_C=temperature_C, pH=pH, oxidant=oxidant, measured_ORP_mV=measured_ORP_mV, measured_ORP_uncertainty_mV=measured_ORP_uncertainty_mV, reference_electrode=reference_electrode, sulfide_mg_L=sulfide_mg_L, candidates=candidates))

@engineering_tool(name='corrosion_screen_galvanic', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def screen_galvanic(material_a: Annotated[str, Field(min_length=1, max_length=80)], material_b: Annotated[str, Field(min_length=1, max_length=80)], area_ratio_cathode_to_anode: Annotated[Optional[float], Field(default=None, gt=0.0, le=10000.0)]=None, sulfide_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, sulfide_uncertainty_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, dissolved_oxygen_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0, le=50.0)]=None, dissolved_oxygen_uncertainty_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, crevice_present: Annotated[Optional[bool], Field(default=None)]=None, electrolyte_note: Annotated[Optional[str], Field(default=None, max_length=200)]=None) -> Any:
    """Rank a bimetallic couple by ASTM G82 driving force. Emits NO rate or current.

    Covers common CI/SS316, CI/SS304/SS410 and CS/SS316 couples as an
    open-circuit ranking. For aluminum, ASTM G82 contains both AA 3003-H and
    AA 6061-T; a generic aluminum request requires the caller to choose one.
    The kinetic `corrosion_assess_galvanic` accessor is withdrawn and is not an
    alternative source of rate or current.

    The active/passive distinction is the point. G82 tabulates stainless twice:

        Cast Iron vs SS304 PASSIVE  ->  530 mV
        Cast Iron vs SS304 ACTIVE   ->   80 mV

    The tool returns every available G82 table state as a separate scenario.
    Sulfide, dissolved oxygen, and crevice inputs are echoed but do not select a
    state: the former any-sulfide and DO<1 mg/L rules had no primary-source
    basis. SS316 active is explicitly unavailable because G82 does not tabulate it.

    Returns only the tabulated potential difference and the caller's cathode/anode
    area ratio. It emits no compatible/incompatible band and no
    acceptable/not-recommended verdict: the former MIL-STD-889 thresholds were
    not verified against a primary source, and this tool has no
    polarization current with which to judge an area ratio.

    No corrosion rate and no galvanic current are emitted. G82 potentials are
    open-circuit values in flowing seawater; they rank a couple, they carry no
    kinetics. This server currently provides no validated galvanic rate accessor.
    """
    return await anyio.to_thread.run_sync(lambda: screen_galvanic_couple(material_a=material_a, material_b=material_b, area_ratio_cathode_to_anode=area_ratio_cathode_to_anode, sulfide_mg_L=sulfide_mg_L, sulfide_uncertainty_mg_L=sulfide_uncertainty_mg_L, dissolved_oxygen_mg_L=dissolved_oxygen_mg_L, dissolved_oxygen_uncertainty_mg_L=dissolved_oxygen_uncertainty_mg_L, crevice_present=crevice_present, electrolyte_note=electrolyte_note))

@engineering_tool(name='corrosion_assess_oxidant_exposure', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def assess_oxidant_exposure_tool(material: Annotated[str, Field(min_length=1, max_length=80, description='Alloy designation')], oxidant: Annotated[Literal['free_chlorine', 'chlorine_dioxide', 'ozone', 'hydrogen_peroxide', 'peracetic_acid', 'ferric_iron', 'dissolved_oxygen'], Field(description='free_chlorine | chlorine_dioxide | ozone | hydrogen_peroxide | peracetic_acid | ferric_iron | dissolved_oxygen')], pH: Annotated[float, Field(ge=0.0, le=14.0, description='Bulk pH; governs free-chlorine speciation')], temperature_C: Annotated[float, Field(ge=-10.0, le=150.0)]=25.0, free_chlorine_mg_L_as_Cl2: Annotated[Optional[float], Field(default=None, ge=0.0, description='Reported free-chlorine residual as Cl2. Required when free_chlorine is selected. With another selected oxidant it is a concurrent supplied exposure: both lenses are retained and the controlling verdict is aggregated.')]=None, Cl_mg_L: Annotated[Optional[float], Field(default=None, ge=0.0)]=None, measured_ORP_mV: Annotated[Optional[float], Field(default=None, ge=-2000.0, le=2000.0, description='MEASURED redox potential. It is reported but does not lift model confidence and is not judged without a sourced alloy-specific threshold. It is never inferred and may be negative. INTERNAL_SCREENING_RULE_ORP_MAGNITUDE_V1 accepts -2000 to +2000 mV only as a broad unit/reference/instrument sanity envelope; the bounds are not a material decision threshold.')]=None, reference_electrode: Annotated[str, Field(default='SHE', description='SHE | SCE | Ag/AgCl. Raises on anything else — it is never defaulted.')]='SHE', service_context: Annotated[Optional[Literal['chemical_feed', 'treated_water']], Field(default=None, description='chemical_feed (concentrated oxidant wetted path: dosing skid, quill, carrier) | treated_water (downstream water). The NaOCl and ferric rules apply only to chemical_feed. Scope is never inferred from concentration; this API has no ferric concentration input.')]=None) -> Any:
    """Report oxidant speciation and apply an explicitly scoped house policy.

    The chloride screening tables carry no oxidant term. This accessor therefore
    keeps Morris free-chlorine speciation, a caller-reported ORP, and the internal
    chemical-feed material policy as outputs with separate evidence standings; it
    does not infer affirmative material suitability from chloride and oxidant
    concentrations.

    `oxidant` selects the primary reporting lens rather than asserting physical
    exclusivity. If `free_chlorine_mg_L_as_Cl2` is also supplied under another
    selector, the chlorine exposure is separately assessed, both assessments are
    retained, and a decisive disqualification controls the aggregate verdict.
    Calls with no concurrent exposure retain the historical payload shape.

    Returns three outputs of deliberately different standing:

      speciation          HIGH confidence only within Morris's measured 5-35 C
                          range and the declared dilute-solution concentration
                          domain; reduced_extrapolated or
                          reduced_concentrated_nonideal outside those domains.
                          The exact reported free-chlorine input is retained so
                          trace inputs round-trip even when species displays round.
      numeric redox index SUPPRESSED. Its E0 values lacked verified primary
                          sources, the former OCl branch omitted pH dependence,
                          and a reversible couple potential is not E_corr.
      house_rule          DECISIVE where it applies, being sourced policy rather
                          than model output.

    Concentrated ferric chloride is the ASTM G48 pitting-test reagent, illustrating
    why a reversible-couple ordering is not a material-suitability verdict. The
    decisive rule applies only when `service_context` is
    explicitly `chemical_feed`; otherwise ferric concentration is unknown and
    the result remains indeterminate.

    Material families outside the tool's implemented family set return the typed
    `UNSUPPORTED_MATERIAL_FAMILY` fail-closed standing. This is a coverage label,
    not a claim that ferritic/martensitic stainless, aluminium, or another
    unsupported material cannot form a passive film.

    Example:
        >>> r = assess_oxidant_exposure(material="316L", oxidant="free_chlorine",
        ...                             pH=7.0, free_chlorine_mg_L_as_Cl2=12500.0,
        ...                             service_context="chemical_feed")
        >>> r["verdict"]
        'disqualified'
    """
    return await anyio.to_thread.run_sync(lambda: assess_oxidant_exposure(material=material, oxidant=oxidant, pH=pH, temperature_C=temperature_C, free_chlorine_mg_L_as_Cl2=free_chlorine_mg_L_as_Cl2, Cl_mg_L=Cl_mg_L, measured_ORP_mV=measured_ORP_mV, reference_electrode=reference_electrode, service_context=service_context))

@engineering_tool(name='corrosion_predict_co2_h2s', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def predict_co2_h2s_tombstone(temperature_C: Annotated[Optional[float], Field(default=None)]=None, pressure_bar: Annotated[Optional[float], Field(default=None)]=None, co2_fraction: Annotated[Optional[float], Field(default=None, ge=0.0, le=1.0)]=None, h2s_fraction: Annotated[Optional[float], Field(default=None, ge=0.0, le=1.0)]=None, pH: Annotated[Optional[float], Field(default=None)]=None, bicarbonate_mg_L: Annotated[Optional[float], Field(default=None)]=None, pipe_diameter_m: Annotated[Optional[float], Field(default=None)]=None) -> Any:
    """RENAMED — this endpoint no longer computes. Use `corrosion_predict_co2`.

    The old name advertised an H2S model that never existed. The vendored NORSOK
    M-506 implementation contains no H2S, sulfide, FeS, or sour-corrosion
    equation, and `h2s_fraction` never entered the rate calculation - it only
    selected a `mechanism` string. The observable consequence was that
    `co2_fraction=0, h2s_fraction=0.005` at 35 C returned 0.0 mm/y, severity
    "negligible", confidence "high": no corrosion predicted for digester gas or
    sewer headspace.

    This tombstone exists so callers get a directed error rather than a missing
    tool. It computes nothing and never will.
    """
    raise CorrosionPublicError('RENAMED_UNSAFE', 'RENAMED_UNSAFE: corrosion_predict_co2_h2s has been renamed to corrosion_predict_co2 and no longer computes. The old name advertised an H2S model that does not exist - NORSOK M-506 is a CO2 model and the vendored implementation has no H2S terms at all. Call corrosion_predict_co2 for CO2 service. For H2S, no model is available on this server; assess sulfide corrosion and wet-H2S cracking (SSC/HIC/SOHIC) separately.', details={'reason_code': 'RENAMED_UNSAFE', 'replacement_tool': 'corrosion_predict_co2', 'h2s_supported': False, 'why': "h2s_fraction never entered any equation; pure-H2S input returned 0.0 mm/y at confidence 'high'"})

@engineering_tool(name='corrosion_predict_aerated_chloride', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def predict_aerated_chloride(temperature_C: Annotated[float, Field(ge=0.0, le=80.0, description='Temperature in Celsius')], chloride_mg_L: Annotated[float, Field(ge=0.0, description='Chloride concentration in mg/L')], pH: Annotated[float, Field(ge=4.0, le=10.0, description='Solution pH')]=7.0, dissolved_oxygen_mg_L: Annotated[Optional[float], Field(ge=DISSOLVED_OXYGEN_MG_L.minimum, le=DISSOLVED_OXYGEN_MG_L.maximum, description='Dissolved oxygen in mg/L')]=None, material: Annotated[str, Field(description="Material type: 'carbon_steel' or 'low_alloy'")]='carbon_steel') -> dict:
    """WITHDRAWN — this endpoint performs no aerated-chloride calculation.

    Every valid call returns a ``TOOL_WITHDRAWN`` error because the former model
    was approximately 1000x low and its ORR diffusion data are quarantined.
    The endpoint remains registered only so older clients fail closed.
    """
    params = build_tool_input(PredictAeratedChlorideInput, temperature_C=temperature_C, chloride_mg_L=chloride_mg_L, pH=pH, dissolved_oxygen_mg_L=dissolved_oxygen_mg_L, material=material)
    return predict_aerated_chloride_corrosion(temperature_C=params.temperature_C, chloride_mg_L=params.chloride_mg_L, pH=params.pH, dissolved_oxygen_mg_L=params.dissolved_oxygen_mg_L, material=params.material)

@engineering_tool(name='corrosion_assess_localized', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def assess_localized(material: Annotated[str, Field(min_length=1, description='Material name')], temperature_C: Annotated[float, Field(ge=0.0, le=150.0, description='Operating temperature in Celsius')], Cl_mg_L: Annotated[float, Field(ge=0.0, description='Chloride concentration in mg/L')], pH: Annotated[float, Field(ge=0.0, le=14.0, description='Solution pH')]=7.0, crevice_gap_mm: Annotated[float, Field(ge=MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE, le=10.0, description='Crevice gap width in mm; minimum is the float-domain bound INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1')]=0.1, dissolved_oxygen_mg_L: Annotated[Optional[float], Field(ge=DISSOLVED_OXYGEN_MG_L.minimum, le=DISSOLVED_OXYGEN_MG_L.maximum, description='Dissolved oxygen in mg/L')]=None) -> dict:
    """Return PREN and sourced electrochemical quantities; service limits are suppressed.

    The internal CPT/CCT, chloride-limit, crevice and ordinal-rank calculations
    are not exposed through this accessor because they are not sourced service
    criteria. PREN remains a relative composition index.
    ``E_redox_equilibrium_VSCE`` is a thermodynamic redox value, not a mixed
    potential, and is defined only inside its machine-readable 0-40 C
    freshwater source domain. Outside that domain it is a typed null while the
    public 0-150 C schema and other retained outputs remain available.
    Absolute E_pit is suppressed. Every derived electrochemical margin is also
    suppressed. This tool emits no pitting/crevice risk, rank, service limit or
    material-suitability verdict.
    """
    params = build_tool_input(AssessLocalizedInput, material=material, temperature_C=temperature_C, Cl_mg_L=Cl_mg_L, pH=pH, crevice_gap_mm=crevice_gap_mm, dissolved_oxygen_mg_L=dissolved_oxygen_mg_L)
    return calculate_localized_corrosion(material=params.material, temperature_C=params.temperature_C, Cl_mg_L=params.Cl_mg_L, pH=params.pH, crevice_gap_mm=params.crevice_gap_mm, dissolved_oxygen_mg_L=params.dissolved_oxygen_mg_L)

@engineering_tool(name='corrosion_calculate_pren', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def calc_pren(Cr_wt_pct: Annotated[float, Field(ge=0.0, le=30.0, description='Chromium content in weight percent')], Mo_wt_pct: Annotated[float, Field(ge=0.0, le=10.0, description='Molybdenum content in weight percent')], N_wt_pct: Annotated[float, Field(ge=0.0, le=1.0, description='Nitrogen content in weight percent')], Mn_wt_pct: Annotated[Optional[float], Field(ge=0.0, le=20.0, description='Manganese content in weight percent; required for the explicit Liu austenite-phase equation')]=None, grade_type: Annotated[str, Field(description="Grade type: 'austenitic', 'duplex', or 'superaustenitic'")]='austenitic', composition_basis: Annotated[Literal['bulk_alloy', 'austenite_phase'], Field(description="Composition basis; Liu's phase-specific equation requires austenite_phase")]='bulk_alloy', formula_variant: Annotated[Literal['ASTM_A511_PREN16', 'LIU_2020_DUPLEX_PREN30'], Field(description='Named formula: ASTM_A511_PREN16 (default, ASTM A511/A511M-16 S32760 note B) or explicit duplex-only LIU_2020_DUPLEX_PREN30 literature variant')]='ASTM_A511_PREN16') -> dict:
    """
    Calculate PREN (Pitting Resistance Equivalent Number) from composition.

    Default: ASTM_A511_PREN16 = %Cr + 3.3×%Mo + 16×%N for every
    grade label. LIU_2020_DUPLEX_PREN30 evaluates the cited alternative
    austenite-phase equation, Cr_gamma + 3.3×Mo_gamma + 30×N_gamma - Mn_gamma,
    only by explicit selection with grade_type="duplex",
    composition_basis="austenite_phase", and reported Mn. Liu et al. state
    that 16 or 30 is usually used for austenite and use 16 in their own work;
    this explicit 30 form is not presented as their recommended equation.

    Args:
        params (CalculatePRENInput): Validated input parameters containing:
            - Cr_wt_pct (float): Chromium content (wt%)
            - Mo_wt_pct (float): Molybdenum content (wt%)
            - N_wt_pct (float): Nitrogen content (wt%)
            - Mn_wt_pct (float | None): Manganese content (wt%)
            - grade_type (str): 'austenitic', 'duplex', or 'superaustenitic'
            - composition_basis (str): bulk_alloy or austenite_phase
            - formula_variant (str): named sourced equation

    Returns:
        Dictionary with the PREN composition index and validation standing. CPT
        is suppressed because the former PREN-minus-constant rules have no
        primary-source basis.

    Example:
        result = await calc_pren(CalculatePRENInput(
            Cr_wt_pct=16.5, Mo_wt_pct=2.0, N_wt_pct=0.05
        ))
        print(result["PREN"])  # 23.9
    """
    params = build_tool_input(CalculatePRENInput, Cr_wt_pct=Cr_wt_pct, Mo_wt_pct=Mo_wt_pct, N_wt_pct=N_wt_pct, Mn_wt_pct=Mn_wt_pct, grade_type=grade_type, composition_basis=composition_basis, formula_variant=formula_variant)
    return calculate_pren(Cr_wt_pct=params.Cr_wt_pct, Mo_wt_pct=params.Mo_wt_pct, N_wt_pct=params.N_wt_pct, Mn_wt_pct=params.Mn_wt_pct, grade_type=params.grade_type, composition_basis=params.composition_basis, formula_variant=params.formula_variant)

@engineering_tool(name='corrosion_estimate_service_life', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def estimate_service_life(thickness_mm: Annotated[float, Field(gt=0.0, description='Current wall thickness in mm')], corrosion_rate_mm_y: Annotated[float, Field(ge=0.0, description='Corrosion rate in mm/year')], minimum_thickness_mm: Annotated[float, Field(gt=0.0, description='Minimum required/retirement wall thickness in mm')], design_allowance_mm: Annotated[float, Field(ge=0.0, description='Original design corrosion allowance in mm, retained for context only; not subtracted from current measured wall')]=0.0, safety_factor: Annotated[float, Field(ge=1.0, le=3.0, description='Safety factor to apply to corrosion rate')]=1.0) -> dict:
    """
    Calculate remaining-life arithmetic from thickness and corrosion rate.

    Uses API 570 remaining-life practice: (current wall - required wall) / rate.
    It does not assess whether the resulting duration is adequate because no
    design-life, inspection-interval, RBI-class, or owner criterion is accepted.

    Args:
        thickness_mm: Current wall thickness in mm.
        corrosion_rate_mm_y: Caller-supplied corrosion rate in mm/year.
        minimum_thickness_mm: Caller-supplied minimum/retirement wall in mm.
        design_allowance_mm: Original allowance, informational only.
        safety_factor: Multiplier applied to the caller-supplied rate.

    Returns:
        Dictionary with remaining-life arithmetic, current/minimum wall, and
        wall margin. No adequacy status or inspection recommendation is returned.

    Example:
        result = await estimate_service_life(
            thickness_mm=10.0,
            corrosion_rate_mm_y=0.5,
            minimum_thickness_mm=3.0
        )
        print(result["service_life_years"])  # 14.0
    """
    params = build_tool_input(EstimateServiceLifeInput, thickness_mm=thickness_mm, corrosion_rate_mm_y=corrosion_rate_mm_y, design_allowance_mm=design_allowance_mm, minimum_thickness_mm=minimum_thickness_mm, safety_factor=safety_factor)
    effective_thickness = params.thickness_mm - params.minimum_thickness_mm
    if not math.isfinite(effective_thickness):
        raise NonFiniteServiceLifeArithmeticError(operation='thickness_mm - minimum_thickness_mm', operands={'thickness_mm': params.thickness_mm, 'minimum_thickness_mm': params.minimum_thickness_mm}, computed_value=effective_thickness)
    effective_rate = params.corrosion_rate_mm_y * params.safety_factor
    if not math.isfinite(effective_rate):
        raise NonFiniteServiceLifeArithmeticError(operation='corrosion_rate_mm_y * safety_factor', operands={'corrosion_rate_mm_y': params.corrosion_rate_mm_y, 'safety_factor': params.safety_factor}, computed_value=effective_rate)
    provenance = {'model': 'API 570 remaining-life arithmetic', 'confidence': 'input_dependent', 'standards': ['API 570 remaining-life practice'], 'minimum_thickness_basis': 'caller_supplied', 'design_allowance_used_in_calculation': False}
    common_result = {'current_thickness_mm': params.thickness_mm, 'minimum_thickness_mm': params.minimum_thickness_mm, 'design_allowance_mm': params.design_allowance_mm, 'design_allowance_standing': 'reported_context_only_not_used_in_remaining_life_arithmetic', 'wall_margin_above_minimum_mm': effective_thickness, 'acceptance_assessment_performed': False, 'acceptance_inputs_not_supplied': ['required_remaining_life', 'inspection_interval', 'RBI_class', 'owner_acceptance_criterion'], 'provenance': provenance}
    if effective_thickness <= 0:
        return {**common_result, 'service_life_years': 0.0, 'effective_corrosion_rate_mm_y': effective_rate, 'calculation_status': 'current_wall_at_or_below_caller_supplied_minimum'}
    if effective_rate == 0.0:
        return {**common_result, 'service_life_years': None, 'effective_corrosion_rate_mm_y': 0.0, 'calculation_status': 'unbounded_for_zero_input_rate'}
    life_years = effective_thickness / effective_rate
    if not math.isfinite(life_years):
        raise NonFiniteServiceLifeArithmeticError(operation='wall_margin_above_minimum_mm / effective_corrosion_rate_mm_y', operands={'wall_margin_above_minimum_mm': effective_thickness, 'effective_corrosion_rate_mm_y': effective_rate}, computed_value=life_years)
    require_representable_nonzero_result('wall_margin_above_minimum_mm / effective_corrosion_rate_mm_y', life_years, operands={'wall_margin_above_minimum_mm': effective_thickness, 'effective_corrosion_rate_mm_y': effective_rate})
    return {**common_result, 'service_life_years': life_years, 'effective_corrosion_rate_mm_y': effective_rate, 'calculation_status': 'finite_remaining_life_calculated'}

@engineering_tool(name='corrosion_get_phreeqc_status', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def get_phreeqc_status() -> PhreeqcStatusResult:
    """
    Probe PHREEQC loadability for the corrosion chemistry surface.

    This performs a real ``PhreeqPython()`` instantiation for each configured
    database and reports the selected VIPhreeqc shared-library architecture.
    """
    build_tool_input(GetPhreeqcStatusInput)
    status = await anyio.to_thread.run_sync(get_phreeqc_engine_status)
    return PhreeqcStatusResult(ready=status.ready, viphreeqc_lib_path=redact_paths(status.viphreeqc_lib_path), viphreeqc_arch=status.viphreeqc_arch, databases_loadable=[PhreeqcDatabaseStatus(name=item.name, ok=item.ok, error=redact_paths(item.error)) for item in status.databases_loadable], error=redact_paths(status.error))

@engineering_tool(name='corrosion_get_server_info', annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
@public_tool_errors
async def get_server_info() -> ServerInfoResult:
    """
    Get corrosion engineering MCP server information.

    Returns:
        Dictionary with server version, capabilities, tool registry with metadata

    Example:
        info = get_server_info()
        print(f"Phase: {info['phase']}")
        print(f"Available tools: {info['tool_count']}")
        # Filter by tier
        tier2_tools = [t for t in info['tool_registry'] if t['tier'] == 'mechanistic']
    """
    build_tool_input(GetServerInfoInput)
    tool_registry = [{'name': 'corrosion_screen_materials', 'tier': 'handbook', 'phase': '0', 'description': 'Corpus-backed material compatibility evidence search', 'typical_latency_sec': 0.5, 'availability_status': 'configuration_required', 'requires_configuration': ['CORROSION_CORPUS_PATH'], 'output_scope': ['corpus_retrieval_when_configured']}, {'name': 'corrosion_query_typical_rates', 'tier': 'handbook', 'phase': '0', 'description': 'Empirical corrosion rate lookup', 'typical_latency_sec': 0.5, 'availability_status': 'configuration_required', 'requires_configuration': ['CORROSION_CORPUS_PATH'], 'output_scope': ['corpus_retrieval_when_configured']}, {'name': 'corrosion_identify_mechanism', 'tier': 'handbook', 'phase': '0', 'description': 'Corrosion mechanism identification', 'typical_latency_sec': 0.5, 'availability_status': 'configuration_required', 'requires_configuration': ['CORROSION_CORPUS_PATH'], 'output_scope': ['corpus_retrieval_when_configured']}, {'name': 'corrosion_langelier_index', 'tier': 'chemistry', 'phase': '1', 'description': 'Langelier Saturation Index for CaCO₃ scaling', 'typical_latency_sec': 1.0}, {'name': 'corrosion_predict_scaling', 'tier': 'chemistry', 'phase': '1', 'description': 'CaCO3 indices plus relative Larson-Skold iron/steel indicator', 'typical_latency_sec': 1.0, 'output_scope': ['calcite_saturation_and_deposition_indices', 'relative_iron_steel_indicator'], 'suppressed_outputs': ['metal_loss_rate', 'material_verdict', 'dosing_recommendation']}, {'name': 'corrosion_assess_galvanic', 'tier': 'mechanistic', 'phase': '2', 'description': 'WITHDRAWN galvanic mixed-potential model; computes no result', 'typical_latency_sec': 0.15, 'availability_status': 'withdrawn', 'suppressed_outputs': ['rate', 'current', 'polarity', 'assessment', 'recommendation']}, {'name': 'corrosion_generate_pourbaix', 'tier': 'chemistry', 'phase': '2', 'description': '25 C H2/O2 water-stability lines; all unsourced element-reaction E0 values and derived boundaries are suppressed', 'typical_latency_sec': 0.2, 'output_scope': ['water_stability_lines'], 'suppressed_outputs': ['unsourced_reaction_E0_and_boundaries', 'phase_selection', 'passivity', 'material_selection']}, {'name': 'corrosion_resolve_material_identity', 'tier': 'database', 'phase': '2', 'description': 'Resolve aliases to a local YAML catalog identity; this is not a material-property accessor', 'typical_latency_sec': 0.01, 'output_scope': ['canonical_identifier', 'uns_designation', 'internal_material_classification'], 'suppressed_outputs': []}, {'name': 'corrosion_screen_sour_service', 'tier': 'mechanistic', 'phase': 'R2.2', 'description': 'ISO 15156 sour-service screen for SSC ONLY (HIC/SOHIC need steel cleanliness data and are not screened). Part 2 for carbon/low-alloy steel and cast iron; its below-0.3-kPa result is not an SSC clearance. Part 3 for CRAs and other alloys has no general 0.3-kPa boundary and requires material-family-specific limits or qualification. No rate. Wetness is a hard gate.', 'typical_latency_sec': 0.02}, {'name': 'corrosion_screen_sulfide_exposure', 'tier': 'mechanistic', 'phase': 'R2.3', 'description': 'Anaerobic/sulfide screen: H2S/HS- speciation, gas-liquid partition, and continuous-effect concentration grades with source status and uncertainty. Above-equilibrium dual measurements are accepted with a non-equilibrium reconciliation standing and more-severe decision bases. No passivity/material verdict and no rate.', 'typical_latency_sec': 0.02}, {'name': 'corrosion_screen_mic', 'tier': 'mechanistic', 'phase': 'R2.6', 'description': 'MIC mechanism identification: SRB, ennoblement, acid producers, headspace sulfur oxidisers. Missing evidence is indeterminate; only affirmative contradiction can mark every mechanism unsupported. NEVER predicts a rate.', 'typical_latency_sec': 0.02}, {'name': 'corrosion_screen_concrete_exposure', 'tier': 'mechanistic', 'phase': 'R2.8', 'description': 'EN 206 XA / ACI S / IS 456 exposure classes plus a biogenic sulfuric acid gate. EN 206 is bounded to 5-25 C and low flow; buried results are groundwater-only with soil explicitly unassessed. DNA duties are conditional outside or without a confirmed Table-2 basis. Zone is required. No deterioration rate.', 'typical_latency_sec': 0.01}, {'name': 'corrosion_screen_material_suitability', 'tier': 'mechanistic', 'phase': 'R2.5', 'description': 'Chloride-margin and ordinal alloy ranking. Ranking-only data; no absolute verdict in either direction and no rate.', 'typical_latency_sec': 0.02, 'output_scope': ['relative_chloride_coordinate', 'ordinal_candidate_ranking'], 'suppressed_outputs': ['material_verdict', 'risk_level', 'corrosion_rate']}, {'name': 'corrosion_screen_galvanic', 'tier': 'mechanistic', 'phase': 'R2.7', 'description': 'ASTM G82 galvanic screening: driving force + area ratio + unselected active/passive table scenarios. Generic aluminum requires an explicit AA 3003-H or AA 6061-T choice. No rate, no current.', 'typical_latency_sec': 0.02, 'output_scope': ['astm_g82_open_circuit_potential_difference', 'reported_area_ratio', 'active_and_passive_table_scenarios_where_tabulated', 'reported_environment_without_state_inference'], 'suppressed_outputs': ['galvanic_current', 'corrosion_rate', 'compatibility_verdict', 'recommendation']}, {'name': 'corrosion_assess_oxidant_exposure', 'tier': 'mechanistic', 'phase': 'R2.1', 'description': 'Oxidant loading: free-chlorine speciation, measured ORP without a verdict, and the scoped sourced house rule. Numeric redox index is suppressed. Unsupported material families fail closed without being labelled non-passive.', 'typical_latency_sec': 0.05, 'output_scope': ['free_chlorine_speciation', 'reported_measured_potential', 'scoped_house_rule'], 'suppressed_outputs': ['redox_forcing_index']}, {'name': 'corrosion_predict_co2', 'tier': 'mechanistic', 'phase': '2', 'description': 'Repaired NORSOK M-506 CO₂ (sweet) corrosion for carbon steel; structured transcription-source records are returned by the calculation payload. Rejects any declared H₂S — no sour model exists here.', 'typical_latency_sec': 0.5}, {'name': 'corrosion_predict_co2_h2s', 'tier': 'metadata', 'phase': '2', 'description': 'TOMBSTONE — renamed to corrosion_predict_co2. Computes nothing; returns RENAMED_UNSAFE. The old name advertised an H₂S model that never existed.', 'typical_latency_sec': 0.01, 'availability_status': 'tombstone', 'suppressed_outputs': ['all_computational_outputs']}, {'name': 'corrosion_predict_aerated_chloride', 'tier': 'mechanistic', 'phase': '2', 'description': 'WITHDRAWN — returns ToolWithdrawnError. Computed ~1000x low.', 'typical_latency_sec': 0.2, 'availability_status': 'withdrawn', 'suppressed_outputs': ['all_computational_outputs']}, {'name': 'corrosion_assess_localized', 'tier': 'mechanistic', 'phase': '3', 'description': 'Localized-corrosion inputs only; internal CPT/CCT, chloride limits, crevice numerics, ranks, verdicts and absolute E_pit are suppressed.', 'typical_latency_sec': 0.3, 'output_scope': ['pren_composition_index', 'named_pren_formula_basis', 'sourced_electrochemical_quantities'], 'suppressed_outputs': ['CPT_C', 'CCT_C', 'chloride_service_threshold', 'crevice_numerics', 'all_service_ranks_and_verdicts', 'absolute_E_pit', 'material_suitability']}, {'name': 'corrosion_calculate_pren', 'tier': 'chemistry', 'phase': '3', 'description': 'PREN relative composition index with named formula: ASTM A511 PREN16 default or explicitly selected, duplex-only Liu 2020 PREN30 literature coefficient', 'typical_latency_sec': 0.01, 'output_scope': ['pren_relative_ranking_index', 'named_formula_and_source'], 'suppressed_outputs': ['CPT', 'service_limit', 'material_verdict']}, {'name': 'corrosion_estimate_service_life', 'tier': 'engineering', 'phase': '3', 'description': 'API 570 remaining-life arithmetic without an adequacy verdict', 'typical_latency_sec': 0.01, 'output_scope': ['remaining_life_arithmetic', 'wall_margin_above_caller_minimum'], 'suppressed_outputs': ['adequacy_status', 'inspection_recommendation', 'replacement_recommendation']}, {'name': 'corrosion_get_phreeqc_status', 'tier': 'metadata', 'phase': '1', 'description': 'PHREEQC shared-library and database loadability probe', 'typical_latency_sec': 0.2}, {'name': 'corrosion_get_server_info', 'tier': 'metadata', 'phase': '0', 'description': 'Server information and tool registry', 'typical_latency_sec': 0.001}]
    material_ids = advertised_materials()
    return ServerInfoResult(name='Corrosion Engineering MCP Server', version='0.5.0', phase='Registered multi-tier toolset with per-tool availability and suppression', tool_count=len(tool_registry), architecture='Per-tool corpus, calculation, screening, catalog, and metadata contracts', total_tools_planned=23, knowledge_base='handbook corpus not bundled; requires CORROSION_CORPUS_PATH', tool_registry=tool_registry, implemented_tools={'tier_0_handbook': [], 'configuration_dependent': ['corrosion_screen_materials', 'corrosion_query_typical_rates', 'corrosion_identify_mechanism'], 'tier_1_chemistry': ['corrosion_langelier_index', 'corrosion_predict_scaling'], 'tier_2_mechanistic': ['corrosion_generate_pourbaix', 'corrosion_predict_co2', 'corrosion_assess_oxidant_exposure', 'corrosion_screen_galvanic', 'corrosion_screen_sour_service', 'corrosion_screen_sulfide_exposure', 'corrosion_screen_mic', 'corrosion_screen_concrete_exposure', 'corrosion_screen_material_suitability'], 'phase_3_localized': ['corrosion_assess_localized', 'corrosion_calculate_pren'], 'engineering': ['corrosion_estimate_service_life', 'corrosion_resolve_material_identity'], 'metadata': ['corrosion_get_server_info', 'corrosion_get_phreeqc_status'], 'withdrawn_or_tombstoned': ['corrosion_predict_co2_h2s', 'corrosion_predict_aerated_chloride', 'corrosion_assess_galvanic']}, supported_materials=material_ids, advertised_materials=material_ids, supported_elements_pourbaix=list(SUPPORTED_POURBAIX_ELEMENTS), nrl_provenance='NRL-derived coefficient files are vendored, but the galvanic and aerated-chloride rate accessors are withdrawn and absolute E_pit is suppressed. No NRL validation dataset is claimed.', github='https://github.com/puran-water/corrosion-engineering-mcp', documentation='See README.md and docs/')

def main():
    mcp.run(transport='stdio')
if __name__ == '__main__':
    main()
