# Журнал решений потока A — Фаза 4 (с 2026-11-03)

Новые решения потока A пишутся **сюда**, сверху, в момент решения (ТЗ Ф4 §0.4.5). Общий
`backend/PHASE4_NOTES.md` — архив до 2026-11-03: новые записи туда не добавляются, это убирает
конфликты слияния. Сводка всех решений — `documentation/Фаза 4 — решения (единое).md`: добавь туда
строку о решении в той же волне.

Формат записи (как в архиве):

```
### D-A-NNN. <суть>
- Класс: A | B | C (§0.4.5)
- WP / место: …; файлы
- Контекст: чего нет в документах
- Решение: …
- Почему: Т-§ / Д-§ / ТЗ
- Обратимость: …
- Тесты: …
- Требует подтверждения владельцем: да / нет
```

Номера продолжают архив: следующий свободный номер — по `grep -h "^### D-A-" backend/PHASE4_NOTES.md backend/notes/A.md`.

<!-- записи ниже, новые сверху -->

### D-A-245. WP7.5: правило пересчёта базовых весов ярусов Т1–Т4 в `calibration.weekly` (заменяет D-A-221)
- Класс: A
- WP / место: WP7.5; `quack/jobs/calibration_p4.py` (`WeightRule`, `WEIGHT_RULE`, `TierSums`, `calibrate_weights`), `quack/graph/calibration_read.py` (новый), `tests/jobs/test_calibration_p4.py`, `tests/graph/test_calibration_read.py`
- Контекст: D-A-221 оставила `evidence_weights: null`: «в документах нет правила». Владелец поручил сверить документы и решить самому. Правило есть в виде принципа: Т-§7.2 п.5 (ярус «группирует данные для калибровки»), Т-§7.11 (мера — оценка Брайера по исходу следующей попытки Т1, «вес вида, который систематически не подтверждается, снижается в конфиге»), порога и шага нет. Номер D-A-245: 240–244 заняты другим разработчиком.
- Решение: (1) Вид свидетельства = ярус Т1–Т4; калибруются `Settings.fold.tier_base_weights`. Множители Т-§7.4 (подсказка, окно «горит», угадывание) **не калибруются**. (2) Единица — попытка Т1 (код-вердикт размеченной задачи) × проверяемая пара (навык, уровень), исход y ∈ {0,1}. Для каждого яруса берутся действующие `Evidence` на навыке этого уровня с `sourceSeq < answer_seq`, исходом correct/incorrect и `assistance != 'shown_solution'`; q — доля correct, x = q − 0,5, y' = y − 0,5; без свидетельств яруса единица для него пропускается, x = 0 — счётчик `ties`. (3) Сила яруса `w* = Σxy' / Σx²` (наименьшие квадраты через ноль, минимизирует Брайера `0,5 + w·x`), `se = sqrt(RSS / (n−1) / Σx²)`; Т1 — шкала, `ρ = w*_T / w*_T1`. (4) Для Т2–Т4 предложение только при `n_T ≥ min_units` и `n_T1 ≥ min_units`, `w*_T1 − z·se_T1 > 0` и верхней границе `ρ` = `(w*_T + z·se_T)/(w*_T1 − z·se_T1)` строго ниже текущего веса: `min(текущий, max(ρ, текущий·(1 − max_step), floor))`. Повышений не предлагается (Т-§7.11 говорит о снижении; рост доверия — риск ложной диагностики, принцип 8): если нижняя граница `ρ` выше текущего — `direction: "above_current"` без предложения; иначе `keep` или `insufficient`. (5) Порядок ярусов сохраняется последовательно Т2→Т3→Т4 (вес не больше веса яруса выше); снижение по порядку — `capped_by_order: true`. (6) Т1 предложения не получает. (7) Параметры — `WeightRule` в модуле, не в `Settings` (config.py — зона C). Стартовые значения: `min_units = 30`, `z = 1,645` (односторонняя 95%), `max_step = 0,5`, `floor = 0,05`. (8) Формат: `evidence_weights = {current, reference_strength, tiers: {T1..T4: {units, ties, strength, se, relative, direction, proposed, capped_by_order}}}`; `evidence_weights_reason` — строка только когда шкала Т1 не определена (мало единиц или нет разброса) или не положительна с доверием, иначе `None`. (9) Свидетельства читаются одним запросом Cypher на ученика (все навыки окна) через `calibration_read.read_tier_evidence`; `calibration_proposal` принимает читателя параметром `evidence` (в задаче — `partial(read_tier_evidence, ctx.neo4j)`), чистая математика — `calibrate_weights(sums, current, rule) -> (отчёт, причина)`. Ничего не меняется: предложение уходит в лог (Т-§18.5).
- Почему: Т-§7.2 п.5 «Группирует данные для калибровки»; Т-§7.4 «Все числа — стартовые, их уточняет калибровка»; Т-§7.11 «для каждого вида свидетельства меряется, насколько оно предсказывает исход следующей попытки Т1 по тому же навыку и уровню (оценка Брайера). Вес вида, который систематически не подтверждается, снижается в конфиге»; Т-§18.5 «предложение… автоматически веса не меняются никогда… проверка на эталонных наборах, а не тихий дрейф» (отсюда шаг не больше `max_step`, только снижение и доверительная граница); Т-§7.3 (ворота).
- Обратимость: полная, это предложение в логе; конфиг не меняется.
- Тесты: `tests/jobs/test_calibration_p4.py` — ручной счёт сумм (слабый ярус снижен на шаг, сильный `keep`), мало данных, шкала Т1 не определена или не положительна, повышение не предлагается, порядок ярусов, интеграционный счёт `evidence_weights` с подставленным читателем (в Postgres и настройках ничего не изменилось); `tests/graph/test_calibration_read.py` — Neo4j: только active, уровень, `sourceSeq < answer_seq`, не `shown_solution`, чужой ученик не читается.
- Требует подтверждения владельцем: нет — числа Т-§7.4/Т-§7.11 «уточняет калибровка», решение принято по поручению владельца «сверься с доками и реши сам»; параметры `WeightRule` — стартовые, меняются правкой значения в модуле.

### D-A-239. WP2.3 / WP3.4: наложение сессии на `rules@3`
- Класс: A
- WP / место: WP2.3, WP3.4; `quack/graph/overlay_build.py` (новый), `quack/graph/overlay.py` (только докстринг), `quack/graph/projection/runner.py`, `quack/events/log.py`, `tests/test_session_overlay.py`
- Контекст: `overlay.py` Фазы 4 держал только форму `SessionOverlay`; `turn_source` строил пустое наложение (STATUS, «Следующие шаги», A п.2). Ждать ответа B не нужно: ТЗ WP3.4 и `Т-§2.6` требуют режим «наложение сессии» на тех же обработчиках.
- Решение: (1) `read_session_tail(db, learner_id, session_id, after_seq)` в `events/log.py` — события одной сессии с `seq > after_seq` по порядку. (2) Из `project_learner` вынесены `load_batch_inputs` (needs → срез Neo4j и ранние события, только чтение) и `run_batch` (фазы А–В–С в памяти над срезом, без записи); `_Neo4jFolds` стал `Neo4jFolds`. Пачка и наложение идут через один и тот же код: правил наложения нет. (3) `overlay_build.build_session_overlay(db, neo4j, settings, learner_id, session_id, applied_seq=None)`: `applied_seq` по умолчанию — `Learner.projectedSeq`; хвост пуст — пустое наложение с `overlay_seq = applied_seq`; иначе срез хвоста читается из Neo4j, фазы А–С — в памяти, `overlay.graph` — весь срез после фаз (`InMemoryGraph`, как читают `neo4j_reader`), `overlay_seq` — последний `seq` хвоста. `apply_overlay_events(...)` — чистая часть над готовым `SubGraph` (базовый срез не меняется). (4) Только события текущей сессии (`Т-§2.6`); события других сессий и без сессии в промежутке не применяются, хвост берётся по `session_id`, поэтому наложение не равно «всей отстающей части журнала». (5) Сводные поля: `assigned_task_ids` — `task.created` и задачи разметки `turn.committed`; `submitted_task_ids` — `answer.submitted`; `evaluations` — `verdict.recorded` (по задаче ответа; `score` 1/0,5/0 за correct/partial/иное, `evaluator` — verification, `feedback` пуст); `hints` — `hint.shown` (текст не хранится в событии, `hint_text` пуст); `last_turn_index` остаётся -1: в событиях Фазы 4 индекса хода нет. Поля нигде не читаются (grep), оставлены ради формы. (6) Построитель вынесен в отдельный модуль: `overlay.py` импортируют читатели, построитель импортирует проектор, проектор — читателей (цикл). (7) `EventFailed` не глотается: вызывающий решает, как деградировать (закон 9). Одна оговорка формы: `InMemoryGraph` ключует связь по концам и типу, поэтому параллельные `TESTS` одной задачи на одном навыке (разные level/part) схлопываются; читатели их из наложения не берут.
- Почему: `Т-§2.5` (один набор на все режимы), `Т-§2.6` (наложение), ТЗ WP2.3 («типы наложения — все события сессии с обработчиком»), WP3.4.
- Обратимость: полная.
- Тесты: `tests/test_session_overlay.py` — эквивалентность наложения и применения проектором (узлы и связи наложения есть в Neo4j после применения с теми же свойствами), пустое наложение при догнавшем проекторе, ничего не записано (базовый срез, Neo4j, `ops.projector_offsets`), события другой сессии не попадают.
- Требует подтверждения владельцем: нет (подключение в `turn_source` — задача B)

### D-A-238. WP1.3 шаг 5 ч.2: что оставлено из списка Фазы 3 и почему
- Класс: A
- WP / место: WP1.3; `quack/graph/projector_rows.py`, `quack/events/payloads.py`, `quack/jobs/kinds.py`, `quack/events/{actor,errors,hashing,keys}.py`
- Контекст: задание велело снять `projector_rows.py`, «если больше нигде не нужен»; остальные модули в списке снятия не названы.
- Решение: оставлены. `projector_rows.py` читают `tests/graph/test_projector_rows.py` и `test_projection_rules3.py` (таблица S5 из 38 типов, T-P3) и описывает `graph/projection/handlers.py`; `events/payloads.py` (модели Фазы 3: `Unit`, `ExtractedElement`, `TurnExtractedV2`, `TaskEvaluated`) импортируют `session`, `practice`, `domains`, `graph/evidence_validation.py`; `jobs/kinds.py` держит `JOB_KINDS` для `check_handlers_complete` и `JobSpec`; `actor/errors/hashing/keys` использует `events/log.py`. Снимать их — вместе с переносом `session`/`practice` на модели Фазы 4 (потоки B и C), не в этом шаге.
- Почему: «если больше нигде не нужен» — он нужен; чужие потребители — зона B/C.
- Обратимость: полная.
- Тесты: нет.
- Требует подтверждения владельцем: нет

### D-A-237. WP1.3 шаг 5 ч.2: `quack/session/*` и тесты сессий — на `StoredEvent` Фазы 4
- Класс: A (правка зоны B по §7.3 п.1, только замена Фазы 3 на Фазу 4)
- WP / место: WP1.3; `quack/session/checkpoint_render.py`, `quack/session/extraction.py`, `tests/session/test_checkpoint_law10.py`, `tests/session/test_extraction_v2.py`, `tests/graph/test_profile_fold.py`
- Контекст: `StoredEvent` Фазы 4 (`quack.events.log`) не имеет `event_id`, `turn_id`, `causation_id`; есть `id` (int), `causation_seq`, `source_refs`. Каталога Фазы 3 нет, а `turn.extracted` v2 в реестре Фазы 4 не зарегистрирован (у Фазы 4 — `observation.extracted`; перенос извлечения — задача B).
- Решение: импорт `StoredEvent` из `quack.events.log`. `checkpoint_render._turn_id` читал `payload.get("turn_id", event.turn_id)` — в конверте Фазы 4 `turn_id` нет, теперь только `payload.get("turn_id")` (оба тестовых журнала кладут `turn_id` в payload). Тесты строят событие по полям Фазы 4 (`id=seq`, `causation_seq=None`, `source_refs=[]`). В `test_extraction_v2` проверка `validate_payload("turn.extracted", …, 2)` заменена на `TurnExtractedV2.model_validate(payload.model_dump(mode="json"))`: каталога Фазы 3 нет, модель та же, строгость та же (`extra="forbid"`), `isinstance` избыточен.
- Почему: Д-§7.3 п.1 ТЗ Ф4; красная линия 5 (утверждения не ослаблены).
- Обратимость: полная.
- Тесты: `tests/session/test_checkpoint_law10.py`, `test_extraction_v2.py`, `tests/graph/test_profile_fold.py` — зелёные.
- Требует подтверждения владельцем: нет (ревью B: `_turn_id` и замена проверки `turn.extracted`)

### D-A-236. WP1.3 шаг 5 ч.2: `pg_engine` не нужен — `get_current_learner_id` на сессии `Db` (D-A-230 закрыта)
- Класс: A
- WP / место: WP1.3; `tests/test_neo4j_outage.py`, `tests/test_learner_stream.py`, `tests/test_api_llm_provider.py`, `tests/domains/conftest.py`, `tests/session/*`
- Контекст: D-A-230 оставила `pg_engine` в маршрутных тестах, пока `get_current_learner_id` читает пометку удаления Фазы 3. Сейчас он читает cookie-сессию `core.auth_sessions` через `Db` (`quack/api/auth.py`, WP18.3; удалённый ученик удаляется физически вместе с сессиями), пометки Фазы 3 нет.
- Решение: из `build_app_resources(...)`, `build_job_context(...)` и фикстур убраны `pg_engine`, `db` (SessionFactory), `session_factory=`, `engine=`; `test_api_llm_provider` без `usefixtures("pg_engine")` (пул `Db` API открывается без ожидания). Построители `tests/support/resources.py` принимают ровно `(settings, neo4j, redis, *, db=…)`, шим «снятых аргументов» убран; AST-проверка всех вызовов в `tests/` — ни одного лишнего аргумента. `tests/support/auth.py` не реэкспортирует `ensure_user`.
- Почему: WP1.3, T-DB3.
- Обратимость: полная.
- Тесты: `tests/session`, `tests/domains`, `tests/test_learner_stream.py`, `tests/test_neo4j_outage.py`, `tests/test_api_llm_provider.py`, `tests/test_app_assembly.py` — зелёные.
- Требует подтверждения владельцем: нет

### D-A-235. WP1.3 шаг 5 ч.2: фикстуры и помощники Фазы 3 в тестах сняты
- Класс: A
- WP / место: WP1.3, T-DB3; `tests/conftest.py`, `tests/support/phase3_pg.py`, `tests/support/phase3_events.py`, `tests/factories.py`, `tests/scenario.py`
- Контекст: после переноса тестов (D-A-227…232, PR C #254) фикстуры `pg_engine`, `db` и построители событий Фазы 3 (`EventLog`, `new_event`, `sample_payloads`) никто не запрашивает.
- Решение: `pytest_plugins = ["tests.db.fixtures"]`; `phase3_pg.py`, `phase3_events.py`, `factories.py` и `scenario.py` (история «из 36 типов» поверх `EventLog`, `build_scenario` нигде не вызывается) удалены. Имя базы `<test db>_p4` оставлено (его держат `tests/db/fixtures.py`, `tests/jobs/conftest.py`).
- Почему: §8.15 п.1; их единственный потребитель — снятый код.
- Обратимость: полная (git).
- Тесты: ссылок на снятые имена нет (AST-скан `imports.py` пуст).
- Требует подтверждения владельцем: нет

### D-A-234. WP1.3 шаг 5 ч.2: снят код Фазы 3 и зависимости SQLAlchemy, asyncpg, Alembic
- Класс: A
- WP / место: WP1.3, T-DB3; `quack/{graph,jobs,events,db,runtime}`, `quack/config.py`, `migrations/`, `alembic.ini`, `pyproject.toml`, `uv.lock`, `TESTS.md`
- Контекст: STATUS.md §5, поток A, п.1: после PR C с тестами (#254) потребителей Фазы 3 в коде не осталось, кроме перечисленных ниже.
- Решение: удалены `graph/{worker,projector,phase_a}.py`, `jobs/{dispatcher,dispatcher_rules,queue}.py`, `events/{append,reader,catalog,projections,models}.py`, `db/{postgres,locks}.py`, `migrations/` (Alembic), `alembic.ini`. `jobs/runner.py`: `JobRunner` снят, остались `JobContext` (без `engine` и `session_factory`), `JobHandler`, `JOB_HANDLERS`, `register_job_handler`; `ClaimedJob` — из `quack.jobs.types`. `supervise` перенесён в `runtime/phase4.py`. `runtime/__init__.py`: `create_job_context` и `close_job_context` без engine. `graph/overlay.py`: оставлены `SessionOverlay`, `TaskEvaluationView`, `HintView` (поля `SessionOverlay` на них ссылаются), `events` — `StoredEvent` Фазы 4; `apply_overlay_events` и `build_session_overlay` сняты, наложение на `rules@3` — отдельная задача (WP2.3/WP3.4), не сделано. `graph/slice.py`: `SliceDeps(redis, applied_seq_reader, snapshot_read_retries=3, *, db=None)` без `session_factory`, хвост журнала — только по `db` (без `db` и без `min_seq` — `RuntimeError`). `events/__init__.py` экспортирует `NewEvent`, `StoredEvent`, `append_events` из `events/log.py`; `jobs/__init__.py` — `ClaimedJob`, `JobSpec`, `JobDeferred`, `JobFailedPermanently` из `jobs/types.py`. `config.py`: снято только свойство `sqlalchemy_dsn` (зона C). Из `pyproject.toml` сняты `sqlalchemy`, `asyncpg`, `alembic` (и `migrations` из `pyright.include`), `uv lock` снял ещё `mako`. `TESTS.md`: строки B1, B2 убраны, абзац после таблицы B и категория P переписаны.
- Почему: STATUS.md §5 п.1; T-DB3; `Т-§0.4.4`.
- Обратимость: полная (git).
- Тесты: `uv run python imports.py` — пусто; `tests/{jobs,events,db,graph,session,domains,review,agents,practice,canvas,artifacts,materials,auth,storage,llm,e2e,embeddings}`, `tests/test_{contracts,route_registry,dispatcher,worker_and_ops,jobs_phase3,learner_stream,neo4j_outage,app_assembly,infra,session_overlay}.py` в контейнере K=7 — зелёные; два теста, которым нужен корень репозитория (`test_stand_dbmate`, `embeddings/test_modal_client`), в контейнере не проходят и до этого (см. `TESTS.md` §2), локально зелёные.
- Правки тестов (§8.15): тестовых функций в этом шаге не удалено (их снятие — D-A-227…232). Удалены помощники без потребителей:
  | Файл | Что удалено | Эквивалент Фазы 4 |
  | :-- | :-- | :-- |
  | `tests/support/phase3_pg.py` | `pg_engine`, `db`, `ensure_user`, `_migrate` (Alembic) | `pg_db`, `p4_db`, `create_learner` (`tests/db/fixtures.py`) |
  | `tests/support/phase3_events.py`, `tests/factories.py` | `EventLog`, `new_event`, `sample_payloads`, `uid` | `tests/events/samples.py::sample_payload`, `NewEvent` из `quack.events.log` |
  | `tests/scenario.py` | `build_scenario` (история на `EventLog`, не вызывалась) | `tests/graph/test_projection_batches.py` (скрипты событий `rules@3`) |
  Изменены без снятия утверждений: `tests/jobs/test_job_context.py` (поля `JobContext` без `engine`, `session_factory`; порядок закрытия без `engine`), `tests/graph/test_projection_runner.py`, `tests/test_session_overlay.py` (`SliceDeps` без `session_factory`), `tests/session/test_turn_source.py` (поле `session_factory` макета ресурсов).
- Требует подтверждения владельцем: нет

### D-A-233. WP3.4: ошибки бюджета обработчика в исполнителе Фазы 4 (шов S3)
- Класс: A
- WP / место: WP3.4, Т-§13.2; `quack/jobs/executor_p4.py`
- Контекст: `LLMBudgetExceededError` (`quack/llm/errors.py`) несёт `scope`; шов S3 требует разных исходов. `JobRunner` Фазы 3 маршрутизировал их, `executor_p4` — нет: обработчик, исчерпавший бюджет, шёл на обычные повторы и жёг попытки (найдено при переносе `test_budget_errors_route…`, D-A-232).
- Решение: `budget_outcome(exc, settings)` в `_finish`: `session` → `JobFailedPermanently` (бюджет сессии не восстанавливается); `daily` и `pool` → `JobDeferred` до ближайшей полуночи UTC плюс джиттер `daily_budget_defer_jitter_s`, попытка не тратится. `skipped_budget` в `claim_next` (D-A-222) остаётся для задач, ещё не начатых.
- Почему: шов S3, Т-§13.2, WP3.4, D-B-211 (режим `judges_only` — `pool`).
- Обратимость: полная.
- Тесты: `tests/jobs/test_executor_p4.py::test_budget_errors_route_to_defer_or_failure`.
- Требует подтверждения владельцем: нет (D-A-232 назвала это пробелом «для владельца»: решение принято по тексту шва)

### D-A-232. WP1.3 шаг 5, часть 2: `test_dispatcher.py` — на `DispatcherP4`; правила `session.digest_created` в Фазе 4 нет; два пробела, о которых решает владелец
- Класс: A
- WP / место: WP1.3, §8.15; `tests/test_dispatcher.py`
- Контекст: задание велело сохранить покрытие правила `session.digest_created → artifact.refresh_cheatsheet` «из `jobs/rules_p4.py`». Там его нет: вид `artifact.refresh_cheatsheet` лежит в `REMOVED_JOB_KINDS`, события `session.digest_created` в реестре Фазы 4 нет (есть `digest.written`). Правило заменяют `session.ended → artifact.topic_refresh` (Т-§17.7) и `digest.written → analyst.deep` (только при расхождении плана). Два пробела найдены при переносе: (а) `executor_p4` не переводит `LLMBudgetExceededError`, брошенный обработчиком, в «отложить до полуночи UTC» (дневной) и «провалить без повторов» (сессионный), как делал `JobRunner` Фазы 3 (WP3.4); в Фазе 4 бюджет сессии проверяет `claim_next` (`skipped_budget`), дневной — шлюз перед вызовом, а обработчик при исчерпании получит обычную ошибку и пойдёт на повторы с паузой; (б) `POST /v1/topics/{id}/cheatsheet/refresh` (зона C, `artifacts/cheatsheets.py`) всё ещё ставит задачу `artifact.refresh_cheatsheet` в очередь Фазы 3 через `session_factory`; в очереди Фазы 4 такого вида нет.
- Решение: `test_dispatcher.py` переписан на `Db`, `DispatcherP4` и события Фазы 4. Перенесены T-D2 (`offset_and_jobs_commit_together`: сбой постановки откатывает пачку, метка стоит, следующий проход повторяет), T-D5, T-D6 (сбой диспетчера не останавливает `ProjectorP4`; `supervise` берётся из `quack.runtime.phase4`, чтобы импорт пережил перенос функции), T-D7 (неизвестная версия схемы — пропуск с записью в лог), `lag`. Удалены: `test_dispatcher_turns_events_into_jobs`, `…_checkpoint_follows_last_checkpoint_turn`, `…_required_seq_is_trigger_seq` (виды `turn.extract`, `session.checkpoint`, `session.finish_artifacts`, `artifact.refresh_cheatsheet` сняты; строки таблицы Т-§13.3 — `test_dispatcher_p4.py`, `test_rules_p4.py`), `…_replay_is_idempotent` (T-D4: `test_dispatcher_p4.py::test_a_replay_of_the_log_adds_no_event_no_job_no_coverage`, `test_queue_p4.py::test_enqueue_is_idempotent_by_job_key`), `test_cheatsheet_refresh_endpoint_enqueues_directly` (T-D8: код Фазы 3 у C; маршрут держат `tests/artifacts/test_artifact_isolation.py`, `test_artifacts_search_export.py::test_cheatsheet_refresh_is_versioned_and_notifies`, `e2e/routes/c.py`). В коде пробелы (а), (б) не правились: (а) — решение владельца, нужно ли отложение по бюджету в Фазе 4 (`executor_p4._finish`, `Settings.daily_budget_defer_jitter_s`); (б) — передать C.
- Почему: §8.15 п.1, красная линия 5; Т-§13.3 (таблица правил); Т-§17.7.
- Обратимость: полная (git).
- Тесты: `tests/test_dispatcher.py` (5), `tests/jobs/test_rules_p4.py::test_digest_with_divergence_puts_analyst_deep`, `::test_artifact_topic_refresh_after_the_session_and_after_hints`, `test_dispatcher_p4.py::test_session_end_refreshes_the_topics_the_session_touched`.
- Требует подтверждения владельцем: да — пробел (а) (отложение по дневному бюджету)

### D-A-231. WP1.3 шаг 5, часть 2: тесты очереди и исполнителя Фазы 3 — на очередь Фазы 4
- Класс: A
- WP / место: WP1.3, §8.15; `tests/test_jobs_phase3.py`, `tests/test_jobs_and_llm_gateway.py`, `tests/jobs/test_executor_p4.py`
- Контекст: `JobRunner`, `quack.jobs.queue` (`enqueue_job`, `claim_next_job`, …) и `ops.jobs` Фазы 3 снимаются; их тесты нужно перенести либо удалить с названным эквивалентом.
- Решение: в `test_executor_p4.py` добавлены четыре теста на `JobWorker`/`run_claimed`: `test_a_deferred_job_yields_to_a_waiting_duplicate` (из `test_job_deferred_merges_into_queued_duplicate`), `test_pressure_flag_holds_back_the_kinds_of_that_model` и `test_without_redis_no_kind_is_held_back` (T-Q10, WP5.3), `test_a_stop_waits_for_the_grace_period_then_returns_the_rest_to_the_queue` (T-Q6, WP3.5); `JobDeferred` и `JobFailedPermanently` — из `quack.jobs.types`. В `test_jobs_phase3.py` оставлены T-Q7 (два теста; «любой вид» — `agent.viz` вместо снятого `artifact.refresh_cheatsheet`) и T-Q9 (теперь вызов `enqueue` очереди Фазы 4 тоже запрещён в продуктовом коде: задачи ставит диспетчер). В `test_jobs_and_llm_gateway.py` остались реестр подсказок и шлюз; `test_backoff_formula` — на `queue_p4.backoff_seconds` (шаг, разброс ±25 %, потолок). `tests/jobs/conftest.py`: снята фикстура `seeded_learner` (журнал Фазы 3), её никто не использовал.
- Почему: §8.15 п.1 и п.3, красная линия 5.
- Обратимость: полная (git).
- Тесты: перечислены выше.
- Правки тестов (§8.15):
  | Файл | Что удалено | Эквивалент Фазы 4 |
  | :-- | :-- | :-- |
  | `tests/test_jobs_phase3.py` | `test_claim_waits_for_projection` (T-Q1/Q2) | `test_queue_p4.py::test_job_waits_for_the_projector`, `::test_projector_of_another_learner_does_not_release_the_job`, `::test_job_that_reads_only_the_log_does_not_wait` |
  | | `test_coalesce_keeps_max_required_seq` | `test_queue_p4.py::test_coalesce_greatest`, `::test_coalesce_null_required_seq_stays_null` |
  | | `test_coalesced_cheatsheet_job_keeps_the_latest_session` | поведение иное: в Фазе 4 нагрузка склеенной задачи не меняется (`test_coalesce_greatest`, строка про `payload`) |
  | | `test_job_deferred_requeues_without_attempt` (T-Q3) | `test_queue_p4.py::test_defer_and_release_do_not_spend_the_attempt`, `test_executor_p4.py::test_permanent_failure_and_deferral` |
  | | `test_budget_errors_route_to_defer_or_dlq` (T-Q8) | `test_queue_p4.py::test_skipped_budget`, `::test_budget_is_per_session_and_inclusive_of_the_limit`, `::test_budget_follows_the_pool_mode` (бюджет сессии на выдаче); отложение по дневному бюджету — пробел (а) D-A-232 |
  | | `test_requeue_on_shutdown_only_by_the_owner` | `test_queue_p4.py::test_stale_worker_cannot_finish_a_job_it_lost` (`release` чужого — `None`) |
  | | `test_worker_graceful_shutdown_requeues_unfinished`, `test_pressure_flag_blocks_kinds_using_that_model`, `test_pressure_flags_without_redis_block_nothing` | перенесены на `JobWorker` (см. «Решение») |
  | `tests/test_jobs_and_llm_gateway.py` | `test_job_kind_catalog` (приоритеты видов Фазы 3) | `test_rules_p4.py::test_every_kind_of_the_spec_is_listed_once_and_removed_ones_are_gone`, `::test_rule_table`; период — `test_queue_p4.py::test_periodic_keys` |
  | | `test_coalesce_updates_queued_job_instead_of_duplicating`, `test_coalesce_only_while_queued` | `test_queue_p4.py::test_coalesce_greatest`, `::test_enqueue_is_idempotent_by_job_key` |
  | | `test_jobs_without_coalesce_key_are_not_merged` | `test_queue_p4.py::test_claim_orders_by_class_then_age` (задачи без `coalesce_key` — отдельные строки по `job_key`) |
  | | `test_enqueue_inside_append_transaction_rolls_back_together` | `tests/test_dispatcher.py::test_dispatcher_offset_and_jobs_commit_together` (D-A-232) |
  | | `test_lock_key_mutual_exclusion`, `test_concurrent_claims_never_run_same_lock_twice` | `test_queue_p4.py::test_one_running_job_per_lock_key`, `::test_concurrent_claims_respect_one_lock_key` |
  | | `test_priority_then_age_ordering`, `test_claim_filters_by_kind_and_run_after` | `test_queue_p4.py::test_claim_orders_by_class_then_age`, `::test_kinds_filter`, `::test_future_run_at_is_not_claimed` |
  | | `test_heartbeat_and_completion_require_owner` | `test_queue_p4.py::test_stale_worker_cannot_finish_a_job_it_lost` |
  | | `test_retry_with_backoff_then_dlq`, `test_stale_job_at_max_attempts_goes_to_dlq` (`ops.dlq` снят) | `test_queue_p4.py::test_failed_writes_agent_completed` (пауза растёт, затем `failed` и `agent.completed`), `::test_reaper_fails_a_job_out_of_attempts_and_cancels_a_stale_cancel` |
  | | `test_stale_running_jobs_are_reaped` | `test_queue_p4.py::test_fresh_pulse_is_not_reaped`, `::test_reaper_fails_a_job_out_of_attempts_and_cancels_a_stale_cancel` |
  | | `test_retry_of_coalesced_job_merges_with_newer_queued` | `test_queue_p4.py::test_requeue_of_a_coalescing_job_yields_to_a_waiting_one` |
- Требует подтверждения владельцем: нет

### D-A-230. WP1.3 шаг 5, часть 2: тесты инфраструктуры, ленты ученика, сбоя Neo4j и обработчиков — на `Db` Фазы 4
- Класс: A
- WP / место: WP1.3, §8.15; `tests/test_infra.py`, `test_learner_stream.py`, `test_neo4j_outage.py`, `test_worker_and_ops.py`, `tests/jobs/test_job_context.py`
- Контекст: в этих файлах оставались фикстуры `db` (`SessionFactory`), `ensure_user`, `quack.db.locks`, `ProjectorWorker`, `JobRunner`, `quack.events.append`.
- Решение: (1) `test_infra.py`: индексы-контракты и поиск по тексту проверяются на базе Фазы 4 (`pg_db`: `uq_jobs_running_lock`, `uq_jobs_queued_coalesce`, `idx_events_fts_en`, `idx_events_session_seq`, уникальность `(learner_id, idempotency_key)`; индексов `uq_sessions_one_active`, `uq_artifacts_topic_cheatsheet` в схеме Фазы 4 нет — не проверяются); два теста `advisory_lock` Фазы 3 — на `Db.advisory_lock` (прямого теста у него не было); `/health/ready` проверяется на базе Фазы 4 (`p4_database`); из `test_settings_require_secrets_in_prod` убраны `sqlalchemy_dsn` и четыре порога Фазы 3 (`llm_semaphore_limit`, `daily_token_limit_per_learner`, `session_token_limit`, `turn_lock_ttl_sec`), которых никто не читает. Оставлены: схема Neo4j, клиент вычислений, формат ошибок, идентификатор запроса, SSE, CLI, журнал. (2) `test_learner_stream.py`, `test_neo4j_outage.py`: убраны `db`, `session_factory`, `ensure_user` и помощники журнала Фазы 3 (`_append`, `_learner`). `AppResources.engine` по-прежнему строится из `pg_engine` (так же в `test_infra.py::test_api_error_format_and_request_id`): `get_current_learner_id` (`quack/api/auth.py`, зона C) читает пометку удаления Фазы 3 через `session_factory`, а `/health/*` — через `engine`; это снимется, когда C переведёт `api/auth.py` и `app.py` на `Db` (WP25). (3) `test_worker_and_ops.py`: тесты `ProjectorWorker` и `JobRunner` удалены; остались реестр `register_job_handler` и переходник `bind_handlers` (теперь и на повтор после отказа, и на пропуск вида без обработчика), добавлен `test_a_job_of_the_new_queue_in_the_shape_the_handlers_read` (`as_phase3_job`). (4) `test_job_context.py` оставлен без правок: `JobContext` ещё в `quack/jobs/runner.py` вместе с полями `engine` и `session_factory`; при переносе и снятии полей правятся строка импорта и `WORKERS_FIELDS`.
- Почему: §8.15, красная линия 5, WP1.3 шаг 5.
- Обратимость: полная (git).
- Тесты: перечислены в решении.
- Правки тестов (§8.15):
  | Файл | Что удалено | Эквивалент Фазы 4 |
  | :-- | :-- | :-- |
  | `tests/test_worker_and_ops.py` | `test_worker_projects_log_into_neo4j` (`ProjectorWorker`, снимок `Neo4jGraphStore`) | `graph/test_projection_runner.py::test_batches_of_any_size_give_one_graph`, `::test_materials_and_vectors_reach_neo4j`, `::test_changed_topics_are_published_after_commit`, `::test_projector_process_walks_the_lagging_learners`; свёртка в памяти — `graph/test_projection_batches.py` |
  | | `test_poison_event_stops_learner_until_replay` | `graph/test_projection_runner.py::test_unknown_type_stops_stream` (T-P6: строка в `ops.projector_failures`, остальные ученики идут, `resume` снимает остановку) |
  | | `test_job_runner_executes_and_retries` | `jobs/test_executor_p4.py::test_worker_runs_only_kinds_with_a_handler_and_admission`, `::test_a_failing_handler_is_retried_then_failed_with_the_event`; через переходник — `test_bound_handlers_run_on_the_worker_and_a_failure_is_retried` |
  | `tests/test_infra.py` | убраны `sqlalchemy_dsn` и четыре порога Фазы 3 в `test_settings_require_secrets_in_prod`; индексы `uq_sessions_one_active`, `uq_artifacts_topic_cheatsheet` | схема Фазы 4: `db/test_baseline_migration.py` (состав таблиц); таких индексов в ней нет |
### D-A-229. WP1.3 шаг 5, ч.2: закон 4 и READ-сессии — на Фазу 4
- Класс: A
- WP / место: WP1.3 шаг 5; `tests/graph/test_evidence_law4.py`, `tests/test_neo4j_read_session.py`
- Контекст: оба файла держали импорты Фазы 3 (`events.catalog`, `graph.projector`, `graph.neo4j_store`, `tests.factories`). `evidence_validation.to_extracted_element`, `payloads.ExtractedElement`, `session.schemas.ExtractorOutputV2` живы (`session/extraction.py` — зона B), поэтому их тесты остаются.
- Решение: `test_evidence_law4.py`: остаются `T-W1` (две схемы `extractor@2`) и `T-W4` (проверки `to_extracted_element`); добавлены `T-W1` для `ExtractorOutputV3` (`test_extractor_v3_output_schema_has_no_weights`; ТЗ Ф4 расширяет `T-W1` на схемы Фазы 4) и `test_skill_ref_requires_exactly_one_form` (из тестов каталога Фазы 3); `T-W3` перенесён на свёртку `rules@3` (`test_evidence_weight_formula` — те же десять строк таблицы, `test_evidence_weight_changes_only_through_settings` — те же пять подмен настроек, через `FoldParams.from_settings`). `test_neo4j_read_session.py`: два теста `read_session` без изменений; `test_graph_store_reads_through_read_session` (`Neo4jGraphStore._read`) заменён на `test_batch_reads_of_the_projector_open_read_sessions` — шесть чтений `graph/projection/store.py` открывают READ-сессии (драйвер-«шпион» записывает режим доступа).
- Почему: §8.15 п.1–3, красная линия 5 (утверждения не слабее), WP1.3 шаг 5 ч.2.
- Обратимость: полная (git).
- Тесты: сами.
- Правки тестов (§8.15):
  | Что | Куда ушло |
  | :-- | :-- |
  | `test_turn_extracted_v2_is_in_the_catalog` | каталог Фазы 4 — `events/test_catalog.py::test_catalog_matches_spec` (`observation.extracted@1`) |
  | `test_tier_assignment_by_source` (`T-W2`, `assign_tier`) | `graph/test_projection_rules3.py::test_tier_by_verification_and_second_opinion`, `::test_card_self_assessment_is_t4_evidence_of_level_1`, `::test_observation_extracted_gives_t3_evidence_and_supersedes_the_earlier_one` (T1 код, T2/T3 модель, T3 экстрактор, T4 карточка) |
  | `test_rejected_elements_create_no_evidence` (`T-W4`, проектор) | отвергнутые элементы лежат в отдельном поле `observation.extracted.rejected` (`events/test_rejected_element.py`), обработчик читает только `elements` (`test_projection_rules3.py::test_observation_extracted_gives_t3_evidence…`) |
  | `test_evidence_time_from_source_event` (`T-W5`) | там же: `atMs`/`sourceSeq` берутся из `at_ms`/`events` элемента (проверка `atMs=1000, sourceSeq=7`) |
  | `test_v1_and_v2_extracted_events_both_apply` (`T-W6`) | в Фазе 4 одна версия схем (`SCHEMA_VERSION == 1`); неизвестная версия — ошибка: `test_projection_rules3.py::test_unknown_type_or_newer_schema_is_an_error`, `events/test_catalog.py::test_unknown_type_or_version_is_rejected` |
  | `test_rules2_rebuild_equals_incremental` (`T-W7`) | `T-P1`: `graph/test_projection_batches.py::test_batches_equal_rebuild_hypothesis`, `test_projector_properties.py`, `graph/test_projection_runner.py::test_batches_of_any_size_give_one_graph`, `::test_rules3_stale_rebuild` |
  | `test_model_strength_does_not_change_the_state` | модель в Фазе 4 не задаёт силу вовсе: `T-W1` для `ExtractorOutputV3` и `T-W3` на свёртке |
  | `test_v2_hint_penalty_is_counted_once` | `test_evidence_weight_formula` (строки `hint_step`), `graph/test_skill_fold.py::test_weights_and_novelty_in_fold` |
  | `test_graph_store_reads_through_read_session` | `test_batch_reads_of_the_projector_open_read_sessions` |
- Требует подтверждения владельцем: нет

### D-A-228. WP1.3 шаг 5, ч.2: тесты проектора, наложения и `read_consistent_slice`
- Класс: A
- WP / место: WP1.3 шаг 5; `tests/test_projector_determinism.py`, `tests/test_projector_properties.py`, `tests/test_session_overlay.py`, `tests/graph/test_read_neo4j_core.py`
- Контекст: файлы держали `graph.projector`, `phase_a`, `neo4j_store`, `overlay.build_session_overlay`, фикстуру `db` и сценарий из 36 типов. `SessionOverlay` (датакласс) остаётся: его принимает читатель (`graph/read`) и строят `session/turn_source.py`, `session/replay.py`. `build_session_overlay` и `apply_overlay_events` не вызывает никто в `quack/` (проверено `grep`): `turn_source` собирает пустое наложение, а перевод наложения на обработчики `rules@3` (ТЗ Ф4 WP2.3, `Т-§2.6`) ещё не сделан.
- Решение: (1) `test_read_neo4j_core.py` — без изменений (`SessionOverlay` — датакласс, остаётся). (2) `test_projector_determinism.py` — убраны тесты проектора и `Neo4jGraphStore` (эквиваленты ниже); остались тесты `InMemoryGraph`, мутаций и компилятора Cypher (модули живы: читатель и сидирование тестов читателя), добавлены `test_deterministic_id_is_the_uuid5_of_learner_seq_and_item` и `test_superseded_evidence_is_as_if_it_never_existed` (фаза B `rules@3`: вытесненное свидетельство не входит в свёртку навыка и заблуждения; активное меняет оба). (3) `test_projector_properties.py` — то же свойство на `rules@3`: случайная часть сценария `graph/test_projection_batches.py` (`seq` сохраняются — события ссылаются друг на друга по `seq`) в пачках случайных границ равна применению разом; сильнее `T-P1`, который режет только целый сценарий. (4) `test_session_overlay.py` — остаются шесть тестов `read_consistent_slice` (без `session_factory`); тест «`min_seq=None` — хвост журнала» заменён существующим `graph/test_projection_runner.py::test_slice_waits_for_the_journal_of_phase_4`; два теста наложения сняты без замены — кода Фазы 4, который строит наложение из журнала, нет. Когда он появится (WP2.3, наложение на новые обработчики), нужны его тесты: свежие события сессии поверх отстающей проекции, пустое наложение при догнавшем проекторе, отсутствие записи в базовый срез.
- Почему: §8.15 п.1–3; красная линия 5; D-A-212 («`SessionOverlay` — нужна `Db`-версия»).
- Обратимость: полная (git); два снятых теста наложения возвращаются из git вместе с новой функцией.
- Тесты: сами.
- Правки тестов (§8.15):
  | Что | Куда ушло |
  | :-- | :-- |
  | `test_scenario_covers_all_36_event_types` | `graph/test_projection_batches.py::test_script_reaches_every_phase`, `events/test_catalog.py::test_samples_cover_catalog` |
  | `test_batch_size_and_rebuild_equivalence`, `test_merged_skill_evidence_refreshes_every_topic_of_the_target`, `test_reapplying_already_applied_events_changes_nothing`, `test_projection_is_reproducible_across_runs`, `test_neo4j_merged_skill_evidence_refreshes_every_topic`, `test_neo4j_projection_equals_in_memory` | `T-P1`: `graph/test_projection_batches.py` (`test_batches_equal_rebuild_hypothesis`, `test_applying_a_batch_again_changes_nothing`), `graph/test_projection_runner.py::test_batches_of_any_size_give_one_graph` |
  | `test_deterministic_uuid5_ids` | `test_deterministic_id_is_the_uuid5_of_learner_seq_and_item` и ожидаемые id в `graph/test_projection_rules3.py` |
  | `test_inline_provisional_skills_reuse_by_normalized_name` | `graph/test_projection_rules3.py::test_a_description_with_the_name_of_a_skill_of_the_domain_is_that_skill`, `::test_task_created_with_markup_options_and_new_skills` |
  | `test_alias_resolution_after_merges`, `test_skill_merge_moves_history_into_target_fold` | `T-P5`: `test_alias_and_empty_apply`, `test_skill_merged_moves_relations_and_leaves_an_alias`, `test_topic_merged_moves_skills_and_hub_membership`, `test_misconception_merged_moves_manifestations_and_traps`, `test_edge_between_merged_skills_is_not_a_loop` |
  | `test_superseded_evidence_equals_history_without_it` | `test_superseded_evidence_is_as_if_it_never_existed` (фаза B) |
  | `test_topic_aggregates_present_after_projection` | `graph/test_projection_batches.py::test_no_missing_properties` (`T-P2`), `graph/test_skill_fold.py::test_topic_status_visibility` |
  | `test_rules_guard_skips_learner_until_rebuild`, `test_neo4j_rules_guard` | `graph/test_projection_runner.py::test_rules3_stale_rebuild` (`T-P8`) |
  | `test_analyze_batch_collects_needed_ids` | `graph/test_projection_rules3.py::test_needs_of_a_batch_join`, `::test_handler_reads_only_the_events_it_needs` |
  | `test_overlay_shows_fresh_session_events_over_lagging_projection`, `test_overlay_is_empty_when_projection_caught_up` | **эквивалента нет** (см. «Решение», п. 4); чтение наложения читателем — `graph/test_read_neo4j_core.py::test_overlay_patches_state_while_the_projector_lags` |
  | `test_slice_uses_last_seq_when_min_seq_is_none` | `graph/test_projection_runner.py::test_slice_waits_for_the_journal_of_phase_4` |
- Требует подтверждения владельцем: нет

### D-A-227. WP1.3 шаг 5, ч.2: тесты журнала событий на Фазу 4
- Класс: A
- WP / место: WP1.3 шаг 5; `tests/test_events_and_append.py` (снят), `tests/events/test_catalog.py`
- Контекст: `test_events_and_append.py` проверял каталог из 36 типов Фазы 3, `append_events` на SQLAlchemy и проекции в `core.domains/sessions/task_bank/artifacts` (`events/projections.py`). Каталога, функции и таблиц в Фазе 4 нет.
- Решение: файл снят целиком. Правила, общие для любого каталога, перенесены в `events/test_catalog.py` без ослабления: `test_type_names_follow_the_naming_rule` (шаблон имени), `test_unknown_type_or_version_is_rejected`, `test_bounded_fields_are_checked` (отрезок 0..1 у `edge.added.confidence`, ступень 1..3 у `hint.shown`), `test_valid_actors`/`test_invalid_actors` (все значения Фазы 3 плюс `agent:<вид>`), `test_payload_hash_is_canonical_and_order_independent`. В `test_catalog_matches_spec` перечень типов Фазы 1–3 вместо импорта `quack.events.catalog` — литерал `REMOVED_PHASE_1_3_TYPES` (31 имя; то же утверждение).
- Почему: §8.15 п.1–3, красная линия 5.
- Обратимость: полная (git).
- Тесты: сами.
- Правки тестов (§8.15):
  | Что | Куда ушло |
  | :-- | :-- |
  | `test_catalog_has_exactly_36_event_types` | `events/test_catalog.py::test_catalog_matches_spec` (каталог Фазы 4) и `::test_type_names_follow_the_naming_rule` |
  | `test_sample_payloads_cover_whole_catalog`, `test_every_event_type_validates_its_payload`, `…rejects_unknown_fields`, `…rejects_missing_required_field`, `test_text_events_use_text_field` | `events/test_catalog.py::test_samples_cover_catalog`, `::test_invalid_payload_rejected` (пример валиден, лишнее поле и каждое обязательное поле отвергаются), `::test_text_types_have_text_and_match_index` |
  | `test_unknown_event_type_is_rejected` | `::test_unknown_type_or_version_is_rejected`, `db/test_append_events.py::test_unknown_type_and_duplicate_keys_are_rejected` |
  | `test_skill_ref_requires_exactly_one_form` | `graph/test_evidence_law4.py` (`SkillRef` жив, пока жив `to_extracted_element`) |
  | `test_unit_interval_fields_are_bounded` | `::test_bounded_fields_are_checked` |
  | `test_valid_actors`, `test_invalid_actors` | `::test_valid_actors`, `::test_invalid_actors`; `db/test_append_events.py::test_actor_versions_and_agents` |
  | `test_payload_hash_is_canonical_and_order_independent` | `::test_payload_hash_is_canonical_and_order_independent` |
  | `test_append_assigns_monotonic_gapless_seq`, `…stores_envelope_and_hash`, `test_idempotent_retry_returns_existing_event`, `test_idempotency_conflict_on_changed_payload`, `test_mixed_batch_returns_existing_and_appends_new`, `test_invalid_payload_writes_nothing`, `test_event_types_seen_recorded`, `test_concurrent_appends_for_one_learner_are_serialized` | `db/test_append_events.py` (одноимённые, `T-DB5`), `events/test_catalog.py::test_invalid_payload_is_not_written`, `::test_derived_key_retry_returns_same_event` |
  | `test_wakeup_is_published_after_commit`, `test_wakeup_not_published_on_rollback` | `db/test_append_events.py::test_signal_is_published_after_commit`, `::test_signal_not_published_on_rollback` |
  | `test_domain_projection_follows_events`, `test_session_projection_follows_events`, `test_task_bank_projection`, `test_artifact_projections` | проекции `core.*` Фазы 3 заменены графом: `graph/test_projection_rules3.py` (`test_domain_created_makes_the_domain…`, `test_task_created_with_markup_options_and_new_skills`, `test_digest_written_replaces_the_digest_of_the_domain`), строки сессий — `session/test_sessions_db.py` |
  | `test_only_one_active_session_per_learner` | правило снято: в Фазе 4 новая сессия не закрывает открытую — `session/test_sessions_db.py::test_a_new_session_does_not_end_the_open_one` |
- Требует подтверждения владельцем: нет

### D-A-226. WP8.2: `split_topic` и `topic_split` — правка v1 перечней; задача `agent.topic_split`; порядок пачки (по #245 B)
- Класс: C (схема события после G1, красная линия 2 — по прецеденту D-A-196/D-A-121: значения перечней v1, событий Фазы 4 в проде нет)
- WP / место: WP8.2 «Разделить (фразой в чат)»; `quack/events/records.py` (`TurnIntent`, `AgentKind`), `quack/jobs/rules_p4.py`
- Контекст: строка контракта WP8.2 требует: учитель размечает намерение → сверка предлагает распределение → `topic.split`. В перечнях `TurnIntent` и `AgentKind` для этого значений нет; B не может писать ход с таким намерением (`TurnHeadRecord` отвергает весь ход).
- Решение: `TurnIntent` += `split_topic`; `AgentKind` += `topic_split`; `JOB_KIND_SPECS` += `agent.topic_split` (P2, видимая, роль `builder`), `AGENT_JOB_KINDS["topic_split"] = "agent.topic_split"`. Правило `agent.requested`: без `params.topic_id` задач нет; иначе граф-задача под замком `domain:{domain_id}` с `topic_id` в нагрузке. Версия схем остаётся 1. Порядок событий в одной пачке для темы с одним навыком: сначала `topic.split` (создаёт части), затем `skill.split` (его части ложатся в созданные темы); обратный порядок теряет часть (`_skill_split` пропускает часть, чьей темы нет) — тест это фиксирует.
- Почему: ТЗ Ф4 WP8.2, §6.3 (`topic.split`, `skill.split`), D-A-131, D-A-196; патч B из #245.
- Обратимость: полная до первых событий в проде (значения перечней можно убрать).
- Тесты: `tests/jobs/test_rules_p4.py::test_topic_split_request_is_a_graph_job_of_the_domain`, `tests/graph/test_projection_rules3.py::test_split_of_a_one_skill_topic_in_one_batch_needs_the_topics_first`; счётчик видов в `test_every_kind_of_the_spec…` 24 → 25 (+5 периодических).
- Требует подтверждения владельцем: да — **подтверждено владельцем 2026-11-04** (чат сессии; отмечено в #245)

### D-A-225. WP1.3 шаг 5, часть 1: что из кода Фазы 3 остаётся до части 2
- Класс: A
- WP / место: WP1.3, §7.3, §8.15; `quack/graph/worker.py`, `quack/jobs/dispatcher.py`, `quack/jobs/dispatcher_rules.py`, `quack/graph/projector.py`
- Контекст: задание шага 5 велит удалять модули, которые «никто не импортирует». Проверка `grep quack.<модуль> quack tests` показала, что проектор и диспетчер Фазы 3 ещё держат тесты зоны C: `tests/artifacts/conftest.py` (`Seeder.project` — `ProjectorWorker`), `tests/support/dispatch.py` → `tests/artifacts/test_artifacts_search_export.py` (`dispatch`: правило `session.digest_created → artifact.refresh_cheatsheet` живёт только в `dispatcher_rules.py`; в правилах Фазы 4 такого вида нет) и стенд `tests/e2e/system.py`. Перевести их — значит менять утверждения тестов артефактов C (красная линия 5), это шаг 3 C.
- Решение: остаются до части 2 (после шага 3 C): `graph/worker.py`, `jobs/dispatcher.py` (`supervise` берёт `runtime/phase4.py`), `jobs/dispatcher_rules.py`, `graph/projector.py` (`project_events` нужен `graph/overlay.py`; `run_projector_batch`/`project_in_memory` — тестам), `graph/projector_rows.py` (таблица `ROWS` нужна тестам `rules@3`), `test_dispatcher.py`. Из `dispatcher_rules.py` убрано правило `graph.curate` (вида нет). Стенд e2e по-прежнему запускает проектор, диспетчер и `JobRunner` Фазы 3 на базе Фазы 3 (тесты `tests/e2e` зелёные без перевода на процессы Фазы 4); снят только планировщик. Модули списка «не удалять» после п.1–2 имеют импортёров в `quack/`, ни один не осиротел.
- Почему: §7.3 (чужие тесты правит их владелец), красная линия 5, §8.15 п.1 (код снимается вместе с последним потребителем).
- Обратимость: полная (перечень).
- Тесты: нет новых.
- Требует подтверждения владельцем: нет

### D-A-224. WP1.3 шаг 5, часть 1: код Фазы 3 снят — планировщик, пересборка, `graph.curate`, `account.purge_deleted`, `ops/registry.py`
- Класс: A
- WP / место: WP1.3, §8.15 п.1; D-A-208, D-A-212, D-A-206; файлы ниже
- Контекст: C согласился (#215), что в шаге 5 A снимает `ops/purge_job.py`, `ops/registry.py`, `account.purge_deleted`, код Фазы 3 зоны A и ветки флага в `cli.py`. `DELETE /v1/learner` удаляет сразу (D-A-207), тело `purge_deleted` не нужно.
- Решение: удалены `quack/jobs/periodic.py`, `quack/graph/rebuild.py`, `quack/jobs/curation_p3.py`, `quack/domains/curation.py`, `quack/ops/purge_job.py`, `quack/ops/registry.py`. Из `quack/jobs/kinds.py` — `graph_curate`, `account_purge_deleted`, виды `graph.curate` и `account.purge_deleted` в `JOB_KINDS` и `KIND_LLM_ROLES`; из `HANDLER_MODULES` — две строки; из `Settings` — `purge_deleted_interval_s`; `REMOVED_JOB_KINDS` в `rules_p4.py` остаётся (это список снятых видов). Схемы `CuratorPromotion`, `CuratorMerge`, `CuratorOutput` переехали из `curation_p3.py` в конец `quack/domains/schemas.py`: их используют тесты и скрипт B (`prompt_contracts`, `test_llm_runtime`, `meta_smoke`) до снятия роли `curator` (D-A-208 п.2). Реестр событий `cli.py` берёт из `quack.runtime.event_registry` (с #237). `ProjectorContext`, `create_projector_context`, `close_projector_context` в `runtime/__init__.py` удалены (никто не вызывает).
- Почему: §8.15 п.1, WP1.3 шаг 5, согласие C в #215.
- Обратимость: полная (git).
- Тесты: `tests/jobs/test_queue_p4.py`, `test_rules_p4.py` (виды `graph.curate`/`account.purge_deleted` в `REMOVED_JOB_KINDS`), `test_ops_p4_registry.py`.
- Правки тестов (§8.15):
  | Файл | Что | Куда ушло |
  | :-- | :-- | :-- |
  | `tests/jobs/test_curation_p3_{graph,keys_and_actor,slice_and_pool,thresholds}.py` (4 файла) | удалены вместе с обработчиком `graph.curate` Фазы 3 | гигиена графа — `agents/test_hygiene.py` (`T-AG4`) |
  | `tests/fixtures/event_registry.json` | удалён | реестр Фазы 4 — `test_ops_p4_registry.py` |
  | `tests/test_ops_phase3.py` | удалены `test_event_registry_and_can_rollback_of_phase3`, `test_event_registry_has_handlers_and_rules` (библиотека `ops/registry.py`); `graph_curate` → `artifact_refresh_cheatsheet` в `test_ops_status_report` | `test_ops_p4_registry.py` (`can-rollback`), новый `test_every_catalog_type_has_a_handler_and_a_rule` (`registry_gaps` Фазы 4, T-O6) |
  | `tests/test_jobs_phase3.py` | удалены `test_periodic_scheduler_runs_once_per_bucket`, `test_purge_job_removes_learner_completely`; `graph.curate`/`account.purge_deleted` как «любой вид» очереди заменены на `artifact.refresh_cheatsheet` (утверждения те же) | периодика Фазы 4 — `jobs/test_queue_p4.py::test_periodic_keys`; удаление аккаунта — `domains/test_domains_routes.py` (T-E10) |
  | `tests/test_worker_and_ops.py` | удалены `test_rules_mismatch_skips_until_rebuild`, `test_chunked_rebuild_respects_presence` (`graph/rebuild.py`) | `graph/test_projection_runner.py::test_rules3_stale_rebuild`, новый `::test_chunked_rebuild_respects_presence` на `rebuild_learner` Фазы 4 |
  | `tests/test_dispatcher.py` | из `_expected`, `required_seq` и пропуска неизвестного события убран `graph.curate` | — |
  | `tests/test_jobs_and_llm_gateway.py` | из `test_job_kind_catalog` убран блок `graph_curate`; в `test_priority_then_age_ordering` низкий приоритет — `session.sweep_inactive` | — |
  | `tests/test_contracts.py` | из `NEO4J_WRITERS` убран `quack.graph.rebuild` | — |
  | `tests/test_ops_p4_registry.py` (зона C, правка с её согласия в #215: `cli.py`) | флаг снят: `_settings(phase4_processes=True)` → `_settings()`, удалён `test_rebuild_without_the_flag_stays_on_phase3`, подмена `rebuild_learner` Фазы 3 | — |
  | `tests/e2e/system.py` (зона C) | из стенда снят `PeriodicScheduler` вместе с `SystemClock`, `System.clock`, `advance_time` (никто не вызывает после T-E10) | — |
- Требует подтверждения владельцем: нет

### D-A-223. WP1.3 шаг 5, часть 1: флаг `phase4_processes` снят — процессы всегда на Фазе 4
- Класс: A
- WP / место: WP1.3 шаг 5; D-A-200, D-A-206, D-C-160; `quack/config.py`, `quack/cli.py`, `quack/runtime/__init__.py`
- Контекст: стенд и compose уже на dbmate и `PHASE4_PROCESSES=true` (C, #239); C согласился (#215), что `cli.py` меняет A.
- Решение: `Settings.phase4_processes` удалена. `projector`/`workers`/`worker` всегда зовут `runtime.phase4.projector_process`/`workers_process`; `ops rebuild` всегда на `graph.projection.rebuild.rebuild_learner` (`_rebuild_p4` стала `_rebuild`); ветки Фазы 3 и импорты `ProjectorWorker`, `Dispatcher`, `PeriodicScheduler`, `JobRunner`, `graph.rebuild`, `find_learners_with_rules` из `cli.py` убраны. `create_job_context` всегда передаёт `db=` шлюзу (одна схема — одна таблица учёта `ops.llm_usage`). Переменная `PHASE4_PROCESSES` в compose теперь ничего не читает (`Settings` — `extra="ignore"`): её, README и проверку `tests/test_stand_dbmate.py` снимает C.
- Почему: D-A-200 («флаг уходит с остатком WP1.3»).
- Обратимость: полная.
- Тесты: `tests/test_ops_p4_registry.py` (`ops rebuild`), `tests/graph/test_projection_runner.py` (процесс `projector`).
- Требует подтверждения владельцем: нет

### D-A-222. WP7.4: `skipped_budget` по лимиту режима пула (D-B-211, #236)
- Класс: A
- WP / место: WP7.4 (T-D7); `quack/jobs/queue_p4.py::_over_budget`
- Контекст: решение владельца D-B-211 (#234): лимит сессии зависит от остатка пула моделей — `normal` → `session_agent_budget_usd`, `tight` → `tight_session_agent_budget_usd`. Очередь снимала задачи только по обычному лимиту.
- Решение: `_over_budget` берёт `budgets_for(settings, await access_mode(db, settings)).session_usd` (`quack.llm.access`, ядро; сумма пула кэшируется на `llm_pool_cache_s`). Без пула (`llm_pool_budget_usd = 0`) — режим `normal`, поведение прежнее. `judges_only` — тот же лимит, что `tight` (`budgets_for`); запрет вызовов не-судьям держит шлюз.
- Почему: D-B-211, `Т-§13.2`, WP7.4.
- Обратимость: полная.
- Тесты: `tests/jobs/test_queue_p4.py::test_budget_follows_the_pool_mode`; T-D7 без изменений.
- Требует подтверждения владельцем: нет (решение владельца D-B-211)

### D-A-221. WP7.5: веса видов свидетельств — предложения нет, пока в документах нет правила пересчёта
- Класс: A
- WP / место: WP7.5, `calibration.weekly`; `quack/jobs/calibration_p4.py`
- Контекст: Т-§18.5 велит пересчитывать «веса видов свидетельств (§7.4) и вероятности §18.2»; Т-§7.11: «вес вида, который систематически не подтверждается, снижается в конфиге». Порога, шага снижения и способа отнести исход попытки к виду свидетельства (у попытки нет «своего» вида — состояние складывается из многих свидетельств) в документах нет.
- Решение: задача предлагает только `p_*`; в журнале `evidence_weights: null` с причиной. Правило пересчёта весов не выдумывается: его даст владелец вместе с решением по Т-§7.11 (калибровка по эталону).
- Почему: Т-§7.4, Т-§7.11, Т-§18.5; закон 8 (не выдумывать)
- Обратимость: полная; поле `evidence_weights` в предложении уже есть
- Тесты: `tests/jobs/test_calibration_p4.py`
- Требует подтверждения владельцем: да (нужно правило пересчёта весов)

### D-A-220. WP7.5: вероятность по правилу Т-§18.2, оценка Брайера и предложение `p_*`
- Класс: A
- WP / место: WP7.5; `quack/jobs/calibration_p4.py`
- Контекст: ТЗ: «предложение новых весов в журнал/лог». Как считать предложение для правил `p × R(сейчас)`, в документах нет.
- Решение: попытка Т1 — вердикт `verification = code`, исход correct/incorrect (как у метрики `next_attempt_brier`, D-B-194). Состояние — `state_at(answer_seq − 1)`; каждая пара (навык, уровень) задачи — одно наблюдение одного правила: proven+direct → `p_proven × R`, proven+transfer и implied → `p_derived × R`, unstable, failed, unknown (нет уровня или not_tested) — константы. Если нужной `R` нет — наблюдение пропускается и считается. По каждому правилу: оценка Брайера, кривая (корзины `round(p, 1)`: доля успехов и число), доля успехов и **предложение** `p = Σ исход / Σ R` (для константы `R = 1` — просто доля успехов), в [0, 1]; меньше `calibration_min_reports` наблюдений — предложения нет (`null`). Окно — ISO-неделя перед периодом задачи (`calibration:2026-46` → 2026-11-02…11-09 UTC), детерминированно при повторе; не больше `metrics_brier_max_attempts` последних попыток. Обработчик пишет `log.info("calibration_proposal", …)`; `Settings`, база, события не меняются (красная линия 2: нового типа события нет).
- Почему: Т-§18.2, Т-§18.5, ТЗ Ф4 WP7.5
- Обратимость: полная
- Тесты: `tests/jobs/test_calibration_p4.py` (два ученика, ручной счёт, ничего не записано)
- Требует подтверждения владельцем: нет

### D-A-219. WP7.5: вид `calibration.weekly` в `JOB_KINDS`, SQL по всем ученикам
- Класс: A
- WP / место: WP7.5; `quack/jobs/kinds.py`, `quack/runtime/__init__.py` (`HANDLER_MODULES`), `tests/db/test_sql_static.py`
- Контекст: ключ периода `calibration:{год-неделя}` уже планирует `periodic_p4`; вида в `kinds.JOB_KINDS` и обработчика не было (проверка полноты обработчиков при старте `workers`, T-Q7).
- Решение: `calibration.weekly` добавлен в `JOB_KINDS` и `KIND_LLM_ROLES` (ролей моделей нет) рядом с `exports.cleanup`; обработчик — в новом модуле `quack/jobs/calibration_p4.py`, он в `HANDLER_MODULES`. Выборка попыток всех учеников `_ATTEMPTS` — в `CROSS_LEARNER_ALLOWED` («еженедельная калибровка по всем ученикам»).
- Почему: Т-§13.1, Т-§18.5
- Обратимость: полная
- Тесты: `tests/db/test_sql_static.py`, `tests/test_jobs_phase3.py`
- Требует подтверждения владельцем: нет

### D-A-218. WP2.4: реестр событий образа Фазы 4 — `quack/runtime/event_registry.py`
- Класс: A
- WP / место: WP2.4 (хвост), WP1.3; `quack/runtime/event_registry.py`
- Контекст: `/app/event_registry.json`, `ops event-registry` и проверка отката (`Д-§6.3`) собираются из каталога и rules@2 Фазы 3 (`quack/ops/registry.py`, зона C, на SQLAlchemy). Шаг CI «реестр событий» (`Д-§5.2` п.6) делает C в WP9.3 — ему нужна функция Фазы 4. Как диспетчер «обслуживает» тип с отметкой в колонке «Диспетчер» §6.3, документ не перечисляет по механизмам.
- Решение: тело ops-команд (§7.3, пересечение 5) — в `quack/runtime/event_registry.py` (слой сборки: видит и проектор, и диспетчер без циклов): `event_registry()` (пары каталога Фазы 4 + `rules@3`), `write_event_registry`, `seen_pairs(db)`, `can_rollback`, `registry_gaps()`. Колонка «Диспетчер» закрыта, если тип есть в строках WP7.1, в эпизодах (`EPISODE_TYPES`, «эпизод»), в фактах структуры (`STRUCTURE_TYPES`) или в маске `*` эмбеддингов (`EMBEDDED_TYPES` по §6.3 «эмбеддинг»: `topic.proposed`, `topic.created`, `skill.created`, `material.parsed`). Регистрацию в `cli.py`/`ops` и шаг CI делает C (#232).
- Почему: `Д-§5.1`, `Д-§5.2` п.6, `Д-§6.3`, ТЗ §6.3, §7.3 п.5, WP2.4.
- Обратимость: полная; `EMBEDDED_TYPES` переезжает в строку `*` WP7.1, когда она реализуется.
- Тесты: `tests/events/test_catalog.py::test_image_registry_has_no_gaps_and_tracks_rollback` (T-EV5).
- Требует подтверждения владельцем: нет

### D-A-217. WP8.3: маршрут `GET /v1/sessions/{id}/summary` объявляет A; тело — в `quack/graph/read/summary.py`
- Класс: A
- WP / место: WP8.3; `quack/session/router.py` (объявление), `quack/graph/read/summary.py`, `tests/e2e/routes/a.py`
- Контекст: D-A-214 отдало объявление B (#228), но ТЗ §7.3, пересечение 3 прямо называет `summary` и `jobs` эндпоинтами A в `quack/session/router.py`. Тело в `quack/review/` нарушает таблицу слоёв §4.1 (T-A6): продуктовый `session` не импортирует продуктовый `review`.
- Решение: A объявляет маршрут в роутере B (только объявление); тело `session_summary` перенесено в ядро `quack/graph/read/summary.py` (зависит только от `db`, `events`, `graph.read`). Чужая или несуществующая сессия — 404. Маршрут — в `other_routes` A (T-A1, T-E3). D-A-214 заменено этим решением.
- Почему: ТЗ §7.3 п.3, §4.1 (T-A6), WP8.3.
- Обратимость: полная.
- Тесты: `tests/review/test_session_summary.py`, `tests/domains/test_edits.py` (T-ED5), T-A1, T-E3.
- Требует подтверждения владельцем: нет

### D-A-216. WP1.3, подготовка шага 5: типы задачи — в `quack/jobs/types.py`
- Класс: A
- WP / место: WP1.3; `quack/jobs/types.py`, `quack/jobs/queue.py`
- Контекст: `ClaimedJob`, `JobSpec`, `JobDeferred`, `JobFailedPermanently` объявлены в очереди Фазы 3 (`jobs/queue.py` на SQLAlchemy), а их импортируют ~20 обработчиков всех потоков (D-A-212). Файл удаляется в шаге 5.
- Решение: типы перенесены в `quack/jobs/types.py` без изменений; `jobs/queue.py` их реэкспортирует до шага 5. Импорты в зоне A уже идут из `types`. Импорты B и C переводятся в шаге 5 одной заменой строки (разрешённое пересечение 1, §7.3), либо потоками раньше.
- Почему: ТЗ WP1.3 (снятие SQLAlchemy без потери обработчиков), §7.3.
- Обратимость: полная.
- Тесты: без новых; `tests/jobs`, `test_jobs_phase3`, `test_dispatcher`, контрактные — зелёные.
- Требует подтверждения владельцем: нет

### D-A-215. WP7.5: обработчик `exports.cleanup`
- Класс: A
- WP / место: WP7.5; `quack/jobs/cleanup_p4.py`, `quack/jobs/kinds.py`
- Контекст: ТЗ §6 (таблица периодических) — «правило жизненного цикла префикса экспорта, если хранилище его не поддерживает» (`Д-§8.4`: на Contabo правило задаётся только API/CLI). Статусов `content.exports` и того, что делать со строкой, в документах нет; `export.data` ещё не написан.
- Решение: раз в сутки — строки с `expires_at < now` и `object_key` не NULL: сначала `delete_keys` объектов, затем строка `status = 'expired'`, `object_key = NULL` (строка остаётся: по ней видно, что ссылка была и истекла). Повтор безопасен; сбой между шагами оставляет строку следующему проходу. Работает и при настроенном правиле хранилища (удаление отсутствующего ключа — не ошибка).
- Почему: `Д-§8.4`, `Т-§19.6` (`export_link_ttl_hours`), WP7.5.
- Обратимость: полная.
- Тесты: `tests/graph/test_projection_runner.py::test_exports_cleanup_deletes_only_expired_archives`.
- Требует подтверждения владельцем: нет

### D-A-214. WP8.3: маршрут `GET /v1/sessions/{id}/summary` — код объявления для B
- Класс: A
- WP / место: WP8.3, §6.4; `quack/session/router.py` (зона B, черновик #222)
- Контекст: функция готова (D-A-213), маршрут лежит в зоне B и не объявлен.
- Решение: B добавляет в `quack/session/router.py` (импорты `create_graph_reader`, `SessionNotFoundError`, `SessionSummary`, `session_summary`):
  ```python
  @router.get("/sessions/{session_id}/summary", response_model=SessionSummary)
  async def get_session_summary(
      session_id: UUID, learner_id: LearnerId, resources: Resources
  ) -> SessionSummary:
      reader = create_graph_reader(
          resources.neo4j, resources.settings, learner_id=learner_id, db=resources.db
      )
      try:
          return await session_summary(resources.db, reader, learner_id, session_id)
      except SessionNotFoundError as exc:
          raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
  ```
  Ответ — `SessionSummary` (200); чужая или несуществующая сессия — `404 NOT_FOUND`. Читатель обязан получить `db=` (без него `state_at` падает, D-A-191). Нужно также: строка в `EXPECTED_ROUTES` (T-A1), пересборка `openapi.json`; сам T-E13 — в e2e B.
- Почему: ТЗ WP8.3, зона B; ресурсов хватает: `AppResources` уже даёт `db`, `neo4j`, `settings`.
- Обратимость: полная.
- Тесты: нет (маршрута нет).
- Требует подтверждения владельцем: нет

### D-A-213. WP8.3: `session_summary` — «до» и «после» сессии в кодах
- Класс: A
- WP / место: WP8.3, §2.3 строка 12, `П-4.10`, `Т-§2.6`; `quack/review/session_summary.py`
- Контекст: в ТЗ нет правила, какие навыки «затронуты сессией», откуда `seq_n` при возврате в сессию и как отличить чужую сессию.
- Решение: `session_summary(db, reader, learner_id, session_id) -> SessionSummary`. События сессии — `core.events` по `learner_id` и `session_id`. Нет `session.started` — `SessionNotFoundError` (чужая сессия выглядит так же: закон 1). `seq_n` = `seq` первого `session.started` минус 1 (возврат `resumed: true` его не сдвигает). «До» — `reader.state_at(learner, seq_n, навыки)`; «после» — `reader.slice_for_turn(learner, session, SliceFocus(skill_ids))` (текущая проекция, ждёт хвост журнала). Затронутые навыки: `skill.created` сессии; `exposures` и `tests` задач хода (`turn.committed`); `elements[].skill_id` и `root_cause.skill_id` (`observation.extracted`); `root_cause_skill_id` (`observation.diagnosed`); `tests` (и `parts[].tests`) задач, на которые в сессии были ответ, подсказка, закрытие или показ, — по `task.created` с последней поправкой `corrected_markup` проверки. Код, не текст: на навык — уровни (`status`, `basis`), `gap_type`, `consolidation_flag`, без имён, весов и времени; `before = None` — навыка до сессии не было; `changed` — коды различаются. Навык, названный событием, но которого нет в графе, пропускается. «Что сделано» — по задаче: число ответов, наибольший шаг подсказки, исход последнего вердикта, как закрыта (`None` — открыта). Слитый после N навык виден под разными id «до» и «после» (известное ограничение: псевдонимы не склеиваются).
- Почему: WP8.3, `Т-§2.6` (проигрывание, не свёртка по `seq ≤ N`), `П-4.10`.
- Обратимость: полная.
- Тесты: `tests/domains/test_edits.py::test_state_at_n_with_late_dispute` (T-ED5); `tests/review/test_session_summary.py::test_summary_before_and_after_differ_for_solved_task` (часть T-E13), `::test_a_session_of_another_learner_is_not_found`.
- Требует подтверждения владельцем: нет

### D-A-212. WP1.3 шаг 3: что остаётся на SQLAlchemy в зоне A после `graph/read/**`
- Класс: A
- WP / место: WP1.3, §7.3, §8.15; `quack/graph`, `quack/events`, `quack/jobs`, `quack/db`, `quack/ops`
- Контекст: шаг 5 удаляет SQLAlchemy целиком; какие модули Фазы 3 удаляются вместе с ним, а какие несут то, что живёт и в Фазе 4, — в документах нет списка.
- Решение: «удаляется в шаге 5 целиком» — `graph/worker.py`, `graph/rebuild.py`, `events/projections.py`, `events/append.py` (замена `events/log.py`), `events/reader.py` (кроме `last_seq` для ещё живых вызывающих в `session`/`artifacts`), `jobs/queue.py` (замена `queue_p4.py`; `ClaimedJob`/`JobSpec`/`JobDeferred`/`JobFailedPermanently` ещё нужны обработчикам до их перевода), `jobs/dispatcher.py`, `jobs/periodic.py`, `db/locks.py` (замена `Db.advisory_lock`), `db/postgres.py` (последним). «Нужно перенести»: `jobs/runner.py` — `JobContext`, `register_job_handler`, `JobHandler`, `JOB_HANDLERS` нужны всем обработчикам Фазы 4 (19 импортов), класс `JobRunner` и поле `session_factory` удаляются; `graph/overlay.py` — `SessionOverlay` читает журнал через `AsyncSession` (функция `load_overlay`), нужна `Db`-версия; `graph/slice.py` — `SliceDeps.session_factory` остаётся до перевода вызывающих (`session`, `artifacts/practice.py`), потом поле удаляется. В `ops/**` — `commands.py`, `registry.py`, `status.py`, `seed.py`, `restore_check.py`, `purge_job.py`, `db_roles.py`: переносит тот, кто ведёт `ops`.
- Почему: §8.15 (удаляется код, который заменяет Фаза 4), WP1.3 шаг 5.
- Обратимость: полная (только перечень).
- Тесты: нет.
- Требует подтверждения владельцем: нет

### D-A-211. WP1.3: `session.sweep_inactive` в периодических задачах Фазы 4
- Класс: A
- WP / место: WP7.5, WP1.3 (просьба B, #222/#215); `quack/jobs/periodic_p4.py`, `quack/runtime/phase4.py`
- Контекст: в таблице WP7.5 у `session.sweep_inactive` ключ «—», период `session_sweep_interval_s`; `job_key` обязан быть детерминированным (T-D9).
- Решение: ключ `sweep-inactive:{unix_time // session_sweep_interval_s}` — один ключ на период, тик `workers` каждые `scheduler_tick_s` ставит ровно одну задачу за период. `periodic_plans(now, sweep_interval_s=)` и `schedule_periodic(..., sweep_interval_s=)` принимают период (по умолчанию 60), `periodic_tick` передаёт `settings.session_sweep_interval_s`. Класс P3, `lock_key` = вид (как у соседних): два обхода не идут одновременно. Обработчик остаётся у B (`session/lifecycle.py`); планировщик Фазы 3 (`jobs/periodic.py`) живёт до шага 5.
- Почему: T-D9, WP7.5, `JOB_KIND_SPECS` (`session.sweep_inactive` — `periodic=True`).
- Обратимость: полная.
- Тесты: `tests/jobs/test_queue_p4.py::test_periodic_keys` (пять задач за первый тик; ключи минут).
- Требует подтверждения владельцем: нет

### D-A-210. WP1.3: `Neo4jGraphReader` без `session_factory`, ожидание журнала через `Db`
- Класс: A
- WP / место: WP1.3 шаг 3, WP6.4, WP8.3; `quack/graph/read/neo4j_reader.py`, `neo4j_extras.py`, `reader.py`, `quack/graph/slice.py`
- Контекст: D-A-191 перевёл `state_at` на `db=`, но читатель ещё принимал `session_factory` (ожидание хвоста журнала в `slice_for_turn`, ветка `state_at` Фазы 3).
- Решение: у `Neo4jGraphReader` убран параметр `session_factory`; сборка — `Neo4jGraphReader(neo4j, settings, learner_id=, db=)` / `create_graph_reader(..., db=)`; публичные методы `GraphReader` не менялись. С `db` `slice_for_turn` без оверлея ждёт хвост журнала (`core.learners.seq`), как раньше ждал с `session_factory`; без `db` цель ожидания 0. `state_at` читает только журнал Фазы 4 (без `db` — `RuntimeError`), ветка `project_in_memory` по `StoredEvent` Фазы 3 снята. `SliceDeps` получил `db` (только по имени); `session_factory` в нём `None`-able и уйдёт, когда вызывающие в `session`/`artifacts` перейдут на `db` (D-A-212).
- Почему: WP1.3 шаг 3, D-A-191, T-DB3.
- Обратимость: полная.
- Тесты: `tests/graph/test_projection_runner.py::test_slice_waits_for_the_journal_of_phase_4` (новый); `tests/graph/test_read_neo4j_extras.py::test_state_at_replays_events_up_to_n_not_a_fold_by_seq` — подмена теперь на `LogEvent` Фазы 4.
- Требует подтверждения владельцем: нет

### Правки тестов (D-A-210…212)
- `tests/graph/test_read_neo4j_extras.py`: события подмены `state_at` — `LogEvent` вместо `StoredEvent` Фазы 3 (проверка та же).
- `tests/jobs/test_queue_p4.py::test_periodic_keys`: счётчики 4/0/3 → 5/0/1/4 и ключи минут обхода (добавлена пятая периодическая задача; проверка периодов остальных четырёх не ослаблена).

### D-A-209. WP1.3 шаг 3: тесты — что снято, что перенесено, мост стенда e2e
- Класс: A
- WP / место: WP1.3, §8.15; `tests/domains/`, `tests/jobs/`, `tests/e2e/journey.py`, `tests/e2e/routes/a.py`, `tests/test_*.py`
- Контекст: стенд e2e и тесты сессий (B, #222) ещё на Фазе 3: `POST /v1/sessions` читает структуру домена в журнале Фазы 3. Маршруты `structure/*` и `diagnostic/*`, которые её писали, сняты (§2.3 строка 5).
- Решение: `onboard()` стенда создаёт домен маршрутом Фазы 4 (`POST /v1/domains`), а структуру (две темы, навык у каждой, одно предусловие — как давал `structurer` стенда) кладёт в журнал Фазы 3 напрямую (`_seed_phase3_structure`). Мост — пока B не переведёт сессии (#222) и не придёт T-E1 Фазы 4; тогда `onboard()` и `structure_node()` уходят. Тесты снятого кода удалены, тесты свойств, которые остаются, перенесены (список — ниже).
- Почему: §8.15 п.1–2, §0.4.4 п.14 (не ломать тесты чужого стека до его перехода).
- Обратимость: полная; мост — один файл теста.
- Тесты: `tests/domains/test_domains_routes.py` (4 теста), весь обход `tests/e2e`.
- Требует подтверждения владельцем: нет

**Правки тестов (§8.15)**

| Снят / изменён | Почему | Что проверяет теперь |
| :-- | :-- | :-- |
| `tests/domains/test_structure_and_dag.py`, `test_structure_actor_and_delete.py`, `test_propose_idempotency.py`, `test_confirm_idempotency.py` | интервью структуры снято (§2.3 строка 5; §8.15 п.1) | свойство «повтор с тем же ключом» — `test_domains_routes.py::test_create_and_list_domains`; рост графа — T-P*, `test_projection_runner.py` |
| `tests/domains/test_diag_idempotency.py`, `test_diag_open_and_bank.py`, `test_diag_state_from_events.py`, `test_diag_wakeup.py`, `test_diagnostic_and_graph_view.py` (диагностика, карта Фазы 3) | диагностика снята (§2.3 строка 5) | карта — `test_domains_routes.py::test_domain_graph_and_topic_card_give_codes` (T-TH5) |
| `tests/domains/test_graph_view_fixes.py`, `test_topic_alias_routes.py` | `p_mastery`/пять `display_state`, короткие маршруты статуса темы сняты (§8.15 п.1) | то же, T-TH1/T-TH2 в `tests/graph/` |
| `tests/domains/test_auth_and_learner.py` | `auth.users.preferences`, tombstone и `learner.preferences_updated` сняты | `test_set_preference`, `test_delete_learner_removes_the_account`; `test_projection_runner.py::test_delete_account` |
| `tests/domains/test_curation_*.py` (3 файла) и два теста `graph.curate` из `test_diagnostic_and_graph_view.py` | не сняты: обработчик жив на конвейере Фазы 3 | перенесены в `tests/jobs/test_curation_p3_*.py` без изменения утверждений (D-A-208) |
| `tests/test_meta_mock.py::test_structurer_answer_gives_topics_with_skills` | проверял `sanitize_structurer_output` снятого модуля | — (роль `structurer` у B) |
| `tests/test_jobs_phase3.py::test_purge_job_removes_learner_completely` | `DELETE /v1/learner` больше не пишет tombstone | тот же тест, tombstone кладёт сам тест: проверяется обработчик чистки Фазы 3, пока он жив |
| `tests/test_neo4j_outage.py` | `GET /v1/domains/{id}/topics/{id}` снят; домен — в журнале Фазы 4 | карта и карточка темы (`/v1/topics/{id}`) при сбое Neo4j — `503` |
| `tests/test_app_assembly.py` (фикстура `sign_in`) | журнал Фазы 4 требует строку `core.learners` | то же утверждение (T-A3, T-A5) |
| `tests/e2e/test_journey_and_laws.py` | T-E1: проверка `p_mastery` по карточке темы Фазы 3 снята; T-E10: чистка планировщиком снята | T-E10: `DELETE /v1/learner` — `delete_account` сразу: строки Фазы 4, граф, ключ присутствия, журнал, `401` |
| `tests/e2e/test_degradation.py`, `test_isolation_and_idempotency.py`, `journey.py`, `routes/a.py` | интервью, диагностика, архив сняты; `preferences` — `{key, value}` | то же, через мост (выше) |

- Правка после CI #226: `tests/session/test_phase3_hot_path.py::test_turn_survives_neo4j_outage` (зона B) — снята проверка `GET /v1/domains/{id}/graph` → 503: домен теста живёт в журнале Фазы 3, маршрут — на Фазе 4; 503 графа при недоступном Neo4j проверяет `tests/test_neo4j_outage.py` (T-H1). Файл переписывает B в #222.

### D-A-208. WP1.3 шаг 3: что остаётся от Фазы 3 в зоне домена
- Класс: A
- WP / место: WP1.3, §6.5; `quack/jobs/curation_p3.py`, `quack/domains/curation.py`, `quack/domains/schemas.py`
- Контекст: обработчик `graph.curate` (`curation.py`) живёт на конвейере задач Фазы 3: диспетчер Фазы 3 ставит `graph.curate`, `quack.runtime.HANDLER_MODULES` его импортирует, на виде задачи стоят тесты очереди и диспетчера. Портировать его на `Db` нельзя: он пишет события Фазы 3 в базу Фазы 3 (`SessionFactory`). Выходы ролей `structurer`, `diagnostic`, `curator` (`StructurerOutput`, `DiagnosticOutput`, `CuratorOutput`) нужны тестам и скриптам B (`tests/llm/prompt_contracts.py`, `test_llm_runtime.py`, `test_meta_mock.py`, `scripts/meta_smoke.py`), пока роли и промпты не сняты.
- Решение:
  1. Обработчик перенесён `quack/domains/curation.py` → `quack/jobs/curation_p3.py` без правок; в `quack/domains/curation.py` остался импорт (`HANDLER_MODULES` не тронут). Тесты перенесены в `tests/jobs/test_curation_p3_*.py`. Всё это уходит вместе с `graph.curate` (`kinds.graph_curate`, `dispatcher_rules`, запись в `HANDLER_MODULES`, `REMOVED_JOB_KINDS`): одним шагом исполнителя, снимающего конвейер Фазы 3.
  2. Три схемы выходов ролей остались в конце `quack/domains/schemas.py` (с подмоделями); B убирает их вместе с ролями.
- Почему: §0.4.4 п.14, §8.15 п.1 (снимается вместе с последним потребителем), §7.3 (зона B — промпты и тесты ролей).
- Обратимость: полная.
- Тесты: `tests/jobs/test_curation_p3_*.py` (перенесены без изменений утверждений), `tests/test_contracts.py` (T-A6, импорт каждого модуля).
- Требует подтверждения владельцем: нет

### D-A-207. WP1.3 шаг 3: `quack/domains/**` на `Db`; маршруты A на код Фазы 4
- Класс: A
- WP / место: WP1.3, WP6.5, WP25 п.3, §6.4; D-A-194, D-A-198, D-A-205, D-A-206; `quack/domains/{service,learner,graph_view,graph_reads,schemas,router}.py`, `quack/deletion/router.py`
- Контекст: в ТЗ нет формы ответа `GET /v1/domains/{id}/graph` и тела `PATCH /v1/learner/preferences` на новых кодах; слово «архив» и статус темы §6.4 заменяет WP25; нет места для `DELETE /v1/learner` между роутерами.
- Решение:
  1. **Домены.** Таблицы нет (§6.1): `POST /v1/domains` пишет `domain.created {domain_id, name}`; id — от ключа (`deterministic_id(learner, 0, "domain:<key>")`), повтор даёт тот же домен, тот же ключ с другим именем — `409`. `GET /v1/domains` читает `domain.created` ученика по `seq`: `{domain_id, name, created_seq, created_at}`. Поля `slug`, `status`, `has_materials`, `active_structure_version` и `include_archived` ушли.
  2. **Предпочтения.** `PATCH /v1/learner/preferences {key, value, idempotency_key}` → `preference.set`, ответ `{key, value, seq}`. Ключ — `^[a-z][a-z0-9_]{0,31}$` (он станет именем свойства `pref<Key>`), значение — строка до 500 знаков, число или булево. Прежнее тело `{preferences: {…}}` снято.
  3. **Карта домена.** `GET /v1/domains/{id}/graph` → `{domain_id, projected_seq, lag_degraded, topics[{topic_id, name, status, visibility, display: TopicDisplay, nearest_due_at_ms, skill_count}], requires[]}`; `requires` — `topic_requires` читателя, только между показанными темами. Домен — ученика по `domain.created` в журнале (только что созданный, ещё не спроецированный домен — пустая карта, не `404`; чужой — `404`). Скрытые темы не отдаются без `?include_hidden=true`. `lag_degraded` — отметка проектора меньше счётчика ученика. Пересчёта «на момент чтения» нет: «пора повторить» — клиент по `nearest_due_at_ms` (Т-§8.6).
  4. **Карточка темы.** `GET /v1/topics/{id}` → `graph_reads.topic_view` (D-A-198); лимит мест — 200 по умолчанию (параметра в `Settings` нет и не вводится).
  5. **Снято с кодом:** `…/structure/propose|confirm`, `PATCH …/structure`, `…/diagnostic/start|answer|finish`, `POST …/archive`, `PATCH …/topics/{id}/status` и `PATCH /topics/{id}/status`, `GET /domains/{id}`, `GET /domains/{id}/topics/{topic_id}` (их нет в §6.4; старый → новый — таблица §6.4), модули `structure`, `diagnostic`, `topics`.
  6. **`DELETE /v1/learner`** → `quack.deletion.account.delete_account` в `quack/deletion/router.py` (рядом с остальными удалениями). Ответ как у домена и темы: `{deletion_id, copies_days: 15}`. `tracer` — `resources.llm.tracer` (шлюз его уже держит; поля в `AppResources` не нужно), `pseudonym = learner_pseudonym(settings.langsmith_pseudonym_ns, learner_id)` (D-A-206). Журнал R2 недоступен или блокировка занята — `503`. Сессия уходит вместе со строками, следующий запрос — `401`.
- Почему: §6.4 (таблицы «Домены», «Граф и темы», «Ученик», «Удаление»; «Старый API → новый»), §2.3 строка 5, §6.1, T-TH5, WP25 п.1, п.3; закон 1.
- Обратимость: полная; `include_hidden` и лимит мест — параметры чтения.
- Тесты: `tests/domains/test_domains_routes.py` (создание и список, предпочтение, карта и карточка с кодами и `TopicDisplay` в OpenAPI, удаление аккаунта), `tests/e2e/routes/a.py` (T-A1, T-E2, T-E3).
- Требует подтверждения владельцем: нет

### D-A-206. WP1.3: `db=` шлюза в `workers` и удаление трасс при удалении аккаунта
- Класс: A
- WP / место: WP1.3, WP25 п.3, WP10.6 (issue #210); `quack/runtime/__init__.py`, `quack/deletion/account.py`
- Контекст: B (D-B-197) просит передать `db=` в `LLMGateway`; при удалении аккаунта — `tracer.delete_learner`. Когда включать `db=` при двух схемах до WP1.3 — в документах нет.
- Решение: `create_job_context` передаёт `db=` только при `phase4_processes` (одна база — одна таблица учёта: `ops.llm_usage` у Фазы 4, `ops.llm_ledger` у Фазы 3). `delete_account(..., tracer=, pseudonym=)` после строк, объектов и подграфа зовёт `tracer.delete_learner(pseudonym)`; `False` — ничего (трассы истекают, D-B-202). Псевдоним считает вызывающий (`learner_pseudonym(settings.langsmith_pseudonym_ns, id)`) при переключении `DELETE /v1/learner`.
- Почему: D-A-103 (две схемы), WP10.4, WP25 п.3, `Т-§19.5`.
- Обратимость: полная; флаг уходит с остатком WP1.3.
- Тесты: `tests/graph/test_projection_runner.py::test_delete_account` (трасса удаляется по псевдониму).
- Требует подтверждения владельцем: нет
