"use client";

import { GraduationCap, Minus, Play, Plus, Share2 } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  INK_VAR,
  K_MAX,
  K_MIN,
  layoutCanvas,
  lineBox,
  moveItem,
  smoothPath,
  strokeHit,
  uid,
  itemBox,
  unionBox,
  type Box,
  type BoardItem,
  type InkColor,
  type Pt,
} from "@/lib/canvas-board";
import type { Canvas } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n/provider";
import { useSoon } from "../soon";
import { BoardChat } from "./chat";
import { Frames } from "./frames";
import { ItemView } from "./items";
import { Toolbar, type Tool } from "./toolbar";

type Cam = { x: number; y: number; k: number };
type Gesture =
  | { type: "pan"; sx: number; sy: number; cam: Cam; captured: boolean }
  | { type: "pinch"; d0: number; k0: number; c0: Pt; cam: Cam }
  | { type: "draw"; pts: Pt[] }
  | { type: "move"; id: string; sx: number; sy: number; lx: number; ly: number; moving: boolean; recorded: boolean }
  | { type: "erase"; recorded: boolean }
  | { type: "tap"; sx: number; sy: number; kind: "text" | "formula" };

type Editing = { id: string; kind: "text" | "formula"; x: number; y: number };
const NEW_ID = "__new";

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const reduced = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isTyping = (t: EventTarget | null) => !!(t as Element | null)?.closest?.("input,textarea,select,[contenteditable='true']");

export function Board({
  canvas,
  canvasId,
  mode,
  focusBlock = null,
  topLeft,
  headingLevel = 2,
  className,
}: {
  canvas?: Canvas;
  canvasId: string;
  mode: "edit" | "view";
  focusBlock?: string | null;
  topLeft?: React.ReactNode;
  headingLevel?: 2 | 3;
  className?: string;
}) {
  const tx = useT();
  const edit = mode === "edit";
  const soon = useSoon();
  const descId = useId();
  const frames = useMemo(() => (canvas ? layoutCanvas(canvas) : []), [canvas]);

  const outer = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [cam, setCamState] = useState<Cam>({ x: 0, y: 0, k: 1 });
  const camRef = useRef(cam);
  const setCam = useCallback((c: Cam) => {
    camRef.current = c;
    setCamState(c);
  }, []);
  const anim = useRef(0);
  const touched = useRef(false);

  const [tool, setToolState] = useState<Tool>("select");
  const toolRef = useRef(tool);
  const setTool = (t: Tool) => {
    toolRef.current = t;
    setToolState(t);
    if (t !== "select") setSelected(null);
  };
  const [ink, setInk] = useState<InkColor>("paper");
  const [teacher, setTeacher] = useState(true);
  const [openLine, setOpenLine] = useState<string | null>(null);
  const [ringKey, setRingKey] = useState<string | null>(null);
  const ringTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [spaceDown, setSpaceDown] = useState(false);
  const spaceRef = useRef(false);
  const [panning, setPanning] = useState(false);

  // ── элементы студента и история ──
  const [items, setItems] = useState<BoardItem[]>([]);
  const itemsRef = useRef<BoardItem[]>([]);
  const stacks = useRef<{ past: BoardItem[][]; future: BoardItem[][] }>({ past: [], future: [] });
  const [hist, setHist] = useState({ undo: false, redo: false });
  const syncHist = useCallback(() => setHist({ undo: stacks.current.past.length > 0, redo: stacks.current.future.length > 0 }), []);
  const selectedRef = useRef<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [draft, setDraft] = useState<{ pts: Pt[]; color: InkColor; marker: boolean } | null>(null);
  const [loaded, setLoaded] = useState(false);

  const mutate = useCallback((fn: (prev: BoardItem[]) => BoardItem[], record = true) => {
    const prev = itemsRef.current;
    const next = fn(prev);
    if (next === prev) return;
    if (record) {
      stacks.current.past.push(prev);
      if (stacks.current.past.length > 100) stacks.current.past.shift();
      stacks.current.future = [];
    }
    itemsRef.current = next;
    setItems(next);
    syncHist();
  }, [syncHist, setItems]);

  const undo = () => {
    const s = stacks.current;
    const prev = s.past.pop();
    if (!prev) return;
    s.future.push(itemsRef.current);
    itemsRef.current = prev;
    setItems(prev);
    syncHist();
  };
  const redo = () => {
    const s = stacks.current;
    const next = s.future.pop();
    if (!next) return;
    s.past.push(itemsRef.current);
    itemsRef.current = next;
    setItems(next);
    syncHist();
  };

  // Сохранение: штрихи, тексты и формулы по id канваса. Картинки живут только в сессии (object URL)
  const storeKey = `quack-board:${canvasId}`;
  const persist = canvasId !== "new";
  useEffect(() => {
    if (persist) {
      try {
        const raw = localStorage.getItem(storeKey);
        const arr = raw ? JSON.parse(raw) : null;
        if (Array.isArray(arr)) {
          itemsRef.current = arr;
          // чтение в эффекте, чтобы не ломать гидратацию
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setItems(arr);
        }
      } catch {}
    }
    setLoaded(true);
  }, [storeKey, persist]);
  useEffect(() => {
    if (!loaded || !persist) return;
    try {
      localStorage.setItem(storeKey, JSON.stringify(items.filter((i) => i.kind !== "image")));
    } catch {}
  }, [items, loaded, storeKey, persist]);

  // ── камера ──
  const cancelAnim = () => cancelAnimationFrame(anim.current);
  const goTo = useCallback(
    (to: Cam, animate: boolean) => {
      cancelAnimationFrame(anim.current);
      if (!animate || reduced()) {
        setCam(to);
        return;
      }
      const from = camRef.current;
      const t0 = performance.now();
      const D = 520;
      const step = (now: number) => {
        const t = Math.min(1, (now - t0) / D);
        const e = 1 - Math.pow(1 - t, 3);
        // зум интерполируем по логарифму, чтобы он ощущался равномерным
        const k = Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e);
        setCam({ k, x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e });
        if (t < 1) anim.current = requestAnimationFrame(step);
      };
      anim.current = requestAnimationFrame(step);
    },
    [setCam],
  );

  const viewFor = useCallback(
    (box: Box, maxK: number): Cam => {
      const el = viewport.current;
      if (!el) return camRef.current;
      const W = el.clientWidth;
      const H = el.clientHeight;
      const narrow = W < 768;
      const chatH = outer.current?.querySelector<HTMLElement>("[data-chat]")?.offsetHeight ?? 80;
      const i = !edit ? { l: 16, t: 56, r: 16, b: 16 } : narrow ? { l: 12, t: 68, r: 12, b: chatH + 76 } : { l: 84, t: 76, r: 24, b: Math.max(140, chatH + 36) };
      const aw = Math.max(80, W - i.l - i.r);
      const ah = Math.max(80, H - i.t - i.b);
      const k = clamp(Math.min(aw / box.w, ah / box.h, maxK), K_MIN, K_MAX);
      return { k, x: i.l + (aw - box.w * k) / 2 - box.x * k, y: i.t + (ah - box.h * k) / 2 - box.y * k };
    },
    [edit],
  );

  const fit = useCallback(
    (animate: boolean) => {
      const el = viewport.current;
      if (!el) return;
      const b = unionBox([...frames.map((f) => ({ x: f.x, y: f.y - 30, w: f.w, h: f.h + 30 })), ...itemsRef.current.map(itemBox)]);
      if (!b) goTo({ k: 1, x: el.clientWidth / 2, y: el.clientHeight / 2 }, animate);
      else if (el.clientWidth < 768 && frames.length > 1 && !itemsRef.current.length) {
        // телефон: во весь экран только первая рамка, остальное рядом, до него дотянешься рукой
        const f = frames[0];
        const v = viewFor({ x: f.x, y: f.y - 30, w: f.w, h: f.h + 30 }, 1);
        goTo({ ...v, y: 76 - (f.y - 30) * v.k }, animate);
      } else goTo(viewFor(b, 1), animate);
    },
    [frames, goTo, viewFor],
  );

  const ring = useCallback((key: string) => {
    clearTimeout(ringTimer.current);
    setRingKey(key);
    ringTimer.current = setTimeout(() => setRingKey(null), 1800);
  }, [setRingKey]);

  // Первая раскладка, deep link ?block= и перерасчёт, пока пользователь не двигал камеру
  useLayoutEffect(() => {
    if (!loaded) return;
    touched.current = false;
    fit(false);
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(() => !touched.current && fit(false));
    ro.observe(el);
    let t: ReturnType<typeof setTimeout> | undefined;
    const f = focusBlock ? frames.find((x) => x.block.id === focusBlock) : null;
    if (f) {
      t = setTimeout(() => {
        goTo(viewFor({ x: f.x, y: f.y - 30, w: f.w, h: f.h + 30 }, 1.15), true);
        ring(`frame:${f.block.id}`);
      }, 250);
    }
    return () => {
      ro.disconnect();
      clearTimeout(t);
      cancelAnimationFrame(anim.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, canvasId, focusBlock, frames]);

  const zoomAt = useCallback(
    (cx: number, cy: number, k2: number) => {
      const c = camRef.current;
      const k = clamp(k2, K_MIN, K_MAX);
      const r = k / c.k;
      setCam({ k, x: cx - (cx - c.x) * r, y: cy - (cy - c.y) * r });
    },
    [setCam],
  );
  const zoomCenter = (k2: number, animate = false) => {
    const el = viewport.current;
    if (!el) return;
    touched.current = true;
    const c = camRef.current;
    const k = clamp(k2, K_MIN, K_MAX);
    const cx = el.clientWidth / 2;
    const cy = el.clientHeight / 2;
    const r = k / c.k;
    goTo({ k, x: cx - (cx - c.x) * r, y: cy - (cy - c.y) * r }, animate);
  };

  // Колесо: Ctrl/⌘ или щипок трекпада — масштаб, обычная прокрутка — перемещение
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if ((e.target as Element).closest("[data-nopan]")) return;
      e.preventDefault();
      cancelAnimationFrame(anim.current);
      touched.current = true;
      const r = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) {
        zoomAt(e.clientX - r.left, e.clientY - r.top, camRef.current.k * Math.exp(-e.deltaY * 0.0105));
      } else {
        const c = camRef.current;
        setCam({ ...c, x: c.x - e.deltaX, y: c.y - e.deltaY });
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt, setCam]);

  // ── указатель ──
  const ptrs = useRef(new Map<number, Pt>());
  const g = useRef<Gesture | null>(null);

  const toWorld = (cx: number, cy: number): Pt => {
    const r = viewport.current!.getBoundingClientRect();
    const c = camRef.current;
    return [(cx - r.left - c.x) / c.k, (cy - r.top - c.y) / c.k];
  };
  const centerWorld = (): Pt => {
    const r = viewport.current!.getBoundingClientRect();
    return toWorld(r.left + r.width / 2, r.top + r.height / 2);
  };

  const itemId = (t: Element) => t.closest("[data-item]")?.getAttribute("data-item") ?? null;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button === 2) return;
    const t = e.target as Element;
    if (t.closest("[data-nopan]")) return;
    cancelAnim();
    touched.current = true;
    const el = viewport.current!;
    ptrs.current.set(e.pointerId, [e.clientX, e.clientY]);

    if (ptrs.current.size === 2) {
      setDraft(null);
      const [a, b] = [...ptrs.current.values()];
      const r = el.getBoundingClientRect();
      g.current = {
        type: "pinch",
        d0: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1,
        k0: camRef.current.k,
        c0: [(a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top],
        cam: camRef.current,
      };
      return;
    }
    if (ptrs.current.size > 2) return;

    const tl = toolRef.current;
    const panByTool = e.button === 1 || spaceRef.current || tl === "hand" || !edit;
    if (panByTool) {
      if (e.button === 1) {
        e.preventDefault();
        el.setPointerCapture(e.pointerId);
      }
      g.current = { type: "pan", sx: e.clientX, sy: e.clientY, cam: camRef.current, captured: e.button === 1 };
      return;
    }
    const w = toWorld(e.clientX, e.clientY);
    if (tl === "select") {
      const id = itemId(t);
      if (id) {
        setSelected(id);
        el.setPointerCapture(e.pointerId);
        g.current = { type: "move", id, sx: e.clientX, sy: e.clientY, lx: e.clientX, ly: e.clientY, moving: false, recorded: false };
      } else {
        setSelected(null);
        g.current = { type: "pan", sx: e.clientX, sy: e.clientY, cam: camRef.current, captured: false };
      }
      return;
    }
    if (tl === "pen" || tl === "marker") {
      el.setPointerCapture(e.pointerId);
      g.current = { type: "draw", pts: [w] };
      setDraft({ pts: [w], color: ink, marker: tl === "marker" });
      return;
    }
    if (tl === "eraser") {
      el.setPointerCapture(e.pointerId);
      g.current = { type: "erase", recorded: false };
      eraseAt(w);
      return;
    }
    if (tl === "text" || tl === "formula") {
      const id = itemId(t);
      const it = id ? itemsRef.current.find((i) => i.id === id) : null;
      if (it && (it.kind === "text" || it.kind === "formula")) {
        setEditing({ id: it.id, kind: it.kind, x: it.x, y: it.y });
        return;
      }
      g.current = { type: "tap", sx: e.clientX, sy: e.clientY, kind: tl };
    }
  };

  const eraseAt = (w: Pt) => {
    const rad = 10 / camRef.current.k;
    const hit = itemsRef.current.filter((i) => i.kind === "stroke" && strokeHit(i.pts, w, rad + i.width / 2)).map((i) => i.id);
    if (!hit.length) return;
    const gg = g.current;
    const rec = gg?.type === "erase" ? !gg.recorded : true;
    if (gg?.type === "erase") gg.recorded = true;
    mutate((prev) => prev.filter((i) => !hit.includes(i.id)), rec);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!ptrs.current.has(e.pointerId)) return;
    ptrs.current.set(e.pointerId, [e.clientX, e.clientY]);
    const gg = g.current;
    if (!gg) return;
    const el = viewport.current!;
    if (gg.type === "pinch") {
      if (ptrs.current.size < 2) return;
      const [a, b] = [...ptrs.current.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]) || 1;
      const r = el.getBoundingClientRect();
      const cx = (a[0] + b[0]) / 2 - r.left;
      const cy = (a[1] + b[1]) / 2 - r.top;
      const k = clamp((gg.k0 * d) / gg.d0, K_MIN, K_MAX);
      const wx = (gg.c0[0] - gg.cam.x) / gg.k0;
      const wy = (gg.c0[1] - gg.cam.y) / gg.k0;
      setCam({ k, x: cx - wx * k, y: cy - wy * k });
    } else if (gg.type === "pan") {
      const dx = e.clientX - gg.sx;
      const dy = e.clientY - gg.sy;
      if (!gg.captured) {
        if (Math.hypot(dx, dy) < 4) return;
        gg.captured = true;
        el.setPointerCapture(e.pointerId);
        setPanning(true);
      }
      setCam({ ...gg.cam, x: gg.cam.x + dx, y: gg.cam.y + dy });
    } else if (gg.type === "draw") {
      const w = toWorld(e.clientX, e.clientY);
      const last = gg.pts[gg.pts.length - 1];
      if (Math.hypot(w[0] - last[0], w[1] - last[1]) * camRef.current.k < 2.5) return;
      gg.pts.push([Math.round(w[0] * 10) / 10, Math.round(w[1] * 10) / 10]);
      setDraft((d) => d && { ...d, pts: [...gg.pts] });
    } else if (gg.type === "erase") {
      eraseAt(toWorld(e.clientX, e.clientY));
    } else if (gg.type === "move") {
      if (!gg.moving) {
        if (Math.hypot(e.clientX - gg.sx, e.clientY - gg.sy) < 3) return;
        gg.moving = true;
      }
      const k = camRef.current.k;
      const dx = (e.clientX - gg.lx) / k;
      const dy = (e.clientY - gg.ly) / k;
      gg.lx = e.clientX;
      gg.ly = e.clientY;
      const rec = !gg.recorded;
      gg.recorded = true;
      mutate((prev) => prev.map((i) => (i.id === gg.id ? moveItem(i, dx, dy) : i)), rec);
    }
  };

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    ptrs.current.delete(e.pointerId);
    const gg = g.current;
    if (!gg) return;
    if (gg.type === "pinch") {
      if (ptrs.current.size < 2) g.current = null;
      return;
    }
    g.current = null;
    setPanning(false);
    if (gg.type === "draw") {
      const color = ink;
      const marker = toolRef.current === "marker";
      mutate((prev) => [...prev, { id: uid(), kind: "stroke", pts: gg.pts, color, width: marker ? 12 : 2, marker }]);
      setDraft(null);
    } else if (gg.type === "tap" && e.type === "pointerup" && Math.hypot(e.clientX - gg.sx, e.clientY - gg.sy) < 5) {
      const [x, y] = toWorld(e.clientX, e.clientY);
      setEditing({ id: NEW_ID, kind: gg.kind, x, y });
    }
  };
  const onPointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    ptrs.current.delete(e.pointerId);
    g.current = null;
    setDraft(null);
    setPanning(false);
  };

  const onDoubleClick = (e: React.MouseEvent) => {
    if (!edit) return;
    const hit = document.elementFromPoint(e.clientX, e.clientY);
    const id = hit ? itemId(hit) : null;
    const it = id ? itemsRef.current.find((i) => i.id === id) : null;
    if (it && (it.kind === "text" || it.kind === "formula")) setEditing({ id: it.id, kind: it.kind, x: it.x, y: it.y });
  };

  const onEditDone = (id: string, v: string | null) => {
    const ed = editing;
    setEditing(null);
    if (v === null || !ed) return;
    const val = v.trim();
    if (id === NEW_ID) {
      if (!val) return;
      const nid = uid();
      mutate((prev) => [...prev, ed.kind === "text" ? { id: nid, kind: "text", x: ed.x, y: ed.y, text: val } : { id: nid, kind: "formula", x: ed.x, y: ed.y, tex: val }]);
      setSelected(nid);
      setToolState("select");
      toolRef.current = "select";
      return;
    }
    mutate((prev) =>
      val
        ? prev.map((i) => (i.id === id ? (i.kind === "text" ? { ...i, text: val } : i.kind === "formula" ? { ...i, tex: val } : i) : i))
        : prev.filter((i) => i.id !== id),
    );
  };

  // ── картинки ──
  const addImages = useCallback(
    (files: FileList | File[], at?: Pt) => {
      const list = [...files].filter((f) => f.type.startsWith("image/"));
      list.forEach((file, n) => {
        const src = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
          const s = Math.min(1, 360 / img.naturalWidth, 300 / img.naturalHeight);
          const w = Math.max(24, Math.round(img.naturalWidth * s));
          const h = Math.max(24, Math.round(img.naturalHeight * s));
          const c = at ?? centerWorld();
          const id = uid();
          mutate((prev) => [...prev, { id, kind: "image", x: Math.round(c[0] - w / 2 + n * 24), y: Math.round(c[1] - h / 2 + n * 24), w, h, src }]);
          setSelected(id);
        };
        img.onerror = () => URL.revokeObjectURL(src);
        img.src = src;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mutate],
  );

  // ── клавиатура ──
  const keyApi = useRef({ undo, redo, zoomCenter, fit });
  useEffect(() => {
    keyApi.current = { undo, redo, zoomCenter, fit };
  });
  useEffect(() => {
    if (!edit) return;
    const TOOL_KEYS: Record<string, Tool> = { KeyV: "select", KeyH: "hand", KeyP: "pen", KeyM: "marker", KeyE: "eraser", KeyT: "text", KeyF: "formula" };
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.code === "KeyZ") {
        e.preventDefault();
        if (e.shiftKey) keyApi.current.redo();
        else keyApi.current.undo();
        return;
      }
      if (mod && e.code === "KeyY") {
        e.preventDefault();
        keyApi.current.redo();
        return;
      }
      if (mod || e.altKey) return;
      if (e.code === "Space") {
        if (!(e.target as Element).closest("button,a,summary")) e.preventDefault();
        if (!spaceRef.current) {
          spaceRef.current = true;
          setSpaceDown(true);
        }
        return;
      }
      const t = TOOL_KEYS[e.code];
      if (t) {
        e.preventDefault();
        setTool(t);
        return;
      }
      if (e.code === "KeyI") {
        fileInput.current?.click();
        return;
      }
      if (e.code === "Escape") {
        setSelected(null);
        setOpenLine(null);
        return;
      }
      if (e.code === "Equal" || e.code === "NumpadAdd") keyApi.current.zoomCenter(camRef.current.k * 1.25, true);
      else if (e.code === "Minus" || e.code === "NumpadSubtract") keyApi.current.zoomCenter(camRef.current.k / 1.25, true);
      else if (e.code === "Digit0") keyApi.current.fit(true);
      if (!selectedRef.current) return;
      const id = selectedRef.current;
      if (e.code === "Delete" || e.code === "Backspace") {
        e.preventDefault();
        mutate((prev) => prev.filter((i) => i.id !== id));
        setSelected(null);
        return;
      }
      const arrow: Record<string, Pt> = { ArrowLeft: [-8, 0], ArrowRight: [8, 0], ArrowUp: [0, -8], ArrowDown: [0, 8] };
      const d = arrow[e.code];
      if (d) {
        e.preventDefault();
        mutate((prev) => prev.map((i) => (i.id === id ? moveItem(i, d[0], d[1]) : i)));
      }
    };
    const onUp = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        spaceRef.current = false;
        setSpaceDown(false);
      }
    };
    const onBlur = () => {
      spaceRef.current = false;
      setSpaceDown(false);
    };
    const onPaste = (e: ClipboardEvent) => {
      if (isTyping(e.target)) return;
      const files = e.clipboardData?.files;
      if (files && [...files].some((f) => f.type.startsWith("image/"))) {
        e.preventDefault();
        addImages(files);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("paste", onPaste);
    };
  }, [edit, addImages, mutate]);
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  // Поповер строки закрывается кликом мимо
  useEffect(() => {
    if (!openLine) return;
    const onDown = (e: MouseEvent) => !(e.target as Element).closest("[data-hand]") && setOpenLine(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpenLine(null);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [openLine]);

  // Перелёт к строке или элементу по ссылке из чата
  const jump = (ref: string) => {
    touched.current = true;
    if (ref.startsWith("line:")) {
      const [, bid, lid] = ref.split(":");
      const f = frames.find((x) => x.block.id === bid);
      if (f && f.block.kind === "task") {
        const idx = f.block.lines.findIndex((l) => l.id === lid);
        if (idx >= 0) {
          const b = lineBox(f, idx);
          goTo(viewFor({ x: b.x, y: b.y - 150, w: b.w, h: b.h + 300 }, 1.2), true);
          ring(ref);
        }
      }
    } else if (ref.startsWith("item:")) {
      const it = itemsRef.current.find((i) => i.id === ref.slice(5));
      if (it) {
        const b = itemBox(it);
        goTo(viewFor({ x: b.x - 80, y: b.y - 80, w: b.w + 160, h: b.h + 160 }, 1.5), true);
        setSelected(it.id);
      }
    }
  };

  const pct = Math.round(cam.k * 100);
  const gridOp = clamp((cam.k - 0.22) / 0.3, 0, 1);
  const cursor = panning ? "grabbing" : spaceDown || tool === "hand" || !edit ? "grab" : tool === "pen" || tool === "marker" ? "crosshair" : tool === "eraser" ? "cell" : tool === "text" || tool === "formula" ? "text" : "default";
  const editingItem: BoardItem | null =
    editing && editing.id === NEW_ID
      ? editing.kind === "text"
        ? { id: NEW_ID, kind: "text", x: editing.x, y: editing.y, text: "" }
        : { id: NEW_ID, kind: "formula", x: editing.x, y: editing.y, tex: "" }
      : null;

  const BAR = "z-20 rounded-[14px] border border-seam bg-cloth-raised shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]";
  const BAR_BTN = "inline-flex h-10 items-center justify-center gap-2 rounded-control px-3 text-small whitespace-nowrap text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper max-md:w-10 max-md:px-0";

  return (
    <div ref={outer} className={cn("relative min-h-0 flex-1 overflow-hidden bg-cloth", className)}>
      <div
        ref={viewport}
        role="application"
        aria-label={tx.board.label}
        aria-describedby={descId}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onDoubleClick={onDoubleClick}
        onDragOver={(e) => edit && e.preventDefault()}
        onDrop={(e) => {
          if (!edit || !e.dataTransfer.files.length) return;
          e.preventDefault();
          addImages(e.dataTransfer.files, toWorld(e.clientX, e.clientY));
        }}
        className="absolute inset-0 touch-none overflow-hidden select-none"
        style={{ cursor }}
      >
        <p id={descId} className="sr-only">
          {tx.board.desc}
        </p>
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            opacity: gridOp,
            backgroundImage: "radial-gradient(circle, var(--seam) 1.2px, transparent 1.8px)",
            backgroundSize: `${24 * cam.k}px ${24 * cam.k}px`,
            backgroundPosition: `${cam.x - 12 * cam.k}px ${cam.y - 12 * cam.k}px`,
          }}
        />
        <div
          className="absolute top-0 left-0 origin-top-left"
          style={{ transform: `translate(${cam.x}px, ${cam.y}px) scale(${cam.k})`, ["--inv" as string]: 1 / cam.k }}
        >
          <Frames frames={frames} teacher={teacher} openLine={openLine} ringKey={ringKey} setOpenLine={setOpenLine} headingLevel={headingLevel} />
          {items.map((it) => (
            <ItemView key={it.id} item={it} selected={selected === it.id} editing={editing?.id === it.id} onSelect={setSelected} onEdit={onEditDone} />
          ))}
          {editingItem && <ItemView item={editingItem} selected={false} editing onSelect={setSelected} onEdit={onEditDone} />}
          {draft && (
            <svg aria-hidden width={1} height={1} className="pointer-events-none absolute top-0 left-0 overflow-visible">
              <path
                d={smoothPath(draft.pts)}
                fill="none"
                stroke={INK_VAR[draft.color]}
                strokeOpacity={draft.marker ? 0.35 : 1}
                strokeWidth={draft.marker ? 12 : 2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </div>
      </div>

      {edit && !canvas && items.length === 0 && !editing && !draft && (
        <p className="pointer-events-none absolute inset-x-0 top-[38%] px-6 text-center text-body text-paper-dim">
          {tx.board.empty}
        </p>
      )}

      {/* Верхние панели */}
      {topLeft && (
        <div data-ui className={cn("absolute top-3 left-3 flex max-w-[calc(100%-8.5rem)] items-center gap-1 p-1 md:max-w-[calc(100%-26rem)]", BAR)}>
          {topLeft}
        </div>
      )}
      <div data-ui className={cn("absolute top-3 right-3 flex items-center gap-0.5 p-1", BAR)}>
        <button
          type="button"
          aria-pressed={teacher}
          aria-label={tx.board.teacherLayer}
          onClick={() => setTeacher((v) => !v)}
          className={cn(BAR_BTN, teacher && "bg-cloth-hover text-paper")}
        >
          <GraduationCap aria-hidden className="size-[18px]" />
          <span className="max-md:sr-only">{tx.board.teacherLayer}</span>
        </button>
        {edit && (
          <>
            <button type="button" aria-label={tx.board.replay} onClick={() => soon(tx.board.replaySoon)} className={BAR_BTN}>
              <Play aria-hidden className="size-[18px]" />
              <span className="max-lg:sr-only">{tx.board.replay}</span>
            </button>
            <button type="button" aria-label={tx.board.share} onClick={() => soon(tx.board.shareSoon)} className={cn(BAR_BTN, "w-10 px-0")}>
              <Share2 aria-hidden className="size-[18px]" />
            </button>
          </>
        )}
      </div>

      {/* Масштаб */}
      <div
        data-ui
        role="group"
        aria-label={tx.board.zoom}
        className={cn("absolute right-3 flex items-center gap-0.5 p-1", BAR, edit ? "max-md:top-[68px] md:bottom-3" : "bottom-3")}
      >
        <button type="button" aria-label={tx.board.zoomOut} onClick={() => zoomCenter(camRef.current.k / 1.25, true)} className={cn(BAR_BTN, "w-10 px-0")}>
          <Minus aria-hidden className="size-[18px]" />
        </button>
        <button type="button" aria-label={tx.board.zoomReset} onClick={() => zoomCenter(1, true)} className={cn(BAR_BTN, "num w-14 px-0")}>
          {pct}%
        </button>
        <button type="button" aria-label={tx.board.zoomIn} onClick={() => zoomCenter(camRef.current.k * 1.25, true)} className={cn(BAR_BTN, "w-10 px-0")}>
          <Plus aria-hidden className="size-[18px]" />
        </button>
        <button type="button" onClick={() => { touched.current = true; fit(true); }} className={cn(BAR_BTN, "px-2.5 max-md:w-auto max-md:px-2.5")}>
          {tx.board.fit}
        </button>
      </div>

      {edit && (
        <>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            aria-label={tx.board.pickImage}
            onChange={(e) => {
              if (e.target.files) addImages(e.target.files);
              e.target.value = "";
            }}
          />
          <div className="absolute inset-x-2 bottom-3 flex flex-col gap-2 md:contents">
            <Toolbar
              tool={tool}
              setTool={setTool}
              ink={ink}
              setInk={setInk}
              onImage={() => fileInput.current?.click()}
              onLasso={() => soon(tx.board.lassoSoon)}
              onUndo={undo}
              onRedo={redo}
              canUndo={hist.undo}
              canRedo={hist.redo}
            />
            <BoardChat canvasId={canvasId} onJump={jump} />
          </div>
        </>
      )}
    </div>
  );
}
