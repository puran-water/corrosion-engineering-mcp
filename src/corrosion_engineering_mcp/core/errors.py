"""Public error shaping and path redaction for corrosion tools."""

from __future__ import annotations

import math
from numbers import Real
import traceback
import uuid
from typing import Any, Mapping, MutableMapping

from puran_mcp_support.engineering.safe_paths import redact_mapping, redact_paths


# Internal serialization/arithmetic sanity domain, not a corrosion threshold.
# Every nonzero numeric input is screened at this magnitude.  This is deliberately
# server-wide: deciding per derivation merely moves the next wrong-finite hole.
MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE = 1.0e-12
FLOAT_MAGNITUDE_SCREENING_RULE = {
    "rule_id": "INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1",
    "minimum_positive_magnitude": MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
    "rationale": (
        "Float-domain sanity bound: nonzero magnitudes below 1e-12 can "
        "underflow or become numerically indistinguishable from zero in this "
        "toolchain; this is not an engineering suitability threshold."
    ),
}


class CorrosionPublicError(Exception):
    """Typed corrosion error that is safe to expose after redaction."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = sanitize_message(message)
        self.details = redact_mapping(dict(details or {}))
        super().__init__(self.message)


def find_non_finite_paths(value: Any, path: str = "$", _seen: set[int] | None = None) -> list[str]:
    """Return JSON-style paths to every non-finite real value in ``value``.

    This is the protocol-boundary backstop.  Producers remain responsible for
    checking their own arithmetic early enough to return an operation-specific
    error; this traversal prevents a missed check from becoming a non-standard
    JSON ``NaN``/``Infinity`` token on the wire.
    """

    if isinstance(value, Real) and not isinstance(value, (bool, int)):
        try:
            return [] if math.isfinite(float(value)) else [path]
        except (OverflowError, TypeError, ValueError):
            return [path]
    if value is None or isinstance(value, (str, bytes, bool, int)):
        return []

    if _seen is None:
        _seen = set()
    identity = id(value)
    if identity in _seen:
        return []

    if isinstance(value, Mapping):
        _seen.add(identity)
        paths: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            child_path = (
                f"{path}.{key_text}"
                if key_text.isidentifier()
                else f"{path}[{key_text!r}]"
            )
            paths.extend(find_non_finite_paths(item, child_path, _seen))
        return paths

    if isinstance(value, (list, tuple, set, frozenset)):
        _seen.add(identity)
        paths = []
        for index, item in enumerate(value):
            paths.extend(find_non_finite_paths(item, f"{path}[{index}]", _seen))
        return paths

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        _seen.add(identity)
        return find_non_finite_paths(model_dump(mode="python"), path, _seen)
    return []


class NonFiniteArithmeticError(CorrosionPublicError, ArithmeticError):
    """A calculation produced an undefined or unrepresentable real result."""

    def __init__(
        self,
        operation: str,
        *,
        operands: Mapping[str, Any] | None = None,
        non_finite_quantities: list[str] | None = None,
        non_finite_paths: list[str] | None = None,
        verdict_emitted: bool = False,
    ) -> None:
        finite_operands: dict[str, int | float] = {}
        omitted_operand_names: list[str] = []
        for name, value in dict(operands or {}).items():
            if isinstance(value, bool) or not isinstance(value, Real):
                omitted_operand_names.append(name)
                continue
            try:
                finite = math.isfinite(float(value))
            except (OverflowError, TypeError, ValueError):
                finite = False
            if finite:
                finite_operands[name] = value
            else:
                omitted_operand_names.append(name)

        details: dict[str, Any] = {
            "reason_code": "NON_FINITE_ARITHMETIC",
            "calculation_status": "out_of_domain_non_finite_arithmetic",
            "operation": operation,
            "operands": finite_operands,
            "non_finite_quantities": list(non_finite_quantities or []),
            "non_finite_paths": list(non_finite_paths or []),
            "result_emitted": False,
            "verdict_emitted": verdict_emitted,
        }
        if omitted_operand_names:
            details["non_finite_or_non_numeric_operand_names"] = omitted_operand_names
        super().__init__(
            "NON_FINITE_ARITHMETIC",
            "NON_FINITE_ARITHMETIC: finite input values produced an undefined "
            "or unrepresentable arithmetic result; no result or verdict is returned.",
            details=details,
        )


class NonFiniteErrorDetailError(CorrosionPublicError, ArithmeticError):
    """A typed error contained a non-finite detail and was replaced safely."""

    def __init__(self, original_error_code: str, non_finite_paths: list[str]) -> None:
        super().__init__(
            "NON_FINITE_ERROR_DETAIL",
            "NON_FINITE_ERROR_DETAIL: a tool failure contained a non-finite "
            "numeric detail; the original unsafe detail payload was not emitted.",
            details={
                "reason_code": "NON_FINITE_ERROR_DETAIL",
                "original_error_code": original_error_code,
                "non_finite_paths": list(non_finite_paths),
                "original_details_emitted": False,
                "result_emitted": False,
            },
        )


class InputBelowFloatMagnitudeDomainError(CorrosionPublicError, ValueError):
    """A positive input is too small for the declared computation domain."""

    def __init__(self, quantity: str, value: float) -> None:
        super().__init__(
            "INPUT_BELOW_FLOAT_MAGNITUDE_DOMAIN",
            "INPUT_BELOW_FLOAT_MAGNITUDE_DOMAIN: a positive reported magnitude "
            "is below the declared floating-point execution domain; no result "
            "is returned.",
            details={
                "quantity": quantity,
                "reported_value": value,
                "screening_rule": dict(FLOAT_MAGNITUDE_SCREENING_RULE),
                "result_emitted": False,
            },
        )


class UnderflowBelowFloatDomainError(CorrosionPublicError, ArithmeticError):
    """Nonzero finite operands collapsed to a finite zero result."""

    def __init__(self, operation: str, operands: Mapping[str, Any]) -> None:
        finite_operands = {
            name: value
            for name, value in operands.items()
            if isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        }
        super().__init__(
            "UNDERFLOW_BELOW_FLOAT_DOMAIN",
            "UNDERFLOW_BELOW_FLOAT_DOMAIN: nonzero finite operands produced "
            "an exact floating-point zero; no derived result or verdict is "
            "returned.",
            details={
                "reason_code": "UNDERFLOW_BELOW_FLOAT_DOMAIN",
                "calculation_status": "out_of_domain_wrong_finite_underflow",
                "operation": operation,
                "operands": finite_operands,
                "result_emitted": False,
                "verdict_emitted": False,
            },
        )


def _iter_numeric_inputs(value: Any, path: str):
    """Yield ``(path, real)`` leaves from ordinary public input containers."""

    if isinstance(value, Real) and not isinstance(value, bool):
        yield path, value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_numeric_inputs(item, child)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            child = f"{path}[{index}]" if path else f"[{index}]"
            yield from _iter_numeric_inputs(item, child)
        return
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        yield from _iter_numeric_inputs(model_dump(mode="python"), path)


def validate_float_domain_inputs(
    values: Any,
    *,
    minimum_nonzero_magnitude: float | None = None,
) -> Any:
    """Apply the one server-wide nonzero float-magnitude input contract.

    Explicit numeric zero is retained.  Signed fields (temperature and
    electrochemical potential, for example) are screened by absolute magnitude
    so a negative subnormal cannot bypass the same wrong-finite protection.
    Other type, finiteness, and engineering-range checks remain with their
    existing schemas/accessors.
    """

    minimum = (
        MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE
        if minimum_nonzero_magnitude is None
        else minimum_nonzero_magnitude
    )
    for quantity, value in _iter_numeric_inputs(values, ""):
        try:
            converted = float(value)
        except (OverflowError, TypeError, ValueError):
            continue
        if (
            math.isfinite(converted)
            and converted != 0.0
            and abs(converted) < minimum
        ):
            raise InputBelowFloatMagnitudeDomainError(quantity, converted)
    return values


def require_representable_nonzero_result(
    operation: str,
    result: float,
    *,
    operands: Mapping[str, Any],
) -> float:
    """Reject exact zero when every declared source operand is finite/nonzero."""

    source_values = list(operands.values())
    if (
        result == 0.0
        and source_values
        and all(
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) != 0.0
            for value in source_values
        )
    ):
        raise UnderflowBelowFloatDomainError(operation, operands)
    return result


_FLOAT_DOMAIN_DESCRIPTION = (
    " Nonzero numeric magnitudes must be at least 1e-12 under "
    "INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1; explicit zero retains the "
    "field's declared zero semantics."
)


def annotate_float_domain_schema(
    schema: MutableMapping[str, Any],
    *,
    property_node: bool = False,
) -> bool:
    """Annotate and constrain every numeric leaf in a JSON Schema in place.

    Returns whether this node contains a numeric leaf, allowing the containing
    property (where descriptions normally live) to carry the same metadata.
    """

    node_type = schema.get("type")
    contains_numeric = node_type in {"number", "integer"}
    for keyword in ("anyOf", "oneOf", "allOf"):
        children = schema.get(keyword, [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, MutableMapping):
                    contains_numeric = (
                        annotate_float_domain_schema(child, property_node=False)
                        or contains_numeric
                    )
    properties = schema.get("properties")
    if isinstance(properties, MutableMapping):
        for child in properties.values():
            if isinstance(child, MutableMapping):
                contains_numeric = (
                    annotate_float_domain_schema(child, property_node=True)
                    or contains_numeric
                )
    definitions = schema.get("$defs")
    if isinstance(definitions, MutableMapping):
        for child in definitions.values():
            if isinstance(child, MutableMapping):
                contains_numeric = (
                    annotate_float_domain_schema(child, property_node=False)
                    or contains_numeric
                )
    items = schema.get("items")
    if isinstance(items, MutableMapping):
        contains_numeric = (
            annotate_float_domain_schema(items, property_node=False)
            or contains_numeric
        )

    if node_type == "number" and schema.get("const") is None:
        lower = schema.get("minimum")
        exclusive_lower = schema.get("exclusiveMinimum")
        already_bounded = (
            isinstance(lower, Real)
            and float(lower) >= MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE
        ) or (
            isinstance(exclusive_lower, Real)
            and float(exclusive_lower) >= MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE
        )
        if not already_bounded:
            gap_constraint = {
                "anyOf": [
                    {
                        "maximum": -MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
                    },
                    {"const": 0.0},
                    {"minimum": MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE},
                ]
            }
            existing_all_of = schema.setdefault("allOf", [])
            if gap_constraint not in existing_all_of:
                existing_all_of.append(gap_constraint)

    # Discovery metadata belongs on the public field, not on each internal
    # ``anyOf``/``allOf`` branch.  Numeric constraints still recurse into those
    # branches, preserving exact conditional schemas such as zero-or-floor.
    if contains_numeric and property_node:
        description = str(schema.get("description", "")).rstrip()
        if "INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1" not in description:
            schema["description"] = (description + _FLOAT_DOMAIN_DESCRIPTION).strip()
        schema["x-corrosion-float-domain"] = {
            "rule_id": "INTERNAL_SCREENING_RULE_FLOAT_MAGNITUDE_V1",
            "minimum_nonzero_magnitude": MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE,
            "zero_semantics": "preserved_where_declared_by_field_contract",
        }
    return contains_numeric


class AmbiguousGalvanicMaterialError(CorrosionPublicError, ValueError):
    """A generic material name maps to multiple ASTM G82 table rows."""

    def __init__(self, material: str) -> None:
        choices = ["AA 3003-H", "AA 6061-T"]
        super().__init__(
            "AMBIGUOUS_GALVANIC_MATERIAL",
            f"AMBIGUOUS_GALVANIC_MATERIAL: {material!r} does not identify one "
            f"ASTM G82 aluminum row. Choose exactly one of: {', '.join(choices)}.",
            details={
                "reason_code": "AMBIGUOUS_GALVANIC_MATERIAL",
                "material": material,
                "choices": choices,
            },
        )


def sanitize_message(message: Any) -> str:
    """Return a single-line, path-redacted public message."""

    text = str(message)
    if "Traceback (most recent call last)" in text:
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and not line.lstrip().startswith("File ")
            and "Traceback (most recent call last)" not in line
        ]
        text = lines[-1] if lines else "Internal corrosion tool error"
    text = " | ".join(part.strip() for part in text.splitlines() if part.strip())
    return redact_paths(text)


def error_code_for_exception(exc: BaseException) -> str:
    """Map an exception class to a stable public error code."""

    if isinstance(exc, CorrosionPublicError):
        return exc.code
    name = type(exc).__name__
    if name == "ValidationError":
        return "VALIDATION_ERROR"
    if isinstance(exc, ValueError):
        return "INVALID_INPUT"
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    return "INTERNAL_ERROR"


def public_error(
    exc: BaseException,
    *,
    code: str | None = None,
    details: Mapping[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Convert any exception to a path-redacted public error payload."""

    public_code = code or error_code_for_exception(exc)
    public_details: dict[str, Any] = {"exception_type": type(exc).__name__}
    if isinstance(exc, CorrosionPublicError):
        public_details.update(exc.details)
    if details:
        public_details.update(dict(details))

    # Force traceback formatting through the sanitizer if a caller passes an
    # exception whose stringification already embeds a traceback.
    message = sanitize_message(str(exc))
    if not message or message == "None":
        message = sanitize_message("".join(traceback.format_exception_only(type(exc), exc)))

    return redact_mapping(
        {
            "code": public_code,
            "message": message,
            "details": public_details,
            "correlation_id": correlation_id or uuid.uuid4().hex,
        }
    )


__all__ = [
    "AmbiguousGalvanicMaterialError",
    "CorrosionPublicError",
    "FLOAT_MAGNITUDE_SCREENING_RULE",
    "InputBelowFloatMagnitudeDomainError",
    "MIN_POSITIVE_FLOAT_DOMAIN_MAGNITUDE",
    "NonFiniteArithmeticError",
    "NonFiniteErrorDetailError",
    "UnderflowBelowFloatDomainError",
    "annotate_float_domain_schema",
    "error_code_for_exception",
    "find_non_finite_paths",
    "public_error",
    "redact_mapping",
    "redact_paths",
    "require_representable_nonzero_result",
    "sanitize_message",
    "validate_float_domain_inputs",
]
