"""A chat on a whole domain (no topic, no hub in the session goal) still gets the skills of the
domain's topics into the turn, so the teacher can name a skill (a `task_generation` request
needs one). Found on the live stand on 2026-11-25: a new subject had its topic in the label
table but none of its skills."""

from __future__ import annotations

from uuid import UUID, uuid4

from quack.graph.read.models import HubGoal, HubTopicGoal
from quack.session.turn_source import skill_topics


def _hub(*topic_ids: UUID) -> HubGoal:
    return HubGoal(
        hub_id=uuid4(),
        name="hub",
        kind="domain_all",
        control_type="tasks",
        topics=[HubTopicGoal(topic_id=t, name="t", required_levels=[3]) for t in topic_ids],
    )


def test_goal_topics_win() -> None:
    goal, other = uuid4(), uuid4()
    assert skill_topics([goal], [_hub(other)], limit=5) == [goal]


def test_a_domain_chat_reads_the_topics_of_its_hubs_once_and_capped() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    hubs = [_hub(a, b), _hub(b, c)]
    assert skill_topics([], hubs, limit=5) == [a, b, c]
    assert skill_topics([], hubs, limit=2) == [a, b]


def test_no_hubs_no_topics() -> None:
    assert skill_topics([], [], limit=5) == []
