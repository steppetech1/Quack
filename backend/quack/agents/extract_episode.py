"""The extractor by episodes, WP15 п.1–3 (ТЗ Ф4; Т-§7.5, Т-§7.6, Т-§4.5): the window, the
input of `extractor@3` and the call of the role. D-B-162.

- **Window** (п.1, Т-§7.5): the episode `[from_seq, to_seq]` plus `episode_context_turns` turns
  before it, for understanding only; only the episode is interpreted.
- **Input** (п.2, Т-§7.6): the events of the window as numbered text lines — learner and teacher
  replies, markup, verdicts, canvas lines, voice, "unclear"; candidate skills — the skills of
  the topics active in the session plus the nearest by the episode vector (with Modal down —
  only the session's), at most `candidate_skills_k`; known misconceptions of the candidates;
  the table of gap types (П-6.4). Everything with short labels.
- **Call** (п.3): `extractor@3` through the gateway, strict schema `ExtractorOutputV3`; an
  unknown label goes back to the model once (the gateway's repair path, Т-§4.5).

- **Re-extraction** (п.6): `episode.reassigned` and `skill.split` restrict the candidates to
  the skills of the named topic or of the split (Т-§2.8, Т-§4.11); the new observation has its
  own `trigger_seq` in the key.

- **Check** (п.4, п.8): `check_extracted` of stream A (D-A-124) on the context built here —
  the text of each episode event, the levels of its task, the pairs «skill + level» with a
  code verdict in the episode.
- **Write** (п.5): `observation.extracted` with key `obs:{episode_seq}:{extractor@3}:{trigger_seq}`
  and the `ops.coverage` row in one transaction (WP7.3, law 7). D-B-165.
- **Job** `extract.episode`: the episode is the seq of its `episode.closed` (Т-§2.1); candidates
  from A's `GraphReader.extraction_candidates` (S4), the markup of tasks from the log. D-B-172.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from quack.agents.schemas import ExtractorOutputV3
from quack.config import Settings
from quack.db import Db
from quack.embeddings.client import EmbeddingClient, EmbeddingsUnavailable
from quack.events.event_types import (
    AnswerSubmitted,
    CanvasLineCommitted,
    CanvasLineErased,
    CheckSkipped,
    ElementResponded,
    EpisodeClosed,
    HandwritingCorrected,
    HintRequested,
    HintShown,
    ItemFlaggedUnclear,
    MessageSent,
    SolutionRequested,
    TaskClosed,
    TaskCreated,
    TurnCommitted,
    TypoDeclared,
    VoiceTranscribed,
)
from quack.events.keys import observation_key
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.records import TaskMarkupRecord
from quack.events.values import CheckFailed, Verdict
from quack.graph.evidence_validation import (
    CheckedElements,
    EpisodeCheckContext,
    EpisodeSource,
    check_extracted,
)
from quack.graph.read import CandidateSkill, GraphReader, MisconceptionFact, create_graph_reader
from quack.jobs.coverage import mark_covered
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.practice.repo import event_by_seq, session_events_up_to, task_events

EXTRACTOR_ROLE = "extractor"
NONE_LINE = "(none)"

# П-6.4, in the words of the doctrine (`extractor@3`, «Caution in diagnosis» п.2).
GAP_TYPE_TABLE = (
    "no_idea — only naming the idea itself helps; their own prediction is empty or random.\n"
    "when_to_apply — solves the problem when the method is named, but not otherwise; a hint "
    "about the kind of technique helps.\n"
    "confusion — used a neighboring idea or picked an option built on a neighboring rule; it "
    "repeats on similar problems.\n"
    "execution — the method is right, the working has a mistake; showing the step helps.\n"
    "slip — a one-off; they did the same operation correctly before, or noticed it themselves "
    'after a short "check this".\n'
    "misconception — they are wrong confidently and on different problems, or picked an option "
    "built from this distortion.\n"
    "none — the outcome is correct."
)

_OUTCOME = {"correct": "correct", "incorrect": "incorrect", "partial": "partly correct"}


@dataclass(frozen=True)
class EpisodeWindow:
    """`before` — the context turns (record nothing from them); `episode` — the episode."""

    before: list[StoredEvent]
    episode: list[StoredEvent]


def extraction_window(
    session_events: Sequence[StoredEvent], from_seq: int, to_seq: int, settings: Settings
) -> EpisodeWindow:
    """The episode and the `episode_context_turns` turns before it (Т-§7.5). A turn ends with
    its `turn.committed`; the events of its own append (`causation_seq` = its `action_seq`,
    WP2.2) stay with it."""
    ordered = sorted(session_events, key=lambda e: e.seq)
    episode = [e for e in ordered if from_seq <= e.seq <= to_seq]
    earlier = [e for e in ordered if e.seq < from_seq]
    commits = [e for e in earlier if e.type == "turn.committed"]
    keep = settings.episode_context_turns
    if keep <= 0:
        return EpisodeWindow(before=[], episode=episode)
    if len(commits) <= keep:
        return EpisodeWindow(before=earlier, episode=episode)
    boundary = commits[-keep - 1]
    outside = {int(e.payload["action_seq"]) for e in commits[: len(commits) - keep]}
    before = [
        e
        for e in earlier
        if e.seq > boundary.seq and (e.causation_seq is None or e.causation_seq not in outside)
    ]
    return EpisodeWindow(before=before, episode=episode)


def _task(labels: LabelMap, task_id: UUID) -> str:
    return labels.label("task", task_id) or "a task"


def _turn_lines(seq: int, turn: TurnCommitted, labels: LabelMap) -> list[str]:
    lines = [f"[{seq}] teacher: {turn.text}"]
    prompts = {
        element.element_id: prompt
        for element in turn.markup.elements
        if isinstance(prompt := getattr(element, "prompt", None), str)
    }
    for task in turn.markup.tasks:
        tests = ", ".join(
            f"{labels.label('skill', t.skill_id) or 'a new skill'} level {t.level}"
            if t.skill_id is not None
            else f"a new skill level {t.level}"
            for t in task.tests
        )
        line = f"[{seq}] problem given {_task(labels, task.task_id)} (tests: {tests})"
        statement = prompts.get(task.element_id)
        lines.append(f"{line}: {statement}" if statement else line)
    for exposure in turn.markup.exposures:
        skill = labels.label("skill", exposure.skill_id) or "a skill"
        lines.append(f"[{seq}] teacher showed for {skill}: {exposure.kind.replace('_', ' ')}")
    return lines


def event_lines(event: StoredEvent, labels: LabelMap, answers: dict[int, UUID]) -> list[str]:
    """One event as numbered lines (`[seq] who: text`); events without text give none."""
    data, head = event.payload, f"[{event.seq}]"
    match event.type:
        case "message.sent":
            text = MessageSent.model_validate(data).text
            return [f"{head} learner: {text}"] if text else []
        case "answer.submitted":
            answer = AnswerSubmitted.model_validate(data)
            part = f" part {answer.part}" if answer.part else ""
            lines = [
                f"{head} learner's answer to {_task(labels, answer.task_id)}{part}: "
                f"{answer.answer.latex or answer.answer.value}"
            ]
            if answer.text:
                lines.append(f"{head} learner's reasoning: {answer.text}")
            return lines
        case "verdict.recorded":
            verdict = Verdict.model_validate(data)
            task_id = answers.get(verdict.answer_seq)
            target = f" to {_task(labels, task_id)}" if task_id else ""
            by = "the checking code" if verdict.verification == "code" else "a model"
            words = _OUTCOME.get(verdict.outcome, verdict.outcome)
            return [f"{head} check by {by} of the answer [{verdict.answer_seq}]{target}: {words}"]
        case "check.failed":
            failed = CheckFailed.model_validate(data)
            return [f"{head} the checking code could not check the answer [{failed.answer_seq}]"]
        case "typo.declared":
            typo = TypoDeclared.model_validate(data)
            return [f"{head} learner says the answer [{typo.answer_seq}] was a typo"]
        case "element.responded":
            response = ElementResponded.model_validate(data)
            lines = [
                f"{head} learner's response to element {response.element_id}: {response.response}"
            ]
            if response.text:
                lines.append(f"{head} learner's reasoning: {response.text}")
            return lines
        case "hint.requested":
            task_id = HintRequested.model_validate(data).task_id
            return [f"{head} learner asked for a hint on {_task(labels, task_id)}"]
        case "solution.requested":
            task_id = SolutionRequested.model_validate(data).task_id
            return [f"{head} learner asked for the solution of {_task(labels, task_id)}"]
        case "check.skipped":
            task_id = CheckSkipped.model_validate(data).task_id
            return [f"{head} learner skipped the check of {_task(labels, task_id)}"]
        case "item.flagged_unclear":
            flag = ItemFlaggedUnclear.model_validate(data)
            return [f'{head} learner marked as unclear: "{flag.text}"']
        case "canvas.line_committed":
            line = CanvasLineCommitted.model_validate(data)
            crossed = " (crossed out)" if line.crossed_out else ""
            return [f"{head} learner wrote on the canvas{crossed}: {line.text}"]
        case "canvas.line_erased":
            erased = CanvasLineErased.model_validate(data)
            return [f"{head} learner erased the canvas line: {erased.text}"]
        case "handwriting.corrected":
            fixed = HandwritingCorrected.model_validate(data)
            return [f"{head} learner corrected the reading of a canvas line: {fixed.text}"]
        case "voice.transcribed":
            return [f"{head} learner said: {VoiceTranscribed.model_validate(data).text}"]
        case "turn.committed":
            return _turn_lines(event.seq, TurnCommitted.model_validate(data), labels)
        case "hint.shown":
            hint = HintShown.model_validate(data)
            return [f"{head} hint, step {hint.step} of 3, on {_task(labels, hint.task_id)}"]
        case "task.closed":
            closed = TaskClosed.model_validate(data)
            how = closed.how.replace("_", " ")
            return [f"{head} {_task(labels, closed.task_id)} closed: {how}"]
        case _:
            return []


def render_events(events: Sequence[StoredEvent], labels: LabelMap, answers: dict[int, UUID]) -> str:
    lines = [line for event in events for line in event_lines(event, labels, answers)]
    return "\n".join(lines) or NONE_LINE


def window_tasks(window: EpisodeWindow) -> list[UUID]:
    """The tasks of the window by appearance: the markup of turns and the events of a task."""
    tasks: list[UUID] = []
    for event in [*window.before, *window.episode]:
        if event.type == "turn.committed":
            tasks.extend(
                t.task_id for t in TurnCommitted.model_validate(event.payload).markup.tasks
            )
        elif "task_id" in event.payload:
            tasks.append(UUID(str(event.payload["task_id"])))
    return list(dict.fromkeys(tasks))


def window_labels(
    window: EpisodeWindow,
    candidates: Sequence[CandidateSkill],
    misconceptions: Sequence[MisconceptionFact],
    settings: Settings,
) -> LabelMap:
    """Labels of the call: candidates in their order, the tasks of the window by appearance,
    the known misconceptions of the candidates."""
    tasks = window_tasks(window)
    skills = [c.skill_id for c in candidates]
    known = [m.misconception_id for m in misconceptions if m.skill_id in set(skills)]
    return LabelMap.build(settings, skills=skills, tasks=tasks, misconceptions=known)


def extractor_variables(
    window: EpisodeWindow,
    candidates: Sequence[CandidateSkill],
    misconceptions: Sequence[MisconceptionFact],
    labels: LabelMap,
) -> dict[str, str]:
    """The five input blocks of `extractor@3`."""
    answers = {
        e.seq: AnswerSubmitted.model_validate(e.payload).task_id
        for e in [*window.before, *window.episode]
        if e.type == "answer.submitted"
    }
    skill_lines = [
        f"{label}: {c.name}" for c in candidates if (label := labels.label("skill", c.skill_id))
    ]
    misconception_lines = [
        f"{label} (skill {skill}): {m.statement}"
        for m in misconceptions
        if (label := labels.label("misconception", m.misconception_id))
        and (skill := labels.label("skill", m.skill_id))
    ]
    return {
        "events_before": render_events(window.before, labels, answers),
        "episode": render_events(window.episode, labels, answers),
        "candidate_skills": "\n".join(skill_lines) or NONE_LINE,
        "known_misconceptions": "\n".join(misconception_lines) or NONE_LINE,
        "gap_types": GAP_TYPE_TABLE,
    }


def unknown_label_errors(
    output: ExtractorOutputV3, skills: Collection[str], known: Collection[str]
) -> str | None:
    """Т-§4.5: a label of skill or misconception that the call did not give."""
    unknown: list[str] = []
    for element in output.elements:
        if element.skill is not None and element.skill not in skills:
            unknown.append(element.skill)
        if element.misconception is not None and element.misconception not in known:
            unknown.append(element.misconception)
        if element.root_cause is not None and element.root_cause.skill not in skills:
            unknown.append(element.root_cause.skill)
    if not unknown:
        return None
    return "unknown labels: " + ", ".join(dict.fromkeys(unknown)) + "; use only the given labels"


def label_errors(output: ExtractorOutputV3, labels: LabelMap) -> str | None:
    """Т-§4.5: a label the call did not give goes back to the model once."""
    return unknown_label_errors(output, labels.labels("skill"), labels.labels("misconception"))


def extraction_key(episode_seq: int, prompt: str, trigger_seq: int) -> str:
    """WP15 п.5: `obs:{episode_seq}:{extractor@3}:{trigger_seq}` — the call carries the same key."""
    return observation_key(episode_seq, prompt, trigger_seq)


async def call_extractor(
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    episode_seq: int,
    trigger_seq: int,
    variables: dict[str, str],
    labels: LabelMap,
) -> ExtractorOutputV3:
    """One call of `extractor@3` per episode and trigger; no second opinion (Т-§7.7)."""
    prompt = settings.llm_roles[EXTRACTOR_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'extractor' has no prompt")
    result = await gateway.generate_structured(
        EXTRACTOR_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        ExtractorOutputV3,
        variables,
        learner_id,
        session_id,
        None,
        extraction_key(episode_seq, prompt, trigger_seq),
        check=lambda output: label_errors(output, labels),
    )
    return result.value


def source_text(event: StoredEvent) -> str:
    """The words of an event that a quote may come from (Т-§7.6: a substring of the source
    event): what the learner wrote, said or typed and the teacher's reply. Events without words
    (verdicts, hints, closures) give "": they may be cited, but not quoted."""
    data = event.payload
    match event.type:
        case "message.sent":
            parts = [MessageSent.model_validate(data).text]
        case "answer.submitted":
            answer = AnswerSubmitted.model_validate(data)
            parts = [answer.answer.latex, answer.answer.value, answer.text]
        case "element.responded":
            response = ElementResponded.model_validate(data)
            parts = [str(response.response), response.text]
        case "item.flagged_unclear":
            parts = [ItemFlaggedUnclear.model_validate(data).text]
        case "canvas.line_committed":
            parts = [CanvasLineCommitted.model_validate(data).text]
        case "canvas.line_erased":
            parts = [CanvasLineErased.model_validate(data).text]
        case "handwriting.corrected":
            parts = [HandwritingCorrected.model_validate(data).text]
        case "voice.transcribed":
            parts = [VoiceTranscribed.model_validate(data).text]
        case "turn.committed":
            parts = [TurnCommitted.model_validate(data).text]
        case _:
            parts = []
    return "\n".join(str(part) for part in parts if part)


def _event_task(event: StoredEvent, answers: Mapping[int, UUID]) -> UUID | None:
    if event.type == "verdict.recorded":
        return answers.get(Verdict.model_validate(event.payload).answer_seq)
    if event.type != "turn.committed" and "task_id" in event.payload:
        return UUID(str(event.payload["task_id"]))
    return None


def episode_check_context(
    window: EpisodeWindow,
    labels: LabelMap,
    task_tests: Mapping[UUID, Sequence[tuple[UUID, int]]],
) -> EpisodeCheckContext:
    """What `check_extracted` checks the elements against (WP15 п.4, п.8; D-B-165).

    `task_tests` — task id → its `tests` pairs (skill id, level) from the stored markup. An
    event of a task caps the level at the highest level of the task; a code verdict in the
    episode bars `evidence` for each pair of its task. Only the episode is citable (`Т-§7.5`)."""
    answers = {
        e.seq: AnswerSubmitted.model_validate(e.payload).task_id
        for e in [*window.before, *window.episode]
        if e.type == "answer.submitted"
    }
    episode: dict[int, EpisodeSource] = {}
    task_levels: dict[int, int] = {}
    code_verdicts: set[tuple[UUID, int]] = set()
    for event in window.episode:
        at_ms = int(event.occurred_at.timestamp() * 1000)
        episode[event.seq] = EpisodeSource(event.seq, source_text(event), at_ms)
        task_id = _event_task(event, answers)
        tests = task_tests.get(task_id, ()) if task_id is not None else ()
        if tests:
            task_levels[event.seq] = max(level for _, level in tests)
        if (
            event.type == "verdict.recorded"
            and Verdict.model_validate(event.payload).verification == "code"
        ):
            code_verdicts.update(tests)
    return EpisodeCheckContext(
        skill_labels=labels.labels("skill"),
        misconception_labels=labels.labels("misconception"),
        episode=episode,
        task_levels=task_levels,
        code_verdicts=frozenset(code_verdicts),
    )


async def record_extraction(
    db: Db,
    learner_id: UUID,
    *,
    prompt: str,
    episode_seq: int,
    trigger_seq: int,
    checked: CheckedElements,
    session_id: UUID | None = None,
    domain_id: UUID | None = None,
) -> StoredEvent:
    """WP15 п.5: `observation.extracted` and its coverage row in one transaction (WP7.3,
    law 7); an empty output is still an observation — the episode was read (D-B-165). A retry
    returns the stored event and points the row at it again (law 5)."""
    async with db.transaction():
        (stored,) = await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="observation.extracted",
                    payload={
                        "episode_seq": episode_seq,
                        "extractor": prompt,
                        "trigger_seq": trigger_seq,
                        "elements": [e.model_dump(mode="json") for e in checked.elements],
                        "rejected": [r.model_dump(mode="json") for r in checked.rejected],
                    },
                    actor=prompt,
                    idempotency_key=observation_key(episode_seq, prompt, trigger_seq),
                    session_id=session_id,
                    domain_id=domain_id,
                    causation_seq=trigger_seq,
                )
            ],
        )
        await mark_covered(
            db,
            learner_id,
            episode_seq=episode_seq,
            extractor=prompt,
            status="extracted",
            observation_seq=stored.seq,
        )
    return stored


_MARKUP_EVENTS = ("task.created", "task.verified", "task.key_disputed", "task.markup_disputed")


def markup_pairs(markup: TaskMarkupRecord) -> list[tuple[UUID, int]]:
    """The pairs (skill id, level) of the markup and its parts; a new skill has no id yet."""
    tested = [*markup.tests, *(t for part in markup.parts or () for t in part.tests or ())]
    return list(dict.fromkeys((t.skill_id, t.level) for t in tested if t.skill_id is not None))


def task_tests(events: Sequence[StoredEvent]) -> dict[UUID, list[tuple[UUID, int]]]:
    """Task id → its `tests` pairs from `task.created`, replaced by a later `corrected_markup`
    of the check (WP12.2). `events` are in seq order."""
    tests: dict[UUID, list[tuple[UUID, int]]] = {}
    for event in events:
        if event.type == "task.created":
            created = TaskCreated.model_validate(event.payload)
            tests[created.task_id] = markup_pairs(created.markup)
        elif (corrected := event.payload.get("corrected_markup")) is not None:
            task_id = UUID(str(event.payload["task_id"]))
            tests[task_id] = markup_pairs(TaskMarkupRecord.model_validate(corrected))
    return tests


def episode_text(window: EpisodeWindow) -> str:
    """The raw words of the episode: what its vector is computed from (WP15 п.2). D-B-172."""
    return "\n".join(text for event in window.episode if (text := source_text(event)))


async def episode_vector(embeddings: EmbeddingClient, window: EpisodeWindow) -> list[float] | None:
    """`None` when the episode has no words or Modal is down: candidates of the session only."""
    text = episode_text(window)
    if not text:
        return None
    try:
        return await embeddings.embed_query(text)
    except EmbeddingsUnavailable:
        return None


@dataclass(frozen=True)
class ExtractionTrigger:
    """The payload of `extract.episode` (WP7.1, D-A-135): `episode.closed` gives only its own
    seq — the episode; `episode.reassigned` names the episode and the topic; `skill.split` —
    the episode and the skills of the split."""

    episode_seq: int
    trigger_seq: int
    topic_id: UUID | None = None
    skill_ids: list[UUID] | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ExtractionTrigger:
        trigger_seq = int(str(payload["trigger_seq"]))
        topic = payload.get("topic_id")
        skills = payload.get("restrict_skill_ids")
        return cls(
            episode_seq=int(str(payload.get("episode_seq", trigger_seq))),
            trigger_seq=trigger_seq,
            topic_id=UUID(str(topic)) if topic is not None else None,
            skill_ids=[UUID(str(s)) for s in skills] if isinstance(skills, list) else None,
        )


async def extract_episode(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    reader: GraphReader,
    embeddings: EmbeddingClient,
    *,
    learner_id: UUID,
    trigger: ExtractionTrigger,
) -> StoredEvent:
    """WP15 п.1–6: the episode is the range of its `episode.closed` (its seq is the episode,
    Т-§2.1); window, candidates, one call, the check by code, the observation with coverage."""
    closed = await event_by_seq(db, learner_id=learner_id, seq=trigger.episode_seq)
    if closed is None or closed.type != "episode.closed" or closed.session_id is None:
        raise ValueError(f"seq {trigger.episode_seq} is no episode.closed of a session")
    bounds = EpisodeClosed.model_validate(closed.payload)
    session_id = closed.session_id
    events = await session_events_up_to(
        db, learner_id=learner_id, session_id=session_id, to_seq=bounds.to_seq
    )
    window = extraction_window(events, bounds.from_seq, bounds.to_seq, settings)
    found = await reader.extraction_candidates(
        learner_id,
        session_id,
        await episode_vector(embeddings, window),
        settings.candidate_skills_k,
        topic_id=trigger.topic_id,
        skill_ids=trigger.skill_ids,
    )
    labels = window_labels(window, found.skills, found.misconceptions, settings)
    output = await call_extractor(
        gateway,
        settings,
        learner_id=learner_id,
        session_id=session_id,
        episode_seq=trigger.episode_seq,
        trigger_seq=trigger.trigger_seq,
        variables=extractor_variables(window, found.skills, found.misconceptions, labels),
        labels=labels,
    )
    markups = await task_events(
        db, learner_id=learner_id, task_ids=window_tasks(window), types=_MARKUP_EVENTS
    )
    context = episode_check_context(window, labels, task_tests(markups))
    prompt = settings.llm_roles[EXTRACTOR_ROLE].prompt
    assert prompt is not None  # call_extractor has checked it
    return await record_extraction(
        db,
        learner_id,
        prompt=prompt,
        episode_seq=trigger.episode_seq,
        trigger_seq=trigger.trigger_seq,
        checked=check_extracted(output.elements, context),
        session_id=session_id,
        domain_id=closed.domain_id,
    )


async def run_extract_episode(job: ClaimedJob, ctx: JobContext, reader: GraphReader) -> StoredEvent:
    """The `extract.episode` job; the reader of memory is A's (S4). D-B-172."""
    if job.learner_id is None:
        raise ValueError("extract.episode needs learner_id")
    return await extract_episode(
        ctx.db,
        ctx.llm,
        ctx.settings,
        reader,
        ctx.embeddings,
        learner_id=job.learner_id,
        trigger=ExtractionTrigger.from_payload(job.payload),
    )


@register_job_handler("extract.episode")
async def handle_extract_episode(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("extract.episode needs learner_id")
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)
    await run_extract_episode(job, ctx, reader)
