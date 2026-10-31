"""OpenAPI schema of the API and its committed snapshot `backend/openapi.json`
(ТЗ Фазы 3 WP8.7, `Д-§5.2` п.7).

Error responses are documented with the common `ErrorResponse` (`С-§8.5`) for the codes
401, 404, 409, 422, 429, 503 where a route can return them; the rules live in one place
here, so the product routers stay as they are (PHASE3_NOTES D-C-015)."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import FastAPI
from pydantic.json_schema import models_json_schema

from quack.api.errors import ErrorResponse

COMPONENT_REF = "#/components/schemas/{model}"
ERROR_REF = COMPONENT_REF.format(model=ErrorResponse.__name__)

# Routes reachable without a token (WP1.2): register and login.
PUBLIC_ROUTES = frozenset({("post", "/v1/auth/register"), ("post", "/v1/auth/login")})
# The heartbeat does not count against the `actions` limit (WP1.5, `quack.api.ratelimit`).
RATE_LIMIT_EXEMPT = frozenset({("post", "/v1/sessions/{session_id}/heartbeat")})
MUTATING_METHODS = frozenset({"post", "patch", "delete"})
# Server-sent event streams (`С-§8.5`); the learner stream (WP7) joins when it is mounted.
SSE_ROUTES = frozenset(
    {
        ("post", "/v1/sessions/{session_id}/turns"),
        ("get", "/v1/sessions/{session_id}/events"),
        ("get", "/v1/learner/stream"),
    }
)
# Events of a stream in their order, for the frontend (С-§8.5, WP6.3, WP6.4).
SSE_EVENTS = {
    ("post", "/v1/sessions/{session_id}/turns"): (
        "; events in order: `turn_started` {turn_id, turn_index, user_seq}; "
        '`status` {"state": "thinking"} once, right after `turn_started`, before the model '
        "call (WP6.3); `token` {delta}, one or more; `task_evaluated` when the turn checks a "
        "task answer; `task_assigned` when the turn assigns a task, after `task_evaluated` "
        "(WP6.4); `done` last"
    ),
}
# FastAPI's default validation models, replaced by `ErrorResponse` (VALIDATION_ERROR).
DEFAULT_VALIDATION_MODELS = ("HTTPValidationError", "ValidationError")

ERROR_DESCRIPTIONS = {
    "401": "UNAUTHORIZED: missing, invalid or revoked token",
    "404": "NOT_FOUND: the resource does not exist or belongs to another learner",
    "409": "IDEMPOTENCY_CONFLICT or TURN_LOCKED",
    "422": "VALIDATION_ERROR",
    "429": "RATE_LIMITED; details.retry_after_sec",
    "503": "SERVICE_UNAVAILABLE or LLM_DEGRADED: a dependency of the answer is down",
}
_PATH_PARAM = re.compile(r"\{[^}]+\}")


def _error_statuses(method: str, path: str, operation: dict[str, Any]) -> set[str]:
    """Which error codes a `/v1` route can return (WP8.7 rules, D-C-015)."""
    if not path.startswith("/v1/"):
        return set()
    statuses = {"503"}
    if (method, path) not in PUBLIC_ROUTES:
        statuses.add("401")
    if _PATH_PARAM.search(path):
        statuses.add("404")
    if method in MUTATING_METHODS:
        statuses.add("409")
        if (method, path) not in RATE_LIMIT_EXEMPT:
            statuses.add("429")
    if operation.get("parameters") or operation.get("requestBody"):
        statuses.add("422")
    return statuses


def _error_response(status: str) -> dict[str, Any]:
    return {
        "description": ERROR_DESCRIPTIONS[status],
        "content": {"application/json": {"schema": {"$ref": ERROR_REF}}},
    }


def _sse_response(description: str, events: str = "") -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": "named events `event: <name>\\ndata: <json>` (С-§8.5)" + events,
                }
            }
        },
    }


def _document(schema: dict[str, Any]) -> dict[str, Any]:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    _, defs = models_json_schema([(ErrorResponse, "serialization")], ref_template=COMPONENT_REF)
    components.update(defs.get("$defs", {}))
    for name in DEFAULT_VALIDATION_MODELS:
        components.pop(name, None)

    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            responses: dict[str, Any] = operation.setdefault("responses", {})
            if (method, path) in SSE_ROUTES:
                ok = next(s for s in responses if s.startswith("2"))
                responses[ok] = _sse_response(
                    responses[ok].get("description", ""), SSE_EVENTS.get((method, path), "")
                )
            for status in _error_statuses(method, path, operation):
                responses[status] = _error_response(status)
    return schema


def install_openapi(app: FastAPI) -> None:
    """Replace `app.openapi` with the documented schema (error responses, SSE bodies)."""

    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if not getattr(app.state, "openapi_documented", False):
            app.openapi_schema = _document(generate())
            app.state.openapi_documented = True
        assert app.openapi_schema is not None
        return app.openapi_schema

    app.openapi = openapi


def render_openapi(app: FastAPI) -> bytes:
    """The schema of `app` serialized deterministically: sorted keys, 2-space indent, UTF-8,
    trailing newline. `ops openapi` and `T-A7` use exactly these bytes."""
    text = json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")
