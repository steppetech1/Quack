"""«Не согласен» and «это была описка» (ТЗ Ф4 WP8.1; Т-§2.8, Т-§7.6, П-15.4).

The learner's target is checked first — `404` when it is not theirs (law 1); then one event is
appended: `dispute.filed` or `typo.declared`. The projector does the rest (the evidence leaves
the fold, D-A-134). A verdict is named like its key, `"<answer_seq>:<part>"`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.events.log import NewEvent, append_events


class DisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_kind: Literal["evidence", "misconception", "verdict"]
    target_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class TypoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part: str = Field(default="main", min_length=1, max_length=64)


class EventAccepted(BaseModel):
    """The number of the appended event (the learner's change, Т-§2.8)."""

    seq: int


_ANSWER = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND seq = %(seq)s AND type = 'answer.submitted'"
)
_OWN_NODE = {
    "evidence": "MATCH (n:Evidence {id: $id}) WHERE n.learnerId = $learner RETURN count(n) AS n",
    # a merged misconception is disputed through its alias (Т-§2.8)
    "misconception": (
        "MATCH (n) WHERE (n:Misconception OR n:Alias) AND n.id = $id AND n.learnerId = $learner "
        "RETURN count(n) AS n"
    ),
}


async def _own_answer(db: Db, learner_id: UUID, answer_seq: int) -> bool:
    found = await db.fetch_one(_ANSWER, {"learner_id": learner_id, "seq": answer_seq})
    return found is not None


async def _own_node(neo4j: AsyncDriver, learner_id: UUID, kind: str, node_id: str) -> bool:
    async with read_session(neo4j) as session:
        result = await session.run(literal(_OWN_NODE[kind]), id=node_id, learner=str(learner_id))
        record = await result.single()
    return record is not None and int(record["n"]) > 0


async def file_dispute(
    db: Db, neo4j: AsyncDriver, redis: Redis | None, learner_id: UUID, body: DisputeRequest
) -> int | None:
    """`dispute.filed`; None — the target is not the learner's (404)."""
    if body.target_kind == "verdict":
        seq, _, part = body.target_id.partition(":")
        if not (seq.isdigit() and part and await _own_answer(db, learner_id, int(seq))):
            return None
    elif not await _own_node(neo4j, learner_id, body.target_kind, body.target_id):
        return None
    event = NewEvent(
        type="dispute.filed",
        payload={"target_kind": body.target_kind, "target_id": body.target_id},
        actor="learner",
        idempotency_key=f"dispute:{body.idempotency_key}",
    )
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return stored.seq


async def declare_typo(
    db: Db, redis: Redis | None, learner_id: UUID, answer_seq: int, part: str
) -> int | None:
    """`typo.declared`; one per verdict (the key is the verdict's, law 5); None — 404."""
    if not await _own_answer(db, learner_id, answer_seq):
        return None
    event = NewEvent(
        type="typo.declared",
        payload={"answer_seq": answer_seq, "part": part},
        actor="learner",
        idempotency_key=f"typo:{answer_seq}:{part}",
    )
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return stored.seq
