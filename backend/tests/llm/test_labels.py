"""Short labels (ТЗ Фазы 3 §8.6c, WP14.1, Т-§4.5)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.prompts.registry import PromptRegistry
from tests.llm.prompt_contracts import (
    CONTRACTS_PATH,
    PROMPT_SCHEMAS,
    render_contracts,
    undeclared_names,
)


def _ids(n: int) -> list[UUID]:
    return [uuid4() for _ in range(n)]


def test_label_map_roundtrip_and_limits(settings: Settings) -> None:
    """T-M1: S/T/K/M labels in the order given; nothing beyond the limits; resolve → ids."""
    skills, topics, tasks, misconceptions = _ids(20), _ids(10), _ids(7), _ids(3)
    labels = LabelMap.build(
        settings, skills=skills, topics=topics, tasks=tasks, misconceptions=misconceptions
    )

    assert settings.candidate_skills_k == 15
    assert settings.label_max_topics == 8
    assert settings.label_max_tasks == 5
    assert [labels.label("skill", s) for s in skills[:15]] == [f"S{i}" for i in range(1, 16)]
    assert [labels.label("topic", t) for t in topics[:8]] == [f"T{i}" for i in range(1, 9)]
    assert [labels.label("task", k) for k in tasks[:5]] == [f"K{i}" for i in range(1, 6)]
    assert [labels.label("misconception", m) for m in misconceptions] == ["M1", "M2", "M3"]

    assert all(labels.label("skill", s) is None for s in skills[15:])
    assert all(labels.label("topic", t) is None for t in topics[8:])
    assert all(labels.label("task", k) is None for k in tasks[5:])

    assert labels.resolve("skill", "S1") == skills[0]
    assert labels.resolve("skill", "S15") == skills[14]
    assert labels.resolve("topic", "T8") == topics[7]
    assert labels.resolve("task", "K5") == tasks[4]
    assert labels.resolve("misconception", "M3") == misconceptions[2]

    table = labels.table()
    assert len(table) == 15 + 8 + 5 + 3
    assert table["S1"] == str(skills[0])
    assert table["M2"] == str(misconceptions[1])


def test_label_map_rejects_unknown_and_foreign_labels(settings: Settings) -> None:
    """T-M1: a label beyond the table or of another kind is an `UnknownLabelError`."""
    labels = LabelMap.build(settings, skills=_ids(2), tasks=_ids(1))

    with pytest.raises(UnknownLabelError):
        labels.resolve("skill", "S3")
    with pytest.raises(UnknownLabelError):
        labels.resolve("task", "S1")
    with pytest.raises(UnknownLabelError):
        labels.resolve("skill", "s1")


def test_label_map_follows_settings_limits(settings: Settings) -> None:
    """T-M1: limits come from `Settings`, not from code."""
    small = settings.model_copy(
        update={"candidate_skills_k": 2, "label_max_topics": 1, "label_max_tasks": 1}
    )
    skills = _ids(3)
    labels = LabelMap.build(small, skills=skills, topics=_ids(2), tasks=_ids(2))
    assert labels.label("skill", skills[1]) == "S2"
    assert labels.label("skill", skills[2]) is None
    assert sorted(labels.table()) == ["K1", "S1", "S2", "T1"]


@pytest.mark.parametrize("ref", sorted(PROMPT_SCHEMAS))
def test_prompt_mentions_only_declared_fields(ref: str) -> None:
    """T-M5: backticked names of `## Output contract` are fields of the role schema or
    labels of `LabelMap`."""
    role, version = ref.split("@")
    text = PromptRegistry().load(role, int(version)).text
    assert undeclared_names(ref, text) == []


def test_prompt_contracts_file_is_current() -> None:
    """`backend/PROMPT_CONTRACTS.md` is built from code (§7.6, S4)."""
    assert CONTRACTS_PATH.read_text(encoding="utf-8") == render_contracts()
