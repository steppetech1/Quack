"""E2E: the learner's text journey (ТЗ Ф4 §8.14, T-E1; WP26.1).

Sign-in by the link of the mail → terms → a domain (`POST /v1/domains`) → a session → a message
(the teacher turn: head, body, a task as an element) → an answer (the instant verdict) →
`task.closed` → `episode.closed` → `extract.episode` → the fold → the topic with its state code.
The processes of Phase 4 run in the stand (`tests/e2e/system.py`); the model is scripted."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx

from tests.e2e.journey import ok, onboard, sse_events
from tests.e2e.system import Learner, System

SCHEMA = "TeacherOutputV2"
TEXT = "Every term is a third of the one before it."
NUMBER = {"type": "number", "value": {"latex": "\frac{1}{3}", "sympy": "1/3"}}


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


async def test_learner_journey_text(system: System) -> None:
    """T-E1."""
    system.llm.respond(
        "TaskCheckOutput",
        lambda _r: {
            "answer_text": "1/3",
            "answer_math": {"latex": "\frac{1}{3}", "sympy": "1/3"},
            "methodNamed": False,
            "is_variant": None,
            "levels_fit": [True],
            "note": "the ratio is a third",
        },
    )
    turns = iter([teacher_turn(with_task=True)])
    system.llm.respond(SCHEMA, lambda _r: next(turns, teacher_turn(with_task=False)))

    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te1")
    session_id = await start_session(system, learner, domain_id, topic_id)

    message = await act(
        system,
        learner,
        session_id,
        {"kind": "message", "text": "Explain the sum of a geometric series"},
    )
    frames = sse_events(message)
    names = [name for name, _ in frames]
    assert names[0] == "action_recorded", names
    assert "error" not in names, frames
    task_row = await system.resources.db.fetch_one(
        "SELECT task_id FROM content.tasks WHERE learner_id = %(learner_id)s",
        {"learner_id": learner.learner_id},
    )
    assert task_row is not None, await event_types(system, learner)
    task_id = task_row["task_id"]

    answered = await act(
        system,
        learner,
        session_id,
        {
            "kind": "answer",
            "task_id": str(task_id),
            "answer": {"kind": "number", "value": "1/3"},
            "text": "I think the ratio is 1/3",
        },
    )
    answer_frames = sse_events(answered)
    assert "error" not in [name for name, _ in answer_frames], answer_frames
    recorded = answer_frames[0][1]
    assert answer_frames[0][0] == "action_recorded" and recorded["verdict"] is not None
    answer_seq = int(recorded["seq"])
    system.llm.respond(
        "ExtractorOutputV3",
        lambda _r: {
            "elements": [
                {
                    "kind": "evidence",
                    "skill": "S1",
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
                    "events": [answer_seq],
                }
            ]
        },
    )
    await system.settle()
    types = await event_types(system, learner)
    log = ", ".join(f"{seq}:{t}" for seq, t in types)
    chain = [t for _, t in types if t in {"verdict.recorded", "task.closed", "episode.closed"}]
    assert chain[:3] == ["verdict.recorded", "task.closed", "episode.closed"], log
    assert "observation.extracted" in [t for _, t in types], log

    jobs = await system.resources.db.fetch_all(
        "SELECT kind, status, required_seq FROM ops.jobs WHERE learner_id = %(learner_id)s "
        "AND kind = 'extract.episode'",
        {"learner_id": learner.learner_id},
    )
    assert [(j["status"]) for j in jobs] == ["succeeded"], jobs
    required = jobs[0]["required_seq"]
    started = [s for s in system.job_starts if s.kind == "extract.episode"]
    assert started and all(s.applied_seq >= (required or 0) for s in started), (started, required)

    card = ok(await system.client.get(f"/v1/topics/{topic_id}", headers=learner.headers))
    async with system.neo4j.session() as graph:
        result = await graph.run(
            "MATCH (s:Skill {learnerId: $learner}) RETURN properties(s) AS p",
            learner=str(learner.learner_id),
        )
        skill_props = [dict(r["p"]) for r in await result.data()]
    obs = await system.resources.db.fetch_one(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'observation.extracted'",
        {"learner_id": learner.learner_id},
    )
    assert obs is not None
    marks = await system.resources.db.fetch_all(
        "SELECT 'offset' AS what, seq::text AS v FROM ops.projector_offsets "
        "UNION ALL SELECT 'failure', error FROM ops.projector_failures "
        "UNION ALL SELECT 'learner', seq::text FROM core.learners"
    )
    detail = json.dumps([card["places"], skill_props, obs["payload"], marks, log], default=str)
    # The extractor's element stands on level 1: level 2 of the answer has the code verdict.
    assert len(obs["payload"]["elements"]) == 1 and not obs["payload"]["rejected"], detail
    assert card["status"] == "active", detail
    assert card["display"]["display_state"] in {"not_tested", "in_progress"}, detail
    [skill] = skill_props
    assert skill["l2SuccessW"] > 0 and skill["l1SuccessW"] > 0, detail
    assert {p["kind"] for p in card["places"]} >= {"exposure", "evidence"}, detail
