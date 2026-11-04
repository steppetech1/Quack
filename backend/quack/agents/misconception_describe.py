"""The misconception describer, WP16.2 (ТЗ Ф4; Т-§9.3, Т-§9.2; `T-AG2`). D-B-175.

- The job `misconception.describe` (WP7.1) reads the status first and ends without a call
  unless the misconception is confirmed: `confirmed`, `in_work` (confirmed and targeted) or
  `watching` (confirmed and resisting) — Т-§9.2.
- Input: the name of its skill and **all** manifestations — quote, the condition of the task,
  the date; no earlier wording (law 10).
- Output `statement`, `triggers` (`misconception_describer@1`); the event
  `misconception.described` with key `misconception.described:{misconception_id}:{trigger_seq}`
  (law 5: a retry of the job is the same event).

The facts are `GraphReader.misconception_detail` of stream A (S4, #170): merges applied, the
status with `unknown` before the first manifestation. Manifestations go in time order, ties by
the source seq; one without a quote is named so (D-B-179).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from quack.agents.schemas import MisconceptionDescription
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import MisconceptionDescribed
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.graph.read import (
    GraphReader,
    ManifestationFact,
    MisconceptionDetail,
    create_graph_reader,
)
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.practice.repo import event_by_seq
from quack.practice.task_store import get_task

DESCRIBER_ROLE = "misconception_describer"
CONFIRMED_STATUSES = frozenset({"confirmed", "in_work", "watching"})
NONE_LINE = "(none)"
NO_QUOTE = "no quote"


def describer_key(misconception_id: UUID, trigger_seq: int) -> str:
    return f"misconception.described:{misconception_id}:{trigger_seq}"


def _date(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, UTC).strftime("%Y-%m-%d")


def render_manifestations(
    manifestations: Sequence[ManifestationFact], conditions: dict[UUID, str]
) -> str:
    """One numbered line per manifestation in time order: date, the problem, the quote."""
    lines: list[str] = []
    ordered = sorted(manifestations, key=lambda m: (m.at_ms, m.source_seq))
    for n, item in enumerate(ordered, start=1):
        condition = conditions.get(item.task_id) if item.task_id is not None else None
        problem = f"problem: {condition}" if condition else "in conversation, no problem"
        said = f'the learner: "{item.quote}"' if item.quote else NO_QUOTE
        lines.append(f"{n}. {_date(item.at_ms)}, {problem}; {said}")
    return "\n".join(lines) or NONE_LINE


async def _conditions(
    db: Db, learner_id: UUID, manifestations: Sequence[ManifestationFact]
) -> dict[UUID, str]:
    found: dict[UUID, str] = {}
    for task_id in dict.fromkeys(m.task_id for m in manifestations if m.task_id is not None):
        task = await get_task(db, learner_id=learner_id, task_id=task_id)
        if task is not None:
            found[task_id] = task.condition.text
    return found


async def describe_misconception(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    trigger_seq: int,
    facts: MisconceptionDetail | None,
    domain_id: UUID | None = None,
) -> StoredEvent | None:
    """`T-AG2`: no misconception or not confirmed — `None` without a model call."""
    if facts is None or facts.status not in CONFIRMED_STATUSES:
        return None
    prompt = settings.llm_roles[DESCRIBER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'misconception_describer' has no prompt")
    conditions = await _conditions(db, learner_id, facts.manifestations)
    key = describer_key(facts.misconception_id, trigger_seq)
    result = await gateway.generate_structured(
        DESCRIBER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        MisconceptionDescription,
        {
            "skill": facts.skill_name,
            "manifestations": render_manifestations(facts.manifestations, conditions),
        },
        learner_id,
        None,
        None,
        key,
    )
    description = result.value
    (stored,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="misconception.described",
                payload=MisconceptionDescribed(
                    misconception_id=facts.misconception_id,
                    statement=description.statement.strip(),
                    triggers=[t.strip() for t in description.triggers if t.strip()],
                ),
                actor=prompt,
                idempotency_key=key,
                domain_id=domain_id,
                causation_seq=trigger_seq,
            )
        ],
    )
    return stored


async def run_misconception_describe(
    job: ClaimedJob, ctx: JobContext, reader: GraphReader
) -> StoredEvent | None:
    """The `misconception.describe` job (WP7.1 row: `misconception_id`, `trigger_seq`); the
    facts are read through the alias, so a merged misconception is described as its target."""
    if job.learner_id is None:
        raise ValueError("misconception.describe needs learner_id")
    trigger_seq = int(str(job.payload["trigger_seq"]))
    facts = await reader.misconception_detail(UUID(str(job.payload["misconception_id"])))
    trigger = await event_by_seq(ctx.db, learner_id=job.learner_id, seq=trigger_seq)
    return await describe_misconception(
        ctx.db,
        ctx.llm,
        ctx.settings,
        learner_id=job.learner_id,
        trigger_seq=trigger_seq,
        facts=facts,
        domain_id=trigger.domain_id if trigger is not None else None,
    )


@register_job_handler("misconception.describe")
async def handle_misconception_describe(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("misconception.describe needs learner_id")
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)
    await run_misconception_describe(job, ctx, reader)
