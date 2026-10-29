"""Canonical payload hash (spec §2.2): sha256 of orjson with sorted keys."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson


def canonical_json(payload: dict[str, Any]) -> bytes:
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()
