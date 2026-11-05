"""Registry of routes for the contract tests of stream C (`T-A1`, `T-E2`, `T-E3`, D-C-147).

Every stream describes its own routes in its own module of this package (`a.py`, `b.py`,
`c.py`; a new file is picked up by name). A module has one object, `STREAM`. The shared tests
never list a route: they collect the streams.

- `T-A1` (`tests/test_app_assembly.py`): the mounted routes equal the union of `Route` entries and
  `other_routes` of all streams, and every router named in `routers` declares only mounted routes.
- `T-E2` (`test_isolation_and_idempotency.py`): every mounted mutating `/v1` route is either a
  `Route` with `no_key` (and a reason) or a `Route` of the walk; the walk goes in the order of
  `stage`.
- `T-E3`: every mounted route with `{id}` in the path gets the ids of another learner and must
  answer 404; the path comes from `path_for`, the body from `body_for` or `Route.files`.

How to add a route: `README.md` of this package.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import KW_ONLY, dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

if TYPE_CHECKING:
    from tests.e2e.system import Learner, System

# An id nobody has: what the walks use for objects they have not created (yet).
NO_ID = UUID(int=0)

type RouteKey = tuple[str, str]
type BodyFactory = Callable[[Ids, int], dict[str, Any]]
type Prepare = Callable[[System, Learner, Ids], Awaitable[Ids]]
type Advance = Callable[[System, Learner, Ids, Any], Awaitable[Ids]]
type Files = Mapping[str, tuple[str, bytes, str]]


class Ids:
    """Objects of one learner that the bodies and paths refer to.

    The names are registered by the streams (`Stream.ids`, with the value a walk uses while the
    object is not created); `Ids(**given)` takes the rest from the registered defaults. Names
    read as attributes: `ids.topic_id`. An unregistered name is an error, not a new id.
    """

    def __init__(self, **given: Any) -> None:
        defaults = id_defaults()
        unknown = sorted(given.keys() - defaults.keys())
        if unknown:
            raise TypeError(f"ids not registered by any stream: {unknown}")
        self._values: dict[str, Any] = {**defaults, **given}

    def __getattr__(self, name: str) -> Any:
        values = self.__dict__.get("_values", {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __repr__(self) -> str:
        return f"Ids({self._values})"

    def replace(self, **changes: Any) -> Ids:
        return Ids(**{**self._values, **changes})

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def is_default(self, name: str) -> bool:
        """The object is not created yet: the value is the registered placeholder."""
        return bool(self._values[name] == id_defaults()[name])


@dataclass(frozen=True)
class Route:
    """One route of a stream, as the contract tests need it.

    `stage` puts the route into the T-E2 walk (global; ascending); a route with `no_key` is not
    walked and has no stage. `body` builds a valid body (without the key) for variant 0, and
    for variant 1 one that differs in a field other than the key; it is needed unless the route
    has `files` or (`no_key` and no `{id}` in the path), because T-E3 sends it.
    """

    method: str
    path: str
    _: KW_ONLY
    stage: int | None = None
    order: int = 0
    body: BodyFactory | None = None
    # How the idempotency key goes into the body: a string, a UUID of it (ТЗ Ф4 WP13.1), or not.
    body_key: Literal["text", "uuid", "none"] = "text"
    # The reason (a ТЗ rule) why the request carries no `idempotency_key` (ТЗ Фазы 3 §8.7, T-E2).
    no_key: str | None = None
    # The reason why "same key, other body" does not exist (the body has no other field).
    no_other_body: str | None = None
    # A multipart route: the files of a valid request.
    files: Files | None = None
    # The form fields next to `files` of a multipart route (the voice segment has its time).
    form: Mapping[str, str] | None = None
    # Ids the walk must have created before this step (their value is not the placeholder).
    requires: tuple[str, ...] = ()
    # Path parameter -> id name, where they differ (`session_id` -> `action_session_id`).
    path_ids: Mapping[str, str] = field(default_factory=dict)
    # Ids put back to the learner's onboarding values before this step.
    restore: tuple[str, ...] = ()
    # Creates what the step needs / takes the ids the next steps need from the response.
    prepare: Prepare | None = None
    advance: Advance | None = None

    @property
    def key(self) -> RouteKey:
        return (self.method, self.path)


@dataclass(frozen=True)
class Stream:
    """What one stream registers: its module in this package builds one of these as `STREAM`."""

    name: str
    routes: tuple[Route, ...] = ()
    # Routes that need no entry (reads, public sign-in, health): only the route-set contract.
    other_routes: frozenset[RouteKey] = frozenset()
    ids: Mapping[str, Any] = field(default_factory=dict)
    # `"module:attribute"` of routers whose declared routes `T-A1` compares with the mounted ones.
    routers: tuple[str, ...] = ()
    # Run once in T-E3 for the objects of the stream that routes refer to (graph nodes, events).
    isolation: tuple[Prepare, ...] = ()


@cache
def streams() -> tuple[Stream, ...]:
    """The `STREAM` of every module of this package, by module name."""
    found: list[Stream] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        stream = importlib.import_module(f"{__name__}.{info.name}").__dict__.get("STREAM")
        assert isinstance(stream, Stream), f"{__name__}.{info.name} has no STREAM"
        found.append(stream)
    return tuple(found)


@cache
def id_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for stream in streams():
        repeated = sorted(defaults.keys() & stream.ids.keys())
        assert not repeated, f"stream {stream.name} registers ids of another stream: {repeated}"
        defaults.update(stream.ids)
    return defaults


@cache
def all_routes() -> dict[RouteKey, Route]:
    routes: dict[RouteKey, Route] = {}
    for stream in streams():
        for route in stream.routes:
            assert route.key not in routes, f"{route.key} is registered twice"
            routes[route.key] = route
    return routes


def route_of(method: str, path: str) -> Route | None:
    return all_routes().get((method, path))


def walk() -> list[Route]:
    """The routes of the T-E2 walk, in order: by `stage`, then `order`, then the stream."""
    stream_of = {r.key: s.name for s in streams() for r in s.routes}
    walked = [r for r in all_routes().values() if r.no_key is None]
    assert all(r.stage is not None for r in walked), [r.key for r in walked if r.stage is None]
    return sorted(walked, key=lambda r: (r.stage or 0, r.order, stream_of[r.key], r.key))


def no_key_routes() -> dict[RouteKey, str]:
    """`(method, path)` without `idempotency_key`, with the reason."""
    return {r.key: r.no_key for r in all_routes().values() if r.no_key is not None}


def expected_routes() -> frozenset[RouteKey]:
    """The route-set contract (`T-A1`): every registered route and every `other_routes`."""
    others = {key for stream in streams() for key in stream.other_routes}
    return frozenset(all_routes().keys() | others)


def isolation_prepares() -> list[Prepare]:
    return [prepare for stream in streams() for prepare in stream.isolation]


def declared_routers() -> list[str]:
    return [spec for stream in streams() for spec in stream.routers]


def path_for(method: str, path: str, ids: Ids) -> str:
    route = route_of(method, path)
    values = ids.as_dict()
    if route is not None:
        values.update({param: values[name] for param, name in route.path_ids.items()})
    return path.format_map(values)


def body_for(method: str, path: str, ids: Ids, key: str, variant: int = 0) -> dict[str, Any]:
    """A valid body; `variant=1` differs from `variant=0` in a non-key field."""
    route = route_of(method, path)
    if route is None or route.body is None:
        raise KeyError((method, path))
    body = dict(route.body(ids, variant))
    if route.body_key == "uuid":
        body["idempotency_key"] = str(uuid5(NAMESPACE_URL, key))
    elif route.body_key == "text":
        body["idempotency_key"] = key
    return body


def form_for(method: str, path: str) -> Mapping[str, str] | None:
    route = route_of(method, path)
    return None if route is None else route.form


def files_for(method: str, path: str) -> Files | None:
    route = route_of(method, path)
    return None if route is None else route.files
