"use client";

import { useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/**
 * Горизонтально прокручиваемый блок (формула). Пока содержимое не помещается,
 * блок получает фокус с клавиатуры и имя региона, иначе остаётся обычным блоком.
 */
export function ScrollRegion({ html, label, className }: { html: string; label?: string; className?: string }) {
  const t = useT();
  label ??= t.artifacts.formula;
  const ref = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setScrolls(el.scrollWidth > el.clientWidth + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [html]);

  return (
    <div
      ref={ref}
      {...(scrolls ? { tabIndex: 0, role: "region", "aria-label": label } : {})}
      className={cn("overflow-x-auto", className)}
      // разметку KaTeX собираем сами из своих моков, пользовательский ввод сюда не попадает
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
