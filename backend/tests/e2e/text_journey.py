"""Steps of the text journey of the learner (ТЗ Ф4 §8.14, T-E1) shared by the e2e tests: the
scripted teacher turn, the session, an action, and the journey up to the answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx

from tests.e2e.journey import ok, onboard, sse_events
from tests.e2e.system import Learner, System

SCHEMA = "TeacherOutputV2"
TEXT = "Every term is a third of the one before it."
NUMBER = {"type": "number", "value": {"latex": r"\frac{1}{3}", "sympy": "1/3"}}


def teacher_turn(*, with_task: bool) -> dict[str, Any]:
    """`teacher@2` output: the head, the body and the markup; one task as an element."""
    blocks: list[dict[str, Any]] = [{"block_id": "b1", "text": TEXT, "element_id": None}]
    tasks: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    if with_task:
        blocks.append({"block_id": "b2", "text": None, "element_id": "e1"})
        tasks.append(
            {
                "element_id": "e1",
                "tests": [{"skill": "S1", "level": 2}],
                "methodNamed": False,
                "answer": NUMBER,
            }
        )
        elements.append(
            {
                "element_id": "e1",
                "kind": "short_answer",
                "prompt": "What is the ratio of the series?",
                "ask_confidence": False,
            }
        )
    return {
        "p1_head": {
            "intent": "explain",
            "targetSkills": ["S1"],
            "verdict_stance": None,
            "branchType": None,
            "topicLabel": None,
            "approach": "example_first",
        },
        "p2_body": {"blocks": blocks},
        "p3_markup": {
            "tasks": tasks,
            "elements": elements,
            "exposures": [{"skill": "S1", "kind": "explained", "approach": "example_first"}],
            "targetsMisconception": None,
            "planUpdate": {
                "completed_steps": [],
                "current_step": "geometric sum",
                "next_steps": [],
                "branches": [],
                "cancel_requests": [],
            },
            "agentRequests": [],
            "correctionsAcknowledged": [],
            "decisionReason": "explain first",
            "deadline": None,
        },
    }


async def event_types(system: System, learner: Learner) -> list[tuple[int, str]]:
    rows = await system.resources.db.fetch_all(
        "SELECT seq, type FROM core.events WHERE learner_id = %(learner_id)s ORDER BY seq",
        {"learner_id": learner.learner_id},
    )
    return [(int(r["seq"]), str(r["type"])) for r in rows]


async def start_session(
    system: System, learner: Learner, domain_id: UUID, topic_id: UUID | None = None
) -> UUID:
    started = ok(
        await system.client.post(
            "/v1/sessions",
            json={
                "domain_id": str(domain_id),
                "intent": "learn",
                "topic_id": None if topic_id is None else str(topic_id),
                "idempotency_key": str(uuid4()),
            },
            headers=learner.headers,
        ),
        201,
    )
    return UUID(started["session_id"])


async def act(
    system: System, learner: Learner, session_id: UUID, body: dict[str, Any]
) -> httpx.Response:
    return await system.client.post(
        f"/v1/sessions/{session_id}/actions",
        json={"idempotency_key": str(uuid4()), **body},
        headers=learner.headers,
    )


def script_task_checker(system: System) -> None:
    """The checker of the task (`task.check`, a job after `task.created`) agrees with the key."""
    system.llm.respond(
        "TaskCheckOutput",
        lambda _r: {
            "answer_text": "1/3",
            "answer_math": {"latex": r"\frac{1}{3}", "sympy": "1/3"},
            "methodNamed": False,
            "is_variant": None,
            "levels_fit": [True],
            "note": "the ratio is a third",
        },
    )


def script_teacher(system: System, pattern: tuple[bool, ...] = (True,)) -> None:
    """The turns offer a task where `pattern` says so; the turns after it only talk."""
    turns = iter([teacher_turn(with_task=with_task) for with_task in pattern])
    system.llm.respond(SCHEMA, lambda _r: next(turns, teacher_turn(with_task=False)))


def script_extractor(system: System, *, events: list[int], skill: str = "S1") -> None:
    """`extractor@3` finds the correct answer at level 1 (level 2 has the code verdict)."""
    system.llm.respond(
        "ExtractorOutputV3",
        lambda _r: {
            "elements": [
                {
                    "kind": "evidence",
                    "skill": skill,
                    "new_skill": None,
                    "skill_unclear": False,
                    "level": 1,
                    "outcome": "correct",
                    "help": "none",
                    "hint_step": None,
                    "gap_type": "none",
                    "misconception": None,
                    "new_misconception": None,
                    "root_cause": None,
                    "quote": "1/3",
                    "events": events,
                }
            ]
        },
    )


@dataclass(frozen=True)
class Journey:
    learner: Learner
    domain_id: UUID
    topic_id: UUID
    session_id: UUID
    task_id: UUID
    answer_seq: int
    answer_frames: list[tuple[str, dict[str, Any]]]


async def run_text_journey(system: System, *, extractor: bool = True) -> Journey:
    """Sign-in by the link → a domain with a topic → a session on the topic → a message (the
    turn offers a task) → a correct answer (the instant verdict, the second turn). With
    `extractor` the scripted extractor answers once the episode closes."""
    script_task_checker(system)
    script_teacher(system)
    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, f"journey:{uuid4()}")
    session_id = await start_session(system, learner, domain_id, topic_id)

    message = await act(
        system, learner, session_id, {"kind": "message", "text": "Explain the sum of a series"}
    )
    frames = sse_events(message)
    assert frames[0][0] == "action_recorded" and "error" not in [n for n, _ in frames], frames
    task = await system.resources.db.fetch_one(
        "SELECT task_id FROM content.tasks WHERE learner_id = %(learner_id)s",
        {"learner_id": learner.learner_id},
    )
    assert task is not None, await event_types(system, learner)

    answered = await act(
        system,
        learner,
        session_id,
        {
            "kind": "answer",
            "task_id": str(task["task_id"]),
            "answer": {"kind": "number", "value": "1/3"},
            "text": "I think the ratio is 1/3",
        },
    )
    answer_frames = sse_events(answered)
    assert "error" not in [n for n, _ in answer_frames], answer_frames
    recorded = answer_frames[0][1]
    assert answer_frames[0][0] == "action_recorded" and recorded["verdict"] is not None
    answer_seq = int(recorded["seq"])
    if extractor:
        script_extractor(system, events=[answer_seq])
    return Journey(
        learner, domain_id, topic_id, session_id, task["task_id"], answer_seq, answer_frames
    )


async def answer_latest_task(
    system: System, learner: Learner, session_id: UUID, value: str = "1/3"
) -> list[tuple[str, dict[str, Any]]]:
    """A number answer to the newest task of the learner; the frames of the turn."""
    task = await system.resources.db.fetch_one(
        "SELECT task_id FROM content.tasks WHERE learner_id = %(learner_id)s "
        "ORDER BY created_seq DESC LIMIT 1",
        {"learner_id": learner.learner_id},
    )
    assert task is not None
    answered = await act(
        system,
        learner,
        session_id,
        {
            "kind": "answer",
            "task_id": str(task["task_id"]),
            "answer": {"kind": "number", "value": value},
            "text": f"I think the ratio is {value}",
        },
    )
    frames = sse_events(answered)
    assert "error" not in [n for n, _ in frames], frames
    return frames
