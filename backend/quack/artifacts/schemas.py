"""Prompt output schema of the role `artifacts@1` (ТЗ Фазы 3 §4); the notes, cheat sheet and
cards of Phase 4 have their own writer (`quack.artifacts.writer`)."""

from __future__ import annotations

from pydantic import BaseModel


class ArtifactGenOutput(BaseModel):
    """artifacts@1; `text` is Markdown."""

    title: str
    text: str
