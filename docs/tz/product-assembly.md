# ТЗ: сборка продукта в единое целое — бэкенд под фронтом

Дата: 2026-09-20. Ветка: `integration/f0-upstream-merge`.
Источники: `backend/app/api/*`, `backend/app/main.py`, `backend/app/config.py`, `frontend/src/api/*`,
`frontend/src/components/*`, `arch-logic.md`, `product-logic.md`, `docs/tz/frontend-integration.md`,
`docs/frontend-backend-integration.md`, `docs/sync-log.md`, `docs/runbook.md`, `Makefile`, `.github/workflows/ci.yml`,
`deploy/*`.

Документ не отменяет `docs/tz/frontend-integration.md` (этапы F0–F8, они выполнены и закоммичены),
а продолжает его: здесь то, что осталось между «каждый экран умеет ходить в бэк» и «продукт собран и сдаётся
как одно целое».

---

## 0. Проверенное состояние на момент написания

Проверки выполнены в этом checkout'е, не со слов документов:

| Проверка | Команда | Результат |
|---|---|---|
| Типы фронта | `npx tsc --noEmit` | чисто |
| Юнит-тесты фронта | `npm test` (vitest) | 47 тестов, 4 файла, зелено |
| Сборка фронта | `npm run build` (Next 16, Turbopack) | зелено, 5 статических маршрутов (`/`, `/choice`, `/login`, `/pricing`, `/_not-found`) |
| Роутеры бэка | `grep @router app/api/` | 62 маршрута в 17 роутерах, включая Ф4/Ф5 (`/quack*`, `/texts*`, `/programs/search*`, `/sets/{id}/summary`) |
| CORS | `app/main.py` | `CORSMiddleware` внешним слоем, `allow_credentials=True`, origins из `CORS_ORIGINS` |
| Незакоммиченное | `git diff --stat` | 4 файла подготовки (`client.ts`, `PrepView.tsx`, `prepModel.ts`, `remoteSets.ts`) |

### 0.1 Что уже связано и не переделывается

| Домен | Бэкенд | Фронт |
|---|---|---|
| Вход, регистрация, сессия | `/auth/register\|login\|logout\|me`, cookie `quack_token` | `account/remoteAuth.ts`, `AuthGate.tsx` |
| UI-состояние | `GET/PATCH/DELETE /state` | `account/store.ts` (белый список ключей) |
| Профиль | `GET/PATCH /profile` | `choice/catalog.ts`, `ProfilePanel.tsx` |
| Каталог, избранное, подборка | `/programs*`, `/saved*`, `/matching`, `/matching/compare` | `api/backend.ts`, `choice/catalog.ts`, `CompareView.tsx` |
| Чат подбора (SSE поверх POST) | `POST/GET /chat/selection/messages` | `api/stream.ts`, `choice/remoteChat.ts` |
| Подготовка | `/sets*`, `/tasks*`, `/knowledge*`, `/diagnostic*`, `/mocks*`, `/overview`, `/prep/knowledge/version` | `prep/remote{Sets,Tasks,Knowledge,Prep,Diagnostic}.ts` |
| Тексты темы и отчёт по сету | `/texts/{set}/{skill}`, `/sets/{id}/summary` | `prep/remoteTexts.ts`, `TopicWorkspace.tsx`, `SetReport.tsx` |
| Чат репетитора и наблюдатель | `/chat/prep/messages`, `/chat/prep/observe`, `/observations` | `prep/remoteChat.ts` |
| Quack! и дашборд | `GET /quack`, `/quack/pace\|activity\|history`, `POST /quack/seen\|{id}/accept\|decline` | `quack/remoteSource.ts`, `quack/remoteAdapter.ts`, `quack/remoteStanding.ts`, `dashboard/*` |

Сквозной сценарий жюри пройден по API на живом стенде (`docs/sync-log.md`, запись 20:45): регистрация → чат →
анкета → подбор → план (10 сетов, 25 тем) → замер → конспект → задачи → карта знаний → отчёт по сету →
Quack с тремя рекомендациями.

### 0.2 Что мешает назвать продукт собранным

| # | Разрыв | Тип | Где видно |
|---|---|---|---|
| **A1** | Подготовка хранит домен в браузере: `quack-prep` в `/state` несёт `states`, `recall`, `evidence`, `misconceptions`, `doneSets`, `currentSet`, а серверные сеты и навыки «подмешиваются» в локальные демо-таблицы через `registerRemoteSets/registerRemoteSkills` | архитектурный | `prep/prepModel.ts`, `prep/prepData.ts:354,505`, `account/store.ts:36` |
| **A2** | Незакоммиченная правка «id сета — это uuid сервера, а не `s2`» лежит в рабочем дереве | незавершённое | `git diff` |
| **A3** | При упавшем Neo4j `GET /matching` и `GET /knowledge` отвечают 500 вместо мягкого отказа | бэкенд, деградация | `sync-log` 15:20 |
| **A4** | 500 из необработанного исключения уходит без CORS-заголовков — в браузере неотличим от обрыва сети | бэкенд, деградация | `app/main.py`, `sync-log` 13:15 и 15:20 |
| **A5** | С нерабочим ключом LLM текст навсегда `generating` (`AuthenticationError` не ловится), а `GET /health` всё равно отдаёт `llm_status: ok` | бэкенд, честность статуса | `agents/jobs_phase4.py::_one_text`, `sync-log` 20:45 |
| **A6** | `p_recall` после любого ответа ≈ 1.0 → навык становится `solid` и уходит из очереди даже после трёх неверных ответов | бэкенд, ядро модели знаний | `sync-log` 20:45 (ВОПРОС к B1) |
| **A7** | Тело 503 отдаёт ученику сырое сообщение провайдера со ссылкой на панель ключей | бэкенд, безопасность | `sync-log` 20:45 |
| **A8** | CI не собирает и не тестирует фронт: есть только `make types` + `git diff --exit-code` | процесс | `.github/workflows/ci.yml` |
| **A9** | Сквозного автотеста нет: сценарий жюри проходится руками и скриптом по API | процесс | `frontend-integration.md` §7.2 |
| **A10** | Реальный деплой не делался: нет домена, TLS, VPS-секретов; `Secure`-cookie и HTTPS проверены только на стенде Caddy :8080 | выпуск | `sync-log` 19:35 |
| **A11** | Без непустого `LLM_API_KEY` приложение не стартует, хотя по `product-logic §6.3` LLM обязана деградировать мягко | эксплуатация | `docs/frontend-backend-integration.md` §8 |
| **A12** | Dev-эргономика на Windows: `scripts/gen_types.sh` не находит бинарь, Git Bash подменяет `NEXT_PUBLIC_API_URL=/api` на путь MSYS | dev | `sync-log` 19:35 |

---

## 1. Целевая архитектура связки

Правило не меняется (`arch-logic.md` §7): **модель понимает и объясняет, детерминированные правила единственные
считают и пишут**. Из этого следует граница фронта и бэка, которую ТЗ закрепляет:

```
L1 браузер :3000        экраны, анимации, материалы (конспект/карточки/PDF), UI-предпочтения
        │                адаптеры: api/backend.ts, choice/catalog.ts, prep/remote*.ts, quack/remote*.ts
        │                транспорт: api/client.ts (один), api/stream.ts (SSE поверх POST)
        ▼ HTTP + cookie quack_token
L2 FastAPI :8000        62 маршрута, ошибки {"error":{"code","message"}}, X-Request-Id
        ├──► L3 LLM      только через агентов и инструменты; чат стримит, фон пишет тексты
        └──► L4 правила  matching, sets, knowledge, quack — единственные, кто считает
                 ▼
L5 данные               PostgreSQL (источник правды) · Neo4j (граф) · Redis (очередь, локи, кэш)
```

Пять инвариантов, по которым принимается результат:

1. **Ни одно число на экране не посчитано в браузере при `remote`.** Шансы, реалистичность, прогноз, темп,
   состояние навыка, состав сета — читаются с бэка. Локальные чистые функции (`programs.ts`, `standing.ts`,
   `dashboardRules.ts`, `prepModel.ts`) остаются офлайн-фолбэком и эталоном тестов — но не путём по умолчанию.
2. **Один HTTP-слой.** Всё через `api/client.ts`: разбор ошибок, `credentials: "include"`, `X-Request-Id`,
   редирект на `/login?next=` при 401, один повтор для идемпотентных GET.
3. **`/state` — не вторая БД.** Только белый список UI-ключей с обоснованием на каждый (`account/store.ts`).
4. **Бэк-контракт первичен.** Расхождение чинится адаптером на фронте; схема меняется только записью в
   `docs/sync-log.md` → владелец зоны → rebase. Frozen: `backend/app/schemas/*`, `events/dispatch.py`,
   `events/handlers.py`, `keys.py`.
5. **Отказ — это состояние экрана, а не белый лист.** Каждый отказ из §5 имеет человеческую фразу и следующий шаг.

---

## 2. Топология запуска

### 2.1 Разработка

| Компонент | Порт | Запуск |
|---|---|---|
| Next.js (фронт) | **3000** | `cd frontend && npm run dev` или `make web` |
| FastAPI | 8000 | `make api` |
| Воркер interactive | — | `make worker-interactive` (чат, наблюдатель) |
| Воркер bulk | — | `make worker-bulk` (тексты, мягкое соответствие, лента Quack) |
| PostgreSQL 16 | 5432 | `make up` |
| Neo4j 5 | 7687 / 7474 | `make up` |
| Redis 7 | 6379 | `make up` |

`frontend/.env.local`:

```
NEXT_PUBLIC_DATA_SOURCE=remote
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Доменные флаги `NEXT_PUBLIC_SRC_PREP`, `NEXT_PUBLIC_QUACK_SOURCE` перекрывают общий; по умолчанию не заданы и
наследуют `DATA_SOURCE`. Без `.env.local` фронт работает на демо-данных в браузере — это поддерживаемый режим,
а не поломка.

`CORS_ORIGINS` бэка по умолчанию покрывает `http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:3001`.
Cookie `quack_token` — `HttpOnly`, `SameSite=Lax` на dev. Одна команда на оба процесса — `make dev`.

### 2.2 Прод: один origin

Caddy отдаёт статический экспорт фронта и проксирует `/api/*` на FastAPI (префикс срезается, `flush_interval -1`
для SSE), `/health` — на API для релизного smoke. Фронт собирается с `NEXT_PUBLIC_API_URL=/api`,
`NEXT_PUBLIC_DATA_SOURCE=remote`. CORS в проде не нужен, cookie получает `Secure`.

**Требование ТЗ:** ни один экран не должен зависеть от того, `http://localhost:8000` перед ним или `/api` —
это уже так, потому что база берётся из одной константы `API_URL` в `api/client.ts`.

---

## 3. Работы

Оценки — человеко-дни. Владельцы по зонам из `CLAUDE.md`: B1 (правила, граф, сеты, знания), B2 (LLM, агенты),
B3 (API, БД, события, платформа), F (фронт), I (интеграция).

### A1. Подготовка: сервер — источник правды — 2 дня, F, самый крупный кусок

Сегодня `prepModel` — это полноценная модель подготовки в браузере: она стартует с демо-значениями
(`doneSets: ["s1","e1"]`, `currentSet: "s2"`, `reportFor: "s1"`), сохраняется целиком в `/state` под `quack-prep`
и лишь дополняется серверными данными. Отсюда весь класс дефектов «локальный id встретился там, где ждут uuid»,
который сейчас лечится фильтром `isUuid` в четырёх местах.

Сделать:

1. **Разделить `quack-prep` на два ключа.**
   - `quack-prep-ui` — остаётся в `/state`: активная вкладка, выбранный экзамен, положение и зум карты навыков,
     свёрнутость панелей, черновики ввода.
   - `quack-prep-materials` — остаётся в `/state`: сгенерированные в браузере конспекты и карточки
     (`frontend-integration.md` §5.7).
   - Домен (`states`, `recall`, `evidence`, `misconceptions`, `doneSets`, `currentSet`, `reportFor`,
     `milestonesDone`, `diagnosticDone`) при `REMOTE_PREP` **не сохраняется вовсе**: он читается из
     `GET /sets`, `GET /knowledge`, `GET /overview` при загрузке экрана и при росте `GET /prep/knowledge/version`.
2. **`initialModel()` при `REMOTE_PREP` отдаёт пустую модель**, а не демо-набор (начато в незакоммиченной правке —
   довести и закрыть тестом).
3. **`registerRemoteSets`/`registerRemoteSkills` перестают дополнять демо-таблицы** и становятся единственным
   источником: при `REMOTE_PREP` `SETS`/`SKILLS` из `prepData.ts` в рантайме не участвуют, `setById` по
   неизвестному id не выдумывает сет, а экран показывает «план строится».
4. **Убрать `isUuid`-заплатки** из `PrepView.tsx` и `prepModel.ts` там, где они перестают быть нужны после п.2–3;
   оставить одну проверку на входе ревайвера как защиту от старого `/state`.
5. Удалить из белого списка `store.ts` ключи `quack-baseline`, `quack-history`, `quack-known`, если при
   `QUACK_SOURCE=remote` они не пишутся (проверить, а не предположить).

**Приёмка.** Свежий аккаунт в `remote`: `GET /state` содержит только UI-ключи и материалы; ни одного `s1`/`e1`/`s2`
в ответе. Подготовка после очистки `localStorage` и перезагрузки показывает тот же план, что API. Переключение на
`local` по-прежнему даёт рабочий демо-режим (сборка и экран).

### A2. Довести и закоммитить правку идентификаторов сетов — 0.5 дня, F

Незакоммиченные изменения (`api/client.ts:isUuid`, `PrepView.tsx`, `prepModel.ts`, `remoteSets.ts`) — это
промежуточный шаг к A1. Их нужно довести до состояния «зелёные тесты + осознанный коммит», а не держать в рабочем
дереве: они меняют поведение выбора сета и замера, и в таком виде их легко потерять.

**Приёмка.** `git status` чист; `npm test` и `npm run build` зелёные; в ветке есть коммит с объяснением, почему
локальный id сета больше не годится.

### A3. Мягкий отказ графа в `/matching` и `/knowledge` — 1 день, B1 + B3

Сейчас при упавшем Neo4j эти два маршрута отвечают 500, хотя `AvailabilityOut` и `product-logic §6.3` обещают
деградацию. Для сценария жюри это значит «Neo4j лёг — подбор и карта легли вместе с ним».

Сделать: обработать `ServiceUnavailable`/`SessionExpired` так же, как уже сделано в `app/api/auth.py`
(`test_me_answers_when_neo4j_goes_down`): отдавать 200 с пометкой недоступности слоя, а не 500.

**Приёмка.** Остановленный Neo4j → вход работает, профиль работает, подборка показывает последнее известное с
пометкой, карта навыков говорит «строится»; ни одного 500 в логах API за сценарий.

### A4. 500 с CORS-заголовками — 0.5 дня, B3

Необработанное исключение уходит мимо `CORSMiddleware`, поэтому в браузере любая внутренняя ошибка выглядит как
обрыв сети. Фронт не может отличить «сервер ответил» от «связи нет» и честно сказать об этом ученику.

Сделать: гарантировать, что ответ 500 проходит тот же путь, что и `AppError` — заголовки CORS и `X-Request-Id`
на месте.

**Приёмка.** Искусственная 500 на кросс-origin стенде читается фронтом как `ApiError(500, "internal")` с
`requestId`, а не как `status 0`.

### A5. Честный статус LLM — 1 день, B2 + B3

1. `jobs_phase4._one_text` ловит только `PostcheckFailed` и `LLMUnavailable`; `openai.AuthenticationError`
   пролетает насквозь, и текст навсегда `generating`. Любая неустранимая ошибка провайдера должна давать `failed`
   с `reason`.
2. Брейкер не считает 401 своей ошибкой, поэтому `GET /health` отдаёт `llm_status: ok` на стенде, где ассистент не
   работает вообще, и `check_release.py` зелёный.
3. Тело 503 не должно содержать сырое сообщение провайдера со ссылкой на панель ключей (A7) — наружу идёт
   `{"error":{"code":"llm_unavailable","message":"..."}}` без внутренностей.
4. Заодно закрыть A11: отсутствие `LLM_API_KEY` не должно мешать старту приложения — это ровно тот режим, который
   `product-logic §6.3` называет «частично доступен».

**Приёмка.** С заведомо нерабочим ключом: приложение стартует, `/health` показывает `llm_status` не `ok`,
`check_release.py` краснеет, чат отвечает 503 с чистым телом, текст темы приходит `failed` и экран предлагает
«сгенерировать ещё раз».

### A6. `p_recall` после ответа — 1–2 дня, B1 (решение за владельцем модели знаний)

`apply_evidence` считает `recall_p(h, t, t)` — единицу при нулевом интервале, поэтому после любого ответа навык
выглядит выученным: три неверных ответа подряд дают `solid` с `p_recall ≈ 0.999`, и навык уходит из очереди.
Формула HLR верна (знание живёт в `half_life_h`), но `skill_level` и `build_queue` читают именно `p_recall`.

Варианты (выбор за B1): считать уровень по `p_at_obs`, либо брать разрыв на `due_at`, а не «сейчас».

**Почему это в ТЗ сборки.** Это единственный открытый дефект, который рвёт главное обещание продукта из
`arch-logic §02` — «ответил на задачу → состояние навыка и прогноз обновились». На демо это видно глазами.

**Приёмка.** Три неверных ответа подряд по навыку не делают его `solid`; навык остаётся в очереди; прогноз не
улучшается после провального сета.

### A7. Фронт в CI — 0.5 дня, F + B3

Сейчас CI проверяет фронт ровно одним шагом — `make types` и `git diff --exit-code`. Сборка, типы и тесты не
проверяются, хотя все три зелёные локально и стоят секунды.

Добавить в `.github/workflows/ci.yml` job `frontend`: `npm ci` → `npx tsc --noEmit` → `npm test` →
`npm run build` (с `NEXT_PUBLIC_DATA_SOURCE=remote`, `NEXT_PUBLIC_API_URL=/api` — то же, что в `Dockerfile.web`).

**Приёмка.** PR, ломающий адаптер или типы, краснеет в CI, а не на демо.

### A8. Сквозной автотест сценария жюри — 2 дня, F + I

Сценарий из `product-logic §9` проходится руками. Нужен Playwright-прогон против локального стека:
регистрация → анкета в чате → подборка → сохранить программу → сравнение → подготовка → замер → тема (теория с
бэка) → ответы на задачи → карта изменилась → сет пересобрался → Quack показал рекомендацию → принять →
шансы на дашборде изменились.

Плюс негативный набор (§5): Neo4j вниз, ключ LLM вниз, bulk-воркер вниз, протухшая cookie, двойная отправка в чат,
обрыв сети посреди стрима.

**Приёмка.** Один скрипт поднимает стек и проходит сценарий; падение любого шага даёт понятное имя шага.

### A9. Выпуск на домен — 1 день, B3 + I

Собрано и проверено на стенде Caddy :8080, но настоящего деплоя не было: нет домена, TLS, VPS-секретов
(`VPS_*`, `DOMAIN`). Проверить на реальном домене: `Secure`-cookie по HTTPS, SSE не буферизуется, `/health`
зелёный, вход под `demo@quack.kz`, `check_release.py` и `docs/runbook.md` от начала до конца.

### A10. Dev-эргономика — 0.5 дня, I

1. `scripts/gen_types.sh` на Windows не находит `node_modules/.bin/openapi-typescript` — звать через
   `node node_modules/openapi-typescript/bin/cli.js`, чтобы `make types` работал везде.
2. Git Bash подменяет `NEXT_PUBLIC_API_URL=/api` на путь MSYS — зафиксировать `MSYS_NO_PATHCONV=1` в скрипте сборки
   и строкой в `README`.
3. `frontend/.env.example` сверить с §2.1 и добавить `NEXT_PUBLIC_SRC_PREP`.

---

## 4. Справочник: экран → эндпоинты

| Экран / модуль фронта | Эндпоинты | Адаптер |
|---|---|---|
| `LoginView`, `AuthGate` | `POST /auth/register\|login\|logout`, `GET /auth/me` | `account/remoteAuth.ts` |
| весь UI | `GET/PATCH/DELETE /state` | `account/store.ts` |
| `ProfilePanel` | `GET /profile`, `PATCH /profile {path,value,by}` | `choice/catalog.ts` |
| `ChoiceApp` (чат) | `POST /chat/selection/messages` (SSE), `GET` история | `choice/remoteChat.ts`, `api/stream.ts` |
| Карточки программ | `GET /programs`, `GET /programs/{id}`, `GET /matching`, `GET/POST/DELETE /saved` | `choice/catalog.ts` |
| Поиск и жалоба | `POST /programs/search` → `GET /programs/search/{id}`, `POST /programs/{id}/flag` | `choice/catalog.ts` |
| `CompareView` | `GET /matching/compare?ids=` | `choice/catalog.ts` |
| `Overview`, вехи | `GET /overview`, `POST /overview/milestones/{key}` | `prep/remotePrep.ts` |
| `SetsView`, `SetDetail`, `RouteView` | `GET /sets`, `POST /sets/switch`, `GET\|PATCH /sets/{id}`, `POST /sets/{id}/open` | `prep/remoteSets.ts` |
| `TopicWorkspace` | `POST /sets/{id}/topics/{skill}/open\|complete`, `POST /tasks`, `/tasks/{id}/answer\|skip\|solution` | `prep/remoteTasks.ts` |
| `TopicTheory` | `GET /texts/{set}/{skill}?kind=`, `POST …/opened`, `POST …/regenerate` | `prep/remoteTexts.ts` |
| `SetReport` | `GET /sets/{id}/summary` | `prep/remoteSets.ts` |
| `SkillGraph`, `GraphCanvas` | `GET /knowledge`, `/knowledge/explain/{node}`, `POST /knowledge/refresh`, `/misconceptions/{id}/dispute` | `prep/remoteKnowledge.ts` |
| `DiagnosticMock`, `FinalMockTest` | `POST /diagnostic`, `GET /diagnostic/active`, `/{id}/answer\|finish`; `POST /mocks*` | `prep/remoteDiagnostic.ts` |
| Чат репетитора | `POST/GET /chat/prep/messages`, `POST /chat/prep/observe`, `GET /chat/prep/observations` | `prep/remoteChat.ts` |
| Инвалидация подготовки | `GET /prep/knowledge/version` | `prep/remoteChat.ts` |
| Quack! и дашборд | `GET /quack`, `/quack/pace\|activity\|history`, `POST /quack/seen`, `/{id}/accept\|decline` | `quack/remoteSource.ts`, `remoteAdapter.ts`, `remoteStanding.ts` |

Правила клиента для Quack (не менять): порядок ленты — по `position`; `POST /quack/seen` — один раз на открытие
экрана; «принять» → перечитать `GET /quack` и затронутый домен; `POST /events` с фронта не шлётся — события
порождают доменные ручки.

---

## 5. Деградация: одна таблица на продукт

| Отказ | Бэкенд | Фронт |
|---|---|---|
| LLM недоступна | 503 `llm_unavailable` в чате; тексты → `failed` с причиной (**после A5**) | чат: «ассистент временно недоступен» + подборка по правилам; текст: локальный вариант и кнопка «сгенерировать ещё раз» |
| Neo4j недоступен | `/auth/me`, `/profile` живы; `/matching`, `/knowledge` — мягкий отказ (**после A3**) | подбор — последнее известное с пометкой; карта — «строится» |
| bulk-воркер лежит | статус `generating` не меняется | через 30 с: «готовим, загляни чуть позже» + кнопка |
| Поиск недоступен | `GET /programs/search/{id}` → `unavailable` | «поиск сейчас недоступен», каталог не меняется |
| Внутренняя ошибка API | 500 с CORS и `X-Request-Id` (**после A4**) | «на сервере ошибка», можно повторить; в логах есть `request_id` |
| Сеть пропала | — | последнее состояние и пометка «офлайн», повтор при возврате фокуса |
| Cookie протухла | 401 | редирект `/login?next=`, после входа — назад на тот же экран |
| Обрыв посреди стрима | — | пузырь помечен «ответ оборвался», кнопка «повторить» (сделано) |

---

## 6. Приёмка продукта

**DoD «продукт собран»:**

1. При `NEXT_PUBLIC_DATA_SOURCE=remote` ни один экран не показывает число, посчитанное в браузере.
   Проверка: в `frontend/src` обращения к `programs.ts`, `prepData.ts`, `dashboardRules.ts`, `topicContent.ts`,
   `localSource.ts` — только внутри ветки фолбэка.
2. `GET /state` аккаунта после полного сценария содержит только UI-ключи и материалы (§A1).
3. `local`-режим по-прежнему собирается и работает без бэка.
4. CI зелёный: бэкенд (тесты, ruff, миграции, seed, секреты), типы без диффа, фронт (типы, тесты, сборка).
5. Сценарий жюри проходит автотестом на локальном стеке (§A8), негативный набор — вручную по §5.
6. `docs/runbook.md` пройден на реальном домене: релиз, отказ, восстановление, откат (§A9).
7. Открытые записи в `docs/sync-log.md` либо закрыты, либо явно перенесены за MVP с указанием, что это значит
   для демо.

---

## 7. Порядок и зависимости

| Этап | Дней | Владелец | Зависит от | Параллелится с |
|---|---|---|---|---|
| A2 доведение правки id | 0.5 | F | — | A3–A5 |
| A1 подготовка на сервере | 2 | F | A2 | A3–A6 |
| A3 мягкий отказ графа | 1 | B1+B3 | — | A1 |
| A4 500 с CORS | 0.5 | B3 | — | A1 |
| A5 честный статус LLM | 1 | B2+B3 | — | A1 |
| A6 `p_recall` | 1–2 | B1 | — | A1 |
| A7 фронт в CI | 0.5 | F+B3 | A2 | всё |
| A10 dev-эргономика | 0.5 | I | — | всё |
| A8 сквозной автотест | 2 | F+I | A1, A3–A6 | — |
| A9 выпуск на домен | 1 | B3+I | A7, A8 | — |

Критический путь: A2 → A1 → A8 → A9, около 5.5 дней; бэкенд-долги A3–A6 идут параллельно и должны быть закрыты
до A8, иначе негативные сценарии нечем проверять. Всего 10–11 человеко-дней, при двух исполнителях — около недели.

---

## 8. Риски

| Риск | Вероятность | Митигация |
|---|---|---|
| A1 ломает подготовку незаметно: это самый большой локальный модуль без прогона | высокая | каждый шаг — отдельный коммит с ручной проверкой экрана; прогон подготовки из A8 готовить параллельно |
| A6 трогает ядро модели знаний | средняя | решение и правка — за B1; фронт не «чинит» клампом, расхождение только записью в `sync-log` |
| Тексты не генерируются на демо (нет ключа или bulk-воркера) | высокая | предгенерация из `seed --demo`, локальные тексты как фолбэк, видимый статус |
| SSE буферизуется на реальном домене | средняя | `flush_interval -1`, `X-Accel-Buffering: no`, проверка на домене, а не только на стенде |
| Расхождение `schema.d.ts` и бэка после мержа | средняя | `types-check` в CI (есть), прогон после каждого мержа бэка |
| Демо-данные примут за настоящие | средняя | `is_demo` → плашка, `source_url` на `example.invalid`, дисклеймер в README — сделано, проверять на каждом новом экране |
