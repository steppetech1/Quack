"use client";

import Link from "next/link";
import { useT } from "@/lib/i18n/provider";
import { Duck } from "./duck";

/** Первый заход: графа нет — утка, короткий текст, поле ввода ниже.
 *  setupHref: предмет на бэкенде ещё не настроен (темы не подтверждены) — ведём довести настройку */
export function EmptyDomain({ titleDat, setupHref }: { titleDat: string; setupHref?: string }) {
  const t = useT();
  if (setupHref)
    return (
      <div className="flex flex-1 items-center">
        <div className="flex max-w-xl flex-col gap-6 sm:flex-row sm:items-center sm:gap-8">
          <Duck className="w-28 sm:w-36" />
          <div>
            <h2 className="font-semibold text-paper">{t.live.setupTitle}</h2>
            <p className="mt-1 text-paper-dim">{t.live.setupBody}</p>
            <Link
              href={setupHref}
              className="mt-4 inline-flex h-10 items-center rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover"
            >
              {t.live.setupCta}
            </Link>
          </div>
        </div>
      </div>
    );
  return (
    <div className="flex flex-1 items-center">
      <div className="flex max-w-xl flex-col gap-6 sm:flex-row sm:items-center sm:gap-8">
        <Duck className="w-28 sm:w-36" />
        <div>
          <h2 className="font-semibold text-paper">{t.empty.title}</h2>
          <p className="mt-1 text-paper-dim">
            {t.empty.body(titleDat)}
          </p>
        </div>
      </div>
    </div>
  );
}
