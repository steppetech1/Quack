"use client";

import Link from "next/link";
import { useT } from "@/lib/i18n/provider";
import { onAnchor } from "./scenes/story";

const LINK =
  "inline-flex min-h-10 items-center rounded-[6px] font-mono text-[0.9375rem] text-[#bba995] outline-none transition-colors duration-(--dur-s) hover:text-[#f6eadc] focus-visible:ring-2 focus-visible:ring-[#ff8a2a]/60";

/** Подвал лендинга: призыв, логотип с подписью, колонки ссылок и копирайт */
export function LandingFooter() {
  const t = useT().landing;

  return (
    <footer className="relative border-t border-[#f1e4d6]/10 bg-[#080504]/95 font-mono text-[#f1e4d6]">
      <div className="mx-auto max-w-6xl px-5 pt-16 sm:px-8 lg:pt-20">
        <div className="flex flex-col items-start justify-between gap-8 lg:flex-row lg:items-center">
          <p className="max-w-[22ch] text-[clamp(1.75rem,3.6vw,3rem)] font-extrabold leading-[1.05] tracking-[-0.05em] text-[#f6eadc]">
            {t.tagline}
          </p>
          <Link
            href="/signup"
            className="inline-flex h-16 items-center justify-center rounded-pill bg-[#ff8a2a] px-10 text-[1.375rem] font-bold leading-none tracking-[-0.03em] text-[#1a0a02] shadow-[0_0_40px_#ff7a1a55] outline-none transition-[background-color,transform] duration-(--dur-s) ease-(--ease-out) hover:bg-[#ff9d4d] focus-visible:ring-4 focus-visible:ring-[#ff8a2a]/50 active:translate-y-px"
          >
            {t.cta}
          </Link>
        </div>

        <div className="mt-16 grid gap-10 border-t border-[#f1e4d6]/10 pt-10 sm:grid-cols-[1.4fr_1fr_1fr]">
          <div>
            <p className="text-[1.5rem] font-extrabold tracking-[-0.06em] text-[#f6eadc]">
              Qvack<span className="text-[#ff8a2a]">!</span>
            </p>
            <p className="mt-2 text-[0.875rem] text-[#8d8273]">{t.footer.note}</p>
          </div>
          <nav aria-label={t.footer.product} className="flex flex-col items-start">
            <p className="mb-2 text-[0.8125rem] text-[#8d8273]">{t.footer.product}</p>
            <a href="#efficiency" onClick={onAnchor("efficiency")} className={LINK}>
              {t.nav.why}
            </a>
            <a href="#how" onClick={onAnchor("how")} className={LINK}>
              {t.nav.pluses}
            </a>
          </nav>
          <nav aria-label={t.footer.account} className="flex flex-col items-start">
            <p className="mb-2 text-[0.8125rem] text-[#8d8273]">{t.footer.account}</p>
            <Link href="/login" className={LINK}>
              {t.nav.login}
            </Link>
            <Link href="/signup" className={LINK}>
              {t.nav.cta}
            </Link>
          </nav>
        </div>

        <p className="mt-12 border-t border-[#f1e4d6]/10 py-6 text-[0.8125rem] text-[#8d8273]">
          © {new Date().getFullYear()} Qvack! · {t.footer.rights}
        </p>
      </div>
    </footer>
  );
}
