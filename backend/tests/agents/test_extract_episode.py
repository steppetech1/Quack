"""The extractor by episodes, WP15 (ТЗ Ф4; Т-§7.5, Т-§7.6; `T-X1`): the window, the input of
`extractor@3` with labels, the call of the role (D-B-162); the check context and the write of
`observation.extracted` with coverage (D-B-165)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.agents import extract_episode
from quack.agents.extract_episode import (
    GAP_TYPE_TABLE,
    NONE_LINE,
    ExtractionTrigger,
    call_extractor,
    episode_check_context,
    extraction_key,
    extraction_window,
    extractor_variables,
    label_errors,
    record_extraction,
    window_labels,
)
from quack.agents.schemas import ExtractedElement, ExtractorOutputV3
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import (
    AnswerSubmitted,
    CanvasLineCommitted,
    EpisodeClosed,
    HintShown,
    ItemFlaggedUnclear,
    MessageSent,
    TaskCreated,
    TaskVerified,
    TurnCommitted,
    VoiceTranscribed,
)
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.records import (
    BodyBlockRecord,
    ElementRecord,
    ExposureRecord,
    PlanUpdateRecord,
    TaskElementRecord,
    TaskMarkupRecord,
    TaskPartRecord,
    TestedLevelRecord,
    TurnHeadRecord,
    TurnMarkupRecord,
    TurnTaskRecord,
)
from quack.events.registry import EventPayload
from quack.events.values import AnswerSpec, LearnerAnswer, MathValue, NewSkill, Verdict
from quack.graph.evidence_validation import CheckedElements, check_extracted
from quack.graph.read import CandidateSkill, ExtractionCandidates, MisconceptionFact
from quack.llm.gateway import LLMGateway, LLMResult
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner
from tests.support.embeddings import FakeEmbeddingClient
from tests.support.graph_read import SKILL_FAILED, SKILL_QUIET, TOPIC_OPEN, FixedGraphReader

SETTINGS = Settings()
LEARNER = uuid4()
SESSION = uuid4()
SKILL = uuid4()
OTHER_SKILL = uuid4()
MISCONCEPTION = uuid4()
TASK = uuid4()
TOPIC = uuid4()


def _candidate(skill_id: UUID, name: str) -> CandidateSkill:
    return CandidateSkill(skill_id=skill_id, name=name, topic_id=TOPIC)


def _known(misconception_id: UUID, skill_id: UUID, statement: str) -> MisconceptionFact:
    return MisconceptionFact(
        misconception_id=misconception_id,
        skill_id=skill_id,
        status="suspected",
        statement=statement,
    )


class _Log:
    def __init__(self) -> None:
        self.events: list[StoredEvent] = []

    def add(self, type_: str, payload: BaseModel, causation_seq: int | None = None) -> int:
        seq = len(self.events) + 1
        self.events.append(
            StoredEvent(
                learner_id=LEARNER,
                seq=seq,
                id=seq,
                type=type_,
                schema_version=1,
                actor="learner",
                domain_id=None,
                session_id=SESSION,
                causation_seq=causation_seq,
                correlation_id=None,
                idempotency_key=f"k{seq}",
                payload_hash="h",
                occurred_at=datetime(2026, 11, 3, tzinfo=UTC),
                payload=payload.model_dump(mode="json"),
                source_refs=[],
            )
        )
        return seq


def _committed(action_seq: int, text: str, *, task: bool = False) -> TurnCommitted:
    tasks = (
        [
            TurnTaskRecord(
                element_id="e1",
                task_id=TASK,
                tests=[TestedLevelRecord(skill_id=SKILL, level=3)],
                method_named=True,
                answer=AnswerSpec(type="number", value=MathValue(latex="4", sympy="4")),
            )
        ]
        if task
        else []
    )
    elements: list[ElementRecord] = (
        [
            TaskElementRecord(
                element_id="e1",
                kind="task",
                prompt="Find the limit of a_n",
                canvas=True,
                ask_confidence=False,
            )
        ]
        if task
        else []
    )
    return TurnCommitted(
        action_seq=action_seq,
        text=text,
        blocks=[BodyBlockRecord(block_id="b1", text=text, element_id=None)],
        head=TurnHeadRecord(
            intent="practice",
            target_skill_ids=[SKILL],
            verdict_stance=None,
            branch_type=None,
            approach="attempt_first",
        ),
        markup=TurnMarkupRecord(
            tasks=tasks,
            elements=elements,
            exposures=[ExposureRecord(skill_id=SKILL, kind="example", approach="example_first")]
            if task
            else [],
            targets_misconception_id=None,
            plan_update=PlanUpdateRecord(
                completed_steps=[],
                current_step="s",
                next_steps=[],
                branches=[],
                cancel_request_ids=[],
            ),
            agent_requests=[],
            corrections_acknowledged=[],
            decision_reason="r",
            deadline=None,
        ),
        role="teacher",
        model="m",
        effort="low",
        degraded_mode=None,
        guard_retries=0,
        manifest_id=uuid4(),
    )


def _turn(log: _Log, n: int) -> int:
    action = log.add("message.sent", MessageSent(text=f"question {n}"))
    log.add("turn.committed", _committed(action, f"reply {n}"), causation_seq=action)
    # an event of the turn's own append, written after `turn.committed` (WP2.2)
    log.add("hint.shown", HintShown(task_id=TASK, step=1, source="teacher"), causation_seq=action)
    return action


def test_extraction_window() -> None:
    """`T-X1` (window): the episode plus `episode_context_turns` turns before it; a turn keeps
    the events of its own append; nothing after the episode."""
    log = _Log()
    for n in range(1, 6):
        _turn(log, n)
    from_seq = len(log.events) + 1
    for n in range(6, 8):
        _turn(log, n)
    to_seq = len(log.events)
    _turn(log, 8)  # after the episode

    window = extraction_window(log.events, from_seq, to_seq, SETTINGS)
    assert [e.seq for e in window.episode] == list(range(from_seq, to_seq + 1))
    texts = [e.payload.get("text") for e in window.before if e.type == "message.sent"]
    assert texts == ["question 3", "question 4", "question 5"]
    # turn 2 is outside: its `hint.shown` (written after its commit) stays out too
    assert min(e.seq for e in window.before) == 7
    assert len(window.before) == 9

    two = extraction_window(log.events, from_seq, to_seq, Settings(episode_context_turns=2))
    assert [e.payload.get("text") for e in two.before if e.type == "message.sent"] == [
        "question 4",
        "question 5",
    ]
    none = extraction_window(log.events, from_seq, to_seq, Settings(episode_context_turns=0))
    assert none.before == []
    # zero context turns and no turn before the episode: still nothing before it
    assert extraction_window(log.events, 2, 3, Settings(episode_context_turns=0)).before == []
    # fewer turns than the setting: all of them
    first = extraction_window(log.events, 4, 6, SETTINGS)
    assert [e.seq for e in first.before] == [1, 2, 3]


def _episode() -> tuple[_Log, int]:
    log = _Log()
    _turn(log, 1)
    start = len(log.events) + 1
    action = log.add("message.sent", MessageSent(text="give me a task"))
    log.add("turn.committed", _committed(action, "Try this one", task=True), causation_seq=action)
    answer = log.add(
        "answer.submitted",
        AnswerSubmitted(
            task_id=TASK,
            answer=LearnerAnswer(kind="number", value="5"),
            text="I used the root test",
            source="chat",
        ),
    )
    log.add(
        "verdict.recorded",
        Verdict(
            answer_seq=answer, part="main", outcome="incorrect", method="exact", verification="code"
        ),
    )
    log.add(
        "canvas.line_committed",
        CanvasLineCommitted(
            canvas_id=uuid4(),
            line_id="l1",
            text="sqrt[n]{n!} -> ?",
            kind="text",
            bbox=(0.0, 0.0, 1.0, 1.0),
            stroke_ids=[],
            started_at_ms=0,
            ended_at_ms=1,
            source="pen",
            recognition_confidence=0.9,
            crossed_out=True,
        ),
    )
    log.add(
        "voice.transcribed", VoiceTranscribed(text="hmm the root", started_at_ms=0, ended_at_ms=1)
    )
    log.add("item.flagged_unclear", ItemFlaggedUnclear(turn_seq=2, block_id="b1", text="why n!"))
    return log, start


def test_extractor_input() -> None:
    """Т-§7.6: the events of the window as numbered lines (replies, markup, verdicts, canvas,
    voice, "unclear"); candidates and their known misconceptions with labels; the gap types of
    П-6.4; no ids; `extractor@3` renders with exactly these variables."""
    log, start = _episode()
    window = extraction_window(log.events, start, len(log.events), SETTINGS)
    candidates = [
        _candidate(SKILL, "D'Alembert test"),
        _candidate(OTHER_SKILL, "Root test"),
    ]
    known = [
        _known(MISCONCEPTION, SKILL, "uses the root test for factorials"),
        _known(uuid4(), uuid4(), "a misconception of a skill that is no candidate"),
    ]
    labels = window_labels(window, candidates, known, SETTINGS)
    variables = extractor_variables(window, candidates, known, labels)
    # only the misconceptions of the candidates get labels
    assert list(labels.labels("misconception").values()) == [MISCONCEPTION]

    assert variables["events_before"].splitlines() == [
        "[1] learner: question 1",
        "[2] teacher: reply 1",
        "[3] hint, step 1 of 3, on K1",
    ]
    s = start
    assert variables["episode"].splitlines() == [
        f"[{s}] learner: give me a task",
        f"[{s + 1}] teacher: Try this one",
        f"[{s + 1}] problem given K1 (tests: S1 level 3): Find the limit of a_n",
        f"[{s + 1}] teacher showed for S1: example",
        f"[{s + 2}] learner's answer to K1: 5",
        f"[{s + 2}] learner's reasoning: I used the root test",
        f"[{s + 3}] check by the checking code of the answer [{s + 2}] to K1: incorrect",
        f"[{s + 4}] learner wrote on the canvas (crossed out): sqrt[n]{{n!}} -> ?",
        f"[{s + 5}] learner said: hmm the root",
        f'[{s + 6}] learner marked as unclear: "why n!"',
    ]
    assert variables["candidate_skills"] == "S1: D'Alembert test\nS2: Root test"
    assert variables["known_misconceptions"] == "M1 (skill S1): uses the root test for factorials"
    assert variables["gap_types"] == GAP_TYPE_TABLE
    for code in ("no_idea", "when_to_apply", "confusion", "execution", "slip", "misconception"):
        assert f"{code} — " in GAP_TYPE_TABLE
    rendered = PromptRegistry().load("extractor", 3).render(variables)
    for object_id in (SKILL, OTHER_SKILL, MISCONCEPTION, TASK, LEARNER, SESSION):
        assert str(object_id) not in rendered

    empty = extractor_variables(extraction_window([], 1, 1, SETTINGS), [], [], labels)
    assert empty["events_before"] == empty["episode"] == empty["candidate_skills"] == NONE_LINE


def _element(**kw: Any) -> ExtractedElement:
    base: dict[str, Any] = {
        "kind": "evidence",
        "skill": "S1",
        "new_skill": None,
        "skill_unclear": False,
        "level": 3,
        "outcome": "incorrect",
        "help": "none",
        "hint_step": None,
        "gap_type": "confusion",
        "misconception": None,
        "new_misconception": None,
        "root_cause": None,
        "quote": "root test",
        "events": [5],
    }
    base.update(kw)
    return ExtractedElement.model_validate(base)


@dataclass
class _Gateway:
    answer: ExtractorOutputV3
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append(
            {
                "role": role,
                "version": prompt_version,
                "model": response_model,
                "variables": variables,
                "key": idempotency_key,
                "check": kwargs.get("check"),
            }
        )
        return LLMResult(
            value=self.answer, from_cache=False, prompt_hash="h", model="m", usage=None
        )


async def test_call_extractor() -> None:
    """WP15 п.3: `extractor@3`, strict `ExtractorOutputV3`, the key of the observation; an
    unknown label goes back to the model through `check` (Т-§4.5)."""
    log, start = _episode()
    window = extraction_window(log.events, start, len(log.events), SETTINGS)
    candidates = [_candidate(SKILL, "D'Alembert test")]
    known = [_known(MISCONCEPTION, SKILL, "uses the root test for factorials")]
    labels = window_labels(window, candidates, known, SETTINGS)
    variables = extractor_variables(window, candidates, known, labels)
    answer = ExtractorOutputV3(elements=[_element()])
    gateway = _Gateway(answer)
    out = await call_extractor(
        cast(LLMGateway, gateway),
        SETTINGS,
        learner_id=LEARNER,
        session_id=SESSION,
        episode_seq=40,
        trigger_seq=40,
        variables=variables,
        labels=labels,
    )
    assert out == answer
    (call,) = gateway.calls
    assert call["role"] == "extractor" and call["version"] == 3
    assert call["model"] is ExtractorOutputV3
    assert call["key"] == extraction_key(40, "extractor@3", 40) == "obs:40:extractor@3:40"
    assert call["variables"] == variables
    check = call["check"]
    assert check(answer) is None
    assert label_errors(ExtractorOutputV3(elements=[_element(skill="S9")]), labels) == (
        "unknown labels: S9; use only the given labels"
    )
    wrong = ExtractorOutputV3(
        elements=[_element(misconception="M4", root_cause={"skill": "S7", "confidence": "low"})]
    )
    assert check(wrong) == "unknown labels: M4, S7; use only the given labels"
    assert check(ExtractorOutputV3(elements=[_element(skill=None, skill_unclear=True)])) is None


def _cited(events: list[int], quote: str = "", **kw: Any) -> ExtractedElement:
    base: dict[str, Any] = {
        "kind": "evidence",
        "skill": "S1",
        "new_skill": None,
        "skill_unclear": False,
        "level": 2,
        "outcome": "correct",
        "help": "none",
        "hint_step": None,
        "gap_type": "none",
        "misconception": None,
        "new_misconception": None,
        "root_cause": None,
        "quote": quote,
        "events": events,
    }
    base.update(kw)
    return ExtractedElement.model_validate(base)


def test_extracted_elements_checked_on_the_episode() -> None:
    """`T-X1` (check): elements citing events outside the episode are rejected; a quote must be
    the words of its event; the level is capped by the task markup; a code verdict of the
    episode bars `evidence` on its pair «skill + level» (WP15 п.4, п.8)."""
    log, start = _episode()
    window = extraction_window(log.events, start, len(log.events), SETTINGS)
    candidates = [_candidate(SKILL, "D'Alembert test"), _candidate(OTHER_SKILL, "Root")]
    labels = window_labels(window, candidates, [], SETTINGS)
    ctx = episode_check_context(window, labels, {TASK: [(SKILL, 3), (OTHER_SKILL, 2)]})
    answer, verdict, canvas, voice = start + 2, start + 3, start + 4, start + 5
    assert ctx.episode[canvas].text == "sqrt[n]{n!} -> ?"
    assert ctx.episode[answer].text == "5\nI used the root test"
    assert ctx.episode[verdict].text == ""  # citable, not quotable
    assert ctx.task_levels == {answer: 3, verdict: 3}  # the turn offering it is no task event
    assert ctx.code_verdicts == {(SKILL, 3), (OTHER_SKILL, 2)}

    checked = check_extracted(
        [
            _cited([1], "question 1"),  # a context turn before the episode
            _cited([canvas], "n!", outcome="incorrect", gap_type="when_to_apply"),
            _cited([canvas], "learner wrote on the canvas", outcome="incorrect", gap_type="slip"),
            _cited([answer], "root test", level=5, outcome="incorrect", gap_type="confusion"),
            _cited([answer, verdict], "", level=3),  # the code verdict already judged it
            _cited([voice], "hmm the root", skill="S2", level=1),
        ],
        ctx,
    )
    assert [(r.index, r.reason) for r in checked.rejected] == [
        (0, "source_outside_episode"),
        (2, "quote_not_in_source"),
        (3, "level_above_task"),
        (4, "code_verdict_exists"),
    ]
    assert [(e.skill_id, e.events) for e in checked.elements] == [
        (SKILL, [canvas]),
        (OTHER_SKILL, [voice]),
    ]


async def test_record_extraction(pg_db: Db) -> None:
    """WP15 п.5, WP7.3: `observation.extracted` with key `obs:{episode_seq}:{extractor@3}:
    {trigger_seq}` and its coverage row in one transaction; a retry is the same event and row;
    a re-extraction (new trigger) is a new event and the row points at it."""
    learner_id = await create_learner(pg_db)
    log, start = _episode()
    window = extraction_window(log.events, start, len(log.events), SETTINGS)
    labels = window_labels(window, [_candidate(SKILL, "D'Alembert test")], [], SETTINGS)
    ctx = episode_check_context(window, labels, {})
    canvas = start + 4
    checked = check_extracted([_cited([canvas], "n!"), _cited([1], "question 1")], ctx)

    async def write(trigger_seq: int, elements: CheckedElements = checked) -> StoredEvent:
        return await record_extraction(
            pg_db,
            learner_id,
            prompt="extractor@3",
            episode_seq=40,
            trigger_seq=trigger_seq,
            checked=elements,
        )

    first = await write(41)
    again = await write(41)
    assert again.seq == first.seq
    assert first.idempotency_key == "obs:40:extractor@3:41"
    assert first.actor == "extractor@3"
    assert first.causation_seq == 41
    assert first.payload["episode_seq"] == 40
    assert first.payload["trigger_seq"] == 41
    assert [e["events"] for e in first.payload["elements"]] == [[canvas]]
    assert [r["reason"] for r in first.payload["rejected"]] == ["source_outside_episode"]

    async def coverage() -> list[tuple[str, int | None]]:
        rows = await pg_db.fetch_all(
            "SELECT status, observation_seq FROM ops.coverage WHERE learner_id = %(l)s",
            {"l": learner_id},
        )
        return [(r["status"], r["observation_seq"]) for r in rows]

    assert await coverage() == [("extracted", first.seq)]
    # an empty output is still an observation: the episode was read
    redo = await write(55, CheckedElements([], []))
    assert redo.seq != first.seq and redo.payload["elements"] == []
    assert await coverage() == [("extracted", redo.seq)]


async def test_record_extraction_commits_nothing_on_failure(
    pg_db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Law 7: the coverage row fails → the observation is not written either."""
    learner_id = await create_learner(pg_db)

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("coverage write failed")

    monkeypatch.setattr(extract_episode, "mark_covered", broken)
    with pytest.raises(RuntimeError):
        await record_extraction(
            pg_db,
            learner_id,
            prompt="extractor@3",
            episode_seq=40,
            trigger_seq=41,
            checked=CheckedElements([], []),
        )
    rows = await pg_db.fetch_all(
        "SELECT seq FROM core.events WHERE learner_id = %(l)s AND type = 'observation.extracted'",
        {"l": learner_id},
    )
    assert rows == []


class _SpyReader(FixedGraphReader):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[dict[str, Any]] = []

    async def extraction_candidates(
        self,
        learner_id: UUID,
        session_id: UUID,
        episode_vector: Sequence[float] | None,
        k: int,
        *,
        topic_id: UUID | None = None,
        skill_ids: Sequence[UUID] | None = None,
    ) -> ExtractionCandidates:
        self.asked.append(
            {"vector": episode_vector, "k": k, "topic_id": topic_id, "skill_ids": skill_ids}
        )
        return await super().extraction_candidates(
            learner_id, session_id, episode_vector, k, topic_id=topic_id, skill_ids=skill_ids
        )


async def _closed_episode(db: Db, learner_id: UUID) -> dict[str, int]:
    """A message before the episode; in it a task testing `SKILL_FAILED` at level 2, a wrong
    answer, a reply; then `episode.closed` of the three."""

    def new(type_: str, payload: EventPayload, key: str) -> NewEvent:
        return NewEvent(
            type=type_, payload=payload, actor="learner", idempotency_key=key, session_id=SESSION
        )

    task_markup = TaskMarkupRecord(
        tests=[TestedLevelRecord(skill_id=SKILL_FAILED, level=2)],
        method_named=False,
        answer=AnswerSpec(type="number", value=MathValue(latex="5/6", sympy="5/6")),
    )
    created = TaskCreated(
        task_id=TASK,
        family_id=uuid4(),
        source="generated",
        markup=task_markup,
        verification="checked",
    )
    answer = AnswerSubmitted(
        task_id=TASK,
        answer=LearnerAnswer(kind="number", value="2/5"),
        text="I added the denominators: 1/2 + 1/3 = 2/5",
        source="chat",
    )
    stored = await append_events(
        db,
        learner_id,
        [
            new("message.sent", MessageSent(text="hello"), "m0"),
            new("task.created", created, "t1"),
            new("answer.submitted", answer, "a1"),
            new("message.sent", MessageSent(text="so the bottoms add up"), "m1"),
        ],
    )
    first, last = stored[1].seq, stored[-1].seq
    (closed,) = await append_events(
        db,
        learner_id,
        [
            new(
                "episode.closed",
                EpisodeClosed(from_seq=first, to_seq=last, has_content=True),
                f"episode:{SESSION}:{last}",
            )
        ],
    )
    return {"before": stored[0].seq, "answer": stored[2].seq, "reply": last, "closed": closed.seq}


async def test_extract_episode_job(pg_db: Db) -> None:
    """WP15 п.1–6 on the reader of S4: the window of `episode.closed`, the candidates of the
    reader with the episode vector, one call, the check by code against the stored markup, the
    observation with coverage. `episode.reassigned` restricts to the topic, `skill.split` to the
    parts (D-A-135); each trigger is its own observation. Modal down — no vector."""
    learner_id = await create_learner(pg_db)
    seqs = await _closed_episode(pg_db, learner_id)
    answer, closed = seqs["answer"], seqs["closed"]
    output = ExtractorOutputV3(
        elements=[
            # the task tests level 2: level 4 from its answer is above the markup
            _cited([answer], "added the denominators", skill="S2", level=4),
            _cited(
                [answer],
                "I added the denominators",
                skill="S2",
                outcome="incorrect",
                gap_type="misconception",
            ),
            _cited([seqs["before"]], "hello"),  # the context turn is not the episode
        ]
    )
    gateway = _Gateway(output)
    reader = _SpyReader()
    embeddings = FakeEmbeddingClient(SETTINGS.embedding_dim)

    async def run(payload: dict[str, Any], *, modal: bool = True) -> StoredEvent:
        embeddings.available = modal
        return await extract_episode.extract_episode(
            pg_db,
            cast(LLMGateway, gateway),
            SETTINGS,
            reader,
            embeddings,
            learner_id=learner_id,
            trigger=ExtractionTrigger.from_payload(payload),
        )

    first = await run({"trigger_seq": closed})
    assert first.idempotency_key == f"obs:{closed}:extractor@3:{closed}"
    assert first.session_id == SESSION
    assert [e["skill_id"] for e in first.payload["elements"]] == [str(SKILL_FAILED)]
    assert [r["reason"] for r in first.payload["rejected"]] == [
        "level_above_task",
        "source_outside_episode",
    ]
    asked = reader.asked[-1]
    assert asked["k"] == SETTINGS.candidate_skills_k
    assert asked["topic_id"] is None and asked["skill_ids"] is None
    assert asked["vector"] == embeddings.vector(
        "2/5\nI added the denominators: 1/2 + 1/3 = 2/5\nso the bottoms add up"
    )
    variables = gateway.calls[-1]["variables"]
    assert variables["candidate_skills"].count("\n") == 2  # all three skills of the reader
    assert f"[{seqs['before']}] learner: hello" in variables["events_before"]

    reassigned = await run(
        {"trigger_seq": closed + 10, "episode_seq": closed, "topic_id": str(TOPIC_OPEN)}
    )
    assert reader.asked[-1]["topic_id"] == TOPIC_OPEN
    assert "add fractions" not in gateway.calls[-1]["variables"]["candidate_skills"]
    assert reassigned.payload["trigger_seq"] == closed + 10 and reassigned.seq != first.seq

    split = await run(
        {
            "trigger_seq": closed + 20,
            "episode_seq": closed,
            "restrict_skill_ids": [str(SKILL_QUIET)],
        },
        modal=False,
    )
    assert reader.asked[-1]["skill_ids"] == [SKILL_QUIET]
    assert reader.asked[-1]["vector"] is None
    # the only candidate is S1 now: the elements citing S2 are unknown labels
    assert [r["reason"] for r in split.payload["rejected"]][:2] == ["unknown_label"] * 2
    rows = await pg_db.fetch_all(
        "SELECT episode_seq, observation_seq FROM ops.coverage WHERE learner_id = %(l)s",
        {"l": learner_id},
    )
    assert [(r["episode_seq"], r["observation_seq"]) for r in rows] == [(closed, split.seq)]
    # a retry of a trigger is the same observation (law 5)
    assert (await run({"trigger_seq": closed})).seq == first.seq


async def test_extract_episode_needs_an_episode(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    seqs = await _closed_episode(pg_db, learner_id)
    with pytest.raises(ValueError):
        await extract_episode.extract_episode(
            pg_db,
            cast(LLMGateway, _Gateway(ExtractorOutputV3(elements=[]))),
            SETTINGS,
            FixedGraphReader(),
            FakeEmbeddingClient(SETTINGS.embedding_dim),
            learner_id=learner_id,
            trigger=ExtractionTrigger(episode_seq=seqs["answer"], trigger_seq=seqs["answer"]),
        )


def test_task_tests_from_the_stored_markup() -> None:
    """The level cap comes from `task.created` with its parts; a `corrected_markup` of the
    check replaces it (WP12.2); a new skill has no id and gives no pair."""
    other = uuid4()

    def markup(level: int, *, part_skill: UUID | None = None) -> TaskMarkupRecord:
        spec = AnswerSpec(type="number", value=MathValue(latex="1", sympy="1"))
        parts = (
            [
                TaskPartRecord(
                    part="a",
                    answer=spec,
                    tests=[TestedLevelRecord(skill_id=part_skill, level=5)],
                )
            ]
            if part_skill
            else None
        )
        return TaskMarkupRecord(
            tests=[
                TestedLevelRecord(skill_id=SKILL, level=level),
                TestedLevelRecord(new_skill=NewSkill(name="new", topic_hint="t"), level=1),
            ],
            method_named=False,
            answer=spec,
            parts=parts,
        )

    log = _Log()
    created = TaskCreated(
        task_id=TASK,
        family_id=uuid4(),
        source="generated",
        markup=markup(2, part_skill=other),
        verification="unchecked",
    )
    log.add("task.created", created)
    assert extract_episode.task_tests(log.events) == {TASK: [(SKILL, 2), (other, 5)]}
    log.add("task.verified", TaskVerified(task_id=TASK, corrected_markup=markup(4)))
    log.add("task.verified", TaskVerified(task_id=TASK))
    assert extract_episode.task_tests(log.events) == {TASK: [(SKILL, 4)]}
