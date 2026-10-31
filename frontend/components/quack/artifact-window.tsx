"use client";

import { X } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";

/** Описание окна: что показать в шапке и внутри. Ключ — значение `?open=` в адресе */
export type WindowSpec = {
  title: string;
  /** Моно-строка под заголовком */
  meta?: string;
  /** Кнопки в шапке, слева от крестика */
  actions?: React.ReactNode;
  body: React.ReactNode;
};

type Ctx = {
  open: (key: string, el: HTMLElement) => void;
  register: (key: string, el: HTMLElement | null) => void;
  activeKey: string | null;
};

const ArtifactContext = createContext<Ctx | null>(null);

const EASE = "cubic-bezier(.2,.8,.2,1)";
const DURATION = 280;
const CLOSE_DURATION = 240;
const noopSubscribe = () => () => {};

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Преобразование «окно → прямоугольник карточки»: масштаб и сдвиг от левого верхнего угла */
function flip(card: DOMRect, win: DOMRect) {
  const sx = card.width / win.width;
  const sy = card.height / win.height;
  return `translate(${card.left - win.left}px, ${card.top - win.top}px) scale(${sx}, ${sy})`;
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),textarea,select,[tabindex]:not([tabindex="-1"])';

/**
 * Хост окон артефактов: следит за `?open=` в адресе, держит окно поверх всего интерфейса
 * и анимирует его из карточки и обратно. Адрес меняется через history (без перезагрузки):
 * «Назад» закрывает окно, прямая ссылка открывает его сразу, без анимации.
 */
export function ArtifactHost({ windows, children }: { windows: Record<string, WindowSpec | undefined>; children: React.ReactNode }) {
  const pathname = usePathname();
  const openKey = useSearchParams().get("open");
  const isClient = useSyncExternalStore(noopSubscribe, () => true, () => false);
  const cards = useRef(new Map<string, HTMLElement>());
  const pushed = useRef(false);
  const [fromRect, setFromRect] = useState<DOMRect | null>(null);
  const [shown, setShown] = useState<{ key: string; from: DOMRect | null } | null>(null);

  // Окно появляется, когда в адресе есть известный ключ; прямая ссылка приходит без прямоугольника
  if (openKey && windows[openKey] && shown?.key !== openKey) setShown({ key: openKey, from: fromRect });
  const leaving = shown !== null && openKey !== shown.key;

  const open = useCallback(
    (key: string, el: HTMLElement) => {
      setFromRect(el.getBoundingClientRect());
      pushed.current = true;
      window.history.pushState(null, "", `${pathname}?open=${key}`);
    },
    [pathname],
  );
  const register = useCallback((key: string, el: HTMLElement | null) => {
    if (el) cards.current.set(key, el);
    else cards.current.delete(key);
  }, []);

  const requestClose = useCallback(() => {
    if (leaving) return;
    if (pushed.current) {
      pushed.current = false;
      window.history.back();
    } else {
      window.history.replaceState(null, "", pathname);
    }
  }, [leaving, pathname]);

  const onClosed = useCallback(() => {
    const key = shown?.key;
    pushed.current = false;
    setShown(null);
    setFromRect(null);
    // фокус возвращается на карточку, из которой окно выросло
    requestAnimationFrame(() => key && cards.current.get(key)?.focus({ preventScroll: true }));
  }, [shown]);

  const ctx = useMemo<Ctx>(() => ({ open, register, activeKey: shown?.key ?? null }), [open, register, shown?.key]);
  const spec = shown ? windows[shown.key] : undefined;

  return (
    <ArtifactContext.Provider value={ctx}>
      {children}
      {isClient &&
        shown &&
        spec &&
        createPortal(
          <ArtifactWindow
            key={shown.key}
            title={spec.title}
            meta={spec.meta}
            actions={spec.actions}
            from={shown.from}
            leaving={leaving}
            targetRect={() => cards.current.get(shown.key)?.getBoundingClientRect() ?? null}
            onClose={requestClose}
            onClosed={onClosed}
          >
            {spec.body}
          </ArtifactWindow>,
          document.body,
        )}
    </ArtifactContext.Provider>
  );
}

/** Карточка-превью: настоящая кнопка, по клику вырастает в окно. Превью внутри помечай aria-hidden */
export function ExpandableCard({
  artifact,
  className,
  buttonClassName,
  behind,
  children,
}: {
  artifact: string;
  className?: string;
  buttonClassName?: string;
  /** Слои «под» карточкой (стопка колоды) */
  behind?: React.ReactNode;
  children: React.ReactNode;
}) {
  const ctx = useContext(ArtifactContext);
  if (!ctx) throw new Error("ExpandableCard outside ArtifactHost");
  const { open, register, activeKey } = ctx;
  return (
    <div className={cn("relative", activeKey === artifact && "opacity-0", className)}>
      {behind}
      <button
        type="button"
        ref={(el) => register(artifact, el)}
        aria-haspopup="dialog"
        onClick={(e) => open(artifact, e.currentTarget)}
        className={cn(
          "relative block min-h-10 w-full rounded-[16px] border border-seam bg-cloth-raised text-left transition-[transform,border-color] duration-150 hover:-translate-y-px hover:border-rule focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon motion-reduce:transition-none motion-reduce:hover:translate-y-0",
          buttonClassName,
        )}
      >
        {children}
      </button>
    </div>
  );
}

/** Большое окно поверх всего приложения */
export function ArtifactWindow({
  title,
  meta,
  actions,
  children,
  from,
  leaving,
  targetRect,
  onClose,
  onClosed,
}: Omit<WindowSpec, "body"> & {
  children: React.ReactNode;
  /** Откуда вырастает окно; null — открыть сразу */
  from: DOMRect | null;
  leaving: boolean;
  targetRect: () => DOMRect | null;
  onClose: () => void;
  onClosed: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const latest = useRef({ onClosed, targetRect });
  useEffect(() => {
    latest.current = { onClosed, targetRect };
  });

  // Появление: окно едет из карточки, содержимое проявляется чуть позже
  useLayoutEffect(() => {
    const p = panel.current;
    const c = content.current;
    const b = backdrop.current;
    if (!p || !c || !b) return;
    p.focus({ preventScroll: true });
    const anims: Animation[] = [];
    if (reducedMotion()) {
      if (from) {
        anims.push(p.animate({ opacity: [0, 1] }, { duration: 120, fill: "backwards" }), b.animate({ opacity: [0, 1] }, { duration: 120, fill: "backwards" }));
      }
    } else if (from) {
      const win = p.getBoundingClientRect();
      p.style.transformOrigin = "0 0";
      anims.push(
        p.animate([{ transform: flip(from, win) }, { transform: "none" }], { duration: DURATION, easing: EASE, fill: "backwards" }),
        b.animate({ opacity: [0, 1] }, { duration: DURATION, easing: EASE, fill: "backwards" }),
        c.animate({ opacity: [0, 1] }, { duration: 160, delay: 120, easing: "ease-out", fill: "backwards" }),
      );
    }
    return () => anims.forEach((a) => a.cancel());
  }, [from]);

  // Закрытие: обратно в карточку, фон гаснет
  useEffect(() => {
    if (!leaving) return;
    const p = panel.current;
    const c = content.current;
    const b = backdrop.current;
    if (!p || !c || !b) return;
    const to = latest.current.targetRect();
    const anims: Animation[] = [];
    if (reducedMotion() || !to) {
      anims.push(p.animate({ opacity: [1, 0] }, { duration: 120, fill: "forwards" }), b.animate({ opacity: [1, 0] }, { duration: 120, fill: "forwards" }));
    } else {
      p.style.transformOrigin = "0 0";
      anims.push(
        p.animate([{ transform: "none" }, { transform: flip(to, p.getBoundingClientRect()) }], { duration: CLOSE_DURATION, easing: EASE, fill: "forwards" }),
        b.animate({ opacity: [1, 0] }, { duration: CLOSE_DURATION, easing: EASE, fill: "forwards" }),
        c.animate({ opacity: [1, 0] }, { duration: 90, easing: "ease-in", fill: "forwards" }),
      );
    }
    let live = true;
    Promise.all(anims.map((a) => a.finished)).then(
      () => live && latest.current.onClosed(),
      () => {},
    );
    return () => {
      live = false;
      anims.forEach((a) => a.cancel());
    };
  }, [leaving]);

  // Всё, что вне окна, не доступно ни мышью, ни клавиатурой, ни скринридеру; страница под окном не прокручивается
  useEffect(() => {
    const root = panel.current?.closest("[data-artifact-root]");
    const changed: Element[] = [];
    for (const el of Array.from(document.body.children)) {
      if (el === root || el.tagName === "SCRIPT" || el.tagName === "NEXTJS-PORTAL" || el.hasAttribute("inert")) continue;
      el.setAttribute("inert", "");
      changed.push(el);
    }
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      changed.forEach((el) => el.removeAttribute("inert"));
      document.body.style.overflow = overflow;
    };
  }, []);

  // Esc и Tab ловим на документе: фокус мог потеряться, когда кнопка внутри окна исчезла
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const p = panel.current;
      if (!p) return;
      if (e.key === "Escape") {
        // открытый поповер строки почерка закрывается сам, окно остаётся
        if (p.querySelector('[data-hand][role="group"]')) return;
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(p.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.getClientRects().length > 0);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const outside = !active || !p.contains(active) || active === p;
      if (e.shiftKey && (outside || active === first)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (outside || active === last)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div data-artifact-root className="fixed inset-0 z-[70]">
      <div ref={backdrop} aria-hidden onClick={onClose} className="absolute inset-0 bg-cloth-deep/70" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="absolute inset-x-3 top-4 bottom-3 overflow-hidden rounded-[18px] border border-seam bg-cloth shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)] outline-none md:inset-x-12 md:top-8 md:bottom-6"
      >
        <div ref={content} className="flex h-full flex-col">
          <header className="flex flex-wrap items-start gap-x-3 gap-y-2 border-b border-seam-soft px-5 py-3 md:px-7">
            <div className="order-1 min-w-0 flex-1 pt-1">
              <h2 id={titleId} className="font-serif text-[22px] leading-tight font-bold text-paper">
                {title}
              </h2>
              {meta && <p className="num mt-0.5 text-small text-paper-dim">{meta}</p>}
            </div>
            <div className="order-3 flex flex-wrap items-center gap-1 max-sm:w-full sm:order-2">{actions}</div>
            <IconButton label={t.shell.close} onClick={onClose} className="order-2 -mr-2 sm:order-3 sm:-mr-3">
              <X />
            </IconButton>
          </header>
          <div role="region" tabIndex={0} aria-label={title} className="min-h-0 flex-1 overflow-y-auto px-5 py-6 outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ribbon md:px-8">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
