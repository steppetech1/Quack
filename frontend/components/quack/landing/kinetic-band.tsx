"use client";

import { useT } from "@/lib/i18n/provider";
import { Reveal } from "./motion";

/**
 * Quiet two-tone editorial breath between the 3D Hero and the interactive flow.
 */
export function KineticBand() {
  const h = useT().landing.hero;

  return (
    <section className="relative border-y border-[#f1e4d6]/10 bg-[#080504]/55">
      <div className="mx-auto max-w-6xl px-5 py-20 sm:px-8 sm:py-28">
        <Reveal mode="rise">
          <p className="max-w-4xl font-display text-[clamp(1.5rem,3.3vw,2.75rem)] font-semibold leading-[1.18] tracking-[-0.025em]">
            <span className="text-[#f3eadc]">{h.statementLead} </span>
            <span className="text-[#8d8273]">{h.statementBody}</span>
          </p>
        </Reveal>
      </div>
    </section>
  );
}
