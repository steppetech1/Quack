# Фронт и бэк: что подключено

Состояние на 2026-11-05 (с пометками о PR, ещё не слитых: #319, #322, #324). Фронт теперь на контракте **Фазы 4** (65 путей `lib/api/openapi.json`, снимок из `backend/openapi.json` ветки `phase-4`), не Фазы 3: вход — `quack_session` cookie вместо JWT, ход сессии — `POST /v1/sessions/{id}/actions` вместо `/turns`, новый предмет — `POST /v1/domains/classify` вместо `structure/propose`.

## Как запустить

Режим фронта — переменная окружения в `frontend/.env.local` (шаблон в `.env.example`; после правки `.env.local` dev-сервер перезапустить):

- `NEXT_PUBLIC_QUACK_API=1` — данные с бэка;
- без неё — моки из `lib/mock/`.

Бэк — `docker compose --profile loadtest up -d --build` из корня репозитория (миграции `migrate`, `projector`, `workers` поднимаются сами). Рядом с `compose.yaml` нужен `.env`; обязательные `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, `JWT_SECRET`, остальные имена — `backend/.env.example`, но он отстаёт от `compose.yaml`: реально читаются ещё `TERMS_VERSION`, `AUTH_SUCCESS_REDIRECT_URL`, `EMAIL_API_KEY`, `GOOGLE_OIDC_CLIENT_ID/SECRET`, `S3_*`, `DELETIONS_S3_*` — все с пустым значением по умолчанию, можно не задавать для стенда. Без живого ключа Meta — `META_API_KEY=stand`, `META_API_BASE_URL=http://meta-mock:8080`, `META_MOCK_LATENCY=zero` (подменный сервер `meta-mock`, профиль `loadtest`).

Браузер обращается только к фронту; Next.js проксирует `/api/*` на `QUACK_API_URL` (по умолчанию `http://localhost:8000`, `next.config.ts`).

### Вход на локальном стенде — два открытых вопроса

1. **Письмо со ссылкой не уходит.** `ResendEmailSender.send_login_link` (`backend/quack/api/email.py`) сейчас `raise NotImplementedError`: `POST /auth/link` отвечает 500 на любом стенде без доведённого Resend-адаптера (WP18.1 числится «влит», но этот метод — нет). Google-вход тоже недоступен локально без реального `GOOGLE_OIDC_CLIENT_ID/SECRET` (действие владельца). Обходной путь для разработки — вставить строку в `core.auth_links` напрямую и открыть `verify`-ссылку с готовым токеном:

   ```sql
   -- token = 16 случайных байт, base64url; token_hash = sha256(token) hex
   INSERT INTO core.auth_links (token_hash, email, created_at, expires_at)
   VALUES ('<sha256(token) в hex>', 'dev@quack.local', now(), now() + interval '1 hour');
   ```

   Затем `GET /api/auth/link/verify?token=<token>` через сам фронт (не напрямую на бэк — см. пункт 2).

2. **`quack_session` не остаётся в браузере на `localhost`.** `session_cookie_domain` (`backend/quack/config.py:610`) захардкожен на `qvack.online` по умолчанию и **не прокинут через `compose.yaml`** в контейнер `api` — его нельзя переопределить через `.env` стенда, только правкой `compose.yaml` или кода. Браузер по RFC 6265 отбрасывает `Set-Cookie` с `Domain`, который не совпадает с хостом ответа и не является его родителем, поэтому на `http://localhost:…` cookie после `verify` просто не сохранялась. Исправлено на уровне фронта, без изменений бэка: `app/api/auth/link/verify/route.ts` и `app/api/auth/google/callback/route.ts` проксируют эти два пути сами (вместо общего rewrite в `next.config.ts`) и вырезают `Domain` из `Set-Cookie`, когда он не совпадает с хостом запроса (`lib/api/cookie-domain.ts`) — на проде, где `Domain=qvack.online` действительно покрывает `app.qvack.online`/`preview.qvack.online`, это no-op. Проверено вручную на локальном `quackp4`-стенде (порт `18000`): вход, создание предмета, запуск сессии, SSE хода — сквозной путь работает по-настоящему, не на `dev:stub`.

## Как устроено во фронте

- Относительные даты («через 3 дня», «вчера») считаются от настоящего «сегодня» в часовом поясе пользователя (`todayIso()`, `localIso()` в `lib/format.ts`); на моках «сегодня» — фиксированный день `MOCK_TODAY` (PR #319, до слияния они везде считались от него).
- `lib/api/client.ts` — запросы, cookie-сессия, ошибки, SSE через `fetch`. `post`, `patch`, `del` и `sse` типизированы по контракту.
- `lib/api/schema.d.ts` — типы из `lib/api/openapi.json` (`npm run api:types` после обновления снимка).
- `lib/api/adapters.ts` — бэк → типы интерфейса (`lib/mock/types.ts`, `lib/graph/types.ts`).
- `lib/api/data.tsx` — `DataProvider`: вход, профиль и настройки, предметы, графы, «Сегодня», активная сессия, история, живые обновления (`appliedSeq`). На моках — моки.
- `components/quack/live/*` — экраны на данных бэка; маршруты и `today-cards.tsx`/`domain-view.tsx` выбирают живые данные или моковые по `API_MODE`.
- `dev/stub-api.mjs` (`npm run dev:stub`) — Node-заглушка под контракт Фазы 4 для быстрой работы над экраном без бэка. Реализует меньше путей, чем настоящий бэк (ниже), — расхождение стоит иметь в виду при переносе проверки со стенда на стаб.

## Что подключено

| Экран | Эндпоинты |
|---|---|
| Вход по ссылке на почту, Google, выход | `POST /auth/link`, `GET /auth/link/verify`, `GET /auth/google/start`, `GET /auth/google/callback`, `POST /auth/logout`, `GET /v1/me` |
| Гейт согласия | `POST /v1/consents` (`kind: "terms"`) |
| Левая панель, вкладка «Предметы» | `GET /v1/domains` |
| Новый предмет: один ввод текста | `POST /v1/domains/classify`, `POST /v1/domains` |
| Граф предмета, главы | `GET /v1/domains/{id}/graph`, `GET /v1/domains/{id}/hubs` |
| **«Сегодня» под графом** | `GET /v1/domains/{id}/today` (`items` → карточки с действием по `reasons`, `pace_warnings` → карточка без действия); **новое** — раньше тут была клиентская догадка по `nearest_due_at_ms` из графа, теперь реальные рекомендации бэка |
| Страница темы: навыки, заметки (блоки), карточки для повторения | `GET /v1/topics/{id}`, `GET/POST /v1/topics/{id}/notes`, `PUT/DELETE /v1/artifacts/{id}/blocks/{id}`, `GET /v1/topics/{id}/cards` |
| Шпаргалка главы | `GET/POST /v1/hubs/{id}/cheatsheet` (страница главы и страница темы) |
| Страница главы: материалы, режим подготовки, дедлайн | `GET /v1/domains/{id}/materials` (список), `POST /v1/hubs/{id}/prep-mode` (только включение), `PATCH /v1/hubs/{id}` (`deadline_at_ms`: сменить или убрать); хук `components/quack/live/use-hub-api.ts` |
| Оценка после дедлайна (PR #322, до слияния не подключено) | `grade_hub_ids` из `GET /v1/domains/{id}/today` и `POST /v1/hubs/{id}/grade`; блок «Как прошло?» на странице главы, «Не сейчас» хранится в `localStorage` (у бэка нет события отказа) |
| Страница темы: «Я это уже знаю», «Эта тема мне не нужна» | `POST /v1/topics/{id}/known`, `POST /v1/topics/{id}/hide` (после «не нужна» граф перечитывается) |
| Карточки: правка и удаление (PR #324, до слияния не подключено) | `PATCH /v1/cards/{id}` (текст лицевой стороны), `DELETE /v1/cards/{id}`; оба отвечают всей колодой, у правленой карточки новый id |
| Skills пользователя | `GET/POST/PATCH/DELETE /v1/learner-skills` (вкладка «Skills», меню `/` в поле ввода); вызов `/имя` в сообщении сессии уходит как `skill_invocation`. У бэка нет выключателя и отдельного имени: имя и есть команда (`[\w-]{1,40}`), один скилл на тип артефакта, поэтому в режиме API переключатель скрыт, а «вызывать через /» всегда включено |
| Поиск | `GET /v1/search` |
| Сессия: история, ход (SSE), присутствие, конец, итог | `POST/GET /v1/sessions`, `GET /v1/sessions/active`, `GET /v1/sessions/{id}`, `POST …/actions`, `POST …/heartbeat`, `POST …/end`, `GET …/summary` |
| Панель сессий справа | `GET /v1/sessions` |
| Настройки «Общие»: имя, предпочтения | `PATCH /v1/learner/preferences` |
| Удаление аккаунта | `DELETE /v1/learner` (на этом стенде падает 500: `quack/storage/deletions.py::write` тоже `NotImplementedError` — журнал удалений в S3 не доведён, см. WP25 в `STATUS.md`) |
| Живые обновления | `GET /v1/learner/stream` (SSE `projection_advanced`, сами данные — обычными `GET`) |

## Чего нет во фронте (на бэке в основном уже есть — см. `STATUS.md`)

Контракт даёт 65 путей, из них не вызывается ни один из этих (все — реальные модули бэка, большинство «влит» по `STATUS.md` §4):

- **Материалы** — подключён только список на странице главы (`GET /v1/domains/{id}/materials`). Не вызываются `POST /v1/domains/{id}/materials` (загрузка лекций: на странице главы и в поле ввода нет прикрепления) и `GET /v1/materials/{id}` (состояние разбора).
- **Канвас, что ещё не подключено** — распознавание строк и пометки учителя (`line_ready`, `mark`, `hint`), `POST /v1/canvases/{id}/photos`, `.../pairing`, `.../lines/{id}/correct`, блоки задач (`task_id`, слой учителя). Формулы, тексты и картинки на доске остаются в браузере (у бэка для них нет позиции), название тоже (в `CanvasView` нет поля). Подключено: создание в сессии, список, `GET /v1/canvases/{id}`, чат с учителем (`message` с `canvas_refs`), штрихи по `WS /v1/canvases/{id}/ws` (пачки с подтверждением и повтором) и их возврат из `/replay`. Сокет ходит напрямую к API: в проде нужен `NEXT_PUBLIC_QUACK_WS` (например `wss://api.qvack.online`); чанки replay браузер читает по подписанным ссылкам хранилища, ему нужен CORS для origin фронта.
- **Компаньон** — `POST /auth/link` (companion scope), фото через телефон.
- **Режим подготовки и «Сегодня», что осталось** — `POST /v1/hubs/{id}/prep-mode {enabled: false}` (выключить режим: кнопки нет), `minutes` из `TodayView` не показан (только `items`, `pace_warnings`, `grade_hub_ids`). Оценку, введённую после дедлайна, прочитать нельзя: у бэка нет `GET` (issue #323 — и про то, что вес дедлайна после `grade.reported` не снимается).
- **Правки темы, что осталось** — `POST /v1/topics/{id}/merge`, `GET /v1/topics/{id}/why`, `POST /v1/disputes`, `POST /v1/verdicts/{seq}/typo`, `POST /v1/episodes/{seq}/reassign`: разделение темы, «почему эта тема здесь», оспаривание вердикта — нет кнопок (пункты «переименовать / слить / перенести» меню страницы темы на моках).
- **Skills: помощник, шаблоны, перенос** — в режиме API «Создать скилл» (`skill-guide.tsx`) — короткая форма: имя = команда (`[\w-]{1,40}`), один тип артефакта или без привязки, текст (1–2000); проверяет контракт до отправки (имя занято, тип занят другим скиллом) и показывает ответ бэка (409/422) в самой форме, потом `POST /v1/learner-skills`. `GET /v1/system-templates` вызывается при раскрытии «Системные шаблоны как основа» (`lib/api/skills.ts`): это промпты системы (teacher, artifact_writer, artifacts) для чтения; в поле текста копируются только те, что влезают в 2000 символов (на деле это `artifacts`). Скиллы из `localStorage` переносит баннер `skills-migrate.tsx` после подтверждения: `POST` по одному, имя, которое уже есть на бэке, пропускается, занятый тип у переносимого снимается (скилл остаётся вызываемым по имени), после переноса локальная копия стирается, отказанные остаются в браузере; нетронутые мок-образцы не предлагаются. Мок-режим без изменений (скриптовый помощник).
- **Skills, что осталось** — `askTeacher` (`lib/api/canvases.ts`) принимает `skillInvocation`, но `board/chat.tsx` его пока не передаёт: нужен шестой аргумент `skillInvocation(v)` из `lib/skills.ts`.
- **Карточки, ответы на них** — оценки в окне колоды остаются в браузере и пропадают, до модели ученика они не доходят. Бэк принимает ответ на карточку только как действие `answer` с `source: "card"` внутри активной сессии, а текста ответа карточки API не отдаёт (в ответе только `prompt`), поэтому «Показать ответ» в режиме API пустой. Вопрос к потоку B — issue #325.
- **Блоки конспекта и шпаргалка, что осталось** — `POST /v1/artifacts/{id}/blocks/{block_id}/accept` (принять «дополнение» к блоку), `POST /v1/hubs/{id}/cheatsheet/review`.
- **Экспорт и данные** — `GET /v1/topics/{id}/export/anki|obsidian`, `GET /v1/learner/exports/{id}`: кнопка «Экспорт» в «Данные и аккаунт» показывает «скоро»; `PATCH /v1/learner/settings` не вызывается (настройки идут через `PATCH /v1/learner/preferences`).
- **Визуализации** — `GET /v1/viz/{id}`.
- **Голос в сессии** — `POST /v1/sessions/{id}/voice`.
- **Видимость фоновых задач** — `GET /v1/sessions/{id}/jobs`.
- **Бюджет LLM** — `GET /v1/llm/access`.
- **Исследовательское согласие** — `POST/DELETE /v1/consents/research` (только `terms` подключён).
- Не подключено и раньше: `GET /v1/sessions/{id}/events`.

## Состояния тем

У бэка восемь кодов `display_state` (Т-§22.2, `DisplayCode` в `lib/graph/types.ts`), в дизайне четыре (`topicState()` в `lib/api/adapters.ts`):

- `solved_independently` → «решаешь сам»;
- `inconsistent`, `in_progress` → «пора повторить»;
- `confused_with`, `persistent_error`, `struggling` → «путаешь»;
- `not_tested`, `known_from_other_domain`, нет кода → «ещё не изучал».

Пропущенные и архивные темы (`status`) на граф не попадают; `visibility: "hidden"` тоже скрыта.
