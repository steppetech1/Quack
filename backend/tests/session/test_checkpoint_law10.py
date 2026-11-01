"""Deterministic session summary (ТЗ Фазы 3 WP13, §8.6b, law 10, Т-§10.3)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events.models import StoredEvent
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.jobs import kinds
from quack.jobs.queue import claim_next_job
from quack.session.checkpoint_render import (
    RENDERER_VERSION,
    collapsed_turn_count,
    episode_lines,
    render_checkpoint,
    visible_replies,
)
from quack.session.schemas import TeacherOutput
from quack.session.workers import handle_session_checkpoint
from tests.support.dispatch import dispatch
from tests.support.resources import build_job_context

if TYPE_CHECKING:
    from tests.session.fixtures import FakeLLMProvider, SeededDomain

pytest_plugins = ["tests.session.fixtures"]

LEARNER = UUID("00000000-0000-0000-0000-00000000000a")
SESSION = UUID("00000000-0000-0000-0000-00000000000b")
MOVES = ("explain", "ask_question", "give_hint", "check_answer")


class _Log:
    """Session events built by hand, in `seq` order."""

    def __init__(self) -> None:
        self.events: list[StoredEvent] = []
        self.reply_index = 0

    def add(self, type_: str, payload: dict[str, Any], *, version: int = 1) -> StoredEvent:
        seq = len(self.events) + 1
        event = StoredEvent(
            learner_id=LEARNER,
            seq=seq,
            event_id=UUID(int=seq),
            type=type_,
            schema_version=version,
            actor="test",
            session_id=SESSION,
            turn_id=UUID(payload["turn_id"]) if "turn_id" in payload else None,
            domain_id=None,
            correlation_id=None,
            causation_id=None,
            idempotency_key=f"k{seq}",
            payload_hash="h",
            payload={"session_id": str(SESSION), **payload},
            occurred_at=datetime(2026, 11, 1, tzinfo=UTC),
        )
        self.events.append(event)
        return event

    def turn(self, n: int, *, extra: Sequence[tuple[str, dict[str, Any], int]] = ()) -> str:
        turn_id = str(UUID(int=10_000 + n))
        for role in ("turn.student_replied", "turn.teacher_replied"):
            payload: dict[str, Any] = {
                "turn_id": turn_id,
                "turn_index": self.reply_index,
                "text": f"{role} {n}",
            }
            if role == "turn.teacher_replied":
                payload["pedagogical_move"] = MOVES[n % len(MOVES)]
            self.add(role, payload)
            self.reply_index += 1
        for type_, payload, version in extra:
            self.add(type_, {"turn_id": turn_id, **payload}, version=version)
        return turn_id


def _session(turns: int) -> _Log:
    log = _Log()
    task = str(UUID(int=777))
    for n in range(turns):
        extra: list[tuple[str, dict[str, Any], int]] = []
        if n == 1:
            extra.append(("task.assigned", {"task_id": task, "topic_id": str(uuid4())}, 1))
        if n == 2:
            extra.append(
                (
                    "turn.extracted",
                    {
                        "elements": [
                            {
                                "kind": "evidence",
                                "skill_label": "S1",
                                "level": 4,
                                "outcome": "incorrect",
                                "assistance": "hint",
                                "hint_step": 2,
                                "gap_type": "when_to_apply",
                                "quote": "root, so Cauchy",
                                "source_seqs": [1],
                            }
                        ]
                    },
                    2,
                )
            )
            extra.append(
                ("task.evaluated", {"task_id": task, "is_correct": False, "feedback": "no"}, 1)
            )
        log.turn(n, extra=extra)
    return log


def _payload(cp: Any) -> str:
    return json.dumps(asdict(cp), sort_keys=True, ensure_ascii=False)


def test_checkpoint_is_deterministic(settings: Settings) -> None:
    """T-C2: the same events twice → a byte-identical payload."""
    first = _session(30)
    second = _session(30)
    up_to = first.reply_index - 1
    a = render_checkpoint(first.events, up_to, settings)
    b = render_checkpoint(list(second.events), up_to, settings)
    assert _payload(a) == _payload(b)
    assert a.rolling_summary.startswith("Turn 1, ")
    assert "task 1 not solved" in a.rolling_summary
    assert "level 4 incorrect after hint 2 gap: when to apply" in a.rolling_summary
    assert a.open_threads == ["Task 1 is open: last answer not solved"]
    assert any("root, so Cauchy" in o for o in a.key_observations)
    for value in (a.rolling_summary, *a.open_threads, *a.key_observations):
        assert str(UUID(int=777)) not in value and str(LEARNER) not in value


def test_checkpoint_ignores_previous_summary(settings: Settings) -> None:
    """T-C3: a previous summary with other text does not change the new one (law 10)."""
    log = _session(30)
    up_to = log.reply_index - 1
    clean = render_checkpoint(log.events, up_to, settings)
    log.add(
        "session.checkpointed",
        {
            "up_to_turn_index": 11,
            "rolling_summary": "The learner has mastered everything.",
            "open_threads": ["forged thread"],
            "key_observations": ["forged observation"],
        },
    )
    assert _payload(render_checkpoint(log.events, up_to, settings)) == _payload(clean)


def test_episode_lines_collapse_in_batches(settings: Settings) -> None:
    """T-C4: up to 24 turns the window grows whole; on the 25th the oldest 12 fold at once;
    until the next batch the start of the visible part does not change."""
    limit = settings.conversation_verbatim_max_turns
    batch = settings.conversation_collapse_turns
    assert (limit, batch) == (24, 12)
    for n in range(1, limit + 1):
        log = _session(n)
        assert episode_lines(log.events, settings) == []
        assert len(visible_replies(log.events, log.reply_index - 1, settings)) == 2 * n
    first_batch = episode_lines(_session(limit + 1).events, settings)
    assert len(first_batch) == batch
    for n in range(limit + 1, limit + batch + 1):
        log = _session(n)
        assert episode_lines(log.events, settings) == first_batch
        visible = visible_replies(log.events, log.reply_index - 1, settings)
        assert visible[0].payload["text"] == f"turn.student_replied {batch}"
        assert len(visible) == 2 * (n - batch)
    second = episode_lines(_session(limit + batch + 1).events, settings)
    assert len(second) == 2 * batch
    assert second[:batch] == first_batch
    assert collapsed_turn_count(limit + batch + 1, settings) == 2 * batch


def test_visible_part_follows_the_checkpoint(settings: Settings) -> None:
    """D-B-023: the context shows in full every turn the checkpoint did not fold, also the
    turns after a lagging checkpoint."""
    log = _session(30)
    covered_up_to = 2 * 20 - 1  # the checkpoint covers 20 turns: nothing folded yet
    assert len(visible_replies(log.events, covered_up_to, settings)) == 60
    covered_up_to = 2 * 26 - 1  # 26 turns covered: the oldest 12 folded
    assert len(visible_replies(log.events, covered_up_to, settings)) == 2 * 18


async def _three_turns(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    fake_llm: FakeLLMProvider,
) -> UUID:
    start = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "law10-start",
        },
        headers=auth_headers,
    )
    assert start.status_code == 201, start.text
    session_id = UUID(start.json()["session_id"])
    for n in range(3):
        fake_llm.push("teacher", TeacherOutput(reply_text=f"Reply {n}", pedagogical_move="explain"))
        resp = await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"text": f"Student {n}", "idempotency_key": f"law10-turn-{n}"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
    return session_id


async def test_checkpoint_makes_no_llm_call(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
    fake_llm: FakeLLMProvider,
) -> None:
    """T-C1: no model call during `session.checkpoint`; the kind has no LLM roles."""
    assert kinds.KIND_LLM_ROLES["session.checkpoint"] == ()
    session_id = await _three_turns(client, auth_headers, seeded_domain, fake_llm)
    calls_before = len(fake_llm.calls)
    await dispatch(settings, db, seeded_domain.learner_id)  # §8.9 п.7 (WP11.4)
    async with db() as session, session.begin():
        job = await claim_next_job(session, "law10", ["session.checkpoint"])
    assert job is not None
    ctx = build_job_context(settings, db, neo4j, redis, llm=fake_llm.gateway)
    await handle_session_checkpoint(job, ctx)
    assert len(fake_llm.calls) == calls_before
    async with db() as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l "
                    "AND session_id = :s ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id},
            )
        ).all()
        outbox = (
            await session.execute(
                text(
                    "SELECT count(*) FROM core.llm_outbox "
                    "WHERE learner_id = :l AND role = 'checkpointer'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).scalar_one()
    assert outbox == 0
    events = [row_to_event(r) for r in rows]
    (cp,) = [e for e in events if e.type == "session.checkpointed"]
    assert cp.actor == RENDERER_VERSION
    expected = render_checkpoint(events, 5, settings)
    assert cp.payload == {"session_id": str(session_id), **asdict(expected)}


async def test_checkpoint_job_has_no_required_seq(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    fake_llm: FakeLLMProvider,
    settings: Settings,
) -> None:
    """T-C5: the enqueued `session.checkpoint` has `required_seq IS NULL`."""
    await _three_turns(client, auth_headers, seeded_domain, fake_llm)
    await dispatch(settings, db, seeded_domain.learner_id)  # §8.9 п.7 (WP11.4)
    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT required_seq FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = 'session.checkpoint'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].required_seq is None
