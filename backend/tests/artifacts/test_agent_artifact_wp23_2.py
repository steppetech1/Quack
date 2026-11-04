"""WP23.2: the job `agent.artifact` — `agent.requested {kind: artifact}` for the notes or the cards
of a topic (ТЗ Ф4 WP23.2, §6.5; issue #263; D-C-230). The body is `refresh_topic_*` of the
`artifact.topic_refresh` job; the answer is `agent.completed` under the key of the request."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from quack.artifacts.agent_artifact import answer_artifact_request
from quack.artifacts.cards import topic_cards
from quack.artifacts.documents import artifact_id_for, get_artifact
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import AgentRequested
from quack.events.log import NewEvent, append_events
from quack.events.records import AgentParamsRecord
from quack.jobs.runner import JOB_HANDLERS, JobContext
from tests.artifacts.test_cards_generation_wp23 import _card
from tests.artifacts.test_topic_refresh_wp23 import DOMAIN, TopicReader, _prefetch
from tests.db.fixtures import create_learner
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

BLOCK = {"updates": None, "kind": "definition", "content": "A limit…", "sources": [],
         "skills": ["S1"]}  # fmt: skip


async def _topic(db: Db, learner: UUID) -> UUID:
    topic_id = uuid4()
    await append_events(
        db,
        learner,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": topic_id, "domain_id": DOMAIN, "name": "Limits", "origin": "session"
                },
                actor="code",
                idempotency_key=f"topic:{topic_id}",
                domain_id=DOMAIN,
            )
        ],
    )  # fmt: skip
    return topic_id


async def _request(
    db: Db, learner: UUID, artifact: str | None, topic_id: UUID | None, hub_id: UUID | None = None
) -> UUID:
    request_id = uuid4()
    params = AgentParamsRecord.model_validate(
        {"artifact": artifact, "topic_id": topic_id, "hub_id": hub_id}
    )
    await append_events(
        db,
        learner,
        [
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=request_id, kind="artifact", params=params, needed_in_session=False
                ),
                actor="learner",
                idempotency_key=f"agent.requested:{request_id}",
                domain_id=DOMAIN,
            )
        ],
    )
    return request_id


async def _completed(db: Db, learner: UUID) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT payload, causation_seq, actor FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'agent.completed' ORDER BY seq",
        {"l": learner},
    )
    return list(rows)


def _ctx(db: Db, settings: Settings, provider: ScriptedLLMProvider) -> JobContext:
    resources = build_app_resources(settings, None, None, db=db, provider=provider)
    return cast(
        JobContext,
        SimpleNamespace(db=db, llm=resources.llm, settings=settings, redis=None, compute=None),
    )


async def test_notes_request_writes_notes_and_answers_once(pg_db: Db, settings: Settings) -> None:
    """The request for the notes of a topic: a version of the notes, `agent.completed {ok}` with
    the artifact as the result; a repeat finds the answer — no second call, no second version."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    provider = ScriptedLLMProvider()
    provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [BLOCK], "cards": []})
    ctx = _ctx(pg_db, settings, provider)
    request_id = await _request(pg_db, learner, "notes", topic_id)
    reader = TopicReader(topic_id, 1)

    for _ in range(2):
        await answer_artifact_request(
            ctx, reader, _prefetch, learner_id=learner, request_id=request_id
        )

    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert notes.version == 1 and notes.blocks[0].kind == "definition"
    (done,) = await _completed(pg_db, learner)
    body = done["payload"]
    assert (body["ok"], body["kind"], body["request_id"]) == (True, "artifact", str(request_id))
    assert body["result_ref"] == str(artifact_id_for(learner, "notes", topic_id))
    assert done["causation_seq"] is not None
    assert len(provider.calls_for(schema_name="ArtifactWriterOutput")) == 1  # not for cards


async def test_cards_request_writes_cards_only(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    provider = ScriptedLLMProvider()
    provider.respond(
        "ArtifactWriterOutput", lambda _r: {"blocks": [BLOCK], "cards": [_card("When?")]}
    )
    ctx = _ctx(pg_db, settings, provider)
    request_id = await _request(pg_db, learner, "cards", topic_id)

    await answer_artifact_request(
        ctx, TopicReader(topic_id, 1), _prefetch, learner_id=learner, request_id=request_id
    )

    cards = await topic_cards(pg_db, learner_id=learner, topic_id=topic_id)
    assert [c.prompt for c in cards.cards] == ["When?"]
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert notes.version == 0
    (done,) = await _completed(pg_db, learner)
    assert done["payload"]["ok"] is True
    assert done["payload"]["result_ref"] == str(artifact_id_for(learner, "cards", topic_id))


async def test_a_request_that_cannot_succeed_is_answered_not_ok(
    pg_db: Db, settings: Settings
) -> None:
    """No topic, a cheat sheet (its own job `artifact.cheatsheet`), a topic the graph does not
    know — `ok: false` with the reason, no call of the model for the first two, nothing written."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    provider = ScriptedLLMProvider()
    provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [BLOCK], "cards": []})
    ctx = _ctx(pg_db, settings, provider)
    reader = TopicReader(topic_id, 1)

    no_topic = await _request(pg_db, learner, "notes", None)
    sheet = await _request(pg_db, learner, "cheatsheet", None, hub_id=uuid4())
    for request_id in (no_topic, sheet):
        await answer_artifact_request(
            ctx, reader, _prefetch, learner_id=learner, request_id=request_id
        )
    assert len(provider.calls) == 0

    unknown = await _request(pg_db, learner, "notes", uuid4())
    await answer_artifact_request(
        ctx, reader, _prefetch, learner_id=learner, request_id=unknown
    )
    answers = [(a["payload"]["ok"], a["payload"]["error"]) for a in await _completed(pg_db, learner)]
    assert answers == [
        (False, "topic_required"), (False, "unsupported_artifact"), (False, "nothing_written")
    ]  # fmt: skip
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert notes.version == 0


async def test_a_failed_generation_commits_nothing(pg_db: Db, settings: Settings) -> None:
    """Law 7: a model that fails raises out of the handler (the queue retries); no
    `agent.completed`, no version."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    provider = ScriptedLLMProvider()  # nothing scripted: the call fails
    ctx = _ctx(pg_db, settings, provider)
    request_id = await _request(pg_db, learner, "notes", topic_id)

    failed = False
    try:
        await answer_artifact_request(
            ctx, TopicReader(topic_id, 1), _prefetch, learner_id=learner, request_id=request_id
        )
    except Exception:
        failed = True
    assert failed
    assert await _completed(pg_db, learner) == []
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert notes.version == 0


def test_the_handler_is_registered() -> None:
    from quack.runtime import register_all_handlers

    handlers = register_all_handlers()
    assert handlers["agent.artifact"].__module__ == "quack.artifacts.agent_artifact"
    assert JOB_HANDLERS["agent.artifact"] is handlers["agent.artifact"]
