"""Code validation of an `extractor@2` element before it is written (law 4; ТЗ Фазы 3
WP12.2 п.1–2, `Т-§7.6`). Seam S6: the `turn.extract` handler of stream B calls
`to_extracted_element` for every element and writes the result into `turn.extracted` v2."""

from __future__ import annotations

from collections.abc import Mapping
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
