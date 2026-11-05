"""Routes of stream A (ТЗ Ф4 §7.3): preferences, domains, graph, topics, hubs, WP8, deletion.

Stages 10-40 are the learner's first steps (preference, domain), 125 the hub and 280 the dispute;
B and C walk between them. The interview of the structure, the diagnostic and the archive are
gone (ТЗ Ф4 §2.3 row 5, D-A-207).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from quack.events.log import NewEvent, append_events
from tests.e2e.routes import NO_ID, Ids, Route, Stream

if TYPE_CHECKING:
    from tests.e2e.system import Learner, System


async def _answer_of_phase4_log(system: System, learner: Learner) -> int:
    """The `seq` of an `answer.submitted` of the learner in the Phase 4 log (WP8.1)."""
    answer = NewEvent(
        type="answer.submitted",
        payload={
            "task_id": str(uuid4()),
            "source": "chat",
            "answer": {"kind": "number", "value": "2"},
        },
        actor="learner",
        idempotency_key=f"te2:answer:{uuid4()}",
    )
    [stored] = await append_events(system.resources.db, learner.learner_id, [answer])
    return stored.seq


async def _verdict_of(system: System, learner: Learner, ids: Ids) -> Ids:
    """WP8.1: the verdict the dispute names."""
    return ids.replace(answer_seq=await _answer_of_phase4_log(system, learner))


async def _wp8_targets_of(system: System, learner: Learner, ids: Ids) -> Ids:
    """T-E3 for WP6.3/WP8: a hub and a topic of the learner in the graph, a verdict and a closed
    episode in the Phase 4 log — what the routes of WP8 and `PATCH /v1/hubs/{id}` look up."""
    hub_id = uuid4()
    async with system.resources.neo4j.session() as session:
        await session.run(
            "MERGE (t:Topic {id: $topic}) ON CREATE SET t.learnerId = $learner, "
            "t.domainId = $domain, t.name = 'Limits' "
            "CREATE (:Hub {id: $hub, learnerId: $learner, domainId: $domain})",
            topic=str(ids.topic_id),
            learner=str(learner.learner_id),
            domain=str(ids.domain_id),
            hub=str(hub_id),
        )
    episode = NewEvent(
        type="episode.closed",
        payload={"from_seq": 1, "to_seq": 1, "has_content": False},
        actor="code",
        idempotency_key=f"te3:episode:{uuid4()}",
    )
    [closed] = await append_events(system.resources.db, learner.learner_id, [episode])
    return ids.replace(
        hub_id=hub_id,
        answer_seq=await _answer_of_phase4_log(system, learner),
        episode_seq=closed.seq,
    )


async def _domain_in_graph(system: System, learner: Learner, ids: Ids) -> Ids:
    """WP6.5: the domain of the walk as a node of `rules@3`, which hub creation looks up."""
    async with system.resources.neo4j.session() as session:
        await session.run(
            "MERGE (d:Domain {id: $domain}) ON CREATE SET d.learnerId = $learner",
            domain=str(ids.domain_id),
            learner=str(learner.learner_id),
        )
    return ids


async def _created_domain(system: System, learner: Learner, ids: Ids, payload: Any) -> Ids:
    return ids.replace(domain_id=UUID(payload["domain_id"]))


STREAM = Stream(
    name="a",
    ids={
        "domain_id": NO_ID,
        "topic_id": NO_ID,
        # WP8: a hub of the learner (Neo4j), a verdict (`answer.submitted`) and an
        # `episode.closed` of the Phase 4 log; `0` is a number nobody has.
        "hub_id": NO_ID,
        "answer_seq": 0,
        "episode_seq": 0,
    },
    routes=(
        Route(
            "PATCH",
            "/v1/learner/preferences",
            stage=10,
            body=lambda ids, v: {"key": "language", "value": ["en", "de"][v]},
        ),
        # ТЗ Ф4 WP25 п.3: the account is deleted physically, the session goes with it.
        Route(
            "DELETE",
            "/v1/learner",
            no_key="ТЗ §6.4 WP25: physical deletion; the session dies with the account",
        ),
        Route(
            "POST",
            "/v1/domains",
            stage=40,
            body=lambda ids, v: {"name": ["Physics", "Chemistry"][v]},
            advance=_created_domain,
        ),
        # ТЗ Ф4 WP8.1: the verdict the dispute names is an `answer.submitted` of the Phase 4 log.
        Route(
            "POST",
            "/v1/disputes",
            stage=280,
            prepare=_verdict_of,
            body=lambda ids, v: {
                "target_kind": "verdict",
                "target_id": f"{ids.answer_seq}:{['main', 'other'][v]}",
            },
        ),
        # ТЗ Ф4 WP6.5 (§6.4 «Хабы», D-A-198): a hub of the learner's request; its id comes from
        # the key, so a repeat gives the same hub.
        Route(
            "POST",
            "/v1/domains/{domain_id}/hubs",
            stage=125,
            requires=("domain_id",),
            prepare=_domain_in_graph,
            body=lambda ids, v: {"name": ["Exam", "Quiz"][v], "control_type": "tasks"},
        ),
        # ТЗ Ф4 WP8.1/WP8.2/WP6.3 (§6.4 tables): the requests carry no key. Each route appends
        # one event whose key is the edit itself with the state it starts from (law 5, D-A-192,
        # D-A-193), so a repeat gives the same event; the models forbid extra fields.
        Route(
            "POST",
            "/v1/verdicts/{answer_seq}/typo",
            no_key="ТЗ §6.4 WP8.1: no key; keyed by the verdict",
            body_key="none",
            body=lambda ids, v: {"part": ["main", "other"][v]},
        ),
        Route(
            "PATCH",
            "/v1/topics/{topic_id}",
            no_key="ТЗ §6.4 WP8.2: no key; keyed by the name change",
            body_key="none",
            body=lambda ids, v: {"name": ["Renamed", "Other"][v]},
        ),
        Route(
            "POST",
            "/v1/topics/{topic_id}/merge",
            no_key="ТЗ §6.4 WP8.2: no key; keyed by the pair",
            body_key="none",
            body=lambda ids, v: {"into": str(UUID(int=1 + v))},
        ),
        Route(
            "POST",
            "/v1/topics/{topic_id}/hide",
            no_key="ТЗ §6.4 WP8.2: no key; keyed by the topic",
            body_key="none",
            body=lambda ids, v: {},
        ),
        Route(
            "POST",
            "/v1/topics/{topic_id}/known",
            no_key="ТЗ §6.4 WP8.2: no key; keyed by the topic state",
            body_key="none",
            body=lambda ids, v: {},
        ),
        Route(
            "POST",
            "/v1/episodes/{episode_seq}/reassign",
            no_key="ТЗ §6.4 WP8.2: no key; keyed by the pair",
            body_key="none",
            body=lambda ids, v: {"topic_id": str(ids.topic_id)},
        ),
        Route(
            "PATCH",
            "/v1/hubs/{hub_id}",
            no_key="ТЗ §6.4 WP6.3: no key; keyed by the scope and date change",
            body_key="none",
            body=lambda ids, v: {"deadline_at_ms": 1_000 + v},
        ),
        # ТЗ Ф4 WP17.4–17.5 (D-A-195): prep mode — the mode already set returns its event; the
        # grade is keyed by its value.
        Route(
            "POST",
            "/v1/hubs/{hub_id}/prep-mode",
            no_key="ТЗ §6.4 WP17.4: no key; the same mode returns its event",
            body_key="none",
            body=lambda ids, v: {"enabled": [True, False][v]},
        ),
        Route(
            "POST",
            "/v1/hubs/{hub_id}/grade",
            no_key="ТЗ §6.4 WP17.5: no key; keyed by the hub and the grade",
            body_key="none",
            body=lambda ids, v: {"grade": ["5", "4"][v], "scale": "1-5"},
        ),
        # ТЗ Ф4 WP25 (D-A-197): a physical deletion; a repeat finds no domain (404).
        Route(
            "DELETE",
            "/v1/domains/{domain_id}",
            no_key="ТЗ §6.4 WP25: physical deletion; a repeat is «not found»",
            body_key="none",
            body=lambda ids, v: {},
        ),
        Route(
            "DELETE",
            "/v1/topics/{topic_id}",
            no_key="ТЗ §6.4 WP25: physical deletion; a repeat is «not found»",
            body_key="none",
            body=lambda ids, v: {},
        ),
    ),
    other_routes=frozenset(
        {
            ("GET", "/v1/domains"),
            # ТЗ Ф4 WP6.5 (D-A-198, D-A-207): the map and the card are codes (T-TH5)
            ("GET", "/v1/domains/{domain_id}/graph"),
            ("GET", "/v1/topics/{topic_id}"),
            # ТЗ Ф4 WP6.5, WP17.2 (D-A-198, D-A-195)
            ("GET", "/v1/topics/{topic_id}/why"),
            ("GET", "/v1/domains/{domain_id}/hubs"),
            ("GET", "/v1/hubs/{hub_id}"),
            ("GET", "/v1/domains/{domain_id}/today"),
            # ТЗ Ф4 WP8.3, §7.3 crossing 3 (D-A-217): declared by A in the session router
            ("GET", "/v1/sessions/{session_id}/summary"),
        }
    ),
    routers=(
        "quack.domains.router:_protected",
        "quack.review.router:router",
        "quack.deletion.router:router",
    ),
    isolation=(_wp8_targets_of,),
)
