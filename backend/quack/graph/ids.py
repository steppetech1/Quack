"""Deterministic ids and the rules version (spec §2.3, §2.5)."""

from __future__ import annotations

from uuid import UUID, uuid5

QUACK_UUID_NS = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

CURRENT_RULES_VERSION_NUM = 1
CURRENT_RULES_VERSION = f"rules@{CURRENT_RULES_VERSION_NUM}"


def deterministic_id(learner_id: UUID | str, seq: int, item_index: int | str) -> str:
    """uuid5(QUACK_UUID_NS, f"{learner_id}:{seq}:{item_index}") as a string."""
    return str(uuid5(QUACK_UUID_NS, f"{learner_id}:{seq}:{item_index}"))


def rules_version_number(version: str) -> int:
    """Integer N of 'rules@N'; comparisons are done on N (spec §2.5)."""
    prefix = "rules@"
    if not version.startswith(prefix) or not version[len(prefix) :].isdigit():
        raise ValueError(f"invalid rules version {version!r}")
    return int(version[len(prefix) :])
