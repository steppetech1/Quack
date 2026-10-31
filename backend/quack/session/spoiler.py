"""Spoiler guard: the answer of the active task is not shown to the learner (Т-§12.4, WP15).

Checked on the full `reply_text` before the first SSE `token` (ТЗ §2.3 row 16). On a
violation the turn goes back to the model up to `guard_max_retries` times; if it remains,
code removes the sentences with the answer (Т-§12.1). Small integers (|x| below
`spoiler_ignore_abs_below`) and one-letter answers are not checked; `open` tasks have no
reference answer and are not checked.
"""

from __future__ import annotations

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.practice.schemas import ReferenceSolution


async def spoiler_sentences(
    reply_text: str,
    reference: ReferenceSolution,
    settings: Settings,
    compute: ComputeClient | None = None,
) -> list[str]:
    """Sentences of `reply_text` that reveal the reference answer; empty if none."""
    raise NotImplementedError


def remove_sentences(reply_text: str, sentences: list[str]) -> str:
    """`reply_text` without `sentences`, the rest unchanged."""
    raise NotImplementedError
