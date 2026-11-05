"use client";

import Link from "next/link";
import { useEffect, useRef, type RefObject } from "react";
import { useT } from "@/lib/i18n/provider";
import { onAnchor } from "./scenes/story";

const LINK =
  "inline-flex h-10 items-center rounded-pill px-3 font-mono text-[0.9375rem] text-[#bba995] outline-none transition-colors duration-(--dur-s) hover:text-[#daa05f] focus-visible:ring-2 focus-visible:ring-[#daa05f]/60";

/**
 * Верхняя панель лендинга по мотивам modal.com: логотип и якорные ссылки слева, вход и главная кнопка справа. Цвета — из заголовка первого экрана: золотистый #daa05f (кнопка, «!» логотипа, наведение, кольцо фокуса) и приглушённый #bba995 (ссылки).
 * Лежит поверх страницы (sticky с отрицательным отступом, чтобы не занимать место), а после прокрутки получает тёмную подложку и линию.
 */
export function LandingNav({ scrollRef }: { scrollRef: RefObject<HTMLElement | null> }) {
  const t = useT().landing;
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const root = scrollRef.current;
    const nav = navRef.current;
    if (!root || !nav) return;
    const update = () => {
      nav.dataset.stuck = String(root.scrollTop > 8);
    };
    update();
    root.addEventListener("scroll", update, { passive: true });
    return () => root.removeEventListener("scroll", update);
  }, [scrollRef]);

  return (
    <nav
      ref={navRef}
      aria-label={t.nav.label}
      className="sticky top-0 z-30 -mb-14 flex h-14 items-center justify-between gap-3 border-b border-transparent px-4 transition-colors duration-(--dur-s) data-[stuck=true]:border-[#f1e4d6]/10 data-[stuck=true]:bg-[#080504]/90 sm:px-8"
    >
      <div className="flex items-center gap-2 sm:gap-6">
        <a
          href="#top"
          onClick={onAnchor("top")}
          className="inline-flex h-10 items-center rounded-pill px-1 font-mono text-[1.375rem] font-extrabold tracking-[-0.06em] text-[#f6eadc] outline-none focus-visible:ring-2 focus-visible:ring-[#daa05f]/60"
        >
          Qvack<span className="text-[#daa05f]">!</span>
        </a>
        <div className="hidden items-center md:flex">
          <a href="#efficiency" onClick={onAnchor("efficiency")} className={LINK}>
            {t.nav.why}
          </a>
          <a href="#how" onClick={onAnchor("how")} className={LINK}>
            {t.nav.pluses}
          </a>
        </div>
      </div>
      <div className="flex items-center gap-1 sm:gap-2">
        <Link href="/login" className={LINK}>
          {t.nav.login}
        </Link>
        <Link
          href="/signup"
          className="inline-flex h-10 items-center rounded-pill bg-[#daa05f] px-5 font-mono text-[0.9375rem] font-bold text-[#1a0a02] shadow-[0_0_16px_#daa05f40] outline-none transition-[background-color,transform] duration-(--dur-s) ease-(--ease-out) hover:bg-[#e6b676] focus-visible:ring-4 focus-visible:ring-[#daa05f]/50 active:translate-y-px"
        >
          {t.nav.cta}
        </Link>
      </div>
    </nav>
  );
}
