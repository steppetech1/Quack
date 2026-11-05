"""Phase B of `rules@3`: the life cycle of one misconception (ТЗ Ф4 WP5.2; Т-§9.2).

Pure (Т-§2.5): manifestations and resistances in time order; «a few days later» compares the
time of a resistance with the time of the move to `watching`, both from events.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quack.config import Settings
from quack.graph.projection.types import Props

_MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class FoldSign:
    """A manifestation (`MANIFESTS`) or a resistance (`RESISTED`) — its evidence and task."""

    at_ms: int
    seq: int
    tier: int
    family_id: str  # "" — not on a task
    resists: bool


@dataclass(frozen=True)
class MisconceptionParams:
    fix_min_resists: int = 2
    watch_ms: int = 3 * _MS_PER_DAY

    @classmethod
    def from_settings(cls, s: Settings) -> MisconceptionParams:
        return cls(fix_min_resists=s.fix_min_resists, watch_ms=s.watch_days * _MS_PER_DAY)


def _families(signs: Sequence[FoldSign]) -> set[str]:
    return {s.family_id for s in signs if s.family_id}


def fold_misconception(
    signs: Sequence[FoldSign],
    params: MisconceptionParams,
    *,
    disputed: bool = False,
    targeted_session: str = "",
) -> Props:
    ordered = sorted(signs, key=lambda s: (s.at_ms, s.seq))
    shown = [s for s in ordered if not s.resists]
    status, watch_since, fixed_at = "unknown", 0, 0
    seen: list[FoldSign] = []
    resisted: list[FoldSign] = []  # strong resistances after the last manifestation
    for s in ordered:
        if not s.resists:
            seen.append(s)
            resisted = []
            if status in ("watching", "fixed"):
                status, watch_since, fixed_at = "confirmed", 0, 0
            elif status in ("unknown", "dismissed"):
                status = "suspected"
            if (
                status == "suspected"
                and len(_families(seen)) >= 2
                and any(m.tier <= 2 for m in seen)
            ):
                status = "confirmed"
            continue
        if s.tier > 2:
            continue
        resisted.append(s)
        enough = len(_families(resisted)) >= params.fix_min_resists
        if status == "suspected" and enough:
            status = "dismissed"
        elif status == "confirmed" and enough:
            status, watch_since = "watching", s.at_ms
        elif status == "watching" and s.at_ms - watch_since >= params.watch_ms:
            status, fixed_at = "fixed", s.at_ms
    if disputed:
        status = "disputed"
    elif status == "confirmed" and targeted_session:
        status = "in_work"
    return {
        "status": status,
        "occurrences": len(shown),
        "firstSeenAtMs": shown[0].at_ms if shown else 0,
        "lastSeenAtMs": shown[-1].at_ms if shown else 0,
        "watchSinceMs": watch_since,
        "fixedAtMs": fixed_at,
    }
