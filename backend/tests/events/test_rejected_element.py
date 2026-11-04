"""Q-A-2 п.3: a rejected element keeps its index, reason and quote (provenance, law 6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quack.events.registry import validate_payload
from tests.events.samples import sample_payload


def test_rejected_element_has_index_reason_and_quote() -> None:
    payload = {
        **sample_payload("observation.extracted"),
        "rejected": [{"index": 2, "reason": "quote_not_in_episode", "quote": "x = 1"}],
    }
    parsed = validate_payload("observation.extracted", payload)
    assert parsed.rejected[0].index == 2  # type: ignore[attr-defined]


def test_rejected_element_without_index_is_refused() -> None:
    payload = {
        **sample_payload("observation.extracted"),
        "rejected": [{"reason": "quote_not_in_episode", "quote": "x = 1"}],
    }
    with pytest.raises(ValidationError):
        validate_payload("observation.extracted", payload)
