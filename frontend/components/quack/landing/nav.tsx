"use client";

import { ArrowRight, Menu, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocale, useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { MagneticLink } from "./motion";
import { useLandingStage } from "./stage/landing-context";

export function LandingNav() {
  const t = useT().landing;
  const { locale, setLocale } = useLocale();
  const { scrollRef, activeSection, subscribeProgress } = useLandingStage();

  const hudFillRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    return subscribeProgress((progress) => {
      const p = Math.min(1, Math.max(0, progress));
      if (hudFillRef.current) {
        hudFillRef.current.style.transform = `scaleX(${p.toFixed(4)})`;
      }
    });
  }, [subscribeProgress]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const scrollToSection = useCallback(
    (id: string) => (e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      setMenuOpen(false);
      const root = scrollRef.current;
      const target = document.getElementById(id);
      if (!root || !target) return;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const top =
        id === "top"
          ? 0
          : root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top;
      root.scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
    },
    [scrollRef],
  );

  const anchors = [
    { id: "flow", label: t.nav.flow },
    { id: "compare", label: t.nav.compare },
    { id: "tools", label: t.nav.tools },
  ];

  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-[#f1e4d6]/8 bg-[#080504]/78 backdrop-blur-sm">
      {/* Hairline 2px scroll progress indicator along top edge */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-[2px] overflow-hidden">
        <div
          ref={hudFillRef}
          style={{ transform: "scaleX(0)" }}
          className="h-full w-full origin-left bg-[#edb966] will-change-transform"
        />
      </div>

      <nav
        aria-label={t.nav.label}
        className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-8"
      >
        <a
          href="#top"
          onClick={scrollToSection("top")}
          className="inline-flex min-h-10 items-center font-mono text-[1.0625rem] font-extrabold tracking-[-0.05em] text-[#f3eadc] outline-none transition-opacity duration-(--dur-s) hover:opacity-85 focus-visible:ring-2 focus-visible:ring-[#edb966]/70"
        >
          Quack<span className="text-[#edb966]">!</span>
        </a>

        {/* Quiet bare anchor links */}
        <div className="hidden items-center gap-6 md:flex">
          {anchors.map((a) => {
            const active = activeSection === a.id;
            return (
              <a
                key={a.id}
                href={`#${a.id}`}
                onClick={scrollToSection(a.id)}
                aria-current={active ? "location" : undefined}
                className={cn(
                  "relative inline-flex min-h-10 items-center font-mono text-[0.75rem] tracking-[-0.01em] outline-none transition-colors duration-(--dur-s) focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
                  active ? "text-[#f3eadc]" : "text-[#8d8273] hover:text-[#f3eadc]",
                )}
              >
                <span>{a.label}</span>
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-0 -bottom-[1px] h-[1px] bg-[#edb966]"
                  />
                )}
              </a>
            );
          })}
        </div>

        <div className="flex items-center gap-3 sm:gap-5">
          {/* Bare inline EN / RU switcher */}
          <div
            role="group"
            aria-label={t.nav.langLabel}
            className="hidden items-center gap-1 font-mono text-[0.6875rem] uppercase sm:inline-flex"
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
                      "inline-flex min-h-8 cursor-pointer items-center px-1 uppercase outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
                      isCur ? "font-semibold text-[#edb966]" : "text-[#8d8273] hover:text-[#f3eadc]",
                    )}
                  >
                    {code}
                  </button>
                </span>
              );
            })}
          </div>

          <button
            type="button"
            onClick={() => setLocale(locale === "en" ? "ru" : "en")}
            aria-label={t.nav.langLabel}
            className="inline-flex min-h-9 cursor-pointer items-center px-1.5 font-mono text-[0.6875rem] uppercase text-[#a39787] outline-none transition-colors hover:text-[#f3eadc] focus-visible:ring-2 focus-visible:ring-[#edb966]/70 sm:hidden"
          >
            {locale}
          </button>

          <Link
            href="/login"
            className="hidden min-h-10 items-center font-mono text-[0.75rem] text-[#a39787] outline-none transition-colors duration-(--dur-s) hover:text-[#f3eadc] focus-visible:ring-2 focus-visible:ring-[#edb966]/70 sm:inline-flex"
          >
            {t.nav.login}
          </Link>

          <MagneticLink
            href="/signup"
            className="group inline-flex h-8.5 items-center gap-1.5 rounded-pill bg-[#edb966] px-4 font-mono text-[0.75rem] font-bold text-[#141008] outline-none hover:bg-[#f5cf8e] focus-visible:ring-4 focus-visible:ring-[#edb966]/45 active:translate-y-px"
          >
            <span>{t.nav.cta}</span>
            <ArrowRight
              aria-hidden
              className="size-3.5 transition-transform duration-300 ease-(--ease-out) group-hover:translate-x-0.5"
            />
          </MagneticLink>

          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-expanded={menuOpen}
            aria-label={menuOpen ? t.nav.menuClose : t.nav.menuOpen}
            className="inline-flex size-10 cursor-pointer items-center justify-center rounded-[4px] border border-[#f1e4d6]/12 text-[#f3eadc] outline-none transition-colors hover:border-[#f1e4d6]/28 focus-visible:ring-2 focus-visible:ring-[#edb966]/70 md:hidden"
          >
            {menuOpen ? <X aria-hidden className="size-4.5" /> : <Menu aria-hidden className="size-4.5" />}
          </button>
        </div>
      </nav>

      {/* Clean flat mobile drawer */}
      {menuOpen && (
        <div className="border-t border-[#f1e4d6]/10 bg-[#080504]/96 px-4 py-3 md:hidden">
          <div className="flex flex-col divide-y divide-[#f1e4d6]/8">
            {anchors.map((a) => {
              const active = activeSection === a.id;
              return (
                <a
                  key={a.id}
                  href={`#${a.id}`}
                  onClick={scrollToSection(a.id)}
                  className={cn(
                    "flex min-h-11 items-center justify-between font-mono text-[0.875rem] transition-colors",
                    active ? "text-[#edb966]" : "text-[#d8c8b6] hover:text-[#f3eadc]",
                  )}
                >
                  <span>{a.label}</span>
                  <ArrowRight aria-hidden className="size-4 opacity-50" />
                </a>
              );
            })}
          </div>

          <div className="mt-3 flex items-center justify-between gap-3 border-t border-[#f1e4d6]/10 pt-3">
            <div
              role="group"
              aria-label={t.nav.langLabel}
              className="inline-flex items-center gap-2 font-mono text-[0.8125rem]"
            >
              {(["en", "ru"] as const).map((code, idx) => {
                const isCur = locale === code;
                return (
                  <span key={code} className="inline-flex items-center gap-2">
                    {idx > 0 && (
                      <span aria-hidden className="text-[#736e63]">
                        /
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        setLocale(code);
                        setMenuOpen(false);
                      }}
                      aria-pressed={isCur}
                      className={cn(
                        "inline-flex min-h-10 cursor-pointer items-center px-1 uppercase transition-colors",
                        isCur ? "font-bold text-[#edb966]" : "text-[#8d8273]",
                      )}
                    >
                      {code}
                    </button>
                  </span>
                );
              })}
            </div>

            <Link
              href="/login"
              onClick={() => setMenuOpen(false)}
              className="inline-flex min-h-10 items-center font-mono text-[0.8125rem] text-[#f3eadc]"
            >
              {t.nav.login}
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
