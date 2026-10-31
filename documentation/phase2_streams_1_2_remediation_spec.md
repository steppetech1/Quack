# Инженерное ТЗ: Доработка Фазы 2 (Потоки 1 и 2) в изолированном контуре

Этот документ содержит исчерпывающее техническое описание всех исправлений, которые необходимо внести в **Поток 1 (`backend/quack/domains/**`)** и **Поток 2 (`backend/quack/session/**`)** прямо сейчас — **параллельно с ведущейся разработкой Потока 3**.

---

## 0. Строгие границы зоны записи (Zero Merge Conflicts с Потоком 3) `[КОНТРАКТ]`

Вся работа по этому документу выполняется **исключительно** внутри файлов Потока 1 и Потока 2:
- **Разрешённые для изменения директории и файлы:**
  - `backend/quack/domains/**`
  - `backend/quack/session/**`
  - `backend/quack/prompts/{structurer,diagnostic,curator,teacher,extractor,checkpointer}/v1.md` и их 6 ключей в `backend/quack/prompts/manifest.json` (только при изменении текста шаблона промпта)
  - `backend/tests/domains/**`
  - `backend/tests/session/**`
- **Запрещено изменять (до завершения Потока 3):**
  - Любые файлы ядра Фазы 1: `backend/quack/{config,logging,cli}.py`, `backend/quack/{db,events,graph,jobs,llm,compute_client,api,ops}/**`, `backend/migrations/**`, `backend/tests/test_*.py`, `backend/tests/conftest.py`.
  - Любые файлы Потока 3: `backend/quack/artifacts/**`, `compute/**`, `backend/quack/prompts/{task_gen,evaluator,artifacts}/**`.
- **Запрет кросс-импортов между потоками:**
  - `quack/domains/**` не импортирует `quack/session/**` и `quack/artifacts/**`.
  - `quack/session/**` не импортирует `quack/domains/**` и `quack/artifacts/**`.

---

## Часть I. Поток 1 (`backend/quack/domains/**`)

### Задача D-1 `[P0]`: Унификация формата `choice`-задач в `core.task_bank` и сохранение вариантов ответа (`options`)

* **Затронутые файлы:**
  - [backend/quack/domains/diagnostic.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/diagnostic.py#L340-L357) (строки `340–357`, `418–435`, `626–643`)
* **Суть дефекта:**
  1. При эмиссии события `task.generated` в `start_diagnostic` (`diagnostic.py:431`) и `answer_diagnostic` (`diagnostic.py:639`) записывается:
     ```python
     "reference_solution": {"answer": correct_ans}
     ```
     при этом список вариантов ответа `options: list[str]` не сохраняется ни в `reference_solution`, ни в `TaskGenerated` (схема `TaskGenerated` в `quack/events/schemas.py:222` фиксирована в Фазе 1 и не имеет верхнеуровневого поля `options`).
  2. В Потоке 2 (`quack/session/tasks.py:201`) проверка `choice`-задачи читает `reference_solution.get("option")`. Из-за отсутствия ключа `"option"` задачи из диагностики не проверяются в сессии.
  3. В самой диагностике в `_reconstruct_state_from_events` (`diagnostic.py:346`) вызов `last_gen.get("options", [])` ищет `options` в корне `payload` события `task.generated`, где его нет, поэтому при реконструкции из БД варианты ответа теряются (`options = []`).
* **Точное инженерное решение:**
  1. В `start_diagnostic` (`diagnostic.py:431`) и `answer_diagnostic` (`diagnostic.py:639`) при формировании `payload` события `task.generated` записывать в `reference_solution` каноническую структуру, совместимую с обоими потоками:
     ```python
     "reference_solution": {
         "option": correct_ans,
         "answer": correct_ans,
         "options": list(options),
     }
     ```
  2. В `_reconstruct_state_from_events` (`diagnostic.py:343-348`) извлекать `correct_answer` и `options` из `last_gen.get("reference_solution", {})`:
     ```python
     ref_sol = last_gen.get("reference_solution") or {}
     correct_ans = ref_sol.get("option") or ref_sol.get("answer") or ""
     options = ref_sol.get("options") or last_gen.get("options") or []
     ```

---

### Задача D-2 `[P0]`: Идемпотентность `start_diagnostic`, `answer_diagnostic`, `finish_diagnostic` и дедупликация `curator@1`

* **Затронутые файлы:**
  - [backend/quack/domains/diagnostic.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/diagnostic.py#L364-L746)
  - [backend/quack/domains/curation.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/curation.py#L305-L318)
* **Суть дефекта:**
  1. В `start_diagnostic` (`diagnostic.py:384-385`) при каждом вызове генерируются случайные `session_id = uuid4()` и `task_id = uuid4()`. Так как `session_id` входит в `payload` события `diagnostic.started` (`idempotency_key=req.idempotency_key`), при повторном запросе с тем же `req.idempotency_key` вычисляется иной `payload_hash`, и `append_events` выбрасывает `409 IDEMPOTENCY_CONFLICT` вместо возврата ранее начатой диагностики.
  2. В `answer_diagnostic` (`diagnostic.py:510`) генерируется случайный `turn_id = uuid4()`, который входит в `payload` события `diagnostic.question_answered` (`idempotency_key=req.idempotency_key`). При ретрае с тем же ключом возникает `409 IDEMPOTENCY_CONFLICT`.
  3. В `curation.py:311` в `llm.generate_structured` передаётся `idempotency_key=f"curator:{domain_id}:{uuid4().hex[:8]}"`. Случайный суффикс убивает дедупликацию в `core.llm_outbox` при повторной попытке выполнения задачи `graph.curate`.
* **Точное инженерное решение:**
  1. **Детерминированные UUID от `(learner_id, domain_id, idempotency_key)`** с использованием контрактного `QUACK_UUID_NS` из `quack/events/hash.py`:
     - В `start_diagnostic`:
       ```python
       session_id = uuid5(QUACK_UUID_NS, f"diag_session:{learner_id}:{domain_id}:{req.idempotency_key}")
       task_id = uuid5(QUACK_UUID_NS, f"diag_task:{learner_id}:{session_id}:0")
       ```
       Перед генерацией вопроса проверить в `core.events` наличие события `diagnostic.started` у `learner_id` с `idempotency_key = req.idempotency_key`:
       - если событие уже есть и его `domain_id != str(domain_id)` — выбросить `ApiError("IDEMPOTENCY_CONFLICT", ..., status_code=409)`;
       - если событие уже есть и `domain_id == str(domain_id)` — прочитать сохранённое событие `task.generated` с `idempotency_key = f"{req.idempotency_key}:task"` и вернуть `DiagnosticStartResponse` без повторного запуска LLM и без перезаписи состояния текущей продвинувшейся сессии.
     - В `answer_diagnostic`:
       ```python
       turn_id = uuid5(QUACK_UUID_NS, f"diag_turn:{learner_id}:{req.session_id}:{req.idempotency_key}")
       next_task_id = uuid5(QUACK_UUID_NS, f"diag_next_task:{learner_id}:{req.session_id}:{req.idempotency_key}")
       ```
       Перед мутацией состояния проверить в `core.events` наличие `diagnostic.question_answered` с `(learner_id, idempotency_key=req.idempotency_key)`:
       - если найдено, сверить входные поля запроса (`session_id`, `task_id`, `answer`, `confidence_self_report`): при расхождении выбросить `409 IDEMPOTENCY_CONFLICT`; при совпадении — восстановить и вернуть `DiagnosticAnswerResponse` из записанных событий этого хода.
  2. **Детерминированный ключ `curator@1` в `curation.py:311`:**
     - Вычислять fingerprint текущих кандидатов на слияние (например, SHA-256 от отсортированных пар ID `provisional` и `active` навыков домена) и передавать:
       ```python
       candidates_hash = hashlib.sha256(
           orjson.dumps(
               {"prov": sorted(str(s["id"]) for s in remaining_provisional),
                "act": sorted(str(s["id"]) for s in active_skills)}
           )
       ).hexdigest()[:16]
       idempotency_key = f"curator:{domain_id}:{candidates_hash}"
       ```

---

### Задача D-3 `[P1]`: Реализация оценки открытых вопросов (`mode="evaluate"`), `confidence_self_report` и выборки из `core.task_bank` в диагностике

* **Затронутые файлы:**
  - [backend/quack/domains/diagnostic.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/diagnostic.py#L390-L665)
  - [backend/quack/domains/schemas.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/schemas.py#L249-L265)
* **Суть дефекта (нарушение `ТЗ-П1 §2.3.1, §2.3.2, §4.2`):**
  1. При старте и продолжении диагностики вообще не проверяется наличие готовых задач по теме в `core.task_bank` (`ТЗ-П1 §2.3.1`).
  2. В `answer_diagnostic` (`diagnostic.py:507-508`) проверка всегда выполняется через `req.answer.strip().lower() == correct_ans.strip().lower()`, даже если вопрос открытый (`kind == "open"`). Роль `diagnostic@1` в режиме `mode="evaluate"` никогда не вызывается.
  3. Поле `req.confidence_self_report` игнорируется при деградации LLM и при расчёте `inferred_priors` (`source="self_report"` никогда не выставляется).
  4. Поле `tested_depth` при записи `diagnostic.question_answered` (`diagnostic.py:527`) захардкожено в `0.5` вместо `tested_depth` сгенерированного вопроса.
* **Точное инженерное решение:**
  1. **Поиск в `core.task_bank` перед вызовом `diagnostic@1` (`mode="generate"`):**
     - Добавить вспомогательную функцию `_find_bank_task(db: AsyncSession, learner_id: UUID, domain_id: UUID, topic_id: UUID, skill_id: UUID, excluded_task_ids: set[str]) -> dict[str, Any] | None`, которая ищет задачу в `core.task_bank` для данной темы и навыка, ещё не задававшуюся в текущей сессии диагностики.
     - Если задача найдена в `core.task_bank`, переиспользовать её `task_id`, `kind`, `prompt_text`, `reference_solution`, `difficulty` (без эмиссии дублирующего `task.generated`).
     - Если не найдена — вызывать `diagnostic@1` (`mode="generate"`), сохранять `tested_depth = diag_out.tested_depth` в состоянии текущего вопроса и эмитить `task.generated` с `actor="diagnostic@1"` (или `actor="code"`, если сработал кодовый fallback).
  2. **Оценка ответа в `answer_diagnostic` по типу вопроса (`kind`):**
     - Если `current_question["kind"] == "choice"` (или ответ эталона задан однозначным вариантом и `kind != "open"`):
       - оценивать детерминированно кодом: `is_correct = req.answer.strip().lower() == correct_ans.strip().lower()`, `score = 1.0 if is_correct else 0.0`, `source = "tested"`.
     - Если `current_question["kind"] == "open"`:
       - вызывать `llm.generate_structured` с `role="diagnostic"`, `prompt_version=1`, `response_model=DiagnosticOutput`, `variables={"mode": "evaluate", "topic_name": ..., "skill_name": ..., "student_answer": req.answer}`, `idempotency_key=f"diag:eval:{req.session_id}:{req.idempotency_key}"`;
       - брать `score = float(eval_out.score)`, `is_correct = bool(eval_out.is_correct) if eval_out.is_correct is not None else (score >= 0.6)`, `source = "tested"`;
       - при перехвате `(LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError)`:
         - если `req.confidence_self_report is not None`: `score = float(req.confidence_self_report)`, `is_correct = score >= 0.6`, `source = "self_report"`;
         - иначе: fallback на сравнение непустого ответа с эталоном (`score = 0.5` при непустом осмысленном ответе, `source = "self_report"`).
     - Передавать сохранённый `tested_depth` текущего вопроса в `diagnostic.question_answered` (вместо хардкода `0.5`) и сохранять `source` (`"tested"` или `"self_report"`) в `answered[topic_id_str]` для последующей записи в `diagnostic.completed.inferred_priors`.

---

### Задача D-4 `[P1]`: Включение `publish_wakeup=True` и передача `redis` во всех вызовах `append_events` Потока 1

* **Затронутые файлы:**
  - [backend/quack/domains/diagnostic.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/diagnostic.py#L451) (строки `451`, `668`, `721`)
  - [backend/quack/domains/curation.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/curation.py#L425) (строка `425`)
* **Суть дефекта:** Во всех 4 вызовах стоит `publish_wakeup=False` и не передан `redis`, из-за чего проектор не получает уведомление в `projector:wakeup` после диагностики и курации (`Спец-§8.1`).
* **Точное инженерное решение:**
  - В `diagnostic.py` (`lines 451, 668, 721`): заменить `publish_wakeup=False` на `redis=resources.redis, publish_wakeup=True`.
  - В `curation.py` (`line 425`): передать `redis=redis, publish_wakeup=True`.

---

### Задача D-5 `[P1]`: Устранение хранения состояния диагностики в Redis и проверка владения сессией (`IDOR`)

* **Затронутые файлы:**
  - [backend/quack/domains/diagnostic.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/diagnostic.py#L297-L357) (строки `297–357`, `463–478`, `678–693`, `724–728`)
* **Суть дефекта:**
  1. Нарушен контракт `Спец-§6`: используется недокументированный ключ `diagnostic:session:{session_id}`.
  2. В `_load_session_state` при чтении из Redis не проверяются `learner_id` и `domain_id` (`IDOR` между учениками).
  3. В `_reconstruct_state_from_events` не восстанавливаются `tested_depth`, `kind` и `source` из событий `task.generated` и `diagnostic.question_answered`.
* **Точное инженерное решение:**
  1. Полностью удалить запись/чтение ключа `diagnostic:session:{session_id}` из Redis (`_diag_redis_key`).
  2. Сделать `_reconstruct_state_from_events(db, learner_id, domain_id, session_id)` единственным авторитетным источником состояния диагностики (чтение из `core.events` по проиндексированным полям `learner_id = :lid AND domain_id = :did`).
  3. В `_reconstruct_state_from_events`:
     - проверять, что `diagnostic.started` существует именно у данного `learner_id` и `domain_id` (иначе `404 NOT_FOUND`);
     - проверять, что для `session_id` ещё нет события `diagnostic.completed` (если есть — при вызове `answer_diagnostic` возвращать `409 IDEMPOTENCY_CONFLICT`, а при вызове `finish_diagnostic` возвращать идемпотентный `DiagnosticFinishResponse` из уже записанного `diagnostic.completed`);
     - восстанавливать из последнего `task.generated` (для этой сессии) поля `task_id`, `topic_id`, `skill_id`, `kind`, `prompt_text`, `options`, `correct_answer`, `tested_depth`.

---

### Задача D-6 `[P1]`: Исправление расчёта `effective_mastery`, пререквизитов и фильтрации `archived` в `graph_view.py`

* **Затронутые файлы:**
  - [backend/quack/domains/graph_view.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/graph_view.py#L108-L382)
* **Суть дефекта:**
  1. В `get_domain_graph_view` (`line 113`) и `get_topic_detail_view` (`line 360`) в `SkillSnapshot` передаётся `p_mastery=p_m` (сырое значение из Neo4j) вместо `eff_m = effective_mastery(p_m, stab, l_rev, now_ms)`. Из-за этого `recompute_topic_display` считает `mastery` темы без учёта забывания.
  2. В `get_topic_detail_view` (`lines 371-378`) для `prereq_snaps` берётся `p["props"].get("mastery", 0.0)` из узла `Topic` в Neo4j вместо вычисленного `mastery` пререквизитной темы, из-за чего открытые темы с пререквизитами ошибочно получают `display_state = "locked"`.
  3. В `get_topic_detail_view` (`lines 240-256`) запрос навыков темы не фильтрует `s.status <> 'archived'`, а запрос `DEPENDS_ON` возвращает зависимости на навыки вне данной темы или на архивные навыки.
* **Точное инженерное решение:**
  1. В `get_domain_graph_view` (`line 113`) и `get_topic_detail_view` (`line 360`) передавать `p_mastery=eff_m` в `SkillSnapshot` (чтобы `recompute_topic_display` агрегировал актуальное владение на момент чтения `now_ms`, согласно `ТЗ-П1 §2.4`).
  2. В `_load_topic_detail_from_neo4j` (`lines 240-275`):
     - добавить фильтр `WHERE coalesce(s.status, 'active') <> 'archived'` при чтении навыков темы и рёбер `DEPENDS_ON` (обе вершины `s1` и `s2` должны принадлежать теме и не быть `archived`);
     - для пререквизитных тем (`prereqs`) загружать их активные навыки или использовать `recompute_topic_display` (либо `coalesce(p.mastery, 0.0)` вместе с пересчётом по навыкам пререквизитов), чтобы `prereq_snaps` содержали точный `mastery` и `status` каждой входной темы.

---

### Задача D-7 `[P1/P2]`: Маршруты-алиасы `/v1/topics/{topic_id}`, `READ_ACCESS` в Neo4j, `read_consistent_slice` и пул ресурсов в `curation.py`

* **Затронутые файлы:**
  - [backend/quack/domains/router.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/router.py#L223-L270)
  - [backend/quack/domains/structure.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/structure.py#L331) (строки `331`, `458`)
  - [backend/quack/domains/diagnostic.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/diagnostic.py#L202)
  - [backend/quack/domains/graph_view.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/graph_view.py#L61)
  - [backend/quack/domains/curation.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/curation.py#L72-L245)
  - [backend/quack/domains/learner.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/domains/learner.py#L85-L89)
* **Точное инженерное решение:**
  1. **Алиасы маршрутов топиков (`domains/router.py`):**
     - Сохранить существующие маршруты `GET /v1/domains/{domain_id}/topics/{topic_id}` и `PATCH /v1/domains/{domain_id}/topics/{topic_id}/status`.
     - Добавить короткие маршруты из `Спец-§9.2` и `ТЗ-П1 §2.2.3, §2.4`:
       - `GET /v1/topics/{topic_id}` → находит `domain_id` топика ученика (через Neo4j / `core.events`) и вызывает `get_topic_detail_view`.
       - `PATCH /v1/topics/{topic_id}/status` → находит `domain_id` топика и вызывает `change_topic_status`.
  2. **`default_access_mode=neo4j.READ_ACCESS`:**
     - Во всех читающих вызовах `driver.session(...)` в `structure.py:458`, `diagnostic.py:202`, `graph_view.py:61, 229`, `curation.py:118` указать `driver.session(default_access_mode=neo4j.READ_ACCESS)`.
  3. **`read_consistent_slice` и переиспользование ресурсов в `curation.py`:**
     - В `curation.py` читать граф через `read_consistent_slice` (чтобы куратор не работал поверх устаревшего среза Neo4j).
     - В `handle_graph_curate` (`curation.py:73-96`) кешировать `(driver, redis, llm_gateway)` на уровне модуля (по `id(ctx.settings)`), чтобы не пересоздавать пулы соединений и `asyncio.Semaphore` на каждую задачу.
  4. **Поле `actor` в событиях (`Спец-§2.4`):**
     - В `structure.py:331`: если сработал fallback `degraded=True`, ставить `actor="code"`, иначе `actor="structurer@1"`.
     - В `curation.py:183, 205, 239`: для детерминированных слияний и промоушенов (шаги 1 и 2 до LLM) ставить `actor="code"`, а для шага 3 (решения модели) — `actor="curator@1"`.
  5. **Отмена задач в `DELETE /v1/learner` (`learner.py:85-89`):**
     - Менять статус на `'cancelled'` для `status IN ('queued', 'running')`.

---

## Часть II. Поток 2 (`backend/quack/session/**`)

### Задача S-1 `[P0]`: Идемпотентный ретрай `POST /v1/sessions/{id}/turns` без `409 IDEMPOTENCY_CONFLICT`

* **Затронутые файлы:**
  - [backend/quack/session/turns.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/turns.py#L115-L441)
* **Суть дефекта:**
  - При повторе запроса `POST /v1/sessions/{id}/turns` с тем же `body.idempotency_key` функция `open_turn` генерирует новый `turn_id = uuid4()` (`turns.py:149`) и берёт увеличившийся `last_turn_index` (`turns.py:115`). При вызове `append_events` событие `turn.student_replied` с тем же `idempotency_key` имеет другой `payload` (`turn_id`, `turn_index`), что приводит к `IdempotencyConflictError` (`HTTP 409`) вместо идемпотентного возврата записанного хода.
* **Точное инженерное решение:**
  1. Сразу после захвата `acquire_turn_lock` и загрузки `session_row` проверить в `core.events`, существует ли у `learner_id` событие с `idempotency_key = body.idempotency_key`:
     ```sql
     SELECT seq, session_id, turn_id, type, payload
     FROM core.events
     WHERE learner_id = :lid AND idempotency_key = :ikey
     ```
  2. Если такое событие существует:
     - проверить, что `type == 'turn.student_replied'`, `session_id == session_id`, `payload.get("text") == body.text`, `payload.get("related_task_id") == (str(body.related_task_id) if body.related_task_id else None)`:
       - если хотя бы одно из этих входных полей отличается — выбросить `ApiError("IDEMPOTENCY_CONFLICT", "Idempotency key reused with different turn parameters", status_code=409)` (`Спец-§2.2`);
     - если входные поля совпадают — прочитать из `core.events` все события этого хода по `(learner_id, session_id, turn_id)` (`turn.student_replied`, опциональные `task.evaluated` / `task.assigned`, `turn.teacher_replied`) и вернуть `sse_response(_stream_turn(...))` по сохранённым событиям без повторного вызова LLM и без повторного `append_events`.
  3. Для нового хода генерировать `turn_id` детерминированно:
     ```python
     turn_id = uuid5(QUACK_UUID_NS, f"turn:{learner_id}:{session_id}:{body.idempotency_key}")
     ```
     а ключ `llm_outbox` для учителя формировать от `body.idempotency_key`: `f"turn:{session_id}:{body.idempotency_key}:teacher"`. Это гарантирует, что даже если предыдущий запрос прервался между ответом LLM и коммитом Postgres, повторный запрос заберёт готовый ответ из `core.llm_outbox` и сформирует идентичный `turn_id`.

---

### Задача S-2 `[P0]`: Проверка `payload` при идемпотентном старте/завершении сессии, корректный `status`, `presence` и `turns_count`

* **Затронутые файлы:**
  - [backend/quack/session/lifecycle.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/lifecycle.py#L89-L156) и [L304-L441](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/lifecycle.py#L304-L441)
* **Суть дефекта:**
  1. `_started_by_key` (`lifecycle.py:89-112`) не проверяет совпадение `domain_id`, `topic_id`, `mode`, `goal` с новым запросом (нет `409 IDEMPOTENCY_CONFLICT` при смене параметров с тем же ключом) и всегда возвращает `status="active"`, а `start_session` (`line 132`) ставит `presence:{learner_id}` даже для уже завершённой сессии.
  2. `finish_session` (`lifecycle.py:320-338`) для завершённой сессии не проверяет конфликт `idempotency_key`, на строке `399` удаляет `presence_key(learner_id)` безусловно (даже если там записан ID другой сессии), а в `turns_count` (`lines 342-352`) считает сумму `turn.student_replied` и `turn.teacher_replied` (удвоенное число ходов).
  3. `sweep_inactive_sessions` (`lifecycle.py:408-441`) при закрытии сессий не очищает `presence_key(learner_id)`.
* **Точное инженерное решение:**
  1. В `_started_by_key` (`lifecycle.py:89-112`):
     - сверять `domain_id`, `mode`, `goal` и (если `body.topic_id is not None`) `topic_id` из `payload` найденного `session.started` с полями `body`; при несовпадении выбрасывать `ApiError("IDEMPOTENCY_CONFLICT", ..., status_code=409)`;
     - подгружать актуальный `status` сессии из `core.sessions` (вместо хардкода `"active"`);
     - в `start_session` (`line 132`) вызывать `_set_presence` только если `existing.status == "active"`.
  2. В `finish_session` (`lifecycle.py:320-405`):
     - считать `turns_count` только по `type = 'turn.student_replied'` (количество совершённых ходов в сессии);
     - удалять `presence_key(learner_id)` только если его текущее значение в Redis равно `str(session_id)` (атомарным Lua-скриптом по аналогии с `_RELEASE_SCRIPT` в `locks.py`).
  3. В `sweep_inactive_sessions` (`lifecycle.py:408-441`):
     - после коммита `session.abandoned` очищать `presence_key(learner_id)` (если значение равно закрытому `session_id`).

---

### Задача S-3 `[P1]`: Сохранение выбранного `topic_id` при старте сессии с `topic_id=None`, `read_consistent_slice` и поддержка поля `plan` в `StartSessionRequest`

* **Затронутые файлы:**
  - [backend/quack/session/lifecycle.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/lifecycle.py#L56-L86) и [L134-L156](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/lifecycle.py#L134-L156)
  - [backend/quack/session/schemas.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/schemas.py#L26-L31)
* **Суть дефекта:**
  1. При `body.topic_id is None` функция `_default_plan` выбирает в Neo4j первую подходящую тему (`available` / `in_progress` / `rusty`), но возвращает только `plan: list[dict]`, теряя выбранный `topic_id`. В `core.sessions.topic_id` записывается `NULL`, из-за чего все последующие ходы (`build_turn_context`, `get_available_tasks`, `pick_task`, `run_turn_extract`) работают с пустым списком навыков и задач.
  2. `_default_plan` читает Neo4j напрямую без `read_consistent_slice` и без `READ_ACCESS`.
  3. В `StartSessionRequest` отсутствует опциональное поле `plan: list[dict[str, Any]] | None = None` (`ТЗ-П2 §2.2.1`).
* **Точное инженерное решение:**
  1. В `StartSessionRequest` (`quack/session/schemas.py:26-31`) добавить:
     ```python
     plan: list[dict[str, Any]] | None = None
     ```
  2. Изменить сигнатуру `_default_plan` на:
     ```python
     async def _resolve_topic_and_plan(
         resources: AppResources,
         settings: Settings,
         learner_id: UUID,
         domain_id: UUID,
         topic_id: UUID | None,
         explicit_plan: list[dict[str, Any]] | None,
     ) -> tuple[UUID | None, list[dict[str, Any]]]:
     ```
     - Внутри использовать `read_consistent_slice` и `driver.session(default_access_mode=neo4j.READ_ACCESS)`.
     - Если `topic_id is None`, выбирать первую тему домена со статусом `displayState IN ('in_progress', 'available', 'rusty')` (или любую активную тему домена) и возвращать её `UUID` как `resolved_topic_id`.
     - Если `explicit_plan is not None`, использовать его; иначе строить план из 3–5 шагов по навыкам `resolved_topic_id`.
     - Записывать `resolved_topic_id` в `session.started.payload["topic_id"]` (откуда проекция `append_events` запишет его в `core.sessions.topic_id`) и возвращать в `SessionStarted.topic_id`.

---

### Задача S-4 `[P1]`: Оценка открытых задач (`kind="open"`), fallback при сбое `compute`, поддержка `response_time_sec` и учёт `hints_used`

* **Затронутые файлы:**
  - [backend/quack/session/schemas.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/schemas.py#L67-L115)
  - [backend/quack/session/tasks.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/tasks.py#L163-L264)
  - [backend/quack/session/turns.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/turns.py#L130-L360)
  - [backend/quack/prompts/teacher/v1.md](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/prompts/teacher/v1.md) и [backend/quack/prompts/manifest.json](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/prompts/manifest.json)
* **Суть дефекта (`ТЗ-П2 §2.3.4`, `Спец-§5.2`):**
  1. Когда ученик отправляет ответ на задачу (`related_task_id is not None`), и задача имеет `kind == "open"` (или `compute` вернул `parse_error` / `ComputeUnavailableError`), `evaluate_submission` возвращает `evaluation = None`. После этого `turns.py` записывает `task.submitted`, но **не создаёт `task.evaluated`** и не отправляет SSE `task_evaluated`.
  2. В `TurnRequest` отсутствует поле `response_time_sec: float | None = None` (`Спец-§5.2`).
  3. В `compute_independence` (`tasks.py:163-167`) учитываются только события `task.hint_given` из оверлея, а переданный клиентом `body.hints_used` игнорируется (если `penalties` пуст, `independence` остаётся `1.0` даже при `hints_used = 3`).
  4. В `turns.py:130, 214, 313` при вызове `get_available_tasks` и `pick_task` не передаётся `session_id=session_id`.
* **Точное инженерное решение:**
  1. В `TurnRequest` (`session/schemas.py:67-73`) добавить:
     ```python
     response_time_sec: float | None = Field(default=None, ge=0.0)
     ```
     и пробросить его в `evaluate_submission` → `TaskSubmitted.response_time_sec`.
  2. В `compute_independence(penalties: Sequence[float], hints_used: int = 0) -> float`:
     - если `len(penalties) < hints_used`, добавлять стандартный штраф `0.25` за каждую недостающую подсказку:
       ```python
       total_penalty = sum(penalties) + max(0, hints_used - len(penalties)) * 0.25
       return max(0.1, round(1.0 - total_penalty, 6))
       ```
  3. Расширить `TeacherOutput` (`session/schemas.py:106-115`) опциональными полями оценки открытого ответа (с `default=None`, чтобы не сломать существующие моки `TeacherOutput` в тестах!):
     ```python
     evaluated_is_correct: bool | None = None
     evaluated_score: float | None = Field(default=None, ge=0.0, le=1.0)
     evaluated_feedback: str | None = None
     ```
  4. В `turns.py`: если `body.related_task_id is not None` и `evaluate_submission` вернул `(submitted, None)` (т.е. задача `open` или `compute` недоступен / вернул `parse_error`):
     - после вызова `teacher@1` (или детерминированного fallback при сбое LLM) сформировать `TaskEvaluated` (`evaluator="llm"` при успешном вызове учителя или `evaluator="compute"` при кодовом fallback), используя `teacher_output.evaluated_score` / `evaluated_is_correct` / `evaluated_feedback` (а если они `None` — вывести оценку из `reference_solution` или `pedagogical_move`), чтобы у каждого `task.submitted` гарантированно появлялась парная запись `task.evaluated` и SSE-событие `task_evaluated`.
  5. Передать `session_id=session_id` во все вызовы `get_available_tasks` и `pick_task` в `turns.py:130, 214, 313`.

---

### Задача S-5 `[P1]`: Закон 4 (`Т-§7.5–7.6`) в `run_turn_extract` (проверка дословной цитаты `evidence_text`), `read_consistent_slice` и кеширование пулов воркера

* **Затронутые файлы:**
  - [backend/quack/session/workers.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/workers.py#L29-L224)
  - [backend/quack/session/deps.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/deps.py#L63-L86)
  - [backend/quack/session/stream.py](file:///C:/Users/duhwk/Desktop/Quack/backend/quack/session/stream.py#L36-L65)
* **Суть дефекта:**
  1. В `run_turn_extract` (`workers.py:125-176`) не проверяется, что `obs.evidence_text` является подстрокой ответа ученика (`student_text`).
  2. `_read_topic_context` (`workers.py:29-37`) читает Neo4j без `read_consistent_slice` и без `default_access_mode=neo4j.READ_ACCESS`.
  3. Обработчики `handle_turn_extract` и `handle_session_checkpoint` (`workers.py:211-224, 329-341`) пересоздают драйвер Neo4j, клиент Redis и `LLMGateway` (со своим `Semaphore`) на каждый вызов задачи.
  4. В `session/deps.py:77-84` не проверяется `auth.users.deleted_at IS NULL`.
  5. В `session/stream.py:44` нет защиты от `resources.redis is None` и `RedisError`.
* **Точное инженерное решение:**
  1. **Проверка цитаты в `run_turn_extract` (`workers.py:155`):**
     - При обработке `output.observations` и `output.misconceptions`: если в реплике ученика `student_text` есть текст, проверять вхождение `obs.evidence_text.strip() in student_text` (или регистронезависимое вхождение при длине `student_text >= len(obs.evidence_text.strip())`). Если `student_text` сам короче цитаты или цитата выдумана моделью, то:
       - в соответствии с `Т-§7.5–7.6` либо подставлять точную подстроку `student_text` (если `obs.evidence_text` не входит в `student_text`, логировать предупреждение `extractor_ungrounded_quote_dropped` и отбрасывать выдуманное наблюдение, кроме случая когда `obs.evidence_text` совпадает с `student_text` без учёта регистра/пробелов).
       - *Важно для совместимости с существующим unit-тестом `test_turn_extract_worker.py`:* в существующем тесте ответ ученика `"I think the derivative is 2x"` и цитата в фейковом ответе LLM `"derivative is 2x"` — `"derivative is 2x" in "I think the derivative is 2x"` истинно (`True`)! Поэтому проверка `obs.evidence_text.strip().lower() in student_text.lower()` проходит на всех существующих тестах и при этом отсекает любые выдуманные галлюцинации LLM.
  2. **`read_consistent_slice` и `READ_ACCESS` в `_read_topic_context` (`workers.py:29-37`):**
     - Использовать `default_access_mode=neo4j.READ_ACCESS` и вызывать `read_consistent_slice` с `min_seq = teacher_seq` (с fallback на прямое чтение при `redis is None`).
  3. **Кеширование ресурсов воркера в `workers.py`:**
     - Кешировать `(driver, redis, gateway)` на уровне модуля `quack/session/workers.py`, чтобы все фоновые задачи разделяли один пул соединений и один семафор `LLMGateway`.
  4. **Проверка `auth.users` в `session/deps.py:77-84`:**
     - Проверять `ops.deletion_tombstones` И (если в `auth.users` есть записи для данного `learner_id`) проверять `deleted_at IS NULL`. *(Примечание: в некоторых unit-тестах Потока 2 фикстура создаёт события ученика напрямую без вставки в `auth.users`, поэтому запрос должен блокировать токен, если в `ops.deletion_tombstones` есть запись ИЛИ в `auth.users` стоит `deleted_at IS NOT NULL`)*.
  5. **Защита от отсутствия Redis в `session/stream.py:44`:**
     - Если `resources.redis is None`, возвращать корректный пустой SSE-стрим (или завершать генератор без необработанного `AttributeError`), а цикл чтения `pubsub.listen()` обернуть в `try ... except (RedisError, OSError):`.
  6. **Поле `actor` при деградации в `turns.py:330, 351`:**
     - Если сработал детерминированный fallback без LLM (`degraded_mode in ("llm_unavailable", "budget_exceeded")`), в событиях `task.assigned` и `turn.teacher_replied` указывать `actor="code"` вместо `"teacher@1"` (`Спец-§2.4`).

---

## Часть III. Критерии приёмки и команды верификации

После внесения изменений в `backend/quack/domains/**` и `backend/quack/session/**`:

1. **Ни один файл за пределами Потоков 1 и 2 не изменён:**
   ```powershell
   git status -s
   ```
   В выводе должны быть только файлы внутри `backend/quack/domains/`, `backend/quack/session/`, `backend/prompts/` (если менялись шаблоны), `backend/tests/domains/` и `backend/tests/session/`.
2. **Статический анализ, форматирование и типизация (`0 errors`):**
   ```powershell
   cd backend
   uv run ruff check
   uv run ruff format --check
   uv run pyright
   ```
3. **Полный прогон тестов с поднятой инфраструктурой (`QUACK_TEST_REQUIRE_INFRA=1`):**
   ```powershell
   cd backend
   $env:QUACK_TEST_REQUIRE_INFRA="1"
   uv run pytest
   ```
   Должны проходить все существующие 305+ тестов, плюс новые тесты на:
   - ретрай `POST /v1/sessions/{id}/turns` с тем же `idempotency_key` (возврат `200 text/event-stream` без `409`);
   - ретрай `POST /v1/domains/{id}/diagnostic/start` и `/answer` с тем же `idempotency_key` (возврат `200` без `409`);
   - оценку `choice`-задачи, созданной в `diagnostic.py`, внутри `session/tasks.py`;
   - старт сессии с `topic_id=None` и последующий ход с выбором задачи из темы;
   - оценку открытого вопроса (`kind="open"`) в диагностике и в ходе сессии;
   - отбрасывание выдуманной цитаты `evidence_text`, отсутствующей в `student_text`, в `run_turn_extract`.
