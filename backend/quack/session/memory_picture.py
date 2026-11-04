"""Memory picture of the teacher context — `renderer@1` (Т-§10.2; ТЗ Ф4 WP14.2, slot 7).

Code renders facts of `quack.graph.read` (seam S4) as words: no numbers of strength, no weights,
no tiers (Т-§7.4); little data is named plainly ("1 time", "not checked"); an empty slot is
better than an irrelevant one. The slice is read with the watermark check and the session
overlay by the caller (Т-§2.6); "due for review" and "fresh in mind" are computed here with the
current time (Т-§8.6). English, like every slot (Т-§10.1, Т-§22.2). D-B-157.

Skills, in this order of importance, at most `memory_skills_max`: the skills of the current task,
the skills of the topics of the session goal, their prerequisites one or two steps away with a
target level not proven or implied, skills of other domains with a proven level.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from quack.config import Settings
from quack.events.values import GapType, Level
from quack.graph.read import (
    EvidenceFact,
    LevelFact,
    MemorySlice,
    MisconceptionFact,
    SkillFact,
    SliceFocus,
)
from quack.llm.labels import LabelMap

RENDERER_VERSION = "renderer@1"

# What slot 7 says when the slice could not be read (`teacher@2`, WP6.1 degraded read).
MEMORY_UNAVAILABLE = "memory temporarily unavailable"

_DAY_MS = 86_400_000
_MINUTE_MS = 60_000

# The level names of the doctrine (`teacher@2`, step 1; П-6.1).
LEVEL_NAMES: dict[Level, str] = {
    1: "Recognizes",
    2: "Recalls",
    3: "Applies",
    4: "Knows when to apply",
    5: "Tells apart",
    6: "Composes and explains",
}

_STATUS_WORDS = {
    "proven": "proven",
    "unstable": "not always",
    "failed": "failed",
}

_OUTCOME_WORDS = {
    "correct": "right",
    "incorrect": "wrong",
    "partial": "partly right",
    "unclear": "unclear",
}

# Т-§8.7: the source in words, never a tier.
_SOURCE_WORDS = {
    "code": "checked by code",
    "model": "judged by a model",
    "conversation": "from the conversation",
}

_EXPOSURE_WORDS = {
    "explained": "explanation",
    "example": "worked example",
    "method_named": "method named",
    "solution_shown": "solution shown",
}

# The gap types of the doctrine (`teacher@2`, step 3; П-6.4).
_GAP_WORDS: dict[GapType, str] = {
    "no_idea": "no idea",
    "when_to_apply": "doesn't recognize when to apply",
    "confusion": "mixes it up with a similar idea",
    "execution": "execution error",
    "slip": "slip",
    "misconception": "misconception",
}

_MISCONCEPTION_WORDS = {
    "suspected": "Suspected",
    "confirmed": "Confirmed",
    "in_work": "In work",
    "watching": "Watching",
}


@dataclass(frozen=True)
class RevisedCheck:
    """A revised verdict the teacher has not corrected yet (Т-§5.6, Т-§6.3): the number of the
    answer (`correctionsAcknowledged`) and what changed, in words."""

    answer_seq: int
    text: str


@dataclass(frozen=True)
class ReviewTask:
    """A bank task of a family the learner has not met on the skill (WP12.7, WP17.6)."""

    task_id: UUID
    text: str


@dataclass(frozen=True)
class PictureInputs:
    """What slot 7 is rendered from. `target_levels` — the target set of each skill (Т-§8.5);
    a skill without one shows the levels it has data on. `evidence` — the deciding evidence by
    id. `review` — the skills to repeat now by priority (`review_candidates`, WP17.6), each with
    its bank tasks in `review_tasks`."""

    slice: MemorySlice
    focus: SliceFocus
    skill_names: Mapping[UUID, str]
    target_levels: Mapping[UUID, Sequence[Level]] = field(default_factory=dict)
    evidence: Mapping[UUID, EvidenceFact] = field(default_factory=dict)
    analyst_summary: str | None = None
    agent_results: Sequence[str] = ()
    revised_checks: Sequence[RevisedCheck] = ()
    review: Sequence[UUID] = ()
    review_tasks: Mapping[UUID, Sequence[ReviewTask]] = field(default_factory=dict)


def _reached(level: LevelFact) -> bool:
    return level.status in ("proven", "implied")


def _targets(skill: SkillFact, inputs: PictureInputs) -> list[LevelFact]:
    by_level = {level.level: level for level in skill.levels}
    wanted = inputs.target_levels.get(skill.skill_id)
    if wanted is None:
        return sorted(
            (level for level in skill.levels if level.status != "not_tested"),
            key=lambda level: level.level,
        )
    return [
        by_level.get(level, LevelFact(level=level, status="not_tested")) for level in sorted(wanted)
    ]


def _below_target(skill: SkillFact, inputs: PictureInputs) -> bool:
    """A prerequisite enters if a level of its target set is not reached, or nothing is known."""
    levels = _targets(skill, inputs)
    if not levels:
        return True
    return any(not _reached(level) for level in levels)


def picture_skills(inputs: PictureInputs, settings: Settings) -> list[SkillFact]:
    """The skills of the picture in the order of importance, cut at `memory_skills_max`."""
    by_id = {skill.skill_id: skill for skill in inputs.slice.skills}
    chosen: dict[UUID, SkillFact] = {}

    def take(skill: SkillFact | None) -> None:
        if skill is not None and skill.skill_id in inputs.skill_names:
            chosen.setdefault(skill.skill_id, skill)

    for skill_id in inputs.focus.skill_ids:
        take(by_id.get(skill_id))
    topics = set(inputs.focus.topic_ids)
    for skill in inputs.slice.skills:
        if skill.topic_id is not None and skill.topic_id in topics:
            take(skill)
    goal = list(chosen.values())
    for skill_id in inputs.review:  # to repeat now, by priority (WP17.6)
        take(by_id.get(skill_id))
    step_one = [by_id.get(link.skill_id) for skill in goal for link in skill.requires]
    step_two = [by_id.get(link.skill_id) for skill in step_one if skill for link in skill.requires]
    for skill in [*step_one, *step_two]:
        if skill is not None and _below_target(skill, inputs):
            take(skill)
    domain = inputs.focus.domain_id
    for skill in inputs.slice.skills:
        other = skill.domain_id is not None and domain is not None and skill.domain_id != domain
        if other and any(_reached(level) for level in skill.levels):
            take(skill)
    return list(chosen.values())[: settings.memory_skills_max]


def _when(at_ms: int, now_ms: int) -> str:
    days = max(0, (now_ms - at_ms) // _DAY_MS)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _times(count: int) -> str:
    return "1 time" if count == 1 else f"{count} times in all"


def _name(skill_id: UUID, inputs: PictureInputs, labels: LabelMap) -> str:
    name = inputs.skill_names.get(skill_id, "an unnamed skill")
    label = labels.label("skill", skill_id)
    return f"{name} ({label})" if label else name


def _level_part(level: LevelFact, inputs: PictureInputs, now_ms: int) -> str:
    name = LEVEL_NAMES[level.level]
    if level.status == "not_tested":
        return f"{name} — not checked."
    if level.status == "implied":
        return f"{name} — follows from a proven level, not checked on its own."
    words = _STATUS_WORDS[level.status]
    if level.basis == "transfer":
        words += " through another domain"
    facts: list[str] = []
    deciding = (
        inputs.evidence.get(level.deciding_evidence_id) if level.deciding_evidence_id else None
    )
    if deciding is not None:
        fact = (
            f"{_OUTCOME_WORDS[deciding.outcome]} {_when(deciding.occurred_at_ms, now_ms)}, "
            f"{_SOURCE_WORDS[deciding.source]}"
        )
        if deciding.quote:
            fact += f': "{deciding.quote}"'
        facts.append(fact + " (deciding)")
    if level.evidence_count:
        facts.append(_times(level.evidence_count))
    return f"{name} — {words}" + (": " + ", ".join(facts) if facts else "") + "."


def _misconception_part(item: MisconceptionFact, inputs: PictureInputs, labels: LabelMap) -> str:
    label = labels.label("misconception", item.misconception_id)
    head = _MISCONCEPTION_WORDS[item.status] + (f" ({label})" if label else "")
    text = f"{head}: {item.statement}"
    if item.triggers:
        text += "; shows when " + "; ".join(item.triggers)
    if item.confused_with_skill_id is not None:
        text += f"; mixes it up with {_name(item.confused_with_skill_id, inputs, labels)}"
    count = item.manifestation_count
    if count:
        text += f" — {count} manifestation" + ("" if count == 1 else "s")
    quotes = [f'"{quote}"' for quote in item.quotes[:2]]
    if quotes:
        text += ": " + ", ".join(quotes)
    return text + "."


def _retention(
    skill: SkillFact, settings: Settings, now_ms: int, *, to_review: bool = False
) -> str | None:
    notes: list[str] = []
    last_shown = max((e.occurred_at_ms for e in skill.exposures), default=None)
    if last_shown is not None and now_ms - last_shown <= settings.hot_window_min * _MINUTE_MS:
        notes.append("shown minutes ago, still fresh in mind")
    if skill.last_lapse_at_ms is not None:
        notes.append(f"forgot after a delay {_when(skill.last_lapse_at_ms, now_ms)}")
    if skill.due_at_ms is None:
        notes.append("no checks after a delay yet")
    if to_review or (skill.due_at_ms is not None and skill.due_at_ms <= now_ms):
        notes.append("due for review")
    return "Retention: " + "; ".join(notes) + "." if notes else None


def _skill_line(
    skill: SkillFact,
    inputs: PictureInputs,
    labels: LabelMap,
    settings: Settings,
    now_ms: int,
) -> str:
    parts = [_name(skill.skill_id, inputs, labels) + "."]
    levels = _targets(skill, inputs)
    parts.extend(_level_part(level, inputs, now_ms) for level in levels)
    if not levels:
        parts.append("Not checked yet.")
    parts.extend(
        _misconception_part(item, inputs, labels)
        for item in inputs.slice.misconceptions
        if item.skill_id == skill.skill_id
    )
    shown = list(dict.fromkeys(_EXPOSURE_WORDS.get(e.kind, e.kind) for e in skill.exposures))
    if shown:
        parts.append("Shown: " + ", ".join(shown) + ".")
    gap = skill.gap_type
    confidence = skill.gap_type_confidence
    if (
        gap is not None
        and gap != "none"
        and confidence is not None
        and confidence >= settings.gap_type_min_confidence
    ):
        parts.append(f"Gap: {_GAP_WORDS[gap]}.")
    if skill.consolidation_flag:
        parts.append("Needs consolidation after a shown solution.")
    retention = _retention(skill, settings, now_ms, to_review=skill.skill_id in inputs.review)
    if retention:
        parts.append(retention)
    for task in inputs.review_tasks.get(skill.skill_id, ()):
        label = labels.label("task", task.task_id)
        if label:
            parts.append(f'Repeat with a new family, bank task {label}: "{task.text}".')
    return " ".join(parts)


def render_memory_picture(
    inputs: PictureInputs, labels: LabelMap, settings: Settings, now_ms: int
) -> str:
    """Slot 7 as text; `""` when nothing relevant is known."""
    skills = picture_skills(inputs, settings)
    lines = [_skill_line(skill, inputs, labels, settings, now_ms) for skill in skills]
    for root in inputs.slice.root_causes:
        if root.skill_id in inputs.skill_names:
            count = root.hypothesis_count
            lines.append(
                f"Possible root of errors: {_name(root.skill_id, inputs, labels)} — "
                f"{count} error hypothes{'is points' if count == 1 else 'es point'} to it."
            )
    if inputs.analyst_summary:
        lines.append(f"Analyst summary: {inputs.analyst_summary}")
    lines.extend(f"Ready from agents: {text}" for text in inputs.agent_results)
    lines.extend(
        f"Revised check of answer {item.answer_seq}: {item.text}" for item in inputs.revised_checks
    )
    return "\n".join(lines)
