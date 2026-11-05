"use client";

import { Check, Minus, X } from "lucide-react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

type Mark = "yes" | "no" | "part";

/** По строкам таблицы: слайды, ютуб, обычный ИИ-чат, Qvack! (порядок как в cols и rows словаря) */
const MARKS: Mark[][] = [
  ["yes", "no", "part", "yes"],
  ["no", "no", "part", "yes"],
  ["no", "no", "no", "yes"],
  ["no", "no", "no", "yes"],
  ["no", "no", "no", "yes"],
];

function MarkIcon({ mark, ours, label }: { mark: Mark; ours: boolean; label: string }) {
  const Icon = mark === "yes" ? Check : mark === "no" ? X : Minus;
  const tone = ours ? "text-[#ff8a2a]" : mark === "yes" ? "text-[#d8c8b6]" : mark === "no" ? "text-st-confused" : "text-[#8d8273]";
  return (
    <>
      <Icon aria-hidden strokeWidth={ours ? 2.5 : 1.8} className={cn("mx-auto size-5 sm:size-6", tone)} />
      <span className="sr-only">{label}</span>
    </>
  );
}

/** Блок 1: чем Qvack! отличается от привычных способов готовиться — таблица «да / нет / частично» */
export function Pluses() {
  const t = useT().landing;
  const p = t.pluses;
  const cols = [p.cols.slides, p.cols.video, p.cols.chat, p.cols.qvack];

  return (
    <section id="pluses" className="relative scroll-mt-14 text-[#f1e4d6]">
      <div className="mx-auto max-w-6xl px-5 py-24 sm:px-8 lg:py-32">
        <p className="font-mono text-[0.9375rem] text-[#ff8a2a]">{p.eyebrow}</p>
        <h2 className="mt-3 max-w-[18ch] font-mono text-[clamp(2rem,4.6vw,3.75rem)] font-extrabold leading-[1] tracking-[-0.05em] text-[#f6eadc]">
          {p.title}
        </h2>
        <p className="mt-5 max-w-[56ch] font-mono text-[clamp(0.9375rem,1.4vw,1.125rem)] leading-snug text-[#bba995]">{p.intro}</p>

        <div className="mt-10 overflow-hidden rounded-[6px] border border-[#f1e4d6]/10 bg-[#0c0705]/85">
          <table className="w-full table-fixed border-collapse font-mono">
            <caption className="sr-only">{p.title}</caption>
            <thead>
              <tr>
                <th scope="col" className="w-[36%] p-3 sm:w-[40%] sm:p-5">
                  <span className="sr-only">{p.eyebrow}</span>
                </th>
                {cols.map((c, i) => (
                  <th
                    key={c}
                    scope="col"
                    className={cn(
                      "px-0.5 py-3 text-center text-[0.625rem] font-medium text-[#bba995] sm:px-3 sm:py-5 sm:text-[0.9375rem]",
                      i === 3 && "bg-[#ff8a2a]/10 font-extrabold text-[#ff8a2a]",
                    )}
                  >
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {p.rows.map((row, r) => (
                <tr key={row} className="border-t border-[#f1e4d6]/10">
                  <th
                    scope="row"
                    className="p-3 text-left text-[0.8125rem] font-normal leading-snug text-[#e4d5c3] sm:p-5 sm:text-[1.0625rem]"
                  >
                    {row}
                  </th>
                  {MARKS[r].map((m, i) => (
                    <td key={i} className={cn("px-1 py-3 sm:px-3 sm:py-5", i === 3 && "bg-[#ff8a2a]/10")}>
                      <MarkIcon mark={m} ours={i === 3} label={p.marks[m]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
