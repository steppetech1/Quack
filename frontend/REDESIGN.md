# Редизайн «Son Daven» — общий бриф для исполнителей

Референс: https://sondaven.com/en — прелоадер с овечкой и полноэкранное меню с цветами.
Ассеты, видео и шрифты референса **не копируем**: только приём и настроение.

Решения владельца:
- переделываем весь UI приложения (цвета, шрифты, кнопки, подписи, переходы) + прелоадер + меню;
  раскладка (домены слева, центр, сессии справа) **не меняется**;
- утка — бесплатная CC0 3D-модель, рендер вживую через three.js со шейдером «штриховки»;
- в меню по краям растут камыши и кувшинки (генерируются кодом);
- шрифты — бесплатные аналоги с кириллицей; «полосатость» — эффектом CSS/SVG.

## 1. Как устроен референс (что повторяем)

- **Два цвета.** Тёмно-коричневый `#2c2824` и бежевый `#a89474`. Почти без других цветов.
- **Штриховка.** Объект (овечка, цветы) нарисован вертикальными бежевыми штрихами на тёмном фоне.
  Шаг столбцов ~3px, штрих виден там, где объект светлый; края рваные, есть лёгкий глитч:
  иногда полоса строк сдвигается вбок, отдельные столбцы «проваливаются».
- **Типографика.** Огромный «полосатый» логотип внизу экрана. Заголовки — антиква капсом с плотным трекингом.
  Подписи — мелкий капс (10–12px) с разрядкой `0.08em`.
- **Прелоадер.** Тёмный фон; сверху слева маленький знак + две строки подписи; по центру зацикленная
  анимация зверя в штриховке; снизу слева «RESORT / DESIGN-HOTEL», снизу справа «LOADING THE WEBSITE / PLEASE WAIT»
  и процент; в самом низу на всю ширину логотип. Уходит маской-шторкой: градиентная маска уезжает вверх.
- **Меню.** Полноэкранный оверлей того же тёмного цвета. Пункты по центру столбиком, антиква капсом.
  При открытии буквы/слова сначала разбросаны (разный размер, сдвиги, прозрачность), за ~0.8s собираются на место.
  По бокам снизу вверх «вырастают» цветы в штриховке. Внизу — мелкие контакты. Кнопка «≡ MENU» превращается в «✕ CLOSE».
  Открытие/закрытие — та же маска-шторка.
- **Движение.** Плавное «ease-out» `cubic-bezier(0.16, 1, 0.3, 1)`, длительности 0.4–1.2s.

## 2. Токены (владелец — задача T1)

Имена CSS-переменных сохраняем (`--cloth`, `--paper`, `--ribbon`…), меняем значения. Ориентиры:

| Токен | Было | Станет (ориентир) |
| :-- | :-- | :-- |
| `--cloth` | `#1c1915` | `#2c2824` |
| `--cloth-deep` | `#171410` | `#25211e` |
| `--cloth-raised` | `#24201a` | `#332e29` |
| `--cloth-hover` | `#2c2720` | `#3a342e` |
| `--seam` / `--seam-soft` | | бежевый 25% / 12% поверх фона |
| `--paper` | `#e6dccb` | `#e2d5bd` (длинный текст должен читаться) |
| `--paper-dim` | `#a99e8b` | `#a89474` |
| `--ribbon` (акцент, главная кнопка) | оранжевый | бежевый `#a89474`, hover светлее; `--on-ribbon` = `#2c2824` |
| `--duck` / цвет штриховки | | `#a89474` |
| статусы `--st-*` | | оставить различимыми, но приглушить к палитре |

- Скругления: карточки и панели — 0–2px; **кнопки — пилюля** (как «CONSULTATION»); поля ввода — линия снизу или тонкая рамка.
- Шрифты (через `next/font/google`, обязательно кириллица):
  - `--font-display` — антиква для заголовков и пунктов меню, капсом (кандидаты: Forum, Cormorant SC, Prata — выбрать и обосновать);
  - `--font-label` — для мелкого капса с разрядкой (широкий гротеск с кириллицей);
  - длинный текст (конспекты, чат) остаётся на Golos Text — капсом его не делаем;
  - JetBrains Mono для чисел оставить.
- Утилиты (Tailwind `@utility`): `caps` (мелкий капс + разрядка), `text-striped` (вертикальные полосы маской
  + рваный край через SVG-фильтр, для логотипа и крупных заголовков), `grain` (лёгкий шум поверх фона),
  `mask-wipe` (градиентная маска как у референса, управляется `--mask-y`).
- Движение: `--ease-out: cubic-bezier(0.16, 1, 0.3, 1)`, `--dur-s: 0.3s`, `--dur-m: 0.6s`, `--dur-l: 1.2s`.
- `prefers-reduced-motion`: всё большое движение отключается или сводится к fade.

## 3. Интерфейсы между задачами

### 3.1 Движок штриховки — `components/quack/scanline/scanline.ts` (задача T2)

```ts
import type * as THREE from "three";

export type ScanlineOptions = {
  color: string;        // цвет штриха, по умолчанию "#a89474"
  step: number;         // шаг столбцов в CSS-пикселях, по умолчанию 3
  lineWidth: number;    // доля столбца под штрих 0..1, по умолчанию 0.45
  threshold: number;    // ниже этой яркости источника штриха нет, по умолчанию 0.08
  rowSize: number;      // высота «ступеньки» по вертикали в CSS-пикселях, по умолчанию 2
  glitch: number;       // 0..1 сила глитча, по умолчанию 0.35
};

export type Scanline = {
  renderer: THREE.WebGLRenderer;                  // общий рендерер (canvas прозрачный)
  /** 3D-сцена → штриховка на canvas */
  renderScene(scene: THREE.Scene, camera: THREE.Camera, timeSec: number): void;
  /** 2D-источник (canvas / video / image) → штриховка на canvas */
  renderSource(source: HTMLCanvasElement | HTMLVideoElement | HTMLImageElement, timeSec: number): void;
  resize(widthCss: number, heightCss: number, dpr?: number): void;
  setOptions(patch: Partial<ScanlineOptions>): void;
  dispose(): void;
};

export function createScanline(canvas: HTMLCanvasElement, options?: Partial<ScanlineOptions>): Scanline;
```

Плюс React-обёртка `components/quack/scanline/scanline-canvas.tsx`:
`<ScanlineCanvas className options onFrame={(s: Scanline, timeSec: number) => void} />` — сама создаёт/уничтожает движок,
следит за размером (ResizeObserver), крутит `requestAnimationFrame`, ставит цикл на паузу, когда canvas не виден
или вкладка скрыта; при `prefers-reduced-motion` рисует один кадр.

### 3.2 Камыши и кувшинки — `components/quack/scanline/reeds.ts` (задача T3)

```ts
export type ReedsOptions = { side: "left" | "right"; seed: number };
/** Рисует растения в 2D-контекст. grow: 0..1 — насколько выросли; timeSec — для покачивания */
export function drawReeds(ctx: CanvasRenderingContext2D, width: number, height: number,
                          grow: number, timeSec: number, options: ReedsOptions): void;
```

Рисует **по яркости**: фон чёрный, растения — оттенки серого с объёмом (градиенты, светлая сторона/тень),
потому что движок превращает яркость в штрихи. Никакого бежевого цвета внутри — цвет добавит движок.

### 3.3 Тексты (задача H2) — новые ключи в `lib/i18n/en.ts` и `lib/i18n/ru.ts`

```ts
preloader: { tagline1, tagline2, kindTop, kindBottom, loading, wait, percent: (n: number) => string },
menu: { open, close, title, items: { graph, canvases, library, newSubject, profile, settings }, language, footerHint }
```

### 3.4 Мелочи (задача H3)
- `components/quack/menu-toggle.tsx` — `<MenuToggle open onClick />`: две линии → крестик, подпись MENU ↔ CLOSE капсом.
- `lib/motion.ts` — `usePrefersReducedMotion()`, `EASE_OUT`, `shouldShowPreloader()` / `markPreloaderShown()`
  (раз за сессию вкладки, `sessionStorage` в try/catch).

## 4. Кто чем владеет (волна 1)

| Задача | Исполнитель | Файлы (трогать только их) |
| :-- | :-- | :-- |
| T1 Токены и шрифты | Sonnet | `app/globals.css`, `app/layout.tsx`, `components/ui/button.tsx`, `DESIGN.md`, раздел «Дизайн» в `CLAUDE.md` и `AGENTS.md` (там сейчас оранжевый акцент и мягкие скругления — переписать под новый стиль) |
| T2 Движок штриховки | Sonnet | `package.json`, `package-lock.json`, `components/quack/scanline/scanline.ts`, `scanline-canvas.tsx`, `app/(app)/stubs/scanline/page.tsx` |
| T3 Камыши и кувшинки | Sonnet | `components/quack/scanline/reeds.ts`, `app/(app)/stubs/reeds/page.tsx` |
| H1 Поиск 3D-утки | Haiku | ничего не пишет, только отчёт |
| H2 Тексты | Haiku | `lib/i18n/en.ts`, `lib/i18n/ru.ts` |
| H3 Мелочи | Haiku | `components/quack/menu-toggle.tsx`, `lib/motion.ts` |

## 4.1 Итоги волны 1 (уже есть, пользоваться)

- Шрифты: `font-display` (Forum, антиква капсом), `font-label` (Montserrat), длинный текст — Golos (`font-sans`).
  `font-serif`/`font-heading` теперь тоже Forum, у Forum один вес — `font-bold` не использовать.
- Утилиты: `caps` (мелкий капс, размер через `[--caps-size:0.75rem]`), `display-caps`, `text-striped`
  (SVG-фильтр `#quack-ragged` уже в `app/layout.tsx`), `grain`, `mask-wipe` (+ `--mask-y`, `animate-wipe-in/out`).
- Скругления: `rounded-pill` — кнопки, теги, чипы, круглые иконки; `rounded-control` 3px — поля и строки; карточки 0–2px.
- Цвета: `ribbon` = бежевый акцент (главная кнопка, фокус), `paper`/`paper-dim` — текст, `seam`/`seam-soft` — рамки с прозрачностью.
- `components/ui/button.tsx` — пилюля с `caps`-подписью.
- `components/quack/scanline/scanline.ts` + `scanline-canvas.tsx` — движок (контракт 3.1, плюс `getSize()`).
- `components/quack/scanline/reeds.ts` — растения (контракт 3.2; фон заливает вызывающий, чёрным).
- `components/quack/menu-toggle.tsx`, `lib/motion.ts`, тексты `t.preloader.*`, `t.menu.*`.
- Включены Cache Components: страница под `app/(app)` должна быть тонким серверным компонентом,
  который рендерит клиентский компонент. Страница целиком с `"use client"` даёт пустой экран.
- Порт 3000 занят dev-сервером пользователя в режиме реального API. Второй `next dev` в той же папке не запускать.

## 4.2 Волна 2 — кто чем владеет

Решение владельца: **3D-утка собирается кодом** из примитивов three.js (без скачанных моделей), анимация тоже в коде.

| Задача | Исполнитель | Файлы |
| :-- | :-- | :-- |
| P Прелоадер с 3D-уткой | Sonnet | `components/quack/scanline/duck-3d.ts`, `components/quack/preloader.tsx`, `app/(app)/layout.tsx`, `app/(app)/stubs/duck/page.tsx`, `components/quack/scanline/duck-demo.tsx` |
| M Полноэкранное меню | Sonnet | `components/quack/site-menu.tsx`, `components/quack/app-shell.tsx`, `components/quack/domain-sidebar.tsx` (интеграция + рестайл этих двух) |
| R1 Рестайл: оболочка и мелкие | Sonnet | `sessions-panel`, `profile-menu`, `icon-button`, `composer`, `soon`, `today-list`, `topic-card`, `topic-list`, `topic-search`, `empty-domain`, `duck`, `num-text`, `scroll-region`, `handwriting`, `sketch`, `use-menu.ts` |
| R2 Рестайл: граф, тема, хаб, артефакты | Sonnet | `domain-view`, `domain-graph`, `plan-graph`, `hub-page`, `topic-page`, `page-parts`, `artifact-window`, `artifact-content`, `question-block`, `rich-text`, `interval-demo` |
| R3 Рестайл: сессия, настройки, канвасы, входы | Sonnet | `session-view`, `skill-chat`, `skills-tab`, `settings-page`, `canvases-page`, `canvas-view`, `board/*`, `live/*`, `stubs/*` кроме `stubs-index.tsx`, `app/(auth)/**` |
| H4 Мелочи | Haiku | `package.json`/`package-lock.json` (`@types/three` → devDependencies), `components/quack/stubs/stubs-index.tsx` (ссылки на демо) |

Все файлы R1–R3 — в `components/quack/`. Чужие файлы не трогать; если для задачи нужен чужой файл — написать об этом в отчёте.

## 5. Общие правила

- Прочитать `CLAUDE.md`/`AGENTS.md` в `frontend/`. Next.js 16 + React 19 + Tailwind 4.
- Комментарии в коде — коротко, по-русски, как в остальном проекте.
- Не коммитить, не пушить, не ставить зависимости (кроме `three` и `@types/three` в T2).
- Проверка своих файлов: `npx tsc --noEmit` и `npx eslint <свои файлы>`. Чужие незаконченные файлы могут временно ломать
  типы — это не повод их править.
