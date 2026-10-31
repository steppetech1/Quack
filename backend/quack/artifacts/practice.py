"""Practice loop (ТЗ §2.2): task selection and generation, hint ladder, hybrid evaluation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, LiteralString
from uuid import UUID, uuid5

import orjson
from neo4j import READ_ACCESS
from pydantic import ValidationError
from sqlalchemy import text

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.artifacts.params import PARAMS
from quack.artifacts.schemas import (
    EvaluatorOutput,
    HintRequest,
    HintResponse,
    NextTaskRequest,
    NextTaskResponse,
    SubmitRequest,
    SubmitResponse,
    TaskGenOutput,
)
from quack.compute_client.client import (
    ComputeClient,
    ComputeUnavailableError,
    MathCheckRequest,
    MathCheckResponse,
)
from quack.config import Settings
from quack.events import NewEvent, StoredEvent, append_events
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.graph.topic_display import INACTIVE_SKILL_STATUSES
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway

# Errors after which practice switches to its deterministic fallbacks (Т-§22.7, decision 4).
LLM_ERRORS = (LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError)

# Decision 9: the hint when `reference_solution` has no text for the level (no LLM call).
HINT_TEMPLATE = "Hint {level}: think about which rule applies to this task."
# Feedback of verdicts given without the LLM (compute, choice, decision 4).
FEEDBACK_CORRECT = "Correct: your answer matches the reference answer."
FEEDBACK_INCORRECT = "Not quite: your answer does not match the reference answer."

# ТЗ §2.2 п.1: the weight of the single target skill of a generated task.
TARGET_SKILL_WEIGHT = 1.0

# Skills of one topic with a live status (A7, A16).
_TOPIC_SKILLS: LiteralString = (
    "MATCH (t:Topic {id: $topic}) WHERE t.learnerId = $l AND t.domainId = $domain "
    "OPTIONAL MATCH (s:Skill)-[:PART_OF]->(t) WHERE s.learnerId = $l AND NOT s.status IN $inactive "
    "RETURN t.name AS topic_name, "
    "collect(s {.id, .name, .pMastery, .masteryStatus, .distinctFamilies}) AS skills"
)

# Correlated conditions on a `core.task_bank b` row.
_ASSIGNED_IN_SESSION = (
    "EXISTS (SELECT 1 FROM core.events e WHERE e.learner_id = b.learner_id "
    "AND e.session_id = :s AND e.type = 'task.assigned' "
    "AND e.payload->>'task_id' = CAST(b.id AS TEXT))"
)
_EVALUATED = (
    "EXISTS (SELECT 1 FROM core.events e WHERE e.learner_id = b.learner_id "
    "AND e.type = 'task.evaluated' AND e.payload->>'task_id' = CAST(b.id AS TEXT))"
)
_NEW_FAMILY_FOR_SKILL = (
    "(b.target_skills @> CAST(:target AS JSONB) AND NOT (b.family_id = ANY(:excluded)))"
)
_BANK_COLUMNS = "b.id, b.topic_id, b.family_id, b.difficulty, b.kind, b.prompt_text"


@dataclass(frozen=True)
class _Verdict:
    evaluator: str
    is_correct: bool
    score: float
    feedback: str
    skill_scores: list[dict[str, Any]]
    triggered_misconceptions: list[dict[str, Any]]
    actor: str


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
) -> dict[str, Any]:
    """A12: the bank row of a task assigned in this session, else 404."""
    rows = await _rows(
        resources,
        "SELECT b.kind, b.prompt_text, b.reference_solution, b.target_skills, "
        "b.target_misconceptions FROM core.task_bank b "
        f"WHERE b.id = :t AND b.learner_id = :l AND {_ASSIGNED_IN_SESSION}",
        t=task_id,
        l=learner_id,
        s=session_id,
    )
    if not rows:
        raise ApiError("NOT_FOUND", "task is not assigned in this session", status_code=404)
    return rows[0]


async def _hint_penalties(
    resources: AppResources, learner_id: UUID, session_id: UUID, task_id: UUID
) -> list[tuple[int, float]]:
    """`(hint_level, independence_penalty)` of every `task.hint_given` for the task."""
    rows = await _rows(
        resources,
        "SELECT payload FROM core.events WHERE learner_id = :l AND session_id = :s "
        "AND type = 'task.hint_given' AND payload->>'task_id' = :t ORDER BY seq",
        l=learner_id,
        s=session_id,
        t=str(task_id),
    )
    return [
        (int(r["payload"]["hint_level"]), float(r["payload"]["independence_penalty"])) for r in rows
    ]


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
        async with resources.neo4j.session(default_access_mode=READ_ACCESS) as session:
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


async def _excluded_families(resources: AppResources, learner_id: UUID, skill_id: str) -> list[str]:
    """A8: families of tasks with a `task.evaluated` that scores this skill."""
    rows = await _rows(
        resources,
        "SELECT DISTINCT b.family_id FROM core.events e JOIN core.task_bank b "
        "ON b.learner_id = e.learner_id AND CAST(b.id AS TEXT) = e.payload->>'task_id' "
        "WHERE e.learner_id = :l AND e.type = 'task.evaluated' "
        "AND e.payload->'skill_scores' @> CAST(:score AS JSONB) ORDER BY b.family_id",
        l=learner_id,
        score=_json([{"skill_id": skill_id}]),
    )
    return [r["family_id"] for r in rows]


def _target_pattern(skill_id: str) -> str:
    return _json([{"skill": {"skill_id": skill_id}}])


async def _bank_task_for_skill(
    resources: AppResources,
    learner_id: UUID,
    session_id: UUID,
    skill_id: str,
    kind: str,
    excluded: list[str],
) -> dict[str, Any] | None:
    """ТЗ §2.2 п.1, A8: an unsolved task of a new family for the skill, earliest first."""
    rows = await _rows(
        resources,
        f"SELECT {_BANK_COLUMNS} FROM core.task_bank b "
        f"WHERE b.learner_id = :l AND b.kind = :kind AND {_NEW_FAMILY_FOR_SKILL} "
        f"AND NOT {_ASSIGNED_IN_SESSION} AND NOT {_EVALUATED} "
        "ORDER BY b.created_seq LIMIT 1",
        l=learner_id,
        s=session_id,
        kind=kind,
        target=_target_pattern(skill_id),
        excluded=excluded,
    )
    return rows[0] if rows else None


async def _fallback_bank_task(
    resources: AppResources,
    learner_id: UUID,
    session_id: UUID,
    topic_id: UUID,
    skill_id: str,
    kind: str,
    excluded: list[str],
) -> dict[str, Any] | None:
    """Any task of the topic and kind not assigned in the session: a new family for the
    skill first, then unsolved before solved, then by `created_seq` (Т-§22.7)."""
    rows = await _rows(
        resources,
        f"SELECT {_BANK_COLUMNS} FROM core.task_bank b "
        "WHERE b.learner_id = :l AND b.topic_id = :topic AND b.kind = :kind "
        f"AND NOT {_ASSIGNED_IN_SESSION} "
        f"ORDER BY {_NEW_FAMILY_FOR_SKILL} DESC, {_EVALUATED}, b.created_seq LIMIT 1",
        l=learner_id,
        s=session_id,
        topic=topic_id,
        kind=kind,
        target=_target_pattern(skill_id),
        excluded=excluded,
    )
    return rows[0] if rows else None


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


def _bank_response(row: dict[str, Any], *, degraded: bool) -> NextTaskResponse:
    return NextTaskResponse(
        task_id=row["id"],
        family_id=row["family_id"],
        difficulty=row["difficulty"],
        kind=row["kind"],
        prompt_text=row["prompt_text"],
        degraded=degraded,
    )


async def _replay_next_task(
    resources: AppResources, learner_id: UUID, session_id: UUID, idempotency_key: str
) -> NextTaskResponse | None:
    """A10. `degraded` is not stored in events: a bank task assigned after a failed
    `task_gen@1` call is recognised by its non-completed `core.llm_outbox` row."""
    assigned = await _stored(resources, learner_id, _key(idempotency_key, "task.assigned"))
    if assigned is None:
        return None
    generated = await _stored(resources, learner_id, _key(idempotency_key, "task.generated"))
    if generated is not None:
        p = generated.payload
        return NextTaskResponse(
            task_id=p["task_id"],
            family_id=p["family_id"],
            difficulty=p["difficulty"],
            kind=p["kind"],
            prompt_text=p["prompt_text"],
            degraded=False,
        )
    [row] = await _rows(
        resources,
        f"SELECT {_BANK_COLUMNS} FROM core.task_bank b WHERE b.id = :t AND b.learner_id = :l",
        t=UUID(assigned.payload["task_id"]),
        l=learner_id,
    )
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
    replay = await _replay_next_task(resources, learner_id, session_id, body.idempotency_key)
    if replay is not None:
        return replay

    topic = await _topic_skills(learner_id, domain_id, body.topic_id, resources, settings)
    if topic is None or not topic[1]:
        raise ApiError("NOT_FOUND", "topic has no skills", status_code=404)
    topic_name, skills = topic
    skill = _target_skill(skills)
    skill_id: str = skill["id"]
    excluded = await _excluded_families(resources, learner_id, skill_id)

    bank = await _bank_task_for_skill(
        resources, learner_id, session_id, skill_id, body.kind, excluded
    )
    if bank is not None:
        await _append(
            resources, learner_id, [_assigned_event(session_id, body, bank["id"], bank["topic_id"])]
        )
        return _bank_response(bank, degraded=False)

    try:
        result = await gateway.generate_structured(
            role="task_gen",
            prompt_version=1,
            response_model=TaskGenOutput,
            variables={
                "topic_name": topic_name,
                "target_skills_json": [{"skill_id": skill_id, "name": skill["name"]}],
                "excluded_families_json": excluded,
                "kind": body.kind,
                "target_difficulty": PARAMS.default_target_difficulty,
            },
            learner_id=learner_id,
            session_id=session_id,
            turn_id=body.turn_id,
            idempotency_key=_task_gen_key(session_id, body.idempotency_key),
        )
    except LLM_ERRORS:
        fallback = await _fallback_bank_task(
            resources, learner_id, session_id, body.topic_id, skill_id, body.kind, excluded
        )
        if fallback is None:
            raise
        await _append(
            resources,
            learner_id,
            [_assigned_event(session_id, body, fallback["id"], fallback["topic_id"])],
        )
        return _bank_response(fallback, degraded=True)

    task = result.value
    task_id = uuid5(QUACK_UUID_NS, f"task:{learner_id}:{session_id}:{body.idempotency_key}")
    generated = NewEvent(
        type="task.generated",
        payload={
            "task_id": str(task_id),
            "domain_id": str(domain_id),
            "topic_id": str(body.topic_id),
            "family_id": task.family_id,
            "difficulty": task.difficulty,
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
        difficulty=task.difficulty,
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
        return _hint_response(replay)

    task = await _assigned_task(resources, learner_id, session_id, task_id)
    given = await _hint_penalties(resources, learner_id, session_id, task_id)
    level = min(PARAMS.max_hint_level, max((lv for lv, _ in given), default=0) + 1)
    event = NewEvent(
        type="task.hint_given",
        payload={
            "session_id": str(session_id),
            "turn_id": str(body.turn_id),
            "task_id": str(task_id),
            "hint_level": level,
            "hint_text": _hint_text(task["reference_solution"], level),
            "independence_penalty": PARAMS.hint_penalties[level - 1],
        },
        actor="system",
        idempotency_key=_key(body.idempotency_key, "task.hint_given"),
    )
    [stored] = await _append(resources, learner_id, [event])
    return _hint_response(stored)


# --- submit_answer --------------------------------------------------------------------


def _skill_ids(task: dict[str, Any]) -> list[str]:
    return [t["skill"]["skill_id"] for t in task["target_skills"] if t["skill"].get("skill_id")]


def _code_verdict(task: dict[str, Any], evaluator: str, is_correct: bool) -> _Verdict:
    """Verdict without the LLM: every target skill gets the score (decision 8)."""
    score = 1.0 if is_correct else 0.0
    return _Verdict(
        evaluator=evaluator,
        is_correct=is_correct,
        score=score,
        feedback=FEEDBACK_CORRECT if is_correct else FEEDBACK_INCORRECT,
        skill_scores=[
            {"skill_id": s, "score": score, "tested_depth": PARAMS.compute_tested_depth}
            for s in _skill_ids(task)
        ],
        triggered_misconceptions=[],
        actor="code",
    )


def _squash(value: str) -> str:
    """Decision 4: no whitespace at all, casefolded."""
    return "".join(value.split()).casefold()


def _matches(answer: str, reference: Any, normalize: Callable[[str], str]) -> bool:
    return reference is not None and normalize(answer) == normalize(str(reference))


def _strip_fold(value: str) -> str:
    return value.strip().casefold()


async def _check_math(
    compute: ComputeClient, reference: dict[str, Any], answer: str
) -> MathCheckResponse | None:
    """A1. None when compute gives no verdict: an invalid request (an answer over 1000
    characters counts as `parse_error`) or `ComputeUnavailableError`."""
    try:
        request = MathCheckRequest.model_validate(
            {
                "mode": reference.get("check_mode", "expression_equiv"),
                "student_expr": answer,
                "reference_expr": reference.get("sympy"),
                "variables": reference.get("variables", ["x"]),
            }
        )
    except ValidationError:
        return None
    try:
        return await compute.check_math(request)
    except ComputeUnavailableError:
        return None


async def _evaluate(
    learner_id: UUID,
    session_id: UUID,
    task_id: UUID,
    task: dict[str, Any],
    body: SubmitRequest,
    gateway: LLMGateway,
    compute: ComputeClient,
) -> _Verdict:
    """Cascade compute → `evaluator@1` → normalized string match (ТЗ §2.2 п.3, Т-§22.7)."""
    reference: dict[str, Any] = task["reference_solution"]
    if task["kind"] == "choice":
        return _code_verdict(
            task, "compute", _matches(body.answer, reference.get("option"), _strip_fold)
        )
    if task["kind"] == "math":
        checked = await _check_math(compute, reference, body.answer)
        if checked is not None and checked.status == "completed":
            return _code_verdict(task, "compute", bool(checked.is_equivalent))
        evaluator = "hybrid"
    else:
        evaluator = "llm"

    skill_ids = _skill_ids(task)
    try:
        result = await gateway.generate_structured(
            role="evaluator",
            prompt_version=1,
            response_model=EvaluatorOutput,
            variables={
                "prompt_text": task["prompt_text"],
                "reference_solution_json": reference,
                "student_answer": body.answer,
                "target_skills_json": [{"skill_id": s} for s in skill_ids],
                "known_misconceptions_json": task["target_misconceptions"],
            },
            learner_id=learner_id,
            session_id=session_id,
            turn_id=body.turn_id,
            idempotency_key=f"evaluator:{session_id}:{task_id}:{body.idempotency_key}",
        )
    except LLM_ERRORS:
        expected = reference.get("answer") or reference.get("sympy")
        return _code_verdict(task, "hybrid", _matches(body.answer, expected, _squash))

    verdict = result.value
    return _Verdict(
        evaluator=evaluator,
        is_correct=verdict.is_correct,
        score=verdict.score,
        feedback=verdict.feedback,
        skill_scores=[
            s.model_dump(mode="json") for s in verdict.skill_scores if str(s.skill_id) in skill_ids
        ],
        triggered_misconceptions=[
            m.model_dump(mode="json") for m in verdict.triggered_misconceptions
        ],
        actor="evaluator@1",
    )


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
        return _submit_response(replay)

    task = await _assigned_task(resources, learner_id, session_id, task_id)
    given = await _hint_penalties(resources, learner_id, session_id, task_id)
    # Decision 1 (Т-§7.4): the highest hint level counts, not the sum.
    independence = round(1.0 - max((penalty for _, penalty in given), default=0.0), 4)
    verdict = await _evaluate(learner_id, session_id, task_id, task, body, gateway, compute)

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
            "independence": independence,
            "feedback": verdict.feedback,
            "skill_scores": verdict.skill_scores,
            "triggered_misconceptions": verdict.triggered_misconceptions,
        },
        actor=verdict.actor,
        idempotency_key=_key(body.idempotency_key, "task.evaluated"),
    )
    _, stored = await _append(resources, learner_id, [submitted, evaluated])
    return _submit_response(stored)
