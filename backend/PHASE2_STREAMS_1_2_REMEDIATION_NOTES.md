# Отчёт о ремедиации Фазы 2 (Потоки 1 и 2)

Данный документ фиксирует результаты выполнения работ по спецификации `documentation/phase2_streams_1_2_remediation_spec.md` (РТЗ, задачи D-1..D-7 и S-1..S-5).

## Сделано

- **D-1**: Источник состояния диагностики переведён строго на `core.events` без использования Redis ключей `diagnostic:session:*` (`T-016`, тесты в `backend/tests/domains/test_diag_state_from_events.py`).
- **D-2 (диагностика)**: Идемпотентный ретрай `POST /v1/domains/{id}/diagnostic/start` и `/answer` с детерминированными `session_id` и `turn_id` (`T-017`, тесты в `backend/tests/domains/test_diag_idempotency.py`).
- **D-2 (курация)**: Детерминированный ключ куратора `curator:{domain_id}:{candidates_hash}` и публикация `projector:wakeup` (`T-020`, тесты в `backend/tests/domains/test_curation_keys_and_actor.py`).
- **D-3**: Интеграция банка задач `core.task_bank`, оценка открытых вопросов (`kind="open"`) через вызов `diagnostic@1` (`mode="evaluate"`) и поддержка self-report при сбое провайдера (`T-018`, тесты в `backend/tests/domains/test_diag_open_and_bank.py`).
- **D-4**: Атрибуция `actor="code"` для детерминированных шагов курации 1 и 2, `actor="curator@1"` для решений модели шага 3 (`T-020`, тесты в `backend/tests/domains/test_curation_keys_and_actor.py`).
- **D-5**: Полнота контракта `task.generated` (наличие полей `option`, `answer`, `options`) (`T-016`, тесты в `backend/tests/domains/test_diag_state_from_events.py`).
- **D-6**: Расчёт `effective_mastery(p_m, stab, l_rev, now_ms)` с учётом кривой забывания, вычисление готовности пререквизитов по активным навыкам и фильтрация архивных навыков в графе (`T-022`, тесты в `backend/tests/domains/test_graph_view_fixes.py`).
- **D-7.1**: Короткие маршруты-алиасы `GET /v1/topics/{topic_id}` и `PATCH /v1/topics/{topic_id}/status` (`T-024`, тесты в `backend/tests/domains/test_topic_alias_routes.py`).
- **D-7.2**: Использование сессий Neo4j с `default_access_mode=neo4j.READ_ACCESS` во всех читающих операциях доменов (`T-021`, `T-022`, `T-023`).
- **D-7.3**: Корректная фильтрация топиков и навыков при генерации вопросов в диагностике (`T-019`).
- **D-7.4**: Корректная атрибуция `actor="code"` при кодовом фолбэке структурирования (`T-023`, тесты в `backend/tests/domains/test_structure_actor_and_delete.py`).
- **D-7.5**: Отмена зависших задач (`cancelled`) при вызове `DELETE /v1/learner` (`T-023`, тесты в `backend/tests/domains/test_structure_actor_and_delete.py`).
- **D-7.6**: Чтение графа в кураторе через `read_consistent_slice` и переиспользование клиентов в пуле модуля (`T-021`, тесты в `backend/tests/domains/test_curation_slice_and_pool.py`).
- **S-1**: Идемпотентный повтор `POST /v1/sessions/{id}/turns` с тем же `idempotency_key` (возврат 200 с сохранённым SSE-потоком, детерминированный `turn_id`, отсутствие повторных вызовов LLM и дублирования событий/джоб) (`T-029`, тесты в `backend/tests/session/test_turn_retry.py`).
- **S-2**: Сверка параметров при идемпотентном старте сессии (409 при несовпадении), реальный статус из `core.sessions`, атомарная очистка присутствия через Lua-скрипт и корректный подсчёт `turns_count` только по репликам ученика (`T-026`, тесты в `backend/tests/session/test_lifecycle_fixes.py`).
- **S-3**: Функция `_resolve_topic_and_plan` при старте сессии с `topic_id=None` (выбор темы со статусом `in_progress`/`available`/`rusty`, запись в `core.sessions.topic_id`, сохранение переданного `plan`) (`T-027`, тесты в `backend/tests/session/test_start_topic_resolution.py`).
- **S-4.1, S-4.2, S-4.5**: Проброс `response_time_sec` в `task.submitted`, вычисление `independence` с учётом переданного `hints_used` (штраф 0.25 за недостающую подсказку) и проброс `session_id` в выбор задач (`T-028`, тесты в `backend/tests/session/test_turn_submission_fields.py`).
- **S-4.3, S-4.4**: Гарантированное создание `task.evaluated` для каждого `task.submitted` (оценка открытых задач через поля `teacher_output.evaluated_*` или детерминированный кодовый фолбэк при недоступности LLM/compute) (`T-025`, `T-030`, тесты в `backend/tests/session/test_open_task_evaluation.py`).
- **S-5.1**: Закон 4: проверка цитат `evidence_text` в `run_turn_extract` (отбрасывание невалидных цитат с предупреждением `extractor_ungrounded_quote_dropped`) (`T-032`, тесты в `backend/tests/session/test_extract_quote_grounding.py`).
- **S-5.2, S-5.3**: Чтение контекста через `read_consistent_slice` с `READ_ACCESS` и модуль-уровневый пул драйвера/редиса/шлюза в воркерах сессий (`T-033`, тесты в `backend/tests/session/test_worker_pool_and_slice.py`).
- **S-5.4, S-5.5**: Проверка `auth.users.deleted_at IS NOT NULL` в аутентификации и устойчивость SSE-стрима событий сессии при отсутствии/ошибках Redis (`T-034`, тесты в `backend/tests/session/test_auth_and_stream_resilience.py`).
- **S-5.6**: Атрибуция `actor="code"` для событий `task.assigned`, `turn.teacher_replied` и кодового `task.evaluated` при работе в режиме деградации без LLM (`T-031`, тесты в `backend/tests/session/test_degraded_actor.py`).

## Решения

1. **Шаблоны промптов и манифест**: Шаблоны промптов и ключи в `backend/quack/prompts/manifest.json` не редактировались. Новые поля схем `TeacherOutput` (`evaluated_score`, `evaluated_is_correct`, `evaluated_feedback`) автоматически передаются в JSON-схему ответа через `LLMGateway`, что исключило конфликты с параллельной разработкой Потока 3.
2. **Пространство имён UUID**: Константа `QUACK_UUID_NS` импортируется из канонического места `quack.graph.ids` (в спецификации упоминался несуществующий `quack/events/hash.py`).
3. **Константы порогов**: Поскольку `backend/quack/config.py` зафиксирован, пороги, требуемые РТЗ (штраф за подсказку `0.25`, порог верности self-report `0.6`), определены как именованные константы на уровне соответствующих модулей (`quack.session.tasks`, `quack.domains.diagnostic`).
4. **Обновление ожиданий в тестах**: В тестах `test_session_lifecycle.py`, `test_session_finish_and_sweep.py` и `test_turn_evaluation.py` ассерты приведены в соответствие с новой бизнес-логикой S-3 (разрешение `topic_id` вместо `None`) и S-4 (расчёт независимости 0.75 вместо 1.0 при использовании подсказки).

## Вопросы

1. Маршруты доменов (`quack.domains.router`) не подключены в `backend/quack/api/app.py` в production-сборке (подключен только роутер сессий). Новые короткие алиасы `/v1/topics/{topic_id}` доступны в тестах, но требуют монтирования в `app.py` после слияния Потока 3.
2. Если в будущем потребуется явное текстовое инструктирование модели о выставлении оценок за открытые вопросы, потребуется выпустить версию `teacher@2` с обновлением хэша в `manifest.json`.
