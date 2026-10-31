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

type LabelKind = Literal["skill", "topic", "task", "misconception"]

LABEL_PREFIX: dict[LabelKind, str] = {"skill": "S", "topic": "T", "task": "K", "misconception": "M"}


class UnknownLabelError(ValueError):
    """The model wrote a label that is not in the table of the call (Т-§4.5)."""

    def __init__(self, kind: LabelKind, label: str) -> None:
        super().__init__(f"unknown {kind} label {label!r}")
        self.kind = kind
        self.label = label


class LabelMap:
    """Two-way map label ↔ id for one model call."""

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        skills: Sequence[UUID] = (),
        topics: Sequence[UUID] = (),
        tasks: Sequence[UUID] = (),
        misconceptions: Sequence[UUID] = (),
    ) -> LabelMap:
        """Labels in the given order, cut at the limits of `settings`."""
        raise NotImplementedError

    def label(self, kind: LabelKind, object_id: UUID) -> str | None:
        """Label of `object_id`, or `None` if it got none (beyond the limit)."""
        raise NotImplementedError

    def resolve(self, kind: LabelKind, label: str) -> UUID:
        """Id of `label`; `UnknownLabelError` if the table has no such label."""
        raise NotImplementedError

    def table(self) -> dict[str, str]:
        """`label → id` of all labels, for `ops.turn_manifests.label_table`."""
        raise NotImplementedError
