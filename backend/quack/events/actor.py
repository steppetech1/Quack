"""Format of `core.events.actor` (spec §2.4)."""

from __future__ import annotations

import re

FIXED_ACTORS = frozenset({"learner", "system", "code"})
_ROLE_VERSION = re.compile(r"^[a-z][a-z0-9_]*@[1-9][0-9]*$")
# ТЗ Ф4 WP2.1 (Т-§2.1): background agents write as `agent:<kind>`.
_AGENT = re.compile(r"^agent:[a-z][a-z0-9_]*$")


def is_valid_actor(actor: str) -> bool:
    return (
        actor in FIXED_ACTORS
        or bool(_ROLE_VERSION.fullmatch(actor))
        or bool(_AGENT.fullmatch(actor))
    )


def validate_actor(actor: str) -> str:
    if not is_valid_actor(actor):
        raise ValueError(
            f"invalid actor {actor!r}: expected learner|system|code or <role>@<N> or agent:<kind>"
        )
    return actor
