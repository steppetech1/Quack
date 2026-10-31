"use client";

import { useEffect } from "react";

/**
 * Клавиатура меню: при открытии фокус на первом пункте, ↑↓ Home End ходят по пунктам,
 * Esc закрывает и возвращает фокус на кнопку-триггер, Tab закрывает.
 */
export function useMenu(open: boolean, root: React.RefObject<HTMLElement | null>, close: () => void) {
  useEffect(() => {
    if (open) root.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
  }, [open, root]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const items = [...(root.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      root.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
      return;
    }
    if (e.key === "Tab") return close();
    const to =
      e.key === "ArrowDown" ? (i + 1) % items.length : e.key === "ArrowUp" ? (i - 1 + items.length) % items.length : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : -1;
    if (to < 0) return;
    e.preventDefault();
    items[to]?.focus();
  };

  return onKeyDown;
}
