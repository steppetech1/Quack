"""Short labels (ТЗ Фазы 3 §8.6c, WP14.1, Т-§4.5)."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.prompts.registry import PromptRegistry
from quack.session import turns
from quack.session.schemas import TeacherOutput
from tests.llm.prompt_contracts import (
    CONTRACTS_PATH,
    PROMPT_SCHEMAS,
    render_contracts,
    undeclared_names,
)
from tests.session.fixtures import FakeLLMProvider, SeededDomain
from tests.session.helpers import parse_sse

pytest_plugins = ["tests.session.fixtures"]


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


# --- T-M4 --------------------------------------------------------------------------------


async def _manifest_rows(db: SessionFactory, learner: UUID) -> list[Any]:
    async with db() as session:
        return list(
            (
                await session.execute(
                    text("SELECT * FROM ops.turn_manifests WHERE learner_id = :l"), {"l": learner}
                )
            ).all()
        )


async def _turn_events(db: SessionFactory, learner: UUID, key: str) -> int:
    async with db() as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM core.events WHERE learner_id = :l "
                        "AND idempotency_key LIKE :k"
                    ),
                    {"l": learner, "k": f"{key}%"},
                )
            ).scalar_one()
        )


async def test_turn_manifest_row_is_atomic(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    fake_llm: FakeLLMProvider,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-M4 (WP14.2, Т-§3.2): after a turn one `ops.turn_manifests` row with the prompt and
    renderer versions, `projected_seq`, `overlay_seqs`, `label_table` with every label of the
    request, `degraded_mode`; a failed write of the turn events → no manifest row, and back."""
    learner = seeded_domain.learner_id
    start = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "manifest-start",
        },
        headers=auth_headers,
    )
    assert start.status_code == 201, start.text
    session_id = UUID(start.json()["session_id"])

    fake_llm.push("teacher", TeacherOutput(reply_text="Let us start.", pedagogical_move="explain"))
    response = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Hello", "idempotency_key": "manifest-turn-1"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    sse = dict(parse_sse(response.text))
    turn_id = UUID(sse["turn_started"]["turn_id"])

    (row,) = await _manifest_rows(db, learner)
    assert row.turn_id == turn_id
    assert row.session_id == session_id
    assert row.prompt_version == 1
    assert row.renderer_version == 1
    assert row.role == "teacher"
    assert row.model == settings.model_for_role("teacher")
    assert row.reasoning_effort == "high"  # the first turn of the session (WP6.5)
    assert row.projected_seq == seeded_domain.applied_seq
    assert all(seq > row.projected_seq for seq in row.overlay_seqs)
    assert row.degraded_mode == sse["turn_completed"]["degraded_mode"]
    prompt = fake_llm.calls_for("teacher")[-1].prompt
    labels = set(re.findall(r"\b[STKM]\d+\b", prompt))
    assert labels
    assert labels <= set(row.label_table)
    assert {str(seeded_domain.topic_id), str(seeded_domain.skill_ids[0])} <= set(
        row.label_table.values()
    )

    # The manifest write fails → no events of the turn.
    async def broken_manifest(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("manifest write failed")

    monkeypatch.setattr(turns, "write_turn_manifest", broken_manifest)
    fake_llm.push("teacher", TeacherOutput(reply_text="Next.", pedagogical_move="explain"))
    with pytest.raises(RuntimeError):
        await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"text": "Go on", "idempotency_key": "manifest-turn-2"},
            headers=auth_headers,
        )
    assert await _turn_events(db, learner, "manifest-turn-2") == 0
    assert len(await _manifest_rows(db, learner)) == 1
    monkeypatch.undo()

    # The event write fails → no manifest row.
    async def broken_append(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("event write failed")

    monkeypatch.setattr(turns, "append_events", broken_append)
    fake_llm.push("teacher", TeacherOutput(reply_text="Next.", pedagogical_move="explain"))
    with pytest.raises(RuntimeError):
        await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"text": "Go on", "idempotency_key": "manifest-turn-3"},
            headers=auth_headers,
        )
    assert len(await _manifest_rows(db, learner)) == 1
