"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/**
 * Side panels you can drag by their edge. `useResizable` keeps a panel's width (null = the panel's own CSS width, which is
 * also what a double click returns to; a number = what the learner dragged to, remembered per panel in localStorage), and
 * `<ResizeHandle>` is the strip on the panel's edge: pointer capture for the drag, arrow keys when it has focus.
 * The panel decides where the width is used (an inline style, or a CSS variable for a breakpoint) and where the handle
 * stands: the handle sits inside the panel, which must be `relative`.
 */

const PREFIX = "quack.panel-width.";
const STEP = 16;
const STEP_BIG = 64;

// what is remembered: a copy in memory (the storage may be missing or full), localStorage under it
const memory = new Map<string, number | null>();
const listeners = new Set<() => void>();
const subscribe = (f: () => void) => {
  listeners.add(f);
  return () => void listeners.delete(f);
};

function read(id: string): number | null {
  if (memory.has(id)) return memory.get(id) ?? null;
  try {
    const raw = localStorage.getItem(PREFIX + id);
    const n = raw === null ? NaN : Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

function put(id: string, width: number | null, persist: boolean) {
  memory.set(id, width);
  if (persist) {
    try {
      if (width === null) localStorage.removeItem(PREFIX + id);
      else localStorage.setItem(PREFIX + id, String(Math.round(width)));
    } catch {}
  }
  listeners.forEach((f) => f());
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

export type Resizable = {
  id: string;
  /** which edge of the panel the handle is on: a panel at the right of the screen grows to the left */
  edge: "left" | "right";
  /** `null`: the panel keeps its own width */
  width: number | null;
  min: number;
  maxOf: (panel: HTMLElement) => number;
  /** while dragging: the width is shown but not yet saved */
  set: (width: number) => void;
  /** saves what `set` showed */
  commit: () => void;
  reset: () => void;
};

export function useResizable({
  id,
  min,
  max,
  edge,
}: {
  id: string;
  min: number;
  /** the widest the panel may get; a function gets the panel (to leave room for what stands beside it) */
  max: number | ((panel: HTMLElement) => number);
  edge: "left" | "right";
}): Resizable {
  const width = useSyncExternalStore(
    subscribe,
    () => read(id),
    () => null,
  );
  const maxOf = useCallback((panel: HTMLElement) => (typeof max === "number" ? max : max(panel)), [max]);
  const set = useCallback((w: number) => put(id, w, false), [id]);
  const commit = useCallback(() => put(id, read(id), true), [id]);
  const reset = useCallback(() => put(id, null, true), [id]);
  return { id, edge, width, min, maxOf, set, commit, reset };
}

/** The strip on the edge of a panel. Hover: the cursor and a thin line; drag, or arrow keys with focus, to resize; double click for the default width */
export function ResizeHandle({ r, label, className }: { r: Resizable; label?: string; className?: string }) {
  const tx = useT();
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; w: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [m, setM] = useState({ now: r.min, max: r.min });
  const { min, edge } = r;
  const maxOf = r.maxOf;

  // aria values follow the panel, whatever changed it (a drag, the keys, the window, the panel's own CSS)
  useEffect(() => {
    const panel = ref.current?.parentElement;
    if (!panel) return;
    const measure = () => setM({ now: Math.round(panel.offsetWidth), max: Math.round(maxOf(panel)) });
    const ro = new ResizeObserver(measure);
    ro.observe(panel);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [maxOf]);

  // while dragging: no text selection, the resize cursor everywhere
  useEffect(() => {
    if (!dragging) return;
    const prev = { sel: document.body.style.userSelect, cur: document.body.style.cursor };
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      document.body.style.userSelect = prev.sel;
      document.body.style.cursor = prev.cur;
    };
  }, [dragging]);

  const panelOf = () => ref.current?.parentElement ?? null;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const panel = panelOf();
    if (!panel || e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, w: panel.offsetWidth };
    setDragging(true);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const panel = panelOf();
    const d = drag.current;
    if (!panel || !d) return;
    const dx = (e.clientX - d.x) * (edge === "left" ? -1 : 1);
    r.set(clamp(d.w + dx, min, maxOf(panel)));
  };
  const end = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setDragging(false);
    r.commit();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const panel = panelOf();
    if (!panel) return;
    const step = e.shiftKey ? STEP_BIG : STEP;
    // the arrow moves the edge itself: for a panel on the right the left edge going left makes it wider
    const toward = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    let next: number | null = null;
    if (toward) next = panel.offsetWidth + toward * step * (edge === "left" ? -1 : 1);
    else if (e.key === "Home") next = min;
    else if (e.key === "End") next = maxOf(panel);
    if (next === null) return;
    e.preventDefault();
    r.set(clamp(next, min, maxOf(panel)));
    r.commit();
  };

  return (
    <>
      <div
        ref={ref}
        role="separator"
        aria-orientation="vertical"
        aria-label={label ?? tx.workspace.resize}
        aria-valuenow={Math.min(Math.max(m.now, min), Math.max(min, m.max))}
        aria-valuemin={min}
        aria-valuemax={Math.max(min, m.max)}
        tabIndex={0}
        data-dragging={dragging || undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={end}
        onPointerCancel={end}
        onKeyDown={onKeyDown}
        onDoubleClick={r.reset}
        className={cn(
          "group absolute inset-y-0 z-20 w-3 cursor-col-resize touch-none outline-none",
          edge === "left" ? "-left-1.5" : "-right-1.5",
          className,
        )}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-[background-color,width] duration-(--dur-s) ease-(--ease-out) group-hover:w-0.5 group-hover:bg-ribbon/70 group-focus-visible:w-0.5 group-focus-visible:bg-ribbon group-data-[dragging]:w-0.5 group-data-[dragging]:bg-ribbon"
        />
      </div>
      {/* a drag over a frame (a demo, the canvas) keeps its pointer: nothing under it can steal the moves */}
      {dragging && <div aria-hidden className="fixed inset-0 z-[70] cursor-col-resize" />}
    </>
  );
}
