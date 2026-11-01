"""JSON Schema of an output model for the strict mode of the Responses API (Т-§11.1, WP5.7).

Every object gets `additionalProperties: false` and all its properties in `required`; an
optional field becomes a type with `null` instead of `anyOf`; `$defs` / `$ref` are inlined.
A model the strict mode cannot express (free dict, untyped field) is an error naming the
field: such fields are turned into lists of objects before gate G1 (§7.2).

`title` and `default` are dropped (D-B-005): in the strict mode every property is
required, so a default is never applied by the model; validation keywords (`enum`,
`format`, `minimum`, `maxLength`, …) and `description` are kept.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_DROPPED_KEYS = frozenset({"title", "default"})
_REF_PREFIX = "#/$defs/"


class StrictSchemaError(ValueError):
    """The output model has a field the strict mode cannot express."""

    def __init__(self, model: str, field_path: str, problem: str) -> None:
        super().__init__(f"{model}.{field_path}: {problem}")
        self.model = model
        self.field_path = field_path
        self.problem = problem


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Strict JSON Schema of `model`; `StrictSchemaError` if it cannot be expressed."""
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})
    return _Converter(model.__name__, defs).node(raw, "")


class _Converter:
    def __init__(self, model: str, defs: dict[str, Any]) -> None:
        self.model = model
        self.defs = defs

    def error(self, path: str, problem: str) -> StrictSchemaError:
        return StrictSchemaError(self.model, path or "<root>", problem)

    def node(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        schema = self._inline_ref(schema, path)
        if "anyOf" in schema:
            return self._any_of(schema, path)
        result = {k: v for k, v in schema.items() if k not in _DROPPED_KEYS}
        kind = result.get("type")
        if kind == "object":
            return self._object(result, path)
        if kind == "array":
            if "items" not in result:
                raise self.error(path, "list without an item type")
            result["items"] = self.node(result["items"], f"{path}[]")
            return result
        if kind is None and "enum" not in result and "const" not in result:
            raise self.error(path, "field without a type")
        return result

    def _inline_ref(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        ref = schema.get("$ref")
        if ref is None:
            return schema
        if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
            raise self.error(path, f"unsupported $ref {ref!r}")
        target = self.defs.get(ref.removeprefix(_REF_PREFIX))
        if target is None:
            raise self.error(path, f"unknown $ref {ref!r}")
        siblings = {k: v for k, v in schema.items() if k != "$ref"}
        return {**copy.deepcopy(target), **siblings}

    def _object(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        extra = schema.get("additionalProperties")
        if "properties" not in schema or (extra is not None and extra is not False):
            raise self.error(path, "free dict: use a list of objects")
        properties = {
            name: self.node(prop, f"{path}.{name}" if path else name)
            for name, prop in schema["properties"].items()
        }
        schema["properties"] = properties
        schema["required"] = list(properties)
        schema["additionalProperties"] = False
        return schema

    def _any_of(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        nullable = len(options) != len(schema["anyOf"])
        siblings = {k: v for k, v in schema.items() if k != "anyOf" and k not in _DROPPED_KEYS}
        if not nullable:
            return {**siblings, "anyOf": [self.node(o, path) for o in options]}
        if len(options) != 1:
            raise self.error(path, "optional union of several types")
        inner = self.node({**options[0], **siblings}, path)
        return _with_null(inner, self.error(path, "optional field without a plain type"))


def _with_null(schema: dict[str, Any], error: StrictSchemaError) -> dict[str, Any]:
    kind = schema.get("type")
    if isinstance(kind, str):
        schema["type"] = [kind, "null"]
    elif isinstance(kind, list) and kind:
        if "null" not in kind:
            schema["type"] = [*kind, "null"]
    else:
        raise error
    if "enum" in schema and None not in schema["enum"]:
        schema["enum"] = [*schema["enum"], None]
    return schema
