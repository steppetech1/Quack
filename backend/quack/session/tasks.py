"""Session overlay helpers of the turn (ТЗ-П2 §2.3 steps 4-5).

The task pick and the answer check live in `quack.practice` (WP6.2, D-B-006): one
implementation for the turn and for `POST .../tasks/{id}/submit`.
"""

from __future__ import annotations

import contextlib
from uuid import UUID

from quack.graph.overlay import SessionOverlay


def assigned_task_ids_from_overlay(overlay: SessionOverlay) -> set[UUID]:
    """All task IDs assigned or evaluated in the session overlay or graph."""
    assigned = set(overlay.assigned_task_ids)
    for (label, nid), props in overlay.graph.nodes.items():
        if label == "Task" and props.get("status") in ("assigned", "evaluated"):
            with contextlib.suppress(ValueError, TypeError):
                assigned.add(UUID(nid))
    return assigned


def solved_families_from_overlay(overlay: SessionOverlay) -> set[str]:
    """All family IDs with a correct evaluation or positive evidence in overlay."""
    solved: set[str] = set()
    for (label, _), props in overlay.graph.nodes.items():
        if (
            label == "Evidence"
            and props.get("direction") == "positive"
            and not props.get("superseded", False)
        ):
            fam = props.get("familyId")
            if fam and fam != "unknown":
                solved.add(str(fam))
    for tid, ev in overlay.evaluations.items():
        if ev.is_correct:
            task_props = overlay.graph.get("Task", str(tid))
            if task_props:
                fam = task_props.get("familyId")
                if fam and fam != "unknown":
                    solved.add(str(fam))
    return solved
