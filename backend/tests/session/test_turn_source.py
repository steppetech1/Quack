"""The context source of the teacher turn (ТЗ Ф4 WP14, WP13.2 «assemble»; Т-§10, Т-§12.9):
the journal gives goal, tasks, the answered action and effort; memory (S4) gives slots 3, 4, 7;
the first turn freezes slots 3–4 into its manifest row. D-B-181."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

import pytest

from quack.artifacts.cards import write_system_cards
from quack.artifacts.documents import Card
from quack.compute_client.math_contracts import MathCheckResponse
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import (
    AgentCompleted,
    AgentRequested,
    AnswerSubmitted,
    DigestWritten,
    MessageSent,
    TaskCreated,
)
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.records import (
    AgentParamsRecord,
    DigestStructuredRecord,
    TaskMarkupRecord,
    TaskPartRecord,
    TestedLevelRecord,
)
from quack.events.values import AnswerOption, AnswerSpec, LearnerAnswer, MathValue, Verdict
from quack.graph.memory import InMemoryGraph
from quack.llm.gateway import LLMGateway
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task
from quack.session.action_turn import TurnRequest
from quack.session.actions import Action
from quack.session.turn_source import (
    MemoryTurnContextSource,
    answer_strings,
    in_context_sample,
    read_journal,
    target_levels,
    turn_effort,
)
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider

SETTINGS = Settings()
SESSION = uuid4()
NOW_MS = fx.NOW_MS


def _event(seq: int, type_: str, payload: Any) -> StoredEvent:
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    return StoredEvent.model_validate(
        {
            "id": seq,
            "seq": seq,
            "learner_id": fx.LEARNER,
            "domain_id": None,
            "causation_seq": None,
            "correlation_id": None,
            "type": type_,
            "payload": data,
            "actor": "learner",
            "idempotency_key": f"k:{seq}",
            "schema_version": 1,
            "payload_hash": "h",
            "session_id": SESSION,
            "occurred_at": "2026-11-03T10:00:00Z",
            "recorded_at": "2026-11-03T10:00:00Z",
            "source_refs": [],
        }
    )


def _markup(*skills: UUID) -> TaskMarkupRecord:
    return TaskMarkupRecord(
        tests=[TestedLevelRecord(skill_id=s, level=2) for s in skills],
        method_named=False,
        answer=AnswerSpec(type="number", value=MathValue(latex="5/6", sympy="5/6")),
    )


def _task(seq: int, task_id: UUID, *skills: UUID) -> StoredEvent:
    return _event(
        seq,
        "task.created",
        TaskCreated(
            task_id=task_id,
            family_id=uuid4(),
            source="generated",
            markup=_markup(*skills),
            verification="checked",
        ),
    )


def _answer(seq: int, task_id: UUID) -> StoredEvent:
    return _event(
        seq,
        "answer.submitted",
        {"task_id": str(task_id), "answer": {"kind": "number", "value": "1"}, "source": "chat"},
    )


def _verdict(seq: int, answer_seq: int, outcome: str, part: str = "a") -> StoredEvent:
    return _event(
        seq,
        "verdict.recorded",
        {
            "answer_seq": answer_seq,
            "part": part,
            "outcome": outcome,
            "method": "exact",
            "verification": "code",
        },
    )


def test_journal_goal_tasks_and_answer() -> None:
    """Slot 6 — the last intent; active tasks — created and not closed; an answer with every
    part correct is `solved`, its own verdict goes to the guard."""
    shared, other = uuid4(), uuid4()
    open_task, done_task = uuid4(), uuid4()
    events = [
        _event(1, "message.sent", MessageSent(text="hi", intent="review", topic_id=fx.TOPIC_OPEN)),
        _event(2, "message.sent", MessageSent(text="go on")),
        _task(3, done_task, other),
        _event(4, "task.closed", {"task_id": str(done_task), "how": "skipped"}),
        _task(5, open_task, shared),
        _answer(6, open_task),
        _verdict(7, 6, "correct"),
    ]
    started = _event(
        0,
        "session.started",
        {
            "session_id": str(uuid4()),
            "domain_id": str(fx.DOMAIN),
            "intent": "exam",
            "hub_id": str(fx.HUB),
        },
    )
    assert read_journal([started], 6).goal.hub_id == fx.HUB
    journal = read_journal([started, *events], 6)
    assert (journal.goal.intent, journal.goal.topic_id) == ("review", fx.TOPIC_OPEN)
    assert journal.open_tasks == [open_task] and journal.task_skills == [shared]
    assert [t.text for t in journal.learner_texts] == ["hi", "go on"]
    assert journal.first_turn
    assert journal.answered is not None and journal.answered.solved
    assert journal.answered_verdict is not None and journal.answered_verdict.answer_seq == 6

    hint = [*events, _event(8, "hint.requested", {"task_id": str(open_task)})]
    assert read_journal(hint, 8).answered == read_journal(hint, 8).answered
    answered = read_journal(hint, 8).answered
    assert answered is not None and answered.kind == "hint_request"


def test_effort_after_two_failures_on_one_skill() -> None:
    """Т-§12.9: `high` on the first turn and after two failed answers sharing a skill; a later
    turn otherwise and `teacher_fallback` — `low`."""
    skill = uuid4()
    a, b = uuid4(), uuid4()
    base = [
        _task(1, a, skill),
        _task(2, b, skill, uuid4()),
        _turn(3, [], []),
        _answer(4, a),
        _verdict(5, 4, "incorrect"),
        _answer(6, b),
    ]
    one_failure = read_journal([*base, _verdict(7, 6, "correct")], 6)
    assert not one_failure.first_turn and turn_effort("teacher", one_failure) == "low"
    two = read_journal([*base, _verdict(7, 6, "incorrect")], 6)
    assert two.repeated_failure and turn_effort("teacher", two) == "high"
    assert turn_effort("teacher_fallback", two) == "low"


def test_answer_strings_of_the_spec() -> None:
    spec = AnswerSpec(
        type="vector",
        items=[MathValue(latex="\\frac{1}{2}", sympy="1/2"), MathValue(latex="3", sympy="3")],
    )
    assert answer_strings(spec) == ["\\frac{1}{2}", "1/2", "3", "3"]


async def _message(db: Db, learner_id: UUID, session_id: UUID, **kw: Any) -> int:
    (stored,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="message.sent",
                payload=MessageSent(**kw),
                actor="learner",
                idempotency_key=f"msg:{uuid4()}",
                session_id=session_id,
                domain_id=fx.DOMAIN,
            )
        ],
    )
    return stored.seq


def _request(learner_id: UUID, session_id: UUID, seq: int) -> TurnRequest:
    return TurnRequest(
        learner_id=learner_id,
        session_id=session_id,
        domain_id=fx.DOMAIN,
        action_seq=seq,
        action=cast(Action, None),
        role="teacher",
        manifest_id=uuid4(),
    )


async def test_assemble_first_turn_freezes_slots(pg_db: Db) -> None:
    """The first turn: slots 3–4 from memory go into the manifest row; the request carries
    every slot, the tools and effort `high`; the active task's answer is a spoiler."""
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    await pg_db.execute(
        'UPDATE core.learners SET settings = settings || \'{"timezone": "Asia/Almaty"}\' '
        "WHERE learner_id = %(l)s",
        {"l": learner_id},
    )
    task_id = uuid4()
    await create_task(
        pg_db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="generated",
        condition=TaskCondition(text="Add 1/2 and 1/3", latex=None),
        markup=_markup(fx.SKILL_FAILED),
        verification="checked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
        session_id=session_id,
        domain_id=fx.DOMAIN,
    )
    viz, pending = uuid4(), uuid4()
    digest = DigestWritten(
        domain_id=fx.DOMAIN,
        text="the denominators are the gap",
        as_of_seq=1,
        structured=DigestStructuredRecord(
            frontier=[], gap_hypotheses=[], uncertainty=None, moves=[], forecast=[]
        ),
        plan_divergence=False,
        manner="short steps",
    )
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="digest.written",
                payload=digest,
                actor="analyst@1",
                idempotency_key=f"digest:{uuid4()}",
                domain_id=fx.DOMAIN,
            ),
            *(
                NewEvent(
                    type="agent.requested",
                    payload=AgentRequested(
                        request_id=r,
                        kind="viz",
                        params=AgentParamsRecord(description="a slider"),
                        needed_in_session=True,
                    ),
                    actor="teacher@2",
                    idempotency_key=f"req:{r}",
                    session_id=session_id,
                )
                for r in (viz, pending)
            ),
            NewEvent(
                type="agent.completed",
                payload=AgentCompleted(request_id=viz, kind="viz", ok=True, result_ref="x"),
                actor="agent:viz",
                idempotency_key=f"agent.completed:{viz}",
            ),
        ],
    )
    seq = await _message(
        pg_db, learner_id, session_id, text="teach me", intent="learn", topic_id=fx.TOPIC_OPEN
    )
    gateway = LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db)
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=gateway,
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        clock=lambda: NOW_MS,
    )

    setup = await source.assemble(_request(learner_id, session_id, seq))

    prompt = setup.assembled.request.base.prompt
    assert "Preference language: ru" in prompt and "what helps: small steps" in prompt
    assert 'Hub "fractions exam"' in prompt  # slot 4 without labels (D-B-180)
    assert "Current action" in prompt and "teach me" in prompt
    assert setup.assembled.effort == "high" and setup.manifest.effort == "high"
    assert setup.assembled.request.tools  # the memory tools
    assert setup.learner_timezone == "Asia/Almaty"
    assert setup.manifest.frozen_slots is not None
    assert setup.manifest.frozen_slots["profile"].startswith("Preference answer_length: short")
    assert setup.manifest.renderer_version == "renderer@1"
    assert setup.manifest.prompt_version == "teacher@5"
    assert {
        "journal_ms",
        "reads_ms",
        "memory_ms",
        "review_prep_ms",
        "slice_ms",
        "chunks_ms",
        "digest_ms",
        "completed_ms",
        "inline_verdict_ms",
        "cards_ms",
        "parallel_ms",
    } <= set(setup.timings)
    assert all(isinstance(v, int) and v >= 0 for v in setup.timings.values())
    assert setup.labels.label("task", task_id) == "K1"
    assert setup.labels.label("skill", fx.SKILL_FAILED) is not None
    assert "Analyst summary: the denominators are the gap" in prompt
    viz_label = setup.labels.label("request", viz)
    assert f"Ready from agents: {viz_label}: viz is ready" in prompt
    assert setup.labels.label("request", pending) is not None
    assert prompt.count("is ready") == 1  # the pending request is not ready
    assert setup.assembled.guard.has_spoiler("the answer is 5/6.")
    assert setup.assembled.guard.remove_spoiler("Try it. It is 5/6.") == "Try it."

    await pg_db.execute(
        "INSERT INTO ops.turn_manifests (manifest_id, learner_id, session_id, prompt_version, "
        "role, model, projected_seq, frozen_slots) VALUES (%(m)s, %(l)s, %(s)s, 'teacher@2', "
        "'teacher', 'm', 0, %(f)s)",
        {
            "m": setup.manifest.manifest_id,
            "l": learner_id,
            "s": session_id,
            "f": json.dumps({"profile": "Preference language: en", "domain_and_goals": ""}),
        },
    )
    later = await source.assemble(_request(learner_id, session_id, seq))
    assert later.manifest.frozen_slots is None
    variables_prompt = later.assembled.request.base.prompt
    assert "Preference language: en" in variables_prompt  # the snapshot, not memory now
    assert "Changed during this session: Preference language: ru" in variables_prompt


def test_verdict_payload_is_the_contract() -> None:
    """The test events above are valid `verdict.recorded` and `answer.submitted` payloads."""
    Verdict.model_validate(_verdict(1, 1, "correct").payload)
    AnswerSubmitted.model_validate(_answer(1, uuid4()).payload)


def _turn(seq: int, acknowledged: list[int], requests: list[dict[str, Any]]) -> StoredEvent:
    return _event(
        seq,
        "turn.committed",
        {"markup": {"corrections_acknowledged": acknowledged, "agent_requests": requests}},
    )


def test_journal_revisions_and_requests() -> None:
    """WP14.2: a revised check stays until a turn acknowledges it (Т-§5.6, Т-§6.3); the agent
    requests of the session come from the turns and from `agent.requested`."""
    task, viz, card = uuid4(), uuid4(), uuid4()
    events = [
        _task(1, task, uuid4()),
        _answer(2, task),
        _verdict(3, 2, "correct"),
        _answer(4, task),
        _verdict(5, 4, "incorrect"),
        _turn(6, [], [{"request_id": str(viz), "kind": "viz"}]),
        _verdict(7, 2, "incorrect"),  # re-check after a key dispute
        _verdict(8, 4, "incorrect"),
        _event(9, "agent.requested", {"request_id": str(card), "kind": "card"}),
    ]
    journal = read_journal(events, 9)
    assert [(r.answer_seq, r.text) for r in journal.revised] == [
        (2, "part a is now incorrect, was correct")
    ]
    assert journal.requests == {viz: "viz", card: "card"}
    assert read_journal([*events, _turn(10, [2], [])], 10).revised == []


def _line(seq: int, canvas: UUID, line_id: str, text: str) -> StoredEvent:
    return _event(
        seq,
        "canvas.line_committed",
        {
            "canvas_id": str(canvas),
            "line_id": line_id,
            "text": text,
            "kind": "text",
            "bbox": [0, 0, 1, 1],
            "stroke_ids": [],
            "started_at_ms": seq * 1000,
            "ended_at_ms": seq * 1000 + 500,
            "source": "pen",
            "recognition_confidence": 0.9,
            "crossed_out": False,
        },
    )


def test_journal_canvas_lines_by_slot_numbers() -> None:
    """WP14.4: the tools see the lines by the numbers of slot 5 (D-B-163): in the order of the
    session, an erased line keeps its number, a learner correction replaces the text."""
    canvas = uuid4()
    events = [
        _line(1, canvas, "a", "x = 1"),
        _event(2, "canvas.line_erased", {"canvas_id": str(canvas), "line_id": "a", "text": "x"}),
        _line(3, canvas, "b", "y = 2"),
        _event(
            4,
            "handwriting.corrected",
            {"canvas_id": str(canvas), "line_id": "b", "text": "y = 3"},
        ),
        _event(
            5, "voice.transcribed", {"text": "so y", "started_at_ms": 3100, "ended_at_ms": 3400}
        ),
    ]
    journal = read_journal(events, 5)
    assert {k: v.text for k, v in journal.canvas_lines.items()} == {"L1": "x = 1", "L2": "y = 3"}
    assert journal.canvas_lines["L2"].started_at_ms == 3000
    assert [v.text for v in journal.voice] == ["so y"]


async def test_target_levels_from_the_hubs() -> None:
    """Т-§8.5: the skills of a goal topic take the levels its hub requires."""
    reader = fx.FixedGraphReader()
    hubs = await reader.domain_goals(fx.DOMAIN)
    memory = await reader.topic_memory([fx.TOPIC_OPEN])
    found = target_levels(hubs, memory)
    assert found == {fx.SKILL_FAILED: [2, 3], fx.SKILL_QUIET: [2, 3]}


def test_context_sample_by_manifest_id() -> None:
    """WP13.9: about `turn_context_sample_rate` of turns keep the context; the same id decides
    the same way."""
    ids = [uuid5(SESSION, str(i)) for i in range(2000)]
    share = sum(in_context_sample(i, 0.02) for i in ids) / len(ids)
    assert 0.005 < share < 0.05
    assert not in_context_sample(UUID(int=0xFFFFFFFF << 96), 0.02)
    assert in_context_sample(UUID(int=0), 0.02)
    assert all(not in_context_sample(i, 0.0) for i in ids)


def test_route_builds_the_source_from_the_resources() -> None:
    """D-B-181: without a source set by the assembly the route builds one from the resources
    of the process, once; without resources — `503`."""
    from types import SimpleNamespace

    import pytest

    from quack.api.errors import ApiError
    from quack.session.router import get_turn_context_source

    resources = SimpleNamespace(
        db=None, llm=None, settings=SETTINGS, neo4j=None, redis=None, embeddings=None, compute=None
    )
    state = SimpleNamespace(resources=resources)
    request = cast(Any, SimpleNamespace(app=SimpleNamespace(state=state)))
    source = get_turn_context_source(request)
    assert isinstance(source, MemoryTurnContextSource)
    assert get_turn_context_source(request) is source
    with pytest.raises(ApiError):
        get_turn_context_source(
            cast(Any, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
        )


async def test_session_overlay_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """WP2.3 (D-B-215): the turn waits for the end of the journal, the overlay brings the session
    tail of a lagging projector; no driver or a failed event — an empty overlay."""
    from neo4j import AsyncDriver

    from quack.graph import overlay_build
    from quack.graph.overlay import SessionOverlay
    from quack.graph.projection.phase_a import EventFailed
    from quack.session import turn_source

    request = _request(fx.LEARNER, SESSION, 5)
    built = SessionOverlay(applied_seq=3, overlay_seq=5, events=[], graph=InMemoryGraph())
    outcome: list[SessionOverlay | Exception] = [built]

    async def fake(*_args: Any, **_kwargs: Any) -> SessionOverlay:
        result = outcome[0]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(turn_source, "build_session_overlay", fake)

    def source(neo4j: object | None) -> MemoryTurnContextSource:
        return MemoryTurnContextSource(
            db=cast(Db, None),
            gateway=cast(LLMGateway, None),
            settings=SETTINGS,
            reader=lambda _learner: fx.FixedGraphReader(),
            neo4j=cast(AsyncDriver | None, neo4j),
        )

    lagging = await source(object())._overlay(request, 9)
    assert (lagging.applied_seq, lagging.overlay_seq) == (3, 9)
    outcome[0] = EventFailed(4, ValueError("bad"))
    failed = await source(object())._overlay(request, 9)
    assert (failed.applied_seq, failed.overlay_seq, failed.events) == (0, 9, [])
    assert overlay_build.empty_overlay(0).applied_seq == 0
    none = await source(None)._overlay(request, 9)
    assert (none.applied_seq, none.overlay_seq) == (0, 0)


async def test_turn_reads_the_verdict_of_its_own_action(pg_db: Db) -> None:
    """T-E1 (#279, D-B-221): the verdict is written right after the answer
    (`causation_seq = answer seq`); the turn reads it, so a correct answer is `solved`."""
    from quack.events.event_types import AnswerSubmitted
    from quack.events.values import LearnerAnswer, Verdict

    learner_id = await create_learner(pg_db)
    session_id, task_id = uuid4(), uuid4()
    await create_task(
        pg_db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="generated",
        condition=TaskCondition(text="Add 1/2 and 1/3", latex=None),
        markup=_markup(fx.SKILL_FAILED),
        verification="checked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
        session_id=session_id,
        domain_id=fx.DOMAIN,
    )
    [answer] = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="answer.submitted",
                payload=AnswerSubmitted(
                    task_id=task_id, answer=LearnerAnswer(kind="number", value="5/6"), source="chat"
                ),
                actor="learner",
                idempotency_key=f"answer:{uuid4()}",
                session_id=session_id,
                domain_id=fx.DOMAIN,
            )
        ],
    )
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="verdict.recorded",
                payload=Verdict(
                    answer_seq=answer.seq,
                    part="main",
                    outcome="correct",
                    method="exact",
                    verification="code",
                ),
                actor="code",
                idempotency_key=f"verdict:{answer.seq}:main",
                session_id=session_id,
                domain_id=fx.DOMAIN,
                causation_seq=answer.seq,
            )
        ],
    )
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        clock=lambda: NOW_MS,
    )
    setup = await source.assemble(_request(learner_id, session_id, answer.seq))
    assert setup.answered is not None and setup.answered.solved is True


async def test_neo4j_down_marks_the_turn_memory_unavailable(pg_db: Db) -> None:
    """T-E11 (#279, D-B-221): `ServiceUnavailable` of Neo4j does not escape the assembly —
    slots 3, 4, 7 are empty and the turn carries the mark."""
    from neo4j.exceptions import ServiceUnavailable

    class _Down(fx.FixedGraphReader):
        async def profile(self, learner_id: UUID) -> Any:
            raise ServiceUnavailable("neo4j is down")

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    seq = await _message(pg_db, learner_id, session_id, text="hello", intent=None, topic_id=None)
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: _Down(),
        clock=lambda: NOW_MS,
    )
    setup = await source.assemble(_request(learner_id, session_id, seq))
    assert setup.assembled.degraded_mode == "memory_unavailable"


async def test_material_search_is_wired_to_the_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-E7 (#279, D-B-221): the query is embedded and the graph search is bound to the learner
    and the domain of the turn; Modal down — the tool answers «search temporarily unavailable»."""
    from neo4j import AsyncDriver

    from quack.embeddings.client import EmbeddingsUnavailable
    from quack.graph.read.materials import ChunkHit
    from quack.session import turn_source
    from tests.support.embeddings import FakeEmbeddingClient

    seen: dict[str, Any] = {}

    async def fake_search(_driver: Any, **kwargs: Any) -> list[ChunkHit]:
        seen.update(kwargs)
        return [
            ChunkHit(
                chunk_id=uuid4(),
                material_id=None,
                page=3,
                text="Ratio test",
                teaches_focus=True,
                score=0.9,
            )
        ]

    monkeypatch.setattr(turn_source, "materials_search", fake_search)

    def source(embeddings: FakeEmbeddingClient | None) -> MemoryTurnContextSource:
        return MemoryTurnContextSource(
            db=cast(Db, None),
            gateway=cast(LLMGateway, None),
            settings=SETTINGS,
            reader=lambda _learner: fx.FixedGraphReader(),
            neo4j=cast(AsyncDriver, object()),
            embeddings=embeddings,
        )

    assert source(None)._search(fx.LEARNER, fx.DOMAIN) is None
    search = source(FakeEmbeddingClient(8))._search(fx.LEARNER, fx.DOMAIN)
    assert search is not None
    [chunk] = await search("ratio test", [fx.SKILL_FAILED], 5)
    assert chunk.page == 3 and chunk.text == "Ratio test"
    assert seen["learner_id"] == fx.LEARNER and seen["domain_id"] == fx.DOMAIN and seen["k"] == 5
    down = source(FakeEmbeddingClient(8, available=False))._search(fx.LEARNER, fx.DOMAIN)
    assert down is not None
    with pytest.raises(EmbeddingsUnavailable):
        await down("ratio test", [], 5)


# --- review of 2026-11-13: the guard's tasks, parts, element answers, the inline verdict ---


def test_spoiler_tasks_leave_out_the_solution_asked_and_the_answer_solved() -> None:
    """Т-§12.4 / WP13.5: «показана, не закрыта, `solution.requested` не было». The task of a
    solution request (closed only by this very turn) and the task a correct answer solves may
    be spoken about; the other active tasks stay guarded."""
    skill = uuid4()
    asked, answered, plain = uuid4(), uuid4(), uuid4()
    events = [
        _task(1, asked, skill),
        _task(2, answered, skill),
        _task(3, plain, skill),
        _event(4, "solution.requested", {"task_id": str(asked)}),
        _answer(5, answered),
        _verdict(6, 5, "correct", "main"),
    ]
    request = read_journal(events[:4], 4)
    assert request.open_tasks == [asked, answered, plain]
    assert request.spoiler_tasks == [answered, plain]
    solved = read_journal(events, 5)
    assert solved.spoiler_tasks == [plain]
    wrong = read_journal([*events[:5], _verdict(6, 5, "incorrect", "main")], 5)
    assert wrong.spoiler_tasks == [answered, plain] and wrong.answered is not None
    assert not wrong.answered.solved


def _parted(seq: int, task_id: UUID) -> StoredEvent:
    spec = AnswerSpec(type="number", value=MathValue(latex="1", sympy="1"))
    markup = _markup(uuid4()).model_copy(
        update={
            "parts": [
                TaskPartRecord(part="a", answer=spec),
                TaskPartRecord(part="b", answer=spec),
            ]
        }
    )
    return _event(
        seq,
        "task.created",
        TaskCreated(
            task_id=task_id,
            family_id=uuid4(),
            source="teacher",
            markup=markup,
            verification="unchecked",
        ),
    )


def test_a_task_with_parts_is_solved_only_when_every_part_is() -> None:
    """Т-§6.2: a correct part a is not a solved task (it used to close as `solved`)."""
    task = uuid4()
    only_a = [_parted(1, task), _answer(2, task), _verdict(3, 2, "correct", "a")]
    journal = read_journal(only_a, 2)
    assert journal.answered is not None and not journal.answered.solved
    assert task in journal.spoiler_tasks

    both = [*only_a, _answer(4, task), _verdict(5, 4, "correct", "b")]
    journal = read_journal(both, 4)
    assert journal.answered is not None and journal.answered.solved
    assert task not in journal.spoiler_tasks

    b_wrong = [*only_a, _answer(4, task), _verdict(5, 4, "incorrect", "b")]
    answered = read_journal(b_wrong, 4).answered
    assert answered is not None and not answered.solved

    fixed_a = [*b_wrong, _answer(6, task), _verdict(7, 6, "correct", "b")]
    answered = read_journal(fixed_a, 6).answered
    assert answered is not None and answered.solved  # the latest verdict of each part counts


def test_a_wrong_attempt_before_the_right_one_does_not_unsolve_the_task() -> None:
    task = uuid4()
    events = [
        _task(1, task, uuid4()),
        _answer(2, task),
        _verdict(3, 2, "incorrect", "main"),
        _answer(4, task),
        _verdict(5, 4, "correct", "main"),
    ]
    answered = read_journal(events, 4).answered
    assert answered is not None and answered.solved


def test_the_answer_of_a_task_element_belongs_to_the_response() -> None:
    """For `element_response` the action is `element.responded`; its `answer.submitted`
    (`causation_seq` = the response) and the verdict of that answer are the answered action."""
    task = uuid4()
    response = _event(
        3, "element.responded", {"turn_seq": 2, "element_id": "e1", "response": "5/6"}
    )
    answer = _answer(4, task).model_copy(update={"causation_seq": 3})
    events = [_task(1, task, uuid4()), response, answer, _verdict(5, 4, "correct", "main")]

    journal = read_journal(events, 3)

    assert journal.answered is not None and journal.answered.task_id == task
    assert journal.answered.solved and journal.answer_seq == 4
    assert journal.answered_verdict is not None and journal.answered_verdict.answer_seq == 4
    alone = read_journal([_task(1, task, uuid4()), response], 3)  # a response that is no answer
    assert alone.answered is None and alone.answer_seq is None


def test_the_correct_option_of_a_choice_is_guarded() -> None:
    spec = AnswerSpec(
        type="choice",
        options=[
            AnswerOption(option_id="A", text="increasing", correct=True),
            AnswerOption(option_id="B", text="decreasing", correct=False),
        ],
    )
    assert answer_strings(spec) == ["increasing"]


EXPRESSION = AnswerSpec(type="expression", value=MathValue(latex="2x", sympy="2*x"))


async def _fast_answer(db: Db, learner_id: UUID, session_id: UUID) -> int:
    """A task whose answer is an expression, and the learner's answer without a verdict."""
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="generated",
        condition=TaskCondition(text="Simplify x + x", latex=None),
        markup=_markup(fx.SKILL_FAILED).model_copy(update={"answer": EXPRESSION}),
        verification="checked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
        session_id=session_id,
        domain_id=fx.DOMAIN,
    )
    [answer] = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="answer.submitted",
                payload=AnswerSubmitted(
                    task_id=task_id,
                    answer=LearnerAnswer(kind="expression", value="x+x", sympy="x + x"),
                    source="chat",
                ),
                actor="learner",
                idempotency_key=f"answer:{uuid4()}",
                session_id=session_id,
                domain_id=fx.DOMAIN,
            )
        ],
    )
    return answer.seq


async def _assemble_with_compute(
    db: Db, learner_id: UUID, session_id: UUID, seq: int, *replies: MathCheckResponse | int
) -> tuple[Any, ComputeScenario]:
    scenario = ComputeScenario()
    scenario.push_for("/v1/math/check", *replies)
    compute = scripted_compute(scenario)
    source = MemoryTurnContextSource(
        db=db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=db),
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        compute=compute,
        clock=lambda: NOW_MS,
    )
    try:
        return await source.assemble(_request(learner_id, session_id, seq)), scenario
    finally:
        await compute.aclose()


async def _verdicts(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT idempotency_key, payload FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'verdict.recorded' ORDER BY seq",
        {"l": learner_id},
    )


async def test_the_turn_waits_for_the_fast_verdict_inline(pg_db: Db) -> None:
    """Т-§6.1, WP11.3, T-CH2: an `expression` answer goes to compute while the context is
    assembled; the verdict that came in time is written (one, by its key), reaches the turn
    (`verdict` frame, `stance code`, `solved`) and the guard lets the correct answer be named."""
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    seq = await _fast_answer(pg_db, learner_id, session_id)
    ok = MathCheckResponse(status="completed", outcome="correct", method="symbolic")

    setup, scenario = await _assemble_with_compute(pg_db, learner_id, session_id, seq, ok)

    frame = setup.assembled.verdict
    assert frame is not None and (frame.answer_seq, frame.part, frame.outcome) == (
        seq,
        "main",
        "correct",
    )
    assert setup.assembled.guard.answered_verdict is not None
    assert setup.answered is not None and setup.answered.solved
    assert not setup.assembled.guard.has_spoiler("so it is 2x, well done")  # the task is solved
    assert b'"timeout_ms":%d' % SETTINGS.symbolic_inline_timeout_ms in scenario.requests[0].content
    rows = await _verdicts(pg_db, learner_id)
    assert [r["idempotency_key"] for r in rows] == [f"verdict:{seq}:main"]


@pytest.mark.parametrize("reply", [504, 503])
async def test_a_verdict_that_is_late_is_left_to_the_insurance(pg_db: Db, reply: int) -> None:
    """Not in time, or compute is down: nothing is written, the turn goes on without a
    verdict; the answer stays guarded."""
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    seq = await _fast_answer(pg_db, learner_id, session_id)

    setup, _ = await _assemble_with_compute(pg_db, learner_id, session_id, seq, reply)

    assert setup.assembled.verdict is None and setup.assembled.guard.answered_verdict is None
    assert setup.answered is not None and not setup.answered.solved
    assert await _verdicts(pg_db, learner_id) == []
    assert setup.assembled.guard.has_spoiler("so it is 2x, well done")


async def test_an_answer_with_a_verdict_is_not_sent_to_compute(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    seq = await _fast_answer(pg_db, learner_id, session_id)
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="verdict.recorded",
                payload=Verdict(
                    answer_seq=seq,
                    part="main",
                    outcome="incorrect",
                    method="symbolic",
                    verification="code",
                ),
                actor="code",
                idempotency_key=f"verdict:{seq}:main",
                session_id=session_id,
                domain_id=fx.DOMAIN,
                causation_seq=seq,
            )
        ],
    )

    setup, scenario = await _assemble_with_compute(pg_db, learner_id, session_id, seq)

    assert scenario.requests == [] and setup.assembled.verdict is None
    assert setup.assembled.guard.answered_verdict is not None


async def test_the_manifest_keeps_the_events_of_the_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Т-§3.2: `overlay_seqs` of the manifest are the events the slice was read with."""
    from neo4j import AsyncDriver

    from quack.graph.overlay import SessionOverlay
    from quack.graph.read import SliceFocus
    from quack.session import turn_source

    request = _request(fx.LEARNER, SESSION, 5)
    tail = [_event(4, "message.sent", {"text": "a"}), _event(5, "message.sent", {"text": "b"})]
    built = SessionOverlay(applied_seq=3, overlay_seq=5, events=tail, graph=InMemoryGraph())

    async def fake_overlay(*_args: Any, **_kwargs: Any) -> SessionOverlay:
        return built

    async def fake_end(*_args: Any, **_kwargs: Any) -> int:
        return 5

    monkeypatch.setattr(turn_source, "build_session_overlay", fake_overlay)
    monkeypatch.setattr(turn_source, "journal_end", fake_end)
    source = MemoryTurnContextSource(
        db=cast(Db, None),
        gateway=cast(LLMGateway, None),
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        neo4j=cast(AsyncDriver, object()),
    )
    focus = SliceFocus(skill_ids=[], topic_ids=[], domain_id=None)

    _slice, unavailable, seqs = await source._slice(fx.FixedGraphReader(), request, focus)  # pyright: ignore[reportPrivateUsage]

    assert not unavailable and seqs == [4, 5]


# --- Q1: the answers of the parts are guarded too (Т-§12.4) ---


def _two_parts(seq: int, task_id: UUID, *, choice: bool = False) -> StoredEvent:
    part_b = (
        AnswerSpec(
            type="choice",
            options=[
                AnswerOption(option_id="A", text="converges absolutely", correct=True),
                AnswerOption(option_id="B", text="diverges", correct=False),
            ],
        )
        if choice
        else AnswerSpec(type="number", value=MathValue(latex="\\frac{7}{9}", sympy="7/9"))
    )
    markup = _markup(uuid4()).model_copy(
        update={
            "parts": [
                TaskPartRecord(
                    part="a",
                    answer=AnswerSpec(type="number", value=MathValue(latex="42", sympy="42")),
                ),
                TaskPartRecord(part="b", answer=part_b),
            ]
        }
    )
    return _event(
        seq,
        "task.created",
        TaskCreated(
            task_id=task_id,
            family_id=uuid4(),
            source="teacher",
            markup=markup,
            verification="unchecked",
        ),
    )


def _guarded(journal: Any) -> list[str]:
    return [s for _task, spec in journal.spoiler_parts for s in answer_strings(spec)]


def test_the_answers_of_the_parts_are_guarded_until_the_part_is_solved() -> None:
    """Q1: the answer of part b is a forbidden string; after a correct answer to part b it is
    not (part a stays guarded, the whole task is not solved); a wrong one changes nothing."""
    task = uuid4()
    opened = [_two_parts(1, task), _message_event(2)]
    assert _guarded(read_journal(opened, 2)) == ["42", "42", "\\frac{7}{9}", "7/9"]

    wrong_b = [*opened, _answer(3, task), _verdict(4, 3, "incorrect", "b")]
    assert "7/9" in _guarded(read_journal(wrong_b, 3))

    right_b = [*opened, _answer(3, task), _verdict(4, 3, "correct", "b")]
    journal = read_journal(right_b, 3)
    assert _guarded(journal) == ["42", "42"] and task in journal.spoiler_tasks

    both = [*right_b, _answer(5, task), _verdict(6, 5, "correct", "a")]
    assert read_journal(both, 5).spoiler_parts == []


def test_the_parts_of_a_task_with_a_solution_request_or_a_solved_whole_are_not_guarded() -> None:
    task = uuid4()
    asked = [_two_parts(1, task), _event(2, "solution.requested", {"task_id": str(task)})]
    assert read_journal(asked, 2).spoiler_parts == []
    whole = [_two_parts(1, task), _answer(2, task), _verdict(3, 2, "correct", "main")]
    assert read_journal(whole, 2).spoiler_parts == []  # the task is solved as a whole
    plain = [_task(1, task, uuid4())]
    assert read_journal(plain, 1).spoiler_parts == []  # no parts, nothing added


def test_the_correct_option_of_a_part_is_guarded() -> None:
    task = uuid4()
    journal = read_journal([_two_parts(1, task, choice=True), _message_event(2)], 2)
    assert "converges absolutely" in _guarded(journal)
    assert "diverges" not in _guarded(journal)


def test_short_text_variants_of_a_part_are_not_guarded() -> None:
    """The owner's decision: `accepted` wordings are short words of an ordinary explanation."""
    spec = AnswerSpec(type="short_text", accepted=["limit", "the limit"])
    assert answer_strings(spec) == []


def _message_event(seq: int) -> StoredEvent:
    return _event(seq, "message.sent", {"text": "go on"})


async def test_the_guard_of_the_turn_catches_the_answer_of_a_part(pg_db: Db) -> None:
    """Q1, through `assemble`: the text of the teacher with the answer of part b is a spoiler,
    and not any more once part b has a correct verdict."""
    learner_id = await create_learner(pg_db)
    session_id, task_id = uuid4(), uuid4()
    created = _two_parts(1, task_id)
    markup = TaskCreated.model_validate(created.payload).markup
    await create_task(
        pg_db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="teacher",
        condition=TaskCondition(text="Find a and b", latex=None),
        markup=markup,
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
        session_id=session_id,
        domain_id=fx.DOMAIN,
    )
    seq = await _message(pg_db, learner_id, session_id, text="go on")
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        clock=lambda: NOW_MS,
    )
    guard = (await source.assemble(_request(learner_id, session_id, seq))).assembled.guard
    assert guard.has_spoiler("So part b is 7/9.")
    assert not guard.has_spoiler("Part b is the one with the ratio.")

    (answered,) = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="answer.submitted",
                payload=AnswerSubmitted(
                    task_id=task_id,
                    answer=LearnerAnswer(kind="number", value="7/9"),
                    source="chat",
                    part="b",
                ),
                actor="learner",
                idempotency_key=f"answer:{uuid4()}",
                session_id=session_id,
                domain_id=fx.DOMAIN,
            ),
        ],
    )
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="verdict.recorded",
                payload=Verdict(
                    answer_seq=answered.seq,
                    part="b",
                    outcome="correct",
                    method="exact",
                    verification="code",
                ),
                actor="code",
                idempotency_key=f"verdict:{answered.seq}:b",
                session_id=session_id,
                domain_id=fx.DOMAIN,
                causation_seq=answered.seq,
            ),
        ],
    )
    later = (await source.assemble(_request(learner_id, session_id, answered.seq))).assembled.guard
    assert not later.has_spoiler("So part b is 7/9.")
    assert later.has_spoiler("And part a is 42.")


async def _focus_card(db: Db, learner_id: UUID, topic_id: UUID, prompt: str) -> Card:
    """A card of the topic: its task in the bank, the card in the cards artifact."""
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=task_id,
        source="generated",
        condition=TaskCondition(text=prompt, latex=None),
        markup=_markup(fx.SKILL_FAILED),
        verification="unchecked",
        actor="artifact_writer@1",
        idempotency_key=f"card:{task_id}",
    )
    card = Card(card_id=task_id, kind="definition", prompt=prompt, owner="system")
    await write_system_cards(
        db, SETTINGS, learner_id=learner_id, topic_id=topic_id, cards=[card],
        created_by="artifact_writer@1",
    )  # fmt: skip
    return card


async def _topic_row(db: Db, learner_id: UUID, topic_id: UUID) -> None:
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": topic_id,
                    "domain_id": fx.DOMAIN,
                    "name": "Fractions",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"topic:{topic_id}",
                domain_id=fx.DOMAIN,
            )
        ],
    )


async def test_the_turn_labels_the_cards_of_its_focus_and_shows_their_questions(
    pg_db: Db,
) -> None:
    """Q1: a card of the focus topic gets a label `C1…` and slot 9 shows the topic and the
    question behind it (the answer is not shown); a card of another topic or another learner
    has none; `label_max_cards` cuts the list; no cards — no `C` lines."""
    learner_id, stranger = await create_learner(pg_db), await create_learner(pg_db)
    session_id = uuid4()
    await _topic_row(pg_db, learner_id, fx.TOPIC_OPEN)
    mine = await _focus_card(pg_db, learner_id, fx.TOPIC_OPEN, "What is a common denominator?")
    second = await _focus_card(pg_db, learner_id, fx.TOPIC_OPEN, "When may fractions be added?")
    elsewhere_topic = uuid4()
    await _topic_row(pg_db, learner_id, elsewhere_topic)
    elsewhere = await _focus_card(pg_db, learner_id, elsewhere_topic, "Not in the focus")
    await _topic_row(pg_db, stranger, uuid4())
    seq = await _message(
        pg_db, learner_id, session_id, text="teach me", intent="learn", topic_id=fx.TOPIC_OPEN
    )

    def source_with(settings: Settings) -> MemoryTurnContextSource:
        return MemoryTurnContextSource(
            db=pg_db,
            gateway=LLMGateway(ScriptedLLMProvider(), settings, db=pg_db),
            settings=settings,
            reader=lambda _learner: fx.FixedGraphReader(),
            clock=lambda: NOW_MS,
        )

    setup = await source_with(SETTINGS).assemble(_request(learner_id, session_id, seq))

    assert setup.labels.label("card", mine.card_id) == "C1"
    assert setup.labels.label("card", second.card_id) == "C2"
    assert setup.labels.label("card", elsewhere.card_id) is None
    assert setup.manifest.label_table["C1"] == str(mine.card_id)
    prompt = setup.assembled.request.base.prompt
    assert "C1: " in prompt and "What is a common denominator?" in prompt
    assert "C2: " in prompt and "When may fractions be added?" in prompt
    assert "Not in the focus" not in prompt
    assert setup.assembled.guard.labels is not None
    assert setup.assembled.guard.labels.labels("card") == {
        "C1": mine.card_id,
        "C2": second.card_id,
    }

    capped = SETTINGS.model_copy(update={"label_max_cards": 1})
    one = await source_with(capped).assemble(_request(learner_id, session_id, seq))
    assert one.labels.labels("card") == {"C1": mine.card_id}
    assert "When may fractions be added?" not in one.assembled.request.base.prompt

    lonely = await create_learner(pg_db)
    lonely_seq = await _message(
        pg_db, lonely, session_id, text="hi", intent="learn", topic_id=fx.TOPIC_OPEN
    )
    none = await source_with(SETTINGS).assemble(_request(lonely, session_id, lonely_seq))
    assert none.labels.labels("card") == {}
    assert "C1: " not in none.assembled.request.base.prompt


async def test_assemble_includes_landmarks_and_teacher_edits_in_slot_6(pg_db: Db) -> None:
    """Slot 6 includes code-computed landmarks and teacher-added landmarks (fetching extra
    topics and skills as needed) when memory is up, with no raw UUIDs in the prompt."""
    from psycopg.types.json import Jsonb

    from quack.graph.read import TopicMemory

    extra_topic = uuid4()

    class _ReaderWithExtraTopic(fx.FixedGraphReader):
        async def topic_memory(self, topic_ids: Any) -> list[TopicMemory]:
            items = await super().topic_memory(topic_ids)
            if extra_topic in topic_ids:
                items.append(
                    TopicMemory(
                        topic_id=extra_topic,
                        name="Decimal fractions",
                        domain_id=fx.DOMAIN,
                        skills=[],
                        misconceptions=[],
                    )
                )
            return items

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    plan_doc = {
        "v": 2,
        "goal": "Master reducing fractions",
        "current_step": {
            "id": "find-gcd",
            "text": "Find common divisor",
            "landmark": {"kind": "skill", "id": str(fx.SKILL_FAILED)},
        },
        "next_steps": [
            {
                "id": "try-extra",
                "text": "Connect to decimals",
                "landmark": {"kind": "topic", "id": str(extra_topic)},
            }
        ],
        "landmark_edits": {
            "added": [
                {
                    "kind": "skill",
                    "id": str(fx.SKILL_PROVEN),
                    "before_kind": None,
                    "before_id": None,
                },
                {"kind": "topic", "id": str(extra_topic), "before_kind": None, "before_id": None},
            ],
            "removed": [],
        },
        "branches": [],
    }
    await pg_db.execute(
        "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
        "VALUES (%(s)s, %(l)s, %(p)s, 'active')",
        {"s": session_id, "l": learner_id, "p": Jsonb(plan_doc)},
    )
    seq = await _message(
        pg_db, learner_id, session_id, text="let's go", intent="learn", topic_id=fx.TOPIC_OPEN
    )
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: _ReaderWithExtraTopic(),
        clock=lambda: NOW_MS,
    )
    setup = await source.assemble(_request(learner_id, session_id, seq))
    prompt = setup.assembled.request.base.prompt

    failed_label = setup.labels.label("skill", fx.SKILL_FAILED)
    quiet_label = setup.labels.label("skill", fx.SKILL_QUIET)
    proven_label = setup.labels.label("skill", fx.SKILL_PROVEN)
    extra_label = setup.labels.label("topic", extra_topic)
    assert all(x is not None for x in (failed_label, quiet_label, proven_label, extra_label))
    assert "Plan goal: Master reducing fractions" in prompt
    assert "Landmarks (built by code; use their labels in planUpdate):" in prompt
    assert f"- {failed_label} (current): common denominator" in prompt
    assert f"- {quiet_label} (upcoming): reduce a fraction" in prompt
    assert f"- {proven_label} (done, added by teacher): add fractions" in prompt
    assert f"- {extra_label} (upcoming, added by teacher): Decimal fractions" in prompt
    assert f"{extra_label}: Decimal fractions" in prompt
    for uid in (fx.SKILL_FAILED, fx.SKILL_QUIET, fx.SKILL_PROVEN, extra_topic):
        assert str(uid) not in prompt


async def test_assemble_degrades_cleanly_without_landmarks_when_neo4j_down(pg_db: Db) -> None:
    """When Neo4j is unavailable, slot 6 keeps the stored plan without landmarks."""
    from neo4j.exceptions import ServiceUnavailable
    from psycopg.types.json import Jsonb

    class _Down(fx.FixedGraphReader):
        async def profile(self, learner_id: UUID) -> Any:
            raise ServiceUnavailable("neo4j is down")

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    await pg_db.execute(
        "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
        "VALUES (%(s)s, %(l)s, %(p)s, 'active')",
        {
            "s": session_id,
            "l": learner_id,
            "p": Jsonb(
                {
                    "v": 2,
                    "goal": "Master reducing fractions",
                    "current_step": {"id": "step-1", "text": "Start", "landmark": None},
                    "next_steps": [],
                    "landmark_edits": {"added": [], "removed": []},
                    "branches": [],
                }
            ),
        },
    )
    seq = await _message(
        pg_db, learner_id, session_id, text="hello", intent="learn", topic_id=fx.TOPIC_OPEN
    )
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: _Down(),
        clock=lambda: NOW_MS,
    )
    setup = await source.assemble(_request(learner_id, session_id, seq))
    prompt = setup.assembled.request.base.prompt

    assert setup.assembled.degraded_mode == "memory_unavailable"
    assert "Plan goal: Master reducing fractions" in prompt
    assert "Current step (id step-1): Start" in prompt
    assert "Landmarks (built by code" not in prompt


def test_fix_markup_request_builds_strict_task_and_element_schemas() -> None:
    """`_fix_markup_request` targets `TurnTask` for task elements and the concrete element
    model for non-task elements, passing the shown text, problems, and label-table rule."""
    from quack.llm.provider import ProviderRequest
    from quack.session.elements import MaterialLinkElement, TaskElement
    from quack.session.teacher_output import TurnTask
    from quack.session.turn_graph import MarkupFix
    from quack.session.turn_source import _fix_markup_request

    base = ProviderRequest(
        model="muse-wonder-1.0",
        prompt="context",
        json_schema={"type": "object"},
        schema_name="TeacherOutputV2",
        temperature=0.0,
        timeout_sec=30.0,
    )
    task_el = TaskElement(
        element_id="e1", kind="task", prompt="Add 1/2 and 1/3", canvas=True, ask_confidence=False
    )
    task_req, task_model = _fix_markup_request(
        base,
        MarkupFix(
            element=task_el,
            task=None,
            problems=["no task markup"],
            text="Try this problem.",
            attempt=1,
        ),
    )
    assert task_model is TurnTask and task_req.base.schema_name == "TurnTask"
    assert task_req.base.json_schema["additionalProperties"] is False
    note = task_req.input_items[0]["content"][0]["text"]
    assert "no task markup" in note and "Label table" in note and "Try this problem." in note

    link_el = MaterialLinkElement(element_id="e2", kind="material_link", chunk="P9", page=4)
    link_req, link_model = _fix_markup_request(
        base,
        MarkupFix(
            element=link_el,
            task=None,
            problems=["unknown_label: P9"],
            text="See the notes.",
            attempt=1,
        ),
    )
    assert link_model is MaterialLinkElement
    assert link_req.base.schema_name == "MaterialLinkElement"
    assert link_req.base.json_schema["additionalProperties"] is False
    assert "unknown_label: P9" in link_req.input_items[0]["content"][0]["text"]


async def test_assemble_wires_fix_markup_and_degrades_on_error(pg_db: Db) -> None:
    """`assemble` wires `TurnSetup.fix_markup` through the gateway stream and `ApiTurnPorts`
    returns `None` cleanly if the fix call fails."""
    from quack.session.action_turn import ApiTurnPorts
    from quack.session.elements import MaterialLinkElement, TaskElement
    from quack.session.record import RecordedAction
    from quack.session.teacher_output import TurnTask
    from quack.session.turn_graph import MarkupFix
    from tests.support.llm import ScriptedFailure

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    seq = await _message(pg_db, learner_id, session_id, text="teach me", intent="learn")
    provider = ScriptedLLMProvider()
    provider.respond(
        "TurnTask",
        lambda _r: {
            "element_id": "e1",
            "tests": [{"skill": "S1", "new_skill": None, "level": 3}],
            "methodNamed": True,
            "variantOf": None,
            "answer": {
                "type": "number",
                "value": {"latex": "5/6", "sympy": "5/6"},
                "items": None,
                "options": None,
                "accepted": None,
                "tol_abs": None,
                "tol_rel": None,
                "sig_digits": None,
                "unit": None,
                "mode": None,
                "form_target": None,
                "variables": None,
                "key_ideas": None,
            },
            "options": None,
            "pairTaskId": None,
            "form": None,
            "parts": None,
        },
    )
    provider.respond(
        "MaterialLinkElement",
        lambda _r: {"element_id": "e2", "kind": "material_link", "chunk": "P1", "page": 4},
    )
    gateway = LLMGateway(provider, SETTINGS, db=pg_db)
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=gateway,
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        clock=lambda: NOW_MS,
    )
    setup = await source.assemble(_request(learner_id, session_id, seq))
    assert setup.fix_markup is not None

    task_el = TaskElement(
        element_id="e1", kind="task", prompt="Add 1/2 and 1/3", canvas=True, ask_confidence=False
    )
    fixed_task = await setup.fix_markup(
        MarkupFix(element=task_el, task=None, problems=["no task markup"], text="Solve.", attempt=1)
    )
    assert isinstance(fixed_task, TurnTask) and fixed_task.element_id == "e1"

    link_el = MaterialLinkElement(element_id="e2", kind="material_link", chunk="P9", page=4)
    fixed_link = await setup.fix_markup(
        MarkupFix(
            element=link_el, task=None, problems=["unknown_label: P9"], text="Read.", attempt=1
        )
    )
    assert isinstance(fixed_link, MaterialLinkElement) and fixed_link.chunk == "P1"

    ports = ApiTurnPorts(
        db=pg_db,
        redis=None,
        settings=SETTINGS,
        source=source,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=fx.DOMAIN,
        recorded=RecordedAction(seq=seq, verdict=None, starts_turn=True),
        action=cast(Action, None),
        frames=cast(Any, None),
    )
    ports.setup = setup
    provider.fail(
        ScriptedFailure(
            kind="unavailable", schema_name="TurnTask", times=1 + SETTINGS.llm_retry_attempts
        )
    )
    assert (
        await ports.fix_markup(
            MarkupFix(
                element=task_el, task=None, problems=["no task markup"], text="Solve.", attempt=1
            )
        )
        is None
    )


def test_canonical_topic_name_strips_domain_prefix_in_teacher_context() -> None:
    from quack.graph.topic_names import canonical_topic_name

    assert canonical_topic_name("Statistics: binomial distribution", "Statistics") == (
        "binomial distribution"
    )
    assert canonical_topic_name("Statistics: normal distribution", "Statistics") == (
        "normal distribution"
    )
    assert canonical_topic_name("normal distribution", "Statistics") == "normal distribution"


async def test_assemble_notes_awareness_when_topic_has_notes(pg_db: Db) -> None:
    from quack.artifacts.documents import SystemBlock, write_system_blocks
    from quack.session.turn_inputs import has_topic_notes

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    await _topic_row(pg_db, learner_id, fx.TOPIC_OPEN)
    assert not await has_topic_notes(pg_db, learner_id=learner_id, topic_id=fx.TOPIC_OPEN)
    assert not await has_topic_notes(pg_db, learner_id=learner_id, topic_id=None)

    await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=fx.TOPIC_OPEN,
        blocks=[SystemBlock(kind="summary", content="Fractions notes")],
        created_by="artifact_writer@1",
    )
    assert await has_topic_notes(pg_db, learner_id=learner_id, topic_id=fx.TOPIC_OPEN)

    seq = await _message(
        pg_db, learner_id, session_id, text="give me notes", intent="learn", topic_id=fx.TOPIC_OPEN
    )
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        clock=lambda: NOW_MS,
    )
    setup = await source.assemble(_request(learner_id, session_id, seq))
    assert "Topic notes: already saved in this topic" in setup.assembled.request.base.prompt
