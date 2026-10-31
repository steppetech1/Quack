"""Entries of `extractor@2` → elements of WP12.1 (D-B-011).

The model answers in the contract of its prompt; the event `turn.extracted` v2 carries the
closed enumerations of WP12.1. The mapping is a pure function: no numbers are added here,
tier and weight are assigned later by code (WP12.2).

- `success` / `failure` → `correct` / `incorrect`; `bypass` (the full solution was shown) →
  `partial` with `assistance = shown_solution`, which gives no weight of understanding
  (WP12.2 п.4) and is not a failure (rule 4 of the prompt).
- `hint_direction` / `hint_key_step` / `hint_near_solution` → `hint` with `hint_step` 1–3.
- `after_example` → `none`: the hot window after an explanation is detected by code from the
  events (WP12.2 п.4), not taken from the model.
- gap codes are renamed one to one; a positive element gets `none`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from quack.logging import get_logger
from quack.session.schemas import ExtractorEntry

log = get_logger(__name__)

type Outcome = Literal["correct", "incorrect", "partial"]
type Assistance = Literal["none", "hint", "shown_solution"]
type GapType = Literal[
    "none", "no_idea", "when_to_apply", "confusion", "execution", "slip", "misconception"
]

OUTCOMES: dict[str, Outcome] = {"success": "correct", "failure": "incorrect", "bypass": "partial"}
ASSISTANCE: dict[str, tuple[Assistance, int | None]] = {
    "none": ("none", None),
    "after_example": ("none", None),
    "hint_direction": ("hint", 1),
    "hint_key_step": ("hint", 2),
    "hint_near_solution": ("hint", 3),
    "solution_shown": ("shown_solution", None),
}
GAP_TYPES: dict[str, GapType] = {
    "no_idea": "no_idea",
    "not_recognizing_when": "when_to_apply",
    "confuses_similar": "confusion",
    "execution_error": "execution",
    "slip": "slip",
    "misconception": "misconception",
}


class ExtractedItem(BaseModel):
    """Element of WP12.1. `misconception_label` (a label `M…` or a new belief) is set only
    for `misconception_hypothesis`; observation fields are null for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["evidence", "misconception_hypothesis"]
    skill_label: str
    misconception_label: str | None = None
    level: int | None = None
    outcome: Outcome | None = None
    assistance: Assistance | None = None
    hint_step: int | None = None
    gap_type: GapType | None = None
    quote: str
    source_seqs: list[int]


def to_extracted_item(entry: ExtractorEntry) -> ExtractedItem | None:
    """The WP12.1 element of one entry; `None` (logged) for an entry that lacks the fields
    of its kind."""
    if entry.kind == "misconception_suspicion":
        if entry.misconception is None:
            log.info("extractor_entry_dropped", reason="no_misconception")
            return None
        return ExtractedItem(
            kind="misconception_hypothesis",
            skill_label=entry.skill,
            misconception_label=entry.misconception,
            quote=entry.quote,
            source_seqs=entry.events,
        )
    if entry.level is None or entry.outcome is None or entry.help is None:
        log.info("extractor_entry_dropped", reason="incomplete_observation")
        return None
    assistance, hint_step = ASSISTANCE[entry.help]
    if entry.outcome == "bypass":
        assistance, hint_step = "shown_solution", None
    gap: GapType = "none"
    if entry.outcome == "failure" and entry.gap_type is not None:
        gap = GAP_TYPES[entry.gap_type]
    return ExtractedItem(
        kind="evidence",
        skill_label=entry.skill,
        level=entry.level,
        outcome=OUTCOMES[entry.outcome],
        assistance=assistance,
        hint_step=hint_step,
        gap_type=gap,
        quote=entry.quote,
        source_seqs=entry.events,
    )


def to_extracted_items(entries: list[ExtractorEntry]) -> list[ExtractedItem]:
    return [item for entry in entries if (item := to_extracted_item(entry)) is not None]
