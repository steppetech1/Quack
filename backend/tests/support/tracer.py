"""`Tracer` test double (ТЗ Ф4 §8.1, WP10.6): a log of the traces, no network. `fail=True` makes
every call raise, to check that a tracer never breaks a call of the gateway."""

from __future__ import annotations

from quack.llm.tracing import TraceRecord


class RecordingTracer:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[TraceRecord] = []
        self.deleted: list[str] = []
        self.fail = fail
        self.closed = False

    async def trace(self, record: TraceRecord) -> None:
        if self.fail:
            raise RuntimeError("tracer is down")
        self.records.append(record)

    async def delete_learner(self, pseudonym: str) -> bool:
        self.deleted.append(pseudonym)
        return True

    async def aclose(self) -> None:
        self.closed = True
