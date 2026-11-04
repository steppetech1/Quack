"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/**
 * Появление текста сцен. Крупные заголовки выезжают построчно из-под маски (каждая строка поднимается из-за края),
 * обычный текст выезжает отдельно — чуть позже, снизу, с проявлением. При уходе сцены с экрана всё возвращается
 * в исходное состояние и сыграет снова. Без анимации при prefers-reduced-motion.
 */

/** true, пока элемент заметно в кадре; false — когда ушёл целиком */
export function useInView<T extends HTMLElement>(threshold = 0.3) {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const io = new IntersectionObserver(
      ([e]) => {
        if (reduced || e.intersectionRatio >= threshold) setInView(true);
        else if (e.intersectionRatio === 0) setInView(false);
      },
      { threshold: [0, threshold] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return [ref, inView] as const;
}

const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";

/** Крупный заголовок: строки выезжают снизу из-под маски по очереди. `accent` — номера строк в акцентном цвете */
export function Headline({ lines, accent = [], inView, className = "", delay = 0 }: { lines: string[]; accent?: number[]; inView: boolean; className?: string; delay?: number }) {
  return (
    <h2 className={`font-sans font-bold leading-[1.04] tracking-[-0.035em] text-[#f1e4d6] ${className}`}>
      {lines.map((line, i) => {
        const style: CSSProperties = {
          transform: inView ? "translateY(0)" : "translateY(112%)",
          transition: `transform 1s ${EASE} ${delay + i * 0.12}s`,
        };
        return (
          <span key={line} className="block overflow-hidden pb-[0.08em]">
            <span className={`block ${accent.includes(i) ? "text-[#daa05f]" : ""}`} style={style}>
              {line}
            </span>
          </span>
        );
      })}
    </h2>
  );
}

/** Обычный текст: проявляется и выезжает снизу, `delay` — задержка в секундах (после заголовка) */
export function Rise({ children, inView, delay = 0, className = "", as: Tag = "div" }: { children: ReactNode; inView: boolean; delay?: number; className?: string; as?: "div" | "p" | "span" }) {
  const style: CSSProperties = {
    opacity: inView ? 1 : 0,
    transform: inView ? "translateY(0)" : "translateY(22px)",
    filter: inView ? "blur(0)" : "blur(4px)",
    transition: `opacity 0.9s ${EASE} ${delay}s, transform 0.9s ${EASE} ${delay}s, filter 0.9s ${EASE} ${delay}s`,
  };
  return (
    <Tag className={className} style={style}>
      {children}
    </Tag>
  );
}
