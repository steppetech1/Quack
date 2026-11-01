"""Property tests of the projector (Т-§2.5, Д-§5.2 п.3, R16): applying the log in batches of any
size gives the same graph as a rebuild. Hypothesis draws which events of the full scenario (all
event types) are in the log and how the log is split into batches; in memory, no infrastructure.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from quack.config import FoldParams
from quack.events.models import StoredEvent
from quack.graph.memory import InMemoryGraph
from quack.graph.projector import project_in_memory, run_projector_batch
from tests.scenario import build_scenario

P = FoldParams()
LOG, _ = build_scenario()
EVENTS: list[StoredEvent] = LOG.events
LEARNER: UUID = LOG.learner_id


async def _batched(events: list[StoredEvent], sizes: list[int]) -> InMemoryGraph:
    graph = InMemoryGraph()
    i, k = 0, 0
    while i < len(events):
        size = sizes[k % len(sizes)]
        outcome = await run_projector_batch(LEARNER, events[i : i + size], graph, P)
        assert outcome.status == "applied"
        i += size
        k += 1
    return graph


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    keep=st.lists(st.booleans(), min_size=len(EVENTS), max_size=len(EVENTS)),
    sizes=st.lists(st.integers(min_value=1, max_value=12), min_size=1, max_size=20),
)
def test_batches_equal_rebuild_on_any_sublog(keep: list[bool], sizes: list[int]) -> None:
    """Any order-preserving part of the log (references to events left out are applied as empty,
    Т-§2.4) projected in batches of the drawn sizes equals its rebuild in one pass. The part is
    renumbered without gaps: a learner's log has contiguous `seq`."""
    kept = [e for e, k in zip(EVENTS, keep, strict=True) if k]
    events = [e.model_copy(update={"seq": i}) for i, e in enumerate(kept, start=1)]
    if not events:
        return

    async def check() -> None:
        batched = await _batched(events, sizes)
        rebuilt = await project_in_memory(LEARNER, events, P, batch_size=len(events))
        assert batched.snapshot() == rebuilt.snapshot()

    asyncio.run(check())
