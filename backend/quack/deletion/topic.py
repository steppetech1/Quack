"""Physical deletion of a topic (ТЗ Ф4 WP25 п.1–4; Т-§19.5 «тема — приватность»).

«Убрать тему с графа» is `POST /v1/topics/{id}/hide`; this is the deletion of its data. Bounds
(`Т-§19.5`): the events of the structure of the topic and of its skills, the tasks of the topic
(the tasks whose markup tests its skills) with their events, the artifacts of the topic, the
vectors of its entities. Other events that named its skills apply as empty at the rebuild;
messages of sessions stay. The members are read from the graph before the deletion.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.db import LockKeyspace
from quack.db.neo4j import literal, read_session
from quack.deletion.domain import DeletionBusy, DeletionDone, rebuild_after
from quack.storage.deletions import DeletionJournal, DeletionRecord
from quack.storage.objects import ObjectStore

_MEMBERS = (
    "MATCH (t:Topic {id: $id}) WHERE t.learnerId = $learner "
    "OPTIONAL MATCH (t)-[:COVERS]->(s:Skill) "
    "OPTIONAL MATCH (s)<-[:TESTS]-(k:Task) "
    # `t.id` groups: without a key an aggregation returns a row even when MATCH found nothing
    "RETURN t.id AS topic, collect(DISTINCT s.id) AS skills, collect(DISTINCT k.id) AS tasks"
)
_LOG_SKILLS = (
    "SELECT DISTINCT payload->>'skill_id' AS skill FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'skill.created' "
    "AND payload->>'topic_id' = %(topic)s"
)
_LOG_TASKS = (
    "SELECT DISTINCT payload->>'task_id' AS task FROM core.events e "
    "WHERE e.learner_id = %(learner_id)s AND e.type = 'task.created' AND EXISTS ("
    "SELECT 1 FROM jsonb_array_elements(COALESCE(e.payload->'markup'->'tests', '[]'::jsonb)) x "
    "WHERE x->>'skill_id' = ANY(%(skills)s))"
)
_ANSWERS = (
    "SELECT seq FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'answer.submitted' AND payload->>'task_id' = ANY(%(tasks)s)"
)
_SELECTED = (
    "learner_id = %(learner_id)s AND ("
    "payload->>'topic_id' = %(topic)s "
    "OR (type IN ('topic.merged', 'topic.split') AND (payload->>'from_id' = %(topic)s "
    "OR payload->>'into_id' = %(topic)s)) "
    "OR (payload->>'skill_id' = ANY(%(skills)s) AND type <> 'answer.submitted') "
    "OR (type LIKE 'skill.%%' AND (payload->>'from_id' = ANY(%(skills)s) "
    "OR payload->>'into_id' = ANY(%(skills)s))) "
    "OR (type LIKE 'edge.%%' AND (payload->>'from_id' = ANY(%(skills)s) "
    "OR payload->>'to_id' = ANY(%(skills)s))) "
    "OR (type = 'embedding.ready' AND payload->>'node_id' = ANY(%(nodes)s)) "
    "OR payload->>'task_id' = ANY(%(tasks)s) "
    "OR ((payload->>'answer_seq')::bigint = ANY(%(answers)s)))"
)
_EVENT_KEYS = (
    "SELECT payload->>'object_key' AS key FROM core.events WHERE "
    + _SELECTED
    + " AND payload ? 'object_key'"
)
_DELETE_EVENTS = "DELETE FROM core.events WHERE " + _SELECTED
_ARTIFACTS = (
    "SELECT artifact_id FROM content.artifacts "
    "WHERE learner_id = %(learner_id)s AND topic_id = %(topic_uuid)s"
)
_DELETES = (
    "DELETE FROM content.tasks WHERE learner_id = %(learner_id)s AND task_id = ANY(%(task_uuids)s)",
    "DELETE FROM content.artifact_versions WHERE learner_id = %(learner_id)s "
    "AND artifact_id = ANY(%(artifacts)s)",
    "DELETE FROM content.artifacts WHERE learner_id = %(learner_id)s "
    "AND artifact_id = ANY(%(artifacts)s)",
)
_LOG = (
    "INSERT INTO ops.deletion_log (deletion_id, learner_id, kind, target_id, deleted_at) "
    "VALUES (%(deletion_id)s, %(learner_id)s, 'topic', %(target_id)s, %(deleted_at)s) "
    "ON CONFLICT (deletion_id) DO NOTHING"
)


async def topic_members(
    neo4j: AsyncDriver, learner_id: UUID, topic_id: UUID
) -> tuple[list[str], list[str]] | None:
    """`(skills, tasks)` of the learner's topic; None — not the learner's topic."""
    async with read_session(neo4j) as session:
        result = await session.run(literal(_MEMBERS), id=str(topic_id), learner=str(learner_id))
        record = await result.single()
    if record is None:
        return None
    return sorted(record["skills"]), sorted(record["tasks"])


async def _members_of_log(
    db: Db, learner_id: UUID, topic_id: UUID, skills: list[str], tasks: list[str]
) -> tuple[list[str], list[str]]:
    """The skills and tasks of the topic by the log too: a replay after a restore finds them
    even when the graph has forgotten the topic (WP25 п.5)."""
    params: dict[str, object] = {"learner_id": learner_id, "topic": str(topic_id)}
    found = {r["skill"] for r in await db.fetch_all(_LOG_SKILLS, params) if r["skill"]}
    all_skills = sorted(set(skills) | found)
    params["skills"] = all_skills
    found_tasks = {r["task"] for r in await db.fetch_all(_LOG_TASKS, params) if r["task"]}
    return all_skills, sorted(set(tasks) | found_tasks)


async def erase_topic(
    db: Db,
    store: ObjectStore,
    learner_id: UUID,
    topic_id: UUID,
    skills: list[str],
    tasks: list[str],
) -> int:
    params: dict[str, object] = {
        "learner_id": learner_id,
        "topic": str(topic_id),
        "topic_uuid": topic_id,
        "skills": skills,
        "tasks": tasks,
        "task_uuids": [UUID(t) for t in tasks],
        "nodes": [str(topic_id), *skills, *tasks],
    }
    async with db.transaction():
        skills, tasks = await _members_of_log(db, learner_id, topic_id, skills, tasks)
        params |= {"skills": skills, "tasks": tasks, "task_uuids": [UUID(t) for t in tasks],
                   "nodes": [str(topic_id), *skills, *tasks]}  # fmt: skip
        params["answers"] = [r["seq"] for r in await db.fetch_all(_ANSWERS, params)]
        params["artifacts"] = [r["artifact_id"] for r in await db.fetch_all(_ARTIFACTS, params)]
        keys = {r["key"] for r in await db.fetch_all(_EVENT_KEYS, params) if r["key"]}
        await db.execute(_DELETE_EVENTS, params)
        for statement in _DELETES:
            await db.execute(statement, params)
    if keys:
        await store.delete_keys(sorted(keys))
    return len(keys)


async def apply_topic_record(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    store: ObjectStore,
    learner_id: UUID,
    record: DeletionRecord,
    *,
    redis: Redis | None = None,
) -> bool:
    """The deletion of the record, its row of `ops.deletion_log`, the rebuild; repeatable."""
    members = await topic_members(neo4j, learner_id, record.target_id) or ([], [])
    async with db.advisory_lock(LockKeyspace.LEARNER_PROJECTION, learner_id) as acquired:
        if not acquired:
            raise DeletionBusy(str(learner_id))
        await erase_topic(db, store, learner_id, record.target_id, *members)
        row = {
            "deletion_id": record.deletion_id,
            "learner_id": learner_id,
            "target_id": record.target_id,
            "deleted_at": record.deleted_at,
        }
        await db.execute(_LOG, row)
    await rebuild_after(db, neo4j, settings, learner_id, redis=redis)
    return True


async def delete_topic(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    journal: DeletionJournal,
    store: ObjectStore,
    learner_id: UUID,
    topic_id: UUID,
    now: datetime,
    *,
    redis: Redis | None = None,
) -> DeletionDone | None:
    """None — not the learner's topic (404); the journal record first (`Д-§8.5`)."""
    if await topic_members(neo4j, learner_id, topic_id) is None:
        return None
    record = DeletionRecord(uuid4(), learner_id, "topic", topic_id, now)
    await journal.write(record)
    await apply_topic_record(db, neo4j, settings, store, learner_id, record, redis=redis)
    return DeletionDone(record.deletion_id)
