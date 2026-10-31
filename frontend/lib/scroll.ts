/** Ближайший прокручиваемый предок элемента */
function scroller(el: HTMLElement) {
  let box = el.parentElement;
  while (box && !/(auto|scroll)/.test(getComputedStyle(box).overflowY)) box = box.parentElement;
  return box;
}

/**
 * Прокрутить колонку к элементу, не трогая саму страницу.
 * align "end" ставит низ элемента к низу колонки — выше остаётся контекст;
 * если элемент выше колонки, он встаёт верхним краем.
 * align "bottom" всегда показывает низ элемента — для кнопки действия под ним.
 */
export function scrollToId(
  id: string,
  offset = 24,
  behavior?: ScrollBehavior,
  align: "start" | "end" | "bottom" = "start",
) {
  const el = document.getElementById(id);
  if (!el) return;
  const box = scroller(el);
  if (!box) return;
  const r = el.getBoundingClientRect();
  const b = box.getBoundingClientRect();
  const start = r.top - b.top + box.scrollTop - offset;
  const end = r.bottom - b.top + box.scrollTop - box.clientHeight + offset;
  const top = align === "end" ? Math.min(start, end) : align === "bottom" ? end : start;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  box.scrollTo({ top, behavior: behavior ?? (reduce ? "auto" : "smooth") });
}

/** Коротко обвести элемент жёлтым кольцом (стили — [data-flash] в globals.css) */
export function flash(id: string, ms = 1600) {
  const el = document.getElementById(id);
  if (!el) return;
  el.removeAttribute("data-flash");
  void el.offsetWidth;
  el.setAttribute("data-flash", "");
  window.setTimeout(() => el.removeAttribute("data-flash"), ms);
}
