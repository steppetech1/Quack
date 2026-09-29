"""Application settings loaded from environment variables."""

from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KnowledgeParams(BaseModel):
    """Parameter names, units and defaults from memory-architecture-quack.md §12."""

    alpha: float = 1.0
    beta: float = 0.5
    h0: float = 24
    h0_prior: float = 240
    h_min: float = 12
    h_max: float = 8760
    k_confidence: float = 3.0
    p_chat_cap: float = 0.8
    p_target_max: float = 0.95
    c_vis: float = 0.3
    c_close: float = 0.6
    c_cov: float = 0.6
    transfer_cross_exam: float = 0.6
    seen_template_factor: float = 0.8
    unmatched_incorrect_factor: float = 0.7
    prior_indirect_weight: float = 0.3
    misc_confirm_min_occ: int = 2
    misc_resolve_avoided: int = 3
    under_watch_days: int = 14
    trigger_min_n: int = 3
    trigger_min_share: float = 0.75
    root_window_days: int = 30
    root_boost: float = 1.5
    canon_merge: float = 0.90
    canon_adjudicate: float = 0.80
    min_templates_personal: int = 3
    observer_every_n: int = 6
    observer_min_confidence: float = 0.7
    # Верхняя граница окна наблюдателя: сколько необработанных сообщений
    # чата он берёт за один прогон (§8.1). Ниже — наблюдатель пропустит
    # часть разговора, выше — окно не влезает в бюджет контекста.
    observer_window_max: int = 30
    # Сколько вопросов ассистент подбора задаёт за один ход (product-logic
    # §3.2 «не больше двух вопросов»); постпроверка считает знаки «?».
    assistant_max_questions: int = 2
    # Доля заполненности анкеты, с которой подбор считается осмысленным
    # (`profiles.profile_readiness`).
    assistant_readiness_threshold: float = 0.6
    # Во сколько раз контекст сета шире окна темы при сборке контекста
    # репетитора (§9.3, открытый вопрос §13.6 — решён в пользу «те же слоты,
    # умноженные на множитель», а не отдельной сборки).
    context_set_multiplier: float = 1.5
    # После скольких подряд неверных ответов репетитор переходит к более
    # простому уровню / предлагает вернуться к предпосылке (§9.4).
    tutor_escalate_after_failures: int = 2
    diag_base: int = 8
    diag_reserve: int = 4
    diag_max: int = 12
    set_size: int = 3
    chat_window: int = 10
    context_budget_tokens: int = 3000

    check_size: int = 3
    review_window_days: int = 7
    consolidation_days: int = 7
    mock_set_min: int = 8
    mock_set_max: int = 12
    mock_topic_min: int = 5
    mock_topic_max: int = 7
    mock_misc_n: int = 3
    diag_reask_after: int = 3
    min_candidates: int = 3
    # --- Phase 4 (docs/tz/40-phase4-background-quack.md §1.6) ---
    # Ритм крона рекомендаций: product-logic §3.6 — «раз в один-два дня».
    recs_interval_h: int = 24
    # Веха ближе этого числа дней — срочная рекомендация.
    urgent_milestone_days: int = 5
    # Шаг и потолок перебора варианта «увеличить часы».
    pace_hours_step: int = 1
    pace_max_hours: int = 20
    # Шаг варианта «снизить цель» в баллах шкалы экзамена.
    pace_target_step_sat: int = 10
    pace_target_step_ent: int = 1
    # Ниже этой доли текущей цели вариант «снизить цель» не опускается.
    pace_min_target_share: float = 0.6
    # Сколько программ из верха `rank` уходит в мягкое соответствие.
    soft_match_max_candidates: int = 15
    soft_match_debounce_s: int = 30
    # Ключ `matching_priority_weights`, которым взвешивается мягкий фактор.
    soft_weight_key: str = "program"
    # Окно дневных агрегатов и календаря активности.
    aggregate_window_days: int = 14
    # В анкете часового пояса нет; аудитория — Казахстан.
    activity_tz: str = "Asia/Almaty"
    session_gap_min: int = 30
    active_day_min_events: int = 1
    summary_top_growth: int = 3
    # При недоступной модели отдавать сохранённую версию текста с пометкой.
    text_stale_ok_on_error: bool = True
    extract_max_urls_per_search: int = 5
    # Страница короче — «без данных», модель не вызывается.
    extract_min_page_chars: int = 800
    # Повторное извлечение по тому же URL не раньше этого срока.
    program_recheck_days: int = 14
    program_domain_denylist: list[str] = Field(
        default_factory=lambda: [
            "reddit.com",
            "quora.com",
            "youtube.com",
            "facebook.com",
            "instagram.com",
            "tiktok.com",
            "wikipedia.org",
        ]
    )
    # Сколько рекомендаций максимум в ленте (`urgent`/`high` не режутся).
    quack_feed_limit: int = 20
    # Сколько дней отказ по причине подавляет её повторное появление.
    declined_window_days: int = 90
    # Сколько программ получают текст «почему реалистично» за один GET.
    realism_texts_limit: int = 10

    matching_priority_weights: dict[str, int] = Field(
        default_factory=lambda: {
            "realism": 3,
            "cost": 2,
            "ranking": 1,
            "location": 1,
            "program": 2,
            "research": 1,
            "mobility": 1,
        }
    )


class Settings(BaseSettings):
    """Infrastructure and application configuration, without service initialization."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
        hide_input_in_errors=True,
    )

    ENV: Literal["local", "prod"] = "local"
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://quack:quack-local-postgres@127.0.0.1:5432/quack",
        repr=False,
    )
    NEO4J_URI: str = "bolt://127.0.0.1:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: SecretStr = SecretStr("quack-local-neo4j")
    REDIS_URL: str = Field(default="redis://127.0.0.1:6379/0", repr=False)

    JWT_SECRET: SecretStr = SecretStr("quack-local-only-development-secret")
    JWT_TTL_DAYS: int = 30
    # Browser origins allowed to call the API with the session cookie. Empty in
    # prod when Caddy serves frontend and API from one origin.
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
    ]

    LLM_BASE_URL: str = ""
    LLM_API_KEY: SecretStr = SecretStr("")
    MODEL_CHAT: str = ""
    MODEL_BULK: str = ""
    # response_format = json_schema (Together, OpenAI); json_object = schema in the
    # prompt (DeepSeek own API has no json_schema); tool = a forced function call.
    LLM_STRUCTURED_MODE: Literal["response_format", "json_object", "tool"] = (
        "response_format"
    )
    LLM_STRICT_SCHEMA: bool = False
    LLM_REASONING_CHAT: str | None = "low"
    LLM_REASONING_BULK: str | None = "high"
    # DeepSeek's own API: `thinking` goes in extra_body. Unset = do not send it.
    LLM_THINKING_CHAT: Literal["enabled", "disabled"] | None = None
    LLM_THINKING_BULK: Literal["enabled", "disabled"] | None = None
    LLM_TIMEOUT_CHAT_S: float = 30
    LLM_TIMEOUT_BULK_S: float = 90
    # Local starting limits; configure these for the selected provider.
    LLM_RPM_CHAT: int = 10
    LLM_RPM_BULK: int = 10
    LLM_FORCE_DOWN: bool = False

    # Таймауты задач ARQ, в секундах. Общий job_timeout очереди — потолок для
    # коротких задач; задачи, которые ходят в LLM, объявляют свой собственный
    # (наблюдатель на слоте bulk не укладывался в 30 с очереди interactive).
    JOB_TIMEOUT_DEFAULT_S: float = 30
    JOB_TIMEOUT_MAX_S: float = 300

    # Фаза 3 (docs/tz/phase3-agents.md §2.5). Наблюдатель и канонизация
    # ходят в LLM на своём слоте; таймаут наблюдателя не меньше
    # LLM_TIMEOUT_BULK_S + 10, иначе ARQ снимет job раньше провайдера.
    OBSERVER_SLOT: Literal["chat", "bulk"] = "bulk"
    CTX_CACHE_TTL_S: int = 3600
    CHAT_LOCK_TTL_S: int = 120
    OBSERVER_JOB_TIMEOUT_S: int = 100
    CANON_JOB_TIMEOUT_S: int = 30

    @property
    def job_timeout_llm_chat_s(self) -> float:
        """Задача с одним вызовом LLM на слоте chat плюс запас на I/O."""
        return self.LLM_TIMEOUT_CHAT_S + 15

    @property
    def job_timeout_llm_bulk_s(self) -> float:
        """Задача с одним вызовом LLM на слоте bulk плюс запас на I/O."""
        return self.LLM_TIMEOUT_BULK_S + 15

    @field_validator(
        "LLM_REASONING_CHAT",
        "LLM_REASONING_BULK",
        "LLM_THINKING_CHAT",
        "LLM_THINKING_BULK",
        mode="before",
    )
    @classmethod
    def _empty_reasoning_means_unset(cls, value: object) -> object:
        """An empty env value means "don't pass reasoning_effort at all",
        not the literal string "" (docs/decisions/llm-provider.md, TTFT
        matrix) — ``LLMClient`` already treats ``None`` this way."""
        if value == "":
            return None
        return value

    # Фаза 4 (§1.6, §10.3–§10.5).
    BULK_MAX_JOBS: int = 2
    SEARCH_RPM: int = 5
    SEARCH_MONTHLY_CAP: int = 800
    LLM_RETRY_DEFER_S: int = 120
    # Доля лимита чата, после которой фоновый слот уступает живому чату.
    LLM_BULK_YIELD_SHARE: float = 0.7
    # ARQ хранит результат job этот срок; с тем же `job_id` внутри окна
    # задача не поставится, поэтому окно короткое (§1.3).
    JOB_KEEP_RESULT_S: int = 60

    # --- Фаза 5 (§13.3, D09). Технические значения под один VPS, не SLA. ---
    # Сколько намерений разбирает один прогон `outbox_replay`.
    OUTBOX_REPLAY_BATCH: int = 100
    # Размер одной порции событий восстановления графа и потолок на задачу:
    # `recover_graph_events` живёт 90 с, порция должна укладываться с запасом.
    RECOVERY_BATCH: int = 25
    RECOVERY_MAX_PER_JOB: int = 200
    # Догон графа при старте воркера (bulk). Выключается в тестах.
    RECOVERY_SWEEP_ON_STARTUP: bool = True

    TAVILY_API_KEY: SecretStr = SecretStr("")
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    EMBEDDING_DIM: int = Field(default=384, gt=0)
    LOG_LEVEL: str = "INFO"
    GIT_SHA: str = "dev"
    KNOWLEDGE: KnowledgeParams = Field(default_factory=KnowledgeParams)

    @property
    def knowledge(self) -> KnowledgeParams:
        """Phase 2 access to the existing Phase 1 knowledge settings."""
        return self.KNOWLEDGE

    @model_validator(mode="after")
    def validate_prod_jwt_secret(self) -> Self:
        if self.ENV == "prod":
            secret_bytes = self.JWT_SECRET.get_secret_value().encode("utf-8")
            if len(secret_bytes) < 32:
                raise ValueError("JWT_SECRET must be at least 32 bytes in prod")
        return self


settings = Settings()
