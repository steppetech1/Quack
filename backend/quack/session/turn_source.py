"""The context source of the teacher turn — `TurnContextSource` (ТЗ Ф4 WP14, WP13.2 «assemble»;
Т-§10). D-B-181.

- **The journal of the session** gives the goal (`session.started`, then the last
  `message.sent` with an intent, Т-§12.7), the active tasks (created in the session and not
  closed), the learner texts, the action the turn answers and its verdict, the effort
  (Т-§12.9).
- **Memory** (S4, `quack.graph.read`): profile and goals now, the topics of the goal, the
  slice for the focus — the skills of the active tasks and of the goal topics (Т-§10.2). The
  reads go in parallel (WP14.5, Т-§10.7). A slice that cannot be read is the empty slice and
  `memory_unavailable` (WP6.1).
- **Slots 3–4** come from the snapshot of the session (D-B-180); the first turn freezes them
  into its manifest row.
- **Material chunks** (slot 8): the prefetch by the graph (Т-§10.5) when the driver is given.
- The rendered context is cut to `teacher_context_max_tokens` (WP14.5); the request goes to the
  streaming provider of the role; the spoiler guard checks the answers of the active tasks.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

from neo4j import AsyncDriver
from neo4j.exceptions import ConnectionAcquisitionTimeoutError, ServiceUnavailable, SessionExpired

from quack.api.resources import AppResources
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import MessageSent, SessionStarted, TaskClosed, TaskCreated
from quack.events.log import StoredEvent
from quack.events.values import AnswerSpec, Level, Verdict
from quack.graph.overlay import SessionOverlay
from quack.graph.overlay_build import build_session_overlay, empty_overlay
from quack.graph.projection.phase_a import EventFailed
from quack.graph.read import (
    GraphReader,
    HubGoal,
    MemorySlice,
    ReviewCandidate,
    SliceFocus,
    TopicMemory,
)
from quack.graph.read.materials import materials_prefetch
from quack.graph.read.neo4j_reader import Neo4jGraphReader
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.logging import get_logger
from quack.practice.repo import session_events_up_to
from quack.practice.task_store import get_task
from quack.session.action_turn import TurnRequest, TurnSetup
from quack.session.conversation import render_conversation
from quack.session.memory_picture import (
    MEMORY_UNAVAILABLE,
    PictureInputs,
    ReviewTask,
    RevisedCheck,
    render_memory_picture,
)
from quack.session.review_turn import bank_for_review, review_for_turn
from quack.session.spoiler_guard import answer_in_text, remove_answer_sentences
from quack.session.teacher_context import (
    SessionGoal,
    TeacherSlots,
    fit_budget,
    frozen_slots,
    render_labels,
    render_materials,
    render_session_plan,
    snapshot_slots,
    teacher_variables,
)
from quack.session.teacher_output import TeacherOutputV2
from quack.session.tools import (
    TOOLS,
    CanvasLine,
    MaterialChunk,
    ToolContext,
    VoiceSegment,
    run_tool,
    tool_definition,
)
from quack.session.turn_commit import AnsweredAction, LearnerText, TurnManifestRow
from quack.session.turn_graph import AssembledTurn, Effort, GuardInputs, Role
from quack.session.turn_inputs import learner_state, load_learner_skill, load_plan, load_snapshot
from quack.session.turn_policy import resolve_degraded_mode

log = get_logger(__name__)

RENDERER_VERSION = "renderer@1"
NEO4J_UNAVAILABLE: tuple[type[BaseException], ...] = (
    ServiceUnavailable,
    SessionExpired,
    ConnectionAcquisitionTimeoutError,
)

_TIMEZONE = (
    "SELECT settings->>'timezone' AS timezone FROM core.learners WHERE learner_id = %(learner_id)s"
)
_JOURNAL_END = "SELECT seq FROM core.learners WHERE learner_id = %(learner_id)s"
_LATEST_DIGEST = (
    "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s AND type = 'digest.written' "
    "AND domain_id = %(d)s ORDER BY seq DESC LIMIT 1"
)
_COMPLETED = (
    "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'agent.completed' AND payload->>'request_id' = ANY(%(ids)s) ORDER BY seq"
)
_LINE_EVENTS = ("canvas.line_committed", "canvas.line_erased", "handwriting.corrected")
_TASK_ACTIONS = {
    "hint.requested": "hint_request",
    "solution.requested": "solution_request",
    "check.skipped": "check_skip",
}


async def journal_end(db: Db, *, learner_id: UUID) -> int:
    """The last `seq` of the learner's journal (0 for a learner without a row)."""
    row = await db.fetch_one(_JOURNAL_END, {"learner_id": learner_id})
    return int(row["seq"]) if row is not None else 0


async def learner_timezone(db: Db, *, learner_id: UUID) -> str | None:
    row = await db.fetch_one(_TIMEZONE, {"learner_id": learner_id})
    return row["timezone"] if row is not None else None


@dataclass(frozen=True)
class SessionJournal:
    """What the turn needs from the events of its session (pure, `read_journal`)."""

    goal: SessionGoal
    open_tasks: list[UUID]
    task_skills: list[UUID]
    learner_texts: list[LearnerText]
    first_turn: bool
    repeated_failure: bool
    requests: dict[UUID, str] = field(default_factory=dict)
    revised: list[RevisedCheck] = field(default_factory=list)
    canvas_lines: dict[str, CanvasLine] = field(default_factory=dict)
    voice: list[VoiceSegment] = field(default_factory=list)
    skill_invocation: str | None = None
    answered: AnsweredAction | None = None
    answered_verdict: Verdict | None = None


def _latest_verdicts(events: Sequence[StoredEvent]) -> dict[tuple[int, str], Verdict]:
    found: dict[tuple[int, str], Verdict] = {}
    for event in events:
        if event.type == "verdict.recorded":
            verdict = Verdict.model_validate(event.payload)
            found[(verdict.answer_seq, verdict.part)] = verdict
    return found


def _repeated_failure(
    events: Sequence[StoredEvent], answer_tasks: dict[int, UUID], skills: dict[UUID, set[UUID]]
) -> bool:
    """Т-§12.9: the two latest checked answers of the session both failed on one skill."""
    outcomes: dict[int, bool] = {}
    for (answer_seq, _part), verdict in _latest_verdicts(events).items():
        failed = verdict.outcome == "incorrect"
        outcomes[answer_seq] = outcomes.get(answer_seq, False) or failed
    last = sorted(outcomes)[-2:]
    if len(last) < 2 or not all(outcomes[seq] for seq in last):
        return False
    first, second = (skills.get(answer_tasks.get(seq, UUID(int=0)), set()) for seq in last)
    return bool(first & second)


def _revised(events: Sequence[StoredEvent]) -> list[RevisedCheck]:
    """Т-§5.6, Т-§6.3: a check whose outcome changed after it was given, not yet acknowledged
    by a turn (`correctionsAcknowledged` of `turn.committed`)."""
    history: dict[tuple[int, str], list[str]] = {}
    acknowledged: set[int] = set()
    for event in events:
        if event.type == "verdict.recorded":
            verdict = Verdict.model_validate(event.payload)
            history.setdefault((verdict.answer_seq, verdict.part), []).append(verdict.outcome)
        elif event.type == "turn.committed":
            acknowledged.update(event.payload["markup"]["corrections_acknowledged"])
    return [
        RevisedCheck(answer_seq=seq, text=f"part {part} is now {seen[-1]}, was {seen[0]}")
        for (seq, part), seen in sorted(history.items())
        if seen[-1] != seen[0] and seq not in acknowledged
    ]


def _requests(events: Sequence[StoredEvent]) -> dict[UUID, str]:
    """The agent requests of the session (Т-§13.2): id → kind."""
    found: dict[UUID, str] = {}
    for event in events:
        if event.type == "agent.requested":
            found[UUID(str(event.payload["request_id"]))] = str(event.payload["kind"])
        elif event.type == "turn.committed":
            for item in event.payload["markup"]["agent_requests"]:
                found[UUID(str(item["request_id"]))] = str(item["kind"])
    return found


def _canvas(events: Sequence[StoredEvent]) -> tuple[dict[str, CanvasLine], list[VoiceSegment]]:
    """Canvas lines by the numbers of slot 5 (`L1…`, in the order of the session, D-B-163) and
    the voice segments, for `voice_at_line` and `past_solution` (WP14.4)."""
    numbers: dict[tuple[str, str], str] = {}
    lines: dict[str, CanvasLine] = {}
    voice: list[VoiceSegment] = []
    for event in events:
        if event.type in _LINE_EVENTS:
            p = event.payload
            label = numbers.setdefault((str(p["canvas_id"]), p["line_id"]), f"L{len(numbers) + 1}")
            if event.type == "canvas.line_committed":
                lines[label] = CanvasLine(p["text"], p["started_at_ms"], p["ended_at_ms"])
            elif event.type == "handwriting.corrected" and label in lines:
                lines[label] = replace(lines[label], text=p["text"])
        elif event.type == "voice.transcribed":
            p = event.payload
            voice.append(VoiceSegment(p["text"], p["started_at_ms"], p["ended_at_ms"]))
    return lines, voice


def read_journal(events: Sequence[StoredEvent], action_seq: int) -> SessionJournal:
    """The goal, active tasks, texts and the answered action of the session up to the action."""
    ordered = sorted(events, key=lambda e: e.seq)
    goal = SessionGoal(intent="learn")
    created: dict[UUID, TaskCreated] = {}
    closed: set[UUID] = set()
    answer_tasks: dict[int, UUID] = {}
    texts: list[LearnerText] = []
    for event in ordered:
        match event.type:
            case "session.started":
                started = SessionStarted.model_validate(event.payload)
                goal = SessionGoal(
                    intent=started.intent, topic_id=started.topic_id, hub_id=started.hub_id
                )
            case "message.sent":
                message = MessageSent.model_validate(event.payload)
                texts.append(LearnerText(seq=event.seq, text=message.text))
                if message.intent is not None:
                    goal = SessionGoal(
                        intent=message.intent, topic_id=message.topic_id, hub_id=message.hub_id
                    )
            case "task.created":
                task = TaskCreated.model_validate(event.payload)
                created[task.task_id] = task
            case "task.closed":
                closed.add(TaskClosed.model_validate(event.payload).task_id)
            case "answer.submitted":
                answer_tasks[event.seq] = UUID(str(event.payload["task_id"]))
            case _:
                pass
    open_tasks = [task_id for task_id in created if task_id not in closed]
    skills = {
        task_id: {t.skill_id for t in task.markup.tests if t.skill_id is not None}
        for task_id, task in created.items()
    }
    task_skills = list(dict.fromkeys(s for t in open_tasks for s in skills[t]))
    journal = SessionJournal(
        goal=goal,
        open_tasks=open_tasks,
        task_skills=task_skills,
        learner_texts=texts,
        first_turn=not any(e.type == "turn.committed" for e in ordered),
        repeated_failure=_repeated_failure(ordered, answer_tasks, skills),
        requests=_requests(ordered),
        revised=_revised(ordered),
        canvas_lines=_canvas(ordered)[0],
        voice=_canvas(ordered)[1],
    )
    action = next((e for e in ordered if e.seq == action_seq), None)
    if action is None:
        return journal
    if action.type == "message.sent":
        invocation = MessageSent.model_validate(action.payload).skill_invocation
        return replace(journal, skill_invocation=invocation)
    if action.type in _TASK_ACTIONS:
        task_id = UUID(str(action.payload["task_id"]))
        kind = _TASK_ACTIONS[action.type]
        return replace(journal, answered=AnsweredAction(kind=kind, task_id=task_id))  # type: ignore[arg-type]
    if action.type == "answer.submitted":
        task_id = answer_tasks[action.seq]
        verdicts = _latest_verdicts(ordered)
        mine = {part: v for (seq, part), v in verdicts.items() if answer_tasks.get(seq) == task_id}
        solved = bool(mine) and all(v.outcome == "correct" for v in mine.values())
        own = [v for (seq, _p), v in verdicts.items() if seq == action.seq]
        return replace(
            journal,
            answered=AnsweredAction(kind="answer", task_id=task_id, solved=solved),
            answered_verdict=own[-1] if own else None,
        )
    return journal


def turn_effort(role: Role, journal: SessionJournal) -> Effort:
    """Т-§12.9: `high` on the first turn of the session and after two failures on one skill;
    `teacher_fallback` is always `low`."""
    if role == "teacher_fallback":
        return "low"
    return "high" if journal.first_turn or journal.repeated_failure else "low"


def answer_strings(spec: AnswerSpec) -> list[str]:
    """What of the answer may not be shown (Т-§12.4): the value and the items, both forms."""
    values = [spec.value] if spec.value is not None else []
    values.extend(spec.items or [])
    return [form for value in values for form in (value.latex, value.sympy) if form]


def target_levels(
    hubs: Sequence[HubGoal], memory: Sequence[TopicMemory]
) -> dict[UUID, list[Level]]:
    """Т-§8.5: the target set of a skill — the levels its topic's hubs require (union)."""
    required: dict[UUID, set[Level]] = {}
    for hub in hubs:
        for topic in hub.topics:
            required.setdefault(topic.topic_id, set()).update(topic.required_levels)
    return {
        skill.skill_id: sorted(required[item.topic_id])
        for item in memory
        if item.topic_id in required
        for skill in item.skills
    }


def in_context_sample(manifest_id: UUID, rate: float) -> bool:
    """`turn_context_sample_rate` (WP13.9): the full context text is kept for this share of
    turns; decided by the manifest id, so a retry of the turn decides the same way."""
    return int(manifest_id.hex[:8], 16) / 0x1_0000_0000 < rate


def _goal_topics(goal: SessionGoal, hubs: Sequence[HubGoal]) -> list[UUID]:
    if goal.topic_id is not None:
        return [goal.topic_id]
    if goal.hub_id is not None:
        return [t.topic_id for hub in hubs if hub.hub_id == goal.hub_id for t in hub.topics]
    return []


class MemoryTurnContextSource:
    """`TurnContextSource` over the journal (Postgres) and memory (S4). `reader` binds the
    reader of memory to the learner of the turn; `neo4j` — the driver of the material prefetch
    (no driver — slot 8 is empty)."""

    def __init__(
        self,
        *,
        db: Db,
        gateway: LLMGateway,
        settings: Settings,
        reader: Callable[[UUID], GraphReader],
        neo4j: AsyncDriver | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._settings = settings
        self._reader = reader
        self._neo4j = neo4j
        self._clock = clock or (lambda: int(time.time() * 1000))

    async def _overlay(self, request: TurnRequest, end: int) -> SessionOverlay:
        """The session overlay of Т-§2.6 (WP2.3, D-B-215): what the session wrote after the
        projector, over the slice it declared. The wait target stays the end of the journal.
        An event the handlers cannot apply — an empty overlay, the teacher answers (law 9)."""
        if self._neo4j is None:
            return empty_overlay(0)
        try:
            built = await build_session_overlay(
                self._db, self._neo4j, self._settings, request.learner_id, request.session_id
            )
        except EventFailed as exc:
            log.warning("session_overlay_failed", seq=exc.seq, session_id=str(request.session_id))
            built = empty_overlay(0)
        return replace(built, overlay_seq=max(built.overlay_seq, end))

    async def _slice(
        self, reader: GraphReader, request: TurnRequest, focus: SliceFocus
    ) -> tuple[MemorySlice, bool]:
        # The wait target is the end of the learner's journal (Т-§2.6): the reader waits for
        # the projector up to it and reports `lag_degraded` (`projector_lag`, §4.4).
        end = await journal_end(self._db, learner_id=request.learner_id)
        try:
            tail = await self._overlay(request, end)
            found = await reader.slice_for_turn(request.learner_id, request.session_id, focus, tail)
        except NEO4J_UNAVAILABLE:
            return MemorySlice(learner_id=request.learner_id, projected_seq=0), True
        return found, False

    async def _review(
        self,
        reader: GraphReader,
        request: TurnRequest,
        journal: SessionJournal,
        goal_skill_ids: Sequence[UUID],
    ) -> list[ReviewCandidate]:
        """Candidates to repeat (WP17.6); unreadable memory — none (the slice reports it)."""
        try:
            return await review_for_turn(
                reader,
                learner_id=request.learner_id,
                now_ms=self._clock(),
                goal=journal.goal,
                goal_skill_ids=goal_skill_ids,
                texts=[t.text for t in journal.learner_texts],
                settings=self._settings,
            )
        except NEO4J_UNAVAILABLE:
            return []

    async def _bank(
        self,
        reader: GraphReader,
        learner_id: UUID,
        review: Sequence[ReviewCandidate],
        targets: dict[UUID, list[Level]],
    ) -> dict[UUID, list[ReviewTask]]:
        if self._neo4j is None or not review:
            return {}
        try:
            return await bank_for_review(
                self._neo4j,
                self._db,
                reader,
                self._settings,
                learner_id=learner_id,
                candidates=review,
                targets=targets,
            )
        except NEO4J_UNAVAILABLE:
            return {}

    async def _chunks(self, learner_id: UUID, skill_ids: Sequence[UUID]) -> list[MaterialChunk]:
        if self._neo4j is None:
            return []
        try:
            hits = await materials_prefetch(
                self._neo4j,
                learner_id=learner_id,
                skill_ids=skill_ids,
                k=self._settings.materials_k,
            )
        except NEO4J_UNAVAILABLE:
            return []
        return [MaterialChunk(h.chunk_id, None, h.page, h.text) for h in hits]

    async def _digest(self, learner_id: UUID, domain_id: UUID) -> str | None:
        """The latest analyst summary of the domain (Т-§13.5): rebuilt by the analyst each
        time from structure, so reading the latest one is no accumulation (law 10)."""
        row = await self._db.fetch_one(_LATEST_DIGEST, {"learner_id": learner_id, "d": domain_id})
        return None if row is None else str(row["payload"]["text"])

    async def _completed(self, learner_id: UUID, requests: dict[UUID, str]) -> set[UUID]:
        """Requests of the session with a successful `agent.completed` (Т-§13.7)."""
        if not requests:
            return set()
        rows = await self._db.fetch_all(
            _COMPLETED, {"learner_id": learner_id, "ids": [str(r) for r in requests]}
        )
        return {UUID(str(r["payload"]["request_id"])) for r in rows if r["payload"]["ok"]}

    async def _answers(self, learner_id: UUID, task_ids: Sequence[UUID]) -> list[str]:
        found: list[str] = []
        for task_id in task_ids:
            task = await get_task(self._db, learner_id=learner_id, task_id=task_id)
            if task is not None:
                found.extend(answer_strings(task.answer_spec))
        return found

    async def assemble(self, request: TurnRequest) -> TurnSetup:
        settings, db = self._settings, self._db
        learner_id, session_id = request.learner_id, request.session_id
        reader = self._reader(learner_id)
        events = await session_events_up_to(
            db, learner_id=learner_id, session_id=session_id, to_seq=request.action_seq
        )
        journal = read_journal(events, request.action_seq)
        now, plan, timezone, snapshot, skill = await asyncio.gather(
            learner_state(reader, learner_id=learner_id, domain_id=request.domain_id),
            load_plan(db, learner_id=learner_id, session_id=session_id),
            learner_timezone(db, learner_id=learner_id),
            load_snapshot(db, learner_id=learner_id, session_id=session_id),
            load_learner_skill(db, learner_id=learner_id, name=journal.skill_invocation)
            if journal.skill_invocation
            else _none(),
        )
        hubs = await reader.domain_goals(request.domain_id)
        topics = _goal_topics(journal.goal, hubs)
        memory: list[TopicMemory] = await reader.topic_memory(topics) if topics else []
        topic_skills = [s.skill_id for m in memory for s in m.skills]
        targets = target_levels(hubs, memory)
        focus = SliceFocus(
            skill_ids=list(dict.fromkeys([*journal.task_skills, *topic_skills])),
            topic_ids=topics,
            domain_id=request.domain_id,
        )
        review = await self._review(reader, request, journal, topic_skills)
        read_focus = focus.model_copy(
            update={
                "skill_ids": list(dict.fromkeys([*focus.skill_ids, *(c.skill_id for c in review)]))
            }
        )
        (memory_slice, unavailable), chunks, answers, digest, completed = await asyncio.gather(
            self._slice(reader, request, read_focus),
            self._chunks(learner_id, read_focus.skill_ids),
            self._answers(learner_id, journal.open_tasks),
            self._digest(learner_id, request.domain_id),
            self._completed(learner_id, journal.requests),
        )

        freeze = snapshot is None
        frozen = frozen_slots(snapshot or snapshot_slots(now, timezone), now, timezone)
        skill_ids = list(
            dict.fromkeys([*read_focus.skill_ids, *(s.skill_id for s in memory_slice.skills)])
        )
        hub_topics = [t.topic_id for hub in hubs for t in hub.topics]
        bank = {} if unavailable else await self._bank(reader, learner_id, review, targets)
        labels = LabelMap.build(
            settings,
            skills=skill_ids,
            topics=list(dict.fromkeys([*topics, *hub_topics])),
            tasks=[*journal.open_tasks, *(t.task_id for ts in bank.values() for t in ts)],
            misconceptions=[m.misconception_id for m in memory_slice.misconceptions],
            hubs=[hub.hub_id for hub in hubs],
            chunks=[chunk.chunk_id for chunk in chunks],
            requests=list(journal.requests),
        )
        names: dict[UUID, str] = {s.skill_id: s.name for s in memory_slice.skills if s.name}
        names.update({m.topic_id: m.name for m in memory})
        names.update({hub.hub_id: hub.name for hub in hubs})
        names.update({t.topic_id: t.name for hub in hubs for t in hub.topics})
        picture = (
            MEMORY_UNAVAILABLE
            if unavailable
            else render_memory_picture(
                PictureInputs(
                    slice=memory_slice,
                    focus=focus,
                    skill_names=names,
                    target_levels=targets,
                    analyst_summary=digest,
                    agent_results=[
                        f"{labels.label('request', r) or 'a request'}: {kind} is ready"
                        for r, kind in journal.requests.items()
                        if r in completed
                    ],
                    revised_checks=journal.revised,
                    review=[c.skill_id for c in review],
                    review_tasks=bank,
                ),
                labels,
                settings,
                self._clock(),
            )
        )
        variables = teacher_variables(
            TeacherSlots(
                frozen=frozen,
                conversation=render_conversation(events, labels, settings),
                session_plan=render_session_plan(journal.goal, plan, labels),
                memory_picture=picture,
                materials=render_materials(chunks, labels),
                labels=render_labels(labels, names),
                skill=skill,
            )
        )
        role = request.role
        prompt_version = settings.llm_roles[role].prompt or "teacher@2"
        prompt_name, version = prompt_version.rsplit("@", 1)
        template = self._gateway.prompts.load(prompt_name, int(version))
        variables = fit_budget(template.render, variables, settings.teacher_context_max_tokens)
        effort = turn_effort(role, journal)
        stream, prompt = self._gateway.stream_request(
            role,
            int(version),
            TeacherOutputV2,
            variables,
            prompt_role=prompt_name,
            reasoning_effort=effort,
            tools=[tool_definition(tool) for tool in TOOLS],
        )
        tools = ToolContext(
            learner_id=learner_id,
            labels=labels,
            reader=reader,
            settings=settings,
            now_ms=self._clock(),
            domain_id=request.domain_id,
            skill_names=names,
            canvas_lines=journal.canvas_lines,
            voice=journal.voice,
        )

        async def execute_tool(name: str, arguments: str) -> str:
            return await run_tool(name, arguments, tools)

        degraded = resolve_degraded_mode(
            budget_exceeded=False,
            llm_unavailable=False,
            memory_unavailable=unavailable,
            teacher_fallback=role == "teacher_fallback",
            projector_lag=memory_slice.lag_degraded,
        )
        return TurnSetup(
            assembled=AssembledTurn(
                request=stream,
                guard=GuardInputs(
                    answered_verdict=journal.answered_verdict,
                    skill_labels=frozenset(labels.labels("skill")),
                    misconception_labels=frozenset(labels.labels("misconception")),
                    task_labels=frozenset(labels.labels("task")),
                    has_spoiler=lambda text: any(
                        answer_in_text(text, answer, settings) for answer in answers
                    ),
                    remove_spoiler=lambda text: remove_answer_sentences(text, answers, settings),
                ),
                effort=effort,
                degraded_mode=degraded,
            ),
            provider=self._gateway.streaming(
                role,
                prompt,
                learner_id=learner_id,
                session_id=session_id,
                turn_id=request.manifest_id,
            ),
            labels=labels,
            manifest=TurnManifestRow(
                manifest_id=request.manifest_id,
                learner_id=learner_id,
                session_id=session_id,
                prompt_version=prompt_version,
                doctrine_version=None,
                renderer_version=RENDERER_VERSION,
                role=role,
                model=settings.model_for_role(role),
                effort=effort,
                projected_seq=memory_slice.projected_seq,
                label_table=labels.table(),
                chunk_ids=[chunk.chunk_id for chunk in chunks],
                context_text=(
                    stream.base.prompt
                    if in_context_sample(request.manifest_id, settings.turn_context_sample_rate)
                    else None
                ),
                degraded_mode=degraded,
                frozen_slots=(
                    {"profile": frozen.profile, "domain_and_goals": frozen.domain_and_goals}
                    if freeze
                    else None
                ),
            ),
            execute_tool=execute_tool,
            learner_texts=journal.learner_texts,
            learner_timezone=timezone,
            answered=journal.answered,
        )


async def _none() -> None:
    return None


def source_of(resources: AppResources) -> MemoryTurnContextSource:
    """The source of the API process: the reader of the teacher turn is bound to the learner of
    the turn (D-A-144) and replays the log of Phase 4 through `db` (WP8.3)."""

    def reader(learner_id: UUID) -> GraphReader:
        return Neo4jGraphReader(
            resources.neo4j,
            resources.settings,
            learner_id=learner_id,
            redis=resources.redis,
            db=resources.db,
        )

    return MemoryTurnContextSource(
        db=resources.db,
        gateway=resources.llm,
        settings=resources.settings,
        reader=reader,
        neo4j=resources.neo4j,
    )
