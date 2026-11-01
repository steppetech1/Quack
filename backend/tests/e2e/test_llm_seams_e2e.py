"""E2E: seams of the LLM layer on the T-E1 journey (ТЗ Фазы 3 §8.6c `T-M3`, §8.2 `T-Q10`)."""

from __future__ import annotations

import contextvars
import re
from collections.abc import Callable
from typing import Any

import pytest

from quack.jobs.kinds import KIND_LLM_ROLES
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, JobHandler
from quack.llm.provider import ProviderRequest, ProviderResponse
from tests.e2e import system as stand
from tests.e2e.journey import run_journey
from tests.e2e.system import System

UUID_LIKE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)

# Output schema of a call → its role (one schema per role, `tests/llm/prompt_contracts.py`).
SCHEMA_ROLES = {
    "TeacherOutput": "teacher",
    "ExtractorOutputV2": "extractor",
    "EvaluatorOutput": "evaluator",
    "TaskGenOutput": "task_gen",
    "StructurerOutput": "structurer",
    "CuratorOutput": "curator",
    "DiagnosticOutput": "diagnostic",
    "ArtifactGenOutput": "artifacts",
}

_KIND: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_kind", default=None)


def _tagged(kind: str, handler: JobHandler) -> JobHandler:
    async def run(job: ClaimedJob, ctx: JobContext) -> None:
        token = _KIND.set(kind)
        try:
            await handler(job, ctx)
        finally:
            _KIND.reset(token)

    return run


async def _journey_with_job_calls(
    system: System, monkeypatch: pytest.MonkeyPatch, key: str
) -> list[tuple[str | None, ProviderRequest]]:
    """Run the T-E1 journey; every provider call is tagged with the job kind it ran in
    (`None` — the hot path or an API request)."""
    await system.stop_background()
    handlers: Callable[[], dict[str, JobHandler]] = stand.job_handlers
    monkeypatch.setattr(
        stand, "job_handlers", lambda: {k: _tagged(k, h) for k, h in handlers().items()}
    )
    tagged: list[tuple[str | None, ProviderRequest]] = []
    complete = system.llm.complete_structured

    async def recording(request: ProviderRequest) -> ProviderResponse:
        tagged.append((_KIND.get(), request))
        return await complete(request)

    monkeypatch.setattr(system.llm, "complete_structured", recording)
    system.start_background()
    await run_journey(system, key)
    return tagged


async def test_no_uuid_in_model_requests(system: System, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-M3 (WP14.1, Т-§4.5): on the T-E1 journey no request to the model, of any role, holds
    a string that looks like a UUID."""
    calls = await _journey_with_job_calls(system, monkeypatch, "tm3")
    assert calls
    for _kind, request in calls:
        for text in (request.prompt, request.repair_hint or ""):
            found = UUID_LIKE.findall(text)
            assert not found, f"{request.schema_name}: ids in the request {found}"


async def test_kind_llm_roles_match_the_calls(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-Q10, second half (WP5.3): every role a job handler calls on the T-E1 journey is in
    `KIND_LLM_ROLES` of its kind; `session.checkpoint` calls none (WP13)."""
    calls = await _journey_with_job_calls(system, monkeypatch, "tq10")
    called: dict[str, set[str]] = {}
    for kind, request in calls:
        if kind is not None:
            called.setdefault(kind, set()).add(SCHEMA_ROLES[request.schema_name])
    ran = {start.kind for start in system.job_starts}
    assert "turn.extract" in ran, ran
    for kind, roles in called.items():
        assert roles <= set(KIND_LLM_ROLES[kind]), (kind, roles, KIND_LLM_ROLES[kind])
    assert called.get("turn.extract") == {"extractor"}, called
    assert "session.checkpoint" not in called, called
    details: dict[str, Any] = {k: sorted(v) for k, v in called.items()}
    assert set(details) <= set(KIND_LLM_ROLES), details
