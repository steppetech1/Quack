"""Memory tools of the teacher (ТЗ Ф4 WP14.4; `T-CX4`): six tools of Т-§10.4 — row limits of
`memory_tool_limits`, labels instead of ids, Modal down → "search temporarily unavailable",
bad arguments → an error text and the turn goes on (D-B-163)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

from quack.config import Settings
from quack.graph.read import (
    EvidenceFact,
    GraphReader,
    LevelFact,
    PastSolution,
    PrerequisiteFact,
    SimilarMistake,
)
from quack.llm.labels import LabelMap
from quack.session.tools import (
    SEARCH_UNAVAILABLE,
    TOOLS,
    CanvasLine,
    MaterialChunk,
    ToolContext,
    VoiceSegment,
    render_chunks,
    run_tool,
)
from tests.support import graph_read as fx
from tests.support.graph_read import FixedGraphReader

SETTINGS = Settings()
LEARNER = uuid4()
SKILL = uuid4()
PREREQ = uuid4()
WEAK = uuid4()
TASK = uuid4()
DOMAIN = uuid4()
OTHER_DOMAIN = uuid4()
NOW_MS = 1_800_000_000_000
DAY_MS = 86_400_000


class _Reader:
    """`GraphReader` double for the tool calls: more rows than every limit."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.evidence = [
            EvidenceFact(
                evidence_id=uuid4(),
                skill_id=SKILL,
                level=3,
                outcome="incorrect" if i == 0 else "correct",
                source="code",
                quote="n!/n^n" if i == 0 else None,
                task_id=TASK if i == 0 else None,
                assistance="hint" if i == 1 else "shown_solution" if i == 2 else "none",
                hint_step=2 if i == 1 else None,
                seq=i,
                occurred_at_ms=NOW_MS - DAY_MS,
            )
            for i in range(15)
        ]

    async def skill_history(
        self, skill_id: UUID, level: int | None = None, limit: int = 10
    ) -> list[EvidenceFact]:
        self.calls.append(("skill_history", (skill_id, level, limit)))
        return self.evidence

    async def prerequisites(self, skill_id: UUID, limit: int = 10) -> list[PrerequisiteFact]:
        self.calls.append(("prerequisites", (skill_id, limit)))
        return [
            PrerequisiteFact(
                skill_id=PREREQ, confidence=0.9, levels=[LevelFact(level=3, status="proven")]
            ),
            PrerequisiteFact(skill_id=WEAK, confidence=0.3, levels=[]),
        ]

    async def similar_mistakes(
        self, learner_id: UUID, evidence_id: UUID | None = None, text: str | None = None
    ) -> list[SimilarMistake]:
        self.calls.append(("similar_mistakes", (learner_id, evidence_id, text)))
        return [
            SimilarMistake(
                evidence_id=uuid4(), skill_id=PREREQ, domain_id=OTHER_DOMAIN, quote=f"slip {i}"
            )
            for i in range(8)
        ]

    async def past_solution(
        self, learner_id: UUID, task_id: UUID | None = None, skill_id: UUID | None = None
    ) -> list[PastSolution]:
        self.calls.append(("past_solution", (learner_id, task_id, skill_id)))
        return [
            PastSolution(
                task_id=TASK,
                skill_id=SKILL,
                lines=["a_n = n!", "ratio -> 0"],
                occurred_at_ms=NOW_MS - 2 * DAY_MS,
            )
            for _ in range(4)
        ]


def _ctx(reader: _Reader, **kw: Any) -> ToolContext:
    base: dict[str, Any] = {
        "learner_id": LEARNER,
        "labels": LabelMap.build(SETTINGS, skills=[SKILL], tasks=[TASK]),
        "domain_id": DOMAIN,
        "reader": cast(GraphReader, reader),
        "settings": SETTINGS,
        "now_ms": NOW_MS,
        "skill_names": {SKILL: "D'Alembert test", PREREQ: "Limits", WEAK: "Factorials"},
    }
    base.update(kw)
    return ToolContext(**base)


def _ids_absent(text: str, *ids: UUID) -> None:
    for object_id in ids:
        assert str(object_id) not in text


async def test_memory_tools() -> None:
    """`T-CX4`: each tool reads through `quack.graph.read` with labels resolved to ids, cuts at
    its limit and answers with labels, never ids."""
    assert [t.name for t in TOOLS] == [
        "skill_history",
        "similar_mistakes",
        "past_solution",
        "voice_at_line",
        "search_materials",
        "prerequisites",
    ]
    reader = _Reader()
    ctx = _ctx(reader)
    limits = SETTINGS.memory_tool_limits

    history = await run_tool("skill_history", json.dumps({"skill": "S1", "level": 3}), ctx)
    lines = history.splitlines()
    assert len(lines) == limits["skill_history"]
    assert lines[0] == 'E1: task K1, level 3 Applies, wrong, checked by code, yesterday: "n!/n^n"'
    assert (
        lines[1] == "E2: level 3 Applies, right, checked by code, after hint step 2 of 3, yesterday"
    )
    assert lines[2] == (
        "E3: level 3 Applies, right, checked by code, after the solution was shown, yesterday"
    )
    assert reader.calls[-1] == ("skill_history", (SKILL, 3, limits["skill_history"]))
    _ids_absent(history, SKILL, TASK, *(e.evidence_id for e in reader.evidence))

    # an evidence label shown by a tool is valid for the next call of this turn
    similar = await run_tool("similar_mistakes", json.dumps({"evidence": "E1", "text": None}), ctx)
    assert reader.calls[-1] == ("similar_mistakes", (LEARNER, reader.evidence[0].evidence_id, None))
    rows = similar.splitlines()
    assert len(rows) == limits["similar_mistakes"]
    assert rows[0].endswith(': Limits (another subject): "slip 0"')
    assert rows[0].startswith("E11: ")

    past = await run_tool("past_solution", json.dumps({"task": "K1", "skill": None}), ctx)
    assert past.count("Solution of") == limits["past_solution"]
    assert past.splitlines()[:3] == [
        "Solution of S1 D'Alembert test, task K1, 2 days ago:",
        "  a_n = n!",
        "  ratio -> 0",
    ]
    assert reader.calls[-1] == ("past_solution", (LEARNER, TASK, None))

    prereq = await run_tool("prerequisites", json.dumps({"skill": "S1"}), ctx)
    assert prereq.splitlines() == [
        "Limits: Applies — proven",
        "Factorials (uncertain link): not checked",
    ]
    assert reader.calls[-1] == ("prerequisites", (SKILL, limits["prerequisites"]))
    for text in (similar, past, prereq):
        _ids_absent(text, SKILL, PREREQ, WEAK, TASK, OTHER_DOMAIN)


async def test_voice_at_line() -> None:
    """`voice_at_line(L…)`: what the learner said while writing the line (time overlap), at most
    `memory_tool_limits['voice_at_line']`."""
    ctx = _ctx(
        _Reader(),
        canvas_lines={"L2": CanvasLine(text="x^2", started_at_ms=1_000, ended_at_ms=2_000)},
        voice=[
            VoiceSegment(text="before", started_at_ms=0, ended_at_ms=900),
            VoiceSegment(text="squared, I think", started_at_ms=1_500, ended_at_ms=2_500),
            VoiceSegment(text="still talking", started_at_ms=1_900, ended_at_ms=3_000),
        ],
    )
    assert await run_tool("voice_at_line", json.dumps({"line": "L2"}), ctx) == "squared, I think"
    assert (await run_tool("voice_at_line", json.dumps({"line": "L9"}), ctx)).startswith("error:")


async def test_search_materials_and_modal_down() -> None:
    """`search_materials`: chunks as quotes with their source and labels `P…`, limit; Modal
    down → "search temporarily unavailable", also for `similar_mistakes(text)` (§4.4)."""
    asked: list[tuple[str, Sequence[UUID], int]] = []
    chunk_ids = [uuid4() for _ in range(7)]

    async def search(query: str, skills: Sequence[UUID], k: int) -> list[MaterialChunk]:
        asked.append((query, skills, k))
        return [MaterialChunk(c, "Lecture 5", 12 + i, f"text {i}") for i, c in enumerate(chunk_ids)]

    reader = _Reader()
    ctx = _ctx(reader, search=search)
    found = await run_tool(
        "search_materials", json.dumps({"query": "ratio", "skills": ["S1"]}), ctx
    )
    assert asked == [("ratio", [SKILL], SETTINGS.memory_tool_limits["search_materials"])]
    assert found.splitlines()[:2] == ['P1. From the material "Lecture 5", page 12:', "> text 0"]
    assert found.count("From the material") == SETTINGS.memory_tool_limits["search_materials"]
    _ids_absent(found, *chunk_ids)
    nameless = render_chunks([MaterialChunk(chunk_ids[0], None, 3, "text")], lambda _: "P1")
    assert nameless.splitlines()[0] == "P1. From a material, page 3:"  # D-B-171

    down = _ctx(_Reader(), search=None)
    assert await run_tool("search_materials", json.dumps({"query": "q", "skills": []}), down) == (
        SEARCH_UNAVAILABLE
    )
    text_call = json.dumps({"evidence": None, "text": "wrong sign"})
    assert await run_tool("similar_mistakes", text_call, down) == SEARCH_UNAVAILABLE
    assert SEARCH_UNAVAILABLE == "search temporarily unavailable"


async def test_bad_calls_are_error_texts() -> None:
    """Т-§11.5: an unknown tool, bad JSON, an unknown label — an error text, the turn goes on."""
    reader = _Reader()
    ctx = _ctx(reader)
    for name, args in (
        ("forget_everything", "{}"),
        ("skill_history", "not json"),
        ("skill_history", json.dumps({"skill": "S7", "level": None})),
        ("past_solution", json.dumps({"task": "K1", "skill": "S1"})),
        ("similar_mistakes", json.dumps({"evidence": "E5", "text": None})),
    ):
        out = await run_tool(name, args, ctx)
        assert out.startswith("error:"), (name, out)
    assert reader.calls == []


async def test_skill_history_help_on_the_s4_stub() -> None:
    """`Т-§10.4` «задача, исход, помощь, цитата, дата» on `FixedGraphReader` (D-A-127): the
    evidence of the failed skill shows its hint step."""
    ctx = _ctx(
        cast(_Reader, FixedGraphReader()),
        labels=LabelMap.build(SETTINGS, skills=[fx.SKILL_FAILED]),
        skill_names={fx.SKILL_FAILED: "Adding fractions"},
    )
    text = await run_tool("skill_history", json.dumps({"skill": "S1", "level": None}), ctx)
    assert f"after hint step {fx.HINT_STEP} of 3" in text
