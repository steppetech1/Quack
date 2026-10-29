"""Settings: environment variables (Д-§3.2) and named thresholds (Т-§20, Т-§21.3).

Numbers are starting values from the docs, not truth (Т-§0.3); every threshold used by
code lives here under a name.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FoldParams(BaseModel):
    """Parameters of the pure folds of Phase B/C (Т-§7–9). Versioned together as rules@N."""

    model_config = {"frozen": True}

    # Т-§7.4: base weight by evidence tier T1..T4.
    tier_base_weights: tuple[float, float, float, float] = (1.0, 0.8, 0.4, 0.15)
    # Т-§8.2: w_eff = w * λ^k, k = number of later gated evidence items.
    level_recency_factor: float = 0.6
    # Т-§8.2: proof_min_weight / fail_min_weight.
    proof_min_weight: float = 0.8
    fail_min_weight: float = 0.8
    # Weight multiplier for tested depth: floor + (1 - floor) * tested_depth.
    depth_weight_floor: float = 0.6
    # confidence = W / (W + confidence_half_weight), W = sum of undecayed gated weights.
    confidence_half_weight: float = 0.5
    # A score at or above this value counts as a success.
    success_score_threshold: float = 0.5
    # Mastered requires pMastery >= this and at least `min_distinct_families` families.
    mastered_min_p_mastery: float = 0.7
    min_distinct_families: int = 2
    # Ceiling while distinctFamilies < min_distinct_families (Т-§7.3, "only in pairs").
    single_family_mastery_ceiling: float = 0.6
    # Ceilings without gated practice (Т-§7.2/§7.3: exposures and T3/T4 never open a level).
    exposure_mastery_gain: float = 0.1
    exposure_mastery_ceiling: float = 0.3
    ungated_evidence_gain: float = 0.25
    ungated_mastery_ceiling: float = 0.4
    prior_mastery_ceiling: float = 0.5
    # Retention (Т-§8.3): delayed threshold, target retention, stability dynamics.
    delay_threshold_hours: float = 20.0
    target_retention: float = 0.9
    initial_stability_days: float = 1.0
    stability_success_multiplier: float = 2.5
    stability_lapse_factor: float = 0.5
    min_stability_days: float = 0.5
    # Misconceptions (Т-§9.2).
    fix_min_resists: int = 2
    watch_days: float = 3.0
    resist_min_score: float = 0.8
    resist_min_independence: float = 0.8
    # Topic aggregates and displayState (Т-§9.2 of the spec, Т-§22.2).
    hub_weight_factor: float = 0.5
    topic_solid_mastery: float = 0.7
    topic_solid_coverage: float = 0.8
    prereq_unlock_mastery: float = 0.6


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Infrastructure (Д-§3.2) ---
    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    postgres_dsn: str = "postgresql://quack:quack@localhost:5432/quack"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("neo4j")
    redis_url: str = "redis://localhost:6379/0"
    compute_url: str = "http://localhost:8001"
    llm_api_key: SecretStr = SecretStr("")
    llm_api_base_url: str = ""
    jwt_secret: SecretStr = SecretStr("")
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Constants (Т-§20 per spec §1.3) ---
    projector_lag_timeout_ms: int = 2000
    projector_batch_max_events: int = 100
    rebuild_max_tx_ops: int = 5000
    active_window_turns: int = 12
    checkpoint_every_turns: int = 6
    session_inactivity_timeout_min: int = 30
    heartbeat_interval_sec: int = 30
    heartbeat_ttl_sec: int = 90
    turn_lock_ttl_sec: int = 60
    llm_semaphore_limit: int = 32
    daily_token_limit_per_learner: int = 150_000
    session_token_limit: int = 40_000

    # --- Projector and reads (Т-§2.4, Т-§2.6, Т-§1.7) ---
    snapshot_read_retries: int = 3
    consumer_poll_ms: int = 500

    # --- Job queue (spec §7, Т-§13) ---
    job_heartbeat_interval_sec: int = 15
    job_stale_after_sec: int = 60
    job_max_attempts: int = 5
    job_backoff_base_sec: int = 5
    job_backoff_max_sec: int = 300
    worker_concurrency: int = 8

    # --- LLM gateway (Т-§11) ---
    llm_timeout_sec: float = 60.0
    llm_default_model: str = "muse-spark"
    llm_role_models: dict[str, str] = Field(
        default_factory=lambda: {"teacher": "muse-wonder", "teacher_fallback": "muse-spark"}
    )

    # --- Compute client (spec §8.4) ---
    compute_timeout_sec: float = 5.0

    fold: FoldParams = Field(default_factory=FoldParams)

    @model_validator(mode="after")
    def _require_secrets_in_prod(self) -> Settings:
        if self.env == "prod":
            missing = [
                name
                for name, value in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("LLM_API_KEY", self.llm_api_key),
                    ("NEO4J_PASSWORD", self.neo4j_password),
                )
                if not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"missing required secrets for ENV=prod: {', '.join(missing)}")
        return self

    @property
    def sqlalchemy_dsn(self) -> str:
        """POSTGRES_DSN rewritten for the SQLAlchemy asyncpg dialect."""
        dsn = self.postgres_dsn
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if dsn.startswith(prefix):
                return "postgresql+asyncpg://" + dsn[len(prefix) :]
        raise ValueError("POSTGRES_DSN must start with postgresql://")

    def model_for_role(self, role: str) -> str:
        return self.llm_role_models.get(role, self.llm_default_model)

    def secret_values(self) -> list[str]:
        """Values the logger must mask (Д-§7)."""
        values = [
            self.llm_api_key.get_secret_value(),
            self.jwt_secret.get_secret_value(),
            self.neo4j_password.get_secret_value(),
        ]
        return [v for v in values if len(v) >= 4]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
