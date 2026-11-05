"""The map of a domain for the client: `GET /v1/domains/{id}/graph` (ТЗ Ф4 WP6.5, §6.4, T-TH5;
Т-§4.7, Т-§22.2).

The state of a topic is `TopicDisplay`: the code and its parameters, written by the projector
(phase C). The server builds no text and recomputes nothing at read time: «пора повторить» is
shown by the client from `nearest_due_at_ms` and its own clock (`Т-§8.6`). Topic-to-topic
`REQUIRES` is not stored; the reader counts it from the skills (`Т-§3.4.3`). The domain is
the learner's by its `domain.created` in the log, so a just created domain that is not
projected yet is an empty map, not «not found» (law 1).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.domains.graph_reads import topic_display
from quack.domains.schemas import DomainGraphResponse, TopicNodeView
from quack.graph.projection.store import read_watermark
from quack.graph.read import create_graph_reader

_OWN_DOMAIN = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'domain.created' AND payload ->> 'domain_id' = %(domain_id)s LIMIT 1"
)
_LAST_SEQ = "SELECT seq FROM core.learners WHERE learner_id = %(learner_id)s"
_TOPICS = (
    "MATCH (d:Domain {id: $domain}) WHERE d.learnerId = $learner "
    "MATCH (d)-[:HAS_TOPIC]->(t:Topic) WHERE t.learnerId = $learner "
    "OPTIONAL MATCH (t)-[:COVERS]->(s:Skill) "
    "RETURN properties(t) AS topic, count(s) AS skills"
)


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _node(topic: dict[str, Any], skills: int) -> TopicNodeView:
    return TopicNodeView(
        topic_id=UUID(_str(topic.get("id"))),
        name=_str(topic.get("name")),
        status=_str(topic.get("status"), "proposed"),
        visibility=_str(topic.get("visibility"), "hidden"),
        display=topic_display(topic),
        nearest_due_at_ms=_int(topic.get("nearestDueAtMs")),
        skill_count=skills,
    )


async def domain_graph(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    *,
    include_hidden: bool = False,
) -> DomainGraphResponse | None:
    """The topics of the domain with their codes and the `REQUIRES` between topics; `None` — the
    domain is not the learner's (`404`). Hidden topics are left out unless `include_hidden`."""
    params = {"learner_id": learner_id, "domain_id": str(domain_id)}
    if await db.fetch_one(_OWN_DOMAIN, params) is None:
        return None
    async with read_session(neo4j) as session:
        result = await session.run(literal(_TOPICS), domain=str(domain_id), learner=str(learner_id))
        rows = [dict(r) async for r in result]
    nodes = sorted(
        (_node(dict(r["topic"]), _int(r["skills"])) for r in rows),
        key=lambda n: (n.name, str(n.topic_id)),
    )
    if not include_hidden:
        nodes = [n for n in nodes if n.visibility != "hidden"]
    shown = {n.topic_id for n in nodes}
    reader = create_graph_reader(neo4j, settings, learner_id=learner_id, db=db)
    requires = [
        r
        for r in await reader.topic_requires(domain_id)
        if r.from_topic_id in shown and r.to_topic_id in shown
    ]
    watermark = await read_watermark(neo4j, str(learner_id))
    projected = 0 if watermark is None else watermark[0]
    last = await db.fetch_one(_LAST_SEQ, {"learner_id": learner_id})
    return DomainGraphResponse(
        domain_id=domain_id,
        projected_seq=projected,
        lag_degraded=last is not None and _int(last["seq"]) > projected,
        topics=nodes,
        requires=requires,
    )
