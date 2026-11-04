"use client";

import { Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { layoutDomain } from "@/lib/graph-layout";
import { layoutGraph, loadPositions } from "@/lib/graph/layout";
import { getGraphModel, subscribeGraph } from "@/lib/graph/mock";
import type { GraphModel } from "@/lib/graph/types";
import { LEARNER } from "@/lib/graph/visit-store";
import { useLocale, useT } from "@/lib/i18n/provider";
import type { Domain, TopicState } from "@/lib/mock/types";
import { useShell } from "./app-shell";
import { type Colors, readColors, stateOf } from "./graph/layered-graph";
import { IconButton } from "./icon-button";

const K_MIN = 0.04;
const K_MAX = 2.4;
const FIT_K_MAX = 1;
const FIT_MARGIN = 72;
/** Empty world space between two graphs */
const GAP = 420;
const TWEEN_MS = 380;
/** Height of the heading over the canvas, px */
const HEADING_H = 96;

type View = { k: number; tx: number; ty: number };

/** One subject's graph in its own world units, taken from the graph model (mocks) or, without one, from the subject's topics */
type Shape = {
  dots: { x: number; y: number; state: TopicState }[];
  rings: { x: number; y: number }[];
  /** SVG path data per kind of line, so a graph is stroked three times, not once per edge */
  solid: string;
  faint: string;
  dashed: string;
  x0: number;
  x1: number;
  y0: number;
  y1: number;
};

function shapeOf(model: GraphModel | undefined, domain: Domain): Shape | null {
  let dots: Shape["dots"] = [];
  let rings: Shape["rings"] = [];
  const solid: string[] = [];
  const faint: string[] = [];
  const dashed: string[] = [];
  if (model) {
    const layout = layoutGraph(model, loadPositions(LEARNER, model.domainId));
    const byId = new Map(model.topics.map((t) => [t.id, t]));
    dots = layout.topics.map((n) => ({ x: n.x, y: n.y, state: stateOf(byId.get(n.id)!, model.nowMs) }));
    rings = layout.hubs.map((n) => ({ x: n.x, y: n.y }));
    for (const e of model.requires) {
      const a = layout.nodes.get(e.requiresId);
      const b = layout.nodes.get(e.topicId);
      if (a && b) solid.push(`M${a.x} ${a.y}L${b.x} ${b.y}`);
    }
    // a hub reaches the topics it includes: faint, so the chains stay the picture
    for (const l of model.includes) {
      const a = layout.nodes.get(`h:${l.hubId}`);
      const b = layout.nodes.get(l.topicId);
      if (a && b) faint.push(`M${a.x} ${a.y}L${b.x} ${b.y}`);
    }
  } else {
    const layout = layoutDomain(domain);
    dots = layout.topics.map((t) => ({ x: t.x, y: t.y, state: t.state }));
    rings = layout.hubs.map((h) => ({ x: h.x, y: h.y }));
    for (const e of layout.edges) (e.kind === "related" ? dashed : e.kind === "cross" ? faint : solid).push(e.d);
  }
  const pts = [...dots, ...rings];
  if (!pts.length) return null;
  return {
    dots,
    rings,
    solid: solid.join(""),
    faint: faint.join(""),
    dashed: dashed.join(""),
    x0: Math.min(...pts.map((p) => p.x)),
    x1: Math.max(...pts.map((p) => p.x)),
    y0: Math.min(...pts.map((p) => p.y)),
    y1: Math.max(...pts.map((p) => p.y)),
  };
}

type Placed = { domain: Domain; shape: Shape; ox: number; oy: number };

const hash01 = (id: string) => {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 9973;
  return h / 9973;
};

/** Graphs go on a loose grid (one column on a tall, narrow screen), the last row centred; a small steady nudge keeps it from looking ruled */
function place(items: { domain: Domain; shape: Shape }[], portrait: boolean): Placed[] {
  const n = items.length;
  if (!n) return [];
  const cols = portrait ? 1 : Math.ceil(Math.sqrt(n));
  const rows = Math.ceil(n / cols);
  const cw = Math.max(...items.map((i) => i.shape.x1 - i.shape.x0)) + GAP;
  const ch = Math.max(...items.map((i) => i.shape.y1 - i.shape.y0)) + GAP;
  return items.map((it, i) => {
    const c = i % cols;
    const r = Math.floor(i / cols);
    const inRow = r === rows - 1 ? n - r * cols : cols;
    const cx = (c - (inRow - 1) / 2) * cw + (hash01(it.domain.id) - 0.5) * GAP * 0.3;
    const cy = (r - (rows - 1) / 2) * ch + (hash01(it.domain.id + "y") - 0.5) * GAP * 0.3;
    return { ...it, ox: cx - (it.shape.x0 + it.shape.x1) / 2, oy: cy - (it.shape.y0 + it.shape.y1) / 2 };
  });
}

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** First screen: the graphs of all subjects on one canvas, same ground as the domain view. A click on a graph opens the subject */
export function AllGraphs() {
  const t = useT();
  const { locale } = useLocale();
  const { domains } = useData();
  const { selectDomain } = useShell();

  // Mock models change by hand in development: re-place when they do
  const [rev, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => subscribeGraph(bump), []);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const portrait = size.w > 0 && size.w < size.h * 0.75;
  const placed = useMemo(() => {
    void rev;
    const items = domains
      .filter((d) => d.id)
      .map((domain) => ({ domain, shape: shapeOf(API_MODE ? undefined : getGraphModel(locale, domain.id), domain) }))
      .filter((i): i is { domain: Domain; shape: Shape } => !!i.shape);
    return place(items, portrait);
  }, [domains, locale, rev, portrait]);

  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef(size);
  const view = useRef<View>({ k: 1, tx: 0, ty: 0 });
  const tween = useRef<{ start: number; from: View; to: View } | null>(null);
  const colors = useRef<Colors | null>(null);
  const hover = useRef<string | null>(null);
  const placedRef = useRef(placed);
  const paths = useRef(new WeakMap<Shape, { solid: Path2D; faint: Path2D; dashed: Path2D }>());
  const raf = useRef(0);
  const frame = useRef<() => void>(() => {});
  const fittedFor = useRef("");

  const schedule = useCallback(() => {
    if (!raf.current) raf.current = requestAnimationFrame(() => frame.current());
  }, []);

  const draw = useCallback(() => {
    const c = canvas.current;
    const ctx = c?.getContext("2d");
    const col = colors.current;
    const { w, h } = sizeRef.current;
    if (!c || !ctx || !col || !w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const v = view.current;
    const k = v.k;
    const hov = hover.current;
    const ringR = clamp(13 * Math.pow(k, 0.6), 5, 15);
    const dotR = clamp(5 * Math.pow(k, 0.5), 2.2, 6);
    const fill: Record<TopicState, string> = { solid: col.solid, review: col.review, confused: col.confused, new: col.new };
    for (const p of placedRef.current) {
      const s = p.shape;
      const sx = (x: number) => v.tx + (p.ox + x) * k;
      const sy = (y: number) => v.ty + (p.oy + y) * k;
      if (sx(s.x1) < -120 || sx(s.x0) > w + 120 || sy(s.y1) < -120 || sy(s.y0) > h + 120) continue;
      const a = hov && hov !== p.domain.id ? 0.45 : 1;

      let ps = paths.current.get(s);
      if (!ps) {
        ps = { solid: new Path2D(s.solid), faint: new Path2D(s.faint), dashed: new Path2D(s.dashed) };
        paths.current.set(s, ps);
      }
      // lines are stroked in world space with a width that stays the same on screen
      ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * (v.tx + p.ox * k), dpr * (v.ty + p.oy * k));
      ctx.strokeStyle = col.dim;
      ctx.lineWidth = 1.4 / k;
      ctx.globalAlpha = a * 0.6;
      ctx.stroke(ps.solid);
      ctx.globalAlpha = a * 0.25;
      ctx.stroke(ps.faint);
      ctx.setLineDash([1.2 / k, 4 / k]);
      ctx.stroke(ps.dashed);
      ctx.setLineDash([]);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      ctx.globalAlpha = a;
      for (const d of s.dots) {
        ctx.fillStyle = fill[d.state];
        ctx.beginPath();
        ctx.arc(sx(d.x), sy(d.y), dotR, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.strokeStyle = col.paper;
      ctx.lineWidth = 1.5;
      for (const r of s.rings) {
        ctx.beginPath();
        ctx.arc(sx(r.x), sy(r.y), ringR, 0, Math.PI * 2);
        ctx.stroke();
      }

      // the name of the subject, above its graph
      const spaced = ctx as CanvasRenderingContext2D & { letterSpacing?: string };
      ctx.font = `600 22px ${col.body}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.fillStyle = col.paper;
      ctx.globalAlpha = hov && hov !== p.domain.id ? 0.6 : 1;
      if ("letterSpacing" in spaced) spaced.letterSpacing = "0.04em";
      ctx.fillText(p.domain.title.toUpperCase(), (sx(s.x0) + sx(s.x1)) / 2, sy(s.y0) - ringR - 28);
      if ("letterSpacing" in spaced) spaced.letterSpacing = "0px";
    }
    ctx.globalAlpha = 1;
  }, []);

  useEffect(() => {
    frame.current = () => {
      raf.current = 0;
      const tw = tween.current;
      if (tw) {
        const p = clamp((performance.now() - tw.start) / TWEEN_MS, 0, 1);
        const e = 1 - (1 - p) ** 3;
        const { from, to } = tw;
        view.current = { k: from.k + (to.k - from.k) * e, tx: from.tx + (to.tx - from.tx) * e, ty: from.ty + (to.ty - from.ty) * e };
        if (p >= 1) tween.current = null;
        else schedule();
      }
      draw();
    };
  }, [draw, schedule]);

  const moveView = useCallback(
    (to: View, animate: boolean) => {
      if (animate && !reducedMotion()) tween.current = { start: performance.now(), from: view.current, to };
      else {
        tween.current = null;
        view.current = to;
      }
      schedule();
    },
    [schedule],
  );

  const fitView = useCallback((): View => {
    const { w, h } = sizeRef.current;
    const all = placedRef.current;
    if (!all.length || !w || !h) return { k: 1, tx: w / 2, ty: h / 2 };
    const x0 = Math.min(...all.map((p) => p.ox + p.shape.x0));
    const x1 = Math.max(...all.map((p) => p.ox + p.shape.x1));
    const y0 = Math.min(...all.map((p) => p.oy + p.shape.y0));
    const y1 = Math.max(...all.map((p) => p.oy + p.shape.y1));
    // The heading takes the top of the canvas, more of it on a narrow screen
    const top = w < h * 0.75 ? HEADING_H : HEADING_H * 0.75;
    const k = clamp(Math.min((w - FIT_MARGIN * 2) / Math.max(1, x1 - x0), (h - top - FIT_MARGIN * 2) / Math.max(1, y1 - y0)), K_MIN, FIT_K_MAX);
    // a little lower than the middle: the names stand above the graphs
    return { k, tx: w / 2 - ((x0 + x1) / 2) * k, ty: (h + top) / 2 - ((y0 + y1) / 2) * k + 10 };
  }, []);

  const zoomAt = useCallback(
    (cx: number, cy: number, factor: number) => {
      const v = tween.current?.to ?? view.current;
      const k = clamp(v.k * factor, K_MIN, K_MAX);
      const f = k / v.k;
      moveView({ k, tx: cx - (cx - v.tx) * f, ty: cy - (cy - v.ty) * f }, false);
    },
    [moveView],
  );

  // Size and colours
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    colors.current = readColors(el);
    const measure = () => {
      const r = el.getBoundingClientRect();
      setSize({ w: Math.round(r.width), h: Math.round(r.height) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    sizeRef.current = size;
    placedRef.current = placed;
    const c = canvas.current;
    if (!c || !size.w || !size.h) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(size.w * dpr);
    c.height = Math.round(size.h * dpr);
    // The first view, and again when the set of graphs changes: everything in sight
    const sig = placed.map((p) => `${p.domain.id}:${p.shape.dots.length}:${Math.round(p.ox)}:${Math.round(p.oy)}`).join("|");
    if (fittedFor.current !== sig) {
      fittedFor.current = sig;
      view.current = fitView();
      tween.current = null;
    }
    draw();
  }, [size, placed, fitView, draw]);

  useEffect(
    () => () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    },
    [],
  );

  // Wheel zoom needs a non-passive listener
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0016));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const setHover = useCallback(
    (id: string | null) => {
      if (hover.current === id) return;
      hover.current = id;
      if (canvas.current) canvas.current.style.cursor = id ? "pointer" : "grab";
      schedule();
    },
    [schedule],
  );

  /** The graph under a screen point: inside its box, a little padded */
  const hitTest = (x: number, y: number) => {
    const v = view.current;
    const pad = 36 / v.k;
    const wx = (x - v.tx) / v.k;
    const wy = (y - v.ty) / v.k;
    let best: { id: string; d: number } | null = null;
    for (const p of placedRef.current) {
      const { x0, x1, y0, y1 } = p.shape;
      const ax = p.ox + x0, bx = p.ox + x1, ay = p.oy + y0, by = p.oy + y1;
      if (wx < ax - pad || wx > bx + pad || wy < ay - pad || wy > by + pad) continue;
      const d = Math.hypot(wx - (ax + bx) / 2, wy - (ay + by) / 2);
      if (!best || d < best.d) best = { id: p.domain.id, d };
    }
    return best?.id ?? null;
  };

  // Pointers: one drags the canvas, two pinch it. A press that did not move is a click
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const press = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const local = (e: React.PointerEvent) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = local(e);
    pointers.current.set(e.pointerId, p);
    tween.current = null;
    press.current = pointers.current.size === 1 ? { ...p, moved: false } : null;
    e.currentTarget.style.cursor = "grabbing";
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = local(e);
    const prev = pointers.current.get(e.pointerId);
    if (!prev) {
      setHover(e.pointerType === "mouse" ? hitTest(p.x, p.y) : null);
      return;
    }
    const v = view.current;
    if (pointers.current.size === 2) {
      const other = [...pointers.current].find(([id]) => id !== e.pointerId)![1];
      const before = Math.hypot(prev.x - other.x, prev.y - other.y) || 1;
      const after = Math.hypot(p.x - other.x, p.y - other.y);
      const k = clamp(v.k * (after / before), K_MIN, K_MAX);
      const f = k / v.k;
      const mx = (p.x + other.x) / 2, my = (p.y + other.y) / 2;
      const pmx = (prev.x + other.x) / 2, pmy = (prev.y + other.y) / 2;
      moveView({ k, tx: mx - (pmx - v.tx) * f, ty: my - (pmy - v.ty) * f }, false);
    } else {
      if (press.current && Math.hypot(p.x - press.current.x, p.y - press.current.y) > 4) press.current.moved = true;
      moveView({ ...v, tx: v.tx + p.x - prev.x, ty: v.ty + p.y - prev.y }, false);
    }
    pointers.current.set(e.pointerId, p);
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = local(e);
    pointers.current.delete(e.pointerId);
    const pr = press.current;
    press.current = null;
    const id = hitTest(p.x, p.y);
    if (pr && !pr.moved && id) selectDomain(id);
    e.currentTarget.style.cursor = e.pointerType === "mouse" && id ? "pointer" : "grab";
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;
    const v = view.current;
    const { w, h } = sizeRef.current;
    const pan = (dx: number, dy: number) => moveView({ ...v, tx: v.tx + dx, ty: v.ty + dy }, false);
    const map: Record<string, () => void> = {
      ArrowLeft: () => pan(60, 0),
      ArrowRight: () => pan(-60, 0),
      ArrowUp: () => pan(0, 60),
      ArrowDown: () => pan(0, -60),
      "+": () => zoomAt(w / 2, h / 2, 1.25),
      "=": () => zoomAt(w / 2, h / 2, 1.25),
      "-": () => zoomAt(w / 2, h / 2, 0.8),
      "0": () => moveView(fitView(), true),
    };
    const run = map[e.key];
    if (!run) return;
    e.preventDefault();
    run();
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={wrap}
        tabIndex={0}
        role="application"
        aria-label={t.allGraphs.aria}
        onKeyDown={onKeyDown}
        className="absolute inset-0 overflow-hidden outline-none select-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ribbon/60"
      >
        <canvas
          ref={canvas}
          aria-hidden
          className="absolute inset-0 size-full cursor-grab touch-none"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={() => !pointers.current.size && setHover(null)}
        />
        {/* keyboard and screen readers: each graph is a button, focus lights it on the canvas */}
        <ul className="sr-only">
          {placed.map((p) => (
            <li key={p.domain.id}>
              <button type="button" onClick={() => selectDomain(p.domain.id)} onFocus={() => setHover(p.domain.id)} onBlur={() => setHover(null)}>
                {t.allGraphs.open(p.domain.title)}
              </button>
            </li>
          ))}
        </ul>
        {placed.length === 0 && <p className="caps absolute inset-0 grid place-items-center text-paper-dim">{t.allGraphs.empty}</p>}
      </div>

      <header className="pointer-events-none absolute top-4 left-4 flex flex-col gap-1 md:top-5 md:left-7">
        <h1 className="display-caps text-[clamp(1.75rem,2.6vw,2.25rem)] text-paper">{t.allGraphs.title}</h1>
        <p className="caps max-w-xs text-paper-dim">{t.allGraphs.lead}</p>
      </header>

      <div role="group" aria-label={t.graph.zoomGroup} className="absolute right-4 bottom-4 flex rounded-pill border border-seam bg-cloth-deep/90 md:right-7">
        <IconButton label={t.graph.zoomIn} onClick={() => zoomAt(size.w / 2, size.h / 2, 1.25)}>
          <Plus />
        </IconButton>
        <IconButton label={t.graph.zoomOut} onClick={() => zoomAt(size.w / 2, size.h / 2, 0.8)}>
          <Minus />
        </IconButton>
        <IconButton label={t.graph.fitAll} onClick={() => moveView(fitView(), true)}>
          <Maximize2 />
        </IconButton>
      </div>
    </div>
  );
}
