# Фаза 2, Поток 2 (сессии, учитель, экстрактор, деградация): решения и вопросы

Работа выполнена автономно (задачи T-001…T-015), поэтому ниже — всё, что пришлось решить
самостоятельно, расхождения документов и вопросы к команде. Код — `quack/session/`, тесты —
`tests/session/`. Проверка по ТЗ §6 (`ruff check`, `ruff format --check`, `pyright`, `pytest`
с `QUACK_TEST_REQUIRE_INFRA=1`): все 4 команды завершаются с кодом 0, 305 тестов.

## Решения

Не зафиксированы ТЗ — нужно подтвердить или поправить.

1. **Тестовые фикстуры — pytest-плагин, а не `conftest.py`.** Фикстуры (`session_app`, `client`,
   `learner`, `auth_headers`, `fake_llm`, `fake_compute`, `seeded_domain`) лежат в
   `tests/session/fixtures.py`; каждый модуль объявляет `pytest_plugins = ["tests.session.fixtures"]`,
   хелперы (`parse_sse`, `make_token`) — в `tests/session/helpers.py`. `conftest.py` не используется:
   он считается изменением тестовой конфигурации.
2. **`fake_llm` / `fake_compute`** ставятся и в `app.state.llm_gateway` / `compute_client`, и в
   `app.dependency_overrides[get_llm_gateway / get_compute_client]`; очереди ответов `fake_llm`
   ключуются по имени схемы ответа.
3. **`deps.py`.** Секрет JWT — `settings.jwt_secret` или `"quack-dev-jwt-secret"`; лимит ходов 20 в
   минуту (INCR, EXPIRE 60 с на первом обращении), при ошибке Redis или `redis is None` лимит
   отключается (fail-open). `get_llm_gateway` / `get_compute_client` один раз собираются из
   `Settings` и кэшируются в `app.state`.
4. **Блокировка хода.** Redis `SET NX EX 60`, освобождение через Lua compare-and-delete (ключ
   удаляется, только если в нём наш токен); при `redis is None` или сбое соединения — advisory lock
   Postgres, keyspace 2 (`LockKeyspace.SESSION_TURN`).
5. **`evaluate_submission(hints_used, penalties)`** принимает штрафы, а не `HintView`:
   у `HintView` нет `independence_penalty`, а `quack/graph/overlay.py` менять нельзя.
   `independence = max(0.1, 1.0 − Σ penalty)`.
6. **Оценка внутри хода:** открытые задачи, `parse_error` и `ComputeUnavailableError` дают «не
   оценено здесь» (событий `task.submitted`/`task.evaluated` нет, SSE `task_evaluated` не шлётся).
7. **Выбор задачи (`pick_task`):** исключает задачи из `overlay.assigned_task_ids` и уже
   назначенные в сессии; предпочитает `family_id` без верной оценки; при деградации LLM фильтр
   `kind in ('math','choice')`.
8. **Деградация.** `LLMUnavailableError`/`LLMSchemaError` → `llm_unavailable`,
   `LLMBudgetExceededError` → `budget_exceeded`, отставание проектора при живом LLM →
   `projector_lag`. Ход всегда отвечает 200 SSE; в шаблонном ответе — текст задачи.
9. **Чекпоинт.** `session.checkpoint` ставится в той же транзакции хода (склеиваемая задача),
   `up_to_turn_index = teacher_turn_index`; обработчик — no-op, если чекпоинт уже покрывает индекс.
10. **Перезапуск экстрактора.** Если для `turn_id` уже есть `turn.extracted`, в том же вызове
    `append_events` сначала пишется `evidence.superseded(target_event_seq=<старый seq>,
    reason="re-extracted")`; затем в `session_events_channel` публикуется `extractor_done`.
11. **`GET /v1/sessions/{id}/events`.** Владение проверяется до начала ответа (404 для чужой
    сессии); Redis pubsub закрывается в shielded `finally` при отключении клиента; сообщения
    пересылаются как есть.
12. **Завершение и сметание.** `finish` идемпотентен для `completed`; `sweep_inactive` закрывает
    сессии с `reason="inactivity_timeout"` и ключом `sweep:{session_id}`.
13. **Тесты ТЗ в отдельных модулях.** Тесты ТЗ §5 разложены по файлам `tests/session/test_*.py`
    (а не в `test_turns_and_sse.py` / `test_degradation_and_workers.py`), чтобы не менять
    существующие тестовые файлы.

## Конфликты документов

| # | Тема | Документ A | Документ B | Что сделано |
| :- | :--- | :--- | :--- | :--- |
| 1 | Порядок SSE | Спецификация §8.5: `task_assigned` (3) раньше `task_evaluated` (4) | ТЗ Потока 2 §2.3.7: `task_evaluated` (3) раньше `task_assigned` (4) | По ТЗ: `turn_started` → `token` → `task_evaluated` → `task_assigned` → `turn_completed` → `done`. Ход `task_evaluated` + `task_assigned` в одном ходу фронтенд должен принимать в любом порядке, пока документы не согласованы |
| 2 | Регистрация обработчиков | ТЗ: `@register_job_handler("turn.extract" / "session.checkpoint" / "session.sweep_inactive")` в `quack/session/**`; менять `quack/cli.py`, `quack/api/*`, `quack/jobs/*` нельзя | Ничто вне `quack/session/` не импортирует `quack.session.workers` / `lifecycle`; `python -m quack.cli worker` регистрирует обработчики только если модули импортированы | Декораторы в коде есть, тесты работают (импортируют модули). В боевом воркере обработчики появятся только после импорта `quack.session.workers` и `quack.session.lifecycle` интегратором (например, в `quack/cli.py`) |
| 3 | `JobContext` без gateway/redis | ТЗ §2.4: воркерам нужны `LLMGateway` и Redis (публикация `extractor_done`, wakeup) | `JobContext` (`quack/jobs/runner.py`) содержит только `settings` и `session_factory`; менять `quack/jobs/*` нельзя | Каждый обработчик сам создаёт `create_redis(settings)`, `HttpLLMProvider` и `LLMGateway` на каждую задачу и закрывает Redis в `finally`. Тестируемые ядра — `run_turn_extract` / `run_session_checkpoint` с внедряемыми gateway/redis/neo4j. Нужно решение: расширить `JobContext` (доли ресурсов) или оставить так |
| 4 | Регистр статуса сессии | Спецификация: `'Completed'` в `core.sessions.status` | Т-§0.4: snake_case, везде в коде `completed` | Нижний регистр, как в Фазе 1 (см. PHASE1_NOTES, п. 1.6) |
| 5 | `HintView` | ТЗ §2.3.4: `independence = 1.0 − Σ hint.independence_penalty` | `HintView` не содержит `independence_penalty`, `quack/graph/overlay.py` защищён | Штрафы передаются в `evaluate_submission` отдельным аргументом (см. «Решения», п. 5) |

## Решения при слиянии (интеграция)

Приняты по Спецификации §7–§8 при сборке PR:

- Порядок SSE оставлен как в ТЗ потока 2 (`task_evaluated` перед `task_assigned`).
- `quack/api/app.py`: `create_app` подключает `quack.session.router` (импорт внутри функции из-за цикла `session.deps` → `api.app`).
- `quack/cli.py`: воркер импортирует `quack.session.lifecycle` и `quack.session.workers` (регистрация `turn.extract`, `session.checkpoint`, `session.sweep_inactive`) и каждые `session_inactivity_timeout_min / 6` минут (не реже раза в минуту) ставит склеиваемую задачу `session.sweep_inactive`.
- `JobContext` не расширялся: обработчики создают Redis и LLM-gateway сами; перенос в `JobContext` — отдельная задача Фазы 1.
- Роутер `quack.domains` по-прежнему не подключён (зона потока 1).

## Вопросы

1. Какой порядок SSE считать каноническим: `task_assigned` перед `task_evaluated` (Спецификация)
   или наоборот (ТЗ)? Нужна правка одного из документов.
2. Кто и где импортирует `quack.session.workers` / `quack.session.lifecycle` для регистрации
   обработчиков в `python -m quack.cli worker` и где монтируется `quack.session.router` в `create_app`?
3. Расширяем ли `JobContext` полями `redis` / `llm_gateway` / `neo4j`, чтобы воркеры не
   создавали клиентов на каждую задачу?
4. Нужно ли в SSE разбивать `token` на несколько чанков? Сейчас весь `reply_text` уходит одним
   `token` (стриминга от LLM нет: gateway возвращает структурированный ответ целиком).
5. Лимит 20 ходов/мин и fail-open при недоступном Redis — значения из ТЗ; подтвердить, что
   fail-open допустим для продакшена.
6. Расписание `session.sweep_inactive`: кто ставит периодическую задачу в `ops.jobs`? В потоке
   2 есть только обработчик.
7. `pytest_plugins` вместо `conftest.py` для тестов потока: допустимо ли добавить
   `tests/session/conftest.py`, если правило «не менять тестовую конфигурацию» не относится к
   новым файлам?
