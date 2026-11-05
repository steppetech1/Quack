"""ТЗ Ф4 WP7.3 (`Т-§2.8`, `Т-§7.10`): coverage of an episode is written together with its
observation, in one transaction; a repeat replaces the row; nothing is left on a rollback."""

from __future__ import annotations

from typing import Any

import pytest

from quack.db import Db
from quack.jobs.coverage import mark_covered
from tests.db.fixtures import create_learner

EXTRACTOR = "extractor@3"


class _ObservationFailed(Exception):
    """The write of the observation failed after the coverage row was written."""


async def _rows(db: Db) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT learner_id, episode_seq, extractor, status, observation_seq FROM ops.coverage "
        "ORDER BY episode_seq",
        {},
    )


async def test_extracted_and_nothing_to_extract(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    await mark_covered(
        pg_db,
        learner_id,
        episode_seq=10,
        extractor=EXTRACTOR,
        status="extracted",
        observation_seq=15,
    )
    await mark_covered(
        pg_db,
        learner_id,
        episode_seq=20,
        extractor=EXTRACTOR,
        status="nothing_to_extract",
        observation_seq=None,
    )
    assert [(r["episode_seq"], r["status"], r["observation_seq"]) for r in await _rows(pg_db)] == [
        (10, "extracted", 15),
        (20, "nothing_to_extract", None),
    ]


async def test_repeat_replaces_the_row(pg_db: Db) -> None:
    """Law 5: the same call twice is one row; a re-extraction points at the new observation."""
    learner_id = await create_learner(pg_db)
    for observation_seq in (15, 15, 31):
        await mark_covered(
            pg_db,
            learner_id,
            episode_seq=10,
            extractor=EXTRACTOR,
            status="extracted",
            observation_seq=observation_seq,
        )
    assert [r["observation_seq"] for r in await _rows(pg_db)] == [31]


async def test_rollback_of_the_observation_leaves_no_coverage(pg_db: Db) -> None:
    """Law 7: written in the caller's transaction, it goes with it."""
    learner_id = await create_learner(pg_db)
    with pytest.raises(_ObservationFailed):
        async with pg_db.transaction():
            await mark_covered(
                pg_db,
                learner_id,
                episode_seq=10,
                extractor=EXTRACTOR,
                status="extracted",
                observation_seq=15,
            )
            raise _ObservationFailed
    assert await _rows(pg_db) == []


async def test_status_and_observation_must_agree(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    with pytest.raises(ValueError):
        await mark_covered(
            pg_db,
            learner_id,
            episode_seq=10,
            extractor=EXTRACTOR,
            status="extracted",
            observation_seq=None,
        )
    with pytest.raises(ValueError):
        await mark_covered(
            pg_db,
            learner_id,
            episode_seq=10,
            extractor=EXTRACTOR,
            status="nothing_to_extract",
            observation_seq=15,
        )
    assert await _rows(pg_db) == []
