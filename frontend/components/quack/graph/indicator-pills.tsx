"use client";

import { useSyncExternalStore } from "react";
import { getIndicators, getIndicatorsServer, subscribeIndicators, toggleIndicator } from "@/lib/graph/indicator-store";
import { INDICATORS, type IndicatorId } from "@/lib/graph/indicators";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

const COLOR: Record<IndicatorId, string> = {
  new: "var(--ind-new)",
  relevant: "var(--ind-relevant)",
  deadline: "var(--ind-deadline)",
  weak: "var(--ind-weak)",
  review: "var(--ind-review)",
};

/** The same form as the ring on the graph, so a colour is never the only clue */
function Mark({ id, color }: { id: IndicatorId; color: string }) {
  const s = { stroke: color, fill: "none", strokeWidth: 1.8 } as const;
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden className="shrink-0">
      {id === "new" && <circle cx="9" cy="9" r="6" {...s} />}
      {id === "relevant" && <circle cx="9" cy="9" r="6" {...s} strokeDasharray="3 2.4" />}
      {id === "deadline" && (
        <>
          <circle cx="9" cy="9" r="4.6" {...s} />
          {Array.from({ length: 8 }, (_, i) => {
            const a = (i * Math.PI) / 4;
            return <line key={i} x1={9 + Math.cos(a) * 6.4} y1={9 + Math.sin(a) * 6.4} x2={9 + Math.cos(a) * 8.4} y2={9 + Math.sin(a) * 8.4} {...s} />;
          })}
        </>
      )}
      {id === "weak" && (
        <>
          <circle cx="9" cy="9" r="6.6" {...s} strokeWidth={1.3} />
          <circle cx="9" cy="9" r="3.8" {...s} strokeWidth={1.3} />
        </>
      )}
      {id === "review" && <circle cx="9" cy="9" r="6" {...s} strokeWidth={2.4} strokeDasharray="0.1 3.6" strokeLinecap="round" />}
    </svg>
  );
}

/**
 * Five fixed pills. They sit against the left edge of the sessions panel and are children of that panel,
 * so they ride on its edge as it opens and closes: one movement, no lag.
 */
export function IndicatorPills() {
  const t = useT();
  const st = useSyncExternalStore(subscribeIndicators, getIndicators, getIndicatorsServer);
  if (!st.mounted) return null;

  const off: Record<IndicatorId, string> = { new: t.graphInd.offNew, relevant: t.graphInd.offRelevant, deadline: t.graphInd.offDeadline, weak: t.graphInd.offWeak, review: t.graphInd.offReview };

  return (
    <ul aria-label={t.graphInd.group} className="absolute top-1/3 right-full z-30 mr-2 hidden -translate-y-1/2 flex-col items-end gap-1.5 md:flex">
      {INDICATORS.map((id) => {
        const sel = st.selections[id];
        const nothing = sel.topics.length === 0 && sel.hubs.length === 0;
        const on = st.active.has(id);
        const color = COLOR[id];
        const count = id === "new" ? sel.topics.length + sel.hubs.length : 0;
        const why = nothing ? (sel.off === "firstVisit" ? t.graphInd.offFirst : off[id]) : undefined;
        return (
          <li key={id}>
            <button
              type="button"
              aria-pressed={on}
              aria-disabled={nothing}
              title={why}
              onClick={() => toggleIndicator(id)}
              style={on ? { borderColor: color } : undefined}
              className={cn(
                "caps inline-flex h-10 items-center gap-2 rounded-pill border bg-cloth-deep pr-3.5 pl-2.5 whitespace-nowrap transition-colors duration-(--dur-s) ease-(--ease-out) focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ribbon",
                nothing ? "cursor-default border-seam-soft text-paper-dim/50" : on ? "bg-cloth-raised text-paper" : "border-seam text-paper-dim hover:border-rule hover:text-paper",
              )}
            >
              <Mark id={id} color={nothing ? "var(--paper-dim)" : color} />
              {t.graphInd[id]}
              {count > 0 && <span className="num rounded-pill bg-cloth-raised px-1.5 text-small text-paper">{count}</span>}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
