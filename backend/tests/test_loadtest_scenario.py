"""Logic of the Locust scenario (ТЗ Фазы 3 WP10.3, `Д-§11`), without importing Locust: Locust
patches the standard library with gevent on import."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from loadtest.scenario import (
    TURNS_PER_SESSION,
    Learner,
    load_learners,
    read_turn_stream,
)


def _learner_row() -> dict[str, object]:
    return {
        "learner_id": str(uuid4()),
        "token": "t",
        "domain_id": str(uuid4()),
        "topic_ids": [str(uuid4())],
    }


def test_learners_file_of_seed_learners(tmp_path: Path) -> None:
    """The file of `ops seed-learners` (WP8.6): a JSON list of
    `{learner_id, token, domain_id, topic_ids}`."""
    rows = [_learner_row(), _learner_row()]
    path = tmp_path / "learners.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    learners = load_learners(path)
    assert [str(x.learner_id) for x in learners] == [r["learner_id"] for r in rows]
    assert isinstance(learners[0], Learner)
    assert learners[0].headers == {"Authorization": "Bearer t"}


@pytest.mark.parametrize(
    "rows",
    [[], [{"learner_id": "x"}], [{**_learner_row(), "topic_ids": []}]],
)
def test_bad_learners_file_is_refused(tmp_path: Path, rows: list[object]) -> None:
    path = tmp_path / "learners.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_learners(path)


def _frames(*events: tuple[str, dict[str, object]]) -> list[bytes]:
    lines: list[bytes] = []
    for name, data in events:
        lines += [f"event: {name}".encode(), f"data: {json.dumps(data)}".encode(), b""]
    return lines


def test_turn_stream_times_status_and_first_token() -> None:
    """`teacher_status_p95_ms` and the time to the first `token` are measured from the start
    of the request; a stream with `done` is complete."""
    ticks = iter([0.0, 0.05, 0.10, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0])
    lines = _frames(
        ("turn_started", {"turn_id": "t1"}),
        ("status", {"state": "thinking"}),
        ("token", {"delta": "Hi"}),
        ("token", {"delta": "!"}),
        ("done", {}),
    )
    result = read_turn_stream(iter(lines), started_at=0.0, clock=lambda: next(ticks))
    assert result.complete
    assert result.turn_id == "t1"
    assert result.status_ms is not None and result.token_ms is not None
    assert 0 < result.status_ms < result.token_ms
    assert result.events == ["turn_started", "status", "token", "token", "done"]


def test_turn_stream_without_done_is_cut() -> None:
    lines = _frames(("turn_started", {"turn_id": "t1"}), ("token", {"delta": "Hi"}))
    result = read_turn_stream(iter(lines), started_at=0.0, clock=lambda: 1.0)
    assert not result.complete
    assert result.status_ms is None


def test_session_length_is_positive() -> None:
    assert TURNS_PER_SESSION >= 1
