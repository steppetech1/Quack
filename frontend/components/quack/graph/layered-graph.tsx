"use client";

import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Maximize2, Minus, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { GAP_X, GAP_Y, layoutGraph, loadPositions, savePositions } from "@/lib/graph/layout";
import { onGraph, type GraphModel, type GraphTopic } from "@/lib/graph/types";
import { clearIndicators, getIndicators, getIndicatorsServer, publishIndicators, subscribeIndicators, unmountIndicators } from "@/lib/graph/indicator-store";
import { computeIndicators, INDICATORS, type IndicatorId } from "@/lib/graph/indicators";
import { enterDomain, getVisit, LEARNER, leaveDomain, saveAllVisits, subscribeVisit, updateDomain } from "@/lib/graph/visit-store";
import { loadCamera, restoreCenter, saveCamera, type SavedCamera } from "@/lib/graph/visit";
import { useT } from "@/lib/i18n/provider";
import type { Domain, TopicState } from "@/lib/mock/types";
import { IconButton } from "../icon-button";
import { IndicatorPills } from "./indicator-pills";
import { StateDot, TopicCardBody } from "../topic-card";

export type GraphHandle = { focusTopic: (id: string) => void };

type View = { k: number; tx: number; ty: number };
type Pt = { x: number; y: number };
export type Colors = Record<"paper" | "dim" | "ribbon" | "cloth" | TopicState, string> & { label: string; body: string; ribbonRgb: string; ind: Record<IndicatorId, string> };
type Arrow = { id: string; dir: "up" | "down" | "left" | "right"; ind: IndicatorId; n: number; x: number; y: number; key: string };
const IND_VAR: Record<IndicatorId, string> = { new: "var(--ind-new)", relevant: "var(--ind-relevant)", deadline: "var(--ind-deadline)", weak: "var(--ind-weak)", review: "var(--ind-review)" };

/** The forms of the canvas rings, as far as a CSS border can draw them: for the left column, which has no canvas node */
const RING_STYLE: Record<IndicatorId, string> = { new: "solid", relevant: "dashed", deadline: "solid", weak: "double", review: "dotted" };

const K_MIN = 0.04;
const K_MAX = 2.4;
const FIT_K_MAX = 1.2;
const FIT_MARGIN = 56;
/** How much of its brightness a node loses while a hub is in focus */
const FOCUS_DIM = 0.86;
/** How much of its brightness a node loses while an indicator is on and the node is not one of the lit ones */
const IND_DIM = 0.78;
const TWEEN_MS = 450;
/** A topic leaving the column for its place on the graph travels slower: the learner should see where it went */
const MOVE_MS = 900;
/** Width of the left column. It is fixed on screen, so the labels stay readable at any zoom */
const COL_W = 208;
/** Smallest zoom at which labels still read. An indicator never zooms out past it: what does not fit gets an arrow */
const K_READ = 0.6;
const CARD_W = 320;
/** How long a changed node pulses */
const FLASH_MS = 1800;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const easeOut = (t: number) => 1 - Math.pow(1 - t, 4);
const smooth = (a: number, b: number, v: number) => {
  const t = clamp((v - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Canvas cannot use CSS classes, so colours and fonts are read once from probe elements */
export function readColors(host: HTMLElement): Colors {
  const probe = (cls: string, prop: "color" | "fontFamily") => {
    const el = document.createElement("span");
    el.className = cls;
    el.style.cssText = "position:absolute;visibility:hidden";
    host.appendChild(el);
    const v = getComputedStyle(el)[prop];
    el.remove();
    return v;
  };
  // Gradient stops need a colour with alpha; whatever CSS gave us is turned into plain r, g, b once
  const rgb = (css: string) => {
    const px = document.createElement("canvas");
    px.width = px.height = 1;
    const c = px.getContext("2d");
    if (!c) return "237,185,102";
    c.fillStyle = css;
    c.fillRect(0, 0, 1, 1);
    const d = c.getImageData(0, 0, 1, 1).data;
    return `${d[0]},${d[1]},${d[2]}`;
  };
  const ribbon = probe("text-ribbon", "color");
  return {
    ribbonRgb: rgb(ribbon),
    paper: probe("text-paper", "color"),
    dim: probe("text-paper-dim", "color"),
    ribbon,
    cloth: probe("text-cloth", "color"),
    solid: probe("text-st-solid", "color"),
    review: probe("text-st-review", "color"),
    confused: probe("text-st-confused", "color"),
    new: probe("text-st-new", "color"),
    ind: {
      new: probe("text-ind-new", "color"),
      relevant: probe("text-ind-relevant", "color"),
      deadline: probe("text-ind-deadline", "color"),
      weak: probe("text-ind-weak", "color"),
      review: probe("text-ind-review", "color"),
    },
    label: probe("font-label", "fontFamily"),
    body: probe("font-sans", "fontFamily"),
  };
}

const WEAK = new Set(["confused_with", "persistent_error", "struggling", "inconsistent"]);

/** Topic state for colour and legend. Stage 6 gives indicators their own palette */
export function stateOf(t: GraphTopic, nowMs: number): TopicState {
  if (t.status === "proposed" || !t.displayState || t.displayState === "not_tested") return "new";
  if (WEAK.has(t.displayState)) return "confused";
  if (t.nearestDueAtMs !== undefined && t.nearestDueAtMs <= nowMs) return "review";
  return "solid";
}

export const LayeredGraph = forwardRef<
  GraphHandle,
  {
    model: GraphModel;
    domain: Domain;
    highlight?: { topicIds: string[]; hubIds: string[] };
    focusHubId?: string | null;
    onClearFocus?: () => void;
    /** Put the whole graph in the middle of the area (a click on the subject name); `onCentered` says it is done */
    center?: boolean;
    onCentered?: () => void;
  }
>(function LayeredGraph({ model, domain, highlight, focusHubId = null, onClearFocus, center = false, onCentered }, ref) {
  const tx = useT();
  const router = useRouter();
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [arrows, setArrows] = useState<Arrow[]>([]);

  // The draw loop reads refs, so panning does not re-render React
  const view = useRef<View>({ k: 1, tx: 0, ty: 0 });
  const shown = useRef(new Map<string, Pt>());
  const from = useRef(new Map<string, Pt>());
  const target = useRef(new Map<string, Pt>());
  const posTween = useRef<number | null>(null);
  const posMs = useRef(TWEEN_MS);
  // Labels fade in and out on their own clock, so a label never blinks when the zoom or a collision changes its fate
  const labelVis = useRef(new Map<string, number>());
  const textW = useRef(new Map<string, number>());
  const lastDraw = useRef(0);
  const flash = useRef(new Map<string, number>());
  const lit = useRef<string[]>([]);
  // Indicators: which indicators light which node, how bright each halo is now, where the camera stood before the first click
  const marks = useRef(new Map<string, IndicatorId[]>());
  // Everything an active indicator lights, ringed on the canvas or not (the left column has no canvas node). The last
  // non-empty set stays while the dimming fades out, so switching off lets the graph come back instead of snapping
  const indLit = useRef<ReadonlySet<string>>(new Set());
  const indMix = useRef(0);
  const lastInd = useRef(0);
  const halo = useRef(new Map<string, number>());
  const haloAt = useRef(new Map<string, Pt>());
  const lastHalo = useRef(0);
  // Hub focus: which hub the graph is dimmed around and how far the fade has gone (0 none, 1 full)
  const focusHub = useRef<string | null>(null);
  // The hub picked in the left panel: the graph stays focused on it until it is cleared
  const pinHub = useRef<string | null>(null);
  const pinApplied = useRef<string | null>(null);
  const clearPinRef = useRef(onClearFocus);
  const focusMix = useRef(0);
  const lastFocus = useRef(0);
  const activeRef = useRef<ReadonlySet<IndicatorId>>(new Set());
  const before = useRef<View | null>(null);
  const prevActive = useRef<ReadonlySet<IndicatorId>>(new Set());
  const arrowSig = useRef("");
  const arrowAt = useRef(0);
  const camTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const colY = useRef(new Map<string, number>());
  const colEls = useRef(new Map<string, HTMLElement>());
  const colScroll = useRef<HTMLElement | null>(null);
  const viewTween = useRef<{ start: number; from: View; to: View } | null>(null);
  const raf = useRef(0);
  const loop = useRef<FrameRequestCallback>(() => {});
  const colors = useRef<Colors | null>(null);
  const hoverKey = useRef<string | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const fitted = useRef(false);
  const sizeRef = useRef(size);

  const topicMap = useMemo(() => new Map(model.topics.map((t) => [t.id, t])), [model]);
  const hubMap = useMemo(() => new Map(model.hubs.map((h) => [h.id, h])), [model]);
  // Saved positions keep known topics in place. The node list does not depend on them, so server and client markup match
  const layout = useMemo(() => layoutGraph(model, loadPositions(LEARNER, model.domainId)), [model]);
  // Left column: unlinked topics of this domain, then topics from other subjects
  const column = useMemo(() => {
    const free = layout.unlinked.map((id) => topicMap.get(id)).filter((t): t is GraphTopic => !!t);
    const outside = [
      ...model.otherDomain.map((o) => ({ topic: o.topic, domainName: o.domainName as string | undefined })),
      ...model.topics.filter((t) => t.status === "off_domain" && t.visibility !== "hidden").map((t) => ({ topic: t, domainName: undefined })),
    ];
    return { free, outside, width: free.length || outside.length ? COL_W : 0 };
  }, [layout, topicMap, model]);
  const lookup = useMemo(() => new Map([...topicMap, ...model.otherDomain.map((o): [string, GraphTopic] => [o.topic.id, o.topic])]), [topicMap, model]);
  const links = useMemo(() => {
    const byHub = new Map<string, string[]>();
    const hubsOf = new Map<string, string[]>();
    for (const l of model.includes) {
      byHub.set(l.hubId, [...(byHub.get(l.hubId) ?? []), l.topicId]);
      hubsOf.set(l.topicId, [...(hubsOf.get(l.topicId) ?? []), l.hubId]);
    }
    const edges = model.requires.filter((e) => layout.nodes.has(e.topicId) && layout.nodes.has(e.requiresId));
    // Topics with more links are more important when two labels fight for the same place
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.topicId, (degree.get(e.topicId) ?? 0) + 1);
      degree.set(e.requiresId, (degree.get(e.requiresId) ?? 0) + 1);
    }
    return { byHub, hubsOf, edges, degree };
  }, [model, layout]);
  const scene = useRef({ layout, model, topicMap, hubMap, colW: column.width, links });

  const draw = useCallback((): boolean => {
    const c = canvas.current;
    const col = colors.current;
    const { w, h } = sizeRef.current;
    if (!c || !col || !w || !h) return false;
    const ctx = c.getContext("2d");
    if (!ctx) return false;
    const { layout: L, model: m, topicMap: topics, hubMap: hubs, colW, links } = scene.current;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const { k, tx: vx, ty: vy } = view.current;
    const pos = (key: string) => shown.current.get(key);
    const sx = (x: number) => x * k + vx;
    const sy = (y: number) => y * k + vy;
    const near = (x: number, y: number, pad: number) => x > -pad && x < w + pad && y > -pad && y < h + pad;
    const hovered = hoverKey.current;

    // Topic to hub: soft, see-through curves. They are brighter the closer the topic is to the hub, dimmer at the foot.
    // Close to the hub they gather into one brighter stem. Hovering a hub lights its lines and mutes the rest;
    // hovering a topic lights its curves to its hubs.
    const hoverHub = hovered?.startsWith("h:") ? hovered.slice(2) : null;
    const hoverTopic = hovered && !hovered.startsWith("h:") ? hovered : null;
    // A topic in the left column has no node on the graph, so it lights no hub: nothing would show what it is tied to
    const litHubs = new Set(hoverTopic && pos(hoverTopic) ? (links.hubsOf.get(hoverTopic) ?? []) : []);

    // Focus: while a hub is under the pointer, only that hub and its topics stay lit, the rest of the graph sinks.
    // The fade runs on its own clock, so moving off a hub lets the graph come back instead of snapping
    const fnow = performance.now();
    const fEase = 1 - Math.exp(-Math.min(100, fnow - lastFocus.current) / 110);
    lastFocus.current = fnow;
    // The hub under the pointer wins; with the pointer elsewhere the graph stays focused on the hub picked in the left panel
    const pin = pinHub.current;
    const hubSel = hoverHub ?? pin;
    if (hubSel) focusHub.current = hubSel;
    const fTarget = hubSel ? 1 : 0;
    const f0 = focusMix.current;
    const focus = Math.abs(fTarget - f0) < 0.01 ? fTarget : f0 + (fTarget - f0) * fEase;
    focusMix.current = focus;
    if (focus === 0) focusHub.current = null;
    const focusId = focusHub.current;
    const focusSet = focusId ? new Set([`h:${focusId}`, ...(links.byHub.get(focusId) ?? []).filter((id) => pos(id))]) : null;

    // Indicators: while one is on, whatever it does not light sinks. Same idea as the hub focus, on its own clock
    const iEase = 1 - Math.exp(-Math.min(100, fnow - lastInd.current) / 140);
    lastInd.current = fnow;
    const iTarget = activeRef.current.size ? 1 : 0;
    const i0 = indMix.current;
    const indNow = Math.abs(iTarget - i0) < 0.01 ? iTarget : i0 + (iTarget - i0) * iEase;
    indMix.current = indNow;
    const indSet = indLit.current;
    const indDim = indNow > 0 && indSet.size > 0;
    const indK = 1 - IND_DIM * indNow;
    /** What an indicator leaves of a node's brightness. The node under the pointer and the one a "Today" card points at stay lit */
    const indFor = (key: string) => (indDim && !indSet.has(key) && key !== hovered && !lit.current.includes(key) ? indK : 1);
    /** How much of its brightness a node keeps while a hub is in focus or an indicator is on */
    const bright = (key: string) => (focusSet && !focusSet.has(key) ? 1 - FOCUS_DIM * focus : 1) * indFor(key);
    for (const [id, el] of colEls.current) el.style.opacity = String(bright(id));
    const ringR = clamp(13 * Math.pow(k, 0.6), 5, 15);
    ctx.lineWidth = 1.2;
    for (const [hubId, members] of links.byHub) {
      const hp = pos(`h:${hubId}`);
      if (!hp) continue;
      const hx = sx(hp.x), hy = sy(hp.y);
      const lit = hubSel === hubId;
      const mute = bright(`h:${hubId}`);
      // The foot of the stem sits below the hub on screen; the curves end there
      const sxp = hx, syp = hy + Math.max(ringR + 6, 0.5 * GAP_Y * k);
      let any = false;
      for (const topicId of members) {
        // A topic of the left column has no node, so there is nothing to draw the curve to
        const a = pos(topicId);
        if (!a) continue;
        const ax = sx(a.x), ay = sy(a.y);
        if (!near(ax, ay, 300) && !near(hx, hy, 300)) continue;
        any = true;
        const dist = Math.max(0, ay - hy) / Math.max(k, 1e-6) / GAP_Y; // in layers
        const close = Math.exp(-dist / 4);
        let alpha = 0.06 + 0.24 * close;
        if (lit) alpha = 0.5 + 0.3 * close;
        else if (hoverTopic === topicId) alpha = 0.85;
        alpha *= mute * indFor(topicId);
        const mid = (ay + syp) / 2;
        const g = ctx.createLinearGradient(0, ay, 0, syp);
        g.addColorStop(0, `rgba(${col.ribbonRgb},${(alpha * 0.3).toFixed(3)})`);
        g.addColorStop(1, `rgba(${col.ribbonRgb},${alpha.toFixed(3)})`);
        ctx.strokeStyle = g;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.bezierCurveTo(ax, mid, sxp, mid, sxp, syp);
        ctx.stroke();
      }
      if (any) {
        ctx.strokeStyle = col.ribbon;
        ctx.globalAlpha = (lit || litHubs.has(hubId) ? 0.9 : 0.38) * mute;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(hx, hy + ringR);
        ctx.lineTo(sxp, syp);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.lineWidth = 1.2;
      }
    }

    // Topic to topic: straight and solid, from the prerequisite up to the topic that needs it
    const edges = links.edges;
    const line = (keep: (a: string, b: string) => boolean) => {
      ctx.beginPath();
      for (const e of edges) {
        if (!keep(e.topicId, e.requiresId)) continue;
        const a = pos(e.requiresId);
        const b = pos(e.topicId);
        if (!a || !b) continue;
        const ax = sx(a.x), ay = sy(a.y), bx = sx(b.x), by = sy(b.y);
        if ((ax < 0 && bx < 0) || (ax > w && bx > w) || (ay < 0 && by < 0) || (ay > h && by > h)) continue;
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
      }
      ctx.stroke();
    };
    ctx.strokeStyle = col.dim;
    ctx.lineWidth = 1.4;
    // Links between the topics of the focused hub stay, and so do links between two lit topics; every other link sinks
    const inFocus = (a: string, b: string) => !!focusSet && focusSet.has(a) && focusSet.has(b);
    const bothLit = (a: string, b: string) => indSet.has(a) && indSet.has(b);
    for (const focused of focusSet ? [false, true] : [true]) {
      for (const marked of indDim ? [false, true] : [true]) {
        ctx.globalAlpha = 0.6 * (focused ? 1 : 1 - FOCUS_DIM * focus) * (marked ? 1 : indK);
        line((a, b) => (!focusSet || inFocus(a, b) === focused) && (!indDim || bothLit(a, b) === marked));
      }
    }
    if (hovered && !hovered.startsWith("h:")) {
      ctx.strokeStyle = col.paper;
      ctx.globalAlpha = 0.95;
      line((a, b) => a === hovered || b === hovered);
    }
    ctx.globalAlpha = 1;

    // Topics
    const rT = clamp(7 * Math.pow(k, 0.7), 2.5, 8.5);
    for (const n of L.topics) {
      const p = pos(n.key);
      const t = topics.get(n.id);
      if (!p || !t) continue;
      const x = sx(p.x), y = sy(p.y);
      if (!near(x, y, 20)) continue;
      const st = stateOf(t, m.nowMs);
      const hollow = st === "new";
      ctx.globalAlpha = (t.status === "proposed" ? 0.6 : 1) * bright(n.key);
      ctx.beginPath();
      ctx.arc(x, y, n.key === hovered ? rT + 2 : rT, 0, Math.PI * 2);
      if (hollow) {
        ctx.fillStyle = col.cloth;
        ctx.fill();
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = col.new;
        ctx.stroke();
      } else {
        ctx.fillStyle = col[st];
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;

    // Hubs: bigger than topics, ring with a dot. Deadline hubs look the same as the others
    const rH = clamp(13 * Math.pow(k, 0.6), 5, 15);
    for (const n of L.hubs) {
      const p = pos(n.key);
      if (!p) continue;
      const x = sx(p.x), y = sy(p.y);
      if (!near(x, y, 40)) continue;
      const r = n.key === hovered || n.id === hubSel || litHubs.has(n.id) ? rH + 2 : rH;
      ctx.globalAlpha = bright(n.key);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = col.cloth;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = col.ribbon;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, r * 0.38, 0, Math.PI * 2);
      ctx.fillStyle = col.ribbon;
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // Indicator halos. Each indicator has its own colour and its own form, so colour is never the only clue.
    // A node under several indicators gets several rings, one outside the other, never a mixed colour.
    // A halo fades in and out on its own clock, and a node that disappeared lets its halo fade where it stood.
    const hnow = performance.now();
    const hEase = 1 - Math.exp(-Math.min(100, hnow - lastHalo.current) / 140);
    lastHalo.current = hnow;
    let haloSettling = false;
    const wanted = new Set<string>();
    for (const [key, ids] of marks.current) for (const id of ids) wanted.add(`${key}|${id}`);
    for (const hk of wanted) if (!halo.current.has(hk)) halo.current.set(hk, 0);
    const rings = new Map<string, { id: IndicatorId; v: number }[]>();
    for (const [hk, v0] of halo.current) {
      const target = wanted.has(hk) ? 1 : 0;
      const v = Math.abs(target - v0) < 0.015 ? target : v0 + (target - v0) * hEase;
      if (v !== target) haloSettling = true;
      if (target === 0 && v === 0) {
        halo.current.delete(hk);
        continue;
      }
      halo.current.set(hk, v);
      const [key, id] = hk.split("|") as [string, IndicatorId];
      rings.set(key, [...(rings.get(key) ?? []), { id, v }]);
    }
    for (const [key, list] of rings) {
      const live = pos(key);
      if (live) haloAt.current.set(key, live);
      const p = live ?? haloAt.current.get(key);
      if (!p) continue;
      const x = sx(p.x), y = sy(p.y);
      if (!near(x, y, 60)) continue;
      list.sort((a, b) => INDICATORS.indexOf(a.id) - INDICATORS.indexOf(b.id));
      const base = (key.startsWith("h:") ? ringR : rT) + 4;
      list.forEach(({ id, v }, i) => {
        const rr = base + 5 * i;
        ctx.save();
        ctx.globalAlpha = 0.95 * v;
        ctx.strokeStyle = col.ind[id];
        ctx.shadowColor = col.ind[id];
        ctx.shadowBlur = 9;
        ctx.lineWidth = 2;
        if (id === "relevant") ctx.setLineDash([4, 3]);
        if (id === "review") {
          ctx.setLineDash([0.1, 4]);
          ctx.lineCap = "round";
          ctx.lineWidth = 2.6;
        }
        ctx.beginPath();
        ctx.arc(x, y, id === "weak" ? rr + 1.5 : rr, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
        if (id === "weak") {
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(x, y, rr - 2, 0, Math.PI * 2);
          ctx.stroke();
        }
        if (id === "deadline") {
          // Ticks like a dial
          ctx.lineWidth = 1.6;
          ctx.beginPath();
          for (let q = 0; q < 12; q++) {
            const a = (q * Math.PI) / 6;
            ctx.moveTo(x + Math.cos(a) * (rr + 1), y + Math.sin(a) * (rr + 1));
            ctx.lineTo(x + Math.cos(a) * (rr + 4.5), y + Math.sin(a) * (rr + 4.5));
          }
          ctx.stroke();
        }
        ctx.restore();
      });
    }

    // What the camera does not show: an arrow toward it, per direction and indicator. Review gets none: its few nodes are not a debt to chase
    if (marks.current.size) {
      const areaW = w - colW;
      const cxA = colW + areaW / 2, cyA = h / 2;
      const groups = new Map<string, { dir: Arrow["dir"]; ind: IndicatorId; n: number; key: string; d: number; sx: number; sy: number }>();
      for (const [key, ids] of marks.current) {
        const p = pos(key);
        if (!p) continue;
        const x = sx(p.x), y = sy(p.y);
        if (x > colW + 6 && x < w - 6 && y > 6 && y < h - 6) continue;
        const dx = x - cxA, dy = y - cyA;
        const dir: Arrow["dir"] = Math.abs(dx) / (areaW / 2) > Math.abs(dy) / (h / 2) ? (dx < 0 ? "left" : "right") : dy < 0 ? "up" : "down";
        const d = Math.hypot(dx, dy);
        for (const id of ids) {
          if (id === "review") continue;
          const gk = `${dir}|${id}`;
          const g = groups.get(gk);
          if (!g) groups.set(gk, { dir, ind: id, n: 1, key, d, sx: x, sy: y });
          else {
            g.n++;
            if (d < g.d) Object.assign(g, { key, d, sx: x, sy: y });
          }
        }
      }
      const seenDir = new Map<string, number>();
      const list: Arrow[] = [...groups.values()]
        .sort((a, b) => INDICATORS.indexOf(a.ind) - INDICATORS.indexOf(b.ind))
        .map((g) => {
          const idx = seenDir.get(g.dir) ?? 0;
          seenDir.set(g.dir, idx + 1);
          const along = (v: number, lo: number, hi: number) => clamp(v, lo, hi);
          const off = idx * 46;
          const x = g.dir === "left" ? colW + 22 : g.dir === "right" ? w - 22 : along(g.sx, colW + 40, w - 40) + off;
          const y = g.dir === "up" ? 22 : g.dir === "down" ? h - 22 : along(g.sy, 40, h - 40) + off;
          return { id: `${g.dir}|${g.ind}`, dir: g.dir, ind: g.ind, n: g.n, x: Math.round(x), y: Math.round(y), key: g.key };
        });
      const sig = list.map((a) => `${a.id}:${a.n}:${Math.round(a.x / 12)}:${Math.round(a.y / 12)}`).join(",");
      if (sig !== arrowSig.current && hnow - arrowAt.current > 70) {
        arrowSig.current = sig;
        arrowAt.current = hnow;
        setArrows(list);
      }
    } else if (arrowSig.current) {
      arrowSig.current = "";
      setArrows([]);
    }

    // The topic or hub of the "Today" card under the pointer
    if (lit.current.length) {
      for (const key of lit.current) {
        const p = pos(key);
        if (!p) continue;
        const x = sx(p.x), y = sy(p.y);
        const r0 = (key.startsWith("h:") ? rH : rT) + 7;
        ctx.globalAlpha = 0.18;
        ctx.fillStyle = col.ribbon;
        ctx.beginPath();
        ctx.arc(x, y, r0, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 0.85;
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = col.ribbon;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Nodes that changed while the learner was looking: a pulse that fades out. The camera does not move for them
    if (flash.current.size) {
      const t0 = performance.now();
      ctx.lineWidth = 2;
      ctx.strokeStyle = col.ribbon;
      for (const [key, start] of flash.current) {
        const age = (t0 - start) / FLASH_MS;
        if (age >= 1) {
          flash.current.delete(key);
          continue;
        }
        const p = pos(key);
        if (!p) continue;
        const phase = (age * 2) % 1;
        ctx.globalAlpha = 0.9 * (1 - age) * (1 - phase);
        ctx.beginPath();
        ctx.arc(sx(p.x), sy(p.y), (key.startsWith("h:") ? rH : rT) + 4 + 14 * phase, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Labels. A topic label fades with the size of a slot on screen, a hub label later: on the far view the hubs are
    // what the eye holds on to. Labels never overlap: the more important one stays (hub, then hovered, then busier topic).
    const now = performance.now();
    const dt = Math.min(100, now - lastDraw.current);
    lastDraw.current = now;
    const ease = 1 - Math.exp(-dt / 90);
    const unit = GAP_X * k;
    const fadeTopic = smooth(40, 78, unit);
    const fadeHub = smooth(8, 26, unit);
    type Label = { key: string; text: string; x: number; y: number; above: boolean; prio: number; fade: number; deg: number; fill: string; font: string };
    const labels: Label[] = [];
    const hubFont = `500 14px ${col.body}`;
    const topicFont = `400 11px ${col.body}`;
    for (const n of L.hubs) {
      const p = pos(n.key);
      if (!p) continue;
      const x = sx(p.x), y = sy(p.y);
      if (!near(x, y, 120)) continue;
      labels.push({ key: n.key, text: hubs.get(n.id)?.name ?? "", x, y: y - rH - 5, above: true, prio: 3, fade: marks.current.has(n.key) ? 1 : fadeHub, deg: 0, fill: col.paper, font: hubFont });
    }
    for (const n of L.topics) {
      const p = pos(n.key);
      const t = topics.get(n.id);
      if (!p || !t) continue;
      const x = sx(p.x), y = sy(p.y);
      if (!near(x, y, 120)) continue;
      const hot = n.key === hovered;
      // The label of a node an indicator lights is shown at any zoom
      const pin = marks.current.has(n.key);
      labels.push({ key: n.key, text: t.name, x, y: y + rT + 4, above: false, prio: pin ? 3 : hot ? 2 : 1, fade: pin || hot ? 1 : fadeTopic, deg: links.degree.get(n.id) ?? 0, fill: hot ? col.paper : col.dim, font: topicFont });
    }
    labels.sort((a, b) => b.prio - a.prio || b.deg - a.deg || (a.key < b.key ? -1 : 1));
    // Boxes already taken, in a coarse grid so a crowded graph stays cheap
    const CELL = 80;
    const grid = new Map<string, { x0: number; y0: number; x1: number; y1: number }[]>();
    const cellsOf = (bx: { x0: number; y0: number; x1: number; y1: number }) => {
      const out: string[] = [];
      for (let gx = Math.floor(bx.x0 / CELL); gx <= Math.floor(bx.x1 / CELL); gx++) for (let gy = Math.floor(bx.y0 / CELL); gy <= Math.floor(bx.y1 / CELL); gy++) out.push(`${gx},${gy}`);
      return out;
    };
    let settling = false;
    ctx.textAlign = "center";
    ctx.lineJoin = "round";
    ctx.textBaseline = "alphabetic";
    for (const lb of labels) {
      let target = 0;
      if (lb.fade > 0.02) {
        const wKey = `${lb.font}|${lb.text}`;
        let tw = textW.current.get(wKey);
        if (tw === undefined) {
          ctx.font = lb.font;
          tw = ctx.measureText(lb.text).width;
          textW.current.set(wKey, tw);
        }
        const hgt = 13;
        const bx = { x0: lb.x - tw / 2 - 3, x1: lb.x + tw / 2 + 3, y0: lb.above ? lb.y - hgt : lb.y - 1, y1: lb.above ? lb.y + 2 : lb.y + hgt };
        const cells = cellsOf(bx);
        const clash = cells.some((c) => (grid.get(c) ?? []).some((o) => bx.x0 < o.x1 && bx.x1 > o.x0 && bx.y0 < o.y1 && bx.y1 > o.y0));
        if (!clash) {
          target = 1;
          for (const c of cells) grid.set(c, [...(grid.get(c) ?? []), bx]);
        }
      }
      const was = labelVis.current.get(lb.key) ?? 0;
      const vis = Math.abs(target - was) < 0.01 ? target : was + (target - was) * ease;
      if (vis !== target) settling = true;
      labelVis.current.set(lb.key, vis);
      const alpha = lb.fade * vis * bright(lb.key);
      if (alpha < 0.02) continue;
      ctx.font = lb.font;
      ctx.globalAlpha = alpha;
      ctx.lineWidth = lb.above ? 4 : 3;
      ctx.strokeStyle = col.cloth;
      ctx.textBaseline = lb.above ? "bottom" : "top";
      ctx.strokeText(lb.text, lb.x, lb.y);
      ctx.fillStyle = lb.fill;
      ctx.fillText(lb.text, lb.x, lb.y);
    }
    ctx.globalAlpha = 1;
    return settling || haloSettling || flash.current.size > 0 || focus !== fTarget || indNow !== iTarget;
  }, []);

  const frame = useCallback(
    (now: number) => {
      raf.current = 0;
      let again = false;
      if (posTween.current !== null) {
        const t = clamp((now - posTween.current) / posMs.current, 0, 1);
        const e = easeOut(t);
        for (const [key, to] of target.current) {
          const f = from.current.get(key) ?? to;
          shown.current.set(key, { x: f.x + (to.x - f.x) * e, y: f.y + (to.y - f.y) * e });
        }
        if (t >= 1) posTween.current = null;
        else again = true;
      }
      const vt = viewTween.current;
      if (vt) {
        const t = clamp((now - vt.start) / TWEEN_MS, 0, 1);
        const e = easeOut(t);
        view.current = { k: vt.from.k + (vt.to.k - vt.from.k) * e, tx: vt.from.tx + (vt.to.tx - vt.from.tx) * e, ty: vt.from.ty + (vt.to.ty - vt.from.ty) * e };
        if (t >= 1) viewTween.current = null;
        else again = true;
      }
      if (draw()) again = true;
      if (again) raf.current = requestAnimationFrame(loop.current);
    },
    [draw],
  );

  useEffect(() => {
    loop.current = frame;
  }, [frame]);

  const schedule = useCallback(() => {
    if (!raf.current) raf.current = requestAnimationFrame(loop.current);
  }, []);

  // Where the camera stands, kept per domain on this device: the world point at the centre of the graph area, the zoom,
  // and the topic nearest to the centre (with a few neighbours) in case that topic is gone next time
  const saveNow = useCallback(() => {
    clearTimeout(camTimer.current);
    const { w, h } = sizeRef.current;
    if (!w || !h) return;
    const cw = scene.current.colW;
    // While an indicator holds the camera, the place worth keeping is the one from before the click
    const v = activeRef.current.size && before.current ? before.current : view.current;
    const cx = (cw + (w - cw) / 2 - v.tx) / v.k;
    const cy = (h / 2 - v.ty) / v.k;
    const near = [...target.current]
      .filter(([key]) => !key.startsWith("h:"))
      .map(([id, p]) => ({ id, x: p.x, y: p.y, d: Math.hypot(p.x - cx, p.y - cy) }))
      .sort((a, b) => a.d - b.d)
      .slice(0, 4)
      .map(({ id, x, y }) => ({ id, x, y }));
    const cam: SavedCamera = { k: v.k, cx, cy, anchor: near[0], near: near.slice(1) };
    saveCamera(LEARNER, scene.current.model.domainId, cam);
  }, []);
  const queueSave = useCallback(() => {
    if (activeRef.current.size) return;
    clearTimeout(camTimer.current);
    camTimer.current = setTimeout(saveNow, 500);
  }, [saveNow]);

  const moveView = useCallback(
    (to: View, animate: boolean) => {
      if (animate && !reducedMotion()) viewTween.current = { start: performance.now(), from: view.current, to };
      else {
        viewTween.current = null;
        view.current = to;
      }
      schedule();
      queueSave();
    },
    [schedule, queueSave],
  );

  const fitView = useCallback((w: number, h: number): View => {
    const { x0, x1, y0, y1 } = scene.current.layout.bounds;
    // The graph lives to the right of the column
    const cw = scene.current.colW;
    const k = clamp(Math.min((w - cw - FIT_MARGIN * 2) / Math.max(1, x1 - x0), (h - FIT_MARGIN * 2) / Math.max(1, y1 - y0)), K_MIN, FIT_K_MAX);
    return { k, tx: cw + (w - cw) / 2 - ((x0 + x1) / 2) * k, ty: h / 2 - ((y0 + y1) / 2) * k };
  }, []);

  // First view of a domain: where the learner stood last time, else the whole graph
  const initialView = useCallback(
    (w: number, h: number): View => {
      const saved = loadCamera(LEARNER, scene.current.model.domainId);
      if (!saved) return fitView(w, h);
      const k = clamp(saved.k, K_MIN, K_MAX);
      const c = restoreCenter(saved, (id) => target.current.get(id));
      const cw = scene.current.colW;
      return { k, tx: cw + (w - cw) / 2 - c.x * k, ty: h / 2 - c.y * k };
    },
    [fitView],
  );

  const zoomAt = useCallback(
    (cx: number, cy: number, factor: number) => {
      const v = viewTween.current?.to ?? view.current;
      const k = clamp(v.k * factor, K_MIN, K_MAX);
      const f = k / v.k;
      moveView({ k, tx: cx - (cx - v.tx) * f, ty: cy - (cy - v.ty) * f }, false);
    },
    [moveView],
  );

  const fitAll = useCallback(() => moveView(fitView(sizeRef.current.w, sizeRef.current.h), true), [fitView, moveView]);

  // A click on the subject name: the whole graph goes to the middle. On a fresh mount the first view does it (see below)
  const centerRef = useRef(center);
  const centeredRef = useRef(onCentered);
  useEffect(() => {
    centerRef.current = center;
    centeredRef.current = onCentered;
  });
  useEffect(() => {
    if (!center || !fitted.current) return;
    fitAll();
    onCentered?.();
  }, [center, fitAll, onCentered]);

  // Colours and size
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    colors.current = readColors(el);
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      setSize({ w: Math.round(r.width), h: Math.round(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    sizeRef.current = size;
    const c = canvas.current;
    if (c && size.w && size.h) {
      const dpr = window.devicePixelRatio || 1;
      c.width = Math.round(size.w * dpr);
      c.height = Math.round(size.h * dpr);
      if (!fitted.current) {
        fitted.current = true;
        if (centerRef.current) {
          view.current = fitView(size.w, size.h);
          centeredRef.current?.();
        } else view.current = initialView(size.w, size.h);
      }
      // Resizing clears the canvas: paint at once so there is no blank frame
      draw();
    }
    schedule();
  }, [size, initialView, fitView, schedule, draw]);

  // A new layout: known nodes stay, whatever moved glides to its place
  useEffect(() => {
    const was = scene.current.layout.unlinked;
    scene.current = { layout, model, topicMap, hubMap, colW: column.width, links };
    savePositions(LEARNER, model.domainId, layout);
    const first = shown.current.size === 0;
    from.current = new Map(shown.current);
    target.current = new Map([...layout.nodes.values()].map((n) => [n.key, { x: n.x, y: n.y }]));
    // A new node appears where it belongs, it does not fly in from the corner.
    // A topic that just got a link starts from its spot in the column, so the learner sees where it went
    let fromColumn = false;
    const { k, tx: vx, ty: vy } = view.current;
    for (const [key, to] of target.current) {
      if (from.current.has(key)) continue;
      const cy = was.includes(key) ? colY.current.get(key) : undefined;
      if (!first && cy !== undefined) {
        from.current.set(key, { x: (COL_W + 6 - vx) / k, y: (cy - vy) / k });
        fromColumn = true;
      } else from.current.set(key, to);
    }
    posMs.current = fromColumn ? MOVE_MS : TWEEN_MS;
    if (first || reducedMotion()) {
      shown.current = new Map(target.current);
      posTween.current = null;
    } else posTween.current = performance.now();
    schedule();
  }, [layout, model, topicMap, hubMap, column.width, links, schedule]);

  // Where each column item stands (for the lines to hubs). Measured after the column is laid out and whenever it scrolls
  const measureColumn = useCallback(() => {
    const box = colScroll.current;
    const next = new Map<string, number>();
    if (box) for (const [id, el] of colEls.current) next.set(id, el.offsetTop - box.scrollTop + el.offsetHeight / 2);
    colY.current = next;
    schedule();
  }, [schedule]);
  useEffect(() => {
    const id = requestAnimationFrame(measureColumn);
    return () => cancelAnimationFrame(id);
  }, [column, size.h, measureColumn]);

  // Strict Mode runs this cleanup once in development: forget the cancelled frame so the loop can restart
  useEffect(
    () => () => {
      cancelAnimationFrame(raf.current);
      raf.current = 0;
    },
    [],
  );

  // What the learner has seen: the camera is saved when it settles and when they leave
  useEffect(() => {
    const domainId = model.domainId;
    enterDomain(domainId, scene.current.model);
    const onHide = () => {
      saveNow();
      saveAllVisits();
    };
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      saveNow();
      leaveDomain(domainId);
    };
  }, [model.domainId, saveNow]);

  // Indicators. What each would light is published for the pills; what is switched on lights the graph
  const visit = useSyncExternalStore(subscribeVisit, () => getVisit(model.domainId), () => getVisit(model.domainId));
  const selections = useMemo(() => computeIndicators(model, visit), [model, visit]);
  const ind = useSyncExternalStore(subscribeIndicators, getIndicators, getIndicatorsServer);
  useEffect(() => {
    publishIndicators(model.domainId, selections);
  }, [model.domainId, selections]);
  useEffect(() => () => unmountIndicators(model.domainId), [model.domainId]);

  useEffect(() => {
    const m = new Map<string, IndicatorId[]>();
    for (const id of ind.active) {
      const s = ind.selections[id];
      for (const t of s.topics) if (layout.nodes.has(t)) m.set(t, [...(m.get(t) ?? []), id]);
      for (const h of s.hubs) if (layout.nodes.has(`h:${h}`)) m.set(`h:${h}`, [...(m.get(`h:${h}`) ?? []), id]);
    }
    marks.current = m;
    const litNow = new Set<string>();
    for (const id of ind.active) {
      const s = ind.selections[id];
      for (const t of s.topics) litNow.add(t);
      for (const h of s.hubs) litNow.add(`h:${h}`);
    }
    if (litNow.size) indLit.current = litNow;
    activeRef.current = ind.active;
    schedule();
  }, [ind, layout, schedule]);

  // Switching an indicator on or off moves the camera so that everything lit is in view, with its labels. Data updates never do
  useEffect(() => {
    const act = ind.active;
    const was = prevActive.current;
    prevActive.current = act;
    if (act === was) return;
    if (act.size === 0) {
      if (before.current) {
        moveView(before.current, true);
        before.current = null;
      }
      return;
    }
    if (was.size === 0 && !before.current) before.current = viewTween.current?.to ?? view.current;
    const pts: Pt[] = [];
    for (const id of act) {
      const s = ind.selections[id];
      for (const t of s.topics) {
        const p = target.current.get(t);
        if (p) pts.push(p);
      }
      for (const h of s.hubs) {
        const p = target.current.get(`h:${h}`);
        if (p) pts.push(p);
      }
    }
    if (!pts.length) return;
    const { w, h } = sizeRef.current;
    const cw = scene.current.colW;
    const aw = w - cw;
    const M = 90;
    const x0 = Math.min(...pts.map((p) => p.x)), x1 = Math.max(...pts.map((p) => p.x));
    const y0 = Math.min(...pts.map((p) => p.y)), y1 = Math.max(...pts.map((p) => p.y));
    const fit = Math.min((aw - 2 * M) / Math.max(1, x1 - x0), (h - 2 * M) / Math.max(1, y1 - y0));
    let k: number, cx: number, cy: number;
    if (fit >= K_READ) {
      k = Math.min(fit, 1);
      cx = (x0 + x1) / 2;
      cy = (y0 + y1) / 2;
    } else {
      // Too spread out to read: stand on the densest cluster at a readable zoom; the rest gets arrows
      k = K_READ;
      const hw = (aw / 2 - M) / k, hh = (h / 2 - M) / k;
      let best = { n: -1, cx: pts[0].x, cy: pts[0].y };
      for (const c of pts) {
        const inside = pts.filter((p) => Math.abs(p.x - c.x) <= hw && Math.abs(p.y - c.y) <= hh);
        if (inside.length > best.n) best = { n: inside.length, cx: inside.reduce((a, p) => a + p.x, 0) / inside.length, cy: inside.reduce((a, p) => a + p.y, 0) / inside.length };
      }
      cx = best.cx;
      cy = best.cy;
    }
    moveView({ k: clamp(k, K_MIN, K_MAX), tx: cw + aw / 2 - cx * k, ty: h / 2 - cy * k }, true);
  }, [ind, moveView]);

  // Esc switches every indicator off and the camera goes back
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (activeRef.current.size) clearIndicators();
      else if (pinHub.current) clearPinRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // A hub picked in the left panel: the graph focuses on it, as if it had been picked on the graph, and the camera brings its cluster into view
  useEffect(() => {
    clearPinRef.current = onClearFocus;
    pinHub.current = focusHubId;
    schedule();
  }, [focusHubId, onClearFocus, schedule]);

  useEffect(() => {
    if (!focusHubId) {
      pinApplied.current = null;
      return;
    }
    const { w, h } = size;
    const hp = target.current.get(`h:${focusHubId}`);
    if (pinApplied.current === focusHubId || !w || !h || !hp) return;
    pinApplied.current = focusHubId;
    const pts = [hp, ...(links.byHub.get(focusHubId) ?? []).map((id) => target.current.get(id)).filter((p): p is Pt => !!p)];
    const cw = scene.current.colW;
    const aw = w - cw;
    const M = 70;
    const x0 = Math.min(...pts.map((p) => p.x)), x1 = Math.max(...pts.map((p) => p.x));
    const y0 = Math.min(...pts.map((p) => p.y)), y1 = Math.max(...pts.map((p) => p.y));
    const k = clamp(Math.min((aw - 2 * M) / Math.max(1, x1 - x0), (h - 2 * M) / Math.max(1, y1 - y0), 1), K_READ, K_MAX);
    moveView({ k, tx: cw + aw / 2 - ((x0 + x1) / 2) * k, ty: h / 2 - ((y0 + y1) / 2) * k }, true);
  }, [focusHubId, size, layout, links, moveView]);

  const goToArrow = (a: Arrow) => {
    const p = target.current.get(a.key);
    if (!p) return;
    const { w, h } = sizeRef.current;
    const cw = scene.current.colW;
    const k = clamp(Math.max(view.current.k, K_READ), K_MIN, K_MAX);
    moveView({ k, tx: cw + (w - cw) / 2 - p.x * k, ty: h / 2 - p.y * k }, true);
  };

  // A "Today" card under the pointer lights its node
  useEffect(() => {
    lit.current = [...(highlight?.topicIds ?? []), ...(highlight?.hubIds ?? []).map((id) => `h:${id}`)];
    schedule();
  }, [highlight, schedule]);

  // The graph changed while it is on screen: what changed pulses, and counts as new. The camera stays where it is
  useEffect(() => {
    const fresh = updateDomain(model.domainId, model);
    const t0 = performance.now();
    for (const id of fresh.topics) flash.current.set(id, t0);
    for (const id of fresh.hubs) flash.current.set(`h:${id}`, t0);
    if (fresh.topics.size || fresh.hubs.size) schedule();
  }, [model, schedule]);

  // A wheel handler must be non-passive to stop the page from scrolling
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const hitTest = (px: number, py: number): string | null => {
    const { k, tx: vx, ty: vy } = view.current;
    const rT = clamp(7 * Math.pow(k, 0.7), 2.5, 8.5) + 6;
    const rH = clamp(13 * Math.pow(k, 0.6), 5, 15) + 6;
    let best: string | null = null;
    let bestD = Infinity;
    for (const n of [...layout.hubs, ...layout.topics]) {
      const p = shown.current.get(n.key);
      if (!p) continue;
      const d = Math.hypot(p.x * k + vx - px, p.y * k + vy - py);
      if (d <= (n.kind === "hub" ? rH : rT) && d < bestD) {
        best = n.key;
        bestD = d;
      }
    }
    return best;
  };

  const setHoverKey = (key: string | null, px = 0, py = 0) => {
    if (hoverKey.current === key) return;
    hoverKey.current = key;
    schedule();
    clearTimeout(hideTimer.current);
    if (key && !key.startsWith("h:")) setHover({ id: key, x: px, y: py });
    else if (key) setHover(null);
    else hideTimer.current = setTimeout(() => setHover(null), 220);
  };

  const local = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const p = local(e);
    viewTween.current = null;
    drag.current = { x: p.x, y: p.y, vx: view.current.tx, vy: view.current.ty, moved: false };
    canvas.current!.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const p = local(e);
    const d = drag.current;
    if (d) {
      if (!d.moved && Math.hypot(p.x - d.x, p.y - d.y) > 4) {
        d.moved = true;
        setHoverKey(null);
        setHover(null);
      }
      if (d.moved) {
        view.current = { ...view.current, tx: d.vx + p.x - d.x, ty: d.vy + p.y - d.y };
        schedule();
      }
      return;
    }
    if (e.pointerType === "touch") return;
    const key = hitTest(p.x, p.y);
    setHoverKey(key, p.x, p.y);
    canvas.current!.style.cursor = key ? "pointer" : "grab";
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (canvas.current?.hasPointerCapture(e.pointerId)) canvas.current.releasePointerCapture(e.pointerId);
    if (d?.moved) queueSave();
    if (!d || d.moved) return;
    const p = local(e);
    const key = hitTest(p.x, p.y);
    if (key) router.push(key.startsWith("h:") ? `/hub/${key.slice(2)}` : `/topic/${key}`);
    else if (pinHub.current) clearPinRef.current?.();
  };

  const focusTopic = useCallback(
    (id: string) => {
      const p = target.current.get(id);
      if (!p) return;
      const { w, h } = sizeRef.current;
      const k = clamp(Math.max(view.current.k, 0.9), K_MIN, K_MAX);
      const cw = scene.current.colW;
      const mid = cw + (w - cw) / 2;
      moveView({ k, tx: mid - p.x * k, ty: h / 2 - p.y * k }, true);
      hoverKey.current = id;
      setHover({ id, x: mid, y: h / 2 });
    },
    [moveView],
  );
  useImperativeHandle(ref, () => ({ focusTopic }), [focusTopic]);

  const enterItem = (id: string) => {
    clearTimeout(hideTimer.current);
    hoverKey.current = id;
    setHover({ id, x: COL_W, y: colY.current.get(id) ?? 0 });
  };

  /** One group of the column. Names wrap instead of being cut, so a label is always whole */
  const group = (title: string, items: { topic: GraphTopic; domainName?: string }[], first: boolean) =>
    items.length === 0 ? null : (
      <section className={first ? "" : "mt-4 border-t border-seam-soft pt-3"}>
        <h3 className="caps px-4 pb-1 text-paper-dim">{title}</h3>
        <ul>
          {items.map(({ topic, domainName }) => {
            const rings = INDICATORS.filter((id) => ind.active.has(id) && ind.selections[id].topics.includes(topic.id));
            return (
              <li
                key={topic.id}
                ref={(el) => {
                  if (el) colEls.current.set(topic.id, el);
                  else colEls.current.delete(topic.id);
                }}
                onPointerEnter={() => enterItem(topic.id)}
                onPointerLeave={() => setHoverKey(null)}
              >
                <Link
                  href={`/topic/${topic.id}`}
                  className="flex items-start gap-2.5 px-4 py-2 text-small text-paper outline-none transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-cloth-hover focus-visible:bg-cloth-hover"
                  style={{ opacity: topic.status === "proposed" ? 0.65 : 1 }}
                >
                  <span className="relative mt-1.5 inline-flex shrink-0">
                    <StateDot state={stateOf(topic, model.nowMs)} />
                    {rings.map((id, i) => (
                      <span
                        key={id}
                        aria-hidden
                        className="pointer-events-none absolute rounded-full"
                        style={{
                          inset: -(3.5 + 3.5 * i),
                          border: `${id === "weak" ? 3 : 1.5}px ${RING_STYLE[id]} ${IND_VAR[id]}`,
                          ...(id === "deadline" ? { outline: `1.5px dotted ${IND_VAR[id]}`, outlineOffset: 1.5 } : null),
                        }}
                      />
                    ))}
                  </span>
                  <span className="min-w-0">
                    {topic.name}
                    {domainName && <span className="caps mt-0.5 block text-paper-dim">{domainName}</span>}
                    {rings.length > 0 && <span className="sr-only"> ({rings.map((id) => tx.graphInd[id]).join(", ")})</span>}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>
    );

  const hoverTopic = hover ? lookup.get(hover.id) : undefined;
  const hoverMock = hover ? domain.topics.find((t) => t.id === hover.id) : undefined;
  const hubCount = model.hubs.filter((h) => h.kind !== "domain_all").length;
  const topicCount = model.topics.filter(onGraph).length;
  const allHub = model.hubs.find((h) => h.kind === "domain_all");
  // Centre of the part of the box the graph can use
  const cx = column.width + (size.w - column.width) / 2;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="@container flex min-h-10 shrink-0 flex-wrap items-center gap-x-3 border-b border-seam-soft text-paper-dim">
        <ul className="order-last flex basis-full flex-wrap items-center gap-x-3 pb-2 @[40rem]:order-none @[40rem]:min-w-0 @[40rem]:flex-1 @[40rem]:basis-auto @[40rem]:pb-0" aria-label={tx.graph.statesLabel}>
          {(["solid", "review", "confused", "new"] as const).map((s) => (
            <li key={s} className="caps flex items-center gap-2 whitespace-nowrap">
              <StateDot state={s} />
              {tx.states[s].toLowerCase()}
            </li>
          ))}
        </ul>
        <div className="ml-auto flex shrink-0 items-center">
          <div className="flex" role="group" aria-label={tx.graph.zoomGroup}>
            <IconButton label={tx.graph.zoomIn} onClick={() => zoomAt(cx, size.h / 2, 1.25)}>
              <Plus />
            </IconButton>
            <IconButton label={tx.graph.zoomOut} onClick={() => zoomAt(cx, size.h / 2, 0.8)}>
              <Minus />
            </IconButton>
            <IconButton label={tx.graph.fitAll} onClick={fitAll}>
              <Maximize2 />
            </IconButton>
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div
          ref={wrap}
          tabIndex={0}
          role="application"
          aria-label={tx.graph.ariaGraph(domain.title)}
          className="relative min-h-0 min-w-0 flex-1 overflow-hidden rounded-[2px] outline-none select-none focus-visible:ring-2 focus-visible:ring-ribbon/60"
          onKeyDown={(e) => {
            if (e.target !== e.currentTarget) return;
            const pan = (dx: number, dy: number) => moveView({ ...view.current, tx: view.current.tx + dx, ty: view.current.ty + dy }, false);
            const map: Record<string, () => void> = {
              ArrowLeft: () => pan(60, 0),
              ArrowRight: () => pan(-60, 0),
              ArrowUp: () => pan(0, 60),
              ArrowDown: () => pan(0, -60),
              "+": () => zoomAt(cx, size.h / 2, 1.25),
              "=": () => zoomAt(cx, size.h / 2, 1.25),
              "-": () => zoomAt(cx, size.h / 2, 0.8),
              "0": fitAll,
            };
            const fn = map[e.key];
            if (fn) {
              e.preventDefault();
              fn();
            }
          }}
        >
          <canvas
            ref={canvas}
            className="absolute inset-0 cursor-grab touch-none"
            style={{ width: size.w, height: size.h }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onPointerLeave={() => !drag.current && setHoverKey(null)}
          />

          {arrows.length > 0 && (
            <ul className="pointer-events-none absolute inset-0 z-[6]">
              {arrows.map((a) => {
                const Icon = a.dir === "up" ? ArrowUp : a.dir === "down" ? ArrowDown : a.dir === "left" ? ArrowLeft : ArrowRight;
                return (
                  <li key={a.id} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: a.x, top: a.y }}>
                    <button
                      type="button"
                      onClick={() => goToArrow(a)}
                      aria-label={tx.graphInd.arrow(tx.graphInd[a.ind], tx.graphInd[a.dir], a.n)}
                      style={{ borderColor: IND_VAR[a.ind], color: IND_VAR[a.ind] }}
                      className="pointer-events-auto inline-flex h-10 min-w-10 items-center justify-center gap-1 rounded-pill border bg-cloth-deep px-2.5 transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-cloth-raised focus-visible:outline-2 focus-visible:outline-ribbon"
                    >
                      <Icon className="size-4" aria-hidden />
                      <span className="num text-small">{a.n}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {column.width > 0 && (
            <nav
              ref={colScroll}
              aria-label={tx.graph.columnLabel}
              onScroll={measureColumn}
              className="absolute inset-y-0 left-0 z-[5] overflow-y-auto border-r border-seam-soft bg-cloth py-3"
              style={{ width: column.width }}
            >
              {group(tx.graph.columnFree, column.free.map((t) => ({ topic: t })), true)}
              {group(tx.graph.columnOther, column.outside, column.free.length === 0)}
            </nav>
          )}

          {/* Real links for keyboard and screen readers. Small graphs only: 500 tab stops would be worse than none */}
          {layout.nodes.size <= 150 && (
            <ul className="sr-only">
              {layout.hubs.map((n) => (
                <li key={n.key}>
                  <Link href={`/hub/${n.id}`}>{hubMap.get(n.id)?.name}</Link>
                </li>
              ))}
              {layout.topics.map((n) => (
                <li key={n.key}>
                  <Link href={`/topic/${n.id}`}>{topicMap.get(n.id)?.name}</Link>
                </li>
              ))}
            </ul>
          )}

          {hover && hoverTopic && (
            <div
              data-card
              onPointerEnter={() => clearTimeout(hideTimer.current)}
              onPointerLeave={() => {
                hoverKey.current = null;
                hideTimer.current = setTimeout(() => setHover(null), 220);
              }}
              className="absolute z-10 rounded-[2px] border border-seam bg-cloth-raised p-4 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
              style={{
                width: CARD_W,
                left: hover.x + 18 + CARD_W < size.w ? hover.x + 18 : Math.max(8, hover.x - 18 - CARD_W),
                top: clamp(hover.y - 36, 8, Math.max(8, size.h - 280)),
              }}
            >
              {hoverMock ? (
                <TopicCardBody topic={hoverMock} hubLabel={domain.hubs.find((h) => h.id === hoverMock.hubId)?.title} />
              ) : (
                <>
                  <h3 className="display-caps text-[1.0625rem]">
                    <Link href={`/topic/${hoverTopic.id}`} className="text-paper underline decoration-seam decoration-1 underline-offset-4 hover:decoration-rule">
                      {hoverTopic.name}
                    </Link>
                  </h3>
                  <p className="caps mt-3 flex items-center gap-2 text-paper">
                    <StateDot state={stateOf(hoverTopic, model.nowMs)} />
                    {tx.states[stateOf(hoverTopic, model.nowMs)]}
                  </p>
                </>
              )}
            </div>
          )}
        </div>

        {/* The indicators and the count have a column of their own: nothing of the graph or of its controls lies under them */}
        <div className="hidden w-32 shrink-0 flex-col gap-3 overflow-y-auto pt-2 pl-2 md:flex">
          <IndicatorPills />
          {allHub && (
            <Link
              href={`/hub/${allHub.id}`}
              aria-label={tx.graph.allTopicsPillAria}
              className="caps inline-flex h-10 w-full shrink-0 items-center justify-center rounded-pill border border-seam px-3 whitespace-nowrap text-paper transition-colors duration-(--dur-s) ease-(--ease-out) hover:border-rule hover:bg-cloth-hover"
            >
              {tx.graph.allTopicsPill}
            </Link>
          )}
          <p aria-live="polite" className="caps px-1 text-paper-dim">
            {tx.graph.status(hubCount, topicCount)}
          </p>
        </div>
      </div>
    </div>
  );
});
