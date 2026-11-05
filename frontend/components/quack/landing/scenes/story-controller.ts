import { useEffect, type RefObject } from "react";
import { smoothstep } from "./scene-utils";
import { STORY, storyPos, storyStore } from "./story";

/**
 * Управляет собранностью сцен при прокрутке (закреплённый режим).
 *  - Пока человек крутит от точки к соседней, текущая сцена рассыпается вместе с прокруткой (назад — собирается).
 *    Остановился — всё остаётся как есть, никуда не возвращается.
 *  - Прокрутил примерно треть пути — дальше сцены меняются сами и уже без прокрутки: текущая дорассыпается, следующая
 *    плавно собирается, а страница сама доезжает до своей точки.
 * Пока идёт переход (и ещё немного после) колесо, тач и инерция ничего не двигают.
 */
const DISSOLVE_END = 0.14; // доля пути, к которой текущая сцена рассыпана полностью
const COMMIT = 0.15; // доля пути, после которой переход идёт сам
const SECONDS = 1.8;
const COOLDOWN_MS = 700;

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export function useStoryController(scrollRef: RefObject<HTMLElement | null>, enabled: boolean) {
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !enabled) {
      storyStore.active = false;
      return;
    }
    const P = STORY.points;
    const forms = storyStore.forms;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const nearest = (s: number) => P.reduce((best, p, i) => (Math.abs(p - s) < Math.abs(P[best] - s) ? i : best), 0);
    storyStore.active = true;
    let cur = nearest(storyPos(root));
    forms.fill(0);
    forms[cur] = 1;
    let raf = 0;
    let animating = false;
    let lockUntil = 0;
    const notify = () => root.dispatchEvent(new Event("story-form"));
    const locked = () => animating || performance.now() < lockUntil;
    const block = (e: Event) => {
      if (locked() && e.cancelable) e.preventDefault();
    };

    const commit = (tgt: number) => {
      cancelAnimationFrame(raf);
      const from = forms.slice();
      const scrollFrom = root.scrollTop;
      const scrollTo = P[tgt] * root.clientHeight;
      const settle = () => {
        forms.fill(0);
        forms[tgt] = 1;
        cur = tgt;
        root.scrollTop = scrollTo;
        animating = false;
        lockUntil = performance.now() + COOLDOWN_MS;
        notify();
      };
      if (reduced) {
        settle();
        return;
      }
      animating = true;
      const t0 = performance.now();
      const dur = SECONDS * 1000;
      const step = (now: number) => {
        const k = Math.min(1, (now - t0) / dur);
        root.scrollTop = scrollFrom + (scrollTo - scrollFrom) * easeInOut(k);
        for (let i = 0; i < forms.length; i++) {
          if (i === tgt) forms[i] = from[i] + (1 - from[i]) * smoothstep(0.2, 0.8, k); // новая собирается после того, как старая ушла
          else forms[i] = from[i] * (1 - smoothstep(0, 0.4, k));
        }
        notify();
        if (k < 1) raf = requestAnimationFrame(step);
        else settle();
      };
      raf = requestAnimationFrame(step);
    };

    const onScroll = () => {
      if (locked()) return;
      const s = storyPos(root);
      const a = P[cur];
      const d = s - a;
      forms.fill(0);
      const tgt = cur + (d > 0 ? 1 : -1);
      if (Math.abs(d) < 0.005 || tgt < 0 || tgt >= P.length) {
        forms[cur] = 1; // после последней сцены (подвал) и до первой прокрутка свободная
        notify();
        return;
      }
      const t = Math.abs(d) / Math.abs(P[tgt] - a);
      forms[cur] = 1 - smoothstep(0.02, DISSOLVE_END, t);
      notify();
      if (t >= COMMIT) commit(tgt);
    };
    const onGo = (e: Event) => {
      const idx = (e as CustomEvent<number>).detail;
      if (typeof idx === "number") commit(Math.max(0, Math.min(P.length - 1, idx)));
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    root.addEventListener("story-go", onGo);
    root.addEventListener("wheel", block, { passive: false });
    root.addEventListener("touchmove", block, { passive: false });
    notify();
    return () => {
      cancelAnimationFrame(raf);
      root.removeEventListener("scroll", onScroll);
      root.removeEventListener("story-go", onGo);
      root.removeEventListener("wheel", block);
      root.removeEventListener("touchmove", block);
      storyStore.active = false;
    };
  }, [scrollRef, enabled]);
}
