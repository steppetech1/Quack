"""T-P1 (ТЗ Ф4 WP3.4; `Т-§2.5`): batches of any size give the graph of applying at once — phases
A, B and C; applying a batch again changes nothing. In memory: the slice of each batch is the
whole graph so far (`WholeSlice`), so the check is of the rules, not of the Neo4j reads (those —
`test_projection_runner.py`)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any
from uuid import UUID

from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

import quack.events.event_types  # noqa: F401  (registers the catalog)
from quack.config import Settings
from quack.events.log import StoredEvent
from quack.graph.projection.phase_a import apply_events
from quack.graph.projection.phase_b import WholeSlice, run_folds
from quack.graph.projection.types import SubGraph
from quack.graph.projection.view import BatchSlice, RulesParams
from tests.events.samples import sample_payload

LEARNER = UUID(int=0xA1)
D, T1, T2 = str(UUID(int=0xD1)), str(UUID(int=0x711)), str(UUID(int=0x712))
S1, S2 = str(UUID(int=0x511)), str(UUID(int=0x512))
HUB, TASK, FAMILY, SESSION = (str(UUID(int=n)) for n in (0xB1, 0x7A1, 0xFA1, 0x5E1))
AT = datetime(2026, 11, 3, tzinfo=UTC)


def _ev(seq: int, event_type: str, payload: dict[str, Any], day: int = 0) -> StoredEvent:
    return StoredEvent(
        learner_id=LEARNER,
        seq=seq,
        id=seq,
        type=event_type,
        schema_version=1,
        actor="code",
        domain_id=UUID(D),
        session_id=UUID(SESSION),
        causation_seq=None,
        correlation_id=None,
        idempotency_key=f"k{seq}",
        payload_hash="h",
        occurred_at=AT + timedelta(days=day, minutes=seq),
        payload=payload,
        source_refs=[],
    )


def _turn(seq: int) -> StoredEvent:
    body = sample_payload("turn.committed")
    body["head"]["target_skill_ids"] = [S1]
    body["markup"]["tasks"][0]["task_id"] = TASK
    body["markup"]["tasks"][0]["tests"] = [{"skill_id": S1, "level": 3}]
    body["markup"]["exposures"] = [
        {"skill_id": S1, "kind": "explained", "approach": "example_first"}
    ]
    body["markup"]["targets_misconception_id"] = None
    return _ev(seq, "turn.committed", body)


def _answer(seq: int, day: int) -> StoredEvent:
    body = {"task_id": TASK, "answer": {"kind": "number", "value": "2"}, "source": "chat"}
    return _ev(seq, "answer.submitted", body, day)


def _verdict(seq: int, day: int, outcome: str, **extra: Any) -> StoredEvent:
    body = {"answer_seq": seq - 1, "part": "main", "outcome": outcome, "method": "exact",
            "verification": "code"} | extra  # fmt: skip
    return _ev(seq, "verdict.recorded", body, day)


# fmt: off
SKILL = {"domain_id": D, "summary": "s", "status": "active", "origin": "session"}
SCRIPT = [
    _ev(1, "domain.created", {"domain_id": D, "name": "Algebra"}),
    _ev(2, "topic.created", {"topic_id": T1, "domain_id": D, "name": "F", "origin": "session"}),
    _ev(3, "topic.proposed", {"topic_id": T2, "domain_id": D, "name": "R", "origin": "material"}),
    _ev(4, "skill.created", SKILL | {"skill_id": S1, "topic_id": T1, "name": "add"}),
    _ev(5, "skill.created", SKILL | {"skill_id": S2, "topic_id": T2, "name": "lcd"}),
    _ev(6, "edge.added", {"kind": "requires", "from_id": S1, "to_id": S2, "origin": "model",
                          "confidence": 0.9}),
    _ev(7, "hub.created", {"hub_id": HUB, "domain_id": D, "name": "Exam", "kind": "request",
                           "origin": "learner", "control_type": "tasks"}),
    _ev(8, "hub.deadline_set", {"hub_id": HUB, "deadline_at_ms": 9_000_000_000_000,
                                "source": "learner"}),
    _ev(9, "hub.topics_changed", {"hub_id": HUB, "topics": [
        {"topic_id": T1, "required_levels": [3, 4]}, {"topic_id": T2, "required_levels": []}]}),
    _ev(10, "task.created", {"task_id": TASK, "family_id": FAMILY, "source": "teacher",
                             "verification": "unchecked", "markup": {
        "tests": [{"skill_id": S1, "level": 3}, {"skill_id": S1, "level": 4}],
        "method_named": False, "answer": {"type": "number", "value": {"latex": "2",
                                                                     "sympy": "2"}}}}),
    _turn(11),
    _answer(12, 0),
    _verdict(13, 0, "correct"),
    _answer(14, 2),
    _verdict(15, 2, "incorrect", misconception_hypothesis="adds the denominators"),
    _answer(16, 3),
    _verdict(17, 3, "incorrect", help_step=1),
    _answer(18, 5),
    _verdict(19, 5, "correct"),
    _ev(20, "session.ended", sample_payload("session.ended") | {"session_id": SESSION}, 5),
]
# fmt: on


def _graph(shot: SubGraph) -> tuple[dict[Any, Any], dict[Any, Any]]:
    return {(n.label, n.id): n.props for n in shot.nodes}, {r.key: r.props for r in shot.rels}


async def _project(events: Sequence[StoredEvent], cuts: Sequence[bool]) -> SubGraph:
    settings, store = Settings(), SubGraph()
    chunks: list[list[StoredEvent]] = [[]]
    for event, cut in zip(events, [*cuts, False], strict=True):
        chunks[-1].append(event)
        if cut:
            chunks.append([])
    done: list[StoredEvent] = []
    for chunk in (c for c in chunks if c):
        batch = BatchSlice(str(LEARNER), store, done, RulesParams.from_settings(settings))
        apply_events(chunk, batch)
        await run_folds(batch, settings, WholeSlice([*done, *chunk]), chunk)
        store, done = batch.snapshot(), [*done, *chunk]
    return store


@cache
def _whole() -> SubGraph:
    """The script applied at once (computed on first use, not at import)."""
    return asyncio.run(_project(SCRIPT, [False] * (len(SCRIPT) - 1)))


def test_script_reaches_every_phase() -> None:
    """The script is not empty work: evidence, a fold of a skill, a misconception, topics."""
    nodes, rels = _graph(_whole())
    labels = {label for label, _ in nodes}
    assert {"Evidence", "Exposure", "Misconception", "Hub", "Task"} <= labels
    # the first success is in the hot window of the exposure (×0.5): the level is not proven,
    # so later attempts are not reviews (Т-§8.3) — a card, no review yet
    assert (nodes[("Skill", S1)]["retState"], nodes[("Skill", S1)]["l3Status"]) == (
        "learning",
        "unstable",
    )
    assert nodes[("Topic", T2)]["status"] == "active"  # a derived evidence touched its skill
    assert any(k[2] == "DERIVED_FROM" for k in rels)


@hypothesis_settings(max_examples=40, deadline=None)
@given(st.lists(st.booleans(), min_size=len(SCRIPT) - 1, max_size=len(SCRIPT) - 1))
def test_batches_equal_rebuild_hypothesis(cuts: list[bool]) -> None:
    """T-P1: any split into batches gives the graph of applying at once; applying a batch again
    on its result changes nothing."""
    batched = asyncio.run(_project(SCRIPT, cuts))
    assert _graph(batched) == _graph(_whole())


def test_applying_a_batch_again_changes_nothing() -> None:
    async def again() -> SubGraph:
        settings = Settings()
        batch = BatchSlice(str(LEARNER), _whole(), SCRIPT, RulesParams.from_settings(settings))
        apply_events(SCRIPT[-6:], batch)
        await run_folds(batch, settings, WholeSlice(SCRIPT), SCRIPT[-6:])
        return batch.snapshot()

    assert _graph(asyncio.run(again())) == _graph(_whole())


_LEVEL = ("Status", "Basis", "DecisiveEvidenceId", "SuccessW", "FailW", "ProvenAtMs", "FailedAtMs")
REQUIRED: dict[str, set[str]] = {
    "Domain": {"name", "status"},
    "Topic": {"name", "origin", "status", "displayState", "displayReason", "displayLevel",
              "displayDomainId", "nearestDueAtMs", "visibility", "knownClaimedAtMs"},
    "Skill": {"name", "summary", "status", "origin", "retState", "retStability", "retDifficulty",
              "retLastRecallAtMs", "retDueAtMs", "retReviews", "retLapses", "retLastLapseAtMs",
              "expCount", "expLastExplainedAtMs", "expLastExampleAtMs", "expLastMethodNamedAtMs",
              "expLastSolutionShownAtMs", "gapType", "gapTypeConfidence", "lastPracticeAtMs",
              "needsConsolidation", "consolidationLevel", "consolidationSinceMs", "bypassCount",
              "asOfSeq"} | {f"l{n}{p}" for n in range(1, 7) for p in _LEVEL},
    "Hub": {"name", "kind", "deadlineAtMs", "origin", "status"},
    "Evidence": {"domainId", "atMs", "level", "outcome", "assistance", "hintStep", "novelty",
                 "confidence", "tier", "weight", "verification", "delayed", "solveTimeMs",
                 "gapType", "quote", "sourceSessionId", "sessionDomainId", "sourceSeq",
                 "derivedKind", "status", "observationSeq", "extractor", "searchable"},
    "Exposure": {"atMs", "kind", "approach", "sourceSeq", "sessionDomainId"},
    "Misconception": {"domainId", "statement", "status", "triggers", "occurrences",
                      "firstSeenAtMs", "lastSeenAtMs", "watchSinceMs", "fixedAtMs",
                      "targetedInSessionId", "searchable"},
    "Task": {"familyId", "source", "verification", "methodNamed"},
}  # fmt: skip


def test_no_missing_properties() -> None:
    """T-P2 (`Т-§3.4.2`, `Т-§3.4.4`, law 8): after phases A–C every node of the script has every
    property of its label — explicit values, never absent (`null` deletes in Neo4j)."""
    nodes, _ = _graph(_whole())
    missing = {
        (label, node_id): sorted(REQUIRED[label] - set(props) - {"learnerId"})
        for (label, node_id), props in nodes.items()
        if label in REQUIRED and REQUIRED[label] - set(props)
    }
    assert missing == {}
    assert all(v is not None for props in nodes.values() for v in props.values())


def test_profile_learned() -> None:
    """T-MS3 (WP5.3, `Т-§9.5`): phase B writes the profile of the learner; below
    `profile_min_samples` a `learned*` is «no data» — absent, and a value that lost its data is
    removed from the node, not set to zero (law 8)."""
    from quack.graph.profile_fold import ProfileParams, profile_id
    from quack.graph.projection.phase_b import fold_profile
    from quack.graph.projection.types import NodeRecord

    profile = profile_id(LEARNER)
    nodes, rels = _graph(_whole())
    assert not [k for k in nodes[("Profile", profile)] if k.startswith("learned")]
    assert ("Learner", str(LEARNER), "HAS_PROFILE", "Profile", profile) in rels
    stale = NodeRecord("Profile", profile, {"learnerId": str(LEARNER), "learnedBranchRate": 0.5})
    batch = BatchSlice(str(LEARNER), SubGraph(nodes=[stale]))
    fold_profile(batch, [], ProfileParams())
    assert batch.diff().remove_props == [(("Profile", profile), ("learnedBranchRate",))]
