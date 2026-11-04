"""Skills of the learner (ТЗ Ф4 WP23.6; Т-§17.8, П-11.6; D-C-216).

A skill is the learner's own instruction: a name, a text and bindings. It is stored in
`content.learner_skills` as `(skill_id, version)`; every change is a new version, so what an
artifact was written under (`artifact_versions.skill_version`) stays readable. Two ways to use it:

- called in the chat as `/name`: stream B reads the latest version by name
  (`quack.session.turn_inputs.load_learner_skill`) and puts the text into the tail of that turn;
- bound to an artifact type (`bindings = {"artifact_types": ["notes", ...]}`): the artifact writer
  gets the text in the `learner_skill` slot and the artifact records the version. A change of the
  skill never regenerates what exists (Т-§17.8).

The skill changes style, format and depth only; everything the memory depends on is checked by code
on the way out (Т-§17.3), so a skill cannot switch a check off. At most one skill is bound to an
artifact type of a learner, so the version an artifact records is unambiguous. A name is unique
among the learner's skills and is kept on every version: the lookup of stream B goes by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from quack.artifacts.documents import ArtifactKind
from quack.db import Db
from quack.events.errors import IdempotencyConflictError

NAME_PATTERN = re.compile(r"^[\w-]{1,40}$")
ARTIFACT_TYPES: tuple[str, ...] = get_args(ArtifactKind.__value__)
NO_LEARNER_SKILL = "None."

_NAMESPACE = UUID("5c0f7d2e-8d1a-4c57-9b1e-3a6f5a0c1d42")
_COLUMNS = "skill_id, version, name, instruction, bindings, created_at"
_LATEST = (
    f"SELECT DISTINCT ON (skill_id) {_COLUMNS} FROM content.learner_skills "
    "WHERE learner_id = %(learner_id)s ORDER BY skill_id, version DESC"
)
_LATEST_ONE = (
    f"SELECT {_COLUMNS} FROM content.learner_skills "
    "WHERE learner_id = %(learner_id)s AND skill_id = %(skill_id)s ORDER BY version DESC LIMIT 1"
)
_FIRST_ONE = (
    f"SELECT {_COLUMNS} FROM content.learner_skills "
    "WHERE learner_id = %(learner_id)s AND skill_id = %(skill_id)s AND version = 1"
)
_INSERT = (
    "INSERT INTO content.learner_skills "
    "(skill_id, version, learner_id, name, instruction, bindings) "
    "VALUES (%(skill_id)s, %(version)s, %(learner_id)s, %(name)s, %(instruction)s, %(bindings)s) "
    f"RETURNING {_COLUMNS}"
)
_RENAME = (
    "UPDATE content.learner_skills SET name = %(name)s "
    "WHERE learner_id = %(learner_id)s AND skill_id = %(skill_id)s"
)
_DELETE = (
    "DELETE FROM content.learner_skills "
    "WHERE learner_id = %(learner_id)s AND skill_id = %(skill_id)s"
)
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%(scope)s, 0))"


class SkillNotFound(Exception):
    """Not the learner's skill (HTTP 404)."""


class SkillConflict(Exception):
    """The name is taken, or the artifact type already has a skill (HTTP 409)."""


@dataclass(frozen=True)
class LearnerSkillRecord:
    skill_id: UUID
    version: int
    name: str
    instruction: str
    artifact_types: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class BoundSkill:
    """The skill an artifact of the type is written under: text for the writer, version for
    `artifact_versions.skill_version`."""

    text: str
    version: int


def normalize_name(name: str) -> str | None:
    """The name as stored (lower case; `/name` in the chat), `None` — not a valid name."""
    lowered = name.strip().lower().removeprefix("/")
    return lowered if NAME_PATTERN.fullmatch(lowered) else None


def _record(row: dict[str, Any]) -> LearnerSkillRecord:
    types = (row["bindings"] or {}).get("artifact_types", [])
    return LearnerSkillRecord(
        skill_id=row["skill_id"],
        version=int(row["version"]),
        name=row["name"],
        instruction=row["instruction"],
        artifact_types=tuple(types),
        created_at=row["created_at"],
    )


def _bindings(artifact_types: tuple[str, ...]) -> Jsonb:
    return Jsonb({"artifact_types": list(artifact_types)})


async def _lock(db: Db, learner_id: UUID) -> None:
    await db.execute(_LOCK, {"scope": f"learner_skills:{learner_id}"})


async def _check_free(
    db: Db,
    learner_id: UUID,
    *,
    own: UUID | None,
    name: str,
    artifact_types: tuple[str, ...],
) -> None:
    """Name and artifact types must not belong to another skill of the learner."""
    for other in await db.fetch_all(_LATEST, {"learner_id": learner_id}):
        record = _record(other)
        if record.skill_id == own:
            continue
        if record.name == name:
            raise SkillConflict(f"the name /{name} is taken by another skill")
        taken = sorted(set(record.artifact_types) & set(artifact_types))
        if taken:
            raise SkillConflict(f"{', '.join(taken)} already has the skill /{record.name}")


async def list_skills(db: Db, *, learner_id: UUID) -> list[LearnerSkillRecord]:
    """The latest version of every skill, by name."""
    rows = await db.fetch_all(_LATEST, {"learner_id": learner_id})
    return sorted((_record(row) for row in rows), key=lambda record: record.name)


async def get_skill(db: Db, *, learner_id: UUID, skill_id: UUID) -> LearnerSkillRecord:
    row = await db.fetch_one(_LATEST_ONE, {"learner_id": learner_id, "skill_id": skill_id})
    if row is None:
        raise SkillNotFound(f"skill {skill_id} not found")
    return _record(row)


async def create_skill(
    db: Db,
    *,
    learner_id: UUID,
    idempotency_key: str,
    name: str,
    instruction: str,
    artifact_types: tuple[str, ...],
) -> LearnerSkillRecord:
    """Version 1 of a new skill. The id comes from the key: a repeat with the same request
    returns the skill, with another one — `IdempotencyConflictError` (409, T-E2)."""
    skill_id = uuid5(_NAMESPACE, f"{learner_id}:{idempotency_key}")
    async with db.transaction():
        await _lock(db, learner_id)
        first = await db.fetch_one(_FIRST_ONE, {"learner_id": learner_id, "skill_id": skill_id})
        if first is not None:
            same = _record(first)
            same_request = same.instruction == instruction and set(same.artifact_types) == set(
                artifact_types
            )
            if not same_request or same.name != name:
                raise IdempotencyConflictError(idempotency_key, "", "")
            return await get_skill(db, learner_id=learner_id, skill_id=skill_id)
        await _check_free(db, learner_id, own=None, name=name, artifact_types=artifact_types)
        created = await db.fetch_one(
            _INSERT,
            {
                "skill_id": skill_id,
                "version": 1,
                "learner_id": learner_id,
                "name": name,
                "instruction": instruction,
                "bindings": _bindings(artifact_types),
            },
        )
    assert created is not None
    return _record(created)


async def update_skill(
    db: Db,
    *,
    learner_id: UUID,
    skill_id: UUID,
    name: str | None = None,
    instruction: str | None = None,
    artifact_types: tuple[str, ...] | None = None,
) -> LearnerSkillRecord:
    """A new version with the given fields changed; nothing changed — the same version back (a
    repeat of a request writes nothing). A new name is set on every version: the chat finds a
    skill by its current name only."""
    async with db.transaction():
        await _lock(db, learner_id)
        current = await get_skill(db, learner_id=learner_id, skill_id=skill_id)
        new_name = current.name if name is None else name
        new_text = current.instruction if instruction is None else instruction
        new_types = current.artifact_types if artifact_types is None else artifact_types
        if (new_name, new_text, set(new_types)) == (
            current.name,
            current.instruction,
            set(current.artifact_types),
        ):
            return current
        await _check_free(db, learner_id, own=skill_id, name=new_name, artifact_types=new_types)
        if new_name != current.name:
            await db.execute(
                _RENAME, {"learner_id": learner_id, "skill_id": skill_id, "name": new_name}
            )
        if (new_text, set(new_types)) == (current.instruction, set(current.artifact_types)):
            return await get_skill(db, learner_id=learner_id, skill_id=skill_id)
        written = await db.fetch_one(
            _INSERT,
            {
                "skill_id": skill_id,
                "version": current.version + 1,
                "learner_id": learner_id,
                "name": new_name,
                "instruction": new_text,
                "bindings": _bindings(new_types),
            },
        )
    assert written is not None
    return _record(written)


async def delete_skill(db: Db, *, learner_id: UUID, skill_id: UUID) -> None:
    """The skill with all its versions. Artifacts written under it stay as they are."""
    async with db.transaction():
        await get_skill(db, learner_id=learner_id, skill_id=skill_id)
        await db.execute(_DELETE, {"learner_id": learner_id, "skill_id": skill_id})


async def bound_skill(db: Db, *, learner_id: UUID, artifact_type: str) -> BoundSkill | None:
    """The skill bound to the artifact type, `None` — there is none."""
    for record in await list_skills(db, learner_id=learner_id):
        if artifact_type in record.artifact_types:
            return BoundSkill(text=record.instruction, version=record.version)
    return None


def writer_text(skill: BoundSkill | None) -> str:
    """The `learner_skill` slot of `artifact_writer`."""
    return NO_LEARNER_SKILL if skill is None else skill.text
