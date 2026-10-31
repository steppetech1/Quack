"""Code validation of an `extractor@2` element before it is written (law 4; ТЗ Фазы 3
WP12.2 п.1–2, `Т-§7.6`). Seam S6: the `turn.extract` handler of stream B calls
`to_extracted_element` for every element and writes the result into `turn.extracted` v2."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from quack.events.payloads import ExtractedElement

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
    def gap_type(self) -> str | None: ...
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
    raise NotImplementedError
