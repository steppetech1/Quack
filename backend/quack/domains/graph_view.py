"""Knowledge map and topic detail views for the UI using `read_consistent_slice` with
read-time recomputation of `effective_mastery`, `is_rusty`, and `display_state`
(ТЗ §2.4, §3; Т-§8.6, Т-§22.2).
"""

from __future__ import annotations

from uuid import UUID

from quack.api.app import AppResources
from quack.config import Settings
from quack.domains.schemas import DomainGraphResponse, TopicDetailResponse


async def get_domain_graph_view(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    *,
    include_archived: bool = False,
) -> DomainGraphResponse:
    """Read consistent domain knowledge graph slice from Neo4j, recomputing `display_state`
    (`rusty` vs `solid`) as of `now_ms` and excluding `merged` (and `archived` unless
    `include_archived=True`) nodes (ТЗ §2.4, §3; Т-§8.6, Т-§22.2).
    """
    raise NotImplementedError


async def get_topic_detail_view(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
) -> TopicDetailResponse:
    """Read consistent topic card details (topic, skills with `effective_mastery` and
    `is_rusty`, `skill_deps`, `misconceptions`, `artifacts`) as of `now_ms`
    (ТЗ §2.4, §3; Т-§8.6, Т-§22.2).
    """
    raise NotImplementedError
