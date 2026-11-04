"""WP23.6: skills of the learner and the system templates (ТЗ Ф4 WP23.6; Т-§17.8, П-11.6;
D-C-216)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
import pytest

from quack.api.app import create_app
from quack.api.ratelimit import enforce_action_rate_limit
from quack.artifacts.documents import get_artifact
from quack.artifacts.learner_skills import (
    SkillConflict,
    SkillNotFound,
    bound_skill,
    create_skill,
    delete_skill,
    list_skills,
    update_skill,
)
from quack.artifacts.skills_routes import SYSTEM_TEMPLATE_ROLES
from quack.artifacts.topic_refresh import refresh_topic_notes, writer_input
from quack.config import Settings
from quack.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.prompts.registry import PromptRegistry
from quack.session.turn_inputs import load_learner_skill
from tests.artifacts.test_topic_refresh_wp23 import (
    TopicReader,
    _prefetch,  # pyright: ignore[reportPrivateUsage]
    _topic,  # pyright: ignore[reportPrivateUsage]
)
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

TEXT = "Answer in two sentences."


async def _make(db: Db, learner: UUID, key: str = "k1", **changes: object) -> UUID:
    fields: dict[str, object] = {
        "name": "brief",
        "instruction": TEXT,
        "artifact_types": ("notes",),
    } | changes
    record = await create_skill(
        db,
        learner_id=learner,
        idempotency_key=key,
        **fields,  # pyright: ignore[reportArgumentType]
    )
    return record.skill_id


async def test_create_is_idempotent_and_a_new_request_under_the_key_conflicts(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    first = await _make(pg_db, learner)
    assert await _make(pg_db, learner) == first
    assert [s.version for s in await list_skills(pg_db, learner_id=learner)] == [1]
    with pytest.raises(IdempotencyConflictError):
        await _make(pg_db, learner, instruction="Other")


async def test_name_and_artifact_type_belong_to_one_skill(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    await _make(pg_db, learner)
    with pytest.raises(SkillConflict, match="/brief"):
        await _make(pg_db, learner, "k2", artifact_types=())
    with pytest.raises(SkillConflict, match="notes"):
        await _make(pg_db, learner, "k3", name="other")
    await _make(pg_db, learner, "k4", name="other", artifact_types=("cards",))
    assert [s.name for s in await list_skills(pg_db, learner_id=learner)] == ["brief", "other"]


async def test_a_change_is_a_new_version_and_a_repeat_writes_nothing(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    skill_id = await _make(pg_db, learner)
    changed = await update_skill(
        pg_db, learner_id=learner, skill_id=skill_id, instruction="Be long"
    )
    assert (changed.version, changed.instruction) == (2, "Be long")
    again = await update_skill(pg_db, learner_id=learner, skill_id=skill_id, instruction="Be long")
    assert again.version == 2
    bound = await bound_skill(pg_db, learner_id=learner, artifact_type="notes")
    assert bound is not None and (bound.text, bound.version) == ("Be long", 2)
    assert await bound_skill(pg_db, learner_id=learner, artifact_type="cards") is None


async def test_the_chat_finds_a_skill_by_its_current_name(pg_db: Db) -> None:
    """T-CX6 side of C: stream B reads `/name` by name; a renamed skill answers to the new name
    only, with its latest text, and a deleted one to none."""
    learner = await create_learner(pg_db)
    skill_id = await _make(pg_db, learner)
    await update_skill(pg_db, learner_id=learner, skill_id=skill_id, instruction="Be long")
    found = await load_learner_skill(pg_db, learner_id=learner, name="brief")
    assert found is not None and found.text == "Be long"
    await update_skill(pg_db, learner_id=learner, skill_id=skill_id, name="short")
    assert await load_learner_skill(pg_db, learner_id=learner, name="brief") is None
    renamed = await load_learner_skill(pg_db, learner_id=learner, name="short")
    assert renamed is not None and renamed.text == "Be long"
    await delete_skill(pg_db, learner_id=learner, skill_id=skill_id)
    assert await load_learner_skill(pg_db, learner_id=learner, name="short") is None


async def test_another_learner_cannot_touch_a_skill(pg_db: Db) -> None:
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    skill_id = await _make(pg_db, learner)
    with pytest.raises(SkillNotFound):
        await update_skill(pg_db, learner_id=other, skill_id=skill_id, instruction="x")
    with pytest.raises(SkillNotFound):
        await delete_skill(pg_db, learner_id=other, skill_id=skill_id)
    assert await list_skills(pg_db, learner_id=other) == []
    assert await bound_skill(pg_db, learner_id=other, artifact_type="notes") is None


async def test_artifact_is_written_under_the_skill_and_is_not_regenerated(
    pg_db: Db, settings: Settings
) -> None:
    """Т-§17.8: the artifact records the version of the skill; changing the skill rewrites
    nothing, a later refresh is written under the new version."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    skill_id = await _make(pg_db, learner)
    reader = TopicReader(topic_id, 1)
    context = await writer_input(
        pg_db, reader, _prefetch, settings, learner_id=learner, topic_id=topic_id
    )
    assert context is not None
    assert context.variables["learner_skill"] == TEXT and context.skill_version == 1

    block = {"updates": None, "kind": "definition", "content": "A limit…", "sources": ["P1"],
             "skills": ["S1"]}  # fmt: skip
    provider = ScriptedLLMProvider()
    provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [block], "cards": []})
    resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)

    async def refresh(key: str) -> int | None:
        return await refresh_topic_notes(
            resources.db, resources.llm, reader, _prefetch, settings,
            learner_id=learner, topic_id=topic_id, key=key,
        )  # fmt: skip

    async def recorded() -> list[int | None]:
        rows = await pg_db.fetch_all(
            "SELECT skill_version FROM content.artifact_versions WHERE learner_id = %(l)s "
            "ORDER BY version",
            {"l": learner},
        )
        return [row["skill_version"] for row in rows]

    assert await refresh("job-1") == 1
    await update_skill(pg_db, learner_id=learner, skill_id=skill_id, instruction="Be long")
    assert await recorded() == [1]  # nothing regenerated
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert notes.version == 1
    block["content"] = "A limit is a number the terms approach."
    assert await refresh("job-2") == 2
    assert await recorded() == [1, 2]


async def test_without_a_skill_the_slot_says_none_and_no_version_is_recorded(
    pg_db: Db, settings: Settings
) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    context = await writer_input(
        pg_db, TopicReader(topic_id, 1), _prefetch, settings, learner_id=learner, topic_id=topic_id
    )
    assert context is not None
    assert context.variables["learner_skill"] == "None." and context.skill_version is None


@pytest.fixture
def skills_client(settings: Settings, pg_db: Db) -> Callable[[UUID], Awaitable[httpx.AsyncClient]]:
    async def make(learner_id: UUID) -> httpx.AsyncClient:
        app = create_app(settings)
        app.state.resources = build_app_resources(settings, None, None, db=pg_db)
        app.dependency_overrides[enforce_action_rate_limit] = lambda: None
        cookie = session_cookie(await session_token(pg_db, learner_id, settings))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=cookie
        )

    return make


async def test_skills_routes(
    pg_db: Db, skills_client: Callable[[UUID], Awaitable[httpx.AsyncClient]]
) -> None:
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    body = {
        "idempotency_key": "k1",
        "name": " /Brief ",
        "instruction": TEXT,
        "bindings": {"artifact_types": ["notes"]},
    }
    async with await skills_client(learner) as client:
        created = await client.post("/v1/learner-skills", json=body)
        assert created.status_code == 201
        view = created.json()
        assert view["name"] == "brief" and view["version"] == 1
        assert view["bindings"] == {"artifact_types": ["notes"]}
        assert (await client.post("/v1/learner-skills", json=body)).json() == view
        assert (
            await client.post("/v1/learner-skills", json={**body, "instruction": "x"})
        ).status_code == 409
        taken = {**body, "idempotency_key": "k2", "bindings": {"artifact_types": []}}
        assert (await client.post("/v1/learner-skills", json=taken)).status_code == 409
        for bad in (
            {**body, "idempotency_key": "k3", "name": "two words"},
            {**body, "idempotency_key": "k3", "name": "x", "instruction": "  "},
            {**body, "idempotency_key": "k3", "name": "x", "instruction": "a" * 5000},
            {**body, "idempotency_key": "k3", "name": "x", "bindings": {"artifact_types": ["z"]}},
        ):
            assert (await client.post("/v1/learner-skills", json=bad)).status_code == 422
        path = f"/v1/learner-skills/{view['skill_id']}"
        patched = await client.patch(path, json={"instruction": "Be long"})
        assert patched.json()["version"] == 2
        assert (await client.patch(path, json={"instruction": "Be long"})).json() == patched.json()
        listed = (await client.get("/v1/learner-skills")).json()
        assert [s["version"] for s in listed["skills"]] == [2]
    async with await skills_client(other) as client:
        assert (await client.get("/v1/learner-skills")).json() == {"skills": []}
        assert (await client.patch(path, json={"instruction": "x"})).status_code == 404
        assert (await client.delete(path)).status_code == 404
    async with await skills_client(learner) as client:
        assert (await client.delete(path)).json() == {"skills": []}
        assert (await client.delete(path)).status_code == 404
        assert (await client.get("/v1/learner-skills")).json() == {"skills": []}


async def test_system_templates_are_the_prompt_files_read_only(
    pg_db: Db, skills_client: Callable[[UUID], Awaitable[httpx.AsyncClient]]
) -> None:
    learner = await create_learner(pg_db)
    async with await skills_client(learner) as client:
        response = await client.get("/v1/system-templates")
        assert response.status_code == 200
        templates = response.json()["templates"]
        assert {t["role"] for t in templates} == set(SYSTEM_TEMPLATE_ROLES)
        writer = next(t for t in templates if t["role"] == "artifact_writer")
        loaded = PromptRegistry().load("artifact_writer", writer["version"])
        assert (writer["text"], writer["sha256"]) == (loaded.text, loaded.sha256)
        for method in ("post", "put", "patch", "delete"):
            sent = await client.request(method, "/v1/system-templates")
            assert sent.status_code in (404, 405)
