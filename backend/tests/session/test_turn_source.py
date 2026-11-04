"""The context source of the teacher turn (ТЗ Ф4 WP14, WP13.2 «assemble»; Т-§10, Т-§12.9):
the journal gives goal, tasks, the answered action and effort; memory (S4) gives slots 3, 4, 7;
the first turn freezes slots 3–4 into its manifest row. D-B-181."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

import pytest

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
    TestedLevelRecord,
)
from quack.events.values import AnswerSpec, MathValue, Verdict
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
    assert setup.manifest.prompt_version == "teacher@2"
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
        db=None, llm=None, settings=SETTINGS, neo4j=None, redis=None, embeddings=None
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
