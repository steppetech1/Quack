# Фаза 3: заметки, аудит и журнал решений

Единый файл заметок трёх потоков (ТЗ Фазы 3 §0.5). Общие разделы ведёт поток C; в разделы
потоков A и B пишут только их исполнители. Номера решений: `D-A-NNN`, `D-B-NNN`, `D-C-NNN`.

## Сводка для владельца

- **Сделано:** WP0 в зоне C. Работа трёх потоков Фазы 2 уже в `main`, сливать нечего.
  Ветка `phase-3` создана от `main` (`694eb50`). Полный набор проверок `backend/` зелёный
  (410 тестов), `compute/` зелёный в Linux-контейнере.
- **Не проверено:** четыре проверки `compute/` на Windows-хосте. Сервис рассчитан только на
  Linux (`forkserver`, `SIGALRM`), поэтому на Windows проверки падают по устройству сервиса,
  а не из-за дефекта. В Linux проверки прошли. Подробно — D-C-002.
- **Заблокировано:** ничего.
- **Решения класса C, требующие подтверждения:** нет.
  - D-C-001 (рабочие ветки `phase-3-<поток>-<тема>`) — подтверждено владельцем 2026-10-31.
  - D-C-004 (токен без строки `auth.users` → 401) — владелец 2026-10-31 передал выбор
    исполнителю; принят вариант (1).
- **Ветка / последний коммит:** `phase-3` = `694eb50`; заметки — в `phase-3-c-wp0-notes`; WP1 — в `phase-3-c-wp1-auth-resources`.

## Вход

Проверка §1.3 (поток C, 2026-10-31).

| Поток Фазы 2 | Где в репозитории | Состояние относительно `main` |
| :-- | :-- | :-- |
| 1 (`quack/domains`, аутентификация) | `origin/2-1-back` (`3bf48be`); в `main` — squash `6587076` «2 1 back (#1)» и ремедиация `d19cd65…d4b3ce3` (PR #4) | Дерево `6587076` совпадает с `origin/2-1-back` по `backend/quack` и `backend/tests`; в `main` дальше идут доработки ремедиации. Ветку не сливать |
| 2 (`quack/session`) | коммиты `04329d6…288fa0b` в `main`, ремедиация `064ef94…546f7cc` (PR #4); `origin/stream-2-session`, `origin/night/2026-10-30-2`, `origin/pr/night-2026-10-30-4-remediation` | Все три ветки целиком в `main` (0 коммитов сверх `main`) |
| 3 (`compute/`, `quack/artifacts`) | `origin/2-3-back` (`37f5cca`); в `main` — squash `b9f4ddb` «3 поток (#5)» | По `backend/quack`, `backend/tests`, `compute/` и `STREAM3_NOTES.md` совпадает с `main`. Отличаются только старые `AGENT.md`, в `main` они новее. Ветку не сливать |

- Интеграционная ветка `phase-3` создана от `main` `694eb50` («documentation for phase 3»).
  Merge-коммитов WP0 нет: все потоки уже слиты в `main` через PR #1, #4, #5.
- Инфраструктура для тестов — по `backend/README.md`: контейнеры `quack-test-pg` (Postgres 16,
  :55432), `quack-test-neo4j` (:57687), `quack-test-redis` (:56379). Docker 29.4.1 доступен.
- `quack/prompts/manifest.json` уже содержит все девять ключей WP0 п.1 (`structurer@1`,
  `diagnostic@1`, `curator@1`, `teacher@1`, `extractor@1`, `checkpointer@1`, `task_gen@1`,
  `evaluator@1`, `artifacts@1`); `test_packaged_prompt_manifest_is_consistent` проходит.

Прогон на `phase-3` (`694eb50`):

| Где | Команда | Итог |
| :-- | :-- | :-- |
| `backend/` | `uv run ruff check .` | All checks passed |
| `backend/` | `uv run ruff format --check .` | 164 files already formatted |
| `backend/` | `uv run pyright` | 0 errors |
| `backend/` | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q` | 410 passed, 0 skipped, 236 warnings (`InsecureKeyLengthWarning` dev-секрета JWT) |
| `compute/` | `uv run ruff check .` / `ruff format --check .` | passed / 7 files already formatted |
| `compute/` | `uv run pyright` (Windows) | 7 errors: `signal.SIGALRM`, `setitimer`, `ITIMER_REAL`, `BaseContext.Process` — API только для Linux |
| `compute/` | `uv run pyright --pythonplatform Linux` | 0 errors |
| `compute/` | `uv run pytest` (Windows) | collection error: `cannot find context for 'forkserver'` |
| `compute/` | `docker run --rm -v "<compute>:/src:ro" ghcr.io/astral-sh/uv:python3.12-bookworm-slim sh -c "cp -r /src /app && cd /app && rm -rf .venv && uv sync -q --frozen && uv run pytest -q"` | 5 passed |

## Статус

| WP | Поток | Статус | Тесты |
| :-- | :-: | :-- | :-- |
| WP0 | C ведёт, A, B | зона C: выполнено (вход, прогон, аудит пунктов C); зоны A и B: ждут аудита своих пунктов | существующие 410 + 5 (compute) зелёные |
| WP1 | C | выполнено полностью (WP1.1–WP1.6); `create_app(resources=)`, единый JWT, единый AppResources, единый ratelimit, пороги в Settings | `T-A1`–`T-A6`, `test_ratelimit`, `test_thresholds_in_settings` зелёные; всего 452 passed |
| WP2 | A | не начат | — |
| WP3 | A | не начат | — |
| WP4 | C | WP4.1: `read_session`, модули C переведены, `T-A9a`/`T-A9b` зелёные; **частично** — модули B в `PENDING_NEO4J_SESSIONS` и `_read` хранилища ждут правки B (D-C-016). WP4.2: шпаргалки чужой темы → 404 (D-C-017), `T-E3` зелёный на стенде | 461 passed |
| WP5 | B | не начат | — |
| WP6 | B | не начат | — |
| WP7 | C | не начат | — |
| WP8 | A, C | WP8.7 (C): выполнено — `ops openapi` (функция `write_openapi`), снимок `backend/openapi.json`, `ErrorResponse` и SSE в схеме (D-C-015); остальное — не начато | `T-A7` + 3 теста схемы зелёные; всего 456 passed |
| WP9 | A | не начат | — |
| WP10 | C | WP10.1 стенд `system` и WP10.2 `T-E1`…`T-E10` — написаны (красные, ветка `phase-3-c-e2e-red`); WP10.3 — не начат | 6 красных, 4 зелёных на текущем коде |
| WP11 | A | не начат | — |
| WP12 | C | не начат | — |
| WP13 | B | не начат | — |
| WP14 | B | не начат | — |
| WP15 | B | не начат | — |

Гейты: G0 — открыт (ждёт аудита A и B, Этапа I всех пакетов и таблицы S11).

Швы потока C к G0:

| Шов | Что выдано | Где |
| :-- | :-- | :-- |
| S1 (часть C) | `AppResources` с полями `settings`, `engine`, `session_factory`, `neo4j`, `redis`, `llm`, `compute`; зависимости `get_resources`, `get_settings`, `get_llm`, `get_compute` (тела — Этап III) | `quack/api/resources.py` |
| S7 (часть C) | код `SERVICE_UNAVAILABLE` (503) в `ErrorCode`; обработчик ошибок Neo4j — в WP6.1 вместе с тестом | `quack/api/errors.py` |
| S8 | `llm_pressure_key(model)` → `llm:pressure:{model}`, `llm_circuit_key(model)` → `llm:circuit:{model}`; лимиты — существующий `ratelimit_key(scope, key)` | `quack/db/redis.py`, блок `# --- stream C ---` |
| S9 (каркас) | `FakeLLMProvider` (перенесён из `tests/domains/conftest.py`), `ScriptedLLMProvider` (сигнатуры; виды сбоев ждут S12 от B), `BrokenRedis`, `BrokenNeo4jDriver`, `ComputeScenario` + `scripted_compute` на `httpx.MockTransport`, `build_app_resources` | `tests/support/` |
| — | `quack/api/auth.py`: `jwt_secret_for`, `issue_access_token`, `decode_access_token`, `get_current_learner_id` (тела — Этап III) | `quack/api/auth.py` |
| — | `Settings`: блоки `# --- stream A/B/C ---`; в блоке C — параметры §6.2 пакетов C (`health_check_timeout_s`, `auth_requests_per_ip_per_min`, `learner_actions_per_min`, `turns_per_min`, `jwt_ttl_days`, `curation_defer_min`, `learner_stream_poll_ms`, `evidence_tier_weights`, `hint_step_penalty`, `hot_window_min`) | `quack/config.py` |

## Аудит

Пункты таблицы §3 на `phase-3` `694eb50`. «Подтверждено» — дефект есть в коде, его закрывает
названный пакет. Пункты потоков A и B поток C отметил по беглому чтению кода; владелец пакета
подтверждает их в своём разделе.

| # | Статус | Что в коде | Пакет / поток |
| :-- | :-- | :-- | :-- |
| A1 | подтверждено, частично | `create_app` подключает `/health/*` и `quack.session.router` (импорт внутри функции из-за цикла `session.deps → api.app`); роутеры `domains` и `artifacts` не подключены | WP1.1 / C |
| A2 | частично (C, проверить A) | `cli.py` импортирует `quack.session.lifecycle` и `quack.session.workers`; обработчики `graph.curate` (`domains/curation.py`), `session.finish_artifacts` и `artifact.refresh_cheatsheet` (`artifacts/cheatsheets.py`) в процессе worker не регистрируются | WP2.2 / A |
| A3 | подтверждено | `domains/router.py::get_llm_gateway` создаёт `LLMGateway` и `HttpLLMProvider` на запрос, если нет `app.state.llm_gateway` | WP1.3 / C, WP5.1 / B |
| A4 | частично (проверить A) | `curation.py` и `session/workers.py` кэшируют `(driver, redis, gateway)` в модульном словаре по `id(ctx.settings)` (ремедиация T-021, T-033), а не берут из `JobContext`; `artifacts` создают клиентов на задачу (`build_worker_deps`, STREAM3_NOTES риск 5) | WP2.1 / A |
| A5 | подтверждено (проверить A) | `curation.py:209–222`: обработчик сам делает `UPDATE ops.jobs SET status='queued'` | WP3.2 / A |
| A6 | подтверждено | три проверки JWT: `domains/auth.py`, `session/deps.py`, `artifacts/deps.py` | WP1.2 / C |
| A7 | подтверждено | `domains/auth.py:37–39` `JWT_TTL_DAYS`, `AUTH_RATE_LIMIT_*`; `curation.py:36–37`; `diagnostic.py:41–53` (13 констант); `artifacts/practice.py:54`, `artifacts/params.py` (`PracticeParams` вне `Settings`); `api/app.py:28` `HEALTH_CHECK_TIMEOUT_SEC`. Fail-open ограничителя при отказе Redis — в `session/deps.py` (лимит ходов) | WP1.5, WP1.6 / C |
| A8 | не проверено C | — | WP3.1 / A |
| A9 | частично (проверить A) | `cli.py::_schedule_sweep` ставит `session.sweep_inactive` из процесса worker; `purge-deleted` по расписанию нет | WP3.3 / A |
| A10 | не проверено C | — | WP3.5 / A |
| A11 | подтверждено, частично | после ремедиации `READ_ACCESS` стоит в `domains/diagnostic.py`, `graph_view.py`, `structure.py`, `session/lifecycle.py`, `session/workers.py`; без режима доступа остаются `domains/curation.py:78`, `graph/neo4j_store.py:59, 260, 293, 316`, `graph/rebuild.py:99`, `db/neo4j.py:69` (проектор и схема: запись там законна, но через общую функцию WP4.1) | WP4 / C |
| A12 | не проверено C | — | WP5 / B |
| A13 | подтверждено (проверить B) | две реализации: `session/tasks.py` и `artifacts/practice.py`; правило `independence` различается (STREAM3_NOTES риск 13) | WP6.2 / B |
| A14 | не проверено C | — | WP6.1 / B |
| A15 | подтверждено | `GET /v1/learner/stream` нет | WP7 / C |
| A16 | не проверено C | — | WP8 / A |
| A17 | подтверждено | Dockerfile, compose, CI в репозитории нет | WP9 / A |
| A18 | закрыто | конфликта нет: `manifest.json` с девятью ключами пришёл в `main` вместе с потоками | WP0 / C |
| A19 | подтверждено | `ErrorCode` в `api/errors.py` без `SERVICE_UNAVAILABLE` | WP6.1 (код — C, шов S7) |
| A20 | подтверждено | см. «Вызовы `enqueue_job`» ниже | WP11 / A |
| A21 | **подтверждено: нарушение закона 4** | `ExtractorOutput.observations: list[ExtractedObservation]` (`session/schemas.py:130`), а `ExtractedObservation` (`events/payloads.py:93–99`) содержит `strength`, `independence`, `tested_depth`, `polarity_weight`; `graph/phase_a.py:743–749` кладёт их в свидетельство как есть. Заблуждение: `misc.confidence` от модели идёт в `strength` (`phase_a.py:769`). Ярус T3 у извлекателя назначает код (`EXTRACTOR_TIER`). WP12 нужен целиком | WP12 / C |
| A22 | не проверено C | — | WP13 / B |
| A23 | не проверено C | — | WP14 / B |
| A24 | не проверено C | — | WP15 / B |
| A25 | не проверено C (для B: формат diagnostic — `{"option", "answer", "options"}`, ремедиация T-016) | — | WP6.4 / B |
| A26 | по STREAM3_NOTES §2 совпадает (проверить B) | `finish_session` ставит `kinds.session_finish_artifacts(learner, sid, {"domain_id", "topic_id"})`, его ждёт `run_finish_artifacts` | WP6.4 / B |
| A27 | конфликт документов (проверить B) | порядок по ТЗ-П2: `task_evaluated` → `task_assigned`; С-§8.5 — наоборот (PHASE2_STREAM2_NOTES, «Конфликты» п.1) | WP6.4 / B |
| A28 | подтверждено: Hypothesis-тестов нет, свойство покрыто частично | `hypothesis` нет ни в `pyproject.toml`, ни в `tests/` (`@given` не найден). Есть детерминированный тест Фазы 1 `tests/test_projector_determinism.py::test_batch_size_and_rebuild_equivalence`: один сценарий со всеми 36 типами событий, пачки по 1, одна пачка, случайные размеры 1–9 (`random.Random(7)`), полная пересборка — снимки равны; и `test_reapplying_already_applied_events_changes_nothing`. Не покрыто: случайные последовательности событий (`Т-§2.5`, ориентир) | WP10, R16 / C |
| A29 | подтверждено, частично (проверить B) | `llm/provider.py`: `/chat/completions`, `response_format`, `temperature`; `config.py`: `llm_timeout_sec = 60.0`, модели `muse-wonder` / `muse-spark` | WP5.7 / B |

### Вызовы `enqueue_job` (вход для WP11, шов S11)

| Место | Задача | В транзакции чего |
| :-- | :-- | :-- |
| `session/turns.py:554` | `turn.extract` | события хода (`turn.student_replied` … `turn.teacher_replied`) |
| `session/turns.py:569` | `session.checkpoint` (склейка) | то же, при достижении `checkpoint_every_turns` |
| `session/lifecycle.py:459` | `session.finish_artifacts` | `session.completed` |
| `session/lifecycle.py:470` | `graph.curate` | `session.completed` |
| `artifacts/cheatsheets.py:89` | `artifact.refresh_cheatsheet` | без события: явный запрос `POST …/cheatsheet/refresh` |
| `artifacts/cheatsheets.py:250` | `artifact.refresh_cheatsheet` | `session.digest_created` (обработчик `session.finish_artifacts`) |
| `cli.py:89` | `session.sweep_inactive` | без события: таймер в процессе worker |
| `ops/commands.py:68` | (проверить A) | команда ops |

`quack/domains` сам задачи не ставит: `graph.curate` ставит `finish_session`.

## Отклонения потоков от ТЗ Фазы 2

Собраны из `PHASE2_STREAM2_NOTES.md`, `PHASE2_STREAMS_1_2_REMEDIATION_NOTES.md`,
`quack/artifacts/STREAM3_NOTES.md`. Поток-владелец пакета дописывает, что сделано с каждым.

| # | Поток | Отклонение | Что делать |
| :-- | :-: | :-- | :-- |
| O1 | 2, 3 | `independence` считается по-разному: ход — `max(0.1, 1 − Σ штрафов − 0.25·(hints_used − записанные))`, `hints_used` от клиента; практика — `round(1 − max(штрафов), 4)`. `tested_depth` без LLM: 0.5 против `task.difficulty` | WP6.2 (B): одно правило в `quack/practice` |
| O2 | 2 | Порядок SSE `task_evaluated` → `task_assigned` против С-§8.5 | WP6.4 (B), A27 |
| O3 | 1, 2 | Пороги — константы модулей, а не `Settings` (`config.py` был закрыт для потоков) | WP1.6 (C) |
| O4 | 3 | `PracticeParams` в `artifacts/params.py`, а не в `Settings` | WP1.6 (C) |
| O5 | 3 | Удалённая заметка и старые версии артефактов находятся в FTS по `core.events` (STREAM3_NOTES риск 6) | Вопрос к команде Q4 |
| O6 | 3 | Склейка `artifact.refresh_cheatsheet` по `coalesce_key` теряет `session_id` (риск 7) | Вопрос к команде Q5 |
| O7 | 1 | Короткие маршруты `/v1/topics/{id}` есть, но роутер `domains` не смонтирован | WP1.1 (C) |
| O8 | 1 | `display_state` `rusty` при затухающем mastery (T-022 blocked). В ремедиации принят вариант (b): `mastery` и разблокировка пререквизитов — по затухающему значению, ворота solid/rusty — по сохранённому `pMastery` (`graph_view.py:73–84`); тест Фазы 2 не менялся | Оставить (§0.4.3 «Частые случаи», строка 1): контракт С-§ не нарушен |

## Реестр отклонений от основных документов

Копия ТЗ §2.4 с итогами аудита. Строки без изменений — как в ТЗ.

| # | Итог аудита |
| :-- | :-- |
| R1, R2, R5–R9, R11–R15 | без изменений, как в ТЗ §2.4 |
| R3 | закрывается WP5.7 (A29 подтверждён: `/chat/completions`) |
| R4 | закрывается WP5.7, WP9.2, WP10.3 |
| R10 | ждёт аудита B (порядок слотов против `Т-§10.1`) |
| R16 | подтверждено: Hypothesis-тестов нет, зависимости `hypothesis` в `pyproject.toml` нет; свойство «пачки = пересборка» проверено детерминированно на одном сценарии в памяти (A28) плюс `T-E4` на Neo4j в WP10. Добавление зависимости — класс D (§0.4.3: разрешён только `locust`), см. Q3 |

## Журнал решений

### Поток A

#### Аудит WP0, зона A (2026-10-31, `phase-3` `57eb4b9`)

| # | Статус | Что в коде | Пакет |
| :-- | :-- | :-- | :-- |
| A2 | подтверждено | `cli.py::_worker` создаёт `JobRunner` с `JOB_HANDLERS`; регистрация идёт побочным импортом (`quack.session.*`, `domains.curation`, `artifacts.cheatsheets`), явного каталога и проверки полноты нет | WP2.2 |
| A4 | подтверждено | `JobContext` несёт только `settings` и `session_factory`; `curation.py` и `session/workers.py` кэшируют драйвер, Redis и шлюз по `id(settings)`, `artifacts` строят клиентов в `build_worker_deps` | WP2.1 |
| A5 | подтверждено | `domains/curation.py:209–222` пишет в `ops.jobs` напрямую (`UPDATE … run_after`, `UPDATE … status='queued'`) | WP3.2 |
| A8 | подтверждено | `claim_next_job` не смотрит на `ops.projector_offsets`; в `ops.jobs` колонка `required_seq` уже есть (миграция `0002`), код её не пишет и не читает | WP3.1 |
| A9 | подтверждено | `cli.py::_schedule_sweep` ставит только `session.sweep_inactive`, интервал `max(60, timeout/6)` не из `Settings`; `account.purge_deleted` нет | WP3.3 |
| A10 | подтверждено | `JobRunner.run_forever` по `stop` ждёт циклы и выходит; недоделанные задачи остаются `running` до чистильщика (60 с) | WP3.5 |
| A16 | подтверждено | команды `api`, `worker`, `ops migrate/verify-schema/rebuild/replay-dlq/purge-deleted`; ролей `projector`/`workers`, `ops status`, `event-registry`, `can-rollback`, `seed-learners`, `openapi` нет | WP8 |
| A17 | подтверждено | Dockerfile, compose и `.github/` нет | WP9 |

#### Шов S11: таблица правил диспетчера

Таблица лежит в коде: `quack/jobs/dispatcher_rules.py::DISPATCHER_RULES`. Параметры задач
берутся из фабрик `kinds.py`, то есть совпадают с тем, что ставят транзакции событий сегодня.

| Событие | Задача | `required_seq` | Условие | Что правилу нужно сверх события | Заменяет |
| :-- | :-- | :-: | :-- | :-- | :-- |
| `turn.teacher_replied` | `turn.extract` | `seq` события | всегда | `seq` `turn.student_replied` того же `turn_id` (payload задачи `student_seq`) | `session/turns.py:554` |
| `turn.teacher_replied` | `session.checkpoint` | `NULL` | `turn_index − last_checkpoint_turn ≥ checkpoint_every_turns` | `last_checkpoint_turn` сессии (D-A-003) | `session/turns.py:569` |
| `session.completed` | `session.finish_artifacts` | `seq` события | всегда | `topic_id` сессии (в payload события его нет) | `session/lifecycle.py:459` |
| `session.completed` | `graph.curate` | `seq` события | всегда | `domain_id` есть в payload | `session/lifecycle.py:470` |
| `session.digest_created` | `artifact.refresh_cheatsheet` | `seq` события | `payload.topic_id` не `null` | `domain_id`, `session_id`, `topic_id` есть в payload | `artifacts/cheatsheets.py:250` |

Не правила: `artifacts/cheatsheets.py:89` (`request_cheatsheet_refresh`) — остаётся прямой
постановкой (исключение WP11.2); `cli.py:89` — планировщик WP3.3; `ops/commands.py:68` —
повтор задач из DLQ, не триггер, остаётся (зона A).

Найдено по ходу:

- `domains/learner.py:85` отменяет задачи ученика прямым `UPDATE ops.jobs` при удалении
  аккаунта; `T-Q9` запрещает SQL с `ops.jobs` в продуктовых пакетах. См. Q-A1.
- Снимок задачи в `ops.dlq` (`queue._retry_or_dlq`) не содержит `required_seq`; `ops replay-dlq`
  поставит задачу без него. Исправляется в WP3.1 (зона A).

### D-A-001. Параметры `Settings` потока A

Класс B. В блок `# --- stream A ---` добавлены все параметры §6.2, которые не отнесены к B и C:
`process_role`, `quack_version`, `api_workers`, `api_graceful_shutdown_s`,
`worker_shutdown_grace_s`, `scheduler_tick_s`, `session_sweep_interval_s`,
`purge_deleted_interval_s`, `daily_budget_defer_jitter_s`, `dispatcher_concurrency`,
`dispatcher_batch_size`, `dispatcher_restart_pause_s`. `process_role`, `quack_version` и
`api_*` ТЗ ни за кем не закрепляет; взял поток A, потому что их потребляют CLI и compose
(§7.3). `consumer_poll_ms` уже есть в `Settings`.

### D-A-002. Необязательный `required_seq` и поля `JobContext` до Этапа III

Класс C (спорное место: §6.5 и WP3.1 требуют обязательный `required_seq`, но вызовы фабрик
стоят в `quack/session`, `quack/artifacts`, зоны B и C, и правятся только в WP11.4).
Выбрано: `required_seq` — именованный аргумент фабрик `turn_extract`,
`session_finish_artifacts`, `artifact_refresh_cheatsheet`, `graph_curate` со значением по
умолчанию `None`; обязательным он становится в PR WP11.4, где вызовы всё равно переписываются.
Тем же способом, что D-C-003 и D-C-005, `JobContext` получил поля `engine`, `neo4j`, `redis`,
`llm`, `compute` со значением `None`: обработчики Фазы 2 и их тесты собирают контекст из двух
полей. Значения по умолчанию снимаются в WP2.1. Тест `T-D3` проверит `required_seq` по факту.

### D-A-003. Условие постановки `session.checkpoint` в диспетчере

Класс C, вынесено в сигнал. Сегодня условие считается в транзакции хода по состоянию сессии
(`last_checkpoint_turn`). Правило диспетчера должно воспроизвести его, читая то же состояние
из реляционных таблиц (граф и модели диспетчер не трогает, WP11.1). Откуда берётся
`last_checkpoint_turn`, уточню в Этапе III WP11; если окажется, что его нет вне транзакции
хода, нужен вопрос к владельцу.

### D-A-004. Исключения очереди живут в `queue.py`

Класс B. `JobDeferred` и `JobFailedPermanently` определены в `quack/jobs/queue.py` (ядро
очереди, §6.5) и реэкспортируются из `quack/jobs/kinds.py`, как требует шов S2.

### D-A-005. WP2 без правок зон B и C; проверка полноты в `cli` — вместе с WP3.3

Класс C, вынесено в сигнал. WP2 разделён по владельцу файлов (поручение разработчика потока A):
сделана часть в зоне A, перевод обработчиков в `domains/curation.py`, `session/workers.py`,
`artifacts/workers.py` на `JobContext` (пересечение №2, §7.3) отложен до уведомления B и C.
Сделано: `quack.runtime` (`create_job_context`, `close_job_context`, `register_all_handlers`,
`check_handlers_complete`), `JobRunner(context=…)`, `cli worker` строит контекст один раз и
регистрирует обработчики через `register_all_handlers` (этим закрыт A2: обработчики
`graph.curate`, `session.finish_artifacts`, `artifact.refresh_cheatsheet` теперь есть в
процессе worker).
`T-Q7` остаётся красным: в `JOB_KINDS` есть `account.purge_deleted`, его обработчик — WP3.3. Вызов
`check_handlers_complete` в `cli worker` с выходом `job_handlers_mismatch` включается в WP3.3
вместе с обработчиком, иначе до него процесс `worker` не стартовал бы. Утверждение `T-Q7` не
ослаблено.

#### Этап I и красные тесты потока A к G0

Сигнатуры: `JobSpec.required_seq`, `JobDeferred`, `JobFailedPermanently`, `JOB_KINDS`,
`KIND_LLM_ROLES`, фабрика `account_purge_deleted`, поля `JobContext`, `quack.runtime`
(`create_job_context`, `close_job_context`, `register_all_handlers`, `check_handlers_complete`,
`JobHandlersMismatchError`), `dispatcher_rules.rules_for`. Красные тесты: `T-Q1`, `T-Q2`,
`T-Q3`, `T-Q4`, `T-Q7`, `T-Q8`, `T-Q9` (`tests/test_jobs_phase3.py`).

#### Вопросы потока A

| # | Вопрос | Кому |
| :-- | :-- | :-- |
| Q-A1 | `domains/learner.py:85` отменяет задачи ученика прямым SQL по `ops.jobs`. Для `T-Q9` нужна функция ядра очереди (зона A, например `cancel_learner_jobs`) и вызов её из `domains` — правка зоны C одной строкой в WP11.4 | C |
| Q-A2 | D-A-002 и D-A-003 — подтвердить | владелец |

### Поток B

#### Аудит WP0, зона B (2026-10-31, `phase-3` `fca744f`)

Проверки зоны на `fca744f`: `ruff check` — passed, `ruff format --check` — 172 files already
formatted, `pyright` — 0 errors. `pytest` в этом прогоне не запускался: контейнеры
`quack-test-*` общие с идущей параллельно сессией потока C; последний полный прогон — у C
(410 passed, раздел «Статус»).

| # | Итог | Что в коде | Пакет |
| :-- | :-- | :-- | :-- |
| A12 | подтверждено | `LLMGateway.__init__`: один `asyncio.Semaphore(llm_semaphore_limit)` на шлюз; `_call` превращает `ProviderUnavailableError` (в том числе 429) и таймаут в `LLMUnavailableError` без повторов; флага давления, размыкателя, `teacher_fallback` нет (роль есть только в `llm_role_models`) | WP5.1–5.5 |
| A13 | подтверждено, расхождений больше, чем в O1 | Ход: `session/tasks.py::_check` (choice — `option`/`answer`; math — `ref["sympy"] or ref["answer"]`, режим `ref["mode"]`), затем **вердикт самого учителя** (`TeacherOutput.evaluated_is_correct/score`, `evaluator="llm"`, actor `teacher@1`), затем `turns.py::_code_evaluation` — эвристика по `pedagogical_move` (`give_feedback` → верно; ветки `remedy_misconception`, `praise`, `scaffold`, `probe` недостижимы: этих значений нет в `Literal`). Практика: `artifacts/practice.py::_evaluate` — choice → compute (`sympy`, `check_mode`) → `evaluator@1` → сравнение строк. Различаются также `skill_scores.tested_depth` (`task.difficulty` против `PARAMS.compute_tested_depth`) и `triggered_misconceptions` (в ходе всегда пусто). Основа для WP6.2 — каскад практики (ТЗ WP6.2) | WP6.2 |
| A14 | подтверждено | `session/context.py::build_turn_context` → `read_consistent_slice` → `_watermarked_read` (`store.dump`, `read_applied_seq`): ошибки драйвера не перехватываются ни там, ни в `turns.py::open_turn`; ход падает до записи событий | WP6.1 |
| A22 | **подтверждено: нарушение закона 10** | `session/workers.py::run_session_checkpoint` подаёт `checkpointer@1` прошлую сводку (`previous_summary`) и прошлые `open_threads` | WP13 целиком |
| A23 | подтверждено | Выходы: `TeacherOutput.focus_skill_ids: list[UUID]`, `assign_task_id`, `concept_explained.topic_id`, `skills: list[SkillRef]` (`skill_id: UUID`); `ExtractorOutput` — `SkillRef.skill_id`, `ExtractedMisconception.topic_id`; `CuratorOutput` — `skill_id`, `topic_id`, `source_id`, `target_id`; `EvaluatorOutput.skill_scores[].skill_id`. Входы: учитель — `skill_id`, `misconception_id`, `task_id`, `turn_id`, `related_task_id` в JSON-блоках; извлекатель — `topic_id`, `skill_id`; оценщик — `target_skills_json` с `skill_id`. Без id: `StructurerOutput` (slug), `DiagnosticOutput`, `ArtifactGenOutput`, `TaskGenOutput`. Манифеста хода нет | WP14 |
| A24 | подтверждено | В `turns.py` проверки текста хода нет | WP15 |
| A25 | **расхождение с таблицей WP6.4** | Пишут: `task_gen@1` (правило 5 промпта) — math `{sympy, latex, check_mode, variables?, hints}`, choice `{option, hints}`, open/code `{answer, hints}`; `diagnostic.py::_reference_solution` — `{option, answer, options}`. Читают: ход — `sympy`/`answer`, `mode`; практика — `sympy`, `check_mode`, `option`, `hints`. Ключей таблицы WP6.4 (`expression`, `mode`, `tolerance`, `key_points`, `sample_answer`) в коде нет; `hints` (лестница подсказок практики) в таблице нет. Ход и практика расходятся между собой (`mode` против `check_mode`). См. D-B-001 | WP6.4 |
| A26 | частично совпадает | `finish_session` ставит payload `{"domain_id", "topic_id"}` **без `session_id`**; обработчик `run_finish_artifacts` берёт `session_id` из `lock_key` (`session:{id}:finish`), поэтому стороны совместимы. `cheatsheet_ready` публикует следующая задача цепочки (`artifact.refresh_cheatsheet`, payload `{"domain_id", "session_id"}`) в `session:events:{session_id}`; `stream.py` пересылает его как есть. До контракта WP6.4 не хватает `session_id` в payload | WP6.4 (B — постановка; после WP11 — правило диспетчера A) |
| A27 | совпадает с решением WP6.4 | `turns.py`: `token` → `task_evaluated` → `task_assigned` → `turn_completed` → `done`, одинаково в обычном ходе и в повторе по ключу идемпотентности. Остаётся `T-H7` и запись порядка в текст `С-§8.5` | WP6.4 |
| A29 | **подтверждено целиком** | `llm/provider.py::HttpLLMProvider`: `POST /chat/completions`, `response_format`, передаётся `temperature` (0.0 из шлюза), нет `reasoning` и `max_output_tokens`, нет `store`, `prompt_cache_key`; ответ — `choices[0].message.content`, `status: incomplete`/`finish_reason` не проверяются; `retry-after` — только число секунд, дата HTTP не разбирается; `x-ratelimit-*` не читаются; 400 и 5xx — один класс `ProviderUnavailableError`. Схема — `model_json_schema()` без препроцессора. `llm_timeout_sec = 60.0` (поднимать не нужно). Модели — условные: `llm_default_model = "muse-spark"`, `llm_role_models = {"teacher": "muse-wonder", "teacher_fallback": "muse-spark"}`, остальные роли — на модель по умолчанию. Окружение — `LLM_API_KEY`, `LLM_API_BASE_URL`. WP5.7 нужен целиком | WP5.7 |
| R10 | расхождение с `Т-§10.1`, записано | `teacher@1` — один шаблон одним сообщением `user`; блоки по порядку: режим и тема, `rolling_summary` (сводка модели), `recent_turns_json`, `skills_slice_json`, `misconceptions_json`, `available_tasks_json`, `task_evaluation_json`, реплика ученика. Против `Т-§10.1`: нет слотов 2 (схема выхода уходит только в формат ответа), 3 (профиль), 4 (домен и цели), 6 (план), 8 (материалы), 9 (метки); слот 7 — сырой JSON свойств узлов с id и числами вместо строк кода (`Т-§10.2`); вместо строк эпизодов — сводка модели (`Т-§10.3`, закрывает WP13); блок доступных задач в `Т-§10.1` не назван. Порядок «разговор → память» совпадает. По §2.3 строка 14 Фаза 3 добавляет только маркер памяти (WP6.1) и таблицу меток (WP14) | R10 → следующая фаза |

Прочее, найденное аудитом:

- `KIND_LLM_ROLES` (WP5.3) совпадает с кодом обработчиков: `turn.extract` → `extractor`,
  `session.checkpoint` → `checkpointer`, `session.finish_artifacts` и
  `artifact.refresh_cheatsheet` → `artifacts`, `graph.curate` → `curator`. Других ролей
  обработчики не вызывают (`role=` по `quack/**`).
- §6.1 \[ПРОВЕРИТЬ в WP0\]: у `ops.llm_ledger.status` CHECK нет (`0001_initial_schema.py`),
  статус `incomplete` расширения ограничения не требует. Колонка `cached_tokens` уже есть
  (D-B-003).
- WP15 \[ПРОВЕРИТЬ в WP0\]: события «показать решение» в коде нет; есть только
  `task.hint_given` со ступенью `hint_level` 1–3 (`PracticeParams.max_hint_level = 3`).
  Признак просьбы для WP15 — подсказка ступени 3 по активной задаче.
- Строгая схема (WP5.7, к G1): свободный словарь — только `TaskGenOutput.reference_solution:
  dict[str, Any]`; закрывается моделью `ReferenceSolution` (WP6.4).
- `llm_semaphore_limit` (WP5.1) станет неиспользуемым после WP5.1; отметка — в WP5.1.
- Скрипта пересборки `manifest.json` (§7.3, зона B) в репозитории нет — появится в первом
  пакете, который меняет промпт или манифест.

### D-B-001. Формат `reference_solution`: таблица WP6.4 против кода
- Класс: C
- WP / место: WP6.4 п.1; `prompts/task_gen/v1.md` (правило 5), `domains/diagnostic.py`,
  `session/tasks.py`, `artifacts/practice.py`
- Контекст: аудит A25. Таблица WP6.4 \[КОНТРАКТ\] задаёт math `{expression, mode, variables,
  tolerance}`, open `{key_points, sample_answer}`. Код и промпт `task_gen@1` пишут и читают
  math `{sympy, latex, check_mode, variables, hints}`, open `{answer, hints}`; лестница
  подсказок практики читает `hints`, которого в таблице нет.
- Варианты: (1) `ReferenceSolution` по таблице WP6.4 плюс необязательные `hints`
  (расширение) и `latex` для показа; запись в банк переводится на неё, читатели — тоже;
  меняется правило 5 промпта `task_gen@1`; (2) `ReferenceSolution` по фактическим ключам кода,
  таблица WP6.4 — отклонение в реестр; (3) модель принимает оба набора и нормализует при записи.
- Решение: (1).
- Почему: §0.4.3 п.2 — таблица WP6.4 — контракт ТЗ, код расходится с ней; `hints` и `latex` —
  только совместимое расширение. Промпты всё равно правятся до G1 (§7.6). Настоящих данных в
  банке нет (хакатон), старые события `task.generated` есть только в тестах.
- Обратимость: одна модель `quack/practice/schemas.py` и правило 5 промпта.
- Тесты: `T-H8` (а).
- Требует подтверждения владельцем: да. Отдельно — какой формат `reference_solution` уже
  заложен в готовящийся промпт `task_gen` (промпт пишет владелец).

### D-B-002. Версия отрисовщика в `session.checkpointed`
- Класс: C
- WP / место: WP13; `events/payloads.py::SessionCheckpointed`, `session/workers.py`
- Контекст: WP13 — «поле версии в payload события получает значение `renderer@1`». В payload
  `SessionCheckpointed` поля версии нет (`session_id`, `up_to_turn_index`, `rolling_summary`,
  `open_threads`, `key_observations`); версия сейчас — `actor = "checkpointer@1"`. Добавить
  поле — изменить payload-схему (§0.4.4 п.2, класс D).
- Варианты: (1) `renderer@1` пишется в `actor` события; (2) новое поле payload — класс D;
  (3) версия не пишется.
- Решение: (1).
- Почему: §0.4.4 п.2 — схема события не меняется; `actor` уже несёт версию у всех событий
  моделей (`teacher@1`, `extractor@1`), `Т-§10.6` требует только версию отрисовщика.
- Обратимость: одна строка в обработчике.
- Тесты: `T-C2` (payload и actor детерминированы).
- Требует подтверждения владельцем: да

### D-B-003. `cached_input_tokens` при существующей `cached_tokens`
- Класс: C
- WP / место: WP5.7, §6.1; `ops.llm_ledger`
- Контекст: §6.1 добавляет `cached_input_tokens BIGINT NULL`, а в `ops.llm_ledger` уже есть
  `cached_tokens INT NOT NULL DEFAULT 0`, которую шлюз заполняет из
  `usage.prompt_tokens_details.cached_tokens`.
- Варианты: (1) добавить колонку по §6.1, заполнять обе одним значением; (2) не добавлять,
  писать `usage.input_tokens_details.cached_tokens` в `cached_tokens`; (3) переименовать —
  запрещено (§0.4.4 п.3).
- Решение: (1).
- Почему: §0.4.3 п.2 — имя колонки задано контрактом §6.1, расширение совместимое; старый код
  и запросы по `cached_tokens` не ломаются.
- Обратимость: убрать колонку из `0002` до слияния в `main`.
- Тесты: `T-L10`.
- Требует подтверждения владельцем: да (колонка-дубль — на выбор владельца между (1) и (2))

#### Этап I потока B к G0 (ветка `phase-3-b-stage1-llm`)

| Шов / пакет | Что выдано | Где |
| :-- | :-- | :-- |
| S1 (часть B) | конструктор `LLMGateway` без изменений; `LLMGateway.aclose()` закрывает HTTP-клиент провайдера (`aclose` провайдера или его `httpx.AsyncClient`) — для `AppResources.close` | `quack/llm/gateway.py` |
| S3 | `pressured_models(redis) -> frozenset[str]`, `set_pressure_flag`, `parse_retry_after`, `low_remaining`, ключи размыкателя (сигнатуры); `LLMRateLimitedError(LLMUnavailableError)`; `LLMUnavailableError.reason` (`unavailable`, `timeout`, `rate_limited`, `bad_request`, `circuit_open`); `LLMSchemaError.reason` (`schema`, `incomplete`, `label`); `LLMBudgetExceededError.scope` уже был | `quack/llm/pressure.py`, `quack/llm/errors.py` |
| S7 (часть B) | `resolve_degraded_mode(...)` и порядок `DEGRADED_MODE_PRIORITY` (§4.4); маркер `MEMORY_UNAVAILABLE_MARKER` (WP6.1) | `quack/session/turn_policy.py` |
| S10 | пакет `quack/practice`: `ReferenceSolution` (`MathReference`, `ChoiceReference`, `OpenReference` по таблице WP6.4 + `hints`, `latex`, D-B-001), `parse_reference_solution`, `evaluate_answer`, `independence_from_hints`, `pick_task` (сигнатуры) | `quack/practice/` |
| S12 | `ProviderRequest` + `reasoning_effort`, `max_output_tokens`, `prompt_cache_key`; `ProviderResponse` + `reasoning_tokens`, `output_items`, `incomplete`, `headers`; `RATE_LIMIT_HEADERS`; ошибки `ProviderRateLimitedError(retry_after_sec, headers, body)`, `ProviderBadRequestError(body)`, `ProviderIncompleteError(partial_text)`; `ProviderUnavailableError` — 5xx, таймаут, связь. Новые поля — со значениями по умолчанию, старый код работает | `quack/llm/provider.py`, `quack/llm/errors.py` |
| WP5.7 | `to_strict_json_schema`, `StrictSchemaError(field_path)`; `ResponsesClient(base_url, api_key, *, http_client)` (сигнатуры) | `quack/llm/strict_schema.py`, `quack/llm/responses_client.py` |
| WP5.4, WP6.5 | `generate_structured(..., *, prompt_role=None, reasoning_effort=None)`; до реализации непустые значения → `NotImplementedError` | `quack/llm/gateway.py` |
| WP6.5 | `teacher_reasoning_effort(...)` | `quack/session/turn_policy.py` |
| WP13 | `render_checkpoint`, `episode_lines`, `RENDERER_VERSION = "renderer@1"` | `quack/session/checkpoint_render.py` |
| WP14 | `LabelMap` (`build`, `label`, `resolve`, `table`), `UnknownLabelError`; `TurnManifest`, `write_turn_manifest` | `quack/llm/labels.py`, `quack/session/manifest.py` |
| WP15 | `spoiler_sentences`, `remove_sentences` | `quack/session/spoiler.py` |
| §6.2 | все параметры B в блоке `# --- stream B ---` (`llm_concurrency*`, `llm_role_reasoning`, `llm_role_max_output_tokens`, `max_output_tokens_cap`, `llm_role_timeout_s`, повторы, давление, допуск, размыкатель, `session_budget_exempt_roles`, `sse_keepalive_s`, окно разговора, метки, защита от спойлера); `meta_api_key`, `meta_api_base_url` читают `META_API_*`, запасные — `LLM_API_*`. Значения `llm_role_models` (ID моделей) меняются в WP5.7 | `quack/config.py` |
| D-C-005 | `_set_presence` без Redis возвращается сразу; проверено: с аннотацией `redis: Redis \| None` в `AppResources` pyright всего дерева — 0 errors | `quack/session/lifecycle.py` |

Не входит в Этап I B и нужно от других: миграция `0002_phase3` (колонки `ops.llm_ledger` и
таблица `ops.turn_manifests` — поля по §6.1 и WP14.2, D-B-003) — владелец `quack/db`,
`migrations/` не назван в §7.3, см. сигнал; заполнение `ScriptedLLMProvider` по S12 — C (S9).

### D-B-004. SDK OpenAI и `jsonschema` не входят в зависимости
- Класс: D
- WP / место: WP5.7 (адаптер `ResponsesClient` поверх `AsyncOpenAI`, `Т-§11.5`), `T-L8`,
  `T-L9` (проверка экземпляра через `jsonschema`); `pyproject.toml`
- Контекст: контракт WP5.7 и `Т-§11.5` называют `AsyncOpenAI` из официального OpenAI SDK, а
  `T-L9` — проверку схемой `jsonschema`. Ни `openai`, ни `jsonschema` нет в `pyproject.toml`;
  §0.4.3 разрешает добавить только `locust`, остальное — класс D.
- Варианты для владельца: (1) разрешить `openai` (основные зависимости) и `jsonschema`
  (группа `dev`); (2) `ResponsesClient` на `httpx` без SDK — отход от `Т-§11.5`, в реестр §2.4;
  `T-L9` — только `model_validate_json`.
- Не блокирует: Этап I и `to_strict_json_schema` (до G1). Блокирует: адаптер WP5.7 после G1,
  часть `T-L9` про `jsonschema`. До ответа `ResponsesClient` — сигнатура без импорта SDK.
- Требует решения владельца: да

### Поток C

### D-C-001. Имена рабочих веток потоков
- Класс: C
- WP / место: §7.4, §1.3 п.4; ветки git
- Контекст: §7.4 требует интеграционную ветку `phase-3` и рабочие ветки `phase-3/a-<тема>`,
  `phase-3/b-<тема>`, `phase-3/c-<тема>`. Git хранит ветки как файлы ссылок: при
  `refs/heads/phase-3` создать `refs/heads/phase-3/…` нельзя (`fatal: cannot lock ref
  'refs/heads/phase-3/c-wp0-notes': 'refs/heads/phase-3' exists`). На `origin` то же самое.
- Варианты: (1) рабочие ветки `phase-3-<поток>-<тема>`; (2) интеграционная ветка
  `phase-3/main`, рабочие — как в ТЗ; (3) рабочие ветки `phase3/<поток>-<тема>`.
- Решение: (1). Интеграционная ветка остаётся `phase-3`, рабочие — `phase-3-a-<тема>`,
  `phase-3-b-<тема>`, `phase-3-c-<тема>`.
- Почему: §0.4.3 п.2 и п.4. Имя `phase-3`, на которое ссылаются §1.3, §7.4, §10 и CI, не
  меняется; меняется один символ в шаблоне рабочих веток.
- Обратимость: переименовать рабочие ветки (`git branch -m`), интеграционная не затронута.
- Тесты: нет (процесс).
- Требует подтверждения владельцем: да — **подтверждено 2026-10-31, вариант (1)**

### D-C-002. Проверки `compute/` идут в Linux
- Класс: B
- WP / место: WP0 п.2, DoD п.2; `compute/`
- Контекст: `quack_compute/checker.py` использует `multiprocessing.get_context("forkserver")`,
  `signal.SIGALRM`, `signal.setitimer` — API только для Linux; сервис работает в контейнере
  (Д-§5.1, WP9.1). На Windows-хосте pyright даёт 7 ошибок, pytest падает на сборке тестов.
- Варианты: (1) запускать pyright с `--pythonplatform Linux`, а pytest — в Linux-контейнере;
  (2) добавить в код ветки для Windows.
- Решение: (1). Код не меняется. Команды — в разделе «Вход»; CI (WP9.3) работает на Linux.
- Почему: §0.4.3 п.4 — минимальность; вариант (2) — код вне скоупа.
- Обратимость: не затрагивает код.
- Тесты: `compute/tests/test_compute_service.py` (5 passed в Linux).
- Требует подтверждения владельцем: нет

### D-C-003. Две `AppResources` до Этапа III WP1.3
- Класс: B
- WP / место: WP1.3, `quack/api/resources.py`, `quack/api/app.py`
- Контекст: шов S1 нужен к G0, а перевод шести мест создания `AppResources` и всех
  импортов `quack.api.app.AppResources` — это реализация WP1.3 (Этап III). Если сразу
  поменять поля старого класса, дерево `phase-3` станет красным.
- Варианты: (1) новый контракт в `quack/api/resources.py`, старый класс живёт до Этапа III;
  (2) сразу перевести все места.
- Решение: (1). Итог Этапа III: класс один — `quack.api.resources.AppResources`; `quack.api.app`
  только реэкспортирует его и `get_resources` до WP1.4, где импорты переводятся.
- Почему: §7.4 — `phase-3` всегда зелёная; §0.4.3 п.4.
- Обратимость: удалить новый модуль.
- Тесты: `T-A6` (после Этапа III в коде один `AppResources`).
- Требует подтверждения владельцем: нет

### D-C-004. Токен ученика без строки `auth.users`
- Класс: C
- WP / место: WP1.2, `quack/api/auth.py`;
  `tests/session/test_auth_and_stream_resilience.py::test_learner_without_auth_user_row_stays_valid`
- Контекст: контракт WP1.2 — `get_current_learner_id` проверяет «пользователь существует,
  `deleted_at IS NULL`, tombstone нет»; так же уже работает `quack/domains/auth.py`.
  Ремедиация потока 2 (T-034) сделала обратное: токен ученика без строки `auth.users`
  принимается, потому что фикстуры `tests/session` не создают пользователя, и закрепила
  это тестом. После WP1.2 этот тест падает, а в перечне §8.9 его нет.
- Варианты: (1) контракт WP1.2: такой токен → 401; фикстуры `tests/session` (через
  `tests/support/`) создают строку `auth.users`; утверждение теста меняется на `401`;
  (2) принимать токен без пользователя, что расходится с контрактом WP1.2 и с `T-A3`.
- Решение: (1).
- Почему: §0.4.1 уровень 2 (контракт ТЗ Фазы 3 выше ТЗ-П2 и ремедиации); §0.4.3 п.1 —
  токен несуществующего пользователя не должен давать доступ. Проверка не слабее: тест
  проверяет то же место (ученик без `auth.users`) с более строгим ожиданием.
- Обратимость: одна ветка условия в `get_current_learner_id` и одно утверждение теста.
- Тесты: `T-A3`, изменённый `test_learner_without_auth_user_row_stays_valid` (Этап III,
  запись в «Правки тестов»).
- Требует подтверждения владельцем: да — **владелец 2026-10-31 передал решение исполнителю
  («прими решение сама»); принят вариант (1)**

### D-C-005. Тип `AppResources.redis` до правки зоны B
- Класс: B
- WP / место: WP1.3, `quack/api/resources.py`; `quack/session/lifecycle.py::_set_presence`
- Контекст: §4.2 задаёт `redis: Redis | None`. С этим типом pyright находит в зоне B
  `resources.redis.set(...)` без проверки на `None` (`lifecycle.py:188`): без Redis старт
  сессии упадёт с `AttributeError`, который `contextlib.suppress(RedisError, OSError)` не ловит.
  Правка `lifecycle.py` не входит в исключения §7.3.
- Варианты: (1) оставить аннотацию `Redis` с пометкой, код C уже обрабатывает `None`;
  поток B добавляет проверку, после чего C меняет аннотацию; (2) поправить `lifecycle.py` самой.
- Решение: (1).
- Почему: §0.4.4 п.11 — чужой файл не правится; одно место отката (аннотация).
- Обратимость: аннотация поля в `quack/api/resources.py`.
- Тесты: pyright (после правки B — с типом `Redis | None`).
- Требует подтверждения владельцем: нет. **Закрыто 2026-10-31:** B добавил проверку в `_set_presence` (PR #12); аннотация — `Redis | None`, `close` вызывает `LLMGateway.aclose()`.

### D-C-006. Пользователи для токенов в тестах сессий
- Класс: B
- WP / место: WP1.2, `tests/support/auth.py`, `tests/session/fixtures.py::client`
- Контекст: после D-C-004 токен без строки `auth.users` получает 401. Тесты потока 2
  выпускают токены для случайных `uuid4()` (свой ученик и «чужой» ученик для проверок 404).
- Варианты: (1) хук `httpx` в фикстуре `client` создаёт строку пользователя для субъекта
  токена (как регистрация), тела тестов не меняются; (2) дописать создание пользователя в
  тело каждого теста.
- Решение: (1). Существующая строка (например, с `deleted_at`) не меняется.
- Почему: §8.9 — меняются фикстуры, а не утверждения; §0.4.3 п.4.
- Обратимость: убрать хук из фикстуры.
- Тесты: весь `tests/session` (утверждения прежние), `test_learner_without_auth_user_row_is_rejected`.
- Требует подтверждения владельцем: нет

### D-C-007. Роутеры смонтированы в WP1.2
- Класс: B
- WP / место: WP1.2 / WP1.1, `quack/api/app.py::create_app`
- Контекст: `T-A3` по определению требует одного приложения с `domains`, `session`,
  `artifacts`; монтирование — часть WP1.1, которая по §7.1 идёт после красных `T-E*`.
- Варианты: (1) смонтировать три роутера сейчас, остальное WP1.1 (`resources=`, `T-A1`,
  порядок маршрутов, `/healthz`, канал WP7) — в WP1.1; (2) собирать приложение в тесте вручную.
- Решение: (1). Аутентификация на уровне роутера: `domains` — в самом роутере (публичны только
  `register`, `login`), `artifacts` — в самом роутере, `session` — через
  `include_router(..., dependencies=...)`, без правки `quack/session/router.py`.
- Почему: §0.4.3 п.5 — `T-A3` проверяет собранное приложение, а не ручную сборку.
- Обратимость: строки `include_router` в `create_app`.
- Тесты: `T-A2`, `T-A3`.
- Требует подтверждения владельцем: нет

### D-C-008. `T-A2` читает маршруты из схемы OpenAPI
- Класс: B
- WP / место: `tests/test_app_assembly.py`
- Контекст: в FastAPI 0.162 `include_router` хранит роутеры лениво (`_IncludedRouter`),
  `app.routes` не перечисляет их маршруты; разворачивание — приватный API.
- Варианты: (1) `app.openapi()["paths"]`; (2) приватные `effective_candidates()`.
- Решение: (1). Все маршруты `/v1` есть в схеме (`include_in_schema` нигде не выключен).
- Почему: публичный API; та же схема — основа `T-A7`.
- Обратимость: одна функция теста.
- Тесты: `T-A2`.
- Требует подтверждения владельцем: нет

### D-C-009. `POST /v1/topics/{topic_id}/cheatsheet/refresh` без ключа идемпотентности
- Класс: B
- WP / место: `T-E2`, `tests/e2e/endpoints.py::NO_IDEMPOTENCY_KEY`
- Контекст: `T-E2` перечисляет эндпоинты без `idempotency_key`: `register`, `login`,
  `DELETE /v1/learner`, `heartbeat`. У `cheatsheet/refresh` ключа нет и по ТЗ-П3 §3
  (`CheatsheetRefreshRequest {domain_id}`); событий он не пишет, задача склеивается по `coalesce_key`.
- Варианты: (1) внести в исключения с причиной; (2) добавить ключ в контракт эндпоинта.
- Решение: (1).
- Почему: §0.4.3 п.2 — контракт `С-§`/ТЗ-П3 не меняется; повтор не создаёт событий.
- Обратимость: строка в `NO_IDEMPOTENCY_KEY`.
- Тесты: `T-E2`.
- Требует подтверждения владельцем: нет

### D-C-010. В `T-E1` `p_mastery` навыка читается из карточки темы
- Класс: B
- WP / место: `T-E1`, `tests/e2e/test_journey_and_laws.py`
- Контекст: `T-E1` проверяет `p_mastery > 0` «в карте `GET /v1/domains/{id}/graph`», но ответ
  карты (`DomainGraphResponse`) содержит только темы; навыки с `p_mastery` есть в карточке темы
  `GET /v1/domains/{id}/topics/{topic_id}` (ТЗ-П1 §2.4).
- Варианты: (1) проверить, что карта отвечает 200, а `p_mastery` взять из карточки темы;
  (2) проверять `mastery` темы в карте.
- Решение: (1): та же величина навыка, условие не слабее.
- Обратимость: одна выборка в тесте.
- Тесты: `T-E1`.
- Требует подтверждения владельцем: нет

### D-C-011. Миграция `0002_phase3` пишет поток C
- Класс: B
- WP / место: §6.1; `backend/migrations/versions/0002_phase3.py`
- Контекст: §6.1 разрешает одну миграцию `0002` с полями трёх пакетов: `ops.jobs.required_seq`
  (WP3.1, A), `ops.dispatcher_offsets` (WP11, A), `ops.turn_manifests` (WP14.2, B), колонки
  `ops.llm_ledger` (WP5.7, B). Каталог `migrations/` в §7.3 не назван; слой данных
  (`quack/db/**`) — у C. Поток B попросил миграцию через сигнал; она нужна к G0.
- Варианты: (1) одну миграцию пишет C по §6.1 и WP14.2; (2) каждый поток — свою (запрещено:
  одна `0002`, §0.4.4 п.3).
- Решение: (1). Только расширение: колонка `NULL`, две новые таблицы, три колонки `NULL`.
  Ограничения CHECK на `ops.llm_ledger.status` нет — статус `incomplete` (WP5.2) правки не
  требует (закрыт «[ПРОВЕРИТЬ в WP0]» §6.1). D-B-003: добавлена `cached_input_tokens`,
  `cached_tokens` остаётся (вариант (1) B, ждёт владельца). В `ops.turn_manifests`
  `renderer_version` и `reasoning_effort` допускают `NULL` (ход в деградации).
- Почему: §0.4.4 п.3; §0.4.3 п.2.
- Обратимость: `downgrade` удаляет всё добавленное; проверено `downgrade → upgrade` на тестовой базе.
- Тесты: весь набор (412 passed на `head` = `0002_phase3`); `T-Q1`, `T-L10`, `T-M*` — у A и B.
- Требует подтверждения владельцем: нет

### D-C-012. Сквозные тесты, зелёные до реализации (Q8, DoD п.3)
- Класс: C
- WP / место: WP10.2, DoD п.3; `T-E4`, `T-E6`, `T-E7`, `T-E9`
- Контекст: DoD п.3 требует, чтобы каждый тест §8 «был красным до реализации». Эти четыре
  проверяют поведение, которое уже есть в Фазах 1–2 (законы 1, 9, блокировка хода, деградация
  без LLM), и проходят сразу. §0.1 для такого случая разрешает закрыть пункт как «уже
  выполнено», а §8.9 даёт способ доказать, что тест не пустой: он должен падать на заведомо
  неверной реализации того же свойства.
- Варианты: (1) записать «зелёный на входе» и заменить красный прогон проверкой на
  неверной реализации (временно сломать свойство, увидеть падение, вернуть, записать); (2)
  подгонять тест, чтобы он падал (запрещено §0.4.4 п.4); (3) ничего не делать — DoD п.3
  останется невыполненным.
- Решение: (1). Проверка делается на Этапе IV WP10 и записывается в «Красные прогоны» по тесту:
  что ломалось и как упал тест.
- Почему: §0.1, §8.9 (проверка «тест не ослаблен»); §0.4.4 п.10 — пункт DoD не объявляется
  выполненным без проверки.
- Обратимость: запись в заметках.
- Тесты: `T-E4`, `T-E6`, `T-E7`, `T-E9`.
- Требует подтверждения владельцем: да — **владелец 2026-10-31 передал решение исполнителю;
  принят вариант (1)**

### D-C-013. Шов с B: перенос практики (WP6.2), схемы `task_gen`, `evaluator`, `curator`
- Класс: B (договорённость двух потоков на шве S4/S10, §0.4; B записывает со своей стороны)
- WP / место: WP6.2, WP14.1, §7.3 п.3; `quack/artifacts/practice.py`, `quack/artifacts/schemas.py`,
  `quack/artifacts/params.py`, `quack/domains/schemas.py::CuratorOutput`
- Контекст: B переносит выбор задачи и проверку ответа из `quack/artifacts/` в `quack/practice/`
  (§7.3 п.3) и к G1 замораживает схемы выходов (S4): `TaskGenOutput.reference_solution` —
  типизированная `ReferenceSolution` (S10, D-B-001), поля с метками в `EvaluatorOutput` и
  `CuratorOutput` (WP14.1). Схемы лежат в файлах C, а их потребители — в переносимом коде
  (`task_gen`, `evaluator`) и в коде C (`curator`).
- Решение:
  1. **Заморозка.** C не правит `quack/artifacts/practice.py` до слияния PR WP6.2. WP1.4 (импорты)
     и WP1.6 (пороги) потока C этот файл обходят; при переносе B сразу импортирует
     `AppResources` из `quack.api.resources`.
  2. **`TaskGenOutput`, `EvaluatorOutput`** переезжают в `quack/practice/` вместе со своим
     единственным потребителем, в том же PR WP6.2 (часть переноса §7.3 п.3); там
     `reference_solution: ReferenceSolution`, поля с метками — по S4. Из
     `quack/artifacts/schemas.py` B их удаляет в том же PR. Ревью — C.
  3. **Пороги практики** из `PracticeParams` (`default_target_difficulty`, `max_hint_level`,
     `hint_penalties`, `compute_tested_depth`) B переносит в свой блок `Settings`
     (`# --- stream B ---`); пороги поиска (`search_*`) остаются у C — блок C, WP1.6.
     `quack/artifacts/params.py` удаляет C, когда в нём не останется полей.
  4. **`CuratorOutput`** остаётся у C: B к G1 называет поля с метками (S4,
     `PROMPT_CONTRACTS.md`), C меняет модель и перевод меток в id в `quack/domains/curation.py`
     через `quack.llm.labels.LabelMap`.
- Почему: §0.4.3 п.4 — схема живёт рядом с единственным кодом, который её читает; один PR на
  перенос вместо двух согласованных правок в разных зонах; §7.3 п.3.
- Обратимость: схемы возвращаются в `quack/artifacts/schemas.py` одной правкой импорта.
- Тесты: тесты практики Фазы 2 (`tests/artifacts/test_practice_engine.py`) после переноса без
  изменения утверждений; `T-M*` (метки) у B.
- Требует подтверждения владельцем: нет

### D-C-014. Шов с B: реэкспорт `quack.api.app`, роутер `session` и ограничитель ходов (WP1.1, WP1.4, WP1.5)
- Класс: B
- WP / место: WP1.1, WP1.4, WP1.5, §7.3 п.2; `quack/api/app.py`, `quack/session/deps.py`, `backend/tests/test_app_assembly.py`
- Контекст: B владеет `quack/session/*` и переносит практику (D-C-013). В `quack/session/` пять файлов (`context`, `lifecycle`, `router`, `stream`, `turns`) и `quack/artifacts/practice.py` импортируют `AppResources`/`get_resources` из `quack.api.app`. Чтобы разорвать импортный цикл и смонтировать роутер `session` в `create_app`:
  1. В `quack/api/app.py` оставлен временный реэкспорт `AppResources`, `get_resources`, `create_app` из `quack.api.resources` до слияния правок B; в `tests/test_app_assembly.py` эти файлы перечислены в `PENDING_IMPORTS` теста `T-A6`.
  2. В `quack/session/deps.py` поправлено тело `enforce_turn_rate_limit`: вместо устаревшего вызова фазы 2 вызывается общий ограничитель `check_turn_rate_limit(resources, learner_id)`.
  3. В `tests/test_app_assembly.py` (T-A1) в `PENDING_ROUTES` добавлены маршруты WP7 (`/v1/learner/stream`) и WP8.3 (`/healthz`, `/health/live`, `/health/ready`), которые будут смонтированы в соответствующих пакетах (отклонение O9).
  4. Пороги диагностики (`diagnostic.*`), поиска (`search_*`) и курации (`curation_min_evidence_for_promotion`) перенесены в `Settings` под блоком `# --- stream C ---` (WP1.6).
  5. По §8.9: устаревшие проверки fail-open (где при отказе Redis лимит пропускал запросы) удалены / заменены проверками in-memory fallback (T-A5, T-E8), так как fail-open устранён согласно ТЗ §2.3 п.3.
- Почему: §0.4.3 п.4; разделение ответственности потоков B и C на стыке роутеров и ресурсов.
- Обратимость: реэкспорт удаляется после перевода файлов B на `quack.api.resources`.
- Тесты: `T-A1`, `T-A4`, `T-A5`, `T-A6`, `tests/test_ratelimit.py`, `tests/test_thresholds_in_settings.py` (все зелёные).
- Требует подтверждения владельцем: нет

### D-C-015. `ops openapi`: где живёт `write_openapi` и правила кодов ошибок (WP8.7)
- Класс: B
- WP / место: WP8.7, §4.1; `quack/api/openapi.py`, `quack/api/app.py::write_openapi`, `backend/openapi.json`
- Контекст: (1) поток A подключил `ops openapi --out PATH` к `quack.api.openapi.write_openapi(path)`
  (ветка `phase-3-a-wp2-pr`). Но `write_openapi` собирает приложение (`create_app`), а
  `quack.api.openapi` — слой «основание API», которому импорт `quack.api.app` запрещён (§4.1, `T-A6`).
  (2) WP8.7 требует `ErrorResponse` для 401, 404, 409, 422, 429, 503 «там, где они возможны»;
  точного перечня по эндпоинтам ТЗ не даёт, а роутеры `session` — зона B.
- Варианты: (1) `write_openapi(path: Path) -> None` в `quack.api.app` (сборка), сериализация и
  правила — в `quack.api.openapi`; A меняет одну строку импорта; (2) оставить в `quack.api.openapi`
  с импортом внутри функции — нарушает §4.1; (3) правила в каждом роутере — правка зоны B.
- Решение: (1). Ошибки описываются одним местом, `install_openapi(app)`, по правилам для `/v1/*`:
  503 — всегда (общие обработчики `LLM_DEGRADED` и шов S7 действуют на всё приложение); 401 —
  кроме `register`/`login`; 404 — путь с параметром; 409 — POST/PATCH/DELETE (ключ идемпотентности,
  `TURN_LOCKED`, конфликты состояния); 429 — POST/PATCH/DELETE, кроме heartbeat (WP1.5); 422 —
  есть параметры или тело. Стандартные `HTTPValidationError`/`ValidationError` FastAPI заменены на
  `ErrorResponse`: так приложение и отвечает (`VALIDATION_ERROR`). SSE-эндпоинты (`turns`, `events`,
  далее `learner/stream` WP7) описаны как `text/event-stream`. `/health/live`, `/health/ready`
  получили модели ответа (`HealthLive`, `HealthReady`), поведение не менялось.
  Правила — верхняя граница: клиент обязан уметь обработать описанный код.
- Почему: §0.4.4 п.11 и §4.1 (чужой файл и слои не нарушаются); §0.4.3 п.3 — одно место отката.
- Обратимость: таблицы и `_error_statuses` в `quack/api/openapi.py`; перегенерировать снимок.
- Тесты: `T-A7` `test_openapi_snapshot_is_current`, `test_write_openapi_is_deterministic`,
  `test_openapi_documents_every_response`, `test_sse_endpoints_document_event_stream`.
- Правка зоны A: в `quack/cli.py` (PR #17 потока A) импорт заменён на
  `from quack.api.app import write_openapi`, снят `# pyright: ignore[reportMissingImports]` — по
  просьбе A в его сообщении о WP8.7; больше `cli.py` не менялся.
- Требует подтверждения владельцем: нет

### D-C-016. WP4.1: модули потока B вне `read_session` и чтение внутри `Neo4jGraphStore`
- Класс: B
- WP / место: WP4.1, §7.3; `tests/test_contracts.py::PENDING_NEO4J_SESSIONS`, `quack/graph/neo4j_store.py::_read`
- Контекст: `quack/session/workers.py`, `quack/session/lifecycle.py` (B) и `quack/artifacts/practice.py`
  (заморожен до WP6.2, D-C-013) открывают сессии на драйвере сами. Кроме того, `session/workers.py`
  передаёт в `Neo4jGraphStore` обёртку `_ReadOnlyDriver`, которая сама добавляет
  `default_access_mode`; если `_read` хранилища перейдёт на `read_session`, обёртка упадёт с
  `TypeError` (аргумент передан дважды). Правки зоны B нет среди исключений §7.3.
- Варианты: (1) список `PENDING_NEO4J_SESSIONS` в `T-A9a` (как `PENDING_IMPORTS` в D-C-014) и проверка
  устаревших записей; `_read` переводится после того, как B уберёт обёртку; (2) поправить файлы B.
- Решение: (1). B переводит свои модули на `read_session`, удаляет обёртку и записи из списка;
  C затем переводит `_read` на `read_session` (одна строка). `neo4j_store` — разрешённый писатель
  WP4.1, поэтому `T-A9a` от этого не зависит.
- Почему: §0.4.4 п.11; §7.4 — `phase-3` зелёная.
- Обратимость: строка в `_read`, записи списка.
- Тесты: `T-A9a`, `test_neo4j_session_exceptions_are_still_needed`.
- Требует подтверждения владельцем: нет

### D-C-017. WP4.2: принадлежность темы проверяется по журналу событий
- Класс: B
- WP / место: WP4.2; `quack/artifacts/store.py::require_topic_of_learner`, `quack/artifacts/cheatsheets.py`
- Контекст: `GET /v1/topics/{topic_id}/cheatsheet` для чужой темы отвечал `200 null`,
  `POST .../cheatsheet/refresh` ставил задачу на чужую тему (`T-E3`, STREAM3_NOTES риск 9). Тем в
  Postgres нет отдельной таблицы; в Neo4j они есть, но проекция может отставать (закон 9).
- Варианты: (1) тема ученика — та, что создана в его `structure.proposed` (`topics`) или
  `structure.edited` (`added_topics`); запрос к `core.events` по `learner_id`; (2) узел `Topic` в
  Neo4j — зависимость от Neo4j и отставания проектора у чтения, которое раньше её не имело.
- Решение: (1). Шпаргалка есть → тема ученика, проверка не нужна; нет шпаргалки и темы → 404;
  своя тема без шпаргалки → `200 null`, как раньше (ТЗ-П3 §3).
- Почему: §0.4.1 уровень 1 (журнал — источник истины); §0.4.3 п.1.
- Обратимость: функция `require_topic_of_learner`.
- Тесты: `tests/artifacts/test_artifact_isolation.py`; `T-E3` — зелёный при прогоне на этом коде
  (стенд `phase-3-c-e2e-red` с двумя правками стенда, см. «Вопросы»/WP10).
- Требует подтверждения владельцем: нет

## Заблокировано

_(пусто)_

## Красные прогоны

| WP | Команда | Итог |
| :-- | :-- | :-- |
| WP1.2–1.3 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_app_assembly.py` (коммит `fd8d31b`) | 2 failed: `T-A2` — `GET /v1/domains is not mounted`; `T-A3` — `POST /v1/auth/register` → 404 |
| WP1.1, 1.4–1.6 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_app_assembly.py tests/test_ratelimit.py tests/test_thresholds_in_settings.py tests/domains/test_curation_thresholds.py` (коммит `f00f41d`) | 20 failed, 5 errors, 6 passed (T-A1 route order/mount, T-A4/T-A5 ratelimit stub, T-A6 imports, local fallback, curation deferral, thresholds in Settings) |
| WP10.2 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/e2e` (ветка `phase-3-c-e2e-red`) | 6 failed, 4 passed. Падают: `T-E1` — нет колонки `ops.jobs.required_seq` (A, WP3.1/WP11); `T-E2` — `structure/propose` с тем же ключом и другим телом → 200, ждали 409 (**дефект C**, `domains`); `T-E3` — `GET /v1/topics/{id}/cheatsheet` чужой темы → `200 null`, ждали 404 (**дефект C**, `artifacts`, STREAM3_NOTES риск 9); `T-E5` — обработчики задач не берут шлюз из `JobContext`, извлечение идёт мимо `ScriptedLLMProvider` (A, WP2.1); `T-E8` — ограничитель fail-open без Redis (C, WP1.5); `T-E10` — нет `purge_deleted_interval_s` и планировщика (A, WP3.3). Зелёные уже на текущем коде: `T-E4`, `T-E6`, `T-E7`, `T-E9` — проверяют поведение Фаз 1–2; DoD п.3 («были красными») для них не выполним, проверка «тест падает на заведомо неверной реализации» (§8.9) — не делалась |
| WP2, WP3.1–3.2, WP3.4 (Этап II) | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_jobs_phase3.py` | 7 failed, 0 passed: `T-Q1` (захват игнорирует `required_seq`), `T-Q2` (склейка не пишет `required_seq`), `T-Q3`/`T-Q4` (`JobDeferred` идёт по ветке повтора, `attempt` растёт), `T-Q7` (`register_all_handlers` — `NotImplementedError`), `T-Q8` (бюджет не маршрутизируется), `T-Q9` (нарушения в `domains`, `session`, `artifacts`). Остальной набор: 423 passed |
| WP8.7 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_app_assembly.py -k "openapi or sse_endpoints"` (коммит `1eec6bd`) | 4 failed: `T-A7` — нет `backend/openapi.json`; `write_openapi` — `NotImplementedError`; схема — нет `ErrorResponse`, 422 описан `HTTPValidationError`; SSE-эндпоинты — `application/json` без модели |
| WP4.1 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_neo4j_read_session.py tests/test_contracts.py -k "neo4j or read_session"` (коммит `b692007`) | 3 failed, 1 passed: `read_session` — `NotImplementedError` (2); `T-A9a` — сессии вне `read_session` в 6 модулях C (`artifacts.cheatsheets`, `artifacts.search`, `domains.curation`, `domains.diagnostic`, `domains.graph_view`, `domains.structure`) |
| WP4.2 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/artifacts/test_artifact_isolation.py` | 1 failed: шпаргалка чужой темы → `200 null`, ждали 404. `T-E3` на стенде `phase-3-c-e2e-red`: то же падение |

## Правки тестов

| Тест | Что изменено | Причина |
| :-- | :-- | :-- |
| `tests/session/test_auth_and_stream_resilience.py::test_learner_without_auth_user_row_stays_valid` → `…_is_rejected` | ожидание `200/404` → `401 UNAUTHORIZED`; запрос идёт обычным клиентом без хука D-C-006 | D-C-004 (класс C, решено по поручению владельца). Проверка не слабее: то же место, более строгое ожидание; на старой реализации (`current_learner_id` потока 2) тест падает |
| там же, `test_events_stream_without_redis_is_empty_sse`, `test_relay_ends_cleanly_on_redis_error` | конструктор `AppResources(...)` → `dataclasses.replace(res, redis=...)` | новый набор полей S1; утверждения не менялись |
| фикстуры `tests/domains/conftest.py`, `tests/session/fixtures.py`, `tests/artifacts/conftest.py` | ресурсы — `build_app_resources`; роутеры не монтируются повторно; удалены переопределения `get_llm_gateway`/`get_compute_client` (`artifacts`) и запасной разбор JWT (`domains`); хук пользователей в `client` (`session`) | §8.1, §8.9 (фикстуры и импорты); D-C-006 |
| `tests/domains/conftest.py`, `tests/session/fixtures.py`, `tests/artifacts/conftest.py`, `tests/test_infra.py` | `create_app(settings, connect=False)` → `create_app(settings, resources=build_app_resources(...))` | WP1.1: убран `connect=False`, введён явный `resources=`; тесты сохраняют владение пулами |
| `tests/conftest.py` | добавлена autouse-фикстура `_clear_local_rate_limits` с вызовом `LOCAL_LIMITS.clear()` | Изоляция тестов ограничителя с локальным in-memory счётчиком при отказе Redis (WP1.5) |
| `tests/test_infra.py::test_cli_parser` | добавлены проверки `ops openapi --out` | §8.9 п.3: тест дополняется новой командой, старые утверждения сохранены |
| `tests/domains/test_curation_keys_and_actor.py::_FakeDriver.session` | принимает `**_config` | WP4.1: сессии открываются через `read_session(driver)` с `default_access_mode`; фикстура, утверждения не менялись |
| `tests/domains/test_graph_view_fixes.py::test_graph_view_opens_every_neo4j_session_with_read_access` | шпион считает и сессии, открытые через `read_session`, вызванную из `graph_view.py` | WP4.1 перенёс место открытия сессии в `quack/db/neo4j.py`; утверждения (≥ 2 сессии, все READ) те же. Проверка «не ослаблен»: при `read_session` без READ тест падает |

## Вопросы к команде

Конфликты ТЗ §2.3 строки 1–22 закрыты решениями ТЗ; здесь — открытые вопросы.

| # | Вопрос | Ссылка |
| :-- | :-- | :-- |
| Q1 | ~~Подтвердить имена рабочих веток `phase-3-<поток>-<тема>`~~ — закрыто: подтверждено 2026-10-31 | D-C-001 |
| Q2 | Порядок SSE `task_evaluated` / `task_assigned` | A27, O2, WP6.4 (B) |
| Q3 | R16: Hypothesis-тестов нет (есть детерминированный тест Фазы 1 на одном сценарии), а добавить `hypothesis` исполнитель не вправе (класс D). Варианты: (а) оставить как есть до тестировщиков, срок по R16; (б) разрешить `hypothesis` в группе `dev` и добавить property-тест на случайных последовательностях событий в памяти | R16, A28 |
| Q4 | FTS по `core.events` находит удалённые заметки и старые версии артефактов: исключать или оставить как журнал | O5 |
| Q5 | Склейка `artifact.refresh_cheatsheet` теряет `session_id` | O6 |
| Q6 | Прод: `JWT_SECRET` не короче 32 байт (dev-секрет 20 байт) | STREAM3_NOTES §2 |
| Q8 | ~~DoD п.3: `T-E4`, `T-E6`, `T-E7`, `T-E9` зелёные до реализации~~ — закрыто: D-C-012 | D-C-012 |
| Q7 | ~~Подтвердить: токен ученика без строки `auth.users` → 401, тест потока 2 меняется~~ — закрыто 2026-10-31: решение передано исполнителю, принят вариант (1) | D-C-004 |

## DoD

| # | Пункт (ТЗ §10) | Статус |
| :-- | :-- | :-- |
| 1 | Проверки `backend/` | на `694eb50` зелёные; финальный прогон — в конце фазы |
| 2 | Проверки `compute/` | ruff зелёный; pyright и pytest — в Linux (D-C-002); финальный прогон — в конце фазы |
| 3–21 | — | не начаты |
