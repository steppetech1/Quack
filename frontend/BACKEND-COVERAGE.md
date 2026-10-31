# Фронт и бэк: что подключено

Состояние на 2026-11-01.

## Как запустить

Фронт работает в одном из двух режимов. Режим выбирается при сборке переменной окружения в `frontend/.env.local`:

- `NEXT_PUBLIC_QUACK_API=1` — данные идут с бэка;
- без неё — моки из `lib/mock/`, как раньше. Это удобно для работы над дизайном.

Шаблон лежит в `.env.example`. После изменения `.env.local` dev-сервер надо перезапустить.

Бэк берётся из ветки **`phase-3`**. На `main` у бэка подключены только сессии, а в `phase-3` уже все роутеры и `openapi.json`.

1. Инфраструктура (Postgres, Neo4j, Redis, compute): контейнеры `quack-dev-*` в Docker.
2. API и воркер из ветки `phase-3`, в папке `backend`:

   ```bash
   uv run python -m quack.cli ops migrate
   ```

   ```bash
   uv run python -m quack.cli api
   ```

   ```bash
   uv run python -m quack.cli worker
   ```

   Воркер обязателен: он строит граф, шпаргалки и итоги сессий.
3. Фронт:

   ```bash
   npm run dev
   ```

Браузер обращается только к фронту. Next.js проксирует `/api/*` на `QUACK_API_URL` (по умолчанию `http://localhost:8000`, см. `next.config.ts`), поэтому CORS на бэке не нужен.

Без ключа к LLM (`LLM_API_KEY`) бэк работает в упрощённом режиме. Предложение тем даёт по одной теме на каждый пункт фокуса. Учитель отвечает заглушкой и выдаёт готовые задачи из банка. Новые задачи для практики не генерируются, и экран практики показывает об этом сообщение.

## Как устроено во фронте

- `lib/api/client.ts` — запросы, токен (`localStorage`, ключ `quack.token`), ошибки, SSE через `fetch`.
- `lib/api/schema.d.ts` — типы, сгенерированные из `lib/api/openapi.json`. Снимок `openapi.json` скопирован из `backend/openapi.json` ветки `phase-3`. После изменений API скопируй его заново и выполни `npm run api:types`.
- `lib/api/adapters.ts` — переводит ответы бэка в типы, на которых построен интерфейс (`lib/mock/types.ts`).
- `lib/api/data.tsx` — `DataProvider`: проверка входа, профиль, список предметов, графы, активная сессия. На моках отдаёт моки.
- `components/quack/live/*` — экраны на данных бэка. Маршруты выбирают живой экран или моковый по `API_MODE`.

## Что подключено

| Экран | Эндпоинты |
|---|---|
| Вход, регистрация, выход | `POST /v1/auth/login`, `POST /v1/auth/register`, `GET /v1/auth/me` |
| Левая панель, вкладка «Предметы» в настройках | `GET /v1/domains` |
| Граф предмета | `GET /v1/domains/{id}/graph` |
| Новый предмет | `POST /v1/domains`, `POST …/structure/propose` |
| Темы предмета: пропустить, добавить, подтвердить, архив | `POST …/structure/confirm`, `PATCH …/structure`, `PATCH …/topics/{id}/status`, `POST …/archive` |
| Диагностика | `POST …/diagnostic/start`, `…/answer`, `…/finish` |
| Страница темы: навыки, заблуждения, заметки, шпаргалка | `GET /v1/topics/{id}`, `GET /v1/artifacts?topic_id=`, `GET /v1/topics/{id}/cheatsheet`, `POST …/cheatsheet/refresh` |
| Сессия: история, ответ учителя потоком, задачи, присутствие, конец | `POST /v1/sessions`, `GET /v1/sessions/{id}`, `POST …/turns` (SSE), `POST …/heartbeat`, `POST …/finish`, `GET /v1/sessions/active` |
| Итог сессии | данные из `finish` |
| Практика | `POST /v1/sessions` (practice), `POST …/tasks/next`, `…/hint`, `…/submit` |
| Заметки и шпаргалки, поиск, выгрузка | `GET /v1/artifacts`, `GET /v1/artifacts/search`, `GET /v1/domains/{id}/export` |
| Заметка | `POST /v1/artifacts/notes`, `PATCH …/{id}`, `DELETE …/{id}` |
| Удаление аккаунта | `DELETE /v1/learner` |

Не подключено: `PATCH /v1/learner/preferences` (настройки пока хранятся в `localStorage`), `GET /v1/learner/stream` и `GET /v1/sessions/{id}/events` (живые обновления без перезагрузки).

## Чего нет на бэке (во фронте остаётся на моках или скрыто)

- **Главы (хабы) и дедлайны.** Все темы предмета показаны в одной главе «Все темы». Страница главы работает только на моках.
- **История сессий.** Есть только активная сессия, поэтому в правой панели не больше одной записи.
- «Сегодня», канвасы, карточки для повторения, материалы (загрузка лекций), готовность к дедлайну.
- Скиллы пользователя (свои промпты) хранятся в `localStorage`. На бэке «skill» значит другое: навык внутри темы.

## Состояния тем

У бэка пять состояний темы, в дизайне четыре:

- `solid` → «решаешь сам»;
- `in_progress` и `rusty` → «пора повторить»;
- `available` и `locked` → «ещё не изучал».

«Путаешь» с бэка пока не выставляется: заблуждения видны на странице темы. Пропущенные и архивные темы на граф не попадают.
