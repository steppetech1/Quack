"""Reads of `core.events` by stream B (ТЗ Ф4 WP1.2: SQL lives in the package modules; A's
answer to D-B-122): parameters only `%(name)s`, every query filtered by `learner_id`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from quack.db.db import Db
from quack.events.log import StoredEvent

_COLUMNS = (
    "learner_id, seq, id, type, schema_version, actor, domain_id, session_id, causation_seq, "
    "correlation_id, idempotency_key, payload_hash, occurred_at, payload, source_refs"
)
_BY_KEY = (
    f"SELECT {_COLUMNS} FROM core.events "
    "WHERE learner_id = %(learner_id)s AND idempotency_key = %(key)s"
)
_BY_SEQ = f"SELECT {_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s AND seq = %(seq)s"
_SESSION_UP_TO = (
    f"SELECT {_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND seq <= %(to_seq)s ORDER BY seq"
)
_SESSION_OF_ACTION = (
    f"SELECT {_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND (seq <= %(action_seq)s OR causation_seq = %(action_seq)s "
    "OR causation_seq IN (SELECT seq FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND causation_seq = %(action_seq)s)) "
    "ORDER BY seq"
)
_AGENT_REQUEST = (
    f"SELECT {_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'agent.requested' AND payload->>'request_id' = %(request_id)s LIMIT 1"
)
_OF_TASKS = (
    f"SELECT {_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = ANY(%(types)s) AND payload->>'task_id' = ANY(%(task_ids)s) ORDER BY seq"
)


async def event_by_key(db: Db, *, learner_id: UUID, key: str) -> StoredEvent | None:
    return await db.fetch_one(_BY_KEY, {"learner_id": learner_id, "key": key}, row=StoredEvent)


async def event_by_seq(db: Db, *, learner_id: UUID, seq: int) -> StoredEvent | None:
    return await db.fetch_one(_BY_SEQ, {"learner_id": learner_id, "seq": seq}, row=StoredEvent)


async def agent_request(db: Db, *, learner_id: UUID, request_id: UUID) -> StoredEvent:
    """The `agent.requested` of a request id (WP16.6)."""
    params = {"learner_id": learner_id, "request_id": str(request_id)}
    found = await db.fetch_one(_AGENT_REQUEST, params, row=StoredEvent)
    if found is None:
        raise LookupError(f"agent.requested {request_id} not found")
    return found


async def session_events_up_to(
    db: Db, *, learner_id: UUID, session_id: UUID, to_seq: int
) -> list[StoredEvent]:
    """The session's events up to `to_seq`, in order."""
    params = {"learner_id": learner_id, "session_id": session_id, "to_seq": to_seq}
    return await db.fetch_all(_SESSION_UP_TO, params, row=StoredEvent)


async def session_events_of_action(
    db: Db, *, learner_id: UUID, session_id: UUID, action_seq: int
) -> list[StoredEvent]:
    """The session's events up to the action and what the action itself caused (its verdicts,
    `causation_seq = action_seq`): the turn answers the action together with its check (D-B-221).
    An element response that is an answer: its `answer.submitted` (`causation_seq` = the response)
    and the verdicts of that answer come too."""
    params = {"learner_id": learner_id, "session_id": session_id, "action_seq": action_seq}
    return await db.fetch_all(_SESSION_OF_ACTION, params, row=StoredEvent)


async def task_events(
    db: Db, *, learner_id: UUID, task_ids: Sequence[UUID], types: Sequence[str]
) -> list[StoredEvent]:
    """Events of `types` about `task_ids`, in order."""
    params = {
        "learner_id": learner_id,
        "types": list(types),
        "task_ids": [str(t) for t in task_ids],
    }
    return await db.fetch_all(_OF_TASKS, params, row=StoredEvent)
