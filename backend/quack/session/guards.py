"""Defensive rules of a teacher turn checked by code (Т-§12.4; ТЗ Ф4 WP13.5), pure part.

Head (`p1_head`, before any body character is shown): honest verdict — `verdict_stance =
code` only when the turn answers an answer with a code-class verdict; the turn is marked —
`targetSkills` is not empty (a domain with no skills yet has none to name, so it may be empty
there) and every label exists. Markup (`p3_markup`): exposures and
`targetsMisconception` reference existing labels. A violation returns the turn to the model
with its reasons (WP13.2 `guard`, up to `guard_max_retries`); what happens after the retries
is the caller's: `settle_stance` lowers `code` to `formative` [РЕШЕНИЕ Ф4 WP13.5], an
unmarked turn is shown and logged: `drop_unknown_head_labels` and `drop_unknown_markup_labels`
take the labels that do not exist out of what is committed, so the write of the turn does not
fail on them. Every label the write turns into an id is checked, not only skills and
misconceptions: those of agent requests, `cancel_requests`, the elements (`card_series`,
`visualization`, `material_link`) and the deadline (`LabelMap`, «short labels exist»,
Т-§12.4); an element with an unknown label leaves the markup, its block goes with it.
«Do not jump the boundary» (head): every prerequisite of a
target skill by `REQUIRES` with `confidence >= requires_min_confidence` has a showing or
evidence (the slice of seam S4 reads them with the session overlay) or is a target of this
turn too (D-B-153); after the retries the turn is shown and the case logged. Unsure links
are a warning in the context (WP14), not a rule.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from quack.graph.read.models import RequiresLink
from quack.llm.labels import LabelKind, LabelMap, UnknownLabelError
from quack.practice.check import Verdict
from quack.session.elements import (
    CardSeriesElement,
    Element,
    MaterialLinkElement,
    VisualizationElement,
)
from quack.session.teacher_output import AgentRequest, TurnHead, TurnMarkup

type GuardReason = Literal[
    "stance_without_code_verdict",
    "no_target_skills",
    "unknown_skill_label",
    "unknown_misconception_label",
    "unknown_label",
    "spoiler",
    "boundary_jump",
]


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


def _boundary_jumps(head: TurnHead, boundary: Boundary) -> list[GuardViolation]:
    """One violation naming, by label, each target whose sure prerequisites are neither shown
    nor evidenced nor taken by this turn; prerequisites without a label are counted."""
    labels = {skill_id: label for label, skill_id in boundary.skill_ids.items()}
    targets = {boundary.skill_ids[s] for s in head.target_skills if s in boundary.skill_ids}
    parts: list[str] = []
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
        named = [labels[m] for m in missing if m in labels]
        unnamed = len(missing) - len(named)
        text = ", ".join(named) + (f" and {unnamed} unlabeled" if unnamed else "")
        parts.append(f"{skill} needs {text.removeprefix(' and ')} first")
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
) -> list[GuardViolation]:
    """Violations of «the turn is marked» in `p3_markup`: unknown labels of exposures and of
    `targetsMisconception`; with the label table of the call (`labels`) also the labels of agent
    requests, `cancel_requests`, elements and the deadline — whatever the write resolves."""
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
    return violations


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
    return []


def _label_uses(markup: TurnMarkup) -> list[tuple[str, LabelKind, str]]:
    """`(where, kind, label)` of the labels of `p3_markup` that the write of the turn turns into
    ids (`turn_commit`), apart from exposures and `targetsMisconception`."""
    uses: list[tuple[str, LabelKind, str]] = []
    for i, request in enumerate(markup.agent_requests):
        uses.extend((f"agentRequests[{i}]", k, label) for k, label in _request_labels(request))
    uses.extend(
        ("planUpdate.cancel_requests", "request", r) for r in markup.plan_update.cancel_requests
    )
    for element in markup.elements:
        uses.extend((f"element {element.element_id}", k, v) for k, v in _element_labels(element))
    deadline = markup.deadline
    if deadline is not None:
        if deadline.hub_label is not None:
            uses.append(("deadline.hub_label", "hub", deadline.hub_label))
        uses.extend(("deadline.coverage", "topic", t) for t in deadline.coverage)
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
) -> TurnMarkup:
    """After the retries: exposures of unknown skills and an unknown `targetsMisconception`
    are dropped, as for the head; with `labels` also an agent request or an element with an
    unknown label (the block of the element goes in the turn graph), unknown `cancel_requests`,
    unknown topics of the deadline coverage, and a deadline of an unknown hub."""
    update: dict[str, object] = {}
    if labels is not None:
        update.update(_drop_unresolved(markup, labels))
    exposures = [e for e in markup.exposures if e.skill in skill_labels]
    if len(exposures) != len(markup.exposures):
        update["exposures"] = exposures
    if (
        markup.targets_misconception is not None
        and markup.targets_misconception not in misconception_labels
    ):
        update["targets_misconception"] = None
    return markup.model_copy(update=update) if update else markup


def _drop_unresolved(markup: TurnMarkup, labels: LabelMap) -> dict[str, object]:
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
    elements = [
        e
        for e in markup.elements
        if all(_resolves(labels, k, label) for k, label in _element_labels(e))
    ]
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
    """After the retries: `code` without a code verdict becomes `formative`; code never
    raises the stance."""
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
