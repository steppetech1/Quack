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
| WP7 | C | выполнено: `GET /v1/learner/stream` (Redis, опрос без Redis, keepalive), снимок `openapi.json` обновлён (D-C-021, D-C-022) | `T-H4` + keepalive + 401 зелёные |
| WP8 | A, C | WP8.3 (C): выполнено — `GET /healthz` только по Postgres, `T-A8`; WP8.7 (C): выполнено — `ops openapi` (функция `write_openapi`), снимок `backend/openapi.json`, `ErrorResponse` и SSE в схеме (D-C-015); остальное — не начато | `T-A7` + 3 теста схемы зелёные; всего 456 passed |
| WP9 | A | не начат | — |
| WP10 | C | WP10.1 стенд `system` и WP10.2 `T-E1`…`T-E10` — написаны (красные, ветка `phase-3-c-e2e-red`); WP10.3 — не начат | 6 красных, 4 зелёных на текущем коде |
| WP11 | A | не начат | — |
| WP12 | C | выполнено: S5/S6, `turn.extracted` v2, вес по коду в свёртке, `rules@2` (D-C-019, D-C-020) | `T-W1`…`T-W7` зелёные; 492 passed |
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

Закрыто ответом потока B. `last_checkpoint_turn` лежит в колонке
`core.sessions.last_checkpoint_turn` (по `session_id` события); её обновляет синхронная
проекция в той же транзакции, что `session.checkpointed` (`events/projections.py`,
`GREATEST`), отставания проектора нет. Условие правила диспетчера:
`payload.turn_index` события `turn.teacher_replied` − `last_checkpoint_turn` ≥
`checkpoint_every_turns`. Диспетчер читает только реляционные таблицы, граф не трогает.

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

### D-A-006. WP3.2 для `graph.curate` — вместе с WP2.1, `T-Q2` и разбиение файла тестов

Класс C. (1) Последний пункт WP3.2 (`graph.curate` при `presence` бросает `JobDeferred` вместо
прямого `UPDATE`) правит `domains/curation.py`, зону C. Исключение №2 из §7.3 разрешает там
только перевод на `JobContext`, поэтому пункт делается в одном PR с WP2.1, после слияния WP4
потока C (порядок договорён с C). Тест отложения `graph.curate` (§8.9 п.2) меняется там же.
Ядро (`defer_job`, обработка в `JobRunner`) готово и проверено `T-Q3`, `T-Q4`.
(2) В `T-Q2` второй ученик взят с тем же `domain`; ключ склейки `curate:{domain}` слил задачи
двух учеников. Исправлен тест (свой `domain`), утверждение не менялось: `NULL`, затем 4 → 4.
(3) Файл `tests/test_jobs_phase3.py` в `phase-3` содержит только зелёные `T-Q1`–`T-Q4`, `T-Q8`.
Красные `T-Q7` (ждёт обработчик `account.purge_deleted`, WP3.3) и `T-Q9` (ждёт WP2.1 в зонах B и
C и WP11.4, плюс Q-A1) лежат в `tests/test_jobs_phase3_pending.py` на ветке
`phase-3-a-pending-red` и перейдут в основной файл, когда позеленеют (§7.4).

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
- Требует подтверждения владельцем: нет — **2026-11-01 владелец передал решение исполнителю; принят вариант (1)**: таблица WP6.4 помечена \[КОНТРАКТ\], `hints` и `latex` — совместимое расширение. Строку банка собирает код (D-B-010), текст промпта `task_gen` не меняется. Переход — пакет WP6.4 (затрагивает `domains/diagnostic.py` потока C).

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
- Требует подтверждения владельцем: нет — **2026-11-01 решено исполнителем по поручению владельца: вариант (1)** (§0.4.4 п.2 — схема события не меняется).

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
- Требует подтверждения владельцем: нет — **2026-11-01 решено исполнителем по поручению владельца: вариант (1)**; имя колонки задано контрактом §6.1, колонка уже в миграции `0002_phase3`.

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

#### Красные прогоны потока B

| WP | Команда | Итог |
| :-- | :-- | :-- |
| WP5.7 (препроцессор), WP14.1, §4.4 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_llm_runtime.py tests/llm/test_labels.py tests/session/test_phase3_hot_path.py` (ветка `phase-3-b-first-red`) | 46 failed, 0 passed: `T-L9` (11), `T-M1` (3), `T-H3` (32) — все `NotImplementedError` |
| WP6.2 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/session/test_phase3_hot_path.py -k parity` (до переноса) | 2 failed: `T-H2[math]`, `T-H2[choice]` — `independence` ход 1.0 ≠ практика 0.5 |
| WP6.1 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/session/test_phase3_hot_path.py -k outage` (до реализации) | 1 failed: `T-H1` — ход 2 падает с `ServiceUnavailable` из `read_consistent_slice` |

`T-L9` здесь — только структура схемы. Часть «минимальный экземпляр проходит `jsonschema` и
`model_validate_json`» ждёт генератора `meta-mock` (WP10.3, C) и D-B-004. `T-H2` (WP6.2)
пишется в начале WP6.2: ему нужно одно приложение, где ход и `submit` идут через подменённые
шлюз и compute.

#### Состояние пакетов потока B

| WP | Статус | Тесты |
| :-- | :-- | :-- |
| WP5.7 | препроцессор `to_strict_json_schema` — выполнено; адаптер `ResponsesClient` — не начат (после G1, D-B-004) | `T-L9` структура: 11 зелёных (`TaskGenOutput` без свободного словаря — D-B-010) |
| WP14.1 | `LabelMap` — выполнено; метки в контексте и выходах ролей — не начаты: имена полей связаны с текстом промптов (`teacher@1` называет `focus_skill_ids`, `assign_task_id`, `topic_id`), правятся вместе с промптами | `T-M1`: 3 зелёных |
| WP6.1 | часть B выполнена: `resolve_degraded_mode`; ход при ошибке связи Neo4j строится на пустом срезе + оверлей сессии, `degraded_mode = "memory_unavailable"`, маркер в слоте памяти (D-B-008). Часть C (шов S7) — обработчик ошибок Neo4j → `503 SERVICE_UNAVAILABLE` в `quack/api` — не начата | `T-H3` (32) зелёные; `T-H1` — ход зелёный, красное только последнее утверждение (`GET /v1/domains/{id}/graph` → 500 вместо 503, ждёт C) |
| WP6.5 | функция `teacher_reasoning_effort` выполнена: `high` — первый ход сессии и два последних `task.evaluated` сессии с `is_correct = false`, у которых есть общий навык в `skill_scores`; `teacher_fallback` — всегда `low`. Ситуации `Т-§12.9` «смена цели», «построение плана», «разбор доказательства» в срезе Фазы 3 не возникают (плана сессии и доказательств нет — R9, R11). Передача уровня в шлюз, манифест и ledger — после параметра `reasoning_effort` шлюза (WP5) и WP14.2 | `T-H9` табличная часть (10) зелёная; часть с ходом через `ScriptedLLMProvider` — вместе с WP5/WP14.2 |
| WP6.2 | выполнено: выбор задачи и каскад проверки — в `quack/practice/` (`evaluation.py`, `selection.py`); ход и `submit` вызывают `evaluate_answer`; `TaskGenOutput`, `EvaluatorOutput` — в `quack/practice/schemas.py` (D-C-013 п.2); пороги практики — в блоке B `Settings` (п.3) | `T-H2` (2) зелёные; тесты `tests/session`, `tests/artifacts` — 102 passed |

Договорённость с C по WP6.2 и S4 — D-C-013 (раздел C), со стороны B принята целиком:
перенос и удаление `TaskGenOutput`/`EvaluatorOutput` из `quack/artifacts/schemas.py` — в этом
PR; поля `PracticeParams` практики удалены из `quack/artifacts/params.py`, поля `search_*`
остались у C; поля с метками `CuratorOutput` B называет к G1 в `PROMPT_CONTRACTS.md`.
`reference_solution` в `TaskGenOutput` пока `dict` — типизация ждёт D-B-001 и промпта
`task_gen` (D-B-007).

### D-B-006. Одна проверка ответа для хода и практики (WP6.2)
- Класс: C
- WP / место: WP6.2; `quack/practice/evaluation.py`, `quack/session/turns.py`; тесты
  `tests/session/test_turn_submission_fields.py`, `test_turn_evaluation.py`,
  `test_open_task_evaluation.py`
- Контекст: аудит A13. Ход потока 2 проверял ответ иначе, чем практика потока 3:
  `independence = max(0.1, 1 − Σ штрафов − 0.25·(hints_used − записанные))` по подсказкам
  **только из оверлея** (подсказки, уже применённые проектором, ход не видел — `T-H2` на старом
  коде: ход 1.0, практика 0.5); открытую задачу оценивал сам учитель
  (`TeacherOutput.evaluated_*`, `evaluator="llm"`, actor `teacher@1`); при отказе compute —
  эвристика по `pedagogical_move`; `tested_depth = task.difficulty`; свой текст `feedback`.
  WP6.2: одна реализация, за основу — ТЗ-П3. Пять утверждений тестов Фазы 2 проверяют старое
  поведение, в §8.9 их нет.
- Варианты: (1) ход вызывает каскад практики целиком: подсказки — из журнала сессии,
  `independence = round(1 − max(штрафов), 4)` (`Т-§7.4`, решение 1 ТЗ-П3), `hints_used`
  клиента пишется в `task.submitted`, но не штрафует; открытый ответ и отказ compute —
  `evaluator@1`; утверждения пяти тестов — под единое правило; (2) оставить в ходе свои
  правила — нарушает WP6.2 и `T-H2`.
- Решение: (1).
- Почему: §0.4.1 уровень 2 — WP6.2 и `T-H2` — контракт ТЗ Ф3; §0.4.3 «Частые случаи», строка
  «Два потока сделали одно и то же по-разному»; закон 4 — вердикт открытого ответа выносит
  проверяющая роль, а не учитель в том же вызове.
- Правки тестов (проверка не слабее — то же место, то же свойство; каждая падает на прежней
  реализации хода):
  1. `test_turn_submission_fields::test_compute_independence_with_hints_used_and_floor` →
     `test_independence_counts_the_highest_logged_hint`: та же функция расчёта, правило
     «наибольшая ступень»; на правиле «сумма» падает (`[0.6, 0.5]` → 0.4).
  2. там же, `…writes_response_time_sec_and_independence`: `independence` 0.5 → 1.0
     (подсказки клиента без события не штрафуют); `hints_used == 2` в `task.submitted` остался.
  3. там же, `…combines_overlay_hint_penalties_and_hints_used`: 0.2 → 0.7.
  4. `test_turn_evaluation::test_math_task_evaluated_in_turn_via_compute`: `feedback`
     `"correct"` → `FEEDBACK_CORRECT` практики; `independence` 0.75 → 1.0 (как п.2).
  5. `test_open_task_evaluation`: вход — ответ `evaluator@1` вместо полей учителя
     (утверждения `evaluator == "llm"`, `score`, `is_correct` прежние); при отказе compute
     `evaluator` `"compute"` → `"hybrid"` (каскад ТЗ-П3), добавлено `is_correct is True`.
- Не меняется: поля `TeacherOutput.evaluated_*` остаются в схеме (имена связаны с промптом,
  см. WP14.1), код их больше не читает.
- Обратимость: функции хода в `turns.py` (один вызов `evaluate_answer`).
- Тесты: `T-H2`; пять правок выше.
- Требует подтверждения владельцем: нет — **2026-11-01 решено исполнителем по поручению владельца: вариант (1)** (WP6.2 и `T-H2` — контракт).

### D-B-007. `reference_solution` в `TaskGenOutput` остаётся `dict` до ответа по D-B-001
- Класс: B
- WP / место: WP6.2 / WP6.4, `quack/practice/schemas.py::TaskGenOutput`; `T-L9`
- Контекст: D-C-013 п.2 предлагает типизировать `reference_solution` в том же PR. Формат ждёт
  D-B-001 (класс C), а правило 5 промпта `task_gen@1` описывает ключи — текст промпта ведёт
  владелец. WP6.2 требует переноса без изменения поведения.
- Решение: перенос без изменения типа; типизация — отдельным PR вместе с промптом, до G1.
  До этого `T-L9[TaskGenOutput]` красный (ветка B, не `phase-3`).
- Обратимость: одна аннотация поля.
- Требует подтверждения владельцем: нет

### D-B-005. `title` и `default` в строгой схеме
- Класс: B
- WP / место: WP5.7, `quack/llm/strict_schema.py`
- Контекст: `model_json_schema()` пишет `title` у каждого поля и `default` у необязательных.
  `Т-§11.1` перечисляет правила строгого режима, но о этих ключах молчит; в строгом режиме
  все свойства обязательны, и `default` модель не применяет.
- Варианты: (1) отбрасывать `title` и `default`, остальные ключи (`enum`, `format`,
  `minimum`, `maxLength`, `description`) сохранять; (2) передавать схему pydantic как есть.
- Решение: (1).
- Почему: §0.4.3 п.1 — меньше ключей, которые сервер может не принять в строгом режиме;
  `description` нужен модели (`Т-§22.2`). Принимает ли Meta `format`, `minimum`,
  `maxLength` в строгом режиме — \[ПРОВЕРИТЬ\] прогоном `meta_smoke.py` (DoD п.6a).
- Обратимость: константа `_DROPPED_KEYS`.
- Тесты: `T-L9`.
- Требует подтверждения владельцем: нет

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
- Требует решения владельца: нет — **2026-11-01 решено исполнителем по поручению владельца: вариант (1)**. `AsyncOpenAI` назван в `Т-§11.5`, `jsonschema` — в описании `T-L9`; `backend/CLAUDE.md` разрешает зависимости, названные в документах или задаче. `openai` — основные зависимости, `jsonschema` — группа `dev`. WP5.7 (адаптер) разблокирован.

### D-B-008. Ход при недоступном Neo4j: какие ошибки ловятся и куда идёт маркер (WP6.1)
- Класс: B
- WP / место: WP6.1; `quack/session/context.py` (`NEO4J_CONNECTION_ERRORS`,
  `build_turn_context`), `quack/session/turns.py` (переменные `teacher@1`)
- Контекст: WP6.1 называет «ошибку связи драйвера Neo4j (недоступен сервер, истекла сессия,
  таймаут)» и маркер `memory temporarily unavailable` в слоте памяти (`Т-§20.7`). Конкретные
  классы драйвера и место маркера в шаблоне не названы; текст промпта ведёт владелец.
- Решение: (1) ловятся `ServiceUnavailable` (с подклассами), `SessionExpired`,
  `ConnectionAcquisitionTimeoutError` и `TimeoutError`; прочие ошибки Neo4j (синтаксис,
  ограничения) по-прежнему роняют ход. Срез — пустой `InMemoryGraph` с `applied_seq = 0`,
  поэтому оверлей накладывает все события сессии и инвариант «задача сессии не назначается
  повторно» сохраняется. (2) Маркер подставляется кодом как значение обеих переменных слота
  памяти — `skills_slice_json` и `misconceptions_json`; текст `teacher/v1.md` не меняется.
  `teacher_fallback` в `resolve_degraded_mode` пока `False` (роль — WP5.4).
- Почему: §0.4.3 п.1 — минимальная правка без изменения текста промпта; классы — те, что
  драйвер 6.x бросает при потере связи (`ServiceUnavailable`, `SessionExpired` уже считаются
  временными в `graph/worker.py::TRANSIENT_ERRORS`).
- Обратимость: кортеж `NEO4J_CONNECTION_ERRORS`; две строки в `turns.py`.
- Тесты: `T-H1`, `T-H3`.
- Требует подтверждения владельцем: нет. Сообщить: маркер в слотах памяти — часть контракта
  `teacher@1` (§7.6), войдёт в `PROMPT_CONTRACTS.md`.

### D-B-009. Промпты из пакета владельца: как они ложатся в репозиторий; `evaluator@1`
- Класс: B
- WP / место: §7.6; `quack/prompts/*`, `quack/prompts/build_manifest.py`,
  `quack/practice/schemas.py::EvaluatorOutput`, `quack/practice/evaluation.py`, `Settings`
  (`evaluator_verdict_scores`)
- Контекст: владелец передал промпты Фазы 3 (пакет `phase3_handover`, 31.10): слой 1
  (доктрина) проверен на стенде, слой 2 — заглушки `⟦…⟧`, тестовые контракты выходов — в
  `scenarios/<роль>/_test_contract.md`. Указание владельца: промпты подгоняются под код,
  критичных правок в них не вносить; решения принимает B.
- Решение:
  1. Слой 1 переносится побайтно; заменяется только раздел `## Output contract` (по тестовому
     контракту роли и схеме кода) и добавляется `## Input blocks` с переменными `{{ }}`
     загрузчика. Имена файлов — роли кода: `teacher/v6` → `teacher/v1.md`,
     `evaluator/v2` → `evaluator/v1.md`, `task_generator/v3` → `task_gen/v1.md`,
     `extractor/v2` → `extractor/v2.md` (WP12.1). До первой выкатки `@1` правится на месте
     (§7.6 п.3). `manifest.json` пересобирает `uv run python -m quack.prompts.build_manifest`
     (прежние хеши скрипт воспроизводит).
  2. Параметры вызова из README пакета (`temperature`, `muse-spark-1.0`, лимиты) не берутся:
     действуют WP5.7 и `Т-§1.3`.
  3. LaTeX в `reply_text`: `$…$` в строке, `$$…$$` отдельной строкой (в документах
     разделители не названы).
  4. `teacher@1`: пункт `task_markup` пакета не переносится (разметка заданий — §2.2).
     Поля `evaluated_*` остаются в схеме, промпт требует `null`. Маркер памяти описан в
     блоке «What we know about the learner».
  5. `evaluator@1`: схема выхода — контракт промпта (`items`, `verdict`, `confidence`,
     `misconception`, `note`). Код переводит вердикт в поля `task.evaluated` (payload не
     меняется): `is_correct = verdict == "correct"`, `score` — по
     `Settings.evaluator_verdict_scores` (1.0 / 0.5 / 0.0), `skill_scores` — всем целевым
     навыкам с этим `score` и `compute_tested_depth` (как вердикт кода, решение 8 ТЗ-П3),
     `feedback` — тексты кода. `unclear` — «не проверено» (закон 8): тот же
     детерминированный fallback, что при сбое LLM. Рубрика — `key_points` эталона
     (`R1…`), без них эталон — один пункт R1. Известные заблуждения — имена задачи с
     метками `M1…`; `new: …` — новое имя; неизвестная метка отбрасывается с логом.
- Почему: §0.4.3 п.1 и указание владельца; закон 4 — чисел от модели (`score`,
  `skill_scores`) больше нет.
- Правки тестов (ожидания по новому контракту; проверяемое свойство то же):
  `tests/artifacts/test_practice_engine.py::test_submit_falls_back_to_evaluator_on_parse_error`
  (вход — вердикт `incorrect` + `new:` заблуждение; ожидаются поля, переведённые кодом,
  `misconception_id = null`), `tests/session/test_open_task_evaluation.py` (два входа;
  `score` 0.9 → 1.0 для `correct`), ответчик по умолчанию в `tests/support/llm.py` (файл C,
  исключение §7.3 — уведомить C).
- Обратимость: схема и одна функция `evaluate_answer`.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-010. `task_gen@1`: схема — контракт промпта, строку банка собирает код
- Класс: B (затрагивает файл C `quack/artifacts/practice.py::next_task` — исключение §7.3)
- WP / место: `quack/practice/schemas.py::TaskGenOutput`, `quack/practice/generation.py`,
  `Settings.task_gen_target_level`
- Решение: выход — контракт промпта (`statement`, `options` с `catches`, `answer`,
  `solution`, `tests`, `method_named`, `variant_of`, `form`, `pair_task`) плюс одно поле
  слоя 2 `hints` (три ступени; без него не работает лестница подсказок практики). Код:
  `family_id` — своё семейство `gen-<12 hex id>` на задачу (меток задач модель не видит,
  `variant_of` не разрешить); `difficulty` — `default_target_difficulty`; целевой навык
  выбирает код (A7), модель видит его как `S1: <имя>` (WP14.1); `reference_solution` — в
  ключах, которые сейчас читают ход и практика (`sympy` + `check_mode` по типу ответа,
  `option`/`answer`/`options`, `answer`), плюс `solution`, `units`, `hints`. Тип ответа не
  подходит к виду задачи или у выбора не ровно один верный вариант → `GeneratedTaskError`
  → тот же откат на банк, что при сбое LLM. `pair_task` пока не показывается.
- Закрывает D-B-007: свободного словаря в `TaskGenOutput` больше нет, `T-L9` зелёный.
  D-B-001 (ключи WP6.4) остаётся: переход — один модуль `generation.py` и читатели.
- Правки тестов: `test_practice_engine::test_next_task_targets_new_family` — `family_id`
  ждётся от кода, вместо UUID навыка в промпте — `S1: <имя>` и отсутствие UUID (WP14.1);
  добавлены проверки `sympy` и `check_mode` в банке. Ответчик по умолчанию в
  `tests/support/llm.py`.
- Требует подтверждения владельцем: нет (делегировано). Уведомить C (файл `next_task`).

### D-B-011. `extractor@2`: схема — контракт промпта, элемент WP12.1 строит код
- Класс: B
- WP / место: WP12.1; `quack/session/schemas.py::ExtractorOutputV2`,
  `quack/session/extraction.py`, `prompts/extractor/v2.md`
- Решение: модель отвечает плоским списком `entries` по контракту промпта; чистая функция
  `to_extracted_items` даёт элементы WP12.1: `success/failure/bypass` →
  `correct/incorrect/partial` (+ `shown_solution` у `bypass`), три вида подсказки →
  `hint` + `hint_step` 1–3, `after_example` → `none` (окно свежести считает код, WP12.2
  п.4), коды пробела переименованы один к одному, у положительного — `none`. У гипотезы
  заблуждения добавлено поле `misconception_label` (в списке WP12.1 его нет, без него
  гипотеза не указывает заблуждение) — сообщить C (S5, событие v2).
- Не сделано: обработчик `turn.extract` остаётся на `extractor@1`, пока C не добавит
  `turn.extracted` v2 в каталог (WP12).
- Тесты: `tests/session/test_extraction_v2.py`.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-012. `structurer`, `curator`, `artifact_writer` из пакета в Фазе 3 не подключаются
- Класс: B
- Контекст: код `structurer` строит карту по названию домена, целям и фону — текста
  материала нет, а слой 1 пакета требует «бери только то, что есть в тексте» и кандидатов с
  метками. `curator` в коде получает весь граф домена и ведёт ещё `promotions`; промпт
  пакета решает готовые пары. `artifact_writer` отдаёт блоки с источниками и карточки, код
  ждёт `title` + Markdown; схема и вызов — файлы C.
- Решение: `structurer@1` и `curator@1` остаются прежними; промпты пакета ждут функций, под
  которые написаны (материалы и кандидаты, пары по сходству) — следующая фаза (§2.1).
  `artifacts` — отдельным шагом по согласованию с C: схема `{blocks, cards, review}`,
  Markdown собирает код.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-013. Учитель на метках (WP14.1)
- Класс: B
- WP / место: WP14.1; `quack/session/schemas.py::TeacherOutput`,
  `quack/session/teacher_labels.py`, `quack/session/turns.py`, `prompts/teacher/v1.md`
- Решение: поля выхода `focus_skill_labels`, `assign_task_label`,
  `concept_explained.topic_label`, `concept_explained.explained_skill_labels` (имена — по
  примеру WP14.1 и списку пакета владельца). Контекст учителя: тема сессии — `T1`, навыки
  среза — `S…` в порядке среза, их заблуждения — `M…`, задачи — `K…` в порядке
  `get_available_tasks`; учителю предлагаются только задачи с меткой (`label_max_tasks`).
  Свойства узлов, ключи которых — id (`id`, `*Id`, `*_id`, `seq`), в промпт не попадают;
  оценка задачи и `related_task_id` прошлых ходов — по метке. Код переводит метки в id перед
  записью событий; payload событий не меняется (`focus_skill_ids`, `topic_id`, `skills`).
  Неизвестная метка: элемент отбрасывается с логом `teacher_unknown_label`, остальной ответ
  применяется (закон 9). Однократный возврат модели с текстом ошибки (WP14.1) — через путь
  исправления шлюза, вместе с WP5 (сейчас не сделан).
- Правки тестов (подменный провайдер не меняется — очередь ответов, §8.1): ответы учителя
  в тестах сессии заданы метками; помощники `skill_label`, `task_label` в
  `tests/session/helpers.py` вычисляют метку по тому же порядку, что код. Утверждения «UUID
  задачи есть в промпте» (`test_turn_evaluation`, `test_turn_submission_fields`) заменены
  на «UUID нет, задача видна по метке / условию» — требование WP14.1 и `T-M3`. В
  `test_turn_lag_and_checkpoints` и `T-H1` второй ход повторяет метку первого: она больше
  не называет первую задачу — свойство «задача сессии не назначается повторно» то же.
- Тесты: `tests/session/test_teacher_labels.py`.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-014. `PROMPT_CONTRACTS.md`, `T-M5`; имена полей с метками для `CuratorOutput`
- Класс: B
- WP / место: §7.6, S4, D-C-013 п.4; `backend/PROMPT_CONTRACTS.md`,
  `tests/llm/prompt_contracts.py`, `tests/llm/test_labels.py`
- Решение: реестр «промпт → схема», поля с метками, маркеры и точки правки — в
  `tests/llm/prompt_contracts.py` (не в `quack/prompts`: ядро не импортирует продуктовые
  схемы, `T-A6`). Файл собирается `uv run python -m tests.llm.prompt_contracts`; тест
  `test_prompt_contracts_file_is_current` падает, если файл устарел. `T-M5` проверяет блок
  `## Output contract` промптов, где он есть (`teacher@1`, `evaluator@1`, `task_gen@1`,
  `extractor@2`).
- Для C (D-C-013 п.4), имена полей с метками в `CuratorOutput` вместо UUID:
  `promotions[].skill_label`, `promotions[].topic_label`; `skill_merges[]`,
  `topic_merges[]`, `misconception_merges[]` — `source_label`, `target_label`. Промпт
  `curator@1` остаётся прежним (D-B-012); после правки схемы C B добавляет поля в
  `LABEL_FIELDS` и пересобирает файл.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-015. Надёжность шлюза (WP5.1–5.6) и переход хода на `teacher_fallback`
- Класс: B
- WP / место: WP5.1–5.6, WP6.5; `quack/llm/gateway.py`, `quack/llm/pressure.py`,
  `quack/session/turns.py`
- Решения:
  1. `llm_semaphore_limit` (`С-§1.3`) остаётся в `Settings` и больше не используется (WP5.1):
     семафор на модель по `llm_concurrency[process_role]`.
  2. Шлюз получает `redis` в конструкторе (необязательный, по умолчанию `None`): флаг давления
     и ключ размыкателя без Redis не ставятся, исключений нет. Передачу `resources.redis` в
     `LLMGateway` при сборке ресурсов делают A/C (`quack/runtime`, `quack/api/resources`).
  3. Допуск учителя (WP5.3): ход читает оставшийся TTL флага модели учителя; если он не
     больше `teacher_admission_wait_ms` — ждёт ровно его, иначе сразу `teacher_fallback`
     (без опроса — нет числа «интервал опроса»). Ожидание слота семафора у ролей горячего
     пути ограничено тем же `teacher_admission_wait_ms`; не дождался — `LLMRateLimitedError`.
  4. Размыкатель: 429, 400 и обрыв по лимиту выхода — не сбои; проба, получившая такой
     ответ, заканчивается без вердикта, следующий вызов пробует снова.
  5. Ход: `LLMRateLimitedError` или `circuit_open` у учителя → вызов `teacher_fallback`
     (`prompt_role="teacher"`, ключ `…:teacher:fallback`), `degraded_mode =
     "teacher_fallback"`; запасной не удался → детерминированная деградация Фазы 2.
     Уровень рассуждения передаётся в шлюз (`teacher_reasoning_effort`, WP6.5).
- Правки тестов (§8.9): `test_gateway_semaphore_limits_concurrency` — на семафоры по моделям
  (п.4); проверка «не ослаблен»: с лимитом 1000 вместо настроенного тест падает.
  Фикстуры: в общей `settings` (`tests/conftest.py`) `llm_retry_attempts=0` — тесты Фаз 1–2
  подают один сбой и ждут деградации сразу, повторы WP5.2 проверяют T-L2/T-L3 со своими
  настройками; `test_gateway_provider_unavailable` — `llm_retry_attempts=0`,
  `test_gateway_session_budget` — `session_budget_exempt_roles=()` (WP5.6 освобождает
  `extractor`, а тест проверяет сам бюджет). Утверждения не менялись.
- Тесты: T-L1–T-L7, T-L11 (часть шлюза), T-L12, T-L13. T-L8, T-L10 — адаптер WP5.7 (ждёт
  D-B-004). T-H6 (`test_teacher_fallback_on_pressure_and_429`, `test_phase3_hot_path.py`)
  — добавлен отдельным коммитом и сразу зелёный: поведение реализовано в этом же решении
  (п.3, п.5), красного прогона нет. Шлюз теста собирается с `redis` (п.2), иначе флаг
  давления в (б) не ставится.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-017. `session/workers.py` на ресурсах `JobContext` (WP2.1, по просьбе A)
- Класс: B
- WP / место: WP2.1 (пересечение №2, §7.3); `quack/session/workers.py`,
  `tests/support/resources.py`
- Решение: драйвер Neo4j, Redis и шлюз берутся только из `ctx` (`ctx.neo4j`, `ctx.redis`,
  `ctx.llm`); модульный кэш `_RESOURCES`, `_worker_resources`, вызовы `create_driver`,
  `create_redis`, `HttpLLMProvider(` удалены. Пока поля `JobContext` необязательны (D-A-002),
  один хелпер `_job_resources(ctx)` проверяет драйвер и шлюз и бросает `RuntimeError`;
  убирается, когда A снимет `None`. `ctx.redis = None` допустим — работа без Redis (§4.2),
  обработчики его уже принимают (ревью A). `compute` обработчики `workers.py` не используют и не проверяют. Логика
  обработчиков не менялась. Сборщик `build_job_context` в `tests/support/resources.py`
  (§2.3 конфликт 20: тесты собирают контекст сборщиком).
- Правки тестов (§8.9): `test_worker_pool_and_slice::test_handlers_share_driver_redis_and_gateway`
  — вместо «`create_driver`/`create_redis`/`LLMGateway` вызваны по одному разу» проверяется,
  что все три вызова получают те же `ctx.llm` и `ctx.redis`, и что без ресурсов в контексте
  обработчик бросает `RuntimeError`. Свойство то же (общие ресурсы на все задачи), критерий
  строже; проверка «не ослаблен»: если обработчик создаёт свой шлюз, тест падает.
  Фикстуры: `JobContext(settings=…, session_factory=db)` → `build_job_context(...)` в
  `test_session_checkpoint_worker`, `test_turn_extract_worker`, `test_extract_quote_grounding`,
  `test_worker_pool_and_slice`; из `test_session_checkpoint_worker` убрана подмена
  `HttpLLMProvider`/`LLMGateway` (шлюз приходит из контекста).
- Снятие `_ReadOnlyDriver` (D-C-016, отдельный коммит): `workers.py` отдаёт `ctx.neo4j` в
  `Neo4jGraphStore` и `read_applied_seq` как есть, своих сессий на драйвере не открывает;
  запись `quack.session.workers` удалена из `PENDING_NEO4J_SESSIONS` (список пуст). До того как
  C переведёт `Neo4jGraphStore._read` на `read_session`, чтение графа в `turn.extract` идёт без
  `READ_ACCESS` — ход C по D-C-016.
- Требует подтверждения владельцем: нет (поручение A).
### D-B-016. `extractor@2` → `turn.extracted` v2 (швы S5/S6)
- Класс: B
- WP / место: WP12.1–12.2; `quack/session/extraction.py`, `quack/llm/labels.py`
- Решение: `extraction_context` собирает `ExtractionContext` из таблицы меток вызова
  (`LabelMap.labels(kind)`), эпизода и темы сессии; `extracted_elements` прогоняет каждый
  элемент через `to_extracted_element` (отклонённые остаются с `rejected_reason`);
  `turn_extracted_v2` — payload `schema_version = 2`. В `episode` — только события ученика
  (`turn.student_replied.text`, `task.submitted.answer`): цитата из реплики учителя получает
  `source_outside_episode` (правило 9 промпта). Префикс `new: ` нового навыка и
  заблуждения снимается при переводе ответа.
- Не сделано: вызов в обработчике `turn.extract` (`session/workers.py`) — после PR потока A
  по WP2.1 (A просил не трогать файл до него); там же снятие `_ReadOnlyDriver` (WP4.1).
- Тесты: `tests/session/test_extraction_v2.py`.
- Требует подтверждения владельцем: нет (делегировано).

### D-B-018. WP6.3: keepalive в зоне B; событие `status` и ранняя запись реплики ученика
- Класс: C (keepalive), конфликт документов (статус) — решён исполнителем по поручению владельца
  2026-11-01
- WP / место: WP6.3; `quack/session/keepalive.py`, `quack/session/router.py`,
  `quack/session/turns.py`, `quack/session/context.py`
- Решение (keepalive): `with_keepalive` / `sse_keepalive_response` — после `sse_keepalive_s`
  тишины поток шлёт `: ping` и пустую строку; шаг потока событий между пингами не
  отменяется. Подключено к `GET /v1/sessions/{id}/events` и к потоку хода. Общий
  `sse_response` лежит в `quack/api/sse.py` (зона C), поэтому обёртка — в `quack/session`;
  C может перенести её в `quack/api/sse.py` и подключить к `GET /v1/learner/stream` (WP7).
- Конфликт (статус хода): WP6.3 требует `status` «первым после `turn_started`» и до вызова
  модели, а `turn_started.user_seq` (`С-§8.5`) — `seq` реплики ученика, которую код Фазы 2
  писал одной транзакцией с ответом учителя после модели. Варианты: (1) `status` до
  `turn_started`; (2) `turn_started` без `user_seq` — меняет `С-§8.5`; (3) реплика ученика —
  своей транзакцией до модели.
- Решение: (3). `Т-§1.4` п.1: «API в одной транзакции выдаёт номер ученика и пишет событие
  действия», сборка контекста и модель — после; `Т-§2.2` «хода в журнале нет» относится к
  ходу учителя (`turn.committed` и его последствия пишутся вместе). Основной документ выше
  `С-§` и кода Фазы 2 (§0.3 п.2). Формат `С-§8.5` не меняется.
  - До ответа: блокировка, проверка сессии, идемпотентность, запись `turn.student_replied`
    (ключ — ключ хода). Поток: `turn_started` → допуск учителя (WP5.3) → `status`
    `{"state": "thinking"}` → остальной ход без изменений; ход учителя и его события
    (`task.submitted`, `task.evaluated`, `task.assigned`, `turn.teacher_replied`, манифест,
    задачи) — одной транзакцией, как раньше. Отказ допуска переводит ход на
    `teacher_fallback` после `status` — событие одно.
  - Повтор с тем же ключом: ход записан — повтор кадров (с `status`); записана только реплика —
    ход возобновляется на ней (тот же `turn_id`, `user_seq`, без второй реплики).
  - `build_turn_context(before_seq=…)`: срез ждёт проектор до события перед репликой (сама
    реплика — в наложении, закон 9), окно разговора кончается перед ней (реплика — отдельная
    переменная промпта). Без этого каждый ход ждал бы `projector_lag_timeout_ms`.
  - Ошибка хода после первого кадра обрывает поток (как `Т-§12.1`, «обрыв до commit»), а не
    отвечает JSON; клиент повторяет ход и получает его возобновление.
- Правки тестов (§8.9 п.6 — новое событие контракта `С-§8.5`, не ослабление): в
  `test_turn_evaluation::test_math_task_evaluated_in_turn_via_compute`,
  `test_turns_and_sse::test_full_turn_sse_order_format_events_and_extract_job`,
  `…::test_turn_without_concept_or_task`,
  `…::test_practice_mode_assigns_task_even_if_move_is_not_assign_task` в точный список кадров
  добавлен `status` после `turn_started` (индексы кадров сдвинуты); в первом из `turns_and_sse`
  добавлена проверка `{"state": "thinking"}`. `T-M4` (свой, WP14.2): «сбой записи манифеста →
  событий хода нет» считает события хода учителя (ключи `<ключ>:…`), реплика ученика по
  решению пишется раньше — свойство «ход учителя и манифест атомарны» то же.
- Тесты: `T-H5`, `T-H10` (`test_turn_status_before_text`, вместо задержки 0,5 с провайдер
  держит ответ до чтения `status` — «раньше, чем провайдер вернул ответ» без таймингов),
  `test_turn_resumes_on_the_stored_reply`.
- `openapi.json`: у потока хода общее описание без перечня событий; добавить `status` в текст —
  `quack/api/openapi.py` (C), фронтенду — на G1 (R-F).
- Требует решения владельца: нет (поручено исполнителю 2026-11-01).

### D-B-019. WP15: защита от спойлера — что считается активной задачей и как сравнивается ответ
- Класс: C
- WP / место: WP15; `quack/session/spoiler_guard.py`, `quack/session/turns.py::_guard_spoiler`
- Решение:
  1. Активная задача — выдана в этой сессии (`task.assigned`) до текущего хода, по ней нет
     верного `task.evaluated` и нет подсказки ступени `max_hint_level` (признак просьбы
     решения по аудиту WP0). Задача, которую этот ход проверил как верную, закрыта.
  2. Ответ — из ключей банка D-B-010: `math` — `sympy`, `choice` — `answer` (текст верного
     варианта; у задач банка без `answer` не проверяется), `open` — не проверяется.
     Сравнение — локальная нормализация (без вызова `compute` на каждую попытку): регистр,
     `**` → `^`, степени-индексы, пробелы у операторов, без `*`; число — по значению,
     выражение и текст варианта — целым токеном. Целые с модулем меньше
     `spoiler_ignore_abs_below` и однобуквенные ответы не проверяются.
  3. Повтор — та же роль (`teacher` или `teacher_fallback`), тот же уровень рассуждения,
     ключ `…:guard<N>`; до `guard_max_retries`, затем удаляется предложение с ответом.
     `LLMBudgetExceededError` на повторе → `budget_exceeded`, недоступность → `llm_unavailable`
     (общий обработчик хода). Лог `guard_violation`: правило, сессия, ход, задача, попытка,
     действие; без текста ученика и ответа.
  4. Причина повтора идёт модели данными в переменной `guard_reason` (JSON `{"rule":
     "spoiler", "task_label": "K…"}`; метки нет, если задача вне таблицы вызова; пустая строка
     на первой попытке). **В `teacher@1` места `{{ guard_reason }}` нет** — лишняя переменная
     не подставляется, модель причину не видит. Добавить блок в промпт — работа владельца
     (промпты).
- Тесты: `T-G1`–`T-G4` (`tests/session/test_spoiler_guard.py`).
- Требует подтверждения владельцем: п.1 — нет, **2026-11-01 решено исполнителем по поручению
  владельца**: признак `ТЗ-П2` «подсказка высшей ступени», события `solution.requested`
  (`Т-§2.2`) в коде нет. П.4 — блок `{{ guard_reason }}` в `teacher@1` пишет владелец (промпты).
### D-B-020. WP14.2: значения полей манифеста хода
- Класс: C
- WP / место: WP14.2; `quack/session/manifest.py`, `quack/session/turns.py`
- Решение: строка пишется в транзакции `append_events` хода, после события. Типы
  `TurnManifest` приведены к колонкам миграции `0002_phase3` (версии — числа `N` из
  `name@N`). `prompt_version` — `TEACHER_PROMPT_VERSION` (1); `renderer_version` — номер
  `RENDERER_VERSION` (`renderer@1`, D-B-002): другой версии отрисовки контекста учителя в
  Фазе 3 нет. `role` — `teacher_fallback`, если ответ дал запасной, иначе `teacher` (и при
  детерминированной деградации); `model` — модель этой роли; `reasoning_effort` — уровень этой
  роли (WP6.5); `projected_seq` — `applied_seq` среза; `overlay_seqs` — `seq` событий
  наложения; `label_table` — `LabelMap.table()`; `degraded_mode` — итог хода.
- В красном коммите `T-M4` ждал `degraded_mode = None`; в фикстуре проектор не применил
  `session.started`, ход честно идёт в `projector_lag`. Ожидание исправлено до зелёного
  коммита: манифест равен `turn_completed.degraded_mode` (проверка не слабее — точное
  совпадение с итогом хода).
- Тесты: `T-M4` (`tests/llm/test_labels.py`).
- Требует подтверждения владельцем: нет

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
### D-C-018. Шов S7: какие ошибки драйвера Neo4j дают `503 SERVICE_UNAVAILABLE`
- Класс: B
- WP / место: WP6.1 (часть C), §4.5; `quack/api/errors.py::NEO4J_OUTAGE_ERRORS`
- Контекст: WP6.1 — эндпоинты, которым Neo4j нужен для ответа, при «ошибке связи драйвера (недоступен
  сервер, истекла сессия, таймаут)» отвечают 503, а не 500. Перечня классов исключений ТЗ не даёт.
- Варианты: (1) `ServiceUnavailable`, `SessionExpired`, `ConnectionAcquisitionTimeoutError` — общий
  обработчик приложения; (2) любой `Neo4jError`/`DriverError` — тогда ошибка запроса (дефект кода)
  тоже маскируется под недоступность.
- Решение: (1). Ошибки запросов остаются 500 и видны как дефект. Обработчик пишет `neo4j_unavailable`
  в лог.
- Почему: §0.4.3 п.1, п.4.
- Обратимость: кортеж `NEO4J_OUTAGE_ERRORS`.
- Тесты: `tests/test_neo4j_outage.py` (граф и карточка темы → 503); ход учителя — `T-H1` у B.
- Требует подтверждения владельцем: нет

### D-C-019. WP12, швы S5/S6: элемент `turn.extracted` v2, проверка и вес
- Класс: B
- WP / место: WP12.1–12.2; `quack/events/payloads.py::ExtractedElement`, `TurnExtractedV2`,
  `quack/events/catalog.py::EVENT_TYPE_VERSIONS`, `quack/graph/evidence_validation.py`,
  `quack/graph/evidence_weights.py`, `quack/graph/phase_a.py::_turn_extracted_v2`, `Settings.hot_window_factor`
- Контекст: WP12.1 перечисляет поля элемента, но не говорит, как в событии хранить метки, время и
  итог проверки; B (D-B-011) добавил `misconception_label`; множитель «окна» 0.5 (`Т-§7.4`) в §6.2 не назван.
- Решение:
  1. Элемент события: поля WP12.1 + `skill` (`SkillRef`: метка → `skill_id`, описание нового навыка →
     `new_skill` темы сессии, `Т-§4.4`), `misconception_label`/`misconception_id`, `occurred_at`
     (самое позднее из исходных событий, WP12.2 п.2), `rejected_reason` (итог проверки кода; `None` —
     применён). Числовых полей, кроме `level`, `hint_step`, `source_seqs`, нет (`T-W1`).
  2. Проверка (`to_extracted_element`, S6): `unclear` → `unclear_skill`; неизвестная метка `S…`/`M…` →
     `unknown_label`; номер вне эпизода → `source_outside_episode`; отрицательный элемент или гипотеза
     без цитаты → `missing_quote`; цитата не из исходных событий → `quote_not_in_source`; уровень выше
     разметки задачи → `level_above_task`; без полей своего вида → `incomplete_element`.
  3. Ярус и вес — чистые `assign_tier`, `evidence_weight` по `Т-§7.4`/`Т-§7.8`; все числа из `Settings`;
     `hot_window_factor = 0.5` — новый параметр (# Phase 3 decision D-C-019).
  4. Проектор: `turn.extracted` v2 → свидетельство T3 со временем элемента, без чисел модели;
     отклонённые и неполные элементы не применяются (поток не останавливается); v1 читается как раньше.
- Не сделано (вторая часть WP12): вес в свёртке фазы Б и `rules@2` (WP12.3, `T-W7`).
- Почему: §0.4.3 п.1, п.2 (новая версия схемы, старая читается); п.5.
- Обратимость: модели v2 и одна запись каталога; обработчик v2; параметр `hot_window_factor`.
- Тесты: `tests/graph/test_evidence_law4.py` — `T-W1`…`T-W6` (27 тестов).
- Требует подтверждения владельцем: нет

### D-C-020. WP12.2 п.4, WP12.3: вес в свёртке под `rules@2`
- Класс: B
- WP / место: `quack/graph/folds.py::_weight`, `recompute_skill_fold`, `quack/graph/ids.py`,
  `quack/config.py::FoldParams` и `Settings._evidence_weights_into_fold`, `quack/graph/phase_a.py` (`optionsCount`)
- Контекст: WP12.2 п.4 — вес = база яруса × угадывание × окно × подсказка (`Т-§7.4`), его считает
  свёртка. В Фазе 1 вес = база × `independence` × множитель глубины проверки; ветка «без ворот»
  умножала на `strength` и `polarity_weight` — числа модели из `extractor@1`.
- Решение:
  1. `_weight` = `evidence_weight` (ярус, угадывание по `optionsCount` задачи с выбором, окно «горит» по
     показам навыка, ступень подсказки элемента v2, показанное решение → 0) × `independence` (штраф
     подсказок задачи, его считает код). Ветка «без ворот» берёт тот же вес — `strength` и
     `polarity_weight` модели больше ни на что не влияют.
  2. Множитель глубины проверки (`depth_weight_floor`) сохранён **только у свидетельств за воротами**
     (T1/T2): глубину там задаёт код по задаче; без него менялись бы формулы Фазы 1 и три её теста
     (не входят в §8.9). У T3/T4 глубины модели в весе нет.
  3. Числа одни: `Settings` копирует `evidence_tier_weights`, `hint_step_penalty`, `hot_window_min`,
     `hot_window_factor` в `FoldParams`; `evidence_weight` и свёртка читают одно и то же.
  4. `CURRENT_RULES_VERSION = "rules@2"`; после выкатки — `ops rebuild` (данных пока нет).
- Почему: закон 4, §0.4.3 п.1; п.4 (Фаза 1 без изменений там, где ТЗ не требует).
- Обратимость: `_weight` в `folds.py`; номер правил в `ids.py`.
- Тесты: `T-W7` `test_rules2_rebuild_equals_incremental`, `test_model_strength_does_not_change_the_state`;
  тесты свёрток Фазы 1 без изменений.
- Требует подтверждения владельцем: нет. Если владелец хочет `Т-§7.4` буквально (без глубины и у
  T1/T2) — убрать строку множителя в `_weight` и переписать `test_deeper_testing_gives_larger_gain` (класс C).
### D-C-021. WP7: где живёт канал и тест `T-H4`
- Класс: B
- WP / место: WP7; `quack/api/learner_stream.py`, `tests/test_learner_stream.py`, `tests/support/sse.py`
- Контекст: канал не относится ни к одному продуктовому пакету; `T-H4` перечислен в §8.5 (файл горячего
  пути — зона B); `httpx.ASGITransport` буферизует весь ответ, бесконечный SSE так не прочитать.
- Варианты: (1) роутер в `quack.api` (основание API, зона C), монтируется в `create_app`; тест — отдельный
  файл C; потоковый транспорт — в `tests/support/` (S9); (2) роутер в `quack/session/` — зона B.
- Решение: (1). Транспорт повторяет тот, что B написал в `tests/session/test_session_events_stream.py`;
  B может перейти на общий из `tests/support/sse.py`.
- Почему: §7.3 (зона C), §4.1 (основание API импортирует только ядро и API).
- Обратимость: одна строка `include_router`, модуль канала.
- Тесты: `T-H4` (`test_learner_stream`, `test_learner_stream_polls_without_redis`), keepalive, 401.
- Требует подтверждения владельцем: нет

### D-C-022. WP7: поведение канала в пограничных случаях и keepalive
- Класс: B
- WP / место: WP7, WP6.3; `quack/api/learner_stream.py`, `quack/api/sse.py::KEEPALIVE_COMMENT`, `sse_bytes_response`
- Контекст: ТЗ не говорит, что делать после обрыва подписки в середине потока и как слать keepalive, пока
  общий keepalive WP6.3 (поток B) не сделан.
- Решение: подписка на `projector:applied:{id}` оформляется до первого чтения смещений (событие между
  ними не теряется); ошибка Redis в середине → подписка закрывается, канал переходит на опрос
  `ops.projector_offsets` раз в `learner_stream_poll_ms` и шлёт событие только при росте `applied_seq`;
  каждое сообщение Redis → `projection_advanced {"applied_seq"}`; молчание дольше `sse_keepalive_s` →
  `: ping`. Соединение с Postgres берётся на один запрос; при отключении клиента подписка закрывается
  под `asyncio.shield`. Кадр keepalive и ответ из готовых кадров — общие в `quack/api/sse.py`: B может
  взять их для WP6.3.
- Почему: §0.4.3 п.1 (событие не теряется), п.4.
- Обратимость: функция `learner_stream`.
- Тесты: `tests/test_learner_stream.py`.
### D-C-023. WP10.3: `meta-mock` — значения, которых нет в документах
- Класс: C
- WP / место: WP10.3, `Д-§11`, `Т-§11.8`; `backend/loadtest/meta_mock.py`, `tests/test_meta_mock.py`
- Контекст: WP10.3 задаёт поведение подменного сервера, но не все значения: имя и значение
  параметра длины ответа учителя, число токенов рассуждения, счёт токенов «по длине», вид
  «короткого текста», выбор доли ответов, тела ошибок. Адаптер `ResponsesClient` (WP5.7, B) —
  пока только сигнатура (D-B-004), поэтому формат на проводе берётся из контракта Responses API
  (`Т-§11.1`, `T-L8`), а не из адаптера (по ответу B).
- Варианты: (1) взять значения из кода стенда и записать здесь, менять без правки контракта;
  (2) ждать владельца.
- Решение: (1). `META_MOCK_TEACHER_CHARS` = 600 (длина `reply_text` в ответе на
  `TeacherOutput`; относится ко всем свободным строкам этой схемы); токены рассуждения: `low` —
  256, `high` — 1024; токены = ⌈символы / 4⌉ (вход — JSON поля `input`); короткий текст —
  `text`; `boolean` — `false`; число без границ — 0, с одной границей — граница; `uuid`,
  `date-time`, `date` — фиксированные значения; `minItems` соблюдается. Доля 429 и доля
  обрывов — детерминированный счётчик (доля 0,5 — каждый второй), чтобы тесты не зависели от
  случая. `retry-after` для доли 429 — 1 с, для пустого ведра — время до пополнения; формат
  `mixed` идёт по кругу `seconds` → `http-date` → `none`. С `tools` шаги чередуют один и два
  `function_call`, аргументы — минимальный экземпляр `parameters` инструмента. Уровень, кроме
  `high`, считается `low` (в `Settings` других нет). Процесс — Starlette, запуск
  `uvicorn --factory loadtest.meta_mock:app_from_env`; `loadtest` добавлен в `include` pyright.
  `minimal_instance` — общий генератор для `T-L9` (B импортирует его из `loadtest.meta_mock`).
- Почему: §0.4.3 п.2 (меньше нового); значения — стенда, не продукта; ни одно не влияет на
  продуктовый код.
- Обратимость: константы в одном модуле.
- Тесты: `tests/test_meta_mock.py` (27). `T-O5` через `ResponsesClient` — после адаптера WP5.7.
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
| WP3.1, 3.2 (ядро), 3.4 (Этап II) | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_jobs_phase3.py` (до реализации) | 5 failed: `T-Q1` (захват игнорирует `required_seq`), `T-Q2` (склейка не пишет `required_seq`), `T-Q3`/`T-Q4` (`JobDeferred` идёт по ветке повтора), `T-Q8` (бюджет не маршрутизируется) |
| WP8.7 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_app_assembly.py -k "openapi or sse_endpoints"` (коммит `1eec6bd`) | 4 failed: `T-A7` — нет `backend/openapi.json`; `write_openapi` — `NotImplementedError`; схема — нет `ErrorResponse`, 422 описан `HTTPValidationError`; SSE-эндпоинты — `application/json` без модели |
| WP4.1 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_neo4j_read_session.py tests/test_contracts.py -k "neo4j or read_session"` (коммит `b692007`) | 3 failed, 1 passed: `read_session` — `NotImplementedError` (2); `T-A9a` — сессии вне `read_session` в 6 модулях C (`artifacts.cheatsheets`, `artifacts.search`, `domains.curation`, `domains.diagnostic`, `domains.graph_view`, `domains.structure`) |
| WP4.2 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/artifacts/test_artifact_isolation.py` | 1 failed: шпаргалка чужой темы → `200 null`, ждали 404. `T-E3` на стенде `phase-3-c-e2e-red`: то же падение |
| S7 (WP6.1, часть C) | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_neo4j_outage.py` | 2 failed: `GET /v1/domains/{id}/graph` при сломанном Neo4j → 500, ждали 503; нет `NEO4J_OUTAGE_ERRORS` |
| WP12 (часть 1) | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/graph` (коммит `d9951df`) | 26 failed, 1 passed: `NotImplementedError` в `assign_tier`/`evidence_weight`/`to_extracted_element`; проектор падает на v2 (`TurnExtractedV2` без `observations`) |
| WP12 (часть 2) | проверка на неверной реализации (§8.9): `CURRENT_RULES_VERSION_NUM = 1` и вес «без ворот» × `strength` × `polarity_weight` модели; `uv run pytest -q tests/graph -k "rules2 or strength"` | 2 failed: `T-W7` (`projectorVersion = rules@1`), `test_model_strength_does_not_change_the_state` (сила модели меняет `pMastery`). Тесты написаны вместе с реализацией, отдельного красного коммита нет |
| WP8.3 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_app_assembly.py -k healthz` | 1 failed: `GET /healthz` → 404 (маршрута нет) |
| WP7 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_learner_stream.py` (коммит `9227447`) | 4 failed: маршрут не смонтирован — `GET /v1/learner/stream` → 404 (ждали 200 / 401) |
| WP10.3 (`meta-mock`) | `uv run pytest -q tests/test_meta_mock.py` (коммит red) | 27 failed: `NotImplementedError` в `minimal_instance`, `latency_s`, `create_app`, `MetaMockConfig.from_env` |
| WP6.3 (keepalive) | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/session/test_session_events_stream.py` (коммит `cdd3337`) | 1 failed, 2 passed: `T-H5` — за 0.7 с ни одного `: ping` |
| WP15 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/session/test_spoiler_guard.py` (коммит `233b6ca`) | 3 failed, 1 passed: `T-G1`, `T-G4` — один вызов учителя, ответ в `token`; `T-G2` — `NotImplementedError`; `T-G3` зелёный (защиты ещё нет) |
| WP14.2 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/llm/test_labels.py` (коммит `97a1a90`) | 1 failed, 13 passed: `T-M4` — строки `ops.turn_manifests` нет (`write_turn_manifest` не вызывается) |
| WP6.3 (статус хода) | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/session/test_phase3_hot_path.py tests/session/test_turn_evaluation.py tests/session/test_turns_and_sse.py tests/session/test_turn_retry.py tests/llm/test_labels.py` (коммит `0d60978`) | 6 failed, 62 passed: `T-H10` ×2 (нет `status`), 4 теста точного порядка кадров ждут `status` |

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
| `tests/factories.py::EventLog.add` | необязательный параметр `schema_version` (по умолчанию 1) | WP12: события `turn.extracted` v2 в сценариях; фикстура, утверждения не менялись |
| `tests/test_app_assembly.py::PENDING_ROUTES` | убран `GET /v1/learner/stream` | WP7 смонтирован (`test_pending_routes_are_still_pending` требует удалить запись) |

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
