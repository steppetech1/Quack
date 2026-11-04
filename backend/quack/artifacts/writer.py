"""Output schema of the `artifact_writer@1` role (ТЗ Ф4 WP23.2–WP23.5, S8; Т-§17.2–Т-§17.6).

One role writes every artifact type; the system template of the type (Т-§17.3 layer 1) says
which part of the output it fills: notes and the hub cheat sheet — `blocks`, topic cards —
`cards`. Skills, misconceptions and material chunks are short labels (`S1`, `M2`, `P3`,
Т-§4.5) that code translates to ids; a block that updates a block of the current version names
it by its number in the input (`updates`), so the writer never sees block ids and can never
address a learner block directly (code turns such an update into a supplement, D-C-125).

What a learner skill cannot cancel is checked by code on the output (Т-§17.3): markup of every
card (`quack.practice.markup.validate_markup`, WP12.3), a chunk reference on definitions and
statements from materials, allowed block kinds — the schema itself holds the last one. The
schema lives in the package that calls the role (§7.2 S8); `tests/llm/prompt_contracts.py`
registers it (D-C-126).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from quack.artifacts.documents import BlockKind, CardKind
from quack.practice.markup import TaskMarkup


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WrittenBlock(_Strict):
    """A block of notes or of a cheat sheet (Т-§17.2). `updates` — the number (from 1) of the
    block of the current version it replaces; null — a new block. `sources` — chunk labels
    (`P…`) the block rests on: a definition or statement from materials without one is
    rejected by code (Т-§17.4); a block without sources is an explanation of the system.
    `formulas` — the identities of the block that can be checked symbolically (`left = right`,
    LaTeX): code sends each to compute; one that does not pass is a violation, and after the
    second generation the block is marked «не проверено» (WP23.3, D-C-156)."""

    updates: Annotated[int, Field(ge=1)] | None
    kind: BlockKind
    content: str
    sources: list[str]
    skills: list[str]
    formulas: list[str] = Field(default_factory=list[str])


class WrittenCard(_Strict):
    """A card is a task with markup (Т-§17.5): what the learner sees and its markup (Т-§5.2)."""

    kind: CardKind
    prompt: str
    markup: TaskMarkup


class ArtifactWriterOutput(_Strict):
    blocks: list[WrittenBlock]
    cards: list[WrittenCard]
