import { useSyncExternalStore } from "react";

/**
 * Закреплённые сцены. Первый экран и две сцены лежат друг на друге в одной неподвижной рамке (sticky). Прокрутка тут
 * только рассыпает текущую сцену; когда человек прокрутил достаточно, следующая сцена сама, плавно и уже без прокрутки,
 * собирается на том же месте (см. story-controller.ts). Если остановиться раньше, сцена остаётся рассыпанной как есть.
 * Собранность каждой сцены (0..1) лежит в `storyStore.forms`, сцены и первый экран читают её каждый кадр.
 */
export const STORY = {
  /** точки остановок в высотах окна (s = scrollTop / высота окна): сцена полностью собрана */
  points: [0, 1.25, 2.55],
  /** до какого s рамка остаётся на месте; дальше страница едет к подвалу */
  end: 2.7,
  /** номера сцен по якорям */
  anchors: { top: 0, efficiency: 1, how: 2 } as Record<string, number>,
};

/** Собранность сцен: [первый экран, эффективность и память, как работает]. Пишет контроллер, читают сцены */
export const storyStore = { forms: [1, 0, 0] as number[], active: false };

/** Закреплённый режим включается, когда окно хотя бы вдвое ниже полного экрана и не телефон; на телефоне — обычная прокрутка */
const PIN_QUERY = "(min-width: 720px) and (min-height: 560px)";

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", cb);
      return () => mq.removeEventListener("change", cb);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
export const usePinned = () => useMedia(PIN_QUERY);

/** Положение прокрутки в высотах окна */
export function storyPos(root: HTMLElement): number {
  return root.scrollTop / Math.max(1, root.clientHeight);
}

/** Якорная ссылка: в закреплённом режиме просит контроллер доехать до сцены, иначе обычный переход к секции */
export function goToScene(root: HTMLElement | null, id: string, e?: { preventDefault: () => void }) {
  if (!root) return;
  if (root.dataset.pinned === "true" && id in STORY.anchors) {
    e?.preventDefault();
    root.dispatchEvent(new CustomEvent("story-go", { detail: STORY.anchors[id] }));
  } else if (id === "top") {
    e?.preventDefault();
    root.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
}

/** onClick для якорной ссылки вне страницы-контейнера (подвал, панель): находит контейнер сам */
export function onAnchor(id: string) {
  return (e: { preventDefault: () => void }) => goToScene(document.querySelector<HTMLElement>("main[data-pinned]"), id, e);
}
