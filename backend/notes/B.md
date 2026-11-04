# Журнал решений потока B — Фаза 4 (с 2026-11-03)

Новые решения потока B пишутся **сюда**, сверху, в момент решения (ТЗ Ф4 §0.4.5). Общий
`backend/PHASE4_NOTES.md` — архив до 2026-11-03: новые записи туда не добавляются, это убирает
конфликты слияния. Сводка всех решений — `documentation/Фаза 4 — решения (единое).md`: добавь туда
строку о решении в той же волне.

Формат записи (как в архиве):

```
### D-B-NNN. <суть>
- Класс: A | B | C (§0.4.5)
- WP / место: …; файлы
- Контекст: чего нет в документах
- Решение: …
- Почему: Т-§ / Д-§ / ТЗ
- Обратимость: …
- Тесты: …
- Требует подтверждения владельцем: да / нет
```

Номера продолжают архив: следующий свободный номер — по `grep -h "^### D-B-" backend/PHASE4_NOTES.md backend/notes/B.md`.

<!-- записи ниже, новые сверху -->

### D-B-217. Шов с C: `second_opinion.diagnosis` и задачи семинара (#271)
- Класс: B
- WP / место: WP21.4, WP11.6, WP12.5, WP20.2, `Т-§7.7`, `Т-§5.6`
- Контекст: правило диспетчера A порождает `second_opinion.diagnosis`, обработчика нет; холст (C) делает второе мнение сам. `material.parse` не создаёт `task.created` для задач семинара, ждёт формат от B
- Решение: (а) Обработчик `second_opinion.diagnosis` — зона C (`quack/canvas`): событие `observation.second_opinion` принадлежит C (таблица событий §6), контекст — доска и строка. Второе мнение B (WP11.6) — внутри проверки ответа, готово; роль `second_opinion@1` у B вызывается через шлюз. Правило A остаётся, C регистрирует обработчик с ключом `observation.second_opinion:{target_seq}` (процедура (б) #173). (б) Поля слоя 2 `material_parser` для задач семинара: `seminar_tasks [{statement, answer | null, chunk, page}]`; разметку и запись `task.created {source: material}` делает код B: с ответом из материала — `verification = disputed`, без ответа — эталон решает Wonder и дальше как у сгенерированной (`Т-§5.6`). Функция B — следующая волна, имя и сигнатуру напишу в #271. (в) Правки C в `scripts/meta_smoke.py`, `tests/llm/prompt_contracts.py` и поле `deadlines` у `material_parser@1` подтверждены
- Почему: §0.4.3 п.2, зоны потоков
- Обратимость: решения о принадлежности; формат полей можно изменить до первой реализации
- Тесты: нет (шов)
- Требует подтверждения владельцем: нет

### D-B-216. Слот 6 в режиме подготовки (WP17.4, `T-RV4`)
- Класс: B
- WP / место: WP17.4, `Т-§14.7`, `Т-§10.1`; `quack/session/prep_mode.py` (`prep_enabled`, `prep_for_turn`, `render_prep`), `quack/session/turn_source.py` (`_prep`, `_session_plan`), `quack/config.py` блок B (`prep_slot_max_items`)
- Контекст: ТЗ — слот 6 получает хаб, дни до дедлайна, готовность, охват и найденный контекст контроля; данные даёт `GraphReader.prep_context` (A, D-A-152), слот рисует B. Режим включается только явным событием, простой заход в хаб его не включает
- Решение: (а) Режим сессии включён, если у хаба цели сессии (`goal.hub_id`) последнее из `prep_mode.enabled`/`prep_mode.disabled` — `enabled` (свой SQL по журналу ученика, импорта `review` нет, §4.1). Цель только с темой без хаба режим не включает. (б) Тогда слот 6 — цель и план, ниже строки: хаб (метка, название), дни до дедлайна, готово «k из n» тем, охват (метки тем), найденные материалы контроля (вид и страница, без меток кусков: текст куска в слот 8 не попадает), навыки для повтора в первую очередь (метки). Объекты — метки, не id (`Т-§4.5`); в каждом списке не больше `prep_slot_max_items` (12). (в) Навыки кандидатов добавлены в фокус среза и в таблицу меток. (г) Neo4j недоступен — слот без блока подготовки. (д) Пробник: элемент `timed_quiz` уже в схеме (Т-§12.3), задачи — `agentRequests task_generation` по навыкам охвата; учителю это объяснено разделом 7 «Preparation mode» в `teacher/v2.md` (черновик стенда правится на месте, D-B-132; `manifest.json` пересобран)
- Почему: §0.4.3 п.2 (WP17.4), `T-RV4`
- Обратимость: один модуль и вызов
- Тесты: `tests/session/test_prep_mode.py` (1): без события — нет; `enabled` — есть; `disabled` — нет; рендер слота. `tests/session` — зелёные
- Требует подтверждения владельцем: нет
- Не решено: `control_materials` у A пусто, пока в графе нет вида материала контроля (D-A-152)

### D-B-215. Наложение сессии в `turn_source` (WP2.3, #260); слой 1 `artifact_writer@1` — принят
- Класс: B
- WP / место: WP2.3, WP3.4, `Т-§2.6`, закон 9; `quack/session/turn_source.py::MemoryTurnContextSource._overlay`; построитель — `quack.graph.overlay_build.build_session_overlay` (A, D-A-239)
- Контекст: `_slice` строил пустое наложение, так что при отстающем проекторе защитные правила не видели свежих событий сессии. A выложила построитель и просила подключить (#260)
- Решение: (а) `_overlay` вызывает `build_session_overlay(db, neo4j, settings, learner_id, session_id)` внутри того же `try` с `NEO4J_UNAVAILABLE`. (б) Нет драйвера — пустое наложение. (в) `EventFailed` — предупреждение в лог `session_overlay_failed` и `empty_overlay(0)`: учитель отвечает (закон 9). (г) Ожидание проектора остаётся до конца журнала: `overlay_seq = max(overlay_seq, end)`, иначе читатель не дождался бы
- Почему: §0.4.3 п.2, `Т-§2.6`, закон 9
- Обратимость: один метод
- Тесты: `tests/session/test_turn_source.py::test_session_overlay_wiring` (1): отставание, `EventFailed`, нет драйвера; `tests/session` и `tests/test_contracts.py` — 227 passed
- Требует подтверждения владельцем: нет
- Слой 1 `artifact_writer@1` (блок «Accuracy», #206/#221, D-C-132): владелец поручил свериться по документам; формулировки совпадают с `Т-§17.4` (формула не прошла — перегенерация, снова не прошла — блок «не проверен»; определение и утверждение называют кусок). Принято без правок как черновик; версия остаётся `v1` до G4

### D-B-178. Отдача страницы визуализации: `GET /v1/viz/{id}` с CSP (WP16.5, #201)
- Класс: B
- WP / место: WP16.5, `Т-§13.6`, `T-AG6`, §7.3 п.3; тело `quack/practice/viz_page.py` (`load_viz_page`, `content_security_policy`), объявление `quack/artifacts/viz_routes.py` (в `quack/api/app.py` одна строка `include_router`), строка в `tests/e2e/routes/b.py`
- Контекст: `Т-§13.6` — скрипты только с нашего статического адреса, а страница `viz_builder` держит логику во встроенных скриптах (D-B-177). Владелец выбрал вариант 1 в #201 (2026-11-04)
- Решение: (а) `{id}` — `request_id` запроса `agent.viz`; страница — объект, на который указывает `agent.completed {kind: viz, ok: true}` этого ученика. Нет события, сборка провалена, объекта нет или он чужой — один 404 (закон 8); ключ вне префикса ученика — тоже 404. (б) CSP: `default-src 'none'`; `script-src` и `style-src` — `viz_static_base_url` и `'unsafe-inline'`; `connect-src 'none'`; `form-action 'none'`, `base-uri 'none'`; шрифты и картинки — статика и `data:`; директива `sandbox allow-scripts` в самом заголовке (без `allow-same-origin`), так что страница без iframe тоже не получает прав источника. (в) Клиент показывает страницу в `<iframe sandbox="allow-scripts">`. (г) Ответ `text/html`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=3600` (ключ объекта неизменяем, Т-§3.3). (д) Тело в `practice`, потому что `artifacts` не может импортировать `agents` (§4.1)
- Почему: решение владельца, §0.4.3 п.2, `Т-§13.6`
- Обратимость: одна функция заголовка; вариант 3 (данные + наш рендерер) — отдельное решение
- Тесты: `tests/session/test_viz_page.py` (1): страница и CSP, 404 на провал, неизвестный и чужой id, 401 без входа; `T-E*` изоляции ученика и снимок `openapi.json` зелёные
- Требует подтверждения владельцем: нет (подтверждено в #201)
- Не решено: файлы и версии `deploy/static/viz/` и `viz_libraries` — C (WP9.2); `viz_static_base_url` пуст — тогда `script-src` только `'unsafe-inline'`

### D-B-214. Живой прогон Meta API (WP10.7, DoD п.6a) 2026-11-04; конец стрима `data: [DONE]`
- Класс: B
- WP / место: WP10.7, DoD п.6a; `quack/llm/responses_client.py::_data_event`, `scripts/meta_smoke.py` (`--only`)
- Контекст: первый живой прогон Фазы 4 по ключу владельца, `META_API_BASE_URL=https://api.meta.ai/v1`, чистая схема dbmate, `ENV=dev` (цен нет, стоимость 0)
- Результат: 26 строк таблицы ролей и 6 промптов Ф3 (`evaluator`, `task_gen`, `structurer`, `curator`, `diagnostic`, `artifacts`) и стрим с инструментом (`stream_tool`: 2 шага, 1 вызов инструмента) — все `ok`. `skipped:no_prompt`: `material_parser` (слой 2 — C, WP20.2) и `viz_checker` (в срезе не вызывается, `Т-§22.10`). Обрезанных ответов (`incomplete`) в итоговых строках нет; кэш входа у учителя 4582 из 4685 токенов. Строки учёта: 63 `ok` в `ops.llm_usage`
- Найдено и исправлено: (1) живой сервер завершает стрим строкой `data: [DONE]`, клиент разбирал её как JSON и падал (`orjson.JSONDecodeError`), мок Meta (`loadtest/meta_mock.py`, C) её не шлёт — поэтому тесты ошибки не видели. `_data_event` пропускает `[DONE]` и пустые `data:`; тест `tests/llm/test_responses_sse.py`. (2) Сбой стрим-пробы не должен терять отчёт: проба ловит любое исключение строкой отчёта. (3) `--only строка…` — повторная проверка отдельных строк
- Временные сбои провайдера: в первом прогоне `structurer` дал таймаут (95 с), `curator` и `teacher` на Wonder — по три ответа «unavailable» подряд; размыкатель цепи Spark (30 с) уронил соседние строки. Повтор тех же строк (`--only`) — все `ok` (`structurer` 31 с). Промпты не менялись: поломок схем и ответов нет. Мок-пробы с таймаутом и размыкателем остаются на тестах
- Почему: DoD п.6a, `Т-§11.8` п.1
- Обратимость: функция разбора строки; режим `--only` — необязательный флаг
- Тесты: `tests/llm/test_responses_sse.py`
- Требует подтверждения владельцем: нет. Ключ в репозиторий и в логи не попал; прогон ставит строки в `ops.llm_usage` отдельной базы стенда

### D-B-213. Разделить тему фразой в чат: намерение `split_topic`, запрос `topic_split`, задача `agent.topic_split` (WP8.2)
- Класс: B
- WP / место: WP8.2 (ТЗ Ф4 §6.4, строка «Разделить»), `Т-§4.11`, `Т-§2.8`, issue #245; `quack/session/teacher_output.py`, `guards.py`, `turn_commit.py`, `quack/agents/topic_split.py`, `schemas.py`, `quack/prompts/{teacher/v2,builder/v1}.md`
- Контекст: типы `topic.split` / `skill.split` и проектор есть у A, производителя из хода нет. ТЗ: «учитель размечает намерение; сверка предлагает распределение → `topic.split` (+ `skill.split`, если навык один)»; `Т-§4.11`: «если навык у темы один, сначала `skill.split` с переизвлечением эпизодов». Не названо: поле метки, роль и режим вызова, как делить один навык, что с идентичностью темы.
- Решение: (1) Учитель: значение `TurnIntent` `split_topic` и поле головы `topicLabel` (метка `T…`, nullable, по strict-схеме обязательно; не null только при этом намерении). Промпты `teacher@2` и `builder@1` правятся на месте, версии не растут: оба — версии Фазы 4 до выпуска (прецедент `cf83ee8`), хеши в манифесте пересобраны. Защита `no_target_skills` для `split_topic` не применяется: предмет хода — тема. (2) `turn_commit`: метка резолвится в `T…` таблицы хода (таблица строится из среза этого ученика — чужой темы в ней нет); неизвестная или пустая метка — ничего не пишется, ход не падает. Иначе после `turn.committed` — `agent.requested {kind: topic_split, params.topic_id}`, `needed_in_session=false`, `request_id = uuid5(ключ хода, тема)`, ключ `{ход}:agent:topic_split:{topic_id}`; запрос входит и в `markup.agent_requests`. (3) `agent.topic_split` (`builder`, режим `Split`, схема `SplitOutput{parts:[{name, skills:[S…]}]}`): навыки темы читает `topic_memory` читателя, привязанного к ученику; код требует ≥2 частей с разными непустыми именами, каждый навык ровно в одной непустой части, меток вне таблицы нет (один повтор через `check` шлюза). Не прошло — граф не пишется, ответ `agent.completed {ok: false, error: split_not_valid}`. (4) Тема из нескольких навыков → `topic.split`: часть с наибольшим числом навыков (при равенстве первая) сохраняет id темы (хабы и планы, ссылающиеся на тему, остаются верными), остальные — новые темы, `uuid5` от нормализованного имени в домене (`reconcile.node_id`, `Т-§4.4`); навыки без имени идут в сохраняющую часть. (5) Тема из одного навыка → сначала `topic.split`, потом `skill.split` (порядок от A, D-A-226, #245: проектор пропускает часть навыка, чьей темы ещё нет, а `topic.split` не покроет навык, которого нет): первая часть сохраняет id темы и старый навык (`skill_ids: [старый]`), остальные — новые пустые темы (`skill_ids: []`); затем `skill.split` старого навыка на по навыку на часть, каждый в теме своей части; `skills` в ответе модели пуст. Старый навык остаётся со свидетельствами, переизвлечение эпизодов ставит диспетчер A. (6) Ключи событий — `topic_split:{request_id}:{тип}`: повтор, даже с иными словами модели, не пишет второй раз; если `agent.completed` уже есть, вызова модели нет. Замок `lock_key = domain:{id}` и `required_seq` ставит правило A.
- Почему: ТЗ Ф4 WP8.2; `Т-§4.11`, `Т-§4.4`, `Т-§4.5`; законы 1 (читатель по ученику), 5 (идемпотентность), 7 (отказ без записи), 9 (ход не ждёт).
- Обратимость: расширение контрактов (одно значение и одно поле ответа учителя, один режим и одна схема `builder`); откат — убрать производителя, события остаются валидными.
- Тесты: `tests/agents/test_topic_split.py` (несколько навыков, один навык, непрошедшее распределение; повтор), `tests/session/test_turn_commit.py::test_split_topic_requests_the_split_of_a_known_topic_only`; фейки ответа учителя дополнены `topicLabel`.
- Требует подтверждения владельцем: нет. От A нужны: `split_topic` в `TurnIntent` и `topic_split` в `AgentKind` (`events/records.py`), строка `agent.topic_split` в `JOB_KIND_SPECS` и `topic_split` в `AGENT_JOB_KINDS` (`jobs/rules_p4.py`), `graph=True` и `lock_key=domain:{domain_id}` в `_agent_requested`.

### D-B-212. LLMGateway только на Db: outbox, ledger и токенные бюджеты Фазы 3 сняты (WP1.3 шаг 4)
- Класс: B
- WP / место: WP1.3, §6.1 «Удаляются» (B1), issue #215, D-B-197; `quack/llm/gateway.py`; вызовы в `quack/runtime/__init__.py` (A), `quack/api/resources.py` (C), `quack/cli.py` (`_extraction_eval`), `scripts/meta_smoke.py`
- Контекст: шлюз держал два пути — Фаза 3 (`session_factory`: `core.llm_outbox`, `ops.llm_ledger`, бюджеты в токенах, `core.sessions.tokens_used`) и Фаза 4 (`db=`: `ops.llm_usage`, доллары, пул D-B-211)
- Решение: (1) `LLMGateway(provider, settings, registry=None, *, db, redis=None, tracer=None)`: `session_factory` нет, `db` обязателен. (2) Удалены `_completed_outbox`, `_mark_pending`, `_finish_outbox`, `_check_budgets`, `_write_ledger` и хеш запроса; `_ledger`/`_error_ledger` переименованы в `_record`/`_record_error` и пишут только строку `ops.llm_usage` (+ трассу). Бюджеты — только `check_usd_budgets`. (3) Идемпотентности вызова модели нет: повтор задачи может заплатить ещё раз (решение B в #215), факты удерживают детерминированные ключи событий (закон 5). `idempotency_key` остаётся в сигнатуре `generate_structured` для вызывающих; `LLMResult.from_cache` остаётся и всегда `False`. (4) Токены сессии (`core.sessions.tokens_used`) больше не растут: лимит сессии — в долларах по `ops.llm_usage`. (5) Код ошибки попытки (`rate_limited`, `unavailable`, `bad_request`, `timeout`) в `ops.llm_usage` не пишется (колонки нет): остаётся `status` (`ok`/`error`/`incomplete`/`schema_error`), код — в логе и трассе. (6) Вызывающие: `create_job_context` и `create_app_resources` передают `db=db` всегда (условие `phase4_processes` убрано); `cli._extraction_eval` больше не открывает SQLAlchemy-движок; `meta_smoke.run_smoke(settings, db, provider)`
- Почему: ТЗ Ф4 §6.1, §2.3 строка 9, закон 5; пока шлюз зависел от `core.llm_outbox`/`ops.llm_ledger`, их нельзя было убрать из схемы
- Обратимость: восстановить файл из истории; таблицы `core.llm_outbox`, `ops.llm_ledger` в схеме остались (их снимает миграция A)
- Побочные правки в зоне C (тесты): `tests/canvas/test_recognition_wp21.py` (повтор — два обращения к модели, событие одно); `tests/canvas/test_step_check_wp21.py` (повтор `second_opinion` без другого ответа модели: с другим ответом ключ даёт `IdempotencyConflictError`); `tests/artifacts/test_cheatsheet_wp23_5.py::test_a_repeated_job_writes_no_new_version` — строгий `xfail`: повтор пишет версию 2, дедупликация по ключу запроса до вызова модели — у C (issue #244)
- Тесты: `tests/test_jobs_and_llm_gateway.py::test_gateway_call_writes_a_usage_row_and_a_repeat_pays_again`, `tests/test_llm_runtime.py` (повторы, 429, breaker, семафор, усечение, строка usage), `tests/llm/test_usage_usd.py` (доллары)
- Требует подтверждения владельцем: нет
- Передать C: `quack/ops/status.py` (строка 63) читает `ops.llm_ledger`, `quack/ops/commands.py` (строки 35–36) чистит `core.llm_outbox`/`ops.llm_ledger`: после шага 4 новых строк в этих таблицах нет — перевести на `ops.llm_usage`; `quack/artifacts/STREAM3_NOTES.md` (A18) ссылается на `core.llm_outbox`
- Правки тестов (§8.15 п.1, тест Фазы 3 удалён вместе с кодом; свойство теперь проверяет тест Фазы 4):
  - `tests/test_jobs_and_llm_gateway.py`: `test_gateway_replay_is_served_from_outbox`, `test_gateway_cached_result_is_returned_even_over_budget` (повтор по ключу — outbox) → `test_gateway_call_writes_a_usage_row_and_a_repeat_pays_again` (повтор платит ещё раз); `test_gateway_daily_budget`, `test_gateway_session_budget` (токенные бюджеты) → `tests/llm/test_usage_usd.py::test_budgets_in_dollars`; `test_gateway_call_writes_ledger_outbox_and_session_tokens` переписан на строку `ops.llm_usage`; `provider_unavailable`, `repairs_schema_once`, `semaphore` переведены на `pg_db`;
  - `tests/test_llm_runtime.py`: `test_session_budget_exempt_roles` (T-L7, токены сессии) → `test_budgets_in_dollars`; в T-L2, T-L3, T-L6, T-L10, T-L11 проверки outbox/ledger заменены проверкой строк `ops.llm_usage` (`retried`, `status`, `effort`, токены); вместо `prompt_hash` в T-L6 проверяется `prompt_version`;
  - `tests/llm/test_gateway_stream.py`, `test_phase4_media_roles.py`, `test_tracing.py`, `test_usage_usd.py`, `test_meta_smoke.py`, `tests/agents/test_judge.py`, `tests/session/test_replay.py`, `test_turn_source.py`, `tests/jobs/test_curation_p3_graph.py`, `tests/artifacts/conftest.py`, `tests/support/resources.py` — шлюз на `pg_db`; `tests/canvas/test_recognition_wp21.py::test_line_is_rendered_read_and_committed` (зона C, одна строка): повтор `recognize_line` теперь вызывает модель второй раз, событие остаётся одно по ключу; строки читаются из `ops.llm_usage` вместо `ops.llm_ledger` (без `turn_id` и `error_code`, которых в таблице нет)

### D-B-211. Пул денег на модели: щедрые лимиты, ужатие у $100, у $50 — только судьи Meta (WP10.4)
- Класс: C — решение владельца 2026-11-04 (в чате потока B)
- WP / место: WP10.4; `quack/llm/access.py`, `quack/llm/usage.py::check_usd_budgets`, `quack/config.py` (блок B), `quack/session/router.py` (`GET /v1/llm/access`, проверка в `post_action`)
- Контекст: пользователей немного (друзья и знакомые), ограничивать их не нужно. Нужен запас денег, чтобы судьи Meta могли проверить прод. В ТЗ есть только бюджеты на ученика (Т-§11.4: $0,5 за сессию, $3 в день)
- Решение:
  - **Пул.** `LLM_POOL_BUDGET_USD` (0 — пула нет, режим всегда `normal`). Остаток = пул − сумма `cost_usd` всех строк `ops.llm_usage`; кэш процесса `llm_pool_cache_s` = 30 с.
  - **Режимы.** `normal`: $5 за сессию, $20 в день. `tight` (остаток ≤ `llm_pool_tight_remaining_usd` = 100): $0,5 и $3 (`tight_*`, значения Т-§11.4). `judges_only` (остаток ≤ `llm_pool_judges_only_remaining_usd` = 50): ученик без отметки судьи получает `LLMBudgetExceededError("pool")` на любую роль, включая учителя. Задачи откладываются до следующих суток, как при `daily`.
  - **Отметка судьи.** Существующий `PATCH /v1/learner/preferences {key: "meta_judge", value: true}` (событие `preference.set`). Одна кнопка, без проверок; последнее значение ключа решает.
  - **Для клиента.** `GET /v1/llm/access` → `{mode, judge, blocked, preference_key}`; остаток не отдаётся. `POST …/actions` при `blocked` отвечает `403 LLM_DEGRADED`, `details.reason = "judges_only"`, до записи действия. Отдельный код ошибки — в `api/errors.py` у C.
  - **Бюджеты на ученика**, как и прежде, считаются только по фоновым ролям P2/P3; учитель ограничен лишь режимом `judges_only`.
- Почему: решение владельца; Т-§11.4 (бюджеты в долларах), §2.3 стр. 9
- Обратимость: пул 0 выключает всё; пороги и лимиты — настройки
- Тесты: `tests/llm/test_usage_usd.py::test_pool_tightens_then_keeps_the_models_for_judges`, `tests/session/test_session_jobs_route.py::test_llm_access_and_judges_only_gate`
- Требует подтверждения владельцем: да — размер пула `LLM_POOL_BUDGET_USD` в окружении `production`. Без `LLM_PRICES` траты равны 0 и пул не убывает, поэтому D-B-198 (запрет старта в prod без цен) остаётся
### D-B-210. Модули B снимаются с SQLAlchemy: код хода Фазы 3 удаляется, сессии — на `Db` (WP1.3 шаг 3, issue #215)
- Класс: B
- WP / место: WP1.3, §2.3 строки 4, 11, 16, §6.1 «Удаляются», §8.15; `quack/session/{lifecycle,stream,spoiler_guard,turn_source,sessions,router,schemas,deps}.py`, `quack/practice/evaluation.py`; удалены `session/{turns,context,workers,manifest,locks,tasks}.py`, `practice/selection.py`
- Контекст: часть названных модулей — код Фазы 3 на таблицах `core.sessions`, `core.task_bank`, `core.learner_seq`, событиях `С-§5` (`turn.student_replied`, `session.completed`), у которого есть замена Фазы 4 (`action_turn`/`turn_commit`/`turn_graph`/`turn_source`, `sessions.py`, `extract.episode`, `task_store`, `bank`/`select`). Переносить его на `Db` нечем: реестр Фазы 4 отвергает типы `С-§5`
- Решение: (1) удалены `turns.py` (маршрут `/turns`), `context.py`, `workers.py` (обработчики `turn.extract`, `session.checkpoint` — удаляются по §6.5), `manifest.py` (строку `ops.turn_manifests` пишет `turn_commit`), `locks.py` (замок хода — в `action_turn`, без Redis — `TurnSlots`, Т-§20.7), `tasks.py`, `selection.py` (выбор из `core.task_bank` — `practice/select.py`), `guarded_tasks` из `spoiler_guard.py`, `load_task`/`hint_penalties` из `evaluation.py` (каскад `evaluate_answer` остаётся чистым). Виды `turn.extract`, `session.checkpoint` убраны из `JOB_KINDS`/`KIND_LLM_ROLES`, модуль — из `HANDLER_MODULES` (процедура (б) #173). (2) Маршруты сессий — на `sessions.py`: `POST /v1/sessions` (тело `StartSession`: домен, намерение, тема или хаб, ключ-UUID; ответ `SessionView`), `GET /v1/sessions` (`domain_id`, `limit` как в Фазе 3), `GET /v1/sessions/active`, `GET /v1/sessions/{id}` (`SessionView` + `events` — лента событий сессии по `seq`, без фильтра типов), `POST …/heartbeat` (присутствие в Redis, как в Фазе 3), `POST …/end` вместо `…/finish`; чужая или несуществующая сессия — `404`, другой ключ старта — `409`. (3) `session.sweep_inactive` — обработчик в `lifecycle.py` на `sessions.end_if_inactive`; выборка устаревших сессий — по всем ученикам (как очередь, Т-§2.2), модуль не импортирует `Db`, поэтому `T-DB4` его не проверяет; расписание — A (`periodic_p4` не ставит этот вид). (4) `turn_source.source_of` не передаёт `session_factory`: чтение памяти ждёт конец журнала ученика через пустое наложение с `overlay_seq` = `core.learners.seq` (поведение «ждать весь журнал» сохранено, `lag_degraded` считает читатель A). (5) Из зоны C удалены `quack/artifacts/practice.py` и три его маршрута (`tasks/next`, `…/hint`, `…/submit`: заменены действиями `answer`, `hint_request`, §6.4), `LLM_ERRORS` берётся из `practice.evaluation`
- Почему: §6.1, §6.5, §8.15 п.1, `Т-§12`, ТЗ WP13.10; без этого нельзя снять `sqlalchemy` из модулей B, а `selection` и `load_task` читали таблицу, которой нет
- Обратимость: восстановить удалённые файлы из истории; маршруты — один файл
- Тесты: `tests/session/test_session_routes.py` (маршруты, изоляция, присутствие, сборщик), `test_sessions_db.py` (библиотека), реестр `tests/e2e/routes/b.py`
- Требует подтверждения владельцем: нет
- Правки тестов (§8.15 п.1, тест Фазы 3 удалён вместе с кодом; свойство теперь проверяет тест Фазы 4):
  - `tests/session/test_turns_and_sse.py`, `test_phase3_hot_path.py`, `test_turn_retry.py`, `test_turn_evaluation.py`, `test_turn_submission_fields.py`, `test_turn_lag_and_checkpoints.py`, `test_llm_degradation.py`, `test_degraded_actor.py`, `test_open_task_evaluation.py` (ход `/turns`) → `test_actions_route.py`, `test_turn_graph.py`, `test_turn_commit.py`, `test_turn_effort.py`;
  - `test_turn_extract_worker.py`, `test_extract_quote_grounding.py`, `test_worker_pool_and_slice.py` (`turn.extract`) → `tests/agents/test_extract_episode.py`;
  - `test_session_checkpoint_worker.py` и задачи в `test_checkpoint_law10.py` (`session.checkpoint`, §2.3 строка 11); чистые тесты отрисовки сводки остаются;
  - `test_degradation_and_workers.py`, `test_session_lock_order.py` (замок хода Фазы 3, Postgres-запасной путь) → `test_actions_route.py::test_turn_lock_is_released_for_the_next_action`; Redis-замок с TTL, чужой ключ и снятие при ошибке отдельным тестом не закрыты (`action_turn._lock`) — добавить с WP14.4;
  - `test_session_lifecycle.py`, `test_session_finish_and_sweep.py`, `test_lifecycle_fixes.py`, `test_start_topic_resolution.py` (маршруты Фазы 3 на `core.sessions`, `finish`, план из Neo4j) → `test_session_routes.py`, `test_sessions_db.py`;
  - `test_spoiler_guard.py`: тесты через `/turns` удалены (повтор и удаление предложения — `test_turn_graph.py`, `test_holdback.py`), чистый `T-G2` остаётся; `test_phase2_seams.py`: удалены `T-H7` (кадры `/turns`) и `finish_session` → кадры §4.7 в `test_actions_route.py`; `test_schema_fields.py`: схемы `StartSessionRequest`/`TurnRequest` ушли;
  - `tests/llm/test_labels.py`: `T-M4` → `test_turn_commit.py::test_turn_atomic_and_manifest`, `test_manifest_failure_writes_no_turn`; `T-M2` → `test_turn_graph.py` (неизвестная метка возвращается модели);
  - `tests/artifacts/test_practice_engine.py` (практика Фазы 3 в зоне C) вместе с `artifacts/practice.py`;
  - `tests/e2e/test_journey_and_laws.py` (T-E1, T-E4, T-E5, T-E6, T-E10), `test_degradation.py` (T-E8, T-E9), `test_llm_seams_e2e.py`, `test_loadtest_stream.py`, `T-E7` из `test_isolation_and_idempotency.py`, `run_journey`/`turn` из `journey.py`: путь `/turns` и интервью — Фаза 3; заменяет `T-E1` Фазы 4 (WP26.1, C). `T-E2`, `T-E3` остаются на реестре маршрутов (сессия — `domain.created` Фазы 4 из `prepare`);
  - `tests/test_app_assembly.py`: часть «turns» из `T-A5`, `test_turn_limit_without_redis_is_local`, `test_turn_stream_documents_status_after_turn_started` — лимит ходов удалён (§2.3 строка 16).

### D-B-209. Качество извлечения на эталонном наборе: сопоставление, каппа, ворота версий (WP16.7, хвост)
- Класс: B
- WP / место: WP16.7, Т-§7.11, Т-§18.1, Т-§18.3; `quack/agents/extraction_eval.py`, `Settings.extraction_eval_runs` = 5
- Контекст: документ задаёт метрики (точность отрицательных элементов и гипотез, каппа по пяти прогонам, доля отказов проверки), но не говорит, как сопоставлять ожидаемые и полученные элементы, между чем считать каппу и что делать при отсутствии данных
- Решение: набор — JSON `EvalSet`: эпизод хранит вход извлекателя как он был отрисован (пять блоков с метками) и ожидаемые элементы по тем же меткам; проверка кодом — по блоку `check` (иначе не проверяется, отказы не измеряются). Прогоны — через шлюз, ключ с версией промпта, эпизодом, хэшем входа и номером прогона; в журнал ученика не пишется. Совпадение: тот же `kind` и навык (новый/неясный — `skill: None`); у свидетельства — та же полярность исхода (неверно/частично против верно) и уровень, если ожидаемый задан; у гипотезы — то же известное заблуждение или оба новые (текст не сравнивается); один к одному по порядку вывода. Точность — по принятым кодом отрицательным (incorrect/partial) и гипотезам, суммарно по всем прогонам; полнота рядом. Каппа Коэна — по сырому выводу, единицы — (вид, навык, номер среди одноимённых); поля presence (по всем единицам, «absent» там, где прогона нет), level, outcome, help, gap_type, misconception (по единицам, найденным в обоих прогонах); пары — упорядоченные пары прогонов, одна каппа на поле по всем эпизодам; итог — среднее по полям; полное согласие в одной категории = 1,0. Нет данных (нет эпизодов, один прогон, нет принятых элементов) — `not_measured`. `compare_reports(old, new, tolerance=0)`: регрессия — падение точности или каппы; `None` — нечего сравнить или разные наборы
- Почему: Т-§7.11 «главная метрика — точность отрицательных; полнота вторична; новая версия не снижает ни точность, ни согласованность»; закон 8
- Обратимость: да (один модуль и один параметр)
- Тесты: `tests/agents/test_extraction_eval.py`
- Требует подтверждения владельцем: нет
### D-B-207. `GET /v1/sessions/{id}/jobs`: модель ответа и проверка владения (WP7.4, issue #212)
- Класс: B
- WP / место: WP7.4, §4.8, Т-§13.8; `quack/session/router.py::session_jobs`, `schemas.SessionJobsResponse`
- Контекст: §4.8/Т-§13.8 задают состав плашки (visible, `queued`/`running`), но не форму ответа и не способ проверки сессии
- Решение: ответ `{jobs: [{job_key, kind, status, created_at}]}` (как `VisibleJob`, обёртка как у `SessionListResponse`); владение — `sessions.get_session` (`content.sessions`, как у `/actions`), `LookupError` → `404 NOT_FOUND`; задачи — `queue_p4.visible_jobs`
- Почему: Т-§13.8, issue #212, §7.3 п.3
- Обратимость: да
- Тесты: `tests/session/test_session_jobs_route.py`, реестр `tests/e2e/routes/b.py`
### D-B-208. `effort`, лимит выхода и таймаут роли — из строки `llm_roles` (WP10.1 п.1)
- Класс: A (исправление ошибки)
- WP / место: WP10.1 п.1; `quack/config.py` (`effort_for_role`, `max_output_tokens_for_role`, `timeout_for_role`), `quack/llm/gateway.py`, `scripts/meta_smoke.py`
- Контекст: шлюз брал `effort`, `max_output_tokens` и таймаут только из словарей Фазы 3 (`llm_role_reasoning`, `llm_role_max_output_tokens`, `llm_role_timeout_s`): роли таблицы Фазы 4 уходили без `effort` и без лимита, замер D-B-134 не применялся (находка WP10.7)
- Решение: запись словаря Фазы 3 — переопределение, иначе строка `llm_roles`, иначе (таймаут) `llm_timeout_sec`; из словарей Фазы 3 убраны `teacher`, `teacher_fallback`, `extractor` — их значения теперь из таблицы (`extractor` 16 384, `teacher_fallback` 8192 по D-B-134)
- Почему: Т-§11.1, ТЗ Ф4 WP10.1 п.1 «ровно таблицы»; та же ошибка, что D-B-188
- Обратимость: три функции
- Тесты: `tests/test_llm_runtime.py` (T-L8: тело запроса), `tests/llm/test_meta_smoke.py`
- Требует подтверждения владельцем: нет

### D-B-204. Без ключа — «Не проверено», код 1; в отчёте — что ушло в запросе (WP10.7)
- Класс: B
- WP / место: WP10.7, DoD п.6a; `backend/scripts/meta_smoke.py` (`run`, `render_report`, `exit_code`)
- Контекст: ТЗ: «без ключа — „Не проверено“ с командой», «код выхода как в Фазе 3» (0 — все роли прошли, 1 — иначе); как отметить пропущенную роль и что писать в отчёт, не сказано
- Решение: пустой `META_API_KEY` — сеть и база не трогаются, в stdout (и `--out`) текст «Не проверено» с точной командой, код 1 (ничего не проверено — не зелёный). Роль без файла промпта в реестре (`material_parser`, `viz_checker`) — строка `skipped:no_prompt`, код выхода её не считает ошибкой, но отчёт перечисляет такие роли под «Не проверено». Отчёт: таблица «роль → статус, модель, задержка» и по строке JSON на роль; к полям Фазы 3 добавлены `sent_effort`, `sent_max_output_tokens` — что реально ушло в запросе (`effort` — строка таблицы ролей). Повод: шлюз берёт `effort` и лимит выхода из старых `llm_role_reasoning`/`llm_role_max_output_tokens` (только роли Фазы 3), а не из `llm_roles`, поэтому у ролей Фазы 4 живой прогон покажет `sent_* = null`; правка шлюза — отдельным пакетом (см. отчёт диспетчеру)
- Почему: ТЗ Ф4 WP10.7, ТЗ Ф3 DoD п.6a; Т-§11.1 (таблица ролей — источник `effort` и лимита)
- Обратимость: да, только скрипт
- Тесты: `tests/llm/test_meta_smoke.py` — прогон без ключа, строка с ошибкой схемы даёт код 1
- Требует подтверждения владельцем: нет

### D-B-203. Состав живого прогона: 26 ролей таблицы, промпты Ф3 вне таблицы, проба стрима с инструментом (WP10.7)
- Класс: B
- WP / место: WP10.7, `Т-§11.8` п.1; `backend/scripts/meta_smoke.py`
- Контекст: ТЗ: «все роли и стриминг с инструментом»; какой вход, какая схема у роли с двумя схемами, на какой модели стрим и как гарантировать вызов инструмента (Meta принимает только `tool_choice: auto`), не сказано
- Решение: (1) строка на каждую из 26 ролей `Settings.llm_roles` в порядке таблицы: текстовая — один `generate_structured` по промпту её строки таблицы (`teacher@2`, `analyst@1` для `analyst_deep`) и первой схеме из `ROLE_SCHEMAS` (`builder` — `BuilderOutput`, режим `Reconcile`; `recognizer` — `RecognizerOutput`, режим `line`); недостающие переменные промпта — «none», как в Фазе 3; `recognizer`, `verdict_free` получают встроенный PNG; `segmenter` — `gateway.segment(png, concept="circle")`, `voice` — `transcribe` на 0,5 с тона WAV 16 кГц, `illustrator` — `illustrate`. (2) Шесть промптов Фазы 3 вне таблицы (`evaluator`, `task_gen`, `structurer`, `curator`, `diagnostic`, `artifacts`) остаются; `teacher@1`, `extractor@2`, `teacher_fallback@1` сняты — заменены `teacher@2`, `extractor@3`. (3) Строка `stream_tool`: `LLMGateway.streaming` + `run_tool_loop` на модели `teacher` (Wonder, как в `Т-§11.8`): свой инструмент `smoke_lookup` и короткая инструкция в коде скрипта (вход пробы, не промпт роли) «вызови функцию, затем ответь `{value}` по её результату»; успех — инструмент вызван, шагов ≥ 2, `value` = результату, ответ прошёл строгую схему. Вызов инструмента модель делает сама (`auto`): `NoToolCall` в статусе — результат прогона, не сбой скрипта
- Почему: ТЗ Ф4 WP10.7, Т-§11.8 п.1, Т-§11.1; D-B-187 (не текстовые роли идут через `segment/transcribe/illustrate`)
- Обратимость: да, только скрипт и его тест
- Тесты: `tests/llm/test_meta_smoke.py` — все роли таблицы и стрим на подменном провайдере (ответ каждой текстовой роли строится из её строгой схемы), набор вызовов совпадает с `ALL_PROMPT_SCHEMAS` кроме снятых версий
### D-B-206. Сверка: ответы модели в события, повтор и встречные связи (WP16.3)
- Класс: B
- WP / место: WP16.3; `quack/agents/reconcile.py`, `Settings.same_idea_confidence`, `Settings.reconcile_requires_confidence`
- Контекст: ТЗ даёт варианты `link`/`create`/`suggest_merge`/«та же идея», но не говорит, чему они равны в событиях, как повтор не плодит узлы и откуда уверенность связей
- Решение: id создаваемых узлов — `uuid5` нормализованного имени в домене (повтор пишет те же события под теми же ключами; одно имя — один узел, `Т-§4.4`); `create` с именем существующего кандидата — `link`. `suggest_merge` у навыка: навык создаётся и пишется `skill.merge_suggested {from = новый, into = цель}`; у темы события нет — тема создаётся. `link` на навык другого домена — предмет пропущен (`outcome = skipped`, правило 6 слоя 1). «Та же идея» — `edge.added {same_idea}` от нового навыка к навыку другого домена; уверенность по категории `same_idea_confidence` (low 0.4, medium 0.75, high 0.9; перенос от `same_idea_min_confidence` = 0.7). `REQUIRES` — из `Subject.requires` (явный порядок материала), `origin = material` при `origin = material`, уверенность `reconcile_requires_confidence` (material 0.85 > model 0.65 ≥ `requires_min_confidence`); встречная пара `A→B`/`B→A` отбрасывается и против записанных в графе (чтение Cypher), и внутри пачки (первая побеждает). Тема нового навыка — метка `T…` модели, иначе имя (в пачке или новая), иначе `Subject.topic`, иначе предмет пропущен. Предмет без ответа модели пропущен. Неизвестная метка — один возврат модели через `check` (`Т-§4.5`). Embeddings недоступны — исключение идёт вверх, задача ждёт (`Т-§20.7`): без кандидатов модель создавала бы дубли. Статус создаваемого навыка — `active` (предложенность темы — статус проектора, `Т-§4.7`)
- Почему: Т-§4.2, Т-§4.3, Т-§4.4, Т-§4.5, Т-§4.6, ТЗ Ф4 WP16.3, T-AG3
- Обратимость: да
- Тесты: `tests/agents/test_reconcile.py`
- Требует подтверждения владельцем: нет

### D-B-205. Кандидаты сверки — Cypher модуля B, не `GraphReader` (WP16.3)
- Класс: B
- WP / место: WP16.3; `quack/agents/reconcile.py::find_candidates/read_requires`
- Контекст: в `GraphReader` нет поиска кандидатов для сверки по тексту предмета (`extraction_candidates` — под эпизод и сессию); сигнатуры `GraphReader` — контракт A
- Решение: `find_candidates` — векторный поиск `topic_emb`/`skill_emb` по `embed_query(имя + текст)` в домене, соседи трёх ближайших навыков (`REQUIRES`, та же тема), ближайшие навыки других доменов (поиск по `learnerId`, фильтр домена после поиска), до `candidate_k` на предмет: по трети — другой вид и чужие домены, остальное — свой вид и соседи. Метки общие на вызов: порядок важности — по кругу по предметам; сверх `candidate_skills_k`/`label_max_topics` метки не выдаются. Сверка временных сущностей после сессии (`Т-§4.4`) — уже `graph.hygiene` по `session.ended` (D-B-185): нового вида и правила не нужно. Если A захочет метод в `GraphReader` — заменить тело `find_candidates`
- Почему: Т-§4.3, Т-§3.4.6, ТЗ Ф4 WP16.3
- Обратимость: да
- Тесты: `tests/agents/test_reconcile.py::test_candidates_vector_neighbours_and_other_domains`
- Требует подтверждения владельцем: нет

### D-B-202. Удаление трасс: нет подтверждённого API, срок хранения (WP10.6)
- Класс: B
- WP / место: WP10.6, WP25; `quack/llm/tracing.py::Tracer.delete_learner`, `Settings.langsmith_retention_days`
- Контекст: ТЗ: удаление трасс по псевдониму — [ПРОВЕРИТЬ] `Т-§19.5`; REST-метод удаления запусков по значению метаданных в документах не назван и не проверен (документация недоступна из сессии)
- Решение: `Tracer.delete_learner(pseudonym) -> bool` есть в интерфейсе; `LangSmithTracer` вызова не делает и возвращает `False` (лог `langsmith_delete_unsupported`); подмена возвращает `True` и пишет журнал. Удаление аккаунта (WP25, A) вызывает `tracer.delete_learner(learner_pseudonym(settings.langsmith_pseudonym_ns, learner_id))` и при `False` ничего не делает: трассы уходят по сроку `langsmith_retention_days` (14, настройка проекта LangSmith — действие владельца). Когда API подтвердят — заменить тело метода
- Почему: ТЗ Ф4 WP10.6, §2.3 стр. 20, Т-§19.5
- Обратимость: да
- Тесты: `tests/llm/test_tracing.py`
- Требует подтверждения владельцем: да (срок хранения в проекте LangSmith; [ПРОВЕРИТЬ] API удаления)

### D-B-201. Отправка в LangSmith — `httpx` по REST, без пакета `langsmith` (WP10.6)
- Класс: B
- WP / место: WP10.6; `quack/llm/tracing.py`, `Settings.langsmith_*`
- Контекст: §4.6 разрешает `langsmith`, но его нет в `pyproject.toml`, а новые зависимости в этой работе запрещены; `httpx` уже есть
- Решение: `LangSmithTracer` шлёт `POST {langsmith_endpoint}/runs` (заголовок `x-api-key`) фоновой задачей с таймаутом `langsmith_timeout_s`; вызов шлюза не ждёт и не падает от сбоя. Ключ `LANGSMITH_API_KEY` (`Settings.langsmith_api_key`, замаскирован в логах) — пустой ключ = трассировка выключена; `create_tracer` даёт `NullTracer` при `env == test`, вне процессов `api`/`workers` и без ключа. Профиль `loadtest` ключ не получает (compose — C). Формат запуска (поля `POST /runs`) — [ПРОВЕРИТЬ] на живой проверке; позже можно заменить пакетом `langsmith` без смены интерфейса `Tracer`
- Почему: ТЗ Ф4 WP10.6, §4.6, Д-§7, Д-§11
- Обратимость: да
- Тесты: `tests/llm/test_tracing.py`

### D-B-200. Что попадает в трассу и на каком шаге (WP10.6)
- Класс: B
- WP / место: WP10.6; `quack/llm/gateway.py::_ledger/_trace`
- Контекст: «каждый вызов шлюза — трасса»; у вызова несколько попыток и путей (текст, стрим с инструментами, `segment`/`transcribe`/`illustrate`)
- Решение: одна трасса на попытку — рядом с каждой строкой журнала (`_ledger`), поэтому покрыты все пути, включая шаги стрима и цикл с инструментами. Поля: роль, `роль@N` (`роль@0` у медиа-ролей), псевдоним `uuid5(langsmith_pseudonym_ns, learner_id)`, модель, токены (вход, выход, кэш, рассуждение), задержка, статус, код ошибки. Текст промпта и ответа, `learner_id`, id сессии и хода в трассу не уходят. Без трассы: ответ из outbox, отказ по бюджету, разомкнутая цепь (нет строки журнала и нет обращения к модели). Сбой трассировщика — строка лога, вызов идёт дальше (закон 7 по смыслу)
- Почему: Т-§11.7, ТЗ Ф4 WP10.6, §2.3 стр. 20, T-AI8
- Обратимость: да
- Тесты: `tests/llm/test_tracing.py`

### D-B-196. Иллюстрация: ключ, ответ только с `url`, подсказка (WP16.6)
- Класс: B
- WP / место: `agent.illustration`; `quack/agents/illustration.py`, `quack/agents/delivery.py` (`result_ref`)
- Контекст: в `quack/storage/keys.py` (C) нет ключа иллюстрации; `Illustration` может прийти только с `url`; промпта у роли нет.
- Решение: ключ `learners/{learner}/illustrations/{request_id}-{ULID}.{png|jpg|webp|bin}` собирается в модуле B из `learner_prefix` и `new_ulid` (C не правим; если C заведёт `illustration_key`, заменить). Ответ только с `url` — `agent.completed {ok: false, error: image_url_only}`: скачивать с чужого хоста без правила нельзя, внешняя ссылка не есть ключ хранилища [ПРОВЕРИТЬ на стенде: какую форму отдаёт `muse-image-1.0`]. Нет `params.description` — `ok: false, error: no_description`, без вызова модели. Подсказка модели — `description` с фиксированным префиксом «концептуальная иллюстрация, без формул, графиков и математических схем» (П-4.5). Актор события — `agent:illustration` (у роли нет версии промпта, формат `<role>@<N>` не подходит).
- Почему: ТЗ Ф4 WP16.6, П-4.5, Т-§3.3, Т-§13.9, D-B-187
- Обратимость: да
- Тесты: `tests/agents/test_illustration.py`
### D-B-199. Бюджеты в долларах: какие роли считаются и что останавливается (WP10.4)
- Класс: B
- WP / место: WP10.4; `quack/llm/usage.py::check_usd_budgets`, `errors.py`
- Контекст: Т-§11.4 не называет роли дневного бюджета; очередь A (`queue_p4.budget_roles`) уже считает сессионный бюджет по ролям P2/P3 без `extractor`, аналитика и `session_budget_exempt_roles`
- Решение: перед вызовом роли из этого же набора шлюз проверяет (а) расход сессии по тем же ролям `> session_agent_budget_usd` → `LLMBudgetExceededError("session", …, "usd")`; (б) расход ученика за UTC-сутки по всем ролям P2/P3 `>= learner_daily_budget_usd` → `"daily"`. Учитель, P0/P1, извлечение и аналитик не останавливаются, но попадают в строки. Без `llm_prices` проверки пропускаются (стоимость 0). Наборы ролей продублированы (llm не импортирует jobs, цикл), оба читают `ops.llm_usage`; изменить правило — в обоих местах
- Почему: Т-§11.4, ТЗ Ф4 WP10.4, §2.3 стр. 9; согласовано с `queue_p4`
- Обратимость: одна функция
- Тесты: `tests/llm/test_usage_usd.py::test_budgets_in_dollars`
- Требует подтверждения владельцем: нет

### D-B-198. Цена голоса и картинки, отказ старта в prod без цен (WP10.4)
- Класс: B (цены — значения владельца, §1.3)
- WP / место: WP10.4; `quack/config.py::ModelPrice`, `Settings._require_prices_in_prod`
- Контекст: у `voice` токенов нет (только `audio_duration_ms`, D-B-187), у `illustrator` токены не отдаются; в таблице цен ТЗ только вход/кэш/выход. Отказа старта без цен в коде не было
- Решение: `ModelPrice` получил `per_minute` (доллары за минуту звука) и `per_image` (за картинку); все пять полей по умолчанию 0. Стоимость = токены (вход без кэша, кэш, выход; рассуждение уже в выходе) + минуты·`per_minute` + картинки·`per_image`. Модель без цены — стоимость 0 и лог `llm_prices_missing` (раз на модель за процесс). `ENV=prod` и `process_role` `api`/`workers` без `llm_prices` — ошибка проверки Settings (`projector`, `ops` не вызывают модели)
- Почему: ТЗ Ф4 §2.3 стр. 9, WP10.4, Т-§11.4
- Обратимость: поля с нулями; проверку убрать одной функцией
- Тесты: `tests/test_settings_meta_api.py::test_prod_requires_the_price_table`, `tests/llm/test_usage_usd.py`
- Требует подтверждения владельцем: да (значения цен; compose prod должен передавать `LLM_PRICES` JSON — C/DevOps)

### D-B-197. Шлюз пишет `ops.llm_usage` через `Db`; переходный период (WP10.4)
- Класс: B
- WP / место: WP10.4; `quack/llm/gateway.py` (`LLMGateway(..., db=)`, `_usage`), `quack/llm/usage.py`
- Контекст: шлюз получал только `SessionFactory` и писал `ops.llm_ledger` (Alembic-схема); `Db` в шлюз не передавался, а в схеме Фазы 4 (dbmate) нет `llm_ledger`, `llm_outbox` и `sessions.tokens_used`
- Решение: новый необязательный `db: Db | None`. С `db` — каждая попытка вызова (текст, стрим, `segment`/`transcribe`/`illustrate`) пишет одну строку `ops.llm_usage` со стоимостью по `llm_prices`, `retried` = не первая строка вызова, `incomplete` по статусу; бюджеты в долларах (D-B-199). Без `db` — прежний путь: `llm_ledger` и токеновые бюджеты. Две таблицы разом не пишутся: схемы разные (одна база — одна из них). «Заменяет ledger» (ТЗ) выполняется на WP1.3: A/C при снятии SQLAlchemy передают `db=` в `LLMGateway` (`quack/runtime/__init__.py`, `quack/api/resources.py`, `tests/support/resources.py`), убирают `session_factory`; B тогда удаляет ветку ledger, токеновые бюджеты и outbox (`core.llm_outbox` в baseline нет)
- Почему: ТЗ Ф4 WP10.4 «Заменяет ops.llm_ledger», Т-§11.4; WP1.3
- Обратимость: ветка по `db is None`
- Тесты: `tests/llm/test_usage_usd.py`
- Требует подтверждения владельцем: нет

### D-B-193. «Я это уже знаю»: уровень, число задач, ключ ответа (WP8.2, WP16.6)
- Класс: B
- WP / место: `check.known_claim`; `quack/agents/task_generation.py`, `Settings.known_check_max_tasks`
- Контекст: ТЗ WP8.2 — «1–3 задачи по навыкам темы на целевых уровнях»; какой уровень и как набрать три — не сказано.
- Решение: по одной задаче на навык темы (первая сохранённая из вызова), в порядке навыков, не больше `known_check_max_tasks = 3`; уровень — рубеж навыка (`frontier`) среди уровней, которые требуют хабы темы (иначе 1–6); навык с доказанными целевыми уровнями пропускается. Ответ `agent.completed {kind: known_check, task_ids}` — по `request_id`, для `known.claimed` — по `job_key = check.known_claim:{learner}:{seq}` (собирается по правилу WP7.1, у `ClaimedJob` Ф3 поля `job_key` нет). `ok: false`, если проверить было что, но ни одна задача не прошла проверку, или темы нет в памяти.
- Почему: ТЗ Ф4 WP8.2, WP12.6, Т-§13.9
- Обратимость: да
- Тесты: `tests/agents/test_task_agents.py`
- Требует подтверждения владельцем: нет

### D-B-192. Тип ответа и цель поручения `agent.task_generation` (WP12.6, WP16.6)
- Класс: B
- WP / место: `quack/agents/task_generation.py`
- Контекст: `agent.requested` не несёт тип ответа и цель (практика/ретест), а `generate_tasks` требует `kind` и `purpose`.
- Решение: тип ответа по уровню — 1 `choice`, 2 `short_text`, 3–5 `number`, 6 `proof`; есть `params.task_id` — это исходная задача, цель `retest` (варианты в её семье, Т-§5.4, Т-§5.7), иначе `practice`; `params.description` идёт в контекст курса, `misconception_id` сужает известные заблуждения. Нет `skill_id`/`level` — `JobFailedPermanently`; ни один вариант не прошёл проверку — `agent.completed {ok: false}`. `task_arbiter` отдельного обработчика не имеет: арбитр внутри `generate_tasks` (WP12.5).
- Почему: ТЗ Ф4 WP12.6, WP16.6; Т-§5.5, Т-§13.9
- Обратимость: да
- Тесты: `tests/agents/test_task_agents.py`
- Требует подтверждения владельцем: нет

### D-B-191. Обработчики `task.solution_space`, `agent.solution_space`, `agent.task_generation`, `check.known_claim` (WP16.6)
- Класс: B
- WP / место: `quack/agents/{delivery,solution_space,task_generation}.py`; регистрация (б) #173 в `kinds.py`, `runtime/__init__.py`
- Контекст: образец — `agents/viz.py`; исчерпавшую попытки задачу закрывает очередь (`agent.completed {ok:false}`, WP7.4), обработчик пишет только исход по существу.
- Решение: `agent.completed` пишется ключом `agent.completed:{request_id | job_key}` с проверкой существующего (повтор не дублирует); пространство решения читает заблуждения навыков задачи из памяти (`slice_for_turn` с пустой сессией `NO_SESSION`), `agent.solution_space` возвращает `task_ids=[task]`, чужая задача — `ok:false task_not_found`; новый параметр `known_check_max_tasks`; в `practice/repo.py` — `agent_request`.
- Почему: ТЗ Ф4 WP12.4, WP16.6, Т-§13.9, закон 5
- Обратимость: да
- Тесты: `tests/agents/test_task_agents.py`
- Требует подтверждения владельцем: нет

### D-B-195. Метрики `ops metrics`: определения там, где `Т-§18.2–18.3` оставляют свободу (WP16.7)
- Класс: B
- WP / место: WP16.7; `quack/agents/metrics.py`, `Settings.metrics_brier_max_attempts` (200)
- Контекст: документ называет метрики без знаменателей и окон; каталог событий не хранит предсказанных вероятностей
- Решение: у каждой метрики `status: measured | not_measured` (пустое окно или нет входа — «не измерено», закон 8). Знаменатели: ветвления и `teacher_fallback` — на `turn.committed`; `unclear`/`numericOnly` — на `verdict.recorded`; `check.failed` — на вердикты + `check.failed`; оспаривания — на 1000 вердиктов (по `target_kind`); снятые пометки — `observation.second_opinion` с `agreed=false` из всех вторых мнений; отказы извлекателя — `rejected` из элементов + `rejected`; объединённые временные навыки — `skill.created {provisional}` с `skill.merged` по `from_id`; стоимость в час — `ops.llm_usage` с учеником ÷ различные (ученик, час) с действием ученика. Самостоятельный успех потом: на (ученик, навык) первая позднейшая попытка Т1 в другой сессии на задаче другого семейства, верно и без подсказок, обе в окне. Брайер: `state_at(seq ответа − 1)` и правило `Т-§18.2` (`p_*` × `R`); нужен `reader` — без него «не измерено»; не больше `metrics_brier_max_attempts` последних попыток (каждая — переигрыш журнала). Точность и кривая калибровки по корзинам 0.1
- Почему: `Т-§18.2`, `Т-§18.3`, `Т-§22.10` (JSON без дашбордов)
- Обратимость: полная (определения в одном модуле)
- Тесты: `tests/agents/test_metrics.py`
- Требует подтверждения владельцем: нет

### D-B-194. Проигрывание сессии и судья релиза (WP16.7)
- Класс: B
- WP / место: WP16.7; `quack/session/replay.py` (`replay_session`), `quack/agents/judge.py` (`release_judge`)
- Контекст: `Т-§12.8`: «восстанавливает ситуацию каждого хода по манифесту»; слои `§4.1` запрещают `session` ↔ `agents`
- Решение: ситуация хода — тот же `MemoryTurnContextSource` и `run_turn`, что на живом ходу, поверх офлайн-портов (коммит держит ход в памяти, кадры никуда, `core.events` не пишется); память — `AsOfReader`: `slice_for_turn` = `state_at(projected_seq манифеста)` по навыкам фокуса и таблице меток манифеста, остальные чтения — живые; слоты 3–4 — снимок сессии. Судья — вызываемый объект `PairJudge`, который передаёт вызывающий (`release_judge(gateway)`): оба порядка, победа только если совпала в обоих, иначе tie. Нарушение хода: старый — `guard_retries > 0`, новый — `guard_retries > 0` или остаточные нарушения `Т-§12.4` (метки, граница, спойлер); «доля не выросла» — `new ≤ old`, «судья не хуже» — побед нового ≥ побед старого. Не восстанавливается и записано в отчёте: заблуждения среза, куски материалов, сводка аналитика и результаты агентов (читаются на сейчас), версия отрисовщика (в коде только `renderer@1`: другая версия — пометка в отчёте). Эталонные наборы LangSmith и фильтр `research` — не сделаны (в ТЗ — «тот же инструмент на наборе», вход набора — данные C)
- Почему: `Т-§12.8`, `Т-§18.4`, ТЗ Ф4 WP16.7
- Обратимость: полная
- Тесты: `tests/session/test_replay.py`, `tests/agents/test_judge.py`
- Требует подтверждения владельцем: нет

### D-B-189. «Давай 5 минут» — лимит из текста ученика (WP17.6)
- Класс: B
- WP / место: WP17.6, WP14.2; `quack/session/review_turn.py` (`time_limit_minutes`, `review_item_cap`)
- Контекст: `Т-§14.2` — «ограничение в цели сессии», но `session.started` / `message.sent` не имеют поля времени; события — контракт A, менять нельзя.
- Решение: код берёт последнее «N мин…»/«N min…» из реплик ученика сессии; пунктов = max(1, N // `review_item_minutes`), отбор по приоритету. Без числа — без ограничения. Когда у клиента/событий появится явное поле лимита, `time_limit_minutes` заменить на него.
- Почему: Т-§14.2, WP17.6; закон 2 не нарушен — число считает код по тексту, без модели.
- Обратимость: легко.
- Тесты: `tests/session/test_review_turn.py`
- Требует подтверждения владельцем: нет

### D-B-190. Кандидаты повторения в картине памяти и подбор из банка (WP17.6)
- Класс: B
- WP / место: WP17.6, WP14.2, WP12.7; `review_turn.py`, `memory_picture.py`, `turn_source.py`
- Контекст: ТЗ не говорит, какие кандидаты показываются вне намерения `review`, на каком уровне брать задачу банка и как её показать учителю.
- Решение: вне `review` — только `is_goal_prerequisite` (цель — навыки тем цели); при `review` с темой/хабом — все кандидаты области (`ReviewScope.topic_id|hub_id`). Они идут в картине сразу после навыков цели и с пометкой «due for review» (пометка по `retDueAtMs` из WP14.2 остаётся). Для первых `label_max_tasks` кандидатов — одна задача `select_tasks` («семейство не встречалось») на уровне `frontier` (все уровни, если цель их не задаёт); строка «Repeat with a new family, bank task K…: "<условие>"» (K — метка, учитель использует как `variantOf`). `max_items` отбора — в коде, после фильтра по цели.
- Почему: Т-§14.2, Т-§5.7, Т-§10.2; ближайшее правило — `today.prepare` (D-B-184).
- Обратимость: легко.
- Тесты: `tests/session/test_review_turn.py`
- Требует подтверждения владельцем: нет


### D-B-188. `model_for_role` читает строку `llm_roles` (WP10.1 п.1)
- Класс: A (исправление ошибки)
- WP / место: WP10.1 п.1; `quack/config.py::Settings.model_for_role`, `quack/llm/gateway.py`
- Контекст: метод брал `llm_role_models` или `llm_default_model` (Spark) и не читал таблицу ролей D-B-101: `verdict_proof`, `second_opinion`, `analyst_deep`, `task_arbiter`, `viz_builder`, `release_judge` шли на Spark вместо Wonder, нетекстовые роли — тоже на Spark
- Решение: порядок — явная запись `llm_role_models` (переопределение окружения) → модель строки `llm_roles` → `llm_default_model`; `hard_model` как прежде. Обходной `_media_model` D-B-187 снят
- Почему: Т-§11.1 (модель роли — по таблице), ТЗ Ф4 WP10.1 п.1 «ровно таблицы»
- Обратимость: одна функция
- Тесты: `tests/test_settings_meta_api.py::test_default_models_are_the_live_ids`
- Требует подтверждения владельцем: нет

### D-B-187. Адаптеры нетекстовых ролей шлюза: `segmenter`, `voice`, `illustrator` (WP10.1)
- Класс: B
- WP / место: WP10.1 третий пункт; `quack/llm/media.py`, `gateway.py` (`segment`, `transcribe`, `illustrate`), `responses_client.py`, `provider.py`; `tests/llm/test_phase4_media_roles.py`
- Контекст: по WP0 п.3д форма `sam-3.1` и голоса известна (документация Meta, без вызова); для `muse-image-1.0` документов нет; формат спецтокенов описан не полностью (запись «id, рамка, маска», w/h рамки, префикс base85, `|>` внутри данных)
- Решение: (1) три публичных метода шлюза возвращают `LLMResult[Segmentation | Transcription | Illustration]`; путь вызова общий с текстовыми ролями (бюджеты, слот модели, повторы, 429 и флаг давления, 400, размыкатель, строка учёта на попытку). Без аутбокса и без промпта: версия 0, `prompt_hash` — хеш `роль@media`; повтор вызова — повторная плата. (2) `segmenter` — `POST /responses`, `input_text` (имя предмета ИЛИ `<0f>1<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>`) + `input_image` (data URL), `metadata.mask_encoding`/`include_confidence`, без `text.format`; ответ разбирает `parse_segmentation` (рамка начинает объект, маска за ней — его; до 16; маску не декодируем: `data` — base85 как пришло, `marker` `~`/`!`). (3) `voice` — `POST {base}/asr/transcribe`, multipart `request`+`audio`; токенов нет, длительность в `audio_duration_ms`. (4) `illustrator` — `POST {base}/images/generations`, `response_format=b64_json`, байты или `url`. (5) модель нетекстовой роли — `llm_role_models[role]`, иначе строка `llm_roles` (`model_for_role` для них вернул бы Spark)
- Почему: Т-§1.3, Т-§11.1, WP0 п.3д; ближайшее правило — OpenAI-совместимые пути
- Обратимость: вся форма запросов изолирована в трёх методах `ResponsesClient`
- Тесты: `tests/llm/test_phase4_media_roles.py` (по одному на роль: тело запроса, разбор, строка `ops.llm_ledger`)
- Требует подтверждения владельцем: нет. [ПРОВЕРИТЬ на стенде]: путь и тело `muse-image-1.0`; значения `w`/`h` рамки; формат записей и префикс маски `sam-3.1`; нужен ли голосу учёт в долларах по секундам (в WP10.4 строка `llm_usage` без токенов)
