"""Ready read-only SQLite databases of the code sandbox (teacher@5): the teacher names a preset
instead of writing `CREATE TABLE` on the fly. `<name>.schema.sql` — the tables, `<name>.data.sql` —
the rows. The runner builds a fresh in-memory database from them for every test and lets the
learner's query only read."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Literal, get_args

type SqlPresetName = Literal["shop", "university"]
SQL_PRESETS: tuple[str, ...] = get_args(SqlPresetName.__value__)


@dataclass(frozen=True)
class SqlPreset:
    name: str
    schema_sql: str
    data_sql: str


@cache
def sql_preset(name: str) -> SqlPreset:
    """`KeyError` for a name that is not a preset."""
    if name not in SQL_PRESETS:
        raise KeyError(name)
    folder = resources.files(__package__)
    return SqlPreset(
        name=name,
        schema_sql=folder.joinpath(f"{name}.schema.sql").read_text(encoding="utf-8"),
        data_sql=folder.joinpath(f"{name}.data.sql").read_text(encoding="utf-8"),
    )
