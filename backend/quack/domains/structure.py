"""Domain structure onboarding, `structurer@1` invocation, deterministic DAG cycle breaking,
LLM degradation fallback, confirmation, and manual structure editing
(ТЗ §2.2, §3, §4; Т-§3.1, Т-§3.2, Т-§16.2, Т-§17.1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID

from quack.api.app import AppResources
from quack.config import Settings
from quack.domains.schemas import (
    ConfirmStructureRequest,
    ConfirmStructureResponse,
    EditStructureRequest,
    EditStructureResponse,
    MisconceptionDraft,
    PrereqEdge,
    ProposeStructureRequest,
    ProposeStructureResponse,
    SkillDepEdge,
    SkillDraft,
    StructurerOutput,
    TopicDraft,
    UpdateTopicStatusRequest,
    UpdateTopicStatusResponse,
)
from quack.llm.gateway import LLMGateway


def deterministic_structure_node_id(
    domain_id: UUID,
    structure_version: int,
    kind: str,
    key: str | int,
) -> UUID:
    """Compute deterministic UUIDv5 for a structure node from `(domain_id, structure_version,
    kind, key)` (ТЗ §2.2, §4; Т-§2.5, Т-§3.2).
    """
    raise NotImplementedError


def break_cycles[T](
    nodes: Iterable[T],
    edges: Sequence[tuple[T, T]],
) -> tuple[list[tuple[T, T]], list[tuple[T, T]]]:
    """Deterministically detect and remove back-edges closing cycles in a directed graph,
    returning `(acyclic_edges, removed_edges)` (ТЗ §2.2, Т-§3.2).
    """
    raise NotImplementedError


def has_cycle[T](
    nodes: Iterable[T],
    edges: Iterable[tuple[T, T]],
) -> bool:
    """Return True if the directed graph `(nodes, edges)` contains any cycle (ТЗ §2.2, Т-§16.2)."""
    raise NotImplementedError


def sanitize_structurer_output(
    domain_id: UUID,
    structure_version: int,
    output: StructurerOutput,
) -> tuple[
    list[TopicDraft],
    list[SkillDraft],
    list[PrereqEdge],
    list[SkillDepEdge],
    list[MisconceptionDraft],
]:
    """Map LLM slugs to deterministic UUIDv5, drop dangling references and self-loops, and
    break cycles in topic `PREREQ_OF` and skill `DEPENDS_ON` edges (ТЗ §2.2, §4; Т-§3.2).
    """
    raise NotImplementedError


def build_degraded_structure(
    domain_id: UUID,
    structure_version: int,
    focus_areas: Sequence[str],
) -> tuple[
    list[TopicDraft],
    list[SkillDraft],
    list[PrereqEdge],
    list[SkillDepEdge],
    list[MisconceptionDraft],
]:
    """Build a minimal linear DAG skeleton from `focus_areas` when LLM is unavailable or over
    budget (ТЗ §2.2, Т-§17.1).
    """
    raise NotImplementedError


async def propose_structure(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: ProposeStructureRequest,
) -> ProposeStructureResponse:
    """Record `structure.interview_completed` (if onboarding), call `structurer@1` (with
    degradation fallback on `LLMUnavailableError` / `LLMBudgetExceededError`), sanitize DAG,
    and emit `structure.proposed` (ТЗ §2.2, §3; Т-§3.1, Т-§3.2, Т-§17.1).
    """
    raise NotImplementedError


async def confirm_structure(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    req: ConfirmStructureRequest,
) -> ConfirmStructureResponse:
    """Confirm proposed structure and emit `structure.confirmed` (ТЗ §2.2, §3; Т-§3.2, Т-§16.2)."""
    raise NotImplementedError


async def edit_structure(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    req: EditStructureRequest,
) -> EditStructureResponse:
    """Validate manual edits against the existing `PREREQ_OF` DAG in Neo4j (rejecting cycles
    with `422 VALIDATION_ERROR`) and emit `structure.edited` (ТЗ §2.2, §3; Т-§16.2).
    """
    raise NotImplementedError


async def update_topic_status(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    req: UpdateTopicStatusRequest,
) -> UpdateTopicStatusResponse:
    """Change topic status (`active`, `skipped`, `archived`) via `topic.status_changed`
    (ТЗ §2.2, §3; Т-§16.2).
    """
    raise NotImplementedError
