"use client";

import Link from "next/link";
import type { TodayItem } from "@/lib/mock/types";
import { useT } from "@/lib/i18n/provider";
import { useSoon } from "./soon";

const CARD =
  "flex h-[88px] w-[260px] shrink-0 flex-col justify-center gap-0.5 rounded-[16px] border border-seam bg-cloth-raised px-3.5 text-left transition-colors hover:border-rule hover:bg-cloth-hover md:w-auto md:min-w-0 md:flex-1";

/** «С чего начнём?»: 1–3 компактные карточки в ряд, над полем ввода */
export function TodayList({ items, idPrefix = "" }: { items: TodayItem[]; idPrefix?: string }) {
  const t = useT();
  const soon = useSoon();
  if (!items.length) return null;
  const total = items.reduce((s, i) => s + i.minutes, 0);

  return (
    <section aria-labelledby={`${idPrefix}today-h`} className="shrink-0">
      <div className="flex items-baseline gap-3 pb-1.5">
        <h2 id={`${idPrefix}today-h`} className="text-body font-semibold text-paper">
          {t.today.heading}
        </h2>
        <span className="num text-small text-paper-dim">≈ {t.today.min(total)}</span>
      </div>
      <ol className="-mx-4 flex gap-2.5 overflow-x-auto px-4 pb-1 md:mx-0 md:overflow-visible md:px-0 md:pb-0">
        {items.map((it, i) => {
          const body = (
            <>
              <span className="flex items-baseline gap-2">
                <span className="num shrink-0 text-small text-paper-dim">{t.today.num(i + 1)}</span>
                <span className="min-w-0 flex-1 truncate font-medium text-paper">{it.title}</span>
              </span>
              <span className="flex items-baseline gap-2 text-small text-paper-dim">
                <span className="line-clamp-2 min-w-0 flex-1">{it.reason}</span>
                <span className="num shrink-0">{t.today.min(it.minutes)}</span>
              </span>
            </>
          );
          return (
            <li key={it.id} className="contents">
              {it.href ? (
                <Link href={it.href} className={CARD}>
                  {body}
                </Link>
              ) : (
                <button type="button" onClick={() => soon(t.soon.session(it.title))} className={CARD}>
                  {body}
                </button>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
