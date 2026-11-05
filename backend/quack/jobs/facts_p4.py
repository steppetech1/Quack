"""Data beyond the event for the rows of the dispatcher table (ТЗ Ф4 WP7.1; D-A-136, D-A-162).

`Т-§13.3`: the dispatcher reads the journal, not the projection. A row that needs more than its
event reads `core.events` / `ops.coverage` of the same learner up to the event's `seq`, so the
answer does not depend on how far the projector has come and a replay of the event gives the
same job. Every query names `learner_id` (law 1).

* `skill.split` — episodes whose current observation holds evidence of the old skill.
* `session.ended`, `task.closed` — the home topics of the skills the session touched / the
  task tests. A skill's home topic is a fold of the structure events (`HomeTopics`): created,
  split and merged skills, split and merged topics. A provisional skill that only the projector
  names (a described new skill) has no home topic in the log and gives none.
* `session.ended` — the temporary entities of the session and its domain.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from quack.db import Db

STRUCTURE_TYPES: tuple[str, ...] = (
    "skill.created",
    "skill.split",
    "skill.merged",
    "topic.split",
    "topic.merged",
)


class HomeTopics:
    """Where each skill lives now, by the structure events in `seq` order (`Т-§4.11`: a merge
    leaves an alias, a split distributes the skills). Ids are strings of UUIDs."""

    def __init__(self) -> None:
        self._skill_topic: dict[str, str] = {}
        self._skill_alias: dict[str, str] = {}
        self._topic_alias: dict[str, str] = {}

    def apply(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "skill.created":
            self._skill_topic[payload["skill_id"]] = payload["topic_id"]
        elif event_type == "skill.split":
            for part in payload["into"]:
                self._skill_topic[part["skill_id"]] = part["topic_id"]
        elif event_type == "skill.merged":
            self._skill_alias[payload["from_id"]] = payload["into_id"]
        elif event_type == "topic.split":
            for part in payload["into"]:
                for skill_id in part["skill_ids"]:
                    self._skill_topic[skill_id] = part["topic_id"]
        elif event_type == "topic.merged":
            self._topic_alias[payload["from_id"]] = payload["into_id"]

    @staticmethod
    def _follow(start: str, aliases: dict[str, str]) -> str:
        seen = {start}
        while start in aliases and aliases[start] not in seen:
            start = aliases[start]
            seen.add(start)
        return start

    def topic_of(self, skill_id: str) -> str | None:
        skill = self._follow(skill_id, self._skill_alias)
        topic = self._skill_topic.get(skill)
        return None if topic is None else self._follow(topic, self._topic_alias)

    def topics_of(self, skill_ids: Iterable[str]) -> tuple[UUID, ...]:
        found = {t for s in skill_ids if (t := self.topic_of(s)) is not None}
        return tuple(sorted((UUID(t) for t in found), key=str))


async def home_topics(db: Db, learner_id: UUID, upto_seq: int) -> HomeTopics:
    rows = await db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND seq <= %(upto)s AND type = ANY(%(types)s) ORDER BY seq",
        {"learner_id": learner_id, "upto": upto_seq, "types": list(STRUCTURE_TYPES)},
    )
    home = HomeTopics()
    for row in rows:
        home.apply(row["type"], row["payload"])
    return home


async def skill_split_episodes(
    db: Db, learner_id: UUID, skill_id: UUID, upto_seq: int
) -> tuple[int, ...]:
    """`Т-§2.8`: the episodes (`seq` of their `episode.closed`) whose observation holds evidence
    of the skill. The observation is the one `ops.coverage` points at — the newest per
    extractor version; with two versions an episode may come twice from different versions,
    never be missed."""
    rows = await db.fetch_all(
        "SELECT DISTINCT c.episode_seq FROM ops.coverage c JOIN core.events o "
        "ON o.learner_id = c.learner_id AND o.seq = c.observation_seq "
        "WHERE c.learner_id = %(learner_id)s AND c.status = 'extracted' "
        "AND c.observation_seq <= %(upto)s AND EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(o.payload->'elements') e WHERE e->>'kind' = 'evidence' "
        "AND e->>'skill_id' = %(skill)s) ORDER BY c.episode_seq",
        {"learner_id": learner_id, "upto": upto_seq, "skill": str(skill_id)},
    )
    return tuple(int(r["episode_seq"]) for r in rows)


_SESSION_SKILLS = (
    "SELECT DISTINCT s.skill_id FROM ("
    # exposures of the teacher's turns (Т-§7.8 п.6)
    "SELECT x->>'skill_id' AS skill_id FROM core.events t, "
    "jsonb_array_elements(t.payload->'markup'->'exposures') x "
    "WHERE t.learner_id = %(learner_id)s AND t.session_id = %(session_id)s "
    "AND t.seq <= %(upto)s AND t.type = 'turn.committed' "
    "UNION ALL "
    # evidence of the extractor
    "SELECT x->>'skill_id' FROM core.events o, jsonb_array_elements(o.payload->'elements') x "
    "WHERE o.learner_id = %(learner_id)s AND o.session_id = %(session_id)s "
    "AND o.seq <= %(upto)s AND o.type = 'observation.extracted' AND x->>'kind' = 'evidence' "
    "UNION ALL "
    # evidence of the verdicts: the skills of the tasks answered in the session
    "SELECT x->>'skill_id' FROM core.events a JOIN core.events tc "
    "ON tc.learner_id = a.learner_id AND tc.type = 'task.created' "
    "AND tc.payload->>'task_id' = a.payload->>'task_id', "
    "jsonb_array_elements(tc.payload->'markup'->'tests') x "
    "WHERE a.learner_id = %(learner_id)s AND a.session_id = %(session_id)s "
    "AND a.seq <= %(upto)s AND a.type = 'answer.submitted'"
    ") s WHERE s.skill_id IS NOT NULL"
)


async def session_topic_ids(
    db: Db, learner_id: UUID, session_id: UUID, upto_seq: int
) -> tuple[UUID, ...]:
    """`Т-§17.7`: topics with exposures or evidence in the session."""
    rows = await db.fetch_all(
        _SESSION_SKILLS, {"learner_id": learner_id, "session_id": session_id, "upto": upto_seq}
    )
    skills = [r["skill_id"] for r in rows]
    if not skills:
        return ()
    return (await home_topics(db, learner_id, upto_seq)).topics_of(skills)


async def hinted_task_topic_ids(
    db: Db, learner_id: UUID, task_id: UUID, upto_seq: int
) -> tuple[UUID, ...]:
    """`Т-§17.7`: the topics of the skills a task tests, when a hint was shown on it; no hint —
    no topics. The markup is the newest corrected one, else the task's own."""
    hinted = await db.fetch_one(
        "SELECT 1 FROM core.events WHERE learner_id = %(learner_id)s AND type = 'hint.shown' "
        "AND payload->>'task_id' = %(task)s AND seq < %(upto)s LIMIT 1",
        {"learner_id": learner_id, "task": str(task_id), "upto": upto_seq},
    )
    if hinted is None:
        return ()
    rows = await db.fetch_all(
        "SELECT x->>'skill_id' AS skill_id FROM core.events t, "
        "jsonb_array_elements(COALESCE((SELECT c.payload->'corrected_markup' FROM core.events c "
        "WHERE c.learner_id = t.learner_id AND c.payload->>'task_id' = t.payload->>'task_id' "
        "AND c.type IN ('task.verified', 'task.key_disputed', 'task.markup_disputed') "
        "AND jsonb_typeof(c.payload->'corrected_markup') = 'object' AND c.seq <= %(upto)s "
        "ORDER BY c.seq DESC LIMIT 1), t.payload->'markup')->'tests') x "
        "WHERE t.learner_id = %(learner_id)s AND t.type = 'task.created' "
        "AND t.payload->>'task_id' = %(task)s AND t.seq <= %(upto)s "
        "AND x->>'skill_id' IS NOT NULL",
        {"learner_id": learner_id, "task": str(task_id), "upto": upto_seq},
    )
    skills = [r["skill_id"] for r in rows]
    if not skills:
        return ()
    return (await home_topics(db, learner_id, upto_seq)).topics_of(skills)


async def session_domain_id(db: Db, learner_id: UUID, session_id: UUID) -> UUID | None:
    """The domain of the session — from its `session.started`."""
    row = await db.fetch_one(
        "SELECT payload->>'domain_id' AS domain_id FROM core.events "
        "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
        "AND type = 'session.started' ORDER BY seq LIMIT 1",
        {"learner_id": learner_id, "session_id": session_id},
    )
    return None if row is None or row["domain_id"] is None else UUID(row["domain_id"])


async def session_has_temporary_entities(
    db: Db, learner_id: UUID, session_id: UUID, upto_seq: int
) -> bool:
    """`Т-§4.4`, `Т-§4.10`: the session made a provisional skill, or described a new one in a task
    or in an observation (the projector gives it a provisional skill and, if the topic hint
    matches none, a temporary topic)."""
    row = await db.fetch_one(
        "SELECT 1 FROM core.events e WHERE e.learner_id = %(learner_id)s "
        "AND e.session_id = %(session_id)s AND e.seq <= %(upto)s AND ("
        "(e.type = 'skill.created' AND e.payload->>'status' = 'provisional') "
        "OR (e.type = 'task.created' AND EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(e.payload->'markup'->'tests') x "
        "WHERE jsonb_typeof(x->'new_skill') = 'object')) "
        "OR (e.type = 'turn.committed' AND EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(e.payload->'markup'->'tasks') k, "
        "jsonb_array_elements(k->'tests') x WHERE jsonb_typeof(x->'new_skill') = 'object')) "
        "OR (e.type = 'observation.extracted' AND EXISTS (SELECT 1 FROM "
        "jsonb_array_elements(e.payload->'elements') x "
        "WHERE jsonb_typeof(x->'new_skill') = 'object'))) LIMIT 1",
        {"learner_id": learner_id, "session_id": session_id, "upto": upto_seq},
    )
    return row is not None
