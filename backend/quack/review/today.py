"""«Сегодня», prep mode and the grade after a deadline (ТЗ Ф4 WP17.2, WP17.4, WP17.5; Т-§14).

`today` is synchronous code at the opening of a domain (`Т-§14.4`): the review candidates of
the core (`quack.graph.read.review_candidates`, WP17.1) of this domain only (`П-3.2`), at most
`today_max_items` with a priority above `today_min_score`; nothing important — an empty answer.
A non-empty show is `today.shown` keyed by the domain, the learner's date and the items, so the
dispatcher prepares the plan once (`today.prepare`, WP16.8). Every read is of the learner's own
nodes; another learner's id is «not found» (law 1).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.events.log import NewEvent, append_events
from quack.graph.read import create_graph_reader

type ItemReason = Literal["due", "consolidation", "misconception", "deadline"]
_REASONS: dict[str, ItemReason] = {
    "retention": "due",
    "consolidation": "consolidation",
    "misconception_recheck": "misconception",
    "hub_requirement": "deadline",
}

_SKILLS = (
    "MATCH (s:Skill) WHERE s.learnerId = $learner AND s.id IN $ids AND s.domainId = $domain "
    "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) RETURN s.id AS skill, t.id AS topic"
)
_DOMAIN = "MATCH (d:Domain {id: $id}) WHERE d.learnerId = $learner RETURN count(d) AS n"
_PAST_DEADLINES = (
    "MATCH (d:Domain {id: $domain})-[:HAS_HUB]->(h:Hub) WHERE d.learnerId = $learner "
    "AND h.deadlineAtMs > 0 AND h.deadlineAtMs < $today_ms RETURN h.id AS hub"
)
_HUB = "MATCH (h:Hub {id: $id}) WHERE h.learnerId = $learner RETURN h.domainId AS domain"
_TIMEZONE = (
    "SELECT settings->>'timezone' AS tz FROM core.learners WHERE learner_id = %(learner_id)s"
)
_GRADED = (
    "SELECT DISTINCT payload->>'hub_id' AS hub FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'grade.reported'"
)
_LAST_PREP = (
    "SELECT seq, type FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type IN ('prep_mode.enabled', 'prep_mode.disabled') AND payload->>'hub_id' = %(hub)s "
    "ORDER BY seq DESC LIMIT 1"
)


class TodayEntry(BaseModel):
    skill_id: UUID
    topic_id: UUID | None = None
    reasons: list[ItemReason]
    score: float


class PaceWarning(BaseModel):
    """`Т-§14.6`: the hub will not be ready by its date at the current pace."""

    hub_id: UUID
    ready: int
    not_ready: int
    forecast: float


class TodayView(BaseModel):
    """Empty `items` — no plaque (`Т-§14.4`). `grade_hub_ids` — hubs whose date has passed
    and that have no grade yet: the offer to enter it (WP17.5)."""

    items: list[TodayEntry] = []
    minutes: float = 0.0
    grade_hub_ids: list[UUID] = []
    pace_warnings: list[PaceWarning] = []


class PrepMode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grade: str = Field(min_length=1, max_length=40)
    scale: str = Field(min_length=1, max_length=40)


class Accepted(BaseModel):
    seq: int


_MS_PER_DAY = 86_400_000
_HUB_SKILLS = (
    "MATCH (d:Domain {id: $domain})-[:HAS_HUB]->(h:Hub)-[i:INCLUDES]->(t:Topic) "
    "MATCH (t)-[:COVERS]->(s:Skill) "
    "WHERE d.learnerId = $learner AND h.deadlineAtMs > $now_ms "
    "RETURN h.id AS hub, h.deadlineAtMs AS deadline, t.id AS topic, "
    "i.requiredLevels AS levels, properties(s) AS skill"
)
_ACTIVE_DAYS = (
    "SELECT count(DISTINCT (occurred_at AT TIME ZONE %(tz)s)::date) AS n FROM core.events "
    "WHERE learner_id = %(learner_id)s AND actor = 'learner' AND occurred_at >= %(since)s"
)


def pace_warning(
    ready_at_ms: list[int],
    not_ready: int,
    active_days: int,
    days_left: float,
    now_ms: int,
    settings: Settings,
) -> float | None:
    """`Т-§14.6`: the forecast of topics made ready by the deadline when it falls short of the
    need with the margin `pace_warning_margin`; None — no warning (enough, or no history)."""
    window = settings.pace_window_days
    if active_days < settings.pace_min_active_days or not_ready == 0:
        return None
    recent = sum(1 for at in ready_at_ms if now_ms - at <= window * _MS_PER_DAY)
    pace = recent / active_days  # topics per day of activity
    expected_active_days = days_left * active_days / window
    forecast = pace * expected_active_days
    return round(forecast, 3) if forecast < settings.pace_warning_margin * not_ready else None


def _ready_at(levels: list[int], skills: list[dict[str, object]]) -> int | None:
    """When the topic became ready: all required levels proven by a direct basis or implied
    (as `topic_readiness`), at the time of the last of them; None — not ready."""
    times: list[int] = []
    for skill in skills:
        for n in levels or [3]:
            status, basis = skill.get(f"l{n}Status"), skill.get(f"l{n}Basis")
            if status not in ("proven", "implied") or basis not in ("direct", "implied"):
                return None
            at = skill.get(f"l{n}ProvenAtMs")
            times.append(at if isinstance(at, int) else 0)
    return max(times, default=None)


@dataclass
class _TopicPace:
    levels: list[int]
    skills: list[dict[str, object]] = field(default_factory=list)


@dataclass
class _HubPace:
    deadline_ms: int
    topics: dict[str, _TopicPace] = field(default_factory=dict)


async def _pace(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    now: datetime,
    tz: str,
) -> list[PaceWarning]:
    now_ms = int(now.timestamp() * 1000)
    async with read_session(neo4j) as session:
        result = await session.run(
            literal(_HUB_SKILLS), domain=str(domain_id), learner=str(learner_id), now_ms=now_ms
        )
        rows = [dict(r) async for r in result]
    if not rows:
        return []
    since = now - timedelta(days=settings.pace_window_days)
    found = await db.fetch_one(_ACTIVE_DAYS, {"learner_id": learner_id, "tz": tz, "since": since})
    active = 0 if found is None else int(found["n"])
    hubs: dict[str, _HubPace] = {}
    for r in rows:
        hub = hubs.setdefault(r["hub"], _HubPace(int(r["deadline"])))
        topic = hub.topics.setdefault(r["topic"], _TopicPace(list(r["levels"] or [])))
        topic.skills.append(dict(r["skill"]))
    warnings: list[PaceWarning] = []
    for hub_id, hub in sorted(hubs.items()):
        times = [_ready_at(topic.levels, topic.skills) for topic in hub.topics.values()]
        ready = [at for at in times if at is not None]
        not_ready = len(times) - len(ready)
        days_left = (hub.deadline_ms - now_ms) / _MS_PER_DAY
        forecast = pace_warning(ready, not_ready, active, days_left, now_ms, settings)
        if forecast is not None:
            warning = PaceWarning(
                hub_id=UUID(hub_id), ready=len(ready), not_ready=not_ready, forecast=forecast
            )
            warnings.append(warning)
    return warnings


async def _learner_today(db: Db, learner_id: UUID, now: datetime) -> datetime:
    """The learner's date by `core.learners.settings.timezone` (WP17.3), UTC by default."""
    row = await db.fetch_one(_TIMEZONE, {"learner_id": learner_id})
    zone: ZoneInfo | timezone = UTC
    if row is not None and row["tz"]:
        try:
            zone = ZoneInfo(str(row["tz"]))
        except (ZoneInfoNotFoundError, ValueError):
            zone = UTC
    local = now.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def today(
    db: Db,
    neo4j: AsyncDriver,
    redis: Redis | None,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    now: datetime,
) -> TodayView | None:
    learner = str(learner_id)
    async with read_session(neo4j) as session:
        found = await (
            await session.run(literal(_DOMAIN), id=str(domain_id), learner=learner)
        ).single()
        if found is None or int(found["n"]) == 0:
            return None
    reader = create_graph_reader(neo4j, settings, learner_id=learner_id, db=db)
    candidates = await reader.review_candidates(learner_id, int(now.timestamp() * 1000))
    ids = [str(c.skill_id) for c in candidates]
    async with read_session(neo4j) as session:
        result = await session.run(
            literal(_SKILLS), learner=learner, ids=ids, domain=str(domain_id)
        )
        homes = {r["skill"]: r["topic"] async for r in result}
    chosen = sorted(
        (
            c
            for c in candidates
            if str(c.skill_id) in homes and c.priority > settings.today_min_score
        ),
        key=lambda c: (-c.priority, str(c.skill_id)),
    )[: settings.today_max_items]
    items = [
        TodayEntry(
            skill_id=c.skill_id,
            topic_id=UUID(homes[str(c.skill_id)]) if homes[str(c.skill_id)] else None,
            reasons=sorted({_REASONS[r] for r in c.reasons}),
            score=round(c.priority, 6),
        )
        for c in chosen
    ]
    day = await _learner_today(db, learner_id, now)
    async with read_session(neo4j) as session:
        result = await session.run(
            literal(_PAST_DEADLINES), domain=str(domain_id), learner=learner,
            today_ms=int(day.timestamp() * 1000),
        )  # fmt: skip
        past = {r["hub"] async for r in result}
    graded = {r["hub"] for r in await db.fetch_all(_GRADED, {"learner_id": learner_id})}
    if items:
        payload = {"domain_id": str(domain_id), "items": [i.model_dump(mode="json") for i in items]}
        digest = hashlib.sha256(str(payload["items"]).encode()).hexdigest()[:16]
        key = f"today:{domain_id}:{day.date().isoformat()}:{digest}"
        event = NewEvent(type="today.shown", payload=payload, actor="system",
                         idempotency_key=key, domain_id=domain_id)  # fmt: skip
        await append_events(db, learner_id, [event], redis=redis)
    tz = str(day.tzinfo) if day.tzinfo is not None else "UTC"
    return TodayView(
        items=items,
        minutes=round(settings.review_item_minutes * len(items), 2),
        grade_hub_ids=sorted(UUID(h) for h in past - graded),
        pace_warnings=await _pace(db, neo4j, settings, learner_id, domain_id, now, tz),
    )


async def _own_hub(neo4j: AsyncDriver, learner_id: UUID, hub_id: UUID) -> bool:
    async with read_session(neo4j) as session:
        result = await session.run(literal(_HUB), id=str(hub_id), learner=str(learner_id))
        return await result.single() is not None


async def set_prep_mode(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, hub_id: UUID, enabled: bool
) -> int | None:
    """`prep_mode.enabled/disabled {hub_id}` (WP17.4); asking for the mode the hub already has
    returns its event — no new one (law 5)."""
    if not await _own_hub(neo4j, learner_id, hub_id):
        return None
    kind = "prep_mode.enabled" if enabled else "prep_mode.disabled"
    last = await db.fetch_one(_LAST_PREP, {"learner_id": learner_id, "hub": str(hub_id)})
    if last is not None and last["type"] == kind:
        return int(last["seq"])
    key = f"{kind}:{hub_id}:{0 if last is None else int(last['seq'])}"
    event = NewEvent(
        type=kind, payload={"hub_id": str(hub_id)}, actor="learner", idempotency_key=key
    )
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return stored.seq


async def report_grade(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, hub_id: UUID, body: Grade
) -> int | None:
    """`grade.reported {hub_id, grade, scale}` (WP17.5); the hub stays."""
    if not await _own_hub(neo4j, learner_id, hub_id):
        return None
    payload = {"hub_id": str(hub_id), "grade": body.grade, "scale": body.scale}
    key = f"grade.reported:{hub_id}:{body.grade}:{body.scale}"
    event = NewEvent(type="grade.reported", payload=payload, actor="learner", idempotency_key=key)
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return stored.seq
