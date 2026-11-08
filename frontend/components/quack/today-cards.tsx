"use client";

import Link from "next/link";
import { useState } from "react";
import { API_MODE } from "@/lib/api/client";
import type { TodayRec } from "@/lib/graph/types";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { useStartSession } from "./live/use-start-session";
import { useSoon } from "./soon";

const CARD =
  "flex min-h-[64px] w-[240px] shrink-0 flex-col justify-center gap-1 rounded-[2px] border border-seam px-3.5 py-2.5 text-left transition-colors duration-(--dur-s) ease-(--ease-out) md:w-auto md:min-w-0 md:max-w-[calc((100%-1.25rem)/3)] md:flex-1";
const CARD_ACTIVE = "hover:border-rule hover:bg-cloth-raised focus-visible:border-rule focus-visible:bg-cloth-raised";

/**
 * "Today": cards over the input. A card says what (topic or hub), how (a fixed word) and about how long.
 * It shows only what the recommendation carried. Nothing to show means nothing on screen: no stub, no empty strip.
 * The strip opens and closes by height, so the graph above does not jump when cards come and go.
 */
export function TodayCards({
  recs,
  nameOf,
  onHover,
  minutes,
  className,
  idPrefix = "",
}: {
  recs: TodayRec[];
  nameOf: (r: TodayRec) => string | undefined;
  onHover: (r: TodayRec | null) => void;
  /** About how long everything for today takes (`minutes` of the backend); a line over the cards when it is there */
  minutes?: number;
  className?: string;
  idPrefix?: string;
}) {
  const t = useT();
  const soon = useSoon();
  const { domain } = useShell();
  const { start, busy } = useStartSession();

  // Keep the last cards while the strip closes, so they do not vanish before it has folded
  const [last, setLast] = useState(recs);
  if (recs.length && recs !== last) setLast(recs);
  const open = recs.length > 0;
  const list = open ? recs : last;

  const words = { learn: t.topicCard.study, practice: t.topicCard.practice, review: t.topicCard.review } as const;

  const content = (r: TodayRec) => {
    const name = r.kind === "pace_warning" ? r.text : nameOf(r);
    const meta = [r.kind === "item" && r.action ? words[r.action] : null, r.kind === "item" && r.minutes !== undefined ? `~${t.today.min(r.minutes)}` : null].filter(Boolean).join(" · ");
    if (!name && !meta) return null;
    return (
      <>
        {name && <span className={cn("min-w-0", r.kind === "pace_warning" ? "text-small text-paper" : "display-caps line-clamp-2 text-[1rem] text-paper")}>{name}</span>}
        {meta && <span className="caps text-paper-dim">{meta}</span>}
      </>
    );
  };

  return (
    <section
      aria-label={t.today.heading}
      aria-hidden={!open}
      inert={!open}
      className={cn("grid shrink-0 transition-[grid-template-rows,margin] duration-(--dur-m) ease-(--ease-out)", className)}
      style={{ gridTemplateRows: open ? "1fr" : "0fr", marginTop: open ? 0 : "-1rem" }}
    >
      <div className="min-h-0 overflow-hidden">
        {minutes ? <p className="caps num mb-1.5 text-paper-dim [--caps-size:0.625rem]">{t.today.total(minutes)}</p> : null}
        <ol className="-mx-4 flex items-stretch gap-2.5 overflow-x-auto px-4 py-0.5 md:mx-0 md:overflow-visible md:px-0">
          {list.map((r) => {
            const body = content(r);
            if (!body) return null;
            const label = r.kind === "item" ? nameOf(r) : undefined;
            const enter = { onPointerEnter: () => onHover(r), onPointerLeave: () => onHover(null), onFocus: () => onHover(r), onBlur: () => onHover(null) };
            let card: React.ReactNode;
            if (r.kind === "pace_warning") {
              card = <div className={CARD}>{body}</div>;
            } else if (r.href) {
              card = (
                <Link href={r.href} className={cn(CARD, CARD_ACTIVE)} {...enter}>
                  {body}
                </Link>
              );
            } else if (r.topicId && r.action === "practice") {
              card = (
                <Link href={`/practice/${r.topicId}`} className={cn(CARD, CARD_ACTIVE)} {...enter}>
                  {body}
                </Link>
              );
            } else {
              card = (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    API_MODE && r.topicId && (r.action === "learn" || r.action === "review")
                      ? void start({ domainId: domain.id, topicId: r.topicId, intent: r.action })
                      : soon(t.soon.session(label ?? ""))
                  }
                  className={cn(CARD, CARD_ACTIVE, "disabled:opacity-50")}
                  {...enter}
                >
                  {body}
                </button>
              );
            }
            return (
              <li key={`${idPrefix}${r.id}`} className="contents">
                {card}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
