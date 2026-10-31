"""Settings: environment variables (Д-§3.2) and named thresholds (Т-§20, Т-§21.3).

Numbers are starting values from the docs, not truth (Т-§0.3); every threshold used by
code lives here under a name.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
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

    # Phase 3 (ТЗ Фазы 3 §6.2, §0.5): each stream edits only its own block below.

    # --- stream A ---

    # --- stream B ---
    # WP5.7 (Д-§7, R4): Meta API credentials; the old LLM_API_* names are read as fallbacks.
    meta_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("META_API_KEY", "LLM_API_KEY")
    )
    meta_api_base_url: str = Field(
        default="", validation_alias=AliasChoices("META_API_BASE_URL", "LLM_API_BASE_URL")
    )
    # WP5.1 (Т-§22.11): semaphores per process role and model; models outside the table get
    # llm_concurrency_default (Phase 3 decision, ТЗ §6.2).
    llm_concurrency: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "api": {"muse-wonder-1.0": 48, "muse-spark-1.3": 24},
            "workers": {"muse-wonder-1.0": 2, "muse-spark-1.3": 6},
        }
    )
    llm_concurrency_default: int = 4
    # WP5.7 (Т-§11.1): reasoning effort, output limit and timeout by role; roles without an
    # entry in llm_role_timeout_s use llm_timeout_sec. Teacher "high" is chosen per turn (WP6.5).
    llm_role_reasoning: dict[str, Literal["low", "high"]] = Field(
        default_factory=lambda: {
            "teacher": "low",
            "teacher_fallback": "low",
            "extractor": "high",
            "evaluator": "high",
            "diagnostic": "high",
            "task_gen": "high",
            "structurer": "high",
            "curator": "high",
            "artifacts": "high",
        }
    )
    llm_role_max_output_tokens: dict[str, int] = Field(
        default_factory=lambda: {
            "teacher": 8192,
            "teacher_fallback": 4096,
            "extractor": 4096,
            "evaluator": 4096,
            "diagnostic": 4096,
            "task_gen": 4096,
            "structurer": 4096,
            "curator": 4096,
            "artifacts": 4096,
        }
    )
    max_output_tokens_cap: int = 16384
    llm_role_timeout_s: dict[str, float] = Field(
        default_factory=lambda: {"teacher": 45.0, "teacher_fallback": 30.0}
    )
    # WP5.2 (Т-§11.2): retries on failure and on 429; base_ms and max_wait_s are Phase 3
    # decisions (ТЗ §6.2).
    llm_retry_attempts: int = 2
    llm_retry_base_ms: int = 500
    llm_rate_limit_retries: int = 3
    llm_rate_limit_base_ms: int = 1000
    llm_rate_limit_max_wait_s: float = 10.0
    # WP5.3 (Т-§22.11): pressure flag by 429 and by remaining-limit headers.
    pressure_default_ttl_s: int = 30
    pressure_remaining_share: float = 0.2
    pressure_header_ttl_s: int = 10
    # WP5.3, WP5.4 (Т-§21.3): how long the teacher waits for admission before teacher_fallback.
    teacher_admission_wait_ms: int = 3000
    # WP5.5 (Т-§21.3): circuit breaker per model.
    circuit_failures: int = 5
    circuit_open_s: float = 30.0
    # WP5.6 (Т-§11.4): roles outside the session token budget.
    session_budget_exempt_roles: tuple[str, ...] = ("extractor",)
    # WP6.3: SSE keepalive comment interval. Phase 3 decision (ТЗ §6.2).
    sse_keepalive_s: float = 15.0
    # WP13 (Т-§10.3): verbatim window and batch collapse into episode lines.
    conversation_verbatim_max_turns: int = 24
    conversation_collapse_turns: int = 12
    # WP14.1 (Т-§4.5): label limits per call.
    candidate_skills_k: int = 15
    label_max_topics: int = 8
    label_max_tasks: int = 5
    # WP6.2 (D-C-013): practice thresholds moved from quack/artifacts/params.py (ТЗ-П3).
    # Т-§7.4, ТЗ-П3 decision 1: independence penalty of hint level k is 0.25·k (k = 1..3).
    hint_penalties: tuple[float, float, float] = (0.25, 0.5, 0.75)
    # ТЗ-П3 §2.2 п.2: hint_level = min(max_hint_level, current_max_level + 1).
    max_hint_level: int = 3
    # ТЗ-П3 decision 8: tested_depth of skill_scores when the verdict comes without the LLM.
    compute_tested_depth: float = 0.5
    # Phase 3 decision D-B-009: `score` of `task.evaluated` for the verdict of evaluator@1
    # (`unclear` is "not checked", law 8: it falls back to the code verdict).
    evaluator_verdict_scores: dict[Literal["correct", "partial", "incorrect"], float] = Field(
        default_factory=lambda: {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
    )
    # ТЗ-П3 A6: difficulty of a task written by task_gen@1.
    default_target_difficulty: float = 0.5
    # Phase 3 decision D-B-010: level of understanding asked from task_gen@1 (3 = applies).
    task_gen_target_level: int = 3
    # WP15 (Т-§12.1, Т-§12.4): spoiler guard retries; numbers with |x| below this are not checked.
    guard_max_retries: int = 2
    spoiler_ignore_abs_below: int = 10

    # --- stream C ---
    # WP8.3: probe timeout of /health/ready and /healthz (was HEALTH_CHECK_TIMEOUT_SEC).
    health_check_timeout_s: float = 2.0
    # WP1.5 (Т-§19.1, ТЗ-П1, ТЗ-П2): fixed 60 s windows, keys ratelimit:{scope}:{key}.
    auth_requests_per_ip_per_min: int = 10
    learner_actions_per_min: int = 60
    turns_per_min: int = 20
    # WP1.6: moved from quack/domains (JWT_TTL_DAYS, CURATION_DEFER_MINUTES).
    jwt_ttl_days: int = 7
    curation_defer_min: int = 10
    # WP7: poll interval of GET /v1/learner/stream. Phase 3 decision (ТЗ §6.2).
    learner_stream_poll_ms: int = 2000
    # WP12 (Т-§7.4): evidence weight by tier, hint step penalty, hot window.
    evidence_tier_weights: dict[str, float] = Field(
        default_factory=lambda: {"T1": 1.0, "T2": 0.8, "T3": 0.4, "T4": 0.15}
    )
    hint_step_penalty: float = 0.25
    hot_window_min: int = 10

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
