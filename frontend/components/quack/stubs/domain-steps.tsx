"use client";

import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/**
 * Ориентир на пути создания предмета: «01 Опиши — 02 Подтверди». Текущий шаг ярче, пройденный — кнопка:
 * по нему можно вернуться и поправить написанное. Шаги не кликабельны вперёд.
 */
export function DomainSteps({ step, onBack }: { step: 1 | 2; onBack?: () => void }) {
  const live = useT().live;
  const items = [live.domainStepDescribe, live.domainStepConfirm];
  return (
    <nav aria-label={live.domainSteps} className="relative z-10 mb-2 [text-shadow:0_0_10px_#141311,0_0_4px_#141311]">
      <ol className="caps flex items-center gap-3 text-paper-dim">
        {items.map((label, i) => {
          const n = i + 1;
          const current = n === step;
          const done = n < step;
          const inner = (
            <>
              <span className={cn("tabular-nums", current ? "text-ribbon" : "text-paper-dim")}>{String(n).padStart(2, "0")}</span>
              <span>{label}</span>
            </>
          );
          return (
            <li key={label} className="flex items-center gap-3">
              {i > 0 && <span aria-hidden className={cn("h-px w-8 sm:w-12", done || current ? "bg-rule" : "bg-seam")} />}
              {done && onBack ? (
                <button
                  type="button"
                  onClick={onBack}
                  className="flex min-h-10 items-center gap-2 text-paper-dim transition-colors duration-(--dur-s) ease-out hover:text-paper"
                >
                  {inner}
                </button>
              ) : (
                <span aria-current={current ? "step" : undefined} className={cn("flex min-h-10 items-center gap-2", current && "text-paper")}>
                  {inner}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
