# Техническое задание — Фаза 2, Поток 3: Сервис Compute (SymPy), Практический движок и Артефакты (Дайджесты, Заметки, Шпаргалки, Поиск, Экспорт)

Этот документ определяет полный объём работ, контракты, алгоритмические решения и тесты для **Разработчика 3 (Поток 3)** во второй фазе реализации бэкенда Quack.

---

## 0. Границы ответственности и защита от Merge-конфликтов `[КОНТРАКТ]`

### 0.1. Владение файлами (Zero Merge Conflicts)
- **Твоя эксклюзивная зона записи:**
  - `compute/**` — изолированный микросервис символьных вычислений на SymPy (`compute/pyproject.toml`, код сервиса и его unit-тесты `compute/tests/`).
  - `backend/quack/artifacts/**` — весь код практического контура (генерация и селектор задач, подсказки, гибридная проверка ответов) и контура артефактов (заметки ученика, версионируемые шпаргалки тем, дайджесты сессий, FTS-поиск, экспорт в Markdown/JSON).
  - `backend/quack/prompts/task_gen/v1.md`, `backend/quack/prompts/evaluator/v1.md`, `backend/quack/prompts/artifacts/v1.md` — шаблоны промптов твоего потока + соответствующие 3 ключа (`task_gen@1`, `evaluator@1`, `artifacts@1`) в `backend/quack/prompts/manifest.json`.
  - `backend/tests/artifacts/**` — все тесты твоего потока в основном бэкенде.
- **Строгий запрет на изменение чужих и базовых файлов:**
  - **НЕ изменяй** файлы ядра Фазы 1 (`quack/config.py`, `quack/logging.py`, `quack/db/*`, `quack/events/*`, `quack/graph/*`, `quack/jobs/*`, `quack/llm/*`, `quack/compute_client/*`, `quack/api/*`, `quack/ops/*`, `quack/cli.py`, `migrations/*`, `tests/test_*.py`).
  - **НЕ импортируй** ничего из `quack/domains/` (Поток 1) и `quack/session/` (Поток 2).
  - Для проверки JWT используй локальную зависимость в `quack/artifacts/deps.py` по единому контракту JWT (раздел `2.2` ниже).
  - В своих тестах монтируй `router` из `quack/artifacts/router.py` локально на `create_app(settings)`.

### 0.2. Первоисточники и готовые примитивы Фазы 1
Перед реализацией изучи:
- `documentation/Спецификация Фаз 1-2 контракты и схемы.md` (`§2–§8`) и `backend/PHASE1_NOTES.md`.
- Разделы основного техдока (`Т-§`): `§6.1–6.3` (генерация задач, семейства `family_id`, подсказки, гибридная проверка `compute` + LLM), `§7.2–7.4` (ярусы свидетельств T1/T2 и влияние `independence`), `§14.1–14.5` (дайджесты, заметки, шпаргалки, FTS-поиск, экспорт), `§17.1` (деградация практики без LLM), `§22.2` (хакатонный срез).
- Разделы операционного документа (`Д-§`): `§5.1` и `§7` (изоляция сервиса `compute`, ограничения безопасности SymPy).
- Используй готовые фасады Фазы 1:
  - `append_events` из `quack/events/append.py` (автоматически синхронно обновляет `core.task_bank` и `core.artifacts`).
  - `QUACK_UUID_NS` из `quack/graph/ids.py`.
  - `ComputeClient`, `MathCheckRequest`, `MathCheckResponse` из `quack/compute_client/client.py`.
  - `enqueue_job`, `kinds.artifact_refresh_cheatsheet` и `@register_job_handler` из `quack/jobs/`.
  - `LLMGateway` из `quack/llm/gateway.py` и `session_events_channel` из `quack/db/redis.py`.

---

## 1. Архитектура сервисов Потока 3 `[ОРИЕНТИР]`

### 1.1. Изолированный сервис `compute/`
- `compute/pyproject.toml` — `requires-python = ">=3.12"`, зависимости: `fastapi`, `uvicorn[standard]`, `pydantic>=2.7`, `sympy>=1.13`, dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `pyright`.
- `compute/quack_compute/parser.py` — безопасный парсер выражений SymPy (белый список функций и символов, запрет `__`, атрибутов и встроенных функций Python).
- `compute/quack_compute/checker.py` — 6 режимов математической проверки и выполнение в дочернем процессе с жёстким убийством по таймауту.
- `compute/quack_compute/app.py` — FastAPI-приложение с `POST /v1/check/math` и `GET /health/live`.

### 1.2. Пакет `backend/quack/artifacts/`
- `deps.py` — декодирование Bearer JWT (`sub -> learner_id`) и проверка отсутствия tombstone в `ops.deletion_tombstones`.
- `practice.py` — подбор и генерация задач (`task_gen@1` + fallback из `core.task_bank`), выдача уровневых подсказок (`1..3`), гибридный оценщик ответов (`ComputeClient` → `evaluator@1` → детерминированный fallback).
- `notes.py` — CRUD заметок ученика (`artifact.note_created`, `artifact.note_updated`, `artifact.note_deleted`).
- `cheatsheets.py` — детерминированный расчёт `artifact_id` шпаргалки темы, чтение и принудительное обновление шпаргалки, воркеры `session.finish_artifacts` и `artifact.refresh_cheatsheet`.
- `search.py` — полнотекстовый поиск по `core.artifacts` и `core.events` (через индекс `idx_events_fts_en`) и экспорт домена в Markdown / JSON.
- `schemas.py` — Pydantic-модели запросов, ответов и схем промптов.
- `router.py` — `router = APIRouter(prefix="/v1")`.

---

## 2. Инженерные решения и контракты модулей `[КОНТРАКТ]`

### 2.1. Изолированный сервис `compute` (`POST /v1/check/math`, `Спецификация §8.4`, `Д-§7`)

1. **Безопасность парсинга (`parser.py`):**
   - Длина `student_expr` и `reference_expr` — не более `1000` символов (при превышении HTTP `422`).
   - Перед парсингом запрещены подстроки `"__"`, `"import"`, `"eval"`, `"exec"`, `"lambda"`, `"open"`, `"getattr"`.
   - Используется `sympy.parsing.sympy_parser.parse_expr(..., local_dict=ALLOWED_LOCALS, global_dict={"Integer": sympy.Integer, "Float": sympy.Float, "Rational": sympy.Rational, "Symbol": sympy.Symbol}, transformations=SAFE_TRANSFORMATIONS, evaluate=False)` с поддержкой неявного умножения (`3x -> 3*x`) и возведения в степень (`^ -> **`).
   - В `ALLOWED_LOCALS` входят только заявленные в запросе `variables` (как `sympy.Symbol`) и стандартные математические функции/константы (`sin`, `cos`, `tan`, `exp`, `log`, `ln`, `sqrt`, `Abs`, `pi`, `E`, `oo`, `Matrix`).
   - При любой синтаксической ошибке сервис возвращает **`200 OK`** со `status = "parse_error"`, `is_equivalent = None` и текстом ошибки в `error_message`.
2. **Изоляция по времени (`timeout_ms`):**
   - Символьное упрощение (`simplify`, `solve`, `diff`, `integrate`) выполняется в отдельном процессе (или пуле воркер-процессов с жёстким `terminate()`/`kill()` при превышении `timeout_ms`).
   - При превышении `timeout_ms` сервис возвращает **`200 OK`** со `status = "timeout"`, `is_equivalent = None`.
3. **Семантика 6 режимов (`mode`):**
   - `expression_equiv`: эквивалентность выражений (`simplify(student - reference) == 0`, с численной подстановкой нескольких контрольных точек при сложных тригонометрических/рациональных тождествах).
   - `equation_roots`: совпадение множества корней относительно `variables[0]`.
   - `derivative`: сравнение `student` с `diff(reference, variables[0])` (или `simplify(student - reference) == 0`, если `reference` уже является производной).
   - `integral`: проверка неопределённого интеграла через дифференцирование ответа студента `simplify(diff(student, variables[0]) - diff(reference, variables[0])) == 0` (чтобы константа интегрирования `+ C` не ломала проверку!).
   - `matrix_eq`: поэлементное равенство матриц.
   - `numeric_tol`: численное сравнение `abs(float(student) - float(reference)) <= tolerance`.

### 2.2. Практический контур: Генерация задач, Подсказки и Гибридная проверка (`Т-§6.1–6.3`, `Т-§7.2`, `Т-§17.1`)

1. **Подбор и генерация следующей задачи (`POST /v1/sessions/{session_id}/tasks/next`):**
   - Читает навыки темы из Neo4j (`read_consistent_slice`) и уже назначенные в этой сессии задачи из `core.events`.
   - **Целевой выбор навыка и семейства (`distinctFamilies`):** выбирает навык темы, у которого `masteryStatus != 'mastered'` (в первую очередь — навыки с `distinctFamilies == 1`, которым для открытия потолка `0.6` и статуса `mastered` нужна задача из нового `family_id`, `Т-§7.3`).
   - Если в `core.task_bank` уже есть нерешённая задача по этому навыку из нового `family_id` (или при `degraded / force_bank`), берёт её из `core.task_bank` и записывает `task.assigned`.
   - Иначе вызывает `LLMGateway.generate_structured(role="task_gen", prompt_version=1, ...)` (при `LLMUnavailableError` / `LLMBudgetExceededError` — fallback на любую подходящую задачу из `core.task_bank`) и в одной транзакции записывает `task.generated` + `task.assigned`.
2. **Прогрессивные подсказки (`POST /v1/sessions/{session_id}/tasks/{task_id}/hint`):**
   - Определяет следующий уровень подсказки `hint_level = min(3, current_max_level + 1)`.
   - Штраф самостоятельности `independence_penalty`: уровень 1 → `0.2`, уровень 2 → `0.4`, уровень 3 → `0.6`.
   - Берет текст подсказки из `reference_solution["hints"]` в `core.task_bank` (или генерирует краткую подсказку) и записывает событие `task.hint_given`.
3. **Гибридная проверка ответа (`POST /v1/sessions/{session_id}/tasks/{task_id}/submit`):**
   - Считает накопленный штраф по всем событиям `task.hint_given` для `(session_id, task_id)`: `independence = max(0.1, round(1.0 - sum_penalties, 4))`.
   - **Каскад проверки (`Т-§6.3`):**
     1) Если `kind == "choice"`: детерминированное сравнение с эталоном (`evaluator = "compute"` → даёт свидетельство **Tier 1**).
     2) Если `kind == "math"`: сначала вызывается `ComputeClient.check_math`. Если `status == "completed"`, вердикт (`is_correct = bool(is_equivalent)`, `score = 1.0 if is_equivalent else 0.0`) фиксируется с `evaluator = "compute"` (**Tier 1**). Если `status in ("parse_error", "timeout", "unsupported")` или `ComputeUnavailableError`, управление передаётся в `evaluator@1` с `evaluator = "hybrid"` (**Tier 2**).
     3) Если `kind in ("open", "code")`: вызывается `evaluator@1` с `evaluator = "llm"` (**Tier 2**).
     4) **Деградация без LLM и без Compute (`Т-§17.1`):** если и `compute`, и `LLMGateway` недоступны, выполняется нормализованное сравнение строки ответа с `reference_solution.get("answer") or reference_solution.get("sympy")`, чтобы ученик никогда не терял отправленный ответ.
   - В одной транзакции вызывается `append_events` с парой событий `[task.submitted, task.evaluated]`.

### 2.3. Контур Артефактов, Инвариант ID шпаргалки, FTS-поиск и Экспорт (`Т-§14.1–14.5`)

1. **КРИТИЧЕСКИЙ ИНВАРИАНТ `artifact_id` для шпаргалки темы (`artifact.cheatsheet_updated`):**
   - На таблице `core.artifacts` установлен частичный уникальный индекс `uq_artifacts_topic_cheatsheet (learner_id, topic_id) WHERE kind = 'cheatsheet' AND NOT is_deleted`, а проекция в `quack/events/projections.py` делает `INSERT ... ON CONFLICT (id) DO UPDATE`.
   - Поэтому для любой пары `(learner_id, topic_id)` идентификатор шпаргалки **всегда детерминированно вычисляется по формуле**:
     `cheatsheet_id = uuid5(QUACK_UUID_NS, f"cheatsheet:{learner_id}:{topic_id}")`.
   - Перед записью `artifact.cheatsheet_updated` читается текущая `version` этой шпаргалки из `core.artifacts` (если строки ещё нет — `new_version = 1`, если есть — `new_version = current_version + 1`). Это гарантирует бесконечное обновление версий без нарушения `uq_artifacts_topic_cheatsheet`.
2. **Фоновые воркеры артефактов:**
   - **`@register_job_handler("session.finish_artifacts")`:**
     - Читает реплики и решённые задачи завершённой сессии из `core.events`.
     - Вызывает `LLMGateway.generate_structured(role="artifacts", prompt_version=1, variables={"mode": "digest", ...})` (при отказе LLM — собирает детерминированный Markdown-дайджест из списка разобранных навыков и решённых задач).
     - Записывает `session.digest_created(artifact_id=uuid5(QUACK_UUID_NS, f"digest:{session_id}"), ...)`.
     - Если у сессии был `topic_id`, ставит в `ops.jobs` задачу `kinds.artifact_refresh_cheatsheet(learner_id, topic_id, {"domain_id": str(domain_id), "session_id": str(session_id)})`.
   - **`@register_job_handler("artifact.refresh_cheatsheet")`:**
     - Читает навыки, закрытые/активные заблуждения темы из Neo4j и текущую шпаргалку из `core.artifacts`.
     - Вызывает `artifacts@1` (`mode = "cheatsheet"`, с детерминированным fallback-шаблоном при отказе LLM).
     - Записывает `artifact.cheatsheet_updated` с детерминированным `cheatsheet_id` и `version = prev + 1`.
     - Если в `job.payload` передан `session_id` и доступен Redis, публикует в `session_events_channel(UUID(session_id))` событие `{"event": "cheatsheet_ready", "data": {"topic_id": str(topic_id), "artifact_id": str(cheatsheet_id), "version": new_version}}`.
3. **Полнотекстовый поиск (`GET /v1/artifacts/search`):**
   - Принимает `q: str` (минимум 2 символа), `domain_id: UUID | None = None`, `limit: int = 20`.
   - Выполняет два быстрых запроса:
     1) По `core.artifacts` (`WHERE learner_id = :l AND NOT is_deleted AND to_tsvector('english', title || ' ' || content_md) @@ plainto_tsquery('english', :q)`).
     2) По `core.events` с точным попаданием в частичный GIN-индекс `idx_events_fts_en` (`WHERE learner_id = :l AND type IN ('turn.student_replied', 'turn.teacher_replied', 'artifact.note_created', 'artifact.note_updated', 'artifact.cheatsheet_updated', 'session.digest_created') AND to_tsvector('english', coalesce(payload->>'text', '')) @@ plainto_tsquery('english', :q)`).
4. **Экспорт домена (`GET /v1/domains/{domain_id}/export`):**
   - Поддерживает `format: Literal["md", "json"] = "md"`.
   - Собирает темы, навыки (`pMastery`, `masteryStatus`), шпаргалки тем, дайджесты сессий и активные заметки ученика по домену и отдаёт единый структурированный Markdown-документ или JSON-пакет (`Т-§14.4`).

---

## 3. Таблица HTTP-эндпоинтов Потока 3 `[КОНТРАКТ]`

| Сервис | Метод и путь | Auth | Вход (Body / Query) | Выход (`200` / `201`) | События `core.events` / Задачи |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **`compute`** | `POST /v1/check/math` | Внутр. | `MathCheckRequest` (`Спецификация §8.4`) | `MathCheckResponse` (`status`, `is_equivalent`, `normalized_student`, `diff_hint`, `error_message`) | — |
| **`compute`** | `GET /health/live` | Нет | — | `{"status": "ok"}` | — |
| **`api`** | `POST /v1/sessions/{session_id}/tasks/next` | Bearer | `topic_id: UUID`, `turn_id: UUID`, `kind: Literal['open','math','code','choice'] = 'math'`, `idempotency_key: str` | `task_id: UUID`, `family_id: str`, `difficulty: float`, `kind: str`, `prompt_text: str`, `degraded: bool` | [`task.generated`], `task.assigned` |
| **`api`** | `POST /v1/sessions/{session_id}/tasks/{task_id}/hint` | Bearer | `turn_id: UUID`, `idempotency_key: str` | `task_id: UUID`, `hint_level: int`, `hint_text: str`, `independence_penalty: float`, `seq: int` | `task.hint_given` |
| **`api`** | `POST /v1/sessions/{session_id}/tasks/{task_id}/submit` | Bearer | `turn_id: UUID`, `answer: str`, `response_time_sec: float \| None = None`, `idempotency_key: str` | `task_id: UUID`, `evaluator: str`, `is_correct: bool`, `score: float`, `independence: float`, `feedback: str`, `skill_scores: list`, `triggered_misconceptions: list`, `seq: int` | `task.submitted`, `task.evaluated` |
| **`api`** | `POST /v1/artifacts/notes` | Bearer | `domain_id: UUID`, `topic_id: UUID \| None = None`, `session_id: UUID \| None = None`, `title: str`, `text: str`, `idempotency_key: str` | `artifact_id: UUID`, `version: 1`, `seq: int` | `artifact.note_created` |
| **`api`** | `PATCH /v1/artifacts/notes/{artifact_id}` | Bearer | `title: str`, `text: str`, `idempotency_key: str` | `artifact_id: UUID`, `version: int`, `seq: int` | `artifact.note_updated` |
| **`api`** | `DELETE /v1/artifacts/notes/{artifact_id}` | Bearer | `idempotency_key: str` | `artifact_id: UUID`, `is_deleted: True`, `seq: int` | `artifact.note_deleted` |
| **`api`** | `GET /v1/artifacts` | Bearer | `domain_id: UUID \| None = None`, `topic_id: UUID \| None = None`, `kind: str \| None = None` | `artifacts: list[ArtifactView]` (активные из `core.artifacts`) | — |
| **`api`** | `GET /v1/topics/{topic_id}/cheatsheet` | Bearer | — | `ArtifactView \| null` | — |
| **`api`** | `POST /v1/topics/{topic_id}/cheatsheet/refresh` | Bearer | `domain_id: UUID` | `job_id: UUID`, `status: "queued"` | Задача `artifact.refresh_cheatsheet` |
| **`api`** | `GET /v1/artifacts/search` | Bearer | `q: str`, `domain_id: UUID \| None = None`, `limit: int = 20` | `artifact_hits: list[ArtifactSearchHit]`, `event_hits: list[EventSearchHit]` | — |
| **`api`** | `GET /v1/domains/{domain_id}/export` | Bearer | `format: Literal['md','json'] = 'md'` | `content_md: str` (для `md`) или полный JSON-объект домена | — |

---

## 4. Контракты промптов (`quack/prompts/`) `[КОНТРАКТ]`

Создай 3 файла шаблонов и добавь их `sha256` в `quack/prompts/manifest.json`:

1. **`task_gen@1` (`quack/prompts/task_gen/v1.md`):**
   - Переменные: `{{ topic_name }}`, `{{ target_skills_json }}`, `{{ excluded_families_json }}`, `{{ kind }}`, `{{ target_difficulty }}`.
   - Выходная схема `TaskGenOutput`:
     - `family_id: str`
     - `difficulty: Unit`
     - `prompt_text: str`
     - `reference_solution: dict[str, Any]` (содержит `"sympy"` или `"answer"` / `"option"` и `"hints": list[str]` из 3 элементов)
     - `target_misconceptions: list[str] = []`
2. **`evaluator@1` (`quack/prompts/evaluator/v1.md`):**
   - Переменные: `{{ prompt_text }}`, `{{ reference_solution_json }}`, `{{ student_answer }}`, `{{ target_skills_json }}`, `{{ known_misconceptions_json }}`.
   - Выходная схема `EvaluatorOutput`:
     - `is_correct: bool`
     - `score: Unit`
     - `feedback: str`
     - `skill_scores: list[SkillEvalScore]`
     - `triggered_misconceptions: list[MisconceptionEvalHit] = []`
3. **`artifacts@1` (`quack/prompts/artifacts/v1.md`):**
   - Переменные: `{{ mode }}` (`"digest"` или `"cheatsheet"`), `{{ title_context }}`, `{{ skills_summary_json }}`, `{{ misconceptions_summary_json }}`, `{{ dialogue_excerpt_json }}`, `{{ previous_content_md }}`.
   - Выходная схема `ArtifactGenOutput`:
     - `title: str`
     - `text: str` (валидный Markdown)

---

## 5. Порядок разработки (Spec-First TDD) и набор тестов

Соблюдай трёхэтапный цикл разработки:
1. **Этап I:** Создай проект `compute/` и сигнатуры/схемы в `backend/quack/artifacts/` с `raise NotImplementedError`.
2. **Этап II:** Напиши целевые тесты в `compute/tests/` и `backend/tests/artifacts/` строго по спецификации ниже.
3. **Этап III:** Реализуй логику до прохождения всех тестов. **Если тест падает — исправляй код реализации, а не ослабляй проверки в тесте.**

### Обязательный и достаточный набор тестов:
1. **`compute/tests/test_compute_service.py` (5 тестов):**
   - Проверка всех 6 режимов (`expression_equiv` включая `3x^2 == 3*x**2`, `equation_roots`, `derivative`, `integral` с константой `+ 5`, `matrix_eq`, `numeric_tol`).
   - Безопасность парсера: попытки передать `__import__('os')`, доступ к атрибутам или синтаксический мусор возвращают `200 OK` со `status = "parse_error"`, не выполняя код.
   - Таймаут дочернего процесса: тяжёлое выражение при `timeout_ms = 50` прерывается и возвращает `200 OK` со `status = "timeout"`.
2. **`backend/tests/artifacts/test_practice_engine.py` (5 тестов):**
   - Генерация и выбор задачи (`POST .../tasks/next`): выбирает новое семейство `family_id` для навыка с `distinctFamilies == 1`, а при отказе LLM (`ProviderUnavailableError`) берёт задачу из `core.task_bank` (`degraded = True`).
   - Прогрессивные подсказки (`level 1 -> 2 -> 3`) и снижение `independence` при последующем `submit`.
   - **Гибридный каскад проверки (`submit`):**
     - `math`-задача проверяется через `ComputeClient` (`evaluator = "compute"`, что даёт свидетельство Tier 1 в проекторе);
     - если `ComputeClient` вернул `status = "parse_error"`, автоматически вызывается `evaluator@1` (`evaluator = "hybrid"`, Tier 2).
3. **`backend/tests/artifacts/test_artifacts_search_export.py` (4 теста):**
   - CRUD заметок ученика (`note_created` → `note_updated` с ростом `version = 2` → `note_deleted` со скрытием из `GET /v1/artifacts`).
   - **Инвариант обновления шпаргалки темы:** многократный запуск воркера `artifact.refresh_cheatsheet` для одной темы обновляет одну и ту же строку в `core.artifacts` (`version = 1 -> 2 -> 3`) без конфликта по `uq_artifacts_topic_cheatsheet` и шлёт `cheatsheet_ready` в Redis.
   - Воркер `session.finish_artifacts`: создаёт дайджест сессии и ставит задачу `artifact.refresh_cheatsheet`.
   - Полнотекстовый поиск `GET /v1/artifacts/search` (находит совпадения и в `core.artifacts`, и в `core.events` через FTS) и экспорт домена `GET /v1/domains/{id}/export?format=md|json`.

---

## 6. Критерии приёмки (Definition of Done)

Все команды в `compute/` и `backend/` завершаются с кодом `0`:
1. В `compute/`: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest`
2. В `backend/`: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest`
