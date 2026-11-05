"""Property test of the projector of Phase 4 (Т-§2.5, Д-§5.2 п.3, R16; D-A-228): applying any
order-preserving part of the log in batches of any size gives the same graph as applying it at
once. Hypothesis draws which events of the script of `graph/test_projection_batches.py` are in
the log and where it is cut into batches; in memory, no infrastructure. T-P1 there cuts the whole
script; this test also drops events, so a handler meets references to events that are not in the
log (Т-§2.4: they are applied as empty)."""

from __future__ import annotations

import asyncio

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.graph.test_projection_batches import (
    SCRIPT,
    _graph,  # pyright: ignore[reportPrivateUsage]
    _project,  # pyright: ignore[reportPrivateUsage]
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    keep=st.lists(st.booleans(), min_size=len(SCRIPT), max_size=len(SCRIPT)),
    cuts=st.lists(st.booleans(), min_size=len(SCRIPT), max_size=len(SCRIPT)),
)
def test_batches_equal_rebuild_on_any_sublog(keep: list[bool], cuts: list[bool]) -> None:
    """The part keeps the `seq` of the script (the events refer to each other by `seq`)."""
    events = [e for e, k in zip(SCRIPT, keep, strict=True) if k]
    if not events:
        return
    marks = cuts[: len(events) - 1]
    batched = asyncio.run(_project(events, marks))
    whole = asyncio.run(_project(events, [False] * len(marks)))
    assert _graph(batched) == _graph(whole)
