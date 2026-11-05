"""T-OPS (ТЗ Ф4 WP2.4, WP3.5, WP16.7; issue #232): `ops event-registry`, `ops can-rollback`,
`ops rebuild` and `ops extraction-eval` call the functions of streams A and B with the right
arguments. The database, Neo4j, Redis and the model are replaced; what is checked is the wiring
of the commands, not the functions themselves."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr

from quack import cli
from quack.config import Settings
from quack.graph.projection.rebuild import RebuildOutcome
from quack.runtime.event_registry import event_registry, registry_gaps

A, B, C = (UUID(int=n) for n in (1, 2, 3))


def _settings(**update: Any) -> Settings:
    base: dict[str, Any] = {"env": "test", "meta_api_key": SecretStr("k")}
    return Settings(_env_file=None, **{**base, **update})  # type: ignore[call-arg]


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class _FakeDb(_Closable):
    """`Db` that answers `fetch_all` from a table of queries and remembers what it was asked."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows
        self.queries: list[str] = []

    async def fetch_all(self, query: str, params: Any = None, **_: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        return self.rows


class _Wiring:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
        self.db = _FakeDb(rows)
        self.neo4j = _Closable()
        self.redis = _Closable()
        monkeypatch.setattr(cli, "open_db", self._open_db)
        monkeypatch.setattr(cli, "create_driver", lambda settings: self.neo4j)
        monkeypatch.setattr(cli, "create_redis", lambda settings: self.redis)

    async def _open_db(self, settings: Settings, **_: Any) -> _FakeDb:
        return self.db


# --- WP2.4: the registry of the image is the registry of Phase 4 --------------------------


def test_event_registry_command_writes_the_phase4_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _settings())
    out = tmp_path / "registry.json"
    assert cli.main(["ops", "event-registry", "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == event_registry()
    assert written["rules_version"] == "rules@3"
    assert out.read_text(encoding="utf-8").endswith("\n")


async def test_can_rollback_reads_the_seen_pairs_from_db(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`seen_pairs` takes the `Db` (not a session factory); exit 0 / 1 as the verdict says."""
    current = event_registry()
    dropped = current["events"][-1]
    previous = {**current, "events": current["events"][:-1]}
    pair = (dropped["type"], dropped["schema_version"])
    wiring = _Wiring(monkeypatch, [])
    parser = cli.build_parser()

    args = parser.parse_args(["ops", "can-rollback", "--to-registry", "unused.json"])
    args.previous = previous
    assert await cli._ops(_settings(), args) == 0  # a new pair, never written: safe
    safe = json.loads(capsys.readouterr().out)
    assert safe["can_rollback"] is True and safe["new_pairs"] == [dropped]
    assert "ops.event_types_seen" in wiring.db.queries[0] and wiring.db.closed

    wiring = _Wiring(monkeypatch, [{"type": pair[0], "schema_version": pair[1]}])
    assert await cli._ops(_settings(), args) == 1  # written at least once: refused
    refused = json.loads(capsys.readouterr().out)
    assert refused["can_rollback"] is False and refused["new_pairs_seen"] == [dropped]
    assert wiring.db.closed

    wiring = _Wiring(monkeypatch, [])
    args.previous = {**previous, "rules_version": "rules@2"}
    assert await cli._ops(_settings(), args) == 1  # other rules
    assert json.loads(capsys.readouterr().out)["rules_changed"] is True


def test_main_reads_the_previous_registry_of_can_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Any] = []

    async def fake_ops(settings: Settings, args: Any) -> int:
        seen.append(args.previous)
        return 0

    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps(event_registry()), encoding="utf-8")
    monkeypatch.setattr(cli, "get_settings", lambda: _settings())
    monkeypatch.setattr(cli, "_ops", fake_ops)
    assert cli.main(["ops", "can-rollback", "--to-registry", str(previous)]) == 0
    assert seen == [event_registry()]


def test_every_catalog_type_has_a_handler_and_a_rule() -> None:
    """Д-§5.2 п.6, T-O6 (replaces the check of the registry of Phase 3): the «Проектор» and
    «Диспетчер» columns of the catalog against the code."""
    assert registry_gaps() == []


# --- WP3.5: `ops rebuild` on rules@3 -----------------------------------------------------


class _Rebuilds:
    """Replaces the rebuild of Phase 4; records the calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, wiring: _Wiring) -> None:
        self.wiring = wiring
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.statuses: dict[UUID, str] = {}
        self.stale: list[tuple[Any, str]] = []
        self.stale_ids: list[str] = []

        async def rebuild_p4(*args: Any, **kw: Any) -> RebuildOutcome:
            self.calls.append((args, kw))
            return RebuildOutcome(self.statuses.get(args[3], "rebuilt"))  # type: ignore[arg-type]

        async def stale_learners(neo4j: Any, version: str) -> list[str]:
            self.stale.append((neo4j, version))
            return self.stale_ids

        monkeypatch.setattr(cli, "rebuild_learner", rebuild_p4)
        monkeypatch.setattr(cli, "stale_learners", stale_learners)


def _rebuild_args(*extra: str) -> Any:
    return cli.build_parser().parse_args(["ops", "rebuild", *extra])


async def test_rebuild_phase4_one_learner(monkeypatch: pytest.MonkeyPatch) -> None:
    wiring = _Wiring(monkeypatch, [])
    rebuilds = _Rebuilds(monkeypatch, wiring)
    settings = _settings()
    assert await cli._ops(settings, _rebuild_args("--learner", str(A))) == 0
    ((args, kw),) = rebuilds.calls
    assert args == (wiring.db, wiring.neo4j, settings, A)
    assert kw == {"redis": wiring.redis}
    assert wiring.db.closed and wiring.neo4j.closed and wiring.redis.closed


async def test_rebuild_phase4_stale_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    wiring = _Wiring(monkeypatch, [])
    rebuilds = _Rebuilds(monkeypatch, wiring)
    rebuilds.stale_ids = [str(B), str(C)]
    assert await cli._ops(_settings(), _rebuild_args("--stale-rules")) == 0
    assert rebuilds.stale == [(wiring.neo4j, "rules@3")]
    assert [args[3] for args, _ in rebuilds.calls] == [B, C]


async def test_rebuild_phase4_all_reads_core_learners(monkeypatch: pytest.MonkeyPatch) -> None:
    wiring = _Wiring(monkeypatch, [{"learner_id": A}, {"learner_id": str(B)}])
    rebuilds = _Rebuilds(monkeypatch, wiring)
    assert await cli._ops(_settings(), _rebuild_args("--all")) == 0
    (query,) = wiring.db.queries
    assert "core.learners" in query and "ORDER BY learner_id" in query
    assert [args[3] for args, _ in rebuilds.calls] == [A, B]


async def test_rebuild_phase4_outcomes_set_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every learner is tried; anything but `rebuilt` (busy, active session, failed) is exit 1."""
    for status in ("busy", "active_session", "failed"):
        wiring = _Wiring(monkeypatch, [{"learner_id": A}, {"learner_id": B}])
        rebuilds = _Rebuilds(monkeypatch, wiring)
        rebuilds.statuses = {A: status}
        code = await cli._ops(_settings(), _rebuild_args("--all"))
        assert code == 1, status
        assert len(rebuilds.calls) == 2  # B was rebuilt after A did not


async def test_rebuild_needs_a_target(monkeypatch: pytest.MonkeyPatch) -> None:
    wiring = _Wiring(monkeypatch, [])
    rebuilds = _Rebuilds(monkeypatch, wiring)
    assert await cli._ops(_settings(), _rebuild_args()) == 2
    assert rebuilds.calls == []


# --- WP16.7, S15: `ops extraction-eval` ---------------------------------------------------

_EVAL = ["ops", "extraction-eval", "--set", "set.json", "--learner", str(A)]


def test_parser_of_extraction_eval() -> None:
    parser = cli.build_parser()
    e = parser.parse_args(_EVAL)
    assert (e.op, e.set, e.learner, e.prompt, e.runs, e.out, e.against) == (
        "extraction-eval",
        "set.json",
        A,
        None,
        None,
        None,
        None,
    )
    e = parser.parse_args([*_EVAL, "--prompt", "extractor@4", "--runs", "3", "--out", "r.json"])
    assert (e.prompt, e.runs, e.out) == ("extractor@4", 3, "r.json")
    assert parser.parse_args([*_EVAL, "--against", "old.json"]).against == "old.json"
    for bad in (
        ["ops", "extraction-eval", "--learner", str(A)],
        ["ops", "extraction-eval", "--set", "s.json"],
        ["ops", "extraction-eval", "--set", "s.json", "--learner", "nope"],
        [*_EVAL, "--runs", "many"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(bad)


class _Eval:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, report: dict[str, Any]) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.loaded: list[Path] = []
        self.eval_set = object()

        def load_eval_set(path: Path) -> object:
            self.loaded.append(path)
            return self.eval_set

        async def evaluate_extraction(gateway: Any, settings: Settings, eval_set: Any, **kw: Any):
            self.calls.append((gateway, {"settings": settings, "set": eval_set, **kw}))
            return report

        monkeypatch.setattr(cli, "load_eval_set", load_eval_set)
        monkeypatch.setattr(cli, "evaluate_extraction", evaluate_extraction)


def _measured(value: float) -> dict[str, Any]:
    return {"status": "measured", "value": value}


def _report(precision: float, kappa: float = 0.8) -> dict[str, Any]:
    return {
        "dataset": "d",
        "fingerprint": "f",
        "prompt": "extractor@3",
        "precision": _measured(precision),
        "kappa": _measured(kappa),
        "recall": _measured(0.5),
        "code_rejections": _measured(0.1),
    }


async def test_extraction_eval_passes_the_documented_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiring = _Wiring(monkeypatch, [])
    ev = _Eval(monkeypatch, _report(0.9))
    settings = _settings()
    out = tmp_path / "report.json"
    args = cli.build_parser().parse_args(
        [*_EVAL, "--prompt", "extractor@4", "--runs", "2", "--out", str(out)]
    )
    assert await cli._ops(settings, args) == 0
    ((gateway, kw),) = ev.calls
    assert ev.loaded == [Path("set.json")]
    assert kw["set"] is ev.eval_set and kw["settings"] is settings
    assert (kw["learner_id"], kw["prompt"], kw["runs"], kw["out"]) == (A, "extractor@4", 2, out)
    assert gateway.settings is settings
    assert json.loads(capsys.readouterr().out)["precision"]["value"] == 0.9
    assert wiring.db.closed


async def test_extraction_eval_defaults_are_left_to_the_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Wiring(monkeypatch, [])
    ev = _Eval(monkeypatch, _report(0.9))
    assert await cli._ops(_settings(), cli.build_parser().parse_args(_EVAL)) == 0
    ((_, kw),) = ev.calls
    assert (kw["prompt"], kw["runs"], kw["out"]) == (None, None, None)


async def test_extraction_eval_refusals_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No model key, an unreadable or invalid set, a bad prompt or run count: exit code 2."""
    wiring = _Wiring(monkeypatch, [])
    ev = _Eval(monkeypatch, _report(0.9))
    args = cli.build_parser().parse_args(_EVAL)
    assert await cli._ops(_settings(meta_api_key=SecretStr("")), args) == 2
    assert ev.loaded == [] and ev.calls == []

    def broken(path: Path) -> object:
        raise OSError("no such file")

    monkeypatch.setattr(cli, "load_eval_set", broken)
    assert await cli._ops(_settings(), args) == 2
    assert ev.calls == []

    monkeypatch.setattr(cli, "load_eval_set", lambda path: object())

    async def refuses(*a: Any, **kw: Any) -> dict[str, Any]:
        raise ValueError("runs must be at least 1")

    monkeypatch.setattr(cli, "evaluate_extraction", refuses)
    assert await cli._ops(_settings(), args) == 2
    assert wiring.db.closed


async def test_extraction_eval_against_is_the_gate_of_a_new_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--against OLD`: the comparison of Т-§18.4 is printed beside the report; a drop of the
    precision or the consistency is exit code 1, no drop is 0."""
    _Wiring(monkeypatch, [])
    old = tmp_path / "old.json"
    old.write_text(json.dumps(_report(0.9)), encoding="utf-8")
    args = cli.build_parser().parse_args([*_EVAL, "--against", str(old)])

    _Eval(monkeypatch, _report(0.95))
    assert await cli._ops(_settings(), args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["comparison"]["regression"] is False
    assert printed["comparison"]["metrics"]["precision"]["delta"] == pytest.approx(0.05)

    _Eval(monkeypatch, _report(0.8))
    assert await cli._ops(_settings(), args) == 1
    assert json.loads(capsys.readouterr().out)["comparison"]["regression"] is True

    other = {**_report(0.99), "fingerprint": "another set"}
    _Eval(monkeypatch, other)
    assert await cli._ops(_settings(), args) == 1  # not comparable is not «passed» (law 8)
    assert json.loads(capsys.readouterr().out)["comparison"]["regression"] is None
