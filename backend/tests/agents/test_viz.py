"""The visualization builder, WP16.5 (ТЗ Ф4; Т-§13.6, Т-§22.10; `T-AG6`): console errors or a
blank snapshot fail; one fix round with the errors; then `failed`; a passed page is stored and
`agent.completed` points at it; a retry of a finished job does nothing. D-B-177."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.agents.schemas import VizBuilderOutput
from quack.agents.viz import NONE_LINE, build_viz, libraries_block, param_sets
from quack.compute_client.client import ComputeClient
from quack.compute_client.contracts import (
    RenderSnapshotsRequest,
    RenderSnapshotsResponse,
    Snapshot,
)
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import AgentRequested
from quack.events.log import NewEvent, append_events
from quack.events.records import AgentParamsRecord
from quack.llm.gateway import LLMGateway, LLMResult
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner
from tests.support.storage import InMemoryObjectStore

SETTINGS = Settings()
GOOD = Snapshot(image_webp_b64="", console_errors=[], blank=False)


@dataclass
class _Gateway:
    answers: list[VizBuilderOutput]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append({"role": role, "variables": variables, "key": idempotency_key})
        answer = self.answers.pop(0)
        return LLMResult(value=answer, from_cache=False, prompt_hash="h", model="m", usage=None)


@dataclass
class _Compute:
    snapshots: list[list[Snapshot]]
    requests: list[RenderSnapshotsRequest] = field(default_factory=list)

    async def render_snapshots(self, request: RenderSnapshotsRequest) -> RenderSnapshotsResponse:
        self.requests.append(request)
        return RenderSnapshotsResponse(snapshots=self.snapshots.pop(0))


def _page(content: str = "<p>ok</p>", values: list[str] | None = None) -> VizBuilderOutput:
    return VizBuilderOutput(
        format="html", content=content, parameter_values=values or [], answer_action=None
    )


async def _requested(db: Db, learner_id: UUID) -> UUID:
    request_id = uuid4()
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=request_id,
                    kind="viz",
                    params=AgentParamsRecord(description="a slider over the ratio test"),
                    needed_in_session=True,
                ),
                actor="teacher@2",
                idempotency_key=f"req:{request_id}",
            )
        ],
    )
    return request_id


async def _build(
    db: Db, gateway: _Gateway, compute: _Compute, store: InMemoryObjectStore, **kw: Any
) -> Any:
    return await build_viz(
        db,
        cast(LLMGateway, gateway),
        cast(ComputeClient, compute),
        store,
        SETTINGS,
        **kw,
    )


async def test_viz_one_fix_round_and_failed(pg_db: Db) -> None:
    """T-AG6: a console error → one fix call that gets the errors → still failing → `failed`."""
    learner_id = await create_learner(pg_db)
    request_id = await _requested(pg_db, learner_id)
    gateway = _Gateway([_page("<p>v1</p>", ['{"a": 1}']), _page("<p>v2</p>")])
    error = Snapshot(image_webp_b64="", console_errors=["x is undefined"], blank=False)
    blank = Snapshot(image_webp_b64="", console_errors=[], blank=True)
    compute = _Compute([[error], [blank]])
    store = InMemoryObjectStore()

    done = await _build(
        pg_db, gateway, compute, store, learner_id=learner_id, request_id=request_id
    )

    assert [c["key"] for c in gateway.calls] == [f"viz:{request_id}:0", f"viz:{request_id}:1"]
    first, second = (c["variables"] for c in gateway.calls)
    assert first["description"] == "a slider over the ratio test"
    assert first["previous_errors"] == NONE_LINE
    assert second["previous_errors"] == 'console error at {"a": 1}: x is undefined'
    PromptRegistry().load("viz_builder", 1).render(first)
    assert [r.param_sets for r in compute.requests] == [[{"a": 1}], [{}]]
    assert done.type == "agent.completed"
    assert done.payload["ok"] is False and done.payload["error"] == "viz_check_failed"
    assert done.payload["request_id"] == str(request_id)
    assert await store.list_objects("") == []


async def test_viz_published_and_retry_is_noop(pg_db: Db) -> None:
    """A bad parameter set is an error of the same fix round; the passed page is stored and
    `agent.completed` refers to it; a retry of the job finds it and calls nothing."""
    learner_id = await create_learner(pg_db)
    request_id = await _requested(pg_db, learner_id)
    svg = VizBuilderOutput(
        format="svg", content="<svg></svg>", parameter_values=[], answer_action=None
    )
    gateway = _Gateway([_page(values=["[1, 2]"]), svg])
    compute = _Compute([[GOOD]])
    store = InMemoryObjectStore()

    done = await _build(
        pg_db, gateway, compute, store, learner_id=learner_id, request_id=request_id
    )

    assert "not a flat object" in gateway.calls[1]["variables"]["previous_errors"]
    assert len(compute.requests) == 1  # the bad set is not rendered
    assert "<svg></svg>" in compute.requests[0].html
    assert done.payload["ok"] is True
    key = done.payload["result_ref"]
    assert key.startswith(f"learners/{learner_id}/viz/{request_id}-")
    assert b"<svg></svg>" in await store.get(key)

    again = await _build(
        pg_db, _Gateway([]), _Compute([]), store, learner_id=learner_id, request_id=request_id
    )
    assert again.seq == done.seq


async def test_viz_unknown_request(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    with pytest.raises(LookupError):
        await _build(
            pg_db,
            _Gateway([]),
            _Compute([]),
            InMemoryObjectStore(),
            learner_id=learner_id,
            request_id=uuid4(),
        )


def test_param_sets_and_libraries() -> None:
    sets, errors = param_sets(['{"a": 2, "on": true}', "not json", '{"a": {"b": 1}}'])
    assert sets == [{"a": 2, "on": True}]
    assert len(errors) == 2
    assert param_sets([]) == ([{}], [])
    assert libraries_block(SETTINGS) == NONE_LINE
    libs = SETTINGS.model_copy(
        update={"viz_static_base_url": "https://x/static/", "viz_libraries": {"D3": "/d3.js"}}
    )
    assert libraries_block(libs) == "D3: https://x/static/d3.js"
