"use client";

import { Maximize2, Minus, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { hubLabelPx, layoutDomain, type GraphLayout, type GraphMode, type PlacedHub, type PlacedTopic } from "@/lib/graph-layout";
import { useFmt, useT } from "@/lib/i18n/provider";
import type { Domain } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { Duck } from "./duck";
import { IconButton } from "./icon-button";
import { STATE_COLOR, StateDot, TopicCardBody } from "./topic-card";

type View = { k: number; tx: number; ty: number };
export type GraphHandle = { focusTopic: (id: string) => void };

const K_MIN = 0.08;
const K_MAX = 2.6;
type Margin = { frac: number; min: number };
const DEFAULT_MARGIN: Margin = { frac: 0.09, min: 48 };
/** Показать весь граф: поля ~40px */
const ALL_MARGIN: Margin = { frac: 0, min: 40 };
const fitMargin = (n: number, m: Margin) => Math.max(m.min, n * m.frac);
const FIT_K_MAX = 1.5;
const CARD_W = 320;
type Anchor = "r" | "l" | "t" | "b";
const TWEEN_MS = 450;
const OVERVIEW: GraphMode = { kind: "overview" };

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const clampK = (k: number) => clamp(k, K_MIN, K_MAX);
const modeKeyOf = (m: GraphMode) => (m.kind === "hub" ? `hub:${m.hubId}` : m.kind);
const clean = (s: string) => s.replace(/­/g, "");

type Ext = { x0: number; x1: number; y0: number; y1: number };

type Prep = {
  topics: { x: number; y: number; hub: number }[];
  hubs: { index: number; x: number; y: number; l: number; r: number; t: number; b: number }[];
  outside: { x: number; y: number; hub: number | undefined; l: number; r: number }[];
};
const prepCache = new WeakMap<GraphLayout, Prep>();

/** Всё, что не зависит от масштаба, считаем один раз на раскладку: extent вызывается сотни раз при подборе ячеек */
function prepare(layout: GraphLayout, domain: Domain): Prep {
  let p = prepCache.get(layout);
  if (p) return p;
  const hubOf = new Map(layout.topics.map((t) => [t.id, t.hubIndex]));
  p = {
    topics: layout.topics.map((t) => ({ x: t.x, y: t.y, hub: t.hubIndex })),
    hubs: layout.hubs.map((h) => {
      const hub = domain.hubs[h.index];
      const lw = hubLabelPx(hub);
      return { index: h.index, x: h.x, y: h.y, l: h.labelSide === -1 ? lw : 18, r: h.labelSide === 1 ? lw : 18, t: h.labelBelow ? 18 : hub.deadline ? 46 : 26, b: h.labelBelow ? (hub.deadline ? 46 : 26) : 18 };
    }),
    outside: layout.outside.map((o) => {
      const right = Math.cos(o.angle) >= -0.2;
      const w = 14 + o.title.length * 8.7 + 22 + o.fromDomain.length * 6.6;
      return { x: o.x, y: o.y, hub: hubOf.get(o.linkedTo), l: right ? 8 : w, r: right ? w : 8 };
    }),
  };
  prepCache.set(layout, p);
  return p;
}

/** Экранная рамка содержимого при масштабе k: мировые точки плюс подписи постоянного размера в пикселях */
function extent(layout: GraphLayout, domain: Domain, k: number, only?: number[], hubOnly: number[] = []): Ext {
  const e: Ext = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
  const add = (x: number, y: number, l: number, r: number, t: number, b: number) => {
    e.x0 = Math.min(e.x0, x * k - l);
    e.x1 = Math.max(e.x1, x * k + r);
    e.y0 = Math.min(e.y0, y * k - t);
    e.y1 = Math.max(e.y1, y * k + b);
  };
  const p = prepare(layout, domain);
  const use = (i: number | undefined) => !only || (i !== undefined && only.includes(i));
  p.topics.forEach((t) => use(t.hub) && add(t.x, t.y, 12, 12, 12, 12));
  p.hubs.forEach((h) => {
    if (!use(h.index) && !hubOnly.includes(h.index)) return;
    add(h.x, h.y, h.l, h.r, h.t, h.b);
  });
  p.outside.forEach((o) => use(o.hub) && add(o.x, o.y, o.l, o.r, 12, 12));
  return e;
}

/** Максимальный масштаб, при котором содержимое помещается в область с полями; по осям отдельно и вместе */
function fitK(layout: GraphLayout, domain: Domain, w: number, h: number, only?: number[], hubOnly?: number[], mg: Margin = DEFAULT_MARGIN) {
  const solve = (axis: "x" | "y" | "xy") => {
    let lo = K_MIN, hi = FIT_K_MAX;
    const ok = (k: number) => {
      const e = extent(layout, domain, k, only, hubOnly);
      return (axis === "y" || e.x1 - e.x0 <= w - fitMargin(w, mg) * 2) && (axis === "x" || e.y1 - e.y0 <= h - fitMargin(h, mg) * 2);
    };
    if (ok(hi)) return hi;
    for (let i = 0; i < 22; i++) {
      const m = (lo + hi) / 2;
      if (ok(m)) lo = m;
      else hi = m;
    }
    return lo;
  };
  return { k: solve("xy"), kx: solve("x"), ky: solve("y") };
}

/** Подписи не гаснут ниже этого: контраст текста ≥ 4.5:1 на коленкоре */
const LABEL_FLOOR = 0.85;
/** Свободные темы не гаснут ниже этого */
const FREE_OP = 0.3;
/** Свободные темы в обзоре и при связи с фокусной главой: как яркий кластер × 0.7 */
const FREE_OP_LIT = 0.7;

const fade = "transition-opacity duration-300 ease-out motion-reduce:transition-none";

export const DomainGraph = forwardRef<
  GraphHandle,
  { domain: Domain; mode: GraphMode; onModeChange: (m: GraphMode) => void }
>(function DomainGraph({ domain, mode, onModeChange }, ref) {
  const tx = useT();
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const layout = useMemo(() => layoutDomain(domain), [domain]);
  const byId = useMemo(() => new Map(layout.topics.map((t) => [t.id, t])), [layout]);
  const hubIndex = useMemo(() => new Map(domain.hubs.map((h, i) => [h.id, i])), [domain]);

  const router = useRouter();
  const [view, setView] = useState<View>({ k: 1, tx: 0, ty: 0 });
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [hoverHub, setHoverHubRaw] = useState<number | null>(null);
  const [found, setFound] = useState<{ id: string; n: number } | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const viewRef = useRef(view);
  const tween = useRef(0);
  const modeRef = useRef(mode);
  const lastMode = useRef<string | null>(null);
  const wantAll = useRef(false);
  const touched = useRef(false);

  const modeKey = modeKeyOf(mode);
  const focusIdx = mode.kind === "hub" ? (hubIndex.get(mode.hubId) ?? -1) : -1;

  const setHoverHub = setHoverHubRaw;

  const fitView = useCallback(
    (w: number, h: number, only?: number[], hubOnly?: number[], mg: Margin = ALL_MARGIN): View => {
      const { k } = fitK(layout, domain, w, h, only, hubOnly, mg);
      const e = extent(layout, domain, k, only, hubOnly);
      return { k, tx: (w - (e.x1 - e.x0)) / 2 - e.x0, ty: (h - (e.y1 - e.y0)) / 2 - e.y0 };
    },
    [layout, domain],
  );

  // Две главы, которыми занимался последними
  const recent = useMemo(() => {
    const dated = domain.hubs.map((h, i) => ({ i, d: h.lastStudied })).filter((x): x is { i: number; d: string } => !!x.d);
    if (dated.length < 2) return [];
    return dated.sort((a, b) => b.d.localeCompare(a.d)).slice(0, 2).map((x) => x.i);
  }, [domain]);

  // С какими главами связана свободная тема (по рёбрам «нужно до» и «похоже»)
  const freeLinks = useMemo(() => {
    const m = new Map<string, Set<number>>();
    layout.edges.forEach((e) => {
      if (e.kind === "hub") return;
      const add = (free: string, other: number) => {
        if (other < 0) return;
        const set = m.get(free) ?? new Set<number>();
        set.add(other);
        m.set(free, set);
      };
      if (e.ha < 0) add(e.a, e.hb);
      if (e.hb < 0) add(e.b, e.ha);
    });
    return m;
  }, [layout]);

  // Кадр вокруг набора объектов: центр кадра на их центроиде, постоянные экранные подписи учтены полями в px
  type FramePt = { x: number; y: number; l: number; r: number; t: number; b: number };
  const frameOn = useCallback((pts: FramePt[], w: number, h: number, mx: number, my: number, kCap: number, kRead = 0): View => {
    const kMin = K_MIN;
    const cx = pts.reduce((sum, p) => sum + p.x, 0) / pts.length;
    const cy = pts.reduce((sum, p) => sum + p.y, 0) / pts.length;
    const hw = w / 2 - mx, hh = h / 2 - my;
    let k = kCap;
    pts.forEach((p) => {
      const dl = cx - p.x, dr = p.x - cx, du = cy - p.y, dd = p.y - cy;
      // (расстояние от центроида) · k + поле подписи ≤ половина кадра
      if (dl > 0.01) k = Math.min(k, (hw - p.l) / dl);
      if (dr > 0.01) k = Math.min(k, (hw - p.r) / dr);
      if (du > 0.01) k = Math.min(k, (hh - p.t) / du);
      if (dd > 0.01) k = Math.min(k, (hh - p.b) / dd);
    });
    // рамка объектов при масштабе z (подписи постоянного размера)
    const box = (z: number) => ({
      x0: Math.min(...pts.map((p) => p.x * z - p.l)),
      x1: Math.max(...pts.map((p) => p.x * z + p.r)),
      y0: Math.min(...pts.map((p) => p.y * z - p.t)),
      y1: Math.max(...pts.map((p) => p.y * z + p.b)),
    });
    // подписи тем читаются, только если кадр не мельче kRead; если это чуть не влезает — подпираем по центру рамки
    const fitsBox = (z: number) => {
      const e = box(z);
      return e.x1 - e.x0 <= w - 2 * mx && e.y1 - e.y0 <= h - 2 * my;
    };
    let kb = kMin;
    if (fitsBox(kCap)) kb = kCap;
    else {
      let lo = K_MIN, hi = kCap;
      for (let i = 0; i < 20; i++) {
        const m = (lo + hi) / 2;
        if (fitsBox(m)) lo = m;
        else hi = m;
      }
      kb = lo;
    }
    k = clampK(Math.max(k, Math.min(kRead, kb)));
    let tx = w / 2 - cx * k, ty = h / 2 - cy * k;
    // центроид по центру, но рамка не выходит за поля, если она в них помещается
    const e = box(k);
    if (e.x1 - e.x0 <= w - 2 * mx) tx = Math.min(w - mx - e.x1, Math.max(mx - e.x0, tx));
    if (e.y1 - e.y0 <= h - 2 * my) ty = Math.min(h - my - e.y1, Math.max(my - e.y0, ty));
    return { k, tx, ty };
  }, []);

  /** Объекты главы: нод с подписью, веер тем, связанные свободные темы и темы вне домена */
  const hubPoints = useCallback(
    (hubs: number[], withFree = true): FramePt[] => {
      const pts: FramePt[] = [];
      hubs.forEach((i) => {
        const hub = layout.hubs[i];
        const lw = hubLabelPx(domain.hubs[i]);
        const tall = domain.hubs[i].deadline ? 46 : 26;
        pts.push({ x: hub.x, y: hub.y, l: hub.labelSide === -1 ? lw : 18, r: hub.labelSide === 1 ? lw : 18, t: hub.labelBelow ? 18 : tall, b: hub.labelBelow ? tall : 18 });
      });
      const topicPt = (t: PlacedTopic) => {
        const right = Math.cos(t.angle) >= -0.2;
        pts.push({ x: t.x, y: t.y, l: right ? 14 : 140, r: right ? 140 : 14, t: 24, b: 24 });
      };
      layout.topics.forEach((t) => {
        if (hubs.includes(t.hubIndex)) topicPt(t);
        else if (withFree && t.hubIndex < 0 && hubs.length === 1 && freeLinks.get(t.id)?.has(hubs[0])) topicPt(t);
      });
      layout.outside.forEach((o) => {
        const hi = byId.get(o.linkedTo)?.hubIndex;
        if (hi === undefined || !hubs.includes(hi)) return;
        const right = Math.cos(o.angle) >= -0.2;
        pts.push({ x: o.x, y: o.y, l: right ? 10 : 170, r: right ? 170 : 10, t: 14, b: 14 });
      });
      return pts;
    },
    [layout, domain, byId, freeLinks],
  );

  // Одна глава: веер, нод, подпись и связанные свободные темы; центр кадра на их центроиде, поля ≥ 48px
  const hubView = useCallback(
    (i: number, w: number, h: number): View => {
      // сначала веер вместе со связанными свободными темами; если из-за них подписи мельчают, кадр только по вееру
      const all = frameOn(hubPoints([i]), w, h, 48, 48, 1.8, 1.05);
      return all.k >= 0.95 ? all : frameOn(hubPoints([i], false), w, h, 48, 48, 1.8, 1.05);
    },
    [frameOn, hubPoints],
  );

  const targetFor = useCallback(
    (m: GraphMode, w: number, h: number): View => {
      const i = m.kind === "hub" ? domain.hubs.findIndex((x) => x.id === m.hubId) : -1;
      if (i >= 0) return hubView(i, w, h);
      if (!recent.length) return fitView(w, h);
      // пара недавних глав занимает ~2/3 поля; остальные кластеры уходят к краю и приглушены.
      // Если главы пары не соседи в цепочке — только самая свежая
      const core = recent.length === 2 && Math.abs(recent[0] - recent[1]) > 1 ? [recent[0]] : [...recent];
      // центр кадра на центроиде объектов, поля 15% с каждой стороны: пара занимает ~70% по большей оси
      return frameOn(hubPoints(core), w, h, Math.max(48, w * 0.15), Math.max(48, h * 0.15), 1.8);
    },
    [domain, hubView, fitView, recent, frameOn, hubPoints],
  );

  const commit = useCallback((v: View) => {
    viewRef.current = v;
    setView(v);
  }, []);

  const cancelTween = useCallback(() => cancelAnimationFrame(tween.current), []);

  // Камера плавно едет к цели; при reduced-motion — прыжок
  const animateTo = useCallback(
    (to: View) => {
      touched.current = false;
      cancelAnimationFrame(tween.current);
      const from = viewRef.current;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        tween.current = requestAnimationFrame(() => commit(to));
        return;
      }
      const t0 = performance.now();
      const step = (now: number) => {
        const t = Math.min(1, Math.max(0, (now - t0) / TWEEN_MS));
        const e = t >= 1 ? 1 : (1 - 2 ** (-10 * t)) / (1 - 2 ** -10);
        const mix = (a: number, b: number) => a + (b - a) * e;
        commit({ k: mix(from.k, to.k), tx: mix(from.tx, to.tx), ty: mix(from.ty, to.ty) });
        if (t < 1) tween.current = requestAnimationFrame(step);
      };
      tween.current = requestAnimationFrame(step);
    },
    [commit],
  );

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  // Размер области и подгонка под текущий режим сразу, без анимации
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width: w, height: h } = e.contentRect;
      if (w > 0) {
        lastMode.current = modeKeyOf(modeRef.current);
        cancelAnimationFrame(tween.current);
        commit(targetFor(modeRef.current, w, h));
      }
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [targetFor, commit]);

  // Режим сменили снаружи — камера едет к новой цели
  useEffect(() => {
    if (!size.w || lastMode.current === modeKey) return;
    lastMode.current = modeKey;
    if (mode.kind === "overview" && wantAll.current) animateTo(fitView(size.w, size.h));
    else animateTo(targetFor(mode, size.w, size.h));
    wantAll.current = false;
  }, [modeKey, mode, size.w, size.h, animateTo, targetFor, fitView]);

  // Раскладка пересчиталась под размер области — подгоняем камеру, если её не трогали
  useEffect(() => {
    if (!size.w || touched.current) return;
    cancelAnimationFrame(tween.current);
    commit(targetFor(modeRef.current, size.w, size.h));
  }, [targetFor, size.w, size.h, commit]);

  useEffect(() => () => cancelAnimationFrame(tween.current), []);

  const zoomAt = useCallback(
    (px: number, py: number, factor: number) => {
      cancelTween();
      touched.current = true;
      const v = viewRef.current;
      const k = clampK(v.k * factor);
      const r = k / v.k;
      commit({ k, tx: px - (px - v.tx) * r, ty: py - (py - v.ty) * r });
    },
    [cancelTween, commit],
  );

  const pan = useCallback(
    (dx: number, dy: number) => {
      cancelTween();
      touched.current = true;
      const v = viewRef.current;
      commit({ ...v, tx: v.tx + dx, ty: v.ty + dy });
    },
    [cancelTween, commit],
  );

  // Колесо: нужен не-пассивный обработчик, чтобы не прокручивать страницу
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0016));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  useImperativeHandle(ref, () => ({
    focusTopic(id) {
      const t = byId.get(id);
      if (!t || !size.w) return;
      if (t.hubIndex < 0 || !t.hubId) {
        // свободная тема: обзор, камера едет к точке
        lastMode.current = modeKeyOf(OVERVIEW);
        onModeChange(OVERVIEW);
        const kk = clampK(Math.max(viewRef.current.k, 0.9));
        animateTo({ k: kk, tx: size.w / 2 - t.x * kk, ty: size.h / 2 - t.y * kk });
      } else {
        // сначала глава этой темы, потом карточка и кольцо
        lastMode.current = `hub:${t.hubId}`;
        onModeChange({ kind: "hub", hubId: t.hubId });
        animateTo(hubView(t.hubIndex, size.w, size.h));
      }
      setPinned(id);
      setFound((f) => ({ id, n: (f?.n ?? 0) + 1 }));
    },
  }));

  // Esc: сначала закрыть карточку, потом выйти в обзор
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if ((e.target as HTMLElement | null)?.closest?.("input,textarea,[role=dialog]")) return;
      if (pinned) setPinned(null);
      else if (mode.kind !== "overview") onModeChange(OVERVIEW);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned, mode.kind, onModeChange]);

  const k = view.k;
  const sx = (x: number) => x * k + view.tx;
  const sy = (y: number) => y * k + view.ty;
  const nodeS = clamp(0.7 + 0.4 * k, 0.9, 1.6);
  const hubS = clamp(0.95 + 0.2 * k, 1.05, 1.3);


  // Подписи тем в экранных пикселях: пробуем справа/слева/сверху/снизу, берём первое место без наложений
  const kKey = Math.round(k * 10) / 10;
  const anchors = useMemo(() => {
    const out = new Map<string, Anchor>();
    const ns = clamp(0.7 + 0.4 * kKey, 0.9, 1.6);
    const R = 7.5 * ns + 5;
    const shown = layout.topics.filter(
      (t) => mode.kind === "hub" && (t.hubIndex === focusIdx || (t.hubIndex < 0 && !!freeLinks.get(t.id)?.has(focusIdx))),
    );
    if (!shown.length) return out;
    type B = { x0: number; x1: number; y0: number; y1: number };
    const hit = (a: B, b: B) => a.x0 < b.x1 + 3 && a.x1 > b.x0 - 3 && a.y0 < b.y1 + 2 && a.y1 > b.y0 - 2;
    const nodes: B[] = layout.topics.map((t) => ({ x0: t.x * kKey - 11, x1: t.x * kKey + 11, y0: t.y * kKey - 11, y1: t.y * kKey + 11 }));
    // узел главы и её подпись тоже занимают место
    layout.hubs.forEach((h) => {
      const hx = h.x * kKey, hy = h.y * kKey;
      nodes.push({ x0: hx - 14, x1: hx + 14, y0: hy - 14, y1: hy + 14 });
      const ly0 = h.labelBelow ? hy : hy - 34, ly1 = h.labelBelow ? hy + 34 : hy;
      nodes.push(h.labelSide === 1 ? { x0: hx + 20, x1: hx + 200, y0: ly0, y1: ly1 } : { x0: hx - 200, x1: hx - 20, y0: ly0, y1: ly1 });
    });
    const placed: B[] = [];
    [...shown].sort((a, b) => a.y - b.y || a.x - b.x).forEach((t) => {
      const px = t.title.replace(/­/g, "").length * 7.8;
      const w = Math.min(130, px);
      const h = px > 112 ? 34 : 18;
      const nx = t.x * kKey, ny = t.y * kKey;
      const side: Anchor = Math.cos(t.angle) >= -0.2 ? "r" : "l";
      const order: Anchor[] = side === "r" ? ["r", "l", "t", "b"] : ["l", "r", "t", "b"];
      const boxOf = (a: Anchor): B =>
        a === "r"
          ? { x0: nx + R + 4, x1: nx + R + 4 + w, y0: ny - h / 2, y1: ny + h / 2 }
          : a === "l"
            ? { x0: nx - R - 4 - w, x1: nx - R - 4, y0: ny - h / 2, y1: ny + h / 2 }
            : a === "t"
              ? { x0: nx - w / 2, x1: nx + w / 2, y0: ny - R - 2 - h, y1: ny - R - 2 }
              : { x0: nx - w / 2, x1: nx + w / 2, y0: ny + R + 2, y1: ny + R + 2 + h };
      const pick =
        order.find((a) => {
          const b = boxOf(a);
          return !placed.some((o) => hit(b, o)) && !nodes.some((o, i) => (i >= layout.topics.length || layout.topics[i].id !== t.id) && hit(b, o));
        }) ?? order[0];
      placed.push(boxOf(pick));
      out.set(t.id, pick);
    });
    return out;
  }, [layout, kKey, mode.kind, focusIdx, freeLinks]);

  const clusterOp = (i: number) => {
    let op: number;
    if (mode.kind === "overview") op = (hoverHub === null ? recent.includes(i) : hoverHub === i) ? 1 : 0.16;
    else op = i === focusIdx ? 1 : 0.15;
    return domain.hubs[i].suggested ? Math.max(op * 0.5, 0.3) : op;
  };
  /** Непрозрачность темы: у темы главы — как у кластера, у свободной — среднее для связанных с фокусом */
  const topicOp = (t: PlacedTopic) => {
    if (t.hubIndex >= 0) return clusterOp(t.hubIndex);
    if (mode.kind === "overview") return FREE_OP_LIT;
    return freeLinks.get(t.id)?.has(focusIdx) ? FREE_OP_LIT : FREE_OP;
  };
  // Какие подтемы доступны для наведения и карточек в этом режиме
  const interactive = (t: PlacedTopic) =>
    t.hubIndex < 0
      ? mode.kind === "overview" || !!freeLinks.get(t.id)?.has(focusIdx)
      : (mode.kind === "hub" && t.hubIndex === focusIdx) || (mode.kind === "overview" && hoverHub === t.hubIndex);
  const chainOp = mode.kind === "overview" ? 0.5 : 0.1;
  const litHub = mode.kind === "hub" ? focusIdx : hoverHub;

  const rawActive = hovered ?? pinned;
  const activeTopic = rawActive ? byId.get(rawActive) : undefined;
  const active = activeTopic && interactive(activeTopic) ? activeTopic.id : null;
  const shownTopic = active ? byId.get(active) : undefined;

  const enter = (id: string) => {
    clearTimeout(hideTimer.current);
    setHovered(id);
  };
  const leave = () => {
    hideTimer.current = setTimeout(() => setHovered(null), 160);
  };

  // Какой кластер под курсором: круг вокруг центра главы
  const hubAt = (clientX: number, clientY: number, el: HTMLElement): number | null => {
    const v = viewRef.current;
    const rect = el.getBoundingClientRect();
    const wx = (clientX - rect.left - v.tx) / v.k;
    const wy = (clientY - rect.top - v.ty) / v.k;
    let best: number | null = null;
    let bestD = Infinity;
    // ближайший узел кластера (нод главы или её тема): в куче круги вокруг глав перекрываются
    const near = (ax: number, ay: number) => (wx - ax) * (wx - ax) + (wy - ay) * (wy - ay);
    layout.hubs.forEach((h) => {
      let d = near(h.x, h.y) / (80 * 80);
      layout.topics.forEach((t) => {
        if (t.hubIndex === h.index) d = Math.min(d, near(t.x, t.y) / (56 * 56));
      });
      if (d < 1 && d < bestD) {
        best = h.index;
        bestD = d;
      }
    });
    return best;
  };
  const focusHubAt = (i: number) => {
    setPinned(null);
    onModeChange({ kind: "hub", hubId: domain.hubs[i].id });
  };
  const fitAll = () => {
    if (mode.kind === "hub") {
      wantAll.current = true;
      onModeChange(OVERVIEW);
    } else animateTo(fitView(size.w, size.h));
  };

  const focusHub = focusIdx >= 0 ? domain.hubs[focusIdx] : null;
  const total = layout.topics.length;
  const statusText =
    mode.kind === "hub" && focusHub
      ? tx.graph.statusHub(focusHub.num, clean(focusHub.title), layout.hubs[focusIdx].count)
      : tx.graph.status(domain.hubs.length, total);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="@container flex min-h-10 shrink-0 flex-wrap items-center gap-x-6 border-b border-seam-soft text-small text-paper-dim">
        <p aria-live="polite" className="num shrink-0">
          {statusText}
        </p>
        <ul
          className={cn(
            "order-last flex basis-full flex-wrap items-center gap-x-4 pb-2",
            mode.kind === "overview"
              ? "@[40rem]:order-none @[40rem]:min-w-0 @[40rem]:flex-1 @[40rem]:basis-auto @[40rem]:pb-0"
              : "@[62rem]:order-none @[62rem]:min-w-0 @[62rem]:flex-1 @[62rem]:basis-auto @[62rem]:pb-0",
          )}
          aria-label={tx.graph.statesLabel}
        >
          {(["solid", "review", "confused", "new"] as const).map((s) => (
            <li key={s} className="flex items-center gap-1.5 whitespace-nowrap">
              <StateDot state={s} />
              {tx.states[s].toLowerCase()}
            </li>
          ))}
        </ul>
        <div className="ml-auto flex shrink-0 items-center">
          {focusHub && (
            <Link
              href={`/hub/${focusHub.id}`}
              className="inline-flex h-10 shrink-0 items-center rounded-control px-3 whitespace-nowrap text-paper transition-colors hover:bg-cloth-hover"
            >
              {tx.graph.openChapter}
            </Link>
          )}
          {mode.kind === "hub" && (
            <button
              type="button"
              onClick={() => onModeChange(OVERVIEW)}
              className="inline-flex h-10 shrink-0 items-center rounded-control px-3 whitespace-nowrap transition-colors hover:bg-cloth-hover hover:text-paper"
            >
              {tx.graph.allChapters}
            </button>
          )}
          <div className="flex shrink-0" role="group" aria-label={tx.graph.zoomGroup}>
            <IconButton label={tx.graph.zoomIn} onClick={() => zoomAt(size.w / 2, size.h / 2, 1.25)}>
              <Plus />
            </IconButton>
            <IconButton label={tx.graph.zoomOut} onClick={() => zoomAt(size.w / 2, size.h / 2, 0.8)}>
              <Minus />
            </IconButton>
            <IconButton label={tx.graph.fitAll} onClick={fitAll}>
              <Maximize2 />
            </IconButton>
          </div>
        </div>
      </div>

      <div
        ref={box}
        tabIndex={0}
        role="application"
        aria-label={tx.graph.ariaGraph(domain.title)}
        className="relative min-h-0 flex-1 cursor-grab touch-none overflow-hidden rounded-xl outline-none select-none focus-visible:ring-2 focus-visible:ring-ribbon/60 active:cursor-grabbing"
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          const step = 60;
          const map: Record<string, () => void> = {
            ArrowLeft: () => pan(step, 0),
            ArrowRight: () => pan(-step, 0),
            ArrowUp: () => pan(0, step),
            ArrowDown: () => pan(0, -step),
            "+": () => zoomAt(size.w / 2, size.h / 2, 1.25),
            "=": () => zoomAt(size.w / 2, size.h / 2, 1.25),
            "-": () => zoomAt(size.w / 2, size.h / 2, 0.8),
            "0": fitAll,
          };
          if (map[e.key]) {
            e.preventDefault();
            map[e.key]();
          }
        }}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest("button,[data-card]")) return;
          drag.current = { x: e.clientX, y: e.clientY, moved: false };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) {
            if (mode.kind !== "hub" && e.pointerType === "mouse" && !(e.target as HTMLElement).closest("[data-card]")) {
              setHoverHub(hubAt(e.clientX, e.clientY, e.currentTarget));
            }
            return;
          }
          const dx = e.clientX - d.x, dy = e.clientY - d.y;
          if (!d.moved && Math.hypot(dx, dy) < 3) return;
          d.moved = true;
          d.x = e.clientX;
          d.y = e.clientY;
          pan(dx, dy);
        }}
        onPointerLeave={() => setHoverHubRaw(null)}
        onPointerCancel={() => (drag.current = null)}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (!d || d.moved) return;
          // клик по пустому месту: кластер → фокус на главе, глава → обзор
          if (mode.kind === "hub") {
            if (pinned) setPinned(null);
            else onModeChange(OVERVIEW);
          } else {
            const i = hubAt(e.clientX, e.clientY, e.currentTarget);
            if (i !== null) focusHubAt(i);
            else setPinned(null);
          }
        }}
      >
        <svg className="pointer-events-none absolute inset-0 size-full" aria-hidden>
          <g transform={`translate(${view.tx} ${view.ty}) scale(${k})`}>
            {/* связи глав «строится на»: живые кривые, чуть толще связей тем */}
            {layout.links.map((l) => (
              <path
                key={l.id}
                d={l.d}
                fill="none"
                stroke="var(--rule)"
                strokeWidth={1.25}
                strokeLinecap="round"
                strokeDasharray={l.suggested ? "3 6" : undefined}
                vectorEffect="non-scaling-stroke"
                className={fade}
                style={{ opacity: l.suggested ? chainOp * 0.7 : chainOp }}
              />
            ))}
            {/* связи тем: внутри главы тонкие прямые, между главами приглушённые дуги, «похоже» — точками */}
            {layout.edges.map((e) => {
              const ta = e.kind === "hub" ? undefined : byId.get(e.a);
              const tb = byId.get(e.b);
              const touchesActive = !!active && (e.a === active || e.b === active);
              // в фокусе без наведения — только связи, у которых хотя бы один конец в фокусной главе
              if (mode.kind === "hub" && (e.kind === "cross" || e.kind === "related") && !touchesActive) {
                // без наведения: только связи внутри главы и со свободными темами, связанными с ней
                const inHub = (h: number, id: string) => h === focusIdx || (h < 0 && !!freeLinks.get(id)?.has(focusIdx));
                if (!(inHub(e.ha, e.a) && inHub(e.hb, e.b) && (e.ha === focusIdx || e.hb === focusIdx))) return null;
              }
              if (e.kind === "hub" || e.kind === "prereq") {
                return (
                  <path
                    key={e.id}
                    d={e.d}
                    fill="none"
                    stroke={touchesActive ? "var(--paper)" : "var(--rule)"}
                    strokeWidth={0.75}
                    vectorEffect="non-scaling-stroke"
                    className={fade}
                    style={{ opacity: clusterOp(e.hb) }}
                  />
                );
              }
              const factor = e.kind === "cross" ? 0.5 : 0.38;
              // заметны только когда ярки оба конца; между приглушёнными кластерами почти не видны
              const opA = ta ? topicOp(ta) : 0, opB = tb ? topicOp(tb) : 0;
              const base = (mode.kind === "hub" ? 0.7 : Math.min(opA, opB) ** 2) * factor;
              const lit = litHub !== null && (e.ha === litHub || e.hb === litHub);
              return (
                <path
                  key={e.id}
                  d={e.d}
                  fill="none"
                  stroke={touchesActive ? "var(--paper)" : lit ? "var(--paper-dim)" : "var(--rule)"}
                  strokeWidth={e.kind === "related" ? 1.1 : 0.75}
                  strokeLinecap="round"
                  strokeDasharray={e.kind === "related" ? "1 5" : undefined}
                  vectorEffect="non-scaling-stroke"
                  className={fade}
                  style={{ opacity: touchesActive ? 0.9 : lit ? Math.max(base, factor + 0.25) : base }}
                />
              );
            })}
            {layout.outside.map((o) => {
              const t = byId.get(o.linkedTo);
              if (!t) return null;
              return (
                <line
                  key={o.id}
                  x1={t.x}
                  y1={t.y}
                  x2={o.x}
                  y2={o.y}
                  stroke="var(--rule)"
                  strokeOpacity={0.7}
                  strokeWidth={0.75}
                  strokeDasharray="3 5"
                  vectorEffect="non-scaling-stroke"
                  className={fade}
                  style={{ opacity: topicOp(t) }}
                />
              );
            })}
            {found && byId.get(found.id) && (
              <circle
                key={found.n}
                cx={byId.get(found.id)!.x}
                cy={byId.get(found.id)!.y}
                r={12}
                fill="none"
                opacity={0}
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
                onAnimationEnd={() => setFound(null)}
                className="animate-found stroke-ribbon"
                style={{ transformBox: "fill-box" }}
              />
            )}
          </g>
        </svg>

        {layout.outside.map((o) => {
          const t = byId.get(o.linkedTo);
          const right = Math.cos(o.angle) >= -0.2;
          const rr = 6 * nodeS;
          const outOp = t ? topicOp(t) : 1;
          return (
            <div
              key={o.id}
              className={cn("pointer-events-none absolute", fade)}
              style={{ left: sx(o.x), top: sy(o.y) }}
            >
              <span
                className={cn("absolute -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed border-st-new", fade)}
                style={{ width: rr * 2, height: rr * 2, opacity: outOp }}
              />
              <span
                className={cn(
                  "absolute top-0 -translate-y-1/2 text-small whitespace-nowrap text-paper-dim",
                  right ? "text-left" : "text-right",
                )}
                style={{ opacity: Math.max(outOp, LABEL_FLOOR), ...(right ? { left: rr + 6 } : { right: rr + 6 }) }}
              >
                {o.title}
                <span className="num ml-1.5 text-[11px]">· {o.fromDomain.toLowerCase()}</span>
              </span>
            </div>
          );
        })}

        {layout.hubs.map((h) => (
          <HubNode
            key={h.id}
            hub={domain.hubs[h.index]}
            placed={h}
            x={sx(h.x)}
            y={sy(h.y)}
            scale={hubS}
            opacity={Math.max(clusterOp(h.index), mode.kind === "overview" ? 0.5 : 0.3)}
            labelOpacity={mode.kind === "hub" ? Math.max(clusterOp(h.index), LABEL_FLOOR) : 1}
            live={mode.kind !== "hub" || h.index === focusIdx}
            onEnter={() => setHoverHub(h.index)}
            onOpen={() => (mode.kind === "hub" ? router.push(`/hub/${h.id}`) : focusHubAt(h.index))}
          />
        ))}

        {layout.topics.map((t) => {
          const live = interactive(t);
          const showLabel = mode.kind === "hub" && (t.hubIndex === focusIdx || (t.hubIndex < 0 && live));
          return (
            <TopicNode
              key={t.id}
              t={t}
              x={sx(t.x)}
              y={sy(t.y)}
              scale={nodeS}
              label={showLabel}
              anchor={anchors.get(t.id) ?? (Math.cos(t.angle) >= -0.2 ? "r" : "l")}
              opacity={topicOp(t)}
              live={live}
              active={active === t.id}
              onEnter={() => enter(t.id)}
              onLeave={leave}
              onToggle={() => router.push(`/topic/${t.id}`)}
            />
          );
        })}

        {shownTopic && (
          <div
            data-card
            onPointerEnter={() => clearTimeout(hideTimer.current)}
            onPointerLeave={leave}
            className="absolute z-10 rounded-[16px] border border-seam bg-cloth-raised p-4 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
            style={cardPos(sx(shownTopic.x), sy(shownTopic.y), size, shownTopic.misconception ? 300 : 266)}
          >
            <TopicCardBody
              topic={shownTopic}
              hubLabel={shownTopic.hubIndex >= 0 ? clean(domain.hubs[shownTopic.hubIndex].title) : tx.graph.noChapter}
            />
          </div>
        )}
      </div>
    </div>
  );
});

/** Карточка сбоку от точки; по вертикали — в пределах графа */
function cardPos(x: number, y: number, size: { w: number; h: number }, cardH: number): React.CSSProperties {
  const gap = 18;
  const right = x + gap + CARD_W < size.w;
  const left = right ? x + gap : Math.max(8, x - gap - CARD_W);
  const top = Math.max(8, Math.min(y - 36, size.h - cardH - 8));
  return { left, top, width: CARD_W };
}

/** Узел главы: кольцо прогресса, название всегда рядом */
function HubNode({
  hub, placed, x, y, scale, opacity, labelOpacity, live, onEnter, onOpen,
}: {
  hub: Domain["hubs"][number]; placed: PlacedHub; x: number; y: number; scale: number; opacity: number; labelOpacity: number;
  live: boolean; onEnter: () => void; onOpen: () => void;
}) {
  const tx = useT();
  const { fullDate } = useFmt();
  const R = 14 * scale;
  const C = 2 * Math.PI * R;
  const box = (R + 4) * 2;
  const mid = box / 2;
  const n = placed.count;
  return (
    <button
      type="button"
      aria-label={tx.graph.openHubAria(clean(hub.title), n)}
      inert={!live}
      tabIndex={live ? 0 : -1}
      onFocus={onEnter}
      onClick={onOpen}
      className={cn(
        "absolute z-[2] flex size-10 min-h-10 min-w-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ribbon/60",
        fade,
        !live && "pointer-events-none",
      )}
      style={{ left: x, top: y }}
    >
      <svg
        width={box}
        height={box}
        viewBox={`0 0 ${box} ${box}`}
        aria-hidden
        className={cn("shrink-0 overflow-visible", fade)}
        style={{ opacity }}
      >
        <circle
          cx={mid}
          cy={mid}
          r={R}
          fill="var(--cloth-raised)"
          stroke={hub.suggested ? "var(--rule)" : "var(--seam)"}
          strokeWidth={2}
          strokeDasharray={hub.suggested ? "3 4" : undefined}
        />
        {!hub.suggested && placed.progress > 0 && (
          <circle
            cx={mid}
            cy={mid}
            r={R}
            fill="none"
            stroke="var(--st-solid)"
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray={`${C * placed.progress} ${C}`}
            transform={`rotate(-90 ${mid} ${mid})`}
          />
        )}
      </svg>
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute top-1/2 flex flex-col gap-0.5 text-[15px] leading-5 font-medium whitespace-nowrap [paint-order:stroke_fill] [-webkit-text-stroke:3px_var(--cloth)]",
          fade,
          placed.labelBelow ? "translate-y-[3px]" : "-translate-y-[calc(100%+3px)]",
          placed.labelSide === 1 ? "items-start text-left" : "items-end text-right",
        )}
        style={{ opacity: labelOpacity, ...(placed.labelSide === 1 ? { left: `calc(50% + ${R + 8}px)` } : { right: `calc(50% + ${R + 8}px)` }) }}
      >
        <span className="font-semibold text-paper">{clean(hub.title)}</span>
        {hub.deadline && (
          <span className={cn("flex items-baseline gap-1.5 text-ribbon", placed.labelSide === 1 ? "flex-row" : "flex-row-reverse")}>
            <Duck className="h-3.5 w-4 self-center" />
            <span className="sr-only">{hub.deadline.label}: </span>
            {tx.graph.due} {fullDate(hub.deadline.date)}
          </span>
        )}
      </span>
    </button>
  );
}

/** Подтема: точка состояния в кольце памяти — дуга показывает, сколько держится */
function TopicNode({
  t, x, y, scale, label, anchor, opacity, live, active, onEnter, onLeave, onToggle,
}: {
  t: PlacedTopic; x: number; y: number; scale: number; label: boolean; anchor: Anchor; opacity: number;
  live: boolean; active: boolean; onEnter: () => void; onLeave: () => void; onToggle: () => void;
}) {
  const tx = useT();
  const color = STATE_COLOR[t.state];
  const R = 7.5 * scale;
  const dot = 3 * scale;
  const C = 2 * Math.PI * R;
  const box = (R + 5) * 2;
  const mid = box / 2;
  return (
    <button
      type="button"
      aria-label={`${clean(t.title)} — ${tx.states[t.state].toLowerCase()}`}
      aria-expanded={active}
      inert={!live}
      tabIndex={live ? 0 : -1}
      onPointerEnter={(e) => e.pointerType === "mouse" && onEnter()}
      onPointerLeave={(e) => e.pointerType === "mouse" && onLeave()}
      onFocus={onEnter}
      onBlur={onLeave}
      onClick={onToggle}
      className={cn(
        "group absolute z-[1] flex size-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full",
        fade,
        !live && "pointer-events-none",
      )}
      style={{ left: x, top: y, opacity }}
    >
      <svg width={box} height={box} viewBox={`0 0 ${box} ${box}`} aria-hidden className="shrink-0 overflow-visible">
        <circle
          cx={mid}
          cy={mid}
          r={R}
          fill="var(--cloth)"
          stroke="var(--seam)"
          strokeWidth={1.5}
          strokeDasharray={t.state === "new" ? "2 3" : undefined}
        />
        {t.state !== "new" && (
          <circle
            cx={mid}
            cy={mid}
            r={R}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeDasharray={`${C * t.retention} ${C}`}
            transform={`rotate(-90 ${mid} ${mid})`}
          />
        )}
        {t.state === "new" ? (
          <circle cx={mid} cy={mid} r={dot * 0.85} fill="none" stroke={color} strokeWidth={1.2} />
        ) : (
          <circle cx={mid} cy={mid} r={dot} fill={color} />
        )}
        {active && <circle cx={mid} cy={mid} r={R + 3.5} fill="none" stroke="var(--paper)" strokeWidth={1.5} />}
      </svg>
      {label && (
        <span
          className={cn(
            "pointer-events-none absolute line-clamp-2 w-[130px] hyphens-manual text-small leading-tight [paint-order:stroke_fill] [-webkit-text-stroke:4px_var(--cloth)]",
            anchor === "r" ? "top-1/2 -translate-y-1/2 text-left" : anchor === "l" ? "top-1/2 -translate-y-1/2 text-right" : "left-1/2 -translate-x-1/2 text-center",
            active ? "text-paper" : "text-paper-dim",
          )}
          style={
            anchor === "r"
              ? { left: `calc(50% + ${R + 4}px)` }
              : anchor === "l"
                ? { right: `calc(50% + ${R + 4}px)` }
                : anchor === "t"
                  ? { bottom: `calc(50% + ${R + 2}px)` }
                  : { top: `calc(50% + ${R + 2}px)` }
          }
        >
          {t.title}
        </span>
      )}
    </button>
  );
}
