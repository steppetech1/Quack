"""`GraphReader.profile` on Neo4j (Т-§9.5, ТЗ Ф4 WP5.3): the `Profile` node of a learner.

One function the core of the reads (WP6.4) plugs in: `read_profile(driver, learner_id)`. The
watermark check of the whole read (Т-§2.6) is the caller's. `learned*` properties are written
by Phase B only when there is data, so an absent property is `None` — "no data" (law 8).
"""

from __future__ import annotations

import re
from typing import Any, get_args
from uuid import UUID

from neo4j import AsyncDriver

from quack.db.neo4j import read_session
from quack.graph.read.models import ConfidenceBias, ProfileFacts

_BIASES = frozenset(get_args(ConfidenceBias.__value__))
_UPPER = re.compile(r"(?<!^)(?=[A-Z])")


def _pref_name(prop: str) -> str:
    """`prefAnswerLength` -> `answer_length` (the inverse of `pref_key`)."""
    return _UPPER.sub("_", prop[len("pref") :]).lower()


def _unit(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def profile_from_props(learner_id: UUID, props: dict[str, Any]) -> ProfileFacts:
    prefs = {
        _pref_name(key): value
        for key, value in sorted(props.items())
        if key.startswith("pref")
        and len(key) > len("pref")
        and key[len("pref")].isupper()
        and isinstance(value, str | int | float | bool)
    }
    approach = props.get("learnedHelpfulApproach")
    manner = props.get("learnedManner")
    bias = props.get("learnedConfidenceBias")
    return ProfileFacts(
        learner_id=learner_id,
        prefs=prefs,
        manner=manner if isinstance(manner, str) else None,
        learned_helpful_approach=approach if isinstance(approach, str) else None,
        learned_confidence_brier=_unit(props.get("learnedConfidenceBrier")),
        learned_confidence_bias=bias if bias in _BIASES else None,
        learned_branch_rate=_unit(props.get("learnedBranchRate")),
        learned_reasoning_share=_unit(props.get("learnedReasoningShare")),
    )


async def read_profile(driver: AsyncDriver, learner_id: UUID) -> ProfileFacts:
    """Slot 3 of the teacher context. No `Profile` node yet — an empty profile of the learner
    (no preferences, no manner, nothing learned)."""
    async with read_session(driver) as session:
        result = await session.run(
            "MATCH (p:Profile {learnerId: $learner_id}) RETURN properties(p) AS props",
            {"learner_id": str(learner_id)},
        )
        record = await result.single()
    if record is None:
        return ProfileFacts(learner_id=learner_id)
    return profile_from_props(learner_id, dict(record["props"]))
