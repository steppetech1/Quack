"use client";

import Link from "next/link";
import { useFmt, useT } from "@/lib/i18n/provider";
import type { Domain } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { Duck } from "./duck";
import { StateDot } from "./topic-card";

/** Телефон: вместо графа — темы по главам, карточка раскрывается по касанию */
export function TopicList({ domain, openId }: { domain: Domain; openId: string | null }) {
  const t9 = useT();
  const { fullDate } = useFmt();
  const row = (t: Domain["topics"][number]) => {
    const open = openId === t.id;
    return (
      <li key={t.id} id={`topic-${t.id}`} className="scroll-mt-4 border-b border-seam-soft last:border-0">
        <Link
          href={`/topic/${t.id}`}
          className={cn("-mx-2 flex min-h-11 items-center gap-3 rounded-[2px] px-2 py-2", open && "bg-cloth-raised")}
        >
          <StateDot state={t.state} />
          <span className="min-w-0 flex-1 truncate text-paper">{t.title}</span>
          <span
            className={cn(
              "caps hidden shrink-0 sm:inline",
              t.state === "confused" ? "text-st-confused" : "text-paper-dim",
            )}
          >
            {t9.states[t.state]}
          </span>
          <span className="sr-only sm:hidden">{t9.states[t.state]}</span>
        </Link>
      </li>
    );
  };
  const free = domain.topics.filter((t) => !t.hubId);
  return (
    <div className="flex flex-col gap-5">
      {domain.hubs.map((h) => (
        <section key={h.id} aria-labelledby={`hub-${h.id}`} >
          <div className="flex items-baseline justify-between gap-3 border-b border-seam-soft pb-1.5">
            <h2 id={`hub-${h.id}`} className="display-caps text-paper">
              <Link href={`/hub/${h.id}`} className="inline-flex min-h-10 items-center">
                <span className="num text-paper-dim">§ {h.num}</span>&nbsp;{h.title}
              </Link>
            </h2>
            {h.deadline && (
              <span className="caps flex items-center gap-1 text-ribbon">
                <Duck className="h-3.5 w-4" striped={false} />
                {t9.topicList.due} {fullDate(h.deadline.date)}
              </span>
            )}
            {h.suggested && <span className="caps text-paper-dim">{t9.topicList.suggested}</span>}
          </div>
          <ul>
            {domain.topics
              .filter((t) => t.hubId === h.id)
              .map(row)}
          </ul>
        </section>
      ))}
      {free.length > 0 && (
        <section aria-labelledby="hub-none">
          <div className="flex items-baseline justify-between gap-3 border-b border-seam-soft pb-1.5">
            <h2 id="hub-none" className="display-caps inline-flex min-h-10 items-center text-paper">
              {t9.graph.noChapter}
            </h2>
          </div>
          <ul>{free.map(row)}</ul>
        </section>
      )}
    </div>
  );
}
