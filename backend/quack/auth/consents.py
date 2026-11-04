"""Consents (Т-§19.4, ТЗ Ф4 WP18.4): `consent.given {kind, version}`, `consent.revoked`.

Without `consent.given {kind: terms}` of the current `terms_version` sessions do not start
(`CONSENT_REQUIRED`). `research` gates the evaluation sets (filter of WP16.7). The
idempotency key comes from the request (`Idempotency-Key`): a consent given again after a
revocation is a new event, so the key cannot be derived from kind and version (law 5).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.events.event_types import ConsentGiven, ConsentRevoked
from quack.events.log import NewEvent, append_events

type ConsentKind = Literal["terms", "research"]


async def give_consent(
    db: Db,
    learner_id: UUID,
    *,
    kind: ConsentKind,
    version: str,
    idempotency_key: str,
    redis: Redis | None = None,
) -> None:
    """`POST /v1/consents`: writes `consent.given`."""
    event = NewEvent(
        type="consent.given",
        payload=ConsentGiven(kind=kind, version=version),
        actor="learner",
        idempotency_key=idempotency_key,
    )
    await append_events(db, learner_id, [event], redis=redis)


async def revoke_research(
    db: Db,
    learner_id: UUID,
    *,
    version: str,
    idempotency_key: str,
    redis: Redis | None = None,
) -> None:
    """`DELETE /v1/consents/research`: writes `consent.revoked {kind: research}`."""
    event = NewEvent(
        type="consent.revoked",
        payload=ConsentRevoked(kind="research", version=version),
        actor="learner",
        idempotency_key=idempotency_key,
    )
    await append_events(db, learner_id, [event], redis=redis)


async def has_current_terms(db: Db, learner_id: UUID, settings: Settings) -> bool:
    """`consent.given {kind: terms, version: terms_version}` exists. With no `terms_version`
    configured (D-C-105) nobody has it."""
    if settings.terms_version is None:
        return False
    row = await db.fetch_one(
        "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'consent.given' AND payload->>'kind' = 'terms' "
        "AND payload->>'version' = %(version)s LIMIT 1",
        {"learner_id": learner_id, "version": settings.terms_version},
    )
    return row is not None
