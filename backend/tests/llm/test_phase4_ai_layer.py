"""ТЗ Ф4 §8.7: AI layer — role table and streaming / tool loop units (T-AI1–T-AI4)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from quack.config import Settings
from quack.llm.streaming import (
    BlockText,
    HeadClosed,
    MarkupClosed,
    TurnStreamParser,
    next_step_input,
)
from quack.llm.strict_schema import to_strict_json_schema
from quack.session.teacher_output import TeacherOutputV2

WONDER = "muse-wonder-1.0"
SPARK = "muse-spark-1.3"

# Т-§11.1 (model, effort, max_output_tokens) and Т-§11.3 (priority class), one row per role.
T111: dict[str, tuple[str, str | None, int | None, str]] = {
    "teacher": (WONDER, "low", 8192, "P0"),
    "teacher_fallback": (SPARK, "low", 4096, "P0"),
    "extractor": (SPARK, "high", 4096, "P2"),
    "verdict_free": (SPARK, "high", 4096, "P1"),
    "verdict_proof": (WONDER, "high", 8192, "P1"),
    "second_opinion": (WONDER, "high", 8192, "P1"),
    "canvas_diagnoser": (SPARK, "low", 4096, "P0"),
    "recognizer": (SPARK, "low", 4096, "P0"),
    "segmenter": ("sam-3.1", None, None, "P1"),
    "voice": ("muse-voice-transcribe-1.0", None, None, "P1"),
    "analyst": (SPARK, "high", 4096, "P2"),
    "analyst_deep": (WONDER, "high", 8192, "P2"),
    "builder": (SPARK, "high", 4096, "P2"),
    "material_parser": (SPARK, "high", 4096, "P2"),
    "task_generator": (SPARK, "high", 4096, "P2"),
    "task_checker": (SPARK, "high", 4096, "P1"),
    "task_arbiter": (WONDER, "high", 8192, "P2"),
    "solution_space": (SPARK, "high", 4096, "P2"),
    "viz_builder": (WONDER, "high", 16384, "P2"),
    "viz_checker": (SPARK, "low", 4096, "P2"),
    "misconception_describer": (SPARK, "high", 4096, "P3"),
    "artifact_writer": (SPARK, "high", 4096, "P2"),
    "today_planner": (SPARK, "high", 4096, "P2"),
    "domain_classifier": (SPARK, "low", 4096, "P1"),
    "release_judge": (WONDER, "high", 8192, "P3"),
    "illustrator": ("muse-image-1.0", None, None, "P2"),
}


def test_role_table_matches_t111() -> None:
    """T-AI1: 26 roles — model, effort, max_output_tokens, class as Т-§11.1 / Т-§11.3."""
    roles = Settings().llm_roles
    assert len(T111) == 26
    assert set(roles) == set(T111)
    for name, (model, effort, max_out, klass) in T111.items():
        row = roles[name]
        assert (row.model, row.effort, row.max_output_tokens, row.priority_class) == (
            model,
            effort,
            max_out,
            klass,
        ), name
        assert (row.prompt is None) == (effort is None), name
    assert roles["teacher_fallback"].prompt == roles["teacher"].prompt == "teacher@2"
    assert roles["analyst_deep"].prompt == roles["analyst"].prompt
    assert all(r.max_output_tokens is None or r.max_output_tokens <= 16384 for r in roles.values())


# --- T-AI2 ------------------------------------------------------------------------------


def _turn(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "p1_head": {
            "approach": "example_first",
            "branchType": None,
            "intent": "explain",
            "targetSkills": ["S1"],
            "verdict_stance": None,
        },
        "p2_body": {"blocks": blocks},
        "p3_markup": {
            "agentRequests": [],
            "correctionsAcknowledged": [],
            "deadline": None,
            "decisionReason": "first contact",
            "elements": [],
            "exposures": [{"approach": "example_first", "kind": "explained", "skill": "S1"}],
            "planUpdate": {
                "branches": [],
                "cancel_requests": [],
                "completed_steps": [],
                "current_step": "idea",
                "next_steps": ["example"],
            },
            "targetsMisconception": None,
            "tasks": [],
        },
    }


BLOCKS = [
    {"block_id": "b1", "element_id": None, "text": 'Ratio test: look at "a_{n+1}/a_n".'},
    {"block_id": "b2", "element_id": None, "text": "If the limit is below 1, \\sum converges."},
]


def _feed(parser: TurnStreamParser, text: str, size: int) -> list[Any]:
    events: list[Any] = []
    for i in range(0, len(text), size):
        events.extend(parser.feed(text[i : i + size]))
    return events


@pytest.mark.parametrize("size", [1, 3, 7, 64, 10_000])
def test_stream_structured_detects_head_close(size: int) -> None:
    """T-AI2: the close of `p1_head` is reported before any body text; block texts arrive
    in pieces whose concatenation is the decoded text; `p3_markup` close is reported."""
    text = json.dumps(_turn(BLOCKS))
    parser = TurnStreamParser()
    events = _feed(parser, text, size)
    kinds = [type(e) for e in events]
    assert kinds[0] is HeadClosed
    assert kinds.count(HeadClosed) == 1
    assert json.loads(events[0].head) == _turn(BLOCKS)["p1_head"]
    assert kinds[-1] is MarkupClosed
    assert parser.body_before_head is False
    for index, block in enumerate(BLOCKS):
        got = "".join(e.text for e in events if isinstance(e, BlockText) and e.index == index)
        assert got == block["text"]
    assert parser.close() == text
    TeacherOutputV2.model_validate_json(parser.close())


def test_stream_body_before_head_is_flagged() -> None:
    """T-AI2: body before head → `body_before_head`, head still reported once."""
    turn = _turn(BLOCKS)
    text = json.dumps({"p2_body": turn["p2_body"], "p1_head": turn["p1_head"]})
    parser = TurnStreamParser()
    events = _feed(parser, text, 5)
    assert parser.body_before_head is True
    assert sum(isinstance(e, HeadClosed) for e in events) == 1


def test_teacher_schema_keys_order_by_name() -> None:
    """Т-§12.2: the alphabetical order of the top keys puts the head first."""
    keys = list(to_strict_json_schema(TeacherOutputV2)["properties"])
    assert keys == sorted(keys) == ["p1_head", "p2_body", "p3_markup"]


# --- T-AI3, T-AI4 -----------------------------------------------------------------------

REASONING = {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc==", "summary": []}
COMMENTARY = {
    "type": "message",
    "id": "msg_1",
    "phase": "commentary",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "Let me look at the history."}],
}
CALL_A = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_a",
    "name": "skill_history",
    "arguments": '{"skill":"S1","level":null}',
}
CALL_B = {
    "type": "function_call",
    "id": "fc_2",
    "call_id": "call_b",
    "name": "prerequisites",
    "arguments": '{"skill":"S1"}',
}


def test_tool_loop_passes_reasoning() -> None:
    """T-AI3: the next step gets `reasoning` and `function_call` unchanged and a
    `function_call_output` per `call_id`, in the order of the calls."""
    previous = ({"role": "user", "content": "context"},)
    step = (REASONING, COMMENTARY, CALL_A, CALL_B)
    items = next_step_input(previous, step, {"call_b": "no prerequisites", "call_a": "2 times"})
    assert list(items) == [
        previous[0],
        REASONING,
        CALL_A,
        CALL_B,
        {"type": "function_call_output", "call_id": "call_a", "output": "2 times"},
        {"type": "function_call_output", "call_id": "call_b", "output": "no prerequisites"},
    ]


def test_commentary_not_returned() -> None:
    """T-AI4 (input side): a `phase: commentary` message never goes back to the input."""
    items = next_step_input((), (COMMENTARY, CALL_A), {"call_a": "x"})
    assert all(item.get("phase") != "commentary" for item in items)
    assert COMMENTARY not in items
