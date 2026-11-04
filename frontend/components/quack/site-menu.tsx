"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocale, useT } from "@/lib/i18n/provider";
import { LOCALES, LOCALE_NAME } from "@/lib/i18n/locale";
import { EASE_OUT, usePrefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { MenuToggle } from "./menu-toggle";
import { drawReeds } from "./scanline/reeds";
import type { Scanline, ScanlineOptions } from "./scanline/scanline";
import { ScanlineCanvas } from "./scanline/scanline-canvas";

/** Пункты меню: ключ словаря t.menu.items и адрес */
const ITEMS = [
  { key: "graph", href: "/" },
  { key: "canvases", href: "/?view=canvases" },
  { key: "library", href: "/library" },
  { key: "newSubject", href: "/domains/new" },
  { key: "settings", href: "/settings" },
] as const;

type ItemKey = (typeof ITEMS)[number]["key"];

const WIPE_MS = 850; // открытие шторкой
const WIPE_OUT_MS = 600; // закрытие
const SCATTER_MS = 900; // слова собираются из разброса
const GROW_DELAY = 0.25; // камыши начинают расти, когда шторка уже идёт
const GROW_TIME = 1.6;
const REED_OPTIONS: Partial<ScanlineOptions> = { lineWidth: 0.5, glitch: 0.3 };

/** Маленький детерминированный ГПСЧ (mulberry32): разброс букв одинаков при каждом открытии */
function mulberry32(seed: number) {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Какой пункт соответствует текущему адресу */
function isCurrent(key: ItemKey, pathname: string, search: string) {
  const canvases = search.includes("view=canvases");
  switch (key) {
    case "graph":
      return pathname === "/" && !canvases;
    case "canvases":
      return (pathname === "/" && canvases) || pathname.startsWith("/canvas");
    case "library":
      return pathname.startsWith("/library");
    case "newSubject":
      return pathname.startsWith("/domains/new");
    case "settings":
      return pathname.startsWith("/settings");
  }
}

/** Слово по буквам: каждая буква потом летит на место отдельно */
function Letters({ text }: { text: string }) {
  const words = text.split(" ");
  return words.map((word, w) => (
    <span key={w}>
      <span className="inline-block whitespace-nowrap">
        {[...word].map((ch, k) => (
          <span key={k} data-letter className="inline-block">
            {ch}
          </span>
        ))}
      </span>
      {w < words.length - 1 && " "}
    </span>
  ));
}

/** Камыши по бокам: рисуются яркостью в offscreen-canvas, штриховку делает движок */
function ReedsLayer() {
  const reduced = usePrefersReducedMotion();
  const off = useRef<HTMLCanvasElement | null>(null);

  const onFrame = useCallback(
    (s: Scanline, t: number) => {
      const { width, height } = s.getSize();
      if (width < 2 || height < 2) return;
      const c = (off.current ??= document.createElement("canvas"));
      // на больших экранах рисуем вдвое мельче: штрихи шагом 3px всё равно не различат
      const k = Math.min(1, 960 / width);
      const w = Math.round(width * k);
      const h = Math.round(height * k);
      if (c.width !== w || c.height !== h) {
        c.width = w;
        c.height = h;
      }
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, w, h);
      const p = reduced ? 1 : Math.min(1, Math.max(0, (t - GROW_DELAY) / GROW_TIME));
      const grow = 1 - (1 - p) ** 3; // ease-out
      drawReeds(ctx, w, h, grow, t, { side: "left", seed: 7 });
      drawReeds(ctx, w, h, grow, t, { side: "right", seed: 19 });
      s.renderSource(c, t);
    },
    [reduced],
  );

  // на телефоне растения не показываем: они закрыли бы пункты
  return <ScanlineCanvas className="pointer-events-none absolute inset-0 hidden size-full md:block" options={REED_OPTIONS} onFrame={onFrame} />;
}

type Props = {
  open: boolean;
  onClose: () => void;
  /** Где стоит кнопка меню: копия кнопки поверх оверлея встаёт на то же место */
  anchor?: { left: number; top: number } | null;
  /** Куда вернуть фокус после закрытия (кнопка, открывшая меню) */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
};

/** Полноэкранное меню: шторка сверху вниз, слова собираются из разброса, по краям растут камыши */
export function SiteMenu({ open, onClose, anchor, returnFocusRef }: Props) {
  // монтируемся при открытии, размонтируемся, когда закончится шторка закрытия
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);
  if (!mounted || typeof document === "undefined") return null;
  return createPortal(
    <Overlay open={open} onClose={onClose} onGone={() => setMounted(false)} anchor={anchor} returnFocusRef={returnFocusRef} />,
    document.body,
  );
}

function Overlay({ open, onClose, onGone, anchor, returnFocusRef }: Props & { onGone: () => void }) {
  const t = useT();
  const { locale, setLocale } = useLocale();
  const pathname = usePathname();
  const reduced = usePrefersReducedMotion();
  const wrap = useRef<HTMLDivElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const openRef = useRef(open);
  // onGone приходит новой функцией на каждый рендер: держим в ref, чтобы не перезапускать шторку
  const goneRef = useRef(onGone);
  useEffect(() => {
    goneRef.current = onGone;
  });
  const wasOpen = useRef(false);
  // копия кнопки сначала «MENU» и через кадр превращается в «CLOSE»
  const [toggled, setToggled] = useState(false);
  // мы на клиенте, меню открывают кликом — window тут есть
  const search = typeof window === "undefined" ? "" : window.location.search;

  useEffect(() => {
    const id = requestAnimationFrame(() => setToggled(open));
    return () => cancelAnimationFrame(id);
  }, [open]);

  // шторка (или простое появление при reduced motion)
  useLayoutEffect(() => {
    const el = sheet.current;
    if (!el) return;
    openRef.current = open;
    const done = () => {
      if (!openRef.current) goneRef.current();
    };
    let a: Animation;
    if (reduced) {
      a = el.animate([{ opacity: open ? 0 : 1 }, { opacity: open ? 1 : 0 }], { duration: 220, easing: "ease-out", fill: "both" });
    } else {
      a = el.animate(open ? [{ "--mask-y": "100%" }, { "--mask-y": "0%" }] : [{ "--mask-y": "0%" }, { "--mask-y": "100%" }], {
        duration: open ? WIPE_MS : WIPE_OUT_MS,
        easing: EASE_OUT,
        fill: "both",
      });
    }
    if (!open) a.onfinish = done;
  }, [open, reduced]);

  // слова разбросаны (смещение, масштаб, прозрачность) и собираются в столбец со ступенчатой задержкой
  useLayoutEffect(() => {
    const root = wrap.current;
    if (!open || reduced || !root) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    root.querySelectorAll<HTMLElement>("[data-item]").forEach((li, i) => {
      li.querySelectorAll<HTMLElement>("[data-letter]").forEach((el, j) => {
        const rnd = mulberry32(1000 + i * 97 + j * 13);
        const dx = (rnd() * 2 - 1) * 0.3 * vw;
        const dy = (rnd() * 2 - 1) * 0.2 * vh;
        const sc = 0.4 + rnd() * 1.2;
        el.animate(
          [
            { transform: `translate(${dx}px, ${dy}px) scale(${sc})`, opacity: 0 },
            { opacity: 0.35, offset: 0.35 },
            { transform: "translate(0, 0) scale(1)", opacity: 1 },
          ],
          { duration: SCATTER_MS, delay: 220 + i * 55 + j * 10, easing: EASE_OUT, fill: "both" },
        );
      });
    });
  }, [open, reduced]);

  // фокус внутрь при открытии и назад на кнопку при закрытии
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      const root = wrap.current;
      const target = root?.querySelector<HTMLElement>('a[aria-current="page"]') ?? root?.querySelector<HTMLElement>("a[href]");
      target?.focus({ preventScroll: true });
      return;
    }
    if (!wasOpen.current) return;
    const back = returnFocusRef?.current;
    if (back?.isConnected && !back.closest("[inert]")) {
      back.focus();
      return;
    }
    const visible = [...document.querySelectorAll<HTMLElement>("[data-menu-anchor] button")].find((b) => b.getClientRects().length > 0);
    visible?.focus();
  }, [open, returnFocusRef]);

  // Esc закрывает; перехватываем раньше остальных, чтобы не закрылось окно артефакта под меню
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  // фокус не выходит за пределы меню
  const trapTab = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Tab") return;
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")].filter((el) => el.getClientRects().length > 0);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const at = anchor ?? { left: 24, top: 16 };

  return (
    <div ref={wrap} role="dialog" aria-modal="true" aria-label={t.menu.title} onKeyDown={trapTab} className="fixed inset-0 z-[70]">
      {/* кнопка над шторкой: тот же MENU → CLOSE, как в шапке референса */}
      <div className="absolute z-20" style={{ left: at.left, top: at.top }}>
        <MenuToggle open={toggled} onClick={onClose} labelOpen={t.menu.open} labelClose={t.menu.close} />
      </div>

      <div
        ref={sheet}
        className={cn("absolute inset-0 bg-cloth grain", !reduced && "mask-wipe")}
        style={reduced ? { opacity: 0 } : ({ "--mask-y": "100%" } as React.CSSProperties)}
      >
        <ReedsLayer />

        <nav aria-label={t.menu.title} className="relative z-10 flex h-full flex-col items-center justify-center px-6 pt-20 pb-28">
          <ul className="flex flex-col items-center">
            {ITEMS.map(({ key, href }) => {
              const label = t.menu.items[key];
              const current = isCurrent(key, pathname, search);
              return (
                <li key={key} data-item>
                  <Link
                    href={href}
                    onClick={onClose}
                    aria-label={label}
                    aria-current={current ? "page" : undefined}
                    className="group display-caps relative inline-block px-3 py-1.5 text-center text-[clamp(1.75rem,4.6vw,2.5rem)] text-paper-dim transition-colors hover:text-paper focus-visible:text-paper aria-[current=page]:text-paper"
                  >
                    <span aria-hidden>
                      <Letters text={label} />
                    </span>
                    {/* подчёркивание: у текущего пункта всегда, у остальных при наведении */}
                    <span
                      aria-hidden
                      className="absolute inset-x-3 bottom-1 h-px origin-left scale-x-0 bg-ribbon transition-transform duration-[var(--dur-m)] ease-[var(--ease-out)] group-hover:scale-x-100 group-focus-visible:scale-x-100 group-aria-[current=page]:scale-x-100"
                    />
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-3 px-6 pb-5 md:flex-row md:items-end md:justify-between">
          <div role="group" aria-label={t.menu.language} className="flex items-center gap-2">
            <span aria-hidden className="caps mr-1 text-paper-dim">
              {t.menu.language}
            </span>
            {LOCALES.map((l) => (
              <button
                key={l}
                type="button"
                lang={l}
                aria-pressed={locale === l}
                aria-label={LOCALE_NAME[l]}
                onClick={() => l !== locale && setLocale(l)}
                className={cn(
                  "caps inline-flex min-h-10 min-w-12 items-center justify-center rounded-pill border px-3 transition-colors",
                  locale === l ? "border-ribbon bg-ribbon text-on-ribbon" : "border-seam text-paper-dim hover:border-rule hover:text-paper",
                )}
              >
                {l}
              </button>
            ))}
          </div>
          <p className="caps text-center text-paper-dim">{t.menu.footerHint}</p>
        </div>
      </div>
    </div>
  );
}
