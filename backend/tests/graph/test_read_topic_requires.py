"""Unit tests for `TopicRequires` confidence aggregation and model defaults."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from quack.config import Settings
from quack.graph.read.models import TopicRequires
from quack.graph.read.neo4j_reader import Neo4jGraphReader


class _FakeRecord:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def data(self) -> dict[str, Any]:
        return self._row


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> AsyncIterator[_FakeRecord]:
        async def _iter() -> AsyncIterator[_FakeRecord]:
            for row in self._rows:
                yield _FakeRecord(row)

        return _iter()


class _FakeSession:
    def __init__(self, learner_id: UUID, domain_id: UUID, rows: list[dict[str, Any]]) -> None:
        self.learner_id = str(learner_id)
        self.domain_id = str(domain_id)
        self.rows = rows
        self.queries: list[str] = []

    async def run(self, query: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        q = str(query)
        self.queries.append(q)
        if "OPTIONAL MATCH (n:Domain" in q:
            return _FakeResult([{"id": self.domain_id, "learner": self.learner_id}])
        if "projectedSeq" in q:
            return _FakeResult([{"seq": 10}])
        if "max(coalesce(r.confidence, 1.0)) AS confidence" in q:
            return _FakeResult(self.rows)
        raise AssertionError(f"Unexpected query: {q} with params={params}")


class _FakeDriver:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self, **_kwargs: Any) -> AsyncIterator[_FakeSession]:
        yield self._session


def test_topic_requires_model_confidence_default_and_bounds() -> None:
    a, b = uuid4(), uuid4()
    default_edge = TopicRequires(from_topic_id=a, to_topic_id=b, weight=1)
    assert default_edge.confidence == 1.0

    explicit_edge = TopicRequires(from_topic_id=a, to_topic_id=b, weight=2, confidence=0.5)
    assert explicit_edge.confidence == 0.5

    with pytest.raises(ValidationError):
        TopicRequires(from_topic_id=a, to_topic_id=b, weight=1, confidence=1.2)
    with pytest.raises(ValidationError):
        TopicRequires(from_topic_id=a, to_topic_id=b, weight=1, confidence=-0.1)


def test_neo4j_reader_topic_requires_reads_max_confidence() -> None:
    learner_id, domain_id = uuid4(), uuid4()
    t1, t2, t3 = uuid4(), uuid4(), uuid4()
    session = _FakeSession(
        learner_id,
        domain_id,
        [
            {"a": str(t1), "b": str(t2), "weight": 2, "confidence": 0.85},
            {"a": str(t2), "b": str(t3), "weight": 1, "confidence": 0.5},
        ],
    )
    reader = Neo4jGraphReader(_FakeDriver(session), Settings())  # type: ignore[arg-type]

    links = asyncio.run(reader.topic_requires(domain_id))
    assert links == [
        TopicRequires(from_topic_id=t1, to_topic_id=t2, weight=2, confidence=0.85),
        TopicRequires(from_topic_id=t2, to_topic_id=t3, weight=1, confidence=0.5),
    ]
    assert any("max(coalesce(r.confidence, 1.0)) AS confidence" in q for q in session.queries)
