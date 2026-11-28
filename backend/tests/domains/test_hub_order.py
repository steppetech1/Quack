"""`domain_hubs` lists the hubs in the order they appeared; hubs without the number go last."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.domains import graph_reads
from quack.graph.read.models import HubGoal


def _goal(seq: int | None) -> HubGoal:
    return HubGoal(
        hub_id=uuid4(), name=f"h{seq}", kind="material", control_type="no_goal", created_seq=seq
    )


async def test_domain_hubs_are_ordered_by_created_seq(monkeypatch: pytest.MonkeyPatch) -> None:
    goals = [_goal(None), _goal(30), _goal(5), _goal(None), _goal(12)]

    class _Reader:
        async def domain_goals(self, domain_id: UUID) -> list[HubGoal]:
            return goals

    async def _own(*args: Any, **kwargs: Any) -> str:
        return "domain"

    monkeypatch.setattr(graph_reads, "_own", _own)
    monkeypatch.setattr(graph_reads, "create_graph_reader", lambda *a, **k: _Reader())
    got = await graph_reads.domain_hubs(None, None, uuid4(), uuid4())  # type: ignore[arg-type]
    assert got is not None
    assert [g.created_seq for g in got] == [5, 12, 30, None, None]
    assert [str(g.hub_id) for g in got[3:]] == sorted(str(g.hub_id) for g in got[3:])
