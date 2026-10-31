# Поток A, Фаза 3: передача состояния для продолжения работы

Документ для новой сессии (в том числе облачной). Состояние на момент слияния PR #34 в `phase-3`
(`phase-3` = `869e25c` и новее). Подробные решения и аудит — в `backend/PHASE3_NOTES.md`, раздел
«Поток A» (D-A-001…D-A-008). Источник правды по задачам — `documentation/ТЗ Фаза 3 (единое).md`.

## Как начать сессию

1. Вызови скилл `phase3-stream-sync` (копия в `.claude/skills/phase3-stream-sync/SKILL.md`), поток **A**.
2. Прочитай из ТЗ: §7.1–7.4, §4.2, §6.5, разделы WP11, WP8, WP9, WP3.5 и тесты §8.3a, §8.6.
3. Прочитай `backend/CLAUDE.md` (правила для ИИ в `backend/`) и этот файл.
4. Подними тестовую инфраструктуру (ниже), сделай `git fetch`, создай ветку от `origin/phase-3`.
5. Назови разработчику стоп-точку и только потом начинай.

## Что сделано (всё влито в `phase-3`)

| Пакет | Что | PR |
| :-- | :-- | :-: |
| WP0 (зона A) | Аудит A2, A4, A5, A8–A10, A16, A17, таблица S11 | #15 |
| Этап I потока A | `JOB_KINDS`, `KIND_LLM_ROLES`, `required_seq` в фабриках, `JobDeferred`, `JobFailedPermanently`, поля `JobContext`, `quack.runtime`, `dispatcher_rules.py` (каркас), параметры `Settings` потока A | #15 |
| WP2 (зона A) | `create_job_context`, `close_job_context`, `register_all_handlers`, `check_handlers_complete`, `JobRunner(context=…)`, `cli worker` строит контекст один раз | #17 |
| WP8.7, часть A | подкоманда `ops openapi --out`, вызывает `write_openapi` из `quack.api.app` | #17, #18 |
| WP3.1, WP3.2 (ядро), WP3.4 | `required_seq` при постановке, захвате, склейке, в снимке DLQ; `defer_job`; `fail_job_permanently`; бюджет `session` → DLQ, `daily` → до полуночи UTC плюс разброс | #23 |
| WP2.1 (части C) и `graph.curate` из WP3.2 | `domains/curation.py` и `artifacts` берут ресурсы из `JobContext`; `graph.curate` откладывается через `JobDeferred` | #31 |
| WP3.3 | `PeriodicScheduler` (`quack/jobs/periodic.py`), обработчик `account.purge_deleted` (`quack/ops/purge_job.py`), проверка `job_handlers_mismatch` в `cli worker`, `PURGE_TABLES` дополнен | #34 |

`session/workers.py` на `JobContext` перевёл поток B (PR #33). Тесты потока A: `backend/tests/test_jobs_phase3.py`
(`T-Q1`–`T-Q5`, `T-Q7`, `T-Q8`, `T-Q11`).

## Что осталось по порядку потока A (§7.1)

`WP11 → WP3.5 → WP8.1, 8.2 → WP8.4–8.6 → WP9`. Плюс мелочи до WP11.

### Мелкий PR до WP11 (снять долги WP2)

- Снять значения `None` у полей `JobContext` (`engine`, `neo4j`, `redis`, `llm`, `compute`; `redis` остаётся `Redis | None`) и убрать
  хелпер `_job_resources` из `session/workers.py` (договорено с B, D-A-002). Сборщики тестов уже есть
  (`tests/support/resources.py::build_job_context`); `JobRunner` без `context` должен перестать собирать контекст из двух полей.
- В `quack/runtime/__init__.py` перейти с `HttpLLMProvider` и `settings.llm_api_*` на `ResponsesClient(base_url, api_key, *, http_client=None)`
  и `settings.meta_api_*` (после PR #35 потока C, D-C-024).
- Обновить ветку `phase-3-a-pending-red`: там осталось только `T-Q9`.

### WP11. Диспетчер журнала (ТЗ WP11, 2,5 дня)

- **WP11.1–11.3 (зона A):** диспетчер как отдельная задача в процессе `projector`; таблица `ops.dispatcher_offsets` уже в миграции `0002`.
  `rules_for(event) -> list[JobSpec]` в `quack/jobs/dispatcher_rules.py` (каркас и таблица `DISPATCHER_RULES` есть, тело `NotImplementedError`).
  Правило должно давать ровно ту же задачу (`kind`, `priority`, `lock_key`, `coalesce_key`), что ставит транзакция события; `required_seq` — `seq` события-триггера.
  Тесты `T-D1`–`T-D8` (§8.3a). Параметры: `dispatcher_concurrency`, `dispatcher_batch_size`, `dispatcher_restart_pause_s`, `consumer_poll_ms`.
- **Правила (таблица S11, из аудита):**

| Событие | Задача | `required_seq` | Условие и данные |
| :-- | :-- | :-: | :-- |
| `turn.teacher_replied` | `turn.extract` | `seq` события | `student_seq` — `seq` `turn.student_replied` того же `turn_id` |
| `turn.teacher_replied` | `session.checkpoint` | `NULL` | `payload.turn_index − core.sessions.last_checkpoint_turn ≥ checkpoint_every_turns` (ответ потока B, D-A-003) |
| `session.completed` | `session.finish_artifacts` | `seq` события | `topic_id` из строки сессии |
| `session.completed` | `graph.curate` | `seq` события | `domain_id` из payload |
| `session.digest_created` | `artifact.refresh_cheatsheet` | `seq` события | `payload.topic_id` не `null` |

  Диспетчер не читает граф и не вызывает модели. Прямая постановка остаётся только у планировщика и у эндпоинта `POST /v1/topics/{topic_id}/cheatsheet/refresh`.
- **WP11.4 (переключение, один PR потока A, ревью B и C):** удалить вызовы `enqueue_job` в `quack/session/turns.py`, `quack/session/lifecycle.py`,
  `quack/artifacts/cheatsheets.py` (кроме `request_cheatsheet_refresh`). Тесты, проверявшие наличие задачи сразу после запроса, получают в фикстуре
  `dispatcher.run_once()` (§8.9 п.7). После этого `required_seq` в фабриках `kinds.py` становится обязательным аргументом (D-A-002).
  Требует подтверждения, что владельцы зон B и C предупреждены.
- **Q-A1 (решить с C до WP11.4):** `domains/learner.py:85` отменяет задачи ученика прямым `UPDATE ops.jobs`, а `T-Q9` такой SQL запрещает.
  План: функция `cancel_learner_jobs` в `quack/jobs/queue.py` (зона A), вызов из `domains` — правка одной строки в зоне C.
- **`T-Q9`** (AST: нет `create_driver`, `create_redis`, `create_engine`, `HttpLLMProvider(`, `ResponsesClient(`, `AsyncOpenAI(`, `ComputeClient(`, SQL по `ops.jobs`,
  `enqueue_job` вне эндпоинта) сейчас красный, лежит в `phase-3-a-pending-red`. Переносить в основной файл, когда позеленеет.
- Видимость (WP11.5): `ops status` (WP8.4) показывает `dispatcher.learners_lagging`, `dispatcher.max_lag_events`; реестр событий (WP8.5) — пометки «без обработчика/без правила», `T-O6`.

### Дальше

- **WP3.5** плавная остановка (`worker_shutdown_grace_s`, возврат недоделанных задач в `queued` без роста `attempt`, `T-Q6`).
- **WP8.1/8.2** роли процессов в CLI (`projector`/`workers`/`api`/`ops`, `Settings.process_role`; сейчас `cli worker` совмещает проектор и исполнителей), плавная остановка.
- **WP8.4–8.6** `ops status`, `event-registry`, `can-rollback`, `ops seed-learners` (нужен C для Locust). По порядку реза (§7.5) WP8.6 и WP9.3 режутся первыми.
- **WP9** образы, `compose.yaml` (профиль `loadtest` с `meta-mock` — `uvicorn --factory loadtest.meta_mock:app_from_env`, путь `POST /responses`, `META_API_BASE_URL=http://meta-mock:<порт>` без `/v1`, у мока нет `/health`, поэтому `depends_on` — `service_started`), CI.

## Открытые решения и вопросы владельцу

- **D-A-002** — `required_seq` необязателен в фабриках и `None` у полей `JobContext` до WP11.4 и мелкого PR выше.
- **D-A-006…008** — записаны, ждут подтверждения владельца (класс C).
- **Q-A1** — см. выше. **Q-A2** — подтвердить D-A-002 и D-A-003.
- C ждёт от A в `phase-3`: WP11 (`T-E1` падает на `required_seq` у `turn.extract`), WP8.6 (Locust). `T-E10` закрыт PR #34.

## Правила, которые нельзя нарушать

- Зона потока A (§7.3): `quack/jobs/**`, `quack/runtime/**`, `quack/cli.py`, `quack/ops/**`, `backend/Dockerfile`, `compose.yaml`, `compose.override.yaml`, `.github/**`.
  Пять разрешённых пересечений — только после подтверждения, что владелец предупреждён. `quack/config.py` — только блок `# --- stream A ---`.
- Закон ядра: пишет в Neo4j только проектор; диспетчер ничего не пишет в граф.
- Ветки: `phase-3-a-<тема>` (форма `phase-3/a-…` невозможна, D-C-001). Каждый WP — минимум два коммита: `phase3(wpN): tests (red)` и `phase3(wpN): implementation (green)`.
- Коммиты: только по просьбе разработчика, без `Co-authored-by`, без упоминаний ИИ (`backend/CLAUDE.md`). Не пушить в `main`, не делать `--force`.
- В `phase-3` — только зелёное на самом слитом коммите: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q` (в `backend/`).
  Подтягивай свежий `phase-3` в свою ветку merge-коммитом перед слиянием; в сообщениях указывай, какие проверки на каком коммите с каким итогом.
- Слой пакетов (`tests/test_contracts.py`, `T-A6`): `quack.jobs` не импортирует `quack.ops`/`quack.runtime`/`quack.cli`/`quack.api.app`.
- Новые тесты не должны ослаблять старые (§8.9); для каждой правки утверждений — проверка на неверной реализации и запись в «Правки тестов».

## Тестовая инфраструктура

Нужны Postgres 16, Neo4j и Redis (команды `docker run` — в `backend/README.md`):
`quack-test-pg` (порт 55432), `quack-test-neo4j` (57687, пароль `quacktest123`), `quack-test-redis` (56379). Без них тесты пропускаются,
поэтому всегда задавай `QUACK_TEST_REQUIRE_INFRA=1`. Один `pytest` одновременно (общая БД). После `git pull` — `uv sync` (в зависимостях есть `openai`, в dev — `jsonschema`).
Полный набор идёт около 6 минут. В облачной сессии Docker может быть недоступен: тогда нужны сервисные контейнеры или внешние Postgres/Neo4j/Redis с теми же портами и паролями,
иначе утверждать «набор зелёный» нельзя.

## Мелочи и расхождения

- В `backend/CLAUDE.md` документы названы `Технический документ AI и архитектура памяти.md` и `DevOps развёртывание и эксплуатация.md`; в `documentation/`
  лежат файлы с суффиксом `_1` (`Технический_документ_AI_и_архитектура_памяти_1.md`, `DevOps_развёртывание_и_эксплуатация_1.md`).
- На Windows `* text=auto` превращает файлы в рабочей копии в `CRLF`; `backend/openapi.json` закреплён `eol=lf` в `backend/.gitattributes`.
- Файлы `graphify-out/` в основном checkout не отслеживаются git и для работы не нужны.
