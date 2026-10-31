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
- the `new: ` prefix of a new skill or misconception is dropped: the element carries the
  description itself (WP12.1).

`turn.extracted` v2 (WP12, seams S5/S6): every element goes through
`quack.graph.evidence_validation.to_extracted_element`; rejected elements are written too.
The episode that may be cited holds only the learner's events (rule 9 of the prompt: the
teacher's words are never a quote of the learner).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from quack.events.models import StoredEvent
from quack.events.payloads import ExtractedElement, TurnExtractedV2
from quack.graph.evidence_validation import ExtractionContext, SourceEvent, to_extracted_element
from quack.llm.labels import LabelMap
from quack.logging import get_logger
from quack.session.schemas import ExtractorEntry, ExtractorOutputV2

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


# The model names a new skill or misconception as "new: <description>" (prompt contract).
NEW_PREFIX = "new:"
# Learner events whose text may be cited, and the payload field holding the text.
LEARNER_TEXT_FIELDS: dict[str, str] = {"turn.student_replied": "text", "task.submitted": "answer"}


def _description(label: str) -> str:
    text = label.strip()
    return text[len(NEW_PREFIX) :].strip() if text.lower().startswith(NEW_PREFIX) else text


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
            skill_label=_description(entry.skill),
            misconception_label=_description(entry.misconception),
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
        skill_label=_description(entry.skill),
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


def learner_episode(events: Sequence[StoredEvent]) -> dict[int, SourceEvent]:
    """The events of the episode that may be cited: the learner's replies and answers."""
    episode: dict[int, SourceEvent] = {}
    for event in events:
        field = LEARNER_TEXT_FIELDS.get(event.type)
        if field is not None:
            text = str(event.payload.get(field) or "")
            episode[event.seq] = SourceEvent(event.seq, text, event.occurred_at)
    return episode


def extraction_context(
    labels: LabelMap,
    episode_events: Sequence[StoredEvent],
    *,
    topic_id: UUID | None,
    task_levels: Mapping[int, int] | None = None,
) -> ExtractionContext:
    """`ExtractionContext` of one `extractor@2` call (seam S6): the label tables of the call,
    the learner events of the episode, task level caps by seq, the topic for a new skill."""
    return ExtractionContext(
        skill_labels=labels.labels("skill"),
        misconception_labels=labels.labels("misconception"),
        episode=learner_episode(episode_events),
        task_levels=dict(task_levels or {}),
        new_skill_topic_id=topic_id,
    )


def extracted_elements(output: ExtractorOutputV2, ctx: ExtractionContext) -> list[ExtractedElement]:
    """Every entry the code could map, checked by code; rejected ones keep their reason."""
    return [to_extracted_element(item, ctx) for item in to_extracted_items(output.entries)]


def turn_extracted_v2(
    session_id: UUID, turn_id: UUID, elements: list[ExtractedElement]
) -> TurnExtractedV2:
    """Payload of `turn.extracted` with `schema_version = 2`."""
    return TurnExtractedV2(session_id=session_id, turn_id=turn_id, elements=elements)


# --- input blocks of extractor@2 (D-B-022) ------------------------------------------------

# Events of an episode shown to the extractor, as numbered lines (Т-§7.6, Т-§10: built by code
# from the events, in English).
EPISODE_EVENT_TYPES: tuple[str, ...] = (
    "turn.student_replied",
    "task.submitted",
    "task.hint_given",
    "task.evaluated",
    "turn.teacher_replied",
    "task.assigned",
)
NONE_LINE = "(none)"


def _event_line(event: StoredEvent, task_lines: Mapping[str, str], labels: LabelMap) -> str | None:
    p = event.payload
    head = f"[{event.seq}]"
    if event.type == "turn.student_replied":
        return f"{head} learner: {p.get('text', '')}"
    if event.type == "task.submitted":
        return f"{head} learner's answer to the problem: {p.get('answer', '')}"
    if event.type == "turn.teacher_replied":
        return f"{head} teacher: {p.get('text', '')}"
    if event.type == "task.assigned":
        return f"{head} problem given: {task_lines.get(str(p.get('task_id')), '')}".rstrip()
    if event.type == "task.evaluated":
        verdict = "correct" if p.get("is_correct") else "incorrect"
        return f"{head} automatic check of the learner's answer: {verdict}"
    if event.type == "task.hint_given":
        return f"{head} hint, step {p.get('hint_level')} of 3: {p.get('hint_text', '')}"
    return None


def task_line(prompt_text: str, skill_ids: Sequence[str], labels: LabelMap) -> str:
    """`<statement> (tests: S1, S2)` — what a problem tests, by the labels of the call."""
    tested = [label for sid in skill_ids if (label := labels.label("skill", UUID(sid))) is not None]
    return prompt_text + (f" (tests: {', '.join(tested)})" if tested else "")


def render_events(
    events: Sequence[StoredEvent], task_lines: Mapping[str, str], labels: LabelMap
) -> str:
    """Numbered lines of `events` (`[seq] who: text`); `(none)` when there are none."""
    lines = [
        line for event in events if (line := _event_line(event, task_lines, labels)) is not None
    ]
    return "\n".join(lines) or NONE_LINE


def render_candidate_skills(skills: Sequence[Mapping[str, Any]], labels: LabelMap) -> str:
    """`S1: <name>` per candidate skill that got a label."""
    lines = [
        f"{label}: {skill.get('name', '')}"
        for skill in skills
        if (label := labels.label("skill", UUID(str(skill["skill_id"])))) is not None
    ]
    return "\n".join(lines) or NONE_LINE


def render_known_misconceptions(
    misconceptions: Sequence[Mapping[str, Any]], labels: LabelMap
) -> str:
    """`M1 (skill S1): <name>` per known misconception of the candidate skills."""
    lines: list[str] = []
    for item in misconceptions:
        label = labels.label("misconception", UUID(str(item["misconception_id"])))
        skill = labels.label("skill", UUID(str(item["skill_id"])))
        if label is None or skill is None:
            continue
        lines.append(f"{label} (skill {skill}): {item.get('name', '')}")
    return "\n".join(lines) or NONE_LINE
