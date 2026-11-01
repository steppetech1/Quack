"""ТЗ Ф4 G1, seams S7, S8, S11: the frozen contracts of stream B build and hold together."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from quack.agents import schemas as agents
from quack.llm.strict_schema import to_strict_json_schema
from quack.practice import role_outputs
from quack.session.actions import ACTION_EVENT_TYPES, TURN_ACTION_KINDS, Action
from quack.session.elements import Element
from quack.session.teacher_output import TeacherOutputV2
from quack.session.tools import TOOLS, tool_definition

ROLE_SCHEMAS: list[type[BaseModel]] = [
    TeacherOutputV2,
    role_outputs.ModelVerdictOutput,
    role_outputs.SolutionSpace,
    role_outputs.TaskGeneratorOutput,
    role_outputs.TaskCheckOutput,
    agents.ExtractorOutputV3,
    agents.AnalystOutput,
    agents.BuilderOutput,
    agents.HygieneOutput,
    agents.MisconceptionDescription,
    agents.TodayPlannerOutput,
    agents.DomainClassifierOutput,
    agents.VizBuilderOutput,
    agents.ReleaseJudgeOutput,
]


def _objects(node: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or node.get("type") == ["object", "null"]:
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_objects(value))
    return found


@pytest.mark.parametrize("model", ROLE_SCHEMAS, ids=lambda m: m.__name__)
def test_role_schemas_are_strict(model: type[BaseModel]) -> None:
    """S8: every role output builds in the strict mode (Т-§11.1): every object closed, all
    properties required, no `$ref` / `oneOf` / `discriminator` left."""
    schema = to_strict_json_schema(model)
    text = str(schema)
    assert "$ref" not in text and "oneOf" not in text and "discriminator" not in text
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])  # type: ignore[arg-type]  # dict


def test_element_union_by_kind() -> None:
    """S11: `Element` is discriminated by `kind`; an unknown kind is rejected."""
    adapter = TypeAdapter(Element)
    element = adapter.validate_python(
        {
            "kind": "task",
            "element_id": "e1",
            "prompt": "Solve",
            "canvas": True,
            "ask_confidence": False,
        }
    )
    assert element.kind == "task"
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "poll", "element_id": "e1"})


def test_action_kinds_cover_wp13_1() -> None:
    """S11: every action kind maps to its event; only `confidence` has no teacher turn."""
    union = get_args(Action.__value__)[0]
    kinds = {get_args(cls.model_fields["kind"].annotation)[0] for cls in get_args(union)}
    assert kinds == set(ACTION_EVENT_TYPES)
    assert set(ACTION_EVENT_TYPES) - TURN_ACTION_KINDS == {"confidence"}
    action = TypeAdapter(Action).validate_python(
        {
            "kind": "confidence",
            "idempotency_key": "6f1c2a9e-4b7d-4e3a-9c21-0d8e7f6a5b43",
            "target_seq": 3,
            "value": 4,
        }
    )
    assert action.kind == "confidence"


def test_tool_definitions() -> None:
    """S11: the six memory tools of Т-§10.4 as strict Responses API functions."""
    names = [t.name for t in TOOLS]
    assert names == [
        "skill_history",
        "similar_mistakes",
        "past_solution",
        "voice_at_line",
        "search_materials",
        "prerequisites",
    ]
    for tool in TOOLS:
        definition = tool_definition(tool)
        assert definition["type"] == "function" and definition["strict"] is True
        assert definition["parameters"]["additionalProperties"] is False
