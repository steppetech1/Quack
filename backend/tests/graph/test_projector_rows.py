"""ТЗ Ф4 WP3.2, seam S5: a projector row for every type the projector handles (§6.3), and the
fields it reads exist in the payload models of the writers (B, C)."""

from __future__ import annotations

import types
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel

import quack.events.event_types  # noqa: F401  (registers the catalog before SPECS is read)
from quack.events.registry import SPECS, payload_model
from quack.graph.projector_rows import ROW_BY_TYPE, ROWS


def _models(annotation: Any) -> list[type[BaseModel]]:
    """The payload models an annotation leads to (through aliases, `Annotated`, unions, lists)."""
    if isinstance(annotation, typing.TypeAliasType):
        return _models(annotation.__value__)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    origin = get_origin(annotation)
    if origin is typing.Annotated:
        return _models(get_args(annotation)[0])
    if origin in (list, tuple, typing.Union, types.UnionType):
        return [m for arg in get_args(annotation) for m in _models(arg)]
    return []


def _resolves(model: type[BaseModel], path: str) -> bool:
    head, _, rest = path.partition(".")
    field = model.model_fields.get(head)
    if field is None:
        return False
    if not rest:
        return True
    return any(_resolves(inner, rest) for inner in _models(field.annotation))


def test_a_row_for_every_type_the_projector_handles() -> None:
    handled = {event_type for (event_type, _), spec in SPECS.items() if spec.projector}
    assert handled  # the catalog is loaded
    assert set(ROW_BY_TYPE) == handled
    assert len(ROWS) == len(ROW_BY_TYPE)  # one row per type


def test_rows_read_only_fields_the_writers_have() -> None:
    missing = [
        f"{row.event_type}: {path}"
        for row in ROWS
        for path in row.reads
        if not _resolves(payload_model(row.event_type), path)
    ]
    assert missing == []
    assert all(row.writers and row.changes and row.source for row in ROWS)


def test_path_check_catches_a_renamed_field() -> None:
    model = payload_model("turn.committed")
    assert _resolves(model, "markup.exposures.skill_id")
    assert not _resolves(model, "markup.exposures.skill")
    assert not _resolves(payload_model("observation.diagnosed"), "diagnosis.skill_id")
