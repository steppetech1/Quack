"""Short labels instead of ids in model calls (Т-§4.5, WP14.1).

Models never see or write ids. Within one call skills get `S1…S15`, topics `T1…T8`, tasks
`K1…K5`, misconceptions `M1…Mn`, in the order of importance given by the caller; beyond the
limits (`candidate_skills_k`, `label_max_topics`, `label_max_tasks`) no labels are given.
Code translates labels back before writing events; an unknown label is a labelling error.
The table lives for one call and is written to the turn manifest (Т-§3.2, WP14.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from quack.config import Settings

type LabelKind = Literal[
    "skill",
    "topic",
    "task",
    "misconception",
    "hub",
    "chunk",
    "card",
    "request",
    "evidence",
    "line",
]

# Phase 4 decision D-B-108: hubs `H`, material chunks `P`, cards `C`, pending agent
# requests `R`, evidence `E` and canvas lines `L` get labels too (ТЗ Ф4 WP13.3, WP13.7,
# WP14.4: deadlines, links, card series, cancelling requests, memory tool arguments);
# like misconceptions, no limit of their own.
LABEL_PREFIX: dict[LabelKind, str] = {
    "skill": "S",
    "topic": "T",
    "task": "K",
    "misconception": "M",
    "hub": "H",
    "chunk": "P",
    "card": "C",
    "request": "R",
    "evidence": "E",
    "line": "L",
}


class UnknownLabelError(ValueError):
    """The model wrote a label that is not in the table of the call (Т-§4.5)."""

    def __init__(self, kind: LabelKind, label: str) -> None:
        super().__init__(f"unknown {kind} label {label!r}")
        self.kind = kind
        self.label = label


class LabelMap:
    """Two-way map label ↔ id for one model call."""

    def __init__(self, by_kind: dict[LabelKind, list[UUID]]) -> None:
        self._ids: dict[LabelKind, list[UUID]] = {kind: [] for kind in LABEL_PREFIX}
        self._labels: dict[tuple[LabelKind, UUID], str] = {}
        for kind, ids in by_kind.items():
            for object_id in ids:
                if (kind, object_id) in self._labels:
                    continue  # one label per object, the first (most important) position
                self._ids[kind].append(object_id)
                self._labels[(kind, object_id)] = f"{LABEL_PREFIX[kind]}{len(self._ids[kind])}"

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        skills: Sequence[UUID] = (),
        topics: Sequence[UUID] = (),
        tasks: Sequence[UUID] = (),
        misconceptions: Sequence[UUID] = (),
        hubs: Sequence[UUID] = (),
        chunks: Sequence[UUID] = (),
        cards: Sequence[UUID] = (),
        requests: Sequence[UUID] = (),
        evidence: Sequence[UUID] = (),
        lines: Sequence[UUID] = (),
    ) -> LabelMap:
        """Labels in the given order, cut at the limits of `settings`. Misconceptions have
        no limit of their own (`M1…Mn`, ТЗ §2.3 row 18)."""
        return cls(
            {
                "skill": _unique(skills)[: settings.candidate_skills_k],
                "topic": _unique(topics)[: settings.label_max_topics],
                "task": _unique(tasks)[: settings.label_max_tasks],
                "misconception": _unique(misconceptions),
                "hub": _unique(hubs),
                "chunk": _unique(chunks),
                "card": _unique(cards),
                "request": _unique(requests),
                "evidence": _unique(evidence),
                "line": _unique(lines),
            }
        )

    def label(self, kind: LabelKind, object_id: UUID) -> str | None:
        """Label of `object_id`, or `None` if it got none (beyond the limit)."""
        return self._labels.get((kind, object_id))

    def resolve(self, kind: LabelKind, label: str) -> UUID:
        """Id of `label`; `UnknownLabelError` if the table has no such label."""
        prefix = LABEL_PREFIX[kind]
        number = label.removeprefix(prefix)
        if label == number or not number.isdigit() or number.startswith("0"):
            raise UnknownLabelError(kind, label)
        index = int(number) - 1
        ids = self._ids[kind]
        if index >= len(ids):
            raise UnknownLabelError(kind, label)
        return ids[index]

    def labels(self, kind: LabelKind) -> dict[str, UUID]:
        """`label → id` of one kind (the label tables of `ExtractionContext`, WP12)."""
        return {
            f"{LABEL_PREFIX[kind]}{i}": object_id for i, object_id in enumerate(self._ids[kind], 1)
        }

    def table(self) -> dict[str, str]:
        """`label → id` of all labels, for `ops.turn_manifests.label_table`."""
        return {label: str(object_id) for (_, object_id), label in self._labels.items()}


def _unique(ids: Sequence[UUID]) -> list[UUID]:
    """`ids` without repeats, first occurrence kept (order of importance)."""
    return list(dict.fromkeys(ids))
