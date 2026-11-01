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
- **Чистый старт (WP0 п.2): подтверждён владельцем в чате 2026-11-02** — событий настоящих учеников нет ни в одном окружении; локальные стенды `quack-*` и `quack-test-*` тестовые.

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
| WP2 (A) | A | черновик каталога S3 (G0) в `phase-4-a-wp2`: 65 типов §6.3, общие модели B в ядре (Q-A-1 (а)), ключи, пометки обработчиков и правил; заморозка — G1 после Q-A-2, Q-A-3 | `tests/events`: 73 passed |
| WP1.1–1.2 (A) | A | выполнен в `phase-4-a-wp1` (шов S2): базовая миграция dbmate, `Db`, `append_events`, параметры §6.7 потока A. WP1.4 (`.squawk.toml`, S16) — к G2; WP1.3 — к G3/G4 | `tests/db`: 19 passed |
| WP0 (зона B) | B | выполнен: аудит, п.3г, п.3д, п.5 | — (скрипт п.3г) |
| Этап I G1 (B: S7, S8, S11, S13) | B | контракты в `phase-4-b`, тесты контрактов зелёные | см. «Красные прогоны» |
| WP10.2–10.3 (B) | B | частично: разбор потока хода и вход шага цикла с инструментами — зелёные (`T-AI2`–`T-AI4`); адаптер `ResponsesClient.stream_structured` и цикл `run_tool_loop` (параллельные инструменты с таймаутом, повтор оборванного с удвоенным лимитом) — зелёные (`T-AI5`, `T-AI6` без строки `ops.llm_usage` — она в WP10.4); встраивание в шлюз (допуск, семафоры, учёт) — с WP13 | 19 passed |
| WP11 (B) | B | частично: `check()` мгновенные типы (WP11.2) — зелёный `T-CH1` (чистая часть; «в той же транзакции» — с WP13.1 на `Db`); быстрые и медленные — дальше | 11 passed |
| WP12.1–12.3 (B) | B | частично: `validate_markup` (WP12.3) — зелёный `T-TK2`; `content.tasks` + `task.created` (WP12.1) ждут `Db` и каталог (S2, S3, A) | 14 passed |

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

- WP2 (A), Этап I–II: схемы нагрузок (Этап I), шаблоны ключей — `NotImplementedError`: `pytest -q tests/events` — 2 failed (`T-EV3`), 71 passed (`T-EV1`, `T-EV2`, `T-EV5` на схемах Этапа I).
- WP1 (A), коммит `8c9fe33` (Этап I–II): `QUACK_TEST_REQUIRE_INFRA=1 python -m pytest -q tests/db` — 13 errors (`NotImplementedError` в `open_db`: все `T-DB5`), 6 passed (`T-DB1` — миграция есть с Этапа I; `T-DB2`, `T-DB4` и их самопроверки; формат `actor`). Зелёный (`phase-4-a-wp1`): 19 passed.
- Этап I G1 (B): `uv run pytest tests/session/test_phase4_contracts.py tests/llm/test_phase4_ai_layer.py tests/practice/test_tasks_markup.py tests/practice/test_check_instant.py tests/test_contracts.py` — 33 красных (`NotImplementedError`: `TurnStreamParser`, `next_step_input`, `validate_markup`, `check`), 26 зелёных (контракты, `T-AI1`, `T-A6`).
- WP10.2–10.3 (адаптер и цикл): `tests/llm/test_phase4_stream_adapter.py` написан до реализации, но красный прогон не снят отдельно (до реализации тесты падали бы на импорте `quack.llm.tool_loop`). Зелёный: 9 passed; с затронутыми старыми (`tests/llm`, `test_llm_runtime.py`, `test_meta_mock.py`, `test_thresholds_in_settings.py`, `test_contracts.py`) — 127 passed.
- Зелёный прогон (B, `phase-4-b`): те же файлы — 59 passed. Полный прогон набора на итоговом SHA (§0.4.7) — job `backend` CI (`QUACK_TEST_REQUIRE_INFRA=1`); SHA и итог — в описании PR #72.

## Правки тестов

- `tests/test_contracts.py` (`T-A6`): в `PRODUCT` добавлен `agents` (ТЗ Ф4 §4.1). Сила проверки не меняется.

## Вопросы к команде

- **Q-A-2 (A → B, к заморозке S3 на G1).** (1) `Verdict.misconception` в событии — id заблуждения или гипотеза словами, метка недопустима (`Т-§4.5`); оставить одно поле `str` или разделить на `misconception_id` и `misconception_hypothesis`? (2) `element.responded` по §6.3 — без `answer`; ответ на элемент-задачу пишется как `answer.submitted` с `element_id` — так? (3) `observation.extracted.rejected` — форма `{reason, quote}` достаточна? (4) `RootCauseRecord.confidence` — категория в событии, число по `root_cause_confidence_map` считает проектор (A) — согласен? (5) `item.flagged_unclear.text` вместо `quote` (D-A-107). (6) `CheckFailed` стал моделью Pydantic — позиционный конструктор dataclass больше не работает.
- **Q-A-3 (A → C, к заморозке S3 на G1).** (1) `observation.diagnosed`: `diagnosis.kind` (`error`, `question`) и `method` (`model`, `symbolic`) — по WP5.1, WP21.4 так? (2) `canvas.line_committed.kind` — пока `str`: дай закрытый перечень видов строки (`Т-§15.3`). (3) `material.parsed` — `{material_id, pages_count}` достаточно? (4) `artifact.updated.kind` — пока `str`: назови значения видов WP23.1. (5) `DeletionRecord.target_id` — UUID, как `ops.deletion_log.target_id` (ревью #74).
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
