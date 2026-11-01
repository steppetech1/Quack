# PHASE4_NOTES

Журнал Фазы 4 (ТЗ Ф4 §0.5, WP0 п.6). Общие разделы ведёт поток C; каждый поток пишет только в свой
раздел. Файл создан потоком B до общих разделов C: при слиянии общие разделы C встают выше раздела B.

---

## Поток B

### Статус пакетов

| WP | Статус | Тесты |
| :-- | :-- | :-- |
| WP0 (B) | выполнен: аудит B8–B12, B20, B25, B27; список промптов — ниже | — |
| Этап I контрактов G1 (S7, S8, S11, S13) | в работе | см. «Красные прогоны» |

### Аудит §3 (зона B)

Проверено по `main` `9ef6e3e` (= `phase-4` на начало работы).

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

### Проверки WP0 п.3 (зона B)

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

### Промпты (WP0 п.5)

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
промптам — `documentation/SYSTEM_PROMPTS_PHASE4_REGISTRY.md` (документ владельца). Его открытые
вопросы, которые закрывает B: п.4 (раскладка полей `extractor@3`, `Element`, `planUpdate`, дедлайн,
`params`) — закрыты кодом (D-B-107…D-B-110, имя `new_misconception` взято из реестра); п.6 (выходы
`task_checker`, `task_arbiter`, `today_planner`, `viz_builder`, `release_judge`) — D-B-113; п.3
(видит ли `second_opinion` первичный вердикт) — D-B-114.

Тексты Фазы 3 (`teacher@1`, `extractor@2`, `evaluator@1`, `task_gen@1`) — доктрина под старые
схемы; переносить их в новые роли без решения владельца нельзя (§0.4.4 п.13). Запрос владельцу —
в сигнале потока B. Перечни `intent` и `approach` выхода хода (WP13.3) тоже ждут доктрину:
до неё стоят временные значения (D-B-104).

### Журнал решений

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

### Заблокировано

(нет)

### Красные прогоны

- Этап I G1 (B): `uv run pytest tests/session/test_phase4_contracts.py tests/llm/test_phase4_ai_layer.py tests/practice/test_tasks_markup.py tests/practice/test_check_instant.py tests/test_contracts.py` — 33 красных (`NotImplementedError`: `TurnStreamParser`, `next_step_input`, `validate_markup`, `check`), 26 зелёных (контракты, `T-AI1`, `T-A6`).

### Правки тестов

- `tests/test_contracts.py` (`T-A6`): в `PRODUCT` добавлен `agents` (ТЗ Ф4 §4.1). Сила проверки не меняется.
