"use client";

import { useT } from "@/lib/i18n/provider";
import { Duck } from "./duck";

/** Первый заход: графа нет — большая полосатая утка, подписи капсом, заголовок, поле ввода ниже.
 *  Спокойно, по центру, шторка при появлении. Граф растёт из первых сессий и материалов */
export function EmptyDomain({ titleDat }: { titleDat: string }) {
  const t = useT();
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 py-6 text-center">
      <p className="caps flex flex-wrap justify-center gap-x-3 text-paper-dim">
        <span>{t.preloader.kindTop}</span>
        <span aria-hidden>/</span>
        <span>{t.preloader.kindBottom}</span>
      </p>
      <div className="mask-wipe animate-wipe-in">
        <Duck className="w-48 sm:w-64" striped />
      </div>
      <div className="flex max-w-md flex-col items-center gap-3">
        <h2 className="display-caps text-display text-paper">{t.empty.title}</h2>
        <p className="text-paper-dim">{t.empty.body(titleDat)}</p>
      </div>
    </div>
  );
}
