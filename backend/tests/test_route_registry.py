"""The registry of routes of the contract tests (D-C-147): it is consistent, so the shared tests
`T-A1`, `T-E2`, `T-E3` can trust it. No infrastructure is needed."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import pytest

from tests.e2e.routes import (
    Ids,
    all_routes,
    body_for,
    expected_routes,
    id_defaults,
    path_for,
    streams,
    walk,
)

PARAM = re.compile(r"\{([^}]+)\}")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def test_streams_are_found_and_do_not_share_ids() -> None:
    assert {s.name for s in streams()} >= {"a", "b", "c"}
    assert id_defaults()  # raises when two streams register the same id


def test_a_route_belongs_to_one_stream_and_has_a_known_method() -> None:
    keys = [r.key for s in streams() for r in s.routes]
    assert len(keys) == len(set(keys)), sorted({k for k in keys if keys.count(k) > 1})
    assert {m for m, _ in keys} <= METHODS
    others = {key for s in streams() for key in s.other_routes}
    assert not others & set(keys), sorted(others & set(keys))
    assert len(expected_routes()) == len(keys) + len(others)


def test_every_walked_route_has_a_stage_and_every_keyless_route_a_reason() -> None:
    for route in all_routes().values():
        if route.no_key is None:
            assert route.stage is not None, f"{route.key}: a walked route needs a `stage`"
            assert route.body is not None, f"{route.key}: a walked route needs a `body`"
            assert route.files is None, f"{route.key}: a multipart route has no key to walk"
        else:
            assert route.no_key.strip(), f"{route.key}: `no_key` is a ТЗ reason"
            assert route.stage is None, f"{route.key}: a keyless route is not walked"
        if route.no_other_body is not None:
            assert route.no_other_body.strip(), f"{route.key}: `no_other_body` is a reason"
            assert route.no_key is None, f"{route.key}: no key, so no 'other body' check"
    stages = [r.stage for r in walk()]
    assert stages == sorted(s for s in stages if s is not None)


def test_t_e3_can_build_every_request() -> None:
    """T-E3 sends every mutating route with `{id}`: a body or files must exist."""
    for route in all_routes().values():
        if "{" in route.path:
            assert route.body is not None or route.files is not None, route.key


def test_paths_and_flags_use_registered_ids() -> None:
    known = set(id_defaults())
    for route in all_routes().values():
        params = set(PARAM.findall(route.path))
        sources = {route.path_ids.get(p, p) for p in params}
        assert sources <= known, f"{route.key}: unknown ids {sorted(sources - known)}"
        assert set(route.path_ids) <= params, f"{route.key}: `path_ids` names no parameter"
        assert set(route.requires) <= known, f"{route.key}: unknown `requires`"
        assert set(route.restore) <= known, f"{route.key}: unknown `restore`"


def test_bodies_and_paths_build_for_both_variants() -> None:
    # Distinct ids, so that two variants do not coincide for lack of a different id.
    ids = Ids(
        **{
            name: UUID(int=n + 1)
            for n, (name, default) in enumerate(id_defaults().items())
            if isinstance(default, UUID)
        }
    )
    for route in all_routes().values():
        assert "{" not in path_for(*route.key, ids), route.key
        if route.body is None:
            continue
        bodies: list[dict[str, Any]] = [body_for(*route.key, ids, "k", v) for v in (0, 1)]
        if route.no_key is None and route.no_other_body is None:
            assert bodies[0] != bodies[1], f"{route.key}: variant 1 must differ from variant 0"
        has_key = "idempotency_key" in bodies[0]
        assert has_key == (route.body_key != "none"), route.key


def test_ids_reject_a_name_nobody_registered() -> None:
    with pytest.raises(TypeError):
        Ids(no_such_id=1)
    with pytest.raises(AttributeError):
        _ = Ids().no_such_id
    assert Ids().is_default("topic_id")
    assert not Ids(topic_id=UUID(int=1)).is_default("topic_id")
