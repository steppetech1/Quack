"""Event registry and the rollback check (ТЗ Фазы 3 WP8.5, WP11.5, Д-§5.1, Д-§6.3).

`event_registry()` lists the (type, schema_version) pairs of the catalog and the rules version;
the image keeps it in `/app/event_registry.json`. `can_rollback` compares the registry of the
previous image with the current one: a rollback is safe when no new pair was ever written
(`ops.event_types_seen`) and the rules did not change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.events.catalog import EVENT_TYPE_VERSIONS
from quack.graph.ids import CURRENT_RULES_VERSION
from quack.graph.phase_a import HANDLERS as PROJECTOR_HANDLERS
from quack.jobs.dispatcher_rules import DISPATCHER_RULES, NO_DISPATCH_RULE

type Pair = tuple[str, int]


def event_registry() -> dict[str, Any]:
    pairs = sorted(EVENT_TYPE_VERSIONS)
    return {
        "rules_version": CURRENT_RULES_VERSION,
        "events": [{"type": t, "schema_version": v} for t, v in pairs],
    }


def write_event_registry(path: Path) -> None:
    path.write_text(json.dumps(event_registry(), indent=2) + "\n", encoding="utf-8")


def _pairs(registry: dict[str, Any]) -> set[Pair]:
    return {(str(e["type"]), int(e["schema_version"])) for e in registry["events"]}


async def seen_pairs(session_factory: SessionFactory) -> set[Pair]:
    async with session_factory() as session:
        rows = await session.execute(text("SELECT type, schema_version FROM ops.event_types_seen"))
        return {(str(r[0]), int(r[1])) for r in rows}


def can_rollback(previous: dict[str, Any], seen: set[Pair]) -> dict[str, Any]:
    """Д-§6.3: `can_rollback = new_pairs_seen is empty and not rules_changed`."""
    current = event_registry()
    new_pairs = sorted(_pairs(current) - _pairs(previous))
    new_seen = [pair for pair in new_pairs if pair in seen]
    rules_changed = previous.get("rules_version") != current["rules_version"]
    return {
        "can_rollback": not new_seen and not rules_changed,
        "new_pairs": [{"type": t, "schema_version": v} for t, v in new_pairs],
        "new_pairs_seen": [{"type": t, "schema_version": v} for t, v in new_seen],
        "rules_changed": rules_changed,
    }


def registry_gaps() -> list[str]:
    """Д-§5.2 п.6, T-O6: every pair has a projector handler (graph-free types have an explicit
    no-op one) and a dispatcher rule or the explicit "no rule" mark."""
    ruled = {rule.event_type for rule in DISPATCHER_RULES}
    gaps: list[str] = []
    for event_type, version in sorted(EVENT_TYPE_VERSIONS):
        if event_type not in PROJECTOR_HANDLERS:
            gaps.append(f"{event_type}@{version}: no projector handler")
        if event_type not in ruled and event_type not in NO_DISPATCH_RULE:
            gaps.append(f"{event_type}@{version}: no dispatcher rule and no 'no rule' mark")
    return gaps
