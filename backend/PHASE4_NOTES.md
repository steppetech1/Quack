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
| б) составной range-индекс `(learnerId, retDueAtMs)` на `Skill` (`Т-§3.4.8`) | A | — | ждёт поток A |
| в) параметры `py-fsrs` (`Т-§8.3`) | A | — | ждёт поток A |
| г) `antlr4-python3-runtime` для SymPy, `parse_latex(…, strict=True)` (`Т-§6.5`) | B | — | ждёт поток B |
| д) Meta Model API: `sam-3.1`, `muse-voice-transcribe-1.0` (`Т-§1.3`) | B | — | ждёт поток B |
| е) `READ`-сессия Neo4j отклоняет запись (`Д-§14.2`, `T-A9b`) | C (перенос) | `tests/test_neo4j_read_session.py` | **Подтверждено**: 3 passed на Neo4j 2026.06.0 (полный прогон) и на 2026.10.1 (`QUACK_TEST_NEO4J_URI=bolt://127.0.0.1:57688`) |

## Статус

| Пакет | Поток | Состояние | Тесты |
| :-- | :-: | :-- | :-- |
| WP0 (зона C) | C | частично: ждёт п.2 (владелец) | — (скрипт п.3а) |

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

## Реестр отклонений от основных документов

Как в ТЗ Фазы 4 §2.4 (R4-1…R4-4). Новых строк нет.

## Журнал решений

### Поток A

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

- **Q-C-1 (C → владелец, к сведению B).** `compute/` на `main`: в первом прогоне в контейнере,
  запущенном одновременно с полным `pytest` backend, упали 2 теста из 5
  (`test_equation_roots_and_matrix` и ещё один); три следующих прогона — 5 passed. Похоже на
  таймаут проверки под нагрузкой (`multiprocessing` с таймаутом). WP24 переводит пул на
  `pebble` — проверить там; до этого не трогаю.

## Для фронтенда

Снимок `backend/openapi.json` и таблица «старый эндпоинт → новый» (§6.4) — на каждом гейте.

## DoD

Таблица §10 — заполняется к G4.
