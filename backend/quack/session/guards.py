"""Defensive rules of a teacher turn checked by code (Т-§12.4; ТЗ Ф4 WP13.5), pure part.

The rules find violations; what the turn graph does with them depends on the stage, not on the
reason alone. Only what is found **before the body text is shown** returns the turn to the model
(the rollback is cheap and invisible): in the head (`p1_head`) an empty `targetSkills` and an
unknown skill label (`HEAD_REJECTIONS`), and later the spoiler and a cut answer (not here). The
markup (`p3_markup`) is checked after the text is shown, so it never returns the turn: code
settles it (`drop_unknown_markup_labels`) and the case is logged (`guard_violation`).

Head: honest verdict — `verdict_stance = code` only when the turn answers an answer with a
code-class verdict (`settle_stance` lowers `code` to `formative` at once, no retry); the turn is
marked — `targetSkills` is not empty (a domain with no skills yet has none to name, so it may be
empty there) and every label exists (a violation before the text is shown goes back to the model
with its reasons, WP13.2 `guard`, up to `guard_max_retries`; after the retries `drop_unknown_head_
labels` takes the labels that do not exist out of what is committed and the unmarked turn is
shown and logged). «Do not jump the boundary» (head): every prerequisite of a target skill by
`REQUIRES` with `confidence >= requires_min_confidence` has a showing or evidence (the slice of
seam S4 reads them with the session overlay) or is a target of this turn too (D-B-153); the
violation is only logged (`boundary_gaps` names it by labels for the log). Unsure links are a
warning in the context (WP14), not a rule.

Markup (`p3_markup`): exposures and `targetsMisconception` reference existing labels; every
label the write turns into an id is checked, not only skills and misconceptions: those of agent
requests, `cancel_requests`, the landmarks of the plan, the elements (`card_series`,
`visualization`, `material_link`) and the deadline (`LabelMap`, «short labels exist», Т-§12.4).
`drop_unknown_markup_labels` takes what does not exist out of what is committed, so the write of
the turn does not fail on it; an element with an unknown label is the exception the graph gives a
fix call first (`unresolved_element_labels`, `keep_elements`) and removes with its block when the
fix fails. The format of the plan (a step `id` is a slug, `next_steps` is at most
`next_steps_max`) is no violation to return the turn for: code makes the slug and cuts the list.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from quack.graph.read.models import RequiresLink
from quack.llm.labels import LabelKind, LabelMap, UnknownLabelError
from quack.practice.check import Verdict
from quack.session.elements import (
    CardSeriesElement,
    CodeTaskElement,
    Element,
    MaterialLinkElement,
    VisualizationElement,
)
from quack.session.teacher_output import (
    AgentRequest,
    LandmarkEdits,
    PlanUpdate,
    Step,
    TurnHead,
    TurnMarkup,
)

type GuardReason = Literal[
    "stance_without_code_verdict",
    "no_target_skills",
    "unknown_skill_label",
    "unknown_misconception_label",
    "unknown_label",
    "spoiler",
    "boundary_jump",
    "plan_step",
]

# What returns the turn to the model: only violations of the head, found before any body character
# is shown. The other reasons are settled or logged by code (see the module docstring); the markup
# reasons are listed in `GuardReason` for the log and for the offline replay (`markup_violations`).
HEAD_REJECTIONS: frozenset[GuardReason] = frozenset({"no_target_skills", "unknown_skill_label"})

# The slug of a plan step (`planUpdate` step `id`): stable within the session.
STEP_ID = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class GuardViolation:
    reason: GuardReason
    detail: str


@dataclass(frozen=True)
class Boundary:
    """`REQUIRES` of the target skills from the slice (S4), by skill label; `skill_ids` —
    label → id of the label table; `min_confidence` — `requires_min_confidence`."""

    requires: Mapping[str, Sequence[RequiresLink]] = field(default_factory=dict)
    skill_ids: Mapping[str, UUID] = field(default_factory=dict)
    min_confidence: float = 1.0


def head_rejections(violations: Sequence[GuardViolation]) -> list[GuardViolation]:
    """The violations of the head that return the turn to the model (before the body is shown)."""
    return [v for v in violations if v.reason in HEAD_REJECTIONS]


def head_violations(
    head: TurnHead,
    *,
    answered_verdict: Verdict | None,
    skill_labels: Collection[str],
    boundary: Boundary | None = None,
) -> list[GuardViolation]:
    """Violations of the head rules (Т-§12.4: honest verdict, do not jump the boundary, the
    turn is marked)."""
    violations: list[GuardViolation] = []
    if head.verdict_stance == "code" and not _code_verdict(answered_verdict):
        violations.append(
            GuardViolation(
                "stance_without_code_verdict",
                "verdict_stance code needs a code-class verdict of the answered answer",
            )
        )
    # the topic is the object of a split; a domain with no skills has nothing to target
    if not head.target_skills and head.intent != "split_topic" and skill_labels:
        violations.append(GuardViolation("no_target_skills", "targetSkills is empty"))
    violations.extend(_unknown("unknown_skill_label", head.target_skills, skill_labels))
    if boundary is not None:
        violations.extend(_boundary_jumps(head, boundary))
    return violations


@dataclass(frozen=True)
class BoundaryGap:
    """A target skill (by label) with prerequisites that are neither shown nor evidenced nor
    taken by the turn: their labels, and how many of them have no label."""

    skill: str
    missing: tuple[str, ...]
    unlabeled: int


def boundary_gaps(head: TurnHead, boundary: Boundary) -> list[BoundaryGap]:
    """One gap for each target whose sure prerequisites are neither shown nor evidenced nor
    taken by this turn; prerequisites without a label are counted."""
    labels = {skill_id: label for label, skill_id in boundary.skill_ids.items()}
    targets = {boundary.skill_ids[s] for s in head.target_skills if s in boundary.skill_ids}
    gaps: list[BoundaryGap] = []
    for skill in dict.fromkeys(head.target_skills):
        missing = list(
            dict.fromkeys(
                link.skill_id
                for link in boundary.requires.get(skill, ())
                if link.confidence >= boundary.min_confidence
                and not (link.has_exposure or link.has_evidence)
                and link.skill_id not in targets
            )
        )
        if not missing:
            continue
        named = tuple(labels[m] for m in missing if m in labels)
        gaps.append(BoundaryGap(skill, named, len(missing) - len(named)))
    return gaps


def _boundary_jumps(head: TurnHead, boundary: Boundary) -> list[GuardViolation]:
    """One violation naming, by label, each target whose sure prerequisites are neither shown
    nor evidenced nor taken by this turn; prerequisites without a label are counted."""
    parts: list[str] = []
    for gap in boundary_gaps(head, boundary):
        text = ", ".join(gap.missing) + (f" and {gap.unlabeled} unlabeled" if gap.unlabeled else "")
        parts.append(f"{gap.skill} needs {text.removeprefix(' and ')} first")
    if not parts:
        return []
    detail = "; ".join(parts) + " (not shown, not practised, not a target of this turn)"
    return [GuardViolation("boundary_jump", detail)]


def markup_violations(
    markup: TurnMarkup,
    *,
    skill_labels: Collection[str],
    misconception_labels: Collection[str],
    labels: LabelMap | None = None,
    next_steps_max: int | None = None,
) -> list[GuardViolation]:
    """Violations of «the turn is marked» in `p3_markup`: unknown labels of exposures and of
    `targetsMisconception`; with the label table of the call (`labels`) also the labels of agent
    requests, `cancel_requests`, the landmarks of the plan, elements and the deadline — whatever
    the write resolves. The plan: a step `id` is a slug, `next_steps` is at most `next_steps_max`
    (`Settings.plan_next_steps_max`; `None` — not checked)."""
    violations = _unknown("unknown_skill_label", [e.skill for e in markup.exposures], skill_labels)
    if markup.targets_misconception is not None:
        violations.extend(
            _unknown(
                "unknown_misconception_label",
                [markup.targets_misconception],
                misconception_labels,
            )
        )
    if labels is not None:
        missing = [
            f"{label} ({kind}, {where})"
            for where, kind, label in _label_uses(markup)
            if not _resolves(labels, kind, label)
        ]
        if missing:
            detail = f"unknown labels: {', '.join(dict.fromkeys(missing))}"
            violations.append(GuardViolation("unknown_label", detail))
    violations.extend(_plan_violations(markup.plan_update, next_steps_max))
    return violations


def _plan_steps(plan: PlanUpdate) -> list[tuple[str, Step]]:
    steps = [("planUpdate.current_step", plan.current_step)] if plan.current_step else []
    steps += [(f"planUpdate.next_steps[{i}]", s) for i, s in enumerate(plan.next_steps or [])]
    return steps


def _plan_violations(plan: PlanUpdate, next_steps_max: int | None) -> list[GuardViolation]:
    parts = [
        f"{where}.id {step.id!r} is not a slug (a-z, 0-9, - and _)"
        for where, step in _plan_steps(plan)
        if not STEP_ID.fullmatch(step.id)
    ]
    if next_steps_max is not None and len(plan.next_steps or []) > next_steps_max:
        parts.append(f"planUpdate.next_steps has more than {next_steps_max} steps")
    return [GuardViolation("plan_step", "; ".join(parts))] if parts else []


def _resolves(labels: LabelMap, kind: LabelKind, label: str) -> bool:
    try:
        labels.resolve(kind, label)
    except UnknownLabelError:
        return False
    return True


def _request_labels(request: AgentRequest) -> list[tuple[LabelKind, str]]:
    params = request.params
    pairs: list[tuple[LabelKind, str | None]] = [
        ("skill", params.skill),
        ("topic", params.topic),
        ("task", params.task),
        ("misconception", params.misconception),
    ]
    return [(kind, label) for kind, label in pairs if label is not None]


def _element_labels(element: Element) -> list[tuple[LabelKind, str]]:
    if isinstance(element, CardSeriesElement):
        return [("card", c) for c in element.cards]
    if isinstance(element, VisualizationElement) and element.request is not None:
        return [("request", element.request)]
    if isinstance(element, MaterialLinkElement):
        return [("chunk", element.chunk)]
    if isinstance(element, CodeTaskElement):
        return [("task", element.task)]
    return []


def unresolved_element_labels(element: Element, labels: LabelMap | None) -> list[str]:
    """The labels of `element` that the label table does not know, as `P9 (chunk)`; without the
    table nothing is checked. An element with such a label cannot be written (`turn_batch`)."""
    if labels is None:
        return []
    return [
        f"{label} ({kind})"
        for kind, label in _element_labels(element)
        if not _resolves(labels, kind, label)
    ]


def _label_uses(markup: TurnMarkup) -> list[tuple[str, LabelKind, str]]:
    """`(where, kind, label)` of the labels of `p3_markup` that the write of the turn turns into
    ids (`turn_commit`), apart from exposures and `targetsMisconception`."""
    uses: list[tuple[str, LabelKind, str]] = []
    for i, request in enumerate(markup.agent_requests):
        uses.extend((f"agentRequests[{i}]", k, label) for k, label in _request_labels(request))
    uses.extend(
        ("planUpdate.cancel_requests", "request", r) for r in markup.plan_update.cancel_requests
    )
    uses.extend(_landmark_uses(markup.plan_update))
    for element in markup.elements:
        uses.extend((f"element {element.element_id}", k, v) for k, v in _element_labels(element))
    deadline = markup.deadline
    if deadline is not None:
        if deadline.hub_label is not None:
            uses.append(("deadline.hub_label", "hub", deadline.hub_label))
        uses.extend(("deadline.coverage", "topic", t) for t in deadline.coverage)
    return uses


def landmark_kind(label: str) -> Literal["skill", "topic"]:
    """The kind of a landmark label: `T…` is a topic, anything else is read as a skill (a label
    that is not `S…` does not resolve, so it is unknown)."""
    return "topic" if label.startswith("T") else "skill"


def _landmark_uses(plan: PlanUpdate) -> list[tuple[str, LabelKind, str]]:
    """The landmark labels of the plan: of the steps, of `add` (with `before`) and of `remove`."""
    uses: list[tuple[str, LabelKind, str]] = [
        (where, landmark_kind(step.landmark), step.landmark)
        for where, step in _plan_steps(plan)
        if step.landmark is not None
    ]
    edits = plan.landmark_edits
    if edits is not None:
        for add in edits.add:
            uses.append(("planUpdate.landmark_edits.add", landmark_kind(add.label), add.label))
            if add.before is not None:
                uses.append(
                    ("planUpdate.landmark_edits.add.before", landmark_kind(add.before), add.before)
                )
        uses.extend(
            ("planUpdate.landmark_edits.remove", landmark_kind(label), label)
            for label in edits.remove
        )
    return uses


def drop_unknown_head_labels(head: TurnHead, skill_labels: Collection[str]) -> TurnHead:
    """After the retries: target skills that are not in the label table are dropped (the turn
    stays shown and logged, but unmarked by them), so `turn_batch` has nothing to reject."""
    known = [s for s in head.target_skills if s in skill_labels]
    if len(known) == len(head.target_skills):
        return head
    return head.model_copy(update={"target_skills": known})


def drop_unknown_markup_labels(
    markup: TurnMarkup,
    *,
    skill_labels: Collection[str],
    misconception_labels: Collection[str],
    labels: LabelMap | None = None,
    next_steps_max: int | None = None,
    keep_elements: bool = False,
) -> TurnMarkup:
    """Settles the markup at every turn (it is checked after its text is shown, so it never
    returns the turn): exposures of unknown skills and an unknown `targetsMisconception`
    are dropped, as for the head; with `labels` also an agent request or an element with an
    unknown label (the block of the element goes in the turn graph; `keep_elements` leaves such
    elements for the fix call that comes first, the graph drops what it cannot fix),
    unknown `cancel_requests`,
    unknown topics of the deadline coverage, and a deadline of an unknown hub. The plan keeps
    what is right: an unknown landmark of a step is nulled (the step stays), an unknown
    landmark of `add`/`remove` is dropped (an unknown `before` — the end), a step id that is not
    a slug is made one, `next_steps` is cut to `next_steps_max`."""
    update: dict[str, object] = {}
    if labels is not None:
        update.update(_drop_unresolved(markup, labels, keep_elements=keep_elements))
    base = update.get("plan_update", markup.plan_update)
    assert isinstance(base, PlanUpdate)
    plan = _settled_plan(base, labels, next_steps_max)
    if plan != markup.plan_update:
        update["plan_update"] = plan
    exposures = [e for e in markup.exposures if e.skill in skill_labels]
    if len(exposures) != len(markup.exposures):
        update["exposures"] = exposures
    if (
        markup.targets_misconception is not None
        and markup.targets_misconception not in misconception_labels
    ):
        update["targets_misconception"] = None
    return markup.model_copy(update=update) if update else markup


def _slug(step_id: str, text: str) -> str:
    """A step id that is not a slug, made one: the same input gives the same id."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", step_id.lower()).strip("-")
    return slug or re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-") or "step"


def _settled_step(step: Step, labels: LabelMap | None) -> Step:
    update: dict[str, object] = {}
    if not STEP_ID.fullmatch(step.id):
        update["id"] = _slug(step.id, step.text)
    if (
        labels is not None
        and step.landmark is not None
        and not _resolves(labels, landmark_kind(step.landmark), step.landmark)
    ):
        update["landmark"] = None
    return step.model_copy(update=update) if update else step


def _settled_edits(edits: LandmarkEdits, labels: LabelMap | None) -> LandmarkEdits:
    if labels is None:
        return edits

    def known(label: str) -> bool:
        return _resolves(labels, landmark_kind(label), label)

    add = [
        a.model_copy(update={"before": None}) if a.before is not None and not known(a.before) else a
        for a in edits.add
        if known(a.label)
    ]
    return edits.model_copy(update={"add": add, "remove": [r for r in edits.remove if known(r)]})


def _settled_plan(
    plan: PlanUpdate, labels: LabelMap | None, next_steps_max: int | None
) -> PlanUpdate:
    update: dict[str, object] = {}
    if plan.current_step is not None:
        update["current_step"] = _settled_step(plan.current_step, labels)
    if plan.next_steps is not None:
        steps = plan.next_steps if next_steps_max is None else plan.next_steps[:next_steps_max]
        update["next_steps"] = [_settled_step(s, labels) for s in steps]
    if plan.landmark_edits is not None:
        update["landmark_edits"] = _settled_edits(plan.landmark_edits, labels)
    return plan.model_copy(update=update)


def _drop_unresolved(
    markup: TurnMarkup, labels: LabelMap, *, keep_elements: bool
) -> dict[str, object]:
    update: dict[str, object] = {}
    requests = [
        r
        for r in markup.agent_requests
        if all(_resolves(labels, k, label) for k, label in _request_labels(r))
    ]
    if len(requests) != len(markup.agent_requests):
        update["agent_requests"] = requests
    plan = markup.plan_update
    cancel = [r for r in plan.cancel_requests if _resolves(labels, "request", r)]
    if len(cancel) != len(plan.cancel_requests):
        update["plan_update"] = plan.model_copy(update={"cancel_requests": cancel})
    if not keep_elements:
        elements = [e for e in markup.elements if not unresolved_element_labels(e, labels)]
        if len(elements) != len(markup.elements):
            update["elements"] = elements
    deadline = markup.deadline
    if deadline is not None:
        if deadline.hub_label is not None and not _resolves(labels, "hub", deadline.hub_label):
            update["deadline"] = None
        else:
            coverage = [t for t in deadline.coverage if _resolves(labels, "topic", t)]
            if len(coverage) != len(deadline.coverage):
                update["deadline"] = deadline.model_copy(update={"coverage": coverage})
    return update


def settle_stance(head: TurnHead, answered_verdict: Verdict | None) -> TurnHead:
    """`code` without a code verdict becomes `formative`, at once and without a retry; code
    never raises the stance."""
    if head.verdict_stance == "code" and not _code_verdict(answered_verdict):
        return head.model_copy(update={"verdict_stance": "formative"})
    return head


def _code_verdict(verdict: Verdict | None) -> bool:
    return verdict is not None and verdict.verification == "code"


def _unknown(
    reason: GuardReason, labels: list[str], known: Collection[str]
) -> list[GuardViolation]:
    missing = [label for label in labels if label not in known]
    if not missing:
        return []
    hint = "" if known else "; there are no such labels in this domain yet, leave the list empty"
    return [GuardViolation(reason, f"unknown labels: {', '.join(dict.fromkeys(missing))}{hint}")]
