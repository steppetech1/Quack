"use client";

import { ChevronDown } from "lucide-react";
import { useT } from "@/lib/i18n/provider";
import type { PlanStep, SessionData } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useSoon } from "./soon";

type Plan = SessionData["plan"];

/** Граф действий: точки и тонкая линия, как граф домена. Пройденное — обычное, текущее — выделено, дальше — бледно */
export function PlanGraph({
  plan,
  progress,
  open,
  onToggle,
  onJump,
  className,
}: {
  plan: Plan;
  progress: { label: string; done: number; total: number };
  open: boolean;
  onToggle: () => void;
  onJump: (anchor: string) => void;
  className?: string;
}) {
  const tx = useT();
  const soon = useSoon();
  const steps: { step: PlanStep; kind: "done" | "current" | "next" }[] = [
    ...plan.done.map((step) => ({ step, kind: "done" as const })),
    { step: plan.current, kind: "current" as const },
    ...plan.next.map((step) => ({ step, kind: "next" as const })),
  ];

  return (
    <section aria-label={tx.plan.title} className={cn("rounded-[16px] border border-seam bg-cloth-raised", className)}>
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="relative flex min-h-11 w-full flex-wrap items-baseline gap-x-2 rounded-[16px] py-2.5 pr-9 pl-3.5 text-left"
      >
        <span className="font-semibold text-paper">{tx.plan.heading}</span>
        <span className="text-small text-paper-dim">
          {tx.plan.progress(progress.label, progress.done, progress.total)}
        </span>
        <ChevronDown
          aria-hidden
          className={cn("absolute top-3.5 right-3 size-4 text-paper-dim transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <ol className="relative px-3.5 pt-0.5 pb-3">
          {/* основная линия плана */}
          <span aria-hidden className="absolute top-3 bottom-6 left-[23px] w-px bg-seam" />
          {steps.map(({ step, kind }) => {
            const clickable = kind !== "next" ? !!step.anchor : true;
            const label = (
              <>
                <span
                  aria-hidden
                  className={cn(
                    "relative z-[1] mt-[5px] size-2.5 shrink-0 rounded-full",
                    kind === "current" && "animate-in duration-300 ease-out zoom-in-50",
                    kind === "done" && "bg-paper-dim",
                    kind === "current" && "bg-ribbon ring-2 ring-ribbon-soft ring-offset-2 ring-offset-cloth-raised",
                    kind === "next" && "border border-rule bg-cloth-raised",
                  )}
                />
                <span
                  className={cn(
                    "text-small",
                    kind === "current" ? "font-semibold text-paper" : kind === "done" ? "text-paper" : "text-paper-dim",
                  )}
                >
                  {step.label}
                </span>
                {kind === "current" && <span className="sr-only">{tx.plan.current}</span>}
              </>
            );
            return (
              <li key={step.id} className={cn("relative", step.branch && "pl-5")}>
                {step.branch && (
                  <span aria-hidden className="absolute top-0 left-[9px] h-[17px] w-4 rounded-bl-[6px] border-b border-l border-seam" />
                )}
                {clickable ? (
                  <button
                    type="button"
                    onClick={() => (kind === "next" ? soon(tx.plan.jumpSoon(step.label)) : step.anchor && onJump(step.anchor))}
                    className="flex min-h-10 w-full items-start gap-2.5 rounded-option px-1.5 py-1.5 text-left transition-colors hover:bg-cloth-hover"
                  >
                    {label}
                  </button>
                ) : (
                  <div className="flex min-h-10 items-start gap-2.5 px-1.5 py-1.5">{label}</div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
