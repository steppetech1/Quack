"""Review of stream C on `quack/config.py` in PR #208 (issue #209 item 6): the price table,
the role-model priority and the bounds of the stream B parameters."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quack.config import ModelPrice, RoleConfig, Settings

TOKENS = {"input": 1.0, "cached_input": 0.1, "output": 4.0}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_PRICES", "LLM_ROLE_MODELS", "META_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


# --- remark 1: ModelPrice ----------------------------------------------------------------


def test_token_prices_are_required() -> None:
    with pytest.raises(ValidationError):
        ModelPrice.model_validate({})
    with pytest.raises(ValidationError):
        ModelPrice.model_validate({"input": 1.0, "output": 4.0})  # no cached_input
    price = ModelPrice.model_validate(TOKENS)
    assert (price.per_minute, price.per_image) == (0.0, 0.0)  # the other two stay optional


def test_a_misspelled_price_key_is_an_error() -> None:
    with pytest.raises(ValidationError, match="inptu"):
        ModelPrice.model_validate({**TOKENS, "inptu": 1.0})


@pytest.mark.parametrize("field", ["input", "cached_input", "output", "per_minute", "per_image"])
def test_a_negative_price_is_an_error(field: str) -> None:
    with pytest.raises(ValidationError):
        ModelPrice.model_validate({**TOKENS, field: -0.01})


def test_per_minute_and_per_image_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LLM_PRICES",
        '{"voice-m": {"input": 0, "cached_input": 0, "output": 0, "per_minute": 0.006},'
        ' "image-m": {"input": 0, "cached_input": 0, "output": 0, "per_image": 0.04}}',
    )
    prices = _settings().llm_prices
    assert prices["voice-m"].per_minute == 0.006
    assert prices["image-m"].per_image == 0.04
    assert prices["image-m"].per_minute == 0.0


def test_a_broken_price_in_the_environment_fails_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PRICES", '{"m": {"input": 1}}')
    with pytest.raises(ValidationError, match="cached_input"):
        _settings()


# --- remark 2: an empty LLM_PRICES --------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "{}"])
def test_an_empty_llm_prices_is_the_empty_table(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """compose passes `LLM_PRICES: ${LLM_PRICES:-}`: on dev the variable is an empty string."""
    monkeypatch.setenv("LLM_PRICES", raw)
    assert _settings(env="dev").llm_prices == {}
    monkeypatch.setenv("META_API_KEY", "k")
    for role in ("api", "workers"):  # prod still refuses it (D-B-198)
        with pytest.raises(ValidationError, match="LLM_PRICES is required"):
            _settings(env="prod", process_role=role, neo4j_password="pw")
    _settings(env="prod", process_role="projector", neo4j_password="pw")


def test_llm_prices_json_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICES", '{"m": {"input": 2, "cached_input": 0.5, "output": 8}}')
    assert _settings().llm_prices["m"].output == 8.0


# --- remark 6: the role model priority (D-B-188) ------------------------------------------


def _row(model: str, **over: object) -> RoleConfig:
    return RoleConfig(
        model=model,
        effort="low",
        max_output_tokens=100,
        priority_class="P1",
        timeout_s=10.0,
        prompt=None,
        **over,  # type: ignore[arg-type]
    )


def test_llm_role_models_wins_over_the_role_row() -> None:
    row = _row("row-model")
    s = _settings(
        llm_roles={"teacher": row, "extractor": row},
        llm_role_models={"teacher": "explicit-model"},
        llm_default_model="default-model",
    )
    assert s.model_for_role("teacher") == "explicit-model"  # the explicit entry
    assert s.model_for_role("extractor") == "row-model"  # the row of the table
    assert s.model_for_role("nobody") == "default-model"  # a role outside the table


def test_hard_model_wins_only_for_a_hard_call() -> None:
    row = _row("spark", hard_model="wonder")
    s = _settings(llm_roles={"r": row}, llm_role_models={"r": "explicit-model"})
    assert s.model_for_role("r") == "explicit-model"
    assert s.model_for_role("r", hard=True) == "wonder"


# --- remark 4: bounds of the stream B parameters ------------------------------------------


@pytest.mark.parametrize("name", ["metrics_brier_max_attempts", "known_check_max_tasks"])
def test_counts_of_stream_b_are_at_least_one(name: str) -> None:
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            _settings(**{name: bad})
    assert getattr(_settings(**{name: 1}), name) == 1
