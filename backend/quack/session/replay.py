"""Replay of a session with a new teacher — `ops replay-session` (ТЗ Ф4 WP16.7; Т-§12.8, Т-§18.4).
D-B-194.

Offline: the situation of each teacher turn is rebuilt from its manifest row
(`ops.turn_manifests`: the watermark `projected_seq`, the label table) and from memory **as it
was** — `GraphReader.state_at(learner, projected_seq, skills)` instead of the slice read now
(`AsOfReader`); the journal up to the action of the turn and the frozen slots 3–4 are those of
the time. The same context source and turn graph as the live turn build the new turn
(`MemoryTurnContextSource`, `run_turn`) over **offline ports**: the commit keeps the turn in
memory, the frames go nowhere, nothing is written to `core.events` of the learner (law 1, law 7;
the model calls still go to the ledger of the gateway). The report — a JSON file — gives the
share of turns that broke a rule of Т-§12.4 (old and new) and the pairwise comparison of the
judge (`quack.agents.release_judge`, passed in as `judge`: product packages do not import each
other, §4.1).

What the replay does not restore (listed in the report, law 8): the misconceptions of the slice
(`state_at` replays skills only), the material chunks of slot 8, the analyst summary and the
agent results (read now), the renderer version (only `renderer@1` exists in code).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid5

import orjson

from quack.config import Settings
from quack.db.db import Db
from quack.graph.overlay import SessionOverlay
from quack.graph.read import GraphReader, MemorySlice, SliceFocus
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LABEL_PREFIX
from quack.llm.streaming import StreamEvent, StreamingLLMProvider, StreamRequest
from quack.practice.repo import session_events_up_to
from quack.session.action_turn import TurnRequest, TurnSetup
from quack.session.actions import Action
from quack.session.conversation import render_conversation
from quack.session.guards import head_violations, markup_violations
from quack.session.teacher_output import TurnTask
from quack.session.turn_graph import AssembledTurn, Frame, MarkupFix, Role, TurnToCommit, run_turn
from quack.session.turn_source import RENDERER_VERSION, MemoryTurnContextSource

type Winner = Literal["old", "new", "tie"]

TEACHER_ROLE: Role = "teacher"
_SKILL_PREFIX = LABEL_PREFIX["skill"]
_MANIFESTS = (
    "SELECT m.manifest_id, m.learner_id, m.session_id, m.projected_seq, "
    "m.label_table, m.prompt_version, "
    "e.seq AS turn_seq, e.domain_id, e.payload "
    "FROM ops.turn_manifests m JOIN core.events e ON e.learner_id = m.learner_id "
    "AND e.type = 'turn.committed' AND e.payload->>'manifest_id' = m.manifest_id::text "
    "WHERE m.learner_id = %(learner_id)s AND m.session_id = %(session_id)s ORDER BY e.seq"
)
# The operator names only the session (`ops replay-session --session`): its learner is found by
# the id, as auth finds the learner of a token (D-B-194, cross-learner by design).
_SESSION_LEARNER = "SELECT learner_id FROM content.sessions WHERE session_id = %(session_id)s"
# What the replay does not restore of the situation (law 8: said, not silently empty).
NOT_RESTORED: tuple[str, ...] = (
    "misconceptions of the memory slice (state_at replays skills only)",
    "material chunks of slot 8 (no prefetch offline)",
    "analyst summary and agent results are read as of now",
)


class PairJudge(Protocol):
    """`quack.agents.release_judge(gateway)`: both orders, the winner of the pair."""

    async def __call__(
        self, *, learner_id: UUID, situation: str, old: str, new: str, key: str
    ) -> Winner: ...


def parse_prompt_ref(ref: str) -> tuple[str, int]:
    """`teacher@3` → `("teacher", 3)`."""
    name, _, number = ref.partition("@")
    if not name or not number.isdigit():
        raise ValueError(f"a prompt is named `name@N`, got {ref!r}")
    return name, int(number)


class AsOfReader:
    """The memory of the turn's time: `slice_for_turn` is `state_at(projected_seq)` of the skills
    in focus and the skills the old turn saw (`seen`, from its label table); every other read
    is the live reader's (the tools and the goals are read now)."""

    def __init__(self, live: GraphReader, projected_seq: int, seen: list[UUID]) -> None:
        self._live = live
        self._seq = projected_seq
        self._seen = seen

    async def slice_for_turn(
        self,
        learner_id: UUID,
        session_id: UUID,
        focus: SliceFocus,
        overlay: SessionOverlay | None = None,
    ) -> MemorySlice:
        skills = list(dict.fromkeys([*focus.skill_ids, *self._seen]))
        state = await self._live.state_at(learner_id, self._seq, skills)
        return MemorySlice(learner_id=learner_id, projected_seq=self._seq, skills=state.skills)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._live, name)


@dataclass(frozen=True)
class _Captured:
    text: str
    guard_retries: int
    violations: list[str]


class _OfflinePorts:
    """`TurnPorts` without a lock, a stream and a write: the commit keeps the turn."""

    def __init__(self, source: MemoryTurnContextSource, request: TurnRequest) -> None:
        self._source = source
        self._request = request
        self._setup: TurnSetup | None = None
        self.captured: _Captured | None = None

    @property
    def setup(self) -> TurnSetup:
        if self._setup is None:
            raise RuntimeError("the turn has no context yet")
        return self._setup

    async def admit(self) -> Role:
        return TEACHER_ROLE

    async def assemble(self, role: Role) -> AssembledTurn:
        self._setup = await self._source.assemble(replace(self._request, role=role))
        return self._setup.assembled

    async def execute_tool(self, name: str, arguments: str) -> str:
        return await self.setup.execute_tool(name, arguments)

    async def commit(self, turn: TurnToCommit) -> int:
        guard = self.setup.assembled.guard
        output = turn.output
        found = head_violations(
            output.p1_head,
            answered_verdict=guard.answered_verdict,
            skill_labels=guard.skill_labels,
            boundary=guard.boundary,
        )
        found += markup_violations(
            output.p3_markup,
            skill_labels=guard.skill_labels,
            misconception_labels=guard.misconception_labels,
        )
        reasons = [v.reason for v in found]
        if guard.has_spoiler(turn.text):
            reasons.append("spoiler")
        self.captured = _Captured(turn.text, turn.guard_retries, reasons)
        return 0

    async def emit(self, frame: Frame) -> None:
        return None

    async def fix_markup(self, fix: MarkupFix) -> TurnTask | None:
        return None


class _Provider:
    """The graph gets its provider before `assemble`; the role's provider comes with it."""

    def __init__(self, ports: _OfflinePorts) -> None:
        self._ports = ports

    def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        provider: StreamingLLMProvider = self._ports.setup.provider
        return provider.stream_structured(request)


def _with_teacher(settings: Settings, ref: str) -> Settings:
    role = settings.llm_roles[TEACHER_ROLE].model_copy(update={"prompt": ref})
    return settings.model_copy(update={"llm_roles": {**settings.llm_roles, TEACHER_ROLE: role}})


def _skill_ids(label_table: dict[str, str]) -> list[UUID]:
    return [
        UUID(object_id)
        for label, object_id in label_table.items()
        if label.startswith(_SKILL_PREFIX) and label[len(_SKILL_PREFIX) :].isdigit()
    ]


def _share(count: int, total: int) -> float | None:
    return None if total == 0 else count / total


async def _replay_turn(
    db: Db,
    llm: LLMGateway,
    settings: Settings,
    reader: Callable[[UUID], GraphReader],
    judge: PairJudge,
    row: dict[str, Any],
    teacher: str,
) -> dict[str, Any]:
    learner_id: UUID = row["learner_id"]
    payload: dict[str, Any] = row["payload"]
    if row["domain_id"] is None:
        raise ValueError("the turn has no domain")
    manifest_id: UUID = row["manifest_id"]
    action_seq = int(payload["action_seq"])
    request = TurnRequest(
        learner_id=learner_id,
        session_id=row["session_id"],
        domain_id=row["domain_id"],
        action_seq=action_seq,
        action=cast(Action, None),  # the context source reads the journal, not the action
        role=TEACHER_ROLE,
        manifest_id=uuid5(manifest_id, teacher),
    )
    projected = int(row["projected_seq"])
    seen = _skill_ids(row["label_table"])
    source = MemoryTurnContextSource(
        db=db,
        gateway=llm,
        settings=settings,
        reader=lambda learner: cast(GraphReader, AsOfReader(reader(learner), projected, seen)),
        neo4j=None,
    )
    ports = _OfflinePorts(source, request)

    await run_turn(ports, _Provider(ports), settings=settings, cancel=asyncio.Event())
    new = ports.captured
    if new is None:
        raise RuntimeError("the replayed turn did not commit")
    old_text = str(payload["text"])
    events = await session_events_up_to(
        db, learner_id=learner_id, session_id=request.session_id, to_seq=action_seq
    )
    situation = render_conversation(events, ports.setup.labels, settings)
    winner = await judge(
        learner_id=learner_id,
        situation=situation,
        old=old_text,
        new=new.text,
        key=f"replay:{request.manifest_id}",
    )
    old_retries = int(payload["guard_retries"])
    return {
        "manifest_id": str(manifest_id),
        "turn_seq": row["turn_seq"],
        "action_seq": action_seq,
        "projected_seq": projected,
        "old": {"text": old_text, "guard_retries": old_retries, "violated": old_retries > 0},
        "new": {
            "text": new.text,
            "guard_retries": new.guard_retries,
            "residual_violations": new.violations,
            "violated": new.guard_retries > 0 or bool(new.violations),
        },
        "judge": winner,
        "error": None,
    }


def _summary(turns: list[dict[str, Any]], total: int) -> dict[str, Any]:
    done = [t for t in turns if t["error"] is None]
    old_share = _share(sum(t["old"]["violated"] for t in done), len(done))
    new_share = _share(sum(t["new"]["violated"] for t in done), len(done))
    wins = {w: sum(t["judge"] == w for t in done) for w in ("new", "old", "tie")}
    return {
        "turns_total": total,
        "turns_replayed": len(done),
        "turns_failed": total - len(done),
        "violation_share": {"old": old_share, "new": new_share},
        # `None` — nothing was replayed: not measured, not «passed» (law 8).
        "violations_not_grown": None
        if old_share is None or new_share is None
        else new_share <= old_share,
        "judge": wins,
        "judge_not_worse": None if not done else wins["new"] >= wins["old"],
    }


async def session_learner(db: Db, session_id: UUID) -> UUID | None:
    """The learner of a session for the operator command, which names only the session."""
    row = await db.fetch_one(_SESSION_LEARNER, {"session_id": session_id})
    return None if row is None else row["learner_id"]


async def replay_session(
    db: Db,
    *,
    learner_id: UUID,
    session_id: UUID,
    teacher: str,
    llm: LLMGateway,
    settings: Settings,
    reader: Callable[[UUID], GraphReader],
    judge: PairJudge,
    out: Path,
    renderer: int | None = None,
) -> dict[str, Any]:
    """Replay every teacher turn of `session_id` with the prompt `teacher` (`name@N`, the
    `teacher` role's template) and write the report to `out`; returns the report.

    `learner_id` — the session's learner (`session_learner`). `reader(learner_id)` must be
    bound to `db` (`create_graph_reader(..., db=db)`: `state_at`
    replays the log of Phase 4). A turn that fails (the model, the context) is in the report
    with its error and does not stop the rest. Nothing is written to `core.events`."""
    name, version = parse_prompt_ref(teacher)
    llm.prompts.load(name, version)  # an unknown prompt fails before any model call
    replayed = _with_teacher(settings, teacher)
    rows = await db.fetch_all(_MANIFESTS, {"learner_id": learner_id, "session_id": session_id})
    turns: list[dict[str, Any]] = []
    for row in rows:
        try:
            turns.append(await _replay_turn(db, llm, replayed, reader, judge, row, teacher))
        except Exception as exc:  # one failed turn does not stop the replay (law 7)
            turns.append(
                {
                    "manifest_id": str(row["manifest_id"]),
                    "turn_seq": row["turn_seq"],
                    "error": repr(exc),
                }
            )
    notes = list(NOT_RESTORED)
    if renderer is not None and f"renderer@{renderer}" != RENDERER_VERSION:
        notes.append(f"renderer@{renderer} is not in the code: turns built with {RENDERER_VERSION}")
    report: dict[str, Any] = {
        "session_id": str(session_id),
        "teacher": teacher,
        "renderer": {"requested": renderer, "applied": RENDERER_VERSION},
        "baseline_prompts": sorted({str(r["prompt_version"]) for r in rows}),
        "summary": _summary(turns, len(rows)),
        "turns": turns,
        "not_restored": notes,
    }
    await asyncio.to_thread(out.write_bytes, orjson.dumps(report, option=orjson.OPT_INDENT_2))
    return report
