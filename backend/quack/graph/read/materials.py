"""Material chunks for the teacher (ТЗ Ф4 WP20.4; Т-§10.5, Т-§3.4.6; owner — stream C, §7.3).

`materials_prefetch` — chunks that `TEACHES` the skills in focus, by walking the graph, without
an embedding: works while Modal is down (Т-§11.9). `materials_search` — free query: `SEARCH`
over `chunk_emb` filtered inside the index by learner, domain and `searchable` (WP0 п.3а: works
on Community, no fallback needed), with the reserve `vector_search_reserve`; chunks teaching a
focus skill go first, the result is `materials_k` with the page. The pattern around `SEARCH`
is one bound variable; walking from the found chunks is separate `MATCH`es (Т-§3.4.6).

The caller binds the learner: tools of the teacher get these functions with `learner_id` and
`domain_id` of the turn (isolation, §4.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from neo4j import AsyncDriver, Record

from quack.db.neo4j import cypher


@dataclass(frozen=True)
class ChunkHit:
    """A chunk with the reference to its page or slide (Т-§10.5, Т-§19.8)."""

    chunk_id: UUID
    material_id: UUID | None
    page: int | None
    text: str
    teaches_focus: bool
    score: float | None  # None — found by the graph, not by the query


_PREFETCH = cypher(
    "MATCH (s:Skill) WHERE s.learnerId = $learnerId AND s.id IN $skills "
    "MATCH (c:Chunk)-[:TEACHES]->(s) "
    "WHERE c.learnerId = $learnerId AND c.searchable = true "
    "WITH c, count(DISTINCT s) AS focus "
    "OPTIONAL MATCH (m:Material)-[:HAS_CHUNK]->(c) "
    "RETURN c.id AS chunk_id, m.id AS material_id, c.page AS page, c.text AS text, "
    "true AS teaches_focus, null AS score "
    "ORDER BY focus DESC, material_id, page, chunk_id LIMIT $k"
)

_SEARCH_TEMPLATE = (
    "CYPHER 25 MATCH (c:Chunk) "
    "SEARCH c IN (VECTOR INDEX chunk_emb FOR $vector "
    "WHERE c.learnerId = $learnerId AND c.domainId = $domainId AND c.searchable = true "
    "LIMIT {reserve}) SCORE AS score "
    "OPTIONAL MATCH (c)-[:TEACHES]->(s:Skill) WHERE s.id IN $skills "
    "WITH c, score, count(s) > 0 AS teaches_focus "
    "OPTIONAL MATCH (m:Material)-[:HAS_CHUNK]->(c) "
    "RETURN c.id AS chunk_id, m.id AS material_id, c.page AS page, c.text AS text, "
    "teaches_focus, score "
    "ORDER BY teaches_focus DESC, score DESC LIMIT $k"
)


async def materials_prefetch(
    driver: AsyncDriver, *, learner_id: UUID, skill_ids: Sequence[UUID], k: int
) -> list[ChunkHit]:
    """Chunks teaching the skills in focus (Т-§10.5 «Предвыборка по графу»); more focus
    skills taught — earlier."""
    if not skill_ids:
        return []
    result = await driver.execute_query(
        _PREFETCH,
        learnerId=str(learner_id),
        skills=[str(skill_id) for skill_id in skill_ids],
        k=k,
        routing_="r",
    )
    return [_hit(record) for record in result.records]


async def materials_search(
    driver: AsyncDriver,
    *,
    learner_id: UUID,
    domain_id: UUID,
    vector: Sequence[float],
    skill_ids: Sequence[UUID],
    k: int,
    reserve: int,
) -> list[ChunkHit]:
    """Chunks of the learner's domain nearest to the query vector (`embed_query`); a chunk
    teaching a focus skill goes above any other (Т-§10.5 «Поиск по свободному запросу»)."""
    # `reserve` is an int from settings, not user input: SEARCH takes the limit as a literal.
    query = cypher(_SEARCH_TEMPLATE.format(reserve=int(reserve)))
    result = await driver.execute_query(
        query,
        vector=list(vector),
        learnerId=str(learner_id),
        domainId=str(domain_id),
        skills=[str(skill_id) for skill_id in skill_ids],
        k=k,
        routing_="r",
    )
    return [_hit(record) for record in result.records]


def _hit(record: Record) -> ChunkHit:
    material_id: str | None = record["material_id"]
    page: int | None = record["page"]
    text: str | None = record["text"]
    score: float | None = record["score"]
    return ChunkHit(
        chunk_id=UUID(record["chunk_id"]),
        material_id=UUID(material_id) if material_id is not None else None,
        page=page,
        text=text or "",
        teaches_focus=bool(record["teaches_focus"]),
        score=score,
    )
