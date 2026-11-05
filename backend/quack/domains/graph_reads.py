"""Reads of topics and hubs for the client (ТЗ Ф4 WP6.5, §6.4 «Граф и темы», «Хабы»).

Codes and parameters, not text (`Т-§22.2`): the state of a topic is `TopicDisplay`, levels are
statuses. Every read is of the learner's own nodes — another learner's id is «not found» (law
1). `topic_view` serves `GET /v1/topics/{id}` and `…/why` (the graph of a domain is
`quack.domains.graph_view`; D-A-198, D-A-207).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.events.log import NewEvent, append_events
from quack.graph.ids import deterministic_id
from quack.graph.read import create_graph_reader
from quack.graph.read.models import HubGoal, HubReadiness, TopicDisplay, WhyFacts

LEVELS = (1, 2, 3, 4, 5, 6)

_TOPIC = (
    "MATCH (t:Topic {id: $id}) WHERE t.learnerId = $learner "
    "OPTIONAL MATCH (t)-[:COVERS]->(s:Skill) "
    "RETURN properties(t) AS topic, collect(properties(s)) AS skills"
)
_PLACES = (
    "MATCH (t:Topic {id: $id})-[:COVERS]->(:Skill)<-[:ON|OF]-(x) WHERE t.learnerId = $learner "
    "AND ((x:Evidence AND x.status = 'active') OR x:Exposure) "
    "RETURN DISTINCT labels(x)[0] AS kind, x.sourceSeq AS seq, x.atMs AS at "
    "ORDER BY at, seq LIMIT $limit"
)
_OWN = {
    "Domain": "MATCH (n:Domain {id: $id}) WHERE n.learnerId = $learner RETURN count(n) AS n",
    "Hub": "MATCH (n:Hub {id: $id}) WHERE n.learnerId = $learner RETURN n.domainId AS domain",
}
_SESSIONS = (
    "SELECT seq, session_id FROM core.events "
    "WHERE learner_id = %(learner_id)s AND seq = ANY(%(seqs)s)"
)


class LevelView(BaseModel):
    level: int
    status: str
    basis: str


class SkillView(BaseModel):
    skill_id: UUID
    name: str
    levels: list[LevelView]
    ret_due_at_ms: int = 0


class Place(BaseModel):
    """«Места в сессиях» (`Т-§4.7`, `П-3.3`): an exposure or an evidence of a skill of the
    topic — its session and the number of its event."""

    kind: Literal["exposure", "evidence"]
    seq: int
    session_id: UUID | None = None
    at_ms: int = 0


class TopicView(BaseModel):
    topic_id: UUID
    domain_id: UUID
    name: str
    status: str
    visibility: str
    display: TopicDisplay
    nearest_due_at_ms: int = 0
    skills: list[SkillView] = []
    places: list[Place] = []


class HubView(BaseModel):
    goal: HubGoal
    readiness: HubReadiness


class CreateHub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    control_type: Literal["tasks", "colloquium_proofs", "oral_theory", "no_goal"] = "no_goal"
    idempotency_key: str = Field(min_length=1, max_length=200)


class HubCreated(BaseModel):
    hub_id: UUID
    seq: int


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _skill(props: dict[str, Any]) -> SkillView:
    levels = [
        LevelView(
            level=n,
            status=_str(props.get(f"l{n}Status"), "unknown"),
            basis=_str(props.get(f"l{n}Basis"), "none"),
        )
        for n in LEVELS
    ]
    return SkillView(
        skill_id=UUID(str(props["id"])),
        name=_str(props.get("name")),
        levels=levels,
        ret_due_at_ms=_int(props.get("retDueAtMs")),
    )


def topic_display(topic: dict[str, Any]) -> TopicDisplay:
    """The code and the parameters the projector wrote on a `Topic` (phase C, `Т-§22.2`)."""
    return TopicDisplay.model_validate(
        {
            "display_state": _str(topic.get("displayState"), "none"),
            "display_reason": _str(topic.get("displayReason")),
            "display_level": _int(topic.get("displayLevel")),
            "display_domain_id": _str(topic.get("displayDomainId")),
        }
    )


async def topic_view(
    db: Db, neo4j: AsyncDriver, learner_id: UUID, topic_id: UUID, *, limit: int = 200
) -> TopicView | None:
    """`limit` — how many places at most; the route of WP1.3 passes it from settings (D-A-198)."""
    learner = str(learner_id)
    async with read_session(neo4j) as session:
        result = await session.run(literal(_TOPIC), id=str(topic_id), learner=learner)
        record = await result.single()
        if record is None:
            return None
        result = await session.run(literal(_PLACES), id=str(topic_id), learner=learner, limit=limit)
        rows = [dict(r) async for r in result]
    topic: dict[str, Any] = dict(record["topic"])
    seqs = sorted({_int(r["seq"]) for r in rows})
    sessions = {
        int(r["seq"]): r["session_id"]
        for r in await db.fetch_all(_SESSIONS, {"learner_id": learner_id, "seqs": seqs})
    }
    places = [
        Place(
            kind="evidence" if r["kind"] == "Evidence" else "exposure",
            seq=_int(r["seq"]),
            session_id=sessions.get(_int(r["seq"])),
            at_ms=_int(r["at"]),
        )
        for r in rows
    ]
    display = topic_display(topic)
    skills = sorted(
        (_skill(dict(s)) for s in record["skills"] if s), key=lambda s: (s.name, str(s.skill_id))
    )
    return TopicView(
        topic_id=topic_id,
        domain_id=UUID(_str(topic.get("domainId"))),
        name=_str(topic.get("name")),
        status=_str(topic.get("status"), "proposed"),
        visibility=_str(topic.get("visibility"), "hidden"),
        display=display,
        nearest_due_at_ms=_int(topic.get("nearestDueAtMs")),
        skills=skills,
        places=places,
    )


async def _own(neo4j: AsyncDriver, learner_id: UUID, label: str, node_id: UUID) -> Any:
    async with read_session(neo4j) as session:
        result = await session.run(literal(_OWN[label]), id=str(node_id), learner=str(learner_id))
        record = await result.single()
    if record is None:
        return None
    value = record[0]
    return value if value else None


async def topic_why(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    learner_id: UUID,
    topic_id: UUID,
    level: int | None,
) -> WhyFacts | None:
    """`Т-§8.7`: the deciding evidence of the level and the latest `why_recent_n`."""
    if await topic_view(db, neo4j, learner_id, topic_id, limit=1) is None:
        return None
    reader = create_graph_reader(neo4j, settings, learner_id=learner_id, db=db)
    return await reader.why(topic_id, level)


async def domain_hubs(
    neo4j: AsyncDriver, settings: Settings, learner_id: UUID, domain_id: UUID
) -> list[HubGoal] | None:
    if await _own(neo4j, learner_id, "Domain", domain_id) is None:
        return None
    reader = create_graph_reader(neo4j, settings, learner_id=learner_id)
    return await reader.domain_goals(domain_id)


async def hub_view(
    neo4j: AsyncDriver, settings: Settings, learner_id: UUID, hub_id: UUID
) -> HubView | None:
    domain = await _own(neo4j, learner_id, "Hub", hub_id)
    if domain is None:
        return None
    reader = create_graph_reader(neo4j, settings, learner_id=learner_id)
    goal = next((g for g in await reader.domain_goals(UUID(domain)) if g.hub_id == hub_id), None)
    if goal is None:
        return None
    return HubView(goal=goal, readiness=await reader.topic_readiness(hub_id))


async def create_hub(
    db: Db,
    neo4j: AsyncDriver,
    redis: Redis | None,
    learner_id: UUID,
    domain_id: UUID,
    body: CreateHub,
) -> HubCreated | None:
    """`hub.created` of kind `request` from the learner (`Т-§4.8`): shown at once. The id is
    derived from the key, so a retry gives the same hub (law 5)."""
    if await _own(neo4j, learner_id, "Domain", domain_id) is None:
        return None
    hub = deterministic_id(learner_id, 0, f"hub:{body.idempotency_key}")
    payload = {"hub_id": hub, "domain_id": str(domain_id), "name": body.name, "kind": "request",
               "origin": "learner", "control_type": body.control_type}  # fmt: skip
    event = NewEvent(
        type="hub.created",
        payload=payload,
        actor="learner",
        idempotency_key=f"hub.created:{body.idempotency_key}",
        domain_id=domain_id,
    )
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return HubCreated(hub_id=UUID(hub), seq=stored.seq)
