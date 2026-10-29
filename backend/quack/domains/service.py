"""Domain CRUD and archival over `core.domains` and `append_events` (ТЗ §2.2, §3; Т-§16.1)."""

from __future__ import annotations

from uuid import UUID

from quack.api.app import AppResources
from quack.domains.schemas import (
    ArchiveDomainRequest,
    ArchiveDomainResponse,
    CreateDomainRequest,
    CreateDomainResponse,
    DomainListResponse,
    DomainSummary,
)


def slugify_domain_name(name: str) -> str:
    """Generate a normalized lowercase ASCII/alphanumeric hyphenated slug from `name`
    (ТЗ §2.2, Т-§16.1).
    """
    raise NotImplementedError


async def create_domain(
    resources: AppResources,
    learner_id: UUID,
    req: CreateDomainRequest,
) -> CreateDomainResponse:
    """Create a new domain for `learner_id`, checking `(learner_id, slug)` uniqueness and
    emitting `domain.created` (ТЗ §2.2, §3; Т-§16.1).
    """
    raise NotImplementedError


async def list_domains(
    resources: AppResources,
    learner_id: UUID,
    *,
    include_archived: bool = False,
) -> DomainListResponse:
    """List learner domains from `core.domains` (ТЗ §3, Т-§16.1)."""
    raise NotImplementedError


async def get_domain(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
) -> DomainSummary:
    """Return a single domain summary from `core.domains` or raise `404 NOT_FOUND`
    (ТЗ §3, Т-§16.1).
    """
    raise NotImplementedError


async def archive_domain(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    req: ArchiveDomainRequest,
) -> ArchiveDomainResponse:
    """Archive an existing domain by emitting `domain.archived` (ТЗ §3, Т-§16.1)."""
    raise NotImplementedError
