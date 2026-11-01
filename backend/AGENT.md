# AGENT.md - backend/

Правила для ИИ-агентов внутри `backend/`. Читать вместе с корневым `AGENT.md` и `backend/CLAUDE.md`
(он действует целиком, кроме исключений ТЗ Ф4 §0.2 п.7: файлы вне `backend/` из §2.1, протокол решений §0.4,
коммиты и push в ветки `phase-4*` по §7.4).

## Что здесь
Бэкенд Quack: `quack/` (код), `tests/`, `db/migrations/` (dbmate, ТЗ Ф4 WP1.1), `PHASE*_NOTES.md`.
Зона — ТЗ Ф4, `documentation/ТЗ Фаза 4 (единое).md`; журнал решений — `PHASE4_NOTES.md` (ТЗ Ф4 §0.4.5).
Python 3.14, менеджер `uv`, pytest, ruff, pyright.

## Команды (в `backend/`)
- `uv sync`
- Полная проверка: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest`
- Интеграционные тесты идут на реальных Postgres 16, Neo4j, Redis (контейнеры — `README.md`).
  `QUACK_TEST_REQUIRE_INFRA=1` — не пропускать тесты, а падать без инфраструктуры.
- Процессы: `python -m quack.cli api | workers | projector | ops ...` (роль задаёт `Settings.process_role`).

## Законы ядра (`Т-§1.1`) — нарушать нельзя
1. Источник истины — журнал событий в Postgres; сначала событие, Neo4j можно пересобрать.
2. Модель интерпретирует, код считает. Пересборка не вызывает модели.
3. Один писатель в хранилище: в Neo4j пишет только проектор. Вне проектора — только сессии чтения (`read_session`).
4. **Силу свидетельства назначает код** (источник, ярус, вес), не модель. Одно суждение модели не ухудшает состояние.
5. Идемпотентность по детерминированным ключам; повтор заменяет запись.
6. Происхождение: всё прослеживается до сырья (реплики, строки канваса, попытки).
7. Упавший фоновый шаг ничего не коммитит и не влияет на ход учителя.
8. Отсутствие — не ноль: «не проверено» — явное значение; в Neo4j вместо `null` — `'unknown'`.
9. Горячий путь не ждёт фоновых процессов.
10. **Нет накопления выводов**: сводка сессии строится кодом, модель не опирается на свои прошлые ответы.

## Архитектура (контракты ТЗ Ф4 §4)
- **Слои импортов** (ТЗ Ф4 §4.1, проверяет тест `T-A6` по AST):
  ядро (`config`, `logging`, `db`, `events`, `graph`, включая `graph.read`, `jobs`, `llm`, `prompts`,
  `compute_client`, `storage`, `embeddings`) → только ядро; основание API (`quack.api`, кроме `api.app`) → ядро и
  основание API; общий продукт (`practice`) → ядро и основание API; продукт (`domains`, `session`, `agents`,
  `review`, `auth`, `materials`, `canvas`, `artifacts`, `deletion`) → ядро, основание API, `practice`, **импорт
  между продуктовыми пакетами запрещён**; сборка (`api.app`, `runtime`, `cli`, `ops`) → всё. Память продуктовые
  пакеты читают только через `quack.graph.read`; циклические импорты запрещены.
- **Ресурсы процесса** (ТЗ Ф4 §4.2) создаются один раз на процесс в `quack.runtime` и передаются явно; набор полей:
  `settings`, `db`, `neo4j`, `redis`, `llm`, `compute`, `storage`, `embeddings`, `email`, `deletions` (`engine` и
  `session_factory` уходят с SQLAlchemy, WP1.3). Каждый внешний клиент имеет подмену в `tests/support/`; модульные
  переопределения запрещены. `projector` не получает ключей моделей, хранилищ и почты.
- **LLM**: Responses API со строгой JSON Schema (`to_strict_json_schema`: без свободных словарей), один шлюз на процесс,
  семафоры по моделям, повторы, размыкатель, флаг давления по 429, роль `teacher_fallback`. Имена моделей — `Т-§1.3`
  (`muse-wonder-1.0`, `muse-spark-1.3`).
- **Метки в промптах**: модели видят `S1…`, `T1…`, `K1…`, `M1…`, не UUID; таблица «метка → id» пишется в
  `ops.turn_manifests`.
- **`degraded_mode`** хода — одно значение по приоритету: `budget_exceeded` > `llm_unavailable` > `memory_unavailable`
  > `teacher_fallback` > `projector_lag` > `null`. Выбор — функция `resolve_degraded_mode` в `quack/session/`.
- **Защита от спойлера** (WP15): проверка полного `reply_text` до первого SSE-токена; повтор до `guard_max_retries`,
  затем код удаляет предложение с ответом.
- **Ошибки API** (ТЗ Ф4 §4.5): к `ErrorCode` добавляются только `UNAUTHENTICATED`, `CONSENT_REQUIRED`,
  `UPLOAD_REJECTED`, `MARKUP_INVALID`; остальные коды Фазы 3 не меняются.

## Соглашения кода
- Идентификаторы по-английски: `snake_case` в Python/Postgres, `camelCase` свойства Neo4j, `PascalCase` метки.
- События `entity.action` в прошедшем времени; роли моделей — существительные; версии `name@N`.
- Пороги и числа — только в `Settings` под именем (комментарий `# Phase 4 decision D-<поток>-NNN` у решений вне
  документов, ТЗ Ф4 §0.4.3);
  числа в коде модулей запрещены. Блок своего потока в `config.py` — под комментарием `# --- stream X ---`.
- Миграции — dbmate в `backend/db/migrations/`: базовая миграция WP1.1, после G0 — только расширяющие
  (ТЗ Ф4 WP1.1, §0.4.4 п.4).
- `# type: ignore` / `# noqa` — только с комментарием-причиной и только для ограничений библиотек.
- Промпты — `quack/prompts/<роль>/v<N>.md`, на английском; `manifest.json` пересобирает скрипт, руками не править;
  `backend/openapi.json` — только командой `ops openapi` (поток C на гейтах).

## Владение файлами (ТЗ Ф4 §7.3)
Поток A: `quack/db`, `db/migrations`, `.squawk.toml`, `quack/events`, `quack/graph` (кроме
`graph/read/materials.py`), `quack/jobs`, `quack/runtime`, `quack/domains`, `quack/review`, `quack/deletion`.
Поток B: `quack/llm`, `quack/prompts`, `quack/session`, `quack/practice`, `quack/agents`, `PROMPT_CONTRACTS.md`,
`scripts/meta_smoke.py`. Поток C: `quack/api`, `quack/auth`, `quack/storage`, `quack/embeddings`,
`quack/materials`, `quack/canvas`, `quack/artifacts`, `quack/compute_client`, `graph/read/materials.py`,
`config.py` (общий каркас), `cli.py`, `quack/ops`, `Dockerfile`, `tests/e2e`, `tests/support` (каркас),
`loadtest`, `openapi.json`, `README.md`, общие разделы `PHASE4_NOTES.md`.
Тесты: A — `tests/db`, `tests/events`, `tests/graph`, `tests/jobs`, `tests/domains`, `tests/review`,
`tests/deletion`; B — `tests/llm`, `tests/session`, `tests/practice`, `tests/agents`; C — `tests/auth`,
`tests/storage`, `tests/materials`, `tests/canvas`, `tests/artifacts`, `tests/ops`, `tests/e2e`; общие тесты в
корне `tests/` — владелец пакета, который они проверяют. `config.py` правят все — каждый в блоке своего потока.
Чужое — только по исключениям ТЗ Ф4 §7.3 (один PR с уведомлением владельца).

## Тесты
- TDD по §8 ТЗ Ф4; тест чинят реализацией. Менять существующий тест можно только по ТЗ Ф4 §8.15.
- Не `sleep` и не повторы для нестабильных тестов — искать гонку, ждать условие с таймаутом.
- Внешние сервисы только подменами из `tests/support/` (ТЗ Ф4 §8.1).

## Do / Don't
- Не придумывай: ответ ищи по порядку источников §0.4.1; не нашёл — решение класса B/C с записью в журнал.
- Не делай «заодно» и не реализуй ТЗ Ф4 §2.2 и помеченное в первоисточнике ПОЗЖЕ (§0.4.4 п.6).
- Не пиши в Neo4j вне проектора; не читай граф до применения события (`required_seq`).
- Не коммить `.env`, ключи, кэши, отчёты нагрузки.
- Перед работой назови разделы документов, на которые опираешься; ссылки — `§2.3` (техдок), `Д-§8.1`, `С-§8.5`.
