"""WP20.4: material chunks by the graph and by a free query (ТЗ Ф4 WP20.4; Т-§10.5, Т-§3.4.6)."""

from __future__ import annotations

from uuid import UUID, uuid4

from neo4j import AsyncDriver

from quack.config import Settings
from quack.graph.read.materials import materials_prefetch, materials_search


def _vector(settings: Settings, hot: int, warm: int | None = None) -> list[float]:
    vector = [0.0] * settings.embedding_dim
    vector[hot] = 1.0
    if warm is not None:
        vector[warm] = 0.5
    return vector


async def _chunk(
    neo4j: AsyncDriver,
    *,
    learner_id: UUID,
    domain_id: UUID,
    material_id: UUID,
    page: int,
    text: str,
    embedding: list[float],
    teaches: list[UUID] = [],  # noqa: B006 — read only
    searchable: bool = True,
) -> UUID:
    chunk_id = uuid4()
    await neo4j.execute_query(
        "MERGE (m:Material {id: $material}) SET m.learnerId = $learner "
        "CREATE (c:Chunk {id: $id, learnerId: $learner, domainId: $domain, page: $page, "
        "text: $text, searchable: $searchable, embedding: $embedding}) "
        "CREATE (m)-[:HAS_CHUNK]->(c) "
        "WITH c UNWIND $teaches AS skill "
        "MERGE (s:Skill {id: skill}) SET s.learnerId = $learner "
        "CREATE (c)-[:TEACHES]->(s)",
        id=str(chunk_id),
        material=str(material_id),
        learner=str(learner_id),
        domain=str(domain_id),
        page=page,
        text=text,
        searchable=searchable,
        embedding=embedding,
        teaches=[str(skill) for skill in teaches],
    )
    return chunk_id


async def test_prefetch_by_teaches(neo4j: AsyncDriver, settings: Settings) -> None:
    """Т-§10.5: chunks teaching the focus skills, without an embedding; not `searchable`
    and another learner's are not returned; more focus skills taught — earlier."""
    learner, other, domain, material = uuid4(), uuid4(), uuid4(), uuid4()
    s1, s2 = uuid4(), uuid4()
    emb = _vector(settings, 0)
    both = await _chunk(
        neo4j, learner_id=learner, domain_id=domain, material_id=material, page=3,
        text="limits and continuity", embedding=emb, teaches=[s1, s2],
    )  # fmt: skip
    one = await _chunk(
        neo4j, learner_id=learner, domain_id=domain, material_id=material, page=1,
        text="limits", embedding=emb, teaches=[s1],
    )  # fmt: skip
    await _chunk(
        neo4j, learner_id=learner, domain_id=domain, material_id=material, page=2,
        text="hidden", embedding=emb, teaches=[s1], searchable=False,
    )  # fmt: skip
    await _chunk(
        neo4j, learner_id=other, domain_id=domain, material_id=uuid4(), page=1,
        text="foreign", embedding=emb, teaches=[uuid4()],
    )  # fmt: skip

    hits = await materials_prefetch(neo4j, learner_id=learner, skill_ids=[s1, s2], k=5)
    assert [hit.chunk_id for hit in hits] == [both, one]
    assert (hits[0].material_id, hits[0].page, hits[0].score) == (material, 3, None)
    assert await materials_prefetch(neo4j, learner_id=learner, skill_ids=[], k=5) == []
    assert await materials_prefetch(neo4j, learner_id=other, skill_ids=[s1], k=5) == []


async def test_search_filters_in_the_index_and_lifts_focus(
    neo4j: AsyncDriver, settings: Settings
) -> None:
    """Т-§3.4.6, Т-§10.5: `SEARCH` over `chunk_emb` with learner, domain and `searchable`
    inside the index; a chunk teaching a focus skill goes above a nearer one; `k` cuts."""
    learner, other, domain, material = uuid4(), uuid4(), uuid4(), uuid4()
    focus = uuid4()
    query = _vector(settings, 0)
    near = await _chunk(
        neo4j, learner_id=learner, domain_id=domain, material_id=material, page=1,
        text="the nearest", embedding=_vector(settings, 0),
    )  # fmt: skip
    teaching = await _chunk(
        neo4j, learner_id=learner, domain_id=domain, material_id=material, page=2,
        text="teaches the focus", embedding=_vector(settings, 0, warm=1), teaches=[focus],
    )  # fmt: skip
    far = await _chunk(
        neo4j, learner_id=learner, domain_id=domain, material_id=material, page=3,
        text="far", embedding=_vector(settings, 2, warm=0),
    )  # fmt: skip
    for kwargs in (
        {"learner_id": learner, "domain_id": uuid4(), "searchable": True},
        {"learner_id": learner, "domain_id": domain, "searchable": False},
        {"learner_id": other, "domain_id": domain, "searchable": True},
    ):
        await _chunk(
            neo4j, material_id=uuid4(), page=9, text="filtered out",
            embedding=_vector(settings, 0), **kwargs,  # type: ignore[arg-type]
        )  # fmt: skip

    hits = await materials_search(
        neo4j, learner_id=learner, domain_id=domain, vector=query, skill_ids=[focus],
        k=5, reserve=settings.vector_search_reserve,
    )  # fmt: skip
    assert [hit.chunk_id for hit in hits] == [teaching, near, far]
    assert hits[0].teaches_focus and not hits[1].teaches_focus
    assert hits[1].score is not None and hits[1].score >= hits[2].score  # type: ignore[operator]

    top = await materials_search(
        neo4j, learner_id=learner, domain_id=domain, vector=query, skill_ids=[],
        k=1, reserve=settings.vector_search_reserve,
    )  # fmt: skip
    assert [hit.chunk_id for hit in top] == [near]
