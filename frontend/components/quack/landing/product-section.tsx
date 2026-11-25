"use client";

import Image from "next/image";
import { useLocale, useT } from "@/lib/i18n/provider";
import { Reveal } from "./motion";

/**
 * The real product, right after the pitch: one honest screenshot of the domain view,
 * so a visitor sees what the "map" actually is before any illustration.
 */
export function ProductSection() {
  const p = useT().landing.product;
  const { locale } = useLocale();

  return (
    <section id="product" className="relative scroll-mt-20 text-[#f3eadc]">
      <div className="mx-auto max-w-6xl px-5 py-20 sm:px-8 lg:py-28">
        <div className="grid gap-6 lg:grid-cols-[1fr_minmax(0,24rem)] lg:items-end lg:gap-16">
          <div>
            <Reveal mode="rise">
              <span className="font-mono text-[0.75rem] uppercase tracking-[0.08em] text-[#edb966]">
                {p.eyebrow}
              </span>
            </Reveal>
            <Reveal mode="wipe" delayMs={80} className="mt-2">
              <h2 className="font-display text-[clamp(1.65rem,3.2vw,2.6rem)] font-bold leading-[1.08] tracking-[-0.025em]">
                {p.title}
              </h2>
            </Reveal>
          </div>
          <Reveal mode="rise" delayMs={120}>
            <p className="text-[0.95rem] leading-relaxed text-[#9c9283]">{p.sub}</p>
          </Reveal>
        </div>

        <Reveal mode="rise" delayMs={100} className="mt-10 max-w-4xl">
          <div className="overflow-hidden rounded-[6px] border border-[#f1e4d6]/12 bg-[#141311] shadow-[0_30px_80px_-30px_rgba(0,0,0,0.8)]">
            <div className="flex items-center gap-1.5 border-b border-[#f1e4d6]/10 bg-[#0e0b09] px-4 py-2.5" aria-hidden>
              <span className="size-2 rounded-full bg-[#f1e4d6]/15" />
              <span className="size-2 rounded-full bg-[#f1e4d6]/15" />
              <span className="size-2 rounded-full bg-[#f1e4d6]/15" />
            </div>
            <Image
              src={`/landing/product-graph-${locale}.png`}
              alt={p.alt}
              width={2360}
              height={1090}
              sizes="(min-width: 960px) 896px, 100vw"
              className="block h-auto w-full"
            />
          </div>
        </Reveal>

        <ol className="mt-6 grid max-w-4xl gap-px overflow-hidden rounded-[6px] border border-[#f1e4d6]/12 bg-[#f1e4d6]/12 sm:grid-cols-3">
          {p.callouts.map((c, i) => (
            <li key={c.label} className="bg-[#0a0807]/92 p-5 sm:p-6">
              <Reveal mode="rise" delayMs={i * 70}>
                <span className="font-mono text-[0.75rem] tabular-nums text-[#edb966]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <h3 className="mt-2 font-display text-[1.05rem] font-semibold tracking-[-0.015em]">{c.label}</h3>
                <p className="mt-1 text-[0.875rem] text-[#9c9283]">{c.note}</p>
              </Reveal>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
