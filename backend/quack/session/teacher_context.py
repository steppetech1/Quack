"""Slots of the teacher context (Т-§10.1; ТЗ Ф4 WP14.1): the variables of `teacher@2`.

Nine slots in the fixed order of Т-§10.1, English (Т-§22.2): 1 — doctrine (the prompt text);
2 — tools, elements, output schema; 3 — profile; 4 — domain and goals; 5 — conversation
(`quack.session.conversation`); 6 — goal and plan of the session; 7 — memory picture
(`quack.session.memory_picture`); 8 — material chunks; 9 — label table. Then the tail.

Slots 3 and 4 come from the snapshot at the session start: a change in the middle of the session
is a line of the tail until the session ends and moves into its slot with the next session, so
the prefix cache is not reset (Т-§10.1). A learner skill called with `/name` puts its text into
the tail of this turn only (Т-§17.8). Material text is data: a quote with its source (Т-§19.8).
D-B-159.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quack.events.records import PlanUpdateRecord
from quack.events.values import Level
from quack.llm.labels import LABEL_PREFIX, LabelMap
from quack.session.memory_picture import LEVEL_NAMES
from quack.session.tools import TOOLS, MaterialChunk, render_chunks

# The slot variables of `teacher@2` in the order of Т-§10.1 (slot 1 is the prompt text), then
# the tail and the guard feedback.
SLOT_VARIABLES = (
    "tools_and_elements",
    "profile",
    "domain_and_goals",
    "conversation",
    "session_plan",
    "memory_picture",
    "materials",
    "labels",
    "tail",
)

# Т-§12.3: the interactive elements the teacher may give.
ELEMENT_KINDS = (
    "choice",
    "why_choice",
    "short_answer",
    "task",
    "scaffold",
    "prediction",
    "free_answer",
    "pair",
    "card_series",
    "timed_quiz",
    "visualization",
    "material_link",
    "reference_card",
)

_CONTROL_WORDS = {
    "tasks": "tasks",
    "colloquium_proofs": "colloquium with proofs",
    "oral_theory": "oral theory",
    "no_goal": "no control",
}

_INTENT_WORDS = {"learn": "Learn", "practice": "Practice", "review": "Review"}


@dataclass(frozen=True)
class Profile:
    """Slot 3 (Т-§9.5): explicit preferences (`preference.set`), what was learned about the
    learner and the teacher manner (`digest.written`)."""

    preferences: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    learned: Sequence[str] = ()
    manner: str | None = None


@dataclass(frozen=True)
class GoalTopic:
    topic_id: UUID
    name: str
    required_levels: Sequence[Level]


@dataclass(frozen=True)
class HubGoal:
    """A hub of slot 4 (Т-§8.5): its deadline and the required levels of its topics."""

    hub_id: UUID
    name: str
    control_type: str
    deadline_at_ms: int | None
    topics: Sequence[GoalTopic] = ()


@dataclass(frozen=True)
class LearnerState:
    """Profile and goals of the learner at one moment."""

    profile: Profile
    hubs: Sequence[HubGoal] = ()


@dataclass(frozen=True)
class FrozenSlots:
    profile: str
    domain_and_goals: str
    changes: list[str]


@dataclass(frozen=True)
class SessionGoal:
    """Slot 6: the intent the session started with (Т-§12.7) and its topic or hub."""

    intent: str
    topic_id: UUID | None = None
    hub_id: UUID | None = None


@dataclass(frozen=True)
class LearnerSkill:
    """A learner skill called with `/name` (`content.learner_skills`, Т-§17.8)."""

    name: str
    text: str


def render_tools_and_elements() -> str:
    """Slot 2: the memory tools (Т-§10.4) and the element kinds (Т-§12.3); changes with a
    release only."""
    lines = ["Memory tools:"]
    lines.extend(f"- {tool.name}: {tool.description}" for tool in TOOLS)
    lines.append("Interactive elements: " + ", ".join(ELEMENT_KINDS) + ".")
    return "\n".join(lines)


def render_profile(profile: Profile) -> str:
    lines = [f"Preference {key}: {value}" for key, value in sorted(profile.preferences.items())]
    lines.extend(f"Learned: {item}" for item in profile.learned)
    if profile.manner:
        lines.append(f"Teacher manner: {profile.manner}")
    return "\n".join(lines)


def _date(at_ms: int, timezone: str | None) -> str:
    try:
        zone = ZoneInfo(timezone) if timezone else UTC
    except ZoneInfoNotFoundError:
        zone = UTC
    return datetime.fromtimestamp(at_ms / 1000, tz=zone).strftime("%Y-%m-%d %H:%M")


def _levels(levels: Sequence[Level]) -> str:
    return ", ".join(f"{level} {LEVEL_NAMES[level]}" for level in sorted(levels))


def render_goals(hubs: Sequence[HubGoal], labels: LabelMap, timezone: str | None) -> str:
    lines: list[str] = []
    for hub in hubs:
        label = labels.label("hub", hub.hub_id)
        head = f'{label} "{hub.name}"' if label else f'"{hub.name}"'
        when = (
            f"deadline {_date(hub.deadline_at_ms, timezone)}"
            if hub.deadline_at_ms is not None
            else "no deadline"
        )
        control = _CONTROL_WORDS.get(hub.control_type, hub.control_type)
        lines.append(f"Hub {head}: {when}, {control}.")
        for topic in hub.topics:
            topic_label = labels.label("topic", topic.topic_id)
            name = f"{topic.name} ({topic_label})" if topic_label else topic.name
            lines.append(f"- {name}: levels {_levels(topic.required_levels)}")
    return "\n".join(lines)


# Slots 3 and 4 are rendered without labels: the label table lives for one call (Т-§4.5)
# while these slots keep their text for the whole session (Т-§10.1); slot 9 names every
# label of the call (D-B-180).
_NO_LABELS = LabelMap({})


def snapshot_slots(state: LearnerState, timezone: str | None) -> dict[str, str]:
    """The text of slots 3 and 4 for `ops.turn_manifests.frozen_slots` (D-A-137)."""
    return {
        "profile": render_profile(state.profile),
        "domain_and_goals": render_goals(state.hubs, _NO_LABELS, timezone),
    }


def frozen_slots(
    snapshot: Mapping[str, str], now: LearnerState, timezone: str | None
) -> FrozenSlots:
    """Slots 3 and 4 from the snapshot at the session start; what changed since — lines of
    the tail (Т-§10.1)."""
    profile, goals = snapshot["profile"], snapshot["domain_and_goals"]
    before = set(profile.splitlines()) | set(goals.splitlines())
    current = snapshot_slots(now, timezone)
    lines = [*current["profile"].splitlines(), *current["domain_and_goals"].splitlines()]
    changes = [f"Changed during this session: {line}" for line in lines if line not in before]
    return FrozenSlots(profile=profile, domain_and_goals=goals, changes=changes)


def render_session_plan(goal: SessionGoal, plan: PlanUpdateRecord | None, labels: LabelMap) -> str:
    """Slot 6 (Т-§12.5): the intent and its target, the plan document of the session."""
    if goal.topic_id is not None:
        target = labels.label("topic", goal.topic_id) or "a topic"
    elif goal.hub_id is not None:
        target = labels.label("hub", goal.hub_id) or "a hub"
    else:
        target = "the domain"
    lines = [f"Goal: {_INTENT_WORDS.get(goal.intent, goal.intent)} {target}"]
    if plan is not None:
        if plan.completed_steps:
            lines.append("Done: " + "; ".join(plan.completed_steps))
        lines.append(f"Current step: {plan.current_step}")
        if plan.next_steps:
            lines.append("Next: " + "; ".join(plan.next_steps))
        lines.extend(f"Branch ({branch.type}): {branch.description}" for branch in plan.branches)
    return "\n".join(lines)


def render_materials(chunks: Sequence[MaterialChunk], labels: LabelMap) -> str:
    """Slot 8: each chunk as a quote from the material with its source (Т-§19.8)."""
    return render_chunks(chunks, lambda chunk_id: labels.label("chunk", chunk_id))


def render_labels(labels: LabelMap, names: Mapping[UUID, str]) -> str:
    """Slot 9: every label of the call with what it names; never an id (Т-§4.5)."""
    lines: list[str] = []
    for kind in LABEL_PREFIX:
        for label, object_id in labels.labels(kind).items():
            name = names.get(object_id)
            lines.append(f"{label}: {name}" if name else label)
    return "\n".join(lines)


def render_tail(changes: Sequence[str], skill: LearnerSkill | None) -> str:
    """The tail: changes of slots 3–4 during the session; a called learner skill — this turn
    only."""
    lines = list(changes)
    if skill is not None:
        lines.append(f'The learner called the skill "/{skill.name}" for this turn:')
        lines.append(skill.text)
    return "\n".join(lines)


@dataclass(frozen=True)
class TeacherSlots:
    frozen: FrozenSlots
    conversation: str
    session_plan: str
    memory_picture: str
    materials: str
    labels: str
    skill: LearnerSkill | None = None


def teacher_variables(slots: TeacherSlots) -> dict[str, str]:
    """The variables of `teacher@2` (WP14.1); `guard_feedback` is empty on the first draft."""
    return {
        "tools_and_elements": render_tools_and_elements(),
        "profile": slots.frozen.profile,
        "domain_and_goals": slots.frozen.domain_and_goals,
        "conversation": slots.conversation,
        "session_plan": slots.session_plan,
        "memory_picture": slots.memory_picture,
        "materials": slots.materials,
        "labels": slots.labels,
        "tail": render_tail(slots.frozen.changes, slots.skill),
        "guard_feedback": "",
    }


# WP14.5 (Т-§10.1): slots cut first when the context is over `teacher_context_max_tokens`, in
# this order; the conversation goes last (D-B-164).
TRIM_ORDER = ("materials", "memory_picture", "labels")


def estimate_tokens(text: str) -> int:
    """WP14.5: the size before the call by characters, `len / 4` (the Meta tokenizer is not
    named in the documents; `usage` of `ops.llm_usage` corrects it)."""
    return len(text) // 4


def _cut(text: str, *, from_start: bool) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    return "\n".join(lines[1:] if from_start else lines[:-1])


def fit_budget(
    render: Callable[[dict[str, str]], str], variables: dict[str, str], max_tokens: int
) -> dict[str, str]:
    """Drop lines until the rendered context fits `max_tokens`: the last lines of slots 8, 7,
    9 (the least important come last), then the oldest lines of the conversation; the current
    action of the learner (the last block of slot 5) is never cut."""
    out = dict(variables)
    for name in TRIM_ORDER:
        while estimate_tokens(render(out)) > max_tokens and out[name]:
            out[name] = _cut(out[name], from_start=False)
    lines = out["conversation"].splitlines()
    current = max(
        (i for i, line in enumerate(lines) if line == "Current action"), default=len(lines)
    )
    head, tail = lines[:current], lines[current:]
    while estimate_tokens(render(out)) > max_tokens and head:
        head = head[1:]
        out["conversation"] = "\n".join([*head, *tail])
    return out
