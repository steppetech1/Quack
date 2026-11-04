"""The event registry of the image for Phase 4 (ТЗ Ф4 WP2.4; Д-§5.1, Д-§5.2 п.6, Д-§6.3; D-A-218).

`event_registry()` lists the `(type, schema_version)` pairs of the Phase 4 catalog and the rules
version `rules@3`; the image keeps it in `/app/event_registry.json`. `can_rollback` compares the
registry of the previous image with the current one: a rollback is safe when no new pair was ever
written (`ops.event_types_seen`) and the rules did not change. `registry_gaps` is the CI step
«реестр событий» (Д-§5.2 п.6, T-EV5): every type the catalog marks for the projector has a
handler, every type it marks for the dispatcher has a rule.

Bodies of the ops commands (ТЗ §7.3, crossing 5): stream C registers them in `quack/cli.py` and
`quack/ops/` in place of the Phase 3 `quack.ops.registry`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quack.db import Db
from quack.events.registry import SPECS, payload_model
from quack.graph.projection.handlers import HANDLERS
from quack.graph.projection.runner import RULES_VERSION
from quack.jobs.episodes import EPISODE_TYPES
from quack.jobs.facts_p4 import STRUCTURE_TYPES
from quack.jobs.rules_p4 import RULES

type Pair = tuple[str, int]

_SEEN = "SELECT type, schema_version FROM ops.event_types_seen"

# §6.3 column «Диспетчер» = «эмбеддинг»: the types the `*` row of WP7.1 plans `embedding.compute`
# for (a new topic, skill or material with text; D-A-117, D-A-218). The row itself still waits for
# its facts (`rules_p4.ROWS_NEED_FACTS`); the list moves into the row when it is implemented.
EMBEDDED_TYPES: frozenset[str] = frozenset(
    {"topic.proposed", "topic.created", "skill.created", "material.parsed"}
)


def _specs() -> dict[Pair, Any]:
    payload_model("message.sent")  # loads the catalog (it registers itself on import)
    return dict(SPECS)


def event_registry() -> dict[str, Any]:
    return {
        "rules_version": RULES_VERSION,
        "events": [{"type": t, "schema_version": v} for t, v in sorted(_specs())],
    }


def write_event_registry(path: Path) -> None:
    path.write_text(json.dumps(event_registry(), indent=2) + "\n", encoding="utf-8")


def _pairs(registry: dict[str, Any]) -> set[Pair]:
    return {(str(e["type"]), int(e["schema_version"])) for e in registry["events"]}


async def seen_pairs(db: Db) -> set[Pair]:
    rows = await db.fetch_all(_SEEN)
    return {(str(r["type"]), int(r["schema_version"])) for r in rows}


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
    """Д-§5.2 п.6: the catalog's «Проектор» and «Диспетчер» columns (§6.3) against the code.

    The dispatcher serves a type by a row of WP7.1, by the episodes (WP7.2, column «эпизод»), by
    the structure facts its rows read (`facts_p4`), or by the `*` row of embeddings."""
    ruled = {r.event_type for r in RULES} | EPISODE_TYPES | set(STRUCTURE_TYPES) | EMBEDDED_TYPES
    gaps: list[str] = []
    for (event_type, version), spec in sorted(_specs().items()):
        if spec.projector and event_type not in HANDLERS:
            gaps.append(f"{event_type}@{version}: no projector handler")
        if spec.dispatcher and event_type not in ruled:
            gaps.append(f"{event_type}@{version}: no dispatcher rule")
    return gaps
