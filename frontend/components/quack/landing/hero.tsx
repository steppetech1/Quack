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
          Archivo Expanded SemiBold прописными, цвет между янтарём утки и цветом растений, лёгкое свечение.
          На телефоне — в две строки */}
      <h1 className="absolute inset-x-4 top-[calc(3.5rem+clamp(0.5rem,4vh,2.75rem))] z-10 text-center font-[family-name:var(--font-archivo)] text-[clamp(1rem,2vw,1.85rem)] font-semibold uppercase leading-[1.1] tracking-[0.02em] text-[#daa05f] [font-stretch:125%] [text-shadow:0_0_12px_#daa05f59]">
        {t.hero.brand} –<br className="sm:hidden" /> {t.hero.title}
      </h1>

      <div className="flex w-full justify-center">
        <HeroDuck scrollRef={scrollRef} label={t.hero.duck} />
      </div>

      <button
        type="button"
        aria-label={t.scroll}
        onClick={(e) => {
          const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          e.currentTarget.closest("section")?.nextElementSibling?.scrollIntoView({ behavior: reduced ? "auto" : "smooth" });
        }}
        className="absolute bottom-3 left-1/2 flex size-12 -translate-x-1/2 items-center justify-center rounded-pill text-[#bba995] outline-none transition-colors duration-(--dur-s) hover:text-[#f6eadc] focus-visible:ring-2 focus-visible:ring-[#ff8a2a]/60"
      >
        <ChevronDown aria-hidden className="size-6 motion-safe:animate-bounce" />
      </button>
    </section>
  );
}
