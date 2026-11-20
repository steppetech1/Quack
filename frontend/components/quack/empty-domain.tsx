"use client";

import { useRef } from "react";
import { useT } from "@/lib/i18n/provider";
import { PixelDuck } from "./pixel-duck";

/** Первый заход: графа нет — подписи капсом, заголовок и текст по центру, поле ввода ниже.
 *  Утка здесь — маленькая пиксельная: она ходит по верхней кромке букв этого заголовка (components/quack/pixel-duck.tsx).
 *  Граф растёт из первых сессий и материалов */
export function EmptyDomain({ titleDat }: { titleDat: string }) {
  const t = useT();
  const titleRef = useRef<HTMLHeadingElement>(null);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 py-6 text-center">
      <p className="caps flex flex-wrap justify-center gap-x-3 text-paper-dim">
        <span>{t.preloader.kindTop}</span>
        <span aria-hidden>/</span>
        <span>{t.preloader.kindBottom}</span>
      </p>
      {/* утка живёт в обёртке без маски: mask-wipe обрезал бы её, она стоит над верхней кромкой букв; mt-3 — место над ней, чтобы не лезть на подпись */}
      <div className="relative mt-3 flex max-w-md flex-col items-center">
        <div className="mask-wipe animate-wipe-in flex flex-col items-center gap-3">
          <h2 ref={titleRef} className="display-caps text-display text-paper">{t.empty.title}</h2>
          <p className="text-paper-dim">{t.empty.body(titleDat)}</p>
        </div>
        <PixelDuck mode="walk" anchorRef={titleRef} text={t.empty.title} />
      </div>
    </div>
  );
}
