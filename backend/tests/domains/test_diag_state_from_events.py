"""Diagnostic state lives only in `core.events` (T-016 D-1 + D-5).

Everything is driven through the public HTTP API of `/v1/domains/{id}/diagnostic/*`. The
LLM double returns questions whose options and correct answers are unique per question, so a
state that was not restored from the event log (Redis, defaults `A/B/C`, another session's
question) shows up as a wrong option list or a wrong grade.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.domains import diagnostic
from quack.domains.auth import DEV_JWT_SECRET, JWT_ALGORITHM
from quack.domains.schemas import (
    DiagnosticOutput,
    PrereqEdge,
    SkillDepEdge,
    SkillDraft,
    TopicDraft,
)
from quack.events.append import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.events.reader import read_learner_events
from quack.graph.worker import ProjectorWorker
from tests.domains.conftest import FakeLLMProvider

DIAG_SESSION_KEY_PATTERN = "diagnostic:session:*"


class DiagDomain(NamedTuple):
    domain_id: UUID
    topics: tuple[UUID, UUID, UUID]
    skills: tuple[UUID, UUID, UUID]


class Question(NamedTuple):
    options: list[str]
    correct: str


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _queue_question(provider: FakeLLMProvider, tag: str) -> Question:
    """Queue one generated diagnostic question with options and a correct answer unique to `tag`."""
    options = [f"{tag}-opt-{i}" for i in range(4)]
    correct = options[2]
    provider.enqueue(
        DiagnosticOutput(
            prompt_text=f"Question {tag}?",
            options=options,
            correct_answer=correct,
            is_correct=False,
            score=0.0,
            tested_depth=0.5,
        )
    )
    return Question(options, correct)


@pytest.fixture
async def diag_domain(
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> DiagDomain:
    """Active domain with chain `T1 -> T2 -> T3` (`PREREQ_OF`), `S2 -> S1`, `S3 -> S2`
    (`DEPENDS_ON`), projected into Neo4j. The diagnostic starts at the middle topic `T2`.
    """
    learner_id, _ = seeded_learner
    domain_id = uuid4()
    t1, t2, t3 = uuid4(), uuid4(), uuid4()
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    topics = [
        TopicDraft(topic_id=t1, name="T1 Basics"),
        TopicDraft(topic_id=t2, name="T2 Core Hub"),
        TopicDraft(topic_id=t3, name="T3 Advanced"),
    ]
    skills = [
        SkillDraft(skill_id=s1, topic_id=t1, name="S1 Basic Skill"),
        SkillDraft(skill_id=s2, topic_id=t2, name="S2 Hub Skill"),
        SkillDraft(skill_id=s3, topic_id=t3, name="S3 Advanced Skill"),
    ]
    prereqs = [
        PrereqEdge(from_topic_id=t1, to_topic_id=t2),
        PrereqEdge(from_topic_id=t2, to_topic_id=t3),
    ]
    skill_deps = [
        SkillDepEdge(skill_id=s2, depends_on_skill_id=s1),
        SkillDepEdge(skill_id=s3, depends_on_skill_id=s2),
    ]
    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"seed-domain:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Linear Algebra",
                        "slug": f"linalg-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    idempotency_key=f"seed-propose:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [t.model_dump(mode="json") for t in topics],
                        "skills": [s.model_dump(mode="json") for s in skills],
                        "prereqs": [e.model_dump(mode="json") for e in prereqs],
                        "skill_deps": [d.model_dump(mode="json") for d in skill_deps],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"seed-confirm:{domain_id}",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
            ],
            publish_wakeup=False,
        )
    await ProjectorWorker(settings, pg_engine, db, neo4j, redis).run_once()
    return DiagDomain(domain_id, (t1, t2, t3), (s1, s2, s3))


@pytest.fixture
async def other_learner(db: SessionFactory, settings: Settings) -> tuple[UUID, str]:
    """A second, unrelated learner (`auth.users` row + bearer token)."""
    learner_id = uuid4()
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO auth.users (id, email, password_hash, display_name, preferences) "
                "VALUES (:id, :email, :pw, :name, CAST(:prefs AS JSONB))"
            ),
            {
                "id": learner_id,
                "email": f"other-{learner_id.hex[:8]}@example.com",
                "pw": "$argon2id$v=19$m=65536,t=3,p=4$seeded$seeded",
                "name": "Other Learner",
                "prefs": "{}",
            },
        )
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(learner_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=7)).timestamp()),
        },
        settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return learner_id, token


async def _start(
    client: httpx.AsyncClient, token: str, domain_id: UUID, key: str = "diag-start"
) -> dict[str, Any]:
    resp = await client.post(
        f"/v1/domains/{domain_id}/diagnostic/start",
        headers=_auth_headers(token),
        json={"idempotency_key": f"{key}:{domain_id}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _answer(
    client: httpx.AsyncClient,
    token: str,
    domain_id: UUID,
    session_id: UUID | str,
    task_id: UUID | str,
    answer: str,
) -> httpx.Response:
    return await client.post(
        f"/v1/domains/{domain_id}/diagnostic/answer",
        headers=_auth_headers(token),
        json={
            "session_id": str(session_id),
            "task_id": str(task_id),
            "answer": answer,
            "confidence_self_report": 0.5,
            "response_time_sec": 3.0,
            "idempotency_key": f"diag-ans:{uuid4()}",
        },
    )


async def _finish(
    client: httpx.AsyncClient,
    token: str,
    domain_id: UUID,
    session_id: UUID | str,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/v1/domains/{domain_id}/diagnostic/finish",
        headers=_auth_headers(token),
        json={"session_id": str(session_id), "idempotency_key": key or f"diag-finish:{uuid4()}"},
    )


async def _events(db: SessionFactory, learner_id: UUID, *types: str) -> list[StoredEvent]:
    async with db() as session:
        events = await read_learner_events(session, learner_id)
    return [e for e in events if not types or e.type in types]


async def _diag_session_keys(redis: Redis) -> list[str]:
    return sorted([str(k) async for k in redis.scan_iter(match=DIAG_SESSION_KEY_PATTERN)])


async def _disrupt_redis(mode: str, app: FastAPI, redis: Redis) -> None:
    """`flush`: wipe Redis (called between requests); `no_redis`: `resources.redis = None`."""
    if mode == "flush":
        await redis.flushdb()
    else:
        app.state.resources.redis = None


def test_state_loader_is_event_only() -> None:
    """The Redis helpers are gone and `_reconstruct_state_from_events` is the state loader."""
    assert callable(getattr(diagnostic, "_reconstruct_state_from_events", None))
    assert not hasattr(diagnostic, "_diag_redis_key")
    assert not hasattr(diagnostic, "_save_session_state")
    assert "diagnostic:session" not in inspect.getsource(diagnostic)


async def test_task_generated_and_task_bank_carry_option_answer_and_options(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    """(a) Both `task.generated` events (start and answer) and their `core.task_bank` rows hold
    `reference_solution == {"option": c, "answer": c, "options": [...]}`.
    """
    learner_id, token = seeded_learner
    q1 = _queue_question(fake_llm_provider, "first")
    q2 = _queue_question(fake_llm_provider, "second")

    started = await _start(api_client, token, diag_domain.domain_id)
    first_task = started["next_question"]["task_id"]
    resp = await _answer(
        api_client, token, diag_domain.domain_id, started["session_id"], first_task, q1.correct
    )
    assert resp.status_code == 200, resp.text
    second_task = resp.json()["next_question"]["task_id"]

    generated = await _events(db, learner_id, "task.generated")
    assert [e.payload["task_id"] for e in generated] == [first_task, second_task]
    async with db() as session:
        for event, question in zip(generated, (q1, q2), strict=True):
            expected = {
                "option": question.correct,
                "answer": question.correct,
                "options": question.options,
            }
            assert event.payload["reference_solution"] == expected
            row = (
                (
                    await session.execute(
                        text("SELECT reference_solution FROM core.task_bank WHERE id = :id"),
                        {"id": UUID(event.payload["task_id"])},
                    )
                )
                .mappings()
                .one()
            )
            assert row["reference_solution"] == expected


@pytest.mark.parametrize("mode", ["flush", "no_redis"])
async def test_start_answer_answer_finish_without_redis_state(
    mode: str,
    domains_app: FastAPI,
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    redis: Redis,
) -> None:
    """(b) A whole diagnostic works with Redis flushed between calls and with
    `resources.redis = None`: every next question keeps its options, every answer is graded
    against the restored correct answer, and `finish` knows what was tested.
    """
    _, token = seeded_learner
    domain_id = diag_domain.domain_id
    t1, t2, t3 = diag_domain.topics
    s1, s2, s3 = diag_domain.skills
    q1 = _queue_question(fake_llm_provider, "q1")
    q2 = _queue_question(fake_llm_provider, "q2")
    q3 = _queue_question(fake_llm_provider, "q3")

    await _disrupt_redis(mode, domains_app, redis)
    started = await _start(api_client, token, domain_id)
    session_id = started["session_id"]
    first = started["next_question"]
    assert UUID(first["topic_id"]) == t2
    assert UUID(first["skill_id"]) == s2
    assert first["options"] == q1.options

    await _disrupt_redis(mode, domains_app, redis)
    resp = await _answer(api_client, token, domain_id, session_id, first["task_id"], q1.correct)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_correct"] is True
    second = body["next_question"]
    assert UUID(second["topic_id"]) == t3
    assert UUID(second["skill_id"]) == s3
    assert second["options"] == q2.options
    assert second["kind"] == "choice"
    assert second["prompt_text"] == "Question q2?"

    await _disrupt_redis(mode, domains_app, redis)
    resp = await _answer(api_client, token, domain_id, session_id, second["task_id"], q2.options[0])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_correct"] is False
    assert body["should_finish"] is False
    third = body["next_question"]
    assert UUID(third["topic_id"]) == t1
    assert UUID(third["skill_id"]) == s1
    assert third["options"] == q3.options

    await _disrupt_redis(mode, domains_app, redis)
    resp = await _finish(api_client, token, domain_id, session_id)
    assert resp.status_code == 200, resp.text
    finished = resp.json()
    assert {UUID(t) for t in finished["tested_topic_ids"]} == {t2, t3}
    priors = {UUID(p["skill_id"]): p for p in finished["inferred_priors"]}
    assert priors[s2]["source"] == "tested"
    assert priors[s2]["prior_mastery"] == pytest.approx(0.5)
    assert priors[s3]["source"] == "tested"
    assert priors[s3]["prior_mastery"] == pytest.approx(0.0)
    assert priors[s1]["source"] == "propagated"


async def test_no_diagnostic_session_key_in_redis(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    redis: Redis,
) -> None:
    """(c) No `diagnostic:session:*` key exists in Redis after start and after answer."""
    _, token = seeded_learner
    q1 = _queue_question(fake_llm_provider, "k1")
    _queue_question(fake_llm_provider, "k2")

    started = await _start(api_client, token, diag_domain.domain_id)
    assert await _diag_session_keys(redis) == []

    resp = await _answer(
        api_client,
        token,
        diag_domain.domain_id,
        started["session_id"],
        started["next_question"]["task_id"],
        q1.correct,
    )
    assert resp.status_code == 200, resp.text
    assert await _diag_session_keys(redis) == []


async def test_two_sessions_of_one_learner_do_not_mix_state(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    redis: Redis,
) -> None:
    """The state of a session comes from the last `task.generated` of *that* session, even when
    another session of the same learner and domain generated a newer task in between.
    """
    _, token = seeded_learner
    domain_id = diag_domain.domain_id

    qa1 = _queue_question(fake_llm_provider, "a1")
    started_a = await _start(api_client, token, domain_id, key="diag-start-a")
    qb1 = _queue_question(fake_llm_provider, "b1")
    started_b = await _start(api_client, token, domain_id, key="diag-start-b")
    assert started_a["session_id"] != started_b["session_id"]
    assert started_a["next_question"]["options"] == qa1.options
    assert started_b["next_question"]["options"] == qb1.options

    await redis.flushdb()
    qa2 = _queue_question(fake_llm_provider, "a2")
    resp = await _answer(
        api_client,
        token,
        domain_id,
        started_a["session_id"],
        started_a["next_question"]["task_id"],
        qa1.correct,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_correct"] is True
    next_a = resp.json()["next_question"]
    assert next_a["options"] == qa2.options

    qb2 = _queue_question(fake_llm_provider, "b2")
    resp = await _answer(
        api_client,
        token,
        domain_id,
        started_b["session_id"],
        started_b["next_question"]["task_id"],
        qb1.correct,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_correct"] is True
    assert resp.json()["next_question"]["options"] == qb2.options

    # Session B generated the newest task of the learner; session A must still be graded
    # against its own latest question.
    await redis.flushdb()
    _queue_question(fake_llm_provider, "a3")
    resp = await _answer(
        api_client, token, domain_id, started_a["session_id"], next_a["task_id"], qa2.correct
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_correct"] is True


async def test_unknown_session_or_wrong_domain_is_404(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    """A `session_id` without `diagnostic.started` for the learner in this domain is `NOT_FOUND`."""
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "n1")
    _queue_question(fake_llm_provider, "n2")
    started = await _start(api_client, token, domain_id)
    session_id = started["session_id"]
    task_id = started["next_question"]["task_id"]
    events_before = await _events(db, learner_id)

    attempts = [
        await _answer(api_client, token, domain_id, uuid4(), task_id, q1.correct),
        await _finish(api_client, token, domain_id, uuid4()),
        await _answer(api_client, token, uuid4(), session_id, task_id, q1.correct),
        await _finish(api_client, token, uuid4(), session_id),
    ]
    for resp in attempts:
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "NOT_FOUND"
    assert len(await _events(db, learner_id)) == len(events_before)

    resp = await _answer(api_client, token, domain_id, session_id, task_id, q1.correct)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_correct"] is True


async def test_other_learner_cannot_answer_or_finish_foreign_session(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    other_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    """(d) Another learner's token gets 404 `NOT_FOUND` for answer and finish on a foreign
    `session_id` (Redis is not flushed: no cross-learner state may be served from it) and
    writes nothing.
    """
    _, token = seeded_learner
    other_id, other_token = other_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "x1")
    _queue_question(fake_llm_provider, "x2")
    started = await _start(api_client, token, domain_id)
    session_id = started["session_id"]
    task_id = started["next_question"]["task_id"]

    resp = await _answer(api_client, other_token, domain_id, session_id, task_id, q1.correct)
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    resp = await _finish(api_client, other_token, domain_id, session_id)
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "NOT_FOUND"

    other_events = await _events(db, other_id)
    assert [e.type for e in other_events if e.type.startswith(("diagnostic.", "task."))] == []

    resp = await _answer(api_client, token, domain_id, session_id, task_id, q1.correct)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_correct"] is True


async def test_answer_after_finish_conflicts_and_second_finish_replays_stored_result(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
    redis: Redis,
) -> None:
    """(e) After `finish`, `answer` is 409 `IDEMPOTENCY_CONFLICT`; a second `finish` (new or same
    idempotency key) returns the body of the stored `diagnostic.completed` and appends nothing.
    """
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "f1")
    _queue_question(fake_llm_provider, "f2")

    started = await _start(api_client, token, domain_id)
    session_id = started["session_id"]
    resp = await _answer(
        api_client, token, domain_id, session_id, started["next_question"]["task_id"], q1.correct
    )
    assert resp.status_code == 200, resp.text
    last_task_id = resp.json()["next_question"]["task_id"]

    first_key = f"diag-finish-first:{session_id}"
    first = await _finish(api_client, token, domain_id, session_id, key=first_key)
    assert first.status_code == 200, first.text
    completed = await _events(db, learner_id, "diagnostic.completed")
    assert len(completed) == 1
    assert first.json()["seq"] == completed[0].seq
    events_after_finish = await _events(db, learner_id)

    await redis.flushdb()
    resp = await _answer(api_client, token, domain_id, session_id, last_task_id, q1.correct)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    again = await _finish(api_client, token, domain_id, session_id)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    same_key = await _finish(api_client, token, domain_id, session_id, key=first_key)
    assert same_key.status_code == 200, same_key.text
    assert same_key.json() == first.json()

    assert len(await _events(db, learner_id, "diagnostic.completed")) == 1
    assert len(await _events(db, learner_id)) == len(events_after_finish)


async def test_finish_same_key_other_session_is_conflict(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    """T-E2: `finish` with a used key and another `session_id` is 409 `IDEMPOTENCY_CONFLICT`,
    also when that session does not exist: the key is checked before the session."""
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    _queue_question(fake_llm_provider, "k1")
    started = await _start(api_client, token, domain_id)
    key = f"diag-finish-key:{started['session_id']}"
    first = await _finish(api_client, token, domain_id, started["session_id"], key=key)
    assert first.status_code == 200, first.text
    events = len(await _events(db, learner_id))

    other = await _finish(api_client, token, domain_id, uuid4(), key=key)
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(await _events(db, learner_id)) == events
