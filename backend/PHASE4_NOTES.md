# Фаза 4: заметки, аудит и журнал решений (архив до 2026-11-03)

> **Архив.** С 2026-11-03 новые решения пишутся в `backend/notes/<A|B|C>.md`, сводка всех решений —
> `documentation/Фаза 4 — решения (единое).md`, состояние пакетов — `STATUS.md`. Сюда новые записи не добавляются.

Файл заметок `ТЗ Фаза 4 (единое).md` (§0.5). Общие разделы ведёт поток C; в разделах потоков
пишет только сам поток. Ссылки: `Т-§`, `Д-§`, `П-`, `Ф3-§`, `P3N` — как в §0.1 ТЗ.

## Сводка для владельца

Состояние на 2026-11-02 (WP0, поток C).

- «Чистый старт» (WP0 п.2) подтверждён владельцем 2026-11-02 (раздел «Вход»).
- `D-C-101` и `D-C-106` подтверждены владельцем 2026-11-02 (передано потоком B).
- **Нужны значения:** `terms_version`, `auth_success_redirect_url` (`D-C-105`).
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
- **Чистый старт (WP0 п.2): подтверждён владельцем в чате 2026-11-02** — событий настоящих учеников нет ни в одном окружении; локальные стенды `quack-*` и `quack-test-*` тестовые. Дословно (передано потоком B): «у нас пока никаких данных учеников нет. остальные решения если за мной какие есть - сверься с тз и тз доком, дай решение».

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
| г) `antlr4-python3-runtime` для SymPy, `parse_latex(…, strict=True)` (`Т-§6.5`) | B | `scripts/checks/wp0_3g_antlr.py` | SymPy 1.14.0 требует **4.11.x**; `strict` есть; с 4.11.1 строгий разбор работает. В `compute` рантайма пока нет — закрепляется в WP11.5. Подробно — ниже, «Поток B: подробности п.3г, п.3д» |
| д) Meta Model API: `sam-3.1`, `muse-voice-transcribe-1.0` (`Т-§1.3`) | B | — (только документация: вызов запрещён п.3д, скрипту нечего проверять) | `sam-3.1` — `POST /v1/responses`, ответ спецтокенами; голос — `POST /v1/asr/transcribe`, `multipart/form-data`, WAV. Подробно — ниже, «Поток B: подробности п.3г, п.3д» |
| е) `READ`-сессия Neo4j отклоняет запись (`Д-§14.2`, `T-A9b`) | C (перенос) | `tests/test_neo4j_read_session.py` | **Подтверждено**: 3 passed на Neo4j 2026.06.0 (полный прогон) и на 2026.10.1 (`QUACK_TEST_NEO4J_URI=bolt://127.0.0.1:57688`) |

#### Поток B: подробности п.3г, п.3д

**г) ANTLR для SymPy (`Т-§6.5`).** SymPy закреплена 1.14.0 (`compute/uv.lock`). Она требует
`antlr4-python3-runtime` **4.11.x**: проверка `version('antlr4-python3-runtime').startswith('4.11')` в
`sympy/parsing/latex/_parse_latex_antlr.py`. `parse_latex` принимает `backend` и `strict`. С 4.11.1
строгий разбор `\frac{x^{2}}{2} + \sin(2x)` даёт `x**2/2 + sin(2*x)`, незакрытая дробь отклоняется.
Сейчас рантайма в `compute` нет — разбор LaTeX падает `ImportError`. Закрепить
`antlr4-python3-runtime==4.11.1` в `compute/pyproject.toml` — в WP11.5 (B, §4.6).
Скрипт: `backend/scripts/checks/wp0_3g_antlr.py`.
`uv run --project compute --with "antlr4-python3-runtime==4.11.*" python backend/scripts/checks/wp0_3g_antlr.py`
→ код 0; без `--with` → код 1.

**д) `sam-3.1` и `muse-voice-transcribe-1.0` (`Т-§1.3`).** Только по документации Meta, без вызовов
(`dev.meta.ai/docs/models`, `/docs/media-segmentation`, `/docs/sam/segmenting`,
`/docs/sam/reading-segmentation`, `/docs/speech-to-text`, `/docs/api-reference/voice/transcribe`;
прочитано 2 нояб. 2026). Живая проверка — DoD п.6a.

- `sam-3.1` — `POST /v1/responses`, `model: "sam-3.1"`. `input` — одно сообщение `role: user`:
  `input_text` (короткое имя предмета **или** токены рамки/точки, не вместе; рамка:
  `<0f>1<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>`) и `input_image` (`image_url`: публичный URL или
  `data:image/png;base64,…`). `stream` необязателен. `metadata` (строки): `mask_encoding`
  (`lossless` по умолчанию / `one_bit`), `include_confidence` (`"true"`/`"false"`).
  Ответ — текст спецтокенов в `output_text`: строка на кадр `<Nf>`, записи «id, рамка, маска»:
  `<|box;x1;y1;x2;y2;w;h[;c]|>` в абсолютных пикселях, `<|mask;x=0;y=0[;c];data=H,W,<~|!>base85|>`.
  Пусто — совпадений нет. До 16 объектов на запрос. Лимиты размера и форматов изображения документация
  не называет.
- `muse-voice-transcribe-1.0` (срез `Т-§22.8` — отрезками, файловый путь) — `POST /v1/asr/transcribe`,
  **не** через `/responses`. `multipart/form-data`: часть `request` (JSON: `model`,
  `audioEncoding: "WAV"`, `mode`: `PUSH_TO_TALK` по умолчанию / `ENDPOINTING` / `DIARIZATION`,
  необязательные `languageBias`, `keywords`) и часть `audio` (WAV: моно, 16-бит PCM, 16 или 24 кГц).
  До 10 мин, тело до 32 МБ, `?sessionId=` — необязательный id корреляции. Ответ JSON
  `TranscribeResponse`: `sessionId`, `transcript`, `audioDurationMs`, `turns[]` {`turnId`, `startMs`,
  `endMs`, `transcript`, `speaker`}; в `PUSH_TO_TALK` `turns` пусты. Ошибки: 400, 406, 413, 429
  (лимит одновременных или часовых сессий), 500.

Следствия: адаптеры WP10.1 — `sam-3.1` через тот же `/responses` с разбором спецтокенов, голос — отдельный
HTTP-адаптер шлюза (допуск, учёт, трасса, размыкатель — общие). Для C (WP21.6): звук из браузера нужно
привести к WAV моно 16-бит 16/24 кГц; время отрезка для привязки к строкам (`Т-§15.6`) даёт клиент —
в `PUSH_TO_TALK` внутренних отметок нет.

## Статус

| Пакет | Поток | Состояние | Тесты |
| :-- | :-: | :-- | :-- |
| WP0 (зона C) | C | частично: ждёт п.2 (владелец) | — (скрипт п.3а) |
| WP0 (зона A) | A | выполнен: аудит B1–B7, B21; п.3б, п.3в | — (скрипты п.3б, п.3в) |
| WP2 (A) | A | **S3 заморожен (G1)** в `phase-4-a-s3-freeze` (D-A-119): 65 типов §6.3, общие модели B в ядре (Q-A-1 (а)), ключи, пометки обработчиков и правил; Q-A-2, Q-A-3 закрыты | `tests/events`: 82 passed (без инфраструктуры) |
| WP6.4 (A), S4 | A | сигнатуры, модели ответов и заглушка `FixedGraphReader` (D-A-111); `MisconceptionFact.statement` (D-B-145) — `phase-4-a-g1`; факты картины памяти (D-A-126); помощь у свидетельства (D-A-127); реализация — G2 | `tests/graph/test_read_stub.py`: 12 passed |
| WP7.1 (A), S6 | A | виды задач и таблица правил по событию (D-A-110); строка `known_check` и отмена поручений (D-A-115) — `phase-4-a-g1`; строки с данными сверх события, диспетчер на `Db`, эпизоды, очередь и исполнитель WP7.1–7.4 — `phase-4-a-wp7-gaps` (D-A-160…D-A-163, аудит выше); сборка `workers`/`projector` — WP1.3 | `tests/jobs`: 114 passed (без `test_coverage.py`, нужен dbmate) |
| S5 (A), WP3.2 таблица | A | строки обработчиков проектора на 38 типов в `phase-4-a-s5` (D-A-125); обработчики — G2 | `tests/graph/test_projector_rows.py`: 3 passed |
| WP15 швы A (п.4–5 сообщения B), WP7.3 | A | `check_extracted` и `mark_covered` в `phase-4-a-wp15-seams` (D-A-124) | `tests/graph/test_extracted_check.py`, `tests/jobs/test_coverage.py`: 16 passed |
| S1 (A), `JobContext` | A | поля §4.2 `db`, `storage`, `embeddings`, `deletions` в `phase-4-a-g1` (D-A-114) | `tests/jobs/test_job_context.py`: 2 passed |
| WP3.1 (A) | A | схема Neo4j Фазы 4 в `quack/db/neo4j.py`: уникальность `id` для Hub, Option, Material, Chunk, Digest, Profile; range по `learnerId` на метках, где его ещё нет; составной `(learnerId, retDueAtMs)` на Skill; пять векторных индексов с фильтрующими свойствами и `embedding_dim`; сосуществует с индексами Фазы 3 до WP1.3. WP3.2 и WP3.3 (фаза А) выполнены в `phase-4-a-wp3-projector` (D-A-129…D-A-134, D-A-180, D-A-181): обработчики всех 38 строк S5, запись разницы в Neo4j, цикл пачки на `Db`, остановка потока и `resume`. Фазы Б–В — с WP4–WP6; WP3.5, WP3.6 — дальше, фазы Б–В, режимы, пересборка — дальше | `tests/graph/test_schema_wp31.py` + `test_infra.py`: 4 passed (Neo4j 2026.06.0) |
| WP1.1–1.2 (A) | A | выполнен в `phase-4-a-wp1` (шов S2): базовая миграция dbmate, `Db`, `append_events`, параметры §6.7 потока A. WP1.4 (`.squawk.toml`, S16) — к G2; WP1.3 — к G3/G4 | `tests/db`: 19 passed |
| WP3.1 (A) | A | схема Neo4j Фазы 4 в `quack/db/neo4j.py`: уникальность `id` для Hub, Option, Material, Chunk, Digest, Profile; range по `learnerId` на метках, где его ещё нет; составной `(learnerId, retDueAtMs)` на Skill; пять векторных индексов с фильтрующими свойствами и `embedding_dim`; сосуществует с индексами Фазы 3 до WP1.3. Остальное WP3 (обработчики, фазы А–В) — дальше | `tests/graph/test_schema_wp31.py` + `test_infra.py`: 4 passed (Neo4j 2026.06.0) |
| WP1.1–1.2 (A) | A | выполнен в `phase-4-a-wp1` (шов S2): базовая миграция dbmate, `Db`, `append_events`, параметры §6.7 потока A; расширяющая миграция `core.pairing_tokens` (D-A-128). WP1.4 (`.squawk.toml`, S16) — к G2; WP1.3 — к G3/G4 | `tests/db`: 20 passed |
| WP0 (зона B) | B | выполнен: аудит, п.3г, п.3д, п.5 | — (скрипт п.3г) |
| Этап I G1 (B: S7, S8, S11, S13) | B | контракты в `phase-4-b`, тесты контрактов зелёные | см. «Красные прогоны» |
| WP10.2–10.3 (B) | B | частично: разбор потока хода и вход шага цикла с инструментами — зелёные (`T-AI2`–`T-AI4`); адаптер `ResponsesClient.stream_structured` и цикл `run_tool_loop` (параллельные инструменты с таймаутом, повтор оборванного с удвоенным лимитом) — зелёные (`T-AI5`, `T-AI6` без строки `ops.llm_usage` — она в WP10.4); встраивание в шлюз (допуск, семафоры, учёт) — с WP13 | 19 passed |
| WP11 (B) | B | частично: `check()` мгновенные типы (WP11.2) — зелёный `T-CH1`, включая «в той же транзакции» (`record_answer`, WP11.4 — ветка `phase-4-b-wp12` поверх S2/S3 A); быстрые — с каркасом compute (S10); медленные — WP11.6 | 11 + 4 passed |
| WP12.1–12.3 (B) | B | частично: `validate_markup` (WP12.3) — зелёный `T-TK2`; `content.tasks` + `task.created` (WP12.1, `quack/practice/task_store.py`) — зелёный `T-TK1` в ветке `phase-4-b-wp12` поверх S2/S3 A (#78, #80); PR — после их слияния | 14 + 4 passed |
| WP10.5 промпты (B) | B | частично: 16 промптов ролей B в `quack/prompts/` (слой 2 по схемам кода), `manifest.json`, `PROMPT_CONTRACTS.md`; `T-M5` на все роли; слой 1 у всех 16 (8 — черновики стенда, D-B-132); стенд на Meta API — §«Промпты», «Стенд B на Meta API»; роли C (`recognizer`, `canvas_diagnoser`, `material_parser`, `artifact_writer`) — слой 2 пишет C | 73 passed (`tests/llm`); весь набор без инфраструктуры — 578 passed, 306 skipped |

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

Пункты §3, которые подтверждает поток B (`main` `9ef6e3e`, 2026-11-02).

| # | Итог | Что видно в коде |
| :-- | :-- | :-- |
| B8 | подтверждено | `quack/llm/responses_client.py`: `complete_structured` без `stream` и без `tools`; семафоры, давление, размыкатель, повторы, `teacher_fallback` — в `gateway.py`, `pressure.py`; учёт — `ops.llm_ledger` в токенах |
| B9 | подтверждено | `quack/session/schemas.py`: `TeacherOutput` плоский (`reply_text`, `pedagogical_move`); ход в `turns.py` без LangGraph; гард спойлера по полному тексту (`spoiler_guard.py`) |
| B10 | подтверждено | `quack/practice/evaluation.py` — каскад Фазы 3; `selection.py` — подбор из `core.task_bank` |
| B11 | подтверждено | `compute/quack_compute/app.py`: только `POST /v1/check/math` и `/health/live`; каталога `math/` нет |
| B12 | подтверждено | `quack/session/extraction.py`, `workers.py`: извлечение по ходу, `ExtractorOutputV2` с перечнями `extractor@2` |
| B13 | подтверждено | `quack/domains/structure.py` (интервью структуры), `diagnostic.py` (диагностика), `curation.py` (задача `graph.curate`, роль `curator@1`) — есть. Удаляются по §2.3 строка 5; сверка и гигиена — WP16.3 (B); сами модули `domains` — зона A |
| B20 | подтверждено | `quack/practice/generation.py` (`build_generated_task`), роль `task_gen@1`; `task_checker`/`task_arbiter` нет |
| B25 | подтверждено | `quack/llm/labels.py`: метки `S`/`T`/`K`/`M`, таблица в `ops.turn_manifests` |
| B27 | подтверждено | `backend/scripts/meta_smoke.py` — роли Фазы 3, без стриминга и инструментов |

### Аудит WP7.1–WP7.4 (раздел A; ветка `phase-4-a-wp7-gaps`)

Сверка `ТЗ Ф4` WP7.1–WP7.4 (`Т-§7.5`, `Т-§7.10`, `Т-§13.1`–`Т-§13.2`, `Т-§13.8`) с кодом `quack/jobs/`. «Есть» — реализовано и покрыто тестом; «частично» и «нет» — с причиной. Все тесты ниже идут на Postgres (частная база, без dbmate, D-A-163), кроме чистых; Neo4j и Redis им не нужны.

| Пункт ТЗ | Есть? | Где / тест |
| :-- | :-: | :-- |
| WP7.1 строки по событию: `episode.closed`, `task.closed`, `session.ended`, `answer.submitted` (быстрый/медленный), `check.failed`, `turn.committed`, `observation.diagnosed`, `photo.uploaded`, `material.uploaded`, `task.created`, `agent.requested`, `today.shown`, проявление заблуждения, `known.claimed`, `episode.reassigned`, `digest.written`, `handwriting.corrected`, `hub.deadline_set` | есть | `rules_p4.py` (G1, D-A-110, D-A-139); `test_rules_p4.py` |
| WP7.1 `skill.split` → `extract.episode` по эпизодам с свидетельствами старого навыка | есть (этот PR) | `rules_p4._skill_split_reextract`, `facts_p4.skill_split_episodes`; `test_rules_p4`, `test_dispatcher_p4::test_skill_split_reextracts_…` |
| WP7.1 `artifact.topic_refresh` (`session.ended`; `task.closed` с подсказками): правило | есть (#154) | `rules_p4`; тест правила в #154 |
| то же: сбор домашних тем из журнала | есть (этот PR) | `facts_p4.session_topic_ids`, `hinted_task_topic_ids`; `test_session_end_refreshes_…`, `test_closed_task_with_hints_…`, `…_with_no_exposure_…` |
| WP7.1 `artifact.cheatsheet` | есть (#154, D-A-139) | строка по событию; вне этого PR |
| WP7.1 `embedding.compute`: кусок, заблуждение | есть (этот PR) | `rules_p4._chunk_indexed`, `_misconception_described`; `test_embedding_rows_…`, `test_embedding_jobs_carry_…` |
| то же: тема, навык, свидетельство | **нет** | свойство для эмбеддинга темы/навыка — \[ПРОВЕРИТЬ\] (D-A-117); у свидетельства id даёт проектор, в событии его нет. Строка `*` осталась в `ROWS_NEED_FACTS` (D-A-162) |
| WP7.1 `graph.hygiene`: `session.ended` с временными сущностями; каждые `graph_hygiene_every_topics` тем (`counters`) | есть (этот PR) | `rules_p4._session_hygiene`, `_topics_hygiene`, `dispatcher_p4._facts`; `test_graph_hygiene_…`, `test_hygiene_every_n_new_topics_…` |
| WP7.1 `required_seq` = событие-триггер, `NULL` у читающих журнал | есть | `rules_p4._plan`; `test_job_waits_for_the_projector` (захват) |
| WP7.1 ключ задачи детерминирован, `{coalesce_key}:{seq}`; `ON CONFLICT DO NOTHING` по `job_key`; склейка `GREATEST` | есть | `queue_p4.enqueue`; `test_enqueue_is_idempotent_by_job_key`, `test_coalesce_raises_required_seq_…`, `test_concurrent_coalescing_inserts_…` |
| WP7.1 счётчики «каждые N» в `ops.dispatcher_offsets.counters` | есть | `dispatcher_p4._Counters`; `test_hygiene_every_n_new_topics_…`, `test_open_episode_of_a_session_is_kept_…` |
| WP7.1 / T-D1: видов без правила нет | есть | `test_every_non_periodic_kind_has_a_row`, `test_dispatcher_column_of_the_catalog_is_covered_by_rows_or_episodes` (четыре метки каталога без строки — вопрос владельцу, D-A-162) |
| WP7.2 `episode.closed` диспетчером, ключ `episode:{session}:{to_seq}`, `append_events` в транзакции отметки | есть | `dispatcher_p4._write_episode_closed`; `test_task_closed_closes_the_episode_…`, `test_a_replay_of_the_log_adds_…` |
| WP7.2 границы: `task.closed`, `head.intent`/шаг плана, `episode_max_turns`, `session.ended` | есть | `episodes.EpisodeTracker`; `test_episodes.py`, `test_session_ended_and_turn_limit_…`, `test_max_turns_closes_…` (T-D2) |
| WP7.2 `episode_idle_min`: периодическая проверка | есть | `dispatcher_p4.close_idle_episodes` (`dispatcher.episode_idle`) + разрыв по времени событий; `test_idle_episode_is_closed_…`, `test_idle_check_waits_for_unread_events` |
| WP7.2 открытый эпизод сессии — в `counters` | есть | `test_open_episode_of_a_session_is_kept_…` |
| WP7.2 `has_content = false` → покрытие `nothing_to_extract`, задачи нет | есть | `episodes.episode_has_content`, `dispatcher_p4._apply_rules`; `test_episode_without_content_is_covered_…` (T-D3) |
| WP7.2 окно извлечения (`episode_context_turns`) | не зона A | собирает задача WP15 (B) по номерам из события |
| WP7.3 `ops.coverage`, запись вместе с наблюдением | есть (D-A-124) | `coverage.mark_covered`; `test_coverage.py` (нужен dbmate — здесь не прогонялся) |
| WP7.3 «нет покрытия — диспетчер ставит задачу» | частично | задача ставится на `episode.closed`; обхода «эпизоды без покрытия» (потерянная задача) нет — потребителя ТЗ не называет |
| WP7.3 `nothing_to_extract` новой версией не переобрабатывается | частично | запись есть и ключ включает версию; задачи «переобработка по смене версии» в ТЗ нет — защита нужна ей, не диспетчеру по событию |
| WP7.4 поля `ops.jobs` ровно `Т-§13.1`, частичные уникальные индексы | есть (G0) | `db/migrations/…baseline.sql`; `queue_p4` пишет и читает именно их |
| WP7.4 захват: `queued`, `run_at`, `ORDER BY priority_class, created_at`, `SKIP LOCKED` | есть | `queue_p4.claim_next`; `test_claim_orders_by_class_then_age`, `test_future_run_at_…`, гонки: `test_concurrent_claims_…` (3) |
| WP7.4 допуск классов (давление моделей роли вида) | есть | `executor_p4.JobWorker.claimable_kinds` → `kinds`; `test_worker_runs_only_kinds_…` |
| WP7.4 `required_seq` против `projector_offsets.seq` | есть | `test_job_waits_for_the_projector`, `test_projector_of_another_learner_…` |
| WP7.4 одна задача на `lock_key` | есть | `test_one_running_job_per_lock_key`, `test_lock_is_held_while_cancel_is_requested`, `test_concurrent_claims_respect_one_lock_key` |
| WP7.4 `max_jobs_per_learner` | есть | `test_max_jobs_per_learner` (T-D5), `test_cancel_requested_job_still_counts_…`, `test_concurrent_claims_never_exceed_the_cap_…` |
| WP7.4 пульс 15 с, чистильщик 60 с, плавная остановка | есть | `executor_p4.run_claimed`, `JobWorker`, `queue_p4.reap_stale`; `test_executor_p4.py`, `test_stale_worker_cannot_finish_…` |
| WP7.4 повторы до `job_max_attempts`, пауза растёт с разбросом; `failed` + `agent.completed {ok: false}`; `ops.dlq` нет | есть | `queue_p4.fail_or_retry`; `test_retry_with_growing_pause_…` (T-D8), `test_failure_writes_the_event_once`, `test_failure_commits_nothing_…` |
| WP7.4 отмена: ждущая → `cancelled`, работающая → `cancel_requested` → пульс → `cancelled` | есть | `queue_p4.request_cancel`, `executor_p4`, правило `turn.committed` (D-A-115); `test_cancel_queued_and_running`, `test_cancel_requested_is_seen_at_the_pulse_…`, `test_plan_update_cancels_the_requests_…` (T-D6) |
| WP7.4 `skipped_budget` для P2/P3 сессии; извлечение и аналитик вне бюджета | есть | `queue_p4.claim_next`; `test_skipped_budget` (T-D7) |
| WP7.4 дневной потолок `learner_daily_budget_usd` | не в очереди | проверяет шлюз при вызове (WP10.4, отложено владельцем до G3) |
| WP7.4 `visible` по виду; плашка `GET /v1/sessions/{id}/jobs` | частично | поле и запрос `queue_p4.visible_jobs` — есть (`test_visible_jobs_of_the_session_only`, T-D10 в части запроса); объявление маршрута — `quack/session/router.py` (B, §7.3 п.3); сообщение `jobs_changed` при смене статуса — нет: канал и издатель в §4.8 не названы (вопрос C) |
| WP7.5 `dispatcher.episode_idle` | есть | см. выше; остальные периодические задачи — G3 |
| Сборка `workers`/`projector` на `DispatcherP4`/`JobWorker`, `HANDLER_MODULES` | нет | переход со старого стека — WP1.3 (D-A-103); модули лежат рядом со старыми |

## Промпты

Поток B, WP0 п.5.

Пакета доктрины владельца (слой 1, §7.6) для ролей Фазы 4 в репозитории нет. В `quack/prompts/` лежат
только промпты Фазы 3: `teacher@1`, `extractor@1`, `extractor@2`, `evaluator@1`, `task_gen@1`,
`curator@1`, `artifacts@1`, `structurer@1`, `diagnostic@1`.

Не хватает доктрины (слой 1) для всех ролей §6.6 / §7.6:

- B вызывает: `teacher@2` (и `teacher_fallback`), `extractor@3`, `verdict_free@1`, `verdict_proof@1`,
  `second_opinion@1`, `task_generator@1`, `task_checker@1`, `task_arbiter@1`, `solution_space@1`,
  `analyst@1` (`analyst_deep`), `builder@1`, `misconception_describer@1`, `today_planner@1`,
  `domain_classifier@1`, `viz_builder@1`, `release_judge@1`;
- C вызывает (слой 2 пишет C, §7.3 п.2): `recognizer@1`, `canvas_diagnoser@1`, `material_parser@1`,
  `artifact_writer@1`;
- не текстовые (`segmenter`, `voice`, `illustrator`) промпта-доктрины не требуют.

Решение владельца (2 нояб.): промпты поток B не пишет; владелец передаёт заготовки, B их только
дорабатывает под схемы (слой 2). Схемы выходов ролей (код, шов S8) B пишет сам. Требования к
промптам — реестр промптов Фазы 4 владельца (в репозитории его нет: `documentation/**` правит только владелец, §2.1). Его открытые
вопросы, которые закрывает B: п.4 (раскладка полей `extractor@3`, `Element`, `planUpdate`, дедлайн,
`params`) — закрыты кодом (D-B-107…D-B-110, имя `new_misconception` взято из реестра); п.6 (выходы
`task_checker`, `task_arbiter`, `today_planner`, `viz_builder`, `release_judge`) — D-B-113; п.3
(видит ли `second_opinion` первичный вердикт) — D-B-114.

Тексты Фазы 3 (`teacher@1`, `extractor@2`, `evaluator@1`, `task_gen@1`) — доктрина под старые
схемы; переносить их в новые роли без решения владельца нельзя (§0.4.4 п.13). Запрос владельцу —
в сигнале потока B. Перечни `intent` и `approach` выхода хода (WP13.3) тоже ждут доктрину:
до неё стоят временные значения (D-B-104).

**Заготовки владельца (2 нояб.).** Владелец передал пакет заготовок стенда (Baseten,
`muse-spark-open-1026`; папка `drive-download-20261102T020137Z-1-001`, вне репозитория) и реестр
промптов `documentation/SYSTEM_PROMPTS_PHASE4_REGISTRY.md` (версия 2 нояб., 792 строки, с таблицей
исправлений §0). Результатам стенда поток B не доверяет: аудит пакета (`AUDIT_REPORT.md` в нём) и
сверка B показали, что схемы Фазы 4 на стенде не проверялись (контракт вырезался или подменялся
схемами Фазы 3), прогоны — по одному разу с `temperature`, holdout частично продублирован в main.
Поэтому из пакета взят только слой 1, а слой 2 написан заново по схемам кода (S8), см. D-B-138.

Решения владельца в чате 2026-11-02 (передано потоком B):

1. Слой 1 `teacher@2` — `teacher/v6.md` пакета как есть: это одобренная доктрина Фазы 3, текст
   совпадает с разделами 1–6 `teacher@1` (отличие только в переводах строк). Черновик стенда
   `teacher/v2.md` (Фаза 4) не берётся: правила подогнаны под сценарии holdout.
2. Заготовки, которые по смыслу годятся, ставятся дословно (слой 1), в слой 2 переносятся только
   правила формата (D-B-143); неточности — вопросами владельцу ниже. Одобрение текста — на ревью PR.
3. Заготовки, противоречащие ТЗ и схемам, не ставятся: роль на заглушке, тест доктрины —
   `xfail(strict=True)` с номером `BLOCKED-B-…` (§0.4.3, §7.6 п.5).

| Промпт | Слой 1 — откуда | Состояние |
| :-- | :-- | :-- |
| `teacher@2` (и `teacher_fallback`) | `teacher/v6.md` = `teacher@1` разд. 1–6 | доктрина владельца |
| `extractor@3` | `extractor/v3.md` до «Layer 2» | заготовка дословно |
| `verdict_free@1` | `verdict_free/v1.md` без разд. 4 (D-B-143) | заготовка |
| `verdict_proof@1` | `verdict_proof/v1.md`, п.9 без правила префикса (D-B-143) | заготовка |
| `second_opinion@1` | `second_opinion/v1.md`, п.5 без правила префикса (D-B-143) | заготовка |
| `task_generator@1` | `task_gen@1` разд. 1–8 (= `task_generator/v3.md` без двух фраз формата, D-B-139) | доктрина владельца |
| `builder@1` | `builder/v1.md` до «Layer 2» | заготовка дословно |
| `analyst@1` (и `analyst_deep`) | `drafts_untested/analyst_v1.md` до «Layer 2» | заготовка дословно, на стенде не гонялась |
| `task_checker@1`, `task_arbiter@1`, `solution_space@1`, `misconception_describer@1`, `today_planner@1`, `domain_classifier@1`, `viz_builder@1`, `release_judge@1` | черновики стенда (пакет `phase4_step1_clean_results/prompts/<role>/v1.md` + `TRACE_v1.md`), написанные по тезисам документа стенда | черновик, D-B-132; до этого — заглушка `BLOCKED-B-101`…`108` |

Вопросы владельцу по поставленным заготовкам (текст слоя 1 не правился):

- `verdict_proof@1` п.1: «authoritative gateway check for … levels 5–6» — по `Т-§7.3` вердикт модели
  проходит ворота только парой; формулировку стоит смягчить.
- `second_opinion@1` п.1: триггеры «confidence < 0.70 … or was explicitly challenged by the learner» —
  в `Т-§7.7` оспаривания учеником среди триггеров нет, порог — параметр `verdict_min_confidence`;
  второе применение (`second_opinion.diagnosis` строки канваса, `Т-§15.4`) заготовкой не покрыто.
- `verdict_free@1` п.2.7 и `verdict_proof@1`/`second_opinion@1`: «set `misconception: null`» —
  в схеме ещё `misconception_hypothesis`; по смыслу оба null, слой 2 это говорит.
- `extractor@3` разд. 2 дублирует таблицу типов пробела общего блока `diagnostics_and_caution`
  (разными словами); в `Т-§7.6` таблица `П-6.4` подаётся ещё и входом (`{{ gap_types }}`).
- `analyst@1` п.5: «affective state (confidence, frustration, fatigue)» — опора манеры в
  `Т-§9.5`/WP5.3 — профиль и выученное; эмоции из динамики сессии там не названы.
- `builder@1` п.4 (циклы `REQUIRES`): выход `builder@1` рёбер не содержит, циклы снимает код
  (`Т-§4.10`); правило безвредно, но не к чему.
- Общий блок `diagnostics_and_caution` (реестр §4.1) есть только в `extractor@3`; у `verdict_*` и
  `second_opinion` — свои формулировки тех же правил.

**Стенд B на Meta API (2 нояб.).** Пакет второго прогона стенда (`workprompt/phase4_step1_clean_results`:
`BENCHMARK_SUMMARY.md`, `PROBE_CALLS_REPORT.md`, 252 лога) перепроверен по сырым логам моделями кода.
Расхождения: сводка ставит PASS всем ролям при 66.7% проверок кода у `teacher`, 50% у
`today_planner` и 6.7% валидных первых ответов у `task_generator`; вывод пробы P1 «модель не может
нарушить strict-схему» неверен — на Baseten есть ответы `assumptions: "real"` и
`neighbor_idea: null` против схемы; сценарии — по 2–5 на роль, у учителя пустой блок инструментов,
у `builder` проверки на несуществующие поля, «спойлер» учителя противоречит доктрине (ученик просит
ответ). Поэтому B собрал свой стенд: вызов как у шлюза (`POST /responses`, одно user-сообщение,
strict-схема `to_strict_json_schema`, без `temperature`, повтор схемы и обрыва как в
`responses_client.py`), модели прода (`muse-spark-1.3`, `muse-wonder-1.0`; ключа Baseten нет),
проверки — модели Pydantic, `validate_markup`, `grammar_ok` и проверки кода из реестра §5.
Сценарии: 117 main/ru и 42 holdout (holdout написан отдельно, до правок; sha256 файлов —
`ff2e4a8f…d691d7`). Прогоны по 3 на сценарий. Стенд, сценарии, логи и отчёт —
`workprompt/phase4_step1_clean_results/stage_b_meta_bench/` (вне репозитория).

Итог на финальных промптах и лимитах (D-B-131…137), 3 прогона на сценарий: main/ru — 411
прогонов, все первые ответы валидны по схеме, 0 обрывов, 404 PASS (98.3%); по сценариям — 126 PASS,
7 FLAKY (по одному сбою из трёх: `teacher` ×3, `teacher_fallback`, `analyst_deep`, `task_checker`,
`task_generator`), 0 FAIL. Holdout (под него промпты не правились) — 35 PASS, 4 FLAKY, 2 FAIL из 42:
FAIL `teacher` h05 — дефект сценария (ждёт знак «?», а учитель спрашивает элементом; ход верный),
`task_checker` h03 — спорно (без слова о возврате модель читает выбор «без возврата» и не отмечает
неоднозначность); FLAKY `viz_builder` ×2 (одно значение параметра вместо трёх в части прогонов),
`task_checker` h02 (назван ли метод в «выборочное стандартное отклонение»), `teacher` h03.
Расход финального main/ru: ~0.88 млн входных и ~0.93 млн выходных токенов.

Открытые вопросы стенда владелец 2026-11-02 поручил решить B по документам: решения D-B-134…137.

Роли, вызываемые кодом C (`recognizer`, `canvas_diagnoser`, `material_parser`, `artifact_writer`):
слой 2 пишет C (§7.3 п.2), B регистрирует схемы и ревьюит PR; заготовки лежат в том же пакете
(`drafts_untested/`, `artifact_writer/v1`–`v4`) и тоже не проверены на схемах Фазы 4.

## Реестр отклонений от основных документов

Как в ТЗ Фазы 4 §2.4 (R4-1…R4-4).

| # | Что | Отступление | Основание | Возврат |
| :-- | :-- | :-- | :-- | :-- |
| R4-C1 | Зависимости backend (§4.6) | Добавлен `python-multipart` — его нет в §4.6, но без него FastAPI не разбирает multipart, который требует WP20.1 | Решение владельца 2026-11-02 («конечно ставь тогда»), D-C-113 п.3 | Внести `python-multipart` в таблицу §4.6 |

## Журнал решений

### D-C-147. Реестр маршрутов в тестах C: каждый поток описывает свои маршруты в своём файле
- Класс: B (C по §7.3: `tests/e2e/**`; общие таблицы правили все три потока)
- WP / место: §7.3, §8.15 п.2; `tests/e2e/routes/` (`a.py`, `b.py`, `c.py`, `README.md`), `tests/e2e/endpoints.py`, `tests/e2e/test_isolation_and_idempotency.py`, `tests/test_app_assembly.py`, `tests/test_route_registry.py`
- Контекст: каждый новый маршрут любого потока ломал CI на тестах C, пока кто-то не правил общие таблицы (`EXPECTED_ROUTES`, `_declared_routes`, `IDEMPOTENT_WALK`, `NO_IDEMPOTENCY_KEY`, `NO_OTHER_BODY`, `MULTIPART`, `body_for`, `Ids`/`path_for`, `_advance`), и три потока правили одни и те же строки.
- Решение:
  1. `tests/e2e/routes/__init__.py` — реестр: `Route` (метод, путь, `stage`, `order`, `body(ids, variant)`, `body_key`, `no_key`, `no_other_body`, `files`, `requires`, `path_ids`, `restore`, `prepare`, `advance`) и `Stream` (`routes`, `other_routes`, `ids`, `routers`, `isolation`). Модули пакета (кроме начинающихся с `_`) находятся по имени; у каждого один объект `STREAM`. Новый поток или новый маршрут — правка только своего файла.
  2. Доли по §7.3: A — домены, структура, диагностика, граф, темы, хабы, споры, WP8 (в том числе `PATCH /v1/learner/preferences` и `DELETE /v1/learner`: роутер `quack/domains`); B — сессии, ходы, действия, практика (`tasks/*`), `domains/classify`; C — вход, согласия, настройки, материалы, холсты, артефакты, карточки, конспект, поток, здоровье.
  3. `T-E2`: обход — маршруты без `no_key`, по возрастанию `stage` (числа взяты из прежнего порядка с шагом 10: 10–120 вход ученика, 130–180 сессия, 190–270 конспект/карточки/холст, 280 спор, 290 завершение, 300 архив). Проверка прежняя: смонтированные изменяющие `/v1`-маршруты без `no_key` = обход; повтор → тот же ответ и без новых событий; другое тело → 409, кроме маршрутов с `no_other_body` (теперь с причиной).
  4. `Ids` — объект со словарём: имена регистрируют потоки (`Stream.ids` со значением-заглушкой), доступ как атрибут, `replace(**kw)`; неизвестное имя — ошибка. `path_for(method, path, ids)` — `str.format_map`; особый случай `/actions` стал полем `path_ids`.
  5. `T-E3`: каждый смонтированный маршрут с `{id}` получает ids чужого ученика и обязан дать 404; объекты потока заводятся функциями `Stream.isolation` (у A — хаб, тема, вердикт, эпизод).
  6. `T-A1`: ожидаемое множество = объединение `routes` и `other_routes` потоков; роутеры для `_declared_routes` — `Stream.routers`. Проверки прежние: смонтированное = ожидаемому, объявленные роутерами маршруты смонтированы и не повторяются. Маршрут из `routes` теперь неявно ожидается, поэтому «зарегистрирован, но не смонтирован» ловится и для маршрутов без ключа.
  7. Добавлено строже прежнего: `requires` (обход падает с понятным сообщением, если id шага ещё не создан) и `tests/test_route_registry.py` (реестр согласован: нет повторов, у обходимого есть `stage` и `body`, причины флагов не пусты, id пути зарегистрированы, вариант 1 отличается от 0).
  8. Шаг `T-E2` `disputes` сам заводит вердикт (`prepare`), а не берёт его из `advance` маршрута холста: поток A больше не зависит от шага C.
- Проверено: до и после правки сняты множества — порядок обхода (30 шагов), `NO_IDEMPOTENCY_KEY` (16 маршрутов с теми же причинами), `NO_OTHER_BODY` (6), `MULTIPART` (2 файла), ожидаемое множество (78), объявленные роутерами (53), тела обоих вариантов и пути для всех маршрутов — совпали.
- Почему: §7.3 (файл потока правит поток), §8.15 п.2, закон «тест не ослаблять»: набор проверок тот же.
- Обратимость: тесты; `git revert` возвращает общие таблицы.
- Тесты: `tests/e2e/test_isolation_and_idempotency.py`, `tests/test_app_assembly.py`, `tests/test_route_registry.py`.
- Требует подтверждения владельцем: нет.

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
- Почему: ТЗ само предполагает, что старый код на SQLAlchemy работает до WP1.3: поля `engine` и `session_factory` уходят «вместе с SQLAlchemy (WP1.3)» (§4.2), зависимость `alembic` удаляется в WP1.3, а не в WP1.1, старые модули переносит владелец «когда переписывает» (WP1.3, риск R-W §9), остаток можно урезать с возвратом R2 в реестр (§7.5 п.6), `phase-4` всегда зелёная (§7.4). Со всем этим расходится одна фраза WP1.1 «Alembic-ревизии 0001, 0002 удаляются»: без старой схемы старые модули и их тесты падают в чужих зонах (§0.4.4 п.14). Первоисточники (`Т-§1.2`, `Т-§3.1`) переход не описывают; итог фазы одинаков в обоих вариантах. Критерии §0.4.3: п.1 — ничего не теряется и не ломается; п.3 — интерфейсы швов S2/S3 те же; п.4 — снимается удалением старых модулей и Alembic в WP1.3. Сдвигается только момент удаления ревизий Alembic: из WP1.1 в WP1.3 (вместе с зависимостью `alembic`).
- Обратимость: удалить отдельную базу и фикстуры; схема и код Фазы 4 от этого не меняются.
- Тесты: `T-DB1`, `T-DB5`, `tests/db/fixtures.py`.
- Согласовано: B — да (2026-11-02: новый код B сразу на `Db` и `append_events`, старые модули B переводит и удаляет при переписывании в WP11–WP15, остаток к G3 — A по §7.3 п.1); C — да (2026-11-02, ответ в PR #74, раздел C; dbmate в CI — PR #77).
- Требует подтверждения владельцем: да — **подтверждено** владельцем 2026-11-02 (передано через B, согласие B — PR #76). Решение принято исполнителем A по поручению разработчика потока A после сверки с ТЗ.

### D-A-104. Колонки базовой миграции, которых §6.1 не уточняет
- Класс: B. WP1.1, `backend/db/migrations/20261102000001_baseline.sql`. После G0 добавить `NOT NULL` нельзя (только расширяющие миграции), поэтому `NOT NULL` — у ключей, `learner_id`, времени и статусов, а ссылки, без которых строка осмысленна (`domain_id`, `topic_id`, `hub_id`, `session_id`), — `NULL`. Исключение по сверке B: `content.sessions.domain_id NOT NULL` (`Т-§22.9`: «Сессий без домена нет»). Целые — `BIGINT` (правило squawk `prefer-bigint-over-int`, `Д-§5.2` п.2; тип после G0 не меняется). `core.learners.email` — `NOT NULL`, уникальный, `CHECK` на нижний регистр. `ops.jobs.job_key` — первичный ключ; `ops.jobs.learner_id` — `NULL` только у периодических задач WP7.5. `ops.turn_manifests` — ключ `manifest_id` (ссылка `turn.committed.manifest_id`; `turn_id` в Фазе 4 нет), `reasoning_effort` Фазы 3 → `effort`; `prompt_version` и `renderer_version` — `TEXT` вида `teacher@2`, `renderer@1` (по сверке B: `Т-§11.6` «версия роль@N записывается в манифест», `Т-§10.6`). `ops.llm_usage` — токены `input`, `cached_input`, `output`, `reasoning` (виды таблицы цен §1.3 плюс рассуждение), `cost_usd numeric(12,6)`. `ops.event_types_seen.first_seen_at` и `updated_at` у отметок — как в Фазе 3. Внешних ключей нет (как в Фазе 3; удаление WP25 идёт по `learner_id`). Схема `langgraph` не создаётся (`Т-§22.3`, §2.2). Почему: §0.4.3, «частые случаи», строка 1; п.4. Обратимость: до G0 — правка baseline, после — расширяющая миграция. Тесты: `T-DB1`. Владельцам таблиц B и C — проверить свои до G0. Подтверждение: нет.

### D-A-105. `append_events` не заводит строку ученика
- Класс: B. WP1.2, `quack/events/log.py`. `Т-§2.3`: «блокирует строку ученика (SELECT … FOR UPDATE)»; Фаза 3 заводила `core.learner_seq` лениво. Решение: нет строки `core.learners` → `LearnerNotFoundError`; строку заводит вход (WP18, C), в тестах — `tests.db.fixtures.create_learner`. Почему: §0.4.3 п.2 (буква `Т-§2.3`), `email NOT NULL` (D-A-104). Обратимость: одна ветка функции. Тесты: `test_learner_row_is_required`. Подтверждение: нет.

### D-A-106. Формат `agent:<kind>` в `actor`
- Класс: B. WP2.1, `quack/events/actor.py`: `agent:` и имя в `snake_case` (`^agent:[a-z][a-z0-9_]*$`), как имена ролей; формы `kind` документ не задаёт. Обратимость: одно регулярное выражение. Тесты: `test_actor_versions_and_agents`. Подтверждение: нет.

### D-A-107. Текст `item.flagged_unclear` — в поле `text`
- Класс: B. WP2.1, WP2.3, `quack/events/event_types.py`. §6.3 называет поле `quote`, а WP2.3 включает тип в полнотекстовый поиск, и `Т-§3.1` требует: «все типы событий с текстом для поиска хранят его в поле text». Решение: цитируемый фрагмент — поле `text` (уровень 2 §0.4.1 выше ТЗ). Обратимость: имя поля до G1. Тесты: `test_text_types_have_text_and_match_index`. Подтверждение: нет (B пишет этот тип — сообщено).

### D-A-108. Ключ `check.failed`
- Класс: B. WP2.1, `quack/events/keys.py`: `check_failed:{answer_seq}:{part}` — по образцу `verdict:{answer_seq}:{part}` (одна запись на часть, `Т-§6.1`). Тесты: `test_derived_keys_deterministic`. Подтверждение: нет.

### D-A-109. Черновик каталога S3 (G0) — поля сверх §6.3
- Класс: B
- WP / место: WP2.1, `quack/events/values.py`, `quack/events/records.py`, `quack/events/event_types.py`
- Контекст: §6.3 даёт ключевые поля, остальное — «минимально нужное для правила» (§0.4.3, «частые случаи», строка 1). Q-A-1 (а): модели B одной формы — в ядре, формы с метками — их id-версии в ядре, имена даёт A.
- Решение: `values.py` — `LearnerAnswer`, `AnswerSpec` и части, `Verdict`, `CheckFailed` (теперь модель Pydantic, а не dataclass), `NewSkill`, перечни; `Verdict` и `CheckFailed` — сами нагрузки `verdict.recorded`, `check.failed`. `records.py` — id-формы: поле-метка `x` → `x_id`, `R…` → `request_id`, `C…` → `card_ids`, `P…` → `chunk_id`; `TurnTaskRecord` несёт `task_id` задачи, созданной ходом (WP2.2); `DeadlineRecord.deadline_at_ms` (как в `hub.deadline_set`); `ExtractedElementRecord.at_ms` — время действия, копирует код (`Т-§2.1`); `RootCauseRecord.confidence` — категория модели, число даёт код при проекции (закон 4). Перечни из `Т-§3.4.2`: `origin` темы и навыка (`session`, `material`, `learner`), задача — `source` (`seminar`, `generated`, `material`, `teacher`), `verification` (`unchecked`, `checked`, `disputed`); `edge.added.origin` — `material`, `model`, `learner` (`Т-§4.6`); `hub.created.kind` — WP6.3; `hub.deadline_set.source` — `turn`, `observation`, `syllabus`, `learner` (WP6.3); `deadline_at_ms = null` снимает дедлайн. `from`/`into` → `from_id`/`into_id`. Пометки «обработчик / правило» — по столбцам §6.3 (`SPECS`, T-EV5).
- Почему: §0.4.3, «частые случаи», строка 1; `Т-§0.4` (snake_case в нагрузке).
- Обратимость: до G1 — правка моделей; после — только новой версией схемы (§0.4.4 п.2).
- Тесты: `T-EV1`, `T-EV2`, `T-EV5`, `test_shared_models_are_the_payloads`, `tests/events/samples.py`.
- Требует подтверждения владельцем: нет. Поля типов B и C — подтвердить владельцам типов до G1 (вопросы Q-A-2, Q-A-3).

### D-A-110. S6: виды задач и таблица правил WP7.1 — выборы там, где ТЗ молчит
- Класс: B
- WP / место: WP7.1, §6.5, `quack/jobs/rules_p4.py` (до WP1.3 рядом с `kinds.py`/`dispatcher_rules.py` Фазы 3, D-A-103)
- Контекст: таблица WP7.1 — по описанию владельцев задач (§7.3 п.4). Часть условий в ней словами; в нагрузке событий они выражаются полями, и надо выбрать какими.
- Решение (таблица — ниже; `plan_jobs(event, settings)` — чистая функция события):
  1. «Числа выражением» (B): `answer.kind == "number"` с заполненным `answer.sympy` → `check.symbolic`; просто число — мгновенный вердикт (WP11.2), задачи нет. `expression`, `quantity` — всегда `check.symbolic`.
  2. Ключи. Без склейки: `job_key = {kind}:{learner}:{seq}`; у `check.symbolic` и `check.model` — `{kind}:{learner}:{answer_seq}:{part}`, часть без имени — `main` (D-B-121). Медленный `answer.submitted` и `check.failed` того же ответа дают один ключ `check.model` — вторая вставка ничего не делает (`ON CONFLICT DO NOTHING`). Со склейкой — `{coalesce_key}:{seq}` (ориентир `Т-§13.1`).
  3. `analyst.deep` склеивается по своему ключу `analyst_deep:{learner}`, а не по `analyst:{learner}` из таблицы: при общем ключе `analyst.deep` на `session.ended` склеился бы с `analyst.run` и один из видов пропал бы. `lock_key` общий (`analyst:{learner}`) — как в таблице.
  4. «Проявление заблуждения»: `verdict.recorded` с `misconception_id` или `misconception_hypothesis`; `observation.diagnosed` с тем же в `diagnosis`; `observation.extracted` с элементом, где `misconception_id`, `new_misconception` или `kind = misconception_hypothesis`. Ставит `analyst.run`; `misconception.describe` — только для известного `misconception_id` (`lock_key = misconception:{id}`): у гипотезы словами узла ещё нет. **Вопрос B (WP16.2):** описывать ли гипотезу, когда проектор создаст узел, и по какому событию.
  5. «Неуверенная пометка» диагноза: `diagnosis.kind == "question"` (`Т-§15.4`, WP21.4: неуверенный диагноз → `question` и второе мнение) → `second_opinion.diagnosis`.
  6. «Задачи банка без пространства решения»: `task.created` с `source` ∈ {`seminar`, `generated`, `material`} → `task.solution_space` (P2). Задачи `teacher` (на лету) получают пространство лениво через `agent.requested {kind: solution_space}` (WP12.4).
  7. `agent.requested`: вид `agent.<kind>` для `viz`, `illustration`, `task_generation`, `solution_space`, `artifact`; класс P1, если `needed_in_session`, иначе P2. `known_check` → `check.known_claim` (вида `agent.known_check` в §6.5 нет), класс так же, ключи и `required_seq` — как у `known.claimed`, в параметрах — `request_id` и `topic_id`; без `params.topic_id` задачи нет — проверять нечего (WP8.2; ответ B, #103; ревью B #117; `phase-4-a-g1`).
  8. `photo.pipeline`: `lock_key = canvas:{canvas_id}`; фото вне канваса — `photo:{photo_id}`. `material.parse`: P1 при `session_id`, иначе P2; блокировку шага записи структуры `domain:{id}` ставит обработчик (C) — домена в нагрузке `material.uploaded` нет.
  9. `skill.split`: `extract.episode` с `restrict_skill_ids` = делимый навык и части; `episode.reassigned`: `extract.episode` с `episode_seq` и `topic_id`. Какие эпизоды переизвлечь, находит обработчик WP15.
  10. `visible` (`Т-§13.8`): видимы `check.symbolic`, `check.model`, `task.check`, `photo.pipeline`, `material.parse`, `agent.*`, `check.known_claim`; остальные — нет. \[ПРОВЕРИТЬ\] для `second_opinion.diagnosis`, `canvas.rediagnose`, `today.prepare`, `task.solution_space`, `artifact.*`, `export.data`: документ не называет — сделаны невидимыми.
  11. Роли по видам (`KIND_LLM_ROLES`) — из перечня классов ролей (`Т-§11.3`, ТЗ §5, «Класс приоритета роли»); `check.symbolic` моделей не зовёт (страховка может уйти в `check.model` отдельной задачей). Класс по умолчанию — класс из таблицы WP7.1; периодические — P3.
  12. Строки, читающие данные сверх события (`artifact.topic_refresh` на `session.ended` и `task.closed`, `artifact.cheatsheet`, `embedding.compute`, `graph.hygiene`), перечислены в `RULES` с полем `needs` и реализуются в G2 вместе с диспетчером (WP7.1); `plan_jobs` их пока не планирует.
- Почему: §0.4.3 п.4, п.5; закон 5 (повтор заменяет запись, не дублирует); `Т-§13.1`.
- Обратимость: таблица данных, один модуль.
- Тесты: `tests/jobs/test_rules_p4.py` (25 passed; `T-D1` в части строк по событию, полный — в G2).
- Требует подтверждения владельцем: нет. Вопросы B — п.4, п.7; \[ПРОВЕРИТЬ\] — п.10.

Таблица S6 (G1; «пакет» — исполнитель вида):

| Событие | Вид задачи | Класс | `lock_key` / `coalesce_key` | `required_seq` | Пакет |
| :-- | :-- | :-: | :-- | :-: | :-- |
| `episode.closed`, `has_content` | `extract.episode` | P2 | — / — | seq события | WP15 |
| `task.closed` | `analyst.run` | P2 | `analyst:{learner}` / `analyst:{learner}` | seq | WP16.1 |
| `session.ended` | `analyst.run` + `analyst.deep` | P2 | `analyst:{learner}` / `analyst:{learner}`, `analyst_deep:{learner}` | seq | WP16.1 |
| `verdict.recorded`, `observation.diagnosed`, `observation.extracted` с проявлением | `analyst.run`; `misconception.describe` (с id) | P2; P3 | `analyst:{learner}` / `analyst:{learner}`; `misconception:{id}` / — | seq | WP16.1, WP16.2 |
| `answer.submitted`: `expression`, `quantity`, `number` с `sympy` | `check.symbolic`, `run_at = occurred_at + symbolic_check_delay_ms` | P1 | — / — | — | WP11.3 |
| `answer.submitted`: `proof`, `free_text`, `drawing` | `check.model` | P1 | — / — | — | WP11.6 |
| `check.failed` | `check.model` | P1 | — / — | — | WP11.6 |
| `turn.committed`, `markup.tasks` не пуст | `task.check` | P1 | — / — | — | WP12.5 |
| `observation.diagnosed`, `diagnosis.kind = question` | `second_opinion.diagnosis` | P1 | — / — | seq | WP21.4 |
| `handwriting.corrected` | `canvas.rediagnose` | P1 | — / — | — | WP21.4 |
| `photo.uploaded` | `photo.pipeline` | P1 | `canvas:{id}` / — | — | WP22.3 |
| `material.uploaded` | `material.parse` | P1 при `session_id`, иначе P2 | — / — | — | WP20.2 |
| `task.created`, `source` банка | `task.solution_space` | P2 | — / — | — | WP12.4 |
| `agent.requested` | `agent.<kind>`; `known_check` → `check.known_claim` | P1 при `needed_in_session`, иначе P2 | — / — | — (у `check.known_claim` — seq) | WP16.6, WP8.2 |
| `turn.committed`, `markup.plan_update.cancel_request_ids` не пуст | отмена задач поручений (D-A-115) | — | — | — | WP7.4 |
| `today.shown` | `today.prepare` | P2 | `today:{learner}` / `today:{learner}` | seq | WP16.8 |
| `digest.written`, `plan_divergence` | `analyst.deep` | P2 | `analyst:{learner}` / `analyst_deep:{learner}` | seq | WP16.1 |
| `known.claimed` | `check.known_claim` | P2 | — / — | seq | WP8.2 |
| `episode.reassigned`, `skill.split` | `extract.episode` (с ограничением) | P2 | — / — | seq | WP15 |
| G2: `session.ended`, `task.closed`, дедлайн хаба; новые узлы с текстом; `session.ended` / счётчик тем | `artifact.topic_refresh`, `artifact.cheatsheet`; `embedding.compute`; `graph.hygiene` | P2; P2; P3 | `artifact:{topic}`, `artifact:{hub}`; —; `domain:{id}` / `hygiene:{domain}` | — | WP23, WP20.3, WP16.3 |

### D-A-111. S4: `quack.graph.read` — доступ через объект `GraphReader`, заглушка с фиксированными данными
- Класс: B
- WP / место: WP6.4, `quack/graph/read/` (`models.py`, `reader.py`), `tests/support/graph_read.py` (`FixedGraphReader`)
- Контекст: S4 (G1) — сигнатуры, модели ответов и заглушки. §4.2 запрещает модульные переопределения, а набор полей `AppResources`/`JobContext` фиксирован (в нём нет поля для чтения памяти).
- Решение: (1) интерфейс — `Protocol GraphReader` с методами WP6.4 (`slice_for_turn`, `frontier`, `topic_readiness`, `why`, `topic_requires`, `recall_probability`, `review_candidates`, `prep_context`, `skill_history`, `prerequisites`, `similar_mistakes`, `past_solution`, `state_at`); читающий код получает объект аргументом, настоящий читатель (G2) строится из `neo4j` и `settings` ресурсов; `materials_prefetch`/`materials_search` — `quack/graph/read/materials.py` (C), не здесь. (2) Модели — Pydantic, неизменяемые, без лишних полей, только id и факты (`Т-§10.2`, `Т-§7.4`: ни весов, ни ярусов); «не проверялось» — `LevelFact.status = not_tested`, не `failed` (закон 8). (3) Для B (WP11.6): `LevelFact.proven_by_direct` («доказан прямым Т1/Т2») и `last_t1_failed` («последнее Т1 по времени — провал») — входы `needs_second_opinion`; `LevelState` B строится из них одной строкой. (4) Для защитного правила WP13.5 `SkillFact.requires` — связи `REQUIRES` с `confidence` и признаками «есть показ», «есть свидетельство»; `PrerequisiteFact.confidence`. (5) `FixedGraphReader` — один ученик, три навыка (доказан прямым Т1/Т2; последнее Т1 — провал; не проверялся), заблуждение, корень, кандидаты на повторение, `prep_context`, `state_at`; `FixedGraphReader(lag_degraded=True)` — срез с отставанием проектора.
- Почему: §4.1, §4.2, WP6.4, S4.
- Обратимость: сигнатуры замораживаются на G1; до этого — правка моделей.
- Тесты: `tests/graph/test_read_stub.py` (9 passed).
- Требует подтверждения владельцем: нет. Поля, которых документ не называет прямо (`ExposureFact.kind: str`, `MaterialRef.kind`, `ReviewReason`), — \[ПРОВЕРИТЬ\] с B (WP14.2) и C (WP17) до G1.

### D-A-112. Единый ввод `get_current_learner_id` на сессиях — моя часть (ответ на D-C-112)
- Класс: B (пересечение зон, §7.3)
- Решение: согласен с п.1 (имя и подпись `get_current_learner_id` не меняются, код роутеров не меняется), п.2 и п.4. Роутер A один: `quack/domains/router.py`; его тесты на старом входе — `tests/domains/test_diag_idempotency.py`, `test_diag_open_and_bank.py`, `test_diag_state_from_events.py`, `test_diagnostic_and_graph_view.py`, `test_graph_view_fixes.py`, `test_topic_alias_routes.py`. Корневые тесты A, которые выпускают токены, — по `grep` на `ensure_users_hook`/`get_current_learner_id` в `tests/` корневых файлов A нет. C правит их одним PR с моим ревью (§7.3): смена способа входа, утверждения не меняются.
- Требует подтверждения владельцем: нет.

### D-A-113. Удаление пароля и JWT из `quack/domains` — ответ на вопрос C
- Класс: B (правка файлов A потоком C, §0.4.4 п.14 — по согласованию)
- Решение: **да**, одним PR C с ревью A. Основание: §2.3 строка 3, WP18.3 («пароль, JWT, `auth.users`, `/v1/auth/register|login` удаляются»), §6.4 «старый → новый» (`POST /v1/auth/register`, `/login`, `GET /v1/auth/me` → вход по ссылке и Google, cookie, `GET /v1/me`), §8.15 п.1–2. Условия:
  1. `quack/domains/router.py`: удаляются `POST /v1/auth/register`, `POST /v1/auth/login`, `GET /v1/auth/me` и публичный роутер с `enforce_auth_rate_limit`, если в нём ничего не осталось. `PATCH /v1/learner/preferences` и `DELETE /v1/learner` остаются (§6.4: A).
  2. `quack/domains/auth.py`: удаляются `register_learner`, `login_learner`, `get_learner_me`, `hash_password`, `verify_password`, выпуск JWT, `DEV_JWT_SECRET`, `JWT_ALGORITHM`; модуль — целиком, если ничего не осталось. Импорт `DEV_JWT_SECRET`/`JWT_ALGORITHM` в `tests/domains/test_diag_state_from_events.py` переводится на `login()` в том же PR (D-A-112).
  3. `tests/domains/test_auth_and_learner.py`: удаляются `test_register_normalizes_email_and_rejects_duplicate`, `test_login_and_me_jwt_validation`, `test_auth_rate_limiting_per_ip` (проверяют удаляемые маршруты) — каждый строкой в «Правках тестов» с тестом `T-AU*`, который проверяет свойство теперь (§8.15 п.1). `test_update_preferences_and_idempotency` и `test_two_phase_account_deletion` **не удаляются**: идемпотентность и удаление остаются (§8.15 п.2) — только вход через `login()`, утверждения те же. Если утверждение одного из них на сессиях не выполняется (например, «после удаления запрос отклонён»), C не ослабляет его и возвращает вопрос A: удаление уходит на `Db` в WP25.
- Тесты: в PR C. Требует подтверждения владельцем: нет.

### D-A-114. S1: поля `JobContext` Фазы 4
- Класс: A (§4.2 — набор полей задан)
- Решение: `JobContext` получает `db`, `storage`, `embeddings`, `deletions` (§4.2: у `workers` всё, кроме `email`); `engine`, `session_factory` уходят с WP1.3. `create_job_context` открывает `Db` (`open_db`, ждёт Postgres, как сам процесс) и строит клиентов конструкторами C (§7.3 п.6); `close_job_context` закрывает в обратном порядке. Пересечения (§7.3, уведомление C): `tests/support/resources.py::build_job_context` получает те же поля с подменами по умолчанию (как `build_app_resources`); `tests/e2e/system.py::job_context` берёт их из ресурсов стенда. `ProjectorContext` не менялся — проектор Фазы 4 строится в WP3.
- Тесты: `tests/jobs/test_job_context.py` (поля, порядок закрытия). Требует подтверждения владельцем: нет.

### D-A-115. Отмена поручений по `planUpdate` — правило диспетчера, не вызов из хода
- Класс: B. WP7.4, `Т-§13.7`; вопрос B п.6.
- Варианты: (а) функция A в транзакции записи хода; (б) диспетчер по `turn.committed`.
- Решение: (б). B ничего не вызывает: ход пишет `markup.plan_update.cancel_request_ids`, диспетчер по этому событию находит задачи поручений ученика по `payload.request_id` и ставит `running` → `cancel_requested`, `queued` → `cancelled` (WP7.4). Почему: задачу поручения ставит тот же диспетчер по `agent.requested`, а он идёт по `seq` ученика — `agent.requested` всегда обработан раньше хода, который его отменяет; при варианте (а) задача ещё могла не существовать (отставание диспетчера), и отмена терялась бы. Закон 9: ход не ждёт очереди.
- Сейчас: чистая часть — `rules_p4.cancelled_requests(event)`; запросы к `ops.jobs` — с очередью WP7.4 (G2).
- Тесты: `tests/jobs/test_rules_p4.py::test_plan_update_cancels_agent_requests`; SQL — тест очереди WP7.4. Требует подтверждения владельцем: нет.

### D-A-116. Обработчики задач Фазы 4: подпись регистрации и ветка рантайма
- Класс: B. §6.5 («проверка полноты обработчиков при старте `workers` — как в Фазе 3»), WP7.4; вопрос B п.4.
- Решение: подпись — как в Фазе 3: `@register_job_handler("<вид>")` над `async def handler(job: ClaimedJob, ctx: JobContext) -> None` (`quack.jobs.runner`). `ClaimedJob` Фазы 4 несёт поля `Т-§13.1` (`job_key`, `kind`, `learner_id`, `session_id`, `payload`, `required_seq`, `attempts`); ресурсы — из `ctx` (D-A-114: `ctx.db`, `ctx.llm`, `ctx.compute`, …). Рантайм на `Db` (очередь WP7.4 и диспетчер WP7.1) — ветка `phase-4-a-wp7`, G2. До её слияния модули B не входят в `HANDLER_MODULES` — проверка полноты Фазы 3 не падает; модули с обработчиками Фазы 4 A добавляет в `HANDLER_MODULES` в том же PR, B пишет одну функцию с декоратором на вид в своём модуле.
- Тесты: `T-Q7` в PR рантайма. Требует подтверждения владельцем: нет.

### D-A-117. Параметры `embedding.compute` — ответ на D-C-117 п.6
- Класс: B. WP7.1, WP20.3, S6.
- Решение: (а) параметры несут текст: `{nodes: [{node_kind, node_id, text}]}` — то же, что `EmbeddingNode` C. Текст берёт диспетчер из события, которое его принесло (диспетчер читает только журнал, закон 1), обработчик ничего не читает; `required_seq` — `NULL` (задача читает только журнал, WP7.1). Источник текста: кусок — `chunk.indexed.text`; свидетельство — `quote` элемента наблюдения; заблуждение — формулировка (`statement`); тема и навык — \[ПРОВЕРИТЬ\]: какое свойство (`name`, у навыка ещё `summary`, `Т-§3.4.2`) эмбеддится, документ не называет — решается со строкой в G2. (б) `JobContext` с `db` и `embeddings` — D-A-114 (`phase-4-a-g1`); регистрация вида — D-A-116 (`phase-4-a-wp7`, G2). (в) Да, по документам: правило — для **новых** узлов (WP7.1, `Т-§13.3`), у `topic.renamed` в §6.3 правила диспетчера нет, ключ `emb:{node_kind}:{node_id}:{model}` — WP2.1. Открыто к G2: `misconception.described` в §6.3 с правилом диспетчера; если оно — пересчёт вектора по новой формулировке, тот же ключ второй вектор не запишет — вопрос записи класса C при строке G2.
- Требует подтверждения владельцем: нет; п.(а) тема/навык и п.(в) `misconception.described` — к G2.

### D-A-118. S6: `plan_jobs` принимает событие Фазы 4
- Класс: A. `rules_p4.py` (#101) типизировал событие моделью Фазы 3 `quack.events.models.StoredEvent` (`event_id`, `turn_id`, `causation_id`), а диспетчер Фазы 4 читает `core.events` моделью `quack.events.log.StoredEvent` (S2). Исправлено до заморозки S6 (G1); поведение то же.

### D-A-119. S3 заморожен на G1
- Класс: A (§7.2: «Каталог событий окончательный (S3)» — G1)
- Решение: каталог `quack/events/event_types.py` — окончательный. Последние поправки по ответам владельцев типов: `Verdict` — `misconception_id` / `misconception_hypothesis` (Q-A-2 п.1, #101); `rejected` — `{index, reason, quote}` (п.3, #101); `CheckFailedReason` + `unsupported` (п.7, #117); `canvas.line_committed.kind` ∈ {`latex`, `text`} (`Т-§15.3`), `artifact.updated.kind` ∈ {`notes`, `cards`, `cheatsheet`} с `topic_id` у конспекта и карточек и `hub_id` у шпаргалки (WP23.1; имена — маршруты §6.4) (Q-A-3, ответ C). Q-A-3 п.1, 3, 5 — без изменений полей.
- Дальше: новый тип, новое поле или изменение схемы — только новой версией схемы с записью класса C и согласием трёх потоков (§0.4.4 п.2).
- ~~Открыто у владельца: пишется ли `observation.diagnosed`, если `canvas_diagnoser` ошибки не нашёл.~~ Закрыто **D-C-120** (C, по поручению владельца): событие пишется только при найденной ошибке, `gap_type` ≠ `none`; без ошибки события нет. Схема не меняется. Следствие для A: в WP5.1 каждое `observation.diagnosed` → `Evidence` с `outcome = incorrect` (как в ТЗ WP5.1), ветки «ошибки нет» нет.
- Тесты: `T-EV1`, `T-EV2`, `T-EV5`, `test_frozen_kinds_reject_other_values`. Требует подтверждения владельцем: нет.

### D-A-120. Прогоны: что по ТЗ, что по скиллу v2
- Класс: B (порядок работы потока A)
- Решение: полный прогон набора — один раз перед открытием PR (§0.4.7); после правок по ревью — затронутые тесты и контрактные (`T-A6`, `T-DB2`, `T-DB4`), полный набор — CI PR («без повторных прогонов на промежуточных состояниях», §0.4.7). Красный прогон — в «Красных прогонах» (§7.4) и только по делу: тест падает на отсутствующем поведении или заведомо неверной реализации, а не на `NotImplementedError`. Зелёные прогоны — в описании PR (§0.4.7: команда, SHA, сводная строка). Сочетание параллельных PR перед слиянием проверяется локально. Слияние в `phase-4` — после ревью соседнего потока и зелёного CI (§7.4), решение владельца для этого не нужно (только для `main`, G4).
- Почему: §0.4.7, §7.4; правка ТЗ исполнителю недоступна (§0.4.4 п.7). Требует подтверждения владельцем: нет.

### D-A-121. `session.started.resumed` — поле из WP13.10 после заморозки S3
- Класс: C (схема события после G1, §0.4.4 п.2; вопрос B в D-B-155 п.е)
- Контекст: WP13.10 \[РЕШЕНИЕ Ф4\]: действие в сессии, закрытой сборщиком неактивных, пишет `session.started {session_id, resumed: true}`. Строка §6.3 этого поля не называет, и в каталоге S3 (D-A-119) его не было — расхождение внутри ТЗ.
- Варианты: (а) поле `resumed: bool = False` в `session.started` v1; (б) версия 2 с апкастером (`Т-§2.1`: «изменение схемы — новая версия»); (в) без поля — возврат распознаётся как второе `session.started` той же сессии (вариант B).
- Решение: (а). Поле прямо названо в WP13.10 — специальное решение ТЗ выше общего перечня §6.3. Версия 1 правится, а не поднимается: версия нужна, чтобы читались уже записанные события, а событий Фазы 4 нет ни в одном окружении (чистый старт WP0 п.2, `phase-4` не выкачена); поле необязательное, `false` по умолчанию. После первой выкатки Фазы 4 — только новой версией с апкастером (`Т-§2.1`). (в) отвергнут: диспетчер и потребители должны видеть возврат в самом событии, без чтения истории сессии.
- Согласие потоков: B — просит поле (D-B-155 п.е); C — ревью этого PR.
- Тесты: `test_session_started_marks_a_resumed_session`. Требует подтверждения владельцем: да (класс C).

### D-A-122. Вопросы B к владельцу — решение по документам (по поручению человека A, 2026-11-03)
- Класс: B (п.1, п.2); п.3 — подтверждение класса C B, по документам
1. **`content.study_plans` (D-B-154).** Строит **код**, не модель (закон 2: «модель толкует, код считает»; `Т-§12.5`: «порядок строится по `REQUIRES` со сжатыми циклами», `Т-§4.6`: план крупной теории работает на графе со сжатыми SCC). **Порция** — вершина сжатого графа: тема хаба или группа тем, связанных циклом `REQUIRES` (одна SCC); порядок — топологический порядок сжатого графа; связи — `graph.read.topic_requires` (S4, `REQUIRES` между темами считается при чтении, §2.3 строка 7). Строится при старте сессии с целью-хабом (WP13.10) и при записи хода, если цель сессии — хаб и плана нет; документ производный, как `session_plans`, — пересобирается из графа. Учитель «держит грубый план» (`П-4.6`) — видит документ в контексте, а не пишет его: в разметке `teacher@2` полей для плана хаба нет, и закон 10 запрещает копить выводы модели. «С повторением между ними» (`П-4.6`) — повторение по сроку (WP17), не часть документа. Размер порции в числах документы не называют — числа нет и в решении.
2. **`verdict_recorded` в канале (D-B-148 п.з).** Не нужен. §4.8 перечисляет виды сообщений; вердикт страховки виден через существующие: `check.symbolic` — видимая задача (`Т-§13.8`: плашка «проверяю решение»), смена её статуса шлёт `jobs_changed {session_id}` (WP7.4, `T-D10`), клиент перечитывает ленту сессии (`GET /v1/sessions/{id}`, WP13.10) и видит `verdict.recorded`; `projection_advanced` — после применения вердикта проектором. Новый вид сообщения — изменение контракта §4.8 без основания в документах.
3. **D-B-104 (`intent`, `approach`).** Вариант (а) B принимается: доктрины владельца нет, перечни взяты из ближайших первоисточников (`П-4.4`, `П-4.2`, `Т-§9.5`; §0.4.3 п.2) и заморожены в S3 (`TurnHeadRecord`, D-A-119). `П-4.2`: приёмы — примеры, «отдельной сущности „приём“ нет» — поэтому `approach` — метка для профиля (`Т-§9.5`, «что реально помогает»), а не ограничение хода; `none` покрывает ход вне перечня. \[ПРОВЕРИТЬ\] по доктрине: строки таблицы `П-4.2` «короткий блок однотипных задач» и «ретест в новой формулировке» кода не имеют; добавление значений — новой версией схемы `turn.committed` (§0.4.4 п.2). Доктрину A не пишет (§0.4.4 п.13); подтверждение класса C владельцем — на G4.

### D-A-123. Вопросы C к владельцу — решены C в D-C-122, A согласен
- Класс: B. C решил свои четыре вопроса по поручению владельца в D-C-122 (`phase-4-c-wp21-ws`), раньше этой записи. A сверил с документами и согласен со всеми пунктами: (1) блок набора формул — инструмент слоя ученика, не вид блока (`П-9.4`, `Т-§15.3`); (2) поправка §0.4.7/§7.4 снята — как D-A-120; (3) расхождения по `JWT_SECRET` в `Д-§7` нет; (4) переполнение куска — `chunk_max_batches = ceil(stroke_chunk_seconds · 1000 / stroke_batch_ms)` из двух параметров `Т-§15.2`, без новых чисел. Отдельных решений A по этим вопросам нет.

### D-A-124. Швы WP15 для B: проверка элементов `extractor@3` и запись покрытия
- Класс: B. WP15 п.4, п.8 (`Т-§7.6`, `Т-§4.4`, `Т-§2.1`), WP7.3 (`Т-§2.8`); `quack/graph/evidence_validation.py` (`check_extracted`), `quack/jobs/coverage.py` (`mark_covered`)
- Контекст: B просит (п.5 сообщения B): проверку элементов на записях Фазы 4 и функцию записи `ops.coverage` в транзакции `observation.extracted`. Ядро не может импортировать модель B (`quack.agents.schemas.ExtractedElement`, §4.1).
- Решение:
  1. `check_extracted(elements, EpisodeCheckContext) -> CheckedElements(elements, rejected)`. Вход — протокол `ExtractorElementLike` с полями модели B (тест проверяет, что модель B ему соответствует). Контекст: `skill_labels`, `misconception_labels` (`LabelMap.labels(...)`), `episode` (`seq → EpisodeSource(text, at_ms)` — только события эпизода, `Т-§7.5`), `task_levels`, `code_verdicts` — их собирает B.
  2. Порядок проверок `Т-§7.6`: метки (навык, заблуждение, корень ошибки — неизвестная метка корня отклоняет элемент: «метки существуют») → полнота → номера внутри эпизода → цитата → уровень не выше разметки задачи → `code_verdict_exists` (WP15 п.8; только `evidence`: толкование закрытого ответа гипотезой остаётся). Первая непройденная проверка — причина в `rejected {index, reason, quote}`.
  3. Полнота, где документ молчит: `evidence` называет ровно одно из трёх — метку навыка, новый навык, «неясно» (`Т-§7.6`: «навык (метка, описание нового или „неясно“)») — и имеет уровень и исход; гипотеза — метку заблуждения или новую формулировку.
  3а. Помощь и тип пробела согласованы (WP15 п.3, `Т-§7.6`; ревью B в #133): ступень есть только у `hint` и у `hint` она есть; у положительного элемента (`correct`) тип пробела — `none`. Иначе — `incomplete_element`. У `partial` тип пробела может быть любым.
  4. «Неясно» — не отказ: элемент записывается с `skill_unclear` и без `skill_id` (это ответ извлекателя по `Т-§7.6`, а не провал проверки); свидетельства из него проектор не строит (WP5).
  5. Цитата обязательна у отрицательного элемента и гипотезы (`Т-§7.6`) и у нового навыка (`Т-§4.4`: «только с цитатой»); цитата без краевых пробелов должна быть подстрокой одного из указанных событий.
  6. Время элемента — время самого позднего из указанных событий (`Т-§2.1`: «время из исходных событий»; так же, как в Фазе 3).
  7. `mark_covered(db, learner_id, *, episode_seq, extractor, status, observation_seq)`: `extracted` — с `seq` наблюдения, `nothing_to_extract` — без него (иначе `ValueError`); повтор заменяет строку (`ON CONFLICT … DO UPDATE`), так что переизвлечение указывает на новое наблюдение. Вызванная внутри `db.transaction()` вступает в транзакцию вызывающего (закон 7).
- Почему: §0.4.3 п.2, п.5; законы 5, 6, 7.
- Обратимость: две функции, данных нет.
- Тесты: `tests/graph/test_extracted_check.py` (13), `tests/jobs/test_coverage.py` (4). Проверено на неверной реализации: 10 подмен (нет проверки цитаты, нет `code_verdict_exists`, нет потолка уровня, время самого раннего события, новый навык без цитаты, неверный `index`, неизвестная метка корня принята, `evidence` без навыка принят, повтор не заменяет строку, `extracted` без наблюдения принят) — каждая роняет тест.
- Требует подтверждения владельцем: нет.

### D-A-125. S5: строки обработчиков проектора для событий B и C
- Класс: B. WP3.2 (таблица), §6.3, §7.2 S5 (G1); `quack/graph/projector_rows.py`
- Контекст: S5 — «строки обработчиков проектора для событий B и C (карточка с самооценкой, диагноз, второе мнение, разметка хода)» к G1; сами обработчики `handle(event, view) → mutations` — WP3.2 (G2). Таблица WP3.2 называет изменения графа, но не всегда — какие поля и на каком навыке.
- Решение: `ROWS` — строка на каждый из 38 типов с обработчиком (§6.3): писатели, читаемые поля (пути в нагрузке), изменения графа, источник. Тест-контракт: строки = типы с пометкой «проектор» в реестре, а каждый читаемый путь есть в модели нагрузки — писатель не переименует поле, которое читает проектор, без красного теста. Где документ молчит:
  1. **Карточка с самооценкой** (`answer.submitted {source: card, self_assessment}`): свидетельство Т4 на навыках разметки карточки, уровень 1, исход — по самооценке (да → `correct`, нет → `incorrect`). Т4 «только подкрепляет уровень 1», отрицательное Т4 «не учитывается» (`Т-§7.2`) — это правило свёртки (WP4), а не повод не писать факт (закон 6, провенанс).
  2. **Диагноз канваса** (`observation.diagnosed`): в нагрузке нет навыка, а свидетельству он нужен. Навык — у названного заблуждения (`diagnosis.misconception_id` → его навык); иначе — навыки, которые проверяет задача (`task_id` → `TESTS`), свидетельство `level = 0` на каждом (WP5.1). Нет ни задачи, ни известного заблуждения — свидетельства нет (не к чему привязать); гипотеза словами без навыка узла не создаёт. Поле навыка в событие не добавляется — каталог заморожен (D-A-119).
  3. **Второе мнение** (`observation.second_opinion {target_seq, agreed}`): согласно — свидетельство целевого диагноза Т3 → Т2 (WP5.1); не согласно — остаётся Т3 (`Т-§7.2`: Т3 — «с которым не согласилось второе мнение»). `diagnosis` второго мнения проектор не читает.
  4. **Разметка хода** (`turn.committed`): `Exposure` по `markup.exposures` (`skill_id`, `kind`, `approach`; `Т-§7.8` п.6), `Misconception.targetedInSessionId` по `markup.targets_misconception_id` и сессии события, временные навыки из `markup.tasks.tests.new_skill` — сводятся по нормализованному имени с `task.created` той же пачки (WP3.2, последний абзац).
  5. **Кусок материала** (`chunk.indexed`, ревью C в #135): у `Chunk` — `page`, `text`, `notation`, а также `learnerId`, `domainId` (из конверта события) и `searchable` (есть текст). Это фильтрующие свойства `chunk_emb` (`Т-§3.4.6`), по ним ищет `quack/graph/read/materials.py` C (D-C-127). Удаление материала (WP25) ставит `searchable = false`.
  6. **`verdict.needs_reading_confirmation`** (вопрос B в #135) проектор не читает. Флаг — только для показа: «ученику сначала показывается, как прочитан ответ» (`Т-§6.9`, `Т-§16.3`, WP11.6, `T-CH8`). Отрицательное свидетельство пишется сразу (закон 9: проектор ничего не ждёт). Если ответ прочитан неверно, исправление идёт через `handwriting.corrected` (повторный диагноз строки, `Т-§2.8`) и «не согласен» (`dispute.filed`, D-A-134 п.7).
- Почему: §0.4.3 п.2, п.5; законы 4, 6, 8.
- Обратимость: таблица данных; обработчики пишутся в WP3.2 по ней.
- Тесты: `tests/graph/test_projector_rows.py` (3). Проверено на неверной реализации: 4 подмены (нет строки, переименованное поле, неверный вложенный путь, повтор строки) — каждая роняет тест.
- Требует подтверждения владельцем: нет. П.2, п.3, п.5 — согласие C (#135); п.1, п.4 — ждут ревью B.

### D-A-126. S4: факты картины памяти WP14.2 — уверенность типа пробела, «забыл», условия проявления
- Класс: B. WP6.4, WP14.2; вопрос B в D-B-157 (#131); `quack/graph/read/models.py`, `tests/support/graph_read.py`
- Решение: в ответные модели среза добавлены поля, названные техдоком: `SkillFact.gap_type_confidence` (`gapTypeConfidence`, `Т-§3.4.4`, `Т-§8.4`: доля голосов за победивший тип), `SkillFact.last_lapse_at_ms` (`retLastLapseAtMs`; «забыл», `Т-§8.2` п.3), `MisconceptionFact.triggers` (`Т-§3.4.2`, `Т-§9.3`). Нет значения — `None` / пустой список (закон 8: «не было» — явное значение). Поля необязательные: читающий код B и C не меняется. Каталог событий не затронут (S3 заморожен, D-A-119). Заглушка `FixedGraphReader` даёт их у «проваленного» навыка и у заблуждения.
- Почему: §0.4.3 п.2 (поля названы `Т-§3.4`); S4 — интерфейс A, реализация — G2.
- Тесты: `tests/graph/test_read_stub.py::test_memory_picture_facts_of_wp14_2`.
- Требует подтверждения владельцем: нет.

### D-A-127. S4: помощь у свидетельства в `skill_history`
- Класс: B. WP6.4, WP14.4; вопрос B в D-B-163 (#132); `quack/graph/read/models.py`, `tests/support/graph_read.py`
- Решение: `EvidenceFact.assistance: Literal["none", "hint", "shown_solution"] = "none"` и `EvidenceFact.hint_step: int | None` (1–3). `skill_history` отдаёт помощь каждого свидетельства («задача, исход, помощь, цитата, дата», `Т-§10.4`). Имена — по свойствам узла `Evidence` (`assistance`, `hintStep`, `Т-§3.4.3`), а не `help` из вопроса B. Перечень значений и ступени 1–3 — `Т-§7.6`. Без подсказки ступени нет — `None` (закон 8). Поля необязательные: читающий код B и C не меняется. Каталог событий не затронут (S3 заморожен, D-A-119). Заглушка `FixedGraphReader` даёт у «проваленного» навыка свидетельство с подсказкой ступени 2.
- Почему: §0.4.3 п.2 (свойства названы в `Т-§3.4.3`) выше п.7 (единообразие с `ExtractedElementRecord.help` в каталоге).
- Тесты: `tests/graph/test_read_stub.py::test_skill_history_says_what_help_was_given`.
- Требует подтверждения владельцем: нет.

### D-A-128. `core.pairing_tokens` — хранение токена привязки (WP22.1 ↔ §6.1)
- Класс: C. WP22.1, §6.1, §0.4.4 п.4; вопрос C в #128; `backend/db/migrations/20261103000001_pairing_tokens.sql`, `…000002_pairing_tokens_learner_index.sql`, `tests/db/test_baseline_migration.py`
- Противоречие: WP22.1 и `Т-§16.1` требуют токен привязки — одноразовый, живёт `pairing_ttl_s`, привязан к ученику, сессии, канвасу и задаче, «хранится хэш». В §6.1 места для него нет: `core.auth_links` — письма (`email NOT NULL`), `core.auth_sessions` — уже выданные сессии; токен в ней сам стал бы кукой и не был бы одноразовым.
- Решение: расширяющая миграция после G0 — таблица `core.pairing_tokens (token_hash TEXT PK, learner_id, session_id, canvas_id NOT NULL, task_id NULL, created_at, expires_at NOT NULL, used_at NULL)`, по образцу `core.auth_links` (\[РЕШЕНИЕ Ф4\] §6.1 для писем). Индекс по `learner_id` для удаления ученика (WP25) — отдельным файлом `CONCURRENTLY` (Д-§6.2). Погашение — одним `UPDATE … SET used_at = now() WHERE token_hash = … AND used_at IS NULL AND expires_at > now()` — код C (`quack/auth`, `quack/canvas`). Запрос погашения по хэшу C вносит в `CROSS_LEARNER_ALLOWED` (`tests/db/test_sql_static.py`) своим PR — согласие A здесь.
- Почему не Redis: `Т-§19.1` держит сессии в Postgres, потому что в Redis нет ничего, что жалко потерять (`Т-§1.2`); без Redis (§4.2 — он необязателен) привязка не работала бы. §0.4.3 п.1 (одноразовость — атомарный `UPDATE` в Postgres), п.7 (как `core.auth_links`).
- Тест `T-DB1` (§8.15 п.3): описание «таблицы §6.1 и только их» уточнено — базовая миграция даёт ровно §6.1, каждая расширяющая — только свои таблицы, `down` откатывает по одной. Сила проверки та же; `test_pairing_tokens_table` — колонки, ключ, индекс.
- Удаление ученика (WP25, A) удаляет и его строки `core.pairing_tokens`.
- Требует подтверждения владельцем: да. **Решено по документам по поручению человека A (2026-11-03):** вариант подтверждён. WP22.1 и `Т-§16.1` требуют хранить хэш одноразового токена. §6.1 для такого же случая — ссылки входа — уже приняла \[РЕШЕНИЕ Ф4\] «таблица в `Т-§3.1` не названа, полные списки — в миграциях» (`core.auth_links`). `Т-§19.1` держит сессии в Postgres, а не в Redis. Расширяющая миграция после G0 разрешена §0.4.4 п.4. Решение класса C закрыто.

### D-A-135. S4: кандидаты извлекателя; S6: переизвлечение после `skill.split` по эпизодам
- Класс: B. WP6.4, WP15 п.2, п.6, WP7.1; `Т-§2.8`, `Т-§4.11`, `Т-§7.6`, `Т-§3.4.6`; вопрос B в #132; `quack/graph/read/`, `tests/support/graph_read.py`, `quack/jobs/rules_p4.py`
- Решение:
  1. **`GraphReader.extraction_candidates(learner_id, session_id, episode_vector, k, *, topic_id=None, skill_ids=None) -> ExtractionCandidates{skills: [CandidateSkill{skill_id, name, topic_id}], misconceptions: [MisconceptionFact]}`** — вход `extractor@3` из памяти (WP15 п.2):
     - навыки тем, активных в сессии, плюс ближайшие к вектору эпизода по `skill_emb`, всего не больше `k` (`candidate_skills_k`);
     - вектора нет (Modal недоступен) — только навыки сессии;
     - `topic_id` или `skill_ids` ограничивают кандидатов при переизвлечении (`episode.reassigned` — тема, `skill.split` — части; WP15 п.6);
     - известные заблуждения — заблуждения навыков-кандидатов.
     
     Один метод вместо трёх, о которых спрашивал B: ограничение темой (п.3) — параметр того же чтения. Реализация — G2 (WP6.4), заглушка `FixedGraphReader` — сейчас.
  2. **`skill.split`** (`Т-§2.8`: «диспетчер ставит переизвлечение эпизодов, где они есть», то есть где есть свидетельства старого навыка):
     - по одной задаче `extract.episode` на эпизод действующего наблюдения со свидетельствами старого навыка;
     - нагрузка — `{episode_seq, restrict_skill_ids}`, кандидаты ограничены частями;
     - это строка S6 с данными сверх события (G2, D-A-110), поэтому прежняя строка «одна задача без эпизода» убрана.
- Почему: §0.4.3 п.2 (буква `Т-§2.8`), п.5.
- Тесты: `tests/graph/test_read_stub.py::test_extraction_candidates_of_wp15`; `tests/jobs/test_rules_p4.py::test_handwriting_known_claim_and_reextraction_rows`.
- Требует подтверждения владельцем: нет.

### D-A-129. WP3.2 `rules@3`, часть 1: срез, изменения, структура и векторы
- Класс: B (п.2–6), A (п.1). WP3.2, WP3.3; `Т-§2.4`, `Т-§2.5`, `Т-§2.8`, `Т-§3.4`; `quack/graph/projection/`
- Решение:
  1. **Место** (класс A). Новый пакет `quack/graph/projection/` рядом с проектором Фазы 3: тот работает на старых событиях до WP1.3 (D-A-103). Изменения — `MergeNode`, `SetProps`, `MergeRel`, `DeleteRel` (`Т-§2.5`) и `DeleteNode` для старого узла слияния (`Т-§2.8`). `Need` — узлы, окрестности (узел, его связи и соседи), узлы метки в домене. Чтение вне `needs(event)` — `OutsideSliceError` (`T-P3`). Применение в памяти строгое: `SetProps` на отсутствующий узел и `MergeRel` без конца — ошибка обработчика.
  2. **Нет цели — пустое применение** (`Т-§2.4`). Ссылки разрешаются через `Alias`. Нет узла — изменений нет. Связь, у которой после слияния оба конца — один навык, не создаётся.
  3. **Хаб «весь домен»** (`Т-§4.8`, «создаётся с доменом»). Создаётся обработчиком `domain.created`: id — UUIDv5(learner_id, seq, 0), имя — имя домена, `origin = learner` (домен заводит ученик), `controlType = no_goal` (у «всего домена» нет вида контроля, `Т-§8.5`), `deadlineAtMs = 0`. Состав не хранится.
  4. **Статусы `Domain` и `Hub` — `active`.** Перечни `Т-§3.4.2` не названы, а в каталоге нет события, которое их меняет (закон 8 — явное значение).
  5. **`domainId` у `Topic` и `Skill`** — фильтр `topic_emb` и `skill_emb` (`Т-§3.4.6`). У новой темы `knownClaimedAtMs = 0` («не было»). Если `topic.created` пришло на уже предложенную тему, меняются только `name`, `origin` и `status`. Состояние навыка (`Т-§3.4.4`) пишет его свёртка в фазе Б (WP4).
  6. **`hub.topics_changed`** несёт весь набор тем хаба: тема не из набора уходит из хаба. Уровни по умолчанию из `controlType` — WP6.3 (`T-TH3`). **`edge.removed`**: перевод выведенных по связи свидетельств в `superseded` — вместе с выведенными свидетельствами WP4.6.
  7. **Вектор `embedding.ready`** — base64 от float32 (WP20.2), порядок байтов little-endian. В документах порядок не назван, сообщено C (#135).
- Почему: §0.4.3 п.1–2, п.5; законы 3, 8.
- Тесты: `tests/graph/test_projection_rules3.py` — `T-P3`, `T-P5`, группа структуры `T-P4`, неизвестный тип `T-P6`. `test_every_projector_row_has_a_handler` красный, пока нет обработчиков остальных 26 строк S5.
- Требует подтверждения владельцем: нет.

### D-A-130. WP3.2 `rules@3`, часть 2: «знаю», предпочтения, материалы, сводка, заблуждения, конец сессии
- Класс: B. WP3.2; `Т-§3.4.2`, `Т-§3.4.3`, `Т-§3.4.6`, `Т-§9.1`, `Т-§9.5`, `Т-§13.5`; `quack/graph/projection/handlers.py`
- Решение:
  1. **Необязательный id в свойстве.** Нет id — явная пустая строка `''` (закон 8: `Material.sessionId` без сессии, `Misconception.targetedInSessionId` после конца сессии). `Т-§3.4.4` задаёт правила для перечислений, счётчиков и времён, но не для id.
  2. **Единственные узлы.** `Profile` (1:1 с учеником) — id UUIDv5(learner_id, 0, `profile`). `Digest` (последняя сводка домена, 1:1) — UUIDv5(learner_id, 0, `digest:<domain_id>`). `seq = 0` в журнале не бывает. Новая сводка заменяет прежнюю на том же узле.
  3. **`preference.set`**: ключ `language` пишется в свойство `prefLanguage` («pref» + ключ в PascalCase, WP5.3 — `prefLanguage`). Набор ключей задаёт запись (`PATCH /v1/learner/preferences`, WP5.3). `digest.written.manner` пишется в `Profile.learnedManner` (§6.2).
  4. **`Material`** — `domainId` из конверта события, `mime`, `sessionId`, `pagesCount` (0 до `material.parsed`). **`Chunk`** — `page`, `text`, `notation`, `domainId` из конверта и `searchable` = «есть текст» (`Т-§3.4.6`, ревью C #135). `TEACHES` и `USES` ведут к навыкам через `Alias`.
  5. **`known.claimed`** → `Topic.knownClaimedAtMs` = время события в мс. **`session.ended`** снимает `targetedInSessionId` у заблуждений домена сессии: у сессии один домен, `Т-§22.9`.
- Почему: §0.4.3 п.2, п.5; закон 8.
- Тесты: `tests/graph/test_projection_rules3.py`, группа 2 (6).
- Требует подтверждения владельцем: нет.

### D-A-131. WP3.2 `rules@3`, часть 3: слияния и разбиения
- Класс: B. WP3.2; `Т-§2.8`, `Т-§3.4.3`; `quack/graph/projection/handlers.py`
- Решение:
  1. **Слияние** (`skill.merged`, `topic.merged`, `misconception.merged`; `Т-§2.8`) — один обработчик:
     - все связи старого узла переходят на цель, вместе с `ALIAS_OF` прежних псевдонимов (иначе цепочка псевдонимов рвётся при удалении узла);
     - старый узел удаляется (`DeleteNode`), его id становится `Alias` → цель.
     
     Не переносятся:
     - связи, которые у цели свои по `Т-§3.4.3`: домашняя тема навыка (`COVERS` внутрь), домен темы (`HAS_TOPIC`), основной навык и «путает с» заблуждения (`ABOUT`, `CONFUSED_WITH`);
     - связь, которая уже есть у цели: например, у цели свои уровни в хабе;
     - связь, которая вела бы к самой цели.
     
     Нет старого узла или цели, или они уже совпадают — изменений нет (`Т-§2.4`). Состояние цели заново считает свёртка фазы Б.
  2. **`topic.split`** — навыки распределяются по частям.
     - Часть со старым id остаётся той же темой и получает имя части.
     - Новая часть — тема того же домена с `origin` и `status` разбиваемой, `knownClaimedAtMs = 0`.
     - Если старой темы среди частей нет, она остаётся без навыков и получает `status = hidden`: она не удаляется (на неё ссылаются события), но и не висит пустой.
  3. **`skill.split`** — появляются новые навыки, старые свидетельства остаются на старом, переизвлечение ставит диспетчер (`Т-§2.8`). Часть берёт `domainId`, `status` и `origin` разбиваемого навыка. Описания у части ещё нет — `summary = ''`.
- Почему: §0.4.3 п.1–2, п.5; закон 8.
- Тесты: `tests/graph/test_projection_rules3.py`, группа 3 (7).
- Требует подтверждения владельцем: нет.

### D-A-132. WP3.2 `rules@3`, часть 4: задачи
- Класс: B. WP3.2; `Т-§3.4.3`, `Т-§4.4`, `Т-§5.1`, `Т-§5.2`, `Т-§5.6`; `quack/graph/projection/`
- Решение:
  1. **`TESTS {level}`** (`Т-§3.4.3`): связь на каждую пару «навык + уровень». Между одной задачей и навыком может быть несколько связей, их различает `level` (`REL_IDENTITY`, Neo4j — `MERGE (t)-[:TESTS {level: $level}]->(s)`). Пары частей задачи (`parts[].tests`) — тоже `TESTS`.
  2. **Описание нового навыка** (`Т-§4.4`):
     - нормализованное имя (регистр и пробелы не считаются) совпало с навыком домена — это тот же навык;
     - иначе — навык `status = provisional`, `origin = session`, `summary = ''`, id = UUIDv5(learner_id, seq, `skill:<имя>`): одинаковые описания в событии дают один навык;
     - `topic_hint` без темы с таким нормализованным именем даёт временную тему `status = proposed` (бледная до принятия, П-2.3), `origin = session`.
  3. **Варианты** — `Option {label, isCorrect, neighborIdea}` из `answer.options` и ловушек разметки. id = UUIDv5(learner_id, seq, `option:<метка>`). Ловушка на известное заблуждение — `TRAPS`. Ловушка на гипотезу — `Misconception {statement, status = unknown, triggers = [], targetedInSessionId = '', searchable}` (WP3.2, §6.2) с `ABOUT` к первому навыку `tests`: основной навык задачи, у заблуждения он ровно один (`Т-§3.4.3`). Без навыков гипотеза узлом не становится.
  4. **Проверка задачи** (`Т-§5.6`):
     - `task.verified` → `verification = checked`, `task.key_disputed` и `task.markup_disputed` → `disputed`;
     - `corrected_markup` заменяет разметку: лишние `TESTS` удаляются, варианты пересоздаются из исправленной разметки.
     
     Понижение уже вынесенных вердиктов задачи до класса model — с обработчиками свидетельств (часть 5).
- Почему: §0.4.3 п.1–2, п.5.
- Тесты: `tests/graph/test_projection_rules3.py`, группа 4 (3).
- Требует подтверждения владельцем: нет.

### D-A-133. WP3.2 `rules@3`, часть 5а: события в срезе, `TESTS {level, part}`
- Класс: B. WP3.2, WP3.3; `Т-§2.4`, `Т-§2.5`, `Т-§3.4.3`, `Т-§7.8` п.1; `quack/graph/projection/`
- Контекст:
  - В нагрузке `verdict.recorded` нет `task_id` — только `answer_seq` и `part` (S3 заморожен).
  - `answer.submitted` граф не меняет (WP3.2).
  - Без задачи проектор не найдёт пары разметки вердикта.
  - Т-§7.8 п.1 требует «части задачи — по своей разметке», но у `TESTS {level}` нет части.
- Решение:
  1. **События в срезе.** У `Need` есть:
     - `events` — события ученика по `seq`, `view.event(seq)`;
     - `event_refs` — `(seq, поле нагрузки, метка)`: узел, названный полем события, вместе с окрестностью (задача ответа для его вердикта).
     
     WP3.2 разрешает такое чтение: прочие события «читают свёртки … через представление». Загрузчик пачки читает эти события из `core.events` до запроса к Neo4j, при пересборке они уже в памяти. События пачки видны следующим событиям той же пачки. Обработчик остаётся чистым (`Т-§2.5`), каталог не меняется.
  2. **`TESTS {level, part}`.** У пары разметки есть часть: `main` для задачи без частей (D-B-121) и `parts[].part` для частей. Связи различает пара `(level, part)`. Это дополнение к `Т-§3.4.3` в рамках закона 8: свойство с явным значением.
- Почему: §0.4.3 п.1–3 (без них вердикт не найдёт пары разметки; каталог S3 не трогается), п.5.
- Тесты: `tests/graph/test_projection_rules3.py` — `test_handler_reads_only_the_events_it_needs`, `test_events_of_the_batch_are_seen_by_later_events`, ключи `TESTS` в тестах задач.
- Требует подтверждения владельцем: нет.

### D-A-134. WP3.2 `rules@3`, часть 5б: вердикты, карточки, «описка», «не согласен»
- Класс: B. WP3.2; `Т-§2.8`, `Т-§3.4.2`, `Т-§7.2`, `Т-§7.8`, `Т-§9.1`, `Т-§9.5`; `quack/graph/projection/`
- Решение:
  1. **Свойства `Evidence`** (`Т-§3.4.2`) фаза А пишет явно (закон 8):
     - `atMs` и `sourceSeq` — время и `seq` действия ученика (ответа) — закон 6;
     - `sourceSessionId` и `sessionDomainId` — из события ответа;
     - `domainId` — домен задачи;
     - `assistance` и `hintStep`: показ решения важнее подсказки, ступень 0 — «не было»;
     - `gapType`: `none` у верного исхода, `unknown` у прочих, пока тип не дало толкование;
     - `quote` — `''` у вердикта и карточки; `searchable` — есть цитата и свидетельство действует;
     - `derivedKind = direct`, `status = active`, `observationSeq = 0`, `extractor = ''`, `solveTimeMs = 0`;
     - `weight`, `novelty`, `delayed` считает свёртка фазы Б (`Т-§7.8` п.5), до неё 0 / false.
  2. **`Evidence.confidence`** — самооценка ученика 1–4 из ответа, 0 — её не было. В вероятности 0.25…0.95 её переводит свёртка профиля (`Т-§9.5`), так обработчик остаётся без параметров. \[ПРОВЕРИТЬ\]: `Т-§3.4.2` называет свойство, но не его смысл; выбрано то, что читает калибровка `Т-§9.5`.
  3. **Вердикт.** Задача и часть берутся из события ответа (D-A-133). Свидетельство создаётся на каждую пару части. Ярус:
     - Т1 — code;
     - Т2 — model, если второе мнение не запрашивалось или согласилось; Т3 — если не согласилось;
     - задача `disputed` понижает вердикты до класса model (`Т-§5.6`).
     
     `unclear` или пропавший ответ — изменений нет. Известное заблуждение вердикта (неверный вариант с ловушкой) или гипотеза модели (новое заблуждение `status = unknown`, `ABOUT` к навыку первого свидетельства) — проявление.
  4. **Одна связь `MANIFESTS` или `RESISTED` на вердикт** — от его первого свидетельства, чтобы один ответ не считался несколько раз.
     - Сопротивление (`Т-§9.1`): успех Т1 или Т2 без показа решения на задаче, вариант которой ловит заблуждение, или которую ход учителя нацелил на него (`Task.targetsMisconceptionId`, часть 5в).
     - Правило пары для Т2 (`Т-§7.3`) применяет свёртка заблуждения (WP5).
  5. **Карточка с самооценкой** (D-A-125 п.1): Т4, уровень 1, по одному свидетельству на навык разметки карточки, исход — по самооценке, `verification = model`.
  6. **Срез задачи** открывает её варианты и свидетельства на ней вместе с их связями (ловушки для `RESISTED`, свидетельства вердикта для правок).
  7. **«Описка» и «не согласен»** (`Т-§2.8`): цель и выведенные из неё по `DERIVED_FROM` получают `status = disputed` и `searchable = false`, у «описки» ещё `gapType = slip`. Вердикт называется как его ключ — `"<answer_seq>:<part>"` (`verdict:{answer_seq}:{part}`, WP2.1). Его свидетельства — на задаче ответа, от этого ответа, не из наблюдения, на паре этой части.
- Почему: §0.4.3 п.1–2, п.5; законы 4, 6, 8.
- Тесты: `tests/graph/test_projection_rules3.py`, группа 5 (12).
- Требует подтверждения владельцем: нет. П.2 решён по документам по поручению человека A (2026-11-03). `Т-§3.4.2` называет `Evidence.confidence`, а единственная потребность в нём — калибровка уверенности ученика `Т-§9.5`: «самооценки 1–4 … сравниваются с исходами свидетельств, проходящих ворота». Единственная уверенность ученика в каталоге — `answer.confidence` 1–4. \[ПРОВЕРИТЬ\] снят.

### D-A-180. WP3.2 `rules@3`, часть 5в: ход учителя, закрытие задачи, наблюдения, диагноз канваса
- Класс: B. WP3.2; `Т-§2.8`, `Т-§7.7`, `Т-§7.8`, `Т-§8.4`, `Т-§9.1`, `Т-§9.4`, `Т-§15.4`; D-A-125 п.2–4; `quack/graph/projection/`
- Решение:
  1. **`turn.committed`**:
     - `Exposure {atMs, kind, approach, sourceSeq, sessionDomainId}` на каждый показ разметки, `OF` к навыку; пропавший навык — без показа;
     - нацеливание ставит `Misconception.targetedInSessionId = сессия` и `Task.targetsMisconceptionId` у задач хода (для `RESISTED`, D-A-134 п.4);
     - временные навыки разметки приходят с `task.created` того же добавления.
  2. **`task.closed`** → `Task.closedHow`, `Task.closedAtMs`. Закрепление после показанного решения (`Т-§8.4`) считает свёртка навыка (WP4.5) по задачам навыка. Это дополнение к свойствам `Task` (`Т-§3.4.2`) в рамках закона 8: без него пересборка не увидела бы закрытие.
  3. **`observation.extracted`**:
     - **Какое наблюдение действует.** `extractor_precedence = ["extractor@3"]` (WP3.6, `Т-§22.10`), других извлекателей проектор не применяет. Новое наблюдение эпизода делает свидетельства прежнего `superseded` вместе с выведенными (`Т-§2.8`).
     - **Поиск по эпизоду.** У `Evidence` новое свойство `episodeSeq` (0 вне наблюдений). В `Need` — `matches` `(метка, свойство, значение)`: узлы по значению вместе со связями.
     - **Элемент → свидетельство Т3** (`Т-§7.8` п.4): цитата, помощь, тип пробела, `atMs` — из элемента, `sourceSeq` — последнее исходное событие, `observationSeq`, `extractor`. «Неясно» — без свидетельства. Новый навык — временный (`Т-§4.4`).
     - **Заблуждения и корни.** Известное заблуждение или гипотеза — проявление (у гипотезы новое заблуждение `unknown`). Корень — `ROOT_CAUSE {confidence}` по `root_cause_confidence_map` (D-B-106, `Т-§9.4`).
     - **Параметры.** Обработчик читает параметры правил через `view.params` (`RulesParams`; параметры входят в `rules@N`, `Т-§2.5`).
  4. **`observation.diagnosed`** (D-A-125 п.2): уровень 0, `incorrect`, Т3, `gapType` диагноза, `observationSeq` = событие. Свидетельство ложится на основной навык (`ABOUT`) названного заблуждения, иначе — по одному на каждый навык `TESTS` задачи; нет ни того, ни другого — изменений нет. Заблуждение или гипотеза — `MANIFESTS` от первого свидетельства. Корень — `ROOT_CAUSE {confidence = уверенность диагноза}`: категории у диагноза канваса нет.
  5. **`observation.second_opinion`** (D-A-125 п.3): согласие поднимает свидетельства целевого диагноза Т3 → Т2, несогласие их не меняет.
- Почему: §0.4.3 п.1–2, п.5; законы 4, 6, 8.
- Тесты: `tests/graph/test_projection_rules3.py`, группа 6 (7); `test_every_projector_row_has_a_handler` зелёный — 38 из 38.
- Требует подтверждения владельцем: нет.

### D-A-181. WP3.3 `rules@3`: запись в Neo4j разницей, цикл пачки на `Db`, остановка потока
- Класс: B (п.2–4), A (п.1). WP3.3; `Т-§2.4`, `Т-§3.4.7`, `Т-§8.1` (ориентир); `quack/graph/projection/store.py`, `runner.py`
- Решение:
  1. **Место.** `project_learner(db, neo4j, settings, learner_id)` работает под той же advisory-блокировкой ученика, что пересборка и удаление (`Т-§2.4`). Запись в Neo4j — только `store.write_batch`: тест T-A9a разрешает его и больше ничего (`tests/test_contracts.py`).
  2. **Пачка.**
     - Отметка — `Learner.projectedSeq`. Берутся события после неё — `projector_batch_max_events` (переименование в `projector_batch_size` по D-A-102 — с WP3.6).
     - События, названные в `needs()` (`Need.events`), читаются из `core.events`. Узлы, названные этими событиями, тоже попадают в срез.
     - Срез читается из Neo4j одной транзакцией: узлы по id, цепочки `Alias`, окрестности, срез задачи, выборки, узлы метки в домене.
     - Фаза А, затем в одной записывающей транзакции — **разница** среза до и после пачки и новая отметка с `projectorVersion = rules@3`. Разница: новые узлы целиком, у старых — только изменившиеся свойства, удалённые узлы и связи; запросы сгруппированы по виду через `UNWIND $rows` (`Т-§3.4.7`). Так пишется только изменившееся (`Т-§8.1`), а порядок изменений внутри пачки не влияет на итог.
     - После фиксации — копия отметки в `ops.projector_offsets` (её отставание безвредно, `Т-§2.4`).
  3. **Событие, которое проектор не может применить** (неизвестный тип, новее известной схемы, ошибка обработчика): вся пачка отклоняется, в `ops.projector_failures` пишется `seq` и ошибка, поток ученика стоит (`stopped`), остальные ученики работают. `resume` (S15, `ops resume`) снимает запись. Временная ошибка Neo4j поднимается к вызывающему: тот повторяет пачку с растущей паузой (цикл процесса — с WP7 и WP9.6).
  4. **Фазы Б–В** (свёртки WP4–WP6) и публикация изменённых тем в Redis подключаются после фазы А. До них у навыков нет состояния в графе.
- Почему: §0.4.3 п.1 (пачка целиком или никак, закон 3), п.2, п.5.
- Тесты: `tests/graph/test_projection_diff.py` (2); `tests/graph/test_projection_runner.py` (3, Postgres и Neo4j): материалы и векторы доходят до Neo4j, пачки любого размера дают один граф (часть `T-P1`), `test_unknown_type_stops_stream` (`T-P6`).
### D-A-137. S4: чтения для контекста хода и описания заблуждения; `GraphReader` в `workers`; снимок слотов 3–4
- Класс: B. WP6.4, WP14.1–14.2, WP16.2; `Т-§9.3`, `Т-§9.5`, `Т-§10.1`, `Т-§10.2`; вопросы B в #125 и #155; `quack/graph/read/`, `tests/support/graph_read.py`
- Решение:
  1. **`SkillFact.name`, `SkillFact.topic_name`** (оба `None`, пока не прочитаны) — срез и все чтения отдают названия сразу.
  2. **`GraphReader.domain_goals(domain_id) -> list[HubGoal{hub_id, name, kind, control_type, deadline_at_ms, topics: [HubTopicGoal{topic_id, name, required_levels}]}]`** — по графу: слияния, разбиения и переименования тем уже учтены.
  3. **`GraphReader.profile(learner_id) -> ProfileFacts{prefs, manner, learned_helpful_approach, learned_confidence_brier, learned_confidence_bias, learned_branch_rate, learned_reasoning_share}`** (`Т-§9.5`): `None` — «нет данных» (меньше `profile_min_samples`, для уверенности — `calibration_min_reports`), не ноль. Выученное считает свёртка профиля (WP5.3).
  4. **`GraphReader.topic_memory(topic_ids) -> list[TopicMemory{topic_id, name, domain_id, skills, misconceptions}]`** — навыки тем цели сессии; их `skill_id` идут в `SliceFocus.skill_ids`. Неизвестная тема пропускается.
  5. **`GraphReader.misconception_detail(misconception_id) -> MisconceptionDetail | None`** — id через `Alias` (после `misconception.merged` читается цель); поля `status` (включая `unknown` у гипотезы до первого проявления), `statement`, `triggers`, `skill_id`, `skill_name`, `manifestations: [ManifestationFact{evidence_id, quote, task_id, source_seq, at_ms, tier}]` — все по `MANIFESTS`, включая перенесённые слиянием.
  6. **Как `workers` получает читателя:** поле в `JobContext` не добавляется — набор полей это контракт §4.2 без читателя. Обработчик строит `quack.graph.read.create_graph_reader(ctx.neo4j, ctx.settings)` там, где ему нужна память; тест подставляет `FixedGraphReader`. До G2 фабрика поднимает `NotImplementedError`.
  7. **Снимок слотов 3–4 на сессию:** `Т-§10.1` требует заморозки на начало сессии, `state_at` для этого не нужен. Место хранения — зона B (контекст хода). Колонка `frozen_slots JSONB NULL` в `ops.turn_manifests` — миграция A, предложение B принято: `db/migrations/20261103000003_turn_manifests_frozen_slots.sql` (`ADD COLUMN frozen_slots JSONB NULL`, откат — `DROP COLUMN`). B пишет `{profile, domain_and_goals}` в манифест первого хода и читает снимок из самого раннего манифеста сессии с непустым `frozen_slots`; строки хвоста — из событий после `session.started`.
- Почему: §0.4.3 п.2 (`Т-§9.5`, `Т-§10.1`), п.5; состав полей назван A в #125 и #155.
- Тесты: `tests/graph/test_read_stub.py` (names, goals, profile, topic_memory, misconception_detail, factory); `tests/db/test_baseline_migration.py::test_turn_manifests_frozen_slots_column`.
- Требует подтверждения владельцем: нет.

### D-A-136. S6 G2: строки `artifact.topic_refresh` по `Т-§17.7`; cheat sheet — после согласия
- Класс: B. WP7.1, WP23.2, WP23.5; `Т-§13.3`, `Т-§17.7`; приоритет C в #135; `quack/jobs/rules_p4.py`
- Решение:
  1. **Данные сверх события** — `RuleFacts`; `plan_with_facts(event, settings, facts)` = строки по событию + строки G2.
     - Условие триггера (показы и свидетельства сессии, подсказки задачи) диспетчер читает из журнала (`Т-§13.3`: «диспетчер читает журнал, а не проекцию»).
     - Домашняя тема навыка — структура графа (`COVERS`, `Т-§3.4.3`), а не состояние ученика. Её диспетчер читает в процессе проектора (WP7.1, `Т-§22.4`) после применения пачки. Сбор фактов — WP7 (диспетчер на `Db`).
  2. **`session.ended`** — `artifact.topic_refresh` на каждую тему с показами или свидетельствами сессии:
     - P2, `lock_key = coalesce_key = artifact:{topic}`, `required_seq` = событие (задача читает граф, WP7.1);
     - ключ `artifact.topic_refresh:{topic}:session:{session_id}` — одно обновление темы за сессию: «дубли отсекает ключ задачи», `Т-§17.7`;
     - нагрузка `{topic_id, reason: session}`.
  3. **`task.closed`, где понадобились подсказки**, — блок «твои ловушки» по темам навыков задачи:
     - ключ `artifact.topic_refresh:{topic}:task:{task_id}`;
     - нагрузка `{topic_id, reason: hints, task_id}`;
     - блокировка та же — `artifact:{topic}`.
     
     Свой ключ, чтобы не отсечь обновление по концу сессии.
  4. **Cheat sheet** (`Т-§17.7`, WP23): «дедлайн ближе `cheatsheet_offer_days`» — это предложение учителя или «Сегодня», а «генерация — после согласия». Поэтому `hub.deadline_set` задачу генерации не ставит. `artifact.cheatsheet` (`artifact:{hub}`) ставится событием согласия; каким именно — вопрос C в #135. Строка остаётся в `ROWS_NEED_FACTS` до ответа C.
- Почему: §0.4.3 п.2 (буква `Т-§17.7`, `Т-§13.3`), п.5.
- Тесты: `tests/jobs/test_rules_p4.py::test_artifact_topic_refresh_after_the_session_and_after_hints`.
- Требует подтверждения владельцем: нет.


### D-A-138. S6: ключ склейки `artifact.topic_refresh` несёт ветку; блокировка прежняя
- Класс: B. WP7.1, WP23.2; `Т-§17.7`, `Т-§11.3`; запрос C в #154; `quack/jobs/rules_p4.py`, `tests/jobs/test_rules_p4.py`
- Решение: `lock_key = artifact:{topic}` (один писатель темы, по документу), `coalesce_key = artifact:{topic}:session:{session_id}` / `artifact:{topic}:task:{task_id}`.
- Почему: при общем `coalesce_key` `enqueue_job` затирает нагрузку ждущей задачи (`ON CONFLICT (coalesce_key) WHERE status = 'queued'`): ветка подсказок теряла `reason: hints` и `task_id` (или наоборот), а `Т-§17.7` требует и «одно обновление на тему за сессию», и «блок ловушек по задаче». Блокировка не меняется, поэтому две ветки одной темы всё равно идут по очереди. Документ задаёт ключ блокировки и склейки одной строкой `artifact:{topic}`; склейка по ключу ветки сохраняет смысл (дубли одной ветки склеиваются), теряется только склейка разных веток — она и была ошибкой.
- Тесты: `tests/jobs/test_rules_p4.py::test_artifact_topic_refresh_after_the_session_and_after_hints` (красный до правки: ключ ветки сессии).
- Требует подтверждения владельцем: нет (мелкое уточнение ключа склейки, блокировка по документу).

### D-A-139. S6: `hub.deadline_set` -> `artifact.cheatsheet` строкой по событию
- Класс: B. WP7.1, WP23.5; `Т-§17.7`; ответ C D-C-138 в #154; `quack/jobs/rules_p4.py`
- Решение: каждое `hub.deadline_set` ставит одну задачу `artifact.cheatsheet`: P2, `lock_key = coalesce_key = artifact:{hub}`, `required_seq` = событие, нагрузка `{hub_id}`. Строка убрана из `ROWS_NEED_FACTS`. Условие `cheatsheet_offer_days` и состояние предложения (нет `agent.requested` согласия и `artifact.updated` вида cheatsheet по хабу) проверяет сама задача по журналу (C, D-C-138); нового события нет, каталог S3 заморожен.
- Почему: C отвечает за состояние предложения (`Т-§17.7`, WP23.5); фактов на этапе планирования строке не нужно.
- Тесты: `tests/jobs/test_rules_p4.py::test_hub_deadline_set_puts_one_cheatsheet_job_without_facts`.
- Требует подтверждения владельцем: нет.

### D-A-160. WP7.2: границы эпизодов и `has_content` — выборы там, где ТЗ молчит
- Класс: B. WP7.2, `Т-§7.5`, `Т-§2.1`; `quack/jobs/episodes.py`, `quack/jobs/dispatcher_p4.py`
- Контекст: ТЗ называет границы и принцип `has_content`, но не говорит, какие события входят в эпизод, по какую сторону границы лежит событие смены намерения, как считать «пустоту».
- Решение:
  1. **События эпизода** — столбец «Диспетчер = эпизод» таблицы §6.3 (`EPISODE_OPENERS`: `session.started`, `message.sent`, `answer.submitted`, `element.responded`, `canvas.line_*`, `voice.transcribed`, `hint.requested`, `solution.requested`, `check.skipped`, `item.flagged_unclear`, `hint.shown`, `turn.committed`) плюс `task.closed` и `session.ended` как закрывающие. `verdict.recorded` только присоединяется к открытому эпизоду и не открывает его: вердикт страховки приходит после закрытия эпизода, и без этого запоздалый вердикт делал бы эпизод из одного себя. Выходы системы (наблюдения, сводки, результаты агентов) эпизод не открывают и таймер простоя не двигают.
  2. **Стороны границ.** `task.closed`, `session.ended` и предел ходов закрывают эпизод вместе с собой (`to_seq` — их `seq`). Смена `head.intent` или `markup.plan_update.current_step` в `turn.committed` закрывает эпизод до этого хода (`to_seq` — последнее событие перед ним), а сам ход открывает следующий: ход с новым намерением относится к новой теме разговора. Первый ход эпизода границы не даёт.
  3. **Простой** `episode_idle_min`: (а) по времени событий — если между последним событием эпизода и следующим событием сессии прошло не меньше, эпизод закрывается на последнем событии (повтор журнала закрывает те же эпизоды); (б) по часам — `close_idle_episodes` (периодическая проверка диспетчера, шаг `consumer_poll_ms`) для сессии, которая замолчала. Ученика с ещё не прочитанными событиями проверка пропускает.
  4. **`has_content`** — `False`, только если в эпизоде нет слов ученика (`message.sent`, `voice.transcribed`, `item.flagged_unclear`, `element.responded` с текстом), строк канваса (`canvas.line_committed`/`erased`), ответов не закрытых типов и ответов с размышлением (`text`), и каждый закрытый ответ (`choice`, `number`, `expression`, `set`, `vector`, `quantity`, `short_text`) имеет в эпизоде вердикт `verification = code` по тому же `(answer_seq, part)`. Закрытый ответ без вердикта в эпизоде (страховка `check.symbolic` придёт позже) считается содержанием: извлекатель лучше прочтёт лишнее, чем пропустит слово. Эпизод вообще без ввода ученика — `False` (толковать нечего; показы пишет код, `Т-§7.8` п.6).
  5. **Запись.** `episode.closed`: актор `system`, ключ `episode:{session_id}:{to_seq}` (`keys.episode_key`), `causation_seq = to_seq`, `occurred_at` — время события, решившего закрытие (по часам — время проверки), `domain_id` — из событий эпизода; `append_events` в транзакции отметки. Открытый эпизод каждой сессии — `counters.episodes[<session_id>]`.
  6. **Покрытие `nothing_to_extract`:** диспетчер пишет его при разборе самого `episode.closed` с `has_content = false` (`episode_seq` — `seq` этого события, как в `observation.extracted`, D-B-172; `extractor` — `prompt` роли `extractor` из `Settings.llm_roles`, сейчас `extractor@3`), без задачи.
- Почему: `Т-§7.5`, `Т-§2.8`; §0.4.3 п.2 (буква документа там, где она есть), п.5.
- Обратимость: один модуль, чистая машина состояний.
- Тесты: `tests/jobs/test_episodes.py` (17), `tests/jobs/test_dispatcher_p4.py` (T-D2, T-D3, повтор журнала). Проверено 10 подменами (граница `>=`/`>`, сторона границы, простой, открытие запоздалым вердиктом, ключ `episode.closed`, нет покрытия, не считаются ответы) — каждая роняет тест.
- Требует подтверждения владельцем: нет. Вопрос к B: п.2 (сторона границы смены намерения) — в `Т-§7.5` не уточнено.

### D-A-161. WP7.4: очередь Фазы 4 на `Db` — выборы там, где `Т-§13.1` молчит
- Класс: B. WP7.4, `Т-§13.1`, `Т-§13.2`, `Т-§13.7`–`Т-§13.9`; `quack/jobs/queue_p4.py`, `quack/jobs/executor_p4.py`
- Решение:
  1. **Попытка считается при захвате** (`attempts += 1` в `claim_next`), а не при сбое: исполнитель, упавший посреди задачи, тоже тратит попытку, и задача, убивающая исполнителя, не крутится вечно; `defer` и `release` попытку возвращают. `attempts` же — защита от «потерянной» задачи: `heartbeat`, `complete`, `fail_or_retry`, `defer`, `release` действуют только при совпадении `attempts` у захвата, поэтому исполнитель, у которого чистильщик отнял задачу и её взял другой, ничего не завершит. Колонки `locked_by` в `Т-§13.1` нет, и она не нужна.
  2. **Захват.** Кандидат: `queued`, `run_at <= now()`, `projector_offsets.seq >= required_seq`, вид из допущенных, нет задачи с тем же `lock_key` в `running` или `cancel_requested`, у ученика в работе меньше `max_jobs_per_learner` (предварительно в запросе, точно — под `pg_advisory_xact_lock` ученика, чтобы два исполнителя не обогнали друг друга); `ORDER BY priority_class, created_at, job_key FOR UPDATE SKIP LOCKED`. Задача в `cancel_requested` ещё занимает ключ и место ученика: исполнитель её не остановил.
  3. **Склейка** оставляет нагрузку ждущей задачи как есть и поднимает только `required_seq` (`Т-§13.1`: нагрузка склеиваемой задачи не несёт данных о событии). Вставка: сначала `job_key` (повтор отбрасывается при любом статусе), затем `UPDATE … GREATEST` по `coalesce_key`, затем `INSERT … ON CONFLICT (job_key) DO NOTHING` в точке сохранения; гонку за `coalesce_key` ловит `UniqueViolation` и один повтор. `ON CONFLICT (coalesce_key)` в одном выражении с `job_key` PostgreSQL не допускает (одна арбитражная цель), поэтому порядок такой.
  4. **Бюджет.** Перед стартом задача P2/P3 сессии, чей вид не в `BUDGET_EXEMPT_KINDS` (`extract.episode`, `analyst.run`, `analyst.deep`), получает `skipped_budget`, если `ops.llm_usage` этой сессии по ролям классов P2/P3, кроме извлечения, аналитика и `session_budget_exempt_roles`, превысила `session_agent_budget_usd` (ровно предел — ещё можно). Ходы учителя и P1 не считаются. Дневной потолок — вызов шлюза (WP10.4), очередь его не трогает.
  5. **Сбой.** Временная ошибка — `queued` с `run_at = now() + min(cap, base · 2^attempts) · (0,75…1,25)`; исчерпание `job_max_attempts` или `JobFailedPermanently` — `failed` и `agent.completed {ok: false, error}` в одной транзакции (закон 7): актор `system`, ключ `agent_completed:{job_key}`, для поручения `request_id` из нагрузки, иначе `job_key`; `kind` — вид агента из нагрузки либо вид задачи; текст ошибки до 500 знаков, без стека (колонки нет). Задача периодическая (без ученика) события не пишет. Чистильщик возвращает задачу без паузы; зависшая `cancel_requested` — `cancelled`. Возврат в очередь задачи с `coalesce_key`, когда уже ждёт новая с тем же ключом, — `cancelled` со склейкой `required_seq` в ждущую (индекс не пустит двух).
  6. **Исполнитель.** `run_claimed`: задача handler'а — `asyncio`-задача, пульс раз в `job_heartbeat_interval_sec`; ответ пульса `cancel_requested` — отмена задачи и `cancelled`; `None` — задачу забрали, ничего не пишем; отмена самого исполнителя (остановка) — `release`. `JobDeferred`/`JobFailedPermanently` — классы Фазы 3 (`quack.jobs.queue`), чтобы обработчики B писали одно и то же. `JobWorker` — цикл `claim → run`, допуск по флагам давления Фазы 3 (`quack.llm.pressure`, только чтение) для ролей вида, чистильщик каждые `job_stale_after_sec / 2`, плавная остановка.
- Почему: `Т-§13.1`, `Т-§13.2`; закон 5 (повтор не дублирует), закон 7.
- Обратимость: модули рядом со старой очередью (D-A-103), старые не тронуты.
- Тесты: `tests/jobs/test_queue_p4.py` (33), `tests/jobs/test_executor_p4.py` (8): T-D4, T-D5, T-D6, T-D7, T-D8, T-D10 (запрос) и гонки (захват 20 исполнителей, склейка 10 вставок, один `lock_key`, предел ученика). Мутации: без блокировки ученика, без `required_seq`, без `GREATEST`, без `cancel_requested` в пульсе, без проверки `attempts`, без `release`, без `complete`, без ветки отмены — каждая роняет тест (мутант «без `FOR UPDATE SKIP LOCKED`» эквивалентен: `UPDATE … WHERE status = 'queued'` не отдаст задачу дважды и без него).
- Требует подтверждения владельцем: нет.

### D-A-162. WP7.1: строки с данными сверх события — диспетчер читает журнал, не граф
- Класс: B. WP7.1, `Т-§13.3`, `Т-§17.7`, `Т-§2.8`, `Т-§4.10`; уточняет D-A-136 п.1, D-A-135, D-A-117; `quack/jobs/facts_p4.py`, `quack/jobs/rules_p4.py`, `quack/jobs/dispatcher_p4.py`
- Решение:
  1. **Домашнюю тему навыка диспетчер берёт из журнала, не из `COVERS`.** D-A-136 п.1 говорил «структура графа»; но `Т-§13.3`: «диспетчер читает журнал, а не проекцию», и диспетчер, и проектор идут по журналу независимо — граф к моменту `session.ended` может быть ещё не применён. `HomeTopics` сворачивает события структуры по `seq` (`skill.created`, `skill.split`, `skill.merged`, `topic.split`, `topic.merged`; псевдонимы идут по цепочке). Временный навык, чей id даёт только проектор, домашней темы в журнале не имеет и тем не даёт.
  2. **`session.ended` → `artifact.topic_refresh`:** навыки показов (`turn.committed.markup.exposures`), свидетельств извлекателя (`observation.extracted`, элементы `evidence`) и свидетельств вердиктов (навыки `tests` задач, на которые в сессии отвечали) → темы. **`task.closed`** → темы навыков задачи (последняя исправленная разметка, иначе своя), если по задаче был `hint.shown`. Строки и ключи — D-A-136; `RuleFacts` расширен полями строк ниже.
  3. **`skill.split`:** эпизоды — строки `ops.coverage` со статусом `extracted`, чьё наблюдение имеет элемент `evidence` старого навыка; по задаче на эпизод, ключ `extract.episode:{learner}:{seq}:{episode_seq}`, нагрузка `{episode_seq, restrict_skill_ids}` (D-A-135). При двух версиях извлекателя эпизод может прийти от старой версии лишний раз, но не потеряться.
  4. **`graph.hygiene`:** `session.ended`, если в сессии есть временные сущности — `skill.created` со статусом `provisional`, описание нового навыка (`new_skill`) в разметке задач, в `task.created` или в элементе наблюдения; домен — из `session.started`. И `topic.created`: счётчик новых тем домена в `counters.new_topics`, по достижении `graph_hygiene_every_topics` — задача и сброс. P3, `domain:{id}` / `hygiene:{domain}`, `required_seq` = событие, нагрузка `{domain_id, reason}`.
  5. **`embedding.compute`:** строки `chunk.indexed` (текст куска) и `misconception.described` (формулировка) — по событию, без графа, `required_seq` пуст (D-A-117). Остаются открытыми: тема и навык (какое свойство эмбеддится — \[ПРОВЕРИТЬ\] в `Т-§3.4.2`), свидетельство (id даёт проектор). Строка `*` в `ROWS_NEED_FACTS` остаётся.
  6. **Вопрос владельцу.** В каталоге §6.3 столбец «Диспетчер» отмечен у `skill.merged` и `material.parsed`, а строки в `Т-§13.3` и WP7.1 у них нет; `test_dispatcher_column_of_the_catalog_…` держит этот список из четырёх типов явно (вместе с `skill.created`, `topic.proposed` — эмбеддинг).
- Почему: `Т-§13.3`; §0.4.3 п.2; закон 1 (журнал — источник), закон 9 (диспетчер не ждёт проектор).
- Обратимость: данные собираются в одном модуле, строки — в таблице.
- Тесты: `tests/jobs/test_dispatcher_p4.py` (`test_skill_split_…`, `test_session_end_…`, `test_closed_task_with_hints_…`, `test_hygiene_…`, `test_embedding_jobs_…`), `tests/jobs/test_rules_p4.py`. Подмены: нет `learner_id` в выборке покрытия, любой элемент вместо `evidence`, нет свёртки структуры, не сбрасывается счётчик — роняют тест.
- Требует подтверждения владельцем: нет.

### D-A-163. Тесты WP7 без dbmate: частная база и селекторный цикл на Windows
- Класс: A (`tests/jobs/`). `tests/jobs/conftest.py`
- Решение: фикстура `jobs_db` создаёт базу `<тестовая>_p4_wp7`, применяет `migrate:up` каждого файла `db/migrations/*.sql` через psycopg и очищает таблицы перед каждым тестом. dbmate не нужен, общая база `<тестовая>_p4` (её `TRUNCATE` ломает параллельные прогоны других потоков) не используется. На Windows psycopg не работает на `ProactorEventLoop`, поэтому `conftest` ставит `WindowsSelectorEventLoopPolicy` (только `win32`).
- Тесты: все тесты WP7 выше. Требует подтверждения владельцем: нет.

### D-A-164. T-DB4 и очередь: межученические запросы очереди и диспетчера — в списке исключений
- Класс: B. WP7.1, WP7.4; `Т-§13.1`–`Т-§13.3`, ТЗ WP1.2 (`T-DB2`, `T-DB4`); `tests/db/test_sql_static.py`, `quack/jobs/queue_p4.py`, `quack/jobs/dispatcher_p4.py`
- Решение: очередь и диспетчер обслуживают всех учеников. Задача находится по глобальному `job_key` или `coalesce_key`. У периодической задачи нет ученика. Выбор берёт лучшую задачу любого ученика. Диспетчер обходит учеников, отставших от своей отметки. Их запросы внесены в `CROSS_LEARNER_ALLOWED` по имени, с причиной (как D-C-111, D-A-128). Встроенный SQL вынесен в именованные константы (`_CANCEL_SUPERSEDED`, `_STALE`, `_PENDING_LEARNERS`, `_LAG`, `_IDLE_LEARNERS`): по имени его можно внести в список, а f-строка в вызове запрещена `T-DB2`. Чтения и отмена по ученику (`request_cancel`, `visible_jobs`) остаются с `learner_id` в `WHERE`.
- Почему: §0.4.3 п.1; CI #182 — 2 failed (`test_no_fstring_sql`, `test_repository_functions_take_learner_id`).
- Тесты: `tests/db/test_sql_static.py` — 4 passed; `tests/jobs` — 118 passed.
- Требует подтверждения владельцем: нет.

### D-A-170. WP5.3: свёртка `learned*` профиля — входы, пороги, "нет данных"
- Класс: B. WP5.3; `Т-§9.5`, `Т-§12.3`, `Т-§12.6`, `Т-§2.5`; `quack/graph/profile_fold.py`
- Решение: чистая свёртка без ввода-вывода и без текущего времени. Вход — выборки (`TurnSample`, `AnswerSample`, `ConfidenceSample`, `EvidenceSample`), порядок поступления не важен (всё упорядочено по `seq`).
  1. **Что помогает** (`learnedHelpfulApproach`): выборка — пара (ход `turn.committed`, навык из `head.target_skill_ids`) с Т1-попыткой этого навыка после хода и до следующего хода с тем же навыком; считается **первая** такая попытка, успех — `outcome = correct` и `assistance = none`. Подход `none` не считается. Подход оценивается, когда у него самого не меньше `profile_min_samples` выборок; побеждает большая доля успехов, при равенстве — больше выборок, затем имя по алфавиту. Нет такого подхода — «нет данных». `Т-§9.5` говорит «пока данных меньше `profile_min_samples`» и не уточняет, на подход или на всё; выбрано на подход, потому что долю нельзя сравнивать по двум-трём попыткам.
  2. **Надёжность уверенности** (`learnedConfidenceBrier`, `learnedConfidenceBias`): самооценка 1–4 → 0.25/0.5/0.75/0.95; пара — самооценка и исход свидетельств её действия (`sourceSeq` = `seq` действия), проходящих ворота (флаг ворот приходит от свёртки навыка, здесь не пересчитывается), только `correct`/`incorrect` (`partial` ворот не открывает, `Т-§7.3`); успех, если все такие свидетельства успешны. Источники самооценки: `answer.submitted.confidence`, `element.responded.confidence`, `confidence.reported {target_seq, value}`; на одно действие берётся самая поздняя по `seq`. Последние `calibration_window` пар по `seq` действия; меньше `calibration_min_reports` — «нет данных». Оценка Брайера — среднее `(p - исход)^2`, 6 знаков. Знак перекоса — знак среднего `p - исход`: `over` (> 0), `under` (< 0), `calibrated` — только при нуле (допуск 1e-9): порога «в пределах нормы» в `Т-§9.5` нет, я его не выдумываю.
  3. **Корректирующие ответвления** (`learnedBranchRate`): доля `turn.committed` с `head.branch_type = corrective` среди всех `turn.committed` ученика; не меньше `profile_min_samples` ходов.
  4. **Ход мысли** (`learnedReasoningShare`): доля `answer.submitted` и `element.responded` с непустым `text` среди всех таких; не меньше `profile_min_samples` ответов.
  5. **«Нет данных»** — свойство **не пишется** (на узле его нет; запись удаляет прежнее): в Neo4j нет `null`-значения свойства, ноль — это данные (закон 8). Читатель отдаёт `None`.
  6. **Профиль целиком** (`profile_node_props`): `pref*` — `preference.set`, последнее по `seq` на ключ (ключ `answer_length` → `prefAnswerLength`, как в D-A-130); `learnedManner` — `manner` последнего `digest.written`; плюс `learned*`. Узел `Profile` — UUIDv5 ученика (D-A-130).
- Почему: `Т-§9.5` («выученное — код и одинаково при пересборке», пороги), WP5.3, `Т-§2.5`.
- Тесты: `tests/graph/test_profile_fold.py` (Брайер и знак, окно, ворота, самооценка по позднейшему сообщению, подход с подсказкой, пороги, повтор в любом порядке и пачками); проверено мутациями (знак, ворота, окно, «подсказка — успех»).
- Требует подтверждения владельцем: нет.

### D-A-171. WP5.3: проектор считает профиль, когда готов проектор Фазы 4 (WP3.3); чтение — `read_profile`
- Класс: B. WP5.3, WP6.4; `quack/graph/read/neo4j_profile.py`
- Решение: (а) в `phase-4` на этот момент нет проектора Фазы 4 с фазой Б (он в `phase-4-a-wp3-projector`, WP3.3 не закончен), поэтому свёртка отдана как `profile_node_props(collect_profile_inputs(events, evidence), ProfileParams.from_settings(settings))` → `(props, unset_keys)`; фаза Б вызывает её на каждом событии из `PROFILE_EVENT_TYPES` и на изменении свидетельств ученика, пишет `props` в `Profile` и удаляет `unset_keys` (`REMOVE`). `evidence` — прямые действующие `Evidence` ученика с флагом ворот от свёртки навыка. Подключение к фазе Б — задача WP3.3 после слияния. (б) `read_profile(driver, learner_id) -> ProfileFacts`: один READ-запрос по `Profile.learnerId`; `pref*` → ключи (`prefAnswerLength` → `answer_length`); нет узла — пустой профиль; проверку отметки (`Т-§2.6`) делает вызывающий читатель. (в) `ProfileFacts` не менялась.
- Почему: закон 3 (чтение через `read_session`), закон 1 (запрос по `learnerId`).
- Тесты: `tests/graph/test_profile_read.py` (запись-чтение, «нет данных» после очистки, ноль — данные, изоляция учеников, нет узла).
- Подключение (после #179): `Neo4jGraphReader.profile` вызывает `read_profile`; на чужого ученика привязанный читатель отвечает пустым профилем (D-A-144). Проверка — `test_no_profile_is_an_empty_profile_and_learners_are_isolated`, `test_factory_and_extras`.
### D-A-150. WP6.4: чтения `ExtraReads` — схема, изоляция, контроль отметки
- Класс: B. WP6.4, `Т-§2.6`, `Т-§3.4`, закон 1; `quack/graph/read/neo4j_extras.py`, `skill_state.py`
- Решение: (1) схема графа — по `Т-§3.4` (узлы `Skill` со свойствами `lNStatus`, `lNBasis`, `lNDecisiveEvidenceId`, `ret*`, `needsConsolidation`, `gapType`; связи `COVERS`, `ON`, `ON_TASK`, `OF`, `MANIFESTS`, `ABOUT`, `REQUIRES`, `INCLUDES`, `ALIAS_OF`) и по обработчикам `rules@3` (ветка `phase-4-a-wp3-projector`); свёртка навыка WP4 пишет `lN*`/`ret*` — чтения готовы под неё. (2) Подпись `skill_history(skill_id)`, `prerequisites(skill_id)`, `prep_context(hub_id)`, `misconception_detail(id)` не несёт `learner_id`: владелец читается с узла-якоря, остальные узлы привязаны к его `learnerId`. (3) Чтения среза идут с проверкой `Learner.projectedSeq` до и после, `1 + snapshot_read_retries` попыток; кончились — возвращается последнее чтение (закон 9). (4) Уровень: `unknown` -> `not_tested`; база `indirect`/`implied`/`none` -> `None`; `proven_by_direct` = `proven` с базой `direct`; `last_t1_failed` — последнее активное Т1 уровня с исходом `incorrect`; ярус: Т1 `code`, Т2 `model`, Т3–Т4 `conversation`. Учитываются только свидетельства `status = active`, уровень >= 1.
- Почему: §0.4.3 п.2 (буква `Т-§3.4`, `Т-§8.2`, `Т-§8.7`).
- Тесты: `tests/graph/test_read_neo4j_extras.py`.
- Требует подтверждения владельцем: нет.

### D-A-151. WP17.1: приоритет кандидатов на повторение
- Класс: B. WP17.1, `Т-§14.1`
- Решение: причины — `retention` (R < `review_trigger_retention` или `retDueAtMs` <= сейчас), `consolidation`, `misconception_recheck` (`watching` и `watchSinceMs + watch_days` <= сейчас), `hub_requirement` (хаб с дедлайном в будущем, требуемый уровень навыка не `proven`/`implied`). Приоритет = `w_need`·нужность + `w_deadline`·`1/(1 + дней до ближайшего дедлайна)` + `w_forgetting`·`(1 − R)` + `w_consolidation`·(флаг или перепроверка); нужность = 1, если навык — предпосылка (`REQUIRES`, 1–2 шага, `confidence >= requires_min_confidence`) одного из `goal_skill_ids`. Вид убывания веса дедлайна в документах не назван — взят без параметров. «Разрешённое забывание»: нет нужности и нет дедлайна, а последняя практика домена (максимум `lastPracticeAtMs` его навыков — «открытие домена» в графе не проецируется) старше `forgetting_domain_idle_days`. Вместо запроса по индексу `(learnerId, retDueAtMs)` читаются все навыки ученика (сотни, `Т-§3.4.5`): R считается в Python. `domain_all` без `INCLUDES` — все не скрытые темы домена. Снятие веса дедлайна после `grade.reported` не реализовано: оценка в графе не хранится.
- Тесты: `test_review_candidates_reasons_scope_and_isolation`.
- Требует подтверждения владельцем: нет.

### D-A-152. WP17.4: `prep_context`
- Класс: B. WP17.4, `Т-§14.7`
- Решение: `ready_count`/`total_count` — темы охвата (`Т-§14.5`: все навыки, все требуемые уровни `proven`/`implied` с базой `direct`; тема без навыков не готова); `days_to_deadline` — округление вверх, не меньше 0; `candidates` — `review_candidates` с `hub_id`. **`control_materials` пусто:** в графе нет вида материала (пробник, прошлый вариант, список вопросов, критерии) — ни в `Т-§3.4.2`, ни в `chunk.indexed`/`material.parsed`; вопрос в PR. Упорядочение «разрыв до требуемых уровней, надёжность источника охвата» не реализовано: источник охвата в `INCLUDES` не хранится.
- Требует подтверждения владельцем: да (вид материала контроля).

### D-A-153. `similar_mistakes`, `past_solution`
- Класс: B. `Т-§10.4`, `Т-§3.4.6`
- Решение: `similar_mistakes` — `SEARCH` по `evidence_emb` (фильтр `learnerId`, `searchable`; запас `vector_search_reserve`), ошибки `outcome <> correct` всех доменов, без самого свидетельства; по тексту нужен `_embedder`, иначе `EmbeddingsUnavailable`. `past_solution` — реплики (`quote`) активных свидетельств по задаче и навыку, последние первыми, не больше `memory_tool_limits.past_solution`: строки канваса лежат в Postgres, не в графе (`Т-§3.4.1`).
- Требует подтверждения владельцем: нет.

### D-A-154. WP8.3: `state_at`
- Класс: B. WP8.3, `Т-§2.6`
- Решение: события ученика `seq <= N` из `core.events` (`read_learner_events`) проигрываются `project_in_memory` — теми же обработчиками, что пересборка; поздние события (оспаривание после N) в проигрывание не входят. Удержание считается на момент последнего события <= N. Нужен `_session_factory`, иначе `RuntimeError`. Шов `_replay` переопределяется в тестах; на текущей базе проигрывание даёт узлы `rules@2` (без `lN*`), полноценно заработает с проектором `rules@3` и свёрткой WP4.
- Требует подтверждения владельцем: нет.

### D-A-155. WP15 п.2: `extraction_candidates`; WP16.2: `misconception_detail`
- Класс: B. D-A-135, D-A-137, `Т-§9.2`, `Т-§9.3`
- Решение: кандидаты — три яруса до `k`: (1) навыки с активным свидетельством этой сессии (`sourceSessionId`) или заблуждением, на которое нацелена сессия; (2) ближайшие по `skill_emb` (если есть вектор); (3) остальные навыки тем яруса 1. `Exposure` не хранит сессию, поэтому показы «темы сессии» не определяют. `topic_id` — только навыки темы (до `k`); `skill_ids` — перечисленные, без обрезки. Заблуждения кандидатов — статусы `suspected`, `confirmed`, `in_work`, `watching` (модель не умеет другие). `misconception_detail`: id через `Alias` (`ALIAS_OF*1..5`); статус `unknown` отдаётся; `dismissed`/`fixed`/`disputed` -> `None` (модель статуса их не содержит; описывать нечего); проявления — активные свидетельства `MANIFESTS` (перенесённые слиянием входят).
- Требует подтверждения владельцем: нет.

### D-A-140. WP6.4: ядро `quack.graph.read` на Neo4j — привязка к ученику, отметка, ожидание
- Класс: A. WP6.4, `Т-§2.6`, `Т-§3.4.2`; `quack/graph/read/neo4j_reader.py`, `tests/graph/test_read_neo4j_core.py`
- Решение:
  1. `Neo4jGraphReader(neo4j, settings, *, learner_id=None, session_factory=None, redis=None, clock=None)`; фабрика `create_graph_reader(neo4j, settings)` возвращает его.
  2. Методы без `learner_id` в сигнатуре (`frontier`, `topic_readiness`, `why`, `topic_requires`, `recall_probability`, `domain_goals`, `topic_memory`) находят владельца узла по его `id` (уникален) и дальше читают только в пределах этого ученика (каждый запрос с `learnerId`, закон 1). `for_learner(id)` даёт читателя, который не видит узлы других учеников.
  3. Отметка — `Learner.projectedSeq` (`Т-§2.6`), запасной вариант `appliedSeq` Фазы 3, пока не слит проектор `rules@3`. Чтение — `read_consistent_slice`: до и после среза, `snapshot_read_retries` повторов, `stable=False` при нестабильности.
  4. `slice_for_turn`: цель ожидания проектора — хвост наложения (`overlay_seq`), без наложения — последний `seq` ученика из Postgres, если читателю дана `session_factory`; иначе без ожидания. Не дождались за `projector_lag_timeout_ms` — `lag_degraded=True`.
- Почему: в `GraphReader` (S4) у семи методов нет `learner_id`; менять сигнатуры после G1 нельзя.
- Требует подтверждения владельцем: нет.

### D-A-141. WP6.4: срез навыков — что читается и как отображается
- Класс: A. `Т-§3.4.4`, `Т-§8.2`–`Т-§8.4`, `Т-§9.2`, `Т-§10.2`
- Решение: в `SkillFact.levels` все шесть уровней (`unknown` → `not_tested`; база `direct`/`transfer`, прочие — `None`); `proven_by_direct` — `proven` с базой `direct`; `last_t1_failed` — последнее по `atMs` действующее свидетельство яруса 1 уровня с исходом `incorrect`; `evidence_count` — действующие свидетельства уровня. Ярус словами: 1 → `code`, 2 → `model`, 3–4 → `conversation`. Заблуждения навыков среза со статусами `suspected`/`confirmed`/`in_work`/`watching`; `confirmed` с `targetedInSessionId`, равным сессии, читается как `in_work`. Корни ошибок — предпосылки, на которые указывают не меньше двух гипотез (`ROOT_CAUSE`). Показов на навык — последние 3, цитат — до 2 (число показов в документах не названо: константа `RECENT_EXPOSURES`). Фокус: `skill_ids` плюс навыки `topic_ids`; `domain_id` не используется.
- Требует подтверждения владельцем: да (число показов).

### D-A-142. WP6.4: готовность хаба, граница, вероятность вспомнить, «почему»
- Класс: A. `Т-§8.2`, `Т-§8.3`, `Т-§8.5`, `Т-§8.7`, `Т-§14.5`, `Т-§4.6`
- Решение: тема готова, если у неё есть навыки и у всех навыков все требуемые уровни `proven` с базой `direct` либо `implied`, следующие (по `level_implications`) из уровня, доказанного с базой `direct` (у `implied` база сама `implied`, `Т-§8.2` п.5). Хаб `domain_all` — все темы домена со статусом `active`/`proposed`, уровни по `control_type_levels`. Граница — наименьший уровень набора не `proven`/`implied`; неизвестный навык — наименьший уровень набора (закон 8). Вероятность вспомнить — `quack.graph.folds.retrievability(retStability, now − retLastRecallAtMs)` (py-fsrs в зависимостях нет); `None` без карточки удержания. `why`: без уровня берётся `Topic.displayLevel`; решающее — по `lNDecisiveEvidenceId` навыков темы (при `displayReason` — навыка), последние `why_recent_n` — действующие свидетельства уровня. `topic_requires` — агрегат `REQUIRES` между навыками разных тем домена, вес — число связей.
- Требует подтверждения владельцем: нет.

### D-A-143. WP6.4: наложение сессии
- Класс: A. `Т-§2.6`, `Т-§5.5`
- Решение: наложение применяется, пока `overlay_seq` больше отметки проектора. Свойства состояния (`l*`, `ret*`, `exp*`, `gap*`, закрепление) навыков и `statement`/`status`/`triggers` заблуждений берутся из `overlay.graph`; свидетельства и показы наложения (связи `ON`/`OF`/`MANIFESTS`, форма `rules@3`), которых нет в Neo4j, добавляются в счётчики, показы и цитаты. Граф наложения в форме Фазы 3 (`EVIDENCE_FOR` и др.) ничего не меняет, пока `overlay.py` не переведён на проектор `rules@3`.
- Требует подтверждения владельцем: нет.


### D-A-144. S4: читатель памяти всегда привязан к ученику
- Класс: B. WP6.4, §4.1, §4.2; `Т-§3.4` (закон `learnerId`); блокер C в #175; `quack/graph/read/reader.py`
- Решение: вариант (а) C — `create_graph_reader(neo4j, settings, *, learner_id)`; читатель без ученика не создаётся, `_anchor` отвергает узел чужого ученика. Обработчик `workers` вызывает его с `job.learner_id`.
- Почему: методы, получающие только id узла (`topic_memory`, `topic_requires`, `topic_readiness`, `recall_probability`, `domain_goals`), брали владельца из узла и отдали бы данные другого ученика. Протокол не меняется, B и C правят только вызов фабрики.
- Тесты: `tests/graph/test_read_neo4j_core.py::test_factory_reader_is_bound_to_its_learner`.
- Требует подтверждения владельцем: нет.
### D-A-182. WP4 и фаза Б: свёртка навыка `rules@3` — выборы там, где `Т-§7`–`Т-§8` молчат
- Класс: B. WP4.2–WP4.5, WP3.3 (фаза Б); `Т-§7.2`–`Т-§7.4`, `Т-§8.1`–`Т-§8.4`, `Т-§3.4.4`; `quack/graph/projection/skill_fold.py`, `phase_b.py`, `view.py`, `store.py`, `runner.py`; зависимость `fsrs==6.3.2` (WP0 п.3в)
- Решение:
  1. **Фаза Б.** Грязные навыки — новые навыки и навыки, у которых пачка добавила, изменила или перенесла свидетельство (`ON`) или показ (`OF`). Их окрестность читается второй читающей транзакцией под той же блокировкой ученика: свидетельства, показы, задачи свидетельств с вариантами и разметкой. Свёртка ставит свойства навыка и вычисленные свойства свидетельств; в Neo4j уходит только изменившееся (разница среза, D-A-181).
  2. **S и F за воротами.** Положительное без пары (Т2), Т3 на уровнях ≤ 3 и Т4 на уровне 1 добавляют вес в S, но ворот не открывают (`Т-§7.2`, «подкрепляет»). Отрицательные Т3, Т4 и Т2 без пары в F не идут (`Т-§7.2`, «статус не меняет», «тоже только парой»). `partial` Т1/Т2 даёт половину в S и половину в F.
  3. **Провал памяти** (`Т-§8.2` п.3): провал с `delayed`, проходящий ворота, на уровне, который до него был `proven`/`implied`. Если после провала памяти на уровне был показ навыка («после показа или напоминания»), следующий провал идёт полным весом. Без показа — снова провал памяти.
  4. **Новизна.** У свидетельства без задачи — `new`. `cross_domain` — первое свидетельство навыка на задаче домена, отличного от домена навыка. `delayed` без прежней практики и показа — `false`.
  5. **FSRS.** `retReviews` считает и первый успех: по `Т-§8.3` он «её первый повтор». Свидетельства одного ответа дают один повтор; провал среди них даёт `Again`.
  6. **Тип пробела.** Голосуют провалы с типом, кроме `none`/`unknown`. Голос — вес свидетельства (у веса 0 голос минимальный). При равенстве голосов побеждает тип, первый по алфавиту.
  7. **Закрепление.** `bypassCount` растёт один раз на ответ. `consolidationLevel` — наибольший уровень разметки задачи.
  8. **Множители выведенных** ×0.5 и ×0.6 (`Т-§7.9`) — значения документа в `FoldParams`: в §6.7 параметра для них нет. Создание выведенных свидетельств в фазе А (WP4.6) — дальше; свёртка уже учитывает `derivedKind`.
- Почему: §0.4.3 п.1–2; закон 2 (код считает), закон 8 (у нового навыка все свойства явные).
- Тесты: `tests/graph/test_skill_fold.py` — по ID ТЗ §8: `test_section_8_2_examples` (T-F1), `test_gates` (T-F2), `test_weights_and_novelty_in_fold` (T-F3), `test_implications_and_direct_over_implied` (T-F4), `test_delayed_fail_on_proven_is_memory` (T-F5), `test_fsrs_card_and_reviews` (T-F6), `test_exposure_and_signals` (T-F8), `test_fold_is_pure` (T-F9); `test_projection_runner.py` — явное состояние нового навыка после пачки. Тесты написаны вместе с кодом: по поручению человека A («минимум тестов, ускоряй») красного прогона и подмен нет.
- Требует подтверждения владельцем: нет.

### D-A-183. WP5.2 и фаза Б: жизненный цикл заблуждения — выборы там, где `Т-§9.2` молчит
- Класс: B. WP5.2; `Т-§9.1`, `Т-§9.2`; `quack/graph/projection/misconception_fold.py`, `phase_b.py`
- Решение:
  1. **Грязные заблуждения** — с новыми или снятыми `MANIFESTS`/`RESISTED`, со своими изменёнными свойствами (`targetedInSessionId`, `disputed`) или с изменённым свидетельством. Их проявления и сопротивления с задачами дочитываются той же второй транзакцией, что навыки (D-A-182).
  2. **Семейства.** Проявление без задачи (гипотеза извлекателя) учитывается в `occurrences`, но в «разных семействах» не участвует. Сопротивления считаются только Т1/Т2 после последнего проявления, по разным `familyId`.
  3. **Возврат.** Новое проявление в `watching`/`fixed` возвращает статус в `confirmed` и обнуляет `watchSinceMs`/`fixedAtMs`. 0 — «не было», и отметка не обманывает «Сегодня». Из `dismissed` статус возвращается в `suspected`. Подтверждение проверяется по всем проявлениям.
  4. **`fixed`** — сопротивление Т1/Т2 не раньше чем через `watch_days` после перехода в `watching`. **`in_work`** — `confirmed` при непустом `targetedInSessionId`. **`disputed`** ставит фаза А (`misconception.disputed`); свёртка его не снимает.
- Почему: §0.4.3 п.1–2; время только из событий (`Т-§9.2`).
- Тесты: `tests/graph/test_skill_fold.py::test_misconception_lifecycle_table` (T-MS1) (написан вместе с кодом, без красного прогона — поручение человека A).
- Требует подтверждения владельцем: нет.

### D-A-184. WP6.1–6.2 и фаза В: темы — статус, видимость, срок повторения, код состояния
- Класс: B. WP6.1, WP6.2, WP3.3 (фаза В); `Т-§4.7`, `Т-§8.5`, `Т-§8.6`, `Т-§22.2`; `quack/graph/projection/topic_fold.py`, `phase_b.py`, `store.py`, `view.py`; модель `TopicDisplay` в `quack/graph/read/models.py`
- Решение:
  1. **Грязные темы:**
     - новые и изменённые темы;
     - темы с изменёнными `INCLUDES`/`COVERS`;
     - темы свёрнутых навыков и навыков изменённых заблуждений (`ABOUT`);
     - темы изменённых хабов; у хаба `domain_all` — все темы домена.
     Вход читается третьей читающей транзакцией: навыки тем, их подтверждённые заблуждения с `CONFUSED_WITH`, хабы с `INCLUDES` и хаб всего домена.
  2. **Ближайший дедлайн.** У проектора нет текущего времени (`Т-§2.5`), поэтому берётся наименьший `deadlineAtMs > 0` среди хабов темы и хаба домена. Прошедший дедлайн действует, пока ученик не поставит новый. Так результат не зависит от размера пачки (T-P1). Пустой `requiredLevels` на `INCLUDES` и хаб `domain_all` берут уровни из `control_type_levels[controlType]` (WP6.3). Без дедлайна — `no_goal` (3).
  3. **Правила.**
     - «Подтверждённое» заблуждение (правила 1–2) — `confirmed` и `in_work`; `watching` уже исправляется.
     - Правило 5 — все целевые уровни `proven`/`implied` с базой `direct`/`implied`. Правило 6 — часть с базой `transfer`/`indirect`. `displayDomainId` пуст, пока нет выведенных свидетельств (WP4.6).
     - Правило 7 — доказан хотя бы один целевой уровень, иначе правило 8.
     - `displayReason` — заблуждение (правила 1–2) или навык (3, 4, 6).
  4. **Статус.** `proposed` → `active`, когда у навыка темы есть свидетельство (`lastPracticeAtMs > 0`) или показ (`expCount > 0`). Состояние получают только `active`, остальные — `none`. Видимость — `Т-§4.7`; предложенная с `knownClaimedAtMs > 0` скрыта (`Т-§4.11`). `nearestDueAtMs` — наименьший `retDueAtMs > 0`.
  5. **Публикация** изменённых тем в Redis после фиксации — с процессом проектора (WP1.3): у `project_learner` пока нет Redis.
- Почему: §0.4.3 п.1–2; закон 8.
- Тесты: `tests/graph/test_skill_fold.py` — `test_topic_status_visibility` (T-TH1), `test_display_state_priority` (T-TH2), `test_hub_required_levels_default` (T-TH3); `test_projection_runner.py` — активная тема без проверок: `not_tested`, `shown`. Написаны вместе с кодом, без красного прогона (поручение человека A).
- Требует подтверждения владельцем: нет.

### D-A-185. WP4.6: выведенные свидетельства в фазе А
- Класс: B. WP4.6; `Т-§7.9`; `quack/graph/projection/handlers.py` (`_derived`), `view.py`, `store.py`
- Решение:
  1. **Создание.** При положительном вердикте каждое прямое свидетельство выводит:
     - `indirect` по `REQUIRES` (из навыка, `confidence ≥ requires_min_confidence`);
     - `transfer` по `SAME_IDEA` в обе стороны (`confidence ≥ same_idea_min_confidence`).
     Уровень — `min(3, уровень источника)`. Остальные свойства копируются с источника (ярус, исход, помощь, время, `sourceSeq`); у выведенного пустая цитата и `searchable = false`. Есть `ON` цели, `ON_TASK` задачи источника, `DERIVED_FROM` источника. Множитель веса ×0.5 / ×0.6 считает свёртка (D-A-182).
  2. **Одно на ответ.** Источники уровней 3 и 4 одного ответа дают одно выведенное уровня 3 на цель, не два: иначе один ответ удвоил бы вес.
  3. **Срез.** Окрестность задачи теперь открывает и навыки её разметки (`TESTS`) с их связями — по образцу вариантов и свидетельств (D-A-134 п.6). Эти навыки всё равно грязные в фазе Б.
  4. **Снятая связь** (`edge.removed`) в фазе А переводит выведенные по ней в `superseded` (ТЗ WP4.6, [РЕШЕНИЕ Ф4]): `indirect` из свидетельств навыка-источника в цель по `REQUIRES`, `transfer` в обе стороны по `SAME_IDEA`. Id выведенного — от источника, вида и цели (`derived:{source}:{kind}:{target}`), поэтому его находит обработчик снятия по окрестностям двух навыков. Фаза А, а не Б: снятие и повторное добавление в разных пачках дают тот же итог, что в одной (T-P1). Выведенные создаются только из вердиктов: элементы извлекателя — Т3, ворота не проходят.
- Почему: §0.4.3 п.1–2; «на один шаг» и детерминизм пересборки (`Т-§2.5`).
- Тесты: `tests/graph/test_projection_rules3.py::test_derived_evidence` (T-F7).
- Требует подтверждения владельцем: нет.

### D-A-186. WP3.4, T-P1: пачки любого размера = применение целиком (фазы А–В)
- Класс: B. WP3.3, WP3.4; `Т-§2.4`, `Т-§2.5`; `quack/graph/projection/phase_b.py` (`run_folds`, `FoldLoader`, `WholeSlice`), `runner.py`, `view.py`, `skill_fold.py`
- Решение:
  1. **Одна функция фаз Б–В** `run_folds(batch, settings, loader)`. Загрузчик — откуда дочитывать: Neo4j в цикле пачки или ничего, когда срез и так весь граф (тест, пересборка в памяти). Ошибка свёртки — ошибка программы, как у обработчика: `EventFailed` на последнем событии пачки, `ops.projector_failures`, поток стоит (`Т-§2.4`).
  2. **`asOfSeq`** — наибольший `sourceSeq` входа свёртки (свидетельства, показы), 0 — входа нет. Номер последнего события пачки зависел бы от деления на пачки и ломал бы T-P1.
  3. **Темы хабов** находятся и по срезу (`INCLUDES`, темы домена у `domain_all`), не только запросом к Neo4j.
- Почему: §0.4.3 п.1–2; закон 7 (пачка целиком или никак).
- Тесты: `tests/graph/test_projection_batches.py` — `test_no_missing_properties` (T-P2: все свойства `Т-§3.4.2`/`Т-§3.4.4` у каждого узла сценария, без `null`), `test_batches_equal_rebuild_hypothesis` (T-P1: Hypothesis, 40 делений сценария из 20 событий на пачки; фазы А–В в памяти), `test_applying_a_batch_again_changes_nothing`, `test_script_reaches_every_phase`. Подмена «темы не пересчитываются от изменённых навыков» — тест падает. Сценарий фиксированный, а не случайный по каталогу: случайный — дальше. Чтения Neo4j проверяет `test_projection_runner.py::test_batches_of_any_size_give_one_graph`.
- Требует подтверждения владельцем: нет.

### D-A-187. WP1.4, S16: `.squawk.toml` и T-DB7
- Класс: B. WP1.4; `Д-§5.2` п.2, `Д-§6.2`; `backend/.squawk.toml`, `tests/db/test_squawk_config.py`
- Решение:
  1. **Конфиг** — только два ключа документа: `pg_version = "16.0"`, `assume_in_transaction = true`. squawk 2.71.0 читает `pg_version` строкой: целое `16` — ошибка конфигурации. Смысл «16» Д-§5.2 сохранён.
  2. **T-DB7** — статическая проверка: SQL с `CONCURRENTLY` (без комментариев) в части `up` требует `-- migrate:up transaction:false` и `-- squawk-disable-assume-in-transaction`. Проверка сама падает на файле без маркера и на файле с одним маркером из двух.
  3. **Для C (WP9.3, шаг squawk в CI).** На двух миграциях `core.pairing_tokens` squawk 2.71.0 с этим конфигом выдаёт предупреждения `require-lock-timeout` и `require-statement-timeout`. Д-§5.2 требует ловить блокирующие `ALTER` и индексы без `CONCURRENTLY`, про тайм-ауты молчит. Исключать ли эти правила в конфиге, решает шаг CI — вопрос к C, конфиг их не исключает.
- Почему: §0.4.3 п.1–2; S16 к G2.
- Тесты: `tests/db/test_squawk_config.py::test_squawk_config_and_concurrent_marker` (T-DB7).
- Требует подтверждения владельцем: нет.


### D-A-145. S4: `ExtraReads` в `Neo4jGraphReader`, привязка к ученику у всех чтений
- Класс: B. WP6.4; `Т-§3.4`; D-A-144; `quack/graph/read/neo4j_reader.py`, `neo4j_extras.py`
- Решение: `Neo4jGraphReader(ExtraReads)`; закрытые помощники `ExtraReads` переименованы (`_xrun`, `_xstable`, `_xevidence_rows`, `_xowner`) — прежние имена совпадали с ядром и расходились по смыслу. Читатель, привязанный к ученику, на чужой `learner_id` или чужой узел по id отвечает пустым (`review_candidates`, `similar_mistakes`, `past_solution`, `state_at`, `extraction_candidates`, `skill_history`, `prerequisites`, `prep_context`, `misconception_detail`). `profile` — после PR #176 (`read_profile`).
- Тесты: `tests/graph/test_read_neo4j_core.py::test_factory_reader_does_not_leak_through_extra_reads` (до привязки `misconception_detail` читал чужое заблуждение).
- Требует подтверждения владельцем: нет.
### D-A-188. WP5.3 в фазе Б: профиль `learned*` подключён (после #176 в ветке)
- Класс: B. WP5.3, WP3.3; `Т-§9.5`; `quack/graph/projection/phase_b.py` (`fold_profile`), `skill_fold.py` (`gate_flags`), `store.py`, `runner.py`, `mutations.py` (`RemoveProps`), `quack/graph/profile_fold.py`
- Решение:
  1. **Когда.** Профиль грязный, если в пачке есть событие из `PROFILE_EVENT_TYPES` или пачка изменила свидетельство.
  2. **Вход.**
     - Журнал этих типов ученика — из `core.events`.
     - Все прямые действующие свидетельства ученика с навыком и задачей — из Neo4j.
     - Флаг ворот (`passes_gate`) считает `gate_flags` по правилам свёртки навыка: ярус, исход, обход, новизна для уровней 4–6, правило пары. Новизна берётся та, что уже записала свёртка.
  3. **Запись.** Узла `Profile` нет — создаётся с `HAS_PROFILE`. Есть — свойства ставятся, а «нет данных» удаляет `learned*`: мутация `RemoveProps`, в разнице `remove_props`, в Neo4j `SET n += {k: null}`. Отсутствие, а не ноль (закон 8).
  4. **Типы.** `profile_fold` читает событие через протокол `JournalEvent` (`seq`, `type`, `schema_version`, `payload`), годятся и события Фазы 3 (тесты #176), и `rules@3`.
  5. **Ветка.** #185 влил `phase-4-a-wp53-profile` (#176, в нём и #179): одна ветка вместо трёх по поручению человека A («минимум PR»).
- Почему: §0.4.3 п.1–2; WP3.3 («Б — свёртки … заблуждений и профиля»).
- Тесты: `tests/graph/test_projection_batches.py::test_profile_learned` (T-MS3); T-P1 проходит с профилем; свёртка профиля — `tests/graph/test_profile_fold.py` (#176).
- Требует подтверждения владельцем: нет.

### D-A-189. WP3.5: пересборка ученика на `rules@3` и `--stale-rules`
- Класс: B. WP3.5; `Т-§2.7`, `Д-§6.3`; `quack/graph/projection/rebuild.py`, `store.py` (`replace_learner`, `stale_learners`)
- Решение:
  1. **Пересборка** идёт под той же advisory-блокировкой ученика. Все события ученика проходят фазы А–В в памяти пачками по `projector_batch_max_events`: те же обработчики и `run_folds`, что в цикле пачки. По T-P1 результат не зависит от деления на пачки.
  2. **Замена подграфа.** Подграф Neo4j заменяется одной транзакцией: по каждой метке `DETACH DELETE` узлов ученика, затем запись всего графа и отметки `rules@3`. Копия отметки в `ops.projector_offsets` ставится точной (`= seq`, не `GREATEST`).
  3. **Большой ученик.** Если операций больше `rebuild_max_tx_ops` и в Redis есть присутствие (`presence_key`), ответ — `active_session`, ученик откладывается. Без сессии граф пишется частями не больше лимита, отметка последней.
  4. **Ошибка** события или свёртки — `failed` с номером события; граф не трогается.
  5. **CLI.** `ops rebuild` пока вызывает пересборку Фазы 3. Переключение процессов `projector`/`ops` на `rules@3` — со сборкой процессов на `Db` (WP1.3, вместе с `DispatcherP4`/`JobWorker` из #182). Функции готовы: `rebuild_learner`, `stale_learners`.
- Почему: §0.4.3 п.1–2; закон 1 (граф — проекция, пересобирается из журнала).
- Тесты: `tests/graph/test_projection_runner.py::test_rules3_stale_rebuild` (T-P8: `rules@2` находится, после пересборки `rules@3` и тот же граф, лишний узел исчез). `replace_learner` добавлен в `NEO4J_WRITERS` (T-A9a).
- Требует подтверждения владельцем: нет.

### D-A-190. WP3.3: публикация изменённых тем после фиксации
- Класс: B. WP3.3; `Т-§2.4`, ТЗ §4.8; `quack/graph/projection/runner.py`
- Решение: после записи пачки и копии отметки `project_learner(..., redis=)` публикует в `projector:applied:{learner_id}` (`projector_applied_channel` — канал, который уже читает `GET /v1/learner/stream`) объект `{"applied_seq": N, "topics": [id, …]}`. В `topics` — темы, свойства которых пачка изменила (разница среза). Сбой публикации только пишется в лог (закон 9). Без Redis ничего не публикуется: поток опрашивает отметку.
- Почему: §0.4.3 п.1; §4.8 — `projection_advanced` «с `topics`»; канал Фазы 3 тот же, ключ `applied_seq` прежний.
- Тесты: `tests/graph/test_projection_runner.py::test_changed_topics_are_published_after_commit`.
- Требует подтверждения владельцем: нет.

### Карта тестов G2 потока A (ТЗ §8 → тест), 2026-11-03
| ID | Тест | Где |
| :-- | :-- | :-- |
| T-P1 | `test_batches_equal_rebuild_hypothesis` | `tests/graph/test_projection_batches.py` (+ `test_projection_runner.py::test_batches_of_any_size_give_one_graph` на Neo4j) |
| T-P2 | `test_no_missing_properties` | `tests/graph/test_projection_batches.py` |
| T-P3 | `test_handler_outside_slice_is_bug` | `tests/graph/test_projection_rules3.py` |
| T-P4 | `test_handler_table` + группы 1–6 модуля | `tests/graph/test_projection_rules3.py` |
| T-P5 | `test_alias_and_empty_apply` | `tests/graph/test_projection_rules3.py` |
| T-P6 | `test_unknown_type_stops_stream` | `tests/graph/test_projection_runner.py` |
| T-P7 | `test_schema_created_on_start` | `tests/graph/test_schema_wp31.py` |
| T-P8 | `test_rules3_stale_rebuild` | `tests/graph/test_projection_runner.py` |
| T-F1 | `test_section_8_2_examples` | `tests/graph/test_skill_fold.py` |
| T-F2–F6, F8, F9 | `test_gates`, `test_weights_and_novelty_in_fold`, `test_implications_and_direct_over_implied`, `test_delayed_fail_on_proven_is_memory`, `test_fsrs_card_and_reviews`, `test_exposure_and_signals`, `test_fold_is_pure` | `tests/graph/test_skill_fold.py` |
| T-F7 | `test_derived_evidence` | `tests/graph/test_projection_rules3.py` |
| T-MS1 | `test_misconception_lifecycle_table` | `tests/graph/test_skill_fold.py` |
| T-MS2 | `test_resist_and_manifest_sources` | `tests/graph/test_projection_rules3.py` |
| T-MS3 | `test_profile_learned` (+ `tests/graph/test_profile_fold.py`) | `tests/graph/test_projection_batches.py` |
| T-TH1–TH3 | `test_topic_status_visibility`, `test_display_state_priority`, `test_hub_required_levels_default` | `tests/graph/test_skill_fold.py` |
| T-TH4 | `test_frontier`, `test_topic_readiness_direct_basis_only`, `test_why`, `test_topic_requires_is_derived_from_skill_links` | `tests/graph/test_read_neo4j_core.py` (четыре чтения — четыре теста, один ID) |
| T-DB2, T-DB4 | `test_no_fstring_sql`, `test_repository_functions_take_learner_id` | `tests/db/test_sql_static.py` |
| T-DB7 | `test_squawk_config_and_concurrent_marker` | `tests/db/test_squawk_config.py` |
| T-D1–D10 | `test_rule_table`, `test_episode_boundaries`, `test_episode_without_content`, `test_coalesce_greatest`, `test_max_jobs_per_learner`, `test_cancel_requested`, `test_skipped_budget`, `test_failed_writes_agent_completed`, `test_periodic_keys` (WP7.5, D-A-166), `test_visible_and_plaque` | `tests/jobs/*` в #182 |

T-DB5 — тесты `append_events` Фазы 1 на psycopg: `tests/db/test_append_events.py` (номера без пропусков, идемпотентность, конфликт хэша, `event_types_seen`, транзакция вызывающего, сигнал после фиксации). Нет к G2 у A: T-DB3 (снятие SQLAlchemy — WP1.3, G3/G4), T-TH5 (API — WP6.5, вне G2). T-DB6 — поиск WP20.5 (C).

### D-A-191. WP8.3: `state_at` проигрывает журнал Фазы 4 обработчиками и свёртками `rules@3`
- Класс: B. WP8.3; `Т-§2.6`; `quack/graph/read/neo4j_extras.py`, `neo4j_reader.py`, `reader.py`
- Решение:
  1. **Путь `rules@3`.** У читателя появился необязательный `db: Db` (`Neo4jGraphReader(..., db=)`, `create_graph_reader(..., db=ctx.db)`). С ним `state_at` берёт события ученика с `seq ≤ N` из `core.events` Фазы 4 и проигрывает их `project_all` — тем же кодом, что пересборка (D-A-189), фазы А–В в памяти. Свёртка по `seq ≤ N` сохранённых свидетельств не используется: поздние оспаривания не применяются задним числом.
  2. **Форма ответа.** Граф `rules@3` переводится в форму, которую уже читает `state_at` (`InMemoryGraph`); ответ `StateAt` прежний.
  3. **Без `db`** работает прежний путь Фазы 3 (`session_factory`, проектор `rules@2`) — до WP1.3, когда журнал Фазы 3 уходит. Аргумент необязательный, сигнатура S4 обратно совместима.
  4. **Импорт** `quack.graph.projection.rebuild` — внутри функции: модуль проекции читает `quack.graph.read.models`, цикла на уровне модулей нет (T-A6).
- Почему: §0.4.3 п.1–2; ТЗ WP8.3 «теми же обработчиками и свёртками».
- Тесты: `tests/graph/test_projection_runner.py::test_state_at_replays_the_log_with_rules3` (до вердикта уровень 3 не проверен, после — доказан Т1 кода); прежние тесты `state_at` зелёные.
- Требует подтверждения владельцем: нет.

### D-A-192. WP8.1: `POST /v1/disputes` и `POST /v1/verdicts/{answer_seq}/typo`
- Класс: B. WP8.1; `Т-§2.8`, `Т-§7.6`, ТЗ §6.4 («Граф и темы»); `quack/domains/disputes.py`, `quack/domains/router.py`
- Решение:
  1. **Проверка цели — до события** (закон 1). Свидетельство — узел `Evidence` с `learnerId` ученика. Заблуждение — узел `Misconception` или его `Alias`. Вердикт — ключ `"<answer_seq>:<part>"` (D-A-134), у ученика в журнале есть `answer.submitted` с этим `seq`. Чужое или несуществующее — `404 NOT_FOUND`.
  2. **Событие.** `dispute.filed {target_kind, target_id}` (ключ `dispute:{idempotency_key}` из запроса) или `typo.declared {answer_seq, part}`. У описки ключ — по вердикту (`typo:{answer_seq}:{part}`): в запросе ТЗ ключа нет, повтор даёт то же событие (закон 5). `actor = learner`.
  3. **Ответ** — `202 {seq}`, номер записанного события. Тело ответа ТЗ не задаёт; изменения клиент видит по каналу `learner/stream` и обычными GET (§4.8).
  4. **Контракт маршрутов.** Оба маршрута добавлены в `EXPECTED_ROUTES` (T-A1). Снимок `backend/openapi.json` пересобран командой `ops openapi`.
- Почему: §0.4.3 п.1–2.
- Тесты: `tests/graph/test_projection_runner.py::test_disputes_check_the_target_and_append_one_event`; `tests/test_app_assembly.py` (маршруты, авторизация до валидации, снимок OpenAPI).
- Требует подтверждения владельцем: нет.

### D-A-193. WP8.2: правки тем и «я это уже знаю» — пять маршрутов
- Класс: B. WP8.2; `Т-§4.11`, ТЗ §6.4; `quack/domains/topic_edits.py`, `quack/domains/router.py`
- Решение:
  1. **Маршруты:** `PATCH /v1/topics/{id} {name}` → `topic.renamed`; `POST /v1/topics/{id}/merge {into}` → `topic.merged`; `POST /v1/topics/{id}/hide` → `topic.hidden`; `POST /v1/topics/{id}/known` → `known.claimed`; `POST /v1/episodes/{episode_seq}/reassign {topic_id}` → `episode.reassigned`. Ответ — `202 {seq}`, как у D-A-192.
  2. **Проверка цели** (закон 1). Тема — узел `Topic` ученика. Объединение — обе темы ученика, разные и одного домена. Эпизод — событие `episode.closed` ученика с этим `seq`. Иначе — `404 NOT_FOUND`.
  3. **Ключ идемпотентности.** В запросах §6.4 его нет, поэтому ключ — сама правка с исходным состоянием (закон 5): переименование — `{тема}:{старое имя}->{новое}`, «знаю» — `{тема}:{knownClaimedAtMs}`, остальные — по своим id. Повтор запроса даёт то же событие, следующая правка того же вида — новое.
  4. **`PATCH /v1/hubs/{id} {topics?, deadline_at_ms?}`** (WP6.3, «Хабы» §6.4): весь новый охват → `hub.topics_changed`; дата → `hub.deadline_set {source: learner}`, `null` снимает дедлайн. Нужно хотя бы одно поле. Темы охвата — ученика и домена хаба, иначе `404`. Ключ — от текущего и нового состояния (дайджест охвата, прежняя и новая дата). Разделение темы делается фразой в сессии (B). `topic.moved` — ПОЗЖЕ.
- Тесты (хаб): `tests/graph/test_projection_runner.py::test_hub_edit_scope_and_deadline`.
- Почему: §0.4.3 п.1–2.
- Тесты: `tests/graph/test_projection_runner.py::test_topic_edits_check_the_topic_and_append_one_event`; `tests/test_app_assembly.py` (T-A1, снимок OpenAPI пересобран).
- Требует подтверждения владельцем: нет.

### D-A-194. T-E2/T-E3: маршруты WP6.3 и WP8 без `idempotency_key` — исключения с причиной
- Класс: B. WP6.3, WP8.1, WP8.2; ТЗ §6.4 («Граф и темы», «Хабы»), строка про `idempotency_key` и `T-E2`; `tests/e2e/endpoints.py`, `tests/e2e/test_isolation_and_idempotency.py`
- Контекст: CI #185 красный на `T-E2` («не пройдены» новые изменяющие маршруты) и `T-E3` (`KeyError: 'hub_id'`). §6.4 требует `idempotency_key` у изменяющих маршрутов, «кроме перечисленных в `T-E2`». Таблицы WP8.1/WP8.2 и WP6.3 ключа в запросе не задают; из семи маршрутов без ключа модели запросов запрещают лишние поля (`extra="forbid"`).
- Решение:
  1. **`POST /v1/disputes`** несёт ключ по ТЗ (WP8.1) и входит в обход `T-E2` (`IDEMPOTENT_WALK`) после правки строки на холсте: цель — вердикт `"<answer_seq>:<part>"` с `answer.submitted` в журнале Фазы 4; тело «иное» (`part`) даёт `409 IDEMPOTENCY_CONFLICT`.
  2. **Семь маршрутов без ключа** — `POST /v1/verdicts/{answer_seq}/typo`, `PATCH /v1/topics/{id}`, `POST /v1/topics/{id}/merge`, `…/hide`, `…/known`, `POST /v1/episodes/{episode_seq}/reassign`, `PATCH /v1/hubs/{id}` — внесены в `NO_IDEMPOTENCY_KEY` с причиной «ТЗ §6.4: ключа нет; ключ события — сама правка с исходным состоянием» (D-A-192 п.2, D-A-193 п.3, закон 5). Ключ в запрос не добавлялся: контракт ТЗ не меняем.
  3. **`T-E3`:** в `Ids` и `path_for` добавлены `hub_id`, `answer_seq`, `episode_seq`; перед обходом у ученика A заводятся узел `Hub`, узел `Topic` (если его нет), `answer.submitted` и `episode.closed` в журнале Фазы 4. Ученик B на все семь маршрутов и на хаб получает `404`, события A не растут.
- Почему: закон 5 (повтор правки даёт то же событие) уже обеспечен ключом события и проверяется `test_projection_runner`; ключ запроса ТЗ для этих маршрутов не вводит. Ближайшее правило ТЗ — перечислить исключения в `T-E2` с причиной.
- Обратимость: если владелец решит ввести ключ запроса, маршрут уходит из `NO_IDEMPOTENCY_KEY` в `IDEMPOTENT_WALK`, в модель добавляется поле.
- Тесты: `tests/e2e/test_isolation_and_idempotency.py::test_mutating_endpoints_are_idempotent`, `::test_cross_learner_isolation`.

### D-A-165. WP7.4: сигнал плашки `jobs_changed` (T-D10) — по ответу C в #183 (D-C-141)
- Класс: B. WP7.4; `Т-§13.8`, `Т-§1.6`; `quack/jobs/queue_p4.py`
- Решение: каждая смена статуса задачи с `visible` и непустым `session_id` после коммита публикует в `learner:{id}` объект `{"kind": "jobs_changed", "session_id"}` (orjson). Переходы: вставка (`queued`), выбор (`running`), `skipped_budget`, завершение (`succeeded`/`failed`/`cancelled`), возврат в очередь, вытеснение, отмена. При отмене — один сигнал на сессию. Публикация идёт через `Db.on_commit`, сбой пишется в лог (закон 9). Без Redis сигнала нет. Функции очереди принимают `redis=` (по умолчанию `None`); исполнитель и диспетчер передают свой.
- Почему: §0.4.3 п.1; формат канала решён C (D-C-141), потребитель — `learner_stream.py` (C).
- Тесты: `tests/jobs/test_queue_p4.py::test_visible_and_plaque` (не до коммита, невидимая и без сессии молчат, по одной публикации на смену).
- Требует подтверждения владельцем: нет.

### D-A-166. WP7.5: периодические задачи Фазы 4 — ключ периода
- Класс: B. WP7.5; `Т-§13.1`; `quack/jobs/periodic_p4.py`, `rules_p4.JobPlan`
- Решение:
  1. **Ключи.** `periodic_plans(now)` даёт по строке таблицы WP7.5 с `job_key`, названным периодом в UTC: `deletions-reconcile:{дата}`, `manifests-cleanup:{дата}`, `exports-cleanup:{дата}`, `calibration:{ISO-год}-{неделя}`. Класс P3, `lock_key` = вид, ученика нет, в нагрузке — `period`.
  2. **Один запуск на период.** `schedule_periodic` кладёт их через `queue_p4.enqueue`, а он отбрасывает повтор `job_key`. Сколько бы тиков ни было у процессов `workers`, на период ставится одна задача.
  3. **Тип.** `JobPlan.learner_id` стал `UUID | None`: у периодической задачи нет ученика (`ops.jobs.learner_id` допускает NULL).
  4. **Не сделано.** `session.sweep_inactive` остаётся у планировщика Фазы 3 до WP1.3. `dispatcher.episode_idle` живёт внутри диспетчера. Обработчиков четырёх видов ещё нет, они ждут в очереди: удаление (WP25), манифесты, калибровка — A; экспорт — по владельцу WP. Тик `workers` вызывает `schedule_periodic` со сборкой процесса (WP1.3).
- Почему: §0.4.3 п.1–2.
- Тесты: `tests/jobs/test_queue_p4.py::test_periodic_keys` (T-D9).
- Требует подтверждения владельцем: нет.

### D-A-167. WP7.2/7.4: сбой одного ученика в проверке простоя и сбой пульса не останавливают остальное (ревью #182)
- Класс: B. WP7.2, WP7.4; `Т-§13.9` (закон 7); `quack/jobs/dispatcher_p4.py`, `quack/jobs/executor_p4.py`
- Контекст: `close_idle_episodes` обходил учеников подряд, и ошибка на одном обрывала проверку для всех, кто идёт после него; ошибка БД на пульсе выходила из `run_claimed`, оставляя обработчик работать без присмотра (отмена не доходит, задачу позже заберёт чистильщик и она пойдёт второй раз).
- Решение: ошибка проверки простоя одного ученика пишется в лог (`dispatcher_idle_learner_failed`), обход идёт дальше. Ошибка пульса пишется в лог (`job_heartbeat_failed`), исполнитель ждёт следующего пульса; если БД не вернулась, задачу вернёт чистильщик, а обработчик остановит ответ `None` пульса.
- Почему: закон 7 — сбой фонового не трогает остальное; `Т-§13.1` — пульс и чистильщик решают судьбу задачи.
- Обратимость: убрать два `try/except`.
- Тесты: `tests/jobs/test_dispatcher_p4.py::test_idle_check_survives_a_failing_learner`, `tests/jobs/test_executor_p4.py::test_a_failed_pulse_does_not_orphan_the_handler`.
### D-A-198. WP6.5: чтения тем и хабов; замена `/graph` и `/topics/{id}` — в точке перехода WP1.3
- Класс: B. WP6.5, WP6.3; ТЗ §6.4 («Граф и темы», «Хабы»), `Т-§4.7`, `Т-§4.8`, `Т-§8.7`, `Т-§22.2`; `quack/domains/graph_reads.py`, `router.py`
- Решение:
  1. **Смонтированы маршруты, которых нет в Фазе 3:**
     - `GET /v1/topics/{id}/why?level=` — `GraphReader.why`, решающее и последние свидетельства (`Т-§8.7`);
     - `GET /v1/domains/{id}/hubs` — `domain_goals`;
     - `POST /v1/domains/{id}/hubs {name, control_type, idempotency_key}` — `hub.created` вида `request`, `origin = learner`; id хаба — от ключа, повтор даёт тот же хаб (закон 5); ответ `201 {hub_id, seq}`;
     - `GET /v1/hubs/{id}` — цель хаба и готовность только по базе `direct`.
     Чужой id — `404`.
  2. **`topic_view`** готов: код состояния `TopicDisplay`, статус, видимость, `nearestDueAtMs`, уровни навыков, «места в сессиях» (`Т-§4.7`, `П-3.3`). Места — показы и действующие свидетельства навыков темы: номер события из графа, сессия — из `core.events`, потому что у `Exposure` по `Т-§3.4.2` нет свойства сессии.
  3. **Точка перехода.** `GET /v1/domains/{id}/graph` и `GET /v1/topics/{id}` есть в Фазе 3; на них держатся тесты старого стека (e2e, сбой Neo4j, псевдоним темы). Две версии одного пути не живут, поэтому замена — при переключении процессов на `Db` (WP1.3), вместе с переходом C (WP9). T-TH5 (`test_graph_api_codes_not_strings`) — там же.
  4. **`POST /v1/hubs/{id}/prep-mode`, `/grade`** — WP17.
- Почему: §0.4.3 п.1–2; §0.4.4 п.14 (тесты чужого стека не ломать до перехода).
- Тесты: `tests/graph/test_projection_runner.py::test_topic_and_hub_reads`; `tests/test_app_assembly.py` (T-A1, снимок OpenAPI пересобран).
- Требует подтверждения владельцем: нет.

### D-A-195. WP17: «Сегодня», режим подготовки, оценка после дедлайна
- Класс: B. WP17.2, WP17.4, WP17.5; `Т-§14.4`, `Т-§14.7`, `Т-§14.8`, `П-3.2`; `quack/review/today.py`, маршруты в `quack/domains/router.py`
- Решение:
  1. **`GET /v1/domains/{id}/today`.**
     - Кандидаты ядра (`review_candidates`, WP17.1, D-A-150…155) — только навыки этого домена (`П-3.2`; в `ReviewScope` домена нет, отбор по `Skill.domainId`).
     - Не больше `today_max_items`, приоритет строго выше `today_min_score` (стартово 0.0: «подбирается на первых данных»).
     - Причины переводятся в коды пунктов `TodayItem`: `retention`→`due`, `consolidation`, `misconception_recheck`→`misconception`, `hub_requirement`→`deadline`. Оценка времени — `review_item_minutes` × пунктов.
     - Пустой список — плашки нет, события нет. Непустой — `today.shown` с ключом `today:{домен}:{дата ученика}:{хэш пунктов}`; дата — по `core.learners.settings.timezone`, иначе UTC.
  2. **Оценка после дедлайна** (WP17.5): `grade_hub_ids` — хабы домена с датой раньше сегодняшнего дня ученика и без `grade.reported`. «Один раз» понимается как «пока оценка не введена»: события «предложение показано» в каталоге нет.
  3. **`POST /v1/hubs/{id}/prep-mode {enabled}`** → `prep_mode.enabled/disabled`. Запрос того режима, что уже стоит, возвращает его событие без нового (закон 5).
  4. **`POST /v1/hubs/{id}/grade {grade, scale}`** → `grade.reported`, ключ — хаб и оценка. Хаб остаётся.
  5. **Место маршрутов.** Пакет `quack.review` — продуктовый (ТЗ §4.1), импорт между продуктовыми пакетами запрещён. Поэтому у него свой роутер `quack/review/router.py`, подключённый в сборке `quack.api.app` (T-A6). `review` и `deletion` добавлены в слой «Продукт» теста слоёв.
  6. **Не сделано:** предупреждения о темпе (WP17.3, `Т-§14.6`) — нужны дни активности ученика; прогноз и запас `pace_warning_margin` — следующим шагом.
- Почему: §0.4.3 п.1–2.
- Тесты: `tests/graph/test_projection_runner.py::test_prep_mode_and_grade`; маршруты в реестре `tests/e2e/routes/a.py` (D-C-147); снимок OpenAPI пересобран.
- Требует подтверждения владельцем: нет.

### D-A-196. S3 после G1: `AgentParamsRecord.hub_id` и `.artifact` — правка v1 (по #191 C)
- Класс: C (поле нагрузки после G1). WP23.2, `Т-§17.7`, ТЗ §6.4; `quack/events/records.py`
- Контекст: `POST /v1/artifacts/requests` и согласие на cheat sheet хаба C пишет как `agent.requested {kind: artifact}` (D-C-138, #154). Обработчику `agent.artifact` нужно знать, какой артефакт строить и для какого хаба. В записи параметров таких полей нет, а класть их в `description` — слова вместо данных (закон 2).
- Решение: в `AgentParamsRecord` два необязательных поля — `hub_id: UUID | None` и `artifact: Literal["notes", "cards", "cheatsheet"] | None` (перечень как у `ArtifactUpdated.kind`). Версия схемы `agent.requested` остаётся 1, как в D-A-121: событий Фазы 4 нигде не развёрнуто, а старые нагрузки без этих полей читаются как раньше.
- Почему: §0.4.3 п.1–2; закон 2; решение владельца поручено A («сверься с доками и реши сам»).
- Согласие: C — автор просьбы (#191); B — уведомлён в #191: поля необязательные, обработчиков и схем B они не меняют.
- Тесты: `tests/events/test_catalog.py::test_agent_artifact_request_names_the_artifact_and_its_hub`.
- Требует подтверждения владельцем: решение принято по поручению человека A; владелец — уведомлён этой записью.

### D-A-197. WP25: физическое удаление домена — `DELETE /v1/domains/{id}`
- Класс: B. WP25 п.1–4; `Т-§19.5`, `Т-§20.7`, `Д-§8.5`, `Д-§8.6`; `quack/deletion/domain.py`, `quack/deletion/router.py`
- Решение:
  1. **Порядок по WP25.**
     - Цель ученика проверяется по узлу `Domain` до любой записи: чужой — `404`, и журнал не трогается.
     - Затем `DeletionRecord` в журнал R2 (`DeletionJournal`, S9). Не записалось — `DeletionJournalUnavailable` → `503 SERVICE_UNAVAILABLE`, ничего не удаляется.
     - Под advisory-блокировкой ученика одной транзакцией удаляются строки домена. Затем объекты, строка `ops.deletion_log` с тем же `deletion_id`, пересборка ученика (D-A-189). Если блокировку держит проектор — повтор до пяти раз.
  2. **Границы домена** (`Т-§19.5`):
     - все события с его `domain_id`;
     - его сессии (`content.sessions.domain_id`) целиком, со всеми их событиями, планами, манифестами, канвасами;
     - материалы домена и сессий со страницами;
     - задачи;
     - артефакты с версиями.
  3. **Объекты.** Ключи из `content.materials.object_key`, `material_pages.image_key` и `payload.object_key` удаляемых событий (фото и загрузки) удаляет `ObjectStore.delete_keys`.
  4. **Ответ** — `200 {deletion_id, copies_days: 15}`. 15 дней — срок копий из `Д-§8.6` («с запасом»: `backup_retention_days` 14 плюс сутки); это значение документа, не настройка.
  5. **Не сделано.** `DELETE /v1/topics/{id}` (границы темы — события её структуры, навыков и задач), `DELETE /v1/learner` Фазы 4 (сейчас — tombstone Фазы 3, меняется при переходе WP1.3), `reconcile_deletions`/`replay_deletions` (WP25 п.5) — следующим шагом.
- Почему: §0.4.3 п.1–2; закон 1.
- Тесты: `tests/graph/test_projection_runner.py::test_delete_domain` (журнал недоступен → ничего; чужой → `None`; события и объект домена удалены, второй домен цел, `ops.deletion_log`, граф без узлов домена). маршрут — в реестре `tests/e2e/routes/a.py` (D-C-147).
- Требует подтверждения владельцем: нет.

### D-A-199. S6: согласие на cheat sheet → `artifact.cheatsheet` хаба; строка `hub.deadline_set` остаётся (по #207 C)
- Класс: B. S6, WP23.5; `Т-§17.7`, ТЗ §6.3 (каталог, колонка диспетчера); `quack/jobs/rules_p4.py`
- Решение:
  1. **Согласие.** `agent.requested {kind: artifact, params: {artifact: cheatsheet, hub_id}}` ставит `artifact.cheatsheet` этого хаба. Блокировка и склейка — `artifact:{hub}`, как у строки дедлайна (D-A-138, D-A-139); нагрузка — `{request_id, kind, hub_id}`, чтобы обработчик ответил на просьбу. Без `hub_id` строить нечего — задачи нет. Остальные артефакты (`notes`, `cards`) остаются `agent.artifact`.
  2. **Строка `hub.deadline_set` → `artifact.cheatsheet` не снимается.** C просила снять её (D-C-144), но каталог ТЗ §6.3 у `hub.*` даёт в колонке диспетчера «артефакты (дедлайн)». Снятая строка разошлась бы с контрактом каталога и с тестом покрытия этой колонки. С `Т-§17.7` («генерация — после согласия») обе строки сходятся, если задача без `request_id` — проверка предложения, которая без согласия ничего не генерирует. Это поведение — обработчика C (D-C-138).
- Почему: §0.4.3 п.1–2; §0.4.4 — контракт каталога не меняется без записи класса C.
- Тесты: `tests/jobs/test_rules_p4.py::test_cheatsheet_consent_is_the_job_of_its_hub`.
- Требует подтверждения владельцем: нет.

### D-A-200. WP1.3, шаг 1: процессы `projector` и `workers` Фазы 4 и переключатель
- Класс: B. WP1.3, WP3.3, WP7; `Т-§2.4`, `Т-§13.1`–`Т-§13.3`, `Т-§22.4`, `Д-§6.1`; `quack/graph/projection/process.py`, `quack/runtime/phase4.py`, `quack/cli.py`, `quack/config.py`
- Контекст: модули Фазы 4 влиты (#185, #182), но процессы работают на коде Фазы 3. Обе стороны смотрят в одну базу (`postgres_dsn`), поэтому полный переход — это переключение схемы развёртывания на dbmate (WP9, C) и перенос 41 модуля Фазы 3 (WP1.3, G4). Без процессов Фазы 4 не пишется `T-E1`, и обработчики задач B и C не запускаются.
- Решение:
  1. **`ProjectorP4`.** Каждые `consumer_poll_ms` — ученики, у которых `core.learners.seq` больше копии отметки, а поток не остановлен (`ops.projector_failures`), по одной пачке (`project_learner`, с Redis — публикация тем). Сбой одного ученика (временная ошибка Neo4j) пишется в лог, следующий проход повторяет его пачку. Пустая пачка подтягивает отставшую копию отметки: иначе ученик, у которого упала копия после фиксации, находился бы снова и снова.
  2. **`projector_process`** — схема Neo4j, затем `ProjectorP4` и `DispatcherP4`: две задачи под надзором (`supervise`, как в WP11.1). **`workers_process`** — `JobWorker` на очереди Фазы 4 и тик периодических задач WP7.5 (`scheduler_tick_s`) на ресурсах `JobContext`. Обработчики регистрируются по-прежнему (`@register_job_handler`, D-A-116); переходник `bind_handlers` даёт каждому задачу в форме Фазы 3: id — от `job_key`, `priority` — номер класса, `attempt` — номер выбора.
  2а. **`job_key` и `session_id`** задачи Фазы 4 переходник передаёт в `ClaimedJob` Фазы 3: там это необязательные поля, по просьбе B в #210 (D-B-191, D-B-193).
  3. **Переключатель** `phase4_processes` (по умолчанию выключен): `quack.cli` запускает `projector`/`workers` Фазы 4 вместо Фазы 3. Включают его вместе с переходом схемы (WP9). Код Фазы 3 и флаг уходят с остатком WP1.3. Имени в §6.7 нет — это механизм перехода, не порог.
- Почему: §0.4.3 п.1–2; разрешённое пересечение 1 §7.3 (WP1.3 — A, `cli.py` — сборка).
- Тесты: `tests/graph/test_projection_runner.py` — `test_projector_process_walks_the_lagging_learners`, `test_workers_adapter_runs_a_registered_handler`, `test_projector_process_starts_and_stops`; межученический обход — в `CROSS_LEARNER_ALLOWED` (T-DB4).
- Требует подтверждения владельцем: нет.

### D-A-201. WP25 п.5: сверка и повтор удалений; задача `deletions.reconcile`
- Класс: B. WP25 п.5, WP7.5; `Т-§19.5`, `Д-§8.5`, `Д-§8.7`; `quack/deletion/journal.py`, `quack/deletion/jobs.py`, `quack/deletion/domain.py`
- Решение:
  1. **Общий шаг** `apply_domain_record(…, learner_id, record)`: под блокировкой ученика — удаление по записи, строка `ops.deletion_log` (`ON CONFLICT DO NOTHING`), пересборка. Его вызывают и `delete_domain` после записи в журнал, и сверка с повтором. Второй запуск ничего не находит, строка уже есть.
  2. **`reconcile_deletions(since)`** — записи журнала R2 без своей строки `ops.deletion_log` выполняются заново. **`replay_deletions(since)`** — все записи с `since` (после восстановления Postgres, `ops replay-deletions` — команда WP9.6 у C).
  3. **`deletions.reconcile`** (периодическая WP7.5, ключ `deletions-reconcile:{дата}`, D-A-166) сверяет весь журнал: WP25 п.5 говорит «объекты R2 без строки», без окна. Вид добавлен в `JOB_KINDS`/`KIND_LLM_ROLES` (без ролей), модуль — в `HANDLER_MODULES` (процедура (б) #173).
  4. **Записи `topic` и `account`** пока пропускаются с предупреждением: их удаление Фазы 4 — следующий шаг WP25.
- Почему: §0.4.3 п.1–2; закон 5 (повтор не дублирует).
- Тесты: `tests/graph/test_projection_runner.py::test_reconcile_and_replay`.
- Требует подтверждения владельцем: нет.

### D-A-202. WP25: физическое удаление темы — `DELETE /v1/topics/{id}`
- Класс: B. WP25 п.1–4; `Т-§19.5`; `quack/deletion/topic.py`, `quack/deletion/router.py`, `journal.py`
- Решение:
  1. **Порядок** тот же, что у домена (D-A-197): проверка темы ученика (`404`) → запись в журнал R2 (`503`, если нет) → удаление под блокировкой → строка `ops.deletion_log` → пересборка. Сверка и повтор (D-A-201) разбирают записи вида `topic`.
  2. **Состав темы** — объединение графа и журнала. Из графа: навыки по `COVERS` и задачи по `TESTS` к ним. Из журнала: `skill.created` с этой темой и `task.created`, разметка которых проверяет эти навыки. Так повтор после восстановления находит состав, даже если граф тему уже забыл.
  3. **Удаляемые события:**
     - с `topic_id` темы; `topic.merged`/`topic.split` с ней;
     - с `skill_id` её навыков (кроме ответов); `skill.*` и `edge.*`, где навык — конец;
     - `embedding.ready` её сущностей (тема, навыки, задачи);
     - всё с `task_id` её задач; события с `answer_seq` ответов на эти задачи (вердикты, `check.failed`, описки).
     Кроме того, строки `content.tasks`, артефакты темы с версиями, объекты из `payload.object_key`. Ход учителя и остальные события остаются и при пересборке применяются как пустые.
  4. **Изоляция.** Запрос состава группируется по `t.id`: агрегация без ключа отдаёт строку и для чужой темы — T-E3 поймал это до слияния; тест проверяет чужого ученика явно.
  5. **Удаление аккаунта Фазы 4** (`DELETE /v1/learner`) — с переключением маршрута Фазы 3 (WP1.3); до него запись вида `account` при сверке пропускается с предупреждением.
- Почему: §0.4.3 п.1–2; закон 1.
- Тесты: `tests/graph/test_projection_runner.py::test_delete_topic` (структура, задача, ответы и вердикты темы удалены, ход остался; граф без темы, навыка и задачи, вторая тема цела). Маршрут — в реестре `tests/e2e/routes/a.py`.
- Требует подтверждения владельцем: нет.

### D-A-203. WP17.3: прогноз темпа и предупреждение в «Сегодня»
- Класс: B. WP17.3; `Т-§14.6`; `quack/review/today.py` (`pace_warning`, `_pace`)
- Решение:
  1. **Темп** — тем хаба, доведённых до готовности за последние `pace_window_days`, на день активности. Время готовности темы — когда стал доказанным (`direct`/`implied`) последний требуемый уровень её навыков (`lNProvenAtMs`). Если тема не готова сейчас, она не готова.
  2. **Дни активности** — различные даты в часовом поясе ученика (`core.learners.settings.timezone`, иначе UTC), в которые есть события `actor = learner` за окно. **Ожидаемые дни до дедлайна** — дни до даты × доля активных дней окна («сколько дней в неделю обычно занимается»).
  3. **Предупреждение** `pace_warnings: [{hub_id, ready, not_ready, forecast}]` — для хабов домена с будущим дедлайном, если прогноз < `pace_warning_margin` × неготовых тем. Успевает — молчание (П-8.2). Меньше `pace_min_active_days` дней активности — прогноза нет.
- Почему: §0.4.3 п.1–2; «прогноз честен, понятен и легко заменяется» (`Т-§14.6`).
- Тесты: `tests/review/test_pace.py::test_pace_warning`.
- Попутно (ТЗ §0.4.4 п.10): подавления `type: ignore` в коде и тестах A заменены проверкой моделью, `cast` и `isinstance` — их не было из-за ограничений библиотек.
- Требует подтверждения владельцем: нет.

### D-A-204. WP7.5: обработчик `manifests.cleanup`
- Класс: B. WP7.5; `Т-§3.2`, `Т-§3.6` (закрывает Ф3 R14); `quack/jobs/cleanup_p4.py`
- Решение: раз в сутки (ключ `manifests-cleanup:{дата}`, D-A-166) удаляются манифесты ходов всех учеников старше `turn_manifest_retention_days` (по `created_at`). Это межученический оператор периодической задачи, он внесён в `CROSS_LEARNER_ALLOWED` с причиной. Вид — в `JOB_KINDS`/`KIND_LLM_ROLES` без ролей, модуль — в `HANDLER_MODULES` (процедура (б) #173). `calibration.weekly` (`Т-§18.5`) и `exports.cleanup` (`Д-§8.4`, экспорт — C) — без обработчиков: задачи ждут в очереди.
- Почему: §0.4.3 п.1–2.
- Тесты: `tests/graph/test_projection_runner.py::test_manifests_cleanup_deletes_only_old_manifests`.
- Требует подтверждения владельцем: нет.

### D-A-205. WP25 п.3: удаление аккаунта Фазы 4 (функция; маршрут — при переходе)
- Класс: B. WP25 п.3; `Т-§19.5`, `Д-§8.5`, `Д-§8.6`; `quack/deletion/account.py`, `store.delete_learner_graph`, `journal.py`
- Решение:
  1. **Порядок.** Запись `account` в журнал R2 первой, иначе `503`. Затем под блокировкой ученика одной транзакцией — все строки ученика в схеме Фазы 4, от детей к родителям, включая `core.events` и `core.learners`; ссылки входа (`core.auth_links`) — по его почте.
  2. **Остальное хранилище.** Префикс объектов `learners/{id}/` (`ObjectStore.delete_prefix`), подграф Neo4j (`delete_learner_graph`, по меткам), ключ присутствия в Redis. Последней — строка `ops.deletion_log`: она остаётся, по ней работают сверка и повтор.
  3. **Повторяемо.** Второй запуск ничего не находит. Сверка и повтор (D-A-201) теперь разбирают все три вида записей.
  4. **Маршрут** `DELETE /v1/learner` Фазы 3 (tombstone и `account.purge_deleted`) переключается на эту функцию с WP1.3. Тогда же вызывается `tracer.delete_learner` LangSmith (#210, D-B-202): на `False` ничего не делать.
- Почему: §0.4.3 п.1–2; закон 1.
- Тесты: `tests/graph/test_projection_runner.py::test_delete_account` (строки, объекты и подграф ученика удалены, чужие целы, строка журнала есть, повтор не падает). `delete_learner_graph` добавлен в `NEO4J_WRITERS` (T-A9a).
- Требует подтверждения владельцем: нет.

### Ответы потока A (2026-11-02)

- **Состояние #78, #80.** Влиты в `phase-4` (слияние в `fc38d58` по сообщению B). Дальше A работает от `phase-4`.
- **C: `quack.runtime`.** `create_job_context` строит `ComputeClient` через `create_compute_client(settings)` (§7.3 п.6): таймаут задания = `compute_task_timeout_s + compute_timeout_sec`.
- **C: D-A-101 и `Д-§4.1`.** `Д-§4.1` называет соединения по ролям: api 2 × 10, workers 2 × 5, projector 8 (всего около 46 из `max_connections = 100`). `pg_pool_max_size` — одно число на процесс (D-A-101, 10) — это верхняя граница, соединения открываются по мере надобности (`pg_pool_min_size = 1`). Худший случай при общем значении 10: 2 × 10 + 2 × 10 + 10 + служебные ≈ 64 из 100. Чтобы совпасть с `Д-§4.1`, **C ставит `PG_POOL_MAX_SIZE=5` у `workers` и `8` у `projector` в `compose.yaml`** (параметр уже есть, кода не нужно). Отдельной настройки «по роли» не заводим — в документах её нет.
- **C: D-A-103.** Подтверждено владельцем (строка «Требует подтверждения» D-A-103); ответ C принят.
- **C: `CROSS_LEARNER_ALLOWED`.** Две записи для #97 добавлены в `tests/db/test_sql_static.py`: `quack/auth/links.py:_LEARNER_BY_EMAIL`, `quack/auth/sessions.py:_SESSION_BY_TOKEN` (до входа ученик неизвестен, D-C-111).
- **C: срок заглушек `graph.read` (S4).** Готовы: D-A-111, `tests/support/graph_read.py`. Реализация — G2 (WP6.4).
- **B: `Verdict`.** `misconception` разделён на `misconception_id: UUID | None` и `misconception_hypothesis: str | None` (Q-A-2 п.1). `model_verdict` принимает `known_misconceptions` — ids в порядке меток `M1`…; метка в событие не попадает, неизвестная метка отбрасывается.
- **B: `rejected` (Q-A-2 п.3).** `{index, reason, quote}`: `index` — позиция элемента в выходе извлекателя (провенанс, закон 6).
- **B: чтение `core.events` (D-B-122).** Общих функций чтения в `quack.events` A не даёт: WP1.2 помещает SQL в модули пакетов, и три запроса B (событие по ключу идемпотентности, события сессии после N, владение доменом по `domain.created`) B пишет в своих `repo.py`. Условия: параметры только `%(name)s`, фильтр `learner_id` (тест `test_sql_static.py` это проверяет), для горячего пути — на `Db`, не на SQLAlchemy. Запрос по ключу идемпотентности уже есть внутри `append_events` (повтор возвращает записанное событие) — для страховки `check.symbolic` вердикт ищется по `verdict:{answer_seq}:{part}` тем же ключом. Если ни одна из функций не нужна второму потоку, дублирования нет.
- **B: правила S6 для WP11.3.** Таблица выше (D-A-110): `answer.submitted` быстрого типа → `check.symbolic` с `run_at = occurred_at + symbolic_check_delay_ms`; `check.failed` → `check.model`; `episode.closed` → `extract.episode`.
- **Ревью (2026-11-02).** Опубликованы в PR: #97, #98, #99 (открытые; в #99 одна ошибка состояния `StrokeJournal.flush`, до слияния), #93, #94, #96 (влиты до ревью; замечания — следующим PR B и C). `open_db(settings, wait=False)` — для замены блока пула в `create_app_resources` (C).

### Поток B

### D-B-101. Строки таблицы ролей без строки в `Т-§11.1`
- Класс: B
- WP / место: WP10.1, `quack/config.py` (`default_llm_roles`)
- Контекст: `Т-§11.1` не даёт `effort`/`max_output_tokens` для `segmenter`, `voice`, `illustrator`; `Т-§11.3` не называет класс `voice`; `recognizer` (P0 канвас / P1 фото) и `builder` (P2 сверка / P3 гигиена) имеют два класса, а `RoleConfig` — одно поле.
- Варианты: (а) null для нетекстовых, класс роли — по горячему использованию, класс задачи — правилом диспетчера; (б) отдельные роли на каждое использование.
- Решение: (а). `voice` — P1 («ученик ждёт результат», `Т-§11.3`); `recognizer` — P0, `builder` — P2; класс P1/P3 задачи фото и гигиены даёт правило диспетчера (WP10.1: «класс задачи задаёт правило диспетчера»). Таймауты: teacher 45 с, teacher_fallback 30 с (Фаза 3), остальные 60 с (`llm_timeout_sec`).
- Почему: §0.4.3 п.2–3 (буква `Т-§11.1`, без новых ролей).
- Обратимость: значения в `default_llm_roles`.
- Тесты: `T-AI1` (`tests/llm/test_phase4_ai_layer.py`).
- Требует подтверждения владельцем: нет

### D-B-102. `gap_type_min_confidence` = 0.6
- Класс: B. WP14.2. Стартовое значение — из ТЗ Ф4 WP14.2 / §6.7. Тесты: `T-CX2` (WP14). Подтверждение: нет.

### D-B-103. Лимиты инструментов памяти в `Settings.memory_tool_limits`
- Класс: B
- WP / место: WP14.4, `quack/config.py`, `quack/session/tools.py`
- Контекст: `Т-§10.4` даёт лимиты 10/5/2/1/5/10 как ориентир; числа в коде запрещены (§0.3).
- Решение: словарь `memory_tool_limits` по имени инструмента со значениями `Т-§10.4`.
- Почему: §0.3 «пороги только в Settings». Обратимость: значение параметра. Тесты: `T-CX4`. Подтверждение: нет.

### D-B-104. Перечни `intent` и `approach` выхода хода до доктрины
- Класс: C
- WP / место: WP13.3, `quack/session/teacher_output.py`
- Контекст: WP13.3 требует закрытых перечней «по доктрине владельца» к G1; доктрины нет (раздел «Промпты»).
- Варианты: (а) временные перечни из `П-4.4` (выбор хода) и `Т-§9.5`/`П-4.2` (приёмы); (б) открытая строка — нарушает строгий режим `Т-§11.1`.
- Решение: (а). `intent`: clarify_goal, explain, worked_example, practice, retrieval, diagnose, hint, feedback, answer_question, plan, wrap_up. `approach`: example_first, attempt_first, small_steps, prediction_first, faded_example, interleaving, contrast_pair, refutation, explain_in_own_words, visualization, none.
- Почему: §0.4.3 п.2 (ближе к `П-4.4`, `Т-§9.5`).
- Обратимость: два `Literal` в одном файле.
- Тесты: `tests/session/test_phase4_contracts.py`.
- Требует подтверждения владельцем: да

### D-B-105. Пространство имён псевдонима LangSmith
- Класс: B. WP10.6, `langsmith_pseudonym_ns` — фиксированный UUID в `Settings` (§2.3 строка 20 требует «хранится в конфиге», значения нет). Менять после первой трассы нельзя (псевдонимы разойдутся). Тесты: `T-AI8` (G4). Подтверждение: нет.

### D-B-106. `root_cause_confidence_map` = {low: 0.3, medium: 0.6, high: 0.9}
- Класс: B. WP15 п.3. Значений в документах нет; корни на состояние не влияют (`Т-§9.4`), число нужно только для `ROOT_CAUSE {confidence}` и ранжирования. Обратимость: параметр. Тесты: `T-X*` (WP15). Подтверждение: нет.

### D-B-107. Форма `AnswerSpec` и `TaskMarkup`
- Класс: B
- WP / место: WP11.1, WP12.2, `quack/practice/answer_spec.py`, `quack/practice/markup.py`
- Контекст: `Т-§5.2`, `Т-§6`, WP11.1 называют содержимое, но не поля.
- Решение: «короткий точный ответ» `Т-§6.4` — тип `short_text`; верный ответ `value: {latex, sympy}` (`Т-§22.7`), элементы `set`/`vector` — `items`; варианты выбора (id, текст, верный) — в `AnswerSpec.options`, ловушки и «соседняя идея» — в `TaskMarkup.options` по `option_id`; части — `TaskMarkup.parts` со своей `AnswerSpec` и необязательными `tests` (спецификация без рекурсии — строгая схема не выражает рекурсию); рубрика без пространства решения — `key_ideas`; `form_target` — `factored`/`simplified` (примеры `Т-§6.6`); допущения — имена SymPy. Имена полей разметки — как в `Т-§5.2` (`methodNamed`, `variantOf`, `pairTaskId`).
- Почему: §0.4.3 «частые случаи», первая строка; строгий режим `Т-§11.1`.
- Обратимость: модели в двух файлах.
- Тесты: `T-TK2`, `T-CH1`, контрактные тесты.
- Требует подтверждения владельцем: нет

### D-B-108. Новые виды коротких меток
- Класс: B. WP13.3, WP13.7, WP14.4, `quack/llm/labels.py`: хабы `H`, куски `P`, карточки `C`, поручения `R`, свидетельства `E`, строки канваса `L` — модель не видит id (`Т-§4.5`), а дедлайн, ссылки, карточки, отмена поручений и аргументы инструментов на них ссылаются. Без своих лимитов (как `M`). Подтверждение: нет.

### D-B-109. Виды и поля `Element`
- Класс: B. WP13.7, `quack/session/elements.py`: 13 видов `Т-§12.3` — `choice`, `why_choice`, `short_answer`, `task`, `scaffold`, `prediction`, `free_answer`, `pair`, `card_series`, `timed_quiz`, `visualization`, `material_link`, `reference_card`. Элемент несёт только показ; спецификация и разметка задания — в `p3_markup.tasks` с тем же `element_id`. Подтверждение: нет.

### D-B-110. Поля `p2_body`, `agentRequests`, дедлайна из разговора
- Класс: B. WP13.3: блок — `{block_id, text | null, element_id | null}`; параметры поручения — один объект с полями по видам (null, если не применимо: строгий режим без свободных словарей); дедлайн — `{hub_label, new_hub_name, deadline_at (ISO 8601), coverage: метки тем}`. Подтверждение: нет.

### D-B-111. `oneOf` в строгой схеме
- Класс: B. `quack/llm/strict_schema.py`: дискриминированное объединение Pydantic (`oneOf` + `discriminator`) превращается в `anyOf` без `discriminator`; `kind` каждого варианта закреплён `const`. Тесты: `test_role_schemas_are_strict`. Подтверждение: нет.

### D-B-112. Самооценка карточки — да/нет
- Класс: B. `answer.submitted.self_assessment` — `bool` («я помнил», `Т-§14.3`, `Т-§17.5`); уверенность 1–4 — отдельное поле `confidence`. Подтверждение: нет.

### D-B-113. Схемы выходов ролей B
- Класс: B. `quack/practice/role_outputs.py`, `quack/agents/schemas.py`: поля — названные в `Т-§5.5`, `Т-§5.6`, `Т-§6.8`, `Т-§7.6`, `Т-§9.3`, `Т-§13.5`, `Т-§4.3`, `Т-§4.9`, `Т-§13.6`; остальное — минимально нужное читающему коду. Категории вместо чисел силы (закон 4); числовая `confidence` есть только у вердикта модели (порог второго мнения `Т-§7.7`) и у классификатора домена (порог `domain_autolink_confidence`). Подтверждение: нет.

### D-B-114. `second_opinion` не видит первичный вердикт
- Класс: B
- WP / место: WP11.6, вход `second_opinion@1`
- Контекст: `Т-§7.7` не говорит, подаётся ли первый вердикт второму мнению (реестр промптов, вопрос 3).
- Варианты: (а) вход тот же, что у первого (условие, рубрика, ответ), без первого вердикта; (б) с первым вердиктом.
- Решение: (а). Согласие считает код, сравнивая `outcome` двух вердиктов.
- Почему: §0.4.3 п.1 — независимая проверка; с первым вердиктом на входе второе мнение склонно соглашаться, и «согласие» (Т2/Т3, `Т-§7.7`) теряет смысл; закон 10 (модель не строит вывод на прошлом выводе).
- Обратимость: состав входа в одной функции сборки (WP11.6).
- Тесты: `T-CH6`.
- Требует подтверждения владельцем: нет

### D-B-115. Разбор выражения в `validate_markup` — грамматикой токенов на стороне API
- Класс: B
- WP / место: WP12.3, `quack/practice/grammar.py`, `quack/practice/markup.py`
- Контекст: WP12.3 — «выражение разбирается SymPy через compute или локальный разбор грамматики»; в backend SymPy нет, вызов compute на каждой проверке разметки — сеть на горячем пути хода.
- Варианты: (а) локальная грамматика токенов `Т-§6.5` (белый список, баланс скобок); (б) вызов compute; (в) SymPy в backend — зависимость вне §4.6.
- Решение: (а). Полный разбор SymPy — в `check()` через compute; не разобралось там — `check.failed` → модель (`Т-§22.7`).
- Почему: §0.4.3 п.2 (вариант назван в ТЗ), п.5 (без новой зависимости).
- Обратимость: одна функция `grammar_ok`.
- Тесты: `T-TK2` (`test_markup_common_rules`: `x.__class__` → `expression_unparsed`).
- Требует подтверждения владельцем: нет

### D-B-116. Точное совпадение числа — `correct` без порога значащих цифр
- Класс: B
- WP / место: WP11.2, `quack/practice/check.py`
- Контекст: `Т-§6.4`: при допуске по точности ученика нужно не меньше `min_sig_digits` значащих цифр; буквально ответ `5` на эталон `5` давал бы `unclear`.
- Решение: точное совпадение значения — `correct`; порог значащих цифр применяется только к приближённому ответу. Дробь вида `a/b` сравнивается точно.
- Почему: §0.4.3 п.2 — смысл `Т-§6.4` («не засчитать слишком грубый ответ»); точный ответ не грубый.
- Обратимость: одна ветка в `_number`.
- Тесты: `T-CH1` (`1/3` → correct, `0.3` → unclear).
- Требует подтверждения владельцем: нет

### D-B-117. Предел шагов цикла с инструментами
- Класс: B
- WP / место: WP10.3, `quack/llm/tool_loop.py`, `Settings.tool_loop_max_steps`
- Контекст: `Т-§11.5` не ограничивает число шагов «модель ⇄ инструменты»; модель, которая всё время вызывает инструменты, держала бы ход бесконечно.
- Варианты: (а) параметр с консервативным значением, после него — ошибка цикла `ToolLoopLimitError` (ход — как при ошибке модели); (б) без предела.
- Решение: (а), `tool_loop_max_steps = 4` (§0.4.3: «меньший лимит»; шесть инструментов можно вызвать за один шаг параллельно).
- Почему: §0.4.3 п.1 (горячий путь не висит), последний абзац (консервативное значение).
- Обратимость: значение параметра.
- Тесты: `test_tool_loop_step_limit`.
- Требует подтверждения владельцем: нет

### D-B-118. Объявление `POST /v1/sessions/{id}/actions` родным SSE FastAPI
- Класс: B
- WP / место: WP13.1 (Этап I, к G1 по запросу C), `quack/session/router.py`, `quack/session/actions.py`
- Контекст: шов S11 требует кадры §4.7 и `Element` в OpenAPI к G1; FastAPI сам не выводит схему тела SSE.
- Варианты: (а) `EventSourceResponse` FastAPI 0.162 и генератор с типом `AsyncIterator[ActionFrame]` — схема кадров в `itemSchema` под `text/event-stream`; (б) свой класс ответа и `responses` — FastAPI добавляет рядом с `$ref` `type: string`.
- Решение: (а); `201 {seq}` — `ActionAccepted` под `application/json`. Тело — `NotImplementedError` до WP13.
- Почему: §0.4.3 п.6–7 (проверяемо, средствами уже используемой библиотеки).
- Обратимость: один маршрут.
- Тесты: `test_actions_route_in_openapi`.
- Требует подтверждения владельцем: нет

### D-B-121. Часть задачи без частей — `main`
- Класс: B. WP11.4, `quack/practice/answers.py` (`DEFAULT_PART`): ключ вердикта `verdict:{answer_seq}:{part}` (`Т-§6.1`) требует часть, а у задачи без частей (`Т-§6.2`) её нет. Значение `main` — одна константа. Тесты: `test_instant_verdict_in_answer_transaction`. Подтверждение: нет.

### D-B-122. Помощь по задаче — запрос B к `core.events` до ответа A
- Класс: B
- WP / место: WP11.2, `quack/practice/answers.py` (`help_in_session`)
- Контекст: WP11.2 — помощь в вердикте вычисляет код по событиям задачи в сессии; общей функции чтения `core.events` в S2 нет (вопрос A в PR #78).
- Варианты: (а) запрос в репозитории B (WP1.2: SQL — в модулях пакетов, [ОРИЕНТИР]); (б) ждать функцию A.
- Решение: (а), параметры только `%(name)s`, фильтр по `learner_id` (`T-DB2`, `T-DB4`). Если A даст общую функцию в `quack.events` — заменить вызов.
- Почему: §7.1 «не ждать гейт»; §0.4.3 п.4 — одна функция.
- Обратимость: одна функция.
- Тесты: `test_help_from_session_events_and_repeat`.
- Требует подтверждения владельцем: нет

### D-B-120. Ядро математики WP11.5 — чистые функции до каркаса compute
- Класс: B
- WP / место: WP11.5, `compute/quack_compute/math/` (`grammar.py`, `equivalence.py`)
- Контекст: каркас compute с пулом и эндпоинтами `/v1/math/check`, `/v1/math/transition` — шов S10 (C, WP24) — ещё не готов; зависимости `pint`, `antlr4-python3-runtime==4.11.1`, `pebble` — в `compute/pyproject.toml` (файл C).
- Решение: ядро без ввода-вывода и без пула: разбор грамматикой-белым списком (`Т-§6.5`, `Т-§22.7`), сравнение в режимах `equal`, `up_to_constant`, `solution_set`, `interval`, `form` (`Т-§6.6`), подсказки `sign`, `coefficient` (`Т-§6.2`). `numeric_points` и бюджет `simplify` приходят параметрами от вызывающего (`Settings`, §6.7). Точность вычислений, диапазон точек и допуск сравнения — константы модуля, как в `checker.py` Фазы 3 (в §6.7 у compute только `compute_workers`, `chromium_idle_close_s`). Переменные вещественные (`Т-§6.6`): точка с невещественным значением — «не определена», кроме выражений с `I`. `form`: структурная проверка есть только для `factored`; для `simplified` — отказ `unsupported` → проверка моделью (`Т-§6.6`). Величины (`pint`), LaTeX (ANTLR strict) и эндпоинты — после S10 и зависимостей.
- Почему: §7.1 «не ждать гейт»: работа в своей зоне без чужих швов.
- Обратимость: новый каталог, старый `checker.py` не тронут.
- Тесты: `compute/tests/math/test_grammar.py` (`T-CH5`), `test_equivalence.py` (`T-CH4`, `T-CH7` без размерности).
- Требует подтверждения владельцем: нет

### D-B-123. Эндпоинты `/v1/math/check`, `/v1/math/transition`
- Класс: B
- WP / место: WP11.5, `compute/quack_compute/math/` (`schemas.py`, `service.py`, `latex.py`, `units.py`), маршруты в `compute/quack_compute/app.py` (объявление B по §7.3 п.3, как просил C в #82)
- Контекст: `Т-§6.5`–`Т-§6.7`, `Т-§15.4` п.1, `Т-§22.7` задают поведение, не форму запроса.
- Решение: выражение — `{latex?, sympy?}`, `sympy` разбирается первым, без него — LaTeX через ANTLR strict; `kind = quantity` — сырой ответ с единицей и `pint` (СИ, запятая → точка, допуск по `Т-§6.4`, иная размерность — `incorrect` + `dimension`); бюджет вызова — `timeout_ms` из запроса (API — `symbolic_inline_timeout_ms`, страховка — `symbolic_background_timeout_ms`), пороги проверки — объект `numeric` и `tolerance` из `Settings` вызывающего (D-B-124); неудача — `status: failed` с `reason` → `check.failed`. Переход строки: уравнение — равенство множеств решений, неравенство — интервалы, иначе — эквивалентность выражений.
- Почему: §0.4.3 п.2 (буква `Т-§`), п.6 (покрыто тестами).
- Обратимость: модели в `math/schemas.py`.
- Тесты: `compute/tests/math/test_service.py`.
- Требует подтверждения владельцем: нет

### D-B-124. Пороги численной проверки WP11.5 — в `Settings`, в compute через `Params`
- Класс: B
- WP / место: WP11.5, `compute/quack_compute/math/equivalence.py` (`Params`), `quack/config.py` (блок B)
- Контекст: ТЗ Ф4 §0.3 — пороги только в `Settings`, числа в коде модулей запрещены (ревью C к #79). `Т-§6.6` называет только `numeric_points` («высокая точность», «случайные точки области» — без чисел); у compute в §6.7 только `compute_workers`, `chromium_idle_close_s`. Отменяет часть D-B-120 про константы модуля.
- Варианты: (1) константы модуля, как в `checker.py` Фазы 3; (2) поля `ComputeSettings` (файл C); (3) `Settings` API (блок B), значения едут в запросе `/v1/math/*` и попадают в `Params`.
- Решение: (3). `math_precision_digits` = 30, `math_sample_low`/`math_sample_high` = −10/10, `math_positive_sample_min` = 1e-3, `math_max_attempts_per_point` = 10, `math_rel_tol` = 1e-9, `math_abs_tol` = 1e-12, `math_simplify_share` = 0.8 — значения Фазы 4 до этого решения, без изменения поведения.
- Почему: §0.3 (буква ТЗ); §0.4.3 п.4 — одно место правки; без правки чужого файла (§7.3).
- Обратимость: параметры в одном блоке `Settings`.
- Тесты: `compute/tests/math/test_params.py` (нет числовых констант в `math/*.py`, состав `Params`, допуск берётся из `Params`); `test_service.py` (`numeric`, `tolerance`, поля `numeric` обязательны — в compute нет значений по умолчанию).
- Требует подтверждения владельцем: нет

### D-B-125. S13: формат `function_call`, событий потока и флагов сбоев для подмен
- Класс: B
- WP / место: WP10.2–10.3, шов S13 (§7.2): интерфейс — `quack/llm/streaming.py` (`StreamingLLMProvider`, `StreamRequest`, `StreamEvent`), разбор SSE — `quack/llm/responses_client.py` (`_stream_event`); подмены — `tests/support/llm.py` (`ScriptedLLMProvider`) и `meta-mock` (C, §8.1, WP26.2)
- Контекст: §8.1 и WP26.2 называют возможности подмен (стриминг, ключи по алфавиту, тело раньше шапки по флагу, `function_call`, иногда два за шаг, `phase: commentary`, сбои Фазы 3), но не формат. Формат задаёт то, что уже читает код B.
- Решение — контракт для C:
  1. **`ScriptedLLMProvider.stream_structured(StreamRequest)`** отдаёт `StreamEvent` по шагам сценария схемы `request.base.schema_name`: `TextDelta` — куски текста ответа (только сообщения без `phase: commentary`); `OutputItemDone(item)` — каждый законченный элемент шага, как есть; последним — всегда `StreamCompleted(status, usage, model, headers)`. В журнал вызовов пишется весь `StreamRequest` (тесты B проверяют `input_items` следующего шага). Ответчик по умолчанию для схемы — один шаг без инструментов, текст — тот же JSON, что у `complete_structured`.
  2. **Элементы шага** (Responses API, Т-§11.5):
     - `{"type": "reasoning", "id": "rs_…", "encrypted_content": "…", "summary": []}`;
     - `{"type": "function_call", "id": "fc_…", "call_id": "call_…", "name": <имя инструмента из quack.session.tools>, "arguments": <JSON-строка аргументов>, "status": "completed"}` — код B берёт `call_id`, а не `id`; два вызова за шаг — два таких элемента подряд;
     - комментарий: `{"type": "message", "id": "msg_…", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "…"}]}` — перед `function_call`;
     - ответ: тот же `message` без `phase: commentary`, `content[0].text` — JSON выхода схемы.
     Шаг с `function_call` не содержит ответа; следующий шаг сценария выдаётся, когда во входе есть `function_call_output` на каждый `call_id` прошлого шага (`next_step_input`).
  3. **`meta-mock` (SSE `/responses`, `stream: true`)**: строки `data: {json}`; ключи по алфавиту на корне и во вложенных объектах (WP26.2). На элемент: `response.output_item.added {item}` (у комментария — с `phase: commentary`), для сообщения — `response.output_text.delta {item_id, delta}`, затем `response.output_item.done {item}`. В конце — `response.completed` или `response.incomplete` с `response: {model, status, usage: {input_tokens, input_tokens_details: {cached_tokens}, output_tokens, output_tokens_details: {reasoning_tokens}}}`. Заголовки `x-ratelimit-*` — у HTTP-ответа потока.
  4. **Флаг `body_before_head`** (§8.1): в тексте ответа хода ключ `p2_body` идёт раньше `p1_head`; парсер B буферизует ход целиком (Т-§12.2). Иначе ключи ответа — по алфавиту (`p1_head`, `p2_body`, `p3_markup`).
  5. **Сбои** — те же `ScriptedFailure.kind` Фазы 3, новых видов нет. Для потока: `unavailable`, `rate_limited` (с `retry_after`: секунды, дата HTTP или `None`), `bad_request` — исключение провайдера до первого события; `incomplete` — часть `TextDelta`, затем `StreamCompleted(status="incomplete")` (код B не разбирает, повторяет шаг с удвоенным лимитом); `invalid_json` — текст ответа не JSON, `status="completed"`; `commentary_only` — только элемент-комментарий, без ответа; `ratelimit_headers` — обычный ответ, заголовки — в `StreamCompleted.headers`. В `meta-mock` событие `response.failed` или `error` код B читает как `ProviderUnavailableError`.
- Почему: §0.4.3 п.3 — контракт совпадает с тем, что уже читает код B (`_stream_event`, `next_step_input`, `tool_calls`), шов не меняется; п.7 — сбои те же, что в Фазе 3.
- Обратимость: описание; код B не меняется.
- Тесты: `tests/llm/test_phase4_stream_adapter.py`, `tests/llm/test_phase4_ai_layer.py` (`T-AI2`–`T-AI6`) — на стороне B; тесты подмен — C.
- Требует подтверждения владельцем: нет

### D-B-126. Быстрая проверка WP11.3 в `check()`: что не пишется сейчас и что идёт к модели
- Класс: B
- WP / место: WP11.3, `quack/practice/check.py` (`check`, `_math_request`, `_from_compute`, `NotYet.reason`)
- Контекст: WP11.3 и `Т-§6.1` говорят «успел — вердикт; нет — ничего не пишет; страховка проверяет позже»; не названо: (а) что делать, если compute недоступен; (б) чем страховке отличить таймаут от недоступности; (в) ответ compute `unsupported` (нет структурной проверки вида, `Т-§6.6`); (г) `set`/`vector` не из чисел; (д) ответ без `sympy` и `latex`.
- Варианты: (1) таймаут и недоступность — `CheckFailed` сразу; (2) оба — `NotYet` с причиной, страховка превращает его в `check.failed` с той же причиной; (3) новый аргумент `check()` «режим API / страховка».
- Решение: (2). `NotYet.reason` — `timeout` (ожидание дольше `timeout_ms` с отменой запроса, или 504 compute) или `compute_unavailable`; значения совпадают с `CheckFailedReason` каталога A. `unsupported` → `NeedsModel` (модель, класс model, `Т-§6.6`), а не `check.failed`. `set`/`vector` не из чисел и спецификация без `value` → `CheckFailed("unparsed")` без вызова compute (в compute нет такого режима; `check.failed` → модель, `Т-§22.7`). Ответ ученика без `sympy` и `latex` уходит строкой `sympy`: compute разбирает любую строку грамматикой-белым списком (`Т-§6.5`). Допуск величины: `tol_abs` / `tol_rel` / `sig_digits` спецификации переводятся в `tol_abs` той же функцией, что у чисел (`Т-§6.4`); `min_sig_digits` и пороги численной проверки — из `Settings` (D-B-124).
- Почему: §0.4.3 п.3 — подпись `check()` (шов S7) не меняется, в `NotYet` только поле со значением по умолчанию; п.1 — каталог событий не трогается; п.2 — `Т-§6.6` («нет её — проверка моделью»).
- Обратимость: одна функция `_from_compute` и поле `NotYet.reason`.
- Тесты: `tests/practice/test_check_fast.py` (чистая часть `T-CH2`, `T-CH3`).
- Требует подтверждения владельцем: нет

### D-B-127. Распознанный ответ: порог «неуверенного» прочтения и снижение уверенности вердикта
- Класс: B
- WP / место: WP11.7, `quack/practice/check.py` (`apply_reading`), `quack/config.py` (блок B, `recognition_min_confidence`)
- Контекст: `Т-§6.9`, `Т-§16.3`: «уверенность распознавания понижает уверенность вердикта»; «неуверенное распознавание + отрицательный вердикт → сначала прочтение». Числа порога и способа понижения в документах нет; `mark_min_confidence` (`Т-§15.4`) — о диагнозе строки, не о прочтении.
- Варианты: (1) новый параметр `recognition_min_confidence`, понижение умножением; (2) взять `mark_min_confidence`; (3) понижение минимумом.
- Решение: (1). `recognition_min_confidence` = 0.8 (консервативно: чаще показываем прочтение до отрицательного вердикта; то же число, что у `mark_min_confidence`). Уверенность вердикта кода (её нет) становится уверенностью распознавания; уверенность модели умножается на неё. «Отрицательный» — `incorrect` и `partial` (консервативно: ученик видит прочтение раньше частичного вердикта). Набранный ответ (`recognition_confidence = None`) не меняется.
- Почему: §0.3 и §0.4.3 последний абзац — параметра нет в документах, значение консервативное, в `Settings`; п.4 — одна функция и один параметр.
- Обратимость: `apply_reading` и параметр в блоке B.
- Тесты: `tests/practice/test_check_reading.py` (`T-CH8`).
- Требует подтверждения владельцем: нет

### D-B-128. Второе мнение: что считается «отрицательным», «согласием» и чей вердикт
- Класс: B
- WP / место: WP11.6, `quack/practice/model_check.py` (`model_verdict`, `needs_second_opinion`, `with_second_opinion`)
- Контекст: `Т-§7.7` называет триггеры и результат, но не говорит: (а) входит ли `partial` в «отрицательный» и «положительный»; (б) что такое «согласно»; (в) как одна задача с несколькими уровнями даёт триггер; (г) куда идёт гипотеза заблуждения, пока `Verdict.misconception` одно поле (Q-A-2 п.1 не закрыт).
- Варианты: (а) `partial` — триггер или нет; (б) равенство `outcome` или совпадение пунктов рубрики; (в) любой уровень или все.
- Решение: (а) `partial` — не триггер: спайк `Т-§7.7` прямо отверг «partial на уровне 4 и выше» как новый триггер; (б) согласно — тот же `outcome`; (в) любой проверяемый уровень задачи; (г) `misconception` — метка, иначе гипотеза словами (`WP11.4` «метка или формулировка»), до разделения полей по Q-A-2. Вердикт кода второго мнения не получает (`Т-§7.7`: «только для вердикта модели»), даже с уверенностью распознавания (D-B-127). Ярус по `secondOpinion` ставит код A (WP4.1).
- Почему: §0.4.3 п.2 — буква `Т-§7.7` и его спайка; п.5 — без новых параметров.
- Обратимость: одна функция `needs_second_opinion`.
- Тесты: `tests/practice/test_second_opinion.py` (чистая часть `T-CH6`).
- Требует подтверждения владельцем: нет

### D-B-129. Чистые части WP13: правила шапки и разметки, буфер удержания, уровень рассуждения
- Класс: B
- WP / место: WP13.5 `quack/session/guards.py`; WP13.4 `quack/session/holdback.py`; WP13.8 `quack/session/turn_policy.py` (`teacher_reasoning_effort`)
- Контекст: граф хода (WP13.2) ждёт `Db` (S2), заглушек `graph.read` (S4) и шлюза; правила `Т-§12.4`, этап 3 `Т-§12.2` и таблица `Т-§12.9` от них не зависят. Не названо: (а) что такое «метки существуют» для разметки хода; (б) форма причины возврата модели; (в) когда проверяется спойлер в буфере.
- Решение: (а) метки `targetSkills`, `exposures[].skill` (навыки) и `targetsMisconception` (заблуждения) — из таблицы меток контекста; задания проверяет `validate_markup` (своё правило «каждое задание размечено»). Правило «опора» читает `REQUIRES` (S4) и придёт с ним. (б) Нарушение — `GuardViolation {reason, detail}`; `reason` — закрытый перечень кода, текст для модели собирает граф хода. (в) Спойлер проверяется по всему накопленному тексту при каждом куске и в конце тела; показывается только проверенный префикс без последних `spoiler_holdback_chars`. Уровень рассуждения — флаги `goal_changed`, `builds_plan`, `proof_answer` у функции Фазы 3 («функция Фазы 3 расширяется», WP13.8), по умолчанию `False`, вызовы Фазы 3 не меняются.
- Почему: §0.4.3 п.2 (`Т-§12.4`, `Т-§12.2`, `Т-§12.9`), п.3 (подпись функции Фазы 3 — только расширение), п.5.
- Обратимость: три модуля, по одной функции или классу.
- Тесты: `tests/session/test_head_guards.py` (часть `T-TT4`), `test_holdback.py` (часть `T-TT3`), `test_turn_effort.py` (`T-TT8`).
- Требует подтверждения владельцем: нет

#### Поток B: сверка таблиц B в базовой миграции (шов S2) и ответ на D-A-103

Сверено: `backend/db/migrations/20261102000001_baseline.sql` (ветка `phase-4-a-wp1`) против ТЗ Ф4 §6.1, `Т-§3.1`, `Т-§3.2`, `Т-§11.4`.

| Таблица | Итог |
| :-- | :-- |
| `content.session_plans`, `content.study_plans`, `content.tasks` | совпадает |
| `ops.llm_usage` | совпадает |
| `content.sessions` | **расхождение:** `domain_id UUID NULL`, а сессий без домена нет (`Т-§22.9`, ТЗ Ф4 WP13.10, WP16.4: «Домен выбирается до старта») — нужно `domain_id UUID NOT NULL` — **исправлено A в #78** (`b561aa3`) |
| `ops.turn_manifests` | **расхождение:** `prompt_version INT`, `renderer_version INT`, а в `ops.llm_usage` — `prompt_version TEXT`. Версии по `Т-§11.6`, `Т-§10.6` — `роль@N`, `renderer@N`; `teacher_fallback` идёт с промптом `teacher@2`, и по одному номеру промпт не восстановить для проигрывания (`Т-§3.2`). Предложение: `prompt_version TEXT`, `renderer_version TEXT` (`teacher@2`, `renderer@1`), как в `ops.llm_usage` — **исправлено A в #78** (`b561aa3`) |

Обе правки — до G0: после G0 только расширяющие миграции (§0.4.4 п.4).

**D-A-103 (две схемы до WP1.3):** B согласен с вариантом (3). Новый код B пишется сразу на `quack.db.Db` и `quack.events.log.append_events` (WP1.3); старые модули B (`quack/session/*`, `quack/practice/evaluation.py`, `selection.py`, `generation.py`) переходят на `Db` и удаляются, когда B переписывает их в WP11–WP15, остаток к G3 — PR A по §7.3 п.1.

### D-B-119. PR #75 (объявление `/actions`) вливается вместе с телом WP13.1
- Класс: B
- WP / место: WP13.1, PR #75
- Контекст: объявление маршрута без тела не проходит обход e2e `T-E2`, `T-E3`; исключить маршрут из обхода — сузить вход (§0.4.4 п.5), `xfail` вне §0.4.6 нельзя; `phase-4` должна быть зелёной (§7.4). G1 требует черновик `openapi.json` для фронтенда (§7.2, S11, S14).
- Варианты: (а) держать #75 открытым до тела WP13.1, черновик `openapi.json` к G1 брать из ветки #75; (б) вопрос владельцу.
- Решение: (а). C публикует снимок `openapi.json` из ветки `phase-4-b-actions-route` (коммит `679b8ea` и позже) в §«Для фронтенда» как черновик G1 с пометкой «маршрут `/actions` объявлен, тело — WP13.1»; в `phase-4` маршрут попадает вместе с телом.
- Почему: §0.4.3 п.1 (тесты не ослабляются, `phase-4` зелёная), п.3 (контракт S11 к G1 выдан в OpenAPI).
- Обратимость: влить #75 раньше, если владелец разрешит.
- Тесты: `test_actions_route_in_openapi` (B), T-A1 и тест 2xx (C, в #75), `T-E2`, `T-E3` — без изменений.
- Требует подтверждения владельцем: нет

### D-B-130. Наборы параметров визуализации: формат и как страница их читает (BLOCKED-C-201)
- Класс: B
- WP / место: WP16.5, `quack/agents/schemas.py` (`VizBuilderOutput.parameter_values`) → `RenderSnapshotsRequest.param_sets` (`quack/compute_client/contracts.py`, D-C-107) → страница в Chromium compute (WP24)
- Контекст: `Т-§13.6` п.1–2 — модель пишет страницу «какие параметры», compute делает снимки «при нескольких значениях параметров»; формат значений и способ передачи не названы. Строгая схема не допускает свободных словарей (`Т-§11.1`), поэтому в выходе модели — `list[str]`; в запросе compute — `list[dict]`.
- Варианты: (1) каждая строка — объект JSON, compute кладёт его в глобальную переменную до скриптов страницы; (2) параметры во фрагменте адреса `#…`; (3) отдельные поля «имя — значение» в схеме модели.
- Решение: (1).
  1. **Формат.** Каждый элемент `parameter_values` — строка с объектом JSON: имя параметра → число, строка или `true`/`false` (например `{"a": 2, "phase": 0.5}`). Один элемент — один снимок.
  2. **Перевод в `param_sets`.** Код B (`quack.agents`, WP16.5) делает `json.loads` каждой строки. Не объект или вложенные значения — нарушение разметки, оно идёт в ту же единственную попытку исправления (`Т-§22.10`). Пустой список — один набор `{}`: снимок со значениями по умолчанию.
  3. **Как страница читает.** compute перед загрузкой страницы задаёт `window.QUACK_VIZ_PARAMS = <набор>` (Playwright `add_init_script`, до любого скрипта страницы), затем `set_content(html)` и снимок. Страница берёт значения из `window.QUACK_VIZ_PARAMS`, а при его отсутствии — свои значения по умолчанию. У ученика (`GET /v1/viz/{id}`, iframe `sandbox="allow-scripts"`) переменной нет: страница открывается со значениями по умолчанию и меняет их своими элементами управления.
  4. **Промпт.** Требование «читать `window.QUACK_VIZ_PARAMS`, иначе значения по умолчанию» входит в контракт роли `viz_builder` (`PROMPT_CONTRACTS.md`). Текст промпта ведётся отдельно (заготовки владельца), здесь не пишется.
- Почему: §0.4.3 п.3 — схемы `VizBuilderOutput` и `RenderSnapshotsRequest` (D-C-107) не меняются; п.1 — без сети и без записи в страницу кода модели (CSP и sandbox `Т-§13.6`); п.5 — одна переменная, без нового протокола; страница одна и та же у compute и у ученика.
- Обратимость: имя переменной и одна функция разбора.
- Тесты: при реализации WP16.5 — разбор `parameter_values` (B, `tests/agents/`); снимок с набором, который страница читает из `window.QUACK_VIZ_PARAMS` (C, `compute/tests`).
- Требует подтверждения владельцем: нет

### D-B-138. Слой 2 промптов Фазы 4 — по схемам кода; имена входных блоков
- Класс: B
- WP / место: WP10.5, `quack/prompts/<role>/v<N>.md`, `tests/llm/prompt_contracts.py`
- Контекст: `## Output contract` заготовок стенда описывает схемы, которых нет в коде (поля
  `verdict`, `note`, `decisions`, `pairs.pair`, `verified`, `known_alternatives` и др.); имена
  входных блоков в реестре владельца — \[ОРИЕНТИР\]; сборщиков контекста для ролей Фазы 4 ещё нет
  (WP11.6, WP12.4–12.6, WP13–WP16).
- Варианты: (а) слой 2 заново по схемам S8 (`teacher_output.py`, `role_outputs.py`,
  `agents/schemas.py`), входы — по составу `Т-§`, имена — реестра, где он их даёт; (б) взять
  контракты заготовок и менять схемы.
- Решение: (а). Учитель: слоты `Т-§10.1` по порядку — слой 1 и слой 2 (статично), затем
  `tools_and_elements` (слой 3, шлёт код), `profile`, `domain_and_goals`, `conversation`,
  `session_plan`, `memory_picture`, `materials`, `labels`, `tail`, `guard_feedback`. Остальные
  роли — блоки по `Т-§` (`PROMPT_CONTRACTS.md`). Имена блоков — точка правки до написания
  сборщиков.
- Почему: §0.4.3 п.3 (схемы S8 не меняются), п.2 (состав входа — буква `Т-§`).
- Обратимость: текст промпта до первой выкатки правится на месте (§7.6 п.3).
- Тесты: `T-M5` (`test_prompt_mentions_only_declared_fields`), `test_prompt_contracts_file_is_current`.
- Требует подтверждения владельцем: нет

### D-B-139. Слой 1 `task_generator@1` — текст `task_gen@1`
- Класс: B. Заготовка `task_generator/v3.md` = разделы 1–8 `task_gen@1` плюс две фразы формата
  (значения `catches`, метка в `tests`) — это слой 2; они перенесены в `## Output contract`, слой 1
  остаётся словом в слово `task_gen@1`. Тесты: `T-M5`. Подтверждение: нет.

### D-B-140. `builder@1` — один промпт, две схемы по режиму
- Класс: B. `Т-§4.3` (сверка) и `Т-§4.10` (гигиена) — одна роль и один промпт (§6.6); код
  передаёт `text.format` по режиму: `BuilderOutput` или `HygieneOutput`. В реестре контрактов —
  кортеж схем (`PHASE4_PROMPT_SCHEMAS`), `T-M5` берёт объединение полей. Подтверждение: нет.

### D-B-141. `task_arbiter@1` — та же схема, что у `task_checker@1`
- Класс: B. `Т-§5.6`: при расхождении — арбитраж Wonder, «снова расхождение — задача
  выбрасывается»: арбитр даёт свой ответ и оценку разметки, сравнивает код (`check()`), как у
  проверяющего. Отдельной схемы нет (`TaskCheckOutput`); вход — оба решения и место расхождения.
  Обратимость: одна строка реестра. Подтверждение: нет.

### D-B-142. Пара уровня 5 от генератора — `pairTaskId` ставит код
- Класс: B. WP12.6: у второй задачи пары ещё нет метки `K…`, модель её не знает (`Т-§4.5`);
  генератор пишет обе задачи записями `tasks`, `pairTaskId` = null, связь ставит код до
  `validate_markup` (иначе правило уровня 5 не пройдёт). Тесты — с WP12.6. Подтверждение: нет.

### D-B-143. Правила формата из слоя 1 заготовок — в слой 2
- Класс: C
- WP / место: WP10.5, `verdict_free@1`, `verdict_proof@1`, `second_opinion@1`
- Контекст: в слое 1 заготовок есть правила формата под их контракт: префикс `new: ` в метке
  заблуждения (`verdict_proof` п.9, `second_opinion` п.5), раздел 4 `verdict_free` «System Output
  Note» (поле `note`, которого в схеме нет), служебная HTML-пометка стенда на русском.
- Варианты: (а) убрать их из слоя 1 и выразить в слое 2 (`misconception_hypothesis`); (б) оставить —
  модель получит противоречивые указания формата.
- Решение: (а), остальной текст слоя 1 — дословно.
- Почему: §7.6 п.1 (формат — слой 2, его пишет B); §0.4.3 п.3 (схема S8).
- Обратимость: вернуть фразы из пакета.
- Тесты: `T-M5`.
- Требует подтверждения владельцем: да

### D-B-131. Метки доменов `D`
- Класс: B
- WP / место: WP16.4, `quack/llm/labels.py`
- Контекст: `domain_classifier@1` выбирает домен ученика по метке (`Т-§4.5`, `Т-§4.9`), а в
  `LabelMap` вида «домен» не было; на стенде модель без метки писала имя домена.
- Решение: вид `domain` с префиксом `D` в `LabelMap`, `LabelMap.build(domains=…)`; без лимита (как `M`).
- Обратимость: убрать вид. Тесты: `test_label_map_labels_domains`, `T-M5`.
- Требует подтверждения владельцем: нет

### D-B-132. Слой 1 восьми ролей — черновики стенда
- Класс: C
- WP / место: WP10.5, `task_checker@1`, `task_arbiter@1`, `solution_space@1`,
  `misconception_describer@1`, `today_planner@1`, `domain_classifier@1`, `viz_builder@1`,
  `release_judge@1`; `builder@1` п.6
- Контекст: черновики слоя 1 написаны на стенде по тезисам документа стенда (реестр §5) и
  прогнаны; владелец в чате 2026-11-02 поручил B дорабатывать промпты самому («можешь дорабатывать
  сама все»). Слой 2 стенд не менял. В `builder@1` добавлен п.6 «та же идея в другом домене — свой
  навык со связью» (`Т-§3.4`, `Т-§4.3`): без него `same_idea` не заполнялся (0/3 → 3/3).
- Решение: черновики поставлены, заглушки и `BLOCKED-B-101…108` сняты (`DOCTRINE_PENDING` пуст).
- Обратимость: вернуть заглушки и записи `DOCTRINE_PENDING`.
- Тесты: `test_prompt_has_owner_doctrine`; стенд B (§«Промпты»).
- Требует подтверждения владельцем: да (текст слоя 1 — на ревью PR)

### D-B-133. Уточнения слоя 2 по стенду
- Класс: B
- WP / место: WP10.5, слой 2 промптов
- Контекст: стенд B показал, где модель путает контракт.
- Решение: пункт рубрики — метка (`R1`) без текста (`verdict_*`, `second_opinion`: было 0/12 →
  12/12); `assumptions` — список, `neighbor_idea` — true/false (`teacher@2`, `task_generator@1`);
  домен — метка `D…`, не имя; в `analyst@1` навыки и заблуждения — только метки; `extractor@3` не
  пишет `evidence` по навыку и уровню с вердиктом кода в эпизоде (`code_verdict_exists`);
  `teacher@2` — таблица уровней `Т-§5.3` для заданий и «метки не в тексте»; `builder@1` —
  `topic_hint` у каждого нового навыка, `same_idea` — навык другого домена; `viz_builder@1` —
  наборы параметров по D-B-130 (один JSON-объект на снимок, страница читает
  `window.QUACK_VIZ_PARAMS`, иначе свои значения по умолчанию; маркер в `MARKERS`, стенд — 6/6
  сценариев, 12 прогонов).
- Обратимость: вернуть формулировки. Тесты: `T-M5`, `test_prompt_contracts_file_is_current`.
- Требует подтверждения владельцем: нет

### D-B-134. Лимиты выхода по замеру с уровнем рассуждения
- Класс: B
- WP / место: WP10.1, `quack/config.py` (`default_llm_roles`)
- Контекст: 4096 в `Т-§11.1` обоснованы спайком без указания уровня рассуждения (самый длинный
  выход — 1674 токена). Замер стенда B на Meta API с продовыми `effort` (≈750 вызовов): обрыв
  первого ответа у `analyst` в 100% (медиана выхода 6043), `extractor` 36% (p95 10 182, максимум
  15 538), `solution_space` 29%, `task_generator` 26%, `analyst_deep` 8%; у `verdict_free`, `builder`,
  `task_checker` максимум — у самого лимита. Повтор с удвоенным лимитом спасает ответ, но удваивает
  задержку и расход; для P0/P1 это задержка, которую видит ученик (П-1.4).
- Решение: по `Т-§11.1` «меняются по замеру без правки документа» — около 1.5× p95, не выше
  `max_output_tokens_cap`: `extractor`, `analyst`, `analyst_deep` — 16 384; `verdict_free`,
  `builder`, `task_generator`, `task_checker`, `solution_space`, `teacher_fallback` — 8192.
  Остальные роли B — без изменений (запас есть). Роли C не мерились.
- Обратимость: значения конфига. Тесты: `test_role_table_matches_t111`.
- Требует подтверждения владельцем: нет (поручено решить B)

### D-B-135. Обход в `extractor@3`: просьба решения — не провал
- Класс: B (по поручению владельца решить по документам)
- WP / место: WP10.5, `extractor@3` слой 1, разд. 3 п.3
- Контекст: на стенде «не знаю, покажи решение» записывалось как `evidence` `incorrect`/`no_idea`,
  а пассивное «понял» после показа — уровнем 3 (main m03, holdout h03).
- Решение: две строки доктрины по `П-4.8` («каждый обход — сигнал, а не повод спорить», ученику
  не отказываем), `П-6.9`/`П-6.10` (показанное решение уровень не доказывает, пропущенная проверка —
  уровень неизвестен), `Т-§7` (показанное решение — «свидетельства понимания нет, есть обход»):
  из самой просьбы ничего отрицательного; работа по задаче — один раз с `help = shown_solution`,
  согласие после показа — не выше уровня 1; пропущенная проверка — ничего.
- Обратимость: убрать строки. Тесты: стенд B.
- Требует подтверждения владельцем: нет (поручено)

### D-B-136. `verdict_stance` — только о последнем действии ученика
- Класс: B
- WP / место: WP13.3/13.5, `teacher@2` слой 2; будущая защита «честный вердикт»
- Контекст: ход, открыто поправляющий пересмотренную проверку, ставил `verdict_stance = code`.
  `Т-§6.3`: позиция — «для ответа, на который отвечает»; поправка — отдельный слот контекста и
  поле `correctionsAcknowledged` (`Т-§5.6`, `Т-§10`).
- Решение: позиция относится только к последнему действию ученика (действию, запустившему ход);
  поправка пересмотра отмечается в `correctionsAcknowledged`. Защита WP13.5 сравнивает позицию с
  вердиктом ответа из этого действия.
- Обратимость: формулировка контракта. Тесты: стенд B; защита — при WP13.5.
- Требует подтверждения владельцем: нет

### D-B-137. Режим `interval` до проверки в коде — не в промптах
- Класс: B
- WP / место: WP10.5, `teacher@2`, `task_generator@1` слой 2; `compute` (`Т-§6.5`, `Т-§6.6`)
- Контекст: `Т-§6.6` — режим `interval` «неравенство: множества как интервалы и их объединения».
  В `phase-4` его сравнивает `compute` (`compute/quack_compute/math/equivalence.py`, `_intervals`:
  два неравенства с одной переменной; грамматика compute пускает `<`, `<=`, `>`, `>=`). Отсекает
  локальная грамматика `validate_markup` (`quack/practice/grammar.py`): в ней нет `<`, `>`, поэтому
  ответ-неравенство даёт `expression_unparsed`, и задача выбрасывается. Расширение `grammar.py` —
  отдельная задача B (не WP10.5).
- Решение: в контрактах режим `interval` не предлагается; неравенство — задачей с выбором
  интервалов или с числовым ответом. Реализация режима (имена `Interval`, `Union` в белом списке
  `Т-§6.5`, сравнение множеств) — отдельная задача проверки в коде; после неё режим возвращается в
  контракты.
- Обратимость: вернуть режим в перечень. Тесты: стенд B (`validate_markup`).
- Требует подтверждения владельцем: нет


#### Поток B: ответы на D-C-112 (п.2–3), слой учителя доски (WP21.1) и D-A-110 (п.4, п.7) — 2026-11-02

**D-C-112 п.2 (тестовый вход в `tests/session`).** Согласен. C переводит `tests/session/*` на `tests/support/auth.py: login(client, db, learner_id)` в своём PR вместе с остальными, ревью — B. Меняется только способ входа: утверждения и данные тестов B не меняются (§8.15).

**D-C-112 п.3 (`CONSENT_REQUIRED` на старте сессии).** Функция — C: `quack.auth.require_terms` (WP18.4). Объявление на `POST` старта сессии в `quack/session/router.py` делает B сам, в своём файле, вместе с WP13.1 и WP13.10: «Сессия не начинается без `consent.given {kind: terms}` — `403 CONSENT_REQUIRED`» — строка WP13.1, пакета B. Исключения §7.3 п.3 здесь не нужно. Тесты `T-AU6` (C) и часть `T-TT1` (B) читают одну зависимость. B ставит объявление сразу после слияния PR C с `require_terms`.

**Слой учителя доски (WP21.1).** Схему документа `content.canvas_boards.doc`, в том числе слоя учителя, задаёт C (WP21.1, §7.3: `quack/canvas/**`) и присылает B на ревью. По ТЗ код хода учителя (WP13, WP14) в доску не пишет:
- пометки `error`/`question` и тихие подсказки первой ступени ставит код канваса вместе с быстрым слоем (WP21.5, `Т-§15.5` «Кто исполняет»);
- если подсказки в пространстве решения нет или ученик спросил в чате канваса, отвечает учитель обычным ходом — в чате, не в доске (WP21.5 п.4);
- строки канваса в разговоре учителя берутся из событий `canvas.line_committed` / `canvas.line_erased` / `handwriting.corrected` с номерами (WP14.3, WP21.6), не из документа доски.

Полей пометки и подсказки коду B не нужно. Если появится место, где ход учителя кладёт визуализацию или каркас на доску, это пробел ТЗ (WP21.1 называет «визуализации, каркасы» в слое учителя, но не называет писателя), и B вернёт вопрос, а не решит сам.

**D-A-110 п.4 (описание гипотезы заблуждения).** Отдельного правила не нужно, таблица A верна. `Т-§9.3`: новое заблуждение создаётся как `suspected`; описание (`statement`, `triggers`) пишется «при подтверждении и при каждом новом проявлении подтверждённого»; WP16.2: задача сначала читает статус и завершается, если заблуждение не подтверждено. Гипотеза словами подтверждённой быть не может, поэтому `misconception.describe` по ней всё равно завершилась бы сразу. После того как проектор создаст узел, следующие проявления ссылаются на его id (метка в контексте, `Т-§4.5`). Событие с `misconception_id`, которое переводит заблуждение в подтверждённое, само ставит `misconception.describe` по строке таблицы.

**D-A-110 п.7 (`agent.requested {kind: known_check}`).** Пишет код хода учителя (B, WP13.2, шаг `commit`) из `p3_markup.agentRequests` (`AgentRequestKind` включает `known_check`, WP16.6). Например, ученик сказал в чате «я это уже знаю», и учитель просит проверку темы (`params.topic`). Задача нужна, и это `check.known_claim`: в §6.5 вида `agent.known_check` нет, а `check.known_claim` делает ровно эту работу (1–3 задачи по навыкам темы через `task_generator`, итог — `agent.completed {kind: known_check, task_ids}`, WP8.2). Предлагаемая строка таблицы S6: `agent.requested`, `kind = known_check` → `check.known_claim`, класс P1 при `needed_in_session`, иначе P2 (WP16.6), `lock_key`/`coalesce_key` — как у строки `known.claimed`. Решение класса B для A (таблица S6 — файл A).

### D-B-145. Тело `check.model`: вход ролей вердикта, метки, порядок второго мнения и прочтения
- Класс: B
- WP / место: WP11.6, `quack/practice/model_check.py` (`verdict_role`, `verdict_inputs`, `check_with_model`, `record_verdict`)
- Контекст: `Т-§6.8`, `Т-§7.7`, `Т-§22.7` и контракты `verdict_free@1`, `verdict_proof@1`, `second_opinion@1` (#84) задают роли, блоки входа и выход. Не названо: (а) вид рубрики и списка заблуждений во входе; (б) рубрики нет ни в пространстве решения, ни в спецификации; (в) порядок второго мнения и понижения уверенности распознанного ответа (WP11.7); (г) ключи идемпотентности вызовов.
- Решение: (а) рубрика — `R1. …`, `R2. …` из ключевых шагов пространства решения, иначе из `key_ideas`; заблуждения — `M1. …` в порядке, который даёт вызывающий; `answer_spec` — JSON спецификации без пустых полей; ответ — `value` и рассуждение `text` с новой строки. (б) Пустой список — блок `none`, модель сверяет со спецификацией. (в) Решение о втором мнении — по уверенности самой модели; понижение по распознаванию (D-B-127) применяется последним: неуверенное прочтение лечится показом прочтения, а не второй моделью. (г) `check.model:{answer_seq}:{part}` и `second_opinion:{answer_seq}:{part}` — повтор задачи берёт ответ из исходящих шлюза. Неизвестная метка `R…`/`M…` возвращается модели через `check` шлюза (`Т-§4.5`); что осталось неизвестным после исправления, `model_verdict` отбрасывает. Регистрация обработчика `check.model` — с диспетчером Фазы 4 (A, WP7.1, G2): тело принимает `Db`, шлюз и факты памяти (`LevelFact` → `LevelState`, заблуждения навыков — `KnownMisconception`) от вызывающего.
- Почему: §0.4.3 п.2 (`Т-§6.8`: «рубрика — ключевые шаги … или ключевые идеи»; `Т-§7.7`: «при сомнении» — сомнении вердикта), п.5.
- Обратимость: одна функция входа и порядок двух строк.
- Тесты: `tests/practice/test_check_model.py`.
- Ответ A (#117, 2026-11-03): `MisconceptionFact.statement` обязателен (`Т-§9.3`). B строит `KnownMisconception` из среза: `known_misconceptions(facts, skill_ids=…)` — заблуждения проверяемых навыков в порядке среза (`quack/practice/model_check.py`).
- Требует подтверждения владельцем: нет
- Открыто: рисунок (`drawing`) идёт в `verdict_free` текстом `value`. Изображения в запросе шлюз пока не передаёт (`Т-§6.8` «Spark с изображением»); нужен вход-изображение в `generate_structured` (B, WP10.2) — до него ответ-рисунок проверяется по описанию. У `MisconceptionFact` (S4) нет формулировки заблуждения — для `KnownMisconception.statement` нужна она (вопрос A).

### D-B-146. Тело страховки `check.symbolic`: чтение `core.events` B и ответ `unsupported`
- Класс: B
- WP / место: WP11.3, `quack/practice/symbolic_job.py` (`insure_symbolic`), `quack/practice/repo.py`
- Контекст: WP11.3 — страховка ищет вердикт по `verdict:{answer_seq}:{part}`, иначе проверяет с `symbolic_background_timeout_ms` и пишет вердикт; «не разобралось или не набралось точек» — `check.failed`. A ответил на D-B-122: чтения `core.events` B пишет в своих модулях. Не названо: что пишет страховка, если compute ответил `unsupported` (нет структурной проверки вида, `Т-§6.6`), — в `CheckFailedReason` каталога такого значения нет.
- Решение: чтения по ключу и по `seq` — `quack/practice/repo.py` (параметры `%(name)s`, фильтр `learner_id`, `T-DB2`, `T-DB4`). `NotYet` (таймаут, compute недоступен, D-B-126) страховка пишет как `check.failed` с той же причиной под `check_failed:{answer_seq}:{part}`. `unsupported` → `NeedsModel`: страховка не пишет ничего, пока в каталоге нет причины, — модель такой ответ сейчас не проверит. **Вопрос A:** добавить `unsupported` в `CheckFailedReason` (`quack/events/values.py`); тогда страховка пишет `check.failed {reason: unsupported}`, и диспетчер ставит `check.model` (`Т-§6.6`: «нет её — проверка моделью»).
- Почему: §0.4.3 п.1 (без изменения каталога в чужом файле), п.3.
- Обратимость: одна ветка в `insure_symbolic`.
- Тесты: `tests/practice/test_check_symbolic_job.py`.
- Ответ A (#117, 2026-11-03): `unsupported` есть в `CheckFailedReason`. Страховка пишет `check.failed {reason: unsupported}` вместо «ничего не писать»; строка S6 `check.failed → check.model` отдаёт ответ модели (`Т-§6.6`).
- Требует подтверждения владельцем: нет

### D-B-144. Грамматика API пускает отношения — режим `interval` проходит проверку разметки
- Класс: B
- WP / место: WP12.3, WP11.5; `quack/practice/grammar.py` (`grammar_ok`, D-B-115)
- Контекст: D-B-137 (#84) убрал режим `interval` из контрактов промптов, потому что ответ-неравенство не проходил `validate_markup` (`expression_unparsed`): токен-грамматика API не пускала `<`, `>`. Грамматика compute (`compute/quack_compute/math/grammar.py`, `Т-§6.5`) эти токены пускает, и сравнение `interval` в compute есть (`_intervals`: два неравенства с одной переменной, `Т-§6.6`).
- Варианты: (1) те же токены отношений, что в compute; (2) оставить как есть, `interval` не предлагать.
- Решение: (1). В грамматику API добавлены токены `<`, `<=`, `>`, `>=` — ровно набор compute; Юникод (`≥`) и `!=` по-прежнему отказ. Объединения интервалов (`Union`, `Interval`) не добавлены: их нет и в compute.
- Почему: D-B-115 — грамматика API отказывает ровно в том, в чём откажет compute (§0.4.3 п.7, единообразие); `Т-§6.6` называет режим `interval`.
- Обратимость: одно регулярное выражение.
- Тесты: `tests/practice/test_grammar_relations.py`.
- Требует подтверждения владельцем: нет
- Следствие для промптов: режим `interval` можно вернуть в контракты `teacher@2` и `task_generator@1` (D-B-137) — с оговоркой «одно неравенство с одной переменной, без объединений».

### D-B-147. Запись действий WP13.1: намерение без текста, ответ на элемент-задание
- Класс: B
- WP / место: WP13.1, `quack/session/record.py` (`record_action`)
- Контекст: таблица WP13.1 задаёт событие каждого вида (`ACTION_EVENT_TYPES`). Не названо: (а) поле `text` у `message.sent` для намерения (в каталоге оно обязательное); (б) откуда взять задачу у `element_response`, если элемент — задание (в действии `task_id` нет); (в) ключ второго события того же действия.
- Решение: (а) намерение — `message.sent {text: "", intent, topic_id | hub_id}`: слов у кнопки нет (`Т-§12.7` «событие действия с намерением и темой»). (б) Задача — та, которую разметка хода поставила за `element_id` (`turn.committed.markup.tasks[].task_id`, WP2.2); нет такого хода или элемента — `LookupError` (роутер отвечает ошибкой). (в) `answer.submitted` за элементом пишется под `{idempotency_key}:answer` в той же транзакции, что `element.responded`, с мгновенным вердиктом (WP11.2). Ответ, `hint_request`, `solution_request` и `check_skip` ищут задачу ученика (`content.tasks` по `learner_id`); чужая или несуществующая — `LookupError`, ничего не пишется (ревью C #108).
- Почему: §0.4.3 п.1 (идемпотентность: повтор действия ничего не дублирует), п.3 (схемы S3 и S11 не меняются).
- Обратимость: одна функция.
- Тесты: `tests/session/test_record_action.py` (часть `T-TT1`). Проверено и на неверной реализации: подмена события у `hint_request` и отсутствие ответа за элементом — тесты падают.
- Требует подтверждения владельцем: нет

### D-B-151. Согласие на старте сессии: `require_terms` и тесты Фазы 3
- Класс: B
- WP / место: WP13.1, WP18.4 (D-C-112 п.3), `quack/session/router.py` (`POST /v1/sessions`), `tests/session/fixtures.py`
- Контекст: после #106 `quack.api.auth.require_terms` берёт сессию через `Depends(get_current_session)` (cookie Фазы 4). Маршрут старта Фазы 3 читает ученика по Bearer-токену; тесты Фазы 3 B входят только токеном, без cookie и согласия.
- Решение: `POST /v1/sessions` — `dependencies=[Depends(require_terms)]` (импорт из `quack.api.auth`, не из `quack.auth`, §4.1/T-A6). В фикстуре `session_app` тестов Фазы 3 зависимость подменена пустой: эти тесты проверяют ход и жизненный цикл, а не вход; ворота проверяет свой тест с cookie и согласием (`tests/session/test_session_terms.py`). e2e потока C (ученик из `System.register` без `consent.given`) падают на старте `403` — правка у C: `register` даёт `consent.given {kind: terms, version: terms_version}`; PR B вливается вместе с ней или после.
- Почему: §0.4.3 п.2 (WP13.1 «без согласия — `CONSENT_REQUIRED`», `Т-§19.4`), п.5.
- Обратимость: одна строка маршрута и одна строка фикстуры.
- Тесты: `tests/session/test_session_terms.py` (часть `T-TT1`): без согласия и со старой версией — `403 CONSENT_REQUIRED`, с текущей — `201`; без cookie — `401`.
- Требует подтверждения владельцем: нет

### D-B-148. Граф хода WP13.2: порты, причина повтора, блоки в потоке `token`
- Класс: B
- WP / место: WP13.2, WP13.4, WP13.5, `quack/session/turn_graph.py`; `quack/llm/tool_loop.py` (шаг и инструменты — публичные функции)
- Контекст: ТЗ называет узлы `admit → assemble → model ⇄ tools → guard → commit` и кадры §4.7. Не названо: (а) как граф получает хранилища; (б) форма причины возврата модели (D-B-129 п.б: «текст для модели собирает граф хода»); (в) как текстовые блоки `p2_body` идут в кадры `token {text}` (у кадра нет `block_id`); (г) кадр, если обрезанный ответ (`Т-§11.1`) пришёл после показанного текста; (д) где проверяется шапка; (е) отмена хода без Redis.
- Решение: (а) порты `TurnPorts`: `admit` (блокировка хода и допуск, роль), `assemble` (запрос с контекстом, входы правил, быстрый вердикт), `execute_tool`, `commit` (`turn.committed` с побочными событиями и манифестом), `emit` (кадры); реализуют тело маршрута `/actions` и сборка контекста WP14. Граф компилируется один раз, без точек сохранения. (б) Повтор — сообщение `developer` во входе: «Code rejected the previous attempt (<reason>: <detail>; …). Write the whole turn again.»; причины прошлых попыток сохраняются, выход инструментов прошлой попытки — нет (попытка начинается с контекста). (в) Текстовые блоки — подряд через пустую строку `\n\n`; элемент — кадр `element {block_id}`. (г) Обрезанный ответ — повтор с удвоенным лимитом (`Т-§11.1`); если текст уже показан — `reset {guard}` (в перечне `reset` только `spoiler | guard`). (д) Шапка — в узле `model` в момент закрытия `p1_head` (иначе тело уже стримилось бы); `guard` — правила разметки, проверка элементов (`validate_markup`, `Т-§5.3`) и маршрут повтора; после повторов — `settle_stance`, ход без разметки показывается с логом `guard_violation`, предложение со спойлером удаляет код. (е) `TurnSlots` — флаг в памяти процесса: новое действие ставит отмену прошлому ходу и ждёт его конца; канал `session:{id}:cancel` и `SET NX` — в порту `admit` вместе с телом маршрута.
- Не сделано в этом пункте (дальше по WP13): отдельный вызов «исправь разметку» (WP13.4 п.4, `T-TT5`), правило «опора» (S4 `REQUIRES`), «сгенерированное проверено» (WP12.5), запись через `Db` (`T-TT7`), встраивание в шлюз (бюджеты, учёт, допуск).
- Почему: §0.4.3 п.2 (`Т-§12.1`, `Т-§12.2`, `Т-§12.4`), п.5.
- Обратимость: один модуль; порты меняются вместе с телом маршрута.
- Тесты: `tests/session/test_turn_graph.py` (`T-TT2`, `T-TT3`, `T-TT4` без «опоры», `T-TT6`). Проверено на неверной реализации: 8 подмен (нет проверки шапки, тело до шапки показывается, нет буфера, нет отмены, нет проверки элементов, нет `settle_stance`, нет `reset` по разметке, нет удаления предложения) — каждая роняет хотя бы один тест.
- Ответы на ревью C #109 (2026-11-03): (ж) Точка невозврата — начало узла `commit`: отмена проверяется прямо перед записью (`TurnCancelled`, ничего не пишется) и во время стрима на каждом куске; транзакцию записи отмена не прерывает. Основание: WP13.2 «стримящий ход прерывается и освобождает блокировку», «обрыв … до commit — хода в журнале нет»: к началу записи текст уже показан целиком, а запись — одна транзакция (WP2.2). Новое действие ждёт конца записи (`TurnSlots` ждёт освобождения) и начинает свой ход после записанного; ход в журнале всегда целый (T-TT6, T-TT7). (з) Ожидание быстрого вердикта — не дольше `symbolic_inline_timeout_ms` (`Т-§6.1`: «перед вызовом модели он ждёт вердикт не дольше symbolic_inline_timeout_ms»; ожидание — в `check()` WP11.3, порт `assemble` его вызывает). Успел — кадр `verdict` сразу после `status: thinking` (`AssembledTurn.verdict`) и вердикт в контексте; не успел — ход идёт без вердикта (позиция не выше `formative`, правило «честный вердикт»), во время стрима вердикт прийти не может: «API пишет вердикт только в пределах своего ожидания, а задача запускается позже его конца» (`Т-§6.1`). Вердикт страховки — `verdict.recorded` после хода; в потоке этого хода кадра нет. Как клиент узнаёт о вердикте страховки («проверяю…» до прихода, `Т-§6.1`): в §4.8 вида сообщения для него нет (`verdict_revised` — только пересмотр, `Т-§5.6`); сейчас — `projection_advanced` (проектор применил `verdict.recorded`) и чтение ленты сессии. \[ПРОВЕРИТЬ\] — вопрос владельцу: нужен ли в §4.8 вид `verdict_recorded {answer_seq}`.
- Решено (2026-11-03): D-A-122 п.2 (#130) — вида `verdict_recorded` в §4.8 нет; клиент узнаёт о вердикте страховки по `jobs_changed` видимой задачи `check.symbolic` и `projection_advanced`, затем читает ленту сессии. B согласен (ревью #130).
- Требует подтверждения владельцем: нет

### D-B-149. Запись хода: пачка событий, ключи, задачи хода, дедлайн из разговора, подсказки и закрытие задачи
- Класс: B (п. г, д решены по поручению владельца 2026-11-03 после сверки с `Т-§4.8`, `Т-§7.4`, `Т-§2.2`, `П-2.3`, `П-2.9`, `П-8.1`)
- WP / место: WP2.2, WP13.3, WP13.9, `quack/session/turn_commit.py` (`turn_batch`, `commit_turn`); `quack/session/teacher_output.py` (`DeadlineFromTalk.quote`, `control_type`)
- Контекст: WP2.2 — `turn.committed` и порождённые события (`task.created`, `agent.requested`, `hub.deadline_set`, `hint.shown`, `task.closed`) одним `append_events`; WP13.9 — манифест вместе с `turn.committed`; WP13.3 — метки → id. Не названо: (а) порядок событий в пачке; (б) ключи идемпотентности; (в) семья, условие и проверка задачи, которую ход показывает; (г) дедлайн из разговора: откуда достоверность и как появляется новый хаб (у `hub.created` есть `kind`, `origin`, `control_type`, в разметке их нет); (д) когда ход пишет `hint.shown` и `task.closed` — в разметке `teacher@2` полей для них нет.
- Решение: (а) `task.created…` → `turn.committed` → `agent.requested…` → события дедлайна → `hint.shown` / `task.closed`: задача есть в журнале раньше хода, который на неё ссылается (`TurnTaskRecord.task_id`), последствия — после хода. (б) `turn:{manifest_id}`, `…:task:{element_id}`, `…:agent:{i}`, `…:deadline`, `…:hub`, `…:topics`, `…:hint`, `…:close`; повтор той же пачки ничего не дублирует (закон 5): события — по ключу, документ задачи — если его нет, манифест — `ON CONFLICT DO NOTHING`, шаг подсказки и закрытие считаются без событий своего же ключа. Ключ не от `action_seq`: новый ход по тому же действию после обрыва (`Т-§12.1`) даёт другой текст и конфликт хэша. (в) `task_id` — UUIDv4 сервера (§4.9), `family_id = task_id` (своя семья, как у сгенерированных Фазы 3); `variantOf` переводится в id, но семью не меняет \[ПРОВЕРИТЬ\]; условие — текст элемента (`prompt`, у каркаса — `text`), без `latex`; `source: teacher`, `verification: unchecked` (параллельная проверка — WP12.5).
  (г) Дедлайн хаба не обязателен (`П-2.3` «любому хабу можно назначить», `П-2.9`): объект `deadline` в разметке — только когда дедлайн прозвучал; хаб без дедлайна ход не создаёт. Достоверность (закон 6, `П-8.1` «фразой в чате… ученик упомянул»): модель пишет `quote` — слова ученика, дословно; код пишет события дедлайна, только если цитата (непустая) — подстрока сообщения ученика этой сессии (`message.sent`, как правило цитат извлекателя `quote_not_in_source`); у событий `source_refs = [{seq}]` этого сообщения. Цитаты нет — ни одного события дедлайна, `deadline = null` в записи хода, лог `turn_deadline_ungrounded`; неразборчивая дата — так же (`turn_deadline_bad_date`). Существующий хаб (`hub_label`) — только `hub.deadline_set`: его охват не трогается (у хаба из силлабуса источник надёжнее слов ученика, `Т-§4.8`; ученик правит охват `PATCH`). Новый хаб (`new_hub_name`; срез: «хабы создаются… под дедлайны», `П-2.3`) — `hub.created {kind: request, origin: learner, control_type}` (хаб из действия ученика, `П-2.3`), `hub.topics_changed` с охватом `coverage` и уровнями `control_type_levels[control_type]` (`Т-§8.5`), затем `hub.deadline_set`. `control_type` модель пишет, если ученик назвал вид контроля (задачи, коллоквиум с доказательствами, устная теория — `П-8.1`); не назвал — `no_goal` (уровень 3), ученик правит набор. Нет домена у сессии — хаб не создаётся (лог).
  (ж) `agentRequests {kind: known_check}` без метки темы не пишется (лог `turn_known_check_without_topic`): `check.known_claim` проверяет навыки темы (WP8.2), а правило S6 иначе получило бы `topic_id = None` (ревью B #117).
  (д) Код считает, модель не пишет (закон 2): по действию, на которое отвечает ход (`AnsweredAction`). `hint_request` → `hint.shown {step, source: teacher}`, шаг — следующий после показанных в этой сессии по задаче, не больше 3 (лестница из трёх ступеней `Т-§5`, `Т-§7.4`; та же выборка, что у помощи вердикта WP11.2); просьба выполняется всегда (`П-4.8`, `Т-§12.4` «правила не ограничивают просьбы ученика»). `solution_request` → `task.closed {solution_shown}`; `check_skip` → `task.closed {skipped}`; ответ, у которого к ходу все части верны (`solved` считает вызывающий по вердиктам) → `task.closed {solved}` (`Т-§2.2`: «задача решена, пропущена или пройдена через решение»). Уже закрытая задача второй раз не закрывается. Подсказки канваса (`source: canvas`) пишет код канваса (WP21), не ход. Просьба о подсказке после третьей ступени снова даёт `step: 3` — осознанно: лестница из трёх ступеней (`Т-§7.4`), просьба выполняется (`П-4.8`), а вердикт учитывает помощь по наибольшей ступени (WP11.2), так что повтор ничего не меняет в оценке (ревью C #109 п.5). Повтор записи — тем же объектом `TurnBatch` (докстринг `commit_turn`, ревью C п.6).
  (е) Ответ на ревью C #109: `deadline_at` без смещения — местное время ученика: его пояс из `core.learners.settings.timezone` (`Т-§14.6`: «дни… считаются в часовом поясе ученика… берётся из браузера при входе», WP17.6 `PATCH /v1/learner/settings {timezone}`); вызывающий передаёт `learner_timezone`. Смещение в строке — как есть. Пояса нет или он неизвестен — дедлайн не пишется (лог `turn_deadline_no_timezone`): дата в чужом поясе сдвинула бы день контроля (достоверность, п. г). До этого исправления дата без смещения переводилась в пояс сервера.
- Почему: §0.4.3 п.1 (идемпотентность), п.2 (WP2.2, WP13.9, `Т-§4.8`, `П-8.1`), п.5; закон 2 (шаг и закрытие считает код), закон 6 (дедлайн прослеживается до слов ученика).
- Обратимость: один модуль и два поля схемы `teacher@2` (расширение: `quote`, `control_type`); промпт описывает их (Клод промптов B).
- Тесты: `tests/session/test_turn_commit.py` (`T-TT7`, часть `T-EV4` потока B; дедлайн — часть `T-E9`). Проверено на неверной реализации: 6 подмен записи (без транзакции, манифест после транзакции, без `ON CONFLICT`, без документа задачи, без `hub.deadline_set`, порядок пачки) и 8 подмен правил (без проверки цитаты, пустая цитата как источник, шаг без потолка, шаг не считается, двойное закрытие, `solved` не закрывает, уровни не по виду контроля, без `source_refs`) — каждая роняет хотя бы один тест.
- Требует подтверждения владельцем: нет

### D-B-150. Вызов «исправь разметку под этот текст» (WP13.4 п.4)
- Класс: B
- WP / место: WP13.4 п.4, WP13.5 «каждое задание размечено», `quack/session/turn_graph.py` (`MarkupFix`, `TurnPorts.fix_markup`, узел `guard`)
- Контекст: `Т-§12.2` этап 4 и WP13.4 п.4: неверная разметка — перегенерируется только разметка отдельным вызовом, текст остаётся; T-TT5: после повторов элемент убран. Не названо: (а) сколько вызовов; (б) что идёт во вход; (в) роль, промпт и схема вызова — в реестре промптов (`SYSTEM_PROMPTS_PHASE4_REGISTRY.md` §5.1) отдельной роли нет.
- Решение: (а) до `guard_max_retries` вызовов на элемент (тот же предел повторов, что у правил хода, `Т-§12.1`); исправление проверяется тем же `validate_markup`; исправление для другого `element_id` не принимается; нет исправления или оно всё ещё неверно — элемент убирается вместе с заданием и блоком, лог `guard_violation`. Тело не стримится заново, кадра `reset` нет. (б) `MarkupFix {element, task (как написала модель, или null), problems (правила `validate_markup`), text (показанный текст тела), attempt}`. (в) Граф зовёт порт; сам вызов модели — со встраиванием графа в шлюз: роль `teacher` той же версии промпта (`teacher@2`, `Т-§11.6`), выход — одна `TurnTask` (`p3_markup.tasks[]`), причина и текст — в хвосте контекста, как причина возврата `guard` (реестр §5.1 «Хвост»). Текст инструкции для этого вызова — у Клода промптов B.
- Почему: §0.4.3 п.2 (`Т-§12.2`, WP13.4), п.5.
- Ревью C #109: (1) задача, у которой разметки не было вовсе, после исправления остаётся в записи хода (`tasks` итога — все исправленные и прошедшие, в порядке появления); (2) отмена ждётся наравне с каждым шагом без текста — шаг модели, инструменты, вызов исправления (`_cancellable`: новое действие прерывает шаг сразу, слот модели освобождается, хода в журнале нет).
- Обратимость: одна функция графа и порт.
- Тесты: `tests/session/test_turn_graph.py` (`T-TT5`; по ревью C — `test_markup_fix_of_a_task_without_markup_is_committed`, `test_cancel_during_a_tool_call_stops_the_turn`, `test_cancel_before_a_markup_fix_writes_nothing`): исправление принято, текст тот же, один вызов стрима; все исправления неверны — элемент убран после `guard_max_retries`; исправление чужого элемента не принято. Проверено на неверной реализации: 5 подмен (нет вызова, нет повторной проверки, не сверяется `element_id`, исправление не сохраняется, лишний вызов) — каждая роняет 1–2 теста.
- Требует подтверждения владельцем: нет

### D-B-152. Стриминг хода через шлюз (WP10.2–10.3 «встраивание в шлюз — с WP13»)
- Класс: B
- WP / место: WP10.2, WP10.3, WP13.2 узел `model`; `quack/llm/gateway.py` (`LLMGateway.stream_request`, `LLMGateway.streaming`, `_GatewayStream`); `quack/llm/tool_loop.py` (`stream_step` закрывает поток)
- Контекст: WP10: «всё, что сделано в Фазе 3 (шлюз на процесс, семафоры по моделям, флаг давления, повторы, размыкатель, `teacher_fallback`, …), остаётся. Пакет добавляет недостающее». Граф хода (D-B-148) зовёт провайдера S13 напрямую. Не названо: (а) как потоковый шаг проходит допуск, размыкатель и учёт; (б) какие сбои повторяются, если ученик мог уже видеть текст; (в) куда пишется учёт до WP10.4.
- Решение: (а) `stream_request` строит первый шаг так же, как `generate_structured` (промпт роли, строгая схема в `text.format`, модель, `effort`, лимиты, `prompt_cache_key = роль@N`, инструменты); `streaming` даёт графу `StreamingLLMProvider`: каждый шаг проходит размыкатель и слот модели (учитель ждёт не дольше `teacher_admission_wait_ms`, затем `LLMRateLimitedError` — у вызывающего `teacher_fallback`, WP5.3 Фазы 3), получает строку учёта, обновляет размыкатель и флаг давления по заголовкам. Таймаут роли — на ожидание следующего события, не на время обработки кадров графом. (б) До первого события шага: недоступность и таймаут повторяются как в `_attempts` (`llm_retry_attempts`); 429 горячего пути не повторяется (флаг давления, ошибка); 400 — ошибка. После первого события не повторяется ничего: ход падает до записи (`Т-§12.1`). Обрезанный шаг передаётся графу (учёт `incomplete`), граф повторяет его с удвоенным лимитом (`Т-§11.1`). Остановленный графом шаг (шапка отклонена, спойлер) закрывает поток сразу (`stream_step`, `aclose`), слот освобождается. (в) Учёт — тот же `_ledger` шлюза, что у всех вызовов (`turn_id` = `manifest_id` хода); перевод всего учёта на `ops.llm_usage` в долларах — WP10.4 одним шагом для всех вызовов.
- Почему: §0.4.3 п.2 (WP10, `Т-§11.3`, `Т-§11.5`, `Т-§12.1`), п.3 (подписи Фазы 3 не меняются, `run_tool_loop` — то же поведение), п.5.
- Обратимость: два метода шлюза и один класс.
- Тесты: `tests/llm/test_gateway_stream.py` (11). Проверено на неверной реализации: 7 подмен — без повтора, повтор после первого события, повтор 429, без слота модели, обрыв как `ok`, без учёта, без размыкателя — каждая роняет 1–3 теста; восьмая — без закрытия потока в `stream_step` — тестом не различима (CPython закрывает брошенный генератор сразу), оставлено как защита.
- Требует подтверждения владельцем: нет

### D-B-153. Правило «не перепрыгивать границу» в шапке хода
- Класс: B
- WP / место: WP13.5, `Т-§12.4`; `quack/session/guards.py` (`Boundary`, `head_violations(boundary=…)`), `quack/session/turn_graph.py` (`GuardInputs.boundary`)
- Контекст: таблица WP13.5: «у каждого навыка `targetSkills` все предпосылки по `REQUIRES` с `confidence ≥ requires_min_confidence` имеют показ или свидетельство (с наложением и показами этого хода); по неуверенным связям — предупреждение в контекст»; когда — шапка; после повторов — ход показывается, случай в журнал (`guard_violation`). T-TT4: нарушение → повтор до показа тела. Не названо: что такое «показы этого хода» в момент шапки — `exposures` приходят позже, в `p3_markup`.
- Решение: в момент шапки «показ этого хода» — предпосылка, которую ход сам берёт в `targetSkills` (ход работает и над ней); показы и свидетельства прошлого и сессии — `RequiresLink.has_exposure` / `has_evidence` среза S4, который читается с наложением сессии (`Т-§2.6`). Порог — `>= requires_min_confidence`. Нарушение — одно `boundary_jump` с перечнем по меткам («S1 needs S2 and 1 unlabeled first»; предпосылка без метки только считается, id модели не показываются, `Т-§4.5`); повтор до показа тела; после повторов — ход показывается, лог `guard_violation`. Неуверенные связи — не правило, а строка в контексте (сборка WP14). Входы правила (`Boundary`: связи по метке цели, метка → id, порог) собирает порт `assemble` из среза.
- Почему: §0.4.3 п.2 (WP13.5, `Т-§12.4`), п.5.
- Обратимость: одна функция и одно поле входов.
- Тесты: `tests/session/test_head_guards.py` (часть `T-TT4`: порог, показ, свидетельство, неуверенная связь, предпосылка — цель хода, текст по меткам), `tests/session/test_turn_graph.py` (повтор до тела; после повторов — показ и лог). Проверено на неверной реализации: 6 подмен — каждая роняет 1–2 теста.
- Требует подтверждения владельцем: нет

### D-B-154. Документ плана сессии при записи хода (WP13.6)
- Класс: B
- WP / место: WP13.6, `Т-§12.5`; `quack/session/turn_commit.py` (`commit_turn`, `_UPSERT_PLAN`)
- Контекст: WP13.6 «`planUpdate` в каждом ходе; документ `content.session_plans` ведёт API при записи хода: сделанные шаги, текущий шаг, одна-две гипотезы следующего, ответвления с типом». Не названо: (а) заменяет ли ход документ или дописывает; (б) `status`; (в) что если строка плана — другого ученика.
- Решение: (а) документ — `planUpdate` последнего записанного хода, в id (`PlanUpdateRecord`: шаги, текущий, 1–2 следующих, ответвления, `cancel_request_ids`): модель видит текущий план в слоте 6 и возвращает обновлённый (`Т-§12.5`); кроме сделанных шагов — они «неизменная история» (`П-4.6`): код сохраняет сделанные шаги документа по порядку и только дописывает новые из хода (строка плана читается `FOR UPDATE` в той же транзакции). Выводов модели код не склеивает (закон 10): история шагов — факт, а не вывод. Пишется в транзакции записи хода (ход не записан — план не тронут). (б) `status = active` (план, подготовленный «Сегодня» — `prepared`, WP17; первый ход его подхватывает). (в) Строка плана сессии другого ученика не меняется: `ON CONFLICT … WHERE learner_id` — строки нет, `LookupError`, ход не пишется (изоляция `Т-§19`).
- Не в этом пункте: план хаба на несколько сессий (`content.study_plans`, порядок по `REQUIRES` со сжатыми SCC через `networkx` — библиотеки ещё нет в `pyproject.toml`, §4.6 её называет); отмена поручений `planUpdate.cancel_requests` → `cancel_requested` (WP7.4, рантайм задач A); `GET /v1/sessions/{id}/plan` — с маршрутами сессии.
- Почему: §0.4.3 п.2 (WP13.6, `Т-§12.5`), п.5; закон 10.
- Обратимость: одна инструкция SQL в транзакции записи.
- Тесты: `tests/session/test_turn_commit.py` (часть `T-TT9`): план последнего хода, повтор, неудачная запись план не трогает, чужой план не перезаписан. Проверено на неверной реализации: 3 подмены — каждая роняет тест. История шагов — `test_completed_steps_are_history` (красный `fdb7955`: шаг стёрт ходом).
- Не решено \[ПРОВЕРИТЬ\] — вопрос владельцу: план хаба на несколько сессий (`content.study_plans`): кто и когда его строит (в разметке `teacher@2` полей для него нет; «учитель держит грубый план», `П-4.6`) и что такое «порция» (размер, единица — тема, компонента SCC?). До ответа `study_plans` не пишется.
- Решено (2026-11-03): D-A-122 п.1 (#130) — `content.study_plans` строит код при старте сессии с целью-хабом (и при записи хода, если плана нет): порция — вершина графа `REQUIRES` тем хаба со сжатыми SCC (`graph.read.topic_requires`, S4), порядок — топологический; модель план хаба не пишет. Согласуется с WP13.6 и `Т-§4.6`. Реализация — B (WP13.6), отдельным пунктом.
- Требует подтверждения владельцем: нет

### D-B-166. Пространство решения задачи (WP12.4)
- Класс: B
- WP / место: WP12.4, `Т-§5.5`, `Т-§11.1`, `T-TK5`; `quack/practice/solution_space.py` (`build_solution_space`), `quack/config.py` (`RoleConfig.hard_model`, `model_for_role(hard=)`), `quack/llm/gateway.py` (`generate_structured(hard=)`)
- Контекст: WP12.4 — роль `solution_space`, Spark; Wonder — для ответа `proof` и уровня 6; хранится в документе задачи с версией. `Т-§11.1`: строка «solution\_space — Spark, для сложных — Wonder», так же `task_generator`; ролей в таблице 26 (`T-AI1`), отдельной роли «глубокой» нет. Не названы: как одна роль зовёт две модели; что хранить на месте метки заблуждения неверной ветки (метки живут один вызов, `Т-§4.5`); откуда уровни задачи (в `content.tasks` разметки нет); что делать при двух задачах на одну задачу (правило банка и запрос канваса).
- Решение: (а) у строки роли — `hard_model` (Wonder для `solution_space`, `task_generator`); `generate_structured(hard=True)` зовёт его, учёт, лимиты и outbox остаются у роли; роли без `hard_model` флаг не меняет. Новой роли нет — таблица `Т-§11.1` цела. (б) Сложная — ответ `proof` или уровень 6 в `tests` разметки; разметка читается из `task.created` по `created_seq` документа. (в) Заблуждение неверной ветки хранится его id (строкой) вместо метки; метка, неизвестная после повтора, — null. (г) Версия — версия промпта (`solution_space@1`); ключ вызова `solution_space:{task_id}:{prompt}`; запись — `UPDATE … WHERE solution_space IS NULL`: первое сохранённое пространство остаётся, повторная задача ничего не строит. (д) Пространство — содержимое документа задачи, не память ученика: события нет (закон 1 о модели ученика). (е) Имена полей JSON `content.tasks.solution_space` (`key_steps[].statement`, `key_steps[].expression {latex, sympy}`, `wrong_branches[].sign|gap_type|misconception`, `hints`) — шов с C: канвас WP21.4 читает их своим SQL; переименование — только с сообщением C (ревью C #144).
- Почему: §0.4.3 п.2 (WP12.4, `Т-§5.5`, `Т-§11.1`), п.5.
- Обратимость: один модуль и поле строки роли.
- Тесты: `T-TK5` — `tests/practice/test_solution_space.py` (6); `tests/test_llm_runtime.py::test_hard_call_takes_the_hard_model`.
- Не решено \[ПРОВЕРИТЬ\]: `model_for_role` берёт модель из `llm_role_models` (Фаза 2), а не из `llm_roles[role].model` — модель строки таблицы на вызов не влияет (кроме `hard_model`); провести через WP10 (G4). Обработчики `task.solution_space`/`agent.solution_space` — после чтения заблуждений навыка из S4 и источника эталонного решения.
- Требует подтверждения владельцем: нет

### D-B-167. Проверка задачи учителя после хода (WP12.5, строка «учитель на лету»)
- Класс: B
- WP / место: WP12.5, `Т-§5.6`, `T-TK6`; `quack/practice/task_check.py` (`check_teacher_task`)
- Контекст: `Т-§5.6`: Spark решает задачу независимо и оценивает разметку (метод назван, вариант, уровень); ответ сравнивается тем же кодом, что ответы учеников; расхождение — `task.key_disputed` или `task.markup_disputed`, неверная разметка исправляется пересвёрткой. Не названы: что делать, если код не может решить ключ (медленный тип, compute недоступен, ответ не разобран); что идёт первым при обоих расхождениях; какой вид у исправленной разметки; ключ события.
- Решение: (а) ответ проверяющего — `LearnerAnswer` в типе спецификации, `check()` с `symbolic_background_timeout_ms`; ключ оспорен только при вердикте `incorrect`; иной исход (`partial`, `unclear`, `NotYet`, `CheckFailed`, `NeedsModel`) ключ не оспаривает — судится только разметка. (б) Ключ первым: при обоих расхождениях — `task.key_disputed` с исправленной разметкой. (в) Исправление: `method_named` — по проверяющему, неподходящие уровни убираются, `variant_of` снимается, если «не вариант»; не подошёл ни один уровень — `tests` остаются (иначе разметка без пар). (г) Вход вслепую: условие, варианты без признака верного, заявленная разметка с именами навыков, условие исходной задачи для варианта. (д) Ключ события `task_check:{task_id}`, ключ вызова — с версией промпта; актор — `task_checker@1`; `causation_seq` — событие хода. (е) Строка «сгенерированная» — `verify_generated`: та же слепая проверка до банка; расхождение → `task_arbiter` (Wonder) с решением генератора, ответом проверяющего и словами расхождения; арбитр снова расходится (ключ или разметка) — `False`, черновик выбрасывается; ключи вызовов — `{ключ черновика}:{промпт}`.
- Почему: §0.4.3 п.2 (WP12.5, `Т-§5.6`), п.5.
- Обратимость: один модуль.
- Тесты: `T-TK6` — `tests/practice/test_task_check.py` (4).
- Не решено \[ПРОВЕРИТЬ\]: строка «семинар» — с разбором материала (C); обработчик задачи `task.check` (`{task_ids}` правила `turn.committed`) — при сборке входов (имена навыков из S4).
- Требует подтверждения владельцем: нет

### D-B-157. Картина памяти `renderer@1` (WP14.2)
- Класс: B
- WP / место: WP14.2, `Т-§10.2`, `Т-§8.2`–`Т-§8.7`; `quack/session/memory_picture.py`
- Контекст: WP14.2 задаёт состав, правила и эталон строки `Т-§10.2`. Не названы: слова статусов и уровней на английском (`Т-§22.2`); что такое «уровень ниже нужного» у предпосылки; как отличить навык другого домена «с разрывом переноса»; что видно, если у навыка нет целевого набора. В срезе S4 нет `gapTypeConfidence` (`Т-§8.4`), условий проявления заблуждения (`misconception.described.triggers`) и времени провала памяти («забыл», `Т-§8.2` п.3).
- Решение: (а) уровни — названия доктрины `teacher@2` (шаг 1), типы пробела — её шаг 3; статусы: proven — «proven», unstable — «not always», failed — «failed», implied — «follows from a proven level, not checked on its own», not_tested — «not checked»; база transfer — «through another domain». (б) Решающий факт — исход, когда («today», «yesterday», «N days ago»), источник словами по `Т-§8.7` («checked by code», «judged by a model», «from the conversation»), цитата, пометка «(deciding)»; объём — «1 time» / «N times in all». Вероятностей, весов, ярусов в тексте нет. (в) Отбор по порядку WP14.2: навыки задачи (`SliceFocus.skill_ids`), навыки тем цели (`topic_ids`), предпосылки по `RequiresLink` на 1–2 шага, если хоть один уровень их целевого набора не proven/implied или о них ничего не известно, навыки другого домена (`domain_id` не домен фокуса) с доказанным уровнем — связь `SAME_IDEA` отбирает срез A. Навык без имени не попадает; лимит `memory_skills_max`. (г) Целевой набор — вход `target_levels` (из хаба, `Т-§8.5`); без него — уровни, по которым есть данные; данных нет — «Not checked yet.». (д) Удержание: «due for review» — `due_at_ms` не позже текущего времени; «shown minutes ago, still fresh in mind» — последний показ не раньше `hot_window_min` (`Т-§8.4`); срока нет — «no checks after a delay yet». (е) Тип пробела — только при `gap_type_confidence >= gap_type_min_confidence` (D-B-102); уверенность — вход по навыку, без неё тип не показывается. (ж) Корни ошибок — все `RootCauseFact` среза (порог «несколько гипотез» — у A, `Т-§9.4`); сводка аналитика, готовые результаты агентов, неисполненные поправки (номер ответа и текст) — строками в конце. (з) Срез не прочитан — `memory temporarily unavailable` (слово в слово из `teacher@2`).
- Почему: §0.4.3 п.2 (WP14.2, `Т-§10.2`, `Т-§8.2`–`Т-§8.7`, доктрина `teacher@2`), п.5.
- Обратимость: слова и порядок — в одном модуле.
- Тесты: `T-CX2` — `tests/session/test_teacher_context.py::test_memory_picture_*`.
- Решено (было \[ПРОВЕРИТЬ\] с A): поля S4 D-A-126 (#136) — тип пробела показывается по `SkillFact.gap_type_confidence` (не ниже `gap_type_min_confidence`), «forgot after a delay …» — по `SkillFact.last_lapse_at_ms`, условия проявления — «shows when …» по `MisconceptionFact.triggers`; без поля — ничего (закон 8). Тест — `test_memory_picture_on_the_s4_stub` на `FixedGraphReader`.
- Требует подтверждения владельцем: нет

### D-B-158. Разговор и строки эпизодов (WP14.3)
- Класс: B
- WP / место: WP14.3, `Т-§10.3`, `Т-§15.6`; `quack/session/conversation.py`
- Контекст: WP14.3 велит переиспользовать функции Фазы 3 WP13; `checkpoint_render` читает события Фазы 3 (`turn.student_replied`), в Фазе 4 ход — `turn.committed`. Не названы: границы хода в журнале; номера строк канваса (`line_id` — строка, метки `L` в `LabelMap` — по UUID).
- Решение: (а) ход — `turn.committed`, события до него после прошлого хода и события его записи (`causation_seq` = `action_seq`, WP2.2); события после последнего хода — текущее действие ученика, в конце слота («Current action»). (б) Свёртка — `collapsed_turn_count` Фазы 3; номер хода — с начала сессии. (в) Строка эпизода — «Turn N, <intent>: …»: показы, выданные задачи с уровнями, вердикты, подсказки, закрытие задач, «непонятно», число строк канваса. (г) Ход целиком: действие ученика, ответ и вердикт (код или модель), текст учителя, элементы (вид, метка задачи), подсказки, закрытие задачи; строки канваса — «Canvas line N», номер по порядку в сессии, при свёртке не сдвигается. (д) Объекты — метками вызова; без метки — «(earlier task)»; id в слот не попадают.
- Почему: §0.4.3 п.2 (WP14.3, `Т-§10.3`), закон 10 (строки кодом).
- Обратимость: один модуль.
- Тесты: `T-CX3` — `tests/session/test_teacher_context.py::test_conversation_*`.
- Не решено \[ПРОВЕРИТЬ\]: метки пересобираются каждый вызов (`Т-§4.5`), а слот 5 на них ссылается — при смене порядка меток начало разговора меняется и кэш (`Т-§10.1`) обрывается; порядок меток задаёт сборка контекста (следующий шаг WP14). Номера строк канваса и метки `L` — при инструменте `voice_at_line` (WP14.4).
- Требует подтверждения владельцем: нет

### D-B-159. Слоты учителя, заморозка слотов 3–4, хвост (WP14.1)
- Класс: B
- WP / место: WP14.1, `Т-§10.1`, `Т-§17.8`, `Т-§19.8`; `quack/session/teacher_context.py`
- Контекст: WP14.1 задаёт порядок и заморозку; формат строк слотов, источник «изменения посреди сессии» и место причины возврата `guard` (реестр промптов: «место в документах не названо») не заданы.
- Решение: (а) переменные `teacher@2` в порядке `Т-§10.1` (`SLOT_VARIABLES`), слот 1 — текст промпта; слот 2 — инструменты `quack.session.tools.TOOLS` и виды элементов `Т-§12.3`, меняется только с выпуском. (б) Слоты 3–4 — отрисовка снимка на начало сессии (`LearnerState`); строки текущего состояния, которых нет в снимке, — в хвост «Changed during this session: …» до конца сессии. (в) Профиль: `preference.set` по ключу (порядок ключей), выученное и манера (`digest.written`); цели: хаб (метка, имя, дедлайн в поясе ученика, тип контроля), темы с требуемыми уровнями словами доктрины. (г) Слот 6 — намерение с целью и документ `content.session_plans` (сделано, текущий шаг, следующие, ответвления); режим подготовки — WP17.4, не здесь. (д) Слот 8 — кусок цитатой «> …» с источником «From the material "<имя>", page N» (`Т-§19.8`). (е) Слот 9 — все метки вызова с именем, без id. (ж) Скилл `/имя` — текст в хвост только этого хода. (з) `guard_feedback` в первой попытке пуст; причина повтора пока — сообщение `developer` графа (D-B-148 п.б).
- Почему: §0.4.3 п.2 (WP14.1, `Т-§10.1`), п.5.
- Обратимость: один модуль.
- Тесты: `T-CX1`, `T-CX6` — `tests/session/test_teacher_context.py`.
- Не решено \[ПРОВЕРИТЬ\]: перенос причины повтора из сообщения `developer` в `{{ guard_feedback }}` (промпт `teacher@2` ждёт её там) — при подключении сборки к графу хода.
- Требует подтверждения владельцем: нет

### D-B-160. WP14.1–14.3 раньше WP15 — отступ от порядка §7.1
- Класс: B
- WP / место: §7.1 (порядок B: … WP13 → WP15 [G2] → WP14 → …), §0.4.7 п.1; #131
- Контекст: по §7.1 и §0.4.7 после WP13 идёт WP15. WP15 п.4–5 (проверка элементов `extractor@3`, запись `ops.coverage`) ждали сигнатур A, и B сделал отрисовку контекста WP14.1–14.3 (#131) — она ни от кого не зависит. A указал на порядок (сообщение A->B, 2026-11-03).
- Решение: отступ признаётся и закрывается: #131 остаётся открытым на ревью (WP14 — пакет G3, на G2 не влияет); B возвращается к порядку — WP15: сначала части, не зависящие от A (окно эпизода, вход, вызов роли `extractor@3`, п.1–3), затем п.4–5 на сигнатурах ветки A `phase-4-a-wp15-seams`. Остаток WP14 (WP14.4–14.5, сборка входов) — после WP15.
- Почему: §0.4.7 п.1, §7.1; правило «не ждать гейт» (§7.1) — WP15 идёт на сигнатурах швов, а не после них.
- Обратимость: порядок работ, кода не касается.
- Тесты: нет.
- Требует подтверждения владельцем: нет

### D-B-161. График на доске канваса — модель ядра `PlotSpecRecord` (ответ C по #116, п.5)
- Класс: B
- WP / место: WP21.1, WP21.5, §4.1; `quack.events.records.PlotSpecRecord`
- Контекст: C (D-C-118): простой график (`PlotSpec` в `quack.session.elements`, зона B) канвас импортировать не может (§4.1); вынести модель в ядро или класть график артефактом — решение B к WP21.5.
- Решение: выносить ничего не нужно — модель графика уже в ядре: `PlotSpecRecord` (S3, `quack/events/records.py`; её несёт `VisualizationElementRecord.plot`). Канвас импортирует `quack.events` (ядро, §4.1). Простой график на доске — визуализация слоя учителя с `plot: PlotSpecRecord` вместо `artifact_id` (одно из двух) — правка `BoardDoc` у C в WP21.5.
- Почему: §0.4.3 п.2 (одна модель на объект, S3 заморожен — новой модели не нужно); §4.1.
- Обратимость: поле документа доски.
- Тесты: у C (WP21.5).
- Требует подтверждения владельцем: нет

### D-B-163. Инструменты памяти учителя (WP14.4)
- Класс: B
- WP / место: WP14.4, `Т-§10.4`, `Т-§11.5`, §4.4; `quack/session/tools.py` (`run_tool`, `ToolContext`), `quack/session/conversation.py`
- Контекст: WP14.4 — шесть инструментов, лимиты (D-B-103), метки вместо id, Modal недоступен — «поиск временно недоступен». Не названы: метки свидетельств и кусков, которых нет в таблице вызова; метки строк канваса (`line_id` — строка, `LabelMap` — по UUID, D-B-158 [ПРОВЕРИТЬ]); «другой домен» у похожих ошибок; поля «помощь» в `EvidenceFact` S4 нет.
- Решение: (а) каждый инструмент читает через `quack.graph.read` (S4) и режет по `memory_tool_limits`; метки навыков и задач — из таблицы вызова; свидетельства и куски, показанные инструментом, получают метки `E…`/`P…` на этот ход (`ToolContext.evidence`, `chunks`) — следующий вызов того же хода может их назвать (`similar_mistakes(evidence)`). (б) Строки канваса — метки `L{N}` по порядку в сессии, те же, что в слоте 5 («Canvas line L1»; закрывает D-B-158 п. о строках); `voice_at_line(L…)` — отрезки голоса, пересекающиеся по времени со строкой. (в) «Another subject» — домен ошибки не домен сессии (`ToolContext.domain_id`). (г) `search_materials` — порт поиска (шов S12, C); `None` — Modal недоступен → `search temporarily unavailable`, так же `similar_mistakes(text)`; куски — тем же видом, что слот 8 (`MaterialChunk`, `render_chunks` перенесены в `tools.py`). (д) Предпосылки — уровни словами, связь ниже `requires_min_confidence` — «(uncertain link)», без чисел. (е) Неизвестный инструмент, неверный JSON, неизвестная метка, два аргумента «ровно один» — текст `error: …`, ход идёт дальше (`Т-§11.5`).
- Почему: §0.4.3 п.2 (WP14.4, `Т-§10.4`), п.5.
- Обратимость: один модуль.
- Тесты: `T-CX4` — `tests/session/test_memory_tools.py`; `T-CX3` обновлён под `L{N}`.
- Решено (было \[ПРОВЕРИТЬ\] с A): помощь — `EvidenceFact.assistance`, `hint_step` (D-A-127, #141); `skill_history` пишет «after hint step N of 3» / «after the solution was shown» между источником и датой (`Т-§10.4`). Тест — `test_skill_history_help_on_the_s4_stub` на `FixedGraphReader`.
- Не решено \[ПРОВЕРИТЬ\]: Подключение порта поиска к `quack/graph/read/materials.py` (C) — при сборке входов WP14. `similar_mistakes(text)` берёт признак «Modal недоступен» из порта поиска материалов (`search is None`): эмбеддинг текста — тот же Modal; если `GraphReader.similar_mistakes` получит свой эмбеддинг, условия развести (ревью C в #137). Порт поиска — `functools.partial(search_chunks, db, embeddings, learner_id=...)` по сигнатуре C (WP20.4, #137).
- Требует подтверждения владельцем: нет

### D-B-164. Бюджет контекста учителя (WP14.5)
- Класс: B
- WP / место: WP14.5, `Т-§10.1`; `quack/session/teacher_context.py` (`estimate_tokens`, `fit_budget`, `TRIM_ORDER`)
- Контекст: WP14.5 — `teacher_context_target_tokens`, `teacher_context_max_tokens`; «урезаются сначала слоты 7–9, разговор — последним»; оценка — `len / 4`. Не названы: когда урезать (выше цели или выше потолка), порядок внутри 7–9, что режется в слоте.
- Решение: (а) урезается только сверх потолка `teacher_context_max_tokens` (`Т-§10.1`: 30 тыс. — «целевой размер», ориентир для кэша и стоимости; потолок — граница); цель меряется по `usage` (WP10.4). (б) Порядок: слот 8 (куски — их можно дозапросить `search_materials`), слот 7 (картина памяти — последние строки наименее важны, порядок отбора WP14.2), слот 9 (метки — без них модель теряет имена, поэтому последним из трёх); строки снимаются с конца слота. (в) Затем разговор — с самых старых строк; блок текущего действия ученика («Current action») не режется. Слоты 1–4, 6 и хвост не режутся. (г) Оценка — `len(text) // 4` всего отрисованного промпта.
- Почему: §0.4.3 п.2 (WP14.5, `Т-§10.1`), п.5.
- Обратимость: порядок — одна константа.
- Тесты: `T-CX5` — `tests/session/test_teacher_context.py::test_context_budget_trim`.
- Требует подтверждения владельцем: нет

### D-B-155. Жизненный цикл сессии на `Db` (WP13.10, библиотечная часть)
- Класс: B (п. е — C: каталог S3 заморожен, D-A-119)
- WP / место: WP13.10, `П-4.10`, `Т-§2.2`, `Т-§22.9`; `quack/session/sessions.py`
- Контекст: WP13.10 — `POST /v1/sessions` (домен; намерение; тема, хаб или пункт «Сегодня»), `…/heartbeat`, `…/end` (`session.ended`), сборщик неактивных, возврат в сессию после сборщика (`session.started {session_id, resumed: true}`), лента и история. Маршруты Фазы 3 (`lifecycle.py`) — на журнале и таблицах Фазы 3. Не названо: (а) перечень намерений старта; (б) что делает новый старт с открытой сессией; (в) `reason` у `session.ended`; (г) ключи событий; (д) что, если сессию закрыл сам ученик; (е) поле `resumed` — в каталоге у `session.started` его нет.
- Решение: (а) `learn | practice | review` (кнопки `Т-§12.7`, как у действия `intent`) и `free` — свободный запрос («ведёт так же, как при свободном запросе», `Т-§12.7`). Тема или хаб — не оба. Пункт «Сегодня» клиент передаёт его темой или хабом; связь с подготовленным планом (`content.session_plans` `prepared`) — с WP17 \[ПРОВЕРИТЬ\]. (б) Новый старт открытую сессию не закрывает: сессия остаётся открытой для возврата (`П-4.10`); события `session.abandoned` Фазы 3 в каталоге Ф4 нет. (в) `learner` — `POST …/end`; `inactive` — сборщик (`session_inactivity_timeout_min`, Фаза 3). (г) Старт — `session.start:{idempotency_key}` (повтор с теми же параметрами — та же сессия, с другими — `SessionConflictError` → `409 IDEMPOTENCY_CONFLICT`); конец — `session.end:{session_id}:{seq открытия}`; возврат — `session.resume:{seq конца}`. `session.started` и строка `content.sessions` — одна транзакция; конец и возврат — в транзакции со строкой `FOR UPDATE`. (д) Возвращается только сессия, закрытая сборщиком: WP13.10 говорит о ней одной; закрытая учеником остаётся закрытой. (е) Возврат пишет `session.started` той же сессии с полным payload без `resumed`: возврат распознаётся как второе `session.started` того же `session_id` в журнале. Нужно поле `resumed: bool = False` в `SessionStarted` — по D-A-119 только новой версией схемы с записью класса C и согласием трёх потоков; вопрос A.
- Не в этом пункте: маршруты (`POST /v1/sessions` и др.) переходят на эту библиотеку после D-C-119 (C переводит весь API на сессии, тесты `tests/session` — у C); выборка сборщика по всем ученикам (запрос через учеников требует строки в `CROSS_LEARNER_ALLOWED`, `tests/db/test_sql_static.py`, зона A) и регистрация задачи `session.sweep_inactive` — с рантаймом A (D-A-116); вызов `resume_if_swept` из записи действия — с телом `/actions`.
- Почему: §0.4.3 п.1 (идемпотентность), п.2 (WP13.10, `П-4.10`), п.5.
- Обратимость: один модуль.
- Тесты: `tests/session/test_sessions_db.py` (10). Проверено на неверной реализации: 7 подмен — 6 роняют тест; «конец дважды» безвреден (повтор под тем же ключом ничего не пишет).
- Требует подтверждения владельцем: нет; п. (е) — вопрос A (класс C)
- Решено (2026-11-03): п. (е) — D-A-121 (#130): `SessionStarted.resumed: bool = False`; возврат в сессию, закрытую сборщиком, пишет `resumed: true` (после слияния #130).
- По ревью C #124 (2026-11-03): (е) сделано — `resume_if_swept` пишет `resumed=True`, старт — `False` (#130 влит). (ж) Два старта с одним ключом одновременно: второй прочитал ключ до записи первого, его `append_events` встречает ключ с другой нагрузкой (`IdempotencyConflictError`) — ключ перечитывается в той же транзакции (запись первого уже видна после блокировки строки ученика), возвращается та же сессия, при других параметрах — `SessionConflictError` (закон 5). (з) Пульс сессию не возвращает: «действие в сессии» WP13.10 — действие `POST …/actions` (виды WP13.1), а пульс — присутствие (`Т-§2.2`: в журнал не пишется); пульс закрытой сессии — `LookupError`, код ответа маршрута — при подключении маршрутов по §4.5 \[ПРОВЕРИТЬ\]; клиент после сна вкладки возвращает сессию первым действием. (и) Остаётся к маршрутам: `GET /v1/sessions/{id}` — лента сессии из событий по `seq` (WP13.10); `get_session` библиотеки отдаёт строку `content.sessions`.

### D-B-162. Извлекатель по эпизодам: окно, вход, вызов роли (WP15 п.1–3)
- Класс: B
- WP / место: WP15 п.1–3, `Т-§7.5`, `Т-§7.6`, `Т-§4.5`; `quack/agents/extract_episode.py`
- Контекст: WP15 п.1–3; п.4–5 (проверка элементов, запись `observation.extracted` с покрытием) стоят на швах A (`phase-4-a-wp15-seams`). Не названы: что такое «ход» для `episode_context_turns` в журнале Фазы 4; вид строк событий; порядок кандидатов при обрезке до `candidate_skills_k`; ключ вызова роли.
- Решение: (а) ход кончается своим `turn.committed`; события его записи (`causation_seq` = его `action_seq`, WP2.2) остаются с ним — в окно не попадают события хода за границей. Окно — эпизод `[from_seq, to_seq]` и события последних `episode_context_turns` ходов перед ним (и действие ученика после последнего хода); ходов меньше — все. Отдельная функция в `quack.agents`: продуктовые пакеты друг друга не импортируют (§4.1, `T-A6`), поэтому `quack.session.conversation` не переиспользуется. (б) Строки — `[seq] кто: текст`, как у `extractor@2` Фазы 3: реплики ученика и учителя, разметка хода (выданные задачи с проверяемыми навыками и уровнями, текст задания; показы), ответ и рассуждение, вердикт (кодом или моделью), `check.failed`, `typo.declared`, ответ на элемент, просьбы подсказки, решения, пропуск проверки, «непонятно», строки канваса (с пометкой зачёркнутой), исправление распознавания, голос, показанная подсказка, закрытие задачи. Номера — `seq` журнала (элементы ссылаются на них, `events`). (в) Кандидаты — навыки тем сессии, затем ближайшие по вектору эпизода, без повторов, первые `candidate_skills_k`; Modal недоступен — вызывающий передаёт пустой список ближайших. Известные заблуждения — только навыков-кандидатов. Метки: навыки — по порядку кандидатов, задачи — по появлению в окне, заблуждения. (г) Таблица типов пробела — коды `Т-§7.6` с описанием `П-6.4` словами доктрины `extractor@3`. (д) Вызов — `generate_structured` роли `extractor` (версия из таблицы ролей, `extractor@3`), схема `ExtractorOutputV3`; неизвестная метка навыка, заблуждения или корня — один возврат модели через `check` (`Т-§4.5`); ключ вызова — ключ наблюдения `obs:{episode_seq}:{extractor@3}:{trigger_seq}` (п.5). Второго мнения нет (`Т-§7.7`).
- Почему: §0.4.3 п.2 (WP15, `Т-§7.5`, `Т-§7.6`), п.5; §4.1.
- Обратимость: один модуль.
- Тесты: `tests/agents/test_extract_episode.py` — `T-X1` (часть окна), вход, кандидаты, вызов. Отклонение элементов вне эпизода (`T-X1`, вторая часть) — с п.4 на швах A.
- Дополнение (п.6, 2026-11-03): переизвлечение по `episode.reassigned` и `skill.split` — кандидаты только из `restrict` (навыки названной темы; разделённый навык и его части, как в задаче диспетчера `restrict_skill_ids`), ближайшие по вектору не добавляются (`Т-§2.8`, `Т-§4.11`); у нового наблюдения свой `trigger_seq` в ключе. Тест — в `test_candidate_skills`, отдельного красного прогона нет (владелец разрешил облегчить TDD, 2026-11-03).
- Требует подтверждения владельцем: нет


### D-B-165. Проверка и запись наблюдения извлекателя (WP15 п.4–5)
- Класс: B
- WP / место: WP15 п.4, п.5, п.8, WP7.3, `Т-§7.6`, `Т-§7.5`, закон 7; `quack/agents/extract_episode.py` (`source_text`, `episode_check_context`, `record_extraction`) на швах A D-A-124 (#133)
- Контекст: `check_extracted` A ждёт текст каждого события эпизода, уровни задач по номерам событий и пары «навык + уровень» с вердиктом code. Не названы: какой текст события — «исходный» для цитаты; какие события — «события задачи»; откуда пара навыка и уровня у вердикта (в `verdict.recorded` их нет); что писать, если извлекатель ничего не вернул.
- Решение: (а) текст события — слова ученика и учителя: реплика, ответ (`latex`, `value`, рассуждение), отклик на элемент, «непонятно», строка канваса, стёртая строка, исправленное чтение, голос, текст хода; у событий без слов (вердикт, подсказка, закрытие) текст пустой — на них можно сослаться, процитировать нельзя. Подписи строк входа («learner wrote on the canvas») цитатой не считаются. (б) Событие задачи — событие с `task_id` (кроме хода, который её предлагает) и вердикт через свой ответ; его потолок — наибольший уровень в `tests` задачи. (в) Вердикт code в эпизоде запрещает `evidence` по каждой паре `tests` его задачи; разметка задач приходит на вход отображением `task_id → [(skill_id, level)]` — его собирает вызывающий (задача `extract.episode`) из `content.tasks`. (г) `observation.extracted` пишется и при пустом выходе (эпизод прочитан; `nothing_to_extract` пишет сам диспетчер, WP7.2); актор и `extractor` — версия промпта роли (`extractor@3`), `causation_seq = trigger_seq`; ключ — `observation_key` A; событие и строка `ops.coverage` — одна транзакция.
- Почему: §0.4.3 п.2 (WP15 п.4–5, `Т-§7.6`), п.5.
- Обратимость: один модуль.
- Тесты: `T-X1` — `tests/agents/test_extract_episode.py::test_extracted_elements_checked_on_the_episode`, `test_record_extraction`, `test_record_extraction_commits_nothing_on_failure`.
- Не решено \[ПРОВЕРИТЬ\]: потолок уровня — по задаче целиком, не по части (`part`) ответа; задача `extract.episode` (сборка окна из журнала, кандидаты из S4, `task_tests` из `content.tasks`) — следующим шагом WP15.
- Требует подтверждения владельцем: нет

### D-B-168. Генерация задач (WP12.6)
- Класс: B
- WP / место: WP12.6, `Т-§5.4`, `Т-§5.6`, `Т-§11.1`; `quack/practice/task_generation.py` (`generate_tasks`, `GenerationRequest`); `markup_record` перенесён из `quack/session/turn_commit.py` в `quack/practice/markup.py` (generation в `practice` не может импортировать `session`, §4.1)
- Контекст: WP12.6 — `task_generator` (Spark; `proof` и уровень 6 — Wonder), варианты одного шага — общий `familyId`; переиспользовать генерацию Фазы 3, новое — проверка `task_checker`/`task_arbiter`. Код Фазы 3 (`quack/practice/generation.py`, вызов в `quack/artifacts/practice.py` — зона C) пишет `core.task_bank` и уходит с WP1.3. Не названы: что делать с вариантом, чья разметка нарушает `Т-§5.3`; идентификаторы задач и семейства при повторе; что делать с частью вариантов, не прошедших проверку.
- Решение: (а) новый модуль на `content.tasks` + `task.created` (WP12.1): вызов с `check` — нарушения `validate_markup` уходят модели один раз; вариант, неверный и после повтора, отбрасывается без проверки. (б) Каждый вариант — `verify_generated` (D-B-167) с ключом `{ключ запроса}:{номер}`; не прошёл — выброшен, остальные сохраняются. (в) `familyId` и `task_id` — `uuid5` от ключа запроса (и номера): повтор задачи пишет те же события (закон 5). (г) Сохранённое — `source: generated`, `verification: checked`, актор — `task_generator@1`. (д) Сложный вызов — `hard=True` при `proof` или уровне 6 (D-B-166). Переименование `task_gen` → `task_generator` в `quack/artifacts/practice.py` — зона C, не трогаю (код уходит с WP1.3).
- Почему: §0.4.3 п.2 (WP12.6, `Т-§5.4`, `Т-§5.6`), п.5.
- Обратимость: один модуль.
- Тесты: `tests/practice/test_task_generation.py` (5).
- Не решено \[ПРОВЕРИТЬ\]: потребители (подбор при пустом банке, поручения учителя `agent.task_generation`, пробники, «я это уже знаю», карточки C) подключаются своими WP; источник `course_context`, `excluded_families` — при сборке входов.
- Требует подтверждения владельцем: нет

### D-B-186. Семейство варианта исходной задачи при генерации (WP12.6, ревью #148)
- Класс: B
- WP / место: WP12.6, `Т-§5.4`, `Т-§5.7`; `quack/practice/task_generation.py` (`generate_tasks`, `source_family`, `is_variant_of_source`)
- Контекст: D-B-168 (в) давал каждому вызову своё `familyId` от ключа запроса. `Т-§5.7`: варианты для ретестов агент генерирует «с тем же familyId» (они доказывают удержание, но не уровень 4); `Т-§5.4`: задачи «те же шаг и шаблон, другие числа» делят `familyId`. Новое семейство у варианта исходной задачи сделало бы свидетельство `new` и дало бы ложное доказательство уровня 4.
- Решение: если в запросе есть исходная задача (`source`) и цель — `retest`, либо разметка черновика называет её в `variantOf`, задача получает `familyId` исходной (читается из её `task.created` по `created_seq`); иначе — семейство вызова, как в D-B-168 (в). `markup_record(markup, labels)` остаётся в `quack.practice.markup` публичной функцией (карточки C, WP23.4).
- Почему: §0.4.3 п.2 (`Т-§5.4`, `Т-§5.7`), п.5.
- Обратимость: одна ветка в одном модуле.
- Тесты: `tests/practice/test_task_generation.py::test_variant_of_the_source_takes_its_family` (3).
- Требует подтверждения владельцем: нет

### D-B-169. Подбор задач из банка (WP12.7)
- Класс: B
- WP / место: WP12.7, `Т-§5.7`; `quack/practice/select.py` (`select_tasks`), `quack/config.py` (`task_select_limit`)
- Контекст: `Т-§5.7` даёт ориентир Cypher: навык, уровень, «семейство ещё не встречалось», «ловит заблуждение M», проверенные первыми, `LIMIT 5`; сессия Neo4j — только чтение. Не названы: изоляция узлов внутри запроса; порядок при равенстве; где число 5.
- Решение: (а) запрос по ориентиру; каждый узел сопоставляется с `learnerId` ученика (навык, задача, задачи семейства; `Т-§3.5`). (б) Порядок — «ловит M», «проверена», затем `t.id` (повторяемость). (в) Число — параметр `task_select_limit = 5` (§0.4: числа — в конфиг). (г) Сессия — `read_session` (закон 3).
- Почему: §0.4.3 п.2 (WP12.7, `Т-§5.7`), п.5.
- Обратимость: один запрос.
- Тесты: `tests/practice/test_select_tasks.py` (Neo4j).


### D-B-172. Задача `extract.episode`: сборка из журнала и S4 (WP15 п.1–6)
- Класс: B
- WP / место: WP15 п.1–2, п.4–6, WP7.1, `Т-§2.1`, `Т-§7.5`, `Т-§7.6`, `Т-§2.8`, `Т-§4.11`; `quack/agents/extract_episode.py` (`ExtractionTrigger`, `extract_episode`, `run_extract_episode`, `task_tests`, `episode_vector`), `quack/practice/repo.py`; швы A: `GraphReader.extraction_candidates` (#147, D-A-135), правила диспетчера `rules_p4.py`
- Контекст: правила A кладут в задачу `trigger_seq` и, при переизвлечении, `episode_seq` с `topic_id` или `restrict_skill_ids`; у `episode.closed` — `from_seq`, `to_seq`. Не названы: где брать границы эпизода при переизвлечении; из какого текста считать вектор эпизода; откуда разметка задач для потолка уровня; кто строит читателя памяти в процессе `workers`.
- Решение: (а) эпизод — номер его `episode.closed` (`Т-§2.1`: «Идентичность эпизода — номер этого события»); без `episode_seq` в задаче эпизод — сам `trigger_seq`; границы и `session_id`/`domain_id` берутся из этого события. Номер, который не `episode.closed` сессии, — ошибка задачи. (б) Окно — события сессии до `to_seq` через `extraction_window` (D-B-162). (в) Вектор эпизода — `embed_query` от сырых слов событий эпизода (`source_text`, D-B-165) построчно; нет слов или `EmbeddingsUnavailable` — `None`, и A отдаёт только навыки сессии. (г) Кандидаты и известные заблуждения — `extraction_candidates(..., settings.candidate_skills_k, topic_id=..., skill_ids=...)` A; свои `CandidateSkill`, `KnownMisconception`, `candidate_skill_ids` удалены — отбор и ограничение делает A. (д) Разметка задач окна — из журнала (закон 1): `task.created` с частями, замещается более поздним `corrected_markup` у `task.verified`/`task.key_disputed`/`task.markup_disputed`; пары нового навыка без id не дают. Это уточняет п. (в) D-B-165 («из `content.tasks`»). (е) `run_extract_episode(job, ctx, reader)` принимает читателя аргументом: в `JobContext` (S1) читателя нет, а реализация `GraphReader` в G2 — у A. Обработчик в `JOB_HANDLERS` не регистрируется, пока A не назовёт, как процесс `workers` получает читателя.
- Почему: §0.4.3 п.2 (WP15, `Т-§2.1`, `Т-§7.6`), п.5.
- Обратимость: один модуль.
- Тесты: `tests/agents/test_extract_episode.py::test_extract_episode_job` (три триггера, Modal недоступен, повтор — то же наблюдение), `test_extract_episode_needs_an_episode`, `test_task_tests_from_the_stored_markup`; мутации (без разметки, без `topic_id`, без вектора, без `corrected_markup`) — тесты падают.
- Не решено \[ПРОВЕРИТЬ\]: регистрация `extract.episode` в `JOB_HANDLERS` — после ответа A, где читатель памяти в `workers`.
- Требует подтверждения владельцем: нет
### D-B-170. Изображение во входе вызова роли (шов S12)
- Класс: B
- WP / место: WP21.3 (`recognizer`), WP11.6 (`verdict_free`), `Т-§11.1` «Spark с изображением»; `quack/llm/provider.py` (`ImageInput`, `ProviderRequest.images`), `quack/llm/responses_client.py` (`image_data_url`), `quack/llm/gateway.py` (`generate_structured(images=)`); вопрос-блокер C в #134
- Контекст: роли «Spark с изображением» есть в таблице `Т-§11.1`, а у шва S12 вход только текст. Формат части с изображением техдок не называет; документация Meta для `POST /v1/responses` (прочитано 2 нояб. 2026, WP0 п.3д этих заметок): `input_image` с `image_url` — публичный URL или `data:image/png;base64,…`.
- Решение: (а) `ImageInput(mime: image/png | image/webp, data: bytes)`; `ProviderRequest.images` — после текста промпта, по порядку. (б) `ResponsesClient`: с изображениями содержимое сообщения — части `input_text` и `input_image` (`image_url` — data URL); без них — строка, как прежде. (в) Шлюз: `generate_structured(..., images=())` передаёт их в запрос; в хэш запроса входят тип и sha256 каждого изображения (повтор с другим изображением — другой запрос). (г) Подменам `tests/support/llm.py` (каркас C) поле не нужно: они получают `ProviderRequest` целиком.
- Почему: §0.4.3 п.2 (`Т-§11.1`, WP21.3, WP11.6), п.5.
- Обратимость: необязательное поле; вызовы без изображений не меняются.
- Тесты: `tests/test_llm_runtime.py::test_role_call_with_an_image`.
- Не решено \[ПРОВЕРИТЬ\]: `input_image` проверен только по документации Meta, не живым вызовом Spark (DoD п.6a, стенд); WebP для `input_image` документация не называет — PNG надёжнее; лимиты размера не названы.

### D-B-171. Источник куска без имени материала
- Класс: B
- WP / место: WP14.1 слот 8, WP14.4 `search_materials`, `Т-§10.5`, `Т-§19.8`; `quack/session/tools.py` (`MaterialChunk.material_name`, `render_chunks`); вопрос C в #137, D-C-127 п.5
- Контекст: `MaterialChunk` требовал имя материала; документы имени материала не называют — ни у узла `Material` (`Т-§3.4`), ни в `content.materials` (§6.1 ТЗ Ф4). `ChunkHit` C (#140) имени не несёт.
- Решение: `material_name: str | None`; без имени источник — метка куска и страница: «P1. From a material, page 3:». С именем — как прежде.
- Почему: §0.4.3 п.2 (поле не названо документами — не выдумывать), п.5.
- Обратимость: одно поле.
- Тесты: `tests/session/test_memory_tools.py::test_search_materials_and_modal_down`.

- Требует подтверждения владельцем: нет

### D-B-156. Тело `POST /v1/sessions/{id}/actions`: порты графа в API
- Класс: B
- WP / место: WP13.1, WP13.2, §4.7, §4.5; `quack/session/action_turn.py` (`ApiTurnPorts`, `turn_frames`, `TurnContextSource`), `quack/session/router.py` (`post_action`), `quack/session/actions.py` (`ActionFrame` — `RootModel`)
- Контекст: #75 объявил маршрут (D-B-119); граф хода — на портах (D-B-148), запись — `turn_commit` (D-B-149), сессии — `sessions.py` (D-B-155). Не названо: (а) откуда граф получает контекст до WP14; (б) кто держит блокировку хода между процессами; (в) коды кадра `error`; (г) ответ на действие в закрытой сессии; (д) как маршрут отвечает и SSE, и `201` при неизменном контракте S11; (е) вход маршрута до перевода API на сессии (D-C-119).
- Решение: (а) порт `TurnContextSource.assemble(TurnRequest) → TurnSetup` (запрос модели и входы правил, провайдер роли через `LLMGateway.streaming`, таблица меток, строка манифеста, инструменты, данные для записи — цитаты ученика, пояс, на что отвечает ход); реализует WP14. Маршрут берёт его из `app.state.turn_context_source`; нет — `503 SERVICE_UNAVAILABLE`. До WP14 PR не вливается (D-B-119). (б) `admit`: Redis `SET NX EX 60` на `lock:turn:{session}` (TTL Фазы 3), занято другим процессом — кадр `error {TURN_LOCKED, retryable}`; освобождение — сравнение токена после хода; в процессе — `TurnSlots` (одна сессия — один ход, новый отменяет старый); без Redis — только `TurnSlots` (`Т-§20.7`). Роль: флаг давления модели учителя — ждать не дольше `teacher_admission_wait_ms`, иначе `teacher_fallback` (`Т-§11.3`, WP5.3 Фазы 3). Канал отмены `session:{id}:cancel` между процессами — \[ПРОВЕРИТЬ\] с развёртыванием нескольких `api` (сейчас один процесс). (в) Коды §4.5 Фазы 3: модель не ответила (ошибки шлюза и провайдера, предел шагов инструментов) — `LLM_DEGRADED`, повторяемо; блокировка — `TURN_LOCKED`; прочее — `SERVICE_UNAVAILABLE`; хода в журнале нет (`Т-§12.1`). Ответ шаблоном при `llm_unavailable` / `budget_exceeded` (`degraded_mode`, §4.4) — отдельным пунктом \[ПРОВЕРИТЬ\]. (г) Сессия ученика открыта — действие пишется; закрыта сборщиком — сначала возврат (`resume_if_swept`, WP13.10); закрыта учеником или чужая — `404 NOT_FOUND`, ничего не пишется. (д) Обычная функция: `201 {seq}` (`ActionAccepted`) или поток SSE (`sse_keepalive_response`, `sse_keepalive_s`); 200 описан тем же `itemSchema` с `data` → `ActionFrame`, что и в #75; `ActionFrame` — `RootModel` с тем же именем компонента (для `responses.model`). (е) Ученик — только из cookie сессии (`get_current_session`, `Т-§19.1`); ограничитель частоты роутера сессий Фазы 3 пока требует и токен — до D-C-119 (тесты шлют оба, как e2e, D-C-115). Вызов «исправь разметку» — без модели (порт отдаёт `None`, элемент убирается): инструкции для него в `teacher@2` нет — с промптом.
- Почему: §0.4.3 п.2 (§4.7, WP13.1–13.2, `Т-§12.1`, `Т-§19.1`), п.3 (контракт S11 не меняется), п.5.
- Обратимость: два модуля и маршрут.
- Тесты: `tests/session/test_actions_route.py` (8, по HTTP): кадры §4.7, `201`, чужая сессия, без cookie, возврат после сборщика, закрытая учеником, кадр ошибки без хода, освобождение блокировки. Проверено на неверной реализации: 5 подмен — каждая роняет тест. e2e потока C (`T-E2`, `T-E3`) ждут маршрут в обходах — у C.
### D-B-182. Сводка аналитика, результаты агентов и поправки в картине памяти (WP14.2)
- Класс: B
- WP / место: WP14.2, `Т-§10.2`, `Т-§13.5`, `Т-§13.7`, `Т-§5.6`, `Т-§6.3`, закон 10; `quack/session/turn_source.py` (`_revised`, `_requests`, `MemoryTurnContextSource._digest`, `_completed`)
- Контекст: строки слота 7 «сводка аналитика, готовые результаты агентов, неисполненные поправки» уже рисует `render_memory_picture` (D-B-157), но источник их не давал (D-B-181 п.2). Не названы: какая сводка; какой результат «готов»; когда поправка «исполнена».
- Решение: (а) Сводка — текст последнего `digest.written` домена ученика. Аналитик пишет её заново из структуры, поэтому чтение последней — не накопление выводов (закон 10). (б) Поручения сессии — `agent.requested` сессии и `agentRequests` её `turn.committed`. Все они получают метки `R…`, чтобы учитель мог их отменить. Готов тот, у которого есть `agent.completed` с `ok = true`: строка «<метка>: <вид> is ready». (в) Поправка — пара `(answer_seq, part)`, последний вердикт которой отличается от первого. Она держится, пока номер ответа не попадёт в `correctionsAcknowledged` какого-либо `turn.committed`. Строка: «part <часть> is now <исход>, was <исход>».
- Почему: §0.4.3 п.2 (WP14.2), п.5.
- Обратимость: один модуль.
- Дополнение: (г) Целевой набор навыка — уровни, которые требуют хабы его темы (объединение, `Т-§8.5`), `target_levels`. (д) Инструменты получают строки канваса по номерам слота 5 (`L1…` в порядке сессии, стёртая строка сохраняет номер, `handwriting.corrected` заменяет текст) и сегменты голоса — для `voice_at_line` и `past_solution` (WP14.4).
- Тесты: `tests/session/test_turn_source.py::test_journal_revisions_and_requests`, `::test_assemble_first_turn_freezes_slots`; мутация «признанная поправка остаётся» — тест падает.
- Не решено \[ПРОВЕРИТЬ\]: нет.
### D-B-181. Источник контекста хода учителя (WP14, WP13.2 «assemble»)
- Класс: B
- WP / место: WP14.1–14.5, WP13.2, `Т-§10`, `Т-§12.7`, `Т-§12.9`, `Т-§12.4`, `Т-§10.5`; `quack/session/turn_source.py` (`MemoryTurnContextSource`, `read_journal`, `turn_effort`, `answer_strings`), `quack/session/spoiler_guard.py` (`answer_in_text`, `remove_answer_sentences`)
- Контекст: порт `TurnContextSource` из #125 нужно реализовать на журнале сессии и чтениях S4 (#170). A в #175: фабрика `workers` — с обязательным `learner_id`; ходу учителя нужен читатель с `session_factory`, его `quack.session` строит напрямую. Не названы: откуда цель сессии; что считать «активной задачей»; как проверять спойлер по `content.tasks` (защита Фазы 3 читает `core.task_bank`); как считать «два провала подряд на одном навыке» без `task.evaluated` Фазы 4.
- Решение: (а) Читатель — параметр `reader(learner_id) -> GraphReader`; сборка процесса API передаёт `Neo4jGraphReader(neo4j, settings, learner_id=..., session_factory=...)` (ответ A в #175). (б) Цель — последний `message.sent` сессии с `intent` (`Т-§12.7`); без него — «learn» по домену. Темы цели — тема или темы хаба цели из `domain_goals`. (в) Активные задачи — `task.created` сессии без `task.closed`; фокус среза — навыки их разметки и навыки тем цели (`topic_memory`), `Т-§10.2`. (г) Чтения идут параллельно (WP14.5). Срез, который нельзя прочитать (Neo4j недоступен), — пустой срез, слот 7 «memory temporarily unavailable» и `memory_unavailable` (WP6.1). (д) Слоты 3–4 — снимок сессии (D-B-180); первый ход пишет его в `frozen_slots` своей строки манифеста. (е) Слот 8 — предвыборка по графу `materials_prefetch` (`Т-§10.5`), `materials_k` кусков. (ж) Действие хода: `hint.requested`, `solution.requested`, `check.skipped` дают `AnsweredAction`. Ответ `solved`, если последние вердикты всех частей ответов на эту задачу — `correct`. Вердикт самого ответа идёт в защиту. (з) Усилие (`Т-§12.9`): `high` на первом ходе сессии (нет `turn.committed`) и когда два последних проверенных ответа провалены на задачах с общим навыком; `teacher_fallback` — всегда `low`. (и) Спойлер — латех и sympy значения и элементов `answer_spec` активных задач; нормализация и пороги — общие с защитой Фазы 3. (к) Бюджет — `fit_budget` по шаблону промпта роли до вызова.
- Почему: §0.4.3 п.2 (WP14, WP13.2), п.5.
- Обратимость: один модуль; порт `TurnContextSource` не менялся.
- Тесты: `tests/session/test_turn_source.py` (5); мутация «снимок пишется каждым ходом» — тест падает.
- Не решено \[ПРОВЕРИТЬ\]: (1) Подключение сделано: `get_turn_context_source` (`quack/session/router.py`) один раз собирает источник из `app.state.resources` процесса API (`source_of`), правок C нет. Читатель — `Neo4jGraphReader(..., learner_id, session_factory, redis)` (#175, D-A-144). Пока A не влила `profile` и `misconception_detail` (#176, #179), сборка хода падает на `NotImplementedError` этих чтений, и клиент получает кадр `error`. (2) Быстрый вердикт кодом пишет `record_answer` при записи действия, до хода, поэтому сборка берёт его из журнала, отдельного ожидания нет (WP13.2). `context_text` хранится для доли `turn_context_sample_rate` ходов; решает `manifest_id`, так что повтор хода решает так же. `digest_id` манифеста пуст: у `digest.written` нет своего UUID — \[ПРОВЕРИТЬ\].
### D-B-180. Слоты 3–4 из памяти и их снимок на сессию (WP14.1)
- Класс: B
- WP / место: WP14.1, `Т-§10.1`, `Т-§9.5`, `Т-§8.5`, `Т-§4.5`, закон 8; `quack/session/turn_inputs.py` (`learner_state`, `learned_lines`, `load_snapshot`), `quack/session/teacher_context.py` (`snapshot_slots`, `frozen_slots`), `quack/session/turn_commit.py` (`TurnManifestRow.frozen_slots`)
- Контекст: A выложила `GraphReader.profile` и `domain_goals` и колонку `ops.turn_manifests.frozen_slots` (#170, D-A-137). Не названы: как подать выученную часть профиля словами; что делать с метками в замороженном тексте — таблица меток живёт один вызов (`Т-§4.5`), а текст слотов 3–4 держится всю сессию (`Т-§10.1`).
- Решение: (а) Слоты 3 и 4 рисуются без меток, только с именами: иначе метки снимка разошлись бы с таблицей следующего хода. Слот 9 называет каждую метку вызова. (б) Снимок — текст `{profile, domain_and_goals}`. Его пишет ход, который его заморозил; читается самая ранняя строка манифеста сессии с непустым `frozen_slots`. Изменения за сессию — строки хвоста: строки текущей отрисовки, которых нет в снимке. (в) Выученное — четыре строки по `Т-§9.5`: «what helps», «confidence» (перекос словами, без оценки Брайера), «corrective branches» и «reasoning shown» (доли в процентах). При нехватке данных — «no data yet», не ноль (закон 8). (г) Свои модели слотов (`Profile`, `HubGoal`) остаются: `learner_state` переводит в них `ProfileFacts` и `HubGoal` A.
- Почему: §0.4.3 п.2 (WP14.1, `Т-§10.1`), п.5.
- Обратимость: два модуля.
- Тесты: `tests/session/test_turn_inputs.py` (4), `tests/session/test_teacher_context.py::test_slot_order_and_freeze`; мутация «самая поздняя строка снимка» — тест падает.
- Не решено \[ПРОВЕРИТЬ\]: «явное предпочтение перекрывает выученное» (`Т-§9.5`) — ключи `pref*` и `learned*` не пересекаются по именам, поэтому в слоте показываются обе группы.

### D-B-185. Гигиена графа `graph.hygiene` (WP16.3)
- Класс: B
- WP / место: WP16.3, `Т-§4.10`, `Т-§22.10`, `T-AG4`; `quack/agents/hygiene.py`, `config.py` (блок B)
- Решение: подграф домена — два Cypher-чтения (навыки `provisional`/`active` с векторами и соседями `REQUIRES`/`COVERS`; заблуждения этих навыков). Кандидат в дубли навыков — косинус + `hygiene_neighbour_weight` (0.1) × доля общих соседей (Жаккар), порог `hygiene_min_score` (0.9); заблуждения — косинус в пределах навыка, тот же порог. Один вызов `builder@1`, режим Hygiene. Дубль с временным навыком — `skill.merged` (временный в другой); два активных — `skill.merge_suggested`; заблуждения — `misconception.merged`. Цель слияния равных — меньший id; узел сливается раз за прогон. Нагрузка задачи — `domain_id`.
- Почему: в ТЗ порога нет — свои параметры B (§0.4.3 п.5). `networkx` не в зависимостях: множества соседей — на Python; хабы Louvain — позже (`Т-§22.10`).
- Тесты: `tests/agents/test_hygiene.py::test_hygiene_merges_only_provisional`; Cypher проверен на пустом Neo4j.
- Не решено \[ПРОВЕРИТЬ\]: строка диспетчера `session.ended` ждёт фактов A (временные сущности, счётчики) — пока задачу никто не ставит; перенос проявлений и сопротивлений на цель (`Т-§9.3`) — проекция A по `misconception.merged`.
### D-B-184. Подготовка «Сегодня» `today.prepare` (WP16.8)
- Класс: B
- WP / место: WP16.8, `Т-§14.4`, `T-RV2`; `quack/agents/today.py`
- Решение: триггер `today.shown`. Для каждого пункта уровень — граница навыка по уровням хабов (все уровни без хаба), кандидаты банка — `select_tasks`, не больше `label_max_tasks // число пунктов`. Один вызов `today_planner`; метки вне кандидатов отбрасываются. Черновой план — `content.session_plans` со статусом `prepared` под id сессии `uuid5(learner, "today:{trigger}:{skill}")`; `generate` — `agent.requested {task_generation}`. План и запросы — одной транзакцией; повтор ничего не пишет.
- Почему: §0.4.3 п.2, закон 5, закон 7.
- Тесты: `tests/agents/test_today.py` (1).
- Не решено \[ПРОВЕРИТЬ\]: маршруты сессий ещё на Фазе 3 — `POST /v1/sessions {today_item}` план пока не берёт (перенос — G3); карточки — `(none)`.
### D-B-183. Задачи проверок: `check.symbolic`, `check.model`, `task.check`
- Класс: B
- WP / место: WP11.3, WP11.6, WP12.5, строки WP7.1; `quack/practice/jobs.py`
- Решение: обработчики читают нагрузку строки (`answer_seq`, `part`; `task_ids`) и вызывают готовые тела (`insure_symbolic`, `check_with_model` + `record_verdict`, `check_teacher_task`). Для `check.model` известные заблуждения и состояния проверяемых уровней берутся из среза S4 для навыков разметки задачи; `failed` — есть ли `check.failed` этого ответа и части; уже записанный вердикт — выход (закон 5). Для `task.check` имена проверяемых навыков берутся из среза сессии хода. Виды — в реестре по процедуре (б) #173.
- Почему: §0.4.3 п.2, п.5.
- Тесты: тела покрыты своими тестами; T-Q7 зелёный.
- Не решено \[ПРОВЕРИТЬ\]: `task.solution_space`, `agent.task_generation`, `agent.solution_space`, `check.known_claim` — после #144 и #148 (их тела там); `second_opinion.diagnosis` — следующий шаг B.
### D-B-179. Описание заблуждения на фактах S4 (WP16.2)
- Класс: B
- WP / место: WP16.2, WP7.1, `Т-§9.3`, `T-AG2`; `quack/agents/misconception_describe.py` (`describe_misconception`, `render_manifestations`, `run_misconception_describe`)
- Контекст: A выложила `MisconceptionDetail` и `ManifestationFact` (#170) и ответила, что `workers` получает читателя фабрикой `create_graph_reader(ctx.neo4j, ctx.settings)` (#155). Своя модель `DescriberInput` из D-B-175 (д) больше не нужна. Открытые места: у `ManifestationFact.quote` бывает `None`; порядок проявлений с одинаковым временем не задан.
- Решение: (а) Модуль принимает `MisconceptionDetail | None` от A напрямую; `DescriberInput` и `Manifestation` удалены. Статус `unknown` (гипотеза до первого проявления) — выход без вызова, как и всё вне трёх подтверждённых. (б) Порядок — по `at_ms`, при равенстве по `source_seq`: вход детерминирован (закон 5). (в) Проявление без цитаты — строка «no quote», а не пустые кавычки. (г) `run_misconception_describe(job, ctx, reader)` берёт `misconception_id` и `trigger_seq` из нагрузки строки WP7.1, домен — из события-триггера. Читатель — параметр: тест подставляет `FixedGraphReader`, а регистрация с `create_graph_reader` — после выбора процедуры реестра видов в #173 (T-Q7: обработчик вида, которого нет в `JOB_KINDS`, роняет `test_all_job_kinds_have_handlers`).
- Почему: §0.4.3 п.2 (WP16.2, `Т-§9.3`), п.5.
- Обратимость: один модуль.
- Тесты: `tests/agents/test_misconception_describe.py` (5); мутация «сортировка только по `at_ms`» — тест падает.
- Регистрация (процедура (б) A в #173, сигнатура фабрики D-A-144): `misconception.describe` и `extract.episode` — в `JOB_KINDS`, `KIND_LLM_ROLES` (роли из `rules_p4.JOB_KIND_SPECS`) и `HANDLER_MODULES`. Обработчики строят читателя `create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)`. Ревью строк реестра — за A.
- Не решено \[ПРОВЕРИТЬ\]: до слияния #179 (`misconception_detail`, `extraction_candidates` на Neo4j) задачи этих видов падают на `NotImplementedError` и уходят в повторы.

### D-B-177. Строитель визуализаций: задача `agent.viz` (WP16.5)
- Класс: B
- WP / место: WP16.5, `Т-§13.6`, `Т-§22.10`, `Т-§13.7`, `Т-§13.9`, `T-AG6`, закон 5; `quack/agents/viz.py` (`build_viz`, `handle_viz`, `param_sets`, `libraries_block`), `quack/config.py` блок B (`viz_libraries`)
- Контекст: на срезе проверка — ошибки консоли и непустой холст, одна попытка исправления (`Т-§22.10`); библиотеки — «D3, Plotly, KaTeX, версии закреплены» с `viz_static_base_url`, но файлы и версии не названы. Ключ объекта `viz_key` содержит ULID (C, `Т-§3.3`), то есть повтор записал бы новый объект.
- Решение: (а) Задача читает своё `agent.requested` по `request_id`, описание — `params.description`. (б) До `1 + viz_max_fix_rounds` вызовов `viz_builder`, ключ `viz:{request_id}:{попытка}`. В следующий вызов идут ошибки прошлой попытки: строка консоли с набором параметров, «blank canvas», набор не плоский объект JSON (D-B-130 п.2). Неверный набор не рендерится. (в) SVG рендерится и хранится телом страницы. (г) Прошло — объект `viz_key(learner, request_id)` в хранилище и `agent.completed {request_id, kind: viz, ok: true, result_ref: ключ}`. Не прошло — `agent.completed {ok: false, error: viz_check_failed}` (`Т-§13.9`). Ключ события — `agent.completed:{request_id}`. (д) Повтор задачи сначала ищет своё `agent.completed` и, найдя, ничего не вызывает и не пишет (закон 5). Объект, записанный до сбоя перед событием, остаётся сиротой для `ops orphan-objects` (Д-§8.3). (е) `viz_libraries` — словарь «имя → путь под `viz_static_base_url`», по умолчанию пуст: модель получает «(none)» и пишет страницу без библиотек. (ж) `ComputeUnavailableError` поднимается в задачу: повтор по политике очереди.
- Почему: §0.4.3 п.2 (WP16.5, `Т-§13.6`, `Т-§22.10`), п.5.
- Обратимость: один модуль.
- Тесты: `T-AG6` (часть «одна попытка исправления») — `tests/agents/test_viz.py` (4); мутации (лишняя попытка, без проверки «blank», без поиска готового события, без ошибок во входе) — тесты падают.
- Не решено \[ПРОВЕРИТЬ\]: файлы и версии библиотек `deploy/static/viz/` (C, WP9.2) — заполнить `viz_libraries`; `GET /v1/viz/{id}` с CSP — отдельным PR (тело B, маршрут в `quack/artifacts/router.py`, §7.3 п.3); подключение `quack.agents.viz` в `HANDLER_MODULES` (`quack/runtime`, A).
### D-B-176. Аналитик границы: вызов и запись (WP16.1)
- Класс: B
- WP / место: WP16.1, `Т-§13.5`, `Т-§13.2`, `Т-§9.5`, `T-AG1`, законы 5, 7, 10; `quack/agents/analyst.py` (`run_analyst`, `render_evidence`, `digest_structured`, `analyst_errors`)
- Контекст: вход аналитика — «только структура»; прогноз — `agent.requested` от `agent:analyst`. Не названы: вид строк свидетельств; что делать с меткой, неверной и после повтора; ключи сводки и поручений; `needed_in_session` у прогноза; что такое `as_of_seq`.
- Решение: (а) Блоки памяти (состояние навыков, заблуждения, план, цели, профиль) отрисовывает вызывающий из `quack.graph.read` (S4, ответ A в #125). Здесь из журнала строится только блок свидетельств: принятые `evidence` из `observation.extracted`, по строке — навык, уровень, исход, помощь, тип пробела, цитата. Навык без метки во вход не идёт. (б) Прошлой сводки и манеры во входе нет вовсе (`T-AG1`). (в) Метки рамки, гипотез и прогноза проверяются (`Т-§4.5`). Элемент, неверный и после повтора, отбрасывается. (г) Ключ сводки — `digest:{domain_id}:{trigger_seq}:{роль}` (у `analyst` и `analyst_deep` свои). Поручение — `{ключ}:request:{i}`, `request_id = uuid5(uuid5(domain_id, ключ), i)`. Повтор задачи даёт те же события и id. (д) `needed_in_session = false`: прогноз — подготовка на потом (класс P2, `Т-§13.3`). (е) `as_of_seq` — номер, по который собран вход; его даёт вызывающий. (ж) Сводка и поручения — одна транзакция (закон 7). `analyst_deep` рендерит `analyst@1` (`prompt_role`).
- Почему: §0.4.3 п.2 (WP16.1, `Т-§13.5`), п.5.
- Обратимость: один модуль.
- Тесты: `T-AG1` — `tests/agents/test_analyst.py` (2); мутации (`request_id` случайный, `needed_in_session`, без `prompt_role`, `as_of_seq`) — тесты падают.
- Задача (D-B-179 рег.): `analyst.run`/`analyst.deep` зарегистрированы. Триггер даёт домен и сессию; триггер вне сессии домена — ничего не пишет. Фокус — навыки из `observation.extracted` сессии. Блоки рисуются здесь же, коротко: `agents` не импортирует `session` (§4.1). Цели — из `domain_goals`, профиль — из `profile`, план — из `content.session_plans`.
- Не решено \[ПРОВЕРИТЬ\]: строки триггеров без сессии (проявления заблуждения вне хода) аналитика не запускают.
### D-B-175. Описание заблуждения (WP16.2)
- Класс: B
- WP / место: WP16.2, `Т-§9.3`, `Т-§9.2`, `T-AG2`, законы 5 и 10; `quack/agents/misconception_describe.py` (`describe_misconception`, `render_manifestations`, `DescriberInput`); факты — `GraphReader.misconception_detail` A (ответ A в #155, сигнатура — в PR A)
- Контекст: «завершается, если заблуждение не подтверждено» (WP16.2), но статусов подтверждённого в `Т-§9.2` три. Не названы: вид строк проявлений во входе `{{ manifestations }}`; ключ события; что делать с пустыми условиями.
- Решение: (а) «Подтверждено» — `confirmed`, `in_work` (подтверждено и учитель нацелен), `watching` (подтверждено и сопротивляется). Остальное и `None` — выход без вызова (`T-AG2`). (б) Строка проявления: порядковый номер, дата (UTC, `ГГГГ-ММ-ДД` — вход детерминирован), условие задачи из `content.tasks` или «в разговоре, без задачи», цитата. Строки идут по времени, все без отбора. Прошлой формулировки во входе нет (закон 10). (в) Ключ события и вызова — `misconception.described:{misconception_id}:{trigger_seq}`, `causation_seq = trigger_seq`, актор — версия промпта. Повтор задачи даёт то же событие (закон 5). (г) Формулировка и условия обрезаются по краям, пустые условия отбрасываются. (д) Модуль принимает `DescriberInput`, а не модель A: обработчик задачи переводит в него `MisconceptionDetail`, когда A выложит сигнатуру. Регистрация задачи — после ответа A, как `workers` получает `GraphReader` (#155).
- Почему: §0.4.3 п.2 (WP16.2, `Т-§9.3`), п.5.
- Обратимость: один модуль.
- Тесты: `T-AG2` — `tests/agents/test_misconception_describe.py` (3); мутации (статус `suspected`, порядок, обрезка, `causation_seq`) — тесты падают.
- Не решено \[ПРОВЕРИТЬ\]: обработчик и регистрация `misconception.describe` — после PR A с `misconception_detail` и ответа о `GraphReader` в `workers`.
### D-B-174. Входы контекста учителя из данных B (WP14.1)
- Класс: B
- WP / место: WP14.1, `Т-§10.1`, `Т-§12.5`, `Т-§17.8`; `quack/session/turn_inputs.py` (`load_plan`, `load_learner_skill`)
- Контекст: план и скилл ученика — данные B (`content.session_plans`, `content.learner_skills`). Профиль и цели домена для слотов 3 и 4 отдаёт память: A в #125 назвала `GraphReader.profile` (предпочтения, манера, выученное) и `domain_goals`. Снимок на начало сессии читается один раз на первом ходе и хранится; где именно — вопрос A в #125.
- Решение: (а) План — документ `content.session_plans` сессии (WP13.6); чужой сессии нет. (б) `/имя` — инструкция последней версии скилла с этим именем; неизвестное имя ничего не даёт; читается своим SQL, без импорта `artifacts` (WP14.1). (в) Профиль из журнала B не сворачивает: два источника предпочтений разошлись бы. Строки изменений хвоста даёт `frozen_slots` — сравнение снимка с текущим чтением (D-B-159).
- Почему: §0.4.3 п.2 (WP14.1), п.5.
- Обратимость: один модуль.
- Тесты: `tests/session/test_turn_inputs.py::test_plan_and_learner_skill`; мутация «первая версия скилла вместо последней» — тест падает.
- Не решено \[ПРОВЕРИТЬ\]: место хранения снимка слотов 3 и 4 — ответ A в #125.
- Требует подтверждения владельцем: нет

### D-B-173. Определение домена: тело `POST /v1/domains/classify` (WP16.4)
- Класс: B
- WP / место: WP16.4, `Т-§4.9`, `Т-§22.9`, `Т-§4.5`, §4.1, §7.3 п.3; `quack/practice/domain_classify.py` (`classify_domain`, `ClassifyDomainRequest`, `DomainSuggestion`), `DomainClassifierOutput` перенесён в `quack/practice/role_outputs.py` (реэкспорт в `quack/agents/schemas.py`); объявление маршрута — `quack/domains/router.py` (A)
- Контекст: §7.3 п.3 — тело в пакете B, маршрут в роутере A; §4.1 — продуктовый пакет импортирует из продуктовых только `practice`, так что `quack.domains` не может импортировать `quack.agents`. Не названы: форма ответа; что такое «начало файла»; что при неуверенном ответе, при материале без текста и при сбое модели; ключ вызова.
- Решение: (а) тело и схема выхода — в `practice` (как `markup_record`, D-B-168). (б) Запрос — ровно одно из `text`, `material_id`. Ответ: `{decision, domain_id?, name?, confidence?}`; `existing` — id домена по метке, `new` — имя без краевых пробелов. Ниже `domain_autolink_confidence` — `unclear`, без предложения (`Т-§4.9`: «Неясно — домен не предлагается»). (в) Домены ученика — события `domain.created` по порядку (закон 1), метки `D…` (D-B-131). (г) «Начало файла» — первая страница материала с текстом (`content.material_pages`). Чужой материал — 404; страниц с текстом ещё нет — `unclear` без вызова. (д) Метка не из списка, `existing` без метки, `new` без имени — один повтор через `check` (`Т-§4.5`). Сбой шлюза (`LLMUnavailableError`, `LLMBudgetExceededError`, `LLMSchemaError`) — `unclear`: классификатор только подсказывает, домен ученик выбирает сам (`Т-§22.9`). (е) Ключ вызова — `domain_classify:{learner}:{sha256 текста}:{хэш набора доменов}`: новый домен меняет вход, а значит и ключ. (ж) Ничего не пишется: новый домен создаёт `POST /v1/domains` (A).
- Почему: §0.4.3 п.2 (WP16.4, `Т-§4.9`), п.5.
- Обратимость: один модуль.
- Тесты: `T-AG5` — `tests/practice/test_domain_classify.py` (4); мутации (порог, проверка метки, обрезка имени) — тесты падают.
- Списки маршрутов в тестах C: `POST /v1/domains/classify` добавлен в `EXPECTED_ROUTES` (`tests/test_app_assembly.py`) и в `NO_IDEMPOTENCY_KEY` (`tests/e2e/endpoints.py`): тело ТЗ Ф4 WP16.4 — `{text | material_id}` без `idempotency_key`, событие не пишется (п. ж), повтор безвреден; путь без `{id}` — T-E3 не затрагивает.
- Требует подтверждения владельцем: нет

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
- Согласование потоков (шов, §0.4): B согласен (2026-11-02, работает в `phase-4-b`, PR #72);
  A согласен (2026-11-02, ревью #74).
- Требует подтверждения владельцем: да — **подтверждено** 2026-11-02: владелец поручил B решать по ТЗ и
  техдоку («у нас пока никаких данных учеников нет. остальные решения если за мной какие есть - сверься с тз и тз доком, дай решение»); решение B — в пользу (1). A согласен (ревью #74).

#### Состояние пакетов потока C

| Пакет | Состояние | Тесты |
| :-- | :-- | :-- |
| S1 (зона C), этап I | интерфейсы `ObjectStore`, `EmbeddingClient`, `EmailSender`, `DeletionJournal`; поля `AppResources`; подмены; имена параметров §6.7 потока C. Продовые клиенты — `NotImplementedError` (WP18–WP20, WP25) | `tests/storage/test_s1_resources.py` — 8 passed |
| S10, этап I + пул | `compute`: пул `pebble` (`quack_compute/pool.py`) с жёстким таймаутом — реализован; настройки, схемы и маршруты WP24 — тела `NotImplementedError`; `Renderer` (Chromium) — этап I. `backend`: `ComputeClient` — методы всех эндпоинтов WP24, `create_compute_client`; заглушка — очереди по эндпоинтам | `compute/tests/test_pool_and_contract.py` — 4 passed (`T-CM1` зелёный; в Linux, весь `compute` — 9 passed); `tests/storage/test_compute_client_s10.py` — 4 passed |
| S13 (реализация подмен) | `tests/support/llm.py`: `ScriptedLLMProvider.stream_structured` — шаги с инструментами (`script_tools`, два вызова за шаг, `phase: commentary`), ответ дельтами с ключами по алфавиту, флаг `body_before_head`, сбои Фазы 3 по D-B-125 п.5, журнал `stream_calls`; `loadtest/meta_mock.py`: SSE при `stream: true` (D-B-125 п.3), после `function_call_output` на каждый вызов — ответ. Формат — D-B-125 (B, PR #86) | `tests/test_support_scripted_llm.py` — 21 passed; `tests/test_meta_mock.py` — 59 passed |
| WP18, библиотека | `quack/auth/`: ссылка по почте (128 бит, в базе хэш, одно погашение, `auth_link_ttl_min`), `LinkLimiter` (Redis, без него — локально), сессии (`core.auth_sessions`, хэш, отзыв, срок), параметры cookie, ученик по подтверждённому адресу Google, согласия событиями. `FakeOIDC` в `tests/support/oidc.py`. **Не сделано (этап II, часть 2):** маршруты `/auth/*`, `/v1/me`, `/v1/consents`, `PATCH /v1/learner/settings`; `AuthlibGoogleOIDC`; замена входа Фазы 3 (JWT, `auth.users`) на сессии во всех роутерах; коды `UNAUTHENTICATED`, `CONSENT_REQUIRED`; `T-AU5` | `tests/auth` — 7 passed (Linux, инфраструктура) |
| WP18, маршруты | `quack/auth/router.py`: `POST /auth/link`, `GET /auth/link/verify`, `GET /auth/google/start`, `GET /auth/google/callback`, `POST /auth/logout`, `GET /v1/me` (с параметрами клиента §6.7), `PATCH /v1/learner/settings` (часовой пояс), `POST /v1/consents`, `DELETE /v1/consents/research`; основание API `quack/api/auth.py`: `get_current_session`, `require_terms` (для старта сессии, B), кука `quack_session`; коды `UNAUTHENTICATED`, `CONSENT_REQUIRED`; `EmailUnavailable` → `SERVICE_UNAVAILABLE`. **Не сделано:** перевод `get_current_learner_id` всех роутеров на сессии и удаление `/v1/auth/register|login` (D-C-112, ждёт согласия A на правку `quack/domains/router.py`, `quack/domains/auth.py`); `AuthlibGoogleOIDC` (настоящий Google, нужен клиент Google OIDC — действие владельца) | `tests/auth` — 14 passed (Linux, инфраструктура); `test_app_assembly.py` + `test_contracts.py` — 26 passed |
| WP20.1, WP20.5 (библиотека) | `quack/materials/library.py`: загрузка (тип по содержимому, объект, затем строка `content.materials` и `material.uploaded` одной транзакцией), список материалов домена, карточка с текстом страниц и подписанными ссылками; `quack/materials/search.py`: полнотекстовый поиск по событиям тем же выражением и типами, что индекс WP1.1. **Не сделано:** маршруты (с переводом входа на сессии, D-C-112; загрузка multipart — `python-multipart` добавлен по решению владельца, D-C-113), WP20.2 (ждёт очередь A, `builder` B, слой 2 `material_parser`), WP20.3 (Modal — действия владельца), WP20.4 (ждёт узлы кусков в графе, проектор A) | `tests/materials` — 4 passed (`T-MT5`; Linux, инфраструктура) |
| WP21.2, журнал штрихов | `quack/canvas/strokes.py`: формат куска (MessagePack + zstd, точки разностью, стирание — операция), `StrokeJournal` (пачки в памяти, кусок раз в `stroke_chunk_seconds`, `ack` только после записи, повтор `batch_no` отсекается, хранилище недоступно — `ack` нет). **Не сделано:** WebSocket и Redis pub/sub (с переводом входа на сессии, D-C-112), доска WP21.1, распознавание WP21.3, проверка шагов и пометки WP21.4–21.5, голос и проигрывание WP21.6 | `tests/canvas` — 4 passed (`T-CV1`, `T-CV2`) |
| S1: поле `db` (после #78) | `AppResources.db: Db` (§4.2): `create_app_resources` стала асинхронной и открывает пул `Db` без ожидания Postgres — API стартует и `/health/ready` отвечает 503 без базы (контракт готовности Фазы 3; `open_db` A ждёт пул — это для workers); закрывается последним. `engine` и `session_factory` остаются до WP1.3 (A). `tests/support/resources.build_app_resources(..., db=)`: без `db` — `Db` над закрытым пулом (`PoolClosed` при обращении) | `tests/storage/test_s1_resources.py`, `tests/test_api_llm_provider.py`, `tests/test_app_assembly.py` — 30 passed (Linux, инфраструктура) |
| WP24 | `compute`: `/v1/pdf/pages` (PyMuPDF), `/v1/image/align`, `/v1/image/clean-drawing`, `/v1/image/render-strokes` (OpenCV), `/v1/render/pdf` (Playwright, A4, число страниц); Chromium запускается на первое задание и закрывается после `chromium_idle_close_s` простоя; образ с Chromium, `TMPDIR=/srv/quack/tmp`; шаг Chromium в CI. `/v1/render/snapshots` по D-B-130. **Не сделано:** почасовая чистка `/srv/quack/tmp` (вопрос, см. D-C-110) | `compute` в Linux — 76 passed (`T-CM1`, `T-CM2`, `T-CM3`) |
| WP19 | `quack/storage/keys.py` — ключи `Т-§3.3` с ULID; `quack/storage/uploads.py` — тип по сигнатуре (PDF, PNG, JPEG, WebP), лимит `upload_max_mb`, `UploadRejected` 415/413; `S3ObjectStore` на `aioboto3` (path-style, подписанные ссылки, сбой → `StorageUnavailable`); `UPLOAD_REJECTED` и `StorageUnavailable` → `SERVICE_UNAVAILABLE` в `quack/api/errors.py`; снимок `openapi.json`. Эндпоинты загрузки — WP20 (материалы), WP22 (фото): там же вторая половина `T-ST3` («ход учителя работает») | `tests/storage/test_storage_wp19.py` — 12 passed (`T-ST1`, `T-ST2`, `T-ST3` — часть хранилища) |
| S10: клиент math (WP24, по просьбе B) | `quack/compute_client/math_contracts.py` — копия `compute/quack_compute/math/schemas.py` (B); `ComputeClient.math_check`, `math_transition`, таймаут HTTP — `timeout_ms / 1000 + compute_timeout_sec`; очереди заглушки `/v1/math/check`, `/v1/math/transition`. `/v1/check/math` Фазы 3 остаётся до перевода `quack.practice` (WP11.3, B) | `tests/storage/test_compute_client_math.py` — 6 passed |

### D-C-102. Где живут `EmailSender` и `DeletionJournal`
- Класс: B
- WP / место: S1, S9; `quack/api/email.py`, `quack/storage/deletions.py`
- Контекст: §4.2 называет ресурсы, §4.1 и §6.9 — пакеты, но не место этих двух интерфейсов.
  `AppResources` лежит в основании API и может импортировать только ядро и основание API (§4.1);
  `quack/auth` — продуктовый пакет; `quack/deletion/**` — зона A, а `DeletionJournal` выдаёт C (S9).
- Варианты: (1) `EmailSender` — `quack/api/email.py`, `DeletionJournal` — `quack/storage/deletions.py`;
  (2) новый пакет ядра на каждый клиент; (3) интерфейсы в `quack/auth` и `quack/deletion`.
- Решение: (1). Конструкторы `create_*` — рядом с классами, чтобы `quack/runtime` (A) вызывал их
  для `JobContext` (§7.3 п.6) без импорта `quack.api.resources`.
- Почему: §0.4.3 п.3 и п.5 — слои §4.1 соблюдены, новых пакетов сверх §6.9 нет; `DeletionJournal` —
  тоже S3 API (R2), как `ObjectStore`.
- Обратимость: перенос модуля и импорты.
- Тесты: `T-A6` (`tests/test_contracts.py`), `tests/storage/test_s1_resources.py`.
- Требует подтверждения владельцем: нет. A согласен (2026-11-02, ревью #74).

### D-C-103. Адрес R2 для журнала удалений — параметр `deletions_s3_endpoint`
- Класс: B
- WP / место: S9, `quack/config.py`
- Контекст: `Д-§7` даёт `DELETIONS_S3_ACCESS_KEY`/`SECRET_KEY` для `api`, `workers`, `backup`, но не
  эндпоинт; `BACKUP_S3_ENDPOINT` есть только у `backup`. Контракт `Д-§7`: несекретные адреса — в
  конфиге окружения.
- Варианты: (1) несекретный параметр `deletions_s3_endpoint`; (2) отдать `api`/`workers` секрет
  `BACKUP_S3_ENDPOINT`.
- Решение: (1), значение — из конфига окружения при выкатке (WP9).
- Почему: §0.4.3 п.2 (буква `Д-§7`: адреса — в конфиге) и п.4.
- Обратимость: один параметр.
- Тесты: `test_production_clients_and_doubles_implement_the_interfaces`.
- Требует подтверждения владельцем: нет

### D-C-104. Имена параметров отправителя письма и redirect URI Google
- Класс: B
- WP / место: WP18.1–18.2, `quack/config.py`
- Контекст: значения есть (`login@qvack.online` — `Т-§19.1`, `Д-§3.1`;
  `https://api.qvack.online/auth/google/callback` — `Д-§3.1`), имён параметров в §6.7 нет.
- Решение: `auth_email_from`, `google_oidc_redirect_url` со значениями документов.
- Почему: §0.3 — адреса в `Settings`, не в коде. Обратимость: имя параметра.
- Тесты: WP18 (`T-AU1`, `T-AU3`).
- Требует подтверждения владельцем: нет

### D-C-105. Параметры без значений в документах — `None`
- Класс: B
- WP / место: §6.7, `quack/config.py`
- Контекст: §6.7 называет `auth_success_redirect_url` и `terms_version`, но значений нет ни в
  §6.7, ни в `Т-§21.3`, `Т-§22.11`, `Д-§14.1`.
- Варианты: (1) `None` до значения владельца, потребитель явно отказывает; (2) придумать значения.
- Решение: (1). `terms_version` — версия текста согласия (продукт, владелец); без
  `auth_success_redirect_url` вход не завершает редирект.
- Исправление 2026-11-02 (по поручению владельца решать по ТЗ и техдоку, передал B): `stroke_batch_ms` 300, `line_pause_ms` 1200,
  `capture_still_ms` 700 в документах есть — таблица `Т-§21.3` (строки §15.2, §15.3, §16.2);
  в первой версии записи ошибочно отнесены к пробелам, в `Settings` теперь эти значения.
- Почему: закон 8 «отсутствие — не ноль», §0.4.3 п.2. Обратимость: значение параметра.
- Тесты: WP18 (`T-AU1`, `T-AU6`).
- Требует подтверждения владельцем: решено D-C-133 п.1–2 (поручение владельца, 2026-11-03).

### D-C-106. Ключ Modal и у `api`
- Класс: C
- WP / место: S1, S12; `_PROD_SECRETS_BY_ROLE` (WP9.2, `T-OPS6`)
- Контекст: `Д-§7` выдаёт `MODAL_TOKEN_ID`/`SECRET` только `workers`. ТЗ §4.2 даёт
  `EmbeddingClient` и `api`; `Д-§3.2` пускает `api` в Modal; `Т-§10.5` и S12: `embed_query` для
  поиска по свободному запросу (инструмент учителя в `api`, `GET /v1/search`).
- Варианты: (1) ключ у `api` и `workers`; (2) `api` ставит поиск задачей в `workers`
  (ход учителя ждал бы фон — закон 9).
- Решение: (1). В этапе I ключи — только поля `Settings`; требование секрета по роли — в WP9.2.
- Почему: §0.4.1 — `Т-§` выше `Д-§`; `Д-§3.2` согласуется; закон 9.
- Обратимость: строка таблицы ролей.
- Тесты: `T-OPS6` (WP9).
- Требует подтверждения владельцем: да — **подтверждено** 2026-11-02, вариант (1): по поручению владельца
  решать по ТЗ и техдоку («у нас пока никаких данных учеников нет. остальные решения если за мной какие есть - сверься с тз и тз доком, дай решение»), решение передал B.

### D-C-107. Контракт эндпоинтов compute WP24 и таймаут задания
- Класс: B
- WP / место: WP24, шов S10; `compute/quack_compute/contracts.py`, `backend/quack/compute_client/contracts.py`, `Settings.compute_task_timeout_s`
- Контекст: WP24 называет эндпоинты и что они делают, но не схемы и не таймаут задания;
  `Д-§3.2`: compute без сети, файл — в теле запроса, результат — в ответе.
- Варианты: (1) JSON с base64 для двоичных данных, одна схема на эндпоинт; (2) сырой двоичный
  запрос и `multipart`-ответ.
- Решение: (1). Схемы — минимальные поля из WP20.2, WP21.3, WP22.3, `Т-§13.6`, `Т-§17.6`:
  `pdf/pages` → страницы (номер, текст, WebP); `image/align` → `aligned` и WebP; `image/clean-drawing`
  (изображение и маска `segmenter`) → WebP; `image/render-strokes` (штрихи в координатах доски,
  размер) → WebP; `render/snapshots` (HTML, наборы параметров) → снимок, ошибки консоли, `blank`;
  `render/pdf` (HTML) → PDF и `page_count`. Таймаут задания — `compute_task_timeout_s` = 60 с в
  compute и backend (документы не называют; консервативно — дольше), задание сверх него убивается,
  ответ 504 → `ComputeJobTimeout`; клиент ждёт `compute_task_timeout_s + compute_timeout_sec`.
  Рендер — в процессе сервиса (`Renderer`, один Chromium с закрытием по простою), не в пуле.
- Почему: §0.4.3 п.5 (одна форма для всех эндпоинтов, без новых зависимостей), п.4.
- Обратимость: схема одного эндпоинта, один параметр.
- Тесты: `T-CM1`, `test_wp24_endpoints_are_declared`, `tests/storage/test_compute_client_s10.py`;
  `T-CM2`, `T-CM3` — с реализацией WP24.
- Требует подтверждения владельцем: нет

### D-C-108. Подмены S13: идентификаторы шагов, размер дельты, журнал
- Класс: B
- WP / место: шов S13, §8.1, WP26.2; `tests/support/llm.py`, `backend/loadtest/meta_mock.py`
- Контекст: D-B-125 задаёт формат элементов, событий и сбоев, но не размер куска текста, не вид идентификаторов в `ScriptedLLMProvider` и не то, как вызов попадает в журнал.
- Решение: (1) текст ответа идёт кусками по `STREAM_DELTA_CHARS` = 16 символов (в обеих подменах; тесты проверяют только, что кусков больше одного и что они склеиваются в ответ); (2) в `ScriptedLLMProvider` идентификаторы детерминированы: `call_{schema}_{шаг}_{n}`, `fc_…`, `rs_…`, `msg_…`. Номер шага считается по входу (сколько шагов сценария закрыты `function_call_output`), а не по порядку вызовов. Поэтому параллельные задачи e2e не мешают друг другу (§8.1, как выбор ответа по `schema_name`). `function_call` из входа без `function_call_output` — `AssertionError`; (3) весь `StreamRequest` пишется в `stream_calls`, его `base` — ещё и в `calls`, чтобы `calls_for` считал все вызовы роли; (4) при `commentary_only` поток содержит только элемент-комментарий, при `incomplete` — только дельты, а на шаге с инструментами — только `StreamCompleted(status="incomplete")` без `function_call`, повтор того же шага отдаёт вызовы; `invalid_json` на шаге с инструментами не берётся и срабатывает на шаге ответа (D-B-125 п.5, замечание B к #87); в остальных шагах первым идёт элемент `reasoning`; (5) `meta-mock` с `tools` отвечает на шаг, где во входе есть `function_call_output` на каждый `function_call`, иначе — шаг с вызовами, как в Фазе 3. Задержка профиля `measured` — целиком до начала потока.
- Почему: §0.4.3 п.5 (без новых зависимостей и параметров `Settings` — это подмены тестов), D-B-125 п.2.
- Обратимость: константы и вспомогательные функции подмен.
- Тесты: `tests/test_support_scripted_llm.py` (`test_stream_*`), `tests/test_meta_mock.py` (`test_stream_*`).
- Требует подтверждения владельцем: нет

### D-C-111. WP18: имя cookie, окно лимитов, ключ согласия, адрес ссылки
- Класс: B
- WP / место: WP18; `quack/auth/links.py`, `quack/auth/sessions.py`, `quack/auth/consents.py`
- Контекст: `Т-§19.1` и WP18 называют флаги и домен cookie, лимиты «в час», события согласия и путь `GET /auth/link/verify`, но не имя cookie, вид окна лимита, ключ идемпотентности согласия и базовый адрес ссылки в письме.
- Решение: (1) cookie `quack_session`; (2) окно лимита — календарный час: счётчики `auth:link:{email|ip}:…:{час}` в Redis с `EXPIRE 3600`, без Redis или при его ошибке — локальные (`Д-§10`); `retry_after` — до конца часа; (3) ключ идемпотентности согласия берётся из запроса (`Idempotency-Key`, как у мутирующих эндпоинтов Фазы 3): согласие, данное снова после отзыва, — новое событие, а ключ из вида и версии его бы схлопнул (закон 5); (4) ссылка в письме — абсолютный адрес `GET /auth/link/verify`, который роутер строит из запроса, без нового параметра; (5) адрес приводится к нижнему регистру (`core.learners.email` с `CHECK (email = lower(email))`). (6) Изоляция (`tests/db/test_sql_static.py`, A): все запросы `quack.auth` берут `%(learner_id)s`, кроме двух запросов до входа, где ученик ещё неизвестен: `quack/auth/links.py:_LEARNER_BY_EMAIL` (ученик по адресу) и `quack/auth/sessions.py:_SESSION_BY_TOKEN` (сессия по хэшу токена). Нужны записи в `CROSS_LEARNER_ALLOWED` — запрос к A. Выход (`revoke_session`) отзывает только сессию своего ученика.
- Почему: §0.4.3 п.5 (без новых параметров), закон 5.
- Обратимость: константа и одна функция.
- Тесты: `tests/auth/test_auth_wp18.py`.
- Требует подтверждения владельцем: нет.

### D-C-112. Перевод входа API с JWT на сессии (WP18.3) — предложение потокам A и B
- Класс: B (пересечение зон, §7.3) — принято A (D-A-112, D-A-113) и B (#103); п.1–2 выполнены в D-C-119, п.3 — B, п.4 — A (WP1.3)
- WP / место: WP18.3, WP18.4; `quack/api/auth.py` (C), объявления в роутерах A и B
- Контекст: §2.3 строка 3 удаляет пароль, JWT, `auth.users`, `/v1/auth/register|login`; WP18.3 — `learner_id` только из сессии. На входе Фазы 3 (`get_current_learner_id`) сидят роутеры всех потоков: A — `quack/domains/router.py`; B — `quack/session/deps.py`; C — `quack/artifacts/router.py`, `quack/api/learner_stream.py`, `quack/api/ratelimit.py`. Токены JWT выпускают тесты: A — `tests/domains/*` (8 файлов); B — `tests/session/*` (5); корневые `test_app_assembly`, `test_dispatcher`, `test_jobs_phase3`, `test_learner_stream`, `test_loadtest_scenario`, `test_neo4j_outage`, `test_ops_phase3`; C — `tests/artifacts/conftest.py`, `tests/e2e/system.py`, `tests/support/auth.py`.
- Предложение:
  1. `quack.api.auth.get_current_learner_id` сохраняет имя и подпись (возвращает `UUID`), но читает cookie сессии (`quack.auth.sessions.resolve_session`); нет или отозвана — 401 `UNAUTHENTICATED`. **Код роутеров A и B не меняется.**
  2. Тестовый вход: `tests/support/auth.py` получает `login(client, db, learner_id)` — строка `core.auth_sessions` и cookie на клиенте; `ensure_users_hook` и выпуск JWT уходят. Тесты A и B меняют только способ входа (одна строка на клиент). C делает это одним PR с ревью A и B (§7.3); если A или B хотят править свои тесты сами — тот же хелпер, их PR после PR C.
  3. `CONSENT_REQUIRED` на старте сессии (WP18.4): зависимость `quack.auth.require_terms` (C) объявляется на `POST` старта сессии в `quack/session/router.py` (B) — в чужом роутере только объявление (§7.3 п.3).
  4. Удаление `auth.users` и старой миграции — в WP1.3 (A), не здесь.
- Нужно: A — согласие с п.1, п.2 для `tests/domains` и корневых тестов A, п.4; B — согласие с п.2 для `tests/session` и с п.3.
- Требует подтверждения владельцем: нет (решение потоков); значения `terms_version`, `auth_success_redirect_url` — по-прежнему за владельцем (D-C-105).

### D-C-110. WP24: числа обработки изображений, разрешение страниц PDF, чистка временных файлов
- Класс: B
- WP / место: WP24; `compute/quack_compute/work.py`, `compute/quack_compute/renderer.py`, `compute/Dockerfile`
- Контекст: WP24, WP20.2, WP21.3, WP22.3, `Т-§17.6` называют, что делает каждый эндпоинт, но не числа: разрешение страниц PDF, качество WebP, пороги поиска контура листа, толщину линии пера; `Д-§4.2` говорит «чистится по возрасту раз в час», но не называет возраст и не говорит, кто чистит.
- Решение: (1) страница PDF — разрешение PyMuPDF по умолчанию, WebP — качество OpenCV по умолчанию; (2) контур листа — обычный приём OpenCV: размытие 5, Canny 75/200, приближение многоугольником с точностью 0,02 периметра, лист — самый большой четырёхугольник не меньше 0,2 площади снимка; иначе снимок как есть (`aligned = false`). Числа — именованные поля `ComputeSettings` (`align_*`, `stroke_thickness_px`, переменные окружения — их имена заглавными) по ТЗ Ф4 §0.3, по замечанию B к #92; (3) фон рисунка — порог Оцу (без числа), прозрачно всё вне маски `segmenter`; (4) линия пера — 2 px; (5) PDF — формат A4 Chromium; (6) почасовая чистка `/srv/quack/tmp` **не сделана**: удалить профиль работающего Chromium — сломать рендер, а возраст файлов документ не называет. Вопрос владельцу: чистит compute сам или cron хоста (WP9), и какого возраста файлы.
- Почему: §0.4.3 п.5 (значения библиотек по умолчанию, где документ молчит), `Т-§1.2`.
- Обратимость: значения `ComputeSettings`.
- Тесты: `compute/tests/test_wp24_endpoints.py`.
- Снимки визуализаций (по D-B-130): набор задаётся `add_init_script("window.QUACK_VIZ_PARAMS = …")`, но страница открывается не через `set_content` (у Playwright скрипты инициализации выполняются при переходе, а не при `set_content` — проверено тестом), а переходом на внутренний адрес `http://viz.quack.internal/`, который `page.route` отдаёт из памяти — без сети. Для модели и для B ничего не меняется: переменная задана до любого скрипта страницы. Ошибки консоли — сообщения `console.error` и неперехваченные исключения страницы; `blank` — все пиксели снимка одного цвета.
- Требует подтверждения владельцем: п.6 решён D-C-133 п.4 (поручение владельца, 2026-11-03). \[ПРОВЕРИТЬ\] п.1–2 на настоящих сканах и фото листов при WP20, WP22.

### D-C-109. WP19: ULID, регион и повторы S3-клиента, код ошибки загрузки
- Класс: B
- WP / место: WP19; `quack/storage/keys.py`, `quack/storage/uploads.py`, `quack/storage/objects.py`, `quack/api/errors.py`
- Контекст: `Т-§3.3` требует случайную часть ключа «например ULID», библиотеки для неё в §4.6 нет; `Д-§8.4` не называет регион бакета и таймауты клиента; ТЗ Ф4 §4.5 даёт `UPLOAD_REJECTED` 415/413 без разбивки.
- Решение: (1) ULID — на стандартной библиотеке (48 бит миллисекунд + 80 случайных, Crockford base32), как Левенштейн в §4.6. ULID стоит в ключах, которые пишутся больше одного раза на id (штрихи, фото, кропы, экспорт, визуализации, миниатюры). `materials/{id}/original.{ext}` и `pages/{n}.webp` — ровно по раскладке `Т-§3.3`: id материала новый на каждую загрузку. (2) Регион, таймауты и повторы клиента — значения botocore по умолчанию: документы их не называют. (3) Неверный тип → 415, сверх `upload_max_mb` → 413. `detect_upload` принимает набор допустимых видов эндпоинта: фото канваса — только WebP (`Т-§22.6`). (4) `put` без условной записи (`If-None-Match`): поддержка на Contabo не проверена, неизменяемость держится на случайной части ключа. (5) Нет ключа при `get` — `KeyError`, как у подмены. Сеть, таймаут, 5xx → `StorageUnavailable` → `SERVICE_UNAVAILABLE` «file storage temporarily unavailable».
- Почему: §0.4.3 п.5 (без новых зависимостей и параметров), `Т-§20.7`.
- Обратимость: одна функция или один аргумент конфигурации клиента.
- Тесты: `tests/storage/test_storage_wp19.py`.
- Требует подтверждения владельцем: нет. \[ПРОВЕРИТЬ\] до первых загрузок (`Д-§8.4` «Проверить»): регион бакета Contabo для подписи, работа подписанных ссылок в path-style. Недоступное хранилище отвечает не сразу: botocore повторяет запрос со своей задержкой (в тесте на Windows — до ~18 с на вызов). Таймаут и число повторов — решено D-C-133 п.3 (умолчания botocore).

### D-C-113. WP20.1, WP20.5: проверка домена, статус материала, multipart, размер выдачи поиска
- Класс: B; п.3 — класс C (зависимость)
- WP / место: WP20.1, WP20.5; `quack/materials/library.py`, `quack/materials/search.py`
- Контекст: WP20.1 называет `POST /v1/domains/{id}/materials` (multipart), строку, событие и одну транзакцию, но не то, как проверить домен, какие значения у `content.materials.status`, и чем разбирать multipart; WP20.5 не называет размер выдачи.
- Решение: (1) домен ученика проверяется по `domain.created` в журнале (закон 1): таблицы доменов в Postgres нет, а `quack.graph.read` (S4) ещё не готов; после S4 — через него. (2) Статус после загрузки — `uploaded`; следующий ставит разбор WP20.2. (3) **Multipart:** FastAPI разбирает формы через `python-multipart`, его нет в §4.6 и в зависимостях. Варианты: добавить `python-multipart` (стандартная зависимость FastAPI для форм) или принимать файл телом запроса с `Content-Type` (отступление от «multipart» в WP20.1). **Решение владельца 2026-11-02:** добавить `python-multipart` («конечно ставь тогда»); отдельный коммит, строка R4-C1 в реестре отклонений. (4) Размер выдачи полнотекстового поиска — аргумент `limit` функции; число задаёт маршрут, и оно будет вопросом при маршрутах — в документах его нет. (5) Маршруты WP20 делаются вместе с переводом входа на сессии (D-C-112), чтобы не писать их на JWT Фазы 3.
- Почему: §0.4.3 п.5, закон 1, §4.6.
- Обратимость: одна функция проверки домена; одно значение статуса.
- Тесты: `tests/materials/test_materials_wp20.py`.
- Требует подтверждения владельцем: п.3 — подтверждено 2026-11-02.

### D-C-114. WP21.2: структура куска штрихов, отсечение повторов, порог переполнения
- Класс: B; п.3 — решён в D-C-122 п.4
- WP / место: WP21.2; `quack/canvas/strokes.py`
- Контекст: `Т-§15.1`–`Т-§15.2` и WP21.2 задают формат точек (`dx, dy, dt, нажим`), MessagePack, zstd, кусок раз в `stroke_chunk_seconds` «или при переполнении», `ack` после записи и отсечение повтора по номеру пачки; не названы структура документа куска и порог переполнения.
- Решение: (1) кусок — `{"batches": [{"batch_no", "strokes": [{"stroke_id", "points"}], "erasures": [{"stroke_ids", "at_ms"}]}]}`; первая точка штриха абсолютная, следующие — разностью; номер пачки хранится в куске для проигрывания. (2) Отсечение повторов — множество виденных `batch_no` журнала канваса в процессе API; ключ куска — `canvas_chunk_key` с ULID, поэтому два процесса не перезапишут кусок друг друга. (3) **Порог переполнения** документы не называют: сейчас кусок пишется по времени и при закрытии сокета (`flush`). Вопрос владельцу: нужен ли порог (число точек или байт) и какое значение — тогда это новый параметр `Settings`.
- Почему: §0.4.3 п.5, `Т-§3.3`.
- Обратимость: одна функция кодирования (у куска нет потребителей, кроме проигрывания).
- Тесты: `tests/canvas/test_strokes_wp21.py`.
- Требует подтверждения владельцем: нет (п.3 — D-C-122 п.4).

### D-C-135. WP21.6: проигрывание — канвас в ключе куска штрихов, порядок кусков, ответ
- Класс: B (раскладка ключей — «ориентир» `Т-§3.3`, ТЗ Ф4 WP19)
- WP / место: WP21.6; `quack/canvas/replay.py`, `quack/canvas/router.py`, `quack/storage/keys.py`, `quack/canvas/strokes.py`
- Контекст: ориентир ключа куска — `canvas/{session_id}/{chunk}-{ulid}.bin`, но у сессии несколько канвасов (по канвасу на задачу, WP21.1), а `GET /v1/canvases/{id}/replay` отдаёт куски одного канваса. Кусок не знает своего канваса, и сервер отдаёт подписанные ссылки, не открывая куски.
- Решение: (1) ключ — `learners/{id}/canvas/{session_id}/{canvas_id}/{chunk}-{ulid}.bin`: префикс сессии из ориентира сохранён, канвас — следующий уровень; данных в старой раскладке нет. (2) Порядок кусков — миллисекунда ULID, затем номер куска: номер начинается заново с каждым сокетом (D-C-114), а случайная часть ULID внутри миллисекунды порядок не держит. (3) Ответ — `{chunks: [{url, size}], expires_in_s}`, ссылки живут `object_url_ttl_s`; повторы `batch_no` и `stroke_id` отсекает клиент (D-C-114). Только полная сессия (`companion` — 403, `T-CP1`); чужой канвас — 404.
- Почему: ТЗ Ф4 WP21.6, `Т-§15.7`, `Т-§3.3`, D-C-114.
- Обратимость: одна функция ключа; до первых данных стенда.
- Тесты: `tests/canvas/test_replay_wp21.py`; `tests/storage/test_storage_wp19.py`.
- Требует подтверждения владельцем: нет.

### D-C-125. WP23.1: артефакт — документ с версиями; конспект блоками, защита правок ученика
- Класс: B
- WP / место: WP23.1, §6.4 «Артефакты»; `quack/artifacts/documents.py`, `quack/artifacts/notes_routes.py`
- Контекст: WP23.1, `Т-§17.1`–`Т-§17.2`, П-11.1 задают версии, поля блока и «дополнение» к блоку ученика, но не называют: как хранятся блоки в `content.artifact_versions.content`, сколько артефактов одного вида у темы, как помечается дополнение, что делает запись генератора с блоками прошлой версии.
- Решение:
  1. Один артефакт вида на тему (`notes`, `cards`) или хаб (`cheatsheet`): `artifact_id = uuid5(QUACK_UUID_NS, "artifact:{kind}:{learner_id}:{topic|hub}")`. Строка создаётся при первом чтении (`INSERT … ON CONFLICT DO NOTHING`), чтобы ученик мог написать первый блок по id. Принадлежность темы или хаба — по `topic.created` / `topic.proposed` / `hub.created` в журнале ученика (закон 1, `Т-§20.7`).
  2. `content = {"blocks": [...]}`. Поля блока — имена `Т-§17.2`: `block_id`, `kind`, `content`, `owner`, `sources`, `skills`, `generatedBy`; дополнение — `supplementTo` (id блока ученика), имени в документах нет.
  3. Сохранение: блокировка строки артефакта, новая версия, `current_version` и `artifact.updated {…, text}` — одна транзакция (`Т-§2.3`). `text` — содержимое блоков через пустую строку. Ключ — `artifact.updated:{artifact_id}:{version}`. Те же блоки ещё раз ничего не пишут (закон 5). Актор: `learner` для правок, роль с версией (`artifact_writer@1`) для генератора; `created_by` — то же.
  4. Запись генератора (WP23.2): блок с `block_id` системного блока заменяет его на месте; с `block_id` блока ученика — встаёт сразу после него дополнением, блок ученика не меняется; без `block_id` — в конец. Id нового блока и дополнения — `uuid5` от артефакта, автора, номера блока во входе, блока ученика и хэша текста: повтор задачи генератора с теми же блоками ничего не пишет (закон 5, ревью B #138). Системные блоки, которые генератор не назвал, уходят: он возвращает всю системную часть. Блоки ученика остаются всегда; неотвеченное дополнение остаётся, пока к тому же блоку не пришло новое.
  5. Ученик: `PUT` правит блок (он становится `owner = learner`, дополнение перестаёт быть дополнением) или по новому `block_id` дописывает блок `custom` (§2.3 строка 14). `accept` сливает дополнение с его блоком (содержимое через пустую строку, `sources` и `skills` объединяются). `DELETE` удаляет блок вместе с его неотвеченными дополнениями. `accept` не дополнения — `422 VALIDATION_ERROR` (§4.5: новых кодов нет).
  6. Все три маршрута ученика принимают `idempotency_key` (§6.4 «изменяющие — `idempotency_key`», `T-E2`). Ключ события — `artifact.edit:{key}`. Повтор: тот же запрос, применённый к версии перед записанной, даёт её же — ответ этой версией, без записи; иначе — `409 IDEMPOTENCY_CONFLICT`. Запрос, который ничего не изменил, события не пишет, и его повтор просто снова ничего не меняет.
- Почему: WP23.1, `Т-§17.1`–`Т-§17.2`, П-11.1, §0.4.3.
- Обратимость: модель блока и одна функция слияния.
- Тесты: `tests/artifacts/test_documents_wp23.py`; маршруты в `tests/test_app_assembly.py` (`T-A1`).
- Осталось: сообщение `artifact_updated {artifact_id}` в `GET /v1/learner/stream` (§4) и маршруты карточек и cheat sheet — WP23.4–23.5. Старые маршруты заметок Фазы 3 (`/v1/artifacts/notes`, `GET /v1/artifacts`, `/v1/artifacts/search`) удаляются отдельно (§2.3 строка 14): на них держится `quack/artifacts/router.py` вместе с практикой.
### D-C-129. WP22.2: снимок листа — объект, затем `photo.uploaded`; кто может загружать
- Класс: B
- WP / место: WP22.2; `quack/canvas/photos.py`, `quack/canvas/router.py`, `quack/api/auth.py` (`get_any_session`)
- Контекст: WP22.2 называет маршрут, ключ объекта и событие; `T-CP1` — компаньон загружает в свой канвас. Формат тела, проверка типа и идемпотентность не названы.
- Решение:
  1. `POST /v1/canvases/{id}/photos` — multipart `file`, как загрузка материалов (D-C-116). Тип — по содержимому, только WebP (`Т-§22.6`), размер — `upload_max_mb`; иначе 415/413 `UPLOAD_REJECTED`.
  2. Объект `photos/{photo_id}/original-{ulid}.webp` пишется первым, затем `photo.uploaded {canvas_id, photo_id, object_key}` с `session_id` канваса (`Т-§2.3`, `Т-§20.7`). Отдельной таблицы снимков в §6.1 нет — снимок живёт объектом и событием.
  3. Кто: `get_any_session` (любая область) и `may_open` канваса — `full` владельца или `companion` этого канваса; компаньон другого канваса — 403 `UNAUTHORIZED`, чужой канвас — 404.
  4. Ключа идемпотентности нет: каждый снимок — новый (исключение `T-E2`, как у материалов).
- Почему: WP22.2, `Т-§22.6`, `T-CP1`, D-C-128.
- Обратимость: один модуль, один маршрут.
- Тесты: `tests/canvas/test_photos_wp22.py`; маршрут — `T-A1`, исключение — `T-E2`.
- Требует подтверждения владельцем: нет.

### D-C-130. §4.8: `artifact_updated` и `material_parsed` в канале ученика
- Класс: B
- WP / место: §4.8; `quack/api/learner_stream.py`
- Контекст: §4.8 добавляет в `GET /v1/learner/stream` виды `projection_advanced {topics}`, `jobs_changed {session_id}`, `verdict_revised {answer_seq}`, `artifact_updated {artifact_id}`, `material_parsed {material_id}`. Откуда канал берёт их, не сказано.
- Решение:
  1. `artifact_updated` и `material_parsed` — из журнала Фазы 4: новые `artifact.updated` / `material.parsed` после открытия канала. Канал подписан на сигнал `learner:{id}`, который `append_events` публикует после коммита (`Т-§2.3`), и по нему дочитывает журнал от своего курсора; без Redis — тем же опросом раз в `learner_stream_poll_ms`. События до открытия канала не повторяются.
  2. `verdict_revised {answer_seq}` (`Т-§5.6`) — по ответу B в #151 (D-B-167): отдельного события пересмотра нет, его запускает `task.key_disputed {task_id}`. Канал по `task_id` находит ответы `answer.submitted` этой задачи, у которых уже есть `verdict.recorded`, и на каждый шлёт `verdict_revised`. `task.markup_disputed` вердикт не пересматривает — сообщения нет.
  3. `jobs_changed` (очередь A, `Т-§13.8`) и `topics` у `projection_advanced` (проектор A, `Т-§2.4`) — не события журнала; источник называет A (вопрос — в #151).
- Почему: §4.8, `Т-§1.6`, `Т-§2.3`.
- Обратимость: одна таблица видов и одно чтение.
- Тесты: `tests/test_learner_stream.py` (Redis и опрос).
### D-C-127. WP20.4: предвыборка кусков по графу и векторный поиск по `chunk_emb`
- Класс: B
- WP / место: WP20.4; `quack/graph/read/materials.py`, `quack/config.py` (`vector_search_reserve`)
- Контекст: WP20.4 называет `materials_prefetch(skills)` и `materials_search(vector, skills)`. `Т-§10.5` фильтрует поиск «по ученику и домену», WP20.4 — по `learnerId` и `searchable`. Имя материала у узла `Material` и порядок кусков в предвыборке не названы.
- Решение:
  1. Обе функции принимают `learner_id` (и `domain_id` у поиска) явно: изоляция (§4.3) — в запросе, сборка инструментов учителя привязывает их к ученику хода.
  2. Фильтр внутри индекса — `learnerId`, `domainId`, `searchable` (все три объявлены у `chunk_emb`, `Т-§3.4.6`; только равенства через AND). Запасной путь не нужен: WP0 п.3а «работает».
  3. Запас внутри `SEARCH` — параметр `vector_search_reserve = 30` (`Т-§3.4.6`, `Т-§10.5`; закон о числах в конфиге). `SEARCH` принимает предел литералом — подставляется целое из настроек, не ввод.
  4. Порядок поиска: куски, учащие навыку в фокусе (`TEACHES`), выше любых других, внутри — по близости; итог — `k` (`materials_k`). Предвыборка: больше навыков фокуса — раньше, затем материал и страница.
  5. Ответ — `ChunkHit {chunk_id, material_id, page, text, teaches_focus, score}`. Имени материала нет: у `Material` в `Т-§3.4` свойства не названы, в `content.materials` колонки имени нет. Закрыто D-B-171 (#153): `MaterialChunk.material_name` необязателен, источник — метка куска и страница.
- Почему: WP20.4, `Т-§10.5`, `Т-§3.4.6`, WP0 п.3а.
- Обратимость: один модуль чтения.
- Тесты: `tests/materials/test_materials_search_wp20.py` (Neo4j).
### D-C-128. WP22.1: привязка телефона — токен, обмен, область `companion`
- Класс: B
- WP / место: WP22.1, §6.4 «Вход» и «Канвас»; `quack/auth/pairing.py`, `quack/auth/router.py`, `quack/api/auth.py`, `quack/api/openapi.py`
- Контекст: WP22.1, `Т-§16.1`, `T-CP1` («любой другой эндпоинт → 403»). Таблица — `core.pairing_tokens` (D-A-128, #142). Код ошибки для 403 и место проверки области не названы; §4.5 запрещает новые коды.
- Решение:
  1. `POST /v1/canvases/{id}/pairing` (сессия `full`): токен 128 бит (`new_token`), в базе хэш, срок `pairing_ttl_s`; сессия и задача — из канваса (`content.canvas_boards`, свой SQL пакета `quack.auth`, §4.1). Чужой канвас или канвас без сессии — 404. Ключа идемпотентности нет: каждый вызов выдаёт новый одноразовый токен, события нет (исключение `T-E2`).
  2. `POST /companion/pair {token}` — публичный маршрут: погашение одним `UPDATE … SET used_at … WHERE used_at IS NULL AND expires_at > now RETURNING` и сессия `companion` этого канваса — одна транзакция; кука — как у обычного входа (`Т-§19.1`). Неизвестный, использованный или истёкший токен — `401 UNAUTHENTICATED`. Ключ погашения — в `CROSS_LEARNER_ALLOWED` (согласовано в #142).
  3. Область: `get_current_session` пропускает только `full`; сессия `companion` на любом маршруте за ней — `403 UNAUTHORIZED` (код Фазы 3, §4.5 «остальные коды не меняются»). Сокет канваса проверяет область сам (`may_open`, D-C-121); загрузка снимков (WP22.2) получит свою зависимость «`full` или `companion` этого канваса».
- Почему: WP22.1, `Т-§16.1`, `T-CP1`, §4.5.
- Обратимость: одна зависимость, один модуль.
- Тесты: `tests/auth/test_pairing_wp22.py` (`T-CP1`); маршруты — `T-A1`, исключение — `T-E2`.
- Требует подтверждения владельцем: нет.
### D-C-139. WP23.2: обновление конспекта темы — вход `artifact_writer@1` по четырём слоям, проверка выхода
- Класс: B (сборка входа и проверка выхода роли C)
- WP / место: WP23.2, `Т-§17.3`, `Т-§17.7`; `quack/artifacts/topic_refresh.py`, задача `artifact.topic_refresh`
- Решение:
  1. **Слой 1** — шаблоны типов уже в слое 1 файла `artifact_writer@1` («Templates by type», D-C-132); вход называет тип (`notes`). Отдельных файлов шаблонов нет: «системные шаблоны — файлы промптов с версиями» (`Т-§17.8`) — это сам файл промпта.
  2. **Слой 2** — скилла ученика нет до WP23.6 (`content.learner_skills`): `None.`.
  3. **Слой 3**: тема и её навыки (`GraphReader.topic_memory`, D-A-137; метки `S`), куски материалов по навыкам темы (`materials_prefetch`, `materials_k`; метки `P`, страница). «Формулировки, которые сработали» — код по журналу (`Т-§17.3` п.3, подтверждено A в #154): успех — свидетельство `skill_history` с `source = code` (Т1), `correct`, без помощи; показ — последний `turn.committed`, где навык есть в `markup.exposures`, раньше этого успеха (по `seq`); формулировка — текст хода. Граф тем — `topic_requires(domain_id)` (`REQUIRES` между темами, `Т-§4.6`): имена тем, на которые опирается тема, и тем, которые опираются на неё (имена — тем же `topic_memory`).
  4. **Слой 4**: заблуждения темы с цитатами (`MisconceptionFact.quotes`, метки `M`), навыки, где понадобились подсказки (`skill_history`, `assistance ≠ none`), прочно известные — с уровнем `proven`.
  5. **Проверка выхода** (`Т-§17.3`): неизвестная метка куска или навыка и номер блока вне текущей версии — нарушение; тогда одна повторная генерация (ключ `…:retry`), затем такие элементы отбрасываются. Допустимые `kind` держит схема. Карточки (`cards`) — WP23.4, здесь не пишутся.
  6. Блоки идут в `write_system_blocks` (D-C-125): блоки ученика не переписываются, повтор задачи (тот же ключ — тот же ответ из outbox) новой версии не даёт. Ключ вызова — id задачи очереди.
  7. Читатель — `create_graph_reader(ctx.neo4j, ctx.settings)` (D-A-137); до G2 он поднимает `NotImplementedError`, тесты передают `FixedGraphReader`. Регистрация вида в `JOB_KINDS`/`HANDLER_MODULES` — зона A (T-Q7), как у `photo.pipeline` (#161).
- Почему: ТЗ Ф4 WP23.2, `Т-§17.3`, `Т-§17.7`, `Т-§17.8`, `Т-§10.5`; D-A-137, D-C-125, D-C-132.
- Обратимость: код C.
- Тесты: `tests/artifacts/test_topic_refresh_wp23.py`.
- Требует подтверждения владельцем: нет.

### D-C-146. CI в два слоя и разбор набора тестов — `backend/TESTS.md`
- Класс: B (C по §7.3: `.github/**`, `backend/TESTS.md`; мандат владельца: перестроить CI и решить, какие тесты не нужны)
- WP / место: WP9.3, WP1.3, §4.1, §8, §8.15; `.github/workflows/ci.yml`, `backend/TESTS.md`, `tests/test_ci_layers.py`
- Контекст: полный прогон (~8 мин, службы Postgres, Neo4j, Redis) шёл на каждый push в `main` и `phase-4` и на каждый PR, а ветки потоков не проверялись совсем; `compute` и `images` собирались при любой правке; в наборе 1739 тестов в 174 файлах, из них много принадлежит коду Фазы 3, который Фаза 4 заменяет.
- Решение:
  1. Триггеры: `pull_request`; `push` в `main`, `phase-4`, `phase-4-a`, `phase-4-b`, `phase-4-c`. `pull_request_target`, `workflow_run`, права токена и закрепление действий по SHA — без изменений.
  2. Job `fast` (без служб, на каждое событие): `uv sync --frozen`, `ruff check`, `ruff format --check`, `pyright`, `pytest -q` без `QUACK_TEST_REQUIRE_INFRA`: тесты на службах пропускаются, остальное идёт (~1100 тестов). Job `backend` (полный, `QUACK_TEST_REQUIRE_INFRA=1`) — только на pull request и push в `main`, `phase-4`; `ruff` и `pyright` из него убраны (они в `fast`).
  3. `compute` и `images` всегда сообщают результат (обязательные проверки), но тяжёлые шаги идут только если `git diff --name-only` относительно базы PR (`github.event.pull_request.base.sha`) или `github.event.before` задел `compute/` (для `images` — `backend/` или `compute/`) либо `.github/workflows/`; без базы (новая ветка) идёт всё. `images` не идёт на push в ветки потоков.
  4. `publish` ждёт `backend`, `fast`, `squawk`, `compute`, `images` и работает только на push в `main` и `phase-4` (раньше — на любой push; теперь ветки потоков в триггерах, и без этого условия они бы публиковали образы).
  5. Тесты разобраны на A (Фаза 4, оставить), P (свойство остаётся, перенос на `Db`), S (стенды), X (e2e и сборка API — не тронуты), B (код Фазы 3, снимается вместе с кодом, список — `TESTS.md` §4), C (удалено сейчас). Удалены только C: `test_stream_c_defaults_equal_phase_2_values`, `test_rate_limit_constants_are_gone`, `test_stub_has_math_queues`, файл `session/test_fixtures.py`; причины и покрытие — `TESTS.md` §5. B не удалены: код Фазы 3 ещё в рантайме (проверено: все его модули достижимы из `quack.cli` и `quack.api.app`).
  6. Политика (`TESTS.md` §1): тест остаётся, если ловит шов, изоляцию, идемпотентность, гонку, ошибку проверки ответа или недетерминизм; не остаются проверки значений по умолчанию, «надгробия», самопроверки помощников и второй тест того же поведения.
- Почему: `Д-§5.2`, `Д-§5.3`; ТЗ Ф4 WP9.3, WP1.3, §8.15 п.1–2.
- Обратимость: условия `if` и список триггеров в одном файле; удалённые тесты — в истории git.
- Тесты: `tests/test_ci_layers.py` (8 тестов), `tests/test_ci_squawk.py`.
- Требует подтверждения владельцем: нет. Обязательными проверками ветки (branch protection) надо сделать `fast`, `backend`, `squawk`, `compute`, `images` — настройка репозитория, не файл.

### D-C-145. §4.8: `projection_advanced {applied_seq, topics}` и отметки Фазы 4 (#188)
- Класс: B
- WP / место: §4.8, WP7; `quack/api/learner_stream.py`
- Решение:
  1. `applied_seq` и `last_seq` берутся из `ops.projector_offsets.seq` и `core.learners.seq` через `Db`. Таблиц Фазы 3 (`applied_seq`, `core.learner_seq`) в базовой миграции Фазы 4 нет. Отметку пишет проектор `rules@3` (D-A-181).
  2. Объект проектора `{"applied_seq", "topics"}` в `projector:applied:{id}` (D-A-190) даёт `projection_advanced {applied_seq, topics}` («публикует, какие темы изменились», `Т-§2.4`). Опрос без Redis тем не знает и шлёт только `applied_seq`.
- Почему: §4.8, `Т-§2.4`, `Т-§1.6`.
- Обратимость: одно чтение и один разбор.
- Тесты: `tests/test_learner_stream.py`.
- Требует подтверждения владельцем: нет.

### D-C-142. WP9.3: шаг squawk в CI — новые миграции, правила вне `Д-§5.2` п.2 исключены
- Класс: B (C по §7.3: `.github/**`; конфиг — S16 A, D-A-187, #185)
- WP / место: WP1.4, WP9.3, `Д-§5.2` п.2, `Д-§6.2`; `.github/workflows/ci.yml` (job `squawk`), `tests/test_ci_squawk.py`
- Контекст: `Д-§5.2` п.2 — «squawk по новым миграциям: блокирующие ALTER и индексы без CONCURRENTLY». squawk 2.71.0 с настройками по умолчанию даёт ещё и предупреждения вне этой области. На текущих миграциях `phase-4` это `require-lock-timeout`, `require-statement-timeout` (вопрос A в #187), `ban-drop-schema` (откат базовой) и `ban-drop-column`; на новых файлах — ещё `prefer-robust-stmts`.
- Решение:
  1. Отдельный job `squawk`. Проверяются файлы `backend/db/migrations/*.sql`, добавленные или изменённые относительно базы PR (`--diff-filter=AM`); на push — относительно `before`. Без базы и без новых миграций проверять нечего.
  2. squawk-cli закреплён: `SQUAWK_VERSION = 2.71.0` (≥ 2.51.0). Запуск — `--config backend/.squawk.toml` (`pg_version`, `assume_in_transaction`, `Д-§6.2`).
  3. Исключены правила вне области п.2: тайм-ауты блокировки и оператора, повторяемость операторов, удаление колонок и схем. Правила индексов (`require-concurrent-index-creation`, `-deletion`, `ban-concurrent-index-creation-in-transaction`) и блокирующих `ALTER` остаются. Тест не даёт их исключить.
  4. `publish` ждёт `squawk` (`Д-§5.2`: без всех проверок не сливается).
- Проверено на squawk 2.71.0:
  - четыре текущие миграции проходят;
  - `CREATE INDEX CONCURRENTLY` без `-- migrate:up transaction:false` и `-- squawk-disable-assume-in-transaction` падает (`ban-concurrent-index-creation-in-transaction`);
  - обычный `CREATE INDEX` падает (`require-concurrent-index-creation`);
  - правильный файл с обеими строками проходит.
- Почему: `Д-§5.2` п.2, `Д-§6.2`; ТЗ Ф4 WP1.4, WP9.3.
- Обратимость: список исключений в одной строке job.
- Тесты: `tests/test_ci_squawk.py`; у A — `tests/db/test_squawk_config.py` (T-DB7).
- Требует подтверждения владельцем: нет.

### D-C-141. §4.8: `jobs_changed` — канал `learner:{id}`, объект от очереди
- Класс: B (C — потребитель канала; издатель — очередь A, D-A-165, #182)
- WP / место: §4.8, WP7.4, T-D10; `quack/api/learner_stream.py`
- Контекст: §4.8 называет сообщение `jobs_changed {session_id}` (плашка `Т-§13.8`), но не говорит, откуда канал его берёт. Это не событие журнала (вопрос A в #183, D-C-130 п.3).
- Решение:
  1. Канал — `learner:{learner_id}` (`learner_signal_channel`). `Т-§1.2` называет единственный канал ученика: «learner:{id} — новые события и изменения тем». `Т-§13.8`: изменения статуса приходят по каналу изменений.
  2. Очередь A после коммита публикует JSON-объект `{"kind": "jobs_changed", "session_id"}` на каждую смену статуса задачи с `visible` и непустым `session_id`. Сбой публикации только пишется в лог (закон 9).
  3. Канал отличает этот объект от сигнала `append_events` (голое число `seq`) и сразу шлёт SSE `jobs_changed {session_id}`, журнал при этом не читает. Данные плашки — `GET /v1/sessions/{id}/jobs`.
  4. Без Redis сообщения нет. Плашку клиент читает тем же GET после переподключения (`Т-§1.6`: «после переподключения клиент делает один повторный запрос»).
- Почему: §4.8; `Т-§1.2`, `Т-§1.6`, `Т-§13.8`; закон 9.
- Обратимость: формат объекта — одна строка у издателя и одна у канала.
- Тесты: `tests/test_learner_stream.py::test_learner_stream_forwards_jobs_changed`; у A — `tests/jobs/test_queue_p4.py::test_status_change_of_a_visible_job_publishes_jobs_changed`.
- Требует подтверждения владельцем: нет.

### D-C-140. WP23.4: карточка — задача банка; хранение, правка и удаление
- Класс: B (C по §7.3; швы — `quack.practice.task_store` B, строка проектора A D-A-125 п.1)
- WP / место: WP23.4, §6.4 «Артефакты»; `quack/artifacts/cards.py`, `quack/artifacts/cards_routes.py`, `quack/artifacts/documents.py`
- Контекст: «Карточка — задание с разметкой» (`Т-§17.5`); ответ — `answer.submitted {task_id, source: card}` (§6.3), а проектор берёт навыки из разметки задачи (D-A-125 п.1). Документы не говорят, где лежит задача карточки и что делает её правка: задача банка не меняется (`Т-§5.1`).
- Решение:
  1. Каждая карточка — задача банка: `create_task` B (`content.tasks` + `task.created`), `card_id` = её `task_id`. Ответ на карточку — обычное действие `answer` с `source = card`: проверка B и строка проектора A работают без изменений, нового события нет (каталог S3 заморожен).
  2. Артефакт `cards` (один на тему, D-C-125) хранит, что видит ученик: `{card_id, kind, prompt, owner, generatedBy}`. Версии и `artifact.updated` — тот же механизм, что у конспекта: содержимое версии выбирается по виду (`_CODECS`), `text` события — тексты карточек.
  3. Не больше `cards_per_topic_max` на тему: новые карточки сверх лимита не пишутся; уже записанная карточка второй раз не добавляется (закон 5).
  4. `PATCH /v1/cards/{id} {prompt, idempotency_key}`: новая задача-преемник с новым условием и той же разметкой, семьёй, источником и проверкой. Её id — `uuid5(card_id, "card.edit:{key}")`, актор `learner`. Карточка в артефакте указывает на преемника, `owner = learner`, и всё это в одной транзакции. Старая задача остаётся в банке.
  5. `DELETE /v1/cards/{id}` пишет следующую версию без карточки; задача остаётся в банке, как всякая показанная.
  6. Повтор запроса с тем же ключом находит артефакт по ключу (карточки может уже не быть в текущей версии) и ничего не пишет. Тот же ключ с другим телом даёт 409. Чужая карточка даёт 404.
  7. Генерация карточек (`artifact_writer@1`, вывод `cards`): разметка из меток переводится в ids через `markup_record` B (#148), `validate_markup` (WP12.3), затем `create_task` и `write_system_cards`. Это следующий шаг, после слияния #148.
- Почему: ТЗ Ф4 WP23.4, §6.3, §6.4; `Т-§17.5`, `Т-§5.1`, `Т-§14.3`; П-11.2; D-A-125 п.1, D-C-125.
- Обратимость: формат содержимого версии `cards` до первых карточек на проде.
- Тесты: `tests/artifacts/test_cards_wp23.py`; `tests/artifacts/test_documents_wp23.py`.
- Требует подтверждения владельцем: нет.

### D-C-137. WP21.3: исправление прочтения строки — `POST /v1/canvases/{id}/lines/{line_id}/correct`
- Класс: B (ответ и ключ маршрута; §4.5)
- WP / место: WP21.3, §6.4; `quack/canvas/recognition.py` (`correct_line`), `quack/canvas/router.py`
- Решение: (1) тело `{idempotency_key, text, sympy?}` → `handwriting.corrected {canvas_id, line_id, text, sympy}`, actor `learner`, `session_id` канваса; ключ события — `handwriting.corrected:{idempotency_key}` (закон 5, T-E2: повтор — тот же ответ без событий, другой текст с тем же ключом — 409). (2) Исправить можно только строку, которая есть в журнале этого канваса (`canvas.line_committed` с этим `line_id`); чужой канвас или нет строки — 404. Стёртую строку исправить можно: стирание — не удаление (`Т-§15.1`). (3) Ответ 200 — `{canvas_id, line_id, text, sympy}`. Только полная сессия (`companion` — 403, `T-CP1`). (4) Повторный диагноз ставит диспетчер A (`canvas.rediagnose`, P1, `rules_p4.py`); обработчик повторного диагноза — с вызовом `canvas_diagnoser` (WP21.4, D-C-136).
- Почему: ТЗ Ф4 WP21.3, §6.4, `Т-§2.8`, П-9.7, §4.5.
- Обратимость: маршрут C.
- Тесты: `tests/canvas/test_line_correct_wp21.py`; `tests/e2e/test_isolation_and_idempotency.py` (T-E2 — обход открывает канвас со строкой).
### D-C-134. WP22.3: конвейер фото — режим листа `recognizer@1`, сравнение с листом, рисунки ждут `segmenter`
- Класс: B (схема роли C, ключи строк фото); рисунки — BLOCKED-C-204
- WP / место: WP22.3, S8; `quack/canvas/photo_pipeline.py`, `quack/canvas/recognition.py`, `quack/prompts/recognizer/v1.md`
- Решение:
  1. **Два режима `recognizer@1`** — как `builder@1` (D-B-140): переменная `mode` (`line` — строка пера, WP21.3; `sheet` — лист фото), по схеме на режим. `RecognizerSheetOutput {lines: [SheetLine]}`, `SheetLine` — поля `RecognizerOutput` плюс `kind: drawing` и `bbox {left, top, right, bottom}` — доли ширины и высоты изображения от левого верхнего угла (рамка примерная, `Т-§22.6`; строгая схема не принимает кортеж). Отдельной версии промпта ТЗ не называет (строка `recognizer@1` таблицы промптов).
  2. **Шаги 1–4** (`Т-§22.6`): `/v1/image/align` → один вызов `recognizer` по выровненному снимку (`image/webp`, ключ `recognizer:photo:{photo_id}`) → выравнивание последовательностей с наибольшим числом совпавших пар (пара — только при нормализованном расстоянии Левенштейна ниже `sheet_line_match_threshold`) → в журнал новые и изменённые строки `canvas.line_committed {source: photo, photo_id, bbox, stroke_ids: []}`, время строки — время `photo.uploaded`; строка, ставшая зачёркнутой, — `canvas.line_erased`. Впервые увиденная зачёркнутая строка пишется обоими событиями: она прочитана и стёрта.
  3. **С чем сравнивается снимок.** «Прошлый снимок» — текущий лист канваса по журналу: последние прочтения строк фото этого листа, сверху вниз по `bbox.top`. Отдельного события листа в каталоге S3 нет, поэтому лист — в id строки: `{sheet}:{photo}:{n}`, где `sheet` — снимок, открывший лист. Изменённая строка сохраняет id; ключ события — `canvas.line_committed:{canvas}:{line}:{photo}`. Совпало меньше `new_sheet_match_share` строк листа — новый лист: все строки снимка новые.
  4. **Повтор задачи** даёт то же прочтение, и каждая строка совпадает сама с собой — ничего нового (закон 5). Ошибки compute и модели не глотаются: задача повторяется очередью.
  5. Источник строки — весь снимок с рамкой (`Т-§22.6`), куски не сохраняются. Снимок без канваса — WP22.4, обработчик ничего не пишет.
  6. **Шаг 5 (рисунки)** — BLOCKED-C-204: в шлюзе нет адаптера `segmenter` (`sam-3.1`, WP10.1 по итогу WP0 п.3д, B). Рисунки считаются и пропускаются (`photo_drawings_skipped`).
  7. Регистрация обработчика в сборке workers — строка `quack.canvas.photo_pipeline` в `HANDLER_MODULES` (`quack/runtime/__init__.py`, зона A) — поручение A.
- Почему: ТЗ Ф4 WP22.3, `Т-§22.6`, `Т-§16.3`, D-B-140, D-C-123.
- Обратимость: код C; формат id строк фото — до первых данных стенда.
- Тесты: `tests/canvas/test_photo_pipeline_wp22.py`; `tests/llm/test_labels.py` (два режима `recognizer@1`).
### D-C-136. WP21.4 п.3: роль `canvas_diagnoser@1` — схема выхода, промпт, перевод в диагноз
- Класс: B (схема роли C, S8); слой 1 — черновик по тезисам документа, как D-C-132
- WP / место: WP21.4 п.3, WP21.5, S8; `quack/canvas/diagnosis.py`, `quack/prompts/canvas_diagnoser/v1.md`
- Решение:
  1. `CanvasDiagnoserOutput {error: bool, gap_type, misconception: M-метка|null, misconception_hypothesis: str|null, root_cause: S-метка|null, confidence: 0..1}`. Поля — один к одному с `DiagnosisRecord` (`observation.diagnosed`, S3), кроме `kind`: `error`/`question` решает код по `mark_min_confidence` (`Т-§15.4`, WP21.5), а не модель. Перечень `gap_type` — `GapType` S3; `none` — нет ошибки.
  2. Нет ошибки (`error = false`, или `gap_type = none`) — диагноза нет, события нет (D-C-120). Известное заблуждение важнее гипотезы: при обоих гипотеза отбрасывается («не больше одного», как у `second_opinion@1`).
  3. Входные блоки: условие, пространство решения, навыки задачи (метки `S`), известные заблуждения (метки `M`), предыдущие строки, новая строка (`Т-§15.4` п.3: «условие, пространство решения, предыдущие строки»). Метки — `LABEL_FIELDS`: `misconception`, `root_cause`.
  4. Слой 1 — черновик по тезисам `Т-§15.4`, П-9.7, `Т-§7.1`: «ничего» и другой путь — не ошибка; ложный диагноз хуже пропущенного; неуверенный диагноз — только вопрос.
  5. Вызов роли (сборка блоков, символьная проверка, пространство решения, второе мнение) — следующий шаг WP21.4, когда в `phase-4` будет пространство решения B (#144).
- Почему: ТЗ Ф4 WP21.4 п.3, S8, §7.3 п.2; `Т-§15.4`, `Т-§7.1`, `Т-§12.4`; D-C-120, D-C-132.
- Обратимость: схема и промпт роли C; до первого вызова.
- Тесты: `tests/canvas/test_diagnosis_wp21.py`; `tests/llm/test_labels.py`.
- Требует подтверждения владельцем: нет.

### D-C-133. Открытые вопросы C к владельцу, решённые по документам (владелец поручил C, 2026-11-03)
- Класс: B (значения конфигурации и эксплуатации); решения приняты C по прямому поручению владельца («детально сверяешься с документацией и принимаешь решение сама»)
- WP / место: §6.7, WP18, WP19, WP24; `compose.yaml`, `deploy/env.example`, `compute/quack_compute/tmp_sweeper.py`, `compute/quack_compute/settings.py`
- Решение:
  1. **D-C-105, `auth_success_redirect_url`** = `https://app.qvack.online/`: фронтенд — app.qvack.online (`Д-§2.1`, `Д-§3.1`), вход возвращает ученика туда (`Т-§19.1`). Значение — в окружении `api` (`compose.yaml`, `deploy/env.example`); умолчание кода остаётся `None` (без значения — 200 с кукой, как раньше).
  2. **D-C-105, `terms_version`** = `2026-11`: документы версии не называют; это первая версия текста согласия для прода хакатона (`Т-§19.4`, П-14.6), в той же форме, что уже используют тесты и стенд. Новый текст — новая версия и повторное согласие (`T-AU6`). Тоже окружение `api`.
  3. **D-C-109, таймаут и повторы S3-клиента** — остаются умолчания botocore: документы чисел не дают (`Д-§8.4`), новое число без основания запрещает §0.4.3. Регион бакета и подписанные ссылки в path-style — по-прежнему [ПРОВЕРИТЬ] на стенде (`Д-§8.4` «Проверить»), это проверка, а не решение.
  4. **D-C-110 п.6, чистка `/srv/quack/tmp`** (`Д-§4.2` «чистится по возрасту раз в час»): чистит сам compute — единственный, кто пишет в этот том (`TMPDIR`, `Д-§3`). Раз в `tmp_sweep_interval_s = 3600` удаляется элемент верхнего уровня, чей самый новый файл старше `tmp_max_age_s = 3600` (одного интервала: файлы задач живут секунды). Профиль работающего Chromium (`playwright_*`) не трогается, пока браузер открыт.
  5. `JWT_SECRET` удалён из `deploy/env.example`: JWT удалён (D-C-119).
- Почему: поручение владельца; `Д-§2.1`, `Д-§3.1`, `Д-§4.2`, `Д-§8.4`, `Т-§19.1`, `Т-§19.4`, §0.4.3.
- Обратимость: значения окружения и настроек compute.
- Тесты: `tests/test_compose_contract.py`; `compute/tests/test_tmp_sweeper.py`.
- Требует подтверждения владельцем: нет (решение передано C).

### D-C-120. `observation.diagnosed` пишется только при найденной ошибке
- Класс: B (тип C по §6.3; владелец поручил C решить по документам, 2026-11-03)
- WP / место: WP21.4 п.3, WP5.1, §6.3; `Т-§7.1`, `Т-§15.4`, `Т-§7.6`, П-9.7; будущий код C `quack/canvas/` (быстрый слой)
- Контекст: Q-A-3 п.1 и D-A-119 оставили открытым, пишет ли C `observation.diagnosed`, когда `canvas_diagnoser` ошибки в строке не нашёл. Если да — нужна новая версия схемы после заморозки S3 (§0.4.4 п.2).
- Решение: **нет.** Событие пишется только тогда, когда диагноз нашёл ошибку: `diagnosis.kind` ∈ {`error`, `question`} (Q-A-3 п.1), `gap_type` — один из типов пробела ошибки, никогда не `none`. Если диагноз ошибки не нашёл, события нет, пометки нет (WP21.5: «иначе без пометки»), и второе мнение не ставится. Схема S3 не меняется.
- Почему (по документам):
  1. `Т-§7.1`: быстрый слой канваса «шаги дают диагноз — тип пробела, заблуждение, корень ошибки». Все три поля описывают ошибку, а результат шага без ошибки в таблице источников свидетельств не назван.
  2. WP5.1: каждое `observation.diagnosed` проектор превращает в `Evidence` с `outcome = incorrect` и ярусом по методу. Событие «ошибки нет» дало бы ложное отрицательное свидетельство, а отличать такое событие проектор по документам не умеет. Пришлось бы вводить новое правило WP5.1, которого в ТЗ нет.
  3. `Т-§15.4` п.1 и `Т-§7.1`: подтверждённый шаг свидетельством уровня не становится, «свидетельство уровня даёт итог задачи». Положительному диагнозу шага в памяти места нет.
  4. П-9.7, WP21.4 п.2: шаг, которого нет в пространстве решения, — «не ошибка». Его нужно молча пропустить, а не записывать.
  5. `gap_type = none` в перечне `Т-§7.6` — значение для положительного элемента извлекателя по эпизоду, а не для диагноза шага.
  6. Закон 6: провенанс строки и так есть — `canvas.line_committed`. Извлекатель видит строку в эпизоде (`Т-§15.1`, `Т-§7.6`) и сам толкует свободное.
- Обратимость: если владелец решит иначе — новая версия схемы `observation.diagnosed` и правило WP5.1 (класс C, §0.4.4 п.2).
- Тесты: в WP21.4 (C) — строка без ошибки не пишет `observation.diagnosed`; диагноз с ошибкой пишет `gap_type ≠ none`.
- Требует подтверждения владельцем: нет (поручено решить C).

### D-C-131. WP21.3: распознавание строк пера, набранные формулы, стёртые строки
- Класс: B
- WP / место: WP21.3, WP21.2; `quack/canvas/recognition.py`, `quack/canvas/socket.py`
- Контекст: WP21.3 и `Т-§15.3` задают путь «строка → изображение (compute) → `recognizer` → `canvas.line_committed`», `typed_formula` → LaTeX и `canvas.line_erased`. Откуда брать штрихи строки, формат картинки, поведение при отказе, ключи событий и форма `line_recognized` не названы.
- Решение:
  1. Штрихи строки — из пачек, пришедших в этот сокет (`LineBook`); стёртые штрихи в строку не входят. Рамка рендера — по точкам штрихов (соглашение клиентского `bbox` документы не фиксируют), `bbox` клиента переносится в событие как есть.
  2. Картинка — WebP от compute `/v1/image/render-strokes` (контракт WP24); `ImageInput("image/webp")` (D-B-170). Принимает ли Spark WebP — [ПРОВЕРИТЬ] на стенде (DoD п.6a), иначе PNG в рендере compute.
  3. Вызов роли — из обработчика сокета напрямую, в фоновой задаче (сокет продолжает подтверждать пачки); ключ события и вызова — `canvas.line_committed:{canvas}:{line}`: повтор `line_ready` с теми же штрихами даёт тот же ответ через outbox и ничего не пишет (закон 5). Актор — `recognizer@1`.
  4. Отказ compute или модели: ничего не пишется, клиенту — `line_recognized {line_id, recognized: false}`, клиент может повторить `line_ready`. Успех — `line_recognized {line_id, recognized: true, kind, text, sympy, confidence, crossed_out}` всем устройствам канваса (П-9.7 «касание строки показывает, как она прочитана»).
  5. `typed_formula {block_id, latex}` — `canvas.line_committed {source: typed, line_id: block_id, kind: latex, recognition_confidence: 1}`; ключ содержит хэш текста: новый текст блока — новое событие, тот же — ничего.
  6. Все штрихи распознанной строки стёрты — `canvas.line_erased {canvas_id, line_id, text}`, ключ — строка.
- Почему: WP21.3, `Т-§15.1`, `Т-§15.3`, D-B-170, §0.4.3.
- Обратимость: один модуль; рендер — один запрос compute.
- Тесты: `tests/canvas/test_recognition_wp21.py`.
- Требует подтверждения владельцем: нет.
### D-C-126. WP23.2: роль `artifact_writer@1` — схема выхода и слой 2 промпта
- Класс: B (поля схемы); слой 1 — BLOCKED-C-203
- WP / место: WP23.2–23.5, §7.2 S8, §7.3 п.2, §7.6; `quack/artifacts/writer.py`, `quack/prompts/artifact_writer/v1.md`, регистрация — `tests/llm/prompt_contracts.py` (B, правка в этом PR с ревью B)
- Контекст: одна роль пишет все виды артефактов, шаблон типа — слой 1 (`Т-§17.3`). Документ называет поля блока (`Т-§17.2`), виды карточек (`Т-§17.5`, словами) и проверки кода на выходе (`Т-§17.3`), но не форму выхода роли.
- Решение:
  1. `ArtifactWriterOutput {blocks, cards}`: конспект и cheat sheet заполняют `blocks`, карточки — `cards`; ненужная часть — пустой список.
  2. `WrittenBlock {updates, kind, content, sources, skills}`: `kind` — перечень `Т-§17.2` (допустимые виды держит схема); `sources` — метки кусков `P…`, `skills` — `S…` (`Т-§4.5`); `generatedBy` пишет код (версии шаблона и скилла), не модель. Блок текущей версии модель называет номером во входе (`updates`, с 1), id блоков она не видит: обновление блока ученика код превращает в дополнение (D-C-125 п.4).
  3. `WrittenCard {kind, prompt, markup}`: `kind` ∈ {`definition`, `sign_to_method`, `distinction`, `against_misconception`} — английские имена видов `Т-§17.5`; `markup` — `TaskMarkup` (`Т-§5.2`, `quack.practice.markup`), проверка — `validate_markup` (WP12.3).
  4. Входные блоки — слои `Т-§17.3`: тип, скилл ученика, тема и граф, куски материалов, сработавшие формулировки, личное минимум, текущие блоки.
- Почему: §0.4.3, §7.6 п.1–5, `Т-§17.2`–`Т-§17.5`.
- Обратимость: одна модель, один файл промпта.
- Тесты: `tests/llm/test_labels.py` (`T-M5`, файл контрактов, доктрина — xfail по §7.6 п.5).
- Требует подтверждения владельцем: нет; слой 1 — пакет владельца (BLOCKED-C-203).
### D-C-132. Слой 1 ролей C — черновики по тезисам документа (решение владельца передано C, 2026-11-03)
- Класс: C (процесс §7.6 п.1, §0.4.4 п.13) — решение принято C по прямому поручению владельца («детально сверяешься с документацией и принимаешь решение сама», 2026-11-03)
- WP / место: §7.6; `quack/prompts/recognizer/v1.md` (BLOCKED-C-202), `quack/prompts/artifact_writer/v1.md` (BLOCKED-C-203)
- Контекст: слой 1 — «только из пакета владельца» (§7.6 п.1). Пакета для ролей C нет; владелец поручил C решить самой. Прецедент — D-B-132: слой 1 восьми ролей B — черновики стенда, написанные по тезисам документа.
- Решение: слой 1 ролей C — черновик, каждая фраза которого — тезис первоисточника, без новых правил:
  - `recognizer@1`: читать, а не решать и не исправлять (`Т-§15.3` «LaTeX или текст»); два поля для математики — LaTeX для показа и SymPy для проверки, SymPy — только если выражение действительно выражение (`Т-§22.7`); английский первым, другие письменности как есть (`Т-§22.2`); честная уверенность — ученик видит прочтение и исправляет одним касанием (П-9.7, `Т-§15.3` «Проверка распознавания»); зачёркнутое читается и помечается (П-9.8 «стёртое хранится», `Т-§15.1`).
  - `artifact_writer@1`: слои генерации в порядке `Т-§17.3` (шаблон типа, скилл ученика, контекст, личное минимум — и ничего больше о ученике); шаблоны типов — конспект по П-11.1 (суть, определения в нотации материалов, почему, когда применять, разобранный пример, «твои ловушки», ссылки), карточки по `Т-§17.5` (задание с разметкой, четыре вида, расписания нет, `Т-§14.3`), cheat sheet по `Т-§17.6` (одна страница A4, сократить, если не влезает); точность по `Т-§17.4` (ссылка на кусок, без опоры — пояснение системы, формулы для символьной проверки); блок ученика не переписывается — дополнение рядом (`Т-§17.2`).
  - Заглушки сняты, `DOCTRINE_PENDING` пуст, проверка доктрины `T-M5` — обычный тест.
- Почему: поручение владельца; §7.6 п.1 (тезисы — из документа), прецедент D-B-132.
- Обратимость: текст слоя 1 одного файла; владелец может заменить своим пакетом — версия промпта до первой выкатки правится на месте (§7.6 п.3).
- Тесты: `tests/llm/test_labels.py` (`T-M5`, доктрина).
- Требует подтверждения владельцем: нет (решение передано C).

### D-C-123. WP21.3: роль `recognizer@1` — схема выхода и слой 2 промпта
- Класс: B (поля схемы); слой 1 — BLOCKED-C-202
- WP / место: WP21.3, §7.2 S8, §7.3 п.2, §7.6; `quack/canvas/recognition.py`, `quack/prompts/recognizer/v1.md`, регистрация — `tests/llm/prompt_contracts.py` (B, по S8 «регистрирует B», правка — в этом PR с ревью B)
- Контекст: WP21.3 и `Т-§15.3` называют, что даёт распознаватель строки: «LaTeX или текст, `sympy` (`Т-§22.7`), уверенность, признак зачёркнутого»; событие — `canvas.line_committed {…, text, sympy, kind, recognition_confidence, crossed_out}`. Имена полей выхода роли не названы.
- Решение: `RecognizerOutput {kind: latex|text, text, sympy: str|null, confidence: 0..1, crossed_out: bool}` — поля один к одному с событием (§0.4.3, «поля, названные в `Т-§`, — с этими именами»): `kind` и `text` → те же поля, `confidence` → `recognition_confidence`, `crossed_out` → `crossed_out`. Перечень `kind` — замороженный S3 (Q-A-3, D-A-119). Режим листа фото (WP22.3: все строки листа за один вызов, с рамкой) — своей схемой в WP22.3, не здесь. Вход — одно изображение строки, текстовых блоков нет. Слой 1 — заглушка с маркером `DOCTRINE_PENDING_MARKER`; `test_prompt_has_owner_doctrine[recognizer@1]` — `xfail(strict=True, reason="BLOCKED-C-202: нет доктрины")` (§7.6 п.5).
- Почему: §0.4.3 п.2, §7.6 п.1–5, ответ A (2026-11-03: слой 2 пишет C, версия `recognizer@1`).
- Обратимость: одна модель, один файл промпта.
- Тесты: `tests/llm/test_labels.py` (`T-M5`, файл контрактов, доктрина — xfail).
- Требует подтверждения владельцем: нет; слой 1 — пакет владельца (BLOCKED-C-202).

### D-C-115. WP18: маршруты входа, слои, ответы
- Класс: B
- WP / место: WP18; `quack/auth/router.py`, `quack/api/auth.py`, `quack/api/app.py`
- Контекст: §6.4 называет маршруты входа, WP18 — их смысл; не названы коды ответов, хранение `state` для Google, поведение без `auth_success_redirect_url` (D-C-105), и как зависимость сессии попадает в роутеры других потоков при запрете импорта между продуктовыми пакетами (§4.1).
- Решение: (1) **Слои.** Зависимости `get_current_session` и `require_terms` и кука `quack_session` — в основании API (`quack/api/auth.py`), функции продукта `quack.auth` (`resolve_session`, `has_current_terms`) подставляет сборка через `app.state`. Поэтому B объявляет `Depends(require_terms)` из `quack.api.auth`, а не из `quack.auth` (импорт `quack.session` → `quack.auth` запрещён §4.1). (2) Ответы — модели (контракт WP8.7: у каждого маршрута один 2xx с телом): `POST /auth/link` → `{"status": "sent"}` для любого адреса; вход → 303 на `auth_success_redirect_url` с кукой, без него — 200 `{learner_id}`; выход → `{"status": "signed_out"}`; согласия → `{kind, version, given}`; настройки → `{settings}`. `GET /auth/google/start` — только редирект 302. (3) `state` Google — 128 бит в своей куке `quack_oidc_state` (те же флаги, срок `auth_link_ttl_min`); неверный `state` или неподтверждённый адрес — 401 `UNAUTHENTICATED`. (4) Лимит ссылок — 429 `RATE_LIMITED` с `Retry-After`; почта недоступна — 503 `SERVICE_UNAVAILABLE` (§4.4). (5) Часовой пояс — проверка IANA (`zoneinfo`), пишется в `core.learners.settings.timezone` без события (WP18.3 называет место — строку ученика). (6) Ключ идемпотентности согласия — в теле, как у мутирующих маршрутов Фазы 3; отзыв `research` берёт `version` из тела. (7) Маршруты на сессиях отвечают `UNAUTHENTICATED`, маршруты Фазы 3 — `UNAUTHORIZED`, пока весь API не перейдёт на сессии (D-C-112).
- Почему: §4.1, §4.5, WP8.7, §0.4.3 п.5.
- Обратимость: модели ответов и один модуль маршрутов.
- Тесты: `tests/auth/test_auth_routes.py`, `tests/test_app_assembly.py`.
- Требует подтверждения владельцем: нет.

### D-C-117. WP20.3: приложение Modal и клиент эмбеддингов
- Класс: B; п.2 и п.4 — вопрос владельцу
- WP / место: WP20.3; `modal/embeddings.py`, `quack/embeddings/client.py`, `quack/config.py`
- Контекст: `Т-§10.5` и WP20.3 называют модель (Qwen3-Embedding-0.6B, CPU, MRL), инструкцию к запросу «как рекомендует карточка модели», пакетную обработку и тёплый контейнер, но не называют: имя приложения и класса, текст инструкции, способ загрузки модели (§4.6 `[ПРОВЕРИТЬ в WP20.3]`), окно простоя и число ядер.
- Решение: (1) Приложение `quack-embeddings`, класс `Embedder`, метод `embed(texts, prompt, dim)`; клиент ищет их по имени (`modal.Cls.from_name`) с `MODAL_TOKEN_*`; имена сверяет тест. (2) **Инструкция запроса** — формат карточки `Instruct: {task}
Query:{query}`; текст задачи — параметр `embedding_query_instruction`, стартовое значение — пример карточки («Given a web search query, retrieve relevant passages that answer the query») до проверки качества поиска (`Т-§10.5` «Проверить»). Документы — без инструкции. (3) Загрузка — `sentence-transformers` 5.1.0 (`transformers` 4.56.1, `torch` 2.8.0 CPU) в образе Modal, не в backend; векторы нормированы и обрезаны до `embedding_dim` (`truncate_dim`). (4) `[ОРИЕНТИР]` окно простоя `SCALEDOWN_WINDOW_S = 300` и `CPU_CORES = 2` — константы приложения; «тёплый, пока есть активные сессии» и стоимость — за владельцем вместе с деплоем (`modal deploy modal/embeddings.py`). (5) Нет токенов, ошибка Modal, таймаут → `EmbeddingsUnavailable` (`Т-§20.7`); вектор другой размерности — `ValueError` (дефект деплоя, не отказ). (6) **Тело `embedding.compute`** — `quack/embeddings/compute.py` (`compute_embeddings(db, client, learner_id, nodes)`): узлы с текстом → один вызов `embed_documents` → `embedding.ready` на узел одним `append_events`, вектор — base64 little-endian float32 (`Т-§3.1`), ключ — `embedding_key` A; узлы, у которых `embedding.ready` этой модели уже есть, пропускаются до вызова (повтор задачи не платит Modal и не даёт конфликта ключа из-за плавающих отличий вектора). Регистрация вида и форма параметров — шов S6 (A). **Вопрос A:** (а) параметры задачи несут текст узла или только `(node_kind, node_id)`, а текст читает обработчик — откуда (кусок — `chunk.indexed.text`; тема, навык, заблуждение, свидетельство — ?); (б) `JobContext` Фазы 4 с `db: Db` и `embeddings: EmbeddingClient` (§4.2) — когда; (в) переименование темы при ключе `emb:{kind}:{id}:{model}` новый вектор не запишет — так задумано?
- Почему: §0.4.3 п.5, `Т-§10.5`, §4.6.
- Обратимость: одна константа имени и один параметр.
- Тесты: `tests/embeddings/test_modal_client.py`. Сам Modal в тестах не вызывается; живой вызов — после деплоя владельцем.
- Требует подтверждения владельцем: п.2 (текст инструкции задачи), п.4 (окно простоя и ядра).
### D-C-119. Весь API на сессиях; пароль, JWT и `/v1/auth/register|login|me` удалены (D-C-112 п.1–2, D-A-113)
- Класс: B (правка файлов A и B по согласованию, §7.3; D-A-112, D-A-113, B в #103)
- WP / место: WP18.3, §2.3 строка 3, §4.5, §8.15; `quack/api/auth.py`, `quack/domains/router.py`, `quack/domains/auth.py` (удалён), `quack/domains/schemas.py`, `quack/api/ratelimit.py`, `quack/api/openapi.py`, `quack/config.py`, `quack/ops/seed.py`, `quack/cli.py`, `loadtest/scenario.py`, `compose.yaml`, `.env.example`, `tests/support/auth.py`
- Решение:
  1. `get_current_learner_id(request) -> UUID` — имя и подпись те же, роутеры A, B, C не менялись: ученик берётся из куки сессии (`get_current_session`); без сессии, с истёкшей или отозванной — 401 `UNAUTHENTICATED` (§4.5). Переходно, до переноса удаления на `Db` (WP25, A): ученик с `auth.users.deleted_at` или строкой `ops.deletion_tombstones` тоже отклоняется (D-C-004) — так `test_two_phase_account_deletion`, `T-A3` и проверка надгробия в артефактах сохраняют утверждения. Наличие строки `auth.users` больше не требуется (вход — сессия ученика `core.learners`).
  2. Удалены по D-A-113: `POST /v1/auth/register`, `POST /v1/auth/login`, `GET /v1/auth/me`, публичный роутер с лимитом `auth`, `quack/domains/auth.py` целиком (`register_learner`, `login_learner`, `get_learner_me`, `hash_password`, `verify_password`, `normalize_email`), выпуск и проверка JWT (`issue_access_token`, `decode_access_token`, `DEV_JWT_SECRET`, `JWT_ALGORITHM`), схемы `RegisterRequest`, `LoginRequest`, `AuthResponse`, `LearnerMeResponse`. Вместе с ними — то, что без них мертво: `enforce_auth_rate_limit` и `auth_requests_per_ip_per_min` (лимиты входа теперь — `auth_link_per_*`, WP18), `jwt_secret`, `jwt_ttl_days`, проверка `JWT_SECRET` в prod, `JWT_SECRET` в `compose.yaml` и `.env.example`. Код `UNAUTHORIZED` в `ErrorCode` остаётся (§4.5: «остальные коды Фазы 3 не меняются»).
  3. `ops seed-learners`: токен в файле — сессия `core.auth_sessions` (`open_session`), не JWT; проверка `JWT_SECRET` снята, роль `ops` по-прежнему без секретов (Д-§7). `loadtest/scenario.py` шлёт куку `quack_session` вместо `Authorization`.
  4. Тестовый вход (D-C-112 п.2): `tests/support/auth.py` — `login` (кука на клиенте), `session_token` + `session_cookie` (кука одного запроса), `learner_headers` + `login_hook` (клиент, который ходит за многих учеников: хук открывает сессию ученика один раз и шлёт её куку вместо пометки). API пометку не читает: запрос с ней без хука — 401 (`test_request_without_session_is_rejected`). `ensure_users_hook` и JWT в тестах удалены; `ensure_user` остаётся для строки `auth.users`, пока на ней работают удаление и настройки Фазы 3.
  5. `auth.users`, старая миграция и `learner.registered` журнала Фазы 3 не трогаются — WP1.3 (A, D-C-112 п.4). E2E-ученик и ученик `T-A3`/`T-A5` получают те же строки, что писал удалённый маршрут, и сессию.
- Почему: §2.3 строка 3, WP18.3 («пароль, JWT, `auth.users`, `/v1/auth/register|login` удаляются»), §6.4 «старый → новый», §8.15 п.1–2, D-A-113.
- Обратимость: одна зависимость `get_current_learner_id`; удалённое — в истории git.
- Тесты: всё затронутое — в описании PR; «Правки тестов» — ниже.
- Требует подтверждения владельцем: нет. Д-§7 `JWT_SECRET` не называет, `Т-§` JWT отвергает — с документами расхождения нет (D-C-122 п.3; прежний вопрос C снят). Компаньон (`scope = companion`) на маршрутах Фазы 3 пока проходит как полная сессия — запрет (`T-CP1`, 403) делает WP22.

### D-C-116. WP20.1, WP20.5: маршруты материалов и поиска на сессиях
- Класс: B
- WP / место: WP20.1, WP20.5; `quack/materials/router.py`, `quack/materials/library.py`, `tests/e2e/endpoints.py`
- Контекст: §6.4 называет `POST/GET /v1/domains/{id}/materials`, `GET /v1/materials/{id}`, `GET /v1/search`; WP20.1 — multipart с необязательным `session_id`. Не названы: ключ идемпотентности загрузки, размер выдачи и минимальная длина запроса поиска (D-C-113 п.4), ответ на список материалов чужого домена.
- Решение: (1) Вход — только кука сессии (`get_current_session`, WP18.3), не JWT Фазы 3 (D-C-113 п.5); без сессии — 401 `UNAUTHENTICATED`. (2) Загрузка — без `idempotency_key`: WP20.1 его не называет, каждая загрузка — новый материал со своим ключом `material.uploaded:{material_id}`; в обходе `T-E2` маршрут — в `NO_IDEMPOTENCY_KEY` с причиной. (3) Поиск берёт параметры полнотекстового поиска Фазы 3: `search_min_query_len`, `search_default_limit`, `search_max_limit` (те же проверки, что у `GET /v1/artifacts/search`, 422 `VALIDATION_ERROR`). (4) Список материалов чужого или неизвестного домена — 404 `NOT_FOUND`, не пустой список (`T-E3`); проверка домена — та же, что у загрузки (D-C-113 п.1). (5) Ответы: материал — `material_id, domain_id, session_id, mime, status, pages_count, created_at`; карточка — материал, `file_url`, страницы `page, text, image_url`; поиск — `hits` из `session_id, seq, type, snippet`.
- Почему: §0.4.3 п.5, §4.5, WP18.3.
- Обратимость: один модуль маршрутов.
- Тесты: `tests/materials/test_materials_routes.py`, `tests/e2e/test_isolation_and_idempotency.py` (`T-E2`, `T-E3`), `tests/test_app_assembly.py`.
- Риск перехода: домен проверяется по `domain.created` в журнале Фазы 4, а домены пока создаёт маршрут A Фазы 3 в старом журнале — загрузить материал через API можно будет, когда создание домена перейдёт на `Db` (R-W, A). Компаньон (`scope = companion`, `T-CP1` → 403) — общей проверкой в WP22, не здесь.
- Требует подтверждения владельцем: нет.

### D-C-124. WP21.2/WP22.1: что получает `companion` по сокету канваса; повтор пачки между сокетами (ответ на ревью B #128)
- Класс: B
- WP / место: WP21.2, WP22.1; `quack/canvas/socket.py`
- Контекст: WP21.2 — «`companion` для своего канваса — только статус»; WP22.1 — «видеть его статус — и ничего больше». Какие сообщения сервера (WP21.2: `ack`, `line_recognized`, `mark`, `hint`, `layer_update`, `task_closed`) — «статус», не перечислено. Ревью B #128 п.1–3.
- Решение:
  1. `companion` получает из канала `canvas:{id}` только `COMPANION_KINDS = {task_closed}`: распознавания, пометки, подсказки и слой — содержимое решения, не статус (§0.4.3 — консервативно, «и ничего больше»). Расширить перечень — WP22, если понадобится статус снимка.
  2. Любое сообщение `companion` закрывает сокет `1008` до разбора (право раньше формата, ревью B п.3).
  3. Повтор пачки между сокетами (переподключение или другой процесс `api` после записи куска, до `ack`): состояния в API не добавляется; читатель журнала штрихов (WP21.3, проигрывание) пропускает пачку с уже встреченным `batch_no` канваса (предложение B п.1, WP21.2 «повтор отсекается по `batch_no`»).
- Почему: WP21.2, WP22.1, §0.4.3.
- Обратимость: одна константа и одно условие.
- Тесты: `tests/canvas/test_canvas_ws.py` (`test_sign_in_and_scope`, `test_canvas_channel_reaches_every_socket`).
- Требует подтверждения владельцем: нет.

### D-C-122. Открытые вопросы C, решённые по документам (владелец поручил C, 2026-11-03)
- Класс: B (п.1, п.3, п.4 — пробелы, решены по §0.4.1 и §0.4.3); п.2 — снятое предложение
- Решения:
  1. **Блок набора формул (D-C-118, ревью B #116 п.4).** Это **инструмент слоя ученика, а не вид блока**. Основания: П-9.4 даёт закрытый перечень «Блоки на доске: задача…, визуализация, подсказка, справочная карточка», а «блок набора формул» стоит в перечне «Инструменты» рядом с пером и ластиком. Там же: «Слой ученика — его штрихи и набранные формулы», то есть набранная формула — содержимое слоя ученика, как штрих. `Т-§15.3`: «набранные формулы — блок набора с клавиатуры — дают LaTeX напрямую». Поэтому `typed_formula {block_id, latex}` (WP21.2) ссылается на блок доски, в котором набрана формула, — для решения это блок задачи, где строки проверяются (WP21.4 «для каждой новой строки в блоке задачи»). Вид блока не добавляется, `BoardDoc` не меняется.
  2. **Поправка §0.4.7 и §7.4 — снята.** §0.4.7 уже требует один полный прогон на итоговом состоянии ветки перед PR, без прогонов на промежуточных состояниях, а §7.4 прямо требует записывать красный прогон в `PHASE4_NOTES.md`. Это явные контракты ТЗ (§0.4.1 уровень 5), а предложение C стоит на уровне 7 и по §0.4.1 не применяется. Работаем по ТЗ, конфликты заметок снимаем объединением сторон.
  3. **`JWT_SECRET` в Д-§7 — расхождения нет.** Д-§7 («Конфиг и секреты») `JWT_SECRET` не называет; `Т-§` отвергает JWT («нельзя отозвать до истечения срока»); ТЗ Ф4 §2.3 строка 3 его удаляет. Правка документа не нужна; вопрос C в D-C-119 был ошибочным и снят.
  4. **Переполнение куска штрихов (D-C-114 п.3, D-C-121).** `Т-§15.2`: кусок пишется «раз в `stroke_chunk_seconds` или при переполнении». Переполнение — когда в памяти ждёт больше пачек, чем клиент шлёт за одно окно куска: `chunk_max_batches = ceil(stroke_chunk_seconds · 1000 / stroke_batch_ms)` (10 с / 300 мс → 34). Новых чисел нет: граница выводится из двух параметров `Т-§15.2`. Это консервативно (§0.4.3, абзац о значениях параметров): при повторной отправке накопленного после обрыва кусок пишется сразу, и данные дольше не ждут в памяти без подтверждения. `StrokeJournal(max_batches=…)`, `flush_due` и `due_in` учитывают переполнение.
- Тесты: `tests/canvas/test_strokes_wp21.py` (`test_chunk_batches_from_the_settings`, `test_overflow_writes_the_chunk_before_its_time`).
- Требует подтверждения владельцем: нет (поручено решить C).

### D-C-121. WP21.2: WebSocket канваса — вход, коды закрытия, формат пачки, повтор подтверждения
- Класс: B
- WP / место: WP21.2, WP22.1, `Т-§1.6`, `Т-§15.1`, `Т-§15.2`, `Т-§20.7`; `quack/canvas/socket.py`, `quack/canvas/router.py`, `quack/canvas/strokes.py`, `quack/api/auth.py` (`websocket_session`), `tests/support/ws.py`
- Контекст: WP21.2 называет маршрут, сообщения клиента и сервера, кусок и `ack`. Не названы: коды отказа и закрытия сокета, вид точек в `stroke_batch`, ответ на повтор уже записанной пачки, повтор записи при недоступном хранилище, вид пары «компаньон шлёт сообщение».
- Решение:
  1. Вход — кука сессии в рукопожатии (`websocket_session`, то же разрешение сессии, что у HTTP). Можно открыть: `full` ученика-владельца канваса или `companion` этого канваса (`canvas_id` сессии, WP22.1). Иначе — нет сессии, чужой или неизвестный канвас, компаньон другого канваса, канвас без сессии — сокет не принимается: `close 1008` (RFC 6455 §7.4.1 — нарушение политики) до `accept`.
  2. Компаньон получает только статус — сообщения сервера канала `canvas:{id}`. Любое его сообщение закрывает сокет кодом 1008.
  3. Неразбираемое сообщение (не JSON, неизвестный `type`, нет полей) — `close 1007`. Сообщения — строгие модели Pydantic, лишние поля запрещены.
  4. `stroke_batch {batch_no, strokes: [{stroke_id, points: [[x, y, t_ms, pressure], …]}], erasures: [{stroke_ids, at_ms}]}`: точки в сообщении абсолютные, в разностную запись их переводит журнал (`Т-§15.1`). Время — мс устройства, как в куске (D-C-114).
  5. Повтор `batch_no`: ещё не записанная пачка — отсекается, `ack` придёт с записью куска; уже записанная — `ack` шлётся снова (первый мог потеряться, иначе клиент повторял бы вечно). Новое в журнале — `written(batch_no)`.
  6. Хранилище недоступно: подтверждений нет, пачки ждут; повтор записи — по следующей пачке клиента, в том числе повторной (`Т-§20.7` «клиент повторит»), и при закрытии сокета, а не в цикле сервера. Новое в журнале — `due_in(now)`: сокет ждёт сообщение клиента не дольше, чем до срока куска.
  7. При закрытии сокета ожидающие пачки пишутся (подтвердить уже некому — клиент повторит неподтверждённое при новом подключении).
  8. `publish_canvas(redis, canvas_id, message)` — сообщения сервера всем сокетам канваса во всех процессах API (`Т-§15.2`); каждый сокет подписан на `canvas:{id}`. Без Redis — только свои `ack` (§4.2). Ошибка Redis заканчивает пересылку, а не сокет.
  9. `line_ready`, `typed_formula`, `check_request` протокол принимает и проверяет, обрабатывают их WP21.3–21.5 (пока — запись в лог `canvas_message_pending`).
  10. Тесты WebSocket — без новой зависимости: `tests/support/ws.py` вызывает приложение ASGI на цикле теста (`TestClient` Starlette крутит своё событийное кольцо и ломает пулы фикстур).
- Почему: §0.4.3 п.5, RFC 6455, `Т-§15.2`, `Т-§20.7`.
- Обратимость: один модуль.
- Тесты: `tests/canvas/test_canvas_ws.py`.
- Переполнение куска — D-C-122 п.4.
- Требует подтверждения владельцем: нет.

### D-C-118. WP21.1: документ доски и маршруты канвасов
- Класс: B; схема документа — на ревью B (B #103)
- WP / место: WP21.1; `quack/canvas/board.py`, `quack/canvas/boards.py`, `quack/canvas/router.py`
- Контекст: `Т-§15.1` и П-9.4 задают состав доски (блоки, расположение, слой учителя, версии) и два слоя разными структурами; WP21.1 — три маршрута и `agent.requested {kind: solution_space}` для задачи на лету без пространства. Не названы: поля документа, ключ идемпотентности открытия, проверка сессии и домена, отличие «задачи на лету».
- Решение: (1) **Документ** `BoardDoc` (строгий, `extra=forbid`): `blocks` (`block_id`, `kind`: task | visualization | hint | reference_card, `task_id?`, `layout {x, y, width?, height?, collapsed}`); `learner` — `typed_formulas {block_id, latex}` (штрихи — в журнале штрихов, строки — события); `teacher` — `marks {mark_id, line_id, kind: error|question, text}`, `hints {hint_id, task_id, step 1–3, text, line_id?}`, `visualizations {item_id, block_id, artifact_id}`, `scaffolds {item_id, block_id, text}`. Слои — разные модели: элемент учителя в слой ученика не проходит проверку. Версия — колонка `version`. (2) Открытие по задаче — блок `task` (`b1`). (3) «Задача на лету» — `content.tasks.source = teacher`; без `solution_space` — `agent.requested {kind: solution_space, params.task_id, needed_in_session: true}` под ключом `solution_space:{task_id}` и с `request_id = uuid5(QUACK_UUID_NS, ключ)` (закон 5, по ревью B #116: один запрос на задачу, второй канвас по ней ничего не пишет), `actor = code`, `domain_id` и `session_id` — сессии канваса, в одной транзакции со строкой; у задач банка пространство строит `task.solution_space` по `task.created` (WP12.4). (4) Сессия ученика — по `session.started` в журнале, домен — по `domain.created` (закон 1, как D-C-113); чужое — 404. Домен канваса — домен его сессии. (5) Открытие без ключа: каждый вызов — новый канвас; в обходе `T-E2` — `NO_IDEMPOTENCY_KEY` с причиной.
- Почему: §0.4.3 п.5, закон 1, WP18.3.
- Обратимость: модели документа (данных нет, §0.3).
- Тесты: `tests/canvas/test_boards_wp21.py`, `tests/e2e/test_isolation_and_idempotency.py`, `tests/test_app_assembly.py`.
- Риск перехода: сессии пока стартуют маршрутом B Фазы 3 в старом журнале — открыть канвас через API можно, когда старт сессии пишет `session.started` в журнал Фазы 4.
- Требует подтверждения владельцем: нет; поля документа — ревью B.
- Ревью B (#116), ответы: п.1 (ключ) — исправлено, тест `test_one_request_per_task`; п.2 (`domain_id` у `agent.requested`) — да, из `session.started` той же выборкой; п.3 (`actor`) — `code` (`FIXED_ACTORS`); п.4 (`TypedFormula.block_id`) — формула ссылается на блок доски, в котором набрана (обычно блок задачи); отдельного вида «блок набора» в видах блоков П-9.4 нет, хотя П-9.4 называет «блок набора формул» среди инструментов — решено в D-C-122 п.1: инструмент слоя ученика, вид блока не добавлен. п.5 (график без артефакта) — модель `PlotSpec` у B в `quack.session`, канвас её импортировать не может (§4.1); пока на доску кладётся только визуализация-артефакт, простой график — нет; вынести `PlotSpec` в ядро или класть график артефактом — решение B при WP21.5. п.6 (`hint.shown {source: canvas}`) — в WP21.5 (C). п.7 — риск перехода, как выше.

#### Ответ A: таблицы C в базовой миграции и D-A-103 (2026-11-02)

Таблицы C в `backend/db/migrations/20261102000001_baseline.sql` (`phase-4-a-wp1`, `4a75a2c`) сверены
с §6.1 по колонкам: `core.auth_sessions`, `core.auth_links`, `content.canvas_boards`,
`content.materials`, `content.material_pages`, `content.artifacts`, `content.artifact_versions`,
`content.learner_skills`, `content.exports`; плюс колонки C в `core.learners` (`email`, `settings`).
Все колонки §6.1 есть, лишних нет, `learner_id` в каждой таблице; `NULL`/`NOT NULL` по D-A-104
C подходят (`material_pages.text NOT NULL` — для страницы без текста пишется пустая строка).
Правок не нужно.

D-A-103 (две схемы до WP1.3): **C согласен** с вариантом (3). В WP9 при переходе (WP1.3) C
переключает `ops migrate`, образ (`dbmate` в `quack-backend`) и сервис `migrate` в compose на
dbmate. Сейчас нужно только одно: dbmate в CI, иначе `T-DB1`/`T-DB5` падают в job `backend` —
PR #77 (dbmate v2.36.0, sha256).

## Заблокировано

- ~~BLOCKED-B-101…108~~ — сняты 2026-11-02: слой 1 восьми ролей — черновики стенда (D-B-132).
- ~~BLOCKED-C-203~~ — снят 2026-11-03: слой 1 `artifact_writer@1` — черновик по тезисам документа (D-C-132).
- **BLOCKED-C-202 (C): нет доктрины (слой 1) роли `recognizer@1`.** Слой 2 написан (D-C-123), роль работает на заглушке; `test_prompt_has_owner_doctrine[recognizer@1]` — `xfail(strict=True)`. Нужно от владельца: текст слоя 1 — как читать рукопись (кириллица и формулы, `Т-§22.2`), что считать зачёркнутым, как выражать неуверенность (§7.6 п.1, §0.4.4 п.13).
- ~~BLOCKED-C-202~~ — снят 2026-11-03: слой 1 `recognizer@1` — черновик по тезисам документа (D-C-132).
- BLOCKED-C-204 — рисунки на фото (WP22.3 шаг 5): нет адаптера `segmenter` (`sam-3.1`) в шлюзе — B, WP10.1 (D-C-134 п.6).

## Красные прогоны

- WP12.6, генерация задач (D-B-168): тесты и код в одной итерации (TDD облегчён владельцем); на неверной реализации — 7 подмен, каждая роняет тест. Зелёный: `tests/practice/test_task_generation.py` с `QUACK_TEST_REQUIRE_INFRA=1` — 5 passed.
- WP12.4, пространство решения (D-B-166): тесты и код в одной итерации (TDD облегчён владельцем); на неверной реализации — 7 подмен (без `proof`, без уровня 6, метка вместо id, повторная постройка, без `IS NULL`, без LaTeX условия, `hard=False`), каждая роняет тест. Зелёный: `tests/test_llm_runtime.py`, `tests/practice`, `tests/llm`, `tests/test_settings_meta_api.py`, `tests/test_contracts.py`, `tests/db/test_sql_static.py`, `tests/e2e/test_llm_seams_e2e.py` с `QUACK_TEST_REQUIRE_INFRA=1` — 272 passed.
- WP13.1–13.2, тело `/actions` (D-B-156): `uv run pytest tests/session/test_actions_route.py` — ошибка сбора (нет `quack.session.action_turn`), коммит `a5066ee`; на неверной реализации — 5 подмен, каждая роняет тест. Зелёный: 8 passed.
- WP21.3, роль `recognizer@1` (C, `phase-4-c-recognizer`, коммит `bf2b46a`): `pytest tests/llm/test_labels.py -k "recognizer or contracts_file"` — 2 failed (файла промпта нет, `PROMPT_CONTRACTS.md` устарел), 1 xfailed. Зелёный: `tests/llm/test_labels.py` — 59 passed, 1 xfailed (BLOCKED-C-202).
- WP22.1, привязка (C, `phase-4-c-wp22-pairing`): красный прогон отдельно не снят (облегчённый TDD). Зелёный: `tests/auth`, `tests/canvas`, `tests/e2e`, `tests/test_app_assembly.py`, `tests/db/test_sql_static.py`, `tests/test_contracts.py` — зелёные.
- WP12.7, подбор задач (D-B-169): тесты и код в одной итерации (TDD облегчён владельцем); на неверной реализации — 5 подмен (без `learnerId` задачи, без уровня, без «семейство не встречалось», порядок, лимит), каждая роняет тест. Зелёный: `tests/practice/test_select_tasks.py` против Neo4j — 1 passed.
- WP12.5, проверка задачи учителя (D-B-167): тесты и код в одной итерации (TDD облегчён владельцем); на неверной реализации — 11 подмен, каждая роняет тест. Зелёный: `tests/practice/test_task_check.py` с `QUACK_TEST_REQUIRE_INFRA=1` — 4 passed.
- WP23.1, документы артефактов (C, `phase-4-c-wp23-artifacts`): красный прогон отдельно не снят (облегчённый TDD по указанию владельца, 2026-11-03). Зелёный: `tests/artifacts/test_documents_wp23.py` + `tests/test_app_assembly.py` — 25 passed; с ключами — `tests/e2e/test_isolation_and_idempotency.py` + `tests/artifacts/test_documents_wp23.py` — 9 passed.
- WP20.3, тело `embedding.compute` (C, `phase-4-c-wp20-modal`, коммит `5ff4812`): в Linux `pytest tests/embeddings/test_embedding_compute.py` — ошибка сбора (нет модуля); на неверной реализации (без пропуска готовых узлов) — 1 failed. Зелёный: с `tests/db/test_sql_static.py`, `tests/test_contracts.py` — 14 passed.
- WP20.3, клиент Modal (C, `phase-4-c-wp20-modal`, коммит `92d406a`): `uv run pytest tests/embeddings` — ошибка сбора (нет `EMBEDDING_APP`, `EMBEDDER_CLASS`, параметра `query_instruction`). На неверной реализации (инструкция у документов; ошибки Modal не переводятся в `EmbeddingsUnavailable`) — по 1 failed. Зелёный: `tests/embeddings` (6, в Linux с `modal/` рядом), `tests/storage/test_s1_resources.py`, `tests/test_contracts.py`, `tests/test_thresholds_in_settings.py`, `tests/test_app_assembly.py` — 46 passed. Правка теста красного коммита: `SecretStr("")` вместо `""` в `model_copy` (ошибка теста, не контракта).
- WP20.4, поиск кусков (C, `phase-4-c-wp20-search`): красный прогон отдельно не снят (облегчённый TDD). Зелёный: `tests/materials/test_materials_search_wp20.py` + `tests/test_contracts.py` — 9 passed.
- WP22.1, привязка (C, `phase-4-c-wp22-pairing`): красный прогон отдельно не снят (облегчённый TDD). Зелёный: `tests/auth`, `tests/canvas`, `tests/e2e`, `tests/test_app_assembly.py`, `tests/db/test_sql_static.py`, `tests/test_contracts.py` — зелёные.
- WP21.3, роль `recognizer@1` (C, `phase-4-c-recognizer`, коммит `bf2b46a`): `pytest tests/llm/test_labels.py -k "recognizer or contracts_file"` — 2 failed (файла промпта нет, `PROMPT_CONTRACTS.md` устарел), 1 xfailed. Зелёный: `tests/llm/test_labels.py` — 59 passed, 1 xfailed (BLOCKED-C-202).
- Шов S12, изображение во входе (D-B-170): тест и код в одной итерации; 2 подмены (шлюз не передаёт изображения, клиент не строит части) — каждая роняет тест. Зелёный: `tests/test_llm_runtime.py`, `tests/llm`, `tests/test_meta_mock.py` с `QUACK_TEST_REQUIRE_INFRA=1` — 184 passed.
- WP14.1–WP14.3, слоты, картина памяти, разговор (D-B-157, D-B-158, D-B-159): `uv run pytest tests/session/test_teacher_context.py` — 9 failed (`NotImplementedError`), коммит `8778d96`; на неверной реализации — 30 подмен в трёх модулях, каждая роняет тест (после усиления тестов, см. «Правки тестов»). Зелёный: 10 passed; с `QUACK_TEST_REQUIRE_INFRA=1` вместе с `tests/test_contracts.py`, `tests/db/test_sql_static.py`, `tests/session/test_checkpoint_law10.py`, `tests/session/test_phase4_contracts.py` — зелёные.
- WP13.10 по ревью C #124 (D-B-155 п.е, п.ж): `uv run pytest tests/session/test_sessions_db.py` — 2 failed (возврат без `resumed: true`; одновременный старт с одним ключом — `IdempotencyConflictError`), коммит `e8c8e77`. Зелёный: 11 passed (с `QUACK_TEST_REQUIRE_INFRA=1`).
- WP13.10, жизненный цикл сессии на `Db` (D-B-155): `uv run pytest tests/session/test_sessions_db.py` — ошибка сбора (нет `quack.session.sessions`), коммит `28f32e3`; на неверной реализации — 6 из 7 подмен роняют тест. Зелёный: 10 passed.
- WP15 п.4–5, проверка и запись (D-B-165): тесты и код в одной итерации (TDD облегчён владельцем); на неверной реализации — 8 подмен (без транзакции, `causation_seq`, `observation_seq`, вердикт code, окно вместо эпизода, `min` вместо `max`, текст ответа, текст строки канваса), каждая роняет тест. Зелёный: `tests/agents`, `tests/graph/test_extracted_check.py`, `tests/jobs/test_coverage.py`, `tests/test_contracts.py`, `tests/db/test_sql_static.py` с `QUACK_TEST_REQUIRE_INFRA=1` — 34 passed.
- WP15 п.1–3, окно, вход и вызов `extractor@3` (D-B-162): `uv run pytest tests/agents/test_extract_episode.py` — 4 failed (`NotImplementedError`), коммит `ac6bed8`; на неверной реализации — 12 подмен, каждая роняет тест (после усиления тестов, см. «Правки тестов»). Зелёный: 4 passed; с `QUACK_TEST_REQUIRE_INFRA=1` вместе с `tests/test_contracts.py`, `tests/db/test_sql_static.py`, `tests/llm/test_labels.py` — 74 passed.
- D-C-119, весь API на сессиях (C, `phase-4-c-d112`, коммит `e8a6aee`): в Linux против `quack-test-*` с dbmate `pytest tests/test_app_assembly.py tests/domains/test_auth_and_learner.py tests/session/test_auth_and_stream_resilience.py` — 14 failed (старый вход: сессия не принимается маршрутами Фазы 3, код 401 `UNAUTHORIZED`, `/v1/auth/register|login|me` ещё смонтированы, лимит без Redis считает только `auth`), 11 passed. Зелёный — в описании PR.
- D-C-122 п.4, переполнение куска (C, `phase-4-c-wp21-ws`, коммит `57127c3`): `pytest tests/canvas/test_strokes_wp21.py` — 2 failed (`chunk_max_batches` — `NotImplementedError`, кусок ждёт время при переполнении), 5 passed. Зелёный: `tests/canvas` — 19 passed.
- WP21.2, WebSocket канваса (C, `phase-4-c-wp21-ws`, коммит `f915ac2`): в Linux против `quack-test-*` с dbmate `pytest tests/canvas/test_canvas_ws.py` — 7 failed (маршрута `WS /v1/canvases/{id}/ws` нет — сокет закрывается при рукопожатии; `publish_canvas` — `NotImplementedError`). Зелёный: `tests/canvas` — 16 passed; `tests/auth`, `tests/test_contracts.py`, `tests/test_app_assembly.py` — зелёные.
- WP21.1, ключ запроса пространства (C, `phase-4-c-wp21-boards`, коммит `3318d8a`, ревью B #116): `pytest tests/canvas/test_boards_wp21.py` — 1 failed (`test_one_request_per_task`: ключ `canvas:{canvas_id}:solution_space`, `actor = system`, без `domain_id`), 4 passed. Зелёный на `f693231`: `tests/canvas`, `tests/e2e/test_isolation_and_idempotency.py`, `tests/test_contracts.py`, `tests/db/test_sql_static.py` — 24 passed.
- WP21.1, доска и канвасы (C, `phase-4-c-wp21-boards`, коммит `0012acb`): в Linux `pytest tests/canvas/test_boards_wp21.py` — ошибка сбора (нет `quack.canvas.board`); на неверной реализации (запрос пространства для любой задачи без него) — 1 failed. Зелёный: `tests/canvas`, `tests/e2e`, `tests/test_app_assembly.py`, `tests/test_contracts.py`, `tests/db/test_sql_static.py`, `tests/materials` — 61 passed. Правка теста красного коммита: домен в тестах создаётся событием `domain.created` (проверка домена — D-C-118 п.4).
- WP20, маршруты (C, `phase-4-c-api-sessions`, коммит `e2fb068`): в Linux против `quack-test-*` с dbmate `pytest tests/materials/test_materials_routes.py` — 4 failed (маршрутов нет: 404 вместо 201/401/415/200). Зелёный: `tests/materials`, `tests/e2e`, `tests/test_app_assembly.py`, `tests/test_contracts.py`, `tests/db/test_sql_static.py`, `tests/auth` — 66 passed.
- WP13.1, согласие на старте сессии (D-B-151): `uv run pytest tests/session/test_session_terms.py` — 2 failed (старт без согласия `201`, без cookie `201`), коммит `f5029a2`. Зелёный: 2 passed; `tests/session` и `tests/llm/test_labels.py` зелёные; `tests/e2e` — 13 failed (`403` на старте, правка у C).
- WP11.3, WP11.6 на полях A #117 (D-B-145, D-B-146): `uv run pytest tests/practice/test_check_symbolic_job.py tests/practice/test_check_model.py` — 1 failed (`unsupported` ничего не писал) и ошибка сбора (нет `known_misconceptions`), коммит `72e96fb`. Зелёный: `tests/practice`, `tests/events`, `tests/graph/test_read_stub.py`, `tests/jobs/test_rules_p4.py`, `tests/test_contracts.py`, `tests/db/test_sql_static.py` — 234 passed.
- WP18, маршруты (C, `phase-4-c-sessions`): тесты и роутер писались в одной итерации, красный прогон отдельно не снят. Зелёный: в Linux против `quack-test-*` — `tests/auth` 14 passed; локально `test_app_assembly.py` + `test_contracts.py` — 26 passed. `test_sql_static.py` красный до слияния #101 (записи A для двух запросов до входа).
- WP18, этап I (C, `phase-4-c-wp18`): в Linux против `quack-test-*` с `QUACK_TEST_REQUIRE_INFRA=1` и dbmate v2.36.0 `pytest tests/auth` — 7 failed (`NotImplementedError`: `request_link`, `redeem_link`, `LinkLimiter`, `cookie_params`, `learner_for_google`, сессии, согласия). Зелёный (библиотека): 7 passed. `T-AU5` — с маршрутами (весь API на сессиях).
- WP20.1, WP20.5 (C, `phase-4-c-wp20`, коммит `d161aed`): в Linux против `quack-test-*` с dbmate — `tests/materials` 4 failed (`NotImplementedError`). Зелёный: `tests/materials`, `test_contracts.py`, `tests/db/test_sql_static.py` — 15 passed.
- WP21.2, журнал штрихов (C, `phase-4-c-wp21`, коммит `79079de`): `uv run pytest tests/canvas` — 4 failed (`NotImplementedError`). Зелёный: `tests/canvas` + `test_contracts.py` — 11 passed.
- Поле `db` (C, `phase-4-c-db-field`): `uv run pytest tests/storage/test_s1_resources.py` — 3 failed (нет поля `db`; `create_app_resources` не асинхронная). Зелёный — в Linux-контейнере против `quack-test-*` с `QUACK_TEST_REQUIRE_INFRA=1` (на Windows async-psycopg не работает с Proactor-циклом pytest — `PoolTimeout`): `test_s1_resources.py`, `test_api_llm_provider.py`, `test_app_assembly.py` — 30 passed; все пользователи `build_app_resources` (`tests/session`, `tests/domains`, `tests/artifacts`, `tests/e2e`, `test_dispatcher.py`) — 281 passed (в первом прогоне один раз упал `test_mutating_endpoints_are_idempotent`, отдельно и при повторе — зелёный; на чистом `phase-4` тот же набор — 281 passed).
- WP24 (C, `phase-4-c-wp24`, коммит `c98d1b8`): в Linux (контейнер uv python3.12-bookworm-slim + `playwright install --with-deps chromium`) `pytest tests/test_wp24_endpoints.py` — 7 failed (`NotImplementedError`), 1 xfailed. Зелёный: весь `compute` — 74 passed, 1 xfailed (BLOCKED-C-201).
- WP10.5 `viz_builder@1` по D-B-130 (B): `uv run pytest tests/llm/test_labels.py` — 2 failed (`test_prompt_has_its_markers[viz_builder@1]`, устаревший `PROMPT_CONTRACTS.md`), 54 passed, коммит `1d7572d`. Зелёный: `tests/llm` — 76 passed.
- WP19 (C, `phase-4-c-wp19`, коммит `4fb10f3`): `uv run pytest tests/storage/test_storage_wp19.py` — ошибка сборки (нет `quack.storage.keys`, `quack.storage.uploads`). Зелёный: 12 passed; `tests/storage` — 29 passed; с затронутыми (`test_app_assembly.py` — снимок OpenAPI, `test_neo4j_outage.py`, `test_ratelimit.py`, `test_infra.py`, `tests/session/test_degradation_and_workers.py`, `test_contracts.py`) — 27 passed, 42 skipped (без инфраструктуры; их прогонит CI).
- WP2 (A), Этап I–II: схемы нагрузок (Этап I), шаблоны ключей — `NotImplementedError`: `pytest -q tests/events` — 2 failed (`T-EV3`), 71 passed (`T-EV1`, `T-EV2`, `T-EV5` на схемах Этапа I). Зелёный: 73 passed.
- WP1 (A), коммит `8c9fe33` (Этап I–II): `QUACK_TEST_REQUIRE_INFRA=1 python -m pytest -q tests/db` — 13 errors (`NotImplementedError` в `open_db`: все `T-DB5`), 6 passed (`T-DB1` — миграция есть с Этапа I; `T-DB2`, `T-DB4` и их самопроверки; формат `actor`). Зелёный (`phase-4-a-wp1`): 19 passed.
- S13, подмены (C, `phase-4-c-s13`, коммит `dbe6022`): `uv run pytest tests/test_meta_mock.py tests/test_support_scripted_llm.py` — `test_meta_mock.py`: 12 красных (нет SSE), 47 зелёных; `test_support_scripted_llm.py` — ошибка сборки (`ScriptedToolCall` нет). Зелёный: 80 passed; с затронутыми (`tests/llm`, `test_llm_runtime.py`, `tests/session/test_phase3_hot_path.py`) — 166 passed, 32 skipped (без инфраструктуры; их прогонит CI).
- S13, сбои на шаге с инструментами (C, замечание B к #87): `test_stream_incomplete_on_a_tool_step_has_no_calls`, `test_stream_invalid_json_waits_for_the_answer_step` на прежнем `tests/support/llm.py` — 2 красных. Зелёный: `tests/test_support_scripted_llm.py` + `tests/test_meta_mock.py` — 82 passed.
- WP10.5 лимиты (B): `uv run pytest tests/llm/test_phase4_ai_layer.py` — 1 failed (`test_role_table_matches_t111`: новые лимиты D-B-134), 9 passed.
- WP10.5 стенд (B, `phase-4-b-prompts`): `uv run pytest tests/llm/test_labels.py` — 10 failed (`test_label_map_labels_domains` — нет `domains`; `test_prompt_has_owner_doctrine` × 8 — заглушки; устаревший `PROMPT_CONTRACTS.md`), 43 passed. Зелёный: `tests/llm` — 73 passed.
- WP10.5 промпты (B, `phase-4-b-prompts`): `uv run pytest tests/llm/test_labels.py -k prompt` без
  файлов промптов Фазы 4 — 25 failed (`PromptNotFoundError`, устаревший `PROMPT_CONTRACTS.md`),
  16 passed, 8 xfailed. Зелёный: 41 passed, 8 xfailed.

- Этап I G1 (B): `uv run pytest tests/session/test_phase4_contracts.py tests/llm/test_phase4_ai_layer.py tests/practice/test_tasks_markup.py tests/practice/test_check_instant.py tests/test_contracts.py` — 33 красных (`NotImplementedError`: `TurnStreamParser`, `next_step_input`, `validate_markup`, `check`), 26 зелёных (контракты, `T-AI1`, `T-A6`).
- WP10.2–10.3 (адаптер и цикл): `tests/llm/test_phase4_stream_adapter.py` написан до реализации, но красный прогон не снят отдельно (до реализации тесты падали бы на импорте `quack.llm.tool_loop`). Зелёный: 9 passed; с затронутыми старыми (`tests/llm`, `test_llm_runtime.py`, `test_meta_mock.py`, `test_thresholds_in_settings.py`, `test_contracts.py`) — 127 passed.
- WP12.1 (`T-TK1`, `tests/practice/test_tasks_store.py`): красный прогон отдельно не снят — тесты и код в одной итерации, до установки `dbmate` все 4 пропускались. Зелёный: `QUACK_TEST_REQUIRE_INFRA=1` — 4 passed (dbmate 2.37.0, Postgres 16).
- WP11.5 ядро математики (#79, коммит `85e07f9`): один коммит, красный прогон не снят отдельно (замечание C по §7.4). Зелёный: `compute/tests/math` — 40 passed.
- WP11.5, пороги через `Params` (D-B-124): `cd compute && uv run pytest tests/math/test_params.py` — красный на сборке (`TypeError: Params.__init__() got an unexpected keyword argument 'precision_digits'`), коммит `32db37f`. Зелёный: `compute/tests/math` — 43 passed.
- WP11.5, эндпоинты без значений по умолчанию (D-B-124, #83): `cd compute && uv run pytest tests/math/test_service.py` — 11 failed, 4 passed (422 и `ValidationError`: `numeric`, `tolerance` не в схеме). Зелёный: весь `compute` — 67 passed.
- Зелёный прогон (B, `phase-4-b`): те же файлы — 59 passed. Полный прогон набора на итоговом SHA (§0.4.7) — job `backend` CI (`QUACK_TEST_REQUIRE_INFRA=1`); SHA и итог — в описании PR #72.
- WP11.3, быстрая проверка (D-B-126): `cd backend && uv run pytest tests/practice/test_check_fast.py` — 11 failed (`NotImplementedError` в `check()`), коммит `6f32612`. Зелёный: 11 passed; с затронутыми (`tests/practice`, `tests/test_contracts.py`, `tests/storage/test_compute_client_math.py`, `tests/test_thresholds_in_settings.py`) — 67 passed.
- WP11.7 (D-B-127): `cd backend && uv run pytest tests/practice/test_check_reading.py` — 4 failed, 1 passed (набранный ответ не меняется уже сейчас), коммит `883b237`. Зелёный: `tests/practice` — 55 passed.
- WP11.6, чистая часть (D-B-128): `cd backend && uv run pytest tests/practice/test_second_opinion.py` — 14 failed (`NotImplementedError`), коммит `6e3de38`. Зелёный: 14 passed; с затронутыми (`tests/practice`, `tests/test_contracts.py`, `tests/test_thresholds_in_settings.py`) — 81 passed.
- WP13.8 (`T-TT8`): `tests/session/test_turn_effort.py` — 6 failed, 3 passed (`TypeError`: нет флагов), коммит `00cb285`. Зелёный: 9 passed.
- WP13.5 (`T-TT4`, часть): `tests/session/test_head_guards.py` — 15 failed (`NotImplementedError`), коммит `26755dd`. Зелёный: 15 passed.
- WP13.4 (`T-TT3`, часть): `tests/session/test_holdback.py` — 4 failed (`NotImplementedError`), коммит `83675d3`. Зелёный: 5 passed; с затронутыми (`tests/session/test_spoiler_guard.py`, `test_phase3_hot_path.py::test_teacher_reasoning_effort_by_situation`, на инфраструктуре B) — 43 passed.
- S3, модели B из ядра (Q-A-1 вариант (а)): `cd backend && uv run pytest tests/practice/test_core_models.py` — 11 failed (модели `quack.practice` — копии, не объекты `quack.events.values`), 3 passed (поля форм с метками и с id уже совпадают), коммит `ed6188c`. Зелёный: 14 passed; с затронутыми (`tests/practice`, `tests/events`, `tests/session/test_phase4_contracts.py`, `tests/llm/test_phase4_ai_layer.py`, `tests/test_contracts.py`, `tests/test_thresholds_in_settings.py`, инфраструктура B) — 195 passed. `CheckFailed` теперь модель Pydantic ядра — вызовы B с именованными аргументами.
- D-B-144 (грамматика API, отношения): `cd backend && uv run pytest tests/practice/test_grammar_relations.py` — 5 failed, 4 passed (отказы и так отказы), коммит `b2d0309`. Зелёный: 9 passed; с затронутыми (`tests/practice`, `tests/session/test_phase4_contracts.py`, `tests/test_contracts.py`) — 116 passed.

- S3, заморозка (A, `phase-4-a-s3-freeze`, коммит `dc04d39`): `pytest tests/events/test_catalog.py -k frozen_kinds` — 5 failed (другие значения `kind` и чужая привязка `topic_id`/`hub_id` принимаются), 1 passed. Зелёный: `tests/events`, `tests/jobs` — 111 passed.
- S5 (A, `phase-4-a-s5`, коммит `c1121a7`, D-A-125): `pytest tests/graph/test_projector_rows.py` — 1 failed (строк нет), 2 passed (проверка путей на пустой таблице и самопроверка). Первый красный падал не по делу — каталог в тесте не был загружен (`SPECS` пуст); исправлено до коммита. Зелёный — в описании PR.
- Швы WP15 (A, `phase-4-a-wp15-seams`, коммит `5281ea1`, D-A-124): `QUACK_TEST_REQUIRE_INFRA=1 pytest tests/graph/test_extracted_check.py tests/jobs/test_coverage.py` — 15 failed (`NotImplementedError`), 1 passed (модель B подходит протоколу); на заведомо неверных реализациях — 10 из 10 подмен роняют тест (D-A-124). В красном прогоне был ложный зелёный: тест отката ловил `RuntimeError`, а `NotImplementedError` — его подкласс; исправлено до красного коммита. Зелёный — в описании PR.
- S4, поля картины памяти (A, `phase-4-a-s4-fields`, коммит `9ccd3eb`, D-A-126): `pytest tests/graph/test_read_stub.py -k wp14` — 1 failed (у навыка нет типа пробела с уверенностью и времени провала памяти). Зелёный — в описании PR.
- S3, `session.started.resumed` (A, `phase-4-a-session-resumed`, коммит `5b2f6f4`, D-A-121): `pytest tests/events/test_catalog.py -k resumed` — 1 failed (`resumed` нет у модели). Зелёный — в описании PR.
- S6, `known_check` без темы (A, `phase-4-a-g1`, ревью B #117): `pytest tests/jobs/test_rules_p4.py -k without_topic` — 1 failed (ставится `check.known_claim` с `topic_id = "None"`). Зелёный — в описании #117.
- G1 потока A (`phase-4-a-g1`, коммит `692f4f3`): `pytest tests/jobs tests/graph/test_read_stub.py tests/events/test_catalog.py` — 7 failed (`JobContext` без полей §4.2 и порядок закрытия; `known_check` без задачи; нет `cancelled_requests`; `MisconceptionFact` без `statement` — 2; `check.failed {reason: unsupported}` отклонён), 104 passed. Зелёный — в описании PR.
- S3/S4/S6 (A, `phase-4-a-s4-s6`): тесты и код писались в одной итерации, красный прогон отдельно не снят (`Verdict` split, `RejectedElementRecord.index`, `quack.graph.read`, `rules_p4.py`). Зелёный: `tests/graph/test_read_stub.py`, `tests/jobs`, `tests/events/test_rejected_element.py`, `tests/practice/test_second_opinion.py`, `tests/db/test_sql_static.py`, `tests/test_contracts.py` (`T-A6`) — локально без инфраструктуры; тесты с Postgres/dbmate (`tests/db`, `tests/events/test_catalog.py`, `tests/practice/test_answer_transaction.py`, `tests/practice/test_tasks_store.py`) локально не запускались (нет `dbmate`) — их прогонит CI.
- WP11.6, тело `check.model` (D-B-145): `cd backend && uv run pytest tests/practice/test_check_model.py` — 12 failed (`NotImplementedError`), коммит `63ba969`. Зелёный: 12 passed; с затронутыми (`tests/practice`, `tests/llm`, `tests/test_contracts.py`, `tests/test_thresholds_in_settings.py`, инфраструктура B) — 196 passed.
- WP11.3, тело `check.symbolic` (D-B-146): `cd backend && uv run pytest tests/practice/test_check_symbolic_job.py` — 5 failed (`NotImplementedError`), коммит `2ead504`. Зелёный: 5 passed; с `tests/db/test_sql_static.py` — 9 passed.
- WP13.1, запись действий (D-B-147): `cd backend && uv run pytest tests/session/test_record_action.py` — 12 failed (`NotImplementedError`), коммит `52c769a`; на неверной реализации (другое событие у `hint_request`, нет ответа за элементом) — 2 failed. Зелёный: 12 passed; с затронутыми (`tests/session/test_phase4_contracts.py`, `tests/practice`, `tests/db/test_sql_static.py`, `tests/test_contracts.py`, инфраструктура B) — 149 passed.
- WP13.1, задача ученика у `hint_request`, `solution_request`, `check_skip` (D-B-147, ревью C #108): `uv run pytest tests/session/test_record_action.py -k another` — 3 failed (событие с чужой задачей записано), коммит `918cc9f`. Зелёный: 16 passed.
- WP13.2, граф хода (D-B-148): `cd backend && uv run pytest tests/session/test_turn_graph.py` — ошибка сбора (нет `quack.session.turn_graph`), коммит `14afaaf`; на неверной реализации (8 подмен) — каждая роняет 1–3 теста. Зелёный: 11 passed.
- WP13.4 п.4, вызов «исправь разметку» (D-B-150): `uv run pytest tests/session/test_turn_graph.py` — ошибка сбора (нет `MarkupFix`), коммит `3500e09`; на неверной реализации — 5 подмен, каждая роняет 1–2 теста. Зелёный: 14 passed.
- WP13.4 п.4, ревью C #109 (D-B-150): `uv run pytest tests/session/test_turn_graph.py` — 2 failed (исправленная задача без исходной разметки пропала из записи; отмена во время инструмента не прерывает ход), коммит `1c03555`. Зелёный: 19 passed.
- WP10.2–10.3, стриминг через шлюз (D-B-152): `uv run pytest tests/llm/test_gateway_stream.py` — 9 failed (нет `stream_request`/`streaming`), коммит `ac1ae09`; на неверной реализации — 7 из 8 подмен роняют 1–3 теста. Зелёный: 11 passed.
- WP13.5, «не перепрыгивать границу» (D-B-153): `uv run pytest tests/session/test_head_guards.py tests/session/test_turn_graph.py` — ошибка сбора (нет `Boundary`), коммит `b7a7916`; на неверной реализации — 6 подмен, каждая роняет 1–2 теста. Зелёный: 38 passed.
- WP13.6, документ плана сессии (D-B-154): `uv run pytest tests/session/test_turn_commit.py -k plan` — 3 failed (плана нет), коммит `0f7a9ab`; на неверной реализации — 3 подмены, каждая роняет тест. Зелёный: 24 passed.
- WP13.6, история сделанных шагов (D-B-154, `П-4.6`): `-k history` — 1 failed (шаг, пропущенный ходом, стёрт), коммит `fdb7955`. Зелёный: 25 passed.
- WP13.9, пояс дедлайна (D-B-149 п. е): `uv run pytest tests/session/test_turn_commit.py -k zone` — 3 failed (дата без смещения — в поясе сервера, без пояса ученика дедлайн пишется), коммит `47aa8e8` (на сборке — неизвестный параметр; с параметром-заглушкой — 3 failed, 2 passed). Зелёный: 21 passed.
- WP13.9, `known_check` без темы (D-B-149 п. ж): `-k known_check` — 1 failed (запрос без темы записан), коммит `730bf0c`. Зелёный: 26 passed.
- WP13.9, запись хода (D-B-149): `cd backend && uv run pytest tests/session/test_turn_commit.py` — ошибка сбора (нет `quack.session.turn_commit`), коммит `d7972cc`; на неверной реализации (6 подмен) — каждая роняет 1–2 теста. Зелёный: 6 passed.
- WP13.9, дедлайн из разговора, подсказки, закрытие задачи (D-B-149 п. г, д): тот же файл — ошибка сбора (нет `AnsweredAction`, `LearnerText`), коммит `6e39af5`; на неверной реализации — 8 подмен, каждая роняет 1–3 теста. Зелёный: 16 passed.
- WP3.2 часть 1 (A, D-A-129, `phase-4-a-wp3-projector`, коммиты `ceeec40`, `cabeccc`): `uv run pytest tests/graph/test_projection_rules3.py` — 12 failed (11 `NotImplementedError`, 1 — пустая таблица обработчиков). Подмены: проверка среза пропускает чужой узел или связи, тип контроля хаба домена, старые `INCLUDES` остаются, снятый дедлайн не 0, вектор без `Alias`, `origin` у `SAME_IDEA`, созданная тема теряет «знаю», петля после слияния — 9/9 роняют тест. Тесты «предложенная → созданная тема» и «петля после слияния» добавлены после реализации и проверены подменой. Зелёный: 13 passed; `test_every_projector_row_has_a_handler` остаётся красным до остальных строк S5.
- WP3.2 часть 2 (A, D-A-130, коммит `4f6f66c`): `uv run pytest tests/graph/test_projection_rules3.py` — 7 failed (6 `KeyError` — нет обработчика, таблица). Подмены: время «знаю» 0, ключ предпочтения не в PascalCase, кусок без текста ищется, навыки куска без `Alias`, сводка на событие, нет манеры, конец сессии снимает чужое нацеливание, страницы не записаны — 8/8. Проверка «чужая сессия» добавлена после реализации и проверена подменой. Зелёный: 19 passed, таблица — красная до остальных 18 строк.
- WP3.2 часть 3 (A, D-A-131, коммит `c74ab8d`): `uv run pytest tests/graph/test_projection_rules3.py` — 8 failed (7 `KeyError` — нет обработчика, таблица). Подмены: свои связи цели переносятся, петля к цели (наружу и внутрь), своя связь цели перезаписана, старый узел остаётся, прежние псевдонимы теряются, разбиение оставляет старый `COVERS`, `origin` новой части, пустая тема видна, часть берёт описание — 10/10 (связь `SAME_IDEA` к цели добавлена в тест после реализации и проверена подменой). Зелёный: 26 passed, таблица — красная до 13 строк задач и свидетельств.
- WP3.2 часть 4 (A, D-A-132, коммит `3135b8b`): `uv run pytest tests/graph/test_projection_rules3.py` — 4 failed (3 `KeyError` — нет обработчика `task.created`, таблица). Подмены: имя без нормализации, подсказка темы без нормализации, навык домена не найден, `TESTS` без уровня в ключе, части задачи не читаются, статус гипотезы, `ABOUT` к последнему навыку, старые `TESTS` остаются, `markup_disputed` → checked, «соседняя идея» теряется — 10/10. Зелёный: 29 passed, таблица — красная до 9 строк свидетельств.
- WP3.2 часть 5а (A, D-A-133, коммит `91cdf60`): `uv run pytest tests/graph/test_projection_rules3.py` — 6 failed (`NotImplementedError`, `TESTS` без части, таблица). Подмены: событие без проверки `needs`, ссылки через событие не читаются, события пачки не сохраняются, `TESTS` без части в ключе, части помечены `main` — 5/5. Зелёный: 31 passed, таблица — красная до 9 строк свидетельств.
- WP3.2 часть 5б (A, D-A-134, коммиты `4debc8c`, `f2fa148`): `uv run pytest tests/graph/test_projection_rules3.py` — 12 failed (11 `KeyError` — нет обработчиков, таблица). До зелёного исправлены данные теста: ответ `{kind, value}` по `LearnerAnswer`, а не `option_id`. Подмены: несогласное второе мнение остаётся Т2, спорная задача не понижает, проявление от другого свидетельства, сопротивление при показе решения, карточка на пару вместо навыка, «описка» без `slip`, выведенные не оспорены, самооценка теряется, `sourceSeq` вердикта, `unclear` даёт свидетельство, гипотеза не создаёт заблуждение, срез задачи без вариантов, без свидетельств — 13/13 (тест гипотезы добавлен после реализации и проверен подменой). Зелёный: 43 passed, таблица — красная до 5 строк.
- `core.pairing_tokens` (A, D-A-128, `phase-4-a-pairing-tokens`, коммит `6c49e1d`): `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest tests/db/test_baseline_migration.py` — 2 failed (нет `core.pairing_tokens`). Подмены: нет файла индекса, `task_id NOT NULL`, `down` не удаляет таблицу, `used_at NOT NULL`, таблица в базовой миграции — 5/5 роняют тест. Зелёный: `tests/db` — 20 passed.
- S4, помощь у свидетельства (A, D-A-127, `phase-4-a-s4-assistance`, коммит `a6f9ed8`): `uv run pytest tests/graph/test_read_stub.py` — 1 failed (у `EvidenceFact` нет `assistance`), 11 passed. Подмены: нет верхней границы ступени, умолчание `hint`, нет `shown_solution`, ступень по умолчанию 1 — 4/4 роняют тест. Зелёный: 12 passed.
- WP15, помощь и тип пробела (A, D-A-124 п.3а, коммит `9a1a444`): `uv run pytest tests/graph/test_extracted_check.py` — 1 failed (подсказка без ступени проходит). Подмены: ступень без подсказки, подсказка без ступени, положительный с типом пробела, `partial` обязан `none` — 4/4. Зелёный: 13 passed.
- S4 кандидаты, S6 `skill.split` (A, D-A-135, коммит `8c5181b`): `uv run pytest tests/graph/test_read_stub.py tests/jobs/test_rules_p4.py` — 2 failed (`NotImplementedError`; `skill.split` ставит задачу без эпизода). Подмены заглушки: тема, навыки, `k`, заблуждения не кандидатов — 4/4. Зелёный: 40 passed.
- WP3.2 часть 5в (A, D-A-180, коммиты `d5e47a4`, `2930307`): `uv run pytest tests/graph/test_projection_rules3.py` — 12 failed (нет обработчиков, нет `episodeSeq`). Подмены: нацеливание сессии, цель задач хода, закрытие задачи, прежнее наблюдение не вытеснено, любой извлекатель, вес корня постоянный, `sourceSeq` элемента, диагноз без навыка заблуждения, несогласие поднимает ярус, гипотеза не создаёт заблуждение, время элемента из события — 11/11. Зелёный: 51 passed.
- WP3.3 (A, D-A-181): разница среза — красный `581d220` (`tests/graph/test_projection_diff.py` — 2 failed, `NotImplementedError`), подмены 4/4, зелёный `d022233`. `store.py` и `runner.py` сначала ушли коммитом WIP `fc38f86` без интеграционных тестов. `tests/graph/test_projection_runner.py` написан после них, красного прогона нет. Проверка подменами: нет отметки, остановленный поток идёт дальше, отказ не записан, нет копии отметки, узлы прошлых пачек не читаются, связи не пишутся — 6/6. Зелёный: 3 passed.
- S6 G2 `artifact.topic_refresh` (A, D-A-136, коммит `5eb9b55`): `uv run pytest tests/jobs/test_rules_p4.py` — 1 failed (`NotImplementedError`). Подмены: ключ по склейке и `seq`, блокировка по ученику, нет причины, нет `required_seq`, факты не читаются, подсказки с ключом сессии — 6/6. Зелёный: `tests/jobs` — 35 passed.

## Правки тестов

- D-C-146 (C, разбор набора): удалены без замены в коде (политика `TESTS.md` §1) — `tests/test_thresholds_in_settings.py::test_stream_c_defaults_equal_phase_2_values` (значения по умолчанию `Settings`; лимиты проверяют `tests/test_app_assembly.py::test_actions_and_turns_rate_limits`, `::test_search_limits_come_from_settings`, `::test_health_probe_timeout_comes_from_settings`, `tests/domains/test_curation_thresholds.py`), `::test_rate_limit_constants_are_gone` (надгробие; лимитёр — `tests/test_ratelimit.py`), `tests/storage/test_compute_client_math.py::test_stub_has_math_queues` (частный случай `tests/storage/test_compute_client_s10.py::test_stub_covers_every_wp24_endpoint`), `tests/session/test_fixtures.py::test_parse_sse_round_trips_format_sse` (самопроверка помощника; формат кадра — `tests/test_infra.py::test_sse_format`). Остальные тесты Фазы 3 снимутся вместе с кодом, список — `backend/TESTS.md` §4.
- A, 2026-11-03 (D-A-186, D-A-187): тесты потока A названы ID ТЗ §8: `test_every_projector_row_has_a_handler` → `test_handler_table` (T-P4; таблицу событий дают группы того же модуля), `test_schema_is_created_in_neo4j` → `test_schema_created_on_start` (T-P7, плюс повторный старт), `test_success_on_a_trap_task_resists_its_misconceptions` → `test_resist_and_manifest_sources` (T-MS2, плюс проявления ловушки Т1 и гипотезы модели Т3). Утверждения прежние или сильнее.
- A, 2026-11-03 (#182): тесты WP7 названы ID ТЗ §8: `test_every_rule_row_names_known_kinds` → `test_rule_table` (T-D1), `test_session_ended_and_turn_limit_and_intent_change` → `test_episode_boundaries` (T-D2), `test_episode_without_content_is_covered_by_the_dispatcher` → `test_episode_without_content` (T-D3), `test_coalesce_raises_required_seq_and_keeps_one_job` → `test_coalesce_greatest` (T-D4), `test_cancel_queued_and_running` → `test_cancel_requested` (T-D6), `test_retry_with_growing_pause_then_failed_with_agent_completed` → `test_failed_writes_agent_completed` (T-D8), `test_status_change_of_a_visible_job_publishes_jobs_changed` → `test_visible_and_plaque` (T-D10). `test_max_jobs_per_learner` (T-D5), `test_skipped_budget` (T-D7) уже так названы. T-D9 (`test_periodic_keys`) в #182 нет — периодические задачи Фазы 4 ещё не сделаны.

- `tests/session/test_actions_route.py` (B, D-B-156, до зелёного): клиент шлёт и токен Фазы 3 (ограничитель частоты роутера сессий), и cookie сессии; маршрут берёт ученика только из cookie. Тест «без cookie» шлёт один токен и ждёт `401 UNAUTHENTICATED`. Добавлен тест освобождения блокировки хода. Утверждения не ослаблены.
- `tests/e2e/test_isolation_and_idempotency.py`, `tests/e2e/endpoints.py` (C, WP23.1): три маршрута блоков — в обходе `T-E2`; тема для них записывается в журнал Фазы 4 (`topic.created`), которого структура Фазы 3 в обходе не пишет (D-C-112); `accept` и `DELETE` — в `NO_OTHER_BODY` (тело — только ключ).
- `tests/test_app_assembly.py` (C, WP23.1): в `EXPECTED_ROUTES` и `_declared_routes` добавлены четыре маршрута конспекта §6.4 (§8.15 п.2 — новый маршрут контракта).
- `tests/e2e/endpoints.py`, `tests/test_app_assembly.py` (C, WP22.2): маршрут снимка — в `T-A1`, в `NO_IDEMPOTENCY_KEY` и `MULTIPART` (`T-E2`, `T-E3`).
- `tests/db/test_sql_static.py` (C, WP22.1): в `CROSS_LEARNER_ALLOWED` добавлен `quack/auth/pairing.py:_REDEEM_TOKEN` — согласовано A в #142 (D-A-128).
- `tests/e2e/endpoints.py`, `tests/test_app_assembly.py` (C, WP22.1): маршруты привязки — в контракте `T-A1`, в `NO_IDEMPOTENCY_KEY` и `NO_BODY` (`T-E2`, `T-E3`).
- `tests/jobs/test_rules_p4.py::test_handwriting_known_claim_and_reextraction_rows` (A, D-A-135): `skill.split` больше не ставит задачу только по событию. Тест проверяет, что строка — с данными сверх события (по эпизодам, `Т-§2.8`); прежнее утверждение (одна задача без эпизода) расходилось с `Т-§2.8` (§8.15 п.3).
- `tests/session/test_teacher_context.py::test_conversation_*` (B, D-B-163): строки канваса в слоте 5 — «Canvas line L{N}» вместо «Canvas line N» (метки для `voice_at_line`). Проверка не ослаблена.
- `tests/session/test_teacher_context.py` (B, D-B-157, D-B-158, до зелёного): четыре подмены прошли (implied и transfer как proven, навык без имени в картине, навык другого домена без доказанного уровня, нумерация строк канваса заново после свёртки). Добавлены `test_memory_picture_status_words`, навык другого домена с провалом в срезе и вторая строка канваса в несвёрнутом ходе. Тесты усилены, не ослаблены.
- `tests/session/test_sessions_db.py::test_start_writes_the_event_and_the_row` (B, D-B-155): нагрузка `session.started` получила поле `resumed: false` (D-A-121, #130) — ожидание дополнено полем. Проверка не ослаблена.
- `tests/agents/test_extract_episode.py` (B, D-B-162, до зелёного): две подмены прошли (метки заблуждениям не-кандидатов; `episode_context_turns = 0` без ходов перед эпизодом). Добавлены проверки меток заблуждений и окна при нуле. Тесты усилены, не ослаблены.
- `tests/graph/test_extracted_check.py::test_negative_elements_need_a_quote_from_their_sources` (A, D-A-124 п.3а, до слияния #133): положительный элемент с новым навыком получил `gap_type = none` — прежние данные нарушали контракт выхода (WP15 п.3). Проверяемое утверждение (`missing_quote`) то же.
- `tests/test_compose_contract.py` (C, D-C-119): удалён `test_only_api_has_the_jwt_secret` из #122 — `JWT_SECRET` удаляется вместе с JWT (§2.3 строка 3, §8.15 п.1); из проверки `projector` убрано несуществующее имя `JWT_SECRET`.
- `tests/session/test_head_guards.py::test_boundary_names_the_missing_prerequisites_once` (D-B-153, до зелёного): проверка «id не в тексте» смотрела на id предпосылки с меткой и не ловила утечку id предпосылки без метки (подмена прошла); теперь проверяются оба id и точный текст. Тест усилен, не ослаблен.
- `tests/practice/test_check_symbolic_job.py::test_unsupported_form_writes_nothing_yet` → `test_unsupported_form_writes_check_failed_for_the_model` (B, D-B-146): прежний тест закреплял временное поведение до ответа A («`unsupported` нет в каталоге»); A добавил значение в #117, тест проверяет новое поведение: `check.failed {reason: unsupported}` под ключом `check_failed_key`. Тест не ослаблен.

- `tests/session/test_holdback.py` (B, до зелёного): `test_spoiler_found_only_at_the_end` ошибочно ждал спойлер только в `finish`, хотя по WP13.4 весь накопленный текст проверяется на каждом куске. Заменён на `test_spoiler_completed_inside_the_holdback` (спойлер ловится на куске, который его дописал) и `test_finish_checks_the_whole_text`. Проверка не ослаблена: показанный текст по-прежнему пуст.
- `tests/llm/test_phase4_ai_layer.py` (`T111`): `max_output_tokens` десяти ролей — по замеру (D-B-134); `Т-§11.1` разрешает менять конфиг по замеру. Сила проверки не меняется.
- `tests/test_contracts.py` (`T-A6`): в `PRODUCT` добавлен `agents` (ТЗ Ф4 §4.1). Сила проверки не меняется.
- `tests/test_app_assembly.py` (C, PR #75, §8.15 п.2): `T-A1` — в набор маршрутов добавлен
  `POST /v1/sessions/{session_id}/actions` (§6.4). `test_openapi_documents_every_response` — у
  `/actions` ровно два ответа 2xx, `200` (SSE) и `201` (`{seq}`) по §4.7, у остальных — по-прежнему
  ровно один; модель SSE-ответа принимается из `itemSchema` (так FastAPI описывает одно событие в
  OpenAPI 3.2), модель обязательна, как раньше. `test_sse_endpoints_document_event_stream` расширен
  на `/actions`. Тест падает на маршруте с одним 2xx или без модели события (§8.15 п.4).
  `backend/openapi.json` пересобран `ops openapi`.
- `compute/tests/math/test_equivalence.py`: `PARAMS` берётся из `test_params.py` (у `Params` новые поля, D-B-124). Утверждения не менялись.

- D-C-119 (C, по D-A-113 и D-C-112 п.2): удалены вместе с кодом (§8.15 п.1) — `tests/domains/test_auth_and_learner.py::test_register_normalizes_email_and_rejects_duplicate` (вход по ссылке и ученик по адресу — `T-AU1` `test_magic_link_flow`), `::test_login_and_me_jwt_validation` (сессия, отзыв, 401 без неё — `T-AU4` `test_session_revocation`, `tests/auth/test_auth_routes.py::test_bad_link_and_no_session_are_401`), `::test_auth_rate_limiting_per_ip` (лимиты входа на адрес и IP — `T-AU2` `test_link_rate_limits_*`); `tests/session/test_fixtures.py::test_token_uses_dev_secret_when_jwt_secret_is_empty` (JWT нет); `tests/test_ops_phase3.py::test_seed_learners_refuses_without_jwt_secret` (JWT_SECRET нет, сессия не требует секрета); `tests/test_settings_meta_api.py::test_prod_jwt_secret_is_at_least_32_bytes` (JWT нет).
- D-C-119, перенос на сессии (§8.15 п.2), утверждения те же: способ входа — `login`/`session_cookie`/`learner_headers` вместо Bearer (`tests/domains/*`, `tests/session/*`, `tests/artifacts/conftest.py`, `tests/e2e/system.py`, корневые `test_dispatcher`, `test_jobs_phase3`, `test_learner_stream`, `test_neo4j_outage`, `test_ops_phase3`); `test_update_preferences_and_idempotency` и `test_two_phase_account_deletion` — только вход (D-A-113). Код 401 — `UNAUTHENTICATED` вместо `UNAUTHORIZED` (§4.5) в `test_auth_and_stream_resilience.py`, `test_artifacts_search_export.py`, `test_app_assembly.py` (`T-A2`, `T-A3`), `test_two_phase_account_deletion`.
- D-C-119, замена удалённого маршрута равным: «после удаления запрос отклонён» в `test_two_phase_account_deletion` проверяется на `PATCH /v1/learner/preferences` (был `GET /v1/auth/me`); «токены сида работают» в `test_seed_learners` и «после удаления вход отклонён» в `T-E` (`test_journey_and_laws.py`) — на `GET /v1/domains` (были `/v1/auth/me` и `/v1/auth/login`); `T-A3` переименован в `test_single_session_contract_across_packages`: чужая подпись JWT → неизвестная кука. `test_learner_without_auth_user_row_is_rejected` → `test_request_without_session_is_rejected` (без сессии — 401; строки `auth.users` для входа больше нет).
- D-C-119, по ревью B (комментарий к `4db22c4`): `tests/session/test_session_terms.py` (B, #111) — вход только кукой сессии (`session_token` + `session_cookie`) вместо Bearer и `ensure_users_hook`; без куки — 401 `UNAUTHENTICATED`; утверждения 403 `CONSENT_REQUIRED` и 201 те же. `tests/session/fixtures.py` сохраняет `dependency_overrides[require_terms]` (D-B-151).
- D-C-119, локальный лимитёр без Redis (`T-A4`, `T-E8` в `test_degradation.py`): лимит `auth` по IP ушёл с `/v1/auth/login`; та же проверка «N проходят, N+1 — 429 `RATE_LIMITED` без Redis» — на лимите `actions` (`learner_actions_per_min`), утверждения те же.
- `tests/test_thresholds_in_settings.py`, `tests/test_infra.py`, `tests/test_settings_meta_api.py`: убраны `auth_requests_per_ip_per_min`, `jwt_ttl_days`, `jwt_secret` (параметры удалены, D-C-119); остальные утверждения те же. `tests/test_loadtest_scenario.py`: заголовок ученика — кука `quack_session`.

## Вопросы к команде

- **Q-A-2 (A → B, к заморозке S3 на G1).** (1) `Verdict.misconception` в событии — id заблуждения или гипотеза словами, метка недопустима (`Т-§4.5`); оставить одно поле `str` или разделить на `misconception_id` и `misconception_hypothesis`? (2) `element.responded` по §6.3 — без `answer`; ответ на элемент-задачу пишется как `answer.submitted` с `element_id` — так? (3) `observation.extracted.rejected` — форма `{reason, quote}` достаточна? (4) `RootCauseRecord.confidence` — категория в событии, число по `root_cause_confidence_map` считает проектор (A) — согласен? (5) `item.flagged_unclear.text` вместо `quote` (D-A-107). (6) `CheckFailed` стал моделью Pydantic — позиционный конструктор dataclass больше не работает.
- **Q-A-3 (A → C, к заморозке S3 на G1).** (1) `observation.diagnosed`: `diagnosis.kind` (`error`, `question`) и `method` (`model`, `symbolic`) — по WP5.1, WP21.4 так? (2) `canvas.line_committed.kind` — пока `str`: дай закрытый перечень видов строки (`Т-§15.3`). (3) `material.parsed` — `{material_id, pages_count}` достаточно? (4) `artifact.updated.kind` — пока `str`: назови значения видов WP23.1. (5) `DeletionRecord.target_id` — UUID, как `ops.deletion_log.target_id` (ревью #74).
- **Q-A-3 — ответ C** (2026-11-03; ТЗ Ф4 §6.3, WP5.1, WP21.3–21.5, WP23.1, `Т-§15.3`, `Т-§15.4`, `Т-§17.1`):
  1. `observation.diagnosed.method` ∈ {`model`, `symbolic`} — **да**. Основания: WP21.4 п.1 (символьное расхождение) и п.3 (`canvas_diagnoser`); ярусы WP5.1 различают «модель» и «символьное расхождение с указанием заблуждения моделью». Согласное второе мнение — не метод, а отдельное событие `observation.second_opinion {target_seq, agreed}`. `diagnosis.kind` ∈ {`error`, `question`} — **да, с уточнением смысла**. «Неуверенный» по документу — это уверенность ниже `mark_min_confidence` (`Т-§15.4`, таблица пометок; WP21.5). Поэтому C пишет `kind = question`, когда `method = model` и `confidence < mark_min_confidence`, иначе `kind = error`. Правило D-A-110 п.5 (`question` → `second_opinion.diagnosis`) этим согласовано. Пометку на доске (WP21.5) код C считает отдельно: `error` на доске — только при `method = symbolic` или согласном втором мнении. Значит, `kind = error` с `method = model` без второго мнения пометки не даёт. Поля `misconception_id` / `misconception_hypothesis` / `root_cause_skill_id` — как в Q-A-2 п.1. Диагноз без найденной ошибки события не пишет — решено в D-C-120 (по документам, владелец поручил решить C); S3 не меняется.
  2. `canvas.line_committed.kind` ∈ {`latex`, `text`}. Основание: `Т-§15.3`, распознаватель выдаёт «LaTeX или текст». Набранная формула (`source = typed`) — всегда `latex` («дают LaTeX напрямую»). Строки с фото (WP22.3) — тот же перечень. Зачёркнутость — отдельное поле `crossed_out`, не вид.
  3. `material.parsed {material_id, pages_count}` — **достаточно** для C. ТЗ полей не называет: канал ученика несёт только `material_parsed {material_id}` (§4.8), `T-MT1` проверяет сам факт события. `pages_count` — столбец `content.materials` (§6.1). Куски и структура приходят своими событиями (`chunk.indexed`, события структуры). Если проектору для узла `Material` (WP3, таблица проектора) нужно что-то ещё — решает A.
  4. `artifact.updated.kind` ∈ {`notes`, `cards`, `cheatsheet`}. Документ называет виды только словами («конспект темы, карточки темы, cheat sheet хаба», WP23.1, `Т-§17.1`). Английские имена взяты из маршрутов §6.4: `GET /v1/topics/{id}/notes`, `/cards`, `GET/POST /v1/hubs/{id}/cheatsheet`. Тот же перечень — в `content.artifacts.kind` (WP23.1, C). Связка: `notes` и `cards` — с `topic_id`, `cheatsheet` — с `hub_id` (`topic_id | hub_id`, WP23.1). Проверку «ровно одно из двух по виду» в модели можно поставить в G1.
  5. `DeletionRecord.target_id: UUID` — **да**, решено в #74 (`8ea7a42`, слова владельца и согласие A).
- **Q-A-1 (A → B, класс C, шов S3).** Каталог событий живёт в ядре (`quack.events`, §4.1), и импортировать модели B из `quack.practice`, `quack.agents`, `quack.session` он не может (слои §4.1, `T-A6`). Кроме того, разметка `turn.committed` хранит id, а `TurnMarkup` модели — метки. Предложение A: модели нагрузки — в каталоге (`quack.events.registry`, S3), поля — по моделям B (`Verdict`, `CheckFailed`, `ExtractedElement`, `TurnMarkup` с id, `LearnerAnswer`); B импортирует их из ядра вместо своих копий или держит свои модели, а тест-контракт сверяет поля. Нужен выбор B до G1.
- **Q-A-2 (A → B) — ответ B:** (1) `Verdict.misconception` разделить на `misconception_id` и `misconception_hypothesis`; (2) да, ответ на элемент-задание — `answer.submitted` с `element_id`; (3) `rejected` — предложение `{index, reason, quote}` (провенанс, закон 6), решает A; (4) согласен, число корня — при проекции по `root_cause_confidence_map`; (5) согласен с D-A-107; (6) согласен. Подробно — комментарий в PR #80.
- **Q-A-1 (A → B, класс C, шов S3) — ответ B: вариант (а).** Модели нагрузки событий, которые пишет B (`answer.submitted`, `verdict.recorded`, `check.failed`, `observation.extracted`, `turn.committed`, `task.created` и др.), живут в ядре — в `quack.events.registry` (A), с полями по моделям B из PR #72. B импортирует их из ядра. Почему: ядро не может импортировать продукт (§4.1, `T-A6`); WP2.1 требует модель Pydantic на каждый тип в реестре и отказ неверной нагрузки при записи — это возможно только с моделями в ядре; одна реализация вместо двух (§0.4.3, «частые случаи»: два потока сделали по-разному). Раскладка:
  - модели с одинаковой формой в событии и в продукте — `LearnerAnswer`, поля `Verdict`, `CheckFailed`, `AnswerSpec` (внутри разметки `task.created`) — переезжают в ядро; `quack.practice` их переэкспортирует, а не копирует. Переезд делает A при заморозке каталога (S3, G1), B меняет импорты одним PR сразу после;
  - модели выхода моделей с метками (`TaskMarkup`, `TurnMarkup`, `ExtractedElement`) остаются у B; в событии — их форма с id вместо меток, это модели ядра (`TaskMarkupIds` и т. п., имена — A). Перевод «метки → id» — код B (`quack.llm.labels`);
  - тест-контракт B: модели `quack.practice` — те же объекты, что в реестре, и у моделей с метками те же поля, что у формы с id, кроме полей меток.

- **Q-C-1 (C → владелец, к сведению B).** `compute/` на `main`: в первом прогоне в контейнере,
  запущенном одновременно с полным `pytest` backend, упали 2 теста из 5
  (`test_equation_roots_and_matrix` и ещё один); три следующих прогона — 5 passed. Похоже на
  таймаут проверки под нагрузкой (`multiprocessing` с таймаутом). WP24 переводит пул на
  `pebble` — проверить там; до этого не трогаю.
- **Q-A-4 (A → B, WP5.3).** Для свёртки профиля нужны входы, которых нет в событиях Фазы 4 в нужном виде: (1) `confidence.reported {target_seq, value}` — на какое событие указывает `target_seq`: на `answer.submitted`/`element.responded` (я так читаю: сопоставление с `Evidence.sourceSeq`) или на `turn.committed`? Если второе, самооценку нельзя сопоставить со свидетельством, и калибровка по ней не считается. (2) `element.responded` у свидетельств извлекателя: `Evidence.sourceSeq` = `seq` ответа на элемент? Иначе самооценка элемента не найдёт свои свидетельства. (3) `Т-§9.5` не задаёт порог для `calibrated` — оставил точный ноль (D-A-170 п.2); если нужен допуск — нужно значение от владельца.

## Для фронтенда

Снимок `backend/openapi.json` и таблица «старый эндпоинт → новый» (§6.4) — на каждом гейте.

### Черновик к G1 (решение владельца 2026-11-02, передано потоком B; D-B-119)

- Снимок: `backend/openapi.json` в ветке `phase-4-b-actions-route`, коммит `679b8ea` или новее
  (PR #75): https://github.com/steppetech1/Quack/blob/679b8ea/backend/openapi.json
- **Маршрут `/actions` объявлен, тело — WP13.1.** `POST /v1/sessions/{session_id}/actions`:
  `200` — `text/event-stream`, событие — `ActionFrame` (кадры §4.7), `Element`; `201` —
  `ActionAccepted`.
- `backend/openapi.json` в `phase-4` до слияния #75 не меняется. Таблица «старый → новый» — §6.4 ТЗ.

## DoD

Таблица §10 — заполняется к G4.
