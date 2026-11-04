"""E2E: laws 1, 7, 9, 4 and 10 on the stand (ТЗ Ф4 §8.14: T-E4, T-E5, T-E10, T-E14).

The text journey of `tests/e2e/text_journey.py` writes the log; the tests end the session to
close the episode (the boundary `session.ended`), so they do not depend on the close of a solved
task (D-C-191)."""

from __future__ import annotations

import re
from typing import Any

from quack.graph.projection.rebuild import rebuild_learner
from tests.e2e.journey import onboard
from tests.e2e.system import System
from tests.e2e.text_journey import (
    Journey,
    act,
    event_types,
    run_text_journey,
    script_extractor,
    script_teacher,
    start_session,
)
from tests.session.helpers import parse_sse
from tests.support.graph_dump import dump_learner
from tests.support.llm import ScriptedFailure

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Names a model output must not carry: strength is assigned by code (law 4, T-X3).
STRENGTH_WORDS = ("strength", "weight", "mastery", "probability", "stability")


async def end_session(system: System, journey: Journey) -> None:
    ended = await system.client.post(
        f"/v1/sessions/{journey.session_id}/end", headers=journey.learner.headers
    )
    assert ended.status_code == 200, ended.text


async def count_of(system: System, journey: Journey, type_: str) -> int:
    row = await system.resources.db.fetch_one(
        "SELECT count(*) AS n FROM core.events WHERE learner_id = %(learner_id)s AND type = %(t)s",
        {"learner_id": journey.learner.learner_id, "t": type_},
    )
    assert row is not None
    return int(row["n"])


async def test_rebuild_equals_incremental(system: System) -> None:
    """T-E4 (law 1): after the journey the subgraph the projector built step by step is the
    subgraph a rebuild from the log gives."""
    journey = await run_text_journey(system)
    await end_session(system, journey)
    await system.settle()
    learner_id = journey.learner.learner_id
    assert await count_of(system, journey, "observation.extracted") == 1
    incremental = await dump_learner(system.neo4j, learner_id)
    assert incremental["nodes"] and incremental["rels"]

    outcome = await rebuild_learner(
        system.resources.db,
        system.neo4j,
        system.settings,
        learner_id,
        respect_presence=False,
    )
    assert outcome.status == "rebuilt", outcome
    assert await dump_learner(system.neo4j, learner_id) == incremental


async def test_failed_background_commits_nothing(system: System) -> None:
    """T-E5 (law 7): the extractor's answer is invalid twice → no `observation.extracted`, no
    coverage; once the model answers again, exactly one observation and one coverage row."""
    journey = await run_text_journey(system, extractor=False)
    system.llm.fail(ScriptedFailure("invalid_json", schema_name="ExtractorOutputV3", times=2))
    script_extractor(system, events=[journey.answer_seq])
    await end_session(system, journey)
    await system.settle()

    db = system.resources.db
    learner_id = journey.learner.learner_id
    job = await db.fetch_one(
        "SELECT status, attempts, last_error FROM ops.jobs WHERE learner_id = %(learner_id)s "
        "AND kind = 'extract.episode'",
        {"learner_id": learner_id},
    )
    assert job is not None and job["status"] == "queued" and job["attempts"] == 1, job
    assert await count_of(system, journey, "observation.extracted") == 0
    coverage = await db.fetch_one(
        "SELECT count(*) AS n FROM ops.coverage WHERE learner_id = %(learner_id)s",
        {"learner_id": learner_id},
    )
    assert coverage is not None and coverage["n"] == 0

    await db.execute(
        "UPDATE ops.jobs SET run_at = now() WHERE learner_id = %(learner_id)s "
        "AND kind = 'extract.episode'",
        {"learner_id": learner_id},
    )
    await system.settle()
    assert await count_of(system, journey, "observation.extracted") == 1
    coverage = await db.fetch_one(
        "SELECT count(*) AS n FROM ops.coverage WHERE learner_id = %(learner_id)s",
        {"learner_id": learner_id},
    )
    assert coverage is not None and coverage["n"] == 1
    done = await db.fetch_one(
        "SELECT status FROM ops.jobs WHERE learner_id = %(learner_id)s "
        "AND kind = 'extract.episode'",
        {"learner_id": learner_id},
    )
    assert done is not None and done["status"] == "succeeded"


async def test_hot_path_never_waits(system: System) -> None:
    """T-E10 (law 9): with the projector and the workers stopped, three actions each answer a
    full SSE stream ending in `turn_committed`; the turns that run ahead of the projector say
    `projector_lag`; after `resume` the processes catch up."""
    script_teacher(system)
    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te10")
    session_id = await start_session(system, learner, domain_id, topic_id)

    await system.pause_background()
    committed: list[dict[str, Any]] = []
    for text in ("first", "second", "third"):
        response = await act(system, learner, session_id, {"kind": "message", "text": text})
        assert response.status_code == 200, response.text
        frames = parse_sse(response.content)
        names = [n for n, _ in frames]
        assert names[0] == "action_recorded" and names[-1] == "turn_committed", names
        assert "error" not in names, frames
        committed.append(frames[-1][1])
    modes = [c["degraded_mode"] for c in committed]
    assert modes[1:] == ["projector_lag", "projector_lag"], modes

    behind = await system.resources.db.fetch_one(
        "SELECT l.seq AS seq, coalesce(p.seq, 0) AS projected FROM core.learners l "
        "LEFT JOIN ops.projector_offsets p USING (learner_id) WHERE l.learner_id = %(l)s",
        {"l": learner.learner_id},
    )
    assert behind is not None and behind["projected"] < behind["seq"], behind

    system.resume_background()
    await system.settle()
    caught = await system.resources.db.fetch_one(
        "SELECT l.seq AS seq, p.seq AS projected FROM core.learners l "
        "JOIN ops.projector_offsets p USING (learner_id) WHERE l.learner_id = %(l)s",
        {"l": learner.learner_id},
    )
    assert caught is not None and caught["projected"] == caught["seq"], caught
    types = [t for _, t in await event_types(system, learner)]
    assert types.count("turn.committed") == 3


def _property_names(schema: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(value)
            names |= _property_names(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= _property_names(item)
    return names


def _strength_fields(schema: Any) -> set[str]:
    return {n for n in _property_names(schema) if any(w in n.lower() for w in STRENGTH_WORDS)}


async def test_law_4_and_10_end_to_end(system: System) -> None:
    """T-E14: no request to a model carries a UUID (T-M3); the output schemas of the extractor
    and of the checks have no field of strength; the extractor's input is the episode."""
    journey = await run_text_journey(system)
    await end_session(system, journey)
    await system.settle()
    assert await count_of(system, journey, "observation.extracted") == 1

    provider = system.llm
    requests = [*provider.calls, *(call.base for call in provider.stream_calls)]
    assert requests
    for request in requests:
        assert not UUID_RE.search(request.prompt), (request.schema_name, request.prompt[:300])
    for call in provider.stream_calls:
        carried = repr((call.input_items, call.tools))
        assert not UUID_RE.search(carried), (call.base.schema_name, carried[:300])

    extractor = provider.calls_for(schema_name="ExtractorOutputV3")
    assert extractor, sorted({r.schema_name for r in requests})
    for request in extractor:
        assert not _strength_fields(request.json_schema), _strength_fields(request.json_schema)
    for request in requests:
        if request.schema_name in {"ModelVerdictOutput", "TaskCheckOutput"}:
            assert not _strength_fields(request.json_schema), request.schema_name
