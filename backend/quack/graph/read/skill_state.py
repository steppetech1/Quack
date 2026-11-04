"""Skill state of the graph as read models (Т-§3.4.4, Т-§8.2–8.4): one mapper for every read.

The node `Skill` keeps the state in named properties (`l1Status` … `l6Status`, `ret*`, `exp*`,
`gapType`, `needsConsolidation`); the evidence is its own nodes. `skill_fact` turns the
properties and the active evidence rows into `SkillFact`, so a read from Neo4j (`prerequisites`)
and a replay in memory (`state_at`) cannot disagree. Retention is computed at read time from
`retStability` (days) and `retLastRecallAtMs` (Т-§8.3); nothing here calls a model (law 2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from quack.events.values import Level
from quack.graph.folds import retrievability
from quack.graph.read.models import (
    EvidenceSource,
    ExposureFact,
    LevelBasis,
    LevelFact,
    LevelStatus,
    RequiresLink,
    SkillFact,
)

LEVELS: tuple[Level, ...] = (1, 2, 3, 4, 5, 6)
_STATUSES: frozenset[str] = frozenset({"proven", "implied", "unstable", "failed"})
_BASES: frozenset[str] = frozenset({"direct", "transfer"})
_GAP_TYPES: frozenset[str] = frozenset(
    {"no_idea", "when_to_apply", "confusion", "execution", "slip", "misconception"}
)

# Properties of `Skill` the reads need (an explicit list: the node also holds `embedding`).
SKILL_PROPS: tuple[str, ...] = (
    "id",
    "name",
    "domainId",
    "status",
    *(f"l{n}{part}" for n in LEVELS for part in ("Status", "Basis", "DecisiveEvidenceId")),
    "retStability",
    "retLastRecallAtMs",
    "retDueAtMs",
    "retLastLapseAtMs",
    "needsConsolidation",
    "gapType",
    "gapTypeConfidence",
    "lastPracticeAtMs",
)
SKILL_PROJECTION = "{" + ", ".join(f".{name}" for name in SKILL_PROPS) + "}"


@dataclass(frozen=True)
class EvidenceRow:
    """The part of an active `Evidence` the level facts need."""

    level: int
    tier: int
    outcome: str
    at_ms: int
    seq: int


def source_of_tier(tier: int) -> EvidenceSource:
    """Т-§8.7: Т1 — checked by code, Т2 — by a model, Т3 and Т4 — from the conversation."""
    return "code" if tier <= 1 else "model" if tier == 2 else "conversation"


def _int(props: Mapping[str, Any], key: str) -> int:
    value = props.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _float(props: Mapping[str, Any], key: str) -> float:
    value = props.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def level_facts(props: Mapping[str, Any], evidence: Sequence[EvidenceRow]) -> list[LevelFact]:
    """The six levels of a skill. A level without data is `not_tested`, not `failed` (law 8)."""
    facts: list[LevelFact] = []
    for level in LEVELS:
        raw = props.get(f"l{level}Status")
        status: LevelStatus = raw if isinstance(raw, str) and raw in _STATUSES else "not_tested"  # type: ignore[assignment]
        raw_basis = props.get(f"l{level}Basis")
        basis: LevelBasis | None = (
            raw_basis if isinstance(raw_basis, str) and raw_basis in _BASES else None  # type: ignore[assignment]
        )
        decisive = props.get(f"l{level}DecisiveEvidenceId")
        rows = [row for row in evidence if row.level == level]
        tier1 = [row for row in rows if row.tier == 1]
        latest = max(tier1, key=lambda row: (row.at_ms, row.seq), default=None)
        facts.append(
            LevelFact(
                level=level,
                status=status,
                basis=basis,
                deciding_evidence_id=_uuid_or_none(decisive),
                evidence_count=len(rows),
                proven_by_direct=status == "proven" and basis == "direct",
                last_t1_failed=latest is not None and latest.outcome == "incorrect",
            )
        )
    return facts


def _uuid_or_none(value: object) -> UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def skill_fact(
    props: Mapping[str, Any],
    *,
    now_ms: int,
    evidence: Sequence[EvidenceRow] = (),
    exposures: Sequence[ExposureFact] = (),
    requires: Sequence[RequiresLink] = (),
    topic_id: str | None = None,
    topic_name: str | None = None,
) -> SkillFact:
    """`SkillFact` of a `Skill` node; `now_ms` is the moment retention is computed at."""
    stability = _float(props, "retStability")
    last_recall = _int(props, "retLastRecallAtMs")
    recall = (
        retrievability(stability, max(0, now_ms - last_recall))
        if stability > 0 and last_recall > 0
        else None
    )
    due = _int(props, "retDueAtMs")
    lapse = _int(props, "retLastLapseAtMs")
    gap = props.get("gapType")
    confidence = props.get("gapTypeConfidence")
    gap_type = gap if isinstance(gap, str) and gap in _GAP_TYPES else None
    domain = props.get("domainId")
    return SkillFact(
        skill_id=UUID(str(props["id"])),
        name=_str_or_none(props.get("name")),
        topic_id=UUID(topic_id) if topic_id else None,
        topic_name=topic_name,
        domain_id=UUID(domain) if isinstance(domain, str) and domain else None,
        levels=level_facts(props, evidence),
        recall_probability=recall,
        due_at_ms=due if due > 0 else None,
        consolidation_flag=props.get("needsConsolidation") is True,
        gap_type=gap_type,  # type: ignore[arg-type]
        gap_type_confidence=(
            float(confidence)
            if gap_type is not None and isinstance(confidence, int | float)
            else None
        ),
        last_lapse_at_ms=lapse if lapse > 0 else None,
        exposures=list(exposures),
        requires=list(requires),
    )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
