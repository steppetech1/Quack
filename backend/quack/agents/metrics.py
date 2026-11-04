"""Metrics of `ops metrics --since <t>` (ТЗ Ф4 WP16.7; Т-§18.2, Т-§18.3). D-B-194, D-B-195.

Only what the journal (`core.events`) and `ops.llm_usage` give without labelling. Every metric is
`{"status": "measured", "value", "count", "of"}` or `{"status": "not_measured", "value": null,
"reason"}` — an empty window or a missing input is «not measured», never a zero (law 8). The
result is JSON-ready; the command prints it.

- **Brier of the next attempt of Т1** (the main one): before each Т1 attempt (a code verdict of
  a marked task) the state at that moment (`GraphReader.state_at(seq − 1)`) gives the probability
  of success by the rule of Т-§18.2 (`p_*` × `R` at that moment); outcome — the verdict. Needs a
  reader per learner: without `reader` it is not measured.
- **Corrective branches**, **teacher_fallback turns**: `turn.committed` (`head.branch_type`,
  `degraded_mode`), per turn.
- **Independent success later**: per (learner, skill) the first later Т1 attempt in another
  session on a task of another family — correct without help (`help_step = 0`, no shown
  solution); both attempts inside the window.
- **Disputes** per thousand `verdict.recorded`; **removed canvas marks**:
  `observation.second_opinion` with `agreed = false` of all second opinions;
  **unclear**, **numericOnly**: of the verdicts;
  **check.failed**: of verdicts and `check.failed`; **extractor rejections**: `rejected` of
  elements and rejected; **merged temporary skills**: `skill.created` with `provisional` that
  have a `skill.merged` of it; **cost per learner-hour**: `ops.llm_usage` with a learner ÷
  distinct (learner, hour) with a learner action.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from quack.config import Settings
from quack.db.db import Db
from quack.graph.read import GraphReader, LevelFact, SkillFact

type Metric = dict[str, Any]

PER_THOUSAND = 1000
CALIBRATION_DIGITS = 1
# Events that are a learner's action: an hour with one of them is an hour of the learner.
LEARNER_ACTIONS = (
    "message.sent",
    "answer.submitted",
    "element.responded",
    "canvas.line_committed",
    "voice.transcribed",
    "hint.requested",
    "solution.requested",
    "check.skipped",
)

_TURNS = (
    "SELECT count(*) AS turns, "
    "count(*) FILTER (WHERE payload->'head'->>'branch_type' = 'corrective') AS corrective, "
    "count(*) FILTER (WHERE payload->>'degraded_mode' = 'teacher_fallback') AS fallback "
    "FROM core.events WHERE type = 'turn.committed' AND occurred_at >= %(since)s"
)
_VERDICTS = (
    "SELECT count(*) AS verdicts, "
    "count(*) FILTER (WHERE payload->>'outcome' = 'unclear') AS unclear, "
    "count(*) FILTER (WHERE COALESCE((payload->>'numeric_only')::boolean, false)) AS numeric_only "
    "FROM core.events WHERE type = 'verdict.recorded' AND occurred_at >= %(since)s"
)
_COUNT_OF_TYPE = (
    "SELECT count(*) AS n FROM core.events WHERE type = %(type)s AND occurred_at >= %(since)s"
)
_DISPUTES = (
    "SELECT payload->>'target_kind' AS kind, count(*) AS n FROM core.events "
    "WHERE type = 'dispute.filed' AND occurred_at >= %(since)s GROUP BY 1"
)
_SECOND_OPINIONS = (
    "SELECT count(*) AS total, "
    "count(*) FILTER (WHERE payload->>'agreed' = 'false') AS disagreed "
    "FROM core.events WHERE type = 'observation.second_opinion' AND occurred_at >= %(since)s"
)
_EXTRACTED = (
    "SELECT COALESCE(sum(jsonb_array_length(payload->'elements')), 0)::bigint AS elements, "
    "COALESCE(sum(jsonb_array_length(COALESCE(payload->'rejected', '[]'::jsonb))), 0)::bigint "
    "AS rejected FROM core.events "
    "WHERE type = 'observation.extracted' AND occurred_at >= %(since)s"
)
_TEMPORARY_SKILLS = (
    "SELECT count(*) AS created, count(m.seq) AS merged FROM core.events c "
    "LEFT JOIN LATERAL (SELECT seq FROM core.events m WHERE m.learner_id = c.learner_id "
    "AND m.type = 'skill.merged' AND m.payload->>'from_id' = c.payload->>'skill_id' LIMIT 1) m "
    "ON true WHERE c.type = 'skill.created' AND c.payload->>'status' = 'provisional' "
    "AND c.occurred_at >= %(since)s"
)
_COST = (
    "SELECT COALESCE(sum(cost_usd), 0)::float8 AS cost FROM ops.llm_usage "
    "WHERE learner_id IS NOT NULL AND created_at >= %(since)s"
)
_HOURS = (
    "SELECT count(DISTINCT (learner_id, date_trunc('hour', occurred_at))) AS hours "
    "FROM core.events WHERE occurred_at >= %(since)s AND type = ANY(%(types)s)"
)
# Т1 attempts: a code verdict that is right or wrong, with the answer, its session and the task.
_ATTEMPTS = (
    "SELECT v.learner_id, v.seq AS verdict_seq, a.seq AS answer_seq, a.session_id, "
    "v.payload AS verdict, t.payload->>'family_id' AS family_id, "
    "t.payload->'markup'->'tests' AS tests "
    "FROM core.events v "
    "JOIN core.events a ON a.learner_id = v.learner_id AND a.type = 'answer.submitted' "
    "AND a.seq = (v.payload->>'answer_seq')::bigint "
    "JOIN core.events t ON t.learner_id = v.learner_id AND t.type = 'task.created' "
    "AND t.payload->>'task_id' = a.payload->>'task_id' "
    "WHERE v.type = 'verdict.recorded' AND v.occurred_at >= %(since)s "
    "AND v.payload->>'verification' = 'code' "
    "AND v.payload->>'outcome' IN ('correct', 'incorrect') "
    "ORDER BY v.occurred_at, v.learner_id, v.seq"
)


def not_measured(reason: str) -> Metric:
    return {"status": "not_measured", "value": None, "reason": reason}


def _share(
    count: int, total: int, *, scale: int = 1, reason: str = "no events in the window"
) -> Metric:
    if total == 0:
        return not_measured(reason)
    return {"status": "measured", "value": scale * count / total, "count": count, "of": total}


@dataclass(frozen=True)
class _Attempt:
    learner_id: UUID
    answer_seq: int
    session_id: UUID | None
    family_id: str
    skills: list[tuple[UUID, int]]
    correct: bool
    helped: bool


def _attempt(row: dict[str, Any]) -> _Attempt:
    verdict = row["verdict"]
    return _Attempt(
        learner_id=row["learner_id"],
        answer_seq=int(row["answer_seq"]),
        session_id=row["session_id"],
        family_id=str(row["family_id"]),
        skills=[
            (UUID(str(t["skill_id"])), int(t["level"]))
            for t in row["tests"]
            if t.get("skill_id") is not None
        ],
        correct=verdict["outcome"] == "correct",
        helped=int(verdict.get("help_step", 0)) > 0 or bool(verdict.get("solution_shown", False)),
    )


def independent_success(attempts: list[_Attempt]) -> Metric:
    """Per (learner, skill): the first attempt in another session on a family not seen before
    for that skill is the «next attempt through time»; success — right and without help."""
    history: dict[tuple[UUID, UUID], list[_Attempt]] = defaultdict(list)
    later: dict[tuple[UUID, UUID], bool] = {}
    for attempt in attempts:
        for skill_id, _level in dict.fromkeys(attempt.skills):
            key = (attempt.learner_id, skill_id)
            before = history[key]
            if (
                key not in later
                and before
                and all(
                    p.session_id != attempt.session_id and p.family_id != attempt.family_id
                    for p in before
                )
            ):
                later[key] = attempt.correct and not attempt.helped
            before.append(attempt)
    return _share(
        sum(later.values()), len(later), reason="no skill has a later attempt in the window"
    )


def _probability(settings: Settings, skill: SkillFact | None, level: int) -> float | None:
    """The rule of Т-§18.2; `None` — the recall probability the rule needs is not known."""
    fact: LevelFact | None = (
        next((f for f in skill.levels if f.level == level), None) if skill else None
    )
    recall = None if skill is None else skill.recall_probability
    if fact is None or fact.status == "not_tested":
        return settings.p_unknown
    if fact.status == "unstable":
        return settings.p_unstable
    if fact.status == "failed":
        return settings.p_failed
    if recall is None:
        return None
    if fact.status == "proven" and fact.basis == "direct":
        return settings.p_proven * recall
    return settings.p_derived * recall  # implied, or proven through transfer


async def next_attempt_brier(
    settings: Settings,
    attempts: list[_Attempt],
    reader: Callable[[UUID], GraphReader] | None,
) -> Metric:
    if reader is None:
        return not_measured("needs GraphReader.state_at per attempt: no reader was given")
    pairs: list[tuple[float, float]] = []
    for attempt in attempts[-settings.metrics_brier_max_attempts :]:
        if not attempt.skills:
            continue
        state = await reader(attempt.learner_id).state_at(
            attempt.learner_id, attempt.answer_seq - 1, [s for s, _ in attempt.skills]
        )
        by_id = {s.skill_id: s for s in state.skills}
        for skill_id, level in dict.fromkeys(attempt.skills):
            p = _probability(settings, by_id.get(skill_id), level)
            if p is not None:
                pairs.append((p, 1.0 if attempt.correct else 0.0))
    if not pairs:
        return not_measured("no Т1 attempt with a known probability in the window")
    curve: dict[float, list[float]] = defaultdict(list)
    for p, outcome in pairs:
        curve[round(p, CALIBRATION_DIGITS)].append(outcome)
    return {
        "status": "measured",
        "value": sum((p - o) ** 2 for p, o in pairs) / len(pairs),
        "of": len(pairs),
        "calibration": [
            {"predicted": p, "observed": sum(o) / len(o), "of": len(o)}
            for p, o in sorted(curve.items())
        ],
    }


async def metrics(
    db: Db,
    since: datetime,
    *,
    settings: Settings | None = None,
    reader: Callable[[UUID], GraphReader] | None = None,
) -> dict[str, Any]:
    """Metrics of Т-§18.2 and Т-§18.3 over the events from `since` (a timezone-aware time).
    `reader(learner_id)` — `create_graph_reader(..., db=db)`: it gives the Brier score; without
    it that metric is «not measured»."""
    settings = settings or Settings()
    p = {"since": since}
    turns = await db.fetch_one(_TURNS, p) or {}
    verdicts = await db.fetch_one(_VERDICTS, p) or {}
    failed = (await db.fetch_one(_COUNT_OF_TYPE, {**p, "type": "check.failed"}) or {})["n"]
    diagnosed = (await db.fetch_one(_COUNT_OF_TYPE, {**p, "type": "observation.diagnosed"}) or {})[
        "n"
    ]
    disputes = {r["kind"]: r["n"] for r in await db.fetch_all(_DISPUTES, p)}
    opinions = await db.fetch_one(_SECOND_OPINIONS, p) or {}
    extracted = await db.fetch_one(_EXTRACTED, p) or {}
    temporary = await db.fetch_one(_TEMPORARY_SKILLS, p) or {}
    cost = (await db.fetch_one(_COST, p) or {})["cost"]
    hours = (await db.fetch_one(_HOURS, {**p, "types": list(LEARNER_ACTIONS)}) or {})["hours"]
    attempts = [_attempt(r) for r in await db.fetch_all(_ATTEMPTS, p)]
    n_verdicts = int(verdicts["verdicts"])
    rejected, elements = int(extracted["rejected"]), int(extracted["elements"])
    result: dict[str, Metric] = {
        "next_attempt_brier": await next_attempt_brier(settings, attempts, reader),
        "corrective_branch_share": _share(int(turns["corrective"]), int(turns["turns"])),
        "independent_success_later": independent_success(attempts),
        "disputes_per_1000_verdicts": {
            **_share(sum(disputes.values()), n_verdicts, scale=PER_THOUSAND),
            "by_kind": disputes,
        },
        "canvas_marks_removed_share": {
            **_share(int(opinions["disagreed"]), int(opinions["total"])),
            "marks": int(diagnosed),
        },
        "unclear_share": _share(int(verdicts["unclear"]), n_verdicts),
        "numeric_only_share": _share(int(verdicts["numeric_only"]), n_verdicts),
        "check_failed_share": _share(int(failed), n_verdicts + int(failed)),
        "teacher_fallback_turn_share": _share(int(turns["fallback"]), int(turns["turns"])),
        "extractor_rejection_share": _share(rejected, rejected + elements),
        "merged_temporary_skill_share": _share(int(temporary["merged"]), int(temporary["created"])),
        "cost_per_learner_hour_usd": _share(0, 0, reason="no learner action in the window")
        if int(hours) == 0
        else {
            "status": "measured",
            "value": float(cost) / int(hours),
            "cost_usd": float(cost),
            "of": int(hours),
        },
    }
    return {"since": since.astimezone(UTC).isoformat(), "metrics": result}
