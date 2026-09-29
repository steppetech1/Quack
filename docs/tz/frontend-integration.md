# ТЗ: связка фронтенда с бэкендом

Состояние на 2026-09-20. Ветка `frontend/upstream-sync`.
Источники: `backend/app/api/*`, `backend/app/schemas/*`, `frontend/src/api/*`, `frontend/src/components/*`,
`docs/frontend-backend-integration.md`, `docs/sync-log.md`, `backend/docs/tz/40-phase4-background-quack.md`, `backend/docs/tz/50-phase5.md`.

Документ заменяет `docs/frontend-backend-integration.md` как рабочий план: тот описывал старт интеграции (этапы I0–I8),
здесь — что осталось после того, как I0–I5 в основном сделаны.

---

## 0. Главный вывод перед планированием

**В этом checkout фронт и бэк из разных поколений.**

| Что | Факт | Проверка |
|---|---|---|
| Фронтенд | Побайтово совпадает с `upstream/main` | `git diff HEAD upstream/main -- frontend` — пусто |
| Бэкенд | Отстаёт от `upstream/main` на 26 коммитов, 147 файлов | `git diff --stat HEAD upstream/main -- backend` |
| Чего нет в локальном бэке | Фазы 4 и 5: `app/api/quack.py`, `app/api/texts.py`, `app/api/commit.py`, `app/quack/*` (папка пустая), `app/sets/report.py`, `app/sets/pace.py`, `POST /programs/search`, `POST /programs/{id}/flag`, `GET /sets/{id}/summary`, демо-сид, `deploy/Dockerfile.web`, one-origin Caddy, `docs/runbook.md`, `scripts/check_release.py` | `git ls-tree -r --name-only upstream/main backend/app/api/` |
| `frontend/src/api/schema.d.ts` | Сгенерирован от бэка Ф4/Ф5: в нём есть `/quack*`, `/texts*`, `/programs/search*`, `/sets/{id}/summary` | `grep '"/quack"' frontend/src/api/schema.d.ts` |
| CI | Шаг «Check frontend API types» делает `make types` + `git diff --exit-code`. На локальном бэке типы перегенерируются **без** путей Ф4/Ф5 → шаг красный | `.github/workflows/ci.yml` |

**Следствие:** первая задача интеграции — не код, а синхронизация бэка (F0). До неё любая работа над Quack и текстами
будет писаться «в воздух», а CI останется красным по типам.

### 0.1 Что уже интегрировано (не переделываем)

| Домен | Эндпоинты | Фронт |
|---|---|---|
| Авторизация | `POST /auth/register|login|logout`, `GET /auth/me` | `components/account/remoteAuth.ts` |
| Состояние UI | `GET/PATCH/DELETE /state` | `components/account/store.ts` |
| Профиль | `GET/PATCH /profile` | `choice/catalog.ts` (`syncFields`), `ProfilePanel.tsx` |
| Каталог и подборка | `GET /programs`, `GET /matching`, `GET/POST/DELETE /saved` | `choice/catalog.ts`, реестр в `choice/programs.ts` |
| Чат подбора | `POST/GET /chat/selection/messages` (SSE) | `choice/remoteChat.ts`, `ChoiceApp.tsx` |
| Обзор подготовки | `GET /overview`, `POST /overview/milestones/{key}` | `prep/remotePrep.ts`, `Overview.tsx` |
| Сеты | `GET /sets`, `POST /sets/switch`, `GET/PATCH /sets/{id}`, `POST /sets/{id}/open`, `topics/{skill}/open|complete` | `prep/remoteSets.ts` |
| Задачи | `POST /tasks`, `/tasks/{id}/answer|skip|solution` | `prep/remoteTasks.ts`, `TopicWorkspace.tsx`, `FinalMockTest.tsx` |
| Модель знаний | `GET /knowledge`, `/knowledge/explain/{node}`, `POST /knowledge/refresh`, `misconceptions/{id}/dispute` | `prep/remoteKnowledge.ts`, `SkillGraph.tsx` |
| Диагностика и моки | `POST /diagnostic`, `GET /diagnostic/active`, `/{id}/answer|finish`, `POST /mocks…` | `prep/remoteDiagnostic.ts`, `DiagnosticMock.tsx` |
| Чат репетитора | `POST/GET /chat/prep/messages`, `/chat/prep/observe`, `/observations` | `prep/remoteChat.ts` |
| Инвалидация | `GET /prep/knowledge/version` | `prep/remoteChat.ts:fetchKnowledgeVersion` |

Транспорт готов и общий: `frontend/src/api/client.ts` (`api.get/post/patch/delete`, `ApiError{status, code, message, requestId}`),
`frontend/src/api/stream.ts` (`postSSE` — SSE поверх POST, keepalive, `AbortController`),
`frontend/src/api/backend.ts` (типизированные вызовы поверх `schema.d.ts`).

### 0.2 Что осталось локальным (предмет этого ТЗ)

| Область | Файлы фронта | Чем заменяется |
|---|---|---|
| Quack! (лента, темп, активность) | `quack/localSource.ts`, `quack/standing.ts`, `quack/planner.ts`, `quack/remoteSource.ts` (по **устаревшему** контракту) | `GET /quack`, `/quack/pace`, `/quack/activity`, `/quack/history`, `POST /quack/seen`, `/quack/{id}/accept|decline` |
| Дашборд «Обзор» | `dashboard/dashboardRules.ts` и все `dashboard/*.tsx` | `GET /quack` + `GET /overview` + `GET /matching` |
| Теория и разборы темы | `prep/topicContent.ts`, `prep/entContent.ts` | `GET /texts/{set_id}/{skill_id}?kind=guideline|explanation`, `POST …/opened`, `POST …/regenerate` |
| Сравнение программ | `choice/CompareView.tsx` на локальном `evaluate()` | `GET /matching/compare?ids=` (`rows`, `collapsed_same`, `conclusion`, `conclusion_status`) |
| Отчёт по завершённому сету | экрана нет | `GET /sets/{set_id}/summary` |
| Поиск и извлечение программ | нет | `POST /programs/search` → `GET /programs/search/{search_id}`, `POST /programs/{id}/flag` |
| Тексты «почему реалистично» | `catalog.ts` читает только `fits_text` | `MatchOut.realism_text` + `realism_text_status`, `MatchOut.soft`, `soft_pending` |
| Материалы (конспект, карточки, PDF) | `prep/materials.tsx` — генерация в браузере | остаётся локальным (вне контракта бэка), см. §5.7 |

---

## 1. Цель и границы

**Цель.** При `NEXT_PUBLIC_DATA_SOURCE=remote` весь домен приходит с бэкенда; в браузере остаются только представление,
UI-предпочтения и офлайн-фолбэк. Сценарий жюри (product-logic §9) проходится на живом стеке — от регистрации до роста шансов.

**В границах:** транспорт, адаптеры, экраны Quack и дашборда, тексты, сравнение, деградация, приёмка, деплой на один origin.

**Вне границ:** новые продуктовые экраны, редизайн, настоящий Google OAuth (G3 отложен, кнопки в UI нет),
Фаза 6 (стретчи), изменение frozen-контрактов бэка (`backend/app/schemas/*`, `events/dispatch.py`, `events/handlers.py`, `keys.py`).

---

## 2. Принципы

1. **Бэк-контракт первичен.** Если фронт ждёт не то, что отдаёт бэк, — правим фронт (адаптер), а не схему.
   Изменение схемы — только через запись в `docs/sync-log.md` → владелец зоны → rebase.
2. **Типы из OpenAPI.** Только `schema.d.ts` (`make types`) + `api/backend.ts`. Рукописных копий моделей бэка не заводим.
   Исключение — тип кадров SSE (`StreamEvent` в `stream.ts`): стриминговых тел в OpenAPI нет, держим рядом с парсером и под тестом.
3. **UI не знает про бэк.** Между ними адаптеры (`choice/catalog.ts`, `prep/remote*.ts`, новые `quack/remoteSource.ts`, `prep/remoteTexts.ts`).
   Чистые функции (`assistant.ts`, `programs.ts`, `standing.ts`, `dashboardRules.ts`) остаются офлайн-фолбэком и эталоном для тестов.
4. **Один переключатель на домен.** `NEXT_PUBLIC_DATA_SOURCE` — общий, доменные `NEXT_PUBLIC_SRC_*` перекрывают его.
   По умолчанию всё `local`: фронт должен собираться и работать без бэка.
5. **Мягкий отказ.** Недоступность Neo4j, LLM или поиска — это деградированное состояние экрана с человеческим текстом,
   а не белый экран и не `alert`. Статусы `generating|stale|failed` показываем словами.
6. **Один HTTP-слой.** Всё, что ходит на бэк, идёт через `api/client.ts`: разбор `{"error":{"code","message"}}`,
   `X-Request-Id`, `credentials: "include"`, редирект на `/login?next=` при 401.

---

## 3. Разрывы контракта (актуальный список)

| # | Разрыв | Сейчас | Решение | Этап |
|---|---|---|---|---|
| **X1** | Бэк в checkout без Ф4/Ф5, фронтовые типы — с Ф4/Ф5 | `make types` стирает `/quack*`, `/texts*`, `/programs/search*` → CI красный | Влить `upstream/main` в рабочую ветку до любой другой задачи | F0 |
| **X2** | `quack/remoteSource.ts` написан под несуществующий контракт: `GET /quack/state`, `GET /sse?topics=quack`, `POST /events` | На бэке Ф4: `GET /quack`, `/quack/pace`, `/quack/activity`, `/quack/history`, `POST /quack/seen`, `/{id}/accept|decline`. Роутеров `/sse` и `/events` нет вообще — SSE-модуль обслуживает только чат | Переписать `remoteSource.ts` под Ф4: поллинг и перечитывание после действий вместо `EventSource`. Клиентские события на бэк не шлём — их порождают доменные ручки | F3 |
| **X3** | Ленты рекомендаций на фронте нет | `QuackState` фронта — `standing` + `signals`, собранные локально из правил | Адаптер `QuackOut → QuackState` (§5.3): `items[]` → карточки с `accept/decline`, `pace` → `ExamPace`, `activity` → `ActivityGrid`, `new_batch`/`n_new` → свечение кнопки | F3 |
| **X4** | Теория темы локальная | `topicContent.ts`, `entContent.ts` — тексты в коде | `GET /texts/{set}/{skill}?kind=` с четырьмя статусами; `POST …/opened` обязателен (питает `after_guideline` и учёт активности) | F4 |
| **X5** | Сравнение считается в браузере | `CompareView.tsx` ← `evaluate()` | `GET /matching/compare?ids=`; `conclusion_status != ready` → «готовим вывод», таблица показывается сразу | F5 |
| **X6** | Тексты реалистичности и мягкое соответствие не показываются | `catalog.ts:121` берёт только `fits_text` | Добавить `realism_text` + `realism_text_status`, `soft`, `soft_pending` в модель карточки | F5 |
| **X7** | `store.ts` ходит на `/state` сырым `fetch` мимо `api/client.ts` | нет разбора `{error:{code}}`, нет 401-редиректа, нет `X-Request-Id` | Перевести на `api.*`; ключи `/state` — только UI-предпочтения (§5.6) | F6 |
| **X8** | Нет поиска и жалобы на программу | — | `POST /programs/search` (202 + опрос статуса), `POST /programs/{id}/flag`; `extracted_auto` → плашка «извлечено автоматически», `is_demo` → «демо» | F5 |
| **X9** | Отчёта по сету нет | `GET /sets/{id}/summary` не вызывается | Показывать при завершении сета; `text=null` → «готовим отчёт», статистика из `stats` доступна сразу | F4 |
| **X10** | Деградация не проверена сквозняком | `soft_pending`, `*_status`, 503, 409, 429 обрабатываются точечно | Единый слой статусов + прогон негативных сценариев (§7.3) | F7 |
| **X11** | Google-вход | `loginWithGoogle` в контракте, кнопки нет, на бэке ручек нет | Оставить отложенным; в `contract.ts` пометить «вне MVP» | — |

---

## 4. План работ

Оценки — в человеко-днях одного фронтендера, при поднятом и рабочем бэке.

### F0. Синхронизация репозитория и стенд — 0.5 дня, блокер для всего

1. Влить `upstream/main` в рабочую ветку (бэк Ф4 и Ф5, `deploy/`, `scripts/seed.py`, `docs/runbook.md`).
   Конфликтов по `frontend/` быть не должно — он идентичен.
2. Поднять стек: `make up` → `cd backend && uv run alembic upgrade head` → `make seed` → `make api`
   → `make worker-interactive` и `make worker-bulk` (без bulk-воркера не будет текстов, мягкого соответствия и ленты Quack).
3. `.env` бэка: `LLM_BASE_URL`, `LLM_API_KEY`, `MODEL_CHAT`, `MODEL_BULK`, `JWT_SECRET`.
   Известное ограничение: без непустого `LLM_API_KEY` приложение не стартует.
4. `frontend/.env.local`: `NEXT_PUBLIC_API_URL=http://localhost:8000`, `NEXT_PUBLIC_DATA_SOURCE=remote`.
5. Прогнать `make types`, закоммитить `schema.d.ts`, если изменился.
6. Добавить в корневой `Makefile` цели `dev` (api и фронт одной командой) и `types-check` (генерация + `git diff --exit-code`).

**Приёмка:** `GET /health` → `ok` по всем зависимостям; `make types` не даёт диффа; `npm run build` зелёный;
в браузере проходит регистрация → `/choice` → карточки программ приходят с бэка.

### F1. Ревизия транспорта — 0.5 дня

1. Все обращения к бэку — через `api/client.ts`. Исключения на сегодня: `store.ts` (X7), `quack/remoteSource.ts` (X2).
2. В `client.ts`: 401 → редирект на `/login?next=<current>` (сейчас только пробрасывается `ApiError`);
   один повтор через 300 мс только для идемпотентных `GET`.
3. В `stream.ts`: тест парсера на фикстурах из `backend/tests/api/test_chat_sse.py` — разорванные кадры, `\r\n`, `: keepalive`.

**Приёмка:** протухшая cookie на любом экране уводит на `/login?next=`, после входа возвращает туда же;
`grep -rn "fetch(" frontend/src --include=*.ts --include=*.tsx` не находит обращений к API вне `api/client.ts`.

### F2. Хвосты подбора — 1 день

1. «Начать заново» чистит профиль и избранное **на бэке**, а не только локально (`ChoiceApp.tsx:845` удаляет `saved`, профиль не трогает).
2. Сверить шкалу реалистичности: `MatchOut.realism ∈ {impossible, try, possible}` ↔ фронтовый `Level ∈ {unlikely, try, realistic}`.
   Маппинг есть в `catalog.ts` (`LEVEL`) — покрыть юнит-тестом, чтобы расхождение ловилось сборкой, а не глазами.
3. Дефект из `docs/frontend-backend-integration.md` §8: агент записывал `sat_score=1300` при пороге 800,
   панель показывала значения удвоенными. Проверить на бэке Ф4; если воспроизводится — запись в `docs/sync-log.md` для B2,
   на фронте не «чинить» клампом.

**Приёмка:** «начать заново» → перезагрузка → профиль пуст, избранного нет; смена бюджета в панели меняет порядок подборки.

### F3. Quack! и дашборд — 3–4 дня, самый крупный кусок

**3.1 Переписать `quack/remoteSource.ts`** под контракт Ф4 (§5.3). Убрать `EventSource`, `/quack/state`, `POST /events`.
Модель обновления: `GET /quack` при открытии приложения, при возврате фокуса на вкладку, по таймеру 60 с
и после каждого действия, меняющего источники правды (ответ на задачу, сохранение программы, правка профиля, отметка вехи).

**3.2 Адаптер `QuackOut → QuackState`** (`quack/contract.ts` не меняем — он и есть интерфейс экранов):

- `pace.exams[]` → `ExamPace[]`: `words` → `summary`, `on_track` и `forecast` → `level: PaceLevel`,
  `variants[]` → `advice[]` и `adviceActions[]` (каждый вариант — реальное действие, а не текст).
- `items[]` (`RecommendationOut`) → лента: `title`, `reason`, `action_text`, `urgency`, `forecast_after`.
  «Принять» → `POST /quack/{id}/accept` → перечитать `GET /quack` и затронутый домен; «отклонить» → `POST /quack/{id}/decline` с причиной.
- `activity` → `ActivityGrid` (`days[]`, `hours_per_week_actual|declared`); `computed_at = null` → «считаем».
- `new_batch`, `n_new` → свечение кнопки; открытие экрана → `POST /quack/seen` (сбрасывает `new_batch`).

**3.3 Дашборд.** `dashboard/*.tsx` переводим на `GET /quack` + `GET /overview` + `GET /matching`.
`dashboardRules.ts` остаётся локальным фолбэком и источником вспомогательных чистых функций (календарь, объединение экзаменов),
но шансы, темп, конфликты и активность при `remote` приходят с сервера.

**3.4 Локальный источник не удаляем.** `localSource.ts` живёт под `NEXT_PUBLIC_QUACK_SOURCE=local` и как фолбэк при 503.

**Приёмка:** ответ на задачу → Quack показывает новый прогноз (сразу после обновления или в пределах минуты);
принятие рекомендации «перенести дату» меняет дату в профиле и пересчитывает подборку;
повторное открытие Quack не светится; при недоступном `GET /quack` экран показывает последнее известное состояние с пометкой «офлайн».

### F4. Тексты темы и отчёт по сету — 2 дня

1. Новый адаптер `prep/remoteTexts.ts`: `get(setId, skillId, kind)`, `opened(setId, skillId, kind, textHash)`, `regenerate(setId, skillId, kind)`.
2. `TopicWorkspace.tsx`: теория и разбор — из `GET /texts/…`, локальные `topicContent.ts` и `entContent.ts` — фолбэк при `failed` и при работе без бэка.
   Статусы: `ready` → текст; `generating` → скелетон «готовим объяснение»; `stale` → текст с пометкой «обновляем»;
   `failed` → локальный текст и кнопка «сгенерировать ещё раз» (`POST …/regenerate`, 202).
3. Открытие теории обязано слать `POST /texts/{set}/{skill}/opened` — от него зависят `after_guideline` в оценке ответа и учёт активности.
4. Отчёт по сету: `GET /sets/{set_id}/summary` на экране завершения; `text = null` → показать `stats` и «готовим отчёт».

**Приёмка:** открыл тему → пришёл guideline с бэка → событие `guideline.opened` записано (видно в логах API);
ответ на задачу после открытия теории помечен `after_guideline=true`; завершение сета показывает отчёт или «готовим».

### F5. Сравнение, тексты реалистичности, поиск программ — 2 дня

1. `CompareView.tsx` ← `GET /matching/compare?ids=`: `rows` (с учётом `relevant_to_student`),
   `collapsed_same` — сворачиваемый блок «одинаково», `conclusion` и `conclusion_status`.
2. Карточка программы: `realism_text` (статусы как в F4), `soft` и `soft_pending` → «уточняем соответствие»,
   `is_demo` → плашка «демо», `extracted_auto` → «извлечено автоматически» со ссылкой `source_url` и датой `checked_at`,
   кнопка «данные неверны» → `POST /programs/{id}/flag` (пол, то есть `extracted_auto=false`, флагать нельзя — бэк отдаёт 409).
3. Поиск: `POST /programs/search {query}` → 202 `{search_id}` → опрос `GET /programs/search/{search_id}`
   (`queued|running|done|unavailable`) с бэкоффом; `unavailable` → «поиск сейчас недоступен».

**Приёмка:** сравнение двух программ показывает таблицу сразу и вывод, когда он готов; жалоба на программу убирает её из подборки;
поиск по запросу добавляет найденные программы в каталог.

### F6. `/state` — только UI-предпочтения — 1 день

1. `store.ts` — на `api/client.ts` (X7).
2. Ревизия ключей: в `/state` остаются ширины панелей, активная вкладка, положение карты, «подсказка показана», выбранные экзамен и сет.
   Доменное (профиль, чаты, избранное, прогресс, базовая линия Quack) — из доменных ручек.
   Каждый оставшийся ключ — строкой в §5.6 с обоснованием.
3. Лимиты бэка: не больше 200 ключей на `PATCH`, ключ до 200 символов — клиент обязан батчить и не превышать.

**Приёмка:** `GET /state` аккаунта, прошедшего сценарий жюри, содержит только UI-ключи;
`DELETE /state` не теряет ни профиля, ни прогресса.

### F7. Деградация, ошибки, устойчивость — 1.5 дня

Единая таблица поведения (§6) реализована и проверена вручную по каждому пункту (§7.3).

### F8. Демо и деплой на один origin — 1 день

1. `deploy/Caddyfile` и `deploy/Dockerfile.web` из `upstream/main`: фронт и `/api/*` с одного origin →
   cookie `Secure`, CORS не нужен, `NEXT_PUBLIC_API_URL=/api`.
2. Проверить, что SSE не буферизуется: `flush_interval -1` в Caddy; бэк уже отдаёт `X-Accel-Buffering: no`.
3. Демо-аккаунт из `scripts/seed.py` (Ф5) и `scripts/check_release.py` в чек-лист релиза; пройти `docs/runbook.md`.

**Приёмка:** на стенде по домену открывается фронт, вход работает, чат стримит без задержек, Quack обновляется.

---

## 5. Детальные контракты

### 5.1 Переменные окружения фронта

| Переменная | Значения | Смысл |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` / `/api` | база API; в проде — один origin |
| `NEXT_PUBLIC_DATA_SOURCE` | `local` \| `remote` | общий переключатель |
| `NEXT_PUBLIC_SRC_PREP` | `local` \| `remote` | перекрывает общий для раздела «Подготовка» |
| `NEXT_PUBLIC_QUACK_SOURCE` | `local` \| `remote` | Quack и дашборд |

Правило: доменный флаг, если задан, важнее общего (так уже сделано в `prep/remoteSets.ts`). Новые домены заводят флаг
`NEXT_PUBLIC_SRC_<DOMAIN>` по этому же образцу.

### 5.2 Слоты профиля → `PATCH /profile {path, value, by}`

`by: "user"` — правка из панели, `by: "assistant"` пишет агент сам. Пути проверены по `backend/app/schemas/profile.py`.

| Слот панели | `path` | Тип значения |
|---|---|---|
| класс | `level.grade` | `int` |
| год поступления | `level.admission_year` | `int` |
| направление | `direction.field` | `str` |
| альтернативы | `direction.alternatives` | `list[str]` |
| ЕНТ (пробный) | `academics.ent_trial_score` | `int` |
| профильная пара ЕНТ | `academics.ent_profile_pair` | `list[str]` |
| SAT сейчас / цель / дата | `academics.sat_score` \| `academics.sat_target` \| `academics.sat_date` | `int` \| `int` \| ISO-дата |
| IELTS сейчас / цель | `academics.ielts_score` \| `academics.ielts_target` | `float` |
| сильные предметы | `academics.self_assessment` | `dict[str, int]` |
| страны / города | `preferences.countries` \| `preferences.cities` | `list[str]` |
| язык | `preferences.language` | `str` |
| бюджет / валюта | `preferences.budget_per_year` \| `preferences.currency` | `int` \| `str` |
| грант | `preferences.grant_need` | `only_grant` \| `preferred` \| `not_needed` |
| обязательно / исключено | `constraints.required` \| `constraints.excluded` | `list[str]` |
| приоритеты | `priorities.ranking` | список из `realism`, `cost`, `ranking`, `location`, `program`, `research`, `mobility` |
| часов в неделю | `pace.hours_per_week` | `int` |
| глубина объяснений | `pace.explanation_depth` | `short` \| `normal` \| `deep` |
| уровень подсказок | `pace.hint_level` | `minimal` \| `normal` \| `generous` |

Метки `ProfileField.mark` показываем словами: `stated` — «сказал», `assumed` — «предположил», `default` — «по умолчанию».
`traits.summary` из панели не редактируется — его ведёт наблюдатель.

### 5.3 Quack: `QuackOut` → `QuackState`

```
GET  /quack                → QuackOut { pace, items[], activity, new_batch, batch_at, n_new }
GET  /quack/pace           → PaceOut { exams: ExamPaceOut[], as_of }
GET  /quack/activity?days= → ActivityOut { days[], window_days, active_days,
                                           hours_per_week_actual|declared, computed_at, tz }
GET  /quack/history?limit= → Page[RecommendationOut]   (accepted | declined | expired)
POST /quack/seen           → { shown: int }            (pending → shown, сбрасывает new_batch)
POST /quack/{id}/accept    → RecommendationOut         (повтор — 200, идемпотентно)
POST /quack/{id}/decline   → RecommendationOut         (после accept — 409)
```

`ExamPaceOut`: `exam_id ∈ {SAT_MATH, ENT_MATH}`, `forecast`, `on_track`, `test_date`, `hours_declared`, `hours_actual`,
`variants[]`, `words`.

`PaceVariantOut.kind ∈ {more_hours, move_date, remove_program, lower_target}` — это и есть `AdviceAction` фронта:
`params` несёт конкретику (новые часы, новая дата, новая цель), `affected_program_ids` — что ухудшится,
`available=false` с `unavailable_reason` — вариант показываем неактивным и с причиной.

`RecommendationOut.kind ∈ {pace_variant, milestone_due, conflict, next_set, set_change, program_new_fit,
saved_realism_shift, diagnostic_suggested, activity_pause}`, `urgency ∈ {urgent, high, normal, low}`,
`status ∈ {pending, shown, accepted, declined, expired}`.

Правила клиента:

- порядок ленты — по `position`, его не пересортировываем;
- «принять» меняет план на сервере: после ответа перечитываем `GET /quack` и затронутый домен (`/profile`, `/sets`, `/matching`);
- `POST /quack/seen` — ровно один раз на открытие экрана, не на каждый ререндер;
- **никакого `POST /events` с фронта**: события порождают доменные ручки.

### 5.4 Тексты

```
GET  /texts/{set_id}/{skill_id}?kind=guideline|explanation
     → GeneratedTextOut { kind, subject, set_id, status: ready|generating|stale|failed,
                          text, mark: generated|saved_version, prompt_version,
                          generated_at, input_hash, reason }
POST /texts/{set_id}/{skill_id}/opened     {kind}  → 204
POST /texts/{set_id}/{skill_id}/regenerate         → 202
```

`mark` показываем: `generated` — «сгенерировано», `saved_version` — «сохранённая версия».
`GET` ничего не пишет в БД и сам ставит задачу на недостающий текст — клиенту достаточно повторять запрос
через 2–5 с с ростом интервала до 30 с, пока статус `generating`.

### 5.5 Коды ошибок → поведение

| HTTP / `error.code` | Где | Что показываем |
|---|---|---|
| 400 `validation_failed` | любой | текст поля; **не** ориентируемся на 422 — бэк его не отдаёт |
| 401 `unauthorized` | любой | редирект `/login?next=` |
| 403 | чужой ресурс | «нет доступа», возврат к списку |
| 404 | `sets/{id}`, `diagnostic/active`, `texts` | для `diagnostic/active` — норма (нет активного прогона), молча стартуем новый |
| 409 `conflict` | `auth/register`, повторный `decline`, флаг пола | «уже существует» либо кнопка неактивна |
| 429 | чат, логин | «подожди секунду», ввод заблокирован до ответа |
| 503 `llm_unavailable` | чат, тексты | «ассистент временно недоступен», фолбэк на `assistant.ts` или локальный текст |
| 503 (Neo4j) | `knowledge`, `sets` | «карта строится», экран в деградированном виде |
| `network` (status 0) | любой | «нет связи с сервером», на экране остаётся последнее известное состояние |

### 5.6 Разрешённые ключи `/state`

Ширины панелей; активная вкладка раздела; положение и зум карты навыков; «подсказка показана»; выбранные экзамен и сет;
черновик несохранённого ввода. Всё остальное — доменные ручки. Новый ключ добавляется только вместе со строкой в этом списке.

### 5.7 Что осознанно остаётся на фронте

Генерация материалов (`prep/materials.tsx`: конспект, карточки, печать в PDF), анимации и переходы,
календарный экспорт (`dashboard/calendarExport.ts`), локальные чистые правила как фолбэк.

---

## 6. Деградация: единая таблица

| Отказ | Что делает бэк | Что показывает фронт |
|---|---|---|
| LLM недоступна | 503 `llm_unavailable` в чате; тексты остаются `generating` или `failed` | чат — фолбэк на правила и прямая фраза об этом; тексты — локальный вариант и кнопка «сгенерировать ещё раз» |
| Neo4j недоступен | `health` показывает деградацию, `knowledge` — soft-fail | карта навыков «строится», прогресс — из последних известных данных |
| bulk-воркер лежит | статус `generating` не меняется | скелетон не крутится вечно: через 30 с — «готовим, зайди позже» |
| Поиск недоступен | `GET /programs/search/{id}` → `unavailable` | «поиск сейчас недоступен», каталог не меняется |
| Сеть пропала | — | последнее состояние и пометка «офлайн», повтор при возврате фокуса |
| Cookie протухла | 401 | редирект на вход с `next` |

---

## 7. Тестирование и приёмка

### 7.1 Контрактные

- CI-шаг `make types` без диффа (уже есть в `.github/workflows/ci.yml`) — обязан быть зелёным сразу после F0.
- Юнит-тесты адаптеров: `catalog.ts` (`Program`/`MatchOut` → модель фронта), новый адаптер Quack (`QuackOut` → `QuackState`),
  маппинг `realism ↔ Level`, маппинг слотов профиля.
- Тест SSE-парсера на записанных кадрах из `backend/tests/api/test_chat_sse.py`.

### 7.2 Сквозной сценарий (Playwright, локальный стек)

Регистрация → анкета в чате → подборка → сохранить программу → сравнение → подготовка → диагностика →
открыть тему (теория с бэка) → ответить на задачи → карта навыков изменилась → сет пересобрался →
Quack показал новую рекомендацию → принять её → шансы на дашборде изменились.
Это же — сценарий жюри из product-logic §9.

### 7.3 Негативные (прогоняются вручную перед сдачей)

1. Остановить Neo4j → карта «строится», остальное живо.
2. Убрать `LLM_API_KEY` у воркера → тексты `failed`, чат отвечает фолбэком.
3. Остановить bulk-воркер → `generating` не висит бесконечно.
4. Протухшая cookie → редирект на вход, после входа возврат на тот же экран.
5. Двойная отправка в чат → 409 или блокировка ввода, дубля в истории нет.
6. Повторное «принять» у рекомендации → 200 без побочных эффектов; «отклонить» после «принять» → 409, кнопка неактивна.
7. Обрыв сети в середине стрима → пузырь не остаётся пустым, есть «повторить».

### 7.4 Критерий готовности

При `NEXT_PUBLIC_DATA_SOURCE=remote` в `frontend/src` нет обращений к `programs.ts`, `prepData.ts`, `dashboardRules.ts`,
`topicContent.ts`, `localSource.ts` иначе как через ветку фолбэка. При `local` приложение по-прежнему собирается и работает без бэка.

---

## 8. Порядок, зависимости, оценка

| Этап | Дней | Зависит от | Параллелится с |
|---|---|---|---|
| F0 синхронизация и стенд | 0.5 | — | — |
| F1 транспорт | 0.5 | F0 | F2 |
| F2 хвосты подбора | 1 | F0 | F1 |
| F3 Quack и дашборд | 3–4 | F0, F1 | F4 |
| F4 тексты и отчёт по сету | 2 | F0, F1 | F3 |
| F5 сравнение, поиск, флаги | 2 | F2 | F3, F4 |
| F6 `/state` | 1 | F3, F4 | — |
| F7 деградация | 1.5 | F3–F6 | — |
| F8 демо и деплой | 1 | F7 | — |

Итого 12.5–13.5 дней одним фронтендером; вдвоём — около 8 календарных дней (F3 и F4+F5 идут параллельно).

---

## 9. Риски

| Риск | Вероятность | Митигация |
|---|---|---|
| Слияние `upstream/main` конфликтует с локальными правками бэка | средняя | F0 делается первой и отдельным PR; фронт не трогаем — он идентичен |
| Контракт Quack Ф4 разойдётся с ожиданиями экранов | высокая | адаптер в одном файле, `contract.ts` не меняем; расхождения — в `docs/sync-log.md`, правим фронт |
| Тексты не генерируются на демо (нет bulk-воркера или ключа) | высокая | предгенерация по Ф5, локальные тексты как фолбэк, видимый статус |
| SSE буферизуется на проде | средняя | `flush_interval -1`, проверка на стенде, а не только локально |
| `/state` превращается во вторую БД | средняя | белый список ключей §5.6, ревью каждого нового ключа |
| Расхождение `schema.d.ts` и бэка | средняя | `types-check` в CI (есть), прогон после каждого мержа бэка |
| Пустой граф без `make seed` | низкая | шаг в runbook, `health` показывает Neo4j |

---

## 10. Первые шаги

1. **F0.1** — PR «Merge upstream/main (phases 4–5 backend)» в рабочую ветку, добиться зелёного CI.
2. **F0.2** — поднять полный стек с обоими воркерами, пройти сценарий жюри вручную, найденное записать в `docs/sync-log.md`.
3. **F1** — ревизия транспорта (`store.ts` на `api/client.ts`, 401-редирект).
4. **F3.1** — переписать `quack/remoteSource.ts` под `GET /quack`: самый длинный хвост, начинать раньше остальных.
