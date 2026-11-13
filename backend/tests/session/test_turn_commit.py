"""ТЗ Ф4 §8.9 T-TT7 and §8.x T-EV4 (stream B part): `turn.committed` with the events the turn
produced (`task.created`, `agent.requested`, `hub.deadline_set`, `hint.shown`, `task.closed`)
— one `append_events` call — and the `ops.turn_manifests` row in the same transaction; a
failure before the end writes nothing; a repeat writes nothing new (law 5). Labels are
translated to ids by code before the write (Т-§4.5, WP13.3); an unknown label writes nothing.
A deadline is written only from the learner's words (law 6, П-8.1, D-B-149)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.values import AnswerSpec, MathValue
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.llm.streaming import StreamUsage
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task, get_task
from quack.session.teacher_output import TeacherOutputV2
from quack.session.turn_commit import (
    AnsweredAction,
    LearnerText,
    TurnManifestRow,
    commit_turn,
    turn_batch,
)
from quack.session.turn_graph import TurnToCommit
from tests.db.fixtures import create_learner

SETTINGS = Settings()
SKILL, TOPIC, HUB, MISCONCEPTION = uuid4(), uuid4(), uuid4(), uuid4()
NUMBER = {"type": "number", "value": {"latex": "\\frac{1}{3}", "sympy": "1/3"}}
TEXT = "Every term is a third of the one before it."
QUOTE = "colloquium on December 1st at 9"
LEARNER = [LearnerText(seq=1, text=f"btw I have a {QUOTE} with proofs, help")]
DOMAIN = uuid4()


def _deadline(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "hub_label": "H1",
        "new_hub_name": None,
        "deadline_at": "2026-12-01T09:00:00+00:00",
        "coverage": ["T1"],
        "quote": QUOTE,
        "control_type": "colloquium_proofs",
    }
    return {**base, **fields}


def _labels() -> LabelMap:
    return LabelMap.build(
        SETTINGS, skills=[SKILL], topics=[TOPIC], hubs=[HUB], misconceptions=[MISCONCEPTION]
    )


def _output(**markup: Any) -> TeacherOutputV2:
    base: dict[str, Any] = {
        "tasks": [
            {
                "element_id": "e1",
                "tests": [{"skill": "S1", "level": 3}],
                "methodNamed": False,
                "answer": NUMBER,
            }
        ],
        "elements": [
            {
                "element_id": "e1",
                "kind": "short_answer",
                "prompt": "What is the sum?",
                "ask_confidence": False,
            }
        ],
        "exposures": [{"skill": "S1", "kind": "explained", "approach": "none"}],
        "targetsMisconception": "M1",
        "planUpdate": {
            "completed_steps": ["definition"],
            "current_step": "sum of a geometric series",
            "next_steps": [],
            "branches": [],
            "cancel_requests": [],
        },
        "agentRequests": [
            {
                "kind": "viz",
                "params": {
                    "skill": "S1",
                    "level": None,
                    "topic": None,
                    "task": None,
                    "misconception": None,
                    "description": "partial sums on a number line",
                },
                "needed_in_session": True,
            }
        ],
        "correctionsAcknowledged": [],
        "decisionReason": "practice after the explanation",
        "deadline": _deadline(),
    }
    return TeacherOutputV2.model_validate(
        {
            "p1_head": {
                "intent": "explain",
                "targetSkills": ["S1"],
                "verdict_stance": None,
                "branchType": None,
                "topicLabel": None,
                "approach": "example_first",
            },
            "p2_body": {
                "blocks": [
                    {"block_id": "b1", "text": TEXT, "element_id": None},
                    {"block_id": "b2", "text": None, "element_id": "e1"},
                ]
            },
            "p3_markup": {**base, **markup},
        }
    )


def _turn(output: TeacherOutputV2 | None = None) -> TurnToCommit:
    return TurnToCommit(
        output=output or _output(),
        text=TEXT,
        role="teacher",
        model="muse-wonder-1.0",
        effort="low",
        degraded_mode=None,
        guard_retries=1,
        usage=StreamUsage(input_tokens=100, output_tokens=40),
    )


def _manifest(learner_id: UUID, session_id: UUID, labels: LabelMap) -> TurnManifestRow:
    return TurnManifestRow(
        manifest_id=uuid4(),
        learner_id=learner_id,
        session_id=session_id,
        prompt_version="teacher@2",
        doctrine_version=None,
        renderer_version="renderer@1",
        role="teacher",
        model="muse-wonder-1.0",
        effort="low",
        projected_seq=0,
        overlay_seqs=[1],
        label_table=labels.table(),
        degraded_mode=None,
    )


async def _events(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, type, payload, idempotency_key FROM core.events "
        "WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )


async def _manifests(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM ops.turn_manifests WHERE learner_id = %(l)s", {"l": learner_id}
    )


def _batch(
    learner_id: UUID,
    output: TeacherOutputV2 | None = None,
    *,
    session_id: UUID | None = None,
    answered: AnsweredAction | None = None,
    learner_texts: list[LearnerText] = LEARNER,
    learner_timezone: str | None = None,
) -> Any:
    labels = _labels()
    return turn_batch(
        _turn(output),
        labels,
        manifest=_manifest(learner_id, session_id or uuid4(), labels),
        action_seq=1,
        domain_id=DOMAIN,
        settings=SETTINGS,
        answered=answered,
        learner_texts=learner_texts,
        learner_timezone=learner_timezone,
    )


def _bare(**markup: Any) -> TeacherOutputV2:
    """A turn without tasks, requests or deadline unless given."""
    output = _output(
        **{"tasks": [], "elements": [], "agentRequests": [], "deadline": None, **markup}
    )
    blocks = [b for b in output.p2_body.blocks if b.text is not None]
    return output.model_copy(
        update={"p2_body": output.p2_body.model_copy(update={"blocks": blocks})}
    )


async def test_turn_atomic_and_manifest(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    batch = _batch(learner_id)

    seq = await commit_turn(pg_db, batch, learner_id=learner_id)

    events = await _events(pg_db, learner_id)
    assert [e["type"] for e in events] == [
        "task.created",
        "turn.committed",
        "agent.requested",
        "hub.deadline_set",
    ]
    assert [e["seq"] for e in events] == [seq - 1, seq, seq + 1, seq + 2]
    turn = events[1]["payload"]
    assert turn["action_seq"] == 1 and turn["text"] == TEXT and turn["guard_retries"] == 1
    assert turn["head"]["target_skill_ids"] == [str(SKILL)]
    assert turn["markup"]["exposures"][0]["skill_id"] == str(SKILL)
    assert turn["markup"]["targets_misconception_id"] == str(MISCONCEPTION)
    [task] = turn["markup"]["tasks"]
    assert task["tests"] == [{"skill_id": str(SKILL), "new_skill": None, "level": 3}]
    assert events[0]["payload"]["task_id"] == task["task_id"]
    assert events[0]["payload"]["source"] == "teacher"
    assert await get_task(pg_db, learner_id=learner_id, task_id=UUID(task["task_id"]))
    request = events[2]["payload"]
    assert request["params"]["skill_id"] == str(SKILL) and request["needed_in_session"]
    assert turn["markup"]["agent_requests"][0]["request_id"] == request["request_id"]
    deadline = events[3]["payload"]
    assert deadline["hub_id"] == str(HUB) and deadline["source"] == "turn"
    sources = await pg_db.fetch_one(
        "SELECT source_refs FROM core.events WHERE learner_id = %(l)s AND seq = %(s)s",
        {"l": learner_id, "s": events[3]["seq"]},
    )
    assert sources == {"source_refs": [{"seq": 1}]}
    assert turn["markup"]["deadline"]["coverage_topic_ids"] == [str(TOPIC)]

    [manifest] = await _manifests(pg_db, learner_id)
    assert manifest["manifest_id"] == batch.manifest.manifest_id
    assert str(manifest["manifest_id"]) == turn["manifest_id"]
    assert manifest["prompt_version"] == "teacher@2" and manifest["effort"] == "low"
    assert manifest["label_table"]["S1"] == str(SKILL)


async def test_failure_before_the_end_writes_nothing(pg_db: Db) -> None:
    """The task document cannot be stored (its id is taken) → no event, no manifest."""
    learner_id, other = await create_learner(pg_db), await create_learner(pg_db)
    batch = _batch(learner_id)
    [new_task] = batch.tasks
    await create_task(
        pg_db,
        learner_id=other,
        task_id=new_task.task_id,
        family_id=new_task.task_id,
        source="teacher",
        condition=TaskCondition(text="taken", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=SKILL, level=3)],
            method_named=False,
            answer=AnswerSpec(type="number", value=MathValue(latex="1", sympy="1")),
        ),
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{new_task.task_id}",
    )

    with pytest.raises(Exception):  # noqa: B017 — the unique violation of the store
        await commit_turn(pg_db, batch, learner_id=learner_id)

    assert await _events(pg_db, learner_id) == []
    assert await _manifests(pg_db, learner_id) == []


async def test_manifest_failure_writes_no_turn(pg_db: Db) -> None:
    """The manifest row cannot be stored (`prompt_version` NOT NULL) → no turn, no task."""
    learner_id = await create_learner(pg_db)
    batch = _batch(learner_id)
    broken = replace(batch, manifest=replace(batch.manifest, prompt_version=None))

    with pytest.raises(Exception):  # noqa: B017 — the NOT NULL violation of the store
        await commit_turn(pg_db, broken, learner_id=learner_id)

    assert await _events(pg_db, learner_id) == []
    [new_task] = batch.tasks
    assert await get_task(pg_db, learner_id=learner_id, task_id=new_task.task_id) is None


async def test_repeat_writes_nothing_new(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    batch = _batch(learner_id)

    first = await commit_turn(pg_db, batch, learner_id=learner_id)
    second = await commit_turn(pg_db, batch, learner_id=learner_id)

    assert first == second
    assert len(await _events(pg_db, learner_id)) == 4
    assert len(await _manifests(pg_db, learner_id)) == 1


async def test_unknown_label_writes_nothing(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    bad = _output(deadline=_deadline(coverage=["T7"]))

    with pytest.raises(UnknownLabelError):
        await commit_turn(pg_db, _batch(learner_id, bad), learner_id=learner_id)

    assert await _events(pg_db, learner_id) == []
    assert await _manifests(pg_db, learner_id) == []


def test_turn_without_side_effects_is_one_event() -> None:
    batch = _batch(uuid4(), _bare())

    assert [e.type for e in batch.events] == ["turn.committed"]
    assert batch.tasks == []


# --- deadline from the conversation (D-B-149 п. г) ---


async def test_new_hub_deadline_from_learner_words(pg_db: Db) -> None:
    """A new hub: `hub.created` (from the learner's request), its coverage with the levels of
    the control type (Т-§4.8, Т-§8.5), then the date; all point at the learner message."""
    learner_id = await create_learner(pg_db)
    output = _bare(deadline=_deadline(hub_label=None, new_hub_name="Calculus colloquium"))

    await commit_turn(pg_db, _batch(learner_id, output), learner_id=learner_id)

    events = await _events(pg_db, learner_id)
    assert [e["type"] for e in events] == [
        "turn.committed",
        "hub.created",
        "hub.topics_changed",
        "hub.deadline_set",
    ]
    created, topics, deadline = (e["payload"] for e in events[1:])
    assert created["domain_id"] == str(DOMAIN) and created["name"] == "Calculus colloquium"
    assert (created["kind"], created["origin"]) == ("request", "learner")
    assert created["control_type"] == "colloquium_proofs"
    assert topics == {
        "hub_id": created["hub_id"],
        "topics": [{"topic_id": str(TOPIC), "required_levels": [2, 3, 6]}],
    }
    assert deadline["hub_id"] == created["hub_id"]
    assert events[0]["payload"]["markup"]["deadline"]["hub_id"] == created["hub_id"]


def test_unknown_control_type_gets_no_goal_levels() -> None:
    output = _bare(deadline=_deadline(hub_label=None, new_hub_name="Exam", control_type=None))
    batch = _batch(uuid4(), output)

    created = next(e for e in batch.events if e.type == "hub.created")
    topics = next(e for e in batch.events if e.type == "hub.topics_changed")
    assert created.payload.control_type == "no_goal"
    assert topics.payload.topics[0].required_levels == SETTINGS.control_type_levels["no_goal"]


@pytest.mark.parametrize(
    "learner_texts",
    [[], [LearnerText(seq=1, text="no date was said here")]],
    ids=["no-learner-message", "quote-not-in-message"],
)
def test_ungrounded_deadline_is_not_written(learner_texts: list[LearnerText]) -> None:
    """The model's quote is not the learner's words → no hub event; the turn record has no
    deadline either."""
    batch = _batch(uuid4(), _bare(deadline=_deadline()), learner_texts=learner_texts)

    assert [e.type for e in batch.events] == ["turn.committed"]
    assert batch.events[0].payload.markup.deadline is None


def test_teacher_words_are_not_a_source() -> None:
    """An empty quote matches every text: it is never a source."""
    batch = _batch(uuid4(), _bare(deadline=_deadline(quote="  ")))

    assert [e.type for e in batch.events] == ["turn.committed"]


@pytest.mark.parametrize(
    ("deadline_at", "timezone", "expected"),
    [
        ("2026-12-01T09:00:00", "Europe/Moscow", "2026-12-01T06:00:00+00:00"),
        ("2026-12-01T09:00:00+03:00", "America/New_York", "2026-12-01T06:00:00+00:00"),
        ("2026-12-01T09:00:00Z", None, "2026-12-01T09:00:00+00:00"),
    ],
    ids=["local-in-learner-zone", "offset-wins", "utc"],
)
def test_deadline_time_zone(deadline_at: str, timezone: str | None, expected: str) -> None:
    """A date without an offset is the learner's local time (Т-§14.6: the zone of
    `core.learners.settings`); an explicit offset is kept."""
    batch = _batch(
        uuid4(), _bare(deadline=_deadline(deadline_at=deadline_at)), learner_timezone=timezone
    )

    [event] = [e for e in batch.events if e.type == "hub.deadline_set"]
    assert event.payload.deadline_at_ms == int(datetime.fromisoformat(expected).timestamp() * 1000)


@pytest.mark.parametrize("timezone", [None, "Not/A_Zone"], ids=["no-zone", "unknown-zone"])
def test_local_deadline_without_learner_zone_is_not_written(timezone: str | None) -> None:
    batch = _batch(
        uuid4(),
        _bare(deadline=_deadline(deadline_at="2026-12-01T09:00:00")),
        learner_timezone=timezone,
    )

    assert [e.type for e in batch.events] == ["turn.committed"]
    assert batch.events[0].payload.markup.deadline is None


# --- help and closing of tasks (D-B-149 п. д) ---


async def _types_of(db: Db, learner_id: UUID, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in await _events(db, learner_id) if e["type"] == kind]


async def test_hint_steps_counted_by_code(pg_db: Db) -> None:
    learner_id, session_id, task_id = await create_learner(pg_db), uuid4(), uuid4()
    answered = AnsweredAction(kind="hint_request", task_id=task_id)

    batches = [
        _batch(learner_id, _bare(), session_id=session_id, answered=answered) for _ in range(4)
    ]
    for batch in batches:
        await commit_turn(pg_db, batch, learner_id=learner_id)
    await commit_turn(pg_db, batches[-1], learner_id=learner_id)  # a repeat of the last turn

    shown = await _types_of(pg_db, learner_id, "hint.shown")
    assert [h["step"] for h in shown] == [1, 2, 3, 3]
    assert {h["source"] for h in shown} == {"teacher"}
    other = _batch(learner_id, _bare(), answered=answered)  # another session starts at 1
    await commit_turn(pg_db, other, learner_id=learner_id)
    assert (await _types_of(pg_db, learner_id, "hint.shown"))[-1]["step"] == 1


@pytest.mark.parametrize(
    ("answered", "how"),
    [
        (AnsweredAction(kind="solution_request", task_id=SKILL), "solution_shown"),
        (AnsweredAction(kind="check_skip", task_id=SKILL), "skipped"),
        (AnsweredAction(kind="answer", task_id=SKILL, solved=True), "solved"),
        (AnsweredAction(kind="answer", task_id=SKILL, solved=False), None),
    ],
    ids=["solution", "skip", "solved", "not-solved"],
)
async def test_task_closed_by_answered_action(
    pg_db: Db, answered: AnsweredAction, how: str | None
) -> None:
    learner_id = await create_learner(pg_db)

    await commit_turn(pg_db, _batch(learner_id, _bare(), answered=answered), learner_id=learner_id)
    await commit_turn(pg_db, _batch(learner_id, _bare(), answered=answered), learner_id=learner_id)

    closed = await _types_of(pg_db, learner_id, "task.closed")
    expected = [] if how is None else [{"task_id": str(SKILL), "how": how}]
    assert closed == expected


# --- session plan document (WP13.6, part of T-TT9; D-B-154) ---


async def _plan(db: Db, session_id: UUID) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT learner_id, plan, status FROM content.session_plans WHERE session_id = %(s)s",
        {"s": session_id},
    )


def _plan_update(current: str, done: list[str]) -> dict[str, Any]:
    return {
        "completed_steps": done,
        "current_step": current,
        "next_steps": ["ratio test"],
        "branches": [{"description": "why does 1/3 converge", "type": "curiosity"}],
        "cancel_requests": [],
    }


async def test_turn_writes_the_session_plan(pg_db: Db) -> None:
    """`planUpdate` of every turn → `content.session_plans` of the session, in the turn's
    transaction; the latest turn's plan is the document."""
    learner_id, session_id = await create_learner(pg_db), uuid4()
    first = _bare(planUpdate=_plan_update("definition", []))
    second = _bare(planUpdate=_plan_update("geometric sum", ["definition"]))

    await commit_turn(
        pg_db, _batch(learner_id, first, session_id=session_id), learner_id=learner_id
    )
    row = await _plan(pg_db, session_id)
    assert row is not None and row["learner_id"] == learner_id and row["status"] == "active"
    assert row["plan"]["current_step"] == "definition"

    batch = _batch(learner_id, second, session_id=session_id)
    await commit_turn(pg_db, batch, learner_id=learner_id)
    await commit_turn(pg_db, batch, learner_id=learner_id)  # a repeat changes nothing

    row = await _plan(pg_db, session_id)
    assert row is not None
    assert row["plan"] == {
        "completed_steps": ["definition"],
        "current_step": "geometric sum",
        "next_steps": ["ratio test"],
        "branches": [{"description": "why does 1/3 converge", "type": "curiosity"}],
        "cancel_request_ids": [],
    }


async def test_failed_turn_leaves_the_plan(pg_db: Db) -> None:
    learner_id, session_id = await create_learner(pg_db), uuid4()
    await commit_turn(
        pg_db,
        _batch(learner_id, _bare(planUpdate=_plan_update("definition", [])), session_id=session_id),
        learner_id=learner_id,
    )
    batch = _batch(learner_id, _bare(planUpdate=_plan_update("later", [])), session_id=session_id)
    broken = replace(batch, manifest=replace(batch.manifest, prompt_version=None))

    with pytest.raises(Exception):  # noqa: B017 — the NOT NULL violation of the manifest
        await commit_turn(pg_db, broken, learner_id=learner_id)

    row = await _plan(pg_db, session_id)
    assert row is not None and row["plan"]["current_step"] == "definition"


async def test_plan_of_another_learner_session_is_not_taken(pg_db: Db) -> None:
    """A session id is the learner's: the plan row of another learner is never overwritten."""
    owner, other, session_id = await create_learner(pg_db), await create_learner(pg_db), uuid4()
    await commit_turn(
        pg_db,
        _batch(owner, _bare(planUpdate=_plan_update("mine", [])), session_id=session_id),
        learner_id=owner,
    )

    with pytest.raises(LookupError):
        await commit_turn(
            pg_db,
            _batch(other, _bare(planUpdate=_plan_update("theirs", [])), session_id=session_id),
            learner_id=other,
        )

    row = await _plan(pg_db, session_id)
    assert row is not None and row["learner_id"] == owner
    assert await _events(pg_db, other) == []


async def test_completed_steps_are_history(pg_db: Db) -> None:
    """П-4.6: done steps are a history that does not change — a turn that leaves a done step
    out of `completed_steps` does not erase it; new ones are added in order, once."""
    learner_id, session_id = await create_learner(pg_db), uuid4()
    turns = [
        _plan_update("geometric sum", ["definition", "example"]),
        _plan_update("ratio test", ["geometric sum"]),
        _plan_update("root test", ["example", "ratio test"]),
    ]
    for plan in turns:
        batch = _batch(learner_id, _bare(planUpdate=plan), session_id=session_id)
        await commit_turn(pg_db, batch, learner_id=learner_id)

    row = await _plan(pg_db, session_id)
    assert row is not None
    assert row["plan"]["completed_steps"] == [
        "definition",
        "example",
        "geometric sum",
        "ratio test",
    ]
    assert row["plan"]["current_step"] == "root test"


def test_known_check_without_topic_is_not_requested() -> None:
    """`check.known_claim` reads the skills of a topic (WP8.2): a `known_check` request
    without a topic label is dropped, the others stay (review B of #117)."""
    params = {
        "skill": None,
        "level": None,
        "topic": None,
        "task": None,
        "misconception": None,
        "description": None,
    }
    requests = [
        {"kind": "known_check", "params": params, "needed_in_session": True},
        {"kind": "known_check", "params": {**params, "topic": "T1"}, "needed_in_session": False},
    ]
    batch = _batch(uuid4(), _bare(agentRequests=requests))

    asked = [e.payload for e in batch.events if e.type == "agent.requested"]
    assert [(a.kind, a.params.topic_id) for a in asked] == [("known_check", TOPIC)]
    [turn] = [e.payload for e in batch.events if e.type == "turn.committed"]
    assert [r.params.topic_id for r in turn.markup.agent_requests] == [TOPIC]


def test_split_topic_requests_the_split_of_a_known_topic_only() -> None:
    """`intent = split_topic` with a label of the table writes `agent.requested {topic_split}`
    keyed by the turn and the topic after `turn.committed`; an unknown label writes nothing and
    the turn goes on (WP8.2, D-B-213)."""

    def split(label: str | None) -> list[Any]:
        output = _bare()
        head = output.p1_head.model_copy(update={"intent": "split_topic", "topic_label": label})
        return _batch(uuid4(), output.model_copy(update={"p1_head": head})).events

    events = split("T1")
    assert [e.type for e in events] == ["turn.committed", "agent.requested"]
    asked = events[1]
    assert (asked.payload.kind, asked.payload.params.topic_id) == ("topic_split", TOPIC)
    assert asked.idempotency_key.endswith(f":agent:topic_split:{TOPIC}")
    assert [e.type for e in split("T9")] == ["turn.committed"]
    assert [e.type for e in split(None)] == ["turn.committed"]


def test_what_the_guard_leaves_the_write_can_resolve() -> None:
    """The guard checks every label `turn_batch` turns into an id: after it drops the unknown
    ones (the turn after the retries) the write raises no `UnknownLabelError`, which used to fail
    a turn whose text was already shown (`cancel_requests: ["R9"]`)."""
    from quack.session.guards import drop_unknown_markup_labels, markup_violations

    labels = LabelMap.build(
        SETTINGS,
        skills=[SKILL],
        topics=[TOPIC],
        hubs=[HUB],
        misconceptions=[MISCONCEPTION],
        requests=[uuid4()],
    )
    output = _output(
        planUpdate={
            "completed_steps": [],
            "current_step": "s",
            "next_steps": [],
            "branches": [],
            "cancel_requests": ["R1", "R9"],
        },
        deadline=_deadline(coverage=["T1", "T7"]),
        elements=[
            {
                "element_id": "e1",
                "kind": "short_answer",
                "prompt": "What is the sum?",
                "ask_confidence": False,
            },
            {"element_id": "e2", "kind": "material_link", "chunk": "P9", "page": None},
            {"element_id": "e3", "kind": "card_series", "cards": ["C1"]},
        ],
    )
    markup = output.p3_markup

    def violations(m: Any) -> Any:
        return markup_violations(m, skill_labels={"S1"}, misconception_labels={"M1"}, labels=labels)

    assert violations(markup)
    manifest = _manifest(uuid4(), uuid4(), labels)

    def write(markup_: Any) -> Any:
        settled = output.model_copy(update={"p3_markup": markup_})
        return turn_batch(
            _turn(settled),
            labels,
            manifest=manifest,
            action_seq=1,
            domain_id=DOMAIN,
            settings=SETTINGS,
            learner_texts=LEARNER,
        )

    with pytest.raises(UnknownLabelError):
        write(markup)
    settled = drop_unknown_markup_labels(
        markup, skill_labels={"S1"}, misconception_labels={"M1"}, labels=labels
    )
    assert violations(settled) == []
    batch = write(settled)
    assert [e.type for e in batch.events if e.type == "hub.deadline_set"] == ["hub.deadline_set"]
