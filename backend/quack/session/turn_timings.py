"""Timings of the stages of the teacher turn (owner decision 2026-11-19: measure first, then decide
whether the context needs a preload). Numbers only, no text of the dialogue.

`TurnTimer` lives for one turn on a monotonic clock. The graph marks `admit_ms`,
`assemble_total_ms`, `first_status_ms` (the first `status: thinking`), `ttft_ms` (the first
`token` frame), `total_ms`
(up to the start of the commit) and counts `model_attempts`, `tool_steps`; the context source
adds the parts of the assembly (`TurnSetup.timings`, `timed`). Everything here is best effort
(law 7): a failed measurement is skipped, it never fails or changes the turn. Counted from the
moment the turn takes the session slot, so a turn that waits for the slot of a cancelled one shows
that wait as `slot_wait_ms`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Mapping

from quack.logging import get_logger

log = get_logger(__name__)

_NS_IN_MS = 1_000_000


def now_ns() -> int:
    """The monotonic clock of the measurements."""
    return time.perf_counter_ns()


def since_ms(start_ns: int) -> int:
    """Whole milliseconds from `start_ns`, never negative."""
    return max(0, (now_ns() - start_ns) // _NS_IN_MS)


def put_since(parts: dict[str, int], name: str, start_ns: int) -> None:
    """`parts[name]` = milliseconds since `start_ns`; a failure is skipped."""
    try:
        parts[name] = since_ms(start_ns)
    except Exception as exc:  # a measurement never fails the turn
        log.warning("turn_timing_failed", name=name, error=repr(exc))


async def timed[T](parts: dict[str, int], name: str, work: Awaitable[T]) -> T:
    """Await `work` and record how long it took under `name`, also when it raises."""
    start = now_ns()
    try:
        return await work
    finally:
        put_since(parts, name, start)


class TurnTimer:
    """The marks of one turn: milliseconds from the creation of the timer."""

    def __init__(self) -> None:
        self._start = now_ns()
        self.marks: dict[str, int] = {}
        self.model_attempts = 0
        self.tool_steps = 0

    def mark_once(self, name: str) -> None:
        """Milliseconds from the start to the first call with this name."""
        if name not in self.marks:
            put_since(self.marks, name, self._start)

    def put_since(self, name: str, start_ns: int) -> None:
        put_since(self.marks, name, start_ns)

    def snapshot(self, *, guard_retries: int) -> dict[str, int]:
        """All numbers of the turn up to now (`total_ms` is up to the start of the commit)."""
        try:
            found = {
                **self.marks,
                "total_ms": since_ms(self._start),
                "model_attempts": self.model_attempts,
                "guard_retries": guard_retries,
                "tool_steps": self.tool_steps,
            }
            return {k: max(0, int(v)) for k, v in found.items()}
        except Exception as exc:
            log.warning("turn_timing_failed", name="snapshot", error=repr(exc))
            return {}


def merged(*parts: Mapping[str, int]) -> dict[str, int]:
    """The numbers of several sources in one mapping; the later wins."""
    found: dict[str, int] = {}
    for part in parts:
        found.update(part)
    return found
