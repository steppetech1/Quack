"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";
import { useLocale, useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { MagneticLink, Reveal } from "./motion";
import { useLandingStage } from "./stage/landing-context";

const LINK =
  "inline-flex min-h-10 items-center rounded-[4px] font-mono text-[0.875rem] text-[#b3a695] outline-none transition-colors duration-(--dur-s) hover:text-[#edb966] focus-visible:ring-2 focus-visible:ring-[#edb966]/70";

export function LandingFooter() {
  const t = useT().landing;
  const { locale, setLocale } = useLocale();
  const { scrollRef } = useLandingStage();

  const scrollToSection = useCallback(
    (id: string) => (e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      const root = scrollRef.current;
      const target = document.getElementById(id);
      if (!root || !target) return;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const top = root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top;
      root.scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
    },
    [scrollRef],
  );

  return (
    <footer className="relative text-[#f3eadc]">
      {/* Closing manifesto stage — returning 3D duck floats unobstructed on the right */}
      <div className="relative mx-auto flex min-h-[60dvh] max-w-6xl items-center px-5 pt-20 pb-20 sm:px-8 lg:py-24">
        <div className="max-w-xl bg-gradient-to-r from-[#080504]/85 via-[#080504]/65 to-transparent p-2 lg:bg-none lg:p-0">
          <Reveal mode="wipe" delayMs={80}>
            <p className="font-display text-[clamp(2rem,4vw,3.4rem)] font-bold leading-[1.06] tracking-[-0.03em]">
              <span className="block text-[#f3eadc]">{t.footer.tagline}</span>
              <span className="block text-[#8d8273]">{t.footer.sub}</span>
            </p>
          </Reveal>

          <Reveal mode="rise" delayMs={160}>
            <div className="mt-7 flex flex-wrap items-center gap-5">
              <MagneticLink
                href="/signup"
                className="group inline-flex h-11.5 items-center gap-2 rounded-pill bg-[#edb966] px-7 font-mono text-[0.875rem] font-bold text-[#141008] outline-none hover:bg-[#f5cf8e] focus-visible:ring-4 focus-visible:ring-[#edb966]/45 active:translate-y-px"
              >
                <span>{t.footer.ctaPrimary}</span>
                <ArrowRight
                  aria-hidden
                  className="size-4 transition-transform duration-300 ease-(--ease-out) group-hover:translate-x-0.5"
                />
              </MagneticLink>

              <Link
                href="/login"
                className="inline-flex min-h-11 items-center font-mono text-[0.875rem] text-[#a39787] outline-none transition-colors duration-(--dur-s) hover:text-[#f3eadc] focus-visible:ring-2 focus-visible:ring-[#edb966]/70"
              >
                {t.footer.ctaSecondary}
              </Link>
            </div>
          </Reveal>
        </div>
      </div>

      {/* Footer links bar + edge-to-edge monumental QUACK! wordmark */}
      <div className="relative border-t border-[#f1e4d6]/12 bg-[#080504]">
        <div className="mx-auto max-w-6xl px-5 pt-12 pb-6 sm:px-8">
          <div className="grid gap-10 sm:grid-cols-[1.5fr_1fr_1fr_auto]">
            <div>
              <p className="font-mono text-[1.375rem] font-extrabold tracking-[-0.06em] text-[#f3eadc]">
                Quack<span className="text-[#edb966]">!</span>
              </p>
              <p className="mt-2 text-[0.875rem] text-[#8d8273]">{t.footer.note}</p>
            </div>

            <nav aria-label={t.footer.product} className="flex flex-col items-start">
              <p className="mb-2 font-mono text-[0.75rem] uppercase tracking-[0.06em] text-[#8d8273]">
                {t.footer.product}
              </p>
              <a href="#flow" onClick={scrollToSection("flow")} className={LINK}>
                {t.nav.flow}
              </a>
              <a href="#compare" onClick={scrollToSection("compare")} className={LINK}>
                {t.nav.compare}
              </a>
              <a href="#tools" onClick={scrollToSection("tools")} className={LINK}>
                {t.nav.tools}
              </a>
            </nav>

            <nav aria-label={t.footer.account} className="flex flex-col items-start">
              <p className="mb-2 font-mono text-[0.75rem] uppercase tracking-[0.06em] text-[#8d8273]">
                {t.footer.account}
              </p>
              <Link href="/login" className={LINK}>
                {t.nav.login}
              </Link>
              <Link href="/signup" className={LINK}>
                {t.nav.cta}
              </Link>
            </nav>

            <div className="flex items-start">
              <div
                role="group"
                aria-label={t.nav.langLabel}
                className="inline-flex h-9 items-center gap-1 font-mono text-[0.75rem]"
              >
                {(["en", "ru"] as const).map((code, idx) => {
                  const isCur = locale === code;
                  return (
                    <span key={code} className="inline-flex items-center gap-1">
                      {idx > 0 && (
                        <span aria-hidden className="text-[#736e63]">
                          /
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => setLocale(code)}
                        aria-pressed={isCur}
                        className={cn(
                          "inline-flex min-h-8 cursor-pointer items-center px-1.5 uppercase transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
                          isCur ? "font-semibold text-[#edb966]" : "text-[#8d8273] hover:text-[#f3eadc]",
                        )}
                      >
                        {code}
                      </button>
                    </span>
                  );
                })}
              </div>
            </div>
          </div>

          <p className="mt-10 border-t border-[#f1e4d6]/10 pt-5 font-mono text-[0.75rem] text-[#8d8273]">
            © {new Date().getFullYear()} Quack! · {t.footer.rights}
          </p>
        </div>

        {/* Monumental striped display wordmark */}
        <div aria-hidden className="overflow-hidden px-2 pt-4 select-none">
          <p className="text-striped text-center font-[family-name:var(--font-archivo)] text-[clamp(4.5rem,20vw,19rem)] font-semibold leading-[0.78] tracking-[-0.06em] text-[#edb966]/85 uppercase [font-stretch:125%]">
            QUACK!
          </p>
        </div>
      </div>
    </footer>
  );
}
