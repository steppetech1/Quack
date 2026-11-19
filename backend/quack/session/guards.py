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
fail on them. «Do not jump the boundary» (head): every prerequisite of a
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
from quack.practice.check import Verdict
from quack.session.teacher_output import TurnHead, TurnMarkup

type GuardReason = Literal[
    "stance_without_code_verdict",
    "no_target_skills",
    "unknown_skill_label",
    "unknown_misconception_label",
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
    markup: TurnMarkup, *, skill_labels: Collection[str], misconception_labels: Collection[str]
) -> list[GuardViolation]:
    """Violations of «the turn is marked» in `p3_markup`: unknown labels of exposures and of
    `targetsMisconception`."""
    violations = _unknown("unknown_skill_label", [e.skill for e in markup.exposures], skill_labels)
    if markup.targets_misconception is not None:
        violations.extend(
            _unknown(
                "unknown_misconception_label",
                [markup.targets_misconception],
                misconception_labels,
            )
        )
    return violations


def drop_unknown_head_labels(head: TurnHead, skill_labels: Collection[str]) -> TurnHead:
    """After the retries: target skills that are not in the label table are dropped (the turn
    stays shown and logged, but unmarked by them), so `turn_batch` has nothing to reject."""
    known = [s for s in head.target_skills if s in skill_labels]
    if len(known) == len(head.target_skills):
        return head
    return head.model_copy(update={"target_skills": known})


def drop_unknown_markup_labels(
    markup: TurnMarkup, *, skill_labels: Collection[str], misconception_labels: Collection[str]
) -> TurnMarkup:
    """After the retries: exposures of unknown skills and an unknown `targetsMisconception`
    are dropped, as for the head."""
    update: dict[str, object] = {}
    exposures = [e for e in markup.exposures if e.skill in skill_labels]
    if len(exposures) != len(markup.exposures):
        update["exposures"] = exposures
    if (
        markup.targets_misconception is not None
        and markup.targets_misconception not in misconception_labels
    ):
        update["targets_misconception"] = None
    return markup.model_copy(update=update) if update else markup


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
