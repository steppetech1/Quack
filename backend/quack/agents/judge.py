"""The pairwise judge of the release gate — `release_judge@1` (ТЗ Ф4 WP16.7; Т-§18.4). D-B-194.

Two turns of the same situation, old and new, go to the judge twice, in both orders (the prompt
requires a symmetric choice, Т-§18.4 «судья — воспроизводимая оценка»): the turn that wins in
both orders wins, any disagreement or a tie is a tie. The replay (`quack.session.replay`, a
sibling product package) takes the judge as a callable, so the two packages do not import each
other (§4.1): `release_judge(gateway)` is that callable.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from quack.agents.schemas import ReleaseJudgeOutput
from quack.llm.gateway import LLMGateway

JUDGE_ROLE = "release_judge"
# The prompt has a `{{ criteria }}` block; the gate of Т-§18.4 names the rules of the turn.
CRITERIA = (
    "Compare the two teacher turns by the criteria of your role: the pedagogical guardrails "
    "first, then the learner's request, the diagnosed boundary, the grounding, the language."
)

type Winner = Literal["old", "new", "tie"]


async def judge_pair(
    gateway: LLMGateway, *, learner_id: UUID, situation: str, old: str, new: str, key: str
) -> Winner:
    """`old` / `new` — the shown texts of the two versions; `key` — the idempotency key of the
    pair (a repeat of the replay does not pay for the same comparison twice, law 5)."""
    prompt = gateway.settings.llm_roles[JUDGE_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {JUDGE_ROLE!r} has no prompt")
    version = int(prompt.rsplit("@", 1)[1])
    seen: list[Winner] = []
    for order, (first, second) in (("old-first", (old, new)), ("new-first", (new, old))):
        result = await gateway.generate_structured(
            JUDGE_ROLE,
            version,
            ReleaseJudgeOutput,
            {"situation": situation, "turn_a": first, "turn_b": second, "criteria": CRITERIA},
            learner_id,
            None,
            None,
            f"{key}:{order}",
        )
        winner = result.value.winner
        if winner == "tie":
            seen.append("tie")
        elif (winner == "a") == (order == "old-first"):
            seen.append("old")
        else:
            seen.append("new")
    return seen[0] if seen[0] == seen[1] else "tie"


class _Judge:
    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    async def __call__(
        self, *, learner_id: UUID, situation: str, old: str, new: str, key: str
    ) -> Winner:
        return await judge_pair(
            self._gateway, learner_id=learner_id, situation=situation, old=old, new=new, key=key
        )


def release_judge(gateway: LLMGateway) -> _Judge:
    """The judge for `quack.session.replay.replay_session(judge=...)`."""
    return _Judge(gateway)
