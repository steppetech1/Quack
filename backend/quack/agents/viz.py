"""The visualization builder, WP16.5 (ТЗ Ф4; Т-§13.6, Т-§22.10, Т-§13.7, Т-§13.9; `T-AG6`).
D-B-177.

- The job `agent.viz` (WP7.1, `agent.requested {kind: viz}`) reads its request by `request_id`.
- **Generation**: `viz_builder` (Wonder) writes a one-page HTML or SVG from the description;
  libraries only from `viz_libraries` at `viz_static_base_url`.
- **Render**: compute opens the page at each parameter set (`parameter_values`, D-B-130).
- **Check** (Т-§22.10): any console error or a blank snapshot fails; no model check on the slice.
- **Fix**: at most `viz_max_fix_rounds` more calls with the errors of the previous attempt; a
  parameter set that is not a flat JSON object is an error of the same kind (D-B-130 п.2).
- **Publish**: the page goes to the object store (`viz_key`), `agent.completed {ok: true,
  result_ref}`; nothing passed — `agent.completed {ok: false}` and the teacher goes on without
  it. A retry of a finished job finds its `agent.completed` and does nothing (law 5).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from quack.agents.schemas import VizBuilderOutput
from quack.compute_client.client import ComputeClient
from quack.compute_client.contracts import RenderSnapshotsRequest
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AgentCompleted, AgentRequested
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.practice.repo import event_by_key
from quack.storage.keys import viz_key
from quack.storage.objects import ObjectStore

VIZ_ROLE = "viz_builder"
VIZ_KIND = "viz"
NONE_LINE = "(none)"
FAILED = "viz_check_failed"

_COLUMNS = (
    "learner_id, seq, id, type, schema_version, actor, domain_id, session_id, causation_seq, "
    "correlation_id, idempotency_key, payload_hash, occurred_at, payload, source_refs"
)
_REQUEST = (
    f"SELECT {_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'agent.requested' AND payload->>'request_id' = %(request_id)s LIMIT 1"
)
_SCALARS = (str, int, float, bool)


def completed_key(request_id: UUID) -> str:
    return f"agent.completed:{request_id}"


def libraries_block(settings: Settings) -> str:
    base = settings.viz_static_base_url.rstrip("/")
    lines = [f"{name}: {base}/{path.lstrip('/')}" for name, path in settings.viz_libraries.items()]
    return "\n".join(lines) or NONE_LINE


def param_sets(values: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """D-B-130: each value is a flat JSON object; an empty list is one set of defaults."""
    sets: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw in values:
        try:
            parsed = json.loads(raw)
        except ValueError:
            errors.append(f"parameter set is not JSON: {raw}")
            continue
        if not isinstance(parsed, dict) or not all(
            isinstance(v, _SCALARS) for v in parsed.values()
        ):
            errors.append(
                f"parameter set is not a flat object of numbers, strings, booleans: {raw}"
            )
            continue
        sets.append(parsed)
    return (sets or [{}]), errors


def page_html(output: VizBuilderOutput) -> str:
    """An SVG is rendered and served as the body of a page."""
    if output.format == "svg":
        return f'<!doctype html><html><body style="margin:0">{output.content}</body></html>'
    return output.content


async def _check(compute: ComputeClient, html: str, sets: list[dict[str, Any]]) -> list[str]:
    rendered = await compute.render_snapshots(RenderSnapshotsRequest(html=html, param_sets=sets))
    errors: list[str] = []
    for index, params in enumerate(sets):
        label = json.dumps(params, sort_keys=True)
        if index >= len(rendered.snapshots):
            # A set without a snapshot is not checked, and "not checked" is not "passed" (law 8).
            errors.append(f"no snapshot at {label}")
            continue
        snapshot = rendered.snapshots[index]
        errors.extend(f"console error at {label}: {e}" for e in snapshot.console_errors)
        if snapshot.blank:
            errors.append(f"blank canvas at {label}")
    return errors


async def _request(db: Db, learner_id: UUID, request_id: UUID) -> StoredEvent:
    params = {"learner_id": learner_id, "request_id": str(request_id)}
    found = await db.fetch_one(_REQUEST, params, row=StoredEvent)
    if found is None:
        raise LookupError(f"agent.requested {request_id} not found")
    return found


async def build_viz(
    db: Db,
    gateway: LLMGateway,
    compute: ComputeClient,
    storage: ObjectStore,
    settings: Settings,
    *,
    learner_id: UUID,
    request_id: UUID,
) -> StoredEvent:
    """`T-AG6`: generation, render, check, one fix round, publish or `ok: false`."""
    key = completed_key(request_id)
    done = await event_by_key(db, learner_id=learner_id, key=key)
    if done is not None:
        return done
    requested = await _request(db, learner_id, request_id)
    request = AgentRequested.model_validate(requested.payload)
    prompt = settings.llm_roles[VIZ_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'viz_builder' has no prompt")
    errors: list[str] = []
    passed: VizBuilderOutput | None = None
    for attempt in range(1 + settings.viz_max_fix_rounds):
        result = await gateway.generate_structured(
            VIZ_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            VizBuilderOutput,
            {
                "description": request.params.description or NONE_LINE,
                "libraries": libraries_block(settings),
                "previous_errors": "\n".join(errors) or NONE_LINE,
            },
            learner_id,
            requested.session_id,
            None,
            f"viz:{request_id}:{attempt}",
        )
        sets, errors = param_sets(result.value.parameter_values)
        if not errors:
            errors = await _check(compute, page_html(result.value), sets)
        if not errors:
            passed = result.value
            break
    if passed is None:
        completed = AgentCompleted(request_id=request_id, kind=VIZ_KIND, ok=False, error=FAILED)
    else:
        object_key = viz_key(learner_id, request_id)
        await storage.put(object_key, page_html(passed).encode(), "text/html")
        completed = AgentCompleted(
            request_id=request_id, kind=VIZ_KIND, ok=True, result_ref=object_key
        )
    (stored,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="agent.completed",
                payload=completed,
                actor=prompt,
                idempotency_key=key,
                domain_id=requested.domain_id,
                session_id=requested.session_id,
                causation_seq=requested.seq,
            )
        ],
    )
    return stored


@register_job_handler("agent.viz")
async def handle_viz(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("agent.viz needs learner_id")
    await build_viz(
        ctx.db,
        ctx.llm,
        ctx.compute,
        ctx.storage,
        ctx.settings,
        learner_id=job.learner_id,
        request_id=UUID(str(job.payload["request_id"])),
    )
