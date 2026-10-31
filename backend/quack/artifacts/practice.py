"""Practice loop (ТЗ §2.2): task selection and generation, hint ladder, hybrid evaluation.

The task pick and the answer check cascade live in `quack.practice` (WP6.2): the same code
serves the teacher turn. This module keeps the endpoints and their events.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, LiteralString
from uuid import UUID, uuid5

import orjson
from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.artifacts.schemas import (
    HintRequest,
    HintResponse,
    NextTaskRequest,
    NextTaskResponse,
    SubmitRequest,
    SubmitResponse,
)
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.neo4j import read_session
from quack.events import NewEvent, StoredEvent, append_events
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.graph.topic_display import INACTIVE_SKILL_STATUSES
from quack.llm.gateway import LLMGateway
from quack.practice import selection
from quack.practice.evaluation import (
    LLM_ERRORS,
    TaskToCheck,
    evaluate_answer,
    hint_penalties,
    load_task,
    misconception_lines,
    task_from_row,
)
from quack.practice.generation import GeneratedTaskError, build_generated_task
from quack.practice.schemas import TaskGenOutput

__all__ = ["LLM_ERRORS", "give_hint", "next_task", "submit_answer"]

# Decision 9: the hint when `reference_solution` has no text for the level (no LLM call).
HINT_TEMPLATE = "Hint {level}: think about which rule applies to this task."

# ТЗ §2.2 п.1: the weight of the single target skill of a generated task.
TARGET_SKILL_WEIGHT = 1.0

# Skills of one topic with a live status (A7, A16).
_TOPIC_SKILLS: LiteralString = (
    "MATCH (t:Topic {id: $topic}) WHERE t.learnerId = $l AND t.domainId = $domain "
    "OPTIONAL MATCH (s:Skill)-[:PART_OF]->(t) WHERE s.learnerId = $l AND NOT s.status IN $inactive "
    "RETURN t.name AS topic_name, "
    "collect(s {.id, .name, .pMastery, .masteryStatus, .distinctFamilies}) AS skills"
)

_ASSIGNED_IN_SESSION = (
    "EXISTS (SELECT 1 FROM core.events e WHERE e.learner_id = b.learner_id "
    "AND e.session_id = :s AND e.type = 'task.assigned' "
    "AND e.payload->>'task_id' = CAST(b.id AS TEXT))"
)
_BANK_COLUMNS = "b.id, b.topic_id, b.family_id, b.difficulty, b.kind, b.prompt_text"


# --- shared reads and writes ----------------------------------------------------------


def _key(idempotency_key: str, event_type: str) -> str:
    """A10: the idempotency key of the event written by one request."""
    return f"{idempotency_key}:{event_type}"


def _task_gen_key(session_id: UUID, idempotency_key: str) -> str:
    return f"task_gen:{session_id}:{idempotency_key}"


def _json(value: Any) -> str:
    return orjson.dumps(value).decode()


async def _rows(resources: AppResources, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with resources.session_factory() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _append(
    resources: AppResources, learner_id: UUID, events: Sequence[NewEvent]
) -> list[StoredEvent]:
    async with resources.session_factory() as session, session.begin():
        return await append_events(session, learner_id, events, redis=resources.redis)


async def _stored(resources: AppResources, learner_id: UUID, key: str) -> StoredEvent | None:
    async with resources.session_factory() as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {EVENT_COLUMNS} FROM core.events "
                    "WHERE learner_id = :l AND idempotency_key = :k"
                ),
                {"l": learner_id, "k": key},
            )
        ).first()
    return None if row is None else row_to_event(row)


async def _session_domain(resources: AppResources, learner_id: UUID, session_id: UUID) -> UUID:
    """A12: the session belongs to the learner, else 404."""
    rows = await _rows(
        resources,
        "SELECT domain_id FROM core.sessions WHERE id = :s AND learner_id = :l",
        s=session_id,
        l=learner_id,
    )
    if not rows:
        raise ApiError("NOT_FOUND", "session not found", status_code=404)
    return rows[0]["domain_id"]


async def _assigned_task(
    resources: AppResources, learner_id: UUID, session_id: UUID, task_id: UUID
) -> TaskToCheck:
    """A12: the bank row of a task assigned in this session, else 404."""
    rows = await _rows(
        resources,
        "SELECT b.id, b.topic_id, b.family_id, b.difficulty, b.kind, b.prompt_text, "
        "b.reference_solution, b.target_skills, b.target_misconceptions "
        "FROM core.task_bank b "
        f"WHERE b.id = :t AND b.learner_id = :l AND {_ASSIGNED_IN_SESSION}",
        t=task_id,
        l=learner_id,
        s=session_id,
    )
    if not rows:
        raise ApiError("NOT_FOUND", "task is not assigned in this session", status_code=404)
    return task_from_row(rows[0])


async def _hint_penalties(
    resources: AppResources, learner_id: UUID, session_id: UUID, task_id: UUID
) -> list[tuple[int, float]]:
    """`(hint_level, independence_penalty)` of every `task.hint_given` for the task."""
    async with resources.session_factory() as session:
        return await hint_penalties(session, learner_id, session_id, task_id)


# --- next_task ------------------------------------------------------------------------


async def _topic_skills(
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    resources: AppResources,
    settings: Settings,
) -> tuple[str, list[dict[str, Any]]] | None:
    """Topic name and its live skills from Neo4j, read-only (A16)."""

    async def load() -> tuple[str, list[dict[str, Any]]] | None:
        async with read_session(resources.neo4j) as session:
            result = await session.run(
                _TOPIC_SKILLS,
                {
                    "topic": str(topic_id),
                    "domain": str(domain_id),
                    "l": str(learner_id),
                    "inactive": sorted(INACTIVE_SKILL_STATUSES),
                },
            )
            record = await result.single()
        return None if record is None else (record["topic_name"], list(record["skills"]))

    deps = SliceDeps(
        resources.session_factory,
        resources.redis,
        lambda learner: read_applied_seq(resources.neo4j, str(learner)),
        settings.snapshot_read_retries,
    )
    consistent = await read_consistent_slice(
        learner_id, None, load, settings.projector_lag_timeout_ms, deps=deps
    )
    return consistent.data


def _target_skill(skills: list[dict[str, Any]]) -> dict[str, Any]:
    """A7: not mastered first by `(distinctFamilies != 1, pMastery, id)`; else all skills."""
    ordered = sorted(skills, key=lambda s: (s["distinctFamilies"] != 1, s["pMastery"], s["id"]))
    candidates = [s for s in ordered if s["masteryStatus"] != "mastered"]
    return (candidates or ordered)[0]


def _assigned_event(
    session_id: UUID, body: NextTaskRequest, task_id: UUID, topic_id: UUID
) -> NewEvent:
    return NewEvent(
        type="task.assigned",
        payload={
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
            "topic_id": str(topic_id),
        },
        actor="system",
        idempotency_key=_key(body.idempotency_key, "task.assigned"),
    )


def _bank_response(task: TaskToCheck, *, degraded: bool) -> NextTaskResponse:
    return NextTaskResponse(
        task_id=task.task_id,
        family_id=task.family_id,
        difficulty=task.difficulty,
        kind=task.kind,
        prompt_text=task.prompt_text,
        degraded=degraded,
    )


def _check_replay(stored: dict[str, Any], request: dict[str, Any], endpoint: str) -> None:
    """T-E2, D-C-027: a used key with another body is 409. `request` holds the fields of the
    stored payload the request fixes."""
    if any(stored.get(k) != v for k, v in request.items()):
        raise ApiError(
            "IDEMPOTENCY_CONFLICT",
            f"Idempotency key was used for another {endpoint} request",
            status_code=409,
        )


def _check_same_request(
    body: NextTaskRequest,
    session_id: UUID,
    assigned: dict[str, Any],
    kind: str,
    topic_id: str | None,
) -> None:
    """`tasks/next`: compared is what the stored events fix: session, turn, kind, and the topic
    of a generated task (a bank task is picked by skill, its topic need not be the requested
    one)."""
    stored = {**assigned, "kind": kind}
    request: dict[str, Any] = {
        "session_id": str(session_id),
        "turn_id": str(body.turn_id),
        "kind": body.kind,
    }
    if topic_id is not None:
        stored["topic_id"] = topic_id
        request["topic_id"] = str(body.topic_id)
    _check_replay(stored, request, "tasks/next")


async def _replay_next_task(
    resources: AppResources, learner_id: UUID, session_id: UUID, body: NextTaskRequest
) -> NextTaskResponse | None:
    """A10. `degraded` is not stored in events: a bank task assigned after a failed
    `task_gen@1` call is recognised by its non-completed `core.llm_outbox` row."""
    idempotency_key = body.idempotency_key
    assigned = await _stored(resources, learner_id, _key(idempotency_key, "task.assigned"))
    if assigned is None:
        return None
    generated = await _stored(resources, learner_id, _key(idempotency_key, "task.generated"))
    if generated is not None:
        p = generated.payload
        _check_same_request(body, session_id, assigned.payload, p["kind"], p["topic_id"])
        return NextTaskResponse(
            task_id=p["task_id"],
            family_id=p["family_id"],
            difficulty=p["difficulty"],
            kind=p["kind"],
            prompt_text=p["prompt_text"],
            degraded=False,
        )
    async with resources.session_factory() as session:
        row = await load_task(session, learner_id, UUID(assigned.payload["task_id"]))
    assert row is not None, "task.assigned of a task missing from core.task_bank"
    _check_same_request(body, session_id, assigned.payload, row.kind, None)
    failed = await _rows(
        resources,
        "SELECT 1 FROM core.llm_outbox WHERE learner_id = :l AND idempotency_key = :k "
        "AND status <> 'completed'",
        l=learner_id,
        k=_task_gen_key(session_id, idempotency_key),
    )
    return _bank_response(row, degraded=bool(failed))


async def next_task(
    learner_id: UUID,
    session_id: UUID,
    body: NextTaskRequest,
    resources: AppResources,
    gateway: LLMGateway,
    settings: Settings,
) -> NextTaskResponse:
    """Bank task of a new family or `task_gen@1` (bank fallback); `task.generated` +
    `task.assigned` (ТЗ §2.2 п.1, A6–A8, A10–A12)."""
    domain_id = await _session_domain(resources, learner_id, session_id)
    replay = await _replay_next_task(resources, learner_id, session_id, body)
    if replay is not None:
        return replay

    topic = await _topic_skills(learner_id, domain_id, body.topic_id, resources, settings)
    if topic is None or not topic[1]:
        raise ApiError("NOT_FOUND", "topic has no skills", status_code=404)
    topic_name, skills = topic
    skill = _target_skill(skills)
    skill_id: str = skill["id"]
    async with resources.session_factory() as session:
        excluded = await selection.excluded_families(session, learner_id, skill_id)
        bank = await selection.bank_task_for_skill(
            session, learner_id, session_id, skill_id, body.kind, excluded
        )
    if bank is not None:
        await _append(
            resources,
            learner_id,
            [_assigned_event(session_id, body, bank.task_id, bank.topic_id)],
        )
        return _bank_response(bank, degraded=False)

    task_id = uuid5(QUACK_UUID_NS, f"task:{learner_id}:{session_id}:{body.idempotency_key}")
    try:
        result = await gateway.generate_structured(
            role="task_gen",
            prompt_version=1,
            response_model=TaskGenOutput,
            variables={
                "topic_name": topic_name,
                "target_skill": f"S1: {skill['name']}",
                "target_level": settings.task_gen_target_level,
                "kind": body.kind,
                "known_misconceptions": misconception_lines([]),
                "excluded_families": ", ".join(excluded) if excluded else "none",
            },
            learner_id=learner_id,
            session_id=session_id,
            turn_id=body.turn_id,
            idempotency_key=_task_gen_key(session_id, body.idempotency_key),
        )
        task = build_generated_task(result.value, body.kind, task_id, [])
    except (*LLM_ERRORS, GeneratedTaskError):
        async with resources.session_factory() as session:
            fallback = await selection.fallback_bank_task(
                session, learner_id, session_id, body.topic_id, skill_id, body.kind, excluded
            )
        if fallback is None:
            raise
        await _append(
            resources,
            learner_id,
            [_assigned_event(session_id, body, fallback.task_id, fallback.topic_id)],
        )
        return _bank_response(fallback, degraded=True)

    difficulty = settings.default_target_difficulty
    generated = NewEvent(
        type="task.generated",
        payload={
            "task_id": str(task_id),
            "domain_id": str(domain_id),
            "topic_id": str(body.topic_id),
            "family_id": task.family_id,
            "difficulty": difficulty,
            "kind": body.kind,
            "prompt_text": task.prompt_text,
            "reference_solution": task.reference_solution,
            "target_skills": [{"skill": {"skill_id": skill_id}, "weight": TARGET_SKILL_WEIGHT}],
            "target_misconceptions": task.target_misconceptions,
        },
        actor="task_gen@1",
        idempotency_key=_key(body.idempotency_key, "task.generated"),
    )
    await _append(
        resources,
        learner_id,
        [generated, _assigned_event(session_id, body, task_id, body.topic_id)],
    )
    return NextTaskResponse(
        task_id=task_id,
        family_id=task.family_id,
        difficulty=difficulty,
        kind=body.kind,
        prompt_text=task.prompt_text,
        degraded=False,
    )


# --- give_hint ------------------------------------------------------------------------


def _hint_text(reference_solution: dict[str, Any], level: int) -> str:
    hints = reference_solution.get("hints")
    if isinstance(hints, list) and len(hints) >= level:
        hint = hints[level - 1]
        if isinstance(hint, str) and hint:
            return hint
    return HINT_TEMPLATE.format(level=level)


def _hint_response(event: StoredEvent) -> HintResponse:
    p = event.payload
    return HintResponse(
        task_id=p["task_id"],
        hint_level=p["hint_level"],
        hint_text=p["hint_text"],
        independence_penalty=p["independence_penalty"],
        seq=event.seq,
    )


async def give_hint(
    learner_id: UUID,
    session_id: UUID,
    task_id: UUID,
    body: HintRequest,
    resources: AppResources,
    gateway: LLMGateway,
    settings: Settings,
) -> HintResponse:
    """Next level of the hint ladder; `task.hint_given` (ТЗ §2.2 п.2, decisions 1 and 9)."""
    await _session_domain(resources, learner_id, session_id)
    replay = await _stored(resources, learner_id, _key(body.idempotency_key, "task.hint_given"))
    if replay is not None:
        request = {
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
        }
        _check_replay(replay.payload, request, "hint")
        return _hint_response(replay)

    task = await _assigned_task(resources, learner_id, session_id, task_id)
    given = await _hint_penalties(resources, learner_id, session_id, task_id)
    level = min(settings.max_hint_level, max((lv for lv, _ in given), default=0) + 1)
    event = NewEvent(
        type="task.hint_given",
        payload={
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
            "hint_level": level,
            "hint_text": _hint_text(task.reference_solution, level),
            "independence_penalty": settings.hint_penalties[level - 1],
        },
        actor="system",
        idempotency_key=_key(body.idempotency_key, "task.hint_given"),
    )
    [stored] = await _append(resources, learner_id, [event])
    return _hint_response(stored)


# --- submit_answer --------------------------------------------------------------------


def _submit_response(event: StoredEvent) -> SubmitResponse:
    p = event.payload
    return SubmitResponse(
        task_id=p["task_id"],
        evaluator=p["evaluator"],
        is_correct=p["is_correct"],
        score=p["score"],
        independence=p["independence"],
        feedback=p["feedback"],
        skill_scores=p["skill_scores"],
        triggered_misconceptions=p["triggered_misconceptions"],
        seq=event.seq,
    )


async def submit_answer(
    learner_id: UUID,
    session_id: UUID,
    task_id: UUID,
    body: SubmitRequest,
    resources: AppResources,
    gateway: LLMGateway,
    compute: ComputeClient,
    settings: Settings,
) -> SubmitResponse:
    """Cascade compute → `evaluator@1` → normalized string match; `task.submitted` +
    `task.evaluated` in one transaction (ТЗ §2.2 п.3, decisions 1, 4, 8, A1, A9)."""
    await _session_domain(resources, learner_id, session_id)
    replay = await _stored(resources, learner_id, _key(body.idempotency_key, "task.evaluated"))
    if replay is not None:
        submitted = await _stored(
            resources, learner_id, _key(body.idempotency_key, "task.submitted")
        )
        request = {
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
            "answer": body.answer,
            "response_time_sec": body.response_time_sec,
        }
        _check_replay(submitted.payload if submitted else {}, request, "submit")
        return _submit_response(replay)

    task = await _assigned_task(resources, learner_id, session_id, task_id)
    given = await _hint_penalties(resources, learner_id, session_id, task_id)
    verdict = await evaluate_answer(
        task,
        body.answer,
        [penalty for _, penalty in given],
        learner_id=learner_id,
        session_id=session_id,
        turn_id=body.turn_id,
        idempotency_key=f"evaluator:{session_id}:{task_id}:{body.idempotency_key}",
        gateway=gateway,
        compute=compute,
        settings=settings,
    )

    submitted = NewEvent(
        type="task.submitted",
        payload={
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
            "answer": body.answer,
            "hints_used": len(given),
            "response_time_sec": body.response_time_sec,
        },
        actor="learner",
        idempotency_key=_key(body.idempotency_key, "task.submitted"),
    )
    evaluated = NewEvent(
        type="task.evaluated",
        payload={
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
            "evaluator": verdict.evaluator,
            "is_correct": verdict.is_correct,
            "score": verdict.score,
            "independence": verdict.independence,
            "feedback": verdict.feedback,
            "skill_scores": verdict.skill_scores,
            "triggered_misconceptions": verdict.triggered_misconceptions,
        },
        actor=verdict.actor,
        idempotency_key=_key(body.idempotency_key, "task.evaluated"),
    )
    _, stored = await _append(resources, learner_id, [submitted, evaluated])
    return _submit_response(stored)
