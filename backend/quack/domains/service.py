"""Domains of the learner: creation and the list, over `Db` and the event log (ТЗ Ф4 WP16.4,
§6.1, §6.4; Т-§4.8).

There is no table of domains: a domain is its `domain.created` event; the list of domains is
read from the log, and the graph keeps `Domain` as a projection. Archiving is gone: the learner
removes a domain physically (`DELETE /v1/domains/{id}`, WP25).
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

from quack.db import Db
from quack.domains.schemas import (
    CreateDomainRequest,
    CreateDomainResponse,
    DomainListResponse,
    DomainSummary,
)
from quack.events.log import NewEvent, append_events
from quack.graph.ids import deterministic_id

_LIST = (
    "SELECT (payload ->> 'domain_id')::uuid AS domain_id, payload ->> 'name' AS name, "
    "seq AS created_seq, occurred_at AS created_at FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'domain.created' ORDER BY seq"
)


async def create_domain(
    db: Db, redis: Redis | None, learner_id: UUID, req: CreateDomainRequest
) -> CreateDomainResponse:
    """`domain.created` of the learner. The id comes from the key, so a retry writes the same
    event and returns the same domain (law 5); the same key with another name is `409`."""
    domain_id = UUID(deterministic_id(learner_id, 0, f"domain:{req.idempotency_key}"))
    event = NewEvent(
        type="domain.created",
        payload={"domain_id": str(domain_id), "name": req.name},
        actor="learner",
        idempotency_key=f"domain.created:{req.idempotency_key}",
        domain_id=domain_id,
    )
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return CreateDomainResponse(domain_id=domain_id, name=req.name, seq=stored.seq)


async def list_domains(db: Db, learner_id: UUID) -> DomainListResponse:
    """The learner's domains in the order they were created."""
    rows = await db.fetch_all(_LIST, {"learner_id": learner_id}, row=DomainSummary)
    return DomainListResponse(domains=rows)
