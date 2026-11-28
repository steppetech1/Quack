"use client";

import { ArrowDown, ArrowRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { MagneticLink, Reveal } from "./motion";
import { useLandingStage, type DuckScreenAnchor } from "./stage/landing-context";

type QuackCallout = { id: number; x: number; y: number };

export function Hero() {
  const t = useT().landing;
  const { scrollRef, triggerQuack, subscribeAnchor, subscribeQuack } = useLandingStage();

  const hitRef = useRef<HTMLButtonElement>(null);
  const [callouts, setCallouts] = useState<QuackCallout[]>([]);
  const calloutSeq = useRef(0);

  // Sync interactive duck hitbox directly with the WebGL duck projection
  useEffect(() => {
    return subscribeAnchor((a: DuckScreenAnchor) => {
      const btn = hitRef.current;
      if (!btn) return;
      btn.style.left = `${a.x.toFixed(1)}px`;
      btn.style.top = `${a.y.toFixed(1)}px`;
      btn.style.width = `${Math.max(150, a.size * 0.85).toFixed(0)}px`;
      btn.style.height = `${Math.max(130, a.size * 0.72).toFixed(0)}px`;
      btn.style.pointerEvents = a.visible ? "auto" : "none";
      btn.tabIndex = a.visible ? 0 : -1;
    });
  }, [subscribeAnchor]);

  useEffect(() => {
    return subscribeQuack((a) => {
      const id = ++calloutSeq.current;
      const x = (a.beakX || (typeof window !== "undefined" ? window.innerWidth * 0.54 : 400)) + 12;
      const y = Math.max(76, (a.y || (typeof window !== "undefined" ? window.innerHeight * 0.48 : 380)) - a.size * 0.34);
      setCallouts((prev) => [...prev.slice(-3), { id, x, y }]);
      window.setTimeout(() => {
        setCallouts((prev) => prev.filter((item) => item.id !== id));
      }, 1350);
    });
  }, [subscribeQuack]);

  const scrollTo = useCallback(
    (id: string) => () => {
      const root = scrollRef.current;
      const target = document.getElementById(id);
      if (!root || !target) return;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const top = root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top;
      root.scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
    },
    [scrollRef],
  );

  const handleDuckClick = () => {
    triggerQuack();
  };

  return (
    <section
      id="top"
      className="relative flex min-h-dvh shrink-0 flex-col justify-between overflow-x-clip px-4 pt-20 pb-5 sm:px-8 sm:pb-7"
    >
      {/* Subtle concentric orbital reticle framed behind the 3D duck */}
      <div
        aria-hidden
        className="pointer-events-none absolute top-[32%] left-1/2 z-0 size-[min(64vw,16.5rem)] -translate-x-1/2 -translate-y-1/2 lg:top-[39%] lg:size-[min(52vw,30rem)]"
      >
        <svg viewBox="0 0 400 400" className="size-full overflow-visible">
          <circle
            cx="200"
            cy="200"
            r="186"
            fill="none"
            stroke="#f1e4d6"
            strokeOpacity="0.07"
            strokeWidth="1"
            strokeDasharray="2 7"
          />
          <circle
            cx="200"
            cy="200"
            r="142"
            fill="none"
            stroke="#edb966"
            strokeOpacity="0.13"
            strokeWidth="1"
          />
          <g stroke="#edb966" strokeOpacity="0.2" strokeWidth="1.2">
            <path d="M 200 8 V 20 M 194 14 H 206" />
            <path d="M 200 380 V 392 M 194 386 H 206" />
            <path d="M 8 200 H 20 M 14 194 V 206" />
            <path d="M 380 200 H 392 M 386 194 V 206" />
          </g>
          <g className="landing-orbit-spin" opacity="0.45">
            <circle cx="200" cy="14" r="3" fill="#edb966" />
            <circle cx="386" cy="200" r="2.5" fill="#8fc79a" />
            <circle cx="200" cy="386" r="2.5" fill="#86a9c9" />
            <circle cx="14" cy="200" r="2.5" fill="#ef8c7a" />
          </g>
        </svg>
      </div>

      {/* Interactive 3D duck hit target (tracked cleanly to the WebGL duck projection) */}
      <button
        ref={hitRef}
        type="button"
        aria-label={t.hero.duck}
        onClick={handleDuckClick}
        style={{ left: "50%", top: "44%", width: "260px", height: "220px" }}
        className="fixed z-10 -translate-x-1/2 -translate-y-1/2 cursor-pointer rounded-full bg-transparent outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/60"
      />

      {/* Ephemeral beak Quack! popups */}
      {callouts.map((c) => (
        <span
          key={c.id}
          aria-hidden
          style={{ left: `${c.x}px`, top: `${c.y}px` }}
          className="landing-quack-pop pointer-events-none fixed z-20 rounded-pill border border-[#edb966]/45 bg-[#0e0b09]/92 px-3 py-1 font-mono text-[0.9375rem] font-bold tracking-[-0.02em] text-[#edb966] shadow-[0_0_20px_rgba(237,185,102,0.35)] backdrop-blur-sm"
        >
          {t.hero.quackWord}
        </span>
      ))}

      {/* Clean bottom editorial floor — zero boxes, zero pills over the 3D duck */}
      <div className="relative z-10 mx-auto mt-auto w-full max-w-6xl pt-4">
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-x-8 -top-14 -bottom-8 -z-10 bg-[linear-gradient(to_top,rgba(8,5,4,0.92)_38%,rgba(8,5,4,0.4)_78%,transparent_100%)]"
        />

        <div className="grid items-end gap-5 lg:grid-cols-12 lg:gap-8">
          <div className="lg:col-span-8">
            <Reveal mode="wipe" delayMs={80}>
              <h1 className="font-display text-[clamp(1.85rem,6vw,2.6rem)] font-bold leading-[1.06] tracking-[-0.035em] lg:text-[clamp(2.35rem,3.9vw,3.5rem)]">
                <span className="block text-[#f3eadc]">{t.hero.titleLead}</span>
                <span className="block text-[#edb966]">{t.hero.titleAccent}</span>
              </h1>
            </Reveal>
          </div>

          <div className="lg:col-span-4">
            <Reveal mode="rise" delayMs={150}>
              <p className="max-w-[30ch] text-[0.9375rem] leading-[1.5] text-[#a39787]">
                {t.hero.sub}
              </p>

              <div className="mt-5 flex items-center gap-5">
                <MagneticLink
                  href="/signup"
                  className="group inline-flex h-10.5 items-center gap-2 rounded-pill bg-[#edb966] px-6 font-mono text-[0.8125rem] font-bold text-[#141008] outline-none hover:bg-[#f5cf8e] focus-visible:ring-4 focus-visible:ring-[#edb966]/45 active:translate-y-px"
                >
                  <span>{t.hero.ctaPrimary}</span>
                  <ArrowRight
                    aria-hidden
                    className="size-4 transition-transform duration-300 ease-(--ease-out) group-hover:translate-x-0.5"
                  />
                </MagneticLink>

                <button
                  type="button"
                  onClick={scrollTo("flow")}
                  className="inline-flex min-h-10 cursor-pointer items-center gap-1.5 font-mono text-[0.8125rem] text-[#a39787] outline-none transition-colors duration-(--dur-s) hover:text-[#f3eadc] focus-visible:ring-2 focus-visible:ring-[#edb966]/70"
                >
                  <span>{t.hero.ctaSecondary}</span>
                  <ArrowDown aria-hidden className="size-3.5 opacity-80" />
                </button>
              </div>
            </Reveal>
          </div>
        </div>

        {/* Minimal bottom hairline floor rail */}
        <div className="mt-6 flex items-center justify-between border-t border-[#f1e4d6]/10 pt-3 font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[#736e63]">
          <span className="hidden tabular-nums sm:block">00 / 03</span>

          <button
            type="button"
            onClick={scrollTo("flow")}
            className="mx-auto inline-flex min-h-8 cursor-pointer items-center gap-2 text-[#a39787] outline-none transition-colors hover:text-[#edb966] focus-visible:ring-2 focus-visible:ring-[#edb966]/70 sm:mx-0"
          >
            <span>{t.hero.scroll}</span>
            <span aria-hidden className="relative h-4 w-px overflow-hidden bg-[#f1e4d6]/20">
              <span className="landing-scroll-runner absolute inset-x-0 top-0 h-2 bg-[#edb966]" />
            </span>
          </button>

          <button
            type="button"
            onClick={handleDuckClick}
            className="inline-flex min-h-8 cursor-pointer items-center text-[#a39787] outline-none transition-colors hover:text-[#edb966] focus-visible:ring-2 focus-visible:ring-[#edb966]/70"
          >
            [ {t.hero.duckHint} ]
          </button>
        </div>
      </div>
    </section>
  );
}
