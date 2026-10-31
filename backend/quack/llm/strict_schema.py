"""JSON Schema of an output model for the strict mode of the Responses API (Т-§11.1, WP5.7).

Every object gets `additionalProperties: false` and all its properties in `required`; an
optional field becomes a type with `null` instead of `anyOf`; `$defs` / `$ref` are inlined.
A model the strict mode cannot express (free dict, untyped field) is an error naming the
field: such fields are turned into lists of objects before gate G1 (§7.2).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StrictSchemaError(ValueError):
    """The output model has a field the strict mode cannot express."""

    def __init__(self, model: str, field_path: str, problem: str) -> None:
        super().__init__(f"{model}.{field_path}: {problem}")
        self.model = model
        self.field_path = field_path
        self.problem = problem


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Strict JSON Schema of `model`; `StrictSchemaError` if it cannot be expressed."""
    raise NotImplementedError
