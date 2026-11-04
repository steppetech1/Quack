"""E2E: the learner's text journey (ТЗ Ф4 §8.14, T-E1; WP26.1).

Sign-in by the link of the mail → terms → a domain (`POST /v1/domains`) → a session → a message
(the teacher turn: head, body, a task as an element) → an answer (the instant verdict) →
`task.closed` → `episode.closed` → `extract.episode` → the fold → the topic with its state code.
The processes of Phase 4 run in the stand (`tests/e2e/system.py`); the model is scripted."""

from __future__ import annotations

import json

from tests.e2e.journey import ok
from tests.e2e.system import System
from tests.e2e.text_journey import event_types, run_text_journey

async def test_learner_journey_text(system: System) -> None:
    """T-E1."""
    journey = await run_text_journey(system)
    learner = journey.learner
    await system.settle()
    types = await event_types(system, learner)
    log = ", ".join(f"{seq}:{t}" for seq, t in types)
    chain = [t for _, t in types if t in {"verdict.recorded", "task.closed", "episode.closed"}]
    assert chain[:3] == ["verdict.recorded", "task.closed", "episode.closed"], log
    assert "observation.extracted" in [t for _, t in types], log

    jobs = await system.resources.db.fetch_all(
        "SELECT status, required_seq FROM ops.jobs WHERE learner_id = %(learner_id)s "
        "AND kind = 'extract.episode'",
        {"learner_id": learner.learner_id},
    )
    assert [j["status"] for j in jobs] == ["succeeded"], jobs
    started = [s for s in system.job_starts if s.kind == "extract.episode"]
    required = jobs[0]["required_seq"] or 0
    assert started and all(s.applied_seq >= required for s in started), (started, required)

    card = ok(await system.client.get(f"/v1/topics/{journey.topic_id}", headers=learner.headers))
    obs = await system.resources.db.fetch_one(
        "SELECT seq, payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'observation.extracted'",
        {"learner_id": learner.learner_id},
    )
    assert obs is not None
    async with system.neo4j.session() as graph:
        result = await graph.run(
            "MATCH (e:Evidence {learnerId: $learner}) RETURN e.observationSeq AS observation, "
            "e.level AS level, e.verification AS verification",
            learner=str(learner.learner_id),
        )
        evidence = await result.data()
        result = await graph.run(
            "MATCH (s:Skill {learnerId: $learner}) RETURN properties(s) AS p",
            learner=str(learner.learner_id),
        )
        skills = [dict(r["p"]) for r in await result.data()]
    detail = json.dumps([card, skills, obs["payload"], evidence, log], default=str)
    # The extractor's element stands on level 1: level 2 of the answer has the code verdict.
    assert len(obs["payload"]["elements"]) == 1 and not obs["payload"]["rejected"], detail
    assert {"observation": obs["seq"], "level": 1, "verification": "model"} in evidence, detail
    assert card["status"] == "active", detail
    assert card["display"]["display_state"] in {"not_tested", "in_progress"}, detail
    [skill] = skills
    assert skill["l2SuccessW"] > 0, detail
    assert {p["kind"] for p in card["places"]} >= {"exposure", "evidence"}, detail
