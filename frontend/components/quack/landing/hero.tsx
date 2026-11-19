"use client";

import { ChevronDown } from "lucide-react";
import type { RefObject } from "react";
import { useT } from "@/lib/i18n/provider";
import { HeroDuck } from "./hero-duck/hero-duck";

/** Первый экран лендинга: заголовок «Quack! – Learning Hub», под ним утка из глифов, которая говорит «Quack!» */
export function Hero({ scrollRef }: { scrollRef: RefObject<HTMLElement | null> }) {
  const t = useT().landing;

  return (
    <section id="top" className="relative flex h-full shrink-0 flex-col items-center justify-center overflow-x-clip px-4 pt-14 pb-12">
      {/* сразу под шапкой: утка остаётся по центру экрана, между ними — воздух с растениями.
          Archivo Expanded, как есть (без прописных): первая строка — про эффективность, вторая — про границу понимания,
          тише и светлее. Цвет первой между янтарём утки и цветом растений, лёгкое свечение */}
      <h1 ref={titleRef} className="absolute inset-x-4 top-[calc(3.5rem+clamp(0.5rem,4vh,2.75rem))] z-10 mx-auto max-w-[52rem] text-center font-[family-name:var(--font-archivo)] font-semibold leading-[1.2] [font-stretch:125%]">
        <span className="block text-[clamp(1.125rem,2.1vw,1.85rem)] text-[#daa05f] [text-shadow:0_0_12px_#daa05f59]">{t.hero.title}</span>
        <span className="mt-1.5 block text-[clamp(0.875rem,1.3vw,1.2rem)] font-medium text-[#bba995]">{t.hero.sub}</span>
      </h1>

      <div className="flex w-full justify-center">
        <HeroDuck scrollRef={scrollRef} label={t.hero.duck} />
      </div>

      <button
        type="button"
        onClick={(e) => {
          const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          e.currentTarget.closest("section")?.nextElementSibling?.scrollIntoView({ behavior: reduced ? "auto" : "smooth" });
        }}
        className="pointer-events-auto absolute bottom-3 left-1/2 flex min-h-12 -translate-x-1/2 flex-col items-center justify-center gap-0.5 rounded-pill px-5 text-[#bba995] outline-none transition-colors duration-(--dur-s) hover:text-[#f6eadc] focus-visible:ring-2 focus-visible:ring-[#ff8a2a]/60"
      >
        {/* подпись видна всегда: она же имя кнопки для скринридера */}
        <span className="font-mono text-[0.75rem] uppercase tracking-[0.18em]">{t.scroll}</span>
        <ChevronDown aria-hidden className="size-6 motion-safe:animate-bounce" />
      </button>
    </section>
  );
}
