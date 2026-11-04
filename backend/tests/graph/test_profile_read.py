"""WP5.3: `read_profile` on Neo4j (Т-§9.5) — the Profile node written by Phase B, read back.

Uses `neo4j_driver` and its own learner ids (the `neo4j` fixture wipes the whole graph, which
other test files share); every test deletes only its own nodes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver, Query

from quack.config import Settings
from quack.graph.profile_fold import (
    ProfileInputs,
    ProfileParams,
    fold_profile,
    profile_id,
    profile_node_props,
)
from quack.graph.read.models import ProfileFacts
from quack.graph.read.neo4j_profile import read_profile
from quack.graph.read.reader import create_graph_reader


@pytest.fixture
async def learners(neo4j_driver: AsyncDriver) -> AsyncIterator[list[UUID]]:
    ids: list[UUID] = []
    yield ids
    async with neo4j_driver.session() as session:
        await session.run(
            "MATCH (p:Profile) WHERE p.learnerId IN $ids DETACH DELETE p",
            {"ids": [str(i) for i in ids]},
        )


async def _write(driver: AsyncDriver, learner: UUID, inputs: ProfileInputs) -> None:
    """What Phase B does with the fold: set present properties, remove the absent `learned*`."""
    props, unset = profile_node_props(inputs, ProfileParams())
    async with driver.session() as session:
        await session.run(
            Query(
                "MERGE (p:Profile {id: $id}) SET p.learnerId = $learner, p += $props "  # type: ignore[arg-type]
                + "".join(f"REMOVE p.{key} " for key in unset)
            ),
            {"id": profile_id(learner), "learner": str(learner), "props": props},
        )


async def test_profile_round_trip_and_no_data(
    neo4j_driver: AsyncDriver, learners: list[UUID]
) -> None:
    learner = uuid4()
    learners.append(learner)
    full = ProfileInputs(
        prefs={"prefLanguage": "ru", "prefAnswerLength": "short", "prefQuietCanvas": True},
        manner="short steps",
    )
    await _write(neo4j_driver, learner, full)
    facts = await read_profile(neo4j_driver, learner)
    assert facts == ProfileFacts(
        learner_id=learner,
        prefs={"language": "ru", "answer_length": "short", "quiet_canvas": True},
        manner="short steps",
    )  # nothing learned yet: «no data», not zeros


async def test_learned_values_are_read_and_cleared(
    neo4j_driver: AsyncDriver, learners: list[UUID]
) -> None:
    learner = uuid4()
    learners.append(learner)
    async with neo4j_driver.session() as session:
        await session.run(
            "MERGE (p:Profile {id: $id}) SET p.learnerId = $learner, "
            "p.learnedHelpfulApproach = 'small_steps', "
            "p.learnedConfidenceBrier = 0.21, p.learnedConfidenceBias = 'over', "
            "p.learnedBranchRate = 0.0, p.learnedReasoningShare = 0.4",
            {"id": profile_id(learner), "learner": str(learner)},
        )
    facts = await read_profile(neo4j_driver, learner)
    assert facts.learned_helpful_approach == "small_steps"
    assert facts.learned_confidence_brier == 0.21
    assert facts.learned_confidence_bias == "over"
    assert facts.learned_branch_rate == 0.0  # zero is data, not "no data"
    assert facts.learned_reasoning_share == 0.4
    # The fold has no data any more (e.g. after a rebuild with a smaller journal): removed.
    await _write(neo4j_driver, learner, ProfileInputs())
    cleared = await read_profile(neo4j_driver, learner)
    assert cleared == ProfileFacts(learner_id=learner)
    assert fold_profile(ProfileInputs(), ProfileParams()).to_props() == {}


async def test_no_profile_is_an_empty_profile_and_learners_are_isolated(
    neo4j_driver: AsyncDriver, learners: list[UUID]
) -> None:
    mine, other = uuid4(), uuid4()
    learners.extend([mine, other])
    await _write(neo4j_driver, other, ProfileInputs(prefs={"prefLanguage": "de"}, manner="m"))
    assert await read_profile(neo4j_driver, mine) == ProfileFacts(learner_id=mine)
    assert (await read_profile(neo4j_driver, other)).prefs == {"language": "de"}
    reader = create_graph_reader(neo4j_driver, Settings(), learner_id=mine)
    assert await reader.profile(other) == ProfileFacts(learner_id=other)  # law 1, D-A-144
    await _write(neo4j_driver, mine, ProfileInputs(prefs={"prefLanguage": "ru"}))
    assert (await read_profile(neo4j_driver, mine)).prefs == {"language": "ru"}
    assert (await read_profile(neo4j_driver, other)).prefs == {"language": "de"}
    assert (await reader.profile(mine)).prefs == {"language": "ru"}
