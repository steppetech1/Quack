"""Artifacts as versioned documents (ТЗ Ф4 WP23.1; Т-§17.1, Т-§17.2, Т-§2.3).

An artifact is a document, not memory: `content.artifacts` holds the current version,
`content.artifact_versions` every saved version. Each save is a new version and
`artifact.updated {artifact_id, version, kind, topic_id | hub_id, text}` in one transaction
(Т-§2.3). Kinds: topic notes, topic cards, hub cheat sheet; one artifact of a kind per topic or
hub, its id derived from the owner (D-C-125).

Notes are an ordered list of blocks (Т-§17.2). A block the learner edited or wrote has
`owner = learner` and the system never rewrites it: new knowledge for it becomes a neighbouring
system block marked as a supplement (`supplementTo`), which the learner accepts (merged into the
own block) or deletes (П-11.1).

Topic cards are an ordered list of cards (Т-§17.5, D-C-140): each card is a task of the bank
(`card_id` is its `task_id`), the artifact holds what the learner sees of it; versioning is the
same, only the content of a version differs by kind (`_CODECS`).

The topic or hub must be the learner's: its `topic.created` / `topic.proposed` / `hub.created`
is in the event log (law 1; the hot path does not wait for Neo4j, Т-§20.7).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter
from redis.asyncio import Redis

from quack.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.events.event_types import ArtifactUpdated
from quack.events.log import NewEvent, append_events
from quack.graph.ids import QUACK_UUID_NS

type ArtifactKind = Literal["notes", "cards", "cheatsheet"]
type BlockKind = Literal[
    "summary",
    "definition",
    "statement",
    "why",
    "when_to_apply",
    "worked_example",
    "my_traps",
    "links",
    "custom",
]
type Owner = Literal["system", "learner"]
# Т-§17.4 (WP23.3): what a system block rests on — the materials (it cites a chunk) or only the
# system's own explanation («без опоры»); None for the learner's blocks.
type Basis = Literal["materials", "system"]
# Т-§17.5: the kinds of cards (П-11.2) — definition or formula, sign → method, distinction,
# against a misconception.
type CardKind = Literal["definition", "sign_to_method", "distinction", "against_misconception"]

# Т-§17.2: what the learner writes from scratch is a `custom` block (§2.3 строка 14).
LEARNER_BLOCK_KIND: BlockKind = "custom"
# П-11.1: accepting a supplement joins it to the learner's block as the next paragraph.
_MERGE_SEPARATOR = "\n\n"


class Block(BaseModel):
    """A notes block, Т-§17.2; the names in JSON are the names of the document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: UUID
    kind: BlockKind
    content: str
    owner: Owner
    sources: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    skills: list[str] = Field(default_factory=list[str])
    generated_by: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("generatedBy", "generated_by"),
        serialization_alias="generatedBy",
    )
    # П-11.1: the system block next to a learner block it adds to; None for other blocks.
    supplement_to: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("supplementTo", "supplement_to"),
        serialization_alias="supplementTo",
    )
    basis: Basis | None = None
    # Т-§17.4: a formula of the block did not pass compute twice (or compute was unavailable).
    unverified: bool = False


_BLOCKS: TypeAdapter[list[Block]] = TypeAdapter(list[Block])


class Card(BaseModel):
    """A topic card, Т-§17.5 (D-C-140): `card_id` — the task of the bank behind it (its markup
    and answer spec live there, Т-§5.1); `prompt` — what the learner sees. A card the learner
    edited is `owner = learner`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    card_id: UUID
    kind: CardKind
    prompt: str
    owner: Owner
    generated_by: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("generatedBy", "generated_by"),
        serialization_alias="generatedBy",
    )


_CARDS: TypeAdapter[list[Card]] = TypeAdapter(list[Card])


class SystemBlock(BaseModel):
    """A block written by the system (the artifact writer, WP23.2). `block_id` names the block of
    the current version it updates; None — a new block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: UUID | None = None
    kind: BlockKind
    content: str
    sources: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    skills: list[str] = Field(default_factory=list[str])
    generated_by: dict[str, Any] | None = None
    basis: Basis | None = None
    unverified: bool = False


@dataclass(frozen=True)
class Artifact:
    artifact_id: UUID
    kind: ArtifactKind
    domain_id: UUID
    topic_id: UUID | None
    hub_id: UUID | None
    version: int  # 0 — nothing saved yet
    blocks: tuple[Block, ...]
    cards: tuple[Card, ...] = ()


class ArtifactNotFound(Exception):
    """No such topic, hub, artifact or block of this learner: 404."""


class NotASupplement(Exception):
    """Only a supplement block can be accepted: 409."""


_OWNER_OF_LEARNER = (
    "SELECT payload ->> 'domain_id' AS domain_id FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = ANY(%(types)s) "
    "AND payload ->> %(field)s = %(owner_id)s ORDER BY seq LIMIT 1"
)
_INSERT_ARTIFACT = (
    "INSERT INTO content.artifacts (artifact_id, learner_id, kind, domain_id, topic_id, hub_id) "
    "VALUES (%(artifact_id)s, %(learner_id)s, %(kind)s, %(domain_id)s, %(topic_id)s, "
    "%(hub_id)s) ON CONFLICT (artifact_id) DO NOTHING"
)
_LOCK_ARTIFACT = (
    "SELECT artifact_id, kind, domain_id, topic_id, hub_id, current_version "
    "FROM content.artifacts WHERE learner_id = %(learner_id)s AND artifact_id = %(artifact_id)s "
    "FOR UPDATE"
)
_ARTIFACT = (
    "SELECT a.artifact_id, a.kind, a.domain_id, a.topic_id, a.hub_id, a.current_version, "
    "v.content FROM content.artifacts a LEFT JOIN content.artifact_versions v "
    "ON v.artifact_id = a.artifact_id AND v.version = a.current_version "
    "AND v.learner_id = %(learner_id)s "
    "WHERE a.learner_id = %(learner_id)s AND a.artifact_id = %(artifact_id)s"
)
_VERSION_CONTENT = (
    "SELECT content FROM content.artifact_versions "
    "WHERE learner_id = %(learner_id)s AND artifact_id = %(artifact_id)s AND version = %(version)s"
)
_INSERT_VERSION = (
    "INSERT INTO content.artifact_versions "
    "(artifact_id, version, learner_id, content, skill_version, created_by) "
    "VALUES (%(artifact_id)s, %(version)s, %(learner_id)s, %(content)s, %(skill_version)s, "
    "%(created_by)s)"
)
_EVENT_BY_KEY = (
    "SELECT payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND idempotency_key = %(idempotency_key)s"
)
_SET_CURRENT = (
    "UPDATE content.artifacts SET current_version = %(version)s "
    "WHERE learner_id = %(learner_id)s AND artifact_id = %(artifact_id)s"
)

_OWNER_EVENTS: dict[ArtifactKind, tuple[str, list[str]]] = {
    "notes": ("topic_id", ["topic.created", "topic.proposed"]),
    "cards": ("topic_id", ["topic.created", "topic.proposed"]),
    "cheatsheet": ("hub_id", ["hub.created"]),
}


def artifact_id_for(learner_id: UUID, kind: ArtifactKind, owner_id: UUID) -> UUID:
    """One artifact of a kind per topic (notes, cards) or hub (cheat sheet), D-C-125."""
    return uuid5(QUACK_UUID_NS, f"artifact:{kind}:{learner_id}:{owner_id}")


def blocks_text(blocks: Sequence[Block]) -> str:
    """`artifact.updated.text` — the text of the version for full-text search (Т-§3.1)."""
    return _MERGE_SEPARATOR.join(block.content for block in blocks if block.content)


async def get_artifact(db: Db, *, learner_id: UUID, kind: ArtifactKind, owner_id: UUID) -> Artifact:
    """The current version of the topic's (hub's) artifact; version 0 and no blocks when nothing
    is saved yet — the row is created then, so the learner can write the first block by its id.
    `ArtifactNotFound` — the topic (hub) is not the learner's."""
    domain_id = await _domain_of_owner(db, learner_id, kind, owner_id)
    artifact_id = await _ensure_artifact(db, learner_id, kind, owner_id, domain_id)
    row = await db.fetch_one(_ARTIFACT, {"learner_id": learner_id, "artifact_id": artifact_id})
    assert row is not None
    return _artifact(row, row["content"])


async def find_artifact(db: Db, *, learner_id: UUID, artifact_id: UUID) -> Artifact:
    """By id (`/v1/artifacts/{id}/…`); `ArtifactNotFound` for another learner's or unknown."""
    row = await db.fetch_one(_ARTIFACT, {"learner_id": learner_id, "artifact_id": artifact_id})
    if row is None:
        raise ArtifactNotFound(f"artifact {artifact_id} not found")
    return _artifact(row, row["content"])


async def write_system_blocks(
    db: Db,
    *,
    learner_id: UUID,
    kind: ArtifactKind,
    owner_id: UUID,
    blocks: Sequence[SystemBlock],
    created_by: str,
    skill_version: int | None = None,
    redis: Redis | None = None,
    event_key: str | None = None,
) -> Artifact:
    """A new version from the system blocks (the artifact writer, WP23.2), D-C-125:

    - a block naming a system block of the current version replaces it in place;
    - a block naming a learner block never touches it: it goes right after it as a supplement;
    - a block without `block_id` (or naming no block) is appended;
    - system blocks of the current version that no block names are dropped — the writer returns
      the whole system part; learner blocks always stay where they are.

    `event_key` names the `artifact.updated` event of the new version, so that a later run can
    ask `written_version` whether this request already wrote its version (D-C-217).
    """
    domain_id = await _domain_of_owner(db, learner_id, kind, owner_id)

    artifact_id = artifact_id_for(learner_id, kind, owner_id)

    def new_id(index: int, new: SystemBlock, supplement_to: UUID | None) -> UUID:
        """A new block or supplement has an id from its source: a repeated run of the
        generator with the same blocks writes nothing new (law 5, D-C-125 п.4)."""
        digest = hashlib.sha256(new.content.encode()).hexdigest()
        return uuid5(artifact_id, f"{created_by}:{index}:{supplement_to or ''}:{digest}")

    def change(current: list[Block]) -> list[Block]:
        by_id = {block.block_id: block for block in current}
        replaced: dict[UUID, Block] = {}
        supplements: dict[UUID, list[Block]] = {}
        appended: list[Block] = []
        for index, new in enumerate(blocks):
            target = by_id.get(new.block_id) if new.block_id is not None else None
            if target is not None and target.owner == "system" and target.supplement_to is None:
                replaced[target.block_id] = _system(new, target.block_id, None)
            elif target is not None and target.owner == "learner":
                supplements.setdefault(target.block_id, []).append(
                    _system(new, new_id(index, new, target.block_id), target.block_id)
                )
            else:
                appended.append(_system(new, new_id(index, new, None), None))
        result: list[Block] = []
        for block in current:
            if block.owner == "learner":
                result.append(block)
                result.extend(supplements.get(block.block_id, []))
            elif block.block_id in replaced:
                result.append(replaced[block.block_id])
            elif block.supplement_to is not None and block.supplement_to in by_id:
                # A supplement the learner has not answered yet stays next to its block.
                if not supplements.get(block.supplement_to):
                    result.append(block)
        return result + appended

    await _ensure_artifact(db, learner_id, kind, owner_id, domain_id)
    return await _save(
        db,
        learner_id=learner_id,
        artifact_id=artifact_id,
        change=change,
        created_by=created_by,
        actor=created_by,
        skill_version=skill_version,
        redis=redis,
        event_key=event_key,
    )


async def written_version(db: Db, *, learner_id: UUID, key: str) -> int | None:
    """The version an `artifact.updated` event of this `event_key` wrote; None — no such event
    (the request has not written yet, or its blocks changed nothing)."""
    found = await db.fetch_one(_EVENT_BY_KEY, {"learner_id": learner_id, "idempotency_key": key})
    return None if found is None else int(found["payload"]["version"])


async def edit_block(
    db: Db,
    *,
    learner_id: UUID,
    artifact_id: UUID,
    block_id: UUID,
    content: str,
    idempotency_key: str,
    redis: Redis | None = None,
) -> Artifact:
    """`PUT /v1/artifacts/{id}/blocks/{block_id}`: the learner edits a block (it becomes
    `owner = learner`, a supplement stops being one) or writes a new one — an unknown
    `block_id` appends a `custom` learner block (Т-§17.2, П-11.1)."""

    def change(current: list[Block]) -> list[Block]:
        for index, block in enumerate(current):
            if block.block_id == block_id:
                edited = block.model_copy(
                    update={
                        "content": content,
                        "owner": "learner",
                        "supplement_to": None,
                        "basis": None,
                        "unverified": False,
                    }
                )
                return [*current[:index], edited, *current[index + 1 :]]
        return [
            *current,
            Block(block_id=block_id, kind=LEARNER_BLOCK_KIND, content=content, owner="learner"),
        ]

    return await _save_learner(db, learner_id, artifact_id, change, idempotency_key, redis)


async def accept_supplement(
    db: Db,
    *,
    learner_id: UUID,
    artifact_id: UUID,
    block_id: UUID,
    idempotency_key: str,
    redis: Redis | None = None,
) -> Artifact:
    """`POST /v1/artifacts/{id}/blocks/{block_id}/accept`: the supplement is merged into the
    learner's block it adds to (П-11.1). `NotASupplement`, `ArtifactNotFound`."""

    def change(current: list[Block]) -> list[Block]:
        supplement = _block(current, block_id)
        if supplement.supplement_to is None:
            raise NotASupplement(f"block {block_id} is not a supplement")
        result: list[Block] = []
        for block in current:
            if block.block_id == block_id:
                continue
            if block.block_id == supplement.supplement_to:
                merged = block.content + _MERGE_SEPARATOR + supplement.content
                block = block.model_copy(
                    update={
                        "content": merged,
                        "sources": _union(block.sources, supplement.sources),
                        "skills": _union(block.skills, supplement.skills),
                    }
                )
            result.append(block)
        return result

    return await _save_learner(db, learner_id, artifact_id, change, idempotency_key, redis)


async def delete_block(
    db: Db,
    *,
    learner_id: UUID,
    artifact_id: UUID,
    block_id: UUID,
    idempotency_key: str,
    redis: Redis | None = None,
) -> Artifact:
    """`DELETE /v1/artifacts/{id}/blocks/{block_id}`: a new version without the block; deleting
    a learner block drops its unanswered supplements with it."""

    def change(current: list[Block]) -> list[Block]:
        _block(current, block_id)
        return [
            block
            for block in current
            if block.block_id != block_id and block.supplement_to != block_id
        ]

    return await _save_learner(db, learner_id, artifact_id, change, idempotency_key, redis)


async def version_blocks(
    db: Db, *, learner_id: UUID, artifact_id: UUID, version: int
) -> tuple[Block, ...]:
    """The blocks of a saved version (the history of Т-§17.1)."""
    row = await db.fetch_one(
        _VERSION_CONTENT,
        {"learner_id": learner_id, "artifact_id": artifact_id, "version": version},
    )
    if row is None:
        raise ArtifactNotFound(f"artifact {artifact_id} has no version {version}")
    return _decode(row["content"])


async def _version_items(
    db: Db, learner_id: UUID, artifact_id: UUID, version: int, codec: _Codec
) -> tuple[Any, ...]:
    row = await db.fetch_one(
        _VERSION_CONTENT,
        {"learner_id": learner_id, "artifact_id": artifact_id, "version": version},
    )
    if row is None:
        raise ArtifactNotFound(f"artifact {artifact_id} has no version {version}")
    return codec.decode(row["content"])


async def _save_learner(
    db: Db,
    learner_id: UUID,
    artifact_id: UUID,
    change: Any,
    idempotency_key: str,
    redis: Redis | None,
) -> Artifact:
    """A learner save under the request's `idempotency_key` (§6.4, T-E2): a repeat returns the
    version the first one wrote and writes nothing; the same key with another request —
    `IdempotencyConflictError` (409)."""
    key = f"artifact.edit:{idempotency_key}"
    async with db.transaction():
        replayed = await _replay(db, learner_id, artifact_id, change, key)
        if replayed is not None:
            return replayed
        return await _save(
            db,
            learner_id=learner_id,
            artifact_id=artifact_id,
            change=change,
            created_by="learner",
            actor="learner",
            skill_version=None,
            redis=redis,
            event_key=key,
        )


async def _replay(
    db: Db, learner_id: UUID, artifact_id: UUID, change: Any, key: str
) -> Artifact | None:
    """The version written under `key`, when the same request applied to the version before it
    gives it again; None — the key is new."""
    found = await db.fetch_one(_EVENT_BY_KEY, {"learner_id": learner_id, "idempotency_key": key})
    if found is None:
        return None
    payload = found["payload"]
    version = int(payload["version"])
    conflict = IdempotencyConflictError(key, "", "")
    if payload["artifact_id"] != str(artifact_id):
        raise conflict
    artifact = await find_artifact(db, learner_id=learner_id, artifact_id=artifact_id)
    codec = _CODECS[artifact.kind]
    before: tuple[Any, ...] = ()
    if version > 1:
        before = await _version_items(db, learner_id, artifact_id, version - 1, codec)
    written = await _version_items(db, learner_id, artifact_id, version, codec)
    try:
        again = tuple(change(list(before)))
    except (ArtifactNotFound, NotASupplement):
        raise conflict from None
    if again != written:
        raise conflict
    return codec.at(replace(artifact, version=version), written)


async def _save(
    db: Db,
    *,
    learner_id: UUID,
    artifact_id: UUID,
    change: Any,
    created_by: str,
    actor: str,
    skill_version: int | None,
    redis: Redis | None,
    event_key: str | None = None,
) -> Artifact:
    """Lock the artifact, apply `change` to the current blocks and, when they differ, write the
    next version, move `current_version` and append `artifact.updated` — one transaction
    (Т-§2.3). The same blocks again write nothing (law 5)."""
    params = {"learner_id": learner_id, "artifact_id": artifact_id}
    async with db.transaction():
        row = await db.fetch_one(_LOCK_ARTIFACT, params)
        if row is None:
            raise ArtifactNotFound(f"artifact {artifact_id} not found")
        version = int(row["current_version"])
        codec = _CODECS[row["kind"]]
        current: tuple[Any, ...] = ()
        if version > 0:
            stored = await db.fetch_one(_VERSION_CONTENT, {**params, "version": version})
            assert stored is not None
            current = codec.decode(stored["content"])
        items = tuple(change(list(current)))
        if items == current:
            return codec.at(_artifact(row, None, blocks=()), current)
        version += 1
        await db.execute(
            _INSERT_VERSION,
            {
                **params,
                "version": version,
                "content": Jsonb(codec.encode(items)),
                "skill_version": skill_version,
                "created_by": created_by,
            },
        )
        await db.execute(_SET_CURRENT, {**params, "version": version})
        row = {**row, "current_version": version}
        artifact = codec.at(_artifact(row, None, blocks=()), items)
        payload = ArtifactUpdated(
            artifact_id=artifact_id,
            version=version,
            kind=artifact.kind,
            topic_id=artifact.topic_id,
            hub_id=artifact.hub_id,
            text=codec.text(items),
        )
        await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="artifact.updated",
                    payload=payload,
                    actor=actor,
                    idempotency_key=event_key or f"artifact.updated:{artifact_id}:{version}",
                    domain_id=artifact.domain_id,
                )
            ],
            redis=redis,
        )
    return artifact


async def _domain_of_owner(db: Db, learner_id: UUID, kind: ArtifactKind, owner_id: UUID) -> UUID:
    field, types = _OWNER_EVENTS[kind]
    row = await db.fetch_one(
        _OWNER_OF_LEARNER,
        {"learner_id": learner_id, "types": types, "field": field, "owner_id": str(owner_id)},
    )
    if row is None or row["domain_id"] is None:
        raise ArtifactNotFound(f"{field.removesuffix('_id')} {owner_id} not found")
    return UUID(row["domain_id"])


async def _ensure_artifact(
    db: Db, learner_id: UUID, kind: ArtifactKind, owner_id: UUID, domain_id: UUID
) -> UUID:
    artifact_id = artifact_id_for(learner_id, kind, owner_id)
    topic_id, hub_id = _owner_ids(kind, owner_id)
    await db.execute(
        _INSERT_ARTIFACT,
        {
            "artifact_id": artifact_id,
            "learner_id": learner_id,
            "kind": kind,
            "domain_id": domain_id,
            "topic_id": topic_id,
            "hub_id": hub_id,
        },
    )
    return artifact_id


def _owner_ids(kind: ArtifactKind, owner_id: UUID) -> tuple[UUID | None, UUID | None]:
    return (None, owner_id) if kind == "cheatsheet" else (owner_id, None)


def _system(new: SystemBlock, block_id: UUID, supplement_to: UUID | None) -> Block:
    return Block(
        block_id=block_id,
        kind=new.kind,
        content=new.content,
        owner="system",
        sources=new.sources,
        skills=new.skills,
        generated_by=new.generated_by,
        supplement_to=supplement_to,
        basis=new.basis,
        unverified=new.unverified,
    )


def _block(blocks: Sequence[Block], block_id: UUID) -> Block:
    for block in blocks:
        if block.block_id == block_id:
            return block
    raise ArtifactNotFound(f"block {block_id} not found")


def _union[T](first: list[T], second: list[T]) -> list[T]:
    return [*first, *(item for item in second if item not in first)]


def _encode(blocks: Sequence[Block]) -> dict[str, Any]:
    return {"blocks": [block.model_dump(mode="json", by_alias=True) for block in blocks]}


def _decode(content: Any) -> tuple[Block, ...]:
    return tuple(_BLOCKS.validate_python((content or {}).get("blocks", [])))


def _artifact(row: Any, content: Any, *, blocks: tuple[Block, ...] | None = None) -> Artifact:
    artifact = Artifact(
        artifact_id=row["artifact_id"],
        kind=row["kind"],
        domain_id=row["domain_id"],
        topic_id=row["topic_id"],
        hub_id=row["hub_id"],
        version=int(row["current_version"]),
        blocks=(),
    )
    if blocks is not None:
        return replace(artifact, blocks=blocks)
    codec = _CODECS[artifact.kind]
    return codec.at(artifact, codec.decode(content))


@dataclass(frozen=True)
class _Codec:
    """The content of a version by kind: blocks (notes, cheat sheet) or cards (D-C-140)."""

    decode: Callable[[Any], tuple[Any, ...]]
    encode: Callable[[Sequence[Any]], dict[str, Any]]
    text: Callable[[Sequence[Any]], str]
    at: Callable[[Artifact, tuple[Any, ...]], Artifact]


def _encode_cards(cards: Sequence[Card]) -> dict[str, Any]:
    return {"cards": [card.model_dump(mode="json", by_alias=True) for card in cards]}


def _decode_cards(content: Any) -> tuple[Card, ...]:
    return tuple(_CARDS.validate_python((content or {}).get("cards", [])))


def _cards_text(cards: Sequence[Card]) -> str:
    return _MERGE_SEPARATOR.join(card.prompt for card in cards if card.prompt)


_BLOCK_CODEC = _Codec(_decode, _encode, blocks_text, lambda a, items: replace(a, blocks=items))
_CODECS: dict[str, _Codec] = {
    "notes": _BLOCK_CODEC,
    "cheatsheet": _BLOCK_CODEC,
    "cards": _Codec(
        _decode_cards, _encode_cards, _cards_text, lambda a, items: replace(a, cards=items)
    ),
}
