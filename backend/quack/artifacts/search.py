"""Full-text search over core.artifacts and core.events (ТЗ §2.3 п.3, A13) and the domain
export to Markdown or JSON (ТЗ §2.3 п.4, decision 5)."""

from __future__ import annotations

from typing import Literal, LiteralString
from uuid import UUID

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.artifacts.schemas import (
    ArtifactSearchHit,
    ArtifactView,
    EventSearchHit,
    ExportDomain,
    ExportJson,
    ExportMdResponse,
    ExportSkill,
    ExportTopic,
    SearchResponse,
)
from quack.artifacts.store import ARTIFACT_VIEW_COLUMNS, fetch_rows
from quack.db.neo4j import read_session
from quack.events.catalog import TEXT_EVENT_TYPES
from quack.graph.topic_display import INACTIVE_SKILL_STATUSES, INACTIVE_TOPIC_STATUSES

_QUERY = "plainto_tsquery('english', :q)"
_ARTIFACT_VECTOR = "to_tsvector('english', title || ' ' || content_md)"
# The expression and the type list of the partial index `idx_events_fts_en` (spec §3.2): the
# types are inlined as literals, otherwise the planner cannot prove the index predicate.
_EVENT_VECTOR = "to_tsvector('english', coalesce(e.payload->>'text', ''))"
_TEXT_TYPES = ", ".join(f"'{t}'" for t in sorted(TEXT_EVENT_TYPES))

# The artifact of an event (notes, cheatsheets, digests).
_EVENT_ARTIFACT_JOIN = (
    " LEFT JOIN core.artifacts a ON a.id = CAST(e.payload->>'artifact_id' AS UUID)"
    " AND a.learner_id = e.learner_id"
)
# Q4 (PHASE3_NOTES): an artifact event is found only while it holds the artifact's current
# content (`source_seq`), so deleted notes and replaced versions do not come back.
_EVENT_CURRENT_ARTIFACT = (
    " AND (e.payload->>'artifact_id' IS NULL OR (NOT a.is_deleted AND a.source_seq = e.seq))"
)
# A13: the domain of an event is its own, else its session's, else its artifact's.
_EVENT_DOMAIN_JOINS = " LEFT JOIN core.sessions s ON s.id = e.session_id"
_EVENT_DOMAIN_FILTER = " AND coalesce(e.domain_id, s.domain_id, a.domain_id) = :d"

# Live topics of the domain and their live skills (A16).
_DOMAIN_TOPICS: LiteralString = (
    "MATCH (d:Domain {id: $domain})-[:HAS_TOPIC]->(t:Topic) "
    "WHERE d.learnerId = $l AND t.learnerId = $l AND NOT t.status IN $inactive_topics "
    "OPTIONAL MATCH (s:Skill)-[:PART_OF]->(t) "
    "WHERE s.learnerId = $l AND NOT s.status IN $inactive_skills "
    "WITH t, s ORDER BY s.name, s.id "
    "WITH t, collect(s {.id, .name, .pMastery, .masteryStatus}) AS skills ORDER BY t.name, t.id "
    "RETURN t.id AS topic_id, t.name AS name, skills"
)


async def search_artifacts(
    learner_id: UUID,
    q: str,
    domain_id: UUID | None,
    limit: int,
    resources: AppResources,
) -> SearchResponse:
    """Two FTS queries (`english` config); the event query matches `idx_events_fts_en`."""
    params = {"l": learner_id, "q": q, "limit": limit}
    if domain_id is not None:
        params["d"] = domain_id
    artifact_rows = await fetch_rows(
        resources.session_factory,
        "SELECT id AS artifact_id, kind, domain_id, topic_id, title, version "
        "FROM core.artifacts WHERE learner_id = :l AND NOT is_deleted"
        + ("" if domain_id is None else " AND domain_id = :d")
        + f" AND {_ARTIFACT_VECTOR} @@ {_QUERY} "
        f"ORDER BY ts_rank({_ARTIFACT_VECTOR}, {_QUERY}) DESC, updated_at DESC LIMIT :limit",
        **params,
    )
    event_rows = await fetch_rows(
        resources.session_factory,
        "SELECT e.seq, e.type, e.session_id, e.occurred_at, e.payload->>'text' AS text "
        "FROM core.events e"
        + _EVENT_ARTIFACT_JOIN
        + ("" if domain_id is None else _EVENT_DOMAIN_JOINS)
        + f" WHERE e.learner_id = :l AND e.type IN ({_TEXT_TYPES}) "
        f"AND {_EVENT_VECTOR} @@ {_QUERY}"
        + _EVENT_CURRENT_ARTIFACT
        + ("" if domain_id is None else _EVENT_DOMAIN_FILTER)
        + f" ORDER BY ts_rank({_EVENT_VECTOR}, {_QUERY}) DESC, e.seq DESC LIMIT :limit",
        **params,
    )
    return SearchResponse(
        artifact_hits=[ArtifactSearchHit.model_validate(r) for r in artifact_rows],
        event_hits=[EventSearchHit.model_validate(r) for r in event_rows],
    )


def _skills_table(skills: list[ExportSkill]) -> str:
    rows = ["| Skill | pMastery | Status |", "| :--- | :--- | :--- |"]
    rows += [f"| {s.name} | {s.pMastery:.2f} | {s.masteryStatus} |" for s in skills]
    return "\n".join(rows)


def _artifact_block(artifact: ArtifactView) -> str:
    return f"### {artifact.title}\n\n{artifact.content_md}"


def _export_md(export: ExportJson) -> str:
    parts = [f"# {export.domain.name}"]
    for topic in export.topics:
        parts.append(f"## {topic.name}")
        parts.append(_skills_table(topic.skills))
        if topic.cheatsheet is not None:
            parts.append(_artifact_block(topic.cheatsheet))
    for heading, artifacts in (("Session digests", export.digests), ("Notes", export.notes)):
        parts.append(f"## {heading}")
        parts.extend(_artifact_block(a) for a in artifacts)
    return "\n\n".join(parts) + "\n"


async def export_domain(
    learner_id: UUID,
    domain_id: UUID,
    format: Literal["md", "json"],
    resources: AppResources,
) -> ExportMdResponse | ExportJson:
    """Topics, skills (`pMastery`, `masteryStatus`), cheatsheets, digests and active notes of
    the learner's domain; 404 for a missing or foreign domain."""
    domains = await fetch_rows(
        resources.session_factory,
        "SELECT id AS domain_id, name, slug FROM core.domains WHERE id = :d AND learner_id = :l",
        d=domain_id,
        l=learner_id,
    )
    if not domains:
        raise ApiError("NOT_FOUND", "domain not found", status_code=404)

    async with read_session(resources.neo4j) as session:
        result = await session.run(
            _DOMAIN_TOPICS,
            {
                "domain": str(domain_id),
                "l": str(learner_id),
                "inactive_topics": sorted(INACTIVE_TOPIC_STATUSES),
                "inactive_skills": sorted(INACTIVE_SKILL_STATUSES),
            },
        )
        topics = [record.data() async for record in result]

    rows = await fetch_rows(
        resources.session_factory,
        f"SELECT {ARTIFACT_VIEW_COLUMNS} FROM core.artifacts WHERE learner_id = :l "
        "AND domain_id = :d AND NOT is_deleted ORDER BY created_at, id",
        l=learner_id,
        d=domain_id,
    )
    artifacts = [ArtifactView.model_validate(r) for r in rows]
    sheets = {a.topic_id: a for a in artifacts if a.kind == "cheatsheet"}
    export = ExportJson(
        domain=ExportDomain.model_validate(domains[0]),
        topics=[
            ExportTopic(
                topic_id=t["topic_id"],
                name=t["name"],
                skills=[
                    ExportSkill(
                        skill_id=s["id"],
                        name=s["name"],
                        pMastery=s["pMastery"],
                        masteryStatus=s["masteryStatus"],
                    )
                    for s in t["skills"]
                ],
                cheatsheet=sheets.get(UUID(t["topic_id"])),
            )
            for t in topics
        ],
        digests=[a for a in artifacts if a.kind == "digest"],
        notes=[a for a in artifacts if a.kind == "note"],
    )
    if format == "json":
        return export
    return ExportMdResponse(content_md=_export_md(export))
