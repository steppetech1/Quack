"""`ops orphan-objects --since <t> [--apply]` (Д-§8.3 step 8, ТЗ Ф4 WP9.6, D-C-211, D-C-215).

After Postgres is restored from a dump, `quack-learners` may hold files that the restored log
does not know: uploads whose events were lost. They belong to no record, and account deletion by
the journal does not cover them. Only objects newer than `since` (the time of the dump) are
looked at; an object is kept when Postgres refers to it. Without `apply` the command only
reports what it would delete (dry run, the default); objects are deleted only with `apply`.

Every place that may name a file is one entry of `REFERENCE_SOURCES`; a new column or table
with a key is added there and gets a case in `tests/test_ops_p4_recovery.py`, which fails for
a source without a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import LiteralString
from uuid import UUID

from quack.db import Db
from quack.storage.objects import ObjectInfo, ObjectStore


@dataclass(frozen=True)
class ReferenceSource:
    """One place in Postgres that names a file of a learner. `query` takes `learner_id` and
    `key` and returns a row when the key is named; the learner of the key is the only one
    looked at (isolation)."""

    name: str
    query: LiteralString


REFERENCE_SOURCES: tuple[ReferenceSource, ...] = (
    ReferenceSource(
        "materials",
        "SELECT 1 AS found FROM content.materials "
        "WHERE learner_id = %(learner_id)s AND object_key = %(key)s LIMIT 1",
    ),
    ReferenceSource(
        "material_pages",
        "SELECT 1 AS found FROM content.material_pages "
        "WHERE learner_id = %(learner_id)s AND image_key = %(key)s LIMIT 1",
    ),
    ReferenceSource(
        "exports",
        "SELECT 1 AS found FROM content.exports "
        "WHERE learner_id = %(learner_id)s AND object_key = %(key)s LIMIT 1",
    ),
    ReferenceSource(
        "events",
        "SELECT 1 AS found FROM core.events "
        "WHERE learner_id = %(learner_id)s AND strpos(payload::text, %(key)s) > 0 LIMIT 1",
    ),
    ReferenceSource(
        "artifact_versions",
        "SELECT 1 AS found FROM content.artifact_versions "
        "WHERE learner_id = %(learner_id)s AND strpos(content::text, %(key)s) > 0 LIMIT 1",
    ),
    # A drawing cut out of a photo is named only by the document of the board
    # (`learner.drawings[].object_key`), by no event and no column (D-A-248).
    ReferenceSource(
        "canvas_drawings",
        "SELECT 1 AS found FROM content.canvas_boards "
        "WHERE learner_id = %(learner_id)s AND strpos(doc::text, %(key)s) > 0 LIMIT 1",
    ),
)
# Canvas chunks are named by no column: they are found by the prefix of the canvas, and the
# canvas is kept when it has a row.
CANVAS_SOURCE = ReferenceSource(
    "canvas_boards",
    "SELECT 1 AS found FROM content.canvas_boards "
    "WHERE learner_id = %(learner_id)s AND canvas_id = %(canvas_id)s LIMIT 1",
)
SOURCE_NAMES = frozenset(source.name for source in (*REFERENCE_SOURCES, CANVAS_SOURCE))

_PREFIX = "learners/"
_CANVAS_PARTS = 6  # learners / {learner} / canvas / {session} / {canvas} / {file}


@dataclass(frozen=True)
class OrphanReport:
    checked: int
    orphans: list[str]
    deleted: int
    dry_run: bool = True


def _uuid(text: str) -> UUID | None:
    try:
        return UUID(text)
    except ValueError:
        return None


async def _referenced(db: Db, learner_id: UUID, key: str, canvas_id: UUID | None) -> bool:
    if canvas_id is not None:
        params: dict[str, object] = {"learner_id": learner_id, "canvas_id": canvas_id}
        return await db.fetch_one(CANVAS_SOURCE.query, params) is not None
    params = {"learner_id": learner_id, "key": key}
    for source in REFERENCE_SOURCES:
        if await db.fetch_one(source.query, params) is not None:
            return True
    return False


async def _kept(db: Db, info: ObjectInfo) -> bool:
    parts = info.key.split("/")
    learner_id = _uuid(parts[1]) if len(parts) > 1 else None
    if learner_id is None:
        return False  # not under a learner: nothing in Postgres can refer to it
    canvas_id = None
    if len(parts) >= _CANVAS_PARTS and parts[2] == "canvas":
        canvas_id = _uuid(parts[4])
        if canvas_id is None:
            return False
    return await _referenced(db, learner_id, info.key, canvas_id)


async def orphan_objects(
    db: Db, store: ObjectStore, since: datetime, *, apply: bool = False
) -> OrphanReport:
    """Objects of `quack-learners` newer than `since` without a reference in Postgres, sorted
    by key. They are deleted only when `apply` is true; by default nothing is touched."""
    newer = [o for o in await store.list_objects(_PREFIX) if o.last_modified > since]
    orphans = sorted([o.key for o in newer if not await _kept(db, o)])
    if orphans and apply:
        await store.delete_keys(orphans)
    return OrphanReport(
        checked=len(newer),
        orphans=orphans,
        deleted=len(orphans) if apply else 0,
        dry_run=not apply,
    )
