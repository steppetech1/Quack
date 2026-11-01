"""`ops seed-learners` (ТЗ Фазы 3 WP8.6, Д-§11): test learners for the load stand.

Each learner gets an `auth.users` row, `learner.registered` and one domain with a confirmed
structure from a fixture, all through `append_events` with `actor = "system"` and without a
model. IDs and idempotency keys are deterministic (`uuid5(QUACK_UUID_NS, "seed:{i}:…")`), so a
second run with the same N creates nothing new; tokens are issued again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import orjson
from sqlalchemy import text

from quack.api.auth import issue_access_token
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.models import NewEvent
from quack.graph.ids import QUACK_UUID_NS

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "loadtest" / "fixtures"
DEFAULT_FIXTURE = "default"
SEEDED_PREFERENCES: dict[str, Any] = {"seeded": True}
# Not a password hash: seeded learners log in with the issued token only.
_NO_PASSWORD = "!seeded"


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
    """The events of learner `i`: registered, domain created, structure proposed and confirmed."""
    learner_id, domain_id = _id(i, "learner"), _id(i, "domain")
    d = str(domain_id)
    topics: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    topic_ids: list[UUID] = []
    for k, topic in enumerate(fixture["topics"]):
        topic_id = _id(i, "topic", k)
        topic_ids.append(topic_id)
        topics.append({"topic_id": str(topic_id), "name": topic["name"]})
        for j, skill in enumerate(topic["skills"]):
            skills.append(
                {"skill_id": str(_id(i, "skill", k, j)), "topic_id": str(topic_id), "name": skill}
            )
    events = [
        NewEvent(
            type="learner.registered",
            payload={
                "email": seed_email(i),
                "display_name": f"Load test {i}",
                "preferences": SEEDED_PREFERENCES,
            },
            actor="system",
            idempotency_key=f"seed:{i}:registered",
        ),
        NewEvent(
            type="domain.created",
            payload={"domain_id": d, "name": fixture["domain"], "slug": f"seed-{i}"},
            actor="system",
            idempotency_key=f"seed:{i}:domain",
            domain_id=domain_id,
        ),
        NewEvent(
            type="structure.proposed",
            payload={
                "domain_id": d,
                "structure_version": 1,
                "topics": topics,
                "skills": skills,
                "prereqs": [],
                "skill_deps": [],
                "misconceptions": [],
            },
            actor="system",
            idempotency_key=f"seed:{i}:structure",
            domain_id=domain_id,
        ),
        NewEvent(
            type="structure.confirmed",
            payload={"domain_id": d, "structure_version": 1},
            actor="system",
            idempotency_key=f"seed:{i}:confirmed",
            domain_id=domain_id,
        ),
    ]
    return learner_id, domain_id, topic_ids, events


def seed_email(i: int) -> str:
    return f"loadtest+{i}@quack.invalid"


async def _check_prod(session_factory: SessionFactory, settings: Settings, confirm: bool) -> None:
    """Д-§11: on prod only with `--confirm` and only before real users exist."""
    if settings.env != "prod":
        return
    if not confirm:
        raise SeedRefusedError("env=prod: seed-learners needs --confirm")
    async with session_factory() as session:
        real = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM auth.users WHERE "
                    "COALESCE((preferences->>'seeded')::boolean, false) IS NOT TRUE"
                )
            )
        ).scalar_one()
    if real:
        raise SeedRefusedError(f"env=prod: {real} real users exist, refusing to seed")


async def seed_learners(
    session_factory: SessionFactory,
    settings: Settings,
    *,
    count: int,
    fixture: str = DEFAULT_FIXTURE,
    confirm: bool = False,
) -> list[SeededLearner]:
    await _check_prod(session_factory, settings, confirm)
    data = load_fixture(fixture)
    seeded: list[SeededLearner] = []
    for i in range(count):
        learner_id, domain_id, topic_ids, events = seed_events(i, data)
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO auth.users (id, email, password_hash, display_name, "
                    "preferences) VALUES (:id, :email, :pw, :name, CAST(:prefs AS JSONB)) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": learner_id,
                    "email": seed_email(i),
                    "pw": _NO_PASSWORD,
                    "name": f"Load test {i}",
                    "prefs": orjson.dumps(SEEDED_PREFERENCES).decode(),
                },
            )
            await append_events(session, learner_id, events, publish_wakeup=False)
        seeded.append(
            SeededLearner(
                learner_id, issue_access_token(learner_id, settings), domain_id, topic_ids
            )
        )
    return seeded


def write_learners(path: Path, learners: list[SeededLearner]) -> None:
    path.write_text(
        json.dumps([learner.as_json() for learner in learners], indent=2) + "\n", encoding="utf-8"
    )
