"""Errors of the event log."""

from __future__ import annotations


class IdempotencyConflictError(Exception):
    """Same (learner_id, idempotency_key) with a different payload_hash (HTTP 409)."""

    def __init__(self, idempotency_key: str, existing_hash: str, new_hash: str) -> None:
        super().__init__(f"idempotency key {idempotency_key!r} reused with a different payload")
        self.idempotency_key = idempotency_key
        self.existing_hash = existing_hash
        self.new_hash = new_hash
