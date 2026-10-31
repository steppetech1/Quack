"""Model IDs and Meta API names in `Settings` (ТЗ Фазы 3 §2.3 п.8, R4, §6.2, WP5.7)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quack.config import Settings

ENV_NAMES = ("META_API_KEY", "META_API_BASE_URL", "LLM_API_KEY", "LLM_API_BASE_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_default_models_are_the_live_ids() -> None:
    """§2.3 п.8: `muse-wonder-1.0` and `muse-spark-1.3` (Т-§1.3); the role split is kept."""
    s = _settings()
    assert s.model_for_role("teacher") == "muse-wonder-1.0"
    assert s.model_for_role("teacher_fallback") == "muse-spark-1.3"
    for role in ("extractor", "evaluator", "diagnostic", "task_gen", "structurer", "curator"):
        assert s.model_for_role(role) == "muse-spark-1.3"
    # The model limits of WP5 are keyed by the same IDs.
    for models in s.llm_concurrency.values():
        assert set(models) <= {"muse-wonder-1.0", "muse-spark-1.3"}


def test_meta_api_names_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4, Д-§7: `META_API_KEY`, `META_API_BASE_URL`; every reader sees the same values."""
    monkeypatch.setenv("META_API_KEY", "meta-key-1")
    monkeypatch.setenv("META_API_BASE_URL", "http://meta-mock:8080")
    s = _settings()
    assert s.meta_api_key.get_secret_value() == "meta-key-1"
    assert s.meta_api_base_url == "http://meta-mock:8080"
    assert s.llm_api_key.get_secret_value() == "meta-key-1"
    assert s.llm_api_base_url == "http://meta-mock:8080"


def test_old_llm_api_names_are_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4: the old `LLM_API_*` names still work for local `.env` files."""
    monkeypatch.setenv("LLM_API_KEY", "old-key-1")
    monkeypatch.setenv("LLM_API_BASE_URL", "http://old:1")
    s = _settings()
    assert s.meta_api_key.get_secret_value() == "old-key-1"
    assert s.meta_api_base_url == "http://old:1"


def test_prod_requires_meta_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="META_API_KEY"):
        _settings(env="prod", jwt_secret="j" * 32, neo4j_password="pw")
    monkeypatch.setenv("META_API_KEY", "meta-key-1")
    s = _settings(env="prod", jwt_secret="j" * 32, neo4j_password="pw")
    assert "meta-key-1" in s.secret_values()
