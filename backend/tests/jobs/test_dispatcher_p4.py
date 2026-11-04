"""ТЗ Ф4 WP7.1–7.3 on a database: the dispatcher on `Db` — episodes, coverage, rows that read
data beyond the event, cancellation, counters, idempotency and races. T-D1 (the rows in action),
T-D2, T-D3, T-D4 (through the dispatcher), D-A-115. Needs Postgres only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.jobs import queue_p4 as q
from quack.jobs.coverage import mark_covered
from quack.jobs.dispatcher_p4 import DispatcherP4
from tests.db.fixtures import create_learner
from tests.events.samples import sample_payload

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SETTINGS = Settings(episode_max_turns=50, episode_idle_min=10, graph_hygiene_every_topics=3)
EXTRACTOR = "extractor@3"


class Log:
    """Writes events of one learner the way the other streams do (`append_events`)."""

    def __init__(self, db: Db, learner: UUID, session: UUID | None = None) -> None:
        self.db, self.learner, self.session = db, learner, session or uuid4()
        self.domain = uuid4()

    async def put(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        at: int = 0,
        session: UUID | bool | None = True,
    ) -> StoredEvent:
        """`at` — minutes after T0. `session=False` — an event outside any session."""
        sid = self.session if session is True else (session or None)
        data = {**sample_payload(event_type), **(payload or {})}
        if event_type.startswith("session."):
            data["session_id"] = str(sid)
        if event_type == "session.started":
            data["domain_id"] = str(self.domain)
        (stored,) = await append_events(
            self.db,
            self.learner,
            [
                NewEvent(
                    type=event_type,
                    payload=data,
                    actor="code",
                    idempotency_key=str(uuid4()),
                    session_id=sid,
                    domain_id=self.domain,
                    occurred_at=T0 + timedelta(minutes=at),
                )
            ],
        )
        return stored


def dispatcher(db: Db, *, now: datetime = T0, settings: Settings = SETTINGS) -> DispatcherP4:
    return DispatcherP4(settings, db, clock=lambda: now)


async def drain(d: DispatcherP4, learner: UUID) -> int:
    """Dispatch until the learner is caught up (the episodes the dispatcher itself closes are
    events too)."""
    total = 0
    while n := await d.process_learner(learner):
        total += n
    return total


async def events_of(db: Db, learner: UUID, event_type: str) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, payload, idempotency_key, session_id, occurred_at FROM core.events "
        "WHERE learner_id = %(l)s AND type = %(t)s ORDER BY seq",
        {"l": learner, "t": event_type},
    )


async def jobs_of(db: Db, learner: UUID | None = None, kind: str | None = None) -> list[Any]:
    return await db.fetch_all(
        "SELECT * FROM ops.jobs WHERE (%(l)s::uuid IS NULL OR learner_id = %(l)s) "
        "AND (%(k)s::text IS NULL OR kind = %(k)s) ORDER BY job_key",
        {"l": learner, "k": kind},
    )


async def counts(db: Db) -> tuple[int, int, int]:
    """(events, jobs, coverage rows) — to see that a replay adds nothing."""
    out = []
    for table in ("core.events", "ops.jobs", "ops.coverage"):
        row = await db.fetch_one(f"SELECT count(*) AS n FROM {table}")
        assert row is not None
        out.append(int(row["n"]))
    return out[0], out[1], out[2]


async def reset_mark(db: Db, learner: UUID) -> None:
    await db.execute(
        "UPDATE ops.dispatcher_offsets SET last_dispatched_seq = 0, counters = '{}'::jsonb "
        "WHERE learner_id = %(l)s",
        {"l": learner},
    )


# --- episodes (T-D2) ---------------------------------------------------------------------


async def test_task_closed_closes_the_episode_and_the_content_goes_to_extraction(
    jobs_db: Db,
) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    started = await log.put("session.started")
    await log.put("message.sent")
    closed = await log.put("task.closed")
    await drain(dispatcher(jobs_db), learner)

    (episode,) = await events_of(jobs_db, learner, "episode.closed")
    assert episode["payload"] == {
        "from_seq": started.seq,
        "to_seq": closed.seq,
        "has_content": True,
    }
    assert episode["idempotency_key"] == f"episode:{log.session}:{closed.seq}"
    assert episode["session_id"] == log.session

    (job,) = await jobs_of(jobs_db, learner, "extract.episode")
    assert job["required_seq"] == episode["seq"] and job["priority_class"] == "P2"
    assert job["payload"]["from_seq"] == started.seq and job["payload"]["to_seq"] == closed.seq
    assert job["session_id"] == log.session and not job["visible"]
    assert len(await jobs_of(jobs_db, learner, "analyst.run")) == 1  # the same task.closed


async def test_episode_boundaries(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    first = await log.put("message.sent")
    await log.put("turn.committed")
    changed = sample_payload("turn.committed")
    changed["head"]["intent"] = "retrieval"
    await log.put("message.sent")
    turn = await log.put("turn.committed", changed)
    ended = await log.put("session.ended")
    await drain(dispatcher(jobs_db), learner)
    spans = [
        (e["payload"]["from_seq"], e["payload"]["to_seq"])
        for e in await events_of(jobs_db, learner, "episode.closed")
    ]
    # The intent changed on the second turn.committed: the first episode ends before that turn.
    assert spans == [(first.seq, turn.seq - 1), (turn.seq, ended.seq)]


async def test_max_turns_closes_the_episode(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    settings = SETTINGS.model_copy(update={"episode_max_turns": 2})
    first = await log.put("message.sent")
    await log.put("turn.committed")
    second_turn = await log.put("turn.committed")
    await log.put("message.sent")
    await drain(dispatcher(jobs_db, settings=settings), learner)
    (episode,) = await events_of(jobs_db, learner, "episode.closed")
    assert (episode["payload"]["from_seq"], episode["payload"]["to_seq"]) == (
        first.seq,
        second_turn.seq,
    )


async def test_idle_episode_is_closed_by_the_periodic_check(jobs_db: Db) -> None:
    """T-D2: `episode_idle_min` minutes without events, the clock replaced."""
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    await log.put("message.sent", at=0)
    last = await log.put("message.sent", at=3)
    await drain(dispatcher(jobs_db), learner)
    assert await events_of(jobs_db, learner, "episode.closed") == []

    assert await dispatcher(jobs_db, now=T0 + timedelta(minutes=12)).close_idle_episodes() == 0
    d = dispatcher(jobs_db, now=T0 + timedelta(minutes=14))
    assert await d.close_idle_episodes() == 1
    (episode,) = await events_of(jobs_db, learner, "episode.closed")
    assert episode["payload"]["to_seq"] == last.seq
    assert await d.close_idle_episodes() == 0  # closed once

    await drain(d, learner)  # the closing event reaches the rules
    assert len(await jobs_of(jobs_db, learner, "extract.episode")) == 1


async def test_idle_check_survives_a_failing_learner(jobs_db: Db) -> None:
    """Law 7: the idle check of one learner failing does not leave the others open."""
    bad, good = await create_learner(jobs_db), await create_learner(jobs_db)
    for learner in (bad, good):
        await Log(jobs_db, learner).put("message.sent", at=0)
        await drain(dispatcher(jobs_db), learner)

    class Flaky(DispatcherP4):
        async def _close_idle_of(self, learner_id: UUID, now: datetime) -> int:
            if learner_id == bad:
                raise RuntimeError("boom")
            return await super()._close_idle_of(learner_id, now)

    d = Flaky(SETTINGS, jobs_db, clock=lambda: T0 + timedelta(hours=1))
    assert await d.close_idle_episodes() == 1
    assert await events_of(jobs_db, bad, "episode.closed") == []
    assert len(await events_of(jobs_db, good, "episode.closed")) == 1


async def test_idle_check_waits_for_unread_events(jobs_db: Db) -> None:
    """Events the dispatcher has not read may belong to the episode: it is not closed yet."""
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    await log.put("message.sent", at=0)
    await drain(dispatcher(jobs_db), learner)
    await log.put("message.sent", at=5)  # unread
    d = dispatcher(jobs_db, now=T0 + timedelta(hours=1))
    assert await d.close_idle_episodes() == 0
    await drain(d, learner)  # reads it; the gap to "now" is then closed by the next check
    assert await d.close_idle_episodes() == 1


async def test_open_episode_of_a_session_is_kept_in_the_counters(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    first = await log.put("message.sent")
    await drain(dispatcher(jobs_db), learner)
    row = await jobs_db.fetch_one(
        "SELECT counters FROM ops.dispatcher_offsets WHERE learner_id = %(l)s", {"l": learner}
    )
    assert row is not None
    assert row["counters"]["episodes"][str(log.session)]["from_seq"] == first.seq


# --- coverage (T-D3) ---------------------------------------------------------------------


async def test_episode_without_content(jobs_db: Db) -> None:
    """T-D3: only closed answers with code verdicts — `nothing_to_extract`, no job."""
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    await log.put("turn.committed")
    answer = await log.put("answer.submitted")
    await log.put("verdict.recorded", {"answer_seq": answer.seq, "verification": "code"})
    await log.put("task.closed")
    await drain(dispatcher(jobs_db), learner)

    (episode,) = await events_of(jobs_db, learner, "episode.closed")
    assert episode["payload"]["has_content"] is False
    rows = await jobs_db.fetch_all(
        "SELECT * FROM ops.coverage WHERE learner_id = %(l)s", {"l": learner}
    )
    assert [
        (r["episode_seq"], r["extractor"], r["status"], r["observation_seq"]) for r in rows
    ] == [(episode["seq"], EXTRACTOR, "nothing_to_extract", None)]
    assert await jobs_of(jobs_db, learner, "extract.episode") == []


async def test_coverage_of_another_learner_is_not_touched(jobs_db: Db) -> None:
    mine, other = await create_learner(jobs_db), await create_learner(jobs_db)
    await mark_covered(
        jobs_db,
        other,
        episode_seq=3,
        extractor=EXTRACTOR,
        status="nothing_to_extract",
        observation_seq=None,
    )
    log = Log(jobs_db, mine)
    answer = await log.put("answer.submitted")
    await log.put("verdict.recorded", {"answer_seq": answer.seq})
    await log.put("task.closed")
    await drain(dispatcher(jobs_db), mine)
    rows = await jobs_db.fetch_all("SELECT learner_id FROM ops.coverage ORDER BY learner_id")
    assert sorted(r["learner_id"] for r in rows) == sorted([mine, other])


# --- idempotency and races ---------------------------------------------------------------


async def test_a_replay_of_the_log_adds_no_event_no_job_no_coverage(jobs_db: Db) -> None:
    """Law 5: the mark reset after a crash — the same episodes by their keys, the same jobs by
    theirs; nothing is doubled."""
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    await log.put("session.started")
    await log.put("message.sent", at=0)
    await log.put("task.closed", at=1)
    answer = await log.put("answer.submitted", at=2)
    await log.put("verdict.recorded", {"answer_seq": answer.seq}, at=2)
    await log.put("task.closed", at=3)
    await log.put("message.sent", at=40)  # an idle gap by the event times
    await log.put("session.ended", at=41)
    d = dispatcher(jobs_db)
    await drain(d, learner)
    before = await counts(jobs_db)
    assert before[0] > 8 and before[1] > 0 and before[2] == 1

    await reset_mark(jobs_db, learner)
    await drain(d, learner)
    assert await counts(jobs_db) == before


async def test_two_dispatchers_on_one_learner_put_each_job_once(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    for i in range(6):
        await log.put("message.sent", at=i)
        await log.put("task.closed", at=i)
    runs = await asyncio.gather(
        *(dispatcher(jobs_db).process_learner(learner) for _ in range(4)),
    )
    assert sum(runs) >= 12  # one of them did the work, the others found the mark held
    await drain(dispatcher(jobs_db), learner)
    keys = [j["job_key"] for j in await jobs_of(jobs_db, learner)]
    assert len(keys) == len(set(keys))
    assert len(await events_of(jobs_db, learner, "episode.closed")) == 6
    assert len(await jobs_of(jobs_db, learner, "extract.episode")) == 6


async def test_one_event_one_job_for_many_learners_in_parallel(jobs_db: Db) -> None:
    learners = [await create_learner(jobs_db) for _ in range(5)]
    for learner in learners:
        log = Log(jobs_db, learner)
        await log.put("message.sent")
        await log.put("task.closed")
    d = dispatcher(jobs_db)
    await asyncio.gather(d.run_once(), d.run_once())
    await d.run_once()
    for learner in learners:
        assert len(await jobs_of(jobs_db, learner, "extract.episode")) == 1
        assert len(await jobs_of(jobs_db, learner, "analyst.run")) == 1
    assert (await d.lag())[0] == 0


async def test_unknown_event_type_is_skipped_not_stuck(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await jobs_db.execute(
        "INSERT INTO core.events (learner_id, seq, type, schema_version, actor, idempotency_key, "
        "payload_hash, occurred_at, payload) VALUES (%(l)s, 1, 'mystery.happened', 1, 'system', "
        "'x', 'h', now(), %(p)s)",
        {"l": learner, "p": Jsonb({})},
    )
    await jobs_db.execute(
        "UPDATE core.learners SET seq = 1 WHERE learner_id = %(l)s", {"l": learner}
    )
    d = dispatcher(jobs_db)
    assert await d.process_learner(learner) == 1
    assert (await d.lag())[0] == 0


# --- coalescing through the dispatcher (T-D4) --------------------------------------------


async def test_closing_tasks_raises_the_waiting_analyst(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    d = dispatcher(jobs_db)
    await log.put("message.sent")
    await log.put("task.closed")
    second = await log.put("task.closed")
    await drain(d, learner)
    (analyst,) = await jobs_of(jobs_db, learner, "analyst.run")
    assert analyst["required_seq"] == second.seq and analyst["status"] == "queued"
    assert analyst["lock_key"] == analyst["coalesce_key"] == f"analyst:{learner}"

    # Once it runs, the next close puts a new one.
    await jobs_db.execute(
        "INSERT INTO ops.projector_offsets (learner_id, seq) VALUES (%(l)s, 999)", {"l": learner}
    )
    started = await q.claim_next(jobs_db, SETTINGS, kinds=["analyst.run"])
    assert started is not None
    third = await log.put("task.closed")
    await drain(d, learner)
    rows = await jobs_of(jobs_db, learner, "analyst.run")
    assert sorted(r["status"] for r in rows) == ["queued", "running"]
    assert max(r["required_seq"] for r in rows) == third.seq


# --- cancel (D-A-115, T-D6) --------------------------------------------------------------


async def test_plan_update_cancels_the_requests_of_the_teacher(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    waiting, running, kept = uuid4(), uuid4(), uuid4()
    for request in (waiting, running, kept):
        await log.put("agent.requested", {"request_id": str(request)})
    d = dispatcher(jobs_db)
    await drain(d, learner)
    assert len(await jobs_of(jobs_db, learner, "agent.viz")) == 3

    # Another learner's job of the same request id stays out of it (law 1).
    stranger = await create_learner(jobs_db)
    await Log(jobs_db, stranger).put("agent.requested", {"request_id": str(waiting)})
    await drain(d, stranger)

    # One request is already running when the plan changes.
    await jobs_db.execute(
        "UPDATE ops.jobs SET status = 'running', attempts = 1, heartbeat_at = now() "
        "WHERE learner_id = %(l)s AND payload->>'request_id' = %(r)s",
        {"l": learner, "r": str(running)},
    )
    turn = sample_payload("turn.committed")
    turn["markup"]["plan_update"]["cancel_request_ids"] = [str(waiting), str(running)]
    await log.put("turn.committed", turn)
    await drain(d, learner)

    by_request = {
        j["payload"]["request_id"]: j["status"]
        for j in await jobs_of(jobs_db, learner, "agent.viz")
    }
    assert by_request == {
        str(waiting): "cancelled",
        str(running): "cancel_requested",
        str(kept): "queued",
    }
    (theirs,) = await jobs_of(jobs_db, stranger, "agent.viz")
    assert theirs["status"] == "queued"


# --- rows that read data beyond the event (D-A-136, D-A-162) -----------------------------


async def _structure(log: Log, *, topic: UUID, skill: UUID, status: str = "active") -> None:
    await log.put(
        "skill.created",
        {
            "skill_id": str(skill),
            "topic_id": str(topic),
            "domain_id": str(log.domain),
            "status": status,
        },
    )


async def test_skill_split_reextracts_the_episodes_with_evidence_of_the_old_skill(
    jobs_db: Db,
) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    old = uuid4()
    element = sample_payload("observation.extracted")["elements"][0]
    obs = {}
    # Episode 40 holds only a hypothesis about the old skill's misconception: not evidence.
    cases = ((10, old, "evidence"), (20, uuid4(), "evidence"), (30, old, "evidence"))
    for episode_seq, skill, kind in (*cases, (40, old, "misconception_hypothesis")):
        obs[episode_seq] = await log.put(
            "observation.extracted",
            {
                "episode_seq": episode_seq,
                "elements": [{**element, "skill_id": str(skill), "kind": kind}],
            },
        )
        await mark_covered(
            jobs_db,
            learner,
            episode_seq=episode_seq,
            extractor=EXTRACTOR,
            status="extracted",
            observation_seq=obs[episode_seq].seq,
        )
    # An episode of another learner with evidence of the same skill id must not be touched.
    stranger = await create_learner(jobs_db)
    foreign = await Log(jobs_db, stranger).put(
        "observation.extracted", {"elements": [{**element, "skill_id": str(old)}]}
    )
    await mark_covered(
        jobs_db,
        stranger,
        episode_seq=77,
        extractor=EXTRACTOR,
        status="extracted",
        observation_seq=foreign.seq,
    )
    parts = [uuid4(), uuid4()]
    split = await log.put(
        "skill.split",
        {
            "skill_id": str(old),
            "into": [
                {"skill_id": str(p), "name": f"part {i}", "topic_id": str(uuid4())}
                for i, p in enumerate(parts)
            ],
        },
    )
    await drain(dispatcher(jobs_db), learner)

    jobs = await jobs_of(jobs_db, learner, "extract.episode")
    assert sorted(j["payload"]["episode_seq"] for j in jobs) == [10, 30]
    for job in jobs:
        assert job["required_seq"] == split.seq
        assert job["payload"]["restrict_skill_ids"] == [str(p) for p in parts]
    assert len({j["job_key"] for j in jobs}) == 2
    assert await jobs_of(jobs_db, stranger, "extract.episode") == []


async def test_session_end_refreshes_the_topics_the_session_touched(jobs_db: Db) -> None:
    """Т-§17.7: exposures and evidence by the home topic of the skill — merged, split and
    renamed structure is followed through the events."""
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    topic, merged_into, skill, other_skill = uuid4(), uuid4(), uuid4(), uuid4()
    await _structure(log, topic=topic, skill=skill)
    await _structure(log, topic=uuid4(), skill=other_skill)
    await log.put("session.started")
    turn = sample_payload("turn.committed")
    turn["markup"]["exposures"] = [
        {"skill_id": str(skill), "kind": "explained", "approach": "small_steps"}
    ]
    await log.put("turn.committed", turn)
    await log.put("topic.merged", {"from_id": str(topic), "into_id": str(merged_into)})
    ended = await log.put("session.ended")
    await drain(dispatcher(jobs_db), learner)

    (job,) = await jobs_of(jobs_db, learner, "artifact.topic_refresh")
    assert job["payload"]["topic_id"] == str(merged_into)  # the alias is followed
    assert job["lock_key"] == f"artifact:{merged_into}" and job["required_seq"] == ended.seq
    assert job["job_key"] == f"artifact.topic_refresh:{merged_into}:session:{log.session}"


async def test_session_end_with_no_exposure_or_evidence_refreshes_nothing(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    await log.put("session.started")
    await log.put("session.ended")
    await drain(dispatcher(jobs_db), learner)
    assert await jobs_of(jobs_db, learner, "artifact.topic_refresh") == []


async def test_closed_task_with_hints_refreshes_the_topic_of_its_skills(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    topic, skill = uuid4(), UUID(sample_payload("task.created")["markup"]["tests"][0]["skill_id"])
    await _structure(log, topic=topic, skill=skill)
    hinted, plain = uuid4(), uuid4()
    for task in (hinted, plain):
        await log.put("task.created", {"task_id": str(task)})
    await log.put("hint.shown", {"task_id": str(hinted)})
    await log.put("task.closed", {"task_id": str(hinted)})
    await log.put("task.closed", {"task_id": str(plain)})
    await drain(dispatcher(jobs_db), learner)
    (job,) = await jobs_of(jobs_db, learner, "artifact.topic_refresh")
    assert job["payload"] == {
        "trigger_seq": job["required_seq"],
        "topic_id": str(topic),
        "reason": "hints",
        "task_id": str(hinted),
    }


async def test_hygiene_every_n_new_topics_and_after_a_session_with_temporary_entities(
    jobs_db: Db,
) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    d = dispatcher(jobs_db)  # graph_hygiene_every_topics = 3
    domain, elsewhere = log.domain, uuid4()

    async def topic(domain_id: UUID) -> StoredEvent:
        return await log.put(
            "topic.created", {"topic_id": str(uuid4()), "domain_id": str(domain_id)}
        )

    await topic(domain)
    await topic(elsewhere)
    await topic(domain)
    await drain(d, learner)  # the counter lives in the mark between the batches
    assert await jobs_of(jobs_db, learner, "graph.hygiene") == []
    third = await topic(domain)
    await drain(d, learner)
    (job,) = await jobs_of(jobs_db, learner, "graph.hygiene")
    assert job["priority_class"] == "P3" and job["required_seq"] == third.seq
    assert job["lock_key"] == f"domain:{domain}" and job["coalesce_key"] == f"hygiene:{domain}"
    assert job["payload"]["reason"] == "new_topics"

    # The counter starts over: two more topics are not enough, the third again is.
    await jobs_db.execute("UPDATE ops.jobs SET status = 'succeeded'")
    await topic(domain)
    await topic(domain)
    await drain(d, learner)
    assert [
        j for j in await jobs_of(jobs_db, learner, "graph.hygiene") if j["status"] == "queued"
    ] == []
    await topic(domain)
    await drain(d, learner)
    assert (
        len(
            [j for j in await jobs_of(jobs_db, learner, "graph.hygiene") if j["status"] == "queued"]
        )
        == 1
    )

    # A session with a provisional skill asks for hygiene too.
    await jobs_db.execute("UPDATE ops.jobs SET status = 'succeeded'")
    await log.put("session.started")
    await _structure(log, topic=uuid4(), skill=uuid4(), status="provisional")
    ended = await log.put("session.ended")
    await drain(d, learner)
    (queued,) = [
        j for j in await jobs_of(jobs_db, learner, "graph.hygiene") if j["status"] == "queued"
    ]
    assert (
        queued["required_seq"] == ended.seq and queued["payload"]["reason"] == "temporary_entities"
    )
    assert queued["lock_key"] == f"domain:{domain}"


async def test_session_without_temporary_entities_asks_for_no_hygiene(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    await log.put("session.started")
    await _structure(log, topic=uuid4(), skill=uuid4())
    await log.put("session.ended")
    await drain(dispatcher(jobs_db), learner)
    assert await jobs_of(jobs_db, learner, "graph.hygiene") == []


async def test_embedding_jobs_carry_their_text_and_read_only_the_log(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    chunk = await log.put("chunk.indexed", session=False)
    await log.put("misconception.described", session=False)
    await drain(dispatcher(jobs_db), learner)
    jobs = await jobs_of(jobs_db, learner, "embedding.compute")
    assert len(jobs) == 2
    assert all(j["required_seq"] is None and j["coalesce_key"] is None for j in jobs)
    texts = {j["payload"]["nodes"][0]["node_kind"]: j["payload"]["nodes"][0]["text"] for j in jobs}
    assert texts == {"chunk": "A limit is…", "misconception": "limit equals the value"}
    assert chunk.seq in {j["payload"]["trigger_seq"] for j in jobs}
