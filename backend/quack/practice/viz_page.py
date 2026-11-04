"""Delivery of a visualization page, WP16.5 (ТЗ Ф4; Т-§13.6; `T-AG6`): the body of
`GET /v1/viz/{id}`. D-B-178 (owner decision on #201: option 1).

- `{id}` is the `request_id` of the `agent.viz` request. The page is the object that
  `agent.completed {kind: viz, ok: true}` of this learner points at; no such event, a failed
  build or an object that is gone — one 404, the same for another learner's id (law 8).
- The CSP: scripts from the static address and inline, nothing else; no network at all
  (`connect-src 'none'`); the policy itself sandboxes the document with `allow-scripts` only, so
  opened outside the client's iframe it still has no same-origin rights. The client puts the page
  into `<iframe sandbox="allow-scripts">` (no `allow-same-origin`).

The body lives in `practice`: the route is declared in `quack/artifacts/viz_routes.py` (ТЗ Ф4
§7.3 п.3), and a product package imports only `practice` among the product ones (§4.1).
"""

from __future__ import annotations

from uuid import UUID

from quack.config import Settings
from quack.db.db import Db
from quack.storage.objects import ObjectStore, learner_prefix

VIZ_KIND = "viz"

_RESULT = (
    "SELECT payload->>'result_ref' AS result_ref FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'agent.completed' "
    "AND payload->>'request_id' = %(request_id)s AND payload->>'kind' = %(kind)s "
    "AND payload->>'ok' = 'true' ORDER BY seq DESC LIMIT 1"
)


class VizNotFound(Exception):
    """No published page of this learner under that id."""


def content_security_policy(settings: Settings) -> str:
    """D-B-178: the static address and inline scripts; no network; sandboxed document."""
    static = settings.viz_static_base_url.rstrip("/")
    sources = " ".join(part for part in (static, "'unsafe-inline'") if part)
    assets = static or "'none'"
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src {sources}",
            f"style-src {sources}",
            f"font-src {assets} data:",
            f"img-src {assets} data: blob:",
            "connect-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            "sandbox allow-scripts",
        )
    )


async def load_viz_page(db: Db, storage: ObjectStore, *, learner_id: UUID, viz_id: UUID) -> bytes:
    row = await db.fetch_one(
        _RESULT, {"learner_id": learner_id, "request_id": str(viz_id), "kind": VIZ_KIND}
    )
    key = row["result_ref"] if row else None
    if not key or not key.startswith(learner_prefix(learner_id)):
        raise VizNotFound(str(viz_id))
    try:
        return await storage.get(key)
    except KeyError as exc:
        raise VizNotFound(str(viz_id)) from exc
