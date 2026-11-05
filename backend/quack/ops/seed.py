"""`ops seed-learners` (ТЗ Фазы 3 WP8.6, Д-§11): test learners for the load stand.

Each learner gets a `core.learners` row (marked `settings.seeded`) and one domain with topics and
skills from a fixture, all through `append_events` with `actor = "system"` and without a model.
IDs and idempotency keys are deterministic (`uuid5(QUACK_UUID_NS, "seed:{i}:…")`), so a second
run with the same N creates nothing new; each run opens new sessions (ТЗ Ф4 WP18.3): the token
of the file is the session cookie.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.ids import QUACK_UUID_NS

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "loadtest" / "fixtures"
DEFAULT_FIXTURE = "default"
SEEDED_SETTINGS: dict[str, Any] = {"seeded": True}


class SeedRefusedError(Exception):
    """`env = prod` without `--confirm`, or real users already present (exit code 2)."""


@dataclass(frozen=True)
class SeededLearner:
    learner_id: UUID
    token: str
    domain_id: UUID
    topic_ids: list[UUID]

    def as_json(self) -> dict[str, Any]:
        return {
            "learner_id": str(self.learner_id),
            "token": self.token,
            "domain_id": str(self.domain_id),
            "topic_ids": [str(t) for t in self.topic_ids],
        }


def load_fixture(name: str = DEFAULT_FIXTURE) -> dict[str, Any]:
    """`{"domain": str, "topics": [{"name": str, "skills": [str, …]}, …]}`."""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _id(i: int, *parts: object) -> UUID:
    return uuid5(QUACK_UUID_NS, ":".join(["seed", str(i), *map(str, parts)]))


def seed_events(i: int, fixture: dict[str, Any]) -> tuple[UUID, UUID, list[UUID], list[NewEvent]]:
    """The events of learner `i`: a domain with its topics and skills, as a confirmed structure
    (Т-§4: `domain.created`, `topic.created`, `skill.created`)."""
    learner_id, domain_id = _id(i, "learner"), _id(i, "domain")
    topic_ids: list[UUID] = []
    events = [
        NewEvent(
            type="domain.created",
            payload={"domain_id": str(domain_id), "name": fixture["domain"]},
            actor="system",
            idempotency_key=f"seed:{i}:domain",
            domain_id=domain_id,
        )
    ]
    for k, topic in enumerate(fixture["topics"]):
        topic_id = _id(i, "topic", k)
        topic_ids.append(topic_id)
        events.append(
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": str(topic_id),
                    "domain_id": str(domain_id),
                    "name": topic["name"],
                    "origin": "learner",
                },
                actor="system",
                idempotency_key=f"seed:{i}:topic:{k}",
                domain_id=domain_id,
            )
        )
        for j, skill in enumerate(topic["skills"]):
            events.append(
                NewEvent(
                    type="skill.created",
                    payload={
                        "skill_id": str(_id(i, "skill", k, j)),
                        "topic_id": str(topic_id),
                        "domain_id": str(domain_id),
                        "name": skill,
                        "summary": skill,
                        "status": "active",
                        "origin": "learner",
                    },
                    actor="system",
                    idempotency_key=f"seed:{i}:skill:{k}:{j}",
                    domain_id=domain_id,
                )
            )
    return learner_id, domain_id, topic_ids, events


def seed_email(i: int) -> str:
    return f"loadtest+{i}@quack.invalid"


_INSERT_LEARNER = (
    "INSERT INTO core.learners (learner_id, email, settings) "
    "VALUES (%(learner_id)s, %(email)s, %(settings)s) ON CONFLICT (learner_id) DO NOTHING"
)
# Д-§11: real learners are those that are not marked as seeded; the check is over all learners.
_COUNT_REAL = (
    "SELECT COUNT(*) AS n FROM core.learners WHERE "
    "COALESCE((settings ->> 'seeded')::boolean, false) IS NOT TRUE"
)


async def _insert_learner(db: Db, learner_id: UUID, email: str) -> None:
    await db.execute(
        _INSERT_LEARNER,
        {"learner_id": learner_id, "email": email, "settings": Jsonb(SEEDED_SETTINGS)},
    )


async def _check_prod(db: Db, settings: Settings, confirm: bool) -> None:
    """Д-§11: on prod only with `--confirm` and only before real learners exist."""
    if settings.env != "prod":
        return
    if not confirm:
        raise SeedRefusedError("env=prod: seed-learners needs --confirm")
    row = await db.fetch_one(_COUNT_REAL)
    real = int(row["n"]) if row else 0
    if real:
        raise SeedRefusedError(f"env=prod: {real} real learners exist, refusing to seed")


async def seed_learners(
    db: Db,
    settings: Settings,
    *,
    count: int,
    fixture: str = DEFAULT_FIXTURE,
    confirm: bool = False,
) -> list[SeededLearner]:
    await _check_prod(db, settings, confirm)
    data = load_fixture(fixture)
    seeded: list[SeededLearner] = []
    for i in range(count):
        learner_id, domain_id, topic_ids, events = seed_events(i, data)
        async with db.transaction():
            await _insert_learner(db, learner_id, seed_email(i))
            await append_events(db, learner_id, events)
        token = await open_session(db, settings, learner_id=learner_id, now=datetime.now(UTC))
        seeded.append(SeededLearner(learner_id, token, domain_id, topic_ids))
    return seeded


def write_learners(path: Path, learners: list[SeededLearner]) -> None:
    path.write_text(
        json.dumps([learner.as_json() for learner in learners], indent=2) + "\n", encoding="utf-8"
    )
