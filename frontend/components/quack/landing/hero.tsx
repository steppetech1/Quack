"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useRef, type RefObject } from "react";
import { useT } from "@/lib/i18n/provider";
import { HeroDuck } from "./hero-duck/hero-duck";
import { smoothstep } from "./scenes/scene-utils";
import { goToScene, storyStore } from "./scenes/story";

/** Первый экран лендинга: заголовок «Quack! – Learning Hub», под ним утка из глифов, которая говорит «Quack!» */
export function Hero({ scrollRef, pinned = false }: { scrollRef: RefObject<HTMLElement | null>; pinned?: boolean }) {
  const t = useT().landing;
  const titleRef = useRef<HTMLHeadingElement>(null);
  const chevronRef = useRef<HTMLButtonElement>(null);

  // заголовок и стрелка гаснут вместе с рассыпающейся сценой
  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    const update = () => {
      const o = String(root.dataset.pinned === "true" ? storyStore.forms[0] : 1 - smoothstep(0.03, 0.4, root.scrollTop / Math.max(1, root.clientHeight)));
      if (titleRef.current) titleRef.current.style.opacity = o;
      if (chevronRef.current) chevronRef.current.style.opacity = o;
    };
    update();
    root.addEventListener("scroll", update, { passive: true });
    root.addEventListener("story-form", update);
    return () => {
      root.removeEventListener("scroll", update);
      root.removeEventListener("story-form", update);
    };
  }, [scrollRef]);

  return (
    <section id="top" className={`flex flex-col items-center justify-center overflow-clip px-4 pt-14 pb-12 ${pinned ? "pointer-events-none absolute inset-0" : "relative h-full shrink-0"}`}>
      {/* сразу под шапкой: утка остаётся по центру экрана, между ними — воздух с растениями.
          Archivo Expanded SemiBold прописными, цвет между янтарём утки и цветом растений, лёгкое свечение.
          На телефоне — в две строки */}
      <h1 ref={titleRef} className="absolute inset-x-4 top-[calc(3.5rem+clamp(0.5rem,4vh,2.75rem))] z-10 text-center font-[family-name:var(--font-archivo)] text-[clamp(1rem,2vw,1.85rem)] font-semibold uppercase leading-[1.1] tracking-[0.02em] text-[#daa05f] [font-stretch:125%] [text-shadow:0_0_12px_#daa05f59]">
        {t.hero.brand} –<br className="sm:hidden" /> {t.hero.title}
      </h1>

      <div className="pointer-events-auto flex w-full justify-center">
        <HeroDuck scrollRef={scrollRef} label={t.hero.duck} />
      </div>

      <button
        ref={chevronRef}
        type="button"
        onClick={(e) => {
          const root = scrollRef.current;
          if (root?.dataset.pinned === "true") {
            goToScene(root, "efficiency", e);
            return;
          }
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
