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
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver
from neo4j.exceptions import ConnectionAcquisitionTimeoutError, ServiceUnavailable, SessionExpired

from quack.api.resources import AppResources
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.db import Db
from quack.embeddings.client import EmbeddingClient, EmbeddingsUnavailable
from quack.events.event_types import (
    AnswerSubmitted,
    MessageSent,
    SessionStarted,
    TaskClosed,
    TaskCreated,
)
from quack.events.log import StoredEvent
from quack.events.values import AnswerSpec, Level, Verdict
from quack.graph.overlay import SessionOverlay
from quack.graph.overlay_build import build_session_overlay, empty_overlay
from quack.graph.projection.phase_a import EventFailed
from quack.graph.read import (
    GraphReader,
    HubGoal,
    MemorySlice,
    PrepContext,
    ReviewCandidate,
    SliceFocus,
    TopicMemory,
)
from quack.graph.read.materials import materials_prefetch, materials_search
from quack.graph.read.neo4j_reader import Neo4jGraphReader
from quack.graph.topic_names import canonical_topic_name
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.llm.provider import ProviderRequest
from quack.llm.streaming import StreamRequest
from quack.llm.strict_schema import to_strict_json_schema
from quack.llm.tool_loop import stream_step
from quack.logging import get_logger
from quack.practice.answer_spec import CODE_TYPES, SLOW_TYPES, LearnerAnswer
from quack.practice.answers import DEFAULT_PART, help_in_session, part_spec
from quack.practice.check import check
from quack.practice.model_check import record_verdict
from quack.practice.repo import session_events_of_action
from quack.practice.session_plan import PlanDoc
from quack.practice.task_store import get_task
from quack.session.action_turn import TurnRequest, TurnSetup
from quack.session.actions import VerdictFrame
from quack.session.conversation import render_conversation
from quack.session.elements import TASK_ELEMENT_KINDS, Element
from quack.session.memory_picture import (
    MEMORY_UNAVAILABLE,
    PictureInputs,
    ReviewTask,
    RevisedCheck,
    render_memory_picture,
)
from quack.session.plan_view import (
    apply_edits,
    landmark_ref,
    points_from_memory,
    resolve_added_points,
    view_of,
    visited_landmarks,
)
from quack.session.prep_mode import prep_for_turn, render_prep
from quack.session.review_turn import bank_for_review, review_for_turn
from quack.session.schemas import PlanLandmark
from quack.session.spoiler_guard import answer_in_text, remove_answer_sentences, spoiler_hits
from quack.session.teacher_context import (
    LearnerState,
    Profile,
    SessionGoal,
    TeacherSlots,
    card_label_line,
    fit_budget,
    frozen_slots,
    render_labels,
    render_materials,
    render_session_plan,
    snapshot_slots,
    teacher_variables,
)
from quack.session.teacher_output import TurnTask, teacher_schema_for_prompt
from quack.session.tools import (
    TOOLS,
    CanvasLine,
    MaterialChunk,
    SearchMaterials,
    ToolContext,
    VoiceSegment,
    run_tool,
    tool_definition,
)
from quack.session.turn_commit import AnsweredAction, LearnerText, TurnManifestRow
from quack.session.turn_graph import AssembledTurn, Effort, GuardInputs, MarkupFix, Role
from quack.session.turn_inputs import (
    cards_for_turn,
    has_topic_notes,
    learner_state,
    load_learner_skill,
    load_plan,
    load_snapshot,
)
from quack.session.turn_policy import resolve_degraded_mode
from quack.session.turn_timings import now_ns, put_since, timed

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
_ATTACHED_MATERIALS = (
    "SELECT material_id, title, mime, status, pages_count FROM content.materials "
    "WHERE learner_id = %(learner_id)s AND material_id = ANY(%(ids)s)"
)
_ATTACHED_PAGES = (
    "SELECT material_id, page, text FROM content.material_pages "
    "WHERE learner_id = %(learner_id)s AND material_id = ANY(%(ids)s) ORDER BY material_id, page"
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
    # Т-§12.4: the active tasks whose answer the text may not show — open, and no
    # `solution.requested`; a task the answered answer solves is closed by this very turn.
    spoiler_tasks: list[UUID]
    # Т-§12.4: the answers of the parts of those tasks (`markup.parts[i].answer`) the learner has
    # not solved yet — a part with a correct latest verdict may be spoken about; (task, spec).
    spoiler_parts: list[tuple[UUID, AnswerSpec]]
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
    answer_seq: int | None = None  # the `answer.submitted` the turn answers


def _latest_verdicts(events: Sequence[StoredEvent]) -> dict[tuple[int, str], Verdict]:
    found: dict[tuple[int, str], Verdict] = {}
    for event in events:
        if event.type == "verdict.recorded":
            verdict = Verdict.model_validate(event.payload)
            found[(verdict.answer_seq, verdict.part)] = verdict
    return found


def _solved(
    task: TaskCreated | None, verdicts: dict[tuple[int, str], Verdict], task_answers: set[int]
) -> bool:
    """Т-§6.2: every part of the task has a correct latest verdict (the latest answer of that
    part), or the whole task (`main`) does; a task without parts has `main` only."""
    latest: dict[str, Verdict] = {}
    for (answer_seq, part), verdict in sorted(verdicts.items()):
        if answer_seq in task_answers:
            latest[part] = verdict
    parts = {p.part for p in (task.markup.parts if task is not None else None) or ()}
    whole = latest.get(DEFAULT_PART)
    if whole is not None and whole.outcome == "correct":
        return True
    if parts:
        return all(part in latest and latest[part].outcome == "correct" for part in parts)
    return bool(latest) and all(v.outcome == "correct" for v in latest.values())


def _spoiler_parts(
    created: dict[UUID, TaskCreated],
    tasks: Sequence[UUID],
    verdicts: dict[tuple[int, str], Verdict],
    answer_tasks: dict[int, UUID],
) -> list[tuple[UUID, AnswerSpec]]:
    """Т-§12.4: the answers of the parts of the guarded tasks, except the parts whose latest
    verdict is `correct` (the same exception as for the whole task). A task without parts has
    none; the accepted wordings of a `short_text` are not added (decision of the owner)."""
    latest: dict[tuple[UUID, str], Verdict] = {}
    for (answer_seq, part), verdict in sorted(verdicts.items()):
        task_id = answer_tasks.get(answer_seq)
        if task_id is not None:
            latest[(task_id, part)] = verdict
    found: list[tuple[UUID, AnswerSpec]] = []
    for task_id in tasks:
        task = created.get(task_id)
        for part in (task.markup.parts if task is not None else None) or ():
            done = latest.get((task_id, part.part))
            if done is None or done.outcome != "correct":
                found.append((task_id, part.answer))
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


_KIND_WORDS = (
    ("presentation", "a presentation (slides)"),
    ("pdf", "a PDF"),
    ("image", "a photo or a picture"),
)
_SLIDES = "presentationml.presentation"


def render_attached(
    materials: Sequence[tuple[dict[str, Any], Sequence[tuple[int, str]]]], *, budget: int
) -> str:
    """The attached files for slot 8. Material text is data (Т-§19.8): every line is quoted."""
    out: list[str] = []
    left = budget
    for row, pages in materials:
        mime = str(row["mime"])
        kind = (
            "a presentation (slides)"
            if mime.endswith(_SLIDES)
            else ("a PDF" if mime == "application/pdf" else "a photo or a picture")
        )
        unit = "slide" if mime.endswith(_SLIDES) else "page"
        name = row["title"] or "a file without a name"
        status = str(row["status"])
        head = f'The learner attached {kind} to the conversation: "{name}"'
        if status == "failed":
            out.append(f"{head}. It could not be read. Say so plainly and ask for another copy.")
            continue
        if status == "uploaded" or not pages:
            out.append(
                f"{head}. It is still being read, its text is not available yet. Say that you "
                "cannot see it yet; do not guess what is in it."
            )
            continue
        out.append(f"{head} ({len(pages)} {unit}s). Its text, as data:")
        shown = 0
        cut = False
        for number, text in pages:
            body = text.strip()
            if not body:
                continue  # an empty slide or page has nothing to quote
            if left <= 0:
                cut = True
                break
            if len(body) > left:
                body, cut = body[:left].rstrip() + "…", True
            left -= len(body)
            out.append(f"{unit.capitalize()} {number}:")
            out.extend(f"> {line}" for line in body.splitlines())
            shown += 1
        if cut:
            out.append(
                f"(Only the first {shown} of {len(pages)} {unit}s fit here; "
                "the search over the materials finds the rest.)"
            )
    return "\n".join(out)


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


def _code_task_note(made: Sequence[UUID], code_tasks: dict[UUID, str], labels: LabelMap) -> str:
    """`teacher@4`: the code tasks a request made, to show with a `code_task` element."""
    found = [
        f"{label} ({code_tasks[t]})"
        for t in made
        if t in code_tasks and (label := labels.label("task", t))
    ]
    return f": code tasks {', '.join(found)}" if found else ""


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
    solution_asked: set[UUID] = set()
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
            case "voice.transcribed":
                # The words of a spoken message (its `message.sent` carries none, `voice_seq`).
                texts.append(LearnerText(seq=event.seq, text=str(event.payload["text"])))
            case "task.created":
                task = TaskCreated.model_validate(event.payload)
                created[task.task_id] = task
            case "task.closed":
                closed.add(TaskClosed.model_validate(event.payload).task_id)
            case "answer.submitted":
                answer_tasks[event.seq] = UUID(str(event.payload["task_id"]))
            case "solution.requested":
                solution_asked.add(UUID(str(event.payload["task_id"])))
            case _:
                pass
    open_tasks = [task_id for task_id in created if task_id not in closed]
    skills = {
        task_id: {t.skill_id for t in task.markup.tests if t.skill_id is not None}
        for task_id, task in created.items()
    }
    task_skills = list(dict.fromkeys(s for t in open_tasks for s in skills[t]))
    spoiler_tasks = [t for t in open_tasks if t not in solution_asked]
    journal = SessionJournal(
        goal=goal,
        open_tasks=open_tasks,
        task_skills=task_skills,
        spoiler_tasks=spoiler_tasks,
        spoiler_parts=_spoiler_parts(
            created, spoiler_tasks, _latest_verdicts(ordered), answer_tasks
        ),
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
    answer = action
    if action.type == "element.responded":
        # WP13.1: an element that is a task also wrote `answer.submitted` (caused by the response).
        answer = next(
            (e for e in ordered if e.type == "answer.submitted" and e.causation_seq == action.seq),
            None,
        )
    if answer is not None and answer.type == "answer.submitted":
        task_id = answer_tasks[answer.seq]
        verdicts = _latest_verdicts(ordered)
        task_answers = {seq for seq, t in answer_tasks.items() if t == task_id}
        solved = _solved(created.get(task_id), verdicts, task_answers)
        own = [v for (seq, _p), v in verdicts.items() if seq == answer.seq]
        return replace(
            journal,
            spoiler_tasks=[t for t in journal.spoiler_tasks if not (solved and t == task_id)],
            spoiler_parts=[
                (t, spec) for t, spec in journal.spoiler_parts if not (solved and t == task_id)
            ],
            answered=AnsweredAction(kind="answer", task_id=task_id, solved=solved),
            answered_verdict=own[-1] if own else None,
            answer_seq=answer.seq,
        )
    return journal


def turn_effort(role: Role, journal: SessionJournal) -> Effort:
    """Т-§12.9: `high` on the first turn of the session and after two failures on one skill;
    `teacher_fallback` is always `low`."""
    if role == "teacher_fallback":
        return "low"
    return "high" if journal.first_turn or journal.repeated_failure else "low"


def answer_strings(spec: AnswerSpec) -> list[str]:
    """What of the answer may not be shown (Т-§12.4): the value and the items, both forms; for a
    `choice` the text of the correct option (the function of Phase 3, `spoiler_guard`)."""
    values = [spec.value] if spec.value is not None else []
    values.extend(spec.items or [])
    found = [form for value in values for form in (value.latex, value.sympy) if form]
    if spec.type == "choice":
        found.extend(o.text for o in spec.options or [] if o.correct and o.text)
    return found


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


def skill_topics(goal_topics: Sequence[UUID], hubs: Sequence[HubGoal], limit: int) -> list[UUID]:
    """The topics whose skills the turn reads: those of the goal; a session on the whole domain
    (no topic, no hub) takes the topics of the domain's hubs, at most `limit`. Without them a
    chat on a domain gets the topics in its label table but none of their skills, and the
    teacher cannot name a skill (a `task_generation` request needs one)."""
    if goal_topics:
        return list(goal_topics)
    found = [t.topic_id for hub in hubs for t in hub.topics]
    return list(dict.fromkeys(found))[:limit]


def _plan_refs(plan: PlanDoc | None) -> tuple[list[UUID], list[UUID]]:
    """Skill and topic ids referenced in `plan` so `LabelMap` and `slice_for_turn` see them."""
    if plan is None:
        return [], []
    skill_ids: list[UUID] = []
    topic_ids: list[UUID] = []
    for add in plan.landmark_edits.added:
        (skill_ids if add.kind == "skill" else topic_ids).append(add.id)
    steps = ([plan.current_step] if plan.current_step is not None else []) + list(plan.next_steps)
    for step in steps:
        if step.landmark is not None:
            (skill_ids if step.landmark.kind == "skill" else topic_ids).append(step.landmark.id)
    return list(dict.fromkeys(skill_ids)), list(dict.fromkeys(topic_ids))


def _fix_markup_request(
    base: ProviderRequest, fix: MarkupFix
) -> tuple[StreamRequest, type[TurnTask] | type[Element]]:
    """Build the follow-up `fix_markup` request reusing the turn's context and label table."""
    is_task = fix.task is not None or fix.element.kind in TASK_ELEMENT_KINDS
    target_model: type[TurnTask] | type[Element] = TurnTask if is_task else type(fix.element)
    problems = "; ".join(fix.problems)
    current = (
        f"Current task markup: {fix.task.model_dump_json()}"
        if is_task and fix.task is not None
        else f"Current element: {fix.element.model_dump_json()}"
    )
    kind_note = (
        f"Return only the fixed `TurnTask` JSON object for element_id={fix.element.element_id!r}."
        if is_task
        else (
            f"Return only the fixed `{target_model.__name__}` JSON object for "
            f"element_id={fix.element.element_id!r} and kind={fix.element.kind!r}."
        )
    )
    note = (
        f"The turn body already shown to the learner (keep it as is):\n{fix.text}\n\n"
        f"Element {fix.element.element_id!r} (kind {fix.element.kind!r}) failed the markup check "
        f"({problems}).\n{current}\n"
        f"{kind_note} Use ONLY labels present in the Label table above."
    )
    req = StreamRequest(
        base=replace(
            base,
            json_schema=to_strict_json_schema(target_model),
            schema_name=target_model.__name__,
            repair_hint=None,
        ),
        input_items=(
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": note}],
            },
        ),
        tools=(),
    )
    return req, target_model


def _session_plan(
    goal: SessionGoal,
    plan: PlanDoc | None,
    labels: LabelMap,
    prep: PrepContext | None,
    names: dict[UUID, str],
    settings: Settings,
    landmarks: Sequence[PlanLandmark] = (),
    *,
    has_topic_notes: bool = False,
) -> str:
    """Slot 6: goal, landmarks, plan, and in preparation mode what the hub needs (WP17.4)."""
    text = render_session_plan(goal, plan, labels, landmarks, has_topic_notes=has_topic_notes)
    return text if prep is None else f"{text}\n{render_prep(prep, labels, names, settings)}"


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
        embeddings: EmbeddingClient | None = None,
        compute: ComputeClient | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._settings = settings
        self._reader = reader
        self._neo4j = neo4j
        self._embeddings = embeddings
        self._compute = compute
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
    ) -> tuple[MemorySlice, bool, list[int]]:
        """The slice, whether memory was unavailable, and the `seq` of the overlay events the
        slice was read with (the manifest keeps them, Т-§3.2)."""
        # The wait target is the end of the learner's journal (Т-§2.6): the reader waits for
        # the projector up to it and reports `lag_degraded` (`projector_lag`, §4.4).
        end = await journal_end(self._db, learner_id=request.learner_id)
        try:
            tail = await self._overlay(request, end)
            found = await reader.slice_for_turn(request.learner_id, request.session_id, focus, tail)
        except NEO4J_UNAVAILABLE:
            return MemorySlice(learner_id=request.learner_id, projected_seq=0), True, []
        return found, False, [e.seq for e in tail.events]

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

    def _search(self, learner_id: UUID, domain_id: UUID | None) -> SearchMaterials | None:
        """`search_materials` of the teacher (T-E7, #279): the query is embedded by Modal, the
        nearest chunks of the learner's domain come from the graph. Bound to the learner of
        the turn (isolation, §4.3); without the client or the driver — no search."""
        neo4j, embeddings = self._neo4j, self._embeddings
        if neo4j is None or embeddings is None or domain_id is None:
            return None
        settings = self._settings

        async def search(query: str, skill_ids: Sequence[UUID], limit: int) -> list[MaterialChunk]:
            try:
                vector = await embeddings.embed_query(query)
                hits = await materials_search(
                    neo4j,
                    learner_id=learner_id,
                    domain_id=domain_id,
                    vector=vector,
                    skill_ids=skill_ids,
                    k=limit,
                    reserve=settings.vector_search_reserve,
                )
            except NEO4J_UNAVAILABLE as exc:
                raise EmbeddingsUnavailable("the graph is down") from exc
            return [MaterialChunk(h.chunk_id, None, h.page, h.text) for h in hits]

        return search

    async def _prep(
        self, reader: GraphReader, request: TurnRequest, journal: SessionJournal
    ) -> PrepContext | None:
        """Data of slot 6 in the preparation mode (WP17.4); unreadable memory — none."""
        try:
            return await prep_for_turn(
                reader,
                self._db,
                learner_id=request.learner_id,
                hub_id=journal.goal.hub_id,
                now_ms=self._clock(),
            )
        except NEO4J_UNAVAILABLE:
            return None

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

    async def _attached(self, learner_id: UUID, events: Sequence[StoredEvent]) -> str:
        """Slot 8, the files the learner attached to messages of this session: the text of the
        stored pages, quoted as data. It does not wait for the graph work on the material (that
        needs the model and the embeddings): a file that is `read` is shown whole up to the
        budget; a file still being read, or one that could not be read, is said to be so, so
        the teacher neither pretends to know it nor ignores it."""
        ids: list[UUID] = []
        for event in events:
            if event.type != "message.sent":
                continue
            for raw in event.payload.get("material_ids") or []:
                found = UUID(str(raw))
                if found in ids:
                    ids.remove(found)
                ids.append(found)
        ids = ids[-self._settings.material_attached_files :]
        if not ids:
            return ""
        params = {"learner_id": learner_id, "ids": [str(i) for i in ids]}
        rows = {r["material_id"]: r for r in await self._db.fetch_all(_ATTACHED_MATERIALS, params)}
        pages: dict[UUID, list[tuple[int, str]]] = {}
        for r in await self._db.fetch_all(_ATTACHED_PAGES, params):
            pages.setdefault(r["material_id"], []).append((r["page"], r["text"]))
        return render_attached(
            [(rows[i], pages.get(i, [])) for i in ids if i in rows],
            budget=self._settings.material_attached_chars,
        )

    async def _digest(self, learner_id: UUID, domain_id: UUID) -> str | None:
        """The latest analyst summary of the domain (Т-§13.5): rebuilt by the analyst each
        time from structure, so reading the latest one is no accumulation (law 10)."""
        row = await self._db.fetch_one(_LATEST_DIGEST, {"learner_id": learner_id, "d": domain_id})
        return None if row is None else str(row["payload"]["text"])

    async def _completed(
        self, learner_id: UUID, requests: dict[UUID, str]
    ) -> dict[UUID, list[UUID]]:
        """Requests of the session with a successful `agent.completed` (Т-§13.7), with the tasks
        they made (a `task_generation` request; `teacher@4` shows a code task by its label)."""
        if not requests:
            return {}
        rows = await self._db.fetch_all(
            _COMPLETED, {"learner_id": learner_id, "ids": [str(r) for r in requests]}
        )
        return {
            UUID(str(r["payload"]["request_id"])): [
                UUID(str(t)) for t in r["payload"].get("task_ids") or []
            ]
            for r in rows
            if r["payload"]["ok"]
        }

    async def _code_tasks(self, learner_id: UUID, task_ids: list[UUID]) -> dict[UUID, str]:
        """The code tasks among `task_ids` with their language (Т-§6.10)."""
        found: dict[UUID, str] = {}
        for task_id in task_ids:
            task = await get_task(self._db, learner_id=learner_id, task_id=task_id)
            if task is not None and task.answer_spec.type in CODE_TYPES:
                found[task_id] = task.answer_spec.language or "code"
        return found

    async def _answers(self, learner_id: UUID, journal: SessionJournal) -> list[str]:
        found: list[str] = []
        for task_id in journal.spoiler_tasks:
            task = await get_task(self._db, learner_id=learner_id, task_id=task_id)
            if task is not None:
                found.extend(answer_strings(task.answer_spec))
        for _task_id, spec in journal.spoiler_parts:
            found.extend(answer_strings(spec))
        return list(dict.fromkeys(found))

    async def _inline_verdict(
        self, request: TurnRequest, events: Sequence[StoredEvent], journal: SessionJournal
    ) -> Verdict | None:
        """The wait for the fast verdict (Т-§6.1, WP11.3, T-CH2): an answer of a fast type
        without a verdict goes to compute while the context is assembled, for no longer than
        `symbolic_inline_timeout_ms`. In time: `verdict.recorded` is written (the key
        `verdict:{answer_seq}:{part}` keeps it single); not in time or compute is down:
        nothing is written, the insurance job `check.symbolic` does it later. A failure here
        never fails the turn (law 9)."""
        if (
            self._compute is None
            or journal.answer_seq is None
            or journal.answered_verdict is not None
        ):
            return None
        learner_id = request.learner_id
        try:
            event = next(e for e in events if e.seq == journal.answer_seq)
            payload = AnswerSubmitted.model_validate(event.payload)
            task = await get_task(self._db, learner_id=learner_id, task_id=payload.task_id)
            if task is None:
                return None
            spec = await part_spec(self._db, learner_id=learner_id, task=task, part=payload.part)
            if spec.type in SLOW_TYPES or spec.type in CODE_TYPES:
                return None  # Т-§6.10: code has its verdict from the tests already
            result = await check(
                LearnerAnswer.model_validate(payload.answer.model_dump()),
                spec,
                answer_seq=event.seq,
                part=payload.part or DEFAULT_PART,
                settings=self._settings,
                compute=self._compute,
                timeout_ms=self._settings.symbolic_inline_timeout_ms,
                help_used=await help_in_session(
                    self._db,
                    learner_id=learner_id,
                    session_id=request.session_id,
                    task_id=payload.task_id,
                ),
                recognition_confidence=payload.recognition_confidence,
            )
            if not isinstance(result, Verdict):
                return None
            await record_verdict(
                self._db,
                result,
                learner_id=learner_id,
                session_id=request.session_id,
                domain_id=request.domain_id,
            )
        except Exception as exc:  # the hot path never fails on this step (law 9)
            log.warning("inline_verdict_failed", error=repr(exc), answer_seq=journal.answer_seq)
            return None
        return result

    async def assemble(self, request: TurnRequest) -> TurnSetup:
        settings, db = self._settings, self._db
        learner_id, session_id = request.learner_id, request.session_id
        reader = self._reader(learner_id)
        # The milliseconds of the stages of the assembly (`turn_timings.py`); the parts of the
        # parallel gather are each its own wait, the wall time of the gather is `parallel_ms`.
        parts: dict[str, int] = {}
        started = now_ns()
        events = await session_events_of_action(
            db, learner_id=learner_id, session_id=session_id, action_seq=request.action_seq
        )
        journal = read_journal(events, request.action_seq)
        put_since(parts, "journal_ms", started)
        started = now_ns()
        plan, timezone, snapshot, skill, topic_has_notes = await asyncio.gather(
            load_plan(db, learner_id=learner_id, session_id=session_id),
            learner_timezone(db, learner_id=learner_id),
            load_snapshot(db, learner_id=learner_id, session_id=session_id),
            load_learner_skill(db, learner_id=learner_id, name=journal.skill_invocation)
            if journal.skill_invocation
            else _none(),
            has_topic_notes(db, learner_id=learner_id, topic_id=journal.goal.topic_id),
        )
        put_since(parts, "reads_ms", started)
        # Neo4j down: slots 3, 4 and 7 are empty and the turn says so (`memory_unavailable`,
        # Т-§20.7, D-B-221); the session overlay and the journal still work.
        memory_down = False
        started = now_ns()
        try:
            now = await learner_state(reader, learner_id=learner_id, domain_id=request.domain_id)
            hubs = await reader.domain_goals(request.domain_id)
            topics = _goal_topics(journal.goal, hubs)
            memory: list[TopicMemory] = await reader.topic_memory(topics) if topics else []
            read_topics = skill_topics(topics, hubs, settings.label_max_topics)
            skill_memory: list[TopicMemory] = (
                memory
                if read_topics == topics
                else await reader.topic_memory(read_topics)
                if read_topics
                else []
            )
        except NEO4J_UNAVAILABLE:
            memory_down = True
            now, hubs, topics, memory = LearnerState(profile=Profile()), [], [], []
            skill_memory = []
        put_since(parts, "memory_ms", started)
        topic_skills = [s.skill_id for m in skill_memory for s in m.skills]
        targets = target_levels(hubs, memory)
        focus = SliceFocus(
            skill_ids=list(dict.fromkeys([*journal.task_skills, *topic_skills])),
            topic_ids=topics,
            domain_id=request.domain_id,
        )
        started = now_ns()
        review = await self._review(reader, request, journal, topic_skills)
        prep = await self._prep(reader, request, journal)
        put_since(parts, "review_prep_ms", started)
        prep_skills = [c.skill_id for c in prep.candidates] if prep else []
        plan_skills, plan_topics = _plan_refs(plan)
        read_focus = focus.model_copy(
            update={
                "skill_ids": list(
                    dict.fromkeys(
                        [
                            *focus.skill_ids,
                            *(c.skill_id for c in review),
                            *prep_skills,
                            *plan_skills,
                        ]
                    )
                )
            }
        )
        hub_topics = [t.topic_id for hub in hubs for t in hub.topics]
        label_topics: list[UUID] = list(
            dict.fromkeys(
                [*topics, *hub_topics, *(prep.coverage_topic_ids if prep else []), *plan_topics]
            )
        )
        started = now_ns()
        sliced, chunks, digest, completed, inline, cards = await asyncio.gather(
            timed(parts, "slice_ms", self._slice(reader, request, read_focus)),
            timed(parts, "chunks_ms", self._chunks(learner_id, read_focus.skill_ids)),
            timed(parts, "digest_ms", self._digest(learner_id, request.domain_id)),
            timed(parts, "completed_ms", self._completed(learner_id, journal.requests)),
            # the wait for the fast verdict: ~0 when the action has none to wait for
            timed(parts, "inline_verdict_ms", self._inline_verdict(request, events, journal)),
            timed(
                parts,
                "cards_ms",
                cards_for_turn(
                    db,
                    learner_id=learner_id,
                    topic_ids=label_topics[: settings.label_max_topics],
                    limit=settings.label_max_cards,
                ),
            ),
        )
        put_since(parts, "parallel_ms", started)
        # one query over the few files the learner attached: after the parallel part (the gather
        # above is as long as it can be typed)
        attached = await timed(parts, "attached_ms", self._attached(learner_id, events))
        memory_slice, unavailable, overlay_seqs = sliced
        known_topics = {m.topic_id for m in memory}
        extra_topic_ids = (
            list(
                dict.fromkeys(
                    a.id
                    for a in plan.landmark_edits.added
                    if a.kind == "topic" and a.id not in known_topics
                )
            )
            if plan is not None
            else []
        )
        if not memory_down and extra_topic_ids:
            try:
                memory.extend(await reader.topic_memory(extra_topic_ids))
            except NEO4J_UNAVAILABLE:
                memory_down = True
        verdict_frame: VerdictFrame | None = None
        if inline is not None:
            # The verdict came in time: the context, the guard and `solved` see it.
            verdict_frame = VerdictFrame(
                answer_seq=inline.answer_seq,
                part=inline.part,
                outcome=inline.outcome,
                method=inline.method,
                verification=inline.verification,
            )
            events = await session_events_of_action(
                db, learner_id=learner_id, session_id=session_id, action_seq=request.action_seq
            )
            journal = read_journal(events, request.action_seq)
        answers = await self._answers(learner_id, journal)
        unavailable = unavailable or memory_down
        freeze = snapshot is None
        frozen = frozen_slots(snapshot or snapshot_slots(now, timezone), now, timezone)
        skill_ids = list(
            dict.fromkeys([*read_focus.skill_ids, *(s.skill_id for s in memory_slice.skills)])
        )
        bank = {} if unavailable else await self._bank(reader, learner_id, review, targets)
        made = [t for ts in completed.values() for t in ts]
        code_tasks = await self._code_tasks(learner_id, made)
        labels = LabelMap.build(
            settings,
            skills=skill_ids,
            topics=label_topics,
            tasks=list(
                dict.fromkeys(
                    [
                        *journal.open_tasks,
                        *(t.task_id for ts in bank.values() for t in ts),
                        *code_tasks,
                    ]
                )
            ),
            misconceptions=[m.misconception_id for m in memory_slice.misconceptions],
            hubs=[hub.hub_id for hub in hubs],
            chunks=[chunk.chunk_id for chunk in chunks],
            cards=[card.card_id for card in cards],
            requests=list(journal.requests),
        )
        domain_name = next((h.name for h in hubs if h.kind == "domain_all"), None)
        names: dict[UUID, str] = {s.skill_id: s.name for s in memory_slice.skills if s.name}
        names.update({m.topic_id: canonical_topic_name(m.name, domain_name) for m in memory})
        names.update({hub.hub_id: hub.name for hub in hubs})
        names.update(
            {
                t.topic_id: canonical_topic_name(t.name, domain_name)
                for hub in hubs
                for t in hub.topics
            }
        )
        card_names = {
            card.card_id: card_label_line(names.get(card.topic_id), card.prompt) for card in cards
        }
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
                        + _code_task_note(completed[r], code_tasks, labels)
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
        landmarks: list[PlanLandmark] = []
        if not unavailable:
            memories_by_id = {m.topic_id: m for m in memory}
            fresh_by_id = {s.skill_id: s for s in memory_slice.skills}
            current_ref = (
                None
                if plan is None or plan.current_step is None
                else landmark_ref(plan.current_step.landmark)
            )
            points = points_from_memory(
                journal.goal.hub_id,
                journal.goal.topic_id,
                hubs,
                memories_by_id,
                settings,
                current=current_ref,
                fresh_skills=fresh_by_id,
            )
            if plan is not None and (plan.landmark_edits.added or plan.landmark_edits.removed):
                resolved = resolve_added_points(plan, memories_by_id, fresh_by_id, settings)
                points = apply_edits(points, plan, resolved)
            visited = visited_landmarks([e.payload for e in events if e.type == "turn.committed"])
            landmarks = view_of(points, current_ref, visited=visited)
        variables = teacher_variables(
            TeacherSlots(
                frozen=frozen,
                conversation=render_conversation(events, labels, settings),
                session_plan=_session_plan(
                    journal.goal,
                    plan,
                    labels,
                    prep,
                    names,
                    settings,
                    landmarks,
                    has_topic_notes=topic_has_notes,
                ),
                memory_picture=picture,
                materials="\n".join(
                    part for part in (render_materials(chunks, labels), attached) if part
                ),
                labels=render_labels(labels, {**names, **card_names}),
                skill=skill,
            )
        )
        role = request.role
        prompt_version = settings.llm_roles[role].prompt or "teacher@4"
        prompt_name, version = prompt_version.rsplit("@", 1)
        template = self._gateway.prompts.load(prompt_name, int(version))
        variables = fit_budget(template.render, variables, settings.teacher_context_max_tokens)
        effort = turn_effort(role, journal)
        output_schema, parse_output = teacher_schema_for_prompt(prompt_name, int(version))
        stream, prompt = self._gateway.stream_request(
            role,
            int(version),
            output_schema,
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
            search=self._search(learner_id, request.domain_id),
        )

        async def execute_tool(name: str, arguments: str) -> str:
            return await run_tool(name, arguments, tools)

        streaming = self._gateway.streaming(
            role,
            prompt,
            learner_id=learner_id,
            session_id=session_id,
            turn_id=request.manifest_id,
        )

        async def fix_markup(fix: MarkupFix) -> TurnTask | Element | None:
            fix_request, target_model = _fix_markup_request(stream.base, fix)
            step = await stream_step(streaming, fix_request, _ignore_text)
            if step.completed.status == "incomplete" or not step.text.strip():
                return None
            return target_model.model_validate_json(step.text)

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
                    labels=labels,
                    has_spoiler=lambda text: any(
                        answer_in_text(text, answer, settings) for answer in answers
                    ),
                    remove_spoiler=lambda text: remove_answer_sentences(text, answers, settings),
                    spoiler_hits=lambda text: spoiler_hits(text, answers, settings),
                ),
                effort=effort,
                degraded_mode=degraded,
                verdict=verdict_frame,
                parse_output=parse_output,
            ),
            provider=streaming,
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
                overlay_seqs=overlay_seqs,
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
            timings=parts,
            fix_markup=fix_markup,
        )


async def _none() -> None:
    return None


async def _ignore_text(_text: str) -> None:
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
        embeddings=resources.embeddings,
        compute=resources.compute,
    )
