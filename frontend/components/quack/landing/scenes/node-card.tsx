"use client";

import type { Ref } from "react";

/**
 * Карточка узла при наведении, как карточка темы на платформе: тонкая рамка, углы 6px, заголовок и одно-два предложения.
 * Положение задаёт сцена (transform через ref каждый кадр), здесь только вид. Шрифт — основной текстовый, Manrope.
 */
export function NodeCard({ cardRef, title, text, visible }: { cardRef: Ref<HTMLDivElement>; title: string; text: string; visible: boolean }) {
  return (
    <div
      ref={cardRef}
      role="status"
      aria-live="polite"
      className={`pointer-events-none absolute left-0 top-0 z-20 w-[min(19rem,78vw)] rounded-[6px] border border-[#f1e4d6]/15 bg-[#141311]/95 p-4 shadow-[0_12px_40px_#000a] transition-opacity duration-(--dur-s) ease-(--ease-out) ${visible ? "opacity-100" : "opacity-0"}`}
      style={{ willChange: "transform" }}
    >
      <p className="font-sans text-[0.9375rem] font-semibold leading-snug tracking-[-0.01em] text-[#daa05f]">{title}</p>
      <p className="mt-1.5 font-sans text-[0.8125rem] leading-[1.5] text-[#bba995]">{text}</p>
    </div>
  );
}
