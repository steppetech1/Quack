"""The service turns of the built-in `/skill-creator` (owner decision 2026-11-24): the teacher
drafts a learner skill with the learner in the chat and proposes it as the element
`skill_proposal`; the dialogue is not learning, so nothing of it may reach the plan, the tasks, the
exposures, the extractor, the profile, the title, the memory search or the teacher context of an
ordinary turn. The learner saves the skill from the browser; the name is reserved."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter

from quack.artifacts.learner_skills import (
    ARTIFACT_TYPES,
    NAME_PATTERN,
    SkillConflict,
    create_skill,
    update_skill,
)
from quack.config import Settings
from quack.db.db import Db
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.service_turns import (
    BUILTIN_SKILL_CREATOR,
    RESERVED_SKILL_NAMES,
    is_service_event,
    service_event_seqs,
    without_service,
)
from quack.graph.profile_fold import collect_profile_inputs
from quack.jobs import rules_p4 as rules
from quack.jobs.episodes import EpisodeTracker, episode_has_content
from quack.llm.labels import LabelMap
from quack.materials.search import search_events
from quack.prompts.registry import PromptRegistry
from quack.session.actions import Action, ElementFrame
from quack.session.conversation import render_conversation
from quack.session.elements import SkillProposalElement
from quack.session.record import record_action
from quack.session.service_turns import (
    SKILL_ARTIFACT_TYPES,
    SKILL_NAME_PATTERN,
    dialogue_open,
    drop_proposals,
    settle_name,
    settle_proposal,
    settle_service_output,
)
from quack.session.teacher_output import TeacherOutputV2
from quack.session.titles import _MESSAGES as TITLE_MESSAGES  # pyright: ignore[reportPrivateUsage]
from quack.session.turn_commit import commit_turn, turn_batch
from quack.session.turn_graph import TurnToCommit
from tests.db.fixtures import create_learner
from tests.events.samples import sample_payload
from tests.session.test_turn_commit import (
    DOMAIN,
    LEARNER,
    SETTINGS,
    _labels,
    _manifest,
    _output,
    _turn,
)
from tests.session.test_turn_graph import (
    _answers,
    _head,
    _markup,
    _ports,
    _run,
    _task,
)
from tests.support.llm import ScriptedLLMProvider

ACTION = TypeAdapter[Action](Action)
SESSION = UUID(int=500)
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _proposal(**fields: Any) -> dict[str, Any]:
    return {
        "element_id": "e1",
        "kind": "skill_proposal",
        "name": "short-notes",
        "text": "Write notes as short numbered steps, one line each.",
        "artifact_type": "notes",
        **fields,
    }


def _service_output(**markup: Any) -> TeacherOutputV2:
    """What the model may write in the mode (dirty on purpose: a task, a plan, a deadline...)."""
    return _output(
        elements=[
            *_output().p3_markup.model_dump(by_alias=True)["elements"],
            _proposal(),
        ],
        **markup,
    )


# --- the pieces of code ---


def test_names_and_types_equal_the_skill_store() -> None:
    """The session package does not import `artifacts`: its copies are kept equal by this test."""
    assert SKILL_NAME_PATTERN.pattern == NAME_PATTERN.pattern
    assert SKILL_ARTIFACT_TYPES == ARTIFACT_TYPES
    assert BUILTIN_SKILL_CREATOR in RESERVED_SKILL_NAMES


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("short-notes", "short-notes"),
        ("/Short Notes!", "short-notes"),
        ("Конспект по шагам", "конспект-по-шагам"),
        ("???", "my-skill"),
        ("x" * 80, "x" * 40),
        ("skill-creator", "skill-creator-2"),
        ("/Skill-Creator", "skill-creator-2"),
    ],
)
def test_the_name_of_a_proposal_is_made_valid_by_code(raw: str, want: str) -> None:
    assert settle_name(raw) == want
    assert SKILL_NAME_PATTERN.fullmatch(want)


def test_the_text_of_a_proposal_is_cut_to_the_limit_and_an_empty_one_is_no_proposal() -> None:
    element = SkillProposalElement.model_validate(_proposal(text="  " + "a" * 5000))
    settled = settle_proposal(element, Settings(learner_skill_max_chars=100))
    assert settled is not None and len(settled.text) == 100
    assert settle_proposal(element.model_copy(update={"text": "  \n"}), SETTINGS) is None


def _turns(*rows: tuple[str | None, str, bool]) -> list[dict[str, Any]]:
    return [{"service": s, "intent": i, "proposal": p} for s, i, p in rows]


@pytest.mark.parametrize(
    ("rows", "want"),
    [
        ([], False),  # enter: no turn yet, the learner has to type the command
        ([(None, "clarify_goal", False)], False),  # an ordinary question is not the dialogue
        ([("skill_creator", "clarify_goal", False)], True),  # stays open after a question
        ([("skill_creator", "answer_question", True)], True),  # stays open after a proposal
        ([("skill_creator", "answer_question", False)], False),  # exit: neither of the two
        ([(None, "explain", False), ("skill_creator", "clarify_goal", False)], False),
        (
            [("skill_creator", "clarify_goal", False), ("skill_creator", "clarify_goal", False)],
            True,
        ),
    ],
)
def test_the_dialogue_stays_open_after_a_question_or_a_proposal(
    rows: list[tuple[str | None, str, bool]], want: bool
) -> None:
    """Newest turn first; the dialogue is open when it ended with a question or a proposal."""
    assert dialogue_open(_turns(*rows), 8) is want


def test_a_dialogue_is_capped() -> None:
    rows = _turns(*[("skill_creator", "clarify_goal", False)] * 3)
    assert dialogue_open(rows, 4) is True
    assert dialogue_open(rows, 3) is False


# --- the output of the turn ---


def test_a_service_output_keeps_the_text_and_one_proposal_and_nothing_else() -> None:
    settled = settle_service_output(_service_output(), SETTINGS)
    markup = settled.p3_markup
    assert [e.kind for e in markup.elements] == ["skill_proposal"]
    assert markup.tasks == [] and markup.exposures == [] and markup.agent_requests == []
    assert markup.deadline is None and markup.targets_misconception is None
    plan = markup.plan_update
    assert (plan.goal, plan.current_step, plan.next_steps, plan.landmark_edits) == (
        None,
        None,
        None,
        None,
    )
    assert plan.branches == [] and plan.cancel_requests == []
    assert settled.p1_head.target_skills == [] and settled.p1_head.approach == "none"
    # the block of the removed task element is gone, the one of the proposal is there
    assert [b.element_id for b in settled.p2_body.blocks] == [None, "e1"]
    assert settle_service_output(settled, SETTINGS) == settled  # idempotent


def test_a_proposal_without_a_block_gets_one() -> None:
    output = _output(elements=[_proposal(element_id="p9")], tasks=[])
    blocks = [b for b in output.p2_body.blocks if b.element_id is None]
    output = output.model_copy(
        update={"p2_body": output.p2_body.model_copy(update={"blocks": blocks})}
    )
    settled = settle_service_output(output, SETTINGS)
    assert [b.element_id for b in settled.p2_body.blocks][-1] == "p9"


def test_a_proposal_is_settled_and_a_second_one_is_dropped() -> None:
    output = _output(
        elements=[_proposal(name="Skill-Creator"), _proposal(element_id="e2", name="other")],
        tasks=[],
    )
    settled = settle_service_output(output, SETTINGS)
    (proposal,) = settled.p3_markup.elements
    assert isinstance(proposal, SkillProposalElement) and proposal.name == "skill-creator-2"


def test_outside_the_mode_a_proposal_is_dropped_with_its_block() -> None:
    output = _output(elements=[_proposal(element_id="e9")], tasks=[], exposures=[])
    blocks = [
        *output.p2_body.blocks,
        output.p2_body.blocks[-1].model_copy(update={"element_id": "e9"}),
    ]
    output = output.model_copy(
        update={"p2_body": output.p2_body.model_copy(update={"blocks": blocks})}
    )
    dropped = drop_proposals(output)
    assert all(e.kind != "skill_proposal" for e in dropped.p3_markup.elements)
    assert all(b.element_id != "e9" for b in dropped.p2_body.blocks)


def _turn_json(**markup: Any) -> dict[str, Any]:
    return {
        "p1_head": _head(intent="clarify_goal"),
        "p2_body": {
            "blocks": [
                {
                    "block_id": "b1",
                    "text": "Here is a draft. Change what you like.",
                    "element_id": None,
                },
                {"block_id": "b2", "text": None, "element_id": "e1"},
            ]
        },
        "p3_markup": _markup(**markup),
    }


async def test_the_graph_settles_a_service_turn_and_shows_only_the_proposal() -> None:
    provider = ScriptedLLMProvider()
    dirty = _turn_json(elements=[_proposal()], tasks=[_task()])
    _answers(provider, dirty)
    ports = _ports()
    ports.assembled = replace(ports.assembled, service="skill_creator")
    assert await _run(ports, provider) == 42
    shown = [f.element.kind for f in ports.frames if isinstance(f, ElementFrame)]
    assert shown == ["skill_proposal"]
    (turn,) = ports.committed
    markup = turn.output.p3_markup
    assert markup.tasks == [] and markup.exposures == [] and markup.plan_update.current_step is None


async def test_the_graph_drops_a_proposal_of_an_ordinary_turn() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn_json(elements=[_proposal()], tasks=[]))
    ports = _ports()
    assert await _run(ports, provider) == 42
    assert not [f for f in ports.frames if isinstance(f, ElementFrame)]
    (turn,) = ports.committed
    assert turn.output.p3_markup.elements == []


# --- the write ---


def _service_turn() -> TurnToCommit:
    return _turn(_service_output())


async def _journal(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )


async def test_a_service_turn_writes_only_its_turn_and_leaves_the_plan_alone(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    labels = _labels()

    def batch(turn: TurnToCommit, action_seq: int, **kw: Any) -> Any:
        return turn_batch(
            turn,
            labels,
            manifest=_manifest(learner_id, session_id, labels),
            action_seq=action_seq,
            domain_id=DOMAIN,
            settings=SETTINGS,
            learner_texts=LEARNER,
            **kw,
        )

    # an ordinary turn first: it makes the plan, a task and a request
    await commit_turn(pg_db, batch(_turn(), 1), learner_id=learner_id)
    before = await pg_db.fetch_one(
        "SELECT plan FROM content.session_plans WHERE session_id = %(s)s", {"s": session_id}
    )
    assert before is not None
    types_before = [r["type"] for r in await _journal(pg_db, learner_id)]
    assert "task.created" in types_before and "agent.requested" in types_before

    # the service turn: even a dirty output writes nothing but `turn.committed`
    service = batch(_service_turn(), 2, service="skill_creator")
    assert service.plan is None and not service.tasks
    seq = await commit_turn(pg_db, service, learner_id=learner_id)
    rows = await _journal(pg_db, learner_id)
    new = [r for r in rows if r["seq"] >= seq]
    assert [r["type"] for r in new] == ["turn.committed"]
    payload = new[0]["payload"]
    assert payload["service"] == "skill_creator"
    assert [e["kind"] for e in payload["markup"]["elements"]] == ["skill_proposal"]
    assert payload["markup"]["tasks"] == [] and payload["markup"]["exposures"] == []
    assert payload["head"]["target_skill_ids"] == []
    after = await pg_db.fetch_one(
        "SELECT plan FROM content.session_plans WHERE session_id = %(s)s", {"s": session_id}
    )
    assert after is not None and after["plan"] == before["plan"]


async def test_a_follow_up_message_is_stamped_while_the_dialogue_is_open(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    session_id = uuid4()

    async def say(text: str, **extra: Any) -> int:
        action = ACTION.validate_python(
            {"kind": "message", "idempotency_key": str(uuid4()), "text": text, **extra}
        )
        recorded = await record_action(
            pg_db,
            action,
            learner_id=learner_id,
            session_id=session_id,
            domain_id=None,
            settings=SETTINGS,
        )
        return recorded.seq

    async def invocation(seq: int) -> str | None:
        row = await pg_db.fetch_one(
            "SELECT payload ->> 'skill_invocation' AS s FROM core.events "
            "WHERE learner_id = %(l)s AND seq = %(q)s",
            {"l": learner_id, "q": seq},
        )
        assert row is not None
        return row["s"]

    async def committed(service: str | None, intent: str, proposal: bool, action_seq: int) -> None:
        elements = [_proposal()] if proposal else []
        payload = {
            **sample_payload("turn.committed"),
            "action_seq": action_seq,
            "service": service,
            "head": {**sample_payload("turn.committed")["head"], "intent": intent},
            "markup": {**sample_payload("turn.committed")["markup"], "elements": elements},
        }
        await append_events(
            pg_db,
            learner_id,
            [
                NewEvent(
                    type="turn.committed",
                    payload=payload,
                    actor="teacher@3",
                    idempotency_key=f"t:{uuid4()}",
                    session_id=session_id,
                )
            ],
        )

    first = await say("/skill-creator short notes", skill_invocation=BUILTIN_SKILL_CREATOR)
    assert await invocation(first) == BUILTIN_SKILL_CREATOR
    # before the first service turn is committed there is no dialogue to follow
    assert await invocation(await say("hello")) is None
    await committed("skill_creator", "clarify_goal", False, first)  # ... a question
    follow = await say("for the notes")
    assert await invocation(follow) == BUILTIN_SKILL_CREATOR  # stays after a question
    # another command is its own: it leaves the dialogue
    assert await invocation(await say("/other x", skill_invocation="other")) == "other"
    await committed("skill_creator", "answer_question", True, follow)  # ... a proposal
    assert await invocation(await say("shorter")) == BUILTIN_SKILL_CREATOR
    await committed("skill_creator", "answer_question", False, follow)  # the exit line
    assert await invocation(await say("explain limits")) is None
    # a repeat of a stamped message does not clash with itself
    key = str(uuid4())
    body = {"kind": "message", "idempotency_key": key, "text": "again"}
    await committed("skill_creator", "clarify_goal", False, follow)
    one = await record_action(
        pg_db,
        ACTION.validate_python(body),
        learner_id=learner_id,
        session_id=session_id,
        domain_id=None,
        settings=SETTINGS,
    )
    await committed("skill_creator", "answer_question", False, one.seq)
    two = await record_action(
        pg_db,
        ACTION.validate_python(body),
        learner_id=learner_id,
        session_id=session_id,
        domain_id=None,
        settings=SETTINGS,
    )
    assert one.seq == two.seq


# --- the readers of the journal skip the dialogue ---


def _ev(seq: int, type_: str, payload: dict[str, Any], at: int = 0) -> StoredEvent:
    return StoredEvent(
        learner_id=UUID(int=7),
        seq=seq,
        id=seq,
        type=type_,
        schema_version=1,
        actor="test",
        domain_id=None,
        session_id=SESSION,
        causation_seq=None,
        correlation_id=None,
        idempotency_key=f"k{seq}",
        payload_hash="h",
        occurred_at=T0 + timedelta(minutes=at),
        payload={**sample_payload(type_), **payload},
        source_refs=[],
    )


def _message(seq: int, text: str, invocation: str | None = None, **extra: Any) -> StoredEvent:
    return _ev(seq, "message.sent", {"text": text, "skill_invocation": invocation, **extra})


def _service_message(seq: int, text: str = "/skill-creator short") -> StoredEvent:
    return _message(seq, text, BUILTIN_SKILL_CREATOR)


def _service_turn_event(seq: int, **extra: Any) -> StoredEvent:
    return _ev(seq, "turn.committed", {"service": "skill_creator", **extra})


def test_service_events_are_found_with_the_voice_of_their_message() -> None:
    voice = _ev(3, "voice.transcribed", {"text": "shorter please"})
    events = [
        _message(1, "teach me limits"),
        _service_message(2),
        voice,
        _message(4, "", BUILTIN_SKILL_CREATOR, voice_seq=3),
        _service_turn_event(5),
        _message(6, "/other x", "other"),
    ]
    assert service_event_seqs(events) == {2, 3, 4, 5}
    assert [e.seq for e in without_service(events)] == [1, 6]
    assert not is_service_event("message.sent", {"skill_invocation": "other"})


def test_the_dialogue_opens_no_episode_and_ends_none() -> None:
    tracker = EpisodeTracker(Settings(episode_max_turns=2, episode_idle_min=10))
    events = [
        _message(1, "teach me limits"),
        _service_message(2),
        _service_turn_event(3),
        _service_message(4),
        _service_turn_event(5),  # two service turns: no `max_turns`, no intent boundary
    ]
    assert [c for e in events for c in tracker.feed(e)] == []
    assert list(tracker.dump()) == [str(SESSION)]
    assert tracker.dump()[str(SESSION)]["turns"] == 0
    assert (
        tracker.dump()[str(SESSION)]["from_seq"] == 1
        and tracker.dump()[str(SESSION)]["last_seq"] == 1
    )
    # a dialogue alone makes no episode at all
    alone = EpisodeTracker(Settings(episode_max_turns=2, episode_idle_min=10))
    assert [c for e in events[1:] for c in alone.feed(e)] == [] and alone.dump() == {}


def test_a_dialogue_is_no_content_of_an_episode() -> None:
    assert episode_has_content([_message(1, "teach me")]) is True
    assert episode_has_content([_service_message(1), _service_turn_event(2)]) is False
    voice = _ev(1, "voice.transcribed", {"text": "shorter"})
    assert (
        episode_has_content([voice, _message(2, "", BUILTIN_SKILL_CREATOR, voice_seq=1)]) is False
    )


def test_the_extractor_window_leaves_the_dialogue_out() -> None:
    from quack.agents.extract_episode import extraction_window

    events = [
        _message(1, "teach me limits"),
        _service_message(2),
        _service_turn_event(3, text="Here is a draft"),
        _message(4, "what is a limit?"),
    ]
    window = extraction_window(events, 1, 4, Settings(episode_context_turns=5))
    assert [e.seq for e in window.episode] == [1, 4]
    assert window.before == []


def test_the_profile_does_not_learn_from_the_dialogue() -> None:
    def turn(seq: int, approach: str, service: str | None) -> StoredEvent:
        head = {**sample_payload("turn.committed")["head"], "approach": approach}
        return _ev(seq, "turn.committed", {"head": head, "service": service})

    inputs = collect_profile_inputs(
        [turn(1, "small_steps", None), turn(2, "visualization", "skill_creator")]
    )
    assert [t.approach for t in inputs.turns] == ["small_steps"]


def test_a_service_turn_does_not_title_the_session() -> None:
    def plans(event: StoredEvent) -> list[str]:
        return [p.kind for p in rules.plan_jobs(event, SETTINGS) if p.kind == "session.title"]

    assert plans(_ev(5, "turn.committed", {})) == ["session.title"]
    assert plans(_service_turn_event(5)) == []


def test_the_teacher_sees_the_dialogue_and_its_proposal_only_in_a_service_turn() -> None:
    labels = LabelMap.build(SETTINGS)
    proposal = _service_turn_event(
        3,
        text="A draft",
        markup={**sample_payload("turn.committed")["markup"], "elements": [_proposal()]},
    )
    events = [_service_message(2), proposal, _message(4, "shorter", BUILTIN_SKILL_CREATOR)]
    seen = render_conversation(events, labels, SETTINGS)
    assert 'Proposed skill "/short-notes" (bound to: notes)' in seen
    assert "Write notes as short numbered steps" in seen
    # an ordinary turn gets the journal without the dialogue (`turn_source._scoped`)
    ordinary = render_conversation(
        without_service([*events, _message(5, "limits?")]), labels, SETTINGS
    )
    assert "short-notes" not in ordinary and "limits?" in ordinary


# --- the database: title, search, skill store ---


async def test_title_and_search_skip_the_dialogue(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    voice = (
        await append_events(
            pg_db,
            learner_id,
            [
                NewEvent(
                    type="voice.transcribed",
                    payload={"text": "zebra quartz shorter", "started_at_ms": 0, "ended_at_ms": 1},
                    actor="learner",
                    idempotency_key=f"v:{uuid4()}",
                    session_id=session_id,
                )
            ],
        )
    )[0]
    texts = [
        ("message.sent", {"text": "zebra quartz draft", "skill_invocation": "skill-creator"}),
        ("message.sent", {"text": "", "skill_invocation": "skill-creator", "voice_seq": voice.seq}),
        ("message.sent", {"text": "limits of zebra quartz"}),
        (
            "turn.committed",
            {
                **sample_payload("turn.committed"),
                "text": "zebra quartz proposal",
                "service": "skill_creator",
            },
        ),
        ("turn.committed", {**sample_payload("turn.committed"), "text": "zebra quartz reply"}),
    ]
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type=t,
                payload=p,
                actor="learner" if t == "message.sent" else "teacher@3",
                idempotency_key=f"e:{i}:{uuid4()}",
                session_id=session_id,
            )
            for i, (t, p) in enumerate(texts)
        ],
    )
    hits = await search_events(pg_db, learner_id=learner_id, query="zebra quartz", limit=10)
    assert sorted(h.snippet for h in hits if h.type != "voice.transcribed") == sorted(
        [
            "limits of <b>zebra</b> <b>quartz</b>",
            "<b>zebra</b> <b>quartz</b> reply",
        ]
    )
    rows = await pg_db.fetch_all(
        TITLE_MESSAGES, {"learner_id": learner_id, "session_id": session_id, "limit": 10}
    )
    assert [r["text"] for r in rows] == ["limits of zebra quartz"]


async def test_the_name_is_reserved_for_the_built_in_command(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    with pytest.raises(SkillConflict, match="built-in"):
        await create_skill(
            pg_db,
            learner_id=learner_id,
            idempotency_key=str(uuid4()),
            name=BUILTIN_SKILL_CREATOR,
            instruction="x",
            artifact_types=(),
        )
    skill = await create_skill(
        pg_db,
        learner_id=learner_id,
        idempotency_key=str(uuid4()),
        name="mine",
        instruction="x",
        artifact_types=(),
    )
    with pytest.raises(SkillConflict, match="built-in"):
        await update_skill(
            pg_db, learner_id=learner_id, skill_id=skill.skill_id, name="skill-creator"
        )
    same = await update_skill(
        pg_db, learner_id=learner_id, skill_id=skill.skill_id, instruction="y"
    )
    assert same.name == "mine"


# --- the prompt ---


def test_the_instruction_is_registered_and_renders() -> None:
    template = PromptRegistry().load("skill_creator", 1)
    assert SETTINGS.skill_creator_prompt == template.ref
    text = template.render({"name_max": 40, "text_max": 2000, "artifact_types": "notes, cards"})
    assert "{{" not in text and "40 characters" in text and "2000 characters" in text
    assert "skill_proposal" in text


# --- the context of the turn ---


async def test_a_service_turn_is_assembled_from_the_dialogue_alone(pg_db: Db) -> None:
    """The instruction is a developer message of the first step, the memory tools are off, the
    plan, memory and labels are not shown, the effort is low; the next ordinary turn does not see
    the dialogue; the built-in command wins over a learner skill of the same name."""
    from quack.llm.gateway import LLMGateway
    from quack.session.turn_source import MemoryTurnContextSource
    from tests.session.test_turn_source import NOW_MS, _message, _request
    from tests.support import graph_read as fx

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    await pg_db.execute(
        "INSERT INTO content.learner_skills (skill_id, version, learner_id, name, instruction, "
        "bindings) VALUES (%(s)s, 1, %(l)s, 'skill-creator', 'IMPOSTOR TEXT', '{}'::jsonb)",
        {"s": uuid4(), "l": learner_id},
    )
    source = MemoryTurnContextSource(
        db=pg_db,
        gateway=LLMGateway(ScriptedLLMProvider(), SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=lambda _learner: fx.FixedGraphReader(),
        clock=lambda: NOW_MS,
    )
    ordinary = await _message(pg_db, learner_id, session_id, text="teach me limits")
    first = await source.assemble(_request(learner_id, session_id, ordinary))
    assert first.assembled.service is None and first.assembled.request.tools
    assert not first.assembled.request.input_items

    service_seq = await _message(
        pg_db,
        learner_id,
        session_id,
        text="/skill-creator short notes",
        skill_invocation=BUILTIN_SKILL_CREATOR,
    )
    setup = await source.assemble(_request(learner_id, session_id, service_seq))
    assert setup.assembled.service == "skill_creator"
    assert setup.assembled.effort == "low" and not setup.assembled.request.tools
    (item,) = setup.assembled.request.input_items
    text = item["content"][0]["text"]
    assert item["role"] == "developer" and "skill_proposal" in text and "{{" not in text
    prompt = setup.assembled.request.base.prompt
    assert "service turn of the built-in command" in prompt
    assert "IMPOSTOR TEXT" not in prompt and "short notes" in prompt
    assert "teach me limits" in prompt  # the journal of the dialogue is the whole session
    assert not setup.labels.labels("task") or "Label table" in prompt

    after = await _message(pg_db, learner_id, session_id, text="what is a limit?")
    later = await source.assemble(_request(learner_id, session_id, after))
    assert later.assembled.service is None
    assert "short notes" not in later.assembled.request.base.prompt
    assert "what is a limit?" in later.assembled.request.base.prompt
