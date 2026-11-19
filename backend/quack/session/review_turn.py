"""Review inside the teacher turn (ТЗ Ф4 WP17.6; Т-§14.2). D-B-189, D-B-190.

The data is `GraphReader.review_candidates` (A). Goal prerequisites go to the memory picture
as «due for review»; the intent `review` with a topic or a hub limits the candidates to it;
«давай 5 минут» — a time limit stated by the learner in the session — keeps as many top
candidates as fit `review_item_minutes` each. Good repeats come from the bank (`select_tasks`,
WP12.7): a family the learner has not met on the skill.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import UUID

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db.db import Db
from quack.events.values import Level
from quack.graph.read import GraphReader, ReviewCandidate, ReviewScope
from quack.practice.select import select_tasks
from quack.practice.task_store import get_task
from quack.session.memory_picture import ReviewTask
from quack.session.teacher_context import SessionGoal

ALL_LEVELS: list[Level] = [1, 2, 3, 4, 5, 6]

# «давай 5 минут», «let's do 10 min»: a number before «мин…» / «min…» (D-B-189).
_LIMIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:мин|min)", re.IGNORECASE)


def time_limit_minutes(texts: Sequence[str]) -> float | None:
    """The latest time limit the learner named in the session, in minutes."""
    for text in reversed(texts):
        found = _LIMIT.search(text)
        if found:
            return float(found.group(1).replace(",", "."))
    return None


def review_item_cap(minutes: float | None, settings: Settings) -> int | None:
    """How many items fit the limit at `review_item_minutes` each; at least one."""
    if minutes is None:
        return None
    return max(1, int(minutes // settings.review_item_minutes))


def select_for_goal(
    found: Sequence[ReviewCandidate], goal: SessionGoal, cap: int | None
) -> list[ReviewCandidate]:
    """Intent `review`: everything the scope returned; otherwise only the prerequisites of
    the goal. By priority (the reader's order), at most `cap`."""
    chosen = [c for c in found if goal.intent == "review" or c.is_goal_prerequisite]
    return chosen[:cap] if cap is not None else chosen


async def review_for_turn(
    reader: GraphReader,
    *,
    learner_id: UUID,
    now_ms: int,
    goal: SessionGoal,
    goal_skill_ids: Sequence[UUID],
    texts: Sequence[str],
    settings: Settings,
) -> list[ReviewCandidate]:
    """The candidates to repeat in this turn (WP17.6)."""
    scope = ReviewScope(
        topic_id=goal.topic_id if goal.intent == "review" else None,
        hub_id=goal.hub_id if goal.intent == "review" else None,
        goal_skill_ids=list(goal_skill_ids),
    )
    found = await reader.review_candidates(learner_id, now_ms, scope)
    cap = review_item_cap(time_limit_minutes(texts), settings)
    return select_for_goal(found, goal, cap)


async def bank_for_review(
    neo4j: AsyncDriver,
    db: Db,
    reader: GraphReader,
    settings: Settings,
    *,
    learner_id: UUID,
    candidates: Sequence[ReviewCandidate],
    targets: dict[UUID, list[Level]],
) -> dict[UUID, list[ReviewTask]]:
    """Per candidate the first checked bank task (Т-§12.4) of a family not met yet, at the frontier
    level of the skill (the levels of `targets`, or all levels without a hub). At most
    `label_max_tasks` candidates, so every task can have a label."""
    found: dict[UUID, list[ReviewTask]] = {}
    for candidate in candidates[: settings.label_max_tasks]:
        levels = targets.get(candidate.skill_id) or ALL_LEVELS
        frontier = await reader.frontier(candidate.skill_id, levels)
        picked = await select_tasks(
            neo4j,
            settings,
            learner_id=learner_id,
            skill_id=candidate.skill_id,
            level=frontier.level if frontier.level is not None else levels[-1],
        )
        # Т-§12.4 «Сгенерированное проверено»: a bank task is offered only with `verification =
        # checked`; `select_tasks` puts checked ones first (Т-§5.7), so this is the first pick.
        chosen = next((t for t in picked if t.checked), None)
        if chosen is None:
            continue
        task = await get_task(db, learner_id=learner_id, task_id=chosen.task_id)
        if task is not None:
            found[candidate.skill_id] = [ReviewTask(chosen.task_id, task.condition.text)]
    return found
