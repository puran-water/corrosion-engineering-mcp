"""Helpers for the flat public MCP tool contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import CorrosionPublicError, validate_float_domain_inputs

ModelT = TypeVar("ModelT", bound=BaseModel)


class NestedParamsError(CorrosionPublicError, ValueError):
    """Raised when a caller uses the retired ``{"params": {...}}`` shape."""

    def __init__(self) -> None:
        super().__init__(
            "NESTED_PARAMS_REJECTED",
            "Nested {'params': {...}} tool arguments are no longer supported; pass flat arguments.",
        )


def reject_nested_params(arguments: Mapping[str, Any]) -> None:
    """Reject the retired nested MCP argument shape."""

    if "params" in arguments:
        raise NestedParamsError()


def build_tool_input(
    model: type[ModelT],
    arguments: Mapping[str, Any] | None = None,
    /,
    **kwargs: Any,
) -> ModelT:
    """Validate flat tool arguments with a Pydantic input model."""

    data: dict[str, Any] = dict(arguments or {})
    data.update(kwargs)
    reject_nested_params(data)
    validate_float_domain_inputs(data)
    try:
        return model.model_validate(data)
    except ValidationError:
        raise


def model_to_public_dict(value: Any) -> Any:
    """Serialize Pydantic outputs to JSON-compatible dictionaries."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [model_to_public_dict(item) for item in value]
    if isinstance(value, tuple):
        return [model_to_public_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: model_to_public_dict(item) for key, item in value.items()}
    return value


__all__ = [
    "NestedParamsError",
    "build_tool_input",
    "model_to_public_dict",
    "reject_nested_params",
]
