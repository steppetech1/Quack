"""Phase C of `rules@3`: topics (ТЗ Ф4 WP6.1, WP6.2; Т-§4.7, Т-§8.5, Т-§8.6).

Pure (Т-§2.5). For each topic the batch touched: its status (`proposed` → `active` on the first
evidence or exposure of its skills), visibility, the nearest review date of its skills and the
state in codes — the first of the eight rules of `Т-§8.6` that fires.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from quack.graph.projection.types import Props
from quack.graph.read.models import DisplayState, TopicDisplay

_PROVEN = ("proven", "implied")
_VISIBILITY = {"active": "shown", "off_domain": "shown", "proposed": "pale", "hidden": "hidden"}
_CONFIRMED = ("confirmed", "in_work")


@dataclass(frozen=True)
class TopicSkill:
    id: str
    props: Props
    confirmed: Sequence[tuple[str, str]] = ()  # (misconception, CONFUSED_WITH skill or "")
    transfer_domain: str = ""  # the domain the transfer of a target level came from


@dataclass(frozen=True)
class TopicGoal:
    deadline_at_ms: int
    required_levels: Sequence[int]


@dataclass(frozen=True)
class TopicParams:
    control_type_levels: Mapping[str, Sequence[int]] = field(
        default_factory=lambda: {"no_goal": [3]}
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def target_levels(goals: Sequence[TopicGoal], params: TopicParams) -> list[int]:
    """The hub with the nearest deadline that includes the topic, else «no goal» (`Т-§8.5`)."""
    dated = sorted((g for g in goals if g.deadline_at_ms > 0), key=lambda g: g.deadline_at_ms)
    if dated and dated[0].required_levels:
        return sorted(set(dated[0].required_levels))
    return sorted(set(params.control_type_levels.get("no_goal", [3])))


def _display(skills: Sequence[TopicSkill], levels: Sequence[int]) -> TopicDisplay:
    for s in skills:
        for m, other in s.confirmed:
            if other:
                return TopicDisplay(display_state="confused_with", display_reason=m)
    for s in skills:
        for m, _ in s.confirmed:
            return TopicDisplay(display_state="persistent_error", display_reason=m)
    rules: tuple[tuple[str, DisplayState], ...] = (
        ("failed", "struggling"),
        ("unstable", "inconsistent"),
    )
    for state, code in rules:
        for s in skills:
            for n in levels:
                if s.props.get(f"l{n}Status") == state:
                    return TopicDisplay(display_state=code, display_reason=s.id, display_level=n)
    pairs = [(s, n) for s in skills for n in levels]
    proven = [(s, n) for s, n in pairs if s.props.get(f"l{n}Status") in _PROVEN]
    if pairs and len(proven) == len(pairs):
        borrowed = [
            (s, n) for s, n in proven if s.props.get(f"l{n}Basis") in ("transfer", "indirect")
        ]
        if not borrowed:
            return TopicDisplay(display_state="solved_independently")
        s, n = borrowed[0]
        return TopicDisplay(
            display_state="known_from_other_domain",
            display_reason=s.id,
            display_level=n,
            display_domain_id=s.transfer_domain,
        )
    if proven:
        return TopicDisplay(display_state="in_progress")
    return TopicDisplay(display_state="not_tested")


def fold_topic(
    topic: Props,
    skills: Sequence[TopicSkill],
    goals: Sequence[TopicGoal],
    params: TopicParams,
) -> Props:
    ordered = sorted(skills, key=lambda s: s.id)
    status = str(topic.get("status") or "proposed")
    touched = any(
        _int(s.props.get("lastPracticeAtMs")) > 0 or _int(s.props.get("expCount")) > 0
        for s in ordered
    )
    if status == "proposed" and touched:
        status = "active"
    visibility = _VISIBILITY.get(status, "hidden")
    if status == "proposed" and _int(topic.get("knownClaimedAtMs")) > 0:
        visibility = "hidden"  # Т-§4.11
    display = (
        _display(ordered, target_levels(goals, params)) if status == "active" else TopicDisplay()
    )
    dues = [_int(s.props.get("retDueAtMs")) for s in ordered]
    return {
        "status": status,
        "visibility": visibility,
        "nearestDueAtMs": min((d for d in dues if d > 0), default=0),
        "displayState": display.display_state,
        "displayReason": display.display_reason,
        "displayLevel": display.display_level,
        "displayDomainId": display.display_domain_id,
    }
