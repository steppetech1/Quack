"""LLM layer of Phase 3 (ТЗ Фазы 3 §8.4)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from quack.artifacts.schemas import ArtifactGenOutput, EvaluatorOutput, TaskGenOutput
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.strict_schema import StrictSchemaError, to_strict_json_schema
from quack.session.schemas import ExtractorOutput, TeacherOutput

# Output models of the eight roles of Phase 3 after WP13 (§7.6).
ROLE_OUTPUT_MODELS: tuple[type[BaseModel], ...] = (
    StructurerOutput,
    DiagnosticOutput,
    CuratorOutput,
    TeacherOutput,
    ExtractorOutput,
    TaskGenOutput,
    EvaluatorOutput,
    ArtifactGenOutput,
)


def _walk(node: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    """All dict nodes of a JSON Schema with their paths."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        found.append((path, node))
        for key, value in node.items():
            found.extend(_walk(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_walk(value, f"{path}[{i}]"))
    return found


def _assert_strict(schema: dict[str, Any]) -> None:
    for path, node in _walk(schema):
        assert "$ref" not in node, f"{path}: $ref is not inlined"
        assert "$defs" not in node, f"{path}: $defs is not inlined"
        for option in node.get("anyOf", []):
            assert option.get("type") != "null", f"{path}: optional field as anyOf with null"
        is_object = node.get("type") == "object" or (
            isinstance(node.get("type"), list) and "object" in node["type"]
        )
        if is_object or "properties" in node:
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties"
            properties = set(node.get("properties", {}))
            assert set(node.get("required", [])) == properties, f"{path}: required != properties"


@pytest.mark.parametrize("model", ROLE_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_strict_schema_preprocessor(model: type[BaseModel]) -> None:
    """T-L9 (structure): every object closed, every property required, no $ref/$defs, no
    anyOf with null, for the output models of all eight roles."""
    _assert_strict(to_strict_json_schema(model))


def test_strict_schema_optional_field_is_type_with_null() -> None:
    """T-L9: an optional field is a type with `null`, still listed in `required`."""

    class Optional_(BaseModel):
        note: str | None = None
        count: int

    schema = to_strict_json_schema(Optional_)
    assert schema["properties"]["note"]["type"] == ["string", "null"]
    assert set(schema["required"]) == {"note", "count"}
    assert schema["additionalProperties"] is False


def test_strict_schema_inlines_nested_models() -> None:
    """T-L9: nested models are inlined in place of `$ref`."""

    class Inner(BaseModel):
        value: int

    class Outer(BaseModel):
        items: list[Inner]
        single: Inner | None = None

    schema = to_strict_json_schema(Outer)
    _assert_strict(schema)
    assert schema["properties"]["items"]["items"]["properties"]["value"]["type"] == "integer"
    assert schema["properties"]["single"]["type"] == ["object", "null"]


def test_strict_schema_rejects_free_dict_with_field_name() -> None:
    """T-L9: a model with a free dict is a preprocessor error naming the field."""

    class FreeDict(BaseModel):
        title: str
        payload: dict[str, Any]

    with pytest.raises(StrictSchemaError) as exc:
        to_strict_json_schema(FreeDict)
    assert "payload" in exc.value.field_path
