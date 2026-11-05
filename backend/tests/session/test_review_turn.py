"""Review inside the teacher turn (ТЗ Ф4 WP17.6; Т-§14.2): the candidates in the memory picture,
the intent `review` limits them, «давай 5 минут» caps them, the bank gives a new family.
No infrastructure: the reader is the S4 stub, the bank is replaced (D-B-189, D-B-190)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from quack.config import Settings
from quack.graph.read import (
    GraphReader,
    MemorySlice,
    ReviewCandidate,
    ReviewScope,
    SkillFact,
    SliceFocus,
)
from quack.llm.labels import LabelMap
from quack.practice.select import SelectedTask
from quack.session import review_turn
from quack.session.memory_picture import PictureInputs, ReviewTask, render_memory_picture
from quack.session.review_turn import bank_for_review, review_for_turn, time_limit_minutes
from quack.session.teacher_context import SessionGoal
from tests.support import graph_read as fx
from tests.support.graph_read import FixedGraphReader

SETTINGS = Settings()
NOW_MS = fx.NOW_MS
NEED, OTHER, THIRD = UUID(int=0x71), UUID(int=0x72), UUID(int=0x73)


class _Reader(FixedGraphReader):
    """Three candidates by priority; the scope of the call is kept."""

    scope: ReviewScope | None = None

    async def review_candidates(
        self, learner_id: UUID, now_ms: int, scope: ReviewScope | None = None
    ) -> list[ReviewCandidate]:
        self.scope = scope
        return [
            ReviewCandidate(
                skill_id=NEED, priority=0.9, reasons=["retention"], is_goal_prerequisite=True
            ),
            ReviewCandidate(skill_id=OTHER, priority=0.6, reasons=["consolidation"]),
            ReviewCandidate(skill_id=THIRD, priority=0.3, reasons=["retention"]),
        ]


async def _review(goal: SessionGoal, texts: list[str] | None = None) -> tuple[Any, _Reader]:
    reader = _Reader()
    found = await review_for_turn(
        reader,
        learner_id=fx.LEARNER,
        now_ms=NOW_MS,
        goal=goal,
        goal_skill_ids=[fx.SKILL_QUIET],
        texts=texts or [],
        settings=SETTINGS,
    )
    return [c.skill_id for c in found], reader


async def test_goal_prerequisite_is_due_for_review_in_the_picture() -> None:
    """Item 1: a prerequisite of the goal comes with the note «due for review»; the other
    candidates stay out of a learn session; the goal skills reach the reader."""
    chosen, reader = await _review(SessionGoal(intent="learn"))
    assert chosen == [NEED]
    assert reader.scope is not None and reader.scope.goal_skill_ids == [fx.SKILL_QUIET]
    skill = SkillFact(skill_id=NEED, name="Fractions", levels=[], due_at_ms=None)
    inputs = PictureInputs(
        slice=MemorySlice(learner_id=fx.LEARNER, projected_seq=1, skills=[skill]),
        focus=SliceFocus(skill_ids=[], topic_ids=[], domain_id=None),
        skill_names={NEED: "Fractions"},
        review=chosen,
    )
    labels = LabelMap.build(SETTINGS, skills=[NEED])
    text = render_memory_picture(inputs, labels, SETTINGS, NOW_MS)
    assert text.startswith("Fractions (S1).") and text.endswith("due for review.")


async def test_review_intent_limits_candidates_to_topic_or_hub() -> None:
    """Item 2: intent `review` with a topic or a hub passes it as the scope and keeps every
    candidate the reader returns."""
    topic, hub = uuid4(), uuid4()
    chosen, reader = await _review(SessionGoal(intent="review", topic_id=topic))
    assert chosen == [NEED, OTHER, THIRD]
    assert reader.scope is not None and reader.scope.topic_id == topic
    _, reader = await _review(SessionGoal(intent="review", hub_id=hub))
    assert reader.scope is not None and reader.scope.hub_id == hub
    _, reader = await _review(SessionGoal(intent="learn", topic_id=topic))
    assert reader.scope is not None and reader.scope.topic_id is None


async def test_five_minutes_keeps_the_top_candidates() -> None:
    """Item 3: «давай 5 минут» — 5 / `review_item_minutes` items (3 at 1.5), by priority;
    the latest limit of the session wins; no limit — no cut."""
    assert time_limit_minutes(["hi", "давай 5 минут"]) == 5
    assert time_limit_minutes(["let's do 3 min", "лучше 10 минут"]) == 10
    assert time_limit_minutes(["no limit here"]) is None
    goal = SessionGoal(intent="review", topic_id=uuid4())
    chosen, _ = await _review(goal, ["давай 5 минут"])
    assert chosen == [NEED, OTHER, THIRD]  # 5 // 1.5 = 3
    chosen, _ = await _review(goal, ["давай 2 минуты"])
    assert chosen == [NEED]  # at least one


async def test_bank_gives_a_new_family_task_for_a_candidate(monkeypatch: Any) -> None:
    """Item 4: the repeat is a `select_tasks` pick (family not met) at the frontier level; its
    condition reaches the skill line with the task label."""
    task_id = uuid4()
    calls: list[dict[str, Any]] = []

    async def fake_select(_driver: Any, _settings: Any, **kw: Any) -> list[SelectedTask]:
        calls.append(kw)
        return [SelectedTask(task_id=task_id, traps_target=False, checked=True)]

    async def fake_get_task(_db: Any, **_kw: Any) -> Any:
        return SimpleNamespace(condition=SimpleNamespace(text="Reduce 4/6"))

    monkeypatch.setattr(review_turn, "select_tasks", fake_select)
    monkeypatch.setattr(review_turn, "get_task", fake_get_task)
    reader: GraphReader = _Reader()
    candidate = ReviewCandidate(skill_id=fx.SKILL_FAILED, priority=1.0, reasons=["retention"])
    bank = await bank_for_review(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        reader,
        SETTINGS,
        learner_id=fx.LEARNER,
        candidates=[candidate],
        targets={fx.SKILL_FAILED: [2, 3]},
    )
    assert calls[0]["skill_id"] == fx.SKILL_FAILED and calls[0]["level"] in (2, 3)
    assert bank == {fx.SKILL_FAILED: [ReviewTask(task_id, "Reduce 4/6")]}
    focus = SliceFocus(skill_ids=[fx.SKILL_FAILED], topic_ids=[], domain_id=None)
    memory = await reader.slice_for_turn(fx.LEARNER, uuid4(), focus)
    inputs = PictureInputs(
        slice=memory,
        focus=focus,
        skill_names={fx.SKILL_FAILED: "Adding fractions"},
        review=[fx.SKILL_FAILED],
        review_tasks=bank,
    )
    labels = LabelMap.build(SETTINGS, skills=[fx.SKILL_FAILED], tasks=[task_id])
    text = render_memory_picture(inputs, labels, SETTINGS, NOW_MS)
    assert 'Repeat with a new family, bank task K1: "Reduce 4/6".' in text
