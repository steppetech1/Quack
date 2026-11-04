"""`ops replay-session` (ТЗ Ф4 WP16.7; Т-§12.8, Т-§18.4; D-B-194): the turns of a session are
built again offline by another teacher version and compared with the shown ones; the journal of
the learner does not change."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import orjson

from quack.config import Settings
from quack.db import Db
from quack.events.event_types import MessageSent
from quack.events.log import NewEvent, append_events
from quack.graph.read import GraphReader
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.llm.streaming import StreamUsage
from quack.session.replay import replay_session
from quack.session.teacher_output import TeacherOutputV2
from quack.session.turn_commit import TurnManifestRow, commit_turn, turn_batch
from quack.session.turn_graph import TurnToCommit
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx
from tests.support.llm import ScriptedLLMProvider

SETTINGS = Settings()
OLD_TEXT = "The old teacher explains it."
NEW_TEXT = "The new teacher asks one question first."


def _turn_output(text: str) -> dict[str, Any]:
    return {
        "p1_head": {
            "intent": "explain",
            "targetSkills": ["S1"],
            "verdict_stance": None,
            "branchType": None,
            "topicLabel": None,
            "approach": "example_first",
        },
        "p2_body": {"blocks": [{"block_id": "b1", "text": text, "element_id": None}]},
        "p3_markup": {
            "tasks": [],
            "elements": [],
            "exposures": [],
            "targetsMisconception": None,
            "planUpdate": {
                "completed_steps": [],
                "current_step": "fractions",
                "next_steps": [],
                "branches": [],
                "cancel_requests": [],
            },
            "agentRequests": [],
            "correctionsAcknowledged": [],
            "decisionReason": "explain first",
            "deadline": None,
        },
    }


class _Judge:
    async def __call__(
        self, *, learner_id: UUID, situation: str, old: str, new: str, key: str
    ) -> Any:
        assert old == OLD_TEXT and new == NEW_TEXT and "teach me" in situation
        return "new"


async def _journal(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, type, payload_hash FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )


async def test_replay_builds_the_turn_offline_and_writes_no_event(
    pg_db: Db, tmp_path: Path
) -> None:
    """The old turn (one guard retry) is replayed by `teacher@2` with a scripted model: the
    report has both texts, the violation shares, the judge's winner; the journal is unchanged."""
    learner_id, session_id = await create_learner(pg_db), uuid4()
    (action,) = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="message.sent",
                payload=MessageSent(text="teach me", intent="learn", topic_id=fx.TOPIC_OPEN),
                actor="learner",
                idempotency_key="msg",
                session_id=session_id,
                domain_id=fx.DOMAIN,
            )
        ],
    )
    labels = LabelMap.build(SETTINGS, skills=[fx.SKILL_FAILED])
    manifest = TurnManifestRow(
        manifest_id=uuid4(),
        learner_id=learner_id,
        session_id=session_id,
        prompt_version="teacher@2",
        doctrine_version=None,
        renderer_version="renderer@1",
        role="teacher",
        model="muse-wonder-1.0",
        effort="low",
        projected_seq=action.seq,
        label_table=labels.table(),
    )
    old = TurnToCommit(
        output=TeacherOutputV2.model_validate(_turn_output(OLD_TEXT)),
        text=OLD_TEXT,
        role="teacher",
        model="muse-wonder-1.0",
        effort="low",
        degraded_mode=None,
        guard_retries=1,
        usage=StreamUsage(input_tokens=1, output_tokens=1),
    )
    batch = turn_batch(
        old,
        labels,
        manifest=manifest,
        action_seq=action.seq,
        domain_id=fx.DOMAIN,
        settings=SETTINGS,
    )
    await commit_turn(pg_db, batch, learner_id=learner_id)
    before = await _journal(pg_db, learner_id)

    provider = ScriptedLLMProvider()
    provider.respond("TeacherOutputV2", lambda _r: _turn_output(NEW_TEXT))
    out = tmp_path / "report.json"

    def reader(_learner: UUID) -> GraphReader:
        return fx.FixedGraphReader()

    report = await replay_session(
        pg_db,
        learner_id=learner_id,
        session_id=session_id,
        teacher="teacher@2",
        llm=LLMGateway(provider, SETTINGS, db=pg_db),
        settings=SETTINGS,
        reader=reader,
        judge=_Judge(),
        out=out,
        renderer=2,
    )

    assert await _journal(pg_db, learner_id) == before
    assert orjson.loads(out.read_bytes()) == report
    summary = report["summary"]
    assert summary["turns_replayed"] == 1 and summary["turns_failed"] == 0, report["turns"]
    assert summary["violation_share"] == {"old": 1.0, "new": 0.0}
    assert summary["violations_not_grown"] is True and summary["judge"]["new"] == 1
    [turn] = report["turns"]
    assert turn["old"]["text"] == OLD_TEXT and turn["new"]["text"] == NEW_TEXT
    assert any("renderer@2" in note for note in report["not_restored"])
