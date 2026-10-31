"""Hot path of Phase 3 (ТЗ Фазы 3 §8.5)."""

from __future__ import annotations

import itertools

import pytest

from quack.session.turn_policy import resolve_degraded_mode

_FLAGS = ("budget_exceeded", "llm_unavailable", "memory_unavailable", "teacher_fallback")
_ORDER = (*_FLAGS, "projector_lag")


@pytest.mark.parametrize("combo", list(itertools.product((False, True), repeat=5)))
def test_resolve_degraded_mode_priority(combo: tuple[bool, ...]) -> None:
    """T-H3: all 2^5 combinations → the first true reason by the priority of §4.4."""
    flags = dict(zip(_ORDER, combo, strict=True))
    expected = next((name for name in _ORDER if flags[name]), None)
    assert resolve_degraded_mode(**flags) == expected
