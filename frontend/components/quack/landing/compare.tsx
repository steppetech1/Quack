"use client";

import {
  Angry,
  Check,
  CircleQuestionMark,
  Frown,
  Heart,
  HeartCrack,
  Hourglass,
  Laugh,
  Meh,
  PartyPopper,
  Rocket,
  Search,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, type CSSProperties, type RefObject } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { CrownDuck } from "./crown-duck";

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ease = (x: number) => 1 - Math.pow(1 - clamp01(x), 3);

// переменные --t (появление, 0..1) и --dim (затемнение, 0..1) ставит скролл; без JS по умолчанию карточка видна
// --dir: -1 для левой карточки, 1 для правой — в какую сторону она выезжает и отъезжает
const BAD_STYLE = {
  opacity: "var(--t, 1)",
  transform:
    "translateX(calc(var(--dir, 1) * ((1 - var(--t, 1)) * 5rem + var(--dim, 0) * 1.25rem))) translateY(calc((1 - var(--t, 1)) * 2rem)) scale(calc(1 - var(--dim, 0) * 0.05))",
  filter: "brightness(calc(1 - var(--dim, 0) * 0.4))",
} as CSSProperties;
const GOOD_STYLE = {
  opacity: "var(--t, 1)",
  transform: "translateY(calc((1 - var(--t, 1)) * 5rem)) scale(calc(0.9 + var(--t, 1) * 0.1))",
} as CSSProperties;

type Entry = { title: string; text: string; points: string[] };
/** Реакция-стикер: значок, размер в rem и наклон */
type Sticker = [icon: LucideIcon, size: number, tilt: number];

const LECTURE_STICKERS: Sticker[] = [
  [ThumbsDown, 3.5, -8],
  [Frown, 2.25, 6],
  [CircleQuestionMark, 2.75, -4],
  [HeartCrack, 2, 10],
];
const VIDEO_STICKERS: Sticker[] = [
  [ThumbsDown, 3.5, 6],
  [Angry, 2.5, -6],
  [Search, 2.25, 8],
  [Hourglass, 2, -10],
  [Meh, 2.25, 4],
];
const QVACK_STICKERS: Sticker[] = [
  [ThumbsUp, 3.5, -6],
  [Laugh, 2.5, 7],
  [Sparkles, 2.25, -8],
  [Heart, 2, 6],
  [Rocket, 2.5, -4],
  [PartyPopper, 2.25, 9],
];

type Side = "left" | "right" | "center";

function Card({ entry, stickers, side }: { entry: Entry; stickers: Sticker[]; side: Side }) {
  const good = side === "center";
  const PointIcon = good ? Check : X;
  return (
    <article
      style={good ? GOOD_STYLE : BAD_STYLE}
      className={cn(
        "relative flex min-h-[70svh] flex-col gap-5 rounded-[6px] border p-6 font-mono sm:p-8 lg:min-h-0",
        good
          ? "border-[#ff8a2a]/45 bg-[#170d07]/95 shadow-[0_0_80px_#ff7a1a30] lg:h-[min(78vh,48rem)] lg:px-10 lg:py-9"
          : "border-[#f1e4d6]/10 bg-[#0e0907]/70 lg:h-[min(72vh,44rem)] lg:py-9",
        // боковые карточки частично прячутся за центральной: текст держим подальше от её края
        side === "left" && "lg:pr-[16%]",
        side === "right" && "lg:pl-[16%]",
      )}
    >
      {good && (
        <CrownDuck className="pointer-events-none absolute bottom-[calc(100%-0.55rem)] left-1/2 w-28 -translate-x-1/2" />
      )}
      <div aria-hidden className={cn("flex flex-wrap items-end gap-x-4 gap-y-2", good ? "text-[#ff8a2a]" : "text-st-confused")}>
        {stickers.map(([Icon, size, tilt], i) => (
          <Icon
            key={i}
            strokeWidth={1.6}
            style={{ width: `${size}rem`, height: `${size}rem`, rotate: `${tilt}deg` }}
            className={i === 0 ? "" : "opacity-75"}
          />
        ))}
      </div>
      <h2 className="text-[clamp(1.75rem,2.8vw,2.75rem)] font-extrabold leading-[1.05] tracking-[-0.04em] text-[#f6eadc]">
        {entry.title}
      </h2>
      <p className="text-[clamp(1rem,1.5vw,1.375rem)] leading-snug text-[#bba995]">{entry.text}</p>
      <ul className="mt-auto flex flex-col gap-3 text-[clamp(0.9375rem,1.25vw,1.1875rem)] leading-snug text-[#d8c8b6]">
        {entry.points.map((p) => (
          <li key={p} className="flex items-start gap-3">
            <PointIcon aria-hidden className={cn("mt-0.5 size-5 shrink-0", good ? "text-[#ff8a2a]" : "text-st-confused")} />
            {p}
          </li>
        ))}
      </ul>
    </article>
  );
}

/**
 * Второй экран лендинга: как обычно готовятся (две карточки с дизлайками) и как это делает Qvack!.
 * На десктопе блок «залипает», а скролл по очереди проявляет карточки: слева лекции, справа видео, в центре наша —
 * при её появлении боковые затемняются и уезжают в стороны. На узком экране карточки идут столбиком и проявляются по мере прокрутки.
 */
export function Compare({ scrollRef }: { scrollRef: RefObject<HTMLElement | null> }) {
  const t = useT().landing;
  const sectionRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = scrollRef.current;
    const section = sectionRef.current;
    const stage = stageRef.current;
    if (!root || !section || !stage) return;
    // три ячейки по порядку: лекции, видео, Qvack!
    const cells = Array.from(stage.children) as HTMLElement[];

    const wide = window.matchMedia("(min-width: 1024px)");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

    const set = (el: HTMLElement, t: number, dim: number) => {
      el.style.setProperty("--t", t.toFixed(3));
      el.style.setProperty("--dim", dim.toFixed(3));
    };

    const update = () => {
      const [a, b, c] = cells;
      if (reduced.matches) {
        set(a, 1, 1);
        set(b, 1, 1);
        set(c, 1, 0);
        return;
      }
      const rootTop = root.getBoundingClientRect().top;
      const vh = root.clientHeight;
      let ta: number, tb: number, tc: number;
      if (wide.matches) {
        // p: 0 — когда верх секции на середине экрана, 1 — когда «залипание» закончилось
        const top = section.getBoundingClientRect().top - rootTop;
        const start = vh * 0.5;
        const end = vh - section.offsetHeight;
        const p = clamp01((start - top) / (start - end));
        ta = ease(p / 0.25);
        tb = ease((p - 0.33) / 0.25);
        tc = ease((p - 0.66) / 0.24);
      } else {
        const appear = (el: HTMLElement) => ease((vh * 0.95 - (el.getBoundingClientRect().top - rootTop)) / (vh * 0.35));
        ta = appear(a);
        tb = appear(b);
        tc = appear(c);
      }
      set(a, ta, tc);
      set(b, tb, tc);
      set(c, tc, 0);
    };

    root.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    wide.addEventListener("change", update);
    reduced.addEventListener("change", update);
    update();

    return () => {
      root.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      wide.removeEventListener("change", update);
      reduced.removeEventListener("change", update);
    };
  }, [scrollRef]);

  const c = t.compare;

  return (
    <section ref={sectionRef} id="compare" className="relative text-[#f1e4d6] lg:h-[340vh] motion-reduce:lg:h-screen">
      <div
        ref={stageRef}
        className="flex flex-col gap-6 px-5 py-20 sm:px-8 lg:sticky lg:top-0 lg:block lg:h-screen lg:p-0"
      >
        <div style={{ "--dir": -1 } as CSSProperties} className="lg:absolute lg:left-[2%] lg:top-1/2 lg:w-[35%] lg:-translate-y-1/2">
          <Card entry={c.lecture} stickers={LECTURE_STICKERS} side="left" />
        </div>
        <div style={{ "--dir": 1 } as CSSProperties} className="lg:absolute lg:right-[2%] lg:top-1/2 lg:w-[35%] lg:-translate-y-1/2">
          <Card entry={c.video} stickers={VIDEO_STICKERS} side="right" />
        </div>
        <div className="mt-14 lg:absolute lg:left-1/2 lg:top-[58%] lg:z-10 lg:mt-0 lg:w-[41%] lg:-translate-x-1/2 lg:-translate-y-1/2">
          <Card entry={c.qvack} stickers={QVACK_STICKERS} side="center" />
        </div>
      </div>
    </section>
  );
}
