"""Slot 6 in the preparation mode (ТЗ Ф4 WP17.4; Т-§14.7, Т-§10.1; `T-RV4`). D-B-216.

The mode is on only by an explicit event: the latest `prep_mode.*` of the goal's hub is
`prep_mode.enabled` (a plain visit to the hub does not turn it on). Then slot 6 gets, after the
goal and the plan, what `GraphReader.prep_context` (A, D-A-152) found: days to the deadline,
readiness and coverage of the hub, the control materials found and the skills to repeat first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from quack.config import Settings
from quack.db.db import Db
from quack.graph.read import GraphReader, PrepContext
from quack.llm.labels import LabelKind, LabelMap

# Own SQL over the learner's journal (§4.1: no import of `review`).
_LAST_PREP = (
    "SELECT type FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type IN ('prep_mode.enabled', 'prep_mode.disabled') "
    "AND payload->>'hub_id' = %(hub)s ORDER BY seq DESC LIMIT 1"
)

_KINDS = {
    "sample_exam": "a sample exam",
    "past_variant": "a past exam variant",
    "question_list": "a list of questions",
    "criteria": "grading criteria",
}


async def prep_enabled(db: Db, *, learner_id: UUID, hub_id: UUID) -> bool:
    row = await db.fetch_one(_LAST_PREP, {"learner_id": learner_id, "hub": str(hub_id)})
    return row is not None and row["type"] == "prep_mode.enabled"


async def prep_for_turn(
    reader: GraphReader, db: Db, *, learner_id: UUID, hub_id: UUID | None, now_ms: int
) -> PrepContext | None:
    """The data of slot 6 while the hub of the session is in the preparation mode."""
    if hub_id is None or not await prep_enabled(db, learner_id=learner_id, hub_id=hub_id):
        return None
    return await reader.prep_context(hub_id, now_ms)


def render_prep(
    prep: PrepContext, labels: LabelMap, names: Mapping[UUID, str], settings: Settings
) -> str:
    """The lines appended to slot 6; objects are labels, never ids (Т-§4.5)."""
    limit = settings.prep_slot_max_items
    hub = labels.label("hub", prep.hub_id) or "the hub"
    lines = [
        f"Preparation mode is on for {hub}"
        + (f" «{names[prep.hub_id]}»" if prep.hub_id in names else "")
    ]
    if prep.days_to_deadline is not None:
        lines[0] += f"; {prep.days_to_deadline} days to the deadline"
    lines.append(f"Ready topics: {prep.ready_count} of {prep.total_count}")
    topics = _labelled("topic", prep.coverage_topic_ids, labels, limit)
    if topics:
        lines.append("Coverage: " + ", ".join(topics))
    if prep.control_materials:
        found = [
            _KINDS[m.kind] + (f", page {m.page}" if m.page is not None else "")
            for m in prep.control_materials[:limit]
        ]
        lines.append("Control materials found: " + "; ".join(found))
    skills = _labelled("skill", [c.skill_id for c in prep.candidates], labels, limit)
    if skills:
        lines.append("Skills to repeat first: " + ", ".join(skills))
    return "\n".join(lines)


def _labelled(kind: LabelKind, ids: Sequence[UUID], labels: LabelMap, limit: int) -> list[str]:
    found = (labels.label(kind, i) for i in ids)
    return [label for label in found if label][:limit]
