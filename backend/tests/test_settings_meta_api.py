"""Model IDs and Meta API names in `Settings` (ТЗ Фазы 3 §2.3 п.8, R4, §6.2, WP5.7)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quack.config import Settings

PRICES = {"muse-wonder-1.0": {"input": 1.0, "cached_input": 0.1, "output": 4.0}}
ENV_NAMES = ("META_API_KEY", "META_API_BASE_URL", "LLM_API_KEY", "LLM_API_BASE_URL")
# The rest of the prod secrets of `api` and `workers` (Д-§7), so that the tests below can reach
# the check they are about.
PROD_REST = {
    "email_api_key": "mail-key",
    "google_oidc_client_id": "google-id",
    "google_oidc_client_secret": "google-secret",
    "terms_version": "2026-11",
    "s3_endpoint": "https://s3.example",
    "s3_access_key": "s3-key",
    "s3_secret_key": "s3-secret",
    "deletions_s3_endpoint": "https://r2.example",
    "deletions_s3_access_key": "r2-key",
    "deletions_s3_secret_key": "r2-secret",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_prod_requires_the_price_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """ТЗ Ф4 §2.3 row 9 (WP10.4, D-B-198): `api` and `workers` do not start in prod without
    prices; `projector`, `ops` and `dev`/`test` do."""
    monkeypatch.setenv("META_API_KEY", "meta-key-1")
    for role in ("api", "workers"):
        with pytest.raises(ValidationError, match="LLM_PRICES"):
            _settings(env="prod", process_role=role, neo4j_password="pw", **PROD_REST)
    _settings(env="prod", process_role="projector", neo4j_password="pw")
    _settings(env="dev")


def test_default_models_are_the_live_ids() -> None:
    """§2.3 п.8: `muse-wonder-1.0` and `muse-spark-1.3` (Т-§1.3); the role split is kept."""
    s = _settings()
    assert s.model_for_role("teacher") == "muse-wonder-1.0"
    assert s.model_for_role("teacher_fallback") == "muse-spark-1.3"
    # D-B-188: the row of `llm_roles` (Т-§11.1) wins over the default text model.
    for role in ("verdict_proof", "second_opinion", "analyst_deep", "task_arbiter"):
        assert s.model_for_role(role) == "muse-wonder-1.0"
    assert s.model_for_role("segmenter") == "sam-3.1"
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
        _settings(env="prod", neo4j_password="pw", llm_prices=PRICES, **PROD_REST)
    monkeypatch.setenv("META_API_KEY", "meta-key-1")
    s = _settings(env="prod", neo4j_password="pw", llm_prices=PRICES, **PROD_REST)
    assert "meta-key-1" in s.secret_values()


STORAGE = {
    "S3_ENDPOINT",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "DELETIONS_S3_ENDPOINT",
    "DELETIONS_S3_ACCESS_KEY",
    "DELETIONS_S3_SECRET_KEY",
}
API_ONLY = {"EMAIL_API_KEY", "GOOGLE_OIDC_CLIENT_ID", "GOOGLE_OIDC_CLIENT_SECRET", "TERMS_VERSION"}


@pytest.mark.parametrize(
    ("role", "required"),
    [
        ("api", {"META_API_KEY", "NEO4J_PASSWORD"} | API_ONLY | STORAGE),
        ("workers", {"META_API_KEY", "NEO4J_PASSWORD"} | STORAGE),
        ("projector", {"NEO4J_PASSWORD"}),
        ("ops", set()),
    ],
)
def test_prod_secrets_follow_the_process_role(
    monkeypatch: pytest.MonkeyPatch, role: str, required: set[str]
) -> None:
    """ТЗ WP9.2, Д-§7: each compose service gets only its own secrets (`projector` and
    `migrate` have no `META_API_KEY`); prod requires exactly those of the role. `api` also needs
    mail, Google sign-in, the terms version and both buckets, `workers` the buckets: otherwise a
    prod start has no sign-in, no files and no deletions, silently. JWT_SECRET is gone with JWT
    (ТЗ Ф4 §2.3 row 3, WP18.3)."""
    full: dict[str, object] = {"neo4j_password": "pw", **PROD_REST, "llm_prices": PRICES}
    for missing in sorted(required):
        monkeypatch.setenv("META_API_KEY", "" if missing == "META_API_KEY" else "meta-key-1")
        values = {k: ("" if k.upper() == missing else v) for k, v in full.items()}
        with pytest.raises(ValidationError, match=missing):
            _settings(env="prod", process_role=role, **values)
    # Nothing missing: the role starts, whatever it needs less than the others.
    monkeypatch.setenv("META_API_KEY", "meta-key-1")
    assert _settings(env="prod", process_role=role, **full).process_role == role
    # What the role does not need may be empty.
    monkeypatch.setenv("META_API_KEY", "")
    bare = {"neo4j_password": "pw", "llm_prices": PRICES}
    if "META_API_KEY" not in required:
        assert _settings(env="prod", process_role=role, **bare).process_role == role
