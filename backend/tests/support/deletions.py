"""`FakeDeletionJournal` (ТЗ Фазы 4 §8.1, seams S1, S9) with the "R2 is down" mode."""

from __future__ import annotations

from datetime import datetime

from quack.storage.deletions import DeletionJournalUnavailable, DeletionRecord


class FakeDeletionJournal:
    """Records in `records`; `available = False` makes `write` and `list_since` raise
    `DeletionJournalUnavailable`, so the deletion must not run (Т-§19.5, `T-DL*`)."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.records: list[DeletionRecord] = []

    async def write(self, record: DeletionRecord) -> None:
        if not self.available:
            raise DeletionJournalUnavailable("fake deletion journal is down")
        self.records.append(record)

    async def list_since(self, since: datetime) -> list[DeletionRecord]:
        if not self.available:
            raise DeletionJournalUnavailable("fake deletion journal is down")
        return sorted(
            (r for r in self.records if r.deleted_at >= since), key=lambda r: r.deleted_at
        )

    async def aclose(self) -> None:
        return None
