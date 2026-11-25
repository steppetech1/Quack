"""Service turns: talk with a built-in command of the chat that is not learning (owner decision
2026-11-24, `/skill-creator`).

The learner writes `/skill-creator <what they want>` in the teacher chat; the teacher drafts a
learner skill with them and proposes it as an element. Everything in that little dialogue lives in
the session journal (it is the same chat), but none of it is learning content: it must not change
the session plan, create tasks, give exposures or evidence, or reach the extractor, the profile or
the memory search. The mark is in two places, both written by code:

- `message.sent.skill_invocation == "skill-creator"` on every message of the dialogue (the first
  one is typed by the learner; the follow-ups are stamped by `session.service_turns` when the last
  turn of the session was an open service turn);
- `turn.committed.service == "skill_creator"` on the turns that answered them.

`is_service_event` and `service_event_seqs` are the one place the readers ask. A voice message
keeps its words in a `voice.transcribed` event that its `message.sent` points at (`voice_seq`):
those words are the dialogue's too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

# The command the learner types, and the name no learner skill may take.
BUILTIN_SKILL_CREATOR = "skill-creator"
RESERVED_SKILL_NAMES: frozenset[str] = frozenset({BUILTIN_SKILL_CREATOR})

# The value of `turn.committed.service`.
type ServiceMode = Literal["skill_creator"]
SERVICE_SKILL_CREATOR: ServiceMode = "skill_creator"


def is_service_event(event_type: str, payload: Mapping[str, Any]) -> bool:
    """A `message.sent` that calls a built-in command, or a `turn.committed` that answered one."""
    if event_type == "message.sent":
        return payload.get("skill_invocation") == BUILTIN_SKILL_CREATOR
    if event_type == "turn.committed":
        return payload.get("service") is not None
    return False


def service_event_seqs(events: Iterable[Any]) -> frozenset[int]:
    """The `seq` of the service events among `events` (objects with `seq`, `type`, `payload`),
    with the `voice.transcribed` events that service messages point at."""
    found: set[int] = set()
    voice: set[int] = set()
    for event in events:
        if is_service_event(event.type, event.payload):
            found.add(event.seq)
            if event.type == "message.sent" and event.payload.get("voice_seq") is not None:
                voice.add(int(event.payload["voice_seq"]))
    return frozenset(found | voice)


def without_service[E](events: Iterable[E]) -> list[E]:
    """`events` without the service ones (`service_event_seqs`), order kept."""
    items = list(events)
    skip = service_event_seqs(items)
    return [e for e in items if e.seq not in skip]  # type: ignore[attr-defined]
