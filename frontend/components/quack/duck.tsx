"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

/** Размер утки в CSS-пикселях, с которого «штриховка» читается (ниже — сплошной силуэт) */
const STRIPE_MIN_PX = 64;
/** Шаг и ширина штриха в CSS-пикселях: как у text-striped */
const STEP_PX = 3;
const LINE_PX = 1.8;
const VIEW_W = 120;
const VIEW_H = 96;

/**
 * Утка Qvack!: геометрический силуэт в профиль. Тело нарисовано вертикальными
 * бежевыми штрихами (как штриховка прелоадера), клюв и глаз — сплошные.
 *
 * striped: undefined — автоматически, штрихи только когда утка отрисована шире 64px
 * (на маленьких «значках» штрихи превращаются в кашу); true/false — принудительно.
 */
export function Duck({ className, title, striped }: { className?: string; title?: string; striped?: boolean }) {
  const ref = useRef<SVGSVGElement>(null);
  const uid = useId().replace(/:/g, "");
  const [auto, setAuto] = useState(false);
  // сколько единиц viewBox в одном CSS-пикселе: штрих должен быть 3px при любом размере
  const [unit, setUnit] = useState(VIEW_W / 144);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      // viewBox вписывается целиком (meet): масштаб — по меньшей стороне
      const scale = Math.min(r.width / VIEW_W, r.height / VIEW_H);
      if (!scale) return;
      setAuto(r.width >= STRIPE_MIN_PX);
      setUnit(1 / scale);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const on = striped ?? auto;
  const maskId = `duck-m-${uid}`;
  const patId = `duck-p-${uid}`;

  return (
    <svg
      ref={ref}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className={cn("shrink-0", className)}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {on && (
        <defs>
          <pattern id={patId} width={STEP_PX * unit} height={VIEW_H} patternUnits="userSpaceOnUse">
            <rect width={LINE_PX * unit} height={VIEW_H} fill="#fff" />
          </pattern>
          <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width={VIEW_W} height={VIEW_H}>
            <rect width={VIEW_W} height={VIEW_H} fill={`url(#${patId})`} />
          </mask>
        </defs>
      )}
      <g mask={on ? `url(#${maskId})` : undefined}>
        <path
          d="M12 54 Q8 40 21 44 Q30 48 40 50 Q54 42 72 45 Q88 48 90 64 Q91 82 66 85 L36 85 Q14 83 12 54 Z"
          fill="var(--duck)"
        />
        <circle cx="78" cy="32" r="16" fill="var(--duck)" />
        <path
          d="M38 62 Q54 55 70 63 Q60 73 43 70"
          fill="none"
          stroke={on ? "var(--cloth)" : "var(--rule)"}
          strokeWidth="2.2"
          strokeLinecap="round"
        />
      </g>
      {/* клюв — сплошной акцент */}
      <path d="M91 28 Q109 29 112 35 Q106 41 90 39 Z" className="fill-ribbon" />
      <circle cx="82.5" cy="28" r="2.4" fill="var(--cloth)" />
      <circle cx="82.5" cy="28" r="6.2" fill="none" stroke="var(--cloth)" strokeWidth="1.8" />
      <path d="M76.3 27.4 Q70 25 64 27.5" fill="none" stroke="var(--cloth)" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
