"""Artifacts (ТЗ §5 п.3, §2.3): notes CRUD, versioned topic cheatsheets with `cheatsheet_ready`,
the `session.finish_artifacts` worker, FTS search and the domain export (A10, A11, A13–A15)."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

import httpx
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from sqlalchemy import text

from quack.artifacts.cheatsheets import run_finish_artifacts, run_refresh_cheatsheet
from quack.artifacts.deps import WorkerDeps
from quack.artifacts.schemas import ArtifactGenOutput
from quack.db.postgres import SessionFactory
from quack.db.redis import session_events_channel
from quack.graph.ids import QUACK_UUID_NS
from quack.jobs import kinds
from quack.jobs.queue import ClaimedJob, JobSpec, claim_next_job, complete_job, enqueue_job
from quack.llm.errors import ProviderUnavailableError
from tests.factories import new_event
from tests.support.dispatch import dispatch

if TYPE_CHECKING:
    from tests.artifacts.conftest import AuthHeaders, FakeProvider, Seeder, Structure

WORKER = "test-worker"
SHEET = ArtifactGenOutput(
    title="Differentiation formulas", text="- Chain rule: $(f(g(x)))' = f'(g(x))\\,g'(x)$"
)
DIGEST = ArtifactGenOutput(
    title="Session recap", text="We practised the chain rule on $\\sin(x^2)$."
)
NOTE_TITLE = "Composite functions"
NOTE_TEXT = "chain rule for composite functions"
CHAIN_REF: dict[str, Any] = {
    "expression": "2*x*cos(x**2)",
    "mode": "expression_equiv",
    "hints": ["Composite function.", "Outer times inner derivative.", "cos(x**2) times 2*x."],
}

type JobFn = Callable[[ClaimedJob, WorkerDeps], Awaitable[None]]


# --- helpers ----------------------------------------------------------------------------


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _last_seq(db: SessionFactory, learner: UUID) -> int:
    [row] = await _q(db, "SELECT last_seq FROM core.learner_seq WHERE learner_id = :l", l=learner)
    return int(row["last_seq"])


async def _events_of(db: SessionFactory, learner: UUID, type_: str) -> list[dict[str, Any]]:
    return await _q(
        db,
        "SELECT seq, actor, payload FROM core.events WHERE learner_id = :l AND type = :t "
        "ORDER BY seq",
        l=learner,
        t=type_,
    )


async def _artifacts(db: SessionFactory, learner: UUID, kind: str) -> list[dict[str, Any]]:
    return await _q(
        db,
        "SELECT id, kind, session_id, topic_id, title, content_md, version, is_deleted "
        "FROM core.artifacts WHERE learner_id = :l AND kind = :k ORDER BY created_at",
        l=learner,
        k=kind,
    )


async def _claim(db: SessionFactory, spec: JobSpec) -> ClaimedJob:
    async with db() as session, session.begin():
        await enqueue_job(session, spec)
    async with db() as session, session.begin():
        job = await claim_next_job(session, WORKER, [spec.kind])
    assert job is not None
    return job


async def _run_job(db: SessionFactory, deps: WorkerDeps, spec: JobSpec, run: JobFn) -> None:
    """enqueue → claim → run → complete, as the job runner does."""
    job = await _claim(db, spec)
    await run(job, deps)
    async with db() as session, session.begin():
        assert await complete_job(session, job.id, WORKER)


async def _next_message(pubsub: PubSub) -> dict[str, Any]:
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
    assert message is not None
    return json.loads(message["data"])


def _cheatsheet_id(learner: UUID, topic_id: UUID) -> UUID:
    return uuid5(QUACK_UUID_NS, f"cheatsheet:{learner}:{topic_id}")


async def _create_note(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    s: Structure,
    title: str,
    text_: str,
    key: str,
) -> httpx.Response:
    body = {
        "domain_id": str(s.domain_id),
        "topic_id": str(s.topic_id),
        "session_id": None,
        "title": title,
        "text": text_,
        "idempotency_key": key,
    }
    return await client.post("/v1/artifacts/notes", json=body, headers=headers)


async def _delete_note(
    client: httpx.AsyncClient, headers: dict[str, str], note_id: str, key: str
) -> httpx.Response:
    return await client.request(
        "DELETE", f"/v1/artifacts/notes/{note_id}", json={"idempotency_key": key}, headers=headers
    )


async def _listed_ids(client: httpx.AsyncClient, headers: dict[str, str], **params: str) -> list:
    r = await client.get("/v1/artifacts", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [a["artifact_id"] for a in r.json()["artifacts"]]


# --- tests ------------------------------------------------------------------------------


async def test_notes_crud(
    client: httpx.AsyncClient, auth: AuthHeaders, seed: Seeder, db: SessionFactory
) -> None:
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    headers = auth(learner)

    r = await _create_note(client, headers, s, NOTE_TITLE, NOTE_TEXT, "note-1")
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["version"] == 1
    note_id = note["artifact_id"]
    assert note_id == str(uuid5(QUACK_UUID_NS, f"note:{learner}:note-1"))  # A11
    count = await _last_seq(db, learner)
    again = await _create_note(client, headers, s, NOTE_TITLE, NOTE_TEXT, "note-1")
    assert again.status_code == 201, again.text
    assert again.json() == note  # A10
    assert await _last_seq(db, learner) == count
    # T-E2: the same key with another body is 409 and writes nothing.
    other = await _create_note(client, headers, s, "Other", NOTE_TEXT, "note-1")
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _last_seq(db, learner) == count

    edit = {"title": "Chain rule, edited", "text": "(f(g))' = f'(g) g'", "idempotency_key": "e-1"}
    r = await client.patch(f"/v1/artifacts/notes/{note_id}", json=edit, headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["artifact_id"], r.json()["version"]) == (note_id, 2)
    [row] = await _artifacts(db, learner, "note")
    assert (row["title"], row["content_md"], row["version"]) == (edit["title"], edit["text"], 2)
    count = await _last_seq(db, learner)
    r = await client.patch(
        f"/v1/artifacts/notes/{note_id}", json={**edit, "text": "other"}, headers=headers
    )
    assert r.status_code == 409, r.text  # T-E2
    assert r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _last_seq(db, learner) == count
    assert note_id in await _listed_ids(client, headers, kind="note")

    stranger = await seed.learner()
    foreign = {"title": "Hijacked", "text": "x", "idempotency_key": "stranger-e-1"}
    r = await client.patch(f"/v1/artifacts/notes/{note_id}", json=foreign, headers=auth(stranger))
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
    [row] = await _artifacts(db, learner, "note")
    assert row["title"] == edit["title"]

    r = await _delete_note(client, headers, note_id, "d-1")
    assert r.status_code == 200, r.text
    assert (r.json()["artifact_id"], r.json()["is_deleted"]) == (note_id, True)
    assert note_id not in await _listed_ids(client, headers)

    r = await client.get("/v1/artifacts")
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "UNAUTHORIZED"

    deleted = await seed.learner()
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO ops.deletion_tombstones (learner_id, requested_at, status) "
                "VALUES (:l, now(), 'pending_purge')"
            ),
            {"l": deleted},
        )
    r = await client.get("/v1/artifacts", headers=auth(deleted))
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_cheatsheet_refresh_is_versioned_and_notifies(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    redis: Redis,
    fake_provider: FakeProvider,
    worker_deps: WorkerDeps,
) -> None:
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    tid, cid = s.topic_id, _cheatsheet_id(learner, s.topic_id)
    fake_provider.respond("ArtifactGenOutput", SHEET)

    r = await client.post(
        f"/v1/topics/{tid}/cheatsheet/refresh",
        json={"domain_id": str(s.domain_id)},
        headers=auth(learner),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    [job] = await _q(
        db,
        "SELECT kind, lock_key, status, learner_id, payload FROM ops.jobs WHERE id = :id",
        id=UUID(r.json()["job_id"]),
    )
    assert (job["kind"], job["lock_key"]) == (
        "artifact.refresh_cheatsheet",
        f"topic:{tid}:cheatsheet",
    )
    assert (job["status"], job["learner_id"]) == ("queued", learner)
    assert job["payload"]["domain_id"] == str(s.domain_id)

    pubsub = redis.pubsub()
    await pubsub.subscribe(session_events_channel(sid))
    await pubsub.get_message(timeout=1.0)
    payload = {"domain_id": str(s.domain_id), "session_id": str(sid)}
    spec = kinds.artifact_refresh_cheatsheet(learner, tid, payload, required_seq=None)
    # The endpoint's job waits for the projection of the learner's log (WP11.2, WP3.1).
    await seed.project(learner)
    for _ in range(3):
        await _run_job(db, worker_deps, spec, run_refresh_cheatsheet)

    [sheet] = await _artifacts(db, learner, "cheatsheet")
    assert (sheet["id"], sheet["version"]) == (cid, 3)
    assert (sheet["title"], sheet["content_md"]) == (SHEET.title, SHEET.text)
    updates = await _events_of(db, learner, "artifact.cheatsheet_updated")
    assert [e["payload"]["version"] for e in updates] == [1, 2, 3]
    assert {e["payload"]["artifact_id"] for e in updates} == {str(cid)}
    assert [await _next_message(pubsub) for _ in range(3)] == [
        {
            "event": "cheatsheet_ready",
            "data": {"topic_id": str(tid), "artifact_id": str(cid), "version": version},
        }
        for version in (1, 2, 3)
    ]

    # Without the LLM the deterministic template is written (A9: actor "code").
    fake_provider.respond("ArtifactGenOutput", ProviderUnavailableError("provider timeout"))
    await _run_job(db, worker_deps, spec, run_refresh_cheatsheet)
    [sheet] = await _artifacts(db, learner, "cheatsheet")
    assert (sheet["id"], sheet["version"]) == (cid, 4)
    assert sheet["content_md"] and sheet["content_md"] != SHEET.text
    updates = await _events_of(db, learner, "artifact.cheatsheet_updated")
    assert [e["actor"] for e in updates] == ["artifacts@1"] * 3 + ["code"]
    assert (await _next_message(pubsub))["data"]["version"] == 4
    await pubsub.aclose()

    r = await client.get(f"/v1/topics/{tid}/cheatsheet", headers=auth(learner))
    assert r.status_code == 200, r.text
    assert (r.json()["artifact_id"], r.json()["version"]) == (str(cid), 4)


async def test_finish_artifacts_creates_digest_and_enqueues_cheatsheet(
    seed: Seeder,
    db: SessionFactory,
    fake_provider: FakeProvider,
    worker_deps: WorkerDeps,
) -> None:
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    await seed.reply(learner, sid, 0, "how do I differentiate sin(x^2)?", teacher=False)
    await seed.reply(learner, sid, 1, "Use the chain rule: outer times inner.", teacher=True)
    task = await seed.seed_bank_task(
        learner, s, skill_id=s.skill_a, family="fam-a", kind="math", reference_solution=CHAIN_REF
    )
    await seed.solve_task(learner, sid, task, s.topic_id, s.skill_a)
    await seed.complete_session(learner, sid, s.domain_id)
    fake_provider.respond("ArtifactGenOutput", DIGEST)

    payload = {"session_id": str(sid), "domain_id": str(s.domain_id), "topic_id": str(s.topic_id)}
    job = await _claim(db, kinds.session_finish_artifacts(learner, sid, payload, required_seq=None))
    await run_finish_artifacts(job, worker_deps)
    await dispatch(seed.settings, db, learner)  # §8.9 п.7: the cheatsheet job comes from the log

    digest_id = uuid5(QUACK_UUID_NS, f"digest:{sid}")
    [digest] = await _events_of(db, learner, "session.digest_created")
    assert digest["payload"]["artifact_id"] == str(digest_id)
    assert digest["payload"]["session_id"] == str(sid)
    assert str(s.skill_a) in digest["payload"]["covered_skill_ids"]  # A15
    [row] = await _artifacts(db, learner, "digest")
    assert (row["id"], row["session_id"]) == (digest_id, sid)
    [refresh] = await _q(
        db,
        "SELECT lock_key, payload FROM ops.jobs WHERE learner_id = :l "
        "AND kind = 'artifact.refresh_cheatsheet' AND status = 'queued'",
        l=learner,
    )
    assert refresh["lock_key"] == f"topic:{s.topic_id}:cheatsheet"
    assert refresh["payload"]["session_id"] == str(sid)
    assert refresh["payload"]["domain_id"] == str(s.domain_id)

    # A retry of the same job does not create a second digest (A10).
    await run_finish_artifacts(job, worker_deps)
    assert len(await _events_of(db, learner, "session.digest_created")) == 1


async def test_finish_artifacts_takes_the_payload_of_finish_session(
    seed: Seeder,
    db: SessionFactory,
    redis: Redis,
    fake_provider: FakeProvider,
    worker_deps: WorkerDeps,
) -> None:
    """T-H8 (б), side of the handler (WP6.4 п.2): the job with the payload of `finish_session`
    `{"session_id", "domain_id", "topic_id"}` is taken as is, whatever its lock key; it creates
    `session.digest_created`, and the cheatsheet job creates `artifact.cheatsheet_updated` and
    sends `cheatsheet_ready` to `session:events:{session_id}`."""
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    await seed.reply(learner, sid, 0, "how do I differentiate sin(x^2)?", teacher=False)
    await seed.complete_session(learner, sid, s.domain_id)
    fake_provider.respond("ArtifactGenOutput", DIGEST)

    payload = {"session_id": str(sid), "domain_id": str(s.domain_id), "topic_id": str(s.topic_id)}
    job = await _claim(db, kinds.session_finish_artifacts(learner, sid, payload, required_seq=None))
    await run_finish_artifacts(replace(job, lock_key=f"learner:{learner}:finish"), worker_deps)
    await dispatch(seed.settings, db, learner)  # §8.9 п.7: the cheatsheet job comes from the log

    [digest] = await _events_of(db, learner, "session.digest_created")
    assert digest["payload"]["session_id"] == str(sid)
    [refresh] = await _q(
        db,
        "SELECT payload FROM ops.jobs WHERE learner_id = :l "
        "AND kind = 'artifact.refresh_cheatsheet' AND status = 'queued'",
        l=learner,
    )
    pubsub = redis.pubsub()
    await pubsub.subscribe(session_events_channel(sid))
    await pubsub.get_message(timeout=1.0)
    fake_provider.respond("ArtifactGenOutput", SHEET)
    spec = kinds.artifact_refresh_cheatsheet(
        learner, s.topic_id, refresh["payload"], required_seq=None
    )
    await seed.project(learner)  # the dispatcher's job waits for session.digest_created (WP3.1)
    await _run_job(db, worker_deps, spec, run_refresh_cheatsheet)

    [update] = await _events_of(db, learner, "artifact.cheatsheet_updated")
    message = await _next_message(pubsub)
    await pubsub.aclose()
    assert message == {
        "event": "cheatsheet_ready",
        "data": {
            "topic_id": str(s.topic_id),
            "artifact_id": update["payload"]["artifact_id"],
            "version": 1,
        },
    }


UUID_LIKE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)


async def test_digest_request_has_names_not_ids(
    seed: Seeder,
    db: SessionFactory,
    fake_provider: FakeProvider,
    worker_deps: WorkerDeps,
) -> None:
    """T-M3 (WP14.1, Т-§4.5): the digest request to `artifacts@1` names skills and numbers tasks;
    no id reaches the model. The template without the model has no ids either."""
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    task = await seed.seed_bank_task(
        learner, s, skill_id=s.skill_a, family="fam-a", kind="math", reference_solution=CHAIN_REF
    )
    await seed.solve_task(learner, sid, task, s.topic_id, s.skill_a)
    await seed.complete_session(learner, sid, s.domain_id)
    await seed.project(learner)
    fake_provider.respond("ArtifactGenOutput", DIGEST)

    payload = {"session_id": str(sid), "domain_id": str(s.domain_id), "topic_id": str(s.topic_id)}
    await run_finish_artifacts(
        await _claim(db, kinds.session_finish_artifacts(learner, sid, payload, required_seq=None)),
        worker_deps,
    )
    [call] = fake_provider.calls_for("ArtifactGenOutput")
    assert UUID_LIKE.findall(call.prompt) == []
    assert s.skill_a_name in call.prompt
    [digest] = await _events_of(db, learner, "session.digest_created")
    assert digest["payload"]["covered_skill_ids"] == [str(s.skill_a)]  # the event keeps ids

    other = await seed.start_session(learner, s.domain_id, s.topic_id)
    await seed.solve_task(learner, other, task, s.topic_id, s.skill_a)
    await seed.complete_session(learner, other, s.domain_id)
    await seed.project(learner)
    fake_provider.respond("ArtifactGenOutput", ProviderUnavailableError("provider timeout"))
    payload = {**payload, "session_id": str(other)}
    await run_finish_artifacts(
        await _claim(
            db, kinds.session_finish_artifacts(learner, other, payload, required_seq=None)
        ),
        worker_deps,
    )
    template = [
        e
        for e in await _events_of(db, learner, "session.digest_created")
        if e["payload"]["session_id"] == str(other)
    ]
    assert template[0]["actor"] == "code"
    assert UUID_LIKE.findall(template[0]["payload"]["text"]) == []
    assert s.skill_a_name in template[0]["payload"]["text"]


async def test_search_skips_deleted_notes_and_old_versions(
    client: httpx.AsyncClient, auth: AuthHeaders, seed: Seeder
) -> None:
    """Q4 (PHASE3_NOTES): the event search shows an artifact event only while it holds the
    artifact's current content — a deleted note and an edited-away text are not found; the
    current text is found in both lists."""
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    headers = auth(learner)
    r = await _create_note(client, headers, s, "Gone", "zygote membrane draft", "n-gone")
    assert r.status_code == 201, r.text
    r = await _delete_note(client, headers, r.json()["artifact_id"], "d-gone")
    assert r.status_code == 200, r.text
    r = await _create_note(client, headers, s, "Integrals", "trapezoid estimate", "n-kept")
    assert r.status_code == 201, r.text
    kept_id = r.json()["artifact_id"]
    edit = {"title": "Integrals", "text": "simpson estimate", "idempotency_key": "e-kept"}
    r = await client.patch(f"/v1/artifacts/notes/{kept_id}", json=edit, headers=headers)
    assert r.status_code == 200, r.text
    await seed.project(learner)

    async def found(q: str) -> tuple[list[str], set[tuple[str, str]]]:
        r = await client.get("/v1/artifacts/search", params={"q": q}, headers=headers)
        assert r.status_code == 200, r.text
        hits = r.json()
        artifacts = [a["artifact_id"] for a in hits["artifact_hits"]]
        return artifacts, {(e["type"], e["text"]) for e in hits["event_hits"]}

    assert await found("zygote membrane") == ([], set())
    assert await found("trapezoid") == ([], set())
    artifacts, events = await found("simpson")
    assert artifacts == [kept_id]
    assert events == {("artifact.note_updated", "simpson estimate")}


async def test_search_and_export(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    fake_provider: FakeProvider,
    worker_deps: WorkerDeps,
) -> None:
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    headers = auth(learner)

    r = await _create_note(client, headers, s, NOTE_TITLE, NOTE_TEXT, "note-1")
    assert r.status_code == 201, r.text
    note_id = r.json()["artifact_id"]
    r = await _create_note(client, headers, s, "Scratch", "temporary draft", "note-2")
    assert r.status_code == 201, r.text
    r = await _delete_note(client, headers, r.json()["artifact_id"], "d-2")
    assert r.status_code == 200, r.text
    await seed.reply(learner, sid, 0, "how does the chain rule work", teacher=False)
    fake_provider.respond("ArtifactGenOutput", SHEET)
    sheet_spec = kinds.artifact_refresh_cheatsheet(
        learner, s.topic_id, {"domain_id": str(s.domain_id)}, required_seq=None
    )
    await _run_job(db, worker_deps, sheet_spec, run_refresh_cheatsheet)
    digest_id = uuid5(QUACK_UUID_NS, f"digest:{sid}")
    await seed.append(
        learner,
        new_event(
            "session.digest_created",
            {
                "artifact_id": str(digest_id),
                "session_id": str(sid),
                "domain_id": str(s.domain_id),
                "topic_id": str(s.topic_id),
                "title": DIGEST.title,
                "text": DIGEST.text,
                "covered_skill_ids": [str(s.skill_a)],
            },
            actor="artifacts@1",
        ),
    )
    await seed.project(learner)

    r = await client.get("/v1/artifacts/search", params={"q": "chain rule"}, headers=headers)
    assert r.status_code == 200, r.text
    hits = r.json()
    assert note_id in [a["artifact_id"] for a in hits["artifact_hits"]]
    event_hits = {(e["type"], e["text"]) for e in hits["event_hits"]}
    assert ("turn.student_replied", "how does the chain rule work") in event_hits
    assert ("artifact.note_created", NOTE_TEXT) in event_hits

    r = await client.get("/v1/artifacts/search", params={"q": "c"}, headers=headers)
    assert r.status_code == 422, r.text
    stranger = await seed.learner()
    foreign = await seed.seed_structure(stranger)
    r = await client.get(
        "/v1/artifacts/search",
        params={"q": "chain rule", "domain_id": str(foreign.domain_id)},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"artifact_hits": [], "event_hits": []}

    r = await client.get(
        f"/v1/domains/{s.domain_id}/export", params={"format": "md"}, headers=headers
    )
    assert r.status_code == 200, r.text
    content = r.json()["content_md"]
    for expected in (
        s.domain_name,
        s.topic_name,
        s.skill_a_name,
        s.skill_b_name,
        SHEET.title,
        DIGEST.title,
        NOTE_TITLE,
    ):
        assert expected in content, expected
    assert "Scratch" not in content

    r = await client.get(
        f"/v1/domains/{s.domain_id}/export", params={"format": "json"}, headers=headers
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["domain"]["name"] == s.domain_name
    [topic] = data["topics"]
    assert {x["skill_id"] for x in topic["skills"]} == {str(s.skill_a), str(s.skill_b)}
    assert all("pMastery" in x and "masteryStatus" in x for x in topic["skills"])
    assert topic["cheatsheet"]["artifact_id"] == str(_cheatsheet_id(learner, s.topic_id))
    assert [d["artifact_id"] for d in data["digests"]] == [str(digest_id)]
    assert [n["artifact_id"] for n in data["notes"]] == [note_id]  # the deleted note is absent

    r = await client.get(f"/v1/domains/{foreign.domain_id}/export", headers=headers)
    assert r.status_code == 404, r.text
