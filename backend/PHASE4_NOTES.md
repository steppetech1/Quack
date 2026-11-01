# Фаза 4: заметки, аудит и журнал решений

Файл заметок `ТЗ Фаза 4 (единое).md` (§0.5). Общие разделы ведёт поток C; в разделах потоков
пишет только сам поток. Ссылки: `Т-§`, `Д-§`, `П-`, `Ф3-§`, `P3N` — как в §0.1 ТЗ.

## Сводка для владельца

Состояние на 2026-11-02 (WP0, поток C).

- **Нужно от владельца до начала кода фазы:** письменное подтверждение в чате, что ни в одном
  окружении нет событий настоящих учеников (WP0 п.2, §0.3 «Чистый старт»). Без него фаза не
  начинается (§0.4.6, причина 1).
- **Нужно подтверждение решения класса C:** `D-C-101` — имена рабочих веток.
- `main` `9ef6e3e` зелёный (раздел «Вход»).
- \[ПРОВЕРИТЬ\] WP0 п.3а закрыт: фильтр внутри векторного индекса работает на Neo4j
  2026.10.1 Community (раздел «Вход»).

## Вход

Проверка §1.3 (поток C, 2026-11-02).

- `main` — `9ef6e3e` (#70, ТЗ Фазы 4 с поправками) поверх `958d42a` (#69). Фаза 3 (#66) и
  решения владельца (#67) влиты.
- Интеграционная ветка `phase-4` уже есть в `origin` и совпадает с `main` `9ef6e3e`; там же
  `phase-4-a`, `phase-4-b`, `phase-4-c` на том же коммите (рабочие ветки — `D-C-101`).
- Инфраструктура тестов — по `backend/README.md`: `quack-test-pg` (Postgres 16, :55432),
  `quack-test-neo4j` (:57687, **Neo4j 2026.06.0** — README и compose называют 2026.10,
  `D-A-017`; локальный контейнер старше, на итог прогона не влияет), `quack-test-redis`
  (Redis 7, :56379; compose — 8.2). Docker 29.8.1, uv 0.12.23.
- **Чистый старт (WP0 п.2): ждёт письменного подтверждения владельца.**

Прогон на `main` `9ef6e3e`:

| Где | Команда | Итог |
| :-- | :-- | :-- |
| `backend/` | `uv run ruff check .` | All checks passed |
| `backend/` | `uv run ruff format --check .` | 260 files already formatted |
| `backend/` | `uv run pyright` | 0 errors |
| `backend/` | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q` | 774 passed, 351 warnings, 631 s |
| `compute/` | `uv run ruff check .` / `ruff format --check .` | passed / 7 files already formatted |
| `compute/` | `uv run pyright --pythonplatform Linux` | 0 errors |
| `compute/` | `docker run --rm -v "<compute>:/src:ro" ghcr.io/astral-sh/uv:python3.12-bookworm-slim sh -c "cp -r /src /app && cd /app && rm -rf .venv && uv sync -q --frozen && uv run pytest -q"` | 5 passed (три прогона подряд). Первый прогон, параллельно с `pytest` backend, — 2 failed (`test_equation_roots_and_matrix` и ещё один), см. «Вопросы к команде» Q-C-1 |

### \[ПРОВЕРИТЬ\] WP0 п.3

| Пункт | Поток | Скрипт | Итог |
| :-- | :-: | :-- | :-- |
| а) `SEARCH … VECTOR INDEX … WHERE learnerId = $id AND searchable = true` (`Т-§3.4.6`, `Т-§1.10`) | C | `scripts/checks/vector_search_filter.py` | **Работает** на `neo4j:2026.10-community` (агент `Neo4j/2026.10.1`, community): индекс с `WITH [c.learnerId, c.searchable]`, запрос `CYPHER 25 MATCH … SEARCH c IN (VECTOR INDEX … FOR $q WHERE … LIMIT 30) SCORE AS score` вернул ровно узлы ученика с `searchable = true`. Запасной путь WP20.4 не нужен |
| б) составной range-индекс `(learnerId, retDueAtMs)` на `Skill` (`Т-§3.4.8`) | A | `scripts/checks/skill_due_index.py` | **Работает** на `neo4j:2026.10-community` (2026.10.1): `CREATE RANGE INDEX … FOR (s:Skill) ON (s.learnerId, s.retDueAtMs)` → `ONLINE`; запрос WP17.1 (`learnerId = $id AND retDueAtMs > 0 AND retDueAtMs <= $now`) идёт через `NodeIndexSeek` по составному индексу. Запасной путь (одиночные индексы) не нужен |
| в) параметры `py-fsrs` (`Т-§8.3`) | A | `scripts/checks/fsrs_params.py` | `py-fsrs` **6.3.2** (закрепляется в WP4): `Scheduler(parameters, desired_retention, learning_steps, relearning_steps, maximum_interval, enable_fuzzing)` — все четыре имени `Т-§8.3` совпадают; по умолчанию 21 вес (FSRS-6); при `enable_fuzzing=False`, пустых шагах и явном UTC-времени два прогона дают одинаковые сроки; первый `Good` сразу даёт `Review`. `uv run --with fsrs==6.3.2 python scripts/checks/fsrs_params.py` → код 0 |
| г) `antlr4-python3-runtime` для SymPy, `parse_latex(…, strict=True)` (`Т-§6.5`) | B | — | ждёт поток B |
| д) Meta Model API: `sam-3.1`, `muse-voice-transcribe-1.0` (`Т-§1.3`) | B | — | ждёт поток B |
| е) `READ`-сессия Neo4j отклоняет запись (`Д-§14.2`, `T-A9b`) | C (перенос) | `tests/test_neo4j_read_session.py` | **Подтверждено**: 3 passed на Neo4j 2026.06.0 (полный прогон) и на 2026.10.1 (`QUACK_TEST_NEO4J_URI=bolt://127.0.0.1:57688`) |

## Статус

| Пакет | Поток | Состояние | Тесты |
| :-- | :-: | :-- | :-- |
| WP0 (зона C) | C | частично: ждёт п.2 (владелец) | — (скрипт п.3а) |
| WP0 (зона A) | A | выполнен: аудит B1–B7, B21; п.3б, п.3в | — (скрипты п.3б, п.3в) |
| WP1.1–1.2 (A) | A | Этап I–II в `phase-4-a-wp1`: базовая миграция dbmate, `Db`, `append_events` — сигнатуры (`NotImplementedError`), параметры §6.7 потока A; тесты `T-DB1`, `T-DB2`, `T-DB4`, `T-DB5`. Красный и зелёный прогоны `T-DB1`, `T-DB5` ждут dbmate на машине A | `T-DB2`, `T-DB4`: 4 passed |

## Аудит

Пункты §3, которые подтверждает поток C (`main` `9ef6e3e`, 2026-11-02).

| # | Итог |
| :-- | :-- |
| B11 | Подтверждено: `compute/quack_compute/` — `GET /health/live`, `POST /v1/check/math`; таймаут на `multiprocessing` (`forkserver`), `pebble` нет. Часть WP24 — C, `math/**` — B |
| B14 | Подтверждено: пароль и JWT в `quack/domains/auth.py`, `quack/api/auth.py`, ссылки в `quack/cli.py`, `quack/config.py`. Пакета `quack/auth/` нет |
| B15 | Подтверждено: `quack/artifacts/` — `notes.py`, `cheatsheets.py`, `practice.py`, `search.py`, `store.py`, `router.py` |
| B16 | Подтверждено: `quack/storage/` нет, `aioboto3` в `pyproject.toml` нет |
| B17 | Подтверждено: `quack/materials/`, `quack/embeddings/` нет; `modal` нет |
| B18 | Подтверждено: `quack/canvas/` нет; `msgpack` нет |
| B22 | Подтверждено с уточнением: сеть `backend` и лимиты `Д-§4.1`, `stop_grace_period`, списки `environment:` есть; **сеть `egress` уже есть** (`compose.yaml`: у `api` и `workers`, объявлена в `networks:`) — пункт «сеть `egress`» WP9.1 уже выполнен, проверить значения при WP9. Caddy нет. CI — три job (`backend`, `compute`, `images`), job выкатки нет |
| B23 | Подтверждено: `ops` — `migrate`, `verify-schema`, `rebuild`, `replay-dlq`, `purge-deleted`, `status`, `event-registry`, `can-rollback`, `seed-learners`, `openapi`. Нет `db-roles`, `backup now`, `restore-check`, `replay-deletions`, `orphan-objects`, `alerts`, `resume` |
| B24 | Подтверждено: `AppResources` — `settings`, `engine`, `session_factory`, `neo4j`, `redis`, `llm`, `compute`; новых ресурсов и CORS-списка нет |
| B27 | Подтверждено: `loadtest/meta_mock.py` строит ответ по схеме (`minimal_instance`), без стриминга и инструментов. Часть WP26.2 — C, `meta_smoke.py` — B |

Пункты §3, которые подтверждает поток A (`phase-4` `a087529`, 2026-11-02).

| # | Итог |
| :-- | :-- |
| B1 | Подтверждено: Alembic `migrations/versions/0001_initial_schema.py`, `0002_phase3.py`; таблицы `auth.users`, `core.learner_seq`, `core.events`, `core.sessions`, `core.artifacts`, `core.domains`, `core.task_bank`, `core.llm_outbox`, `ops.jobs`, `ops.dlq`, `ops.llm_ledger`, `ops.deletion_tombstones`, `ops.projector_offsets`, `ops.event_types_seen`, `ops.dispatcher_offsets`, `ops.turn_manifests` |
| B2 | Подтверждено, пересчёт: `sqlalchemy`/`asyncpg` импортируют **41** модуль `quack/` и **61** файл `tests/`; `loadtest/` — 0 |
| B3 | Подтверждено: `quack/events/catalog.py` — 36 типов |
| B4 | Подтверждено: `quack/graph/` — `phase_a.py`, `folds.py`, `topic_display.py`; в коде `p_mastery`, `PART_OF`, `PREREQ_OF`, `DEPENDS_ON`, `appliedSeq`, `rules@2` |
| B5 | Подтверждено: `quack/graph/evidence_weights.py`, `evidence_validation.py` есть |
| B6 | Подтверждено: `quack/jobs/dispatcher.py`, `dispatcher_rules.py` есть |
| B7 | Подтверждено: `quack/jobs/queue.py` — числовой `priority`, `attempt`/`max_attempts`, `run_after`, статусы `queued`/`running`/`done`/`failed`/`cancelled`, `ops.dlq`; нет `visible`, `cancel_requested`, `skipped_budget`, `max_jobs_per_learner` |
| B21 | Подтверждено: `quack/domains/learner.py` пишет `ops.deletion_tombstones` (`pending_purge`); зачистка — `quack/ops/purge_job.py` |

## Реестр отклонений от основных документов

Как в ТЗ Фазы 4 §2.4 (R4-1…R4-4). Новых строк нет.

## Журнал решений

### Поток A

### D-A-101. Размер пула `Db`
- Класс: B. WP1.2, `quack/config.py`: `pg_pool_min_size` 1, `pg_pool_max_size` 10 на процесс. Размеров нет ни в `Т-§`, ни в `Д-§`; числа в коде запрещены (§0.3). Варианты: (а) 1/10; (б) как в Фазе 3 (10 + 5 сверх). Решение (а) — консервативно (§0.4.3, последний абзац). Обратимость: значения параметров. Тесты: `T-DB5` (работает на пуле). Подтверждение: нет.

### D-A-102. `projector_batch_size` и `rebuild_max_tx_ops` 200 000 — вместе с WP3
- Класс: B. §6.7 требует переименовать `projector_batch_max_events` → `projector_batch_size` и поднять `rebuild_max_tx_ops` до 200 000. Оба читают проектор и пересборка Фазы 3, `quack/cli.py` (C) и тесты Фазы 3 (`test_worker_and_ops.py`, `test_infra.py` проверяет 5000). Варианты: (а) сейчас — правка файлов C и утверждения чужого теста; (б) в WP3, когда старый проектор заменяется и его потребители уходят. Решение (б). Почему: §0.4.3 п.3, п.5; §0.4.4 п.14. Обратимость: имя и значение параметра. Подтверждение: нет.

### D-A-103. Переходный период: две схемы Postgres до WP1.3
- Класс: C
- WP / место: WP1.1–1.3; `backend/db/migrations/`, `quack/db/db.py`, `quack/events/log.py`, `quack/events/registry.py`, `tests/db/fixtures.py`
- Контекст: WP1.1 (чистый старт) создаёт таблицы с теми же именами, что у Фаз 1–3, но с другими колонками (`core.events`, `ops.jobs`, `ops.event_types_seen`, `ops.projector_offsets`, `ops.dispatcher_offsets`, `ops.turn_manifests`), а каталог WP2 убирает 36 типов `С-§5`. WP1.3 при этом оставляет старые модули B и C на SQLAlchemy до G3, и им нужны старые таблицы и старый каталог; `phase-4` должна быть всегда зелёной (§7.4). ТЗ не говорит, как обе схемы живут одновременно.
- Варианты: (1) заменить схему сразу — старый код и ~770 тестов B и C падают, чинить пришлось бы в чужих зонах (§0.4.4 п.14); (2) новые таблицы под временными именами — нарушает контракт §6.1; (3) две схемы до WP1.3: новая — dbmate из `backend/db/migrations/` в отдельной базе (в тестах — `<test db>_p4`, фикстуры `p4_db`, `pg_db`, `create_learner` в `tests/db/fixtures.py`), новый код (`quack.db.Db`, `quack.events.log.append_events`, `quack.events.registry`) — рядом со старым (`quack.db.postgres`, `quack.events.append`, `quack.events.catalog`). Alembic, старый каталог и старые модули удаляются в WP1.3 вместе с последним потребителем; переключение `ops migrate`, compose и CI на dbmate — WP9 (C) в момент перехода.
- Решение: (3).
- Почему: §0.4.3 п.1 (ничего не теряется и не ломается), п.3 (интерфейсы швов S2/S3 те же), п.4 (снимается удалением старых модулей).
- Обратимость: удалить отдельную базу и фикстуры; схема и код Фазы 4 от этого не меняются.
- Тесты: `T-DB1`, `T-DB5`, `tests/db/fixtures.py`.
- Требует подтверждения владельцем: да. Шов S2 — нужно согласие B и C (§0.4).

### D-A-104. Колонки базовой миграции, которых §6.1 не уточняет
- Класс: B. WP1.1, `backend/db/migrations/20261102000001_baseline.sql`. После G0 добавить `NOT NULL` нельзя (только расширяющие миграции), поэтому `NOT NULL` — у ключей, `learner_id`, времени и статусов, а ссылки, без которых строка осмысленна (`domain_id`, `topic_id`, `hub_id`, `session_id`), — `NULL`. `core.learners.email` — `NOT NULL`, уникальный, `CHECK` на нижний регистр. `ops.jobs.job_key` — первичный ключ; `ops.jobs.learner_id` — `NULL` только у периодических задач WP7.5. `ops.turn_manifests` — ключ `manifest_id` (ссылка `turn.committed.manifest_id`; `turn_id` в Фазе 4 нет), `reasoning_effort` Фазы 3 → `effort`. `ops.llm_usage` — токены `input`, `cached_input`, `output`, `reasoning` (виды таблицы цен §1.3 плюс рассуждение), `cost_usd numeric(12,6)`. `ops.event_types_seen.first_seen_at` и `updated_at` у отметок — как в Фазе 3. Внешних ключей нет (как в Фазе 3; удаление WP25 идёт по `learner_id`). Схема `langgraph` не создаётся (`Т-§22.3`, §2.2). Почему: §0.4.3, «частые случаи», строка 1; п.4. Обратимость: до G0 — правка baseline, после — расширяющая миграция. Тесты: `T-DB1`. Владельцам таблиц B и C — проверить свои до G0. Подтверждение: нет.

### D-A-105. `append_events` не заводит строку ученика
- Класс: B. WP1.2, `quack/events/log.py`. `Т-§2.3`: «блокирует строку ученика (SELECT … FOR UPDATE)»; Фаза 3 заводила `core.learner_seq` лениво. Решение: нет строки `core.learners` → `LearnerNotFoundError`; строку заводит вход (WP18, C), в тестах — `tests.db.fixtures.create_learner`. Почему: §0.4.3 п.2 (буква `Т-§2.3`), `email NOT NULL` (D-A-104). Обратимость: одна ветка функции. Тесты: `test_learner_row_is_required`. Подтверждение: нет.

### D-A-106. Формат `agent:<kind>` в `actor`
- Класс: B. WP2.1, `quack/events/actor.py`: `agent:` и имя в `snake_case` (`^agent:[a-z][a-z0-9_]*$`), как имена ролей; формы `kind` документ не задаёт. Обратимость: одно регулярное выражение. Тесты: `test_actor_versions_and_agents`. Подтверждение: нет.

### Поток B

### Поток C

### D-C-101. Имена рабочих веток
- Класс: C
- WP / место: §7.4, ветки git
- Контекст: §7.4 задаёт интеграционную ветку `phase-4` и рабочие `phase-4/c-<тема>`. Git не
  может держать ветку `phase-4` и ветки `phase-4/…` одновременно (конфликт ссылок: `phase-4` —
  файл, `phase-4/` — каталог). В `origin` уже есть `phase-4`, `phase-4-a`, `phase-4-b`, `phase-4-c`.
- Варианты: (1) рабочие ветки `phase-4-c-<тема>`, как в Фазе 3 (`phase-3-c-…`);
  (2) переименовать интеграционную ветку; (3) работать только в `phase-4-c`.
- Решение: (1) — `phase-4-<поток>-<тема>`, PR в `phase-4`.
- Почему: §0.4.3 п.3 и п.7 — интеграционная ветка и уже созданные ветки не меняются,
  соглашение Фазы 3.
- Обратимость: имя ветки, ничего в коде.
- Тесты: нет (процесс).
- Требует подтверждения владельцем: да

## Заблокировано

## Красные прогоны

## Правки тестов

## Вопросы к команде

- **Q-A-1 (A → B, класс C, шов S3).** Каталог событий живёт в ядре (`quack.events`, §4.1), и импортировать модели B из `quack.practice`, `quack.agents`, `quack.session` он не может (слои §4.1, `T-A6`). Кроме того, разметка `turn.committed` хранит id, а `TurnMarkup` модели — метки. Предложение A: модели нагрузки — в каталоге (`quack.events.registry`, S3), поля — по моделям B (`Verdict`, `CheckFailed`, `ExtractedElement`, `TurnMarkup` с id, `LearnerAnswer`); B импортирует их из ядра вместо своих копий или держит свои модели, а тест-контракт сверяет поля. Нужен выбор B до G1.

- **Q-C-1 (C → владелец, к сведению B).** `compute/` на `main`: в первом прогоне в контейнере,
  запущенном одновременно с полным `pytest` backend, упали 2 теста из 5
  (`test_equation_roots_and_matrix` и ещё один); три следующих прогона — 5 passed. Похоже на
  таймаут проверки под нагрузкой (`multiprocessing` с таймаутом). WP24 переводит пул на
  `pebble` — проверить там; до этого не трогаю.

## Для фронтенда

Снимок `backend/openapi.json` и таблица «старый эндпоинт → новый» (§6.4) — на каждом гейте.

## DoD

Таблица §10 — заполняется к G4.
