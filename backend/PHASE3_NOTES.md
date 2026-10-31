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
| WP1 | C | WP1.2, WP1.3 — выполнено (Этапы I–III); WP1.1, WP1.4–WP1.6 — не начаты (роутеры уже смонтированы, D-C-007) | `T-A2`, `T-A3` зелёные; всего 412 passed |
| WP2 | A | не начат | — |
| WP3 | A | не начат | — |
| WP4 | C | не начат | — |
| WP5 | B | не начат | — |
| WP6 | B | не начат | — |
| WP7 | C | не начат | — |
| WP8 | A, C | не начат | — |
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

_(пусто)_

### Поток B

_(пусто)_

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
- Требует подтверждения владельцем: нет. **Нужно от B:** в `_set_presence` — `if resources.redis is None: return`.

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

## Заблокировано

_(пусто)_

## Красные прогоны

| WP | Команда | Итог |
| :-- | :-- | :-- |
| WP1.2–1.3 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/test_app_assembly.py` (коммит `fd8d31b`) | 2 failed: `T-A2` — `GET /v1/domains is not mounted`; `T-A3` — `POST /v1/auth/register` → 404 |
| WP10.2 | `QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q tests/e2e` (ветка `phase-3-c-e2e-red`) | 6 failed, 4 passed. Падают: `T-E1` — нет колонки `ops.jobs.required_seq` (A, WP3.1/WP11); `T-E2` — `structure/propose` с тем же ключом и другим телом → 200, ждали 409 (**дефект C**, `domains`); `T-E3` — `GET /v1/topics/{id}/cheatsheet` чужой темы → `200 null`, ждали 404 (**дефект C**, `artifacts`, STREAM3_NOTES риск 9); `T-E5` — обработчики задач не берут шлюз из `JobContext`, извлечение идёт мимо `ScriptedLLMProvider` (A, WP2.1); `T-E8` — ограничитель fail-open без Redis (C, WP1.5); `T-E10` — нет `purge_deleted_interval_s` и планировщика (A, WP3.3). Зелёные уже на текущем коде: `T-E4`, `T-E6`, `T-E7`, `T-E9` — проверяют поведение Фаз 1–2; DoD п.3 («были красными») для них не выполним, проверка «тест падает на заведомо неверной реализации» (§8.9) — не делалась |

## Правки тестов

| Тест | Что изменено | Причина |
| :-- | :-- | :-- |
| `tests/session/test_auth_and_stream_resilience.py::test_learner_without_auth_user_row_stays_valid` → `…_is_rejected` | ожидание `200/404` → `401 UNAUTHORIZED`; запрос идёт обычным клиентом без хука D-C-006 | D-C-004 (класс C, решено по поручению владельца). Проверка не слабее: то же место, более строгое ожидание; на старой реализации (`current_learner_id` потока 2) тест падает |
| там же, `test_events_stream_without_redis_is_empty_sse`, `test_relay_ends_cleanly_on_redis_error` | конструктор `AppResources(...)` → `dataclasses.replace(res, redis=...)` | новый набор полей S1; утверждения не менялись |
| фикстуры `tests/domains/conftest.py`, `tests/session/fixtures.py`, `tests/artifacts/conftest.py` | ресурсы — `build_app_resources`; роутеры не монтируются повторно; удалены переопределения `get_llm_gateway`/`get_compute_client` (`artifacts`) и запасной разбор JWT (`domains`); хук пользователей в `client` (`session`) | §8.1, §8.9 (фикстуры и импорты); D-C-006 |

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
| Q8 | DoD п.3: `T-E4`, `T-E6`, `T-E7`, `T-E9` зелёные до реализации Фазы 3 (поведение уже есть) — принять как есть? | «Красные прогоны», WP10.2 |
| Q7 | ~~Подтвердить: токен ученика без строки `auth.users` → 401, тест потока 2 меняется~~ — закрыто 2026-10-31: решение передано исполнителю, принят вариант (1) | D-C-004 |

## DoD

| # | Пункт (ТЗ §10) | Статус |
| :-- | :-- | :-- |
| 1 | Проверки `backend/` | на `694eb50` зелёные; финальный прогон — в конце фазы |
| 2 | Проверки `compute/` | ruff зелёный; pyright и pytest — в Linux (D-C-002); финальный прогон — в конце фазы |
| 3–21 | — | не начаты |
