"""The write of a teacher turn (Т-§2.2, Т-§3.2, Т-§4.5; ТЗ Ф4 WP2.2, WP13.3, WP13.9).

`turn_batch` translates the labels of the turn output to ids (an unknown label —
`UnknownLabelError`, nothing is written) and builds the batch: `task.created` of the tasks
the turn shows, `turn.committed`, `agent.requested` of its requests, a deadline heard in the
conversation (`hub.created` and `hub.topics_changed` of a new hub, `hub.deadline_set`) — only
when the learner's words the model quotes are in a learner message of the session. What the
turn answered adds `hint.shown` (a hint request; the step is counted by code) and
`task.closed` (solved, skipped, solution shown). D-B-149. `commit_turn` writes the batch with
one `append_events` call, the documents of the new tasks and the `ops.turn_manifests` row in
one transaction, with the session plan document (`content.session_plans`, WP13.6): a failure
writes none of them; a repeat of the same batch writes nothing new.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb
from pydantic import BaseModel, TypeAdapter
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.event_types import (
    AgentRequested,
    DegradedMode,
    HintShown,
    HubCreated,
    HubDeadlineSet,
    HubTopic,
    HubTopicsChanged,
    TaskClosed,
    TurnCommitted,
)
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec
from quack.graph.ids import QUACK_UUID_NS
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger
from quack.practice.markup import markup_record
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import get_task, insert_task_document, task_created_event
from quack.session.elements import Element
from quack.session.teacher_output import AgentRequest, DeadlineFromTalk, TurnHead, TurnMarkup
from quack.session.turn_graph import TurnToCommit

log = get_logger(__name__)

_ELEMENT_RECORD: TypeAdapter[records.ElementRecord] = TypeAdapter(records.ElementRecord)

_INSERT_MANIFEST = """
INSERT INTO ops.turn_manifests (
    manifest_id, learner_id, session_id, prompt_version, doctrine_version, renderer_version,
    role, model, effort, projected_seq, overlay_seqs, label_table, digest_id, chunk_ids,
    similar_ids, context_text, degraded_mode, frozen_slots
) VALUES (
    %(manifest_id)s, %(learner_id)s, %(session_id)s, %(prompt_version)s, %(doctrine_version)s,
    %(renderer_version)s, %(role)s, %(model)s, %(effort)s, %(projected_seq)s,
    %(overlay_seqs)s, %(label_table)s, %(digest_id)s, %(chunk_ids)s, %(similar_ids)s,
    %(context_text)s, %(degraded_mode)s, %(frozen_slots)s
)
ON CONFLICT (manifest_id) DO NOTHING
"""

# WP13.6: the plan document of the session follows the turn; a session of another learner
# is never touched (no row comes back).
_UPSERT_PLAN = """
INSERT INTO content.session_plans (session_id, learner_id, plan, status, updated_at)
VALUES (%(session_id)s, %(learner_id)s, %(plan)s, 'active', now())
ON CONFLICT (session_id) DO UPDATE SET plan = EXCLUDED.plan, status = 'active', updated_at = now()
WHERE content.session_plans.learner_id = EXCLUDED.learner_id
RETURNING session_id
"""

_CURRENT_PLAN = (
    "SELECT plan FROM content.session_plans "
    "WHERE session_id = %(session_id)s AND learner_id = %(learner_id)s FOR UPDATE"
)

# Hints shown for the task in the session before this turn (Т-§7.4: steps 1–3).
_PRIOR_HINT_STEP = (
    "SELECT coalesce(max((payload->>'step')::int), 0) AS step FROM core.events "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND type = 'hint.shown' AND payload->>'task_id' = %(task_id)s "
    "AND idempotency_key <> %(key)s"
)
_TASK_CLOSED = (
    "SELECT count(*) AS n FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'task.closed' AND payload->>'task_id' = %(task_id)s "
    "AND idempotency_key <> %(key)s"
)
HINT_STEPS = 3


class _Step(BaseModel):
    step: int


class _Count(BaseModel):
    n: int


@dataclass(frozen=True)
class LearnerText:
    """A learner message of the session (`message.sent`): what a quote may come from."""

    seq: int
    text: str


@dataclass(frozen=True)
class AnsweredAction:
    """The action the turn answers, as far as it changes a task (D-B-149): a hint request
    gets the next hint step; a solution request closes the task `solution_shown`; a skipped
    check — `skipped`; an answer with every part correct by the time of the turn
    (`solved`, computed by the caller from the verdicts) — `solved`."""

    kind: Literal["hint_request", "solution_request", "check_skip", "answer"]
    task_id: UUID
    solved: bool = False


@dataclass(frozen=True)
class TurnManifestRow:
    """A row of `ops.turn_manifests` (Т-§3.2, Т-§10.6, WP13.9); `context_text` — only for the
    `turn_context_sample_rate` sample."""

    manifest_id: UUID
    learner_id: UUID
    session_id: UUID
    prompt_version: str
    doctrine_version: str | None
    renderer_version: str | None
    role: str
    model: str
    effort: str | None
    projected_seq: int
    overlay_seqs: list[int] = field(default_factory=list)
    label_table: dict[str, str] = field(default_factory=dict)
    digest_id: UUID | None = None
    chunk_ids: list[UUID] = field(default_factory=list)
    similar_ids: list[UUID] = field(default_factory=list)
    context_text: str | None = None
    degraded_mode: DegradedMode | None = None
    # Slots 3 and 4 frozen at the session start (Т-§10.1, D-A-137): only on the turn that
    # froze them; later turns read the earliest row of the session that has them.
    frozen_slots: dict[str, str] | None = None


@dataclass(frozen=True)
class NewTurnTask:
    """The document of a task the turn shows (`content.tasks`), stored with its
    `task.created` (`idempotency_key`)."""

    task_id: UUID
    condition: TaskCondition
    answer_spec: AnswerSpec
    idempotency_key: str


@dataclass(frozen=True)
class TurnBatch:
    """`events` — in order, without the help events; `hint_task_id` / `close` — the help
    events `commit_turn` adds after reading the session inside its transaction."""

    events: list[NewEvent]
    tasks: list[NewTurnTask]
    manifest: TurnManifestRow
    domain_id: UUID | None
    common: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    hint_task_id: UUID | None = None
    close: tuple[UUID, Literal["solved", "skipped", "solution_shown"]] | None = None


def turn_batch(
    turn: TurnToCommit,
    labels: LabelMap,
    *,
    manifest: TurnManifestRow,
    action_seq: int,
    domain_id: UUID | None,
    settings: Settings,
    answered: AnsweredAction | None = None,
    learner_texts: Sequence[LearnerText] = (),
    learner_timezone: str | None = None,
) -> TurnBatch:
    """The events of the turn with ids instead of labels (WP2.2, WP13.3)."""
    output = turn.output
    key = f"turn:{manifest.manifest_id}"
    common: dict[str, Any] = {
        "actor": manifest.prompt_version,
        "domain_id": domain_id,
        "session_id": manifest.session_id,
        "causation_seq": action_seq,
    }
    elements = {e.element_id: e for e in output.p3_markup.elements}
    tasks: list[NewTurnTask] = []
    task_events: list[NewEvent] = []
    task_records: list[records.TurnTaskRecord] = []
    for task in output.p3_markup.tasks:
        task_id = uuid4()
        markup = markup_record(task, labels)
        task_key = f"{key}:task:{task.element_id}"
        task_records.append(
            records.TurnTaskRecord(
                **markup.model_dump(), element_id=task.element_id, task_id=task_id
            )
        )
        # D-B-149: a task of the turn is its own family; its condition is the element text.
        task_events.append(
            task_created_event(
                task_id=task_id,
                family_id=task_id,
                source="teacher",
                markup=markup,
                verification="unchecked",
                idempotency_key=task_key,
                **common,
            )
        )
        tasks.append(
            NewTurnTask(
                task_id=task_id,
                condition=TaskCondition(text=_condition(elements.get(task.element_id)), latex=None),
                answer_spec=markup.answer,
                idempotency_key=task_key,
            )
        )
    requests = [
        _request_record(r, labels) for r in output.p3_markup.agent_requests if _requestable(r)
    ]
    if (split := _split_request(output.p1_head, labels, key)) is not None:
        requests.append(split)
    deadline, deadline_events = (None, [])
    if output.p3_markup.deadline is not None:
        deadline, deadline_events = _deadline(
            output.p3_markup.deadline,
            labels,
            learner_texts,
            learner_timezone=learner_timezone,
            settings=settings,
            key=f"{key}:deadline",
            common=common,
        )
    markup = _turn_markup(output.p3_markup, labels, task_records, requests, deadline)
    head = output.p1_head
    committed = TurnCommitted(
        action_seq=action_seq,
        text=turn.text,
        blocks=[records.BodyBlockRecord(**b.model_dump()) for b in output.p2_body.blocks],
        head=records.TurnHeadRecord(
            intent=head.intent,
            target_skill_ids=[labels.resolve("skill", s) for s in head.target_skills],
            verdict_stance=head.verdict_stance,
            branch_type=head.branch_type,
            approach=head.approach,
        ),
        markup=markup,
        role=turn.role,
        model=turn.model,
        effort=turn.effort,
        degraded_mode=turn.degraded_mode,
        guard_retries=turn.guard_retries,
        manifest_id=manifest.manifest_id,
    )
    events = [
        *task_events,
        NewEvent(type="turn.committed", payload=committed, idempotency_key=key, **common),
    ]
    for index, request in enumerate(requests):
        payload = AgentRequested(**request.model_dump())
        # the split of a topic is keyed by the turn and the topic (WP8.2, D-B-213)
        request_key = (
            f"{key}:agent:topic_split:{request.params.topic_id}"
            if request.kind == "topic_split"
            else f"{key}:agent:{index}"
        )
        events.append(
            NewEvent(type="agent.requested", payload=payload, idempotency_key=request_key, **common)
        )
    events.extend(deadline_events)
    hint_task_id, close = None, None
    if answered is not None:
        match answered.kind:
            case "hint_request":
                hint_task_id = answered.task_id
            case "solution_request":
                close = (answered.task_id, "solution_shown")
            case "check_skip":
                close = (answered.task_id, "skipped")
            case "answer" if answered.solved:
                close = (answered.task_id, "solved")
            case "answer":
                pass
    return TurnBatch(
        events=events,
        tasks=tasks,
        manifest=manifest,
        domain_id=domain_id,
        common=common,
        plan=markup.plan_update.model_dump(mode="json"),
        hint_task_id=hint_task_id,
        close=close,
    )


def _deadline(
    deadline: DeadlineFromTalk,
    labels: LabelMap,
    learner_texts: Sequence[LearnerText],
    *,
    learner_timezone: str | None,
    settings: Settings,
    key: str,
    common: dict[str, Any],
) -> tuple[records.DeadlineRecord | None, list[NewEvent]]:
    """A deadline is written only from the learner's words (law 6, П-8.1): the quote must be
    in a learner message of the session; the events point at it (`source_refs`). An existing
    hub gets the date; a new one is created from the learner's request (`kind: request`,
    `origin: learner`) with its coverage and the levels of its control type (Т-§4.8)."""
    quote = deadline.quote.strip()
    source = next((t for t in learner_texts if quote and quote in t.text), None)
    if source is None:
        log.warning("turn_deadline_ungrounded", quote=deadline.quote)
        return None, []
    try:
        at = datetime.fromisoformat(deadline.deadline_at)
    except ValueError:
        log.warning("turn_deadline_bad_date", deadline_at=deadline.deadline_at)
        return None, []
    if at.tzinfo is None:
        # Т-§14.6: the learner's days are in their zone (`core.learners.settings`).
        zone = _zone(learner_timezone)
        if zone is None:
            log.warning("turn_deadline_no_timezone", deadline_at=deadline.deadline_at)
            return None, []
        at = at.replace(tzinfo=zone)
    coverage = [labels.resolve("topic", t) for t in deadline.coverage]
    refs = [{"seq": source.seq}]
    events: list[NewEvent] = []
    if deadline.hub_label is not None:
        hub_id = labels.resolve("hub", deadline.hub_label)
    elif deadline.new_hub_name and common["domain_id"] is not None:
        hub_id = uuid4()
        control = deadline.control_type or "no_goal"
        events.append(
            NewEvent(
                type="hub.created",
                payload=HubCreated(
                    hub_id=hub_id,
                    domain_id=common["domain_id"],
                    name=deadline.new_hub_name,
                    kind="request",
                    origin="learner",
                    control_type=control,
                ),
                idempotency_key=f"{key}:hub",
                source_refs=refs,
                **common,
            )
        )
        if coverage:
            levels = settings.control_type_levels[control]
            events.append(
                NewEvent(
                    type="hub.topics_changed",
                    payload=HubTopicsChanged(
                        hub_id=hub_id,
                        topics=[HubTopic(topic_id=t, required_levels=levels) for t in coverage],
                    ),
                    idempotency_key=f"{key}:topics",
                    source_refs=refs,
                    **common,
                )
            )
    else:
        log.warning("turn_deadline_without_hub", new_hub_name=deadline.new_hub_name)
        return None, []
    deadline_at_ms = int(at.timestamp() * 1000)
    events.append(
        NewEvent(
            type="hub.deadline_set",
            payload=HubDeadlineSet(hub_id=hub_id, deadline_at_ms=deadline_at_ms, source="turn"),
            idempotency_key=key,
            source_refs=refs,
            **common,
        )
    )
    record = records.DeadlineRecord(
        hub_id=hub_id,
        new_hub_name=deadline.new_hub_name,
        deadline_at_ms=deadline_at_ms,
        coverage_topic_ids=coverage,
    )
    return record, events


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


async def commit_turn(
    db: Db, batch: TurnBatch, *, learner_id: UUID, redis: Redis | None = None
) -> int:
    """Write the batch, the task documents and the manifest row in one transaction; the seq
    of `turn.committed`. A retry of the write passes the same `TurnBatch` object: a batch
    built again has new ids (tasks, requests, hub) under the same keys and conflicts."""
    manifest = batch.manifest
    if manifest.learner_id != learner_id:
        raise ValueError("the manifest belongs to another learner")
    async with db.transaction():
        events = [*batch.events, *await _help_events(db, batch, learner_id)]
        stored = await append_events(db, learner_id, events, redis=redis)
        by_key = {event.idempotency_key: event for event in stored}
        for task in batch.tasks:
            if await get_task(db, learner_id=learner_id, task_id=task.task_id):
                continue
            await insert_task_document(
                db,
                learner_id=learner_id,
                task_id=task.task_id,
                domain_id=batch.domain_id,
                source="teacher",
                condition=task.condition,
                answer_spec=task.answer_spec,
                created_seq=by_key[task.idempotency_key].seq,
            )
        await db.execute(_INSERT_MANIFEST, _manifest_params(manifest))
        keys = {"session_id": manifest.session_id, "learner_id": learner_id}
        current = await db.fetch_one(_CURRENT_PLAN, keys)
        plan = _with_history(batch.plan, current["plan"] if current else None)
        planned = await db.fetch_one(_UPSERT_PLAN, {**keys, "plan": Jsonb(plan)})
        if planned is None:
            raise LookupError(f"session {manifest.session_id} is not the learner's")
    [turn] = [event for event in stored if event.type == "turn.committed"]
    return turn.seq


def _with_history(plan: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    """П-4.6: done steps are a history that does not change — the done steps of the document
    stay in order, the turn only adds new ones."""
    if current is None:
        return plan
    done = list(current.get("completed_steps", []))
    done += [s for s in plan["completed_steps"] if s not in done]
    return {**plan, "completed_steps": done}


async def _help_events(db: Db, batch: TurnBatch, learner_id: UUID) -> list[NewEvent]:
    """`hint.shown` with the next step (at most `HINT_STEPS`) and `task.closed` unless the
    task is closed already; read inside the write transaction."""
    key = f"turn:{batch.manifest.manifest_id}"
    events: list[NewEvent] = []
    if batch.hint_task_id is not None:
        prior = await db.fetch_one(
            _PRIOR_HINT_STEP,
            {
                "learner_id": learner_id,
                "session_id": batch.manifest.session_id,
                "task_id": str(batch.hint_task_id),
                "key": f"{key}:hint",
            },
            row=_Step,
        )
        step = min((prior.step if prior else 0) + 1, HINT_STEPS)
        events.append(
            NewEvent(
                type="hint.shown",
                payload=HintShown(task_id=batch.hint_task_id, step=step, source="teacher"),
                idempotency_key=f"{key}:hint",
                **batch.common,
            )
        )
    if batch.close is not None:
        task_id, how = batch.close
        closed = await db.fetch_one(
            _TASK_CLOSED,
            {"learner_id": learner_id, "task_id": str(task_id), "key": f"{key}:close"},
            row=_Count,
        )
        if closed is None or closed.n == 0:
            events.append(
                NewEvent(
                    type="task.closed",
                    payload=TaskClosed(task_id=task_id, how=how),
                    idempotency_key=f"{key}:close",
                    **batch.common,
                )
            )
    return events


# --- labels → ids (Т-§4.5) ---


def _optional(labels: LabelMap, kind: Any, label: str | None) -> UUID | None:
    return None if label is None else labels.resolve(kind, label)


def _requestable(request: AgentRequest) -> bool:
    """`known_check` is `check.known_claim` over the skills of a topic (WP8.2): without a topic
    label there is nothing to check (review B of #117)."""
    if request.kind == "known_check" and request.params.topic is None:
        log.warning("turn_known_check_without_topic")
        return False
    return True


def _split_request(head: TurnHead, labels: LabelMap, key: str) -> records.AgentRequestRecord | None:
    """`intent = split_topic` with a label of a topic of the learner's table (WP8.2, D-B-213):
    the request `topic_split` that `agent.topic_split` answers. A missing, unknown or foreign
    label writes nothing, the turn goes on."""
    if head.intent != "split_topic":
        return None
    try:
        topic_id = labels.resolve("topic", head.topic_label or "")
    except UnknownLabelError:
        log.warning("turn_split_topic_without_known_topic", label=head.topic_label)
        return None
    return records.AgentRequestRecord(
        request_id=uuid5(QUACK_UUID_NS, f"{key}:topic_split:{topic_id}"),
        kind="topic_split",
        params=records.AgentParamsRecord(topic_id=topic_id),
        needed_in_session=False,
    )


def _request_record(request: AgentRequest, labels: LabelMap) -> records.AgentRequestRecord:
    params = request.params
    return records.AgentRequestRecord(
        request_id=uuid4(),
        kind=request.kind,
        params=records.AgentParamsRecord(
            skill_id=_optional(labels, "skill", params.skill),
            level=params.level,
            topic_id=_optional(labels, "topic", params.topic),
            task_id=_optional(labels, "task", params.task),
            misconception_id=_optional(labels, "misconception", params.misconception),
            description=params.description,
        ),
        needed_in_session=request.needed_in_session,
    )


def _turn_markup(
    markup: TurnMarkup,
    labels: LabelMap,
    tasks: list[records.TurnTaskRecord],
    requests: list[records.AgentRequestRecord],
    deadline: records.DeadlineRecord | None,
) -> records.TurnMarkupRecord:
    plan = markup.plan_update
    return records.TurnMarkupRecord(
        tasks=tasks,
        elements=[_element_record(e, labels) for e in markup.elements],
        exposures=[
            records.ExposureRecord(
                skill_id=labels.resolve("skill", e.skill), kind=e.kind, approach=e.approach
            )
            for e in markup.exposures
        ],
        targets_misconception_id=_optional(labels, "misconception", markup.targets_misconception),
        plan_update=records.PlanUpdateRecord(
            completed_steps=plan.completed_steps,
            current_step=plan.current_step,
            next_steps=plan.next_steps,
            branches=[records.BranchRecord(**b.model_dump()) for b in plan.branches],
            cancel_request_ids=[labels.resolve("request", r) for r in plan.cancel_requests],
        ),
        agent_requests=requests,
        corrections_acknowledged=markup.corrections_acknowledged,
        decision_reason=markup.decision_reason,
        deadline=deadline,
    )


def _element_record(element: Element, labels: LabelMap) -> records.ElementRecord:
    data = element.model_dump(mode="json")
    if element.kind == "card_series":
        data["card_ids"] = [str(labels.resolve("card", c)) for c in data.pop("cards")]
    elif element.kind == "visualization":
        data["request_id"] = _optional(labels, "request", data.pop("request"))
    elif element.kind == "material_link":
        data["chunk_id"] = labels.resolve("chunk", data.pop("chunk"))
    return _ELEMENT_RECORD.validate_python(data)


def _condition(element: Element | None) -> str:
    """The text the learner sees for the task (D-B-149)."""
    if element is None:
        return ""
    text = getattr(element, "prompt", None) or getattr(element, "text", None)
    return str(text or "")


def _manifest_params(manifest: TurnManifestRow) -> dict[str, Any]:
    return {
        "manifest_id": manifest.manifest_id,
        "learner_id": manifest.learner_id,
        "session_id": manifest.session_id,
        "prompt_version": manifest.prompt_version,
        "doctrine_version": manifest.doctrine_version,
        "renderer_version": manifest.renderer_version,
        "role": manifest.role,
        "model": manifest.model,
        "effort": manifest.effort,
        "projected_seq": manifest.projected_seq,
        "overlay_seqs": manifest.overlay_seqs,
        "label_table": Jsonb(manifest.label_table),
        "digest_id": manifest.digest_id,
        "chunk_ids": manifest.chunk_ids,
        "similar_ids": manifest.similar_ids,
        "context_text": manifest.context_text,
        "frozen_slots": Jsonb(manifest.frozen_slots) if manifest.frozen_slots is not None else None,
        "degraded_mode": manifest.degraded_mode,
    }


__all__ = [
    "AnsweredAction",
    "LearnerText",
    "NewTurnTask",
    "TurnBatch",
    "TurnManifestRow",
    "commit_turn",
    "markup_record",
    "turn_batch",
]
