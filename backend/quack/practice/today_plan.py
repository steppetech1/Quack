"""The session an item of «Сегодня» opens (ТЗ Ф4 WP16.8, WP13.10; Т-§14.4). D-B-222.

`today.prepare` (stream B, `quack.agents.today`) writes a draft plan under the id the session
of the item will take; `POST /v1/sessions` (`quack.session`) takes that id when the learner
presses the item. The id lives here: a product package imports only `practice` (§4.1).
"""

from __future__ import annotations

from uuid import UUID, uuid5

from quack.db.db import Db
from quack.events.event_types import TodayShown

_LAST_SHOWN = (
    "SELECT seq, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'today.shown' AND domain_id = %(domain_id)s ORDER BY seq DESC LIMIT 1"
)
_PREPARED = (
    "SELECT 1 AS found FROM content.session_plans p WHERE p.session_id = %(session_id)s "
    "AND p.learner_id = %(learner_id)s AND p.status = 'prepared' AND NOT EXISTS ("
    "SELECT 1 FROM content.sessions s WHERE s.session_id = p.session_id "
    "AND s.learner_id = p.learner_id)"
)


def prepared_session_id(learner_id: UUID, trigger_seq: int, skill_id: UUID) -> UUID:
    """The session an item of «Сегодня» opens (`POST /v1/sessions {today_item}`)."""
    return uuid5(learner_id, f"today:{trigger_seq}:{skill_id}")


async def prepared_session_for(
    db: Db,
    *,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID | None = None,
    skill_id: UUID | None = None,
) -> UUID | None:
    """The id of the prepared session of the item the learner opens: the item of the latest
    `today.shown` of the domain with this skill (`today_item`) or on this topic. `None` — no
    such item, the plan is not prepared yet, or the item's session already started: the
    teacher begins on his own by the same goal (Т-§14.4)."""
    if topic_id is None and skill_id is None:
        return None
    row = await db.fetch_one(_LAST_SHOWN, {"learner_id": learner_id, "domain_id": domain_id})
    if row is None:
        return None
    shown = TodayShown.model_validate(row["payload"])
    item = next(
        (
            i
            for i in shown.items
            if (skill_id is not None and i.skill_id == skill_id)
            or (skill_id is None and i.topic_id == topic_id)
        ),
        None,
    )
    if item is None:
        return None
    session_id = prepared_session_id(learner_id, int(row["seq"]), item.skill_id)
    found = await db.fetch_one(_PREPARED, {"session_id": session_id, "learner_id": learner_id})
    return session_id if found is not None else None
