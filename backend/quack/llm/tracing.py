"""Tracing of gateway calls in LangSmith (ТЗ Ф4 WP10.6, Т-§11.7, Д-§7, Д-§11).

One trace per attempt of a model call (the gateway writes one ledger row per attempt and the
trace goes with it): role, prompt version `role@N`, the pseudonym of the learner, model, tokens,
latency, status. No text of the prompt or of the answer is sent: the trace is a metric, not a
copy of the dialogue (D-B-200). The learner is named only by the pseudonym — UUIDv5 of the
`learner_id` in the namespace `langsmith_pseudonym_ns` (ТЗ Ф4 §2.3 row 20).

Tracing is background work: a failure of a tracer never changes the course of a call (law 7 by
meaning); the gateway swallows it. The HTTP tracer sends the run in a background task, so the
call does not wait for LangSmith either.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4, uuid5

import httpx

from quack.config import Settings
from quack.logging import get_logger

log = get_logger(__name__)

# Processes that call models and hold the key (Д-§7).
TRACING_ROLES = frozenset({"api", "workers"})


def learner_pseudonym(namespace: str, learner_id: UUID) -> str:
    """The id of the learner in traces (ТЗ Ф4 §2.3 row 20): the same learner → the same string;
    the way back is only through our database."""
    return str(uuid5(UUID(namespace), str(learner_id)))


@dataclass(frozen=True)
class TraceRecord:
    role: str
    prompt: str  # `role@N`; `role@0` for a non-text role
    pseudonym: str
    model: str
    status: str  # ok | error | incomplete | schema_error
    error_code: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    latency_ms: int
    ended_at: datetime


class Tracer(Protocol):
    """Interface of the tracing client (ТЗ Ф4 §4.2, WP10.6). Test double:
    `tests/support/tracer.py::RecordingTracer`."""

    async def trace(self, record: TraceRecord) -> None: ...

    async def delete_learner(self, pseudonym: str) -> bool:
        """Delete the traces of one learner by the pseudonym (WP25); `False` — the API does not
        allow it, the traces expire by `langsmith_retention_days` (D-B-202)."""
        ...

    async def aclose(self) -> None: ...


class NullTracer:
    """Tracing is off: tests, profile `loadtest`, processes without the key (Д-§11)."""

    async def trace(self, record: TraceRecord) -> None:
        return None

    async def delete_learner(self, pseudonym: str) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class LangSmithTracer:
    """LangSmith over its REST API with `httpx` (the library `langsmith` is allowed by §4.6 but
    is not in `pyproject.toml`; no new dependency, D-B-201): `POST {endpoint}/runs`."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.langsmith_endpoint.rstrip("/"),
            headers={"x-api-key": settings.langsmith_api_key.get_secret_value()},
            timeout=settings.langsmith_timeout_s,
        )
        self._pending: set[asyncio.Task[None]] = set()

    async def trace(self, record: TraceRecord) -> None:
        task = asyncio.create_task(self._send(record))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _send(self, record: TraceRecord) -> None:
        try:
            response = await self._client.post("/runs", json=self._payload(record))
            response.raise_for_status()
        except Exception as exc:
            log.warning("langsmith_trace_failed", error=type(exc).__name__)

    def _payload(self, record: TraceRecord) -> dict[str, object]:
        started = record.ended_at - timedelta(milliseconds=record.latency_ms)
        return {
            "id": str(uuid4()),
            "name": record.role,
            "run_type": "llm",
            "start_time": started.astimezone(UTC).isoformat(),
            "end_time": record.ended_at.astimezone(UTC).isoformat(),
            "session_name": self._settings.langsmith_project,
            "error": record.error_code if record.status != "ok" else None,
            "inputs": {},
            "outputs": {},
            "tags": [record.role, record.prompt],
            "extra": {
                "metadata": {
                    "role": record.role,
                    "prompt_version": record.prompt,
                    "learner": record.pseudonym,
                    "ls_model_name": record.model,
                    "status": record.status,
                    "usage": {
                        "input_tokens": record.input_tokens,
                        "output_tokens": record.output_tokens,
                        "cached_tokens": record.cached_tokens,
                        "reasoning_tokens": record.reasoning_tokens,
                    },
                }
            },
        }

    async def delete_learner(self, pseudonym: str) -> bool:
        # [ПРОВЕРИТЬ] the LangSmith API of deleting runs by a metadata value is not confirmed
        # (D-B-202): no call is made, the traces expire by `langsmith_retention_days`.
        log.warning(
            "langsmith_delete_unsupported", retention_days=self._settings.langsmith_retention_days
        )
        return False

    async def aclose(self) -> None:
        if self._pending:
            await asyncio.wait(self._pending, timeout=self._settings.langsmith_timeout_s)
        await self._client.aclose()


def create_tracer(settings: Settings) -> Tracer:
    """`LangSmithTracer` only in `api` and `workers` with a key, outside `test` (Д-§7, Д-§11);
    otherwise `NullTracer`. The profile `loadtest` does not pass the key."""
    if (
        settings.env == "test"
        or settings.process_role not in TRACING_ROLES
        or not settings.langsmith_api_key.get_secret_value()
    ):
        return NullTracer()
    return LangSmithTracer(settings)


def now() -> datetime:
    return datetime.now(UTC)
