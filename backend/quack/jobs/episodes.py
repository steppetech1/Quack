"""Episodes: where the dispatcher closes them and whether they have anything to interpret
(ТЗ Ф4 WP7.2; `Т-§7.5`, `Т-§2.1`).

`EpisodeTracker` is a pure state machine over the events of one learner in `seq` order; its state
(`open episode of each session`) is kept in `counters` of the learner's dispatcher mark
(`ops.dispatcher_offsets`) and moves with the offset. Boundaries (`Т-§7.5`):

* `task.closed` — the episode ends with that event;
* a change of `head.intent` or of the plan step (`markup.plan_update.current_step`) in
  `turn.committed` — the episode ends before that turn, the turn opens the next one (D-A-160);
* `episode_max_turns` teacher turns without a boundary — ends with the last of them;
* `episode_idle_min` minutes without events — by the event times (a gap between two events of the
  session, so a replay of the log closes the same episodes) and by the clock (`idle`, the periodic
  check of the dispatcher, for a session that went quiet);
* `session.ended` — ends with that event.

Only the dialogue of the session counts (`EPISODE_TYPES`): the outputs of the system
(observations, digests, agent results, late verdicts) neither open an episode nor move its idle
timer. Late verdicts join an open episode and never open one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from quack.config import Settings
from quack.events.log import StoredEvent

type CloseReason = Literal["task_closed", "boundary", "max_turns", "idle", "session_ended"]

# §6.3, column «Диспетчер» = «эпизод».
EPISODE_OPENERS: frozenset[str] = frozenset(
    {
        "session.started",
        "message.sent",
        "answer.submitted",
        "element.responded",
        "canvas.line_committed",
        "canvas.line_erased",
        "voice.transcribed",
        "hint.requested",
        "solution.requested",
        "check.skipped",
        "item.flagged_unclear",
        "hint.shown",
        "turn.committed",
    }
)
# Verdicts of the episode's answers arrive right away or after the insurance delay: they belong to
# an open episode but never open one (D-A-160).
EPISODE_JOINERS: frozenset[str] = frozenset({"verdict.recorded"})
EPISODE_TYPES: frozenset[str] = EPISODE_OPENERS | EPISODE_JOINERS | {"task.closed", "session.ended"}


@dataclass
class OpenEpisode:
    """The open episode of a session. Everything here is JSON in `counters`."""

    from_seq: int
    last_seq: int
    last_at: datetime
    domain_id: UUID | None = None
    turns: int = 0
    intent: str | None = None
    step: str | None = None

    def dump(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_at"] = self.last_at.isoformat()
        data["domain_id"] = None if self.domain_id is None else str(self.domain_id)
        return data

    @classmethod
    def load(cls, data: dict[str, Any]) -> OpenEpisode:
        return cls(
            from_seq=int(data["from_seq"]),
            last_seq=int(data["last_seq"]),
            last_at=datetime.fromisoformat(data["last_at"]),
            domain_id=None if data.get("domain_id") is None else UUID(data["domain_id"]),
            turns=int(data.get("turns", 0)),
            intent=data.get("intent"),
            step=data.get("step"),
        )


@dataclass(frozen=True)
class ClosedEpisode:
    """What the dispatcher writes as `episode.closed {from_seq, to_seq}` (WP7.2)."""

    session_id: UUID
    from_seq: int
    to_seq: int
    reason: CloseReason
    domain_id: UUID | None
    # Time of the event that decided the close; the idle check closes at the clock's time.
    closed_at: datetime


class EpisodeTracker:
    def __init__(self, settings: Settings, state: dict[str, Any] | None = None) -> None:
        self._max_turns = settings.episode_max_turns
        self._idle = timedelta(minutes=settings.episode_idle_min)
        self._open: dict[UUID, OpenEpisode] = {
            UUID(key): OpenEpisode.load(value) for key, value in (state or {}).items()
        }

    def dump(self) -> dict[str, Any]:
        return {str(sid): ep.dump() for sid, ep in sorted(self._open.items(), key=str)}

    def open_sessions(self) -> list[UUID]:
        return sorted(self._open, key=str)

    def _close(
        self, session_id: UUID, to_seq: int, reason: CloseReason, at: datetime
    ) -> ClosedEpisode:
        ep = self._open.pop(session_id)
        return ClosedEpisode(session_id, ep.from_seq, to_seq, reason, ep.domain_id, at)

    def feed(self, event: StoredEvent) -> list[ClosedEpisode]:
        """Advance by one event; returns the episodes it closes, in `seq` order."""
        sid = event.session_id
        if sid is None or event.type not in EPISODE_TYPES:
            return []
        closed: list[ClosedEpisode] = []
        ep = self._open.get(sid)
        if ep is not None and event.occurred_at - ep.last_at >= self._idle:
            closed.append(self._close(sid, ep.last_seq, "idle", ep.last_at + self._idle))
            ep = None
        if event.type == "session.ended":
            if ep is not None:
                closed.append(self._close(sid, event.seq, "session_ended", event.occurred_at))
            return closed
        if ep is None and event.type not in EPISODE_OPENERS:
            return closed  # a late verdict or a close without an episode: nothing to join
        turn = event.type == "turn.committed"
        intent, step = _turn_head(event) if turn else (None, None)
        if ep is not None and turn and ep.turns > 0 and _changed(ep, intent, step):
            closed.append(self._close(sid, ep.last_seq, "boundary", event.occurred_at))
            ep = None
        if ep is None:
            ep = OpenEpisode(event.seq, event.seq, event.occurred_at, event.domain_id)
            self._open[sid] = ep
        ep.last_seq, ep.last_at = event.seq, event.occurred_at
        if ep.domain_id is None:
            ep.domain_id = event.domain_id
        if turn:
            ep.turns += 1
            ep.intent, ep.step = intent, step
            if ep.turns >= self._max_turns:
                closed.append(self._close(sid, event.seq, "max_turns", event.occurred_at))
        elif event.type == "task.closed":
            closed.append(self._close(sid, event.seq, "task_closed", event.occurred_at))
        return closed

    def idle(self, now: datetime) -> list[ClosedEpisode]:
        """The periodic check (`dispatcher.episode_idle`, WP7.5): sessions with no event for
        `episode_idle_min` minutes by the clock. Closes at the last event of the episode."""
        out: list[ClosedEpisode] = []
        for sid in self.open_sessions():
            ep = self._open[sid]
            if now - ep.last_at >= self._idle:
                out.append(self._close(sid, ep.last_seq, "idle", now))
        return out


def _turn_head(event: StoredEvent) -> tuple[str | None, str | None]:
    head = event.payload.get("head") or {}
    plan = (event.payload.get("markup") or {}).get("plan_update") or {}
    return head.get("intent"), plan.get("current_step")


def _changed(ep: OpenEpisode, intent: str | None, step: str | None) -> bool:
    return (ep.intent is not None and intent != ep.intent) or (
        ep.step is not None and step != ep.step
    )


# --- content --------------------------------------------------------------------------------

# Answers of these types are closed: code decides them (`Т-§5.2`, §6.1 `check()`).
CLOSED_ANSWER_KINDS: frozenset[str] = frozenset(
    {"choice", "number", "expression", "set", "vector", "quantity", "short_text"}
)
# The learner's own words or marks.
_FREE_TYPES: frozenset[str] = frozenset(
    {
        "message.sent",
        "canvas.line_committed",
        "canvas.line_erased",
        "voice.transcribed",
        "item.flagged_unclear",
    }
)
MAIN_PART = "main"


def episode_has_content(events: Iterable[StoredEvent]) -> bool:
    """`Т-§7.5`: False only when the episode holds nothing but closed answers with a code verdict
    — no words of the learner, no canvas lines (D-A-160). A closed answer whose verdict is not in
    the episode (the insurance check comes later) counts as content: the extractor reads
    more than it needs rather than miss a word. Teacher turns are not the learner's content."""
    answers: dict[tuple[int, str], StoredEvent] = {}
    code_verdicts: set[tuple[int, str]] = set()
    for event in events:
        if event.type in _FREE_TYPES:
            return True
        if event.type == "element.responded" and event.payload.get("text"):
            return True
        if event.type == "answer.submitted":
            answer = event.payload.get("answer") or {}
            if answer.get("kind") not in CLOSED_ANSWER_KINDS or event.payload.get("text"):
                return True
            answers[(event.seq, event.payload.get("part") or MAIN_PART)] = event
        elif event.type == "verdict.recorded" and event.payload.get("verification") == "code":
            code_verdicts.add((int(event.payload["answer_seq"]), event.payload["part"]))
    return any(key not in code_verdicts for key in answers)
