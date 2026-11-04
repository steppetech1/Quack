"""Job kinds and dispatcher rules of Phase 4 (ТЗ Ф4 WP7.1, §6.5, seam S6; G1: the table).

Phase 3's `kinds.py` / `dispatcher_rules.py` stay until WP1.3 (D-A-103): `workers` still start
on `kinds.JOB_KINDS`. This module is the Phase 4 table; WP1.3 / G2 merge it into `kinds.py`.

`plan_jobs(event, settings)` is a pure function of the event: it returns the jobs the rows of
the table put for it. Rows that need data beyond the event (`ROWS_NEED_FACTS`) are listed with
what they read and are implemented in G2 with the dispatcher (WP7.1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from quack.config import Settings
from quack.events import registry
from quack.events.event_types import TurnCommitted
from quack.events.log import StoredEvent

type PriorityClass = Literal["P1", "P2", "P3"]


@dataclass(frozen=True)
class JobKindSpec:
    """A kind of the queue (§6.5): default class, `visible` (Т-§13.8), roles of its models
    (a kind waits while any of them is pressured, T-Q10)."""

    kind: str
    priority_class: PriorityClass
    visible: bool = False
    roles: tuple[str, ...] = ()
    periodic: bool = False


def _k(
    kind: str,
    cls: PriorityClass,
    *,
    visible: bool = False,
    roles: tuple[str, ...] = (),
    periodic: bool = False,
) -> JobKindSpec:
    return JobKindSpec(kind, cls, visible, roles, periodic)


JOB_KIND_SPECS: tuple[JobKindSpec, ...] = (
    _k("extract.episode", "P2", roles=("extractor",)),
    _k("analyst.run", "P2", roles=("analyst",)),
    _k("analyst.deep", "P2", roles=("analyst_deep",)),
    _k("check.symbolic", "P1", visible=True),
    _k("check.model", "P1", visible=True, roles=("verdict_proof", "verdict_free")),
    _k("task.check", "P1", visible=True, roles=("task_checker",)),
    _k("task.solution_space", "P2", roles=("solution_space",)),
    _k("second_opinion.diagnosis", "P1", roles=("second_opinion",)),
    _k("canvas.rediagnose", "P1", roles=("canvas_diagnoser",)),
    _k("photo.pipeline", "P1", visible=True, roles=("recognizer", "segmenter")),
    _k("material.parse", "P1", visible=True, roles=("material_parser",)),
    _k("agent.viz", "P2", visible=True, roles=("viz_builder", "viz_checker")),
    _k("agent.illustration", "P2", visible=True, roles=("illustrator",)),
    _k("agent.task_generation", "P2", visible=True, roles=("task_generator", "task_arbiter")),
    _k("agent.solution_space", "P2", visible=True, roles=("solution_space",)),
    _k("agent.artifact", "P2", visible=True, roles=("artifact_writer",)),
    _k("agent.topic_split", "P2", visible=True, roles=("builder",)),
    _k("check.known_claim", "P2", visible=True, roles=("task_generator",)),
    _k("export.data", "P2"),
    _k("today.prepare", "P2", roles=("today_planner",)),
    _k("artifact.topic_refresh", "P2", roles=("artifact_writer",)),
    _k("artifact.cheatsheet", "P2", roles=("artifact_writer",)),
    _k("embedding.compute", "P2"),
    _k("graph.hygiene", "P3", roles=("builder",)),
    _k("misconception.describe", "P3", roles=("misconception_describer",)),
    _k("session.sweep_inactive", "P3", periodic=True),
    _k("deletions.reconcile", "P3", periodic=True),
    _k("manifests.cleanup", "P3", periodic=True),
    _k("calibration.weekly", "P3", periodic=True),
    _k("exports.cleanup", "P3", periodic=True),
)

JOB_KINDS: tuple[str, ...] = tuple(s.kind for s in JOB_KIND_SPECS if not s.periodic)
PERIODIC_JOB_KINDS: tuple[str, ...] = tuple(s.kind for s in JOB_KIND_SPECS if s.periodic)
# §6.5: dropped with WP1.3 together with the Phase 3 handlers.
REMOVED_JOB_KINDS: tuple[str, ...] = (
    "turn.extract",
    "session.checkpoint",
    "session.finish_artifacts",
    "artifact.refresh_cheatsheet",
    "graph.curate",
    "account.purge_deleted",
)
KIND_SPEC: dict[str, JobKindSpec] = {s.kind: s for s in JOB_KIND_SPECS}
KIND_LLM_ROLES: dict[str, tuple[str, ...]] = {s.kind: s.roles for s in JOB_KIND_SPECS}
VISIBLE_KINDS: frozenset[str] = frozenset(s.kind for s in JOB_KIND_SPECS if s.visible)

# §7.1 check.symbolic types; the others of `AnswerType` are instant (WP11.2) or slow (WP11.6).
FAST_ANSWER_KINDS: frozenset[str] = frozenset({"expression", "quantity"})
SLOW_ANSWER_KINDS: frozenset[str] = frozenset({"proof", "free_text", "drawing"})
# D-B-121: an answer without parts is the part `main`.
MAIN_PART = "main"
# Task sources of the bank, whose solution space is built ahead in the background (WP12.4);
# `teacher` tasks on the fly get it lazily through `agent.requested`.
BANK_TASK_SOURCES: frozenset[str] = frozenset({"seminar", "generated", "material"})
# AgentKind -> job kind. `known_check` has no agent kind in §6.5: it is `check.known_claim`, the
# kind of `known.claimed` (B's answer to D-A-110 p.7).
AGENT_JOB_KINDS: dict[str, str] = {
    "viz": "agent.viz",
    "illustration": "agent.illustration",
    "task_generation": "agent.task_generation",
    "solution_space": "agent.solution_space",
    "artifact": "agent.artifact",
    "known_check": "check.known_claim",
    "topic_split": "agent.topic_split",
}


@dataclass(frozen=True)
class JobPlan:
    """One row of `ops.jobs` before the insert (Т-§13.1). `job_key` is deterministic; for
    coalescing kinds it is `{coalesce_key}:{seq}`. `required_seq` None — reads no graph."""

    kind: str
    job_key: str
    priority_class: PriorityClass
    learner_id: UUID | None  # None — a periodic job of no learner (WP7.5)
    lock_key: str | None = None
    coalesce_key: str | None = None
    required_seq: int | None = None
    run_at: datetime | None = None
    session_id: UUID | None = None
    visible: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


def _plan(
    event: StoredEvent,
    kind: str,
    *,
    key_tail: str | None = None,
    cls: PriorityClass | None = None,
    lock_key: str | None = None,
    coalesce_key: str | None = None,
    graph: bool = False,
    run_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> JobPlan:
    spec = KIND_SPEC[kind]
    if coalesce_key is not None:
        job_key = f"{coalesce_key}:{event.seq}"
    else:
        job_key = f"{kind}:{event.learner_id}:{key_tail or event.seq}"
    return JobPlan(
        kind=kind,
        job_key=job_key,
        priority_class=cls or spec.priority_class,
        learner_id=event.learner_id,
        lock_key=lock_key,
        coalesce_key=coalesce_key,
        required_seq=event.seq if graph else None,
        run_at=run_at,
        session_id=event.session_id,
        visible=spec.visible,
        payload={"trigger_seq": event.seq, **(payload or {})},
    )


# --- rows --------------------------------------------------------------------------------

type Planner = Callable[[StoredEvent, Settings, Any], list[JobPlan]]


@dataclass(frozen=True)
class RuleFacts:
    """Data beyond the event for the G2 rows (WP7.1; D-A-136), gathered by the dispatcher:
    `session.ended` — the home topics of the skills of the session's exposures and evidence;
    `task.closed` — the home topics of the task's skills when a hint was shown on it."""

    topic_ids: tuple[UUID, ...] = ()
    # `skill.split`: episodes whose current observation holds evidence of the old skill.
    episode_seqs: tuple[int, ...] = ()
    # `session.ended`, `topic.created` (graph.hygiene): the domain, the temporary entities of the
    # session, the counter of new topics of the domain having reached its period.
    domain_id: UUID | None = None
    temporary_entities: bool = False
    hygiene_due: bool = False


type FactsPlanner = Callable[[StoredEvent, Any, RuleFacts], list[JobPlan]]


@dataclass(frozen=True)
class RuleRow:
    """A row of the WP7.1 table. `plan` — the row plans from the event alone; `plan_facts` —
    from the event and the data beyond it the dispatcher gathers (`needs`, `RuleFacts`); neither
    — the row is still to be implemented in G2."""

    event_type: str
    job_kinds: tuple[str, ...]
    condition: str
    plan: Planner | None = None
    needs: str = ""
    plan_facts: FactsPlanner | None = None


def _episode_closed(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    if not p.has_content:
        return []  # the dispatcher itself writes `nothing_to_extract` coverage (WP7.2, WP7.3)
    return [
        _plan(
            event,
            "extract.episode",
            graph=True,
            payload={"from_seq": p.from_seq, "to_seq": p.to_seq},
        )
    ]


def _analyst(event: StoredEvent, _s: Settings, _p: Any, *, deep: bool) -> list[JobPlan]:
    plans = [
        _plan(
            event,
            "analyst.run",
            lock_key=f"analyst:{event.learner_id}",
            coalesce_key=f"analyst:{event.learner_id}",
            graph=True,
        )
    ]
    if deep:
        # Own coalesce key: with the shared one `analyst.deep` would merge into `analyst.run`.
        plans.append(
            _plan(
                event,
                "analyst.deep",
                lock_key=f"analyst:{event.learner_id}",
                coalesce_key=f"analyst_deep:{event.learner_id}",
                graph=True,
            )
        )
    return plans


def _task_closed(event: StoredEvent, s: Settings, p: Any) -> list[JobPlan]:
    return _analyst(event, s, p, deep=False)


def _session_ended(event: StoredEvent, s: Settings, p: Any) -> list[JobPlan]:
    return _analyst(event, s, p, deep=True)


def _answer_part(p: Any) -> str:
    return getattr(p, "part", None) or MAIN_PART


def _answer_submitted(event: StoredEvent, s: Settings, p: Any) -> list[JobPlan]:
    part = _answer_part(p)
    answer = p.answer
    # "Numbers as expressions": a `number` the learner typed as an expression carries `sympy`.
    fast = answer.kind in FAST_ANSWER_KINDS or (answer.kind == "number" and bool(answer.sympy))
    if fast:
        return [
            _plan(
                event,
                "check.symbolic",
                key_tail=f"{event.seq}:{part}",
                run_at=event.occurred_at + timedelta(milliseconds=s.symbolic_check_delay_ms),
                payload={"answer_seq": event.seq, "part": part},
            )
        ]
    if answer.kind in SLOW_ANSWER_KINDS:
        return [_check_model(event, event.seq, part)]
    return []  # instant types: the verdict is written in the transaction of the answer


def _check_model(event: StoredEvent, answer_seq: int, part: str) -> JobPlan:
    # One key per (answer, part): the slow path and the `check.failed` cascade never double.
    return _plan(
        event,
        "check.model",
        key_tail=f"{answer_seq}:{part}",
        payload={"answer_seq": answer_seq, "part": part},
    )


def _check_failed(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    return [_check_model(event, p.answer_seq, p.part)]


def _turn_committed(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    task_ids = [str(t.task_id) for t in p.markup.tasks]
    if not task_ids:
        return []
    return [_plan(event, "task.check", graph=False, payload={"task_ids": task_ids})]


def _manifests(p: Any, event_type: str) -> list[UUID | None]:
    """Misconception ids of a manifestation: an id, or None for a hypothesis in words."""
    if event_type == "verdict.recorded":
        if p.misconception_id is not None:
            return [p.misconception_id]
        return [None] if p.misconception_hypothesis else []
    if event_type == "observation.diagnosed":
        d = p.diagnosis
        if d.misconception_id is not None:
            return [d.misconception_id]
        return [None] if d.misconception_hypothesis else []
    found: list[UUID | None] = []
    for e in p.elements:
        if e.misconception_id is not None:
            found.append(e.misconception_id)
        elif e.new_misconception or e.kind == "misconception_hypothesis":
            found.append(None)
    return found


def _manifestation(event: StoredEvent, s: Settings, p: Any) -> list[JobPlan]:
    ids = _manifests(p, event.type)
    if not ids:
        return []
    plans = _analyst(event, s, p, deep=False)
    # `misconception.describe` needs the node: a hypothesis without an id gets none (D-A-110).
    for mid in sorted({i for i in ids if i is not None}, key=str):
        plans.append(
            _plan(
                event,
                "misconception.describe",
                key_tail=f"{event.seq}:{mid}",
                lock_key=f"misconception:{mid}",
                graph=True,
                payload={"misconception_id": str(mid)},
            )
        )
    return plans


def _diagnosed(event: StoredEvent, s: Settings, p: Any) -> list[JobPlan]:
    plans = _manifestation(event, s, p)
    # Т-§15.4: an uncertain diagnosis is `question`; it goes to the second opinion.
    if p.diagnosis.kind == "question":
        plans.append(_plan(event, "second_opinion.diagnosis", graph=True))
    return plans


def _photo_uploaded(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    lock = f"canvas:{p.canvas_id}" if p.canvas_id else f"photo:{p.photo_id}"
    return [_plan(event, "photo.pipeline", lock_key=lock, payload={"photo_id": str(p.photo_id)})]


def _material_uploaded(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    cls: PriorityClass = "P1" if p.session_id is not None else "P2"
    return [_plan(event, "material.parse", cls=cls, payload={"material_id": str(p.material_id)})]


def _task_created(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    if p.source not in BANK_TASK_SOURCES:
        return []
    return [_plan(event, "task.solution_space", payload={"task_id": str(p.task_id)})]


def _agent_requested(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    kind = AGENT_JOB_KINDS[p.kind]
    cls: PriorityClass = "P1" if p.needed_in_session else "P2"
    payload: dict[str, Any] = {"request_id": str(p.request_id), "kind": p.kind}
    if kind == "check.known_claim":
        # The same job as `known.claimed`: it checks the skills of a topic (WP8.2), so a
        # request without a topic has nothing to check (review B of #117).
        if p.params.topic_id is None:
            return []
        payload["topic_id"] = str(p.params.topic_id)
        return [_plan(event, kind, cls=cls, graph=True, payload=payload)]
    if kind == "agent.topic_split":
        # WP8.2 (D-A-226): the topic to split is the parameter of the request; the job writes
        # to the graph, so it holds the lock of the domain (as the other graph writers do).
        if p.params.topic_id is None:
            return []
        payload["topic_id"] = str(p.params.topic_id)
        return [_plan(event, kind, cls=cls, lock_key=f"domain:{event.domain_id}", graph=True,
                      payload=payload)]  # fmt: skip
    if p.kind == "artifact" and p.params.artifact == "cheatsheet":
        # Т-§17.7 «генерация — после согласия», D-A-199: the consent to the cheat sheet of a hub
        # is the job of that hub, with the request to answer; without a hub nothing to build.
        if p.params.hub_id is None:
            return []
        key = f"artifact:{p.params.hub_id}"
        payload["hub_id"] = str(p.params.hub_id)
        return [_plan(event, "artifact.cheatsheet", cls=cls, lock_key=key, coalesce_key=key,
                      graph=True, payload=payload)]  # fmt: skip
    return [_plan(event, kind, cls=cls, payload=payload)]


def _today_shown(event: StoredEvent, _s: Settings, _p: Any) -> list[JobPlan]:
    key = f"today:{event.learner_id}"
    return [_plan(event, "today.prepare", lock_key=key, coalesce_key=key, graph=True)]


def _digest_written(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    if not p.plan_divergence:
        return []
    return [
        _plan(
            event,
            "analyst.deep",
            lock_key=f"analyst:{event.learner_id}",
            coalesce_key=f"analyst_deep:{event.learner_id}",
            graph=True,
        )
    ]


def _handwriting_corrected(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    return [
        _plan(
            event,
            "canvas.rediagnose",
            key_tail=f"{event.seq}:{p.line_id}",
            payload={"canvas_id": str(p.canvas_id), "line_id": p.line_id},
        )
    ]


def _known_claimed(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    return [_plan(event, "check.known_claim", graph=True, payload={"topic_id": str(p.topic_id)})]


def _episode_reassigned(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    return [
        _plan(
            event,
            "extract.episode",
            graph=True,
            payload={"episode_seq": p.episode_seq, "topic_id": str(p.topic_id)},
        )
    ]


def _deadline_cheatsheet(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    """Т-§17.7, WP23.5 (D-A-139): every `hub.deadline_set` puts one `artifact.cheatsheet` job
    on the hub; the job reads the journal and itself checks `cheatsheet_offer_days` and the
    state of the offer (consent `agent.requested` / `artifact.updated`), per C's D-C-138."""
    key = f"artifact:{p.hub_id}"
    return [
        _plan(
            event,
            "artifact.cheatsheet",
            lock_key=key,
            coalesce_key=key,
            graph=True,
            payload={"hub_id": str(p.hub_id)},
        )
    ]


def _topic_refresh(
    event: StoredEvent, topic: UUID, key_tail: str, payload: dict[str, Any]
) -> JobPlan:
    """Т-§17.7: `artifact:{topic}` for the lock — one writer of a topic at a time (WP7.1); the
    job key cuts doubles — one refresh of a topic per session, one traps block per task
    (D-A-136). The coalesce key adds the branch (`session:…` / `task:…`): with one key for
    both, `enqueue_job` would overwrite the payload of the waiting job (D-A-138)."""
    key = f"artifact:{topic}"
    plan = _plan(
        event,
        "artifact.topic_refresh",
        lock_key=key,
        coalesce_key=f"{key}:{key_tail}",
        graph=True,
        payload={"topic_id": str(topic), **payload},
    )
    return replace(plan, job_key=f"artifact.topic_refresh:{topic}:{key_tail}")


def _session_topics_refresh(event: StoredEvent, _p: Any, facts: RuleFacts) -> list[JobPlan]:
    tail = f"session:{event.session_id}"
    return [_topic_refresh(event, t, tail, {"reason": "session"}) for t in facts.topic_ids]


def _hinted_topics_refresh(event: StoredEvent, p: Any, facts: RuleFacts) -> list[JobPlan]:
    payload = {"reason": "hints", "task_id": str(p.task_id)}
    return [_topic_refresh(event, t, f"task:{p.task_id}", payload) for t in facts.topic_ids]


def _skill_split_reextract(event: StoredEvent, p: Any, facts: RuleFacts) -> list[JobPlan]:
    """`Т-§2.8`: the episodes where the old skill has evidence are interpreted again, the
    candidates restricted to the parts (D-A-135). One job per episode."""
    parts = [str(part.skill_id) for part in p.into]
    return [
        _plan(
            event,
            "extract.episode",
            key_tail=f"{event.seq}:{episode_seq}",
            graph=True,
            payload={"episode_seq": episode_seq, "restrict_skill_ids": parts},
        )
        for episode_seq in facts.episode_seqs
    ]


def _hygiene(event: StoredEvent, domain_id: UUID, reason: str) -> JobPlan:
    """`Т-§4.10`: one waiting hygiene job per domain; `domain:{id}` is the lock of the writes to
    the structure of the domain (WP7.1)."""
    return _plan(
        event,
        "graph.hygiene",
        lock_key=f"domain:{domain_id}",
        coalesce_key=f"hygiene:{domain_id}",
        graph=True,
        payload={"domain_id": str(domain_id), "reason": reason},
    )


def _session_hygiene(event: StoredEvent, _p: Any, facts: RuleFacts) -> list[JobPlan]:
    if not facts.temporary_entities or facts.domain_id is None:
        return []
    return [_hygiene(event, facts.domain_id, "temporary_entities")]


def _topics_hygiene(event: StoredEvent, p: Any, facts: RuleFacts) -> list[JobPlan]:
    if not facts.hygiene_due:
        return []
    return [_hygiene(event, p.domain_id, "new_topics")]


def _embedding(event: StoredEvent, node_kind: str, node_id: UUID, text: str) -> list[JobPlan]:
    """D-A-117: the text rides in the job — the dispatcher takes it from the event that brought
    it, the handler reads nothing; `required_seq` is empty (the job reads only the log)."""
    nodes = [{"node_kind": node_kind, "node_id": str(node_id), "text": text}]
    return [_plan(event, "embedding.compute", payload={"nodes": nodes})]


def _chunk_indexed(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    return _embedding(event, "chunk", p.chunk_id, p.text)


def _misconception_described(event: StoredEvent, _s: Settings, p: Any) -> list[JobPlan]:
    return _embedding(event, "misconception", p.misconception_id, p.statement)


RULES: tuple[RuleRow, ...] = (
    RuleRow("episode.closed", ("extract.episode",), "has_content", _episode_closed),
    RuleRow("task.closed", ("analyst.run",), "always", _task_closed),
    RuleRow("session.ended", ("analyst.run", "analyst.deep"), "always", _session_ended),
    RuleRow(
        "observation.extracted",
        ("analyst.run", "misconception.describe"),
        "an element shows a misconception",
        _manifestation,
    ),
    RuleRow(
        "verdict.recorded",
        ("analyst.run", "misconception.describe"),
        "the verdict names a misconception",
        _manifestation,
    ),
    RuleRow(
        "observation.diagnosed",
        ("analyst.run", "misconception.describe", "second_opinion.diagnosis"),
        "a misconception shown; diagnosis.kind == question -> second opinion",
        _diagnosed,
    ),
    RuleRow(
        "answer.submitted",
        ("check.symbolic", "check.model"),
        "fast type -> check.symbolic; proof/free_text/drawing -> check.model",
        _answer_submitted,
    ),
    RuleRow("check.failed", ("check.model",), "always", _check_failed),
    RuleRow("turn.committed", ("task.check",), "markup.tasks is not empty", _turn_committed),
    RuleRow("photo.uploaded", ("photo.pipeline",), "always", _photo_uploaded),
    RuleRow(
        "material.uploaded", ("material.parse",), "P1 with session_id, else P2", _material_uploaded
    ),
    RuleRow("task.created", ("task.solution_space",), "source of the bank", _task_created),
    RuleRow(
        "agent.requested",
        (*AGENT_JOB_KINDS.values(), "artifact.cheatsheet"),
        "P1 if needed_in_session; known_check -> check.known_claim; a cheat sheet -> "
        "artifact.cheatsheet of its hub",
        _agent_requested,
    ),
    RuleRow("today.shown", ("today.prepare",), "always", _today_shown),
    RuleRow("digest.written", ("analyst.deep",), "plan_divergence", _digest_written),
    RuleRow("handwriting.corrected", ("canvas.rediagnose",), "always", _handwriting_corrected),
    RuleRow("known.claimed", ("check.known_claim",), "always", _known_claimed),
    RuleRow("episode.reassigned", ("extract.episode",), "always", _episode_reassigned),
    # G2 rows that read data beyond the event.
    RuleRow(
        "skill.split",
        ("extract.episode",),
        "one job per episode where the old skill has evidence, candidates restricted to the "
        "parts: payload {episode_seq, restrict_skill_ids} (Т-§2.8; D-A-135)",
        needs="episodes of the current observations with evidence of the old skill",
        plan_facts=_skill_split_reextract,
    ),
    RuleRow(
        "session.ended",
        ("artifact.topic_refresh",),
        "the session has exposures or evidence on the topic (Т-§17.7)",
        needs="home topics of the skills of the session's exposures and evidence",
        plan_facts=_session_topics_refresh,
    ),
    RuleRow(
        "task.closed",
        ("artifact.topic_refresh",),
        "hints were needed (Т-§17.7)",
        needs="hint.shown of the task; home topics of the task's skills",
        plan_facts=_hinted_topics_refresh,
    ),
    RuleRow(
        "hub.deadline_set",
        ("artifact.cheatsheet",),
        "always; the job checks cheatsheet_offer_days and the offer state (Т-§17.7, D-C-138)",
        _deadline_cheatsheet,
    ),
    RuleRow(
        "chunk.indexed", ("embedding.compute",), "always: the text of the chunk", _chunk_indexed
    ),
    RuleRow(
        "misconception.described",
        ("embedding.compute",),
        "always: the statement of the misconception",
        _misconception_described,
    ),
    RuleRow(
        "*",
        ("embedding.compute",),
        "new topic, skill or evidence with text",
        needs="topic and skill: which property is embedded (`name`, for a skill `summary` too) "
        "is [ПРОВЕРИТЬ] in Т-§3.4.2 (D-A-117); evidence: its id is given by the projector, not "
        "the event",
    ),
    RuleRow(
        "session.ended",
        ("graph.hygiene",),
        "the session has temporary entities (a described new skill, a provisional skill)",
        needs="new skill descriptions and provisional skills of the session; its domain",
        plan_facts=_session_hygiene,
    ),
    RuleRow(
        "topic.created",
        ("graph.hygiene",),
        "every graph_hygiene_every_topics new topics of the domain (counter of the mark)",
        needs="counters of the dispatcher mark",
        plan_facts=_topics_hygiene,
    ),
)

ROWS_NEED_FACTS: tuple[RuleRow, ...] = tuple(
    r for r in RULES if r.plan is None and r.plan_facts is None
)


def cancelled_requests(event: StoredEvent) -> list[UUID]:
    """Agent requests the teacher's `planUpdate` cancels (WP7.4, Т-§13.7; D-A-115). The
    dispatcher sets `cancel_requested` on their running jobs and `cancelled` on queued ones."""
    if event.type != "turn.committed":
        return []
    payload = TurnCommitted.model_validate(event.payload)
    return list(payload.markup.plan_update.cancel_request_ids)


def plan_with_facts(event: StoredEvent, settings: Settings, facts: RuleFacts) -> list[JobPlan]:
    """Jobs of the event-only rows plus the G2 rows that read `facts`."""
    plans = plan_jobs(event, settings)
    rows = [r for r in RULES if r.event_type == event.type and r.plan_facts is not None]
    if rows:
        payload = registry.validate_payload(event.type, event.payload, event.schema_version)
        for row in rows:
            assert row.plan_facts is not None
            plans.extend(row.plan_facts(event, payload, facts))
    return plans


def plan_jobs(event: StoredEvent, settings: Settings) -> list[JobPlan]:
    """Jobs the event-only rows of the table put for the event."""
    rows = [r for r in RULES if r.event_type == event.type and r.plan is not None]
    if not rows:
        return []
    payload = registry.validate_payload(event.type, event.payload, event.schema_version)
    plans: list[JobPlan] = []
    for row in rows:
        assert row.plan is not None
        plans.extend(row.plan(event, settings, payload))
    return plans
