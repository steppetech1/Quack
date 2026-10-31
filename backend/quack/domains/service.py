"""Domain CRUD and archival over `core.domains` and `append_events` (ТЗ §2.2, §3; Т-§16.1)."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.domains.schemas import (
    ArchiveDomainRequest,
    ArchiveDomainResponse,
    CreateDomainRequest,
    CreateDomainResponse,
    DomainListResponse,
    DomainSummary,
)
from quack.events.append import append_events
from quack.events.models import NewEvent


def slugify_domain_name(name: str) -> str:
    """Generate a normalized lowercase ASCII/alphanumeric hyphenated slug from `name`
    (ТЗ §2.2, Т-§16.1).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "domain"


async def create_domain(
    resources: AppResources,
    learner_id: UUID,
    req: CreateDomainRequest,
) -> CreateDomainResponse:
    """Create a new domain for `learner_id`, checking `(learner_id, slug)` uniqueness and
    emitting `domain.created` (ТЗ §2.2, §3; Т-§16.1).
    """
    slug = req.slug if req.slug else slugify_domain_name(req.name)

    async with resources.session_factory() as session, session.begin():
        existing_domain = (
            (
                await session.execute(
                    text(
                        "SELECT id, name, slug, status FROM core.domains "
                        "WHERE learner_id = :l AND slug = :s"
                    ),
                    {"l": learner_id, "s": slug},
                )
            )
            .mappings()
            .first()
        )

        if existing_domain:
            existing_event = (
                (
                    await session.execute(
                        text(
                            "SELECT event_id, seq, payload FROM core.events "
                            "WHERE learner_id = :l AND idempotency_key = :k"
                        ),
                        {"l": learner_id, "k": req.idempotency_key},
                    )
                )
                .mappings()
                .first()
            )

            if (
                existing_event
                and str(existing_domain["id"]) == existing_event["payload"].get("domain_id")
                and existing_event["payload"].get("name") == req.name
            ):
                return CreateDomainResponse(
                    domain_id=existing_domain["id"],
                    name=existing_domain["name"],
                    slug=existing_domain["slug"],
                    status=existing_domain["status"],
                    seq=existing_event["seq"],
                )

            raise ApiError(
                "IDEMPOTENCY_CONFLICT",
                f"Domain with slug '{slug}' already exists",
                status_code=409,
            )

        domain_id = uuid4()
        event = NewEvent(
            type="domain.created",
            actor="learner",
            domain_id=domain_id,
            idempotency_key=req.idempotency_key,
            payload={
                "domain_id": str(domain_id),
                "name": req.name,
                "slug": slug,
                "has_materials": False,
            },
        )
        stored = await append_events(
            session, learner_id, [event], publish_wakeup=True, redis=resources.redis
        )
        return CreateDomainResponse(
            domain_id=domain_id,
            name=req.name,
            slug=slug,
            status="onboarding",
            seq=stored[0].seq,
        )


async def list_domains(
    resources: AppResources,
    learner_id: UUID,
    *,
    include_archived: bool = False,
) -> DomainListResponse:
    """List learner domains from `core.domains` (ТЗ §3, Т-§16.1)."""
    async with resources.session_factory() as session:
        if include_archived:
            query = text(
                "SELECT id, name, slug, status, has_materials, active_structure_version, "
                "created_seq, updated_seq, created_at, updated_at FROM core.domains "
                "WHERE learner_id = :l ORDER BY created_at ASC"
            )
        else:
            query = text(
                "SELECT id, name, slug, status, has_materials, active_structure_version, "
                "created_seq, updated_seq, created_at, updated_at FROM core.domains "
                "WHERE learner_id = :l AND status != 'archived' ORDER BY created_at ASC"
            )
        rows = (await session.execute(query, {"l": learner_id})).mappings().all()
        domains = [
            DomainSummary(
                domain_id=row["id"],
                name=row["name"],
                slug=row["slug"],
                status=row["status"],
                has_materials=row["has_materials"],
                active_structure_version=row["active_structure_version"],
                created_seq=row["created_seq"],
                updated_seq=row["updated_seq"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        return DomainListResponse(domains=domains)


async def get_domain(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
) -> DomainSummary:
    """Return a single domain summary from `core.domains` or raise `404 NOT_FOUND`
    (ТЗ §3, Т-§16.1).
    """
    async with resources.session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id, name, slug, status, has_materials, active_structure_version, "
                        "created_seq, updated_seq, created_at, updated_at FROM core.domains "
                        "WHERE id = :id AND learner_id = :l"
                    ),
                    {"id": domain_id, "l": learner_id},
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise ApiError("NOT_FOUND", f"Domain {domain_id} not found", status_code=404)
        return DomainSummary(
            domain_id=row["id"],
            name=row["name"],
            slug=row["slug"],
            status=row["status"],
            has_materials=row["has_materials"],
            active_structure_version=row["active_structure_version"],
            created_seq=row["created_seq"],
            updated_seq=row["updated_seq"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


async def archive_domain(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    req: ArchiveDomainRequest,
) -> ArchiveDomainResponse:
    """Archive an existing domain by emitting `domain.archived` (ТЗ §3, Т-§16.1)."""
    async with resources.session_factory() as session, session.begin():
        row = (
            await session.execute(
                text("SELECT id, status FROM core.domains WHERE id = :id AND learner_id = :l"),
                {"id": domain_id, "l": learner_id},
            )
        ).first()
        if not row:
            raise ApiError("NOT_FOUND", f"Domain {domain_id} not found", status_code=404)

        event = NewEvent(
            type="domain.archived",
            actor="learner",
            domain_id=domain_id,
            idempotency_key=req.idempotency_key,
            payload={
                "domain_id": str(domain_id),
                "reason": req.reason,
            },
        )
        stored = await append_events(
            session, learner_id, [event], publish_wakeup=True, redis=resources.redis
        )
        return ArchiveDomainResponse(
            domain_id=domain_id,
            status="archived",
            seq=stored[0].seq,
        )
