"use client";

import { useT } from "@/lib/i18n/provider";

/** Блок 2: что ещё есть в Quack! помимо объяснений — слева заголовок, справа пронумерованный список */
export function Features() {
  const f = useT().landing.features;

  return (
    <section id="features" className="relative scroll-mt-14 text-[#f1e4d6]">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 pb-24 sm:px-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16 lg:pb-32">
        <div className="lg:sticky lg:top-28 lg:self-start">
          <p className="font-mono text-[0.9375rem] text-[#ff8a2a]">{f.eyebrow}</p>
          <h2 className="mt-3 font-mono text-[clamp(2rem,4.6vw,3.75rem)] font-extrabold leading-[1] tracking-[-0.05em] text-[#f6eadc]">
            {f.title}
          </h2>
        </div>

        <ol className="rounded-[6px] border border-[#f1e4d6]/10 bg-[#0c0705]/85 font-mono">
          {f.items.map((it, i) => (
            <li key={it.title} className="flex gap-4 border-t border-[#f1e4d6]/10 p-5 first:border-t-0 sm:gap-6 sm:p-7">
              <span aria-hidden className="pt-1 text-[0.9375rem] text-[#ff8a2a]">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <h3 className="text-[clamp(1.125rem,1.8vw,1.5rem)] font-bold leading-tight tracking-[-0.03em] text-[#f6eadc]">{it.title}</h3>
                <p className="mt-2 text-[clamp(0.9375rem,1.2vw,1.0625rem)] leading-snug text-[#bba995]">{it.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
