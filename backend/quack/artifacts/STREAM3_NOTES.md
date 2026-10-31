# Фаза 2, Поток 3: принятые решения и заметки для интеграции

Поток 3 — это сервис `compute/` и пакет `backend/quack/artifacts/` (практика, заметки,
шпаргалки, дайджесты, поиск, экспорт), а также промпты `task_gen@1`, `evaluator@1` и `artifacts@1`.
Спецификация — «ТЗ Фаза 2 Поток 3» (дальше «ТЗ»). Ниже собраны решения по пробелам и
конфликтам документов, шаги интеграции, которые выходят за зону Потока 3, и известные риски.
Решения 1–11 и допущения A1–A18 утвердил владелец потока. Пункты раздела 1.3 приняты по ходу
Этапа III, их нужно подтвердить или поправить.

Номера разделов техдока в ТЗ сдвинуты. «Т-§6.1–6.3» в ТЗ — это Т-§5.4, §5.7 и §6;
«Т-§14.1–14.5» — Т-§17, §19.7 и §22.2; «Т-§17.1» — Т-§22.7; «Д-§7, безопасность SymPy» — Т-§6.5
и Д-§5.1.

## 1. Конфликты документов и принятые решения

### 1.1. Принятые решения

| # | Вопрос | Где конфликт или пробел | Решение |
| :- | :--- | :--- | :--- |
| 1 | Штраф за подсказку | ТЗ §2.2 п.2–3 (0.2/0.4/0.6, сумма, низ 0.1) ↔ Т-§7.4 | По Т-§7.4: штраф ступени k равен `0.25·k`, `independence = round(1 − max(штрафов), 4)`, без подсказок 1.0. С суммой из ТЗ ученик, взявший две подсказки на задачах T1, никогда не получает `mastered` (раздел 3) |
| 2 | Где пороги | ТЗ §0.1 (`config.py` заморожен) ↔ `backend/CLAUDE.md` (числа — в конфиг) | `quack/artifacts/params.py`; при интеграции перенести в `Settings` |
| 3 | `session_id` / `topic_id` в задачах очереди | ТЗ §2.3 п.2, спец. §7.1: у Потока 2 в payload нет `session_id` | `session_id` берётся из `lock_key` вида `session:{id}:finish`, `topic_id` — из `topic:{id}:cheatsheet`. Payload не расширяем |
| 4 | `evaluator`, когда недоступны и compute, и LLM | ТЗ §2.2 п.3.4, Т-§22.7: значение не задано | Нормализованное сравнение строк → `"hybrid"`; для `choice` всегда `"compute"` |
| 5 | Экспорт | ТЗ §2.3 п.4 ↔ Т-§19.7 (Obsidian, Anki, PDF) | По ТЗ: `GET /v1/domains/{id}/export?format=md\|json`, один документ Markdown или JSON |
| 6 | Порядок проверки эквивалентности | ТЗ §2.1 п.3 (`simplify`, затем точки) ↔ Т-§6.6 | По Т-§6.6: сначала численная проверка в 20 точках, затем `simplify` |
| 7 | Безопасность парсера | ТЗ §2.1 п.1 ↔ Т-§6.5, Д-§5.1 | Оба слоя: грамматика токенов (Т-§6.5), запрещённые подстроки из ТЗ и `"__builtins__": {}` в `global_dict` |
| 8 | `tested_depth` без LLM | ТЗ §2.2 п.3: значение не задано | 0.5 |
| 9 | Подсказка, если в `reference_solution` нет `hints` | ТЗ §2.2 п.2: «или генерирует» | Шаблонный текст, без вызова LLM |
| 10 | Форматы ответа `equation_roots` | ТЗ §2.1 п.3: не заданы | `a = b` → уравнение; `2, -2` → множество корней; просто выражение → `expr = 0` |
| 11 | Текст ученика в промптах | В документах нет (решение владельца) | `evaluator@1`: ответ ученика — в `<learner_answer>`; `artifacts@1`: реплики и прежний текст — в `<dialogue_excerpt>` и `<previous_content>`. Текст внутри тегов — только данные, не инструкции |

### 1.2. Дополнительные допущения

| # | Вопрос | Решение |
| :- | :--- | :--- |
| A1 | Какой режим compute выбирает бэкенд для `math` | `mode = reference_solution.get("check_mode", "expression_equiv")`, эталон — `reference_solution["sympy"]`, `variables = reference_solution.get("variables", ["x"])`. Режим `derivative` бэкенд сам не выбирает: в нём `reference` — исходная функция (ТЗ §2.1 п.3) |
| A2 | Допущения о переменных в compute | `Symbol(name, real=True)` (Т-§6.6) |
| A3 | Область корней `equation_roots` | `solveset(..., domain=S.Reals)`; если множество бесконечное (`ImageSet`, `ConditionSet`, `Interval`) → `"unsupported"` |
| A4 | Численная проверка | Seed — из sha256 входа. Точка засчитывается, только если обе стороны — конечные вещественные числа. Меньше 20 таких точек → `"unsupported"` |
| A5 | Числа compute | Именованные константы в начале `compute/quack_compute/checker.py` |
| A6 | `target_difficulty` для `task_gen@1` | `PARAMS.default_target_difficulty = 0.5` |
| A7 | Порядок выбора навыка | Навыки темы с `masteryStatus != 'mastered'`, сортировка по `(distinctFamilies != 1, pMastery, id)` |
| A8 | «Новое семейство» и «нерешённая задача» | Семейство новое, если его `family_id` нет среди задач этого навыка с `task.evaluated` у ученика. Задача нерешённая, если у неё нет `task.evaluated` и её не назначали в этой сессии |
| A9 | `actor` событий | `task.generated` → `task_gen@1`; `task.assigned`, `task.hint_given` → `system`; `task.submitted` и заметки → `learner`; `task.evaluated` → `code` или `evaluator@1`; дайджест и шпаргалка → `artifacts@1` или `code` (шаблон) |
| A10 | Повтор запроса с тем же ключом | До любых вычислений ищем событие с ключом `f"{idempotency_key}:{event_type}"`; если нашлось — отвечаем из него. Ключи воркеров: `f"{job.kind}:{job.id}"`, у дайджеста — `f"session.digest_created:{session_id}"` |
| A11 | Детерминированные id | `task_id = uuid5(NS, f"task:{learner}:{session}:{key}")`, id заметки — `uuid5(NS, f"note:{learner}:{key}")` |
| A12 | Проверки в эндпоинтах практики | Сессии ученика нет → 404. Для `hint` и `submit` задача должна быть назначена в этой сессии, иначе 404 |
| A13 | Поиск | `limit` от 1 до 100. Сортировка по `ts_rank`, затем по свежести. Домен события — `coalesce(e.domain_id, s.domain_id, a.domain_id)` |
| A14 | `ArtifactView` | `artifact_id, kind, domain_id, topic_id, session_id, title, content_md, version, created_at, updated_at` |
| A15 | Дайджест | Реплики — последние `settings.active_window_turns`. `covered_skill_ids` — из `task.evaluated.skill_scores` и `teacher.concept_explained.skills` этой сессии |
| A16 | Чтение Neo4j | Только `driver.session(default_access_mode=READ_ACCESS)` (закон 3, Д-§7) |
| A17 | Задача из банка при отказе LLM | Сначала новое семейство для навыка, затем нерешённые, затем решённые; внутри группы — по `created_seq` |
| A18 | `degraded` при повторе `next_task` | Есть `task.generated` → `False`; иначе `True`, если в `core.llm_outbox` есть незавершённая строка `task_gen:{sid}:{key}`. При `LLMBudgetExceededError` такой строки нет, и повтор вернёт `False` |

### 1.3. Решения, принятые на Этапе III (нужно подтвердить)

| # | Вопрос | Решение |
| :- | :--- | :--- |
| B1 | Какие заблуждения считать активными («Typical traps» в шаблоне шпаргалки) | `suspected`, `confirmed`, `watching` (Т-§9.2). Не попадают: `latent` (не проявлялось), `dismissed`, `fixed`, `resolved`. В `artifacts@1` передаются все заблуждения со статусами |
| B2 | Входные данные `artifacts@1` для дайджеста | `title_context = "Session digest"`; `skills_summary_json = {covered_skill_ids, evaluated_tasks: [{task_id, is_correct, score}]}`; `misconceptions_summary_json` — `triggered_misconceptions` из `task.evaluated`; реплики — `[{role, text}]` |
| B3 | Тема ещё не спроецирована в Neo4j | Шпаргалка всё равно пишется: заголовок «Topic cheatsheet», без навыков. Воркер читает Neo4j напрямую, без `read_consistent_slice` (тесты проектор не запускают) |
| B4 | Что попадает в экспорт | Темы без статусов `archived`, `merged`, `skipped` и навыки без `archived`, `merged` (константы Фазы 1 из `topic_display.py`). Темы и навыки сортируются по имени, артефакты — по `created_at`. Шпаргалка, дайджесты и заметки идут в Markdown как есть, с заголовком `### <title>` |
| B5 | Список типов в FTS-запросе по событиям | Литералы `sorted(TEXT_EVENT_TYPES)`. Порядок не совпадает с миграцией, но планировщик всё равно доказывает предикат индекса: `EXPLAIN` показывает `idx_events_fts_en` и с фильтром домена, и без него |
| B6 | Гонка двух `PATCH` / `DELETE` одной заметки | Строка заметки блокируется `SELECT … FOR UPDATE` в той же транзакции, что и `append_events`. Иначе два параллельных `PATCH` записали бы одну и ту же `version` |

## 2. Что нужно при интеграции (вне зоны Потока 3)

| Где | Что сделать | Зачем |
| :--- | :--- | :--- |
| `quack/api/app.py` | `from quack.artifacts.router import router as artifacts_router` и `app.include_router(artifacts_router)` в `create_app` | Без этого эндпоинты ТЗ §3 не смонтированы (тесты монтируют роутер локально, ТЗ §0.1) |
| `quack/cli.py`, команда воркера | `import quack.artifacts.cheatsheets  # noqa: F401` до создания `JobRunner` | Импорт регистрирует обработчики `session.finish_artifacts` и `artifact.refresh_cheatsheet`. Иначе раннер их не забирает, и задачи остаются в `queued` |
| `docker-compose` (в репозитории его пока нет) | Сервис `compute`: образ `quack-compute`, `uvicorn quack_compute.app:app --host 0.0.0.0 --port 8001`, только внутренняя сеть без выхода в интернет, непривилегированный пользователь, без секретов и `.env` (Д-§5.1, Т-§6.5, Д-§7). У `api` — `COMPUTE_URL=http://compute:8001` | Бэкенд по умолчанию ходит на `http://localhost:8001` (`Settings.compute_url`) |
| `quack/config.py` | Перенести поля `PracticeParams` из `quack/artifacts/params.py` в `Settings` | Решение 2 |
| Поток 2 | Ставить задачу так: `kinds.session_finish_artifacts(learner, sid, {"domain_id": …, "topic_id": …})`. `topic_id` может быть `None`, тогда шпаргалка не обновляется. SSE сессии должен слушать `session:events:{sid}` и передавать дальше `cheatsheet_ready` | Формат payload, который ждёт `run_finish_artifacts` |
| Прод | Задать `JWT_SECRET` длиной не меньше 32 байт | Dev-секрет из контракта JWT короче (20 байт): PyJWT предупреждает `InsecureKeyLengthWarning`, RFC 7518 §3.2 |

## 3. Известные риски

| # | Риск | Последствие | Кто чинит |
| :- | :--- | :--- | :--- |
| 1 | `reference_solution: dict[str, Any]` в `TaskGenOutput` и `strict: true` в `HttpLLMProvider` могут не ужиться у провайдера (Т-§11.8 «Проверить») | `task_gen@1` падает на схеме, и практика уходит в банк задач | Проверить на реальном провайдере |
| 2 | В свёртке Фазы 1 (`quack/graph/folds.py`) нет правила пары для T2 (Т-§7.3) | T2 проходит ворота в одиночку | Владелец `folds.py` |
| 3 | Свидетельства с весом `w ≤ 0.32` не дают `mastered` при любом их числе: из-за затухания `S < 2.5·w`, а порог 0.8 (контракт Т-§8.2). Стену сдвигает множитель глубины `(0.6 + 0.4·tested_depth)` из `folds.py:156–160`, которого нет в Т-§7.4 | Решение 1 снимает стену для T1; для T2 с двумя подсказками она остаётся | Владелец `folds.py` |
| 4 | Без правила пары одно отрицательное суждение модели обнуляет `pMastery` или ставит `struggling` | Нарушение закона 4 | Владелец `folds.py` (подробно — отдельный документ `team-issues-hints-and-fold.md`) |
| 5 | `JobContext` даёт только `settings` и `session_factory`. Драйвер Neo4j, Redis и `LLMGateway` воркеры создают на каждую задачу (`build_worker_deps`) | Лишние соединения на задачу | При интеграции — общие пулы в `JobContext` |
| 6 | **Удалённая заметка находится в поиске.** Второй запрос (ТЗ §2.3 п.3) ищет по `core.events`, а журнал только дополняется: `artifact.note_created` с полным текстом остаётся, отметку `is_deleted` запрос не проверяет | В `event_hits` попадают текст удалённой заметки, старые версии изменённых заметок и прежние версии шпаргалок. Тест этого не видит: удалённую заметку он проверяет только в экспорте | Решить: исключать события удалённых артефактов (через `core.artifacts`) или оставить как журнал |
| 7 | Склейка `artifact.refresh_cheatsheet` по `coalesce_key`: `enqueue_job` заменяет payload | Запрос `POST …/cheatsheet/refresh` без `session_id`, склеенный с задачей из `finish_artifacts`, теряет `session_id`, и `cheatsheet_ready` не уходит | Семантика очереди Фазы 1 |
| 8 | Повтор `finish_artifacts` после того, как шпаргалка уже обновилась, ставит ещё одно обновление (промпт: «постановку выполнить всё равно») | Лишняя версия шпаргалки и лишний вызов LLM | Допустимо |
| 9 | `refresh` и заметки не проверяют, что `topic_id`, `domain_id` и `session_id` принадлежат ученику (в ТЗ таких проверок нет) | Для чужого или несуществующего `topic_id` появится шпаргалка «Topic cheatsheet» без навыков. Чужие данные при этом не читаются: все запросы фильтруют по `learner_id` | Решить, нужна ли проверка |
| 10 | Воркер шпаргалки и экспорт читают Neo4j без ожидания проектора | Если проектор отстаёт, в шпаргалке или экспорте может не хватать последних свидетельств | Допустимо для фона; для экспорта можно перейти на `read_consistent_slice` |
| 11 | В дайджесте навыки указаны только id (промпт читает только `core.events`, имена навыков — в Neo4j) | Шаблонный дайджест показывает UUID; LLM получает id без имён | Решить, читать ли имена навыков из Neo4j |
| 12 | Т-§17.2–17.6 (блоки, `owner = learner`, проверка «одна страница» через PDF) в срезе нет: артефакт — один `content_md` | Правки ученика не защищены от перегенерации, но шпаргалку пишет только система, а заметки — только ученик | Вне хакатонного среза |

## 4. Команды

```bash
# compute: запуск и проверки (ТЗ §6 п.1)
cd compute
uv sync
uv run uvicorn quack_compute.app:app --host 127.0.0.1 --port 8001   # GET /health/live, POST /v1/check/math
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest

# backend: тестовые контейнеры — по backend/README.md (Postgres :55432, Neo4j :57687, Redis :56379)
cd backend
uv run ruff check && uv run ruff format --check && uv run pyright && QUACK_TEST_REQUIRE_INFRA=1 uv run pytest
QUACK_TEST_REQUIRE_INFRA=1 uv run pytest tests/artifacts   # только тесты Потока 3 (9)
```

Если `pyright` не может скачать node (без системного node `nodeenv` падает на alpha-версии
в `index.json`), задайте версию явно: `PYRIGHT_PYTHON_NODE_VERSION=26.13.0 uv run pyright`.
