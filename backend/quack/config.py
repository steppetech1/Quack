"""Settings: environment variables (Д-§3.2) and named thresholds (Т-§20, Т-§21.3).

Numbers are starting values from the docs, not truth (Т-§0.3); every threshold used by
code lives here under a name.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FoldParams(BaseModel):
    """Parameters of the pure folds of Phase B/C (Т-§7–9). Versioned together as rules@N."""

    model_config = {"frozen": True}

    # Т-§7.4: base weight by evidence tier T1..T4, hint step penalty and the hot window. Settings
    # copies them from `evidence_tier_weights`, `hint_step_penalty`, `hot_window_min`,
    # `hot_window_factor` (ТЗ Фазы 3 §6.2, WP12), so the fold and `evidence_weight` agree.
    tier_base_weights: tuple[float, float, float, float] = (1.0, 0.8, 0.4, 0.15)
    hint_step_penalty: float = 0.25
    hot_window_min: int = 10
    hot_window_factor: float = 0.5
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


class DiagnosticParams(BaseModel):
    """Thresholds of the diagnostic (ТЗ-П1 §2.3, Т-§3.3); values of Phase 2, moved here by
    WP1.6 from constants of `quack/domains/diagnostic.py`."""

    model_config = {"frozen": True}

    # The diagnostic asks at most this many questions.
    max_questions: int = 5
    # Prior of a tested skill: min(max_mastery, score * factor) with this confidence.
    prior_tested_max_mastery: float = 0.5
    prior_tested_factor: float = 0.5
    prior_tested_confidence: float = 0.5
    # Propagation of the result to neighbours: success at/above, failure below the threshold;
    # the effect decays by this factor per edge of distance.
    prior_propagated_success_threshold: float = 0.7
    prior_propagated_fail_threshold: float = 0.5
    prior_propagated_decay: float = 0.85
    # Priors for prerequisites (success) and for dependent skills (failure).
    prior_down_base_mastery: float = 0.4
    prior_down_base_confidence: float = 0.35
    prior_up_fail_mastery: float = 0.05
    prior_up_base_confidence: float = 0.4
    # Self-reported answers: a score at/above the threshold counts as correct.
    self_report_correct_threshold: float = 0.6
    no_self_report_score: float = 0.5
    # tested_depth of a diagnostic task without difficulty.
    default_tested_depth: float = 0.5


# Q6 (PHASE3_NOTES): tokens are HS256; RFC 7518 §3.2 requires a key at least as long as the
# hash output (256 bits). A property of the algorithm, not a tunable threshold.
HS256_MIN_SECRET_BYTES = 32

# Д-§7: the secrets each process role needs in prod (compose passes only these, ТЗ WP9.2).
_PROD_SECRETS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "api": ("JWT_SECRET", "META_API_KEY", "NEO4J_PASSWORD"),
    "workers": ("META_API_KEY", "NEO4J_PASSWORD"),
    "projector": ("NEO4J_PASSWORD",),
    "ops": (),
}


# --- stream B ---
type PriorityClass = Literal["P0", "P1", "P2", "P3"]

WONDER = "muse-wonder-1.0"
SPARK = "muse-spark-1.3"


class RoleConfig(BaseModel):
    """One row of the role table Т-§11.1 (ТЗ Ф4 WP10.1). `effort`, `max_output_tokens` and
    `prompt` are null for the roles that are not text calls (`segmenter`, `voice`,
    `illustrator`): Т-§11.1 gives them no effort row (D-B-101)."""

    model_config = ConfigDict(frozen=True)

    model: str
    effort: Literal["low", "high"] | None
    max_output_tokens: int | None
    priority_class: PriorityClass
    timeout_s: float
    prompt: str | None


def _role(
    model: str,
    effort: Literal["low", "high"] | None,
    max_output_tokens: int | None,
    priority_class: PriorityClass,
    prompt: str | None,
    timeout_s: float = 60.0,
) -> RoleConfig:
    return RoleConfig(
        model=model,
        effort=effort,
        max_output_tokens=max_output_tokens,
        priority_class=priority_class,
        timeout_s=timeout_s,
        prompt=prompt,
    )


def default_llm_roles() -> dict[str, RoleConfig]:
    """Т-§11.1 (models, effort, output limits), Т-§11.3 (priority classes), Т-§1.3 (model ids).
    Timeouts: teacher 45 s and teacher_fallback 30 s as in Phase 3, others `llm_timeout_sec`
    (60 s). Classes of roles with two uses (`recognizer`, `builder`) and of `voice` — D-B-101."""
    return {
        "teacher": _role(WONDER, "low", 8192, "P0", "teacher@2", 45.0),
        "teacher_fallback": _role(SPARK, "low", 4096, "P0", "teacher@2", 30.0),
        "extractor": _role(SPARK, "high", 4096, "P2", "extractor@3"),
        "verdict_free": _role(SPARK, "high", 4096, "P1", "verdict_free@1"),
        "verdict_proof": _role(WONDER, "high", 8192, "P1", "verdict_proof@1"),
        "second_opinion": _role(WONDER, "high", 8192, "P1", "second_opinion@1"),
        "canvas_diagnoser": _role(SPARK, "low", 4096, "P0", "canvas_diagnoser@1"),
        "recognizer": _role(SPARK, "low", 4096, "P0", "recognizer@1"),
        "segmenter": _role("sam-3.1", None, None, "P1", None),
        "voice": _role("muse-voice-transcribe-1.0", None, None, "P1", None),
        "analyst": _role(SPARK, "high", 4096, "P2", "analyst@1"),
        "analyst_deep": _role(WONDER, "high", 8192, "P2", "analyst@1"),
        "builder": _role(SPARK, "high", 4096, "P2", "builder@1"),
        "material_parser": _role(SPARK, "high", 4096, "P2", "material_parser@1"),
        "task_generator": _role(SPARK, "high", 4096, "P2", "task_generator@1"),
        "task_checker": _role(SPARK, "high", 4096, "P1", "task_checker@1"),
        "task_arbiter": _role(WONDER, "high", 8192, "P2", "task_arbiter@1"),
        "solution_space": _role(SPARK, "high", 4096, "P2", "solution_space@1"),
        "viz_builder": _role(WONDER, "high", 16384, "P2", "viz_builder@1"),
        "viz_checker": _role(SPARK, "low", 4096, "P2", "viz_checker@1"),
        "misconception_describer": _role(SPARK, "high", 4096, "P3", "misconception_describer@1"),
        "artifact_writer": _role(SPARK, "high", 4096, "P2", "artifact_writer@1"),
        "today_planner": _role(SPARK, "high", 4096, "P2", "today_planner@1"),
        "domain_classifier": _role(SPARK, "low", 4096, "P1", "domain_classifier@1"),
        "release_judge": _role(WONDER, "high", 8192, "P3", "release_judge@1"),
        "illustrator": _role("muse-image-1.0", None, None, "P2", None),
    }


class ModelPrice(BaseModel):
    """Т-§11.4: dollars per 1M tokens of one model (ТЗ Ф4 WP10.4)."""

    input: float
    cached_input: float
    output: float


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
    # §2.3 п.8 (Т-§1.3): the live model IDs; the role split of Phase 2 is kept.
    llm_default_model: str = "muse-spark-1.3"
    llm_role_models: dict[str, str] = Field(
        default_factory=lambda: {"teacher": "muse-wonder-1.0", "teacher_fallback": "muse-spark-1.3"}
    )

    # --- Compute client (spec §8.4) ---
    compute_timeout_sec: float = 5.0

    # Phase 3 (ТЗ Фазы 3 §6.2, §0.5): each stream edits only its own block below.

    # --- stream A ---
    # §4.3: set by the CLI before resources are created (WP8.1).
    process_role: Literal["api", "workers", "projector", "ops"] = "api"
    # Д-§6.1, Д-§9.1: build version, reported by /healthz and `ops status`.
    quack_version: str = "dev"
    # Д-§4.1, Д-§14.1: API process count and graceful shutdown (compose values: 2 / 30).
    api_workers: int = 1
    api_graceful_shutdown_s: int = 30
    # WP3.5 (Д-§6.1): how long `workers` waits for running jobs on SIGTERM.
    worker_shutdown_grace_s: float = 20.0
    # WP3.3: periodic jobs. Phase 3 decisions (ТЗ §6.2).
    scheduler_tick_s: float = 15.0
    session_sweep_interval_s: int = 60
    purge_deleted_interval_s: int = 600
    # WP3.4: a job deferred to the next UTC midnight gets up to this much random jitter.
    daily_budget_defer_jitter_s: int = 900
    # WP11.1 (Т-§22.4): dispatcher inside `projector`; batch size as projector_batch_max_events.
    dispatcher_concurrency: int = 8
    dispatcher_batch_size: int = 100
    dispatcher_restart_pause_s: float = 5.0

    # --- stream B ---
    # Т-§7.5: turns before the episode given to the extractor for understanding only (WP12).
    episode_context_turns: int = 3
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
    # GET /v1/sessions: session history page size (frontend sessions panel).
    session_list_default_limit: int = 50
    session_list_max_limit: int = 200
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
    # --- Phase 4, stream B (ТЗ Ф4 §6.7; starting values Т-§21.3, Т-§22.11) ---
    # WP10.1: the role table Т-§11.1, one row per role.
    llm_roles: dict[str, RoleConfig] = Field(default_factory=default_llm_roles)
    # WP10.4 (Т-§11.4, ТЗ Ф4 §2.3 строка 9): prices come from the owner; empty until then.
    llm_prices: dict[str, ModelPrice] = Field(default_factory=dict)
    # WP10.3 (Т-§11.5).
    tool_timeout_ms: int = 2000
    # WP10.3: model steps of one tool loop before it is cut. Phase 4 decision D-B-117.
    tool_loop_max_steps: int = 4
    # WP10.4 (Т-§11.4).
    session_agent_budget_usd: float = 0.5
    learner_daily_budget_usd: float = 3.0
    # WP13.4 (Т-§12.2).
    spoiler_holdback_chars: int = 120
    # WP14.5 (Т-§10.1).
    teacher_context_target_tokens: int = 30000
    teacher_context_max_tokens: int = 60000
    # WP14.2 (Т-§10.2).
    memory_skills_max: int = 12
    # Phase 4 decision D-B-102: threshold of "confident enough" gap type (Т-§10.2).
    gap_type_min_confidence: float = 0.6
    # WP14.4 (Т-§10.4): result limits of the memory tools. Phase 4 decision D-B-103.
    memory_tool_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "skill_history": 10,
            "similar_mistakes": 5,
            "past_solution": 2,
            "voice_at_line": 1,
            "search_materials": 5,
            "prerequisites": 10,
        }
    )
    # WP16.3 (Т-§4.3).
    candidate_k: int = 15
    # WP16.4 (Т-§4.9).
    domain_autolink_confidence: float = 0.8
    # WP11.2–11.3 (Т-§6.1, Т-§6.4, Т-§6.6).
    symbolic_inline_timeout_ms: int = 1500
    symbolic_background_timeout_ms: int = 10000
    symbolic_check_delay_ms: int = 3000
    min_sig_digits: int = 3
    numeric_points: int = 20
    # WP11.5 (Т-§6.6): numbers of the numeric check sent to compute in `/v1/math/*`; the
    # documents give none of them. Phase 4 decision D-B-124.
    math_precision_digits: int = 30
    math_sample_low: float = -10.0
    math_sample_high: float = 10.0
    math_positive_sample_min: float = 1e-3
    math_max_attempts_per_point: int = 10
    math_rel_tol: float = 1e-9
    math_abs_tol: float = 1e-12
    # WP11.6 (Т-§7.7).
    verdict_min_confidence: float = 0.7
    second_opinion_model: str = WONDER
    # WP13.9 (Т-§3.2).
    turn_context_sample_rate: float = 0.02
    # WP16.5 (Т-§22.10: one fix round; target value Т-§21.3 is 3).
    viz_max_fix_rounds: int = 1
    viz_static_base_url: str = ""
    # WP10.6 (ТЗ Ф4 §2.3 строка 20): UUIDv5 namespace of the learner pseudonym in traces.
    # Phase 4 decision D-B-105: a fixed namespace, never changed after the first trace.
    langsmith_pseudonym_ns: str = "6f1c2a9e-4b7d-5e3a-9c21-0d8e7f6a5b43"
    # WP15 (Т-§9.4): root-cause confidence category of extractor@3 → number. D-B-106.
    root_cause_confidence_map: dict[Literal["low", "medium", "high"], float] = Field(
        default_factory=lambda: {"low": 0.3, "medium": 0.6, "high": 0.9}
    )
    # WP10.3 (Т-§11.8 п.1): fallback two-step teacher turn, off until the live run says so.
    teacher_two_step: bool = False

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
    # WP1.6, audit A7: other thresholds of Phase 2 (domains, artifacts) — Phase 3 decision D-C-014.
    curation_min_evidence_for_promotion: int = 2
    search_min_query_len: int = 2
    search_default_limit: int = 20
    search_max_limit: int = 100
    diagnostic: DiagnosticParams = Field(default_factory=DiagnosticParams)
    # WP7: poll interval of GET /v1/learner/stream. Phase 3 decision (ТЗ §6.2).
    learner_stream_poll_ms: int = 2000
    # WP12 (Т-§7.4): evidence weight by tier, hint step penalty, hot window.
    evidence_tier_weights: dict[str, float] = Field(
        default_factory=lambda: {"T1": 1.0, "T2": 0.8, "T3": 0.4, "T4": 0.15}
    )
    hint_step_penalty: float = 0.25
    hot_window_min: int = 10
    # Т-§7.4 «окно горит» ×0.5; ТЗ §6.2 не называет параметр — Phase 3 decision D-C-019.
    hot_window_factor: float = 0.5

    # Phase 4 (ТЗ Фазы 4 §6.7), stream C. Secrets per role — Д-§7; values from Т-§21.3,
    # Т-§22.11, Д-§14.1. `None` — the value is not in the documents (Phase 4 decision D-C-105).
    # WP18 (Т-§19.1, Д-§3.1, Д-§7)
    auth_link_ttl_min: int = 15
    auth_link_per_email_per_hour: int = 5
    auth_link_per_ip_per_hour: int = 20
    auth_session_ttl_days: int = 7
    session_cookie_domain: str = "qvack.online"
    auth_success_redirect_url: str | None = None
    terms_version: str | None = None
    # Phase 4 decision D-C-104: names of sender and redirect URI are not in §6.7.
    auth_email_from: str = "login@qvack.online"
    google_oidc_redirect_url: str = "https://api.qvack.online/auth/google/callback"
    email_api_key: SecretStr = SecretStr("")
    google_oidc_client_id: SecretStr = SecretStr("")
    google_oidc_client_secret: SecretStr = SecretStr("")
    # WP19 (Т-§19.8, Д-§8.4)
    upload_max_mb: int = 50
    object_url_ttl_s: int = 300
    export_link_ttl_hours: int = 24
    s3_bucket_learners: str = "quack-learners"
    s3_endpoint: SecretStr = SecretStr("")
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    # WP25 seam S9 (Д-§8.5). Phase 4 decision D-C-103: the R2 endpoint is a config address.
    deletions_bucket: str = "quack-deletions"
    deletions_s3_endpoint: str = ""
    deletions_s3_access_key: SecretStr = SecretStr("")
    deletions_s3_secret_key: SecretStr = SecretStr("")
    # WP20 (Т-§10.5, ТЗ §2.3 п.17)
    materials_k: int = 5
    embedding_dim: int = 768
    modal_token_id: SecretStr = SecretStr("")
    modal_token_secret: SecretStr = SecretStr("")
    # WP21 (Т-§15, Т-§22.11)
    stroke_chunk_seconds: int = 10
    mark_min_confidence: float = 0.8
    error_grace_ms: int = 8000
    stuck_idle_s: int = 60
    max_unprompted_marks_per_task: int = 2
    min_intervention_gap_s: int = 45
    # Client parameters served in GET /v1/me (ТЗ §6.7); Т-§21.3 (§15.2, §15.3, §16.2).
    stroke_batch_ms: int = 300
    line_pause_ms: int = 1200
    capture_still_ms: int = 700
    # WP22 (Т-§16, Т-§22.6)
    pairing_ttl_s: int = 120
    sheet_line_match_threshold: float = 0.2
    new_sheet_match_share: float = 0.3
    # WP23 (Т-§17)
    cards_per_topic_max: int = 15
    cheatsheet_fit_rounds: int = 2
    # WP9 (Д-§2.1, Д-§4.2, Д-§8.1, Д-§9.3)
    disk_warn_pct: int = 75
    disk_crit_pct: int = 90
    team_daily_alert_usd: float = 60.0
    backup_interval_hours: int = 6
    backup_retention_days: int = 14
    cors_origins: list[str] = Field(
        default_factory=lambda: ["https://app.qvack.online", "https://preview.qvack.online"]
    )

    fold: FoldParams = Field(default_factory=FoldParams)

    @model_validator(mode="after")
    def _evidence_weights_into_fold(self) -> Settings:
        """WP12: the fold weighs evidence with the §6.2 parameters (one source of numbers)."""
        tiers = self.evidence_tier_weights
        self.fold = self.fold.model_copy(
            update={
                "tier_base_weights": (tiers["T1"], tiers["T2"], tiers["T3"], tiers["T4"]),
                "hint_step_penalty": self.hint_step_penalty,
                "hot_window_min": self.hot_window_min,
                "hot_window_factor": self.hot_window_factor,
            }
        )
        return self

    @model_validator(mode="after")
    def _require_secrets_in_prod(self) -> Settings:
        """Д-§7, ТЗ WP9.2: each process gets only its own secrets, prod requires exactly those of
        the role (`projector` has no META_API_KEY, `migrate` and other `ops` commands none)."""
        if self.env == "prod":
            secrets = {
                "JWT_SECRET": self.jwt_secret,
                "META_API_KEY": self.meta_api_key,
                "NEO4J_PASSWORD": self.neo4j_password,
            }
            missing = [
                name
                for name in _PROD_SECRETS_BY_ROLE[self.process_role]
                if not secrets[name].get_secret_value()
            ]
            if missing:
                raise ValueError(f"missing required secrets for ENV=prod: {', '.join(missing)}")
            jwt_bytes = len(self.jwt_secret.get_secret_value().encode())
            if "JWT_SECRET" in _PROD_SECRETS_BY_ROLE[self.process_role] and (
                jwt_bytes < HS256_MIN_SECRET_BYTES
            ):
                raise ValueError(
                    f"JWT_SECRET for ENV=prod must be at least {HS256_MIN_SECRET_BYTES} bytes"
                )
        return self

    @property
    def sqlalchemy_dsn(self) -> str:
        """POSTGRES_DSN rewritten for the SQLAlchemy asyncpg dialect."""
        dsn = self.postgres_dsn
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if dsn.startswith(prefix):
                return "postgresql+asyncpg://" + dsn[len(prefix) :]
        raise ValueError("POSTGRES_DSN must start with postgresql://")

    @property
    def llm_api_key(self) -> SecretStr:
        """R4: the old name of `meta_api_key`, kept for readers not yet moved to it."""
        return self.meta_api_key

    @property
    def llm_api_base_url(self) -> str:
        """R4: the old name of `meta_api_base_url`, kept for readers not yet moved to it."""
        return self.meta_api_base_url

    def model_for_role(self, role: str) -> str:
        return self.llm_role_models.get(role, self.llm_default_model)

    def secret_values(self) -> list[str]:
        """Values the logger must mask (Д-§7)."""
        values = [
            self.meta_api_key.get_secret_value(),
            self.jwt_secret.get_secret_value(),
            self.neo4j_password.get_secret_value(),
            *(
                secret.get_secret_value()
                for secret in (
                    self.email_api_key,
                    self.google_oidc_client_id,
                    self.google_oidc_client_secret,
                    self.s3_endpoint,
                    self.s3_access_key,
                    self.s3_secret_key,
                    self.deletions_s3_access_key,
                    self.deletions_s3_secret_key,
                    self.modal_token_id,
                    self.modal_token_secret,
                )
            ),
        ]
        return [v for v in values if len(v) >= 4]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
