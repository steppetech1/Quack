"""DEMO-ONLY seed of a domain's map from the learner's own words (no materials uploaded).

Normally the map grows from materials: extraction -> reconcile writes `topic.created`,
`skill.created`, `edge.added`. A learner who only types «I want to learn X» had an empty map.
This module is the minimal demo path: from the text it builds an outline (topics, one or two
skills each, a prerequisite chain) and appends the same events reconcile would, with
`origin = "learner"`. No LLM is called, so it works without a model key; the outline is a
deterministic split of the text (a list after a colon or commas) or a generic five-step template.

`POST /v1/domains/{domain_id}/demo-seed`. Idempotent: ids and keys derive from the learner, the
domain and the topic names, so a retry writes nothing new (law 5). Not a replacement for
extraction: the material flow is untouched.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.ids import deterministic_id

_MAX_TOPICS = 6
_OWN_DOMAIN = (
    "SELECT payload ->> 'name' AS name FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'domain.created' AND payload ->> 'domain_id' = %(domain_id)s LIMIT 1"
)
_TEMPLATE = (
    "Foundations of {s}",
    "Core concepts of {s}",
    "Key methods in {s}",
    "Applying {s}",
    "Common mistakes in {s}",
)
_LEAD = re.compile(
    r"^\s*(i\s+(?:want|need|would like|wanna)\s+to\s+(?:learn|study|understand|master)|"
    r"(?:teach|help)\s+me(?:\s+to)?(?:\s+(?:learn|study|understand))?|"
    r"хочу\s+(?:выучить|изучить|понять|освоить|научиться)|научи\s+меня)\s*",
    re.IGNORECASE,
)


class DemoSeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class DemoSeedResponse(BaseModel):
    domain_id: UUID
    topics: list[str]
    skills: int
    requires: int


def outline(text: str, domain_name: str) -> list[str]:
    """Topic names from the learner's text: «X: a, b, c» gives a, b, c; else the template."""
    body = _LEAD.sub("", text).strip().rstrip(".!?")
    head, sep, tail = body.partition(":")
    items = [p.strip(" .-") for p in re.split(r"[,;\n]|\s+(?:and|и)\s+", tail if sep else body)]
    items = [p for p in items if 2 <= len(p) <= 60]
    if len(items) >= 3:
        seen: dict[str, str] = {}
        for p in items:
            seen.setdefault(p.casefold(), p[0].upper() + p[1:])
        return list(seen.values())[:_MAX_TOPICS]
    subject = (head if sep and head.strip() else domain_name).strip() or domain_name
    return [t.format(s=subject) for t in _TEMPLATE]


async def seed_domain(
    db: Db, redis: Redis | None, learner_id: UUID, domain_id: UUID, req: DemoSeedRequest
) -> DemoSeedResponse | None:
    """`None` — the domain is not the learner's (`404`)."""
    own = await db.fetch_one(_OWN_DOMAIN, {"learner_id": learner_id, "domain_id": str(domain_id)})
    if own is None:
        return None
    names = outline(req.text, str(own["name"]))
    scope = f"demo-seed:{domain_id}"

    def eid(kind: str, name: str) -> str:
        return deterministic_id(learner_id, 0, f"{scope}:{kind}:{name.casefold()}")

    def new(event_type: str, payload: dict[str, object], key: str) -> NewEvent:
        return NewEvent(
            type=event_type,
            payload=payload,
            actor="learner",
            idempotency_key=f"{scope}:{event_type}:{key}",
            domain_id=domain_id,
        )

    events: list[NewEvent] = []
    skills: list[str] = []
    for name in names:
        topic = eid("topic", name)
        events.append(
            new(
                "topic.created",
                {"topic_id": topic, "domain_id": str(domain_id), "name": name, "origin": "learner"},
                topic,
            )
        )
        for label in (f"Explain: {name}", f"Apply: {name}"):
            skill = eid("skill", label)
            skills.append(skill)
            payload = {"skill_id": skill, "topic_id": topic, "domain_id": str(domain_id),
                       "name": label, "summary": label, "status": "active",
                       "origin": "learner"}  # fmt: skip
            events.append(new("skill.created", payload, skill))
    pairs = [(skills[2 * i + 2], skills[2 * i]) for i in range(len(names) - 1)]
    for a, b in pairs:
        payload = {"kind": "requires", "from_id": a, "to_id": b, "origin": "learner",
                   "confidence": 0.8}  # fmt: skip
        events.append(new("edge.added", payload, f"{a}:{b}"))
    await append_events(db, learner_id, events, redis=redis)
    return DemoSeedResponse(
        domain_id=domain_id, topics=names, skills=len(skills), requires=len(pairs)
    )
