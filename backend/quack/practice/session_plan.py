"""The plan document of a session, `content.session_plans.plan`, version 2 (plan graph).

One shape for every writer and reader: the turn (`turn_commit` merges each `planUpdate` into it),
`today.prepare` (a prepared draft), the teacher's slot 6 and the analyst. It lives in `practice`
because a product package imports only the core and `practice` (§4.1).

The document, JSON (`"v": 2`)::

    {"v": 2, "goal": str | null,
     "current_step": {"id", "text", "landmark": {"kind", "id"} | null} | null,
     "next_steps": [step, …],
     "landmark_edits": {"added": [{"kind", "id", "before_kind", "before_id"}, …],
                        "removed": [{"kind", "id"}, …]},
     "branches": [{"description", "type"}, …]}

A turn changes what it names and leaves the rest (`null` of the update is «no change»). The
document written before version 2 has no `"v"`: `{current_step: str, next_steps: [str], …}`;
`lift_plan` reads it as «no goal, no landmarks, the step is its own text». It is never rewritten
by itself — the next turn that writes the plan writes version 2.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quack.events.records import (
    BranchRecord,
    LandmarkAddRecord,
    LandmarkEditsRecord,
    LandmarkRefRecord,
    PlanStepRecord,
    PlanUpdateRecord,
)

PLAN_DOC_VERSION = 2


class _Doc(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class LandmarkEditsDoc(_Doc):
    """What the teacher changed in the landmarks the code built, accumulated over the session."""

    added: list[LandmarkAddRecord] = Field(default_factory=list)
    removed: list[LandmarkRefRecord] = Field(default_factory=list)


class PlanDoc(_Doc):
    v: Literal[2] = PLAN_DOC_VERSION
    goal: str | None = None
    current_step: PlanStepRecord | None = None
    next_steps: list[PlanStepRecord] = Field(default_factory=list)
    landmark_edits: LandmarkEditsDoc = Field(default_factory=LandmarkEditsDoc)
    branches: list[BranchRecord] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _ref(kind: object, node_id: object) -> LandmarkRefRecord | None:
    uid = _uuid(node_id)
    if kind not in ("skill", "topic") or uid is None:
        return None
    return LandmarkRefRecord(kind=kind, id=uid)


def _landmark_ref(value: object) -> LandmarkRefRecord | None:
    if not isinstance(value, Mapping):
        return None
    return _ref(value.get("kind"), value.get("id"))


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _old_step(value: object) -> PlanStepRecord | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return PlanStepRecord(id=value, text=value, landmark=None)


def _v2_step(value: object) -> PlanStepRecord | None:
    if not isinstance(value, Mapping):
        return None
    step_id, text = value.get("id"), value.get("text")
    if not isinstance(step_id, str) or not isinstance(text, str):
        return None
    return PlanStepRecord(id=step_id, text=text, landmark=_landmark_ref(value.get("landmark")))


def _v2_added(edits: Mapping[str, Any]) -> list[LandmarkAddRecord]:
    out: list[LandmarkAddRecord] = []
    for item in _list(edits.get("added")):
        if not isinstance(item, Mapping):
            continue
        ref = _ref(item.get("kind"), item.get("id"))
        if ref is None:
            continue
        before = _ref(item.get("before_kind"), item.get("before_id"))
        out.append(
            LandmarkAddRecord(
                kind=ref.kind,
                id=ref.id,
                before_kind=None if before is None else before.kind,
                before_id=None if before is None else before.id,
            )
        )
    return out


def lift_plan(raw: object) -> PlanDoc:
    """The document as version 2, whatever its stored form; `None` or something malformed — an
    empty plan (malformed parts are skipped rather than failing the read)."""
    if not isinstance(raw, Mapping):
        return PlanDoc()
    if raw.get("v") != PLAN_DOC_VERSION:
        next_steps = [_old_step(s) for s in _list(raw.get("next_steps"))]
        return PlanDoc(
            current_step=_old_step(raw.get("current_step")),
            next_steps=[s for s in next_steps if s is not None],
            branches=_old_branches(raw.get("branches")),
        )
    goal = raw.get("goal")
    next_steps = [_v2_step(s) for s in _list(raw.get("next_steps"))]
    edits = raw.get("landmark_edits")
    edits_map = edits if isinstance(edits, Mapping) else {}
    removed = [_landmark_ref(item) for item in _list(edits_map.get("removed"))]
    return PlanDoc(
        goal=goal if isinstance(goal, str) and goal.strip() else None,
        current_step=_v2_step(raw.get("current_step")),
        next_steps=[s for s in next_steps if s is not None],
        landmark_edits=LandmarkEditsDoc(
            added=_v2_added(edits_map),
            removed=[r for r in removed if r is not None],
        ),
        branches=_old_branches(raw.get("branches")),
    )


def _old_branches(raw: object) -> list[BranchRecord]:
    branches: list[BranchRecord] = []
    for item in _list(raw):
        try:
            branches.append(BranchRecord.model_validate(item))
        except ValidationError:
            continue
    return branches


def merge_plan(current: object, update: PlanUpdateRecord) -> PlanDoc:
    """The document after a turn: the fields the turn named replace the old ones, `null` leaves
    them. `branches` are the branches of the last turn (the teacher names the open ones each
    turn). Merging the same update again gives the same document (a retried commit)."""
    base = lift_plan(current)
    return PlanDoc(
        goal=base.goal if update.goal is None else update.goal,
        current_step=base.current_step if update.current_step is None else update.current_step,
        next_steps=base.next_steps if update.next_steps is None else update.next_steps,
        landmark_edits=_merge_edits(base.landmark_edits, update.landmark_edits),
        branches=update.branches,
    )


def _merge_edits(base: LandmarkEditsDoc, edits: LandmarkEditsRecord | None) -> LandmarkEditsDoc:
    """The last word on a landmark wins: adding what was removed cancels the removal, removing
    what was added cancels the addition."""
    if edits is None:
        return base
    added = {(a.kind, a.id): a for a in base.added}
    removed = {(r.kind, r.id): r for r in base.removed}
    for a in edits.add:
        removed.pop((a.kind, a.id), None)
        added[(a.kind, a.id)] = a
    for r in edits.remove:
        added.pop((r.kind, r.id), None)
        removed[(r.kind, r.id)] = r
    return LandmarkEditsDoc(added=list(added.values()), removed=list(removed.values()))


def prepared_plan(steps: list[PlanStepRecord]) -> PlanDoc:
    """A draft of a session of «Сегодня» (`today.prepare`): no goal (the teacher states it in the
    first turn), the draft steps."""
    return PlanDoc(
        current_step=steps[0] if steps else None,
        next_steps=steps[1:],
    )


__all__ = [
    "PLAN_DOC_VERSION",
    "LandmarkEditsDoc",
    "PlanDoc",
    "lift_plan",
    "merge_plan",
    "prepared_plan",
]
