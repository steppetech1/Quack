"""Adaptive diagnostics over the domain DAG and propagation of `inferred_priors`
upward and downward along prerequisite edges (ТЗ §2.3, §3, §4; Т-§3.3).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.config import DiagnosticParams, Settings
from quack.db.neo4j import read_session
from quack.db.postgres import SessionFactory
from quack.domains.schemas import (
    AnswerDiagnosticRequest,
    AnswerDiagnosticResponse,
    DiagnosticOutput,
    DiagnosticQuestion,
    FinishDiagnosticRequest,
    FinishDiagnosticResponse,
    PrereqEdge,
    SkillDepEdge,
    StartDiagnosticRequest,
    StartDiagnosticResponse,
    TopicPriorItem,
)
from quack.events.append import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.events.reader import EVENT_COLUMNS, read_session_tail, row_to_event
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway
from quack.practice.schemas import ChoiceReference, reference_solution_payload

DIAGNOSTIC_FAMILY_PREFIX = "diag_"
# Options of the deterministic question of code (no model, or no valid reference).
FALLBACK_OPTIONS: tuple[str, ...] = ("A", "B", "C")

type PriorSource = Literal["tested", "self_report"]


def compute_inferred_priors(
    skills: Sequence[tuple[UUID, UUID]],
    skill_deps: Sequence[SkillDepEdge],
    topic_prereqs: Sequence[PrereqEdge],
    tested_scores: dict[UUID, float],
    tested_sources: dict[UUID, PriorSource] | None = None,
    params: DiagnosticParams | None = None,
) -> list[TopicPriorItem]:
    """Compute `TopicPriorItem` list for domain skills from tested scores, propagating
    downward to prerequisites on success (`score >= params.prior_propagated_success_threshold`)
    and upward to dependents on failure (`score < params.prior_propagated_fail_threshold`)
    (ТЗ §2.3, Т-§3.3). `tested_sources` marks the skills whose score came from
    the learner's self-report instead of a check (default `tested`).
    """
    params = params or DiagnosticParams()
    sources = tested_sources or {}
    skill_scores: dict[UUID, float] = {}
    topic_to_skills: dict[UUID, list[UUID]] = {}
    skill_to_topic: dict[UUID, UUID] = {}

    for s_id, t_id in skills:
        skill_to_topic[s_id] = t_id
        topic_to_skills.setdefault(t_id, []).append(s_id)
        if s_id in tested_scores:
            skill_scores[s_id] = tested_scores[s_id]
        elif t_id in tested_scores:
            skill_scores[s_id] = tested_scores[t_id]

    prereqs_of: dict[UUID, set[UUID]] = {s_id: set() for s_id, _ in skills}
    dependents_of: dict[UUID, set[UUID]] = {s_id: set() for s_id, _ in skills}

    for dep_edge in skill_deps:
        s_dep = dep_edge.skill_id
        s_pre = dep_edge.depends_on_skill_id
        if s_dep in prereqs_of and s_pre in prereqs_of:
            prereqs_of[s_dep].add(s_pre)
            dependents_of[s_pre].add(s_dep)

    for p_edge in topic_prereqs:
        from_skills = topic_to_skills.get(p_edge.from_topic_id, [])
        to_skills = topic_to_skills.get(p_edge.to_topic_id, [])
        for s_pre in from_skills:
            for s_dep in to_skills:
                prereqs_of[s_dep].add(s_pre)
                dependents_of[s_pre].add(s_dep)

    min_down_dist: dict[UUID, int] = {}
    for s_test, score in skill_scores.items():
        if score >= params.prior_propagated_success_threshold:
            queue = [(s_pre, 1) for s_pre in prereqs_of.get(s_test, set())]
            visited = {s_test}
            while queue:
                curr, dist = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                if curr not in skill_scores and (
                    curr not in min_down_dist or dist < min_down_dist[curr]
                ):
                    min_down_dist[curr] = dist
                for nxt in prereqs_of.get(curr, set()):
                    if nxt not in visited:
                        queue.append((nxt, dist + 1))

    min_up_dist: dict[UUID, int] = {}
    for s_test, score in skill_scores.items():
        if score < params.prior_propagated_fail_threshold:
            queue = [(s_dep, 1) for s_dep in dependents_of.get(s_test, set())]
            visited = {s_test}
            while queue:
                curr, dist = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                if curr not in skill_scores and (
                    curr not in min_up_dist or dist < min_up_dist[curr]
                ):
                    min_up_dist[curr] = dist
                for nxt in dependents_of.get(curr, set()):
                    if nxt not in visited:
                        queue.append((nxt, dist + 1))

    priors: list[TopicPriorItem] = []
    for s_id, t_id in skills:
        if s_id in skill_scores:
            score = skill_scores[s_id]
            prior_m = min(params.prior_tested_max_mastery, score * params.prior_tested_factor)
            conf = params.prior_tested_confidence
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=prior_m,
                    confidence=conf,
                    source=sources.get(s_id, "tested"),
                )
            )
        elif s_id in min_up_dist:
            dist = min_up_dist[s_id]
            prior_m = params.prior_up_fail_mastery
            conf = params.prior_up_base_confidence * (params.prior_propagated_decay**dist)
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=prior_m,
                    confidence=conf,
                    source="propagated",
                )
            )
        elif s_id in min_down_dist:
            dist = min_down_dist[s_id]
            prior_m = params.prior_down_base_mastery * (params.prior_propagated_decay**dist)
            conf = params.prior_down_base_confidence * (params.prior_propagated_decay**dist)
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=prior_m,
                    confidence=conf,
                    source="propagated",
                )
            )
        else:
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=0.0,
                    confidence=0.0,
                    source="propagated",
                )
            )
    return priors


async def _load_domain_dag(
    resources: AppResources, learner_id: UUID, domain_id: UUID
) -> tuple[
    list[tuple[UUID, str, float]],
    list[tuple[UUID, UUID, str]],
    list[PrereqEdge],
    list[SkillDepEdge],
]:
    """Query active topics, skills, prereq edges, and skill deps from Neo4j via
    `read_consistent_slice` (ТЗ §2.3, Т-§3.3).
    """
    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=lambda lid: read_applied_seq(resources.neo4j, str(lid)),
    )

    async def _loader() -> tuple[
        list[tuple[UUID, str, float]],
        list[tuple[UUID, UUID, str]],
        list[PrereqEdge],
        list[SkillDepEdge],
    ]:
        async with read_session(resources.neo4j) as s:
            t_res = await s.run(
                "MATCH (d:Domain {id: $d, learnerId: $l})-[:HAS_TOPIC]->(t:Topic) "
                "WHERE t.status = 'active' "
                "RETURN t.id AS id, t.name AS name, properties(t) AS props",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            topics: list[tuple[UUID, str, float]] = []
            async for rec in t_res:
                tid = UUID(rec["id"])
                name = str(rec["name"] or "")
                props = rec["props"] or {}
                hub = float(props.get("hubScore", 0.0))
                topics.append((tid, name, hub))

            sk_res = await s.run(
                "MATCH (t:Topic)<-[:PART_OF]-(sk:Skill) "
                "WHERE t.domainId = $d AND t.learnerId = $l AND sk.status = 'active' "
                "RETURN sk.id AS id, t.id AS topic_id, sk.name AS name",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            skills: list[tuple[UUID, UUID, str]] = []
            async for rec in sk_res:
                skills.append((UUID(rec["id"]), UUID(rec["topic_id"]), str(rec["name"] or "")))

            pr_res = await s.run(
                "MATCH (a:Topic)-[:PREREQ_OF]->(b:Topic) "
                "WHERE a.domainId = $d AND b.domainId = $d AND a.learnerId = $l "
                "AND a.status = 'active' AND b.status = 'active' "
                "RETURN a.id AS from_id, b.id AS to_id",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            topic_prereqs = [
                PrereqEdge(from_topic_id=UUID(rec["from_id"]), to_topic_id=UUID(rec["to_id"]))
                async for rec in pr_res
            ]

            dep_res = await s.run(
                "MATCH (a:Skill)-[:DEPENDS_ON]->(b:Skill) "
                "WHERE a.domainId = $d AND b.domainId = $d AND a.learnerId = $l "
                "AND a.status = 'active' AND b.status = 'active' "
                "RETURN a.id AS sid, b.id AS did",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            skill_deps = [
                SkillDepEdge(skill_id=UUID(rec["sid"]), depends_on_skill_id=UUID(rec["did"]))
                async for rec in dep_res
            ]

        return topics, skills, topic_prereqs, skill_deps

    slice_res = await read_consistent_slice(learner_id, min_seq=None, loader_fn=_loader, deps=deps)
    return slice_res.data


def _topological_candidate_topics(
    topics: list[tuple[UUID, str, float]],
    topic_prereqs: list[PrereqEdge],
) -> list[UUID]:
    """Sort active topics topologically by PREREQ_OF, then order starting from DAG center/hub."""
    topic_ids = [t[0] for t in topics]
    hub_map = {t[0]: t[2] for t in topics}

    adj: dict[UUID, list[UUID]] = {tid: [] for tid in topic_ids}
    in_degree: dict[UUID, int] = {tid: 0 for tid in topic_ids}
    for edge in topic_prereqs:
        if edge.from_topic_id in adj and edge.to_topic_id in in_degree:
            adj[edge.from_topic_id].append(edge.to_topic_id)
            in_degree[edge.to_topic_id] += 1

    queue = [tid for tid in topic_ids if in_degree[tid] == 0]
    topo_order: list[UUID] = []
    while queue:
        u = queue.pop(0)
        topo_order.append(u)
        for v in adj.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    for tid in topic_ids:
        if tid not in topo_order:
            topo_order.append(tid)

    if not topo_order:
        return []

    mid_idx = len(topo_order) // 2
    candidates = sorted(
        topo_order,
        key=lambda tid: (abs(topo_order.index(tid) - mid_idx), -hub_map.get(tid, 0.0)),
    )
    return candidates


@dataclass(frozen=True)
class _DiagnosticState:
    candidate_topic_ids: list[UUID]
    tested_topic_ids: list[UUID]
    tested_scores: dict[UUID, float]
    tested_sources: dict[UUID, PriorSource]
    asked_task_ids: list[UUID]
    current_question: dict[str, Any] | None
    completed: StoredEvent | None

    @property
    def step_count(self) -> int:
        return len(self.tested_topic_ids)


@dataclass(frozen=True)
class _BankTask:
    task_id: UUID
    topic_id: UUID
    skill_id: UUID
    kind: str
    prompt_text: str
    reference_solution: dict[str, Any]
    difficulty: float


def _reference_solution(correct_answer: str, options: list[str]) -> dict[str, Any]:
    """`ChoiceReference` of WP6.4 as stored in the bank; `ValueError` if the correct option is
    not among the options or there are fewer than two."""
    reference = ChoiceReference(option=correct_answer, answer=correct_answer, options=options)
    return reference_solution_payload(reference)


def _unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _first_target_skill_id(target_skills: Any) -> UUID | None:
    """Skill a question is attributed to: the first target with a `skill_id` (as for the
    questions restored from `task.generated`).
    """
    if not isinstance(target_skills, list) or not target_skills:
        return None
    raw = (target_skills[0].get("skill") or {}).get("skill_id")
    return UUID(str(raw)) if raw else None


def _bank_task_from_row(row: Any) -> _BankTask | None:
    skill_id = _first_target_skill_id(row.target_skills)
    if skill_id is None:
        return None
    return _BankTask(
        task_id=row.id,
        topic_id=row.topic_id,
        skill_id=skill_id,
        kind=row.kind,
        prompt_text=row.prompt_text,
        reference_solution=dict(row.reference_solution or {}),
        difficulty=float(row.difficulty),
    )


_BANK_COLUMNS = (
    "id, topic_id, family_id, kind, prompt_text, reference_solution, target_skills, difficulty"
)


async def _find_bank_task(
    db: SessionFactory,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    skill_id: UUID,
    excluded_task_ids: Sequence[UUID],
) -> _BankTask | None:
    """A `core.task_bank` row of this learner and topic that was not yet asked in the diagnostic
    session, preferring rows whose `target_skills` contain `skill_id` (ТЗ-П1 §2.3.1). Questions
    generated by earlier diagnostics (`family_id` with the diagnostic prefix) are not reused.
    """
    async with db() as session:
        result = await session.execute(
            text(
                f"SELECT {_BANK_COLUMNS} FROM core.task_bank WHERE learner_id = :l "
                "AND domain_id = :d AND topic_id = :t ORDER BY created_seq, id"
            ),
            {"l": learner_id, "d": domain_id, "t": topic_id},
        )
        rows = result.all()
    excluded = set(excluded_task_ids)
    candidates = [
        r
        for r in rows
        if r.id not in excluded and not r.family_id.startswith(DIAGNOSTIC_FAMILY_PREFIX)
    ]
    targeting = [
        r
        for r in candidates
        if any(
            (t.get("skill") or {}).get("skill_id") == str(skill_id) for t in (r.target_skills or [])
        )
    ]
    for row in [*targeting, *candidates]:
        task = _bank_task_from_row(row)
        if task is not None:
            return task
    return None


async def _load_bank_task(db: SessionFactory, learner_id: UUID, task_id: UUID) -> _BankTask | None:
    async with db() as session:
        result = await session.execute(
            text(f"SELECT {_BANK_COLUMNS} FROM core.task_bank WHERE learner_id = :l AND id = :id"),
            {"l": learner_id, "id": task_id},
        )
        row = result.first()
    return _bank_task_from_row(row) if row is not None else None


def _options_of(reference: dict[str, Any], payload_options: Any = None) -> list[str]:
    return list(reference.get("options") or payload_options or [])


def _correct_answer_of(reference: dict[str, Any]) -> str:
    return str(reference.get("option") or reference.get("answer") or "")


def _question_dict_from_generated(
    payload: dict[str, Any], params: DiagnosticParams
) -> dict[str, Any]:
    reference = payload.get("reference_solution") or {}
    targets = payload.get("target_skills") or []
    return {
        "task_id": payload["task_id"],
        "topic_id": payload["topic_id"],
        "skill_id": targets[0]["skill"]["skill_id"] if targets else "",
        "kind": payload.get("kind", "choice"),
        "prompt_text": payload.get("prompt_text", ""),
        "options": _options_of(reference, payload.get("options")),
        "correct_answer": _correct_answer_of(reference),
        "tested_depth": _unit(payload.get("difficulty", params.default_tested_depth)),
    }


def _question_dict_from_bank(task: _BankTask) -> dict[str, Any]:
    return {
        "task_id": str(task.task_id),
        "topic_id": str(task.topic_id),
        "skill_id": str(task.skill_id),
        "kind": task.kind,
        "prompt_text": task.prompt_text,
        "options": _options_of(task.reference_solution),
        "correct_answer": _correct_answer_of(task.reference_solution),
        "tested_depth": _unit(task.difficulty),
    }


async def _reconstruct_state_from_events(
    db: SessionFactory,
    learner_id: UUID,
    domain_id: UUID,
    session_id: UUID,
    params: DiagnosticParams,
) -> _DiagnosticState:
    """The only loader of diagnostic session state: replays the session events of `core.events`
    (T-016 D-1). `NOT_FOUND` when this learner has no `diagnostic.started` for the session in
    this domain. A question is either a `task.generated` event or a `task.assigned` event of a
    `core.task_bank` row; the tested depth of a question is its `difficulty`. The `source` of a
    tested topic is `self_report` when an open answer has no completed `diagnostic@1` evaluation.
    """
    async with db() as session:
        events = await read_session_tail(session, learner_id, session_id, after_seq=0)

    diag_events = [e for e in events if e.domain_id == domain_id]
    started = next((e for e in diag_events if e.type == "diagnostic.started"), None)
    if started is None:
        raise ApiError("NOT_FOUND", "Diagnostic session not found", status_code=404)

    answered = [e for e in diag_events if e.type == "diagnostic.question_answered"]
    asked = [e for e in diag_events if e.type in ("task.generated", "task.assigned")]
    completed = next((e for e in diag_events if e.type == "diagnostic.completed"), None)

    questions: dict[str, dict[str, Any]] = {}
    current_question: dict[str, Any] | None = None
    for e in asked:
        question: dict[str, Any] | None
        if e.type == "task.generated":
            question = _question_dict_from_generated(e.payload, params)
        else:
            bank_task = await _load_bank_task(db, learner_id, UUID(e.payload["task_id"]))
            question = _question_dict_from_bank(bank_task) if bank_task is not None else None
        if question is not None:
            questions[question["task_id"]] = question
            current_question = question

    eval_keys = [f"diag:eval:{session_id}:{e.idempotency_key}" for e in answered]
    async with db() as session:
        result = await session.execute(
            text(
                "SELECT idempotency_key FROM core.llm_outbox WHERE learner_id = :l "
                "AND status = 'completed' AND idempotency_key = ANY(:keys)"
            ),
            {"l": learner_id, "keys": eval_keys},
        )
        evaluated = {str(r.idempotency_key) for r in result}

    tested_sources: dict[UUID, PriorSource] = {}
    for e, eval_key in zip(answered, eval_keys, strict=True):
        is_open = (questions.get(e.payload["task_id"]) or {}).get("kind") == "open"
        tested_sources[UUID(e.payload["skill_id"])] = (
            "self_report" if is_open and eval_key not in evaluated else "tested"
        )

    return _DiagnosticState(
        candidate_topic_ids=[UUID(t) for t in started.payload.get("candidate_topic_ids", [])],
        tested_topic_ids=[UUID(e.payload["topic_id"]) for e in answered],
        tested_scores={UUID(e.payload["skill_id"]): float(e.payload["score"]) for e in answered},
        tested_sources=tested_sources,
        asked_task_ids=[UUID(e.payload["task_id"]) for e in asked],
        current_question=current_question,
        completed=completed,
    )


async def _find_event_by_key(
    db: SessionFactory, learner_id: UUID, idempotency_key: str, event_type: str
) -> StoredEvent | None:
    async with db() as session:
        result = await session.execute(
            text(
                f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :learner_id "
                "AND idempotency_key = :key AND type = :type"
            ),
            {"learner_id": learner_id, "key": idempotency_key, "type": event_type},
        )
        row = result.first()
    return row_to_event(row) if row is not None else None


def _question_from_task_generated(payload: dict[str, Any]) -> DiagnosticQuestion:
    reference = payload.get("reference_solution") or {}
    targets = payload.get("target_skills") or []
    return DiagnosticQuestion(
        task_id=UUID(payload["task_id"]),
        topic_id=UUID(payload["topic_id"]),
        skill_id=UUID(targets[0]["skill"]["skill_id"]),
        kind=payload.get("kind", "choice"),
        prompt_text=payload.get("prompt_text", ""),
        options=list(reference.get("options") or payload.get("options") or []),
    )


def _question_from_bank_task(task: _BankTask) -> DiagnosticQuestion:
    return DiagnosticQuestion(
        task_id=task.task_id,
        topic_id=task.topic_id,
        skill_id=task.skill_id,
        kind=task.kind,  # type: ignore[arg-type]
        prompt_text=task.prompt_text,
        options=_options_of(task.reference_solution),
    )


def _task_assigned_event(
    domain_id: UUID,
    session_id: UUID,
    turn_id: UUID,
    task: _BankTask,
    idempotency_key: str,
) -> NewEvent:
    """A `core.task_bank` row asked in the diagnostic: it is already in the bank, so no
    `task.generated` is appended; `task.assigned` records which question the session is on."""
    return NewEvent(
        type="task.assigned",
        actor="code",
        domain_id=domain_id,
        session_id=session_id,
        turn_id=turn_id,
        idempotency_key=idempotency_key,
        payload={
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "task_id": str(task.task_id),
            "topic_id": str(task.topic_id),
        },
    )


async def _question_by_task_key(
    db: SessionFactory, learner_id: UUID, idempotency_key: str
) -> DiagnosticQuestion | None:
    """The question stored under `idempotency_key`: a `task.generated` or the `task.assigned`
    of a bank task; `None` when neither exists."""
    generated = await _find_event_by_key(db, learner_id, idempotency_key, "task.generated")
    if generated is not None:
        return _question_from_task_generated(generated.payload)
    assigned = await _find_event_by_key(db, learner_id, idempotency_key, "task.assigned")
    if assigned is None:
        return None
    task = await _load_bank_task(db, learner_id, UUID(assigned.payload["task_id"]))
    return _question_from_bank_task(task) if task is not None else None


def _idempotency_conflict(message: str) -> ApiError:
    return ApiError("IDEMPOTENCY_CONFLICT", message, status_code=409)


async def _generate_question(
    llm: LLMGateway,
    *,
    learner_id: UUID,
    domain_id: UUID,
    session_id: UUID,
    turn_id: UUID | None,
    task_id: UUID,
    topic_id: UUID,
    topic_name: str,
    skill_id: UUID,
    skill_name: str,
    fallback_prompt: str,
    event_key: str,
    params: DiagnosticParams,
) -> tuple[NewEvent, DiagnosticQuestion]:
    """Ask `diagnostic@1` (`mode="generate"`) for a question and build its `task.generated`
    event; `actor="diagnostic@1"`, or `"code"` when the deterministic fallback was used. The
    question's `tested_depth` is stored as the task `difficulty`.
    """
    actor = "diagnostic@1"
    try:
        res = await llm.generate_structured(
            role="diagnostic",
            prompt_version=1,
            response_model=DiagnosticOutput,
            variables={
                "mode": "generate",
                "topic_name": topic_name,
                "skill_name": skill_name,
                "question_prompt": "",
                "student_answer": "",
            },
            learner_id=learner_id,
            session_id=session_id,
            turn_id=turn_id,
            idempotency_key=f"diag-gen:{task_id}",
        )
        diag_out = res.value
    except Exception:
        diag_out = None

    options: list[str] = []
    correct_ans = ""
    reference: dict[str, Any] | None = None
    if diag_out is not None:
        options = diag_out.options or list(FALLBACK_OPTIONS)
        correct_ans = diag_out.correct_answer or options[0]
        # WP6.4 п.1: an answer without a valid reference does not reach the bank.
        with contextlib.suppress(ValueError):
            reference = _reference_solution(correct_ans, options)
    if diag_out is None or reference is None:
        actor = "code"
        diag_out = DiagnosticOutput(
            prompt_text=fallback_prompt,
            options=list(FALLBACK_OPTIONS),
            correct_answer=FALLBACK_OPTIONS[0],
            is_correct=False,
            score=0.0,
            tested_depth=params.default_tested_depth,
        )
        options, correct_ans = list(FALLBACK_OPTIONS), FALLBACK_OPTIONS[0]
        reference = _reference_solution(correct_ans, options)
    event = NewEvent(
        type="task.generated",
        actor=actor,
        domain_id=domain_id,
        session_id=session_id,
        idempotency_key=event_key,
        payload={
            "task_id": str(task_id),
            "domain_id": str(domain_id),
            "topic_id": str(topic_id),
            "family_id": f"{DIAGNOSTIC_FAMILY_PREFIX}{skill_id.hex[:8]}",
            "difficulty": diag_out.tested_depth,
            "kind": "choice",
            "prompt_text": diag_out.prompt_text or "Diagnostic question",
            "reference_solution": reference,
            "target_skills": [{"skill": {"skill_id": str(skill_id)}, "weight": 1.0}],
            "target_misconceptions": [],
        },
    )
    question = DiagnosticQuestion(
        task_id=task_id,
        topic_id=topic_id,
        skill_id=skill_id,
        kind="choice",
        prompt_text=diag_out.prompt_text,
        options=options,
    )
    return event, question


async def start_diagnostic(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: StartDiagnosticRequest,
) -> StartDiagnosticResponse:
    """Select mid-DAG high-hub candidate topics, generate first diagnostic question
    (`task.generated`), and emit `diagnostic.started` (ТЗ §2.3, §3; Т-§3.3).
    """
    existing = await _find_event_by_key(
        resources.session_factory, learner_id, req.idempotency_key, "diagnostic.started"
    )
    if existing is not None:
        return await _replay_start(resources, learner_id, domain_id, req, existing)

    topics, skills, topic_prereqs, _ = await _load_domain_dag(resources, learner_id, domain_id)
    if not topics:
        raise ApiError("NOT_FOUND", "No active topics found in domain", status_code=404)

    candidate_topic_ids = _topological_candidate_topics(topics, topic_prereqs)
    first_topic_id = candidate_topic_ids[0]

    skills_for_topic = [s for s in skills if s[1] == first_topic_id]
    first_skill_id, _, first_skill_name = (
        skills_for_topic[0] if skills_for_topic else (uuid4(), first_topic_id, "Core Skill")
    )
    first_topic_name = next((t[1] for t in topics if t[0] == first_topic_id), "Core Topic")

    session_id = uuid5(
        QUACK_UUID_NS, f"diag_session:{learner_id}:{domain_id}:{req.idempotency_key}"
    )
    task_id = uuid5(QUACK_UUID_NS, f"diag_task:{learner_id}:{session_id}:0")
    task_key = f"{req.idempotency_key}:task"

    bank_task = await _find_bank_task(
        resources.session_factory, learner_id, domain_id, first_topic_id, first_skill_id, []
    )
    if bank_task is not None:
        start_turn_id = uuid5(QUACK_UUID_NS, f"diag_turn:{learner_id}:{session_id}:start")
        task_event = _task_assigned_event(domain_id, session_id, start_turn_id, bank_task, task_key)
        first_question = _question_from_bank_task(bank_task)
    else:
        task_event, first_question = await _generate_question(
            llm,
            learner_id=learner_id,
            domain_id=domain_id,
            session_id=session_id,
            turn_id=None,
            task_id=task_id,
            topic_id=first_topic_id,
            topic_name=first_topic_name,
            skill_id=first_skill_id,
            skill_name=first_skill_name,
            fallback_prompt=f"What is the principle behind {first_skill_name}?",
            event_key=task_key,
            params=settings.diagnostic,
        )

    started_event = NewEvent(
        type="diagnostic.started",
        actor="learner",
        domain_id=domain_id,
        session_id=session_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "session_id": str(session_id),
            "candidate_topic_ids": [str(tid) for tid in candidate_topic_ids],
        },
    )

    async with resources.session_factory() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [task_event, started_event],
            redis=resources.redis,
            publish_wakeup=True,
        )

    return StartDiagnosticResponse(
        session_id=session_id,
        candidate_topic_ids=candidate_topic_ids,
        next_question=first_question,
    )


async def _replay_start(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    req: StartDiagnosticRequest,
    started: StoredEvent,
) -> StartDiagnosticResponse:
    """Retry of `start`: rebuild the response from the stored events, no LLM call, no append."""
    if started.domain_id != domain_id:
        raise _idempotency_conflict("Idempotency key was used to start another domain")
    question = await _question_by_task_key(
        resources.session_factory, learner_id, f"{req.idempotency_key}:task"
    )
    if question is None:
        raise _idempotency_conflict("Stored diagnostic start has no generated task")
    return StartDiagnosticResponse(
        session_id=UUID(str(started.payload["session_id"])),
        candidate_topic_ids=[UUID(t) for t in started.payload.get("candidate_topic_ids", [])],
        next_question=question,
    )


async def _replay_answer(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    req: AnswerDiagnosticRequest,
    answered: StoredEvent,
) -> AnswerDiagnosticResponse:
    """Retry of `answer`: 409 when the request differs from the stored turn, otherwise rebuild
    the response from the events of that turn (no LLM call, no append).
    """
    stored = answered.payload
    if (
        answered.domain_id != domain_id
        or stored["session_id"] != str(req.session_id)
        or stored["task_id"] != str(req.task_id)
        or stored["answer"] != req.answer
        or stored.get("confidence_self_report") != req.confidence_self_report
    ):
        raise _idempotency_conflict("Idempotency key was used for a different diagnostic answer")
    next_task_id = _next_task_id(learner_id, req.session_id, req.idempotency_key)
    next_question = await _question_by_task_key(
        resources.session_factory, learner_id, f"tgen:{next_task_id}"
    )
    return AnswerDiagnosticResponse(
        is_correct=bool(stored["is_correct"]),
        score=float(stored["score"]),
        next_question=next_question,
        should_finish=next_question is None,
    )


def _next_task_id(learner_id: UUID, session_id: UUID, idempotency_key: str) -> UUID:
    return uuid5(QUACK_UUID_NS, f"diag_next_task:{learner_id}:{session_id}:{idempotency_key}")


def _normalized(value: str) -> str:
    return value.strip().lower()


async def _evaluate_answer(
    llm: LLMGateway,
    *,
    learner_id: UUID,
    turn_id: UUID,
    req: AnswerDiagnosticRequest,
    question: dict[str, Any],
    topic_name: str,
    skill_name: str,
    params: DiagnosticParams,
) -> tuple[bool, float, PriorSource]:
    """Grade the answer: a code comparison for `kind != "open"`; for `open` questions
    `diagnostic@1` (`mode="evaluate"`), falling back to the learner's self-report when the model
    is unavailable (ТЗ-П1 §2.3.2). Returns `(is_correct, score, source)`.
    """
    if question["kind"] != "open":
        is_correct = _normalized(req.answer) == _normalized(question["correct_answer"])
        return is_correct, 1.0 if is_correct else 0.0, "tested"
    try:
        res = await llm.generate_structured(
            role="diagnostic",
            prompt_version=1,
            response_model=DiagnosticOutput,
            variables={
                "mode": "evaluate",
                "topic_name": topic_name,
                "skill_name": skill_name,
                "question_prompt": question["prompt_text"],
                "student_answer": req.answer,
            },
            learner_id=learner_id,
            session_id=req.session_id,
            turn_id=turn_id,
            idempotency_key=f"diag:eval:{req.session_id}:{req.idempotency_key}",
        )
    except (LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError):
        if req.confidence_self_report is not None:
            score = req.confidence_self_report
        else:
            score = params.no_self_report_score if req.answer.strip() else 0.0
        return score >= params.self_report_correct_threshold, score, "self_report"
    return res.value.is_correct, res.value.score, "tested"


async def answer_diagnostic(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: AnswerDiagnosticRequest,
) -> AnswerDiagnosticResponse:
    """Evaluate diagnostic answer, emit `diagnostic.question_answered`, and adaptively select
    the next question upward (if correct) or downward (if incorrect) along the DAG up to
    `settings.diagnostic.max_questions` (ТЗ §2.3, §3; Т-§3.3).
    """
    previous = await _find_event_by_key(
        resources.session_factory, learner_id, req.idempotency_key, "diagnostic.question_answered"
    )
    if previous is not None:
        return await _replay_answer(resources, learner_id, domain_id, req, previous)

    state = await _reconstruct_state_from_events(
        resources.session_factory, learner_id, domain_id, req.session_id, settings.diagnostic
    )
    if state.completed is not None:
        raise ApiError(
            "IDEMPOTENCY_CONFLICT", "Diagnostic session is already finished", status_code=409
        )
    curr_q = state.current_question
    if curr_q is None:
        raise ApiError("NOT_FOUND", "Diagnostic session has no question", status_code=404)
    curr_topic_id = UUID(curr_q["topic_id"])
    curr_skill_id = UUID(curr_q["skill_id"])

    topics, skills, topic_prereqs, _ = await _load_domain_dag(resources, learner_id, domain_id)

    turn_id = uuid5(QUACK_UUID_NS, f"diag_turn:{learner_id}:{req.session_id}:{req.idempotency_key}")
    curr_topic_name = next((t[1] for t in topics if t[0] == curr_topic_id), "Topic")
    curr_skill_name = next((sk[2] for sk in skills if sk[0] == curr_skill_id), "Skill")
    is_correct, score, _ = await _evaluate_answer(
        llm,
        learner_id=learner_id,
        turn_id=turn_id,
        req=req,
        question=curr_q,
        topic_name=curr_topic_name,
        skill_name=curr_skill_name,
        params=settings.diagnostic,
    )
    answered_event = NewEvent(
        type="diagnostic.question_answered",
        actor="learner",
        domain_id=domain_id,
        session_id=req.session_id,
        turn_id=turn_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "session_id": str(req.session_id),
            "turn_id": str(turn_id),
            "task_id": str(req.task_id),
            "topic_id": str(curr_topic_id),
            "skill_id": str(curr_skill_id),
            "answer": req.answer,
            "is_correct": is_correct,
            "score": score,
            "confidence_self_report": req.confidence_self_report,
            "response_time_sec": req.response_time_sec,
            "tested_depth": curr_q["tested_depth"],
        },
    )

    tested_topic_ids = [*state.tested_topic_ids, curr_topic_id]
    step_count = state.step_count + 1

    tested_set = set(tested_topic_ids)

    adj_dependents: dict[UUID, list[UUID]] = {}
    adj_prereqs: dict[UUID, list[UUID]] = {}
    for p in topic_prereqs:
        adj_dependents.setdefault(p.from_topic_id, []).append(p.to_topic_id)
        adj_prereqs.setdefault(p.to_topic_id, []).append(p.from_topic_id)

    next_topic: UUID | None = None
    if step_count < settings.diagnostic.max_questions:
        if is_correct:
            queue = list(adj_dependents.get(curr_topic_id, []))
            visited = set(queue)
            while queue:
                cand = queue.pop(0)
                if cand not in tested_set:
                    next_topic = cand
                    break
                for nxt in adj_dependents.get(cand, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)
        else:
            queue = list(adj_prereqs.get(curr_topic_id, []))
            visited = set(queue)
            while queue:
                cand = queue.pop(0)
                if cand not in tested_set:
                    next_topic = cand
                    break
                for nxt in adj_prereqs.get(cand, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)

        if next_topic is None:
            cand_order = state.candidate_topic_ids
            for cand in cand_order:
                if cand not in tested_set:
                    next_topic = cand
                    break

    events_to_append = [answered_event]
    next_question: DiagnosticQuestion | None = None
    should_finish = next_topic is None or step_count >= settings.diagnostic.max_questions

    if next_topic is not None and not should_finish:
        sk_cand = [s for s in skills if s[1] == next_topic]
        next_sk_id, _, next_sk_name = (
            sk_cand[0] if sk_cand else (uuid4(), next_topic, "Diagnostic Skill")
        )
        next_t_name = next((t[1] for t in topics if t[0] == next_topic), "Topic")

        next_task_id = _next_task_id(learner_id, req.session_id, req.idempotency_key)
        next_key = f"tgen:{next_task_id}"
        next_bank = await _find_bank_task(
            resources.session_factory,
            learner_id,
            domain_id,
            next_topic,
            next_sk_id,
            state.asked_task_ids,
        )
        if next_bank is not None:
            next_task_event = _task_assigned_event(
                domain_id, req.session_id, turn_id, next_bank, next_key
            )
            next_question = _question_from_bank_task(next_bank)
        else:
            next_task_event, next_question = await _generate_question(
                llm,
                learner_id=learner_id,
                domain_id=domain_id,
                session_id=req.session_id,
                turn_id=turn_id,
                task_id=next_task_id,
                topic_id=next_topic,
                topic_name=next_t_name,
                skill_id=next_sk_id,
                skill_name=next_sk_name,
                fallback_prompt=f"Diagnostic question on {next_sk_name}?",
                event_key=next_key,
                params=settings.diagnostic,
            )
        events_to_append.append(next_task_event)

    async with resources.session_factory() as session, session.begin():
        await append_events(
            session, learner_id, events_to_append, redis=resources.redis, publish_wakeup=True
        )

    return AnswerDiagnosticResponse(
        is_correct=is_correct,
        score=score,
        next_question=next_question,
        should_finish=should_finish,
    )


async def finish_diagnostic(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    req: FinishDiagnosticRequest,
) -> FinishDiagnosticResponse:
    """Compute `inferred_priors` across the DAG and emit `diagnostic.completed`
    (ТЗ §2.3, §3; Т-§3.3).
    """
    state = await _reconstruct_state_from_events(
        resources.session_factory, learner_id, domain_id, req.session_id, settings.diagnostic
    )
    if state.completed is not None:
        stored_payload = state.completed.payload
        return FinishDiagnosticResponse(
            tested_topic_ids=[UUID(t) for t in stored_payload["tested_topic_ids"]],
            inferred_priors=[
                TopicPriorItem.model_validate(p) for p in stored_payload["inferred_priors"]
            ],
            seq=state.completed.seq,
        )
    tested_topic_ids = state.tested_topic_ids
    tested_scores = state.tested_scores

    _, skills, topic_prereqs, skill_deps = await _load_domain_dag(resources, learner_id, domain_id)
    skill_tuples = [(s[0], s[1]) for s in skills]

    inferred_priors = compute_inferred_priors(
        skills=skill_tuples,
        skill_deps=skill_deps,
        topic_prereqs=topic_prereqs,
        tested_scores=tested_scores,
        tested_sources=state.tested_sources,
        params=settings.diagnostic,
    )

    completed_event = NewEvent(
        type="diagnostic.completed",
        actor="learner",
        domain_id=domain_id,
        session_id=req.session_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "session_id": str(req.session_id),
            "tested_topic_ids": [str(tid) for tid in tested_topic_ids],
            "inferred_priors": [item.model_dump(mode="json") for item in inferred_priors],
        },
    )

    async with resources.session_factory() as session, session.begin():
        stored = await append_events(
            session, learner_id, [completed_event], redis=resources.redis, publish_wakeup=True
        )

    seq = stored[0].seq if stored else 0
    return FinishDiagnosticResponse(
        tested_topic_ids=tested_topic_ids,
        inferred_priors=inferred_priors,
        seq=seq,
    )
