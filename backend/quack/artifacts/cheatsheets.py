"""Topic cheatsheets and session digests (ТЗ §2.3 п.1–2): the deterministic cheatsheet id,
reads, forced refresh and the `session.finish_artifacts` / `artifact.refresh_cheatsheet`
workers. Importing this module registers the job handlers."""

from __future__ import annotations

from typing import Any, LiteralString
from uuid import UUID, uuid5

import orjson
from redis.exceptions import RedisError

from quack.api.resources import AppResources
from quack.artifacts.deps import WorkerDeps, worker_deps
from quack.artifacts.practice import LLM_ERRORS
from quack.artifacts.schemas import (
    ArtifactGenOutput,
    ArtifactView,
    CheatsheetRefreshRequest,
    CheatsheetRefreshResponse,
)
from quack.artifacts.store import (
    ARTIFACT_VIEW_COLUMNS,
    fetch_rows,
    require_topic_of_learner,
    stored_event,
)
from quack.db.neo4j import read_session
from quack.db.redis import session_events_channel
from quack.events import NewEvent, append_events
from quack.events.reader import last_seq
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.topic_display import INACTIVE_SKILL_STATUSES
from quack.jobs import kinds
from quack.jobs.queue import ClaimedJob, enqueue_job
from quack.jobs.runner import JobContext, register_job_handler
from quack.logging import get_logger

log = get_logger(__name__)

# A9: actors of digests and cheatsheets.
LLM_ACTOR = "artifacts@1"
TEMPLATE_ACTOR = "code"

# Т-§9.2: misconceptions the learner still holds (not latent, dismissed, fixed or resolved).
ACTIVE_MISCONCEPTION_STATUSES = frozenset({"suspected", "confirmed", "watching"})

# Titles of the deterministic templates; the cheatsheet one is used when the topic is not
# projected into Neo4j yet.
DIGEST_TITLE = "Session digest"
CHEATSHEET_FALLBACK_TITLE = "Topic cheatsheet"

# Topic name, its live skills and the misconceptions of those skills (A16).
_TOPIC_SHEET: LiteralString = (
    "MATCH (t:Topic {id: $topic}) WHERE t.learnerId = $l AND t.domainId = $domain "
    "OPTIONAL MATCH (s:Skill)-[:PART_OF]->(t) WHERE s.learnerId = $l AND NOT s.status IN $inactive "
    "OPTIONAL MATCH (m:Misconception)-[:ABOUT_SKILL]->(s) WHERE m.learnerId = $l "
    "WITH t, s, m ORDER BY m.name, m.id "
    "WITH t, s, collect(m {.name, .status}) AS miscs ORDER BY s.name, s.id "
    "RETURN t.name AS topic_name, collect(s {.name, .pMastery, .masteryStatus}) AS skills, "
    "reduce(acc = [], ms IN collect(miscs) | acc + ms) AS misconceptions"
)


def cheatsheet_id(learner_id: UUID, topic_id: UUID) -> UUID:
    """uuid5(QUACK_UUID_NS, f"cheatsheet:{learner_id}:{topic_id}") (ТЗ §2.3 п.1)."""
    return uuid5(QUACK_UUID_NS, f"cheatsheet:{learner_id}:{topic_id}")


async def get_cheatsheet(
    learner_id: UUID, topic_id: UUID, resources: AppResources
) -> ArtifactView | None:
    """The active cheatsheet of the topic from core.artifacts, or None."""
    rows = await fetch_rows(
        resources.session_factory,
        f"SELECT {ARTIFACT_VIEW_COLUMNS} FROM core.artifacts WHERE learner_id = :l "
        "AND topic_id = :t AND kind = 'cheatsheet' AND NOT is_deleted",
        l=learner_id,
        t=topic_id,
    )
    if rows:
        return ArtifactView.model_validate(rows[0])
    await require_topic_of_learner(resources.session_factory, learner_id, topic_id)
    return None


async def request_cheatsheet_refresh(
    learner_id: UUID,
    topic_id: UUID,
    body: CheatsheetRefreshRequest,
    resources: AppResources,
) -> CheatsheetRefreshResponse:
    """Enqueue `artifact.refresh_cheatsheet` (spec §7.1). The learner's action has no event of
    its own, so this is the one direct enqueue left in product code (WP11.2 exception):
    `required_seq` is the learner's current `last_seq`."""
    await require_topic_of_learner(resources.session_factory, learner_id, topic_id)
    async with resources.session_factory() as session, session.begin():
        spec = kinds.artifact_refresh_cheatsheet(
            learner_id,
            topic_id,
            {"domain_id": str(body.domain_id)},
            required_seq=await last_seq(session, learner_id),
        )
        job_id = await enqueue_job(session, spec)
    return CheatsheetRefreshResponse(job_id=job_id, status="queued")


# --- shared worker steps --------------------------------------------------------------


def _learner(job: ClaimedJob) -> UUID:
    if job.learner_id is None:
        raise ValueError(f"job {job.id} ({job.kind}) has no learner_id")
    return job.learner_id


def _id_from_lock_key(job: ClaimedJob, prefix: str, suffix: str) -> UUID:
    """Decision 3: `session_id` / `topic_id` come from the lock key, not the payload."""
    return UUID(job.lock_key.removeprefix(prefix).removesuffix(suffix))


async def _generate(
    deps: WorkerDeps,
    learner_id: UUID,
    session_id: UUID | None,
    idempotency_key: str,
    variables: dict[str, Any],
) -> ArtifactGenOutput | None:
    """`artifacts@1`; None when the LLM is unavailable (the caller writes a template)."""
    try:
        result = await deps.gateway.generate_structured(
            role="artifacts",
            prompt_version=1,
            response_model=ArtifactGenOutput,
            variables=variables,
            learner_id=learner_id,
            session_id=session_id,
            turn_id=None,
            idempotency_key=idempotency_key,
        )
    except LLM_ERRORS:
        return None
    return result.value


def _markdown(title: str, sections: list[tuple[str, list[str]]]) -> str:
    parts = [f"# {title}"]
    for heading, lines in sections:
        parts.append(f"## {heading}\n\n" + "\n".join(lines or ["- none"]))
    return "\n\n".join(parts) + "\n"


# --- session.finish_artifacts ---------------------------------------------------------


async def _digest_event(
    deps: WorkerDeps,
    learner_id: UUID,
    session_id: UUID,
    domain_id: UUID,
    topic_id: UUID | None,
    key: str,
) -> NewEvent:
    """`session.digest_created` from the session events (A15)."""
    params = {"l": learner_id, "s": session_id}
    replies = await fetch_rows(
        deps.session_factory,
        "SELECT type, payload FROM core.events WHERE learner_id = :l AND session_id = :s "
        "AND type IN ('turn.student_replied', 'turn.teacher_replied') ORDER BY seq DESC LIMIT :n",
        n=deps.settings.active_window_turns,
        **params,
    )
    facts = await fetch_rows(
        deps.session_factory,
        "SELECT type, payload FROM core.events WHERE learner_id = :l AND session_id = :s "
        "AND type IN ('task.evaluated', 'teacher.concept_explained') ORDER BY seq",
        **params,
    )
    dialogue = [
        {
            "role": "learner" if r["type"] == "turn.student_replied" else "teacher",
            "text": r["payload"]["text"],
        }
        for r in reversed(replies)
    ]
    evaluated = [r["payload"] for r in facts if r["type"] == "task.evaluated"]
    skill_ids: list[str] = []
    for r in facts:
        p = r["payload"]
        refs = p["skill_scores"] if r["type"] == "task.evaluated" else p["skills"]
        skill_ids.extend(ref["skill_id"] for ref in refs if ref.get("skill_id"))
    covered = list(dict.fromkeys(skill_ids))
    tasks = [
        {"task_id": p["task_id"], "is_correct": p["is_correct"], "score": p["score"]}
        for p in evaluated
    ]

    output = await _generate(
        deps,
        learner_id,
        session_id,
        f"artifacts:digest:{session_id}",
        {
            "mode": "digest",
            "title_context": DIGEST_TITLE,
            "skills_summary_json": {"covered_skill_ids": covered, "evaluated_tasks": tasks},
            "misconceptions_summary_json": [
                m for p in evaluated for m in p.get("triggered_misconceptions", [])
            ],
            "dialogue_excerpt_json": dialogue,
            "previous_content_md": "",
        },
    )
    if output is None:
        title = DIGEST_TITLE
        text = _markdown(
            title,
            [
                ("Covered skills", [f"- {s}" for s in covered]),
                (
                    "Solved tasks",
                    [
                        f"- {t['task_id']}: correct = {t['is_correct']}, score = {t['score']}"
                        for t in tasks
                    ],
                ),
            ],
        )
    else:
        title, text = output.title, output.text
    return NewEvent(
        type="session.digest_created",
        payload={
            "artifact_id": str(uuid5(QUACK_UUID_NS, f"digest:{session_id}")),
            "session_id": str(session_id),
            "domain_id": str(domain_id),
            "topic_id": None if topic_id is None else str(topic_id),
            "title": title,
            "text": text,
            "covered_skill_ids": covered,
        },
        actor=TEMPLATE_ACTOR if output is None else LLM_ACTOR,
        idempotency_key=key,
    )


async def run_finish_artifacts(job: ClaimedJob, deps: WorkerDeps) -> None:
    """`session.digest_created` (`artifacts@1`, mode digest, or a template). For a session with
    a topic the dispatcher turns that event into `artifact.refresh_cheatsheet` (decision 3, A15,
    WP11.2). The payload is the one of `finish_session`: `{"session_id", "domain_id",
    "topic_id"}` (WP6.4)."""
    learner_id = _learner(job)
    session_id = UUID(job.payload["session_id"])
    domain_id = UUID(job.payload["domain_id"])
    topic = job.payload.get("topic_id")
    topic_id = None if topic is None else UUID(topic)

    # A10: a retried job does not write a second digest.
    key = f"session.digest_created:{session_id}"
    events: list[NewEvent] = []
    if await stored_event(deps.session_factory, learner_id, key) is None:
        events.append(await _digest_event(deps, learner_id, session_id, domain_id, topic_id, key))
    # WP11.4: artifact.refresh_cheatsheet is enqueued by the log dispatcher from
    # session.digest_created (quack/jobs/dispatcher_rules.py).
    async with deps.session_factory() as session, session.begin():
        await append_events(session, learner_id, events, redis=deps.redis)


# --- artifact.refresh_cheatsheet ------------------------------------------------------


async def _topic_sheet(
    deps: WorkerDeps, learner_id: UUID, domain_id: UUID, topic_id: UUID
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Topic name (None if not projected yet), live skills and their misconceptions (A16)."""
    async with read_session(deps.neo4j) as session:
        result = await session.run(
            _TOPIC_SHEET,
            {
                "topic": str(topic_id),
                "domain": str(domain_id),
                "l": str(learner_id),
                "inactive": sorted(INACTIVE_SKILL_STATUSES),
            },
        )
        record = await result.single()
    if record is None:
        return None, [], []
    return record["topic_name"], list(record["skills"]), list(record["misconceptions"])


def _cheatsheet_template(
    title: str, skills: list[dict[str, Any]], misconceptions: list[dict[str, Any]]
) -> str:
    table = ["| Skill | pMastery | Status |", "| :--- | :--- | :--- |"]
    table += [f"| {s['name']} | {s['pMastery']:.2f} | {s['masteryStatus']} |" for s in skills]
    traps = [
        f"- {m['name']}" for m in misconceptions if m["status"] in ACTIVE_MISCONCEPTION_STATUSES
    ]
    return _markdown(title, [("Skills", table if skills else []), ("Typical traps", traps)])


async def run_refresh_cheatsheet(job: ClaimedJob, deps: WorkerDeps) -> None:
    """`artifact.cheatsheet_updated` with `version = prev + 1` (`artifacts@1`, mode cheatsheet,
    or a template), then `cheatsheet_ready` on `session:events:{session_id}` (decision 3)."""
    learner_id = _learner(job)
    topic_id = _id_from_lock_key(job, "topic:", ":cheatsheet")
    domain_id = UUID(job.payload["domain_id"])
    session = job.payload.get("session_id")
    session_id = None if session is None else UUID(session)
    artifact_id = cheatsheet_id(learner_id, topic_id)

    key = f"{job.kind}:{job.id}"
    stored = await stored_event(deps.session_factory, learner_id, key)
    if stored is None:
        topic_name, skills, misconceptions = await _topic_sheet(
            deps, learner_id, domain_id, topic_id
        )
        current = await fetch_rows(
            deps.session_factory,
            "SELECT version, content_md FROM core.artifacts WHERE id = :id AND learner_id = :l",
            id=artifact_id,
            l=learner_id,
        )
        title_context = topic_name or CHEATSHEET_FALLBACK_TITLE
        output = await _generate(
            deps,
            learner_id,
            session_id,
            f"artifacts:cheatsheet:{job.id}",
            {
                "mode": "cheatsheet",
                "title_context": title_context,
                "skills_summary_json": skills,
                "misconceptions_summary_json": misconceptions,
                "dialogue_excerpt_json": [],
                "previous_content_md": current[0]["content_md"] if current else "",
            },
        )
        if output is None:
            title = title_context
            text = _cheatsheet_template(title, skills, misconceptions)
        else:
            title, text = output.title, output.text
        event = NewEvent(
            type="artifact.cheatsheet_updated",
            payload={
                "artifact_id": str(artifact_id),
                "domain_id": str(domain_id),
                "topic_id": str(topic_id),
                "version": (current[0]["version"] + 1) if current else 1,
                "title": title,
                "text": text,
            },
            actor=TEMPLATE_ACTOR if output is None else LLM_ACTOR,
            idempotency_key=key,
        )
        async with deps.session_factory() as db_session, db_session.begin():
            [stored] = await append_events(db_session, learner_id, [event], redis=deps.redis)

    if session_id is None or deps.redis is None:
        return
    message = {
        "event": "cheatsheet_ready",
        "data": {
            "topic_id": str(topic_id),
            "artifact_id": str(artifact_id),
            "version": stored.payload["version"],
        },
    }
    try:
        await deps.redis.publish(session_events_channel(session_id), orjson.dumps(message))
    except (RedisError, OSError):
        log.warning("cheatsheet_ready_publish_failed", session_id=str(session_id))


@register_job_handler("session.finish_artifacts")
async def _finish_artifacts_handler(job: ClaimedJob, ctx: JobContext) -> None:
    await run_finish_artifacts(job, worker_deps(ctx))


@register_job_handler("artifact.refresh_cheatsheet")
async def _refresh_cheatsheet_handler(job: ClaimedJob, ctx: JobContext) -> None:
    await run_refresh_cheatsheet(job, worker_deps(ctx))
