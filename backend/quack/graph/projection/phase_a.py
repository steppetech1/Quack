"""Phase A of a batch (Т-§2.4): the events of one learner in `seq` order through the handlers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from quack.events.log import StoredEvent
from quack.graph.projection.handlers import handler_for
from quack.graph.projection.mutations import Mutation
from quack.graph.projection.view import NOTHING, BatchSlice, Need


@dataclass
class PhaseAResult:
    mutations: list[Mutation] = field(default_factory=list)


class EventFailed(Exception):
    """An event of the batch the projector cannot apply: an unknown type, a newer schema or an
    error of its handler. The learner's stream stops on it (Т-§2.4)."""

    def __init__(self, seq: int, cause: Exception) -> None:
        super().__init__(f"event {seq}: {cause!r}")
        self.seq = seq
        self.cause = cause


def batch_need(events: Sequence[StoredEvent]) -> Need:
    """Union of `needs(event)` of the batch: the slice is read with one query (Т-§2.4)."""
    need = NOTHING
    for event in events:
        try:
            handler = handler_for(event)
            if handler is not None:
                need = need | handler.needs(event)
        except Exception as exc:
            raise EventFailed(event.seq, exc) from exc
    return need


def apply_events(events: Sequence[StoredEvent], batch: BatchSlice) -> PhaseAResult:
    """Each event sees the slice plus the changes of the earlier events of the batch."""
    result = PhaseAResult()
    for event in events:
        try:
            handler = handler_for(event)
            batch.add_event(event)
            if handler is None:
                continue
            changes = handler.handle(event, batch.view(handler.needs(event)))
            batch.apply(changes)
        except Exception as exc:
            raise EventFailed(event.seq, exc) from exc
        result.mutations.extend(changes)
    return result
