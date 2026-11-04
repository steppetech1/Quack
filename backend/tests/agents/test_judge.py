"""`release_judge` (ТЗ Ф4 WP16.7; Т-§18.4; D-B-194): both orders go to the model; the turn that
wins in both wins, a disagreement is a tie."""

from __future__ import annotations

from uuid import uuid4

from quack.agents.judge import release_judge
from quack.config import Settings
from quack.db import Db
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ProviderRequest
from tests.support.llm import ScriptedLLMProvider

SETTINGS = Settings()
SCHEMA = "ReleaseJudgeOutput"


def _prefers(marker: str) -> object:
    def respond(request: ProviderRequest) -> dict[str, str]:
        first = request.prompt.split("### Turn B")[0]
        return {"winner": "a" if marker in first else "b", "reason": "x"}

    return respond


async def test_judge_symmetric_and_tie_on_disagreement(pg_db: Db) -> None:
    provider = ScriptedLLMProvider()
    judge = release_judge(LLMGateway(provider, SETTINGS, db=pg_db))
    learner_id = uuid4()
    provider.respond(SCHEMA, _prefers("GOOD"))  # type: ignore[arg-type]
    won = await judge(learner_id=learner_id, situation="s", old="bad", new="GOOD", key="k1")
    assert won == "new" and len(provider.calls) == 2
    provider.respond(SCHEMA, lambda _r: {"winner": "a", "reason": "position bias"})
    tied = await judge(learner_id=learner_id, situation="s", old="x", new="y", key="k2")
    assert tied == "tie"
