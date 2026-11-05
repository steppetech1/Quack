"""T-OPS (ТЗ Ф4 WP16.7, S15): `ops metrics` and `ops replay-session` call the functions of
stream B with the right arguments. The database, Neo4j and the model are replaced; what is
checked is the wiring of the command (issue #209 item 3), not the metrics themselves."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from quack import cli
from quack.config import Settings
from quack.prompts.registry import PromptNotFoundError

SESSION = uuid4()
LEARNER = uuid4()
_REPLAY = ["ops", "replay-session", "--session", str(SESSION), "--teacher", "teacher@2"]


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Wiring:
    """Substitutes for everything `cli` opens; records what the command passed on."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.db = _Closable()
        self.driver = _Closable()
        self.readers: list[dict[str, Any]] = []
        self.calls: dict[str, Any] = {}

        async def open_db(settings: Settings, **_: Any) -> _Closable:
            return self.db

        def create_driver(settings: Settings) -> _Closable:
            return self.driver

        def create_graph_reader(neo4j: Any, settings: Settings, **kw: Any) -> str:
            self.readers.append({"neo4j": neo4j, "settings": settings, **kw})
            return "reader"

        monkeypatch.setattr(cli, "open_db", open_db)
        monkeypatch.setattr(cli, "create_driver", create_driver)
        monkeypatch.setattr(cli, "create_graph_reader", create_graph_reader)


def _settings(key: str = "k") -> Settings:
    return Settings(_env_file=None, env="test", meta_api_key=SecretStr(key))  # type: ignore[call-arg]


def test_parser_of_the_p4_ops_commands() -> None:
    parser = cli.build_parser()
    m = parser.parse_args(["ops", "metrics", "--since", "2026-11-01T12:00:00+03:00"])
    assert m.op == "metrics"
    assert m.since == datetime.fromisoformat("2026-11-01T09:00:00+00:00")
    naive = parser.parse_args(["ops", "metrics", "--since", "2026-11-01"])
    assert naive.since == datetime(2026, 11, 1, tzinfo=UTC)  # a time without a zone is UTC
    r = parser.parse_args(
        ["ops", "replay-session", "--session", str(SESSION), "--teacher", "teacher@4"]
    )
    assert (r.op, r.session, r.teacher, r.renderer, r.out) == (
        "replay-session",
        SESSION,
        "teacher@4",
        None,
        None,
    )
    r = parser.parse_args([*_REPLAY, "--renderer", "2", "--out", "r.json"])
    assert (r.renderer, r.out) == (2, "r.json")
    for bad in (
        ["ops", "metrics"],
        ["ops", "metrics", "--since", "yesterday"],
        ["ops", "replay-session", "--session", "not-a-uuid", "--teacher", "teacher@1"],
        ["ops", "replay-session", "--session", str(SESSION)],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(bad)


async def test_ops_metrics_prints_json_and_binds_the_reader(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wiring = _Wiring(monkeypatch)
    settings = _settings()
    since = datetime(2026, 11, 1, tzinfo=UTC)

    async def metrics(db: Any, when: datetime, *, settings: Settings, reader: Any) -> dict:
        wiring.calls["metrics"] = (db, when, settings)
        wiring.calls["reader"] = reader(LEARNER)  # the command's reader is bound to the db
        return {"since": when.isoformat(), "metrics": {"x": {"status": "measured", "value": 0.5}}}

    monkeypatch.setattr(cli, "metrics", metrics)
    args = cli.build_parser().parse_args(["ops", "metrics", "--since", "2026-11-01"])
    assert await cli._ops(settings, args) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["since"] == since.isoformat()
    assert out["metrics"]["x"]["value"] == 0.5
    assert wiring.calls["metrics"] == (wiring.db, since, settings)
    assert wiring.calls["reader"] == "reader"
    (reader,) = wiring.readers
    assert reader["neo4j"] is wiring.driver
    assert reader["learner_id"] == LEARNER
    assert reader["db"] is wiring.db
    assert wiring.db.closed and wiring.driver.closed


async def test_ops_replay_session_passes_the_documented_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiring = _Wiring(monkeypatch)
    settings = _settings()
    out = tmp_path / "report.json"
    judge = object()

    async def session_learner(db: Any, session_id: UUID) -> UUID | None:
        wiring.calls["session_learner"] = (db, session_id)
        return LEARNER

    async def replay_session(db: Any, **kw: Any) -> dict:
        wiring.calls["replay"] = (db, kw)
        return {"summary": {"turns": 2}, "turns": []}

    monkeypatch.setattr(cli, "session_learner", session_learner)
    monkeypatch.setattr(cli, "replay_session", replay_session)
    monkeypatch.setattr(cli, "release_judge", lambda gateway: judge)
    args = cli.build_parser().parse_args([*_REPLAY, "--renderer", "3", "--out", str(out)])
    assert await cli._ops(settings, args) == 0

    assert wiring.calls["session_learner"] == (wiring.db, SESSION)
    db, kw = wiring.calls["replay"]
    assert db is wiring.db
    assert (kw["learner_id"], kw["session_id"], kw["teacher"]) == (LEARNER, SESSION, "teacher@2")
    assert (kw["renderer"], kw["out"], kw["judge"], kw["settings"]) == (3, out, judge, settings)
    assert kw["llm"].settings is settings
    assert kw["reader"](LEARNER) == "reader"
    assert wiring.readers[0]["db"] is wiring.db and wiring.readers[0]["learner_id"] == LEARNER
    assert json.loads(capsys.readouterr().out)["summary"] == {"turns": 2}
    assert wiring.db.closed and wiring.driver.closed


async def test_ops_replay_session_default_out_is_in_the_working_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wiring = _Wiring(monkeypatch)
    monkeypatch.chdir(tmp_path)

    async def session_learner(db: Any, session_id: UUID) -> UUID:
        return LEARNER

    async def replay_session(db: Any, **kw: Any) -> dict:
        wiring.calls["out"] = kw["out"]
        return {"summary": {}}

    monkeypatch.setattr(cli, "session_learner", session_learner)
    monkeypatch.setattr(cli, "replay_session", replay_session)
    args = cli.build_parser().parse_args(
        ["ops", "replay-session", "--session", str(SESSION), "--teacher", "teacher@2"]
    )
    assert await cli._ops(_settings(), args) == 0
    assert wiring.calls["out"] == Path(f"replay-session-{SESSION}.json")


async def test_ops_replay_session_refusals_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No such session, an unknown prompt, no model key: exit code 2, nothing else replayed."""
    wiring = _Wiring(monkeypatch)
    state: dict[str, Any] = {"learner": None, "error": None, "replayed": 0}

    async def session_learner(db: Any, session_id: UUID) -> UUID | None:
        return state["learner"]

    async def replay_session(db: Any, **kw: Any) -> dict:
        state["replayed"] += 1
        if state["error"]:
            raise state["error"]
        return {"summary": {}}

    monkeypatch.setattr(cli, "session_learner", session_learner)
    monkeypatch.setattr(cli, "replay_session", replay_session)
    args = cli.build_parser().parse_args([*_REPLAY, "--out", str(tmp_path / "r.json")])
    assert await cli._ops(_settings(), args) == 2  # the session is unknown
    assert state["replayed"] == 0 and wiring.db.closed

    state["learner"] = LEARNER
    for error in (PromptNotFoundError("teacher@2"), ValueError("a prompt is named `name@N`")):
        state["error"] = error
        assert await cli._ops(_settings(), args) == 2

    state["error"] = None
    state["replayed"] = 0
    assert await cli._ops(_settings(key=""), args) == 2  # the replay calls the model
    assert state["replayed"] == 0


def test_main_runs_the_ops_p4_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` takes the new commands through the same path as the other `ops` commands."""
    seen: list[Any] = []

    async def fake_ops(settings: Settings, args: Any) -> int:
        seen.append(args)
        return 0

    monkeypatch.setattr(cli, "get_settings", lambda: _settings())
    monkeypatch.setattr(cli, "_ops", fake_ops)
    assert cli.main(["ops", "metrics", "--since", "2026-11-01"]) == 0
    assert cli.main(["ops", "replay-session", "--session", str(SESSION), "--teacher", "t@1"]) == 0
    assert [a.op for a in seen] == ["metrics", "replay-session"]
    assert isinstance(seen[1].session, UUID)  # parsed, not a string
