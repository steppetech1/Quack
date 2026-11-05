"""Code validation of an `extractor@2` element before it is written (law 4; ТЗ Фазы 3
WP12.2 п.1–2, `Т-§7.6`). Seam S6: the `turn.extract` handler of stream B calls
`to_extracted_element` for every element and writes the result into `turn.extracted` v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from quack.events.payloads import (
    ElementRejection,
    ExtractedElement,
    GapType,
    NewSkillRef,
    SkillRef,
)
from quack.events.records import (
    ExtractedElementRecord,
    RejectedElementRecord,
    RootCauseRecord,
)
from quack.events.values import GapType as P4GapType
from quack.events.values import NewSkill

# The extractor's word for "skill not clear" (`Т-§7.6`): no evidence.
UNCLEAR_SKILL = "unclear"


class ExtractedItemLike(Protocol):
    """The WP12.1 element produced by stream B from the model output
    (`quack.session.extraction.ExtractedItem`)."""

    @property
    def kind(self) -> Literal["evidence", "misconception_hypothesis"]: ...
    @property
    def skill_label(self) -> str: ...
    @property
    def misconception_label(self) -> str | None: ...
    @property
    def level(self) -> int | None: ...
    @property
    def outcome(self) -> Literal["correct", "incorrect", "partial"] | None: ...
    @property
    def assistance(self) -> Literal["none", "hint", "shown_solution"] | None: ...
    @property
    def hint_step(self) -> int | None: ...
    @property
    def gap_type(self) -> GapType | None: ...
    @property
    def quote(self) -> str: ...
    @property
    def source_seqs(self) -> list[int]: ...


@dataclass(frozen=True)
class SourceEvent:
    """An event of the extraction window: its text and when it happened."""

    seq: int
    text: str
    occurred_at: datetime


@dataclass(frozen=True)
class ExtractionContext:
    """What code knows when it checks the elements of one `turn.extracted`.

    - `skill_labels` / `misconception_labels`: the label table of the call (WP14), label → id;
    - `episode`: the events that may be cited (the episode itself, `Т-§7.5`), by seq;
    - `task_levels`: seq of a task event → the highest level of its markup;
    - `new_skill_topic_id`: topic for a new skill described by the extractor (`Т-§4.4`);
      `None` — such elements are not applied."""

    skill_labels: Mapping[str, UUID]
    episode: Mapping[int, SourceEvent]
    misconception_labels: Mapping[str, UUID] = field(default_factory=dict)
    task_levels: Mapping[int, int] = field(default_factory=dict)
    new_skill_topic_id: UUID | None = None


def to_extracted_element(item: ExtractedItemLike, ctx: ExtractionContext) -> ExtractedElement:
    """The element as written into `turn.extracted` v2: ids resolved from labels, `occurred_at`
    from the latest source event, and `rejected_reason` when a check of `Т-§7.6` fails
    (the element is then kept in the observation but not applied)."""
    skill, skill_reason = _skill_ref(item.skill_label, ctx)
    misconception_id = None
    if item.misconception_label is not None:
        misconception_id = ctx.misconception_labels.get(item.misconception_label)
    sources = [ctx.episode[seq] for seq in item.source_seqs if seq in ctx.episode]
    occurred_at = max((e.occurred_at for e in sources), default=None)
    element = ExtractedElement(
        kind=item.kind,
        skill_label=item.skill_label,
        skill=skill,
        misconception_label=item.misconception_label,
        misconception_id=misconception_id,
        level=item.level,
        outcome=item.outcome,
        assistance=item.assistance,
        hint_step=item.hint_step,
        gap_type=item.gap_type,
        quote=item.quote,
        source_seqs=list(item.source_seqs),
        occurred_at=occurred_at,
    )
    reason = skill_reason or _check(item, ctx, misconception_id)
    return element.model_copy(update={"rejected_reason": reason}) if reason else element


def _looks_like_label(text: str, prefix: str) -> bool:
    return len(text) >= 2 and text[0] == prefix and text[1:].isdigit()


def _skill_ref(
    label: str, ctx: ExtractionContext
) -> tuple[SkillRef | None, ElementRejection | None]:
    """A label `S…` → its skill; a description → a new skill of `new_skill_topic_id`
    (`Т-§4.4`); `unclear` → no evidence."""
    if label.strip().lower() == UNCLEAR_SKILL:
        return None, "unclear_skill"
    if label in ctx.skill_labels:
        return SkillRef(skill_id=ctx.skill_labels[label]), None
    if _looks_like_label(label, "S") or ctx.new_skill_topic_id is None or not label.strip():
        return None, "unknown_label"
    return SkillRef(
        new_skill=NewSkillRef(name=label.strip(), topic_id=ctx.new_skill_topic_id)
    ), None


def _check(
    item: ExtractedItemLike, ctx: ExtractionContext, misconception_id: UUID | None
) -> ElementRejection | None:
    """The checks of `Т-§7.6`, in the order of the contract."""
    if item.kind == "misconception_hypothesis":
        label = item.misconception_label
        if label is None or not label.strip():
            return "incomplete_element"
        if misconception_id is None and _looks_like_label(label, "M"):
            return "unknown_label"
    elif item.level is None or item.outcome is None or item.assistance is None:
        return "incomplete_element"
    if not item.source_seqs or any(seq not in ctx.episode for seq in item.source_seqs):
        return "source_outside_episode"
    negative = item.kind == "misconception_hypothesis" or item.outcome == "incorrect"
    if not item.quote.strip():
        return "missing_quote" if negative else None
    if not any(item.quote in ctx.episode[seq].text for seq in item.source_seqs):
        return "quote_not_in_source"
    caps = [ctx.task_levels[seq] for seq in item.source_seqs if seq in ctx.task_levels]
    if item.level is not None and caps and item.level > min(caps):
        return "level_above_task"
    return None


# --- Phase 4: `extractor@3` (ТЗ Ф4 WP15 п.4, п.8; `Т-§7.6`) --------------------------------
#
# The extractor output of stream B (`quack.agents.schemas.ExtractedElement`) is checked here
# before `observation.extracted` is written: labels become ids (`ExtractedElementRecord`),
# failed elements stay in the observation with the reason (`RejectedElementRecord`). The core
# cannot import the product model (§4.1), so the input is a protocol of the same fields.

type RejectionReason = Literal[
    "unknown_label",
    "incomplete_element",
    "source_outside_episode",
    "missing_quote",
    "quote_not_in_source",
    "level_above_task",
    "code_verdict_exists",
]


class RootCauseLike(Protocol):
    @property
    def skill(self) -> str: ...
    @property
    def confidence(self) -> Literal["low", "medium", "high"]: ...


class ExtractorElementLike(Protocol):
    """One element of `extractor@3` (`Т-§7.6`, WP15 п.3) with short labels."""

    @property
    def kind(self) -> Literal["evidence", "misconception_hypothesis"]: ...
    @property
    def skill(self) -> str | None: ...
    @property
    def new_skill(self) -> NewSkill | None: ...
    @property
    def skill_unclear(self) -> bool: ...
    @property
    def level(self) -> int | None: ...
    @property
    def outcome(self) -> Literal["correct", "incorrect", "partial"] | None: ...
    @property
    def help(self) -> Literal["none", "hint", "shown_solution"]: ...
    @property
    def hint_step(self) -> int | None: ...
    @property
    def gap_type(self) -> P4GapType: ...
    @property
    def misconception(self) -> str | None: ...
    @property
    def new_misconception(self) -> str | None: ...
    @property
    def root_cause(self) -> RootCauseLike | None: ...
    @property
    def quote(self) -> str: ...
    @property
    def events(self) -> Sequence[int]: ...


@dataclass(frozen=True)
class EpisodeSource:
    """An event of the episode that an element may cite: its text and time (`Т-§2.1`)."""

    seq: int
    text: str
    at_ms: int


@dataclass(frozen=True)
class EpisodeCheckContext:
    """What code knows when it checks the elements of one `observation.extracted`.

    - `skill_labels` / `misconception_labels`: label → id of the call (`LabelMap.labels`);
    - `episode`: the events of the episode by seq — the context turns before it are not
      citable (`Т-§7.5`: only the episode is interpreted);
    - `task_levels`: seq of an event of a task → the highest level of the task markup;
    - `code_verdicts`: `(skill_id, level)` with a verdict of class code in the episode
      (WP15 п.8)."""

    skill_labels: Mapping[str, UUID]
    misconception_labels: Mapping[str, UUID]
    episode: Mapping[int, EpisodeSource]
    task_levels: Mapping[int, int] = field(default_factory=dict)
    code_verdicts: frozenset[tuple[UUID, int]] = frozenset()


@dataclass(frozen=True)
class CheckedElements:
    """`elements` and `rejected` of `observation.extracted`; `rejected[i].index` — the position
    of the element in the extractor output."""

    elements: list[ExtractedElementRecord]
    rejected: list[RejectedElementRecord]


def check_extracted(
    elements: Sequence[ExtractorElementLike], ctx: EpisodeCheckContext
) -> CheckedElements:
    """The checks of `Т-§7.6` and WP15 п.4, п.8 for every element, in output order."""
    accepted: list[ExtractedElementRecord] = []
    rejected: list[RejectedElementRecord] = []
    for index, element in enumerate(elements):
        checked = _check_element(element, ctx)
        if isinstance(checked, ExtractedElementRecord):
            accepted.append(checked)
        else:
            rejected.append(RejectedElementRecord(index=index, reason=checked, quote=element.quote))
    return CheckedElements(accepted, rejected)


def _check_element(
    element: ExtractorElementLike, ctx: EpisodeCheckContext
) -> ExtractedElementRecord | RejectionReason:
    """The element with ids, or the reason it is rejected — checks in the order of `Т-§7.6`."""
    skill_id = misconception_id = root_cause = None
    if element.skill is not None:
        skill_id = ctx.skill_labels.get(element.skill)
        if skill_id is None:
            return "unknown_label"
    if element.misconception is not None:
        misconception_id = ctx.misconception_labels.get(element.misconception)
        if misconception_id is None:
            return "unknown_label"
    if element.root_cause is not None:
        root_skill_id = ctx.skill_labels.get(element.root_cause.skill)
        if root_skill_id is None:
            return "unknown_label"
        root_cause = RootCauseRecord(
            skill_id=root_skill_id, confidence=element.root_cause.confidence
        )
    if not _complete(element):
        return "incomplete_element"
    if not element.events or any(seq not in ctx.episode for seq in element.events):
        return "source_outside_episode"
    quote = element.quote.strip()
    # A negative element, a misconception hypothesis (`Т-§7.6`) and a new skill (`Т-§4.4`)
    # stand only on a quote.
    needs_quote = (
        element.kind == "misconception_hypothesis"
        or element.outcome == "incorrect"
        or element.new_skill is not None
    )
    if not quote:
        if needs_quote:
            return "missing_quote"
    elif not any(quote in ctx.episode[seq].text for seq in element.events):
        return "quote_not_in_source"
    caps = [ctx.task_levels[seq] for seq in element.events if seq in ctx.task_levels]
    if element.level is not None and caps and element.level > min(caps):
        return "level_above_task"
    if (
        element.kind == "evidence"
        and skill_id is not None
        and element.level is not None
        and (skill_id, element.level) in ctx.code_verdicts
    ):
        return "code_verdict_exists"
    return ExtractedElementRecord(
        kind=element.kind,
        skill_id=skill_id,
        new_skill=element.new_skill,
        skill_unclear=element.skill_unclear,
        level=element.level,
        outcome=element.outcome,
        help=element.help,
        hint_step=element.hint_step,
        gap_type=element.gap_type,
        misconception_id=misconception_id,
        new_misconception=element.new_misconception,
        root_cause=root_cause,
        quote=element.quote,
        events=list(element.events),
        at_ms=max(ctx.episode[seq].at_ms for seq in element.events),
    )


def _complete(element: ExtractorElementLike) -> bool:
    """Evidence names exactly one of: a skill label, a new skill, «unclear» (`Т-§7.6`), and has
    a level and an outcome; a hypothesis names a known misconception or a new wording. Help and
    gap type agree (WP15 п.3, `Т-§7.6`): only a hint has a step, a positive element has `none`."""
    if (element.help == "hint") != (element.hint_step is not None):
        return False
    if element.outcome == "correct" and element.gap_type != "none":
        return False
    if element.kind == "misconception_hypothesis":
        return element.misconception is not None or bool((element.new_misconception or "").strip())
    named = [element.skill is not None, element.new_skill is not None, element.skill_unclear]
    return named.count(True) == 1 and element.level is not None and element.outcome is not None
