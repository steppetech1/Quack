"use client";

import { useT } from "@/lib/i18n/provider";
import { Inline } from "./rich-text";

/**
 * Демо от фонового агента: интервал сходимости на числовой оси.
 * Центр a = 2, R = 3 → интервал (−1; 5), концы под вопросом.
 */
export function IntervalDemo() {
  const tx = useT();
  const x = (v: number) => 30 + (v + 3) * 50; // −3…9 → 30…630
  const ticks = [-3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  return (
    <figure className="rounded-[16px] border border-seam bg-cloth-deep p-4">
      <svg viewBox="0 0 660 110" className="w-full" role="img" aria-label={tx.session.demoAria}>
        <line x1="20" x2="640" y1="62" y2="62" stroke="var(--rule)" strokeWidth="1" />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1="57" y2="67" stroke="var(--rule)" />
            <text x={x(t)} y="88" textAnchor="middle" className="fill-paper-dim font-sans text-small tabular-nums">
              {t}
            </text>
          </g>
        ))}
        {/* интервал сходимости */}
        <line x1={x(-1)} x2={x(5)} y1="62" y2="62" stroke="var(--st-solid)" strokeWidth="4" strokeLinecap="butt" />
        <path d={`M${x(2)},62 Q${(x(2) + x(5)) / 2},18 ${x(5)},62`} fill="none" stroke="var(--paper-dim)" strokeDasharray="3 4" />
        <text x={(x(2) + x(5)) / 2} y="28" textAnchor="middle" className="fill-paper font-mono text-small">
          R = 3
        </text>
        <circle cx={x(2)} cy="62" r="4" fill="var(--paper)" />
        {[-1, 5].map((e) => (
          <g key={e}>
            <circle cx={x(e)} cy="62" r="6" fill="var(--cloth-deep)" stroke="var(--st-review)" strokeWidth="1.5" />
            <text x={x(e)} y="44" textAnchor="middle" className="fill-st-review font-mono text-small">
              ?
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-1 text-small text-paper-dim">
        <Inline text={tx.session.demoCaption} />
      </figcaption>
    </figure>
  );
}
