"""Edits of topics and «я это уже знаю» (ТЗ Ф4 WP8.2; Т-§4.11).

Each edit checks that the topic (and the target topic, the episode) is the learner's — `404`
otherwise (law 1) — and appends one event; the projector and the dispatcher do the rest
(`topic.merged` — skills to the target and an alias, `episode.reassigned` — re-extraction,
`known.claimed` — `check.known_claim`). The requests of §6.4 carry no idempotency key, so the
key is the edit itself with the state it starts from: a retry gives the same event, a later
edit of the same kind a new one (law 5).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Self
from uuid import UUID

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field, model_validator
from redis.asyncio import Redis

from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.events.log import NewEvent, append_events
from quack.events.values import Level

_TOPIC = (
    "MATCH (t:Topic {id: $id}) WHERE t.learnerId = $learner "
    "RETURN t.name AS name, t.domainId AS domain, t.knownClaimedAtMs AS known"
)
_EPISODE = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND seq = %(seq)s AND type = 'episode.closed'"
)


class RenameTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class MergeTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    into: UUID


class ReassignEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_id: UUID


class Edited(BaseModel):
    """The number of the appended event."""

    seq: int


async def _topic(neo4j: AsyncDriver, learner_id: UUID, topic_id: UUID) -> dict[str, Any] | None:
    async with read_session(neo4j) as session:
        result = await session.run(literal(_TOPIC), id=str(topic_id), learner=str(learner_id))
        record = await result.single()
    return None if record is None else dict(record)


async def _append(
    db: Db, redis: Redis | None, learner_id: UUID, kind: str, payload: dict[str, Any], key: str
) -> int:
    event = NewEvent(type=kind, payload=payload, actor="learner", idempotency_key=key)
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return stored.seq


async def rename(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, topic_id: UUID, name: str
) -> int | None:
    topic = await _topic(neo4j, learner_id, topic_id)
    if topic is None:
        return None
    payload = {"topic_id": str(topic_id), "name": name}
    key = f"topic.renamed:{topic_id}:{topic['name']}->{name}"
    return await _append(db, redis, learner_id, "topic.renamed", payload, key)


async def merge(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, topic_id: UUID, into: UUID
) -> int | None:
    """None — either topic is not the learner's, they are one topic or of different domains."""
    source = await _topic(neo4j, learner_id, topic_id)
    target = await _topic(neo4j, learner_id, into)
    if source is None or target is None or topic_id == into:
        return None
    if source["domain"] != target["domain"]:
        return None
    payload = {"from_id": str(topic_id), "into_id": str(into)}
    key = f"topic.merged:{topic_id}:{into}"
    return await _append(db, redis, learner_id, "topic.merged", payload, key)


async def hide(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, topic_id: UUID
) -> int | None:
    if await _topic(neo4j, learner_id, topic_id) is None:
        return None
    payload = {"topic_id": str(topic_id)}
    return await _append(db, redis, learner_id, "topic.hidden", payload, f"topic.hidden:{topic_id}")


async def claim_known(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, topic_id: UUID
) -> int | None:
    topic = await _topic(neo4j, learner_id, topic_id)
    if topic is None:
        return None
    key = f"known.claimed:{topic_id}:{topic['known'] or 0}"  # a later claim after a check: new
    return await _append(db, redis, learner_id, "known.claimed", {"topic_id": str(topic_id)}, key)


async def reassign(
    db: Db,
    neo4j: AsyncDriver,
    redis: Redis | None,
    learner_id: UUID,
    episode_seq: int,
    topic_id: UUID,
) -> int | None:
    """The episode is an `episode.closed` of the learner (its `seq`); the topic is theirs."""
    found = await db.fetch_one(_EPISODE, {"learner_id": learner_id, "seq": episode_seq})
    if found is None or await _topic(neo4j, learner_id, topic_id) is None:
        return None
    payload = {"episode_seq": episode_seq, "topic_id": str(topic_id)}
    key = f"episode.reassigned:{episode_seq}:{topic_id}"
    return await _append(db, redis, learner_id, "episode.reassigned", payload, key)


# --- WP6.3: scope and deadline of a hub (`PATCH /v1/hubs/{id}`, Т-§4.8) ---------------------

_HUB = (
    "MATCH (h:Hub {id: $id}) WHERE h.learnerId = $learner "
    "OPTIONAL MATCH (h)-[i:INCLUDES]->(t:Topic) "
    "RETURN h.domainId AS domain, h.deadlineAtMs AS deadline, "
    "collect([t.id, i.requiredLevels]) AS topics"
)
_TOPICS_OF_DOMAIN = (
    "MATCH (t:Topic) WHERE t.learnerId = $learner AND t.domainId = $domain AND t.id IN $ids "
    "RETURN count(t) AS n"
)


class HubTopicLevels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_id: UUID
    required_levels: list[Level] = Field(default_factory=list)


class EditHub(BaseModel):
    """`topics` — the whole new scope with required levels (empty levels — by `controlType`,
    WP6.3); `deadline_at_ms` — the new date, `null` removes it. At least one of them."""

    model_config = ConfigDict(extra="forbid")
    topics: list[HubTopicLevels] | None = None
    deadline_at_ms: int | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not ({"topics", "deadline_at_ms"} & self.model_fields_set):
            raise ValueError("nothing to change: give topics or deadline_at_ms")
        return self


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


async def edit_hub(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, hub_id: UUID, body: EditHub
) -> int | None:
    """`hub.topics_changed` and/or `hub.deadline_set {source: learner}`; None — the hub or a
    topic is not the learner's, or a topic is of another domain. Returns the last event."""
    async with read_session(neo4j) as session:
        result = await session.run(literal(_HUB), id=str(hub_id), learner=str(learner_id))
        hub = await result.single()
        if hub is None:
            return None
        if body.topics is not None:
            ids = sorted({str(t.topic_id) for t in body.topics})
            result = await session.run(
                literal(_TOPICS_OF_DOMAIN), learner=str(learner_id), domain=hub["domain"], ids=ids
            )
            found = await result.single()
            if found is None or int(found["n"]) != len(ids):
                return None
    seq: int | None = None
    if body.topics is not None:
        now = sorted([str(t), sorted(levels or [])] for t, levels in hub["topics"] if t)
        new = sorted([str(t.topic_id), sorted(t.required_levels)] for t in body.topics)
        payload = {"hub_id": str(hub_id), "topics": [
            {"topic_id": t, "required_levels": levels} for t, levels in new]}  # fmt: skip
        key = f"hub.topics_changed:{hub_id}:{_digest(now)}->{_digest(new)}"
        seq = await _append(db, redis, learner_id, "hub.topics_changed", payload, key)
    if "deadline_at_ms" in body.model_fields_set:
        payload = {
            "hub_id": str(hub_id),
            "deadline_at_ms": body.deadline_at_ms,
            "source": "learner",
        }
        key = f"hub.deadline_set:{hub_id}:{hub['deadline'] or 0}->{body.deadline_at_ms}"
        seq = await _append(db, redis, learner_id, "hub.deadline_set", payload, key)
    return seq
