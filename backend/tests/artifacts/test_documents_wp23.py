"""WP23.1: artifacts as versioned documents, notes in blocks, protection of learner edits
(ТЗ Ф4 WP23.1; Т-§17.1, Т-§17.2, Т-§2.3; П-11.1; D-C-125)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.ratelimit import enforce_action_rate_limit
from quack.artifacts.documents import (
    ArtifactNotFound,
    NotASupplement,
    SystemBlock,
    accept_supplement,
    artifact_id_for,
    delete_block,
    edit_block,
    find_artifact,
    get_artifact,
    version_blocks,
    write_system_blocks,
)
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token
from tests.support.resources import build_app_resources

WRITER = "artifact_writer@1"


async def _topic(db: Db, learner_id: UUID) -> UUID:
    topic_id, domain_id = uuid4(), uuid4()
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": topic_id,
                    "domain_id": domain_id,
                    "name": "Limits",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"topic:{topic_id}",
                domain_id=domain_id,
            )
        ],
    )
    return topic_id


async def _hub(db: Db, learner_id: UUID) -> UUID:
    hub_id, domain_id = uuid4(), uuid4()
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="hub.created",
                payload={
                    "hub_id": hub_id,
                    "domain_id": domain_id,
                    "name": "Exam",
                    "kind": "request",
                    "origin": "learner",
                    "control_type": "tasks",
                },
                actor="learner",
                idempotency_key=f"hub:{hub_id}",
                domain_id=domain_id,
            )
        ],
    )
    return hub_id


async def _updates(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT payload, actor, domain_id FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'artifact.updated' ORDER BY seq",
        {"learner_id": learner_id},
    )


def _sys(content: str, *, kind: str = "summary", block_id: UUID | None = None) -> SystemBlock:
    return SystemBlock.model_validate({"kind": kind, "content": content, "block_id": block_id})


async def test_each_save_is_a_version_and_an_event(pg_db: Db) -> None:
    """Т-§17.1, Т-§2.3: a save writes the version, moves `current_version` and appends
    `artifact.updated {artifact_id, version, kind, topic_id, text}` in one transaction; the
    same blocks again write nothing (law 5)."""
    learner_id = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner_id)

    empty = await get_artifact(pg_db, learner_id=learner_id, kind="notes", owner_id=topic_id)
    assert (empty.version, empty.blocks) == (0, ())
    assert empty.artifact_id == artifact_id_for(learner_id, "notes", topic_id)

    first = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=topic_id,
        blocks=[_sys("Limit is…"), _sys("$\\lim f$", kind="definition")],
        created_by=WRITER,
    )
    assert first.version == 1
    assert [b.content for b in first.blocks] == ["Limit is…", "$\\lim f$"]
    assert all(b.owner == "system" for b in first.blocks)

    again = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=topic_id,
        blocks=[_sys(b.content, kind=b.kind, block_id=b.block_id) for b in first.blocks],
        created_by=WRITER,
    )
    assert again.version == 1

    updates = await _updates(pg_db, learner_id)
    assert len(updates) == 1
    assert updates[0]["actor"] == WRITER
    assert updates[0]["payload"] == {
        "artifact_id": str(first.artifact_id),
        "version": 1,
        "kind": "notes",
        "topic_id": str(topic_id),
        "hub_id": None,
        "text": "Limit is…\n\n$\\lim f$",
    }
    read = await get_artifact(pg_db, learner_id=learner_id, kind="notes", owner_id=topic_id)
    assert read == first
    assert updates[0]["domain_id"] == read.domain_id


async def test_learner_block_is_never_rewritten(pg_db: Db) -> None:
    """Т-§17.2, П-11.1: the edited block becomes `owner = learner`; new knowledge for it is a
    supplement right after it; the system blocks nobody names are replaced, the learner's
    stay."""
    learner_id = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner_id)
    v1 = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=topic_id,
        blocks=[_sys("A"), _sys("B", kind="why")],
        created_by=WRITER,
    )
    a, b = v1.blocks
    v2 = await edit_block(
        pg_db,
        learner_id=learner_id,
        artifact_id=v1.artifact_id,
        block_id=a.block_id,
        content="A, my words",
        idempotency_key="k1",
    )
    assert v2.version == 2
    assert (v2.blocks[0].owner, v2.blocks[0].content, v2.blocks[0].kind) == (
        "learner",
        "A, my words",
        "summary",
    )

    v3 = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=topic_id,
        blocks=[
            _sys("A, more", block_id=a.block_id),
            _sys("B2", kind="why", block_id=b.block_id),
            _sys("C", kind="my_traps"),
        ],
        created_by=WRITER,
    )
    contents = [(blk.owner, blk.content) for blk in v3.blocks]
    assert contents == [
        ("learner", "A, my words"),
        ("system", "A, more"),
        ("system", "B2"),
        ("system", "C"),
    ]
    assert v3.blocks[1].supplement_to == a.block_id
    assert v3.blocks[2].block_id == b.block_id

    v4 = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=topic_id,
        blocks=[_sys("D")],
        created_by=WRITER,
    )
    # B2 and C are dropped (not named); the learner block and its open supplement stay.
    assert [blk.content for blk in v4.blocks] == ["A, my words", "A, more", "D"]


async def test_accept_merges_and_delete_removes(pg_db: Db) -> None:
    """П-11.1: the learner accepts a supplement (merged into the own block) or deletes it; a new
    `block_id` from the learner is a `custom` block of the learner; old versions stay."""
    learner_id = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner_id)
    notes = await get_artifact(pg_db, learner_id=learner_id, kind="notes", owner_id=topic_id)
    mine = uuid4()
    v1 = await edit_block(
        pg_db,
        learner_id=learner_id,
        artifact_id=notes.artifact_id,
        block_id=mine,
        content="X",
        idempotency_key="k2",
    )
    assert [(b.block_id, b.kind, b.owner) for b in v1.blocks] == [(mine, "custom", "learner")]

    v2 = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="notes",
        owner_id=topic_id,
        blocks=[_sys("Y", block_id=mine), _sys("Z", block_id=mine)],
        created_by=WRITER,
    )
    supplement_y, supplement_z = v2.blocks[1].block_id, v2.blocks[2].block_id
    with pytest.raises(NotASupplement):
        await accept_supplement(
            pg_db,
            learner_id=learner_id,
            artifact_id=notes.artifact_id,
            block_id=mine,
            idempotency_key="k3",
        )
    v3 = await accept_supplement(
        pg_db,
        learner_id=learner_id,
        artifact_id=notes.artifact_id,
        block_id=supplement_y,
        idempotency_key="k4",
    )
    assert [(b.content, b.owner) for b in v3.blocks] == [("X\n\nY", "learner"), ("Z", "system")]
    v4 = await delete_block(
        pg_db,
        learner_id=learner_id,
        artifact_id=notes.artifact_id,
        block_id=supplement_z,
        idempotency_key="k5",
    )
    assert [b.content for b in v4.blocks] == ["X\n\nY"]
    assert v4.version == 4

    old = await version_blocks(
        pg_db, learner_id=learner_id, artifact_id=notes.artifact_id, version=2
    )
    assert [b.content for b in old] == ["X", "Y", "Z"]
    with pytest.raises(ArtifactNotFound):
        await delete_block(
            pg_db,
            learner_id=learner_id,
            artifact_id=notes.artifact_id,
            block_id=uuid4(),
            idempotency_key="k6",
        )


async def test_cheatsheet_belongs_to_a_hub(pg_db: Db) -> None:
    """WP23.1: a hub cheat sheet carries `hub_id`; topic artifacts — `topic_id`."""
    learner_id = await create_learner(pg_db)
    hub_id = await _hub(pg_db, learner_id)
    sheet = await write_system_blocks(
        pg_db,
        learner_id=learner_id,
        kind="cheatsheet",
        owner_id=hub_id,
        blocks=[_sys("$a^2+b^2=c^2$", kind="statement")],
        created_by=WRITER,
    )
    assert (sheet.hub_id, sheet.topic_id) == (hub_id, None)
    (update,) = await _updates(pg_db, learner_id)
    assert update["payload"]["hub_id"] == str(hub_id)
    with pytest.raises(ArtifactNotFound):  # a hub is not a topic
        await get_artifact(pg_db, learner_id=learner_id, kind="notes", owner_id=hub_id)


async def test_other_learner_sees_nothing(pg_db: Db) -> None:
    """Isolation (§4.3): another learner's topic and artifact are unknown."""
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    topic_id = await _topic(pg_db, owner)
    notes = await get_artifact(pg_db, learner_id=owner, kind="notes", owner_id=topic_id)
    with pytest.raises(ArtifactNotFound):
        await get_artifact(pg_db, learner_id=other, kind="notes", owner_id=topic_id)
    with pytest.raises(ArtifactNotFound):
        await find_artifact(pg_db, learner_id=other, artifact_id=notes.artifact_id)
    with pytest.raises(ArtifactNotFound):
        await edit_block(
            pg_db,
            learner_id=other,
            artifact_id=notes.artifact_id,
            block_id=uuid4(),
            content="mine",
            idempotency_key="k7",
        )


@pytest.fixture
def notes_client(settings: Settings, pg_db: Db) -> Callable[[UUID], Awaitable[httpx.AsyncClient]]:
    """The API over `pg_db`, signed in with a real Phase 4 session of the learner (D-C-119);
    without Redis the `actions` limit is off."""

    async def make(learner_id: UUID) -> httpx.AsyncClient:
        app = create_app(settings)
        app.state.resources = build_app_resources(settings, None, None, db=pg_db)
        app.dependency_overrides[enforce_action_rate_limit] = lambda: None
        cookie = session_cookie(await session_token(pg_db, learner_id, settings))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=cookie
        )

    return make


async def test_notes_routes(
    pg_db: Db, notes_client: Callable[[UUID], Awaitable[httpx.AsyncClient]]
) -> None:
    """§6.4 «Артефакты»: read the notes, write and edit a block, accept a supplement, delete;
    another learner — 404, accepting a non-supplement — 422."""
    learner_id, other = await create_learner(pg_db), await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner_id)
    async with await notes_client(learner_id) as client:
        notes = (await client.get(f"/v1/topics/{topic_id}/notes")).json()
        assert (notes["version"], notes["blocks"], notes["kind"]) == (0, [], "notes")
        base = f"/v1/artifacts/{notes['artifact_id']}/blocks"
        mine = uuid4()
        edit = {"content": "my note", "idempotency_key": "put-1"}
        put = await client.put(f"{base}/{mine}", json=edit)
        assert put.status_code == 200
        assert put.json()["blocks"] == [
            {
                "block_id": str(mine),
                "kind": "custom",
                "content": "my note",
                "owner": "learner",
                "sources": [],
                "skills": [],
                "generatedBy": None,
                "supplementTo": None,
                "basis": None,
                "unverified": False,
            }
        ]
        # §6.4, T-E2: the repeat answers the same and writes nothing; another body — 409.
        again = await client.put(f"{base}/{mine}", json=edit)
        assert again.json() == put.json()
        conflict = await client.put(f"{base}/{mine}", json={**edit, "content": "other"})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        await write_system_blocks(
            pg_db,
            learner_id=learner_id,
            kind="notes",
            owner_id=topic_id,
            blocks=[_sys("more", block_id=mine)],
            created_by=WRITER,
        )
        listed = (await client.get(f"/v1/topics/{topic_id}/notes")).json()
        supplement = listed["blocks"][1]["block_id"]
        key = {"idempotency_key": "accept-1"}
        wrong = await client.post(f"{base}/{mine}/accept", json={"idempotency_key": "accept-0"})
        assert wrong.status_code == 422
        accepted = await client.post(f"{base}/{supplement}/accept", json=key)
        assert (await client.post(f"{base}/{supplement}/accept", json=key)).json() == (
            accepted.json()
        )
        assert [b["content"] for b in accepted.json()["blocks"]] == ["my note\n\nmore"]
        drop = {"idempotency_key": "delete-1"}
        deleted = await client.request("DELETE", f"{base}/{mine}", json=drop)
        assert (deleted.json()["version"], deleted.json()["blocks"]) == (4, [])
        repeat = await client.request("DELETE", f"{base}/{mine}", json=drop)
        assert repeat.json() == deleted.json()
        gone = await client.request("DELETE", f"{base}/{mine}", json={"idempotency_key": "d2"})
        assert gone.status_code == 404
    async with await notes_client(other) as client:
        assert (await client.get(f"/v1/topics/{topic_id}/notes")).status_code == 404
        foreign = {"content": "x", "idempotency_key": "put-1"}
        assert (await client.put(f"{base}/{uuid4()}", json=foreign)).status_code == 404


async def test_repeated_generator_run_writes_nothing(pg_db: Db) -> None:
    """Law 5 (review B #138): the generator job repeated with the same blocks — a new block and
    a supplement to a learner block — gives the same ids and no new version."""
    learner_id = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner_id)
    notes = await get_artifact(pg_db, learner_id=learner_id, kind="notes", owner_id=topic_id)
    mine = uuid4()
    await edit_block(
        pg_db,
        learner_id=learner_id,
        artifact_id=notes.artifact_id,
        block_id=mine,
        content="mine",
        idempotency_key="own",
    )
    run = [_sys("more", block_id=mine), _sys("new", kind="why")]
    first = await write_system_blocks(
        pg_db, learner_id=learner_id, kind="notes", owner_id=topic_id, blocks=run,
        created_by=WRITER,
    )  # fmt: skip
    again = await write_system_blocks(
        pg_db, learner_id=learner_id, kind="notes", owner_id=topic_id, blocks=run,
        created_by=WRITER,
    )  # fmt: skip
    assert (first.version, again.version) == (2, 2)
    assert again.blocks == first.blocks
