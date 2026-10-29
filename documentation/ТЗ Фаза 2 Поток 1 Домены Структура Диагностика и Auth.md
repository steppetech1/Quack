# Техническое задание — Фаза 2, Поток 1: Auth, Домены, Структура, Диагностика, Карта знаний и Курация

Этот документ определяет полный объём работ, контракты, алгоритмические решения и тесты для **Разработчика 1 (Поток 1)** во второй фазе реализации бэкенда Quack.

---

## 0. Границы ответственности и защита от Merge-конфликтов `[КОНТРАКТ]`

### 0.1. Владение файлами (Zero Merge Conflicts)
- **Твоя эксклюзивная зона записи:**
  - `backend/quack/domains/**` — весь код модуля (аутентификация, профиль ученика, домены, онбординг и редактирование структуры, адаптивная диагностика, чтение карты знаний для UI, фоновая курация).
  - `backend/quack/prompts/structurer/v1.md`, `backend/quack/prompts/diagnostic/v1.md`, `backend/quack/prompts/curator/v1.md` — шаблоны промптов твоего потока + соответствующие 3 ключа (`structurer@1`, `diagnostic@1`, `curator@1`) в `backend/quack/prompts/manifest.json`.
  - `backend/tests/domains/**` — все тесты твоего потока.
- **Строгий запрет на изменение чужих и базовых файлов:**
  - **НЕ изменяй** файлы ядра Фазы 1 (`quack/config.py`, `quack/logging.py`, `quack/db/*`, `quack/events/*`, `quack/graph/*`, `quack/jobs/*`, `quack/llm/*`, `quack/api/*`, `quack/ops/*`, `quack/cli.py`, `migrations/*`, `tests/test_*.py`).
  - **НЕ импортируй** ничего из `quack/session/` (Поток 2) и `quack/artifacts/` (Поток 3).
  - Подключение твоего `router: APIRouter` из `quack/domains/router.py` в `quack/api/app.py` в параллельной ветке **не делается** (это 2 строчки при финальной склейке веток). В своих тестах монтируй роутер локально в фикстуре: `app = create_app(settings, connect=...); app.include_router(domains_router)`.

### 0.2. Первоисточники и готовые примитивы Фазы 1
Перед реализацией изучи:
- `documentation/Спецификация Фаз 1-2 контракты и схемы.md` (`§2–§8`) и `backend/PHASE1_NOTES.md`.
- Разделы основного техдока (`Т-§`): `§3.1–3.3` (интервью, генерация структуры, DAG-валидация, диагностика), `§4` (узлы и рёбра графа), `§7–9` (свёртки навыков и тем), `§10` (фоновая курация), `§16` (управление доменами, структурой и аккаунтом), `§17.1` (деградация без LLM), `§19` (аутентификация и rate-limit), `§22.2` (хакатонный срез).
- Используй готовые фасады Фазы 1:
  - Запись событий: `append_events` из `quack/events/append.py` (автоматически синхронно обновляет `core.domains`).
  - Консистентное чтение графа: `read_consistent_slice` из `quack/graph/slice.py` + `effective_mastery`, `skill_is_rusty` из `quack/graph/folds.py` и `recompute_topic_display` из `quack/graph/topic_display.py`.
  - Постановка и обработка фоновых задач: `enqueue_job`, `kinds.graph_curate` из `quack/jobs/` и декоратор `@register_job_handler("graph.curate")` из `quack/jobs/runner.py`.
  - Вызовы моделей: `LLMGateway.generate_structured` из `quack/llm/gateway.py`.

---

## 1. Архитектура пакета `quack/domains/` `[ОРИЕНТИР]`

Рекомендуемая декомпозиция внутри `backend/quack/domains/`:
- `auth.py` — хеширование паролей (`pwdlib[argon2]`), выпуск/проверка JWT (`pyjwt`), FastAPI-зависимость `get_current_learner_id`, Redis rate-limiter для `/v1/auth/*`.
- `learner.py` — чтение/обновление профиля и `preferences`, двухфазное удаление аккаунта.
- `service.py` — создание, список и архивация доменов (`core.domains` + `append_events`).
- `structure.py` — проведение интервью, вызов `structurer@1`, детерминированная проверка и разрыв циклов в DAG тем/навыков, подтверждение и ручное редактирование структуры.
- `diagnostic.py` — адаптивный выбор диагностических вопросов по DAG, запись ответов и распространение априорных оценок (`inferred_priors`) вверх и вниз по графу предпосылок.
- `graph_view.py` — сборка карты знаний домена и карточки темы для UI через `read_consistent_slice` с пересчётом `effective_mastery` и `displayState` (`rusty`) на текущий момент времени.
- `curation.py` — обработчик фоновой задачи `graph.curate` (дедупликация и продвижение provisional навыков, слияние дубликатов).
- `schemas.py` — Pydantic-модели HTTP-запросов, ответов и контрактов Structured Outputs для промптов.
- `router.py` — единый `router = APIRouter(prefix="/v1")` со всеми эндпоинтами Потока 1.

---

## 2. Инженерные решения и контракты модулей `[КОНТРАКТ]`

### 2.1. Аутентификация, JWT, Rate-Limit и Жизненный цикл аккаунта (`Т-§16.3`, `Т-§19`, `Т-§22.2`)

1. **Пароли и `auth.users`:**
   - Email нормализуется (`strip().lower()`). Повторная регистрация существующего email возвращает `409 IDEMPOTENCY_CONFLICT`.
   - Пароль(минимум 8 символов) хешируется через `pwdlib` с `Argon2Hasher`.
   - При регистрации в одной транзакции Postgres создаётся строка в `auth.users` и вызывается `append_events` с событием `learner.registered(email, display_name, preferences)` (`actor="learner"`, `idempotency_key=f"register:{user_id}"`).
2. **Единый контракт JWT (используется всеми 3 потоками):**
   - Заголовок: `Authorization: Bearer <token>`.
   - Алгоритм: `HS256`. Ключ подписи: `settings.jwt_secret.get_secret_value()` (если пуст в `env in ("dev", "test")` — используется константа `"quack-dev-jwt-secret"`).
   - Обязательные поля payload: `{"sub": str(learner_id), "iat": <unix_sec>, "exp": <unix_sec>}` (срок жизни токена — 7 суток).
   - Зависимость `get_current_learner_id`: декодирует токен, проверяет в `auth.users`, что пользователь существует и `deleted_at IS NULL` (а также нет записи в `ops.deletion_tombstones`). При любой ошибке — `ApiError("UNAUTHORIZED", ..., status_code=401)`.
3. **Rate Limiting (`/v1/auth/register`, `/v1/auth/login`):**
   - Ключ Redis из `quack/db/redis.py`: `ratelimit_key("auth", client_ip)` (`TTL = 60s`), лимит — `10` запросов в минуту с одного IP.
   - При превышении — `ApiError("RATE_LIMITED", ..., status_code=429)`.
   - Если Redis недоступен (`Т-§17.3`), ошибка Redis логируется, и запрос пропускается без падения (fail-open).
4. **Двухфазное удаление аккаунта (`DELETE /v1/learner`):**
   - В одной транзакции Postgres:
     1) ставит `deleted_at = now()` в `auth.users`,
     2) отменяет активные задачи ученика в `ops.jobs` (`UPDATE ops.jobs SET status = 'cancelled' WHERE learner_id = :l AND status = 'queued'`),
     3) вставляет запись в `ops.deletion_tombstones (learner_id, requested_at, status = 'pending_purge') ON CONFLICT (learner_id) DO NOTHING`.
   - Физическая зачистка данных в Postgres и Neo4j уже реализована в Фазе 1 (`ops purge-deleted`).

### 2.2. Домены, Онбординг структуры и DAG-валидация (`Т-§3.1`, `Т-§3.2`, `Т-§16.1`, `Т-§16.2`)

1. **Создание домена (`POST /v1/domains`):**
   - Генерирует `slug` из `name` (латиница/цифры через дефис; если у ученика такой `slug` уже есть в `core.domains`, возвращает `409 IDEMPOTENCY_CONFLICT`).
   - Вызывает `append_events` с `domain.created(domain_id, name, slug, has_materials=False)`.
2. **Интервью и генерация структуры (`POST /v1/domains/{domain_id}/structure/propose`):**
   - Принимает ответы интервью (`goals`, `background`, `focus_areas`, `idempotency_key`).
   - Если домен ещё в статусе `'onboarding'`, записывает событие `structure.interview_completed`.
   - Вызывает `LLMGateway.generate_structured(role="structurer", prompt_version=1, ...)` для получения черновика тем, навыков, предпосылок и типичных заблуждений.
   - **Детерминированная санитизация и проверка DAG перед записью события (`Т-§3.2`):**
     - Каждой теме, навыку и заблуждению из ответа LLM присваивается детерминированный `UUIDv5` от `(domain_id, structure_version, kind, index)` (чтобы повтор при сбое после `llm_outbox` порождал те же ID).
     - Удаляются висячие ссылки (навык ссылается на несуществующую тему, ребро ссылается на несуществующий узел) и петли (`from == to`).
     - Проверяется ацикличность графа тем (`PREREQ_OF`) и графа навыков (`DEPENDS_ON`) алгоритмом поиска циклов (DFS с цветами / топологической сортировкой). Если модель сгенерировала цикл, **обратное ребро, замыкающее цикл, детерминированно удаляется** (с логированием `warning`), чтобы в `structure.proposed` всегда попадал строгий DAG.
     - Направление `SkillDepEdge` строго соблюдает контракт Фазы 1: `SkillDepEdge(skill_id=dependent, depends_on_skill_id=prerequisite)` соответствует ребру `(dependent)-[:DEPENDS_ON]->(prerequisite)`.
   - Записывает `structure.proposed(domain_id, structure_version=1, topics, skills, prereqs, skill_deps, misconceptions)`.
   - **Режим деградации (`LLMUnavailableError` / `LLMBudgetExceededError`, `Т-§17.1`):**
     - Если LLM недоступна, создаёт минимальный валидный скелет из `focus_areas` (по одной теме и одному базовому навыку на каждый элемент `focus_areas`, линейно связанных `PREREQ_OF`) и возвращает ответ с флагом `degraded: true`, чтобы ученик мог отредактировать структуру вручную и начать учиться.
3. **Подтверждение и ручное редактирование структуры (`POST .../confirm`, `PATCH .../structure`, `PATCH /v1/topics/{topic_id}/status`):**
   - `confirm`: проверяет, что в домене есть предложенная структура, и эмитит `structure.confirmed(domain_id, structure_version)`.
   - `PATCH .../structure`: перед записью `structure.edited` загружает текущие рёбра `PREREQ_OF` домена из Neo4j (через `read_consistent_slice`), применяет `added_prereqs` / `removed_prereqs` в памяти и проверяет отсутствие циклов. Если добавление ребра создаёт цикл — отклоняет запрос с `422 VALIDATION_ERROR`.

### 2.3. Адаптивная диагностика и распространение априоров по DAG (`Т-§3.3`)

1. **Старт диагностики (`POST /v1/domains/{domain_id}/diagnostic/start`):**
   - Читает DAG тем и навыков домена из Neo4j (`read_consistent_slice`).
   - Выбирает стартовые темы-кандидаты: узлы ближе к середине топологической сортировки DAG с максимальным `hubScore` (навыки, от которых зависит больше всего других навыков).
   - Генерирует (через `diagnostic@1` или берёт из `core.task_bank`) первый диагностический вопрос (`task.generated`), эмитит `diagnostic.started(domain_id, session_id, candidate_topic_ids)` и возвращает вопрос.
2. **Ответ на вопрос диагностики (`POST /v1/domains/{domain_id}/diagnostic/answer`):**
   - Оценивает ответ (для `choice` — детерминированно кодом, для `open` — через `diagnostic@1` с fallback на самооценку `confidence_self_report`).
   - Эмитит `diagnostic.question_answered(...)`.
   - Выбирает следующий вопрос адаптивно (до `max_questions = 5`):
     - при верном ответе (`is_correct = True`) двигается **вверх по DAG** (к зависимым темам, для которых текущая является предпосылкой);
     - при неверном ответе (`is_correct = False`) двигается **вниз по DAG** (к темам-предпосылкам `PREREQ_OF`).
3. **Завершение диагностики и расчёт `inferred_priors` (`POST /v1/domains/{domain_id}/diagnostic/finish`):**
   - По результатам ответов строит список `TopicPriorItem` для всех навыков домена:
     - Напрямую проверенные навыки получают `source = "tested"`, `prior_mastery = min(0.5, score * 0.5)`, `confidence = 0.5`.
     - **Распространение вниз по `PREREQ_OF` / `DEPENDS_ON` (к предпосылкам от успешного навыка):** если сложный навык сдан успешно (`score >= 0.7`), его непроверенные предпосылки получают `source = "propagated"`, `prior_mastery = 0.4 * (0.85 ** distance)`, `confidence = 0.35 * (0.85 ** distance)`.
     - **Распространение вверх по `PREREQ_OF` / `DEPENDS_ON` (к следствиям от проваленного навыка):** если базовый навык провален (`score < 0.5`), зависящие от него непроверенные навыки получают `source = "propagated"`, `prior_mastery = 0.05`, `confidence = 0.4 * (0.85 ** distance)`.
   - Эмитит `diagnostic.completed(domain_id, session_id, tested_topic_ids, inferred_priors)`.

### 2.4. Чтение карты знаний для UI (`GET /v1/domains/{domain_id}/graph`, `GET /v1/topics/{topic_id}`) (`Т-§8.6`, `Т-§22.2`)

1. **Консистентное чтение:**
   - Вызывает `read_consistent_slice(learner_id, min_seq=None, loader_fn=..., deps=...)`.
   - Возвращает в ответе `applied_seq` и `lag_degraded: bool` (для отображения плашки синхронизации в UI).
2. **Обязательный пересчёт `effective_mastery` и `rusty` на момент чтения (`now_ms`):**
   - Поскольку проектор не читает системное время, сохранённый в Neo4j `Topic.displayState` отражает момент последнего события.
   - При формировании ответа API вычисляется `now_ms = to_ms(datetime.now(UTC))`:
     - Для каждого навыка темы считается `effective_mastery(p_mastery, stability, last_reviewed_at, now_ms)` и `is_rusty = skill_is_rusty(fold, now_ms)`.
     - Для каждой темы вызывается чистая функция `recompute_topic_display(skills, dependents_count, prereqs, as_of_ms=now_ms, params=settings.fold)`, чтобы тема со статусом `solid`, у которой наступил срок повторения (`0 < nearestReviewAt < now_ms`), отдавалась на фронтенд как `display_state = "rusty"`.
   - Узлы со статусом `'merged'` и `'archived'` (если не запрошен фильтр `include_archived=true`) исключаются из выдачи карты.

### 2.5. Фоновая курация графа: воркер `graph.curate` (`Т-§10`)

Обработчик регистрируется через `@register_job_handler("graph.curate")`:
1. **Защита активного урока (`Т-§10`, `Спецификация §6`):**
   - Проверяет ключ Redis `presence_key(learner_id)`. Если ключ существует (ученик сейчас на уроке), воркер **не выполняет слияний**, а переоткладывает задачу в `ops.jobs` (`run_after = now() + interval '10 minutes'`) и завершает текущий запуск.
2. **Логика курации:**
   - Читает из Neo4j все навыки (`status in ('provisional', 'active')`), темы и заблуждения домена `domain_id`.
   - **Детерминированный проход (работает даже без LLM):**
     - Если provisional навык имеет `evidenceCount >= 2` и не дублирует существующий `active` навык — эмитит `skill.promoted`.
     - Если два навыка или заблуждения имеют совпадающий или тривиально эквивалентный `normName` — эмитит `skill.merged` / `misconception.merged` (оставляя `active` узел как `target_id`, а `provisional` как `source_id`).
   - **Модельный проход (`curator@1`):**
     - Если есть нетривиальные provisional навыки/заблуждения, вызывает `LLMGateway.generate_structured(role="curator", prompt_version=1, ...)` со списком узлов домена.
     - Валидирует предложенные слияния (`source_id != target_id`, оба узла существуют в домене, слияние тем не создаёт цикл в `PREREQ_OF`) и записывает события `skill.promoted`, `skill.merged`, `topic.merged`, `misconception.merged` через `append_events(actor="curator@1")`.

---

## 3. Таблица HTTP-эндпоинтов Потока 1 `[КОНТРАКТ]`

| Метод и путь | Auth | Вход (Body / Query) | Выход (`200` / `201`) | События `core.events` и задачи `ops.jobs` |
| :--- | :---: | :--- | :--- | :--- |
| `POST /v1/auth/register` | Нет (Rate-limit) | `email`, `password`, `display_name: str \| None = None`, `preferences: dict = {}` | `token: str`, `learner_id: UUID`, `email: str`, `display_name: str \| None`, `preferences: dict` | `learner.registered` |
| `POST /v1/auth/login` | Нет (Rate-limit) | `email`, `password` | `token: str`, `learner_id: UUID`, `email: str`, `display_name: str \| None`, `preferences: dict` | — |
| `GET /v1/auth/me` | Bearer | — | `learner_id: UUID`, `email: str`, `display_name: str \| None`, `preferences: dict`, `created_at: datetime` | — |
| `PATCH /v1/learner/preferences` | Bearer | `preferences: dict` (`pace`, `style`, `daily_goal_min`), `idempotency_key: str` | `preferences: dict`, `seq: int` | `learner.preferences_updated` |
| `DELETE /v1/learner` | Bearer | — | `status: "pending_purge"`, `requested_at: datetime` | Запись в `ops.deletion_tombstones`, отмена queued `ops.jobs` |
| `POST /v1/domains` | Bearer | `name: str`, `slug: str \| None = None`, `idempotency_key: str` | `domain_id: UUID`, `name: str`, `slug: str`, `status: str`, `seq: int` | `domain.created` |
| `GET /v1/domains` | Bearer | `include_archived: bool = False` | `domains: list[DomainSummary]` (из `core.domains`) | — |
| `GET /v1/domains/{domain_id}` | Bearer | — | `DomainSummary` | — |
| `POST /v1/domains/{domain_id}/archive` | Bearer | `reason: str \| None = None`, `idempotency_key: str` | `domain_id: UUID`, `status: "archived"`, `seq: int` | `domain.archived` |
| `POST /v1/domains/{domain_id}/structure/propose` | Bearer | `goals: str`, `background: str`, `focus_areas: list[str]`, `idempotency_key: str` | `domain_id: UUID`, `structure_version: int`, `topics: list`, `skills: list`, `prereqs: list`, `skill_deps: list`, `misconceptions: list`, `degraded: bool` | `structure.interview_completed`, `structure.proposed` |
| `POST /v1/domains/{domain_id}/structure/confirm` | Bearer | `structure_version: int`, `idempotency_key: str` | `domain_id: UUID`, `status: "active"`, `active_structure_version: int`, `seq: int` | `structure.confirmed` |
| `PATCH /v1/domains/{domain_id}/structure` | Bearer | Поля `StructureEdited` (кроме `domain_id`, `structure_version`) + `idempotency_key: str` | `domain_id: UUID`, `active_structure_version: int`, `seq: int` | `structure.edited` |
| `PATCH /v1/domains/{domain_id}/topics/{topic_id}/status` | Bearer | `status: Literal['active','skipped','archived']`, `reason: str \| None = None`, `idempotency_key: str` | `topic_id: UUID`, `status: str`, `seq: int` | `topic.status_changed` |
| `POST /v1/domains/{domain_id}/diagnostic/start` | Bearer | `idempotency_key: str` | `session_id: UUID`, `candidate_topic_ids: list[UUID]`, `next_question: DiagnosticQuestion` | `task.generated`, `diagnostic.started` |
| `POST /v1/domains/{domain_id}/diagnostic/answer` | Bearer | `session_id: UUID`, `task_id: UUID`, `answer: str`, `confidence_self_report: float \| None = None`, `response_time_sec: float \| None = None`, `idempotency_key: str` | `is_correct: bool`, `score: float`, `next_question: DiagnosticQuestion \| None`, `should_finish: bool` | `diagnostic.question_answered` (и `task.generated`, если есть следующий вопрос) |
| `POST /v1/domains/{domain_id}/diagnostic/finish` | Bearer | `session_id: UUID`, `idempotency_key: str` | `tested_topic_ids: list[UUID]`, `inferred_priors: list[TopicPriorItem]`, `seq: int` | `diagnostic.completed` |
| `GET /v1/domains/{domain_id}/graph` | Bearer | `include_archived: bool = False` | `domain_id: UUID`, `applied_seq: int`, `lag_degraded: bool`, `topics: list[TopicNodeView]`, `prereqs: list[PrereqEdge]` | — |
| `GET /v1/domains/{domain_id}/topics/{topic_id}` | Bearer | — | `topic: TopicNodeView`, `skills: list[SkillNodeView]`, `skill_deps: list[SkillDepEdge]`, `misconceptions: list[MisconceptionNodeView]`, `artifacts: list[ArtifactRefView]` | — |

---

## 4. Контракты промптов (`quack/prompts/`) `[КОНТРАКТ]`

Создай 3 файла шаблонов и добавь их `sha256` в `quack/prompts/manifest.json`:

1. **`structurer@1` (`quack/prompts/structurer/v1.md`):**
   - Переменные: `{{ domain_name }}`, `{{ goals }}`, `{{ background }}`, `{{ focus_areas }}`.
   - Выходная схема `StructurerOutput`:
     - `topics: list[StructurerTopic]` (`slug: str`, `name: str`, `description: str`)
     - `skills: list[StructurerSkill]` (`slug: str`, `topic_slug: str`, `name: str`, `description: str`)
     - `prereqs: list[StructurerPrereq]` (`from_topic_slug: str`, `to_topic_slug: str`)
     - `skill_deps: list[StructurerSkillDep]` (`skill_slug: str`, `depends_on_skill_slug: str`)
     - `misconceptions: list[StructurerMisconception]` (`skill_slug: str`, `name: str`, `description: str`)
   - *Примечание:* модель оперирует читаемыми `slug` внутри своего ответа, а код `structure.py` маппит `slug → UUIDv5(domain_id, structure_version, slug)`. Это полностью исключает галлюцинации невалидных UUID со стороны LLM!
2. **`diagnostic@1` (`quack/prompts/diagnostic/v1.md`):**
   - Переменные: `{{ mode }}` (`"generate"` или `"evaluate"`), `{{ topic_name }}`, `{{ skill_name }}`, `{{ student_answer }}`, `{{ question_prompt }}`.
   - Выходная схема `DiagnosticOutput`: `prompt_text: str`, `options: list[str]`, `correct_answer: str`, `is_correct: bool`, `score: Unit`, `tested_depth: Unit`.
3. **`curator@1` (`quack/prompts/curator/v1.md`):**
   - Переменные: `{{ domain_name }}`, `{{ topics_json }}`, `{{ skills_json }}`, `{{ misconceptions_json }}`.
   - Выходная схема `CuratorOutput`:
     - `promotions: list[CuratorPromotion]` (`skill_id: UUID`, `topic_id: UUID`, `name: str`, `description: str`)
     - `skill_merges: list[CuratorMerge]` (`source_id: UUID`, `target_id: UUID`, `reason: str`)
     - `topic_merges: list[CuratorMerge]` (`source_id: UUID`, `target_id: UUID`, `reason: str`)
     - `misconception_merges: list[CuratorMerge]` (`source_id: UUID`, `target_id: UUID`, `reason: str`)

---

## 5. Порядок разработки (Spec-First TDD) и набор тестов

Соблюдай трёхэтапный цикл разработки:
1. **Этап I:** Создай `schemas.py`, шаблоны промптов и сигнатуры функций/эндпоинтов с `raise NotImplementedError`.
2. **Этап II:** Напиши целевые тесты в `backend/tests/domains/` строго по спецификации ниже (без раздувания избыточных проверок).
3. **Этап III:** Реализуй логику до прохождения всех тестов. **Если тест падает — исправляй код реализации, а не ослабляй проверки в тесте.**

### Обязательный и достаточный набор тестов (`backend/tests/domains/`):
1. **`test_auth_and_learner.py` (5 тестов):**
   - Регистрация (`learner.registered` в `core.events` + строка в `auth.users`), логин, проверка `/v1/auth/me` с валидным и невалидным JWT.
   - Обновление `preferences` (`learner.preferences_updated`).
   - Rate-limiting на `/v1/auth/login` (11-й запрос возвращает `429 RATE_LIMITED`).
   - Двухфазное удаление `DELETE /v1/learner`: создаёт tombstone в `ops.deletion_tombstones`, отменяет queued задачи в `ops.jobs`, и последующий запрос с тем же JWT получает `401 UNAUTHORIZED`.
2. **`test_structure_and_dag.py` (5 тестов):**
   - Онбординг домена: `propose` через фейковый `LLMProvider` маппит `slug → UUIDv5`, создаёт `structure.interview_completed` и `structure.proposed`, а `confirm` переводит домен и узлы в `active`.
   - **Разрыв циклов DAG:** если `structurer@1` возвращает цикл `A -> B -> C -> A` в `prereqs` или `skill_deps`, санитизатор детерминированно удаляет замыкающее ребро и сохраняет ацикличный DAG.
   - **Деградация без LLM:** при `ProviderUnavailableError` эндпоинт `propose` не падает с 500, а строит базовый DAG из `focus_areas` с `degraded = True`.
   - Ручное редактирование `PATCH .../structure`: валидные изменения сохраняют `structure.edited`, а попытка добавить ребро, создающее цикл с существующими рёбрами в графе, отклоняется с `422 VALIDATION_ERROR`.
3. **`test_diagnostic_and_graph_view.py` (5 тестов):**
   - Полный цикл диагностики (`start` → `answer` верный с переходом вверх по DAG → `answer` неверный с переходом вниз по DAG → `finish`): проверка расчёта `inferred_priors` (`tested` и `propagated` вверх/вниз) и применения их проектором к `Skill.priorMastery`.
   - Чтение карты знаний `GET /v1/domains/{id}/graph` и `GET /v1/domains/{id}/topics/{topic_id}`: проверка пересчёта `display_state = "rusty"` на момент чтения (`now_ms > nearestReviewAt`) для темы, которая в БД лежит как `solid`, и фильтрации `merged`-узлов.
   - Воркер `graph.curate`: откладывает выполнение при наличии `presence:learner:{id}` в Redis, а без него продвигает provisional навыки и сливает дубликаты через `skill.merged` / `misconception.merged`.

---

## 6. Критерии приёмки (Definition of Done)

Все 4 команды в `backend/` завершаются с кодом `0`:
1. `uv run ruff check`
2. `uv run ruff format --check`
3. `uv run pyright`
4. `uv run pytest`
