# Журнал решений потока C — Фаза 4 (с 2026-11-03)

Новые решения потока C пишутся **сюда**, сверху, в момент решения (ТЗ Ф4 §0.4.5). Общий
`backend/PHASE4_NOTES.md` — архив до 2026-11-03: новые записи туда не добавляются, это убирает
конфликты слияния. Сводка всех решений — `documentation/Фаза 4 — решения (единое).md`: добавь туда
строку о решении в той же волне.

Формат записи (как в архиве):

```
### D-C-NNN. <суть>
- Класс: A | B | C (§0.4.5)
- WP / место: …; файлы
- Контекст: чего нет в документах
- Решение: …
- Почему: Т-§ / Д-§ / ТЗ
- Обратимость: …
- Тесты: …
- Требует подтверждения владельцем: да / нет
```

Номера продолжают архив: следующий свободный номер — по `grep -h "^### D-C-" backend/PHASE4_NOTES.md backend/notes/C.md`.

<!-- записи ниже, новые сверху -->

### D-C-241. Q-C-1: `timeout_ms` в тестах compute; `illustrations/` не описывается в `keys.py`
- Класс: C
- WP / место: #300 (A → C); `compute/tests/test_compute_service.py`, `backend/tests/test_ops_p4_recovery.py`
- Контекст: продуктовый `timeout_ms` 1500 делал тесты `compute` зависимыми от загрузки машины; префикс `illustrations/` нигде не описан в `Т-§3.3`, ключ строит `agents/illustration.py` (зона B).
- Решение: (1) хелпер `check` передаёт `timeout_ms=10_000` (`symbolic_background_timeout_ms`, `Т-§6`), явное значение в тесте перекрывает его, тест на таймаут (50 мс) не тронут. (2) Префикс `illustrations/` в `storage/keys.py` не описывать: ключ принадлежит B, а источник `events` находит его по `agent.completed.result_ref`; добавлен тест именно с этим событием. (3) `thumbnail_key`, `photo_crop_key` — к сведению: писателя нет; когда появится, новый источник в `orphan_objects.py` + тест (D-C-215).
- Почему: D-A-248, D-A-249 п.12; D-C-215.
- Обратимость: полная.
- Тесты: `test_an_illustration_named_only_by_agent_completed_is_kept`; `compute/tests/test_compute_service.py` с новым умолчанием.
- Требует подтверждения владельцем: нет

### D-C-240. WP23.7: экспорт темы в Obsidian и Anki и статус архива данных (`GET`-маршруты); `POST /v1/learner/export` ждёт `data_export` у A
- Класс: C (раскладка хранилища, привязка карточки к ответу, форма статуса в документах не заданы; поставка задачи - у A)
- WP / место: WP23.7, `Т-§19.6`, `Т-§19.7`, §6.4; `quack/artifacts/topic_export.py`, `quack/artifacts/export_routes.py`, `tests/e2e/routes/c.py`, `openapi.json`, `genanki` в `pyproject.toml` (назван в ТЗ §4.6)
- Контекст: ТЗ задаёт Markdown с YAML, `[[ссылками]]` по `REQUIRES`, формулами в `$...$` и колоду Anki из фиксированных карточек, но не говорит, что кладётся в архив при экспорте одной темы, что на обороте карточки (ответ живёт в задаче банка, `D-C-140`) и как выглядит состояние архива. `POST /v1/learner/export` должен писать `agent.requested {kind: data_export}`: вида нет в `AgentKind` (`quack/events/records.py`, строка 102), значит записать запрос нечем (`D-C-231`).
- Решение: (1) `GET /v1/topics/{id}/export/obsidian` - zip с одним файлом `{имя темы}.md` (имя без `\ / : * ? " < > | # ^ [ ]`); YAML: `domain` (имя из `domain.created`, иначе id), `domain_id`, `topic_id`, `date` (день выгрузки), `sources` (материал и страница из `sources` блоков, без повторов); блоки под заголовками по виду, блок с `unverified` помечен `> [!warning] не проверено`; в конце `Опирается на` и `Нужна для` со ссылками `[[имя]]` на соседние темы (`topic_requires`). Файлы соседних тем в архив не кладутся: ссылка Obsidian на несуществующую заметку допустима. (2) `GET /v1/topics/{id}/export/anki` - `.apkg`: лицо карточки - `prompt`, оборот - ответ задачи (`answer_spec`: верные варианты, `$latex$` значения, первый из `accepted`, `key_ideas`; формы без короткого ответа дают пустой оборот); формулы `$...$` становятся `\(...\)`, текст экранируется; id колоды и модели, `guid` заметки выводятся из id темы и карточки, повторная выгрузка обновляет импорт Anki, не дублирует. (3) Ответ файла идёт телом (`Content-Disposition: attachment`), не по ссылке: файл мал, собирается из записей ученика на лету и в хранилище не пишется. Чужая и неизвестная тема - 404. (4) `GET /v1/learner/exports/{id}`: `ready` - ссылка хранилища на оставшийся срок (`expires_at - now`, `export_link_ttl_hours`) и `expires_in_s`; строка `expired` или `ready` с истёкшим сроком - `expired` без ссылки (очистка может ещё не пройти); строки ещё нет, но есть `agent.requested` вида `data_export` с этим `request_id` у этого ученика - `pending`; иначе и чужой - 404. (5) `POST /v1/learner/export` не сделан. Нужно от A: `"data_export"` в `AgentKind` (`quack/events/records.py:102`) и строка `"data_export": "export.data"` в `AGENT_JOB_KINDS` (`quack/jobs/rules_p4.py:108`) с нагрузкой `{request_id}`; после этого C добавляет маршрут (ключ идемпотентности - `export_id`, `agent.requested` от `learner`, `202` с `export_id`) и запись в реестр e2e.
- Почему: `Т-§19.7`, ТЗ WP23.7, `D-C-140` (карточка - задача банка), `D-C-231`, закон 1 (запрос ученика - событие, маршрут сам задачу не ставит).
- Обратимость: код; раскладка vault и колоды - формат для ученика, менять только вперёд.
- Тесты: `tests/artifacts/test_topic_export_wp23_7.py` (vault, YAML, ссылки, имя файла, колода и её повторяемость, формы ответа, три маршрута, чужой ученик, 401, статусы `pending`/`ready`/`expired`); `tests/e2e/routes/c.py` (маршруты в `other_routes`, `export_id` в `ids`).
- Требует подтверждения владельцем: нет

### D-C-233. WP18.1/18.2: `T-AU1`–`T-AU3` проверены на реальных адаптерах через подмену транспорта; `nonce` и PKCE в потоке Google не добавлены
- Класс: C (тесты и подмена `tests/support/google_idp.py`; код `quack/**` не менялся)
- WP / место: WP18.1, WP18.2, `Т-§19.1`; `backend/tests/auth/test_auth_wire_wp18.py`, `backend/tests/auth/test_auth_adapters.py`, `backend/tests/support/google_idp.py`
- Контекст: `test_auth_routes.py` и `test_auth_wp18.py` идут на `FakeEmailSender` и `FakeOIDC`; после замены заглушек на `ResendEmailSender` и `AuthlibGoogleOIDC` (#283) ни один маршрут не проходил через реальные классы. В документах нет требования `nonce` и PKCE; `state` лежит в cookie (D-C-115).
- Решение: (1) приложение собирается с `ResendEmailSender` на `httpx.MockTransport` и `AuthlibGoogleOIDC` на `httpx2.MockTransport` (сети и настоящих секретов нет); проверяются форма запроса к Resend (метод, URL, `Authorization`, `from`, `to` в нижнем регистре, ссылка в тексте входит один раз), хеш токена в базе, отказ Resend (401/422/429/500, обрыв связи) = 503 без ключа и токена в ответе, лимиты на адрес (в любом регистре) и IP режут запрос до Resend и до записи токена; (2) Google: `state` равен значению cookie, чужой/пустой `state` и отсутствие cookie дают 401 до обращения к Google, после входа cookie state снимается и повтор даёт 401, отказ кода, подмена подписи, `alg: none`, чужие `aud`/`iss`, истёкший токен, непроверенный адрес дают 401 без сессии и без нового ученика; запрос к token endpoint несёт `code`, `redirect_uri`, `grant_type` и секрет клиента; (3) сценарный Google вынесен из `test_auth_adapters.py` в `tests/support/google_idp.py`; (4) `nonce` и PKCE не добавляю: обмен кода идёт с секретом клиента на сервере, `state` связан с браузером, документы их не требуют.
- Почему: ТЗ Ф4 §8.1, §8.11 (`T-AU1`–`T-AU3`); `CLAUDE.md` backend: ничего сверх документов.
- Обратимость: полная; тесты можно удалить, `nonce`/PKCE добавляются в `AuthlibGoogleOIDC` и `google_start` без миграций.
- Тесты: `tests/auth/test_auth_wire_wp18.py` (18 случаев), `tests/auth/test_auth_adapters.py` (+ форма запроса к token endpoint, `alg: none`)
- Требует подтверждения владельцем: нет (решение (4) — если владелец хочет `nonce`/PKCE, это новое требование)

### D-C-232. WP26.2: `meta-mock` отвечает ролям Фазы 4 по их схемам; ответы, пригодные коду, для `teacher@2`, `task_generator`, `task_checker`/`task_arbiter`, вердиктов
- Класс: C (подмена нагрузочного стенда и тесты; код `quack/**` не менялся)
- WP / место: WP26.2, `Т-§11.1`, `PROMPT_CONTRACTS.md`; `backend/loadtest/meta_mock.py`, `backend/tests/test_meta_mock.py`, `tests/e2e/test_laws_end_to_end.py` (T-E14)
- Контекст: минимальный экземпляр схемы (Фаза 3) проходит `model_validate_json` у всех 31 пары (роль, схема) реестра `tests/llm/prompt_contracts.py`, но код не может им пользоваться: у `TeacherOutputV2` нет блоков тела (ход пуст), у `TaskGeneratorOutput` задача без `tests` и без верного варианта (`validate_markup`: `no_tests`, `choice_one_correct`), проверяющий не находит ключ, уверенность вердикта 0,5 ниже `verdict_min_confidence` (0,7) и вызывает второе мнение.
- Решение: (1) схемы ролей: ответ по схеме запроса, ключи по алфавиту, как в Фазе 3; тесты обходят весь реестр (`ALL_PROMPT_SCHEMAS`), новая роль получает проверку без правки теста. (2) По имени схемы: `TeacherOutputV2` - один текстовый блок `b1` длиной `META_MOCK_TEACHER_CHARS`; `TaskGeneratorOutput` - одна задача, проверяющая навык `S1`: выбор с верным вариантом `A` на уровне 1, а для вида `number`/`math`/`expression`/`quantity` (блок `### Kind of problem`) - число `2` на уровне 2; `TaskCheckOutput` (checker и arbiter) - первый вариант из блока `### Options` либо `2`, `levels_fit = [true]`, метод не назван (ключ совпадает, разметка не оспорена); `ModelVerdictOutput` - `confidence = 0,9` (`VERDICT_CONFIDENCE`). (3) Не сделано: подмена SAM (`segmenter`) и Voice (`/asr/transcribe`), подмена эмбеддингов в профиле `loadtest`: формы запросов `sam-3.1`, Voice и пути подключения клиента эмбеддингов помечены [ПРОВЕРИТЬ на стенде] (`quack/llm/media.py`) или требуют правки сборки процессов; остаются в WP26.2 до проверки на стенде.
- Почему: `Т-§11.1`, ТЗ Ф4 WP26.2, DoD п.7 (smoke на стенде не должен давать вырожденные ходы и `LLM_DEGRADED`); закон 4: вердикт стенда не зовёт второе мнение сам.
- Обратимость: код подмены; значения (`S1`, `A`, `2`, 0,9) - константы файла.
- Тесты: `tests/test_meta_mock.py` (на каждую пару реестра: провод и поток; ход, задача, проверяющий, вердикты); T-E14 в `tests/e2e/test_laws_end_to_end.py` дополнен: вход аналитика не содержит текст и манеру прошлой сводки.
- Требует подтверждения владельцем: нет

### D-C-231. WP23.7: задача `export.data` - архив данных ученика одним zip, строка `content.exports`, ответ `agent.completed`
- Класс: C (состав и раскладка архива, срок, идемпотентность в документах не заданы; в части поставки задачи нужны правки A, см. ниже)
- WP / место: WP23.7, Т-§19.6, П-14.4, issue #263; `quack/artifacts/data_export.py`, `quack/jobs/kinds.py`, `quack/runtime/__init__.py`
- Контекст: ТЗ перечисляет содержимое (события JSONL, документы JSON, объекты файлами, артефакты читаемо), хранилище и срок ссылки; раскладки и состояния нет. Таблица `content.exports` и очистка `exports.cleanup` (D-A-215) уже есть у A. Вида `data_export` нет в `AgentKind` (`quack/events/records.py`) и в `AGENT_JOB_KINDS` диспетчера, поэтому событие `agent.requested {kind: data_export}` записать нельзя и задачу `export.data` диспетчер не ставит.
- Решение: (1) нагрузка задачи `{request_id}`; `request_id` и есть `export_id`. (2) Архив: `events.jsonl` (события ученика по `seq`, страницами по 1000), `documents/{схема.таблица}.json` для таблиц с `learner_id` из `DOCUMENT_TABLES` (без `core.auth_*`, `content.exports`, `ops.*`), `objects/…` (файлы префикса ученика кроме `exports/`), `artifacts/{kind}/{тема|хаб}.md` (текущая версия читаемо). Obsidian/Anki (`GET /v1/topics/{id}/export/*`) сюда не входят. (3) Ключ архива `export_key` (`learners/{id}/exports/{export_id}-{ulid}.zip`); строка `content.exports` становится `ready`, `expires_at = now + export_link_ttl_hours`, и `agent.completed {kind: data_export, request_id = export_id, result_ref = ключ}` с ключом `agent.completed:{export_id}` пишутся одной транзакцией. (4) Закон 5: строка `ready` или `expired` повторно не собирается. (5) Закон 7: архив кладётся в хранилище до транзакции; если транзакция не прошла, объект удаляется, ошибка уходит в очередь. (6) Архив собирается в памяти (потолка размера нет; для объёмов Фазы 4 достаточно).
- Почему: Т-§19.6, ТЗ WP23.7, законы 5 и 7, D-B-191 (ключ `agent.completed`), D-A-215 (`expires_at`, `expired`).
- Обратимость: код; раскладка архива - формат для ученика, менять только вперёд.
- Тесты: `tests/artifacts/test_data_export_wp23_7.py` (состав и изоляция, повтор, отказ хранилища, `expired`, регистрация).
- Требует подтверждения владельцем: нет. Нужно от A: `data_export` в `AgentKind` (новая версия схемы `agent.requested`/правка v1 как D-C-143), строка диспетчера `agent.requested {data_export}` -> `export.data` `{request_id}`; после этого C добавит `POST /v1/learner/export` и `GET /v1/learner/exports/{id}` (маршруты не сделаны: записать запрос нечем).

### D-C-230. WP23.2: задача `agent.artifact` - конспект или карточки темы по запросу, ответ `agent.completed`
- Класс: C (в документах не заданы: что делает задача при запросе без темы, при `artifact` не указанном, ключ ответа)
- WP / место: WP23.2, §6.5, issue #263; `quack/artifacts/agent_artifact.py`, `quack/jobs/kinds.py`, `quack/runtime/__init__.py`
- Контекст: диспетчер ставит `agent.artifact` для каждого `agent.requested {kind: artifact}` кроме шпаргалки хаба (она в `artifact.cheatsheet`); вид и тема - в `params.artifact`, `params.topic_id` (D-C-143), в нагрузке задачи только `{request_id, kind}`. Писатель, проверки и запись версий уже есть в `artifact.topic_refresh` (D-C-125, D-C-139, D-C-154).
- Решение: (1) Обработчик читает `agent.requested` по `request_id` и вызывает `refresh_topic_notes` и/или `refresh_topic_cards` с ключом `request:{request_id}` (`artifact: notes` - конспект, `cards` - карточки, не указан - оба). (2) Ответ `agent.completed {kind: artifact, ok, result_ref = artifact_id первого записанного}` с ключом `agent.completed:{request_id}`, причина связана с запросом (`causation_seq`). (3) `ok: false` без повтора: `topic_required` (нет темы), `unsupported_artifact` (шпаргалка не идёт этим видом), `nothing_written` (тема неизвестна графу, нет ни одного допустимого блока или карточки, потолок карточек). (4) Ошибка модели или хранилища уходит исключением в очередь, ничего не пишется (закон 7); готовый ответ найден до вызова модели (закон 5). (5) Кто пишет `agent.requested {artifact}` для конспекта и карточек, не определено: `POST /v1/artifacts/requests` в ТЗ есть, в коде его нет, маршрут не сделан. (6) `agent.completed` пишется через `quack/artifacts/delivery.py`: копия `quack.agents.delivery` (D-B-191) с тем же ключом `agent.completed:{ref}`, потому что слои §4.1 не пускают `artifacts` в `agents` (T-A6).
- Почему: ТЗ WP23.2 («поручение учителя или просьба ученика - любой артефакт»), D-C-143, D-B-191.
- Обратимость: код; набор причин `ok: false` - константы.
- Тесты: `tests/artifacts/test_agent_artifact_wp23_2.py`.
- Требует подтверждения владельцем: нет.
### D-C-218. WP21.3 п.4, WP21.4 п.4: обработчики `second_opinion.diagnosis` и `canvas.rediagnose` в `quack/canvas/jobs.py`
- Класс: C (ТЗ задаёт вид и ключ; тело задачи, пропуски и повтор при недоступной модели не заданы)
- WP / место: WP21.3 п.4, WP21.4 п.4, #263 (A -> C), #271 п.1 (B: D-B-217 оставляет обработчик за C); `quack/canvas/jobs.py`, `quack/canvas/step_check.py` (`diagnosed_key` стал публичным), строки процедуры #173 в `quack/jobs/kinds.py` (`JOB_KINDS`, `KIND_LLM_ROLES`) и `quack/runtime/__init__.py` (`HANDLER_MODULES`)
- Контекст: правила диспетчера (`rules_p4.py`) ставят обе задачи, обработчиков не было, задачи копились в `queued`. Сокет уже делает то же сам (`check_step`, `second_opinion`), так что обработчик повторяет вызов из журнала, а не из памяти процесса API.
- Решение: (1) `second_opinion.diagnosis`: по `trigger_seq` берётся `observation.diagnosed`; не `question` - выход; `observation.second_opinion:{seq}` уже есть - выход без вызова модели; холст без задачи или строка стёрта - выход; строка исправлена после диагноза (в ключе диагноза хэш текста, он уже другой) - выход: то прочтение судит `canvas.rediagnose`. Иначе вызов `step_check.second_opinion` с тем же ключом события. Если модель недоступна (`second_opinion` вернул `None`) - обычное исключение, задача повторяется с задержкой; пока модели нет, пометка остаётся вопросом. Нет события диагноза или холста ученика - `JobFailedPermanently`. (2) `canvas.rediagnose`: строка берётся из журнала с применёнными исправлениями, предыдущие строки - как в сокете, затем `check_step`. Ключ диагноза включает хэш текста, поэтому исправленное прочтение - новый диагноз, повтор задачи ничего не пишет и никого не спрашивает. Неуверенный диагноз ставит свою `second_opinion.diagnosis` через диспетчер, из этой задачи второе мнение не вызывается. Стёртой строки и холста без задачи нет в работе. Недоступная модель диагноста, как и в сокете, даёт «ошибки нет» (`check_step` не различает), задача не повторяется: пропущенная ошибка дешевле ложной (D-C-158). (3) Чтение памяти для диагноста - `create_graph_reader(ctx.neo4j, ...)`; при сбое диагност работает без известных заблуждений (D-C-158 п.6).
- Почему: ТЗ WP21.4 п.4 «неуверенный диагноз - второе мнение, `second_opinion.diagnosis`, P1», WP21.3 п.4, закон 5 (ключ события), закон 7; #263, D-B-217.
- Обратимость: код. Снятие `canvas.rediagnose` из диспетчера (A) оставит сокет единственным путём. Старая пометка на доске при исправлении прочтения здесь не снимается: это решает шлюз вмешательств в сокете (WP21.5).
- Тесты: `tests/canvas/test_canvas_jobs_wp21.py` (регистрация по #173, запись и повтор, пропуски, недоступная модель, повторный диагноз исправленной строки, стёртая и чужая).
- Требует подтверждения владельцем: нет

### D-C-217. WP20.2, WP12.5: задачи семинара из `material.parse` (`seminar_tasks` у `material_parser@1`, вызов `create_seminar_task`)
- Класс: B (формат полей дал B в #271 п.2; привязка к навыку, ключ и порядок шагов в документах не заданы)
- WP / место: WP20.2 п.4, WP12.5, #271; `quack/materials/parse.py` (`ParsedSeminarTask`, `SeminarSpec`, `SeminarWriter`, `structure_step`), `quack/ops/material_parse.py` (`create_seminar_tasks`), `quack/config.py` (`material_seminar_tasks_max`), `quack/prompts/material_parser/v1.md`, `PROMPT_CONTRACTS.md`, `manifest.json`
- Контекст: B прислал формат `seminar_tasks: [{statement, answer|null, chunk "P...", page|null}]` (D-B-217) и функцию `create_seminar_task(db, gateway, settings, SeminarTask, *, learner_id, compute)` (D-B-218). `quack.materials` не импортирует `quack.practice` (§4.1).
- Решение: (1) Слой 2: поле `seminar_tasks`; ссылка на кусок - `P` и номер куска в пачке (`P2` для второго куска). Кусок вне пачки заменяется первым куском указанной страницы, иначе задача отбрасывается; пустое условие и повтор условия (без учёта регистра и пробелов) отбрасываются; не больше `material_seminar_tasks_max` (30). Страница в задаче - страница куска. Первый шаг кладёт задачи в `payload` (с `chunk_id`). (2) Шаг структуры после сверки навыков и до записи событий и статуса `parsed` вызывает через шов `SeminarWriter` (в сборке `quack/ops/material_parse.py`) `create_seminar_task` на каждую задачу, параллельно не больше `material_parser_concurrency`. Навык - первый связанный сверкой навык из `teaches` куска, у куска без `teaches` - из `uses`; нет навыка - задача пропущена с записью в лог. Тема - тема куска, иначе название материала. Уровень и вид - по умолчанию B (`3`, `expression`): в метках куска их нет. `course_context` - название материала. (3) Ключ `seminar:{material_id}:{n}`, `n` - место в списке `payload` (он один на все повторы шага). Задачи пишутся до статуса `parsed`: ошибка модели повторяет шаг, ключи не дают записать дважды, `None` от B (выброшена проверкой) - не ошибка. (4) `reconcile_domain` вызывался из шага структуры и раньше (`reconcile_subjects`, origin `material`); добавлен тест, что обработчик зовёт его и передаёт id узла навыка в задачу.
- Почему: WP20.2 п.4 «задачи семинара из материала», `Т-§5.6`, закон 5 (ключ), закон 7 (шаг либо весь, либо ничего: события материала пишутся после задач и одной транзакцией со статусом), #271 п.2.
- Обратимость: код и слой 2 `material_parser@1` (черновик; поле остаётся в контракте). Привязка к `uses` - допущение для листов задач, владелец может ужесточить до `teaches`.
- Тесты: `tests/materials/test_seminar_wp20.py`; `test_parse_wp20.py` (схема), `tests/llm` (T-M5).

### D-C-191. WP26.1: сквозные тесты T-E1…T-E14 на стенде Фазы 4; красные места и что нужно от A и B
- Класс: C (только тесты и подмены; код `quack/**` не менялся)
- WP / место: WP26.1, WP26.2 (тесты); `tests/e2e/{text_journey,test_learner_journey,test_laws_end_to_end,test_classify_and_summary,test_deletions,test_failure_modes,test_photo_and_materials,test_canvas_journey,test_hub_deadline_today}.py`, `tests/e2e/{system,conftest}.py`, `tests/support/graph_dump.py`
- Контекст: стенд D-C-190 работает на процессах Фазы 4. Тесты T-E1…T-E14 (§8.14) пишутся по сценариям таблицы. Где код других потоков не даёт описанного поведения, тест остаётся красным как `xfail(strict=True)` с причиной в `reason`: когда поток исправит код, строгий `xfail` упадёт и маркер снимают.
- Решение: зелёные: T-E2, T-E3 (были), T-E4 (дамп подграфа ученика после `rebuild_learner` равен дампу проектора), T-E5 (два невалидных ответа извлекателя, повтор задачи вручную через `run_at`), T-E6 (канвас по задаче, `mark` после `error_grace_ms`, исправление строки, подсказка, вердикт с `help_step = 1`), T-E7 первая половина (PDF, `material.parse`, куски, эмбеддинги), T-E8 (привязка, два снимка, только новая строка), T-E9 первая половина (дедлайн из разметки, хаб, `today` с причиной `deadline` и `pace_warnings`, подготовленный план), T-E10 (`pause_background`, три хода со `projector_lag`, догоняние), T-E11 (строки redis, modal, storage, compute, r2, resend), T-E12, T-E13, T-E14 (нет UUID в запросах к моделям, нет полей силы в схемах извлекателя и проверок). Красные (`xfail`, причина и нужное от потока):
  1. T-E1: B, `quack/session/turn_source.py::assemble` читает журнал сессии до `seq` ответа (`to_seq=request.action_seq`), а мгновенный вердикт записан сразу после ответа; `answered.solved` всегда ложно, ход после верного ответа не пишет `task.closed`, эпизод остаётся открытым. Нужно читать до конца пачки действия (вердикты с `answer_seq` = действию). Проверено локальной правкой `to_seq + 5`: цепочка `verdict.recorded → task.closed → episode.closed → extract.episode → observation.extracted` и фолд проходят.
  2. T-E11 `meta`: B, `turn_source` ставит `llm_unavailable=False`; недоступный провайдер даёт `error` LLM_DEGRADED, а не ход с `degraded_mode = llm_unavailable` (ТЗ §4.4).
  3. T-E11 `neo4j`: A/B, `ServiceUnavailable` Neo4j выходит из сборки хода (`turn_failed`), а не даёт `memory_unavailable`.
  4. T-E7 вторая половина: B, `source_of` создаёт `MemoryTurnContextSource` без клиента эмбеддингов, у `ToolContext` нет `search`, `search_materials` в боевом процессе всегда отвечает «search temporarily unavailable» (WP14.4).
  5. T-E9 вторая половина: B, ничто не подхватывает подготовленный план (`prepared_session_id`) при старте сессии из пункта «Сегодня» (WP16.8, WP13.6).
  Не покрыто: T-E14 «вход аналитика без прошлой сводки» (в сценарии аналитик не запускается), T-E4 не включает T-E7 в сравниваемый журнал. Заметка для A: после `observation.extracted` с верным элементом уровня 1 узел `Skill` не обновился (`asOfSeq` остался на последнем событии до наблюдения, `l1SuccessW = 0`), Evidence в графе есть; если свёртка должна учитывать доказательство модели, это красный T-E1 после п.1.
  Обработчика `canvas.rediagnose` нет (зона A): задача ставится диспетчером и остаётся в очереди; `System.settle` ждёт только виды задач с обработчиком (`handled_kinds`), это не прячет сбой, другой вид останется виден по тайм-ауту.
  WP26.2: `meta-mock` под новые роли не менялся (остаток: схемы ролей `Т-§11.1`, подмена эмбеддингов в профиле `loadtest`); WP26.3 (Locust) вне задачи.
- Почему: ТЗ Ф4 §8.14, WP26; красная линия 5 (утверждения не ослаблены, красное помечено, а не удалено).
- Обратимость: тесты.
- Тесты: перечислены выше; `tests/e2e/` целиком.
- Требует подтверждения владельцем: нет

### D-C-216. WP23.6: скиллы ученика (`/v1/learner-skills`), системные шаблоны (`/v1/system-templates`), привязка к артефактам
- Класс: C (в документах не заданы: форма `bindings`, правила имён, какие промпты считаются «системными шаблонами», идемпотентность без события)
- WP / место: WP23.6, Т-§17.8, П-11.6; `quack/artifacts/learner_skills.py`, `skills_routes.py`, `topic_refresh.py`, `hub_cheatsheet.py`, `config.py`
- Контекст: таблица `content.learner_skills` и вызов `/имя` в чате (B, `turn_inputs.load_learner_skill`, ищет последнюю версию по имени) уже есть; слот `learner_skill` писателя был заглушкой `None.`.
- Решение: (1) `GET/POST /v1/learner-skills`, `PATCH/DELETE /v1/learner-skills/{id}`; тело POST `{idempotency_key, name, instruction, bindings: {artifact_types: [notes|cards|cheatsheet]}}`; ответ `{skill_id, version, name, instruction, bindings}`. (2) Имя: 1-40 знаков `\w` или `-`, приводится к нижнему регистру, ведущий `/` отбрасывается; уникально среди скиллов ученика, повтор - 409. На тип артефакта - не больше одного скилла (409), чтобы `artifact_versions.skill_version` был однозначным. Длина инструкции `learner_skill_max_chars` = 2000. (3) Каждое изменение - новая версия `(skill_id, version+1)`; PATCH без изменений версию не пишет (повтор безопасен); переименование пишет новое имя во все версии, иначе `/старое` в чате находило бы старую версию; DELETE удаляет все версии (артефакты, написанные по скиллу, остаются). (4) POST: `skill_id = uuid5(ученик, ключ)`, повтор с тем же запросом отдаёт тот же скилл, другой запрос под тем же ключом - 409 (T-E2); PATCH и DELETE без ключа (нет события и столбца ключа), причины - в `tests/e2e/routes/c.py`. (5) Писатель: `learner_skill` = текст скилла, привязанного к типу (`notes`, `cards`, `cheatsheet`), иначе `None.`; версия скилла пишется в `artifact_versions.skill_version`; смена скилла ничего не перегенерирует (Т-§17.8). (6) `GET /v1/system-templates` отдаёт файлы промптов с версиями и sha256 только для чтения; показываются роли `teacher`, `artifact_writer`, `artifacts` (константа `SYSTEM_TEMPLATE_ROLES`), остальные (судьи, проверки, разбор материалов) не показываются.
- Почему: Т-§17.8 («Только новое», версии, системные шаблоны только для чтения); ТЗ WP23.6; проверку связи с памятью делает код на выходе, скилл меняет стиль.
- Обратимость: код; набор ролей шаблонов - константа; смена формы `bindings` потребует миграции данных. Вызов в чате и хвост хода - у B (T-CX6), здесь только хранение и привязка.
- Тесты: `tests/artifacts/test_learner_skills_wp23_6.py`; реестр маршрутов `tests/e2e/routes/c.py`.
- Требует подтверждения владельцем: да (какие роли показывать как системные шаблоны; «один скилл на тип артефакта»).

### D-C-215. WP9.6: `orphan-objects` по умолчанию ничего не удаляет (`--apply`), перечень источников ссылок в одном списке
- Класс: C (безопасность ops-команды; уточняет D-C-211, текст Д-§8.3 шаг 8 «Они удаляются» не меняется по сути)
- WP / место: WP9.6, Д-§8.3; `quack/ops/orphan_objects.py`, `quack/cli.py`, `deploy/README.md`
- Контекст: D-C-211 удалял файлы по умолчанию, а `--dry-run` надо было вспомнить; ссылки на файлы были тремя строками SQL, тест лишь на часть из них. Пропущенный источник ссылки стоит файла ученика.
- Решение: `ops orphan-objects --since <t>` без флага только строит отчёт; удаление только с `--apply`; `--dry-run` убран (старый вызов с ним даёт ошибку аргументов, а не удаление). `orphan_objects(..., apply=False)`; отчёт `OrphanReport{checked, orphans, deleted, dry_run}`. Источники ссылок - один кортеж `REFERENCE_SOURCES` (`materials.object_key`, `material_pages.image_key`, `exports.object_key`, `core.events.payload`, `artifact_versions.content`) и `CANVAS_SOURCE` (`canvas_boards` по префиксу холста); `SOURCE_NAMES` - их имена. События и версии артефактов ищутся по ученику ключа.
- Почему: удаление необратимо; безопасное поведение по умолчанию; тест на каждый источник ловит забытое место ссылки.
- Обратимость: код. Новое место ссылки на ключ (таблица, колонка) - строка в `REFERENCE_SOURCES` и случай в `_insert_reference` (иначе `test_every_reference_source_has_a_test_case` красный).
- Тесты: `tests/test_ops_p4_recovery.py` (`test_a_live_file_named_by_any_source_is_never_deleted` по шести источникам и режимам, `test_every_reference_source_has_a_test_case`, разбор аргументов: `--dry-run` отвергается).
- Требует подтверждения владельцем: нет (сужает D-C-211); определение «ссылка» из D-C-211 по-прежнему ждёт подтверждения.
### D-C-214. WP20.2: PDF без текстового слоя читается распознавателем
- Класс: C (закрывает п.4 D-C-202; пороги — параметры)
- WP / место: WP20.2 п.1, Т-§16.5; `quack/materials/parse.py::_read_scans`, `quack/config.py`
- Контекст: ТЗ: «PDF — `/v1/pdf/pages`; изображение — распознавание `recognizer`». Скан PDF даёт страницы без текста (D-C-202 п.4), и разбор оставался пустым.
- Решение: (1) Страница PDF, чей текст короче `material_scan_min_chars = 20` знаков, читается по своему изображению `recognizer` в режиме листа (тот же `_read_sheet`, что у фото: строки не-рисунки и не зачёркнутые). Выравнивание compute не вызывается: страница уже отрисована. (2) Читаются первые `material_scan_max_pages = 50` таких страниц файла, параллельно (`material_parser_concurrency`); остальные остаются пустыми, в журнал идёт `material_scan_truncated`. (3) Ключ идемпотентности вызова — `recognizer:material:{id}:{page}`; повтор после сбоя находит прежний ответ. Ошибки шлюза не глотаются: задача повторяется.
- Почему: WP20.2 п.1, Т-§16.5, D-C-202 п.4, §0.4.3 (числа в `Settings`).
- Обратимость: параметры и код.
- Тесты: `test_a_pdf_page_without_a_text_layer_is_read_by_the_recognizer`, `test_a_scan_is_read_up_to_the_cap_of_pages`.
- Требует подтверждения владельцем: нет (50 страниц - стартовое значение; цена скана в вызовах `recognizer`)

### D-C-213. WP20.2: дедлайны силлабуса — `hub.deadline_set {source: syllabus}`
- Класс: B (ТЗ: «из силлабуса — структура курса и дедлайны (`hub.deadline_set`, Т-§4.8)»; как парсер отдаёт даты и как они превращаются в хабы - не сказано)
- WP / место: WP20.2 п.4, Т-§4.8; `quack/materials/parse.py` (`ParsedDeadline`, `_syllabus_events`), `quack/prompts/material_parser/v1.md`, `PROMPT_CONTRACTS.md`, `manifest.json`
- Контекст: `material_parser@1` размечал только куски; отдельного вида материала «силлабус» в `content.materials` нет, и он не нужен: даты видны в тексте.
- Решение: (1) Схема ответа получила поле `deadlines [{label, date, control_type, covers}]` (пустое почти всегда). `date` - ISO 8601 с годом (`YYYY-MM-DD` или `YYYY-MM-DDTHH:MM`); дату без года, историческую дату и выдуманную парсер не отдаёт (слой 2). `control_type` - один из `tasks`, `colloquium_proofs`, `oral_theory` (`no_goal` для контроля не имеет смысла). `covers` - темы словами материала. (2) Шаг 1 склеивает ответы пачек (одно имя в разных регистрах и дата - один контроль; не больше `material_deadlines_max = 20`) и кладёт в полезную нагрузку шага 2. (3) Шаг 2: темы из `covers` идут в сверку вместе с темами кусков (структура курса). На каждый контроль: `hub.created {kind: material, origin: material, control_type, name = label}`, `hub.topics_changed` (темы из `covers`, уровни по `control_type_levels`; без распознанных тем события нет) и `hub.deadline_set {source: syllabus}`. Всё в той же транзакции, что `material.parsed`; ключи `…:syllabus:{material}:{hub_id}`, `hub_id` = `uuid5` от материала, названия и даты. (4) Время: дата без времени - конец этого дня, 23:59; время без пояса - в поясе ученика (`core.learners.settings.timezone`, иначе UTC). Дата в прошлом и нечитаемая дата пропускаются (запись `syllabus_deadline_skipped`). (5) Плашка «добавлено» с отменой - `PATCH /v1/hubs/{id}` (WP6.3), своих окон нет.
- Почему: WP20.2 п.4, Т-§4.8, П-8.1 (дедлайн пишется сразу), Т-§19.8 (текст - данные: парсер размечает, код проверяет дату и тип).
- Обратимость: код и слой 2 `material_parser@1` (черновик, владелец может заменить пакетом; поле `deadlines` остаётся в контракте). Задачи семинара (`task.created`) по-прежнему ждут разметку WP12.5 (B).
- Тесты: `test_a_syllabus_gives_hubs_with_deadlines`, `test_a_material_without_dates_writes_no_deadline`, `test_deadlines_are_capped_by_the_setting`, `test_the_prompt_material_parser_v1_has_its_contract`, `tests/llm` (T-M5).
- Требует подтверждения владельцем: нет

### D-C-212. WP9.5: `GET /internal/status` в приложении `api`
- Класс: C
- WP / место: WP9.5, Д-§9.1; `quack/api/app.py` (`_internal_router`), `tests/e2e/routes/c.py`, `tests/test_ops_p4_status.py`
- Контекст: ТЗ WP9.5 требует внутренний эндпоинт сводки с содержимым `ops status`; в коде его не было (Caddyfile уже отдаёт `/internal/*` кодом 404).
- Решение: `GET /internal/status` вызывает `collect_status` на ресурсах процесса и отвечает отчётом: 200, либо 503, если Postgres не отвечает (код 2 команды). Маршрут вне схемы OpenAPI (`include_in_schema=False`), без входа ученика: защита — сеть Docker и Caddy, который его не проксирует. Вне реестра маршрутов `tests/e2e/routes` (реестр сверяется со схемой OpenAPI).
- Почему: ТЗ Ф4 WP9.5, Д-§9.1.
- Обратимость: код.
- Тесты: `test_internal_status_serves_the_report_of_ops_status`.
- Требует подтверждения владельцем: нет.

### D-C-211. WP9.6: `orphan-objects`, `resume`, `replay-deletions`, `rebuild --concurrency`
- Класс: C (определение «есть ссылка в Postgres» для `orphan-objects` в документах не дано)
- WP / место: WP9.6, Д-§8.3, Д-§8.7; `quack/ops/orphan_objects.py`, `quack/cli.py`, `deploy/README.md`
- Контекст: функции A из S15 влиты (`replay_deletions`, `reconcile_deletions` в `deletion/journal.py`, `resume` в `graph/projection/runner.py`); функции `orphan_objects` у A нет, она в зоне C (ops).
- Решение: `ops resume --learner`, `ops replay-deletions --since` (печатает `{"replayed": N}`), `ops rebuild ... --concurrency N` (по умолчанию `rebuild_concurrency`; ученики пересобираются по N одновременно, код выхода 1, если хоть один не `rebuilt`). `ops orphan-objects --since [--apply]` (с D-C-215; был `--dry-run`) смотрит объекты `learners/` новее метки: объект держат ссылки: ключ в `content.materials.object_key`, `material_pages.image_key`, `exports.object_key`; ключ в `payload` события или в `content` версии артефакта того же ученика; для кусков холста `learners/{l}/canvas/{s}/{c}/…` - строка `content.canvas_boards` (куски находятся по префиксу, ключа в базе нет). Остальное удаляется только с `--apply`, без него только список. Печатает JSON `{checked, orphans, deleted, dry_run}`. Перечень источников ссылок - `REFERENCE_SOURCES` в `orphan_objects.py` (D-C-215). Поиск ключа в событиях идёт по ученику ключа (`strpos(payload::text, key)`): дорого на большом журнале, но команда одноразовая, после восстановления, и объектов новее дампа немного.
- Почему: ТЗ Ф4 WP9.6, Д-§8.3 шаги 5 и 8, Д-§8.7; T-OPS4.
- Обратимость: код. Удаление необратимо, поэтому по умолчанию команда только сообщает (D-C-215). Если появятся новые места ссылок на ключи (таблица, колонка), их надо добавить в `orphan_objects.py`, иначе команда удалит живой файл.
- Тесты: `tests/test_ops_p4_recovery.py`.
- Требует подтверждения владельцем: да (риск удаления файлов ученика при неполном списке ссылок).

### D-C-210. WP9.3: шаги CI (типы фронтенда, `modal/`, изоляция и один писатель), тесты порядка выкатки, ссылки OpenAPI
- Класс: C (зона C: `.github`, `deploy`, `openapi.json`)
- WP / место: WP9.3, WP9.4; `.github/workflows/ci.yml`, `quack/api/openapi.py`, `tests/test_ci_layers.py`, `tests/test_deploy_order.py` (T-OPS8), `tests/test_app_assembly.py`
- Контекст: п.7 `Д-§5.2` («типы фронтенда из OpenAPI совпадают») требует Node, пакета нет в ТЗ §4.6; `modal/` не имел проверок; изоляция и один писатель шли только внутри полного прогона; `backend/openapi.json` не собирался генератором типов: у `/auth/*` и `/companion/pair` в ответе 422 остался `$ref` на удалённый `HTTPValidationError`.
- Решение: (1) job `frontend-types` (только pull_request, не в `needs` у `publish`, ожидаемо красный до перевода фронтенда, R4-2): `npm ci --ignore-scripts` в `frontend/` по его lock-файлу (генератор `openapi-typescript` уже в `frontend/package.json`, новых зависимостей и actions нет, файлы фронтенда не меняются), генерация из `backend/openapi.json` теми же флагами, что в `api:types`, и `cmp` с `frontend/lib/api/schema.d.ts`. (2) `modal/` в job `fast`: `ruff check`, `ruff format --check` с настройками backend и `pyright ../modal` (пакет `modal` есть в backend, ТЗ §4.6). (3) Перед полным прогоном job `backend` отдельные шаги: изоляция (T-DB4, T-E3) и один писатель (T-A9a, T-A9b). (4) Выкатка (WP9.4): job `deploy` с окружением `production`, `concurrency: deploy` и пропуском при `DEPLOY_FREEZE` уже были; добавлены тесты (`test_ci_layers`) и `test_deploy_order.py` (T-OPS8): порядок шагов `Д-§6.1` и запрет автоотката при новых событиях, правилах, миграциях читаются по тексту `deploy.sh` (режима проверки в скрипте нет). (5) `quack/api/openapi.py`: ссылка 422 на удалённую модель заменена на `ErrorResponse` для всех маршрутов; тест `test_openapi_refs_all_resolve`; `openapi.json` пересобран.
- Почему: `Д-§5.2` п.5, п.7, ТЗ Ф4 WP9.3, WP9.4, R4-2, §4.6, §8.13 (T-OPS8).
- Обратимость: код и workflow.
- Тесты: `tests/test_ci_layers.py`, `tests/test_deploy_order.py`, `tests/test_app_assembly.py::test_openapi_refs_all_resolve`.
- Требует подтверждения владельцем: нет (обязательным `frontend-types` делает владелец после перевода фронтенда).
### D-C-190. WP1.3 шаг 3 C, тесты: зона C без Фазы 3, стенд e2e на процессах Фазы 4, остаток Фазы 3 собран в `tests/support/phase3_*`
- Класс: C (только тесты и подмены; код `quack/**` не менялся)
- WP / место: WP1.3 шаг 3 (issue #215), WP26.1; `tests/support/{resources,dispatch,auth,phase3_pg,phase3_events}.py`, `tests/conftest.py`, `tests/factories.py`, `tests/e2e/{system,conftest,journey,test_isolation_and_idempotency}.py`, `tests/test_infra.py`, `tests/test_app_assembly.py`, `tests/{artifacts,canvas,auth,materials,storage}/*`, `tests/test_stand_dbmate.py`, `compose.yaml`, `deploy/README.md`, `.github/workflows/deploy.yml`
- Контекст: тесты A/B (`session`, `domains`, `jobs`, `test_learner_stream`, `test_worker_and_ops`, `test_dispatcher`, `test_events_and_append`, `test_projector_determinism`, `test_session_overlay`, `test_neo4j_outage`, `test_jobs_phase3`) ещё просят фикстуры `pg_engine`, `db` (Alembic, SQLAlchemy), `ensure_user`, `EventLog`/`new_event`/`sample_payloads` и зовут `build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db)` / `build_job_context(settings, db, None, None, engine=...)`. Править их нельзя, ломать - тоже. `JobContext` в `quack/jobs/runner.py` (зона A) пока имеет поля `session_factory` и `engine`.
- Решение: (1) `build_app_resources` и `build_job_context` берут `settings, neo4j, redis, db=pg_db, ...`; старая форма (лишний пул перед `neo4j`, ключи `session_factory=`, `engine=`) принимается и отбрасывается, у `JobContext` оставшиеся поля Фазы 3 заполняются из неё или `None` (без их имён в файле). Зона C старую форму не использует. (2) Фикстуры `pg_engine`, `db`, `ensure_user` переехали из `tests/conftest.py` и `tests/support/auth.py` в `tests/support/phase3_pg.py` (подключён через `pytest_plugins`; `auth.py` реэкспортирует `ensure_user`); построители `EventLog`, `new_event`, `sample_payloads` - в `tests/support/phase3_events.py` (`tests/factories.py` реэкспортирует). A снимает оба файла и реэкспорты одним коммитом вместе с последним тестом Фазы 3 (шаг 5). (3) `tests/support/dispatch.py` - на `DispatcherP4` и `project_learner`; `mark_dispatched` пишет `ops.dispatcher_offsets` по `core.learners.seq`. (4) Стенд e2e: `projector_process` (проектор `rules@3` и диспетчер) и `workers_process` (исполнитель очереди Фазы 4 и такт периодических задач) задачами asyncio; процессы открывают свою `Db` по DSN базы Фазы 4 (`p4_database`), `consumer_poll_ms=100`; `settle()` смотрит `core.learners.seq`, `ops.projector_offsets.seq`, `ops.dispatcher_offsets`, `ops.jobs`; ученик - строка `core.learners` и сессия, `onboard` - `POST /v1/domains` и `topic.created`, `skill.created` в журнале Фазы 4. (5) `PHASE4_PROCESSES` снят из compose, README выкатки, workflow и теста стенда.
- Почему: критерий WP1.3 (T-DB3) - нет `sqlalchemy`, `asyncpg`, `quack.db.postgres`, `quack.jobs.queue`, `quack.events.append|reader|catalog`, `quack.graph.worker`, `quack.jobs.dispatcher`, `session_factory` в тестах зоны C; красная линия 5 - утверждения не ослаблены.
- Обратимость: переходник п.1 и два файла `phase3_*` исчезают одним коммитом A; стенд п.4 - основа `T-E1`.
- Тесты и «Правки тестов» (§8.15): `test_infra.py::test_migration_creates_contract_indexes` читает базу Фазы 4; убраны проверки `uq_sessions_one_active` (единственная активная сессия снята, П-4.10) и `uq_artifacts_topic_cheatsheet` (в схеме dbmate нет такой таблицы), остальные индексы и `UNIQUE (learner_id, idempotency_key)` проверяются; `test_fts_index_query_finds_text_events`, `test_advisory_locks_*` перенесены на `append_events(db)` и `Db.advisory_lock` с теми же утверждениями; `test_app_assembly::sign_in` не пишет `auth.users` (никто не читает); `test_stand_dbmate::test_the_processes_of_phase_4_need_no_flag` вместо проверки флага проверяет его отсутствие. `T-E1` целиком не написан: остаток WP26.1 - текстовый ход на новом стенде (`/v1/sessions`, сообщение, ответ, `settle`, события и задачи по журналу), красные `T-E2`–`T-E14`.
- Требует подтверждения владельцем: нет

### D-C-170. WP1.3 шаг 3 C: артефакты и сборка API без SQLAlchemy - маршруты Фазы 3 сняты, `AppResources` без `engine` и `session_factory`
- Класс: C (зона C; формат событий и схемы БД не менялись); две правки в зоне A - п.5
- WP / место: WP1.3 шаг 3 (issue #215, #235), WP23, WP25, §6.4 «Что заменено»; `quack/artifacts/{notes,cheatsheets,search,store,deps,router}.py` (удалены), `quack/artifacts/schemas.py`, `quack/api/{app,resources,auth}.py`, `tests/support/resources.py`, `tests/e2e/routes/c.py`
- Контекст: план WP1.3 велел перевести модули C на `Db` «или снять, если их заменяет Фаза 4». Модули `notes`, `cheatsheets`, `search`, `store`, `deps` читали и писали `core.artifacts`/`core.events` через SQLAlchemy; в схеме dbmate этих таблиц нет (артефакты - `content.artifacts` и `content.artifact_versions`, WP23.1). Таблица §6.4 «Что заменено» называет замены: конспект блоками, карточки, cheat sheet хаба, `GET /v1/search`.
- Решение:
  1. Сняты без переноса на `Db`: `POST/PATCH/DELETE /v1/artifacts/notes`, `GET /v1/artifacts`, `GET /v1/artifacts/search` (замена - `GET /v1/topics/{id}/notes`, блоки, `GET /v1/search`), `GET /v1/topics/{id}/cheatsheet` и `POST …/cheatsheet/refresh` (замена - `GET/POST /v1/hubs/{id}/cheatsheet`), обработчики `session.finish_artifacts` и `artifact.refresh_cheatsheet` (дайджест сессии заменён итогом `GET …/summary`; cheat sheet обновляет `artifact.cheatsheet`).
  2. `GET /v1/domains/{id}/export` Фазы 3 снят: он собирал `pMastery` и `core.domains`, которых в Фазе 4 нет; новый экспорт - WP23.7 (T-AR5, не начат), в STATUS строка WP23.7 это называет.
  3. `quack/artifacts/schemas.py` оставляет `ArtifactGenOutput` (схема выхода роли `artifacts@1`, её читают контракты промптов и `scripts/meta_smoke.py`); модели запросов и ответов сняты.
  4. `AppResources` без `engine` и `session_factory`; `/health/ready` и `/healthz` спрашивают Postgres через `resources.db` (`SELECT 1`, тот же срок `health_check_timeout_s`). `get_current_learner_id` читает только сессию-cookie: `_DELETED_LEARNER_SQL` (надгробия `auth.users`, `ops.deletion_tombstones`) снят, аккаунт удаляется физически через `delete_account` вместе с `core.auth_sessions`, cookie перестаёт разбираться.
  5. Шлюз: `LLMGateway(..., db=db)` собран с `db=` (D-B-197), но его первый аргумент `session_factory` остаётся обязательным (outbox `core.llm_outbox`); API держит для него одно ленивое соединение SQLAlchemy (`_outbox_session_factory`, `pool_size=1`). Уходит с аргументом в шаге 4 потока B. Правки в зоне A: из `quack/jobs/kinds.py` убраны виды `session.finish_artifacts` и `artifact.refresh_cheatsheet` (как B убрал `turn.extract`), из `quack/runtime/__init__.py` - модуль `quack.artifacts.cheatsheets`; без этого `T-Q7` падал бы на лишнем виде. Фабрики видов и правила `dispatcher_rules` Фазы 3 остаются до шага 5 A.
  6. `tests/support/resources.py`: `build_app_resources` и `build_job_context` передают `db=` в шлюз, если `db` дан; аргументы `engine` и `session_factory` только строят шлюз.
- Почему: ТЗ Ф4 WP1.3, §6.4, §8.15 п.1, D-A-103, D-B-197, D-C-146 (политика тестов).
- Обратимость: код - откатом коммита; данных Фазы 3 в боевой базе нет (чистый старт, D-C-160).
- Тесты: `tests/test_app_assembly.py` (`test_healthz_depends_only_on_postgres` - через `Db`, T-A3 на `/v1/me`, `test_search_limits_come_from_settings` на `/v1/search`), `tests/e2e/routes/c.py` (T-A1, T-E2, T-E3 без маршрутов Фазы 3, заметки Фазы 4 в изоляции), `tests/storage/test_s1_resources.py::test_app_resources_fields` (набор полей `AppResources` без `engine` и `session_factory`).
- Правки тестов (§8.15 п.1, тест Фазы 3 удалён вместе с кодом; свойство теперь проверяет тест Фазы 4):
  - `tests/artifacts/test_artifacts_search_export.py` (заметки Markdown, `finish_artifacts`, обновление cheat sheet темы, поиск по `core.artifacts`, экспорт домена) → блоки и версии: `test_documents_wp23.py`, `test_topic_refresh_wp23.py`; cheat sheet хаба: `test_cheatsheet_wp23_5.py`; поиск: `tests/materials/test_materials_routes.py` (`GET /v1/search`); экспорт - T-AR5 в WP23.7;
  - `tests/artifacts/test_artifact_isolation.py` (шпаргалка чужой темы - 404, `T-E3`) → `tests/e2e/test_isolation_and_idempotency.py::test_cross_learner_isolation` (все маршруты с id в пути, включая блоки заметок и cheat sheet хаба) и `test_cheatsheet_routes_wp23_5.py`;
  - `tests/artifacts/conftest.py` (стенд Фазы 3: `Seeder`, `worker_deps`, `client`) - без потребителей, удалён;
  - `tests/e2e/test_isolation_and_idempotency.py`: заметка Markdown и её поиск `T-E3` сняты (изоляция заметок Фазы 4 - `isolation=(_topic_with_notes,)` потока C); `tests/e2e/routes/c.py`: пять маршрутов Фазы 3 сняты из реестра `T-A1`/`T-E2`;
  - `tests/test_dispatcher.py::test_cheatsheet_refresh_endpoint_enqueues_directly` (T-D8 Фазы 3, `POST /v1/topics/{id}/cheatsheet/refresh`) - вместе с маршрутом; кнопка «собери сам» хаба и согласие - `test_cheatsheet_routes_wp23_5.py`;
  - `tests/test_jobs_phase3.py` (`T-Q7`: `test_all_job_kinds_have_handlers`, `test_worker_exits_with_code_1_on_handler_mismatch`): вид, чей пропуск проверяется, - `artifact.topic_refresh` вместо `artifact.refresh_cheatsheet` (его нет в `JOB_KINDS`); свойство то же;
  - `tests/test_app_assembly.py`: `test_search_limits_come_from_settings` переведён с `/v1/artifacts/search` на `/v1/search` (лимиты те же), проба `T-A3` `/v1/artifacts` → `/v1/me`.
### D-C-203. WP20.2: фото конспекта, слой 1 `material_parser@1`, правка файлов B
- Класс: B (форма данных и черновик промпта; доктрина — по D-C-132)
- WP / место: WP20.2, S8; `quack/materials/parse.py` (`SheetReading`), `quack/prompts/material_parser/v1.md`, `tests/llm/prompt_contracts.py`, `scripts/meta_smoke.py`, `PROMPT_CONTRACTS.md`, `manifest.json`
- Контекст: (1) фото конспекта читает `recognizer` в режиме листа (Т-§16.5), но `quack.materials` не может импортировать `quack.canvas` (§4.1). (2) Для `material_parser@1` нужен слой 2 (формат, контракт) и какой-то слой 1 (красная линия 13: доктрину пишет владелец).
- Решение: (1) `SheetReading`, `SheetLine`, `SheetBox` в `materials/parse.py` — копия схемы `RecognizerSheetOutput`; тест сверяет `$defs` и `required` двух схем. Из ответа берутся строки не-рисунки и не зачёркнутые, склеенные через перевод строки, одна страница; выровненное изображение — картинка страницы. (2) Слой 2 `v1.md`: контракт `MaterialParserOutput {title, chunks[{n, topic, notation, teaches, uses}]}` и блок входа `chunks`. Слой 1 — черновик по тезисам Т-§4.3 (прецедент D-C-132, как у `recognizer@1` и `artifact_writer@1`): размечать, а не обучать; названия словами материала; что преподаёт и что использует; честная пустота; текст куска — данные (Т-§19.8); новых правил нет. Владелец может заменить своим пакетом. (3) Схема зарегистрирована в `tests/llm/prompt_contracts.py` и `scripts/meta_smoke.py` (`ROLE_SCHEMAS`, файл B; правка в несколько строк: иначе `test_smoke_runs_every_role...` и покрытие `ALL_PROMPT_SCHEMAS` красные).
- Почему: §4.1, §7.6 п.1–2, §0.4.4 п.13, D-C-132, Т-§16.5, Т-§19.8.
- Обратимость: код и файл промпта; версия `@2` для новой доктрины.
- Тесты: `test_sheet_reading_is_the_schema_of_the_recognizer`, `test_image_material_is_read_by_the_recognizer`, `test_the_prompt_material_parser_v1_has_its_contract`, `tests/llm` (T-M5).
- Требует подтверждения владельцем: нет (замена слоя 1 — на его усмотрение)

### D-C-202. WP20.2: что разбор отдаёт структуре — имена, REQUIRES из `uses`, хаб «из материала»; что оставлено
- Класс: B (WP20.2 п.3–4 не называет формат метки и состав записи)
- WP / место: WP20.2 п.3–5, WP16.3 (`reconcile_domain`, B); `quack/materials/parse.py::structure_step`
- Контекст: ТЗ: `material_parser` размечает «по кандидатам с метками», а `chunk.indexed` несёт `teaches`/`uses` как id навыков; кандидатов из графа на шаге 1 нет (он без блокировки и без графа).
- Решение: (1) Парсер называет темы и навыки словами материала; метки кандидатов ставит сверка B (`builder`, Reconcile) на шаге 2 — она и есть «по кандидатам с метками». (2) Шаг 2: темы, затем навыки пачками по `candidate_k` (совет docstring `reconcile_domain`); `Subject.topic` нового навыка — тема куска, где его преподают (иначе название материала); `Subject.requires` — `uses` кусков, которые преподают навык (кроме самого навыка), `origin = material`. Одно имя в разных регистрах — один субъект (`casefold`, первое написание). (3) События одной транзакцией со статусом: `hub.created {kind: material, origin: material, control_type: no_goal, name = title или «Untitled material»}` (`hub_id` = `uuid5` от материала) и `hub.topics_changed` (темы из ответа сверки, `required_levels` = `control_type_levels["no_goal"]`), `chunk.indexed` на каждый кусок (в том числе без разметки), `material.parsed {pages_count}`; ключи `…:{chunk_id|material_id}`. (4) Оставлено: дедлайны и структура курса из силлабуса (`hub.deadline_set`) и задачи семинара (`task.created`, проверка Т-§5.6) — нужны разметка задач WP12.5 (B) и часовой пояс ученика, в T-MT1 их нет; PDF без текстового слоя (скан) даёт пустые страницы — распознавание картинок страниц не делается. Это хвост WP20.2, не закрытый этим решением.
- Почему: Т-§4.3, Т-§4.4, Т-§4.6, Т-§4.8, WP16.3, WP20.2 п.3–5.
- Обратимость: код; формат `chunk.indexed` не менялся.
- Тесты: `test_material_parse_pipeline` (T-MT1), `test_parse_step_repeat_after_a_crash_reuses_pages`.
- Требует подтверждения владельцем: нет

### D-C-201. WP20.2: размер куска и параметры разбора, полезная нагрузка второго шага
- Класс: C (числа из документа «около 1–2 КБ», остальное — параметры)
- WP / место: WP20.2; `quack/config.py`, `quack/materials/parse.py`
- Контекст: Т-§3.4.5 «около 1–2 КБ», Т-§4.3 «параллельно для больших материалов» — без чисел.
- Решение: `material_chunk_chars = 1500` (цель), `material_chunk_max_chars = 2000` (потолок), `material_parser_batch = 8` кусков на вызов, `material_parser_concurrency = 4` вызова одновременно, `material_marks_per_chunk = 6`. Кусок лежит на одной странице; абзац цел, пока влезает; маленький кусок берёт следующий абзац до потолка; длинный текст режется по концу предложения, строки или пробелу. Номер куска в промпте — `[n]` с 1; номер вне диапазона и повтор отбрасываются, пропущенный кусок остаётся без разметки (`check=` не ставится: он стоил бы лишний вызов). Метки уходят в payload второго шага (`chunks[{chunk_id, topic, notation, teaches, uses}]`, около 0,2 КБ на кусок); тексты кусков там не лежат — шаг 2 режет страницы из `content.material_pages` тем же `split_chunks`, `chunk_id` = `uuid5` от материала, страницы и места на ней (закон 5). Изменение параметров между шагами сдвинет границы: метки несовпавших кусков теряются, кусок индексируется без них.
- Почему: Т-§3.4.5, Т-§4.3, §0.4.3 (числа только в `Settings`).
- Обратимость: параметры.
- Тесты: `test_split_chunks_are_one_to_two_kb_inside_a_page`, `test_parser_gets_batches_and_loses_nothing_on_bad_numbers`.
- Требует подтверждения владельцем: нет

### D-C-200. WP20.2: `material.parse` — два шага одного вида, блокировка домена на втором, обработчик в слое сборки
- Класс: B (ТЗ: «шаг записи структуры — `domain:{id}`», «тяжёлая часть — без блокировки»; как это сделать в очереди — не сказано)
- WP / место: WP20.2, #173, S6; `quack/materials/parse.py`, `quack/ops/material_parse.py`, `quack/jobs/kinds.py`, `quack/runtime/__init__.py`
- Контекст: правило диспетчера A ставит `material.parse` без `lock_key` (`rules_p4._material_uploaded`), а `reconcile_domain` (B) требует, чтобы вызвавшая задача держала `domain:{id}`. Блокировка на всю задачу заперла бы домен на время вызовов `material_parser`. Слои §4.1: `quack.materials` не импортирует `quack.agents`.
- Решение: (1) Шаг 1 (`payload.step` нет) идёт без блокировки: страницы, куски, разметка; он ставит через `queue_p4.enqueue` второй шаг того же вида `material.parse` с `job_key = material.parse:{learner}:{material}:structure`, `lock_key = domain:{id}`, классом P1 (в сессии) / P2 (иначе), `required_seq = trigger_seq` (шаг 2 читает граф), `visible = true`, `payload.step = "structure"`. Повтор шага 1 находит тот же ключ — второго шага не будет (закон 5). Правки правила A не нужны. (2) Обработчик `material.parse` — клей в `quack/ops/material_parse.py` (слой сборки): передаёт в `structure_step` функцию над `reconcile_domain`; `quack.materials` знает только протокол `StructureResolver` и `SubjectSpec`. (3) Регистрация по #173: `JOB_KINDS`, `KIND_LLM_ROLES` (`material_parser`), `HANDLER_MODULES` (`quack.ops.material_parse`) — те же три строки, что у `artifact.topic_refresh`. (4) Статус строки `content.materials`: `uploaded` -> `parsed` вместе с событиями шага 2; `parsed` на входе любого шага — выход без работы.
- Почему: Т-§4.3, Т-§13.4, WP20.2 п.4, WP16.3, D-A-110, #173, §4.1.
- Обратимость: код. Если A захочет второй вид задачи (`material.structure`) — переименование `step`.
- Тесты: `test_material_parse_pipeline`, `test_a_parsed_material_is_not_parsed_again`, `test_unknown_material_fails_permanently`, `test_the_kind_is_registered_by_the_procedure_of_issue_173`, `tests/test_jobs_phase3.py::test_all_job_kinds_have_handlers`.
- Требует подтверждения владельцем: нет

### D-C-182. WP9.5: поддержка Telegram в алертинге и деплое (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
- Класс: C (универсальный транспорт вебхуков тревог по Д-§9.3, Д-§14.3)
- WP / место: WP9.5, T-OPS5; `quack/ops/notify.py`, `quack/config.py`, `quack/cli.py`, `deploy/env.example`, `compose.yaml`, `.github/workflows/deploy.yml`
- Контекст: в Д-§14.3 канал чата команды не был зафиксирован; для Telegram требуются sendMessage URL и chat_id. Переменные могут быть заданы в GitHub Secrets как `ALERT_WEBHOOK_URL` / `ALERT_WEBHOOK_CHAT_ID`, так и напрямую через `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.
- Решение:
  1. `quack/ops/notify.py`: автоопределение формата telegram при наличии `TELEGRAM_BOT_TOKEN` или `api.telegram.org` в URL; автоматическая сборка URL `https://api.telegram.org/bot<token>/sendMessage`; фоллбек `alert_webhook_chat_id` на `telegram_chat_id`; форматирование с иконками (`🚨 CRITICAL:`, `⚠️ WARNING:`) и обрезка длинных сообщений до 4000 символов (лимит Telegram 4096); логирование деталей ошибки при `HTTPStatusError`.
  2. `quack/config.py`: настройки `telegram_bot_token` (маскируется в логах) и `telegram_chat_id`.
  3. `quack/cli.py`: флаг `ops alerts --test` для моментальной проверки отправки тестового уведомления в чат команды.
  4. `.github/workflows/deploy.yml` и `deploy/env.example`: поддержка переменных `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` и чтение `ALERT_WEBHOOK_FORMAT`/`CHAT_ID` как из `secrets`, так и из `vars`.
  5. `compose.yaml`: проброс переменных в сервисы `backup` и `alerts`.
- Почему: Д-§9.3, Д-§14.3; удобство настройки через GitHub Secrets без ручной сборки URL.
- Обратимость: код; обратная совместимость со Slack/Discord полностью сохранена.
- Тесты: `tests/test_ops_notify.py` (12 тестов), `tests/test_ops_alerts.py` (29 тестов).
- Требует подтверждения владельцем: нет.

### D-C-181. WP1.3 шаг 3: `ops status`, `restore-check`, `seed-learners`, `alerts` на схеме Фазы 4
- Класс: C (состав отчёта `ops status` Фазы 4; поля Фазы 3 переехали на таблицы Фазы 4)
- WP / место: WP1.3 шаг 3, WP9.5, WP9.6, WP10.4, issue #215; `quack/ops/{status,restore_check,seed,alerts}.py`
- Контекст: ТЗ Ф3 WP8.4 описывает отчёт через `core.learner_seq`, `ops.dlq`, `ops.llm_ledger`, `run_after`, `updated_at`; в схеме Фазы 4 их нет. ТЗ Ф4 WP10.4 просит расход за час и сутки в `ops status` и тревогу против `team_daily_alert_usd`.
- Решение:
  1. `projector.*`: `core.learners.seq` против `ops.projector_offsets.seq`; `dispatcher.*`: против `ops.dispatcher_offsets.last_dispatched_seq`; `stopped_streams` - число строк `ops.projector_failures` (по строке на остановленный поток; `ops resume` её снимает).
  2. `jobs.*` из `ops.jobs`: возраст самой старой `queued` - по `run_at`; «ждут проекции» - условие захвата очереди (`queue_p4._CANDIDATE`), отрицание. У `ops.jobs` нет времени завершения: `finished_last_hour`/`failed_last_hour` считают `succeeded`/`failed` с `heartbeat_at` за час (последний пульс ближе всего к концу; задача короче интервала пульса - момент старта). Столбец времени завершения - просьба к A, если нужна точность.
  3. Ключ `dlq` убран: DLQ нет, упавшая задача остаётся в `ops.jobs` (`jobs.by_status.failed`); потребителей ключа нет (`alerts` его не читал).
  4. `llm.*` из `ops.llm_usage`; новые `llm.cost_last_hour_usd`, `llm.cost_last_day_usd`. `ops alerts`: условие `team_spend` (предупреждение), когда расход за сутки выше `team_daily_alert_usd`; нет числа (Postgres не ответил) - нет тревоги (закон 8).
  5. `ops restore-check`: `Learner.projectedSeq` (Neo4j) против `core.learners.seq`; узел без `projectedSeq` - 0, не нарушение.
  6. `ops seed-learners`: строка `core.learners` с `settings.seeded = true` и события `domain.created`, `topic.created`, `skill.created` (актор `system`, происхождение `learner`); в Фазе 4 нет `learner.registered` и `structure.*`. Проверка прода: «настоящие» ученики - без `settings.seeded`.
  7. `ops status` и `ops alerts` открывают пул без ожидания (`open_db(wait=False)`): недоступный Postgres - отчёт с кодом 2, а не трассировка.
- Почему: ТЗ Ф3 WP8.4; ТЗ Ф4 WP9.5, WP9.6, WP10.4; Д-§6.1 шаг 8, Д-§8.3, Д-§9.1, Д-§9.3, Д-§11.
- Обратимость: код; формат отчёта - ключ `dlq` и два новых поля.
- Тесты: `tests/test_ops_p4_status.py`, `test_ops_p4_seed.py`, `test_ops_restore_check.py`, `test_ops_alerts.py` (`test_team_spend_...`).
- Требует подтверждения владельцем: нет

### D-C-180. WP1.3 шаг 3: `quack/cli.py` и `quack/ops/*` без SQLAlchemy
- Класс: C (зона C; сняты команды Фазы 3)
- WP / место: WP1.3 шаг 3, WP9.6, issue #215, #235; `quack/cli.py`, `quack/ops/{commands,db_roles}.py`
- Контекст: план #215 шаг 3: C переводит `ops` и CLI на `Db`; A снимает остальное на шаге 5. Роли БД и `LLMGateway` - в документах и у B не решены до конца.
- Решение:
  1. Сняты команды `ops replay-dlq` и `ops purge-deleted` (таблицы `ops.dlq`, `ops.deletion_tombstones` Фазы 3 ушли; повтор и удаление Фазы 4 - у A, D-A-197..205).
  2. A снял флаг `phase4_processes` и код процессов Фазы 3 (D-A-223..225, шаг 5 ч.1); в CLI от Фазы 3 оставались `ops replay-dlq`, `ops purge-deleted`, движок и фабрика сессий - сняты здесь.
  3. `purge_deleted` и `PURGE_TABLES` удалены из `commands.py` вместе с командой: обработчик `account.purge_deleted` и `purge_job.py` A уже убрал (удаление Фазы 4 - у A, D-A-197..205). `quack.ops.commands` - только `migrate`.
  4. `ops db-roles` на `Db` (`psycopg.sql`), схемы `core`, `content`, `ops` (схемы `auth` нет). Роли на DSN сервисов не переводятся (D-C-160 п.6): `ops migrate` и сервисы идут под `quack`; перевод - решение владельца после шага 5, схема Фазы 4 его допускает (все таблицы в трёх схемах, права по умолчанию для `quack_migrator`).
  5. `LLMGateway.__init__` (B) ещё требует фабрику сессий Фазы 3: `ops extraction-eval` и `ops replay-session` строят её в одном месте (`cli._gateway`, при `db=`). Это единственное упоминание движка в CLI; уйдёт, когда B уберёт параметр (шаг 4 плана). Шлюз на схеме Фазы 4 до этого читает `core.llm_outbox`, которого в ней нет - обе команды заработают целиком после шага 4.
- Почему: ТЗ Ф4 WP1.3, §8.15 (тесты снятого кода удалены в том же PR), §4.1 (слои); план #215.
- Обратимость: откатом коммита.
- Тесты: `tests/test_ops_phase3.py` (`test_cli_parser_phase3`: команды сняты), `test_ops_db_roles.py`, `test_ops_p4_cli.py`.
- Требует подтверждения владельцем: нет (п.4 - перевод ролей сервисов остаётся за владельцем)

### D-C-159. WP21.5: пометки и вмешательства — шлюз вмешательств, ключ `canvas_quiet`, сообщения `mark` и `hint`
- Класс: C (поведение быстрого слоя канваса, Т-§15.5; формат событий и схем БД не меняется); `stuck_erase_times` — число из документа («стёрто трижды») в `Settings`
- WP / место: WP21.5, T-CV5; `quack/canvas/interventions.py` (новый), `quack/canvas/socket.py`, `quack/canvas/boards.py` (`put_mark`, `remove_mark`, `put_hint`), `quack/config.py`
- Контекст: ТЗ называет пометки, паузу `error_grace_ms`, лимиты, тихий режим и «застрял», но не говорит: что считается самоисправлением, считаются ли вопросы и подсказки в лимит, откуда берётся тихий режим, что делать с пометкой, дожидающейся паузы между вмешательствами, и как выглядят сообщения сервера.
- Решение:
  1. Один шлюз `InterventionGate` на канвас задачи: чистое состояние над часами (секунды), которые передаёт вызывающий; сокет кормит его событиями, отдельная задача-«тикер» спрашивает `due(now)` и пишет на доску. Тесты гоняют его с подставными часами.
  2. Пометка (`error` и `question` одинаково) — не сразу: через `error_grace_ms` или после следующей строки (если диагноз пришёл, когда позднейшая строка уже есть, — сразу). Самоисправление — стирание строки целиком за это время: пометки нет; уже показанная пометка снимается тихо и в лимите остаётся.
  3. Лимиты: `max_unprompted_marks_per_task` считает непрошеные пометки и подсказки вместе; `min_intervention_gap_s` — между ними. Пометка, дождавшаяся паузы, откладывается и показывается, когда пауза прошла; при исчерпанном лимите — отбрасывается. Повышение `question` → `error` вторым мнением и снятие пометки новым вмешательством не считаются. Переподключённый сокет начинает счёт с числа пометок и подсказок слоя учителя на доске (лимит не обнуляется).
  4. Тихий режим — последнее `preference.set {key: "canvas_quiet", value: true}` ученика (`false` — выключен; нет события — сдержанный режим). Ключ — решение C: документы режим называют, ключ нет; клиент пишет его действием `preference.set` (WP13.1, поток B). Режим читается при каждом срабатывании; в нём пометка, у которой вышла пауза, отбрасывается, подсказка «застрял» не показывается. Свидетельства (`observation.*`) пишутся как обычно.
  5. «Проверь» (`check_request`): проверка всех строк холста (повтор берёт записанные диагнозы, закон 5) и показ пометок сразу — в обход паузы, лимитов и тихого режима; такие пометки в лимит не входят.
  6. «Застрял»: `stuck_idle_s` без штрихов, пока решение не закончено (нет `task.closed` и `answer.submitted {source: canvas}` по задаче) и написана хотя бы одна строка, или одно место стёрто `stuck_erase_times` (3) раз; «место» — пересекающиеся рамки стёртых штрихов. Подсказка — первая ступень лестницы пространства решения (`hints[0]`), один раз за канвас, рядом с последней строкой: `hint.shown {task_id, step: 1, source: canvas}` (ключ `hint.shown:canvas:{canvas}:{task}:1`) и подсказка в слое учителя. Нет пространства решения или подсказки — подсказки нет, учитель отвечает обычным ходом (клиент шлёт действие, WP13.1).
  7. Сообщения сервера: `mark {mark_id: "mark:{line_id}", line_id, kind: "error" | "question" | null}` (`null` — снять) и `hint {step, line_id, text}`; пометка лежит в слое учителя доски (`Mark.text` пуст: слова пометки — на стороне клиента). Пометка по строке одна, повышение заменяет её.
  8. Не сделано: «задача закончена — вердикт и запись в ленте» (`task.closed` + `hint.shown`): это чтение событий для ленты, а не вмешательство канваса; остаётся диспетчеру/потоку, который строит ленту.
- Почему: Т-§15.4, Т-§15.5, П-9.5, П-9.7; ТЗ Ф4 WP21.5; закон 5 (повтор «проверь» не плодит диагнозов), закон 9 (проверка в фоне, сокет подтверждает штрихи, не дожидаясь).
- Обратимость: код C; ключ `canvas_quiet` — до первого `preference.set` клиента.
- Тесты: `tests/canvas/test_interventions_wp21.py` (T-CV5: `test_intervention_limits` и др.), `tests/canvas/test_canvas_checks_ws.py`.
- Требует подтверждения владельцем: нет (ключ тихого режима согласовать с B/фронтендом).

### D-C-158. WP21.4: каскад проверки шагов в сокете — порядок, метка, второе мнение, повтор
- Класс: C (поведение быстрого слоя, Т-§15.4); оговорка про шов B — п.5
- WP / место: WP21.4, T-CV4; `quack/canvas/step_check.py` (новый), `quack/canvas/socket.py`
- Контекст: Т-§15.4 задаёт четыре шага, но не говорит, как сопоставлять строку с пространством решения (у неверной ветки нет выражения), чем именовать подтверждённую символьно ошибку, как вызвать `second_opinion@1` для строки (его вход рассчитан на ответ задачи) и что делать при повторе проверки.
- Решение:
  1. Порядок для новой строки холста, открытого задачей (блок задачи = холст с `task_id`; зачёркнутая строка не проверяется): (a) `/v1/math/transition` от предыдущей незачёркнутой строки, если обе разбираются (`sympy` строки, иначе LaTeX строки вида `latex`; текстовая строка без `sympy` — не математика); расхождение (`follows = false`, в том числе числовое) — подтверждённая ошибка: `observation.diagnosed {method: symbolic, kind: error, confidence: 1.0}`, actor `code`; подтверждённый переход (`true`) — ничего, ни события, ни пометки (Т-§15.4 п.1). (b) Иначе (нет предыдущей, не разбирается, `status = failed`, `follows = null`, compute недоступен) — `canvas_diagnoser@1`. (c) Для подтверждённой ошибки диагност тоже зовётся, но только чтобы назвать пробел, заблуждение и корень (WP5.1 «символьное расхождение с указанием заблуждения моделью»); если модель недоступна или ошибки не нашла — ошибка остаётся, `gap_type = execution`, без заблуждения.
  2. Сопоставление с пространством решения («веха / неверная ветка / ничего») делает диагност: пространство целиком идёт во вход (`Key steps`, `Typical wrong branches`); у неверной ветки в `SolutionSpace` нет выражения, по тексту код сопоставить не может. «Ничего» и другой путь — `error = false`, события нет (D-C-120, D-C-136).
  3. Метка на доске (Q-A-3 п.1, WP21.5): `error` — только символьное расхождение или согласное второе мнение; `question` — модельный диагноз `kind = question` (уверенность ниже `mark_min_confidence`); уверенный модельный диагноз без подтверждения пишется в журнал (`kind = error`, `method = model`), но пометки не даёт.
  4. Второе мнение — только для `question`: роль `second_opinion@1` (`ModelVerdictOutput`) со входом: условие, `rubric = (none)`, спецификация ответа, известные заблуждения (метки `M`), «ответ» = предыдущие строки + оцениваемая строка. `agreed = (outcome == incorrect)`; `correct`, `partial`, `unclear` — не согласие (ложный диагноз хуже пропущенного, Т-§7.7). Пишется `observation.second_opinion {target_seq, agreed}` с ключом `observation.second_opinion:{target_seq}`; модель недоступна — ничего не пишется, пометка остаётся вопросом. Согласие повышает пометку до `error`, несогласие снимает её тихо (WP21.5).
  5. Шов к B: правило A уже ставит `second_opinion.diagnosis` (P1) на `observation.diagnosed` с `question`; обработчика этой задачи в `phase-4` нет. Сокет сам делает второе мнение (бюджет 10 с, Т-§15.8) и пишет событие под ключом диагноза; обработчик B обязан писать под тем же ключом `observation.second_opinion:{target_seq}` (повтор не плодит событий), иначе правило A стоит снять. Промпт `second_opinion@1` рассчитан на ответ задачи; второе применение (строка холста) заготовкой не покрыто.
  6. Память: известные заблуждения и имена навыков — `GraphReader.slice_for_turn` по навыкам задачи (`task.created.markup.tests`); недоступность памяти проверку не отменяет (диагност без списка заблуждений). Предыдущие строки — из журнала (`canvas.line_committed` минус `canvas.line_erased`, с применённым `handwriting.corrected`), не из памяти сокета: переподключение и «проверь» видят одно и то же.
  7. Повтор: ключ диагноза `observation.diagnosed:{canvas}:{line}:{digest(text, sympy)}`; повторная проверка той же записи строки берёт записанный диагноз и не зовёт ни compute, ни модель. Исправленное прочтение (`handwriting.corrected`) — новая запись строки и новая проверка.
- Почему: Т-§15.4, Т-§7.7, Т-§15.8, WP5.1, Q-A-3 п.1, D-C-120, D-C-136, закон 5.
- Обратимость: код C; формат событий не менялся.
- Тесты: `tests/canvas/test_step_check_wp21.py` (T-CV4: `test_step_check_cascade` и др.), `tests/canvas/test_canvas_checks_ws.py`.
- Требует подтверждения владельцем: нет (шов п.5 — на B).

### D-C-157. WP23.3: поля блока `basis` и `unverified`, общий код проверок для конспекта и cheat sheet
- Класс: C (формат хранения без данных — правится на месте, как D-C-135; маршрутов и событий нет)
- WP / место: WP23.3, Т-§17.2, Т-§17.4; `quack/artifacts/documents.py` (`Block`, `SystemBlock`), `quack/artifacts/accuracy.py` (новый), `topic_refresh.py`, `hub_cheatsheet.py`, `hub_cheatsheet_routes.py`
- Контекст: ТЗ требует отметки «не проверено» и «без опоры», но поля блока (Т-§17.2) их не называют, `kind` — закрытый список.
- Решение: (1) У `Block` два поля: `basis` (`materials` — блок назвал кусок; `system` — «пояснение системы», без опоры; `null` — блок ученика) и `unverified` (bool, по умолчанию `false`). В JSON версии и в ответах API — `basis`, `unverified` (без псевдонимов). (2) Правка блока учеником сбрасывает обе отметки (блок стал его). Принятие дополнения блок ученика не меняет. (3) Блоки системы, которые «собери сам» оставляет на месте, сохраняют отметки. (4) Общий код — `accuracy.write_blocks`: цикл «вызов роли, проверка, одна перегенерация» вместо двух копий в `topic_refresh` и `hub_cheatsheet`; `compute` — необязательный параметр (`None` = формулы «не проверено»), задача `artifact.topic_refresh` передаёт `ctx.compute`, маршрут `review` — `resources.compute`.
- Почему: Т-§17.4, закон 8 («не проверено» — явное значение), WP23.2 («повторная генерация один раз»), «общий код — в quack/artifacts».
- Обратимость: код C; данных учеников нет (D-C-135), старые версии читаются (поля по умолчанию).
- Тесты: `tests/artifacts/test_accuracy_wp23_3.py` (`test_a_learner_edit_clears_the_marks`, `test_the_review_keeps_the_marks_of_the_systems_blocks`, `test_the_topic_job_passes_compute_to_the_notes`); `test_documents_wp23.py` (форма блока в ответе).
- Требует подтверждения владельцем: нет

### D-C-156. WP23.3: формулы блока — поле `formulas`, проверка в compute, «не проверено»
- Класс: C (поле выхода роли C; слой 2 промпта — C; `PROMPT_CONTRACTS.md` пересобран, регистрация схемы у B не менялась)
- WP / место: WP23.3, Т-§17.4; `quack/artifacts/writer.py` (`WrittenBlock.formulas`), `quack/artifacts/accuracy.py` (`split_identity`, `formula_request`, `verify_formula`), `quack/prompts/artifact_writer/v1.md` (черновик, правится на месте, манифест пересобран), `quack/config.py` (`artifact_formulas_per_block_max`)
- Контекст: ТЗ: «формулы, проверяемые символьно, проверяет compute (`/v1/math/check` в режиме тождества)», но не сказано, какие формулы в блоке проверяемые. Выделять их из Markdown эвристикой нельзя: уравнение `x^2 = 4` не тождество, и проверка дала бы ложное «неверно».
- Решение: (1) Писатель сам перечисляет в `formulas` тождества, производные и интегралы блока одной строкой LaTeX `слева = справа` (ровно один `=` вне скобок). (2) Код шлёт каждую в `ComputeClient.math_check`: `kind = expression`, `mode = equal`, обе стороны как `latex`, `timeout_ms = symbolic_background_timeout_ms`, пороги из `Settings`. (3) `passed` — `completed` и `correct`. Всё остальное (`incorrect`, `unclear`, `failed`, не тождество, длиннее предела `MathInput`) — `failed`: нарушение, одна перегенерация, при повторном провале блок остаётся с `unverified = true` (не отбрасывается). (4) Нет ответа compute (`ComputeUnavailableError`, таймаут) или `compute = None` — `unavailable`: писатель не виноват, перегенерации нет, блок помечен `unverified`. (5) Не больше `artifact_formulas_per_block_max` (5) формул на блок, остальные не проверяются, блок помечен.
- Почему: Т-§17.4, закон 2 (проверяет код, не модель), закон 8, §0.3 (пороги в конфиге).
- Обратимость: код C; поле имеет значение по умолчанию `[]`, строгая схема делает его обязательным для модели.
- Тесты: `tests/artifacts/test_accuracy_wp23_3.py` (формулы: прошла, перегенерация, дважды не прошла, не разобрана, compute недоступен, без compute, не тождество, cheat sheet).
- Требует подтверждения владельцем: нет

### D-C-155. WP23.3: опора блока — определение без куска, «без опоры», страница из куска
- Класс: C (достройка контракта: Т-§17.3 и Т-§17.4 по-разному говорят о блоке без куска)
- WP / место: WP23.3, WP23.2, T-AR2, Т-§17.3, Т-§17.4; `quack/artifacts/accuracy.py` (`lacks_source`, `system_block`)
- Контекст: Т-§17.3 / T-AR2: определение или утверждение без ссылки — нарушение (перегенерация, затем отбрасывание). Т-§17.4: утверждения без опоры на материалы помечаются как пояснения системы. Оба требования нельзя применить к одному блоку.
- Решение: (1) Нарушение — блок вида `definition` или `statement` без `sources`, когда в контексте есть хотя бы один кусок материалов: писатель мог сослаться. (2) Если кусков нет вовсе, ссылаться не на что: блок остаётся и помечен `basis = system`. Любой другой вид без `sources` (`why`, `summary`, …) — не нарушение, `basis = system`; своё пояснение писатель кладёт в такой вид (промпт обновлён). (3) Страница или слайд — `sources[].page` из куска, который писатель назвал меткой `P…`; выдумать страницу он не может. Кусок без страницы (`page = null`) ссылкой допустим. (4) Одинаково для конспекта и cheat sheet.
- Почему: T-AR2, Т-§17.3, Т-§17.4, закон 2.
- Обратимость: код C; ужесточить (требовать страницу) — одна проверка в `lacks_source`.
- Тесты: `tests/artifacts/test_accuracy_wp23_3.py` (`test_output_checks_regenerate_once` — T-AR2, `test_a_definition_names_the_page_of_its_chunk`, `test_a_block_without_a_source_is_a_system_explanation`, `test_without_materials_a_definition_is_a_system_explanation`, `test_a_fixed_definition_on_the_second_try_is_kept`).
- Требует подтверждения владельцем: нет

### D-C-154. WP23.4: генерация карточек — второй вызов `artifact_writer@1` в `artifact.topic_refresh`, карточка — задача банка без проверки истинности
- Класс: C (достройка контракта WP23.4 по ТЗ и D-C-140; новых полей и событий нет)
- WP / место: WP23.4, WP23.2, Т-§17.5, Т-§17.7, Т-§5.3; `quack/artifacts/topic_refresh.py` (`refresh_topic_cards`, `refresh_topic`, `valid_cards`, `card_id_for`)
- Контекст: ТЗ говорит «`session.ended` с показами — обновление конспекта и новые карточки (`artifact:{topic}`, одно на тему за сессию)» и «WP23.4 вызывает функцию генерации разметки», но не называет, одним ли вызовом роли пишутся конспект и карточки, какая у карточки `verification` и что делать, когда тема уже полна.
- Решение: (1) Одна задача `artifact.topic_refresh` (замок `artifact:{topic}`) делает два вызова роли: тип `notes`, затем тип `cards` (`artifact_type` = `cards (at most N new cards)`, `current_blocks` = пронумерованные карточки темы); выход `cards` для конспекта и `blocks` для карточек отбрасываются. (2) Код проверяет разметку каждой карточки `validate_markup` (Т-§5.3, метки навыков и заблуждений этого вызова; `variantOf`/`pairTaskId` без таблицы задач недопустимы); нарушение — одна перегенерация (`…:retry`), затем карточка отбрасывается. (3) Каждая карточка — `markup_record` → `create_task` (`source = generated`, `family_id = task_id`, `actor = artifact_writer@1`, `verification = unchecked`: код проверил форму, не истинность; `domain_id` темы) и запись в артефакт `cards` в одной транзакции. (4) `task_id` = `uuid5(topic, ключ задачи, номер в ответе)`: повтор задачи пишет те же задачи и не пишет версию. (5) Потолок: если в теме уже `cards_per_topic_max` карточек — модель не вызывается; иначе просим не больше свободных мест и обрезаем выход тем же числом (карточка сверх потолка не получает ни задачи, ни места).
- Почему: Т-§17.5 («не больше `cards_per_topic_max` на тему»), Т-§17.7, WP23.2 (разметка карточки проверяется кодом, нарушение — одна перегенерация), закон 8 (не проверено — явное значение), закон 5. Проверка арбитром `verify_generated` (WP12.5) в ТЗ для карточек не названа и стоила бы вызов модели на карточку.
- Обратимость: код C; если владелец захочет проверку истинности карточек — `verify_generated` перед `create_task` и `verification = checked`.
- Тесты: `tests/artifacts/test_cards_generation_wp23.py`; ответ на карточку (`answer` с `source = card`, T4 и только уровень 1) — `tests/graph/test_projection_rules3.py::test_card_self_assessment_is_t4_evidence_of_level_1` (T-AR3, зона A/B, без изменений).
- Требует подтверждения владельцем: нет

### D-C-152. WP21.6: голос отрезками — WAV для модели, сжатое аудио переводит клиент, ключ события
- Класс: B (формат входа — граница с фронтендом; декодер на сервере потребовал бы зависимости вне §4.6)
- WP / место: WP21.6, Т-§22.8, П-14.1; `quack/canvas/voice.py`, `quack/canvas/router.py` (`POST /v1/sessions/{id}/voice`), `tests/support/llm.py` (медиа-роли двойника), `tests/e2e/routes/` (поле `Route.form`)
- Контекст: ТЗ даёт маршрут, роль `voice`, событие и «аудио не записывается», но не говорит, как браузерное аудио (WebM/Opus, Ogg, MP4/AAC) становится WAV моно 16 бит 16 или 24 кГц, которого ждёт `muse-voice-transcribe-1.0`. Декодера в списке §4.6 нет (`ffmpeg`, `pydub`, `av`, `soundfile` не названы); в `backend/Dockerfile` ffmpeg нет, а добавлять пакет образа §4.6 не разрешает.
- Решение: (1) Клиент шлёт WAV (у него есть PCM микрофона: `AudioContext`/`AudioWorklet` → WAV). Маршрут принимает `multipart`: `audio` и поля формы `started_at_ms`, `ended_at_ms` (целые, ≥ 0; конец раньше начала — 422). (2) Сервер на стандартной библиотеке (`wave`, `array`) приводит любой PCM WAV к виду модели: стерео усредняется в моно, 8/24/32 бит → 16, частота 16 или 24 кГц остаётся, другая — 16 кГц (понижение — среднее по окну, повышение — линейная интерполяция); работа в `asyncio.to_thread`. (3) Не PCM WAV (WebM, Ogg, MP4, MP3, float, пустой) — 415 `UPLOAD_REJECTED` с указанием «отправь WAV»; дольше 600 с — 413 (предел эндпоинта Меты, не настраиваемый порог). (4) Ключ события `voice.transcribed:{session_id}:{started_at_ms}:{ended_at_ms}` (закон 5): повтор того же отрезка отдаёт записанный ответ без вызова модели; тишина (пустой текст) события не пишет, ответ `seq: null`. (5) 201 `{text, started_at_ms, ended_at_ms, seq}`; только полная сессия (companion — 403, T-CP1); чужая сессия — 404; модель недоступна — 503 `LLM_DEGRADED`, события нет. (6) Аудио нигде не сохраняется (T-CV7). (7) В реестре e2e маршрут — `no_key` с причиной; в `Route` добавлено поле `form` для полей формы мультипарт-маршрута (T-E3).
- Почему: Т-§22.8 («аудио обрабатывается в памяти и не записывается»), ТЗ Ф4 WP21.6, §4.6 (зависимости), закон 5, Т-§2.1 (время как поле нагрузки).
- Обратимость: код C. Если фронтенд не сможет собрать WAV, нужен владелец: разрешить `ffmpeg` в образе (правка `Dockerfile`, §4.6) и добавить перекодирование перед `normalize_wav`.
- Тесты: `tests/canvas/test_voice_wp21.py` (T-CV7 и отказы); `tests/e2e/` (T-A1, T-E3 по реестру).
- Требует подтверждения владельцем: да (граница с фронтендом: WAV собирает клиент)

### D-C-153. WP22.3 шаг 5: рисунки на фото — рамка в пикселях, маска `clean-drawing`, слой ученика доски
- Класс: B (формат маски `sam-3.1` помечен «проверить на стенде»; место рисунка в документе доски ТЗ не называет)
- WP / место: WP22.3 шаг 5, Т-§22.6, Т-§16.3 п.4; `quack/canvas/photo_pipeline.py`, `quack/canvas/drawings.py`, `quack/canvas/board.py` (`LearnerDrawing`), `quack/canvas/boards.py` (`add_drawing`), `quack/storage/keys.py` (`photo_drawing_key`)
- Контекст: адаптер `LLMGateway.segment` (D-B-187) есть. Нужны рамка области в пикселях и размер изображения (`BoxPrompt`), а compute `/v1/image/align` размера не возвращает; `SegmentMask.data` — base85 как пришло от модели, вид после декодирования в документации не описан; `CleanDrawingRequest.mask_b64` compute читает через `cv2.imdecode` (файл изображения, серое) и подгоняет размер под снимок; куда класть «изображение, похожее на штрихи» в документе доски, не сказано.
- Решение: (1) Только области `kind = drawing` идут в `segmenter`: по одному вызову, `box=` (не `concept`), изображение — выровненный снимок `image/webp`. (2) Размер снимка — из заголовка WebP (`webp_size`: VP8, VP8L, VP8X; библиотека изображений в backend не разрешена); если размер не читается или рамка пуста, рисунки считаются и пропускаются (`photo_drawings_skipped`). (3) Маска для compute — серый PNG размера снимка (`drawing_mask`): payload после base85 — файл изображения (идёт как есть), либо `H x W` байт, либо они же в zlib (кладётся в `x`, `y`); иначе — рамка объекта модели, а если объектов нет — рамка области от распознавателя. Берётся первый объект с маской. (4) `clean-drawing` → объект `photos/{photo_id}/drawing_{n}-{ulid}.webp` (ключи неизменяемы, Т-§3.3) → запись `LearnerDrawing {drawing_id = "{photo_id}:{n}", photo_id, object_key, bbox}` в `doc.learner.drawings` одним `UPDATE ... jsonb_set` с проверкой, что такого id ещё нет. (5) Повтор задачи ничего не вызывает для рисунков, что уже на доске. Ошибки шлюза и compute не глотаются: задача повторяется очередью (строки журнала уже записаны и идемпотентны). (6) `PhotoResult.drawings_placed`; `drawings` — число областей. Снимается BLOCKED-C-204.
- Почему: Т-§22.6 п.5, Т-§16.3 п.4, П-9.4 (слой ученика), законы 5 и 7.
- Обратимость: код C. Формат маски и приём WebP у `sam-3.1` проверяются ручным прогоном DoD п.6a; ссылка на рисунок для фронтенда (подписанный URL по `object_key`) — отдельным маршрутом, когда фронтенд назовёт форму.
- Тесты: `tests/canvas/test_drawings_wp22.py`; `tests/canvas/test_photo_pipeline_wp22.py::test_sam_only_for_drawings` (T-CP3).
- Требует подтверждения владельцем: нет

### D-C-151. WP16.7: `ops metrics` и `ops replay-session` — формат аргументов, вывод, отказы
- Класс: C
- WP / место: WP16.7, S15, issue #209 п.3; `quack/cli.py`, `tests/test_ops_p4_cli.py`
- Контекст: сигнатуры функций B заданы (#209), но формат `--since`, имя файла отчёта, вывод и коды выхода команд не названы.
- Решение: `--since` — ISO 8601, время без пояса считается UTC; `metrics` печатает один JSON (`sort_keys`) и выходит с 0. `replay-session`: `--out` необязателен, по умолчанию `replay-session-<session>.json` в текущем каталоге; в stdout идёт `{"out", "summary"}`, полный отчёт в файле. Код 2: сессии нет, промпт не зарегистрирован или `name@N` разобран неверно, пустой `META_API_KEY` (проверка до открытия ресурсов). Шлюз команды строится с `db=db` (D-B-197), чтобы строки `ops.llm_usage` писались. Роль `ops` в prod не получает `META_API_KEY` (Д-§7), поэтому `replay-session` запускается там, где ключ есть (стенд, dev); `metrics` ключа не требует.
- Почему: `Д-§7` (ключ модели только у `api` и `workers`), соглашение кодов выхода `seed-learners` (отказ = 2), `Т-§18.2`.
- Обратимость: изменение аргументов — правка парсера и теста; формат JSON не контракт.
- Тесты: `tests/test_ops_p4_cli.py`.
- Требует подтверждения владельцем: нет

### D-C-150. `ModelPrice`: три токеновых цены обязательны, лишний ключ и отрицательное число — ошибка
- Класс: C
- WP / место: WP10.4, ревью потока C в PR #208 (замечание 1, issue #209 п.6); `quack/config.py`, `tests/test_settings_p4_review.py`, `tests/llm/test_usage_usd.py`
- Контекст: в #208 все пять полей имели значение по умолчанию 0: `{}`, опечатка ключа и минус проходили, модель считалась бесплатной, бюджеты в долларах (D-B-199) переставали работать.
- Решение: `input`, `cached_input`, `output` обязательны, все поля `ge=0`, `extra="forbid"`; `per_minute` и `per_image` необязательны (0). Модель голоса или картинок пишет токеновые цены явными нулями. Тест B `test_usage_usd` поправлен на явные нули у `voice-m`.
- Почему: закон 8 (отсутствие не ноль), D-B-198, D-B-199; предложение ревью.
- Обратимость: убрать `extra`/`ge`/обязательность — одна правка класса.
- Тесты: `tests/test_settings_p4_review.py` (обязательность, опечатка, минус, разбор `per_minute`/`per_image` из `LLM_PRICES`).
- Требует подтверждения владельцем: нет

### D-C-149. Пустая `LLM_PRICES` читается как пустая таблица; compose передаёт `${LLM_PRICES:-}`
- Класс: C
- WP / место: WP9.1, WP10.4, D-B-198, ревью потока C в PR #208 (замечание 2, issue #209 п.4); `quack/config.py`, `compose.yaml`, `deploy/env.example`, `backend/.env.example`, `tests/test_compose_contract.py`
- Контекст: `LLM_PRICES=` давала `SettingsError` при разборе JSON, в том числе в dev; compose для прочих секретов использует `${X:-}`.
- Решение: поле `llm_prices` помечено `NoDecode`, `field_validator(mode="before")` читает пустую или пробельную строку как `{}`, остальное как JSON. В compose `LLM_PRICES: ${LLM_PRICES:-}` и `LANGSMITH_API_KEY: ${LANGSMITH_API_KEY:-}` только у `api` и `workers` (не у `meta-mock` профиля `loadtest`). В prod пустая таблица по-прежнему останавливает `api`/`workers` (D-B-198); в dev и test пустая допустима. Значения цен даёт владелец: в `env.example` они пусты.
- Почему: D-B-198, D-B-201, Д-§7 (секрет только у своих сервисов), Д-§11 (трассировка выключена на стенде).
- Обратимость: вернуть строгий разбор — убрать валидатор; compose — две строки.
- Тесты: `tests/test_settings_p4_review.py::test_an_empty_llm_prices_is_the_empty_table`, `tests/test_compose_contract.py` (держатели переменных, имена в двух `env.example`).
- Требует подтверждения владельцем: нет

### D-C-148. WP23.5: cheat sheet хаба — окно предложения, подгонка под страницу, «собери сам»
- Класс: C
- WP / место: WP23.5; `quack/graph/read/cheatsheet_offer.py`, `quack/artifacts/hub_cheatsheet.py`, `hub_cheatsheet_routes.py`, строка в `HANDLER_MODULES` (`quack/runtime/__init__.py`), `tests/artifacts/test_cheatsheet_*`
- Контекст: D-C-143 и D-C-144 задают состояние предложения, согласие и задачу, но не называют: окно дедлайна, как просить писателя сократить, что считать замечаниями, что делает задача без согласия; поля `artifact` и `hub_id` в `AgentParamsRecord` ещё не влиты (D-A-196).
- Решение: (1) предложение — последний `hub.deadline_set` хаба с дедлайном в будущем не дальше `cheatsheet_offer_days`, без согласия (`agent.requested`, `params.artifact = cheatsheet`, `params.hub_id`) и без `artifact.updated` вида `cheatsheet`; прошедший дедлайн предложения не даёт; согласие ищется по JSON журнала, поэтому чтение не ждёт схемы A. (2) Подгонка: каждый раунд — сохранённая версия (история `Т-§17.1`), в compute уходит весь документ вместе с блоками ученика; больше одной страницы — писатель вызывается снова, до `cheatsheet_fit_rounds` повторов (0 — без повторов); требование «сократить» и число страниц идут в переменной `artifact_type` прежнего промпта `artifact_writer@1` (промпт и схема не менялись); последняя версия остаётся, даже если не влезла. HTML самодостаточный (compute без сети), Markdown — свой минимальный разбор с экранированием, формулы `$…$` остаются TeX-текстом до локальной копии KaTeX (WP9.2): счёт страниц по ним консервативный. (3) Задача `artifact.cheatsheet` без `request_id` ничего не пишет (старая строка `hub.deadline_set` у A ставит такую, пока её не снимут). (4) «Собери сам»: `POST /v1/hubs/{id}/cheatsheet/review` синхронно вызывает писателя в режиме проверки; блоки системы не трогаются, замечания — дополнения рядом с блоком ученика (П-11.1) или новые блоки; нет блоков ученика или граф не знает хаб — вызова нет; повтор с тем же ключом читает тот же ответ. (5) Согласие: `POST /v1/hubs/{id}/cheatsheet {idempotency_key}` → 202 `{request_id}`, `request_id = uuid5(ключ)`.
- Почему: `Т-§17.6`, `Т-§17.7`, WP23.5, D-C-143, D-C-144; п.5 §0.4.3 (без новых параметров и зависимостей), п.4 (обратимо).
- Обратимость: константы и текст запроса в одном модуле; окно — одно сравнение.
- Тесты: `T-AR4` `test_cheatsheet_one_page`, `test_cheatsheet_stops_after_fit_rounds`; `tests/artifacts/test_cheatsheet_offer.py`, `test_cheatsheet_wp23_5.py`, `test_cheatsheet_routes_wp23_5.py`. Три теста с `xfail(strict)` ждут D-A-196 (запись согласия).
- Требует подтверждения владельцем: да (класс C)

### D-C-160. WP1.3 шаг 2 / WP9: стенд на dbmate - `ops migrate` = `dbmate up`, `PHASE4_PROCESSES=true`, чистый старт
- Класс: C (инфраструктура зоны C; схема и миграции A не менялись)
- WP / место: WP1.3 шаг 2, WP9.1, WP9.2, WP9.4, issue #223 (план #215); `quack/ops/commands.py::migrate`, `quack/cli.py`, `backend/Dockerfile`, `compose.yaml`, `deploy/deploy.sh`, `deploy/README.md`
- Контекст: ТЗ WP1.1 говорит «`ops migrate` запускает `dbmate up` с ролью `quack_migrator`»; D-A-103 держит две схемы до WP1.3; в образе `quack-backend` бинарника dbmate не было (STATUS называл WP9.2 влитым с `dbmate`); как быть со стендом, где уже лежит схема Alembic, и с ролью миграций, документы не говорят.
- Решение:
  1. `ops migrate` запускает бинарник `dbmate --migrations-dir <backend>/db/migrations --no-dump-schema up`; DSN идёт в `DATABASE_URL` (не в аргументы), к нему добавляется `sslmode=disable`, если режима нет (lib/pq по умолчанию требует TLS, сеть `backend` без TLS). Каталог миграций - `/app/db/migrations` в образе.
  2. Образ: бинарник dbmate `v2.36.0` с проверкой sha256 (те же версия и сумма, что в `ci.yml` и `ct.sh`; тест сверяет три места), `COPY db ./db`; `alembic.ini` и `migrations/` из образа убраны.
  3. compose: `PHASE4_PROCESSES: "true"` у `api`, `projector`, `workers` (общий `x-db-env`); сервис `migrate` идёт первым, остальные ждут `service_completed_successfully` (так и было).
  4. `deploy.sh`: список миграций образа читается из `/app/db/migrations`; у образа Фазы 3 каталога нет, список пуст, любой файл нового образа считается новым - миграция запустится. Порядок шагов не менялся: роли, дамп, `migrate`, затем `projector`, `compute`+`workers`, `api`.
  5. Чистый старт (ТЗ Ф4 §2.3 строка 1): если в базе есть `public.alembic_version`, `ops migrate` завершается кодом 1 с пояснением и ничего не меняет, выкатка останавливается до запуска сервисов, старая версия остаётся. Базу стенда с данными Фаз 1-3 пересоздают вручную (том `postgres-data`).
  6. Роли `quack_app`/`quack_migrator` не переводятся: DSN сервисов остаются под `quack` (deploy/README, «что пока не готово»). Перевод - когда A снимет SQLAlchemy (шаг 5).
  7. Не закрыто этим шагом: `ops status` (проверка выкатки, `Д-§6.1` шаг 8), `can-rollback`, `restore-check`, `seed-learners`, `alerts` читают таблицы Фазы 3 (`core.learner_seq`, `ops.dlq`, `ops.llm_ledger`); на схеме Фазы 4 `ops status` вернёт код 2. Их перенос на `Db` - шаг 3 C (`quack/ops/*`, #215).
- Почему: ТЗ Ф4 WP1.1, WP9, §2.3; D-A-103, D-A-200; Д-§5.1, Д-§6.1, Д-§7.
- Обратимость: код и compose - откатом коммита; пересозданная база стенда - нет.
- Тесты: `tests/test_stand_dbmate.py` (compose, образ, порядок в `deploy.sh`, `ops migrate`), `test_compose_contract`, `test_deploy_env`, `test_ci_layers`.
- Требует подтверждения владельцем: нет (пересоздание базы стенда - действие владельца стенда)
### D-C-167. `ops extraction-eval`: команда оценки извлечения (S15, WP16.7)
- Класс: C (регистрация функции B, ТЗ §7.3 п.5; формат отчёта и метрики — D-B-209)
- WP / место: WP16.7, S15; `quack/cli.py` (`_extraction_eval`)
- Контекст: B дал `evaluate_extraction`, `load_eval_set`, `compare_reports`, но не имя и аргументы команды.
- Решение: `ops extraction-eval --set EVAL.json --learner UUID [--prompt extractor@N] [--runs N] [--out REPORT.json] [--against OLD_REPORT.json]`. `--learner` обязателен: чей реестр расходов оплачивает вызовы модели (учебный ученик оператора). Отчёт — один JSON на stdout (и в `--out`). `--against` — ворота Т-§18.4: рядом с отчётом печатается `comparison`, код выхода 1, если `regression` не `False` (падение точности/каппы либо «нечего сравнить» — не «пройдено», закон 8). Код 2: нет `META_API_KEY`, набор или прежний отчёт не читается/не `EvalSet`, `ValueError` функции (число прогонов, нет промпта). Шлюз собирается как у `replay-session`.
- Почему: STATUS WP16.7 «регистрация команды оценки извлечения (C, S15)»; docstring `extraction_eval.py` («`compare_reports` — ворота Т-§18.4»); по образцу `replay-session` (D-C-151).
- Обратимость: да (одна команда)
- Тесты: `tests/test_ops_p4_registry.py` (`test_extraction_eval_*`, `test_parser_of_extraction_eval`)
- Требует подтверждения владельцем: нет

### D-C-166. Шаг CI «реестр событий» — шаг `fast`, а не команда `ops registry-gaps` (WP9.3, Д-§5.2 п.6)
- Класс: C
- WP / место: WP9.3, WP2.4; `.github/workflows/ci.yml` (job `fast`)
- Контекст: issue #232 п.2 допускает шаг `uv run python -c …` либо команду `ops registry-gaps` с тем же телом.
- Решение: шаг `Event registry (реестр событий)` в `fast` после `pyright`, тело — строка из issue; команды `ops registry-gaps` нет. Шаг не требует сервисов; `registry_gaps()` чистая, поэтому не нужны `Settings` и роль процесса, которые подняла бы команда `ops`. Любой пробел — код 1, список пробелов в логе шага.
- Почему: Д-§5.2 п.6 говорит о шаге CI; меньше поверхности, чем команда, которой пользуется только CI.
- Обратимость: да
- Тесты: `tests/test_ci_layers.py::test_fast_job_checks_the_event_registry_gaps`
- Требует подтверждения владельцем: нет

### D-C-165. `ops event-registry`, `can-rollback`, `rebuild` на Фазе 4 (WP2.4, WP3.5; issue #232)
- Класс: C (регистрация функций A, ТЗ §7.3 п.5)
- WP / место: WP2.4, WP3.5; `quack/cli.py`
- Контекст: issue допускает переход «при `phase4_processes` или сразу». Реестр образа пишется при сборке (`Dockerfile`, без БД и без флага), поэтому от флага зависеть не может.
- Решение: (1) `event-registry` и `can-rollback` — всегда из `quack.runtime.event_registry` (каталог Фазы 4, `rules@3`); `seen_pairs(db)` берёт `Db` из `open_db`. Образ с `phase4_processes = off` всё равно содержит каталог Фазы 4, откат на образ с `rules@2` честно даёт `rules_changed`. (2) `rebuild` — при `phase4_processes`: `rebuild_learner(db, neo4j, settings, id, redis=redis)` из `graph.projection.rebuild`; `--stale-rules` — `stale_learners(neo4j, RULES_VERSION)`; `--all` — `SELECT learner_id FROM core.learners ORDER BY learner_id`; без флага остаётся код Фазы 3 (`core.learner_seq`, `graph.rebuild`) до шага 2 WP1.3 (#223). Каждый ученик пробуется; код 1, если исход хоть одного не `rebuilt` (`busy`, `active_session`, `failed`); код 2 — цель не указана. `ops/registry.py` не удалён (шаг 5 WP1.3).
- Почему: issue #232; Д-§5.1, Д-§6.3, D-A-189, D-A-218.
- Обратимость: да
- Тесты: `tests/test_ops_p4_registry.py`; `tests/test_ops_phase3.py` (библиотека Фазы 3 без CLI)
- Требует подтверждения владельцем: нет
