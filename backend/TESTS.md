# Тесты backend: политика, слои и разбор

Решение `D-C-146` (`PHASE4_NOTES.md`). Источники: ТЗ Ф4 §8 (план тестов), §8.15 (что делать с тестами Фаз 1–3), WP1.3 (снятие SQLAlchemy), §4.1 (слои пакетов); `Д-§5.2`. Состояние на 2026-11-03: **174 файла, 1743 теста** (до разбора: 174 файла, 1739 теста; удалено 1 файл и 4 теста, добавлено 8 тестов слоёв CI).

## 1. Политика: что тест должен ловить, чтобы остаться

Тест живёт, пока ловит то, что без него сломается тихо:

1. **Швы** — контракт между потоками и пакетами: схемы событий и ролей, `T-A1` (маршруты), `T-A6` (слои пакетов), подмены `tests/support`, строки таблицы правил, обработчики видов задач.
2. **Изоляция** — `learner_id` в каждом запросе и маршруте (`T-DB4`, `T-E3`, «чужой ученик → 404»).
3. **Идемпотентность** — повтор с тем же ключом: тот же ответ и ни одного нового события (`T-E2`, `T-DB5`, ключи событий).
4. **Гонки и порядок** — параллельные записи, блокировки, `seq` без пропусков, один писатель, плавная остановка.
5. **Проверка ответа и закон 4/5** — вердикты `check()`, силу доказательства назначает код, не модель; проверка выхода роли кодом.
6. **Определённость и повтор** — проектор и свёртки дают один результат при любом размере пачки и при повторе лога.

Что **не** оставляем:
- проверку значения по умолчанию из `Settings` (она повторяет сам `Settings`; поведение проверяют потребители лимита);
- «надгробие» — проверку того, что константы или файла больше нет, если ничто не мешает вернуть;
- самопроверку тестового помощника, если его ломкость и так роняет сотни тестов;
- второй тест того же поведения того же кода (оставляем сильнейший, второй указываем в таблице как покрытый);
- тест кода Фазы 3, который заменил тест §8 (§8.15 п.1) — снимается **вместе с кодом**, в том же PR, строкой в «Правках тестов».

Тест не слабеет ради зелёного (§8.15 п.2–4): ослабление — только новой записью `D-…` и строкой в «Правках тестов».

## 2. Слои и как запускать

| Слой | Где | Что | Когда |
| :-- | :-- | :-- | :-- |
| `fast` | CI job `fast`, без служб | `uv sync --frozen`, `ruff check`, `ruff format --check`, `pyright`, `pytest -q` **без** `QUACK_TEST_REQUIRE_INFRA`: тесты на Postgres, Neo4j и Redis пропускаются, остальное идёт (~1100 тестов, ~1 мин) | каждое событие: pull request и push в `main`, `phase-4`, `phase-4-a`, `phase-4-b`, `phase-4-c` |
| `full` | CI job `backend`, службы Postgres 16, Neo4j, Redis | те же тесты с `QUACK_TEST_REQUIRE_INFRA=1`: ничего не пропускается (~8 мин) | pull request и push в `main`, `phase-4`; на push в ветки потоков не идёт |

`compute` и `images` всегда сообщают результат, но тяжёлые шаги выполняют, только если изменились `compute/` (для `images` — `backend/` или `compute/`) или сам workflow; без базы сравнения (новая ветка) идёт всё. `publish` ждёт `backend`, `fast`, `squawk`, `compute`, `images` и работает только на push в `main` и `phase-4`. Job `frontend-types` (только PR; типы фронтенда из `backend/openapi.json`, ожидаемо красный до перевода фронтенда) не входит в `needs` у `publish`; `modal/` проверяется в `fast`; изоляция и один писатель идут отдельными шагами job `backend` (D-C-210). Контроль: `tests/test_ci_layers.py`, `tests/test_ci_squawk.py`, `tests/test_deploy_order.py` (T-OPS8).

Локально (из `backend/`):

```bash
# fast: как в CI, без служб (общие контейнеры quack-test-* на портах по умолчанию надо остановить
# или направить DSN на закрытые порты — иначе тесты пойдут на них)
uv run ruff check . && uv run ruff format --check . && uv run pyright
QUACK_TEST_POSTGRES_DSN=postgresql://x@127.0.0.1:1/x QUACK_TEST_NEO4J_URI=bolt://127.0.0.1:1 \
  QUACK_TEST_REDIS_URL=redis://127.0.0.1:1/0 uv run pytest -q

# затронутое + контрактные — на своей инфраструктуре K (1..9, порты 56K01..56K03)
scripts/dev/test_infra.sh up K
scripts/dev/ct.sh K tests/<затронутое> tests/test_contracts.py tests/db/test_sql_static.py \
  tests/test_app_assembly.py tests/e2e/test_isolation_and_idempotency.py
scripts/dev/test_infra.sh down K

# full: весь набор — один раз на волну, перед PR (~8 мин)
scripts/dev/ct.sh K
```

Правила локального прогона:
- **затронутое + контрактные**: контрактные тесты (`test_contracts`, `db/test_sql_static`, `test_app_assembly`, `e2e/test_isolation_and_idempotency`) идут, когда менялись маршруты, пакеты или SQL; полный набор — один раз перед PR или слиянием;
- **своя инфраструктура на сессию**: два прогона на общих базах обнуляют таблицы друг друга; у каждой сессии или субагента свой K и свои `qa-K-pg`, `qa-K-neo4j`, `qa-K-redis`; по окончании `scripts/dev/test_infra.sh down K`;
- на Windows `tests/db/test_sql_static.py::test_repository_functions_take_learner_id` падает из-за разделителя пути (`\`), в Linux и CI зелёный; тесты, читающие корень репозитория (`.github`, `compose.yaml`), требуют полного checkout, не одного `backend/`.

## 3. Разбор набора

Категории:
- **A** — контрактные и интеграционные тесты Фазы 4 из §8 (`T-…`) и их основа: оставить.
- **P** — свойство остаётся (§8.15 п.2): оставить; тест перенесён на `Db` (`pg_db`) с тем же утверждением (WP1.3).
- **S** — стенды, подмены, `compose`, нагрузочный сценарий, операции: оставить.
- **X** — `tests/e2e/**` и `tests/test_app_assembly.py`: этим разбором не трогаются (их правит другой агент).
- **B** — код Фазы 3, который заменяет Фаза 4: **снимается вместе с кодом** (WP1.3 и удаление маршрутов), сейчас не удаляется.
- **C** — удалено сейчас (раздел 5).

| Категория | Файлов | Тестов |
| :-- | --: | --: |
| A | 93 | 875 |
| P | 25 | 288 |
| S | 8 | 122 |
| X | 6 | 32 |
| B | 42 | 426 |
| **Итого** | **174** | **1743** |

### 3.1. A — Phase 4, оставить

| Файлы | Тест §8 | Почему |
| :-- | :-- | :-- |
| `db/test_append_events.py`, `db/test_baseline_migration.py`, `db/test_sql_static.py`, `db/test_squawk_config.py` | `T-DB5`, `T-DB1`, `T-DB2`–`T-DB4`, `T-DB7` | слой данных на `Db`: гонки, изоляция, SQL без f-строк |
| `events/test_catalog.py`, `events/test_rejected_element.py` | `T-EV1`–`T-EV3`, `T-EV5` | каталог Ф4, ключи, реестр |
| `graph/test_projection_{batches,diff,rules3,runner}.py`, `graph/test_projector_rows.py`, `graph/test_schema_wp31.py` | `T-P1`, `T-P3`–`T-P7` | проектор `rules@3`: пачки любого размера, таблица обработчиков, схема |
| `graph/test_skill_fold.py`, `graph/test_profile_fold.py`, `graph/test_profile_read.py`, `graph/test_extracted_check.py` | `T-F*`, `T-MS1`, `T-TH*`, `T-X1` | свёртки, профиль, проверка выхода `extractor@3` |
| `graph/test_read_stub.py`, `graph/test_read_neo4j_core.py`, `graph/test_read_neo4j_extras.py` | шов S4, `T-TH4` | общее чтение памяти |
| `jobs/*` (7 файлов) | `T-D1`–`T-D8`, `T-D10`, `T-Q*` | диспетчер, эпизоды, очередь, исполнитель Ф4 |
| `llm/test_phase4_ai_layer.py`, `llm/test_phase4_stream_adapter.py`, `llm/test_gateway_stream.py` | `T-AI1`–`T-AI4`, `T-AI6` | стриминг, цикл инструментов, шлюз |
| `practice/*` кроме `test_reference_solution.py` | `T-CH*`, `T-TK*`, `T-AG5` | `check()`, задачи, разметка, второе мнение |
| `session/test_{actions_route,head_guards,holdback,memory_tools,phase4_contracts,record_action,session_terms,sessions_db,teacher_context,turn_commit,turn_effort,turn_graph,turn_inputs,turn_source}.py` | `T-TT*`, `T-CX*`, `T-EV4` | действие ученика, ход учителя, контекст |
| `agents/*`, `canvas/*`, `auth/*`, `materials/*`, `embeddings/*`, `storage/*`, `artifacts/test_{cards,documents,topic_refresh}_wp23.py` | `T-AG*`, `T-CV*`, `T-AU*`, `T-CP1`, `T-MT*`, `T-ST*`, `T-AR*` | пакеты Ф4 WP15–WP23 |
| `test_contracts.py`, `test_ci_squawk.py`, `test_ci_layers.py`, `test_neo4j_read_session.py` | `T-A6`, `T-A9b`, `D-C-142`, `D-C-146` | слои пакетов, запись в READ-сессии, CI |

### 3.2. P — оставить, перенести на `Db` (WP1.3)

| Файлы | Что остаётся | Что снимается при переносе |
| :-- | :-- | :-- |
| `test_infra.py` | замки, схема Neo4j, клиент compute, формат ошибок, `/healthz`, SSE, CLI, журнал | `migration_creates_contract_indexes` (→ `T-DB1`); `fts_index_query_finds_text_events` — **после** появления `T-DB6` (его ещё нет) |
| `test_jobs_phase3.py` | `T-Q7` (`all_job_kinds_have_handlers`, `worker_exits_with_code_1…`), давление (`pressure_flag…`), `product_code_uses_context_resources`, `purge_job_removes_learner_completely` (до WP25) | 8 тестов старой очереди: `claim_waits_for_projection`, `coalesce_keeps_max_required_seq`, `coalesced_cheatsheet_job…`, `job_deferred_*`, `budget_errors…`, `periodic_scheduler…`, `worker_graceful_shutdown…`, `requeue_on_shutdown…` → `jobs/test_queue_p4.py`, `test_executor_p4.py` |
| `test_jobs_and_llm_gateway.py`, `test_llm_runtime.py` | шлюз, бюджеты, повторы, размыкатель, строгие схемы, реестр промптов | 15 тестов очереди `ops.jobs` на SQLAlchemy (`claim`, `coalesce`, `lock_key`, `retry/DLQ`, `reaper`) → `jobs/test_queue_p4.py`; `ops.llm_ledger` уходит с таблицей |
| `test_learner_stream.py`, `test_ops_phase3.py`, `test_neo4j_outage.py`, `test_api_llm_provider.py`, `test_ratelimit.py`, `test_settings_meta_api.py` | `T-H4`, `T-O1`–`T-O6`, `T-H1`, лимитёр, секреты по ролям | только способ засева |
| `test_thresholds_in_settings.py` | `no_numeric_threshold_constants…` (закон «пороги в `Settings`») | `diagnostic_parameters_have_phase_2_values` — вместе с диагностикой (B5) |
| `graph/test_evidence_law4.py` | `T-W1`–`T-W6`: нет весов в схеме, силу назначает код, выход проверен (ID сохраняются, §8.15 п.2) | `turn_extracted_v2_is_in_the_catalog`, `v1_and_v2_extracted_events_both_apply`, `rules2_rebuild_equals_incremental` — каталог и `rules@2` Ф3 (B1, B2) |
| `llm/test_labels.py`, `llm/test_meta_smoke.py`, `practice/test_reference_solution.py` | `T-M1` метки, смоук промптов, формат эталона | способ засева; `smoke_covers_every_phase3_prompt` пересмотреть при снятии промптов Ф3 |
| `session/test_degradation_and_workers.py`, `session/test_auth_and_stream_resilience.py` | замок хода и его деградация (`D-B-*`, замок остаётся), отзыв сессии, обрыв SSE | способ засева |
| `domains/test_{graph_view_fixes,diagnostic_and_graph_view}.py` | маршруты `/graph` и деталь темы остаются (`T-TH4`, `T-TH5`) | способ засева; уходят тесты `p_mastery` (`forgetting_lowers_topic_mastery_below_raw_p_mastery`) и диагностики/кураторства (`adaptive_diagnostic_cycle…`, `inferred_priors…`, `graph_curate_*`) |
| `domains/test_{structure_and_dag,structure_actor_and_delete,confirm_idempotency,propose_idempotency,topic_alias_routes,auth_and_learner}.py` | маршруты `structure/*`, `topics/*`, `learner/*` остаются в `T-A1`; идемпотентность — `T-E2` | способ засева; решает владелец домена при WP16/WP25 |

### 3.3. S — оставить

`test_support_scripted_llm.py` (подмена LLM, §8.1), `test_meta_mock.py` (стенд `meta-mock`, `T-L8`, `T-O5`), `test_loadtest_scenario.py`, `test_compose_contract.py` (`T-OPS1`), `test_ops_backup.py` (`T-OPS7`), `test_ops_db_roles.py` (`T-OPS2`), `test_ops_notify.py`, `test_api_cors.py`.

### 3.4. X — не трогаю

`tests/e2e/*` (5 файлов, 13 тестов) и `tests/test_app_assembly.py` (19 тестов): контрактные тесты потока C; их рефакторит другой агент.

## 4. Список снятия: B — вместе с кодом Фазы 3

Каждое снятие идёт тем же PR, что и удаление кода, и строкой в «Правках тестов» (§8.15 п.1): какой тест, какой тест §8 проверяет свойство теперь.

| № | Код Фазы 3, который уходит | Тесты (файлы → тестов) | Чем заменено | Условие |
| :-- | :-- | :-- | :-- | :-- |
| B3 | свёртки Ф3 (`p_mastery`, пять `display_state`): `quack/graph/{folds,evidence_weights}.py` | `test_skill_and_topic_folds.py` → 50 | `graph/test_skill_fold.py` (`T-F*`, `T-MS1`, `T-TH*`), `graph/test_profile_fold.py` | `graph/read/skill_state.py` и `neo4j_reader.py` ещё импортируют `folds` — снимать после перевода чтения |
| B4 | диспетчер Ф3: `quack/jobs/{dispatcher,dispatcher_rules,runner}.py` | `test_dispatcher.py` → 10 | `jobs/test_dispatcher_p4.py` (`T-D1`–`T-D4`), `jobs/test_rules_p4.py` | WP7 включён; `dispatcher_crash_does_not_stop_projector` проверить против `T-D8` при снятии |
| B5 | диагностика и `graph.curate`: `quack/domains/{diagnostic,curation}.py` | `domains/test_diag_{idempotency,open_and_bank,state_from_events,wakeup}.py` 30, `test_curation_{keys_and_actor,slice_and_pool,thresholds}.py` 4 → 34 | §8.15 п.1 («диагностика»); гигиена графа — `agents/test_hygiene.py` (`T-AG4`) | маршруты `diagnostic/*` убраны из `T-A1` |
| B6 | ход `POST /sessions/{id}/turns` и извлечение `turn.extract`, `session.checkpoint`: `quack/session/{turns,workers,extraction,spoiler_guard,…}.py`, `extractor@2`, `teacher@1` | 20 файлов `session/test_{turns_and_sse,turn_retry,turn_submission_fields,turn_lag_and_checkpoints,turn_evaluation,turn_extract_worker,session_checkpoint_worker,checkpoint_law10,extract_quote_grounding,extraction_v2,open_task_evaluation,phase2_seams,phase3_hot_path,spoiler_guard,degraded_actor,llm_degradation,worker_pool_and_slice,start_topic_resolution,schema_fields,teacher_labels}.py` → 121 | `session/test_turn_graph.py` (`T-TT2`–`T-TT7`), `test_holdback.py`, `test_head_guards.py`, `test_turn_commit.py`, `test_record_action.py`, `agents/test_extract_episode.py` (`T-X1`) | `/turns` убран из `T-A1`; `session.checkpoint` — §8.15 п.1 |
| B7 | жизненный цикл сессий на SQLAlchemy (`core.sessions`), `GET /sessions/{id}/events` | `session/test_{session_lifecycle,session_finish_and_sweep,lifecycle_fixes,session_lock_order,session_events_stream}.py` → 18 | `session/test_sessions_db.py` (старт, пульс, конец, подметание), `test_learner_stream.py` (`T-H4`) | `finish` и `heartbeat` — проверить, что `test_sessions_db` закрывает присутствие и очередь итога |
| B8 | заметки Markdown, шпаргалка темы, `tasks/next|hint|submit` Ф3: `quack/artifacts/{notes,cheatsheets,practice,store}.py` | `artifacts/test_{artifacts_search_export,artifact_isolation,practice_engine}.py` → 14 | `artifacts/test_documents_wp23.py` (изоляция: `other_learner_sees_nothing`), `test_cards_wp23.py`, `practice/test_*` (`T-CH*`, `T-TK*`); общая изоляция — `T-E3` | маршруты `notes`, `cheatsheet`, `tasks/*` убраны из `T-A1` |

Строки B1 (журнал и каталог Фазы 3) и B2 (проектор `rules@2`) сняты вместе с кодом в WP1.3 шаг 5 ч.2 (D-A-234): в `tests/**`, `quack/**`, `loadtest/**` нет `sqlalchemy`, `asyncpg`, `alembic` (`T-DB3`), `conftest.py` не содержит `pg_engine`, `db`, `_migrate`.

## 5. Удалено сейчас (C)

Удаляется только то, у чего код уже не существует или поведение подтверждённо покрыто другим названным тестом.

| Тест | Причина | Чем покрыто |
| :-- | :-- | :-- |
| `test_thresholds_in_settings.py::test_stream_c_defaults_equal_phase_2_values` | повторяет значения по умолчанию из `Settings` | потребители берут лимиты из `Settings`: `test_app_assembly.py::{test_actions_and_turns_rate_limits, test_search_limits_come_from_settings, test_health_probe_timeout_comes_from_settings}`, `domains/test_curation_thresholds.py` |
| `test_thresholds_in_settings.py::test_rate_limit_constants_are_gone` | «надгробие»: ищет строку `RATE_LIMIT` в `session/deps.py` | лимитёр — `test_ratelimit.py`, `test_app_assembly.py::test_actions_and_turns_rate_limits` |
| `storage/test_compute_client_math.py::test_stub_has_math_queues` | `set(MATH_PATHS) <= set(answers)` — частный случай | `storage/test_compute_client_s10.py::test_stub_covers_every_wp24_endpoint` (равенство с `{*MATH_PATHS, *WP24_PATHS}`) |
| `session/test_fixtures.py` (файл, 1 тест) | самопроверка помощника `parse_sse` | формат кадра — `test_infra.py::test_sse_format`; разбор кадров ломает каждый SSE-тест |

Проверено и **оставлено**: пары с одинаковыми названиями (`test_events_and_append` ↔ `db/test_append_events` — разный код, это B1; `dispatcher_p4` ↔ `rules_p4` по гигиене — разные слои: счётчик в базе и чистая строка таблицы; `compute_client_math` ↔ `compute_client_s10` — разные эндпоинты); тесты без точных двойников по телу (проверено по AST); пропущенных (`skip`) и `xfail`-тестов, кроме `llm/test_labels.py` (`xfail(strict)` на доктрину, ждёт владельца), нет.
