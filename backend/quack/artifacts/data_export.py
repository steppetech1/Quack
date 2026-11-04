"""The job `export.data`: the archive of the learner's data (ТЗ Ф4 WP23.7; Т-§19.6, П-14.4;
issue #263; D-C-231).

The job (P3) is put for `agent.requested {kind: data_export}` of the actor `learner`; its payload
names the request, and the request id is the id of the export (`content.exports.export_id`,
`GET /v1/learner/exports/{id}`). The archive is one zip:

- `events.jsonl` — the learner's events in the order of `seq`, one JSON object per line;
- `documents/{schema.table}.json` — the rows of the learner's tables as JSON arrays;
- `objects/…` — the files of the learner's prefix as they lie in the storage (earlier archives
  excluded);
- `artifacts/{kind}/{topic|hub}.md` — the current version of each artifact, readable.

The archive goes to `learners/{id}/exports/` (`export_key`); the row of `content.exports` becomes
`ready` with `expires_at` after `export_link_ttl_hours`, and `agent.completed {kind: data_export,
result_ref: object_key}` is written in the same transaction. A row that is `ready` or `expired`
is never built again (law 5); a failure before the commit deletes the object it put and commits
nothing (law 7). The expired archives are deleted by `exports.cleanup` (D-A-215).
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString
from uuid import UUID

from quack.artifacts.delivery import deliver
from quack.db import Db
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.logging import get_logger
from quack.storage.keys import export_key
from quack.storage.objects import ObjectStore, learner_prefix

log = get_logger(__name__)

KIND = "data_export"
ACTOR = "code"
EVENTS_PAGE = 1000
# Tables with `learner_id`, in the order of the archive; the secrets of the login (`core.auth_*`)
# and the exports themselves are not the learner's data.
DOCUMENT_TABLES: tuple[LiteralString, ...] = (
    "core.learners",
    "content.sessions",
    "content.session_plans",
    "content.study_plans",
    "content.canvas_boards",
    "content.tasks",
    "content.materials",
    "content.material_pages",
    "content.artifacts",
    "content.artifact_versions",
    "content.learner_skills",
)

_EVENTS_PAGE = (
    "SELECT seq, type, schema_version, actor, domain_id, session_id, causation_seq, "
    "correlation_id, idempotency_key, occurred_at, payload, source_refs FROM core.events "
    "WHERE learner_id = %(learner_id)s AND seq > %(after)s ORDER BY seq LIMIT %(limit)s"
)
_ARTIFACTS = (
    "SELECT a.kind, a.topic_id, a.hub_id, a.artifact_id, a.current_version, v.content "
    "FROM content.artifacts a JOIN content.artifact_versions v "
    "ON v.artifact_id = a.artifact_id AND v.version = a.current_version "
    "WHERE a.learner_id = %(learner_id)s AND v.learner_id = %(learner_id)s "
    "ORDER BY a.kind, a.artifact_id"
)
_EXPORT = (
    "SELECT status FROM content.exports "
    "WHERE export_id = %(export_id)s AND learner_id = %(learner_id)s"
)
_READY = (
    "INSERT INTO content.exports (export_id, learner_id, status, object_key, expires_at) "
    "VALUES (%(export_id)s, %(learner_id)s, 'ready', %(object_key)s, %(expires_at)s) "
    "ON CONFLICT (export_id) DO UPDATE SET status = 'ready', object_key = EXCLUDED.object_key, "
    "expires_at = EXCLUDED.expires_at WHERE content.exports.learner_id = EXCLUDED.learner_id"
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


async def _events_jsonl(db: Db, learner_id: UUID) -> bytes:
    lines: list[str] = []
    after = 0
    while True:
        page = await db.fetch_all(
            _EVENTS_PAGE, {"learner_id": learner_id, "after": after, "limit": EVENTS_PAGE}
        )
        lines.extend(_json(row) for row in page)
        if len(page) < EVENTS_PAGE:
            break
        after = page[-1]["seq"]
    return ("\n".join(lines) + "\n" if lines else "").encode()


def _readable(kind: str, content: dict[str, Any] | None) -> str:
    """The current version as Markdown: blocks as paragraphs under their kind, cards as items."""
    parts: list[str] = [f"# {kind}"]
    for block in (content or {}).get("blocks", []):
        parts.append(f"## {block['kind']}\n\n{block['content']}")
    cards = (content or {}).get("cards", [])
    if cards:
        parts.append("\n".join(f"- ({card['kind']}) {card['prompt']}" for card in cards))
    return "\n\n".join(parts) + "\n"


async def build_archive(db: Db, store: ObjectStore, learner_id: UUID) -> bytes:
    """The zip of `learner_id`; every query is scoped to the learner (T-ST isolation)."""
    prefix = learner_prefix(learner_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("events.jsonl", await _events_jsonl(db, learner_id))
        for table in DOCUMENT_TABLES:
            rows = await db.fetch_all(
                f"SELECT * FROM {table} WHERE learner_id = %(learner_id)s ORDER BY 1",
                {"learner_id": learner_id},
            )
            archive.writestr(f"documents/{table}.json", _json(rows))
        for row in await db.fetch_all(_ARTIFACTS, {"learner_id": learner_id}):
            owner = row["topic_id"] or row["hub_id"] or row["artifact_id"]
            archive.writestr(
                f"artifacts/{row['kind']}/{owner}.md", _readable(row["kind"], row["content"])
            )
        for info in await store.list_objects(prefix):
            if info.key.startswith(f"{prefix}exports/"):
                continue  # an archive is not part of the next one
            archive.writestr(f"objects/{info.key[len(prefix) :]}", await store.get(info.key))
    return buffer.getvalue()


async def export_learner_data(
    ctx: JobContext, *, learner_id: UUID, export_id: UUID, now: datetime | None = None
) -> None:
    """Build, store and publish the archive of `export_id`; a finished export is left alone."""
    row = await ctx.db.fetch_one(_EXPORT, {"export_id": export_id, "learner_id": learner_id})
    if row is not None and row["status"] in ("ready", "expired"):
        return
    moment = now or datetime.now(UTC)
    data = await build_archive(ctx.db, ctx.storage, learner_id)
    key = export_key(learner_id, export_id)
    await ctx.storage.put(key, data, "application/zip")
    try:
        async with ctx.db.transaction():
            await ctx.db.execute(
                _READY,
                {
                    "export_id": export_id,
                    "learner_id": learner_id,
                    "object_key": key,
                    "expires_at": moment + timedelta(hours=ctx.settings.export_link_ttl_hours),
                },
            )
            await deliver(
                ctx.db,
                learner_id=learner_id,
                request_id=export_id,
                job_key=None,
                kind=KIND,
                ok=True,
                actor=ACTOR,
                result_ref=key,
            )
    except BaseException:
        await ctx.storage.delete_keys([key])
        raise


@register_job_handler("export.data")
async def _export_data_handler(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = job.learner_id
    if learner_id is None:
        raise ValueError("export.data without a learner")
    await export_learner_data(
        ctx, learner_id=learner_id, export_id=UUID(str(job.payload["request_id"]))
    )
