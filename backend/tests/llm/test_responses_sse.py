"""WP10.7: one `data:` line of the `/responses` stream. The live API ends the stream with
`data: [DONE]` (not JSON); the live run of 2026-11-04 crashed on it."""

from __future__ import annotations

from quack.llm.responses_client import _data_event  # pyright: ignore[reportPrivateUsage]


def test_data_line_is_json_and_the_terminator_and_other_lines_are_not() -> None:
    assert _data_event('data: {"type": "response.completed"}') == {"type": "response.completed"}
    assert _data_event("data: [DONE]") is None
    assert _data_event("data:") is None
    assert _data_event("event: response.completed") is None
    assert _data_event("") is None
