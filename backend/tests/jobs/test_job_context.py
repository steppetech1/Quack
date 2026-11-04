"""ТЗ Ф4 §4.2, seam S1 (A — `JobContext`): the resources of `workers`."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from quack.config import Settings
from quack.jobs.runner import JobContext
from quack.runtime import close_job_context

# §4.2: `workers` get every resource but `email`.
WORKERS_FIELDS = {
    "settings",
    "db",
    "neo4j",
    "redis",
    "llm",
    "compute",
    "storage",
    "embeddings",
    "deletions",
}


def test_job_context_fields() -> None:
    assert {f.name for f in fields(JobContext)} == WORKERS_FIELDS


class _Closable:
    def __init__(self, name: str, closed: list[str]) -> None:
        self._name = name
        self._closed = closed

    async def _record(self) -> None:
        self._closed.append(self._name)

    aclose = close = dispose = _record


async def test_close_job_context_in_reverse_creation_order() -> None:
    closed: list[str] = []
    resources: dict[str, Any] = {
        name: _Closable(name, closed) for name in WORKERS_FIELDS - {"settings"}
    }
    context = JobContext(settings=Settings(), **resources)
    await close_job_context(context)
    assert closed == [
        "deletions",
        "embeddings",
        "storage",
        "compute",
        "llm",
        "redis",
        "neo4j",
        "db",
    ]
