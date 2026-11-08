"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CanvasEvent } from "@/lib/api/canvas-socket";
import { correctLine, type CanvasView } from "@/lib/api/canvases";

export type MarkKind = "error" | "question";

/** A line of the learner's handwriting the server was asked to read: where it is, which strokes it is made of, how it was read */
export type TeacherLine = {
  id: string;
  /** [x1, y1, x2, y2] in board coordinates */
  box: [number, number, number, number];
  strokeIds: string[];
  text?: string;
  kind?: "latex" | "text";
  crossedOut?: boolean;
};

export type LayerHint = { key: string; lineId: string | null; step: number; text: string };
export type LayerBlock = { id: string; kind: string; x: number; y: number; w: number; h: number };

type Doc = CanvasView["doc"];

type State = { lines: Record<string, TeacherLine>; marks: Record<string, MarkKind | null>; hints: LayerHint[] };

const EMPTY: State = { lines: {}, marks: {}, hints: [] };
const hintKey = (lineId: string | null, step: number, text: string) => `${lineId ?? ""}|${step}|${text}`;
const linesKey = (canvasId: string) => `quack-board-lines:${canvasId}`;

/**
 * The teacher layer of a backend canvas (Т-§15, WP21.3-21.5). The server knows the learner's lines by `line_id` only;
 * where a line lies on the board, the board knows (it sent the box with `line_ready`), so the lines are kept here
 * and in this browser, and the recognitions, marks and hints that come through the socket are laid on them.
 * Marks and hints already in the document of the canvas are the start of the layer.
 */
export function useTeacherLayer({ enabled, canvasId, doc }: { enabled: boolean; canvasId: string; doc?: Doc }) {
  const [state, setState] = useState<State>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const canvasRef = useRef(canvasId);
  useEffect(() => {
    canvasRef.current = canvasId;
  });

  // lines from this browser (position and reading of an earlier visit)
  useEffect(() => {
    if (!enabled) return;
    let lines: Record<string, TeacherLine> = {};
    try {
      const raw = JSON.parse(localStorage.getItem(linesKey(canvasId)) ?? "null") as TeacherLine[] | null;
      if (Array.isArray(raw)) lines = Object.fromEntries(raw.filter((l) => l && typeof l.id === "string" && Array.isArray(l.box)).map((l) => [l.id, l]));
    } catch {}
    // read in an effect so that hydration is not broken
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((s) => ({ ...s, lines: { ...lines, ...s.lines } }));
    setLoaded(true);
  }, [enabled, canvasId]);

  useEffect(() => {
    if (!enabled || !loaded) return;
    try {
      localStorage.setItem(linesKey(canvasId), JSON.stringify(Object.values(state.lines)));
    } catch {}
  }, [state.lines, enabled, loaded, canvasId]);

  const handle = useCallback((e: CanvasEvent) => {
    setState((s) => {
      if (e.type === "line_sent") {
        return { ...s, lines: { ...s.lines, [e.line_id]: { id: e.line_id, box: e.bbox, strokeIds: e.stroke_ids } } };
      }
      if (e.type === "line_recognized") {
        const line = s.lines[e.line_id];
        if (!line) return s;
        const next: TeacherLine = e.recognized && e.text ? { ...line, text: e.text, kind: e.kind, crossedOut: !!e.crossed_out } : line;
        return { ...s, lines: { ...s.lines, [e.line_id]: next } };
      }
      if (e.type === "mark") return { ...s, marks: { ...s.marks, [e.line_id]: e.kind } };
      const key = hintKey(e.line_id, e.step, e.text);
      if (s.hints.some((h) => h.key === key)) return s;
      return { ...s, hints: [...s.hints, { key, lineId: e.line_id, step: e.step, text: e.text }] };
    });
  }, []);

  /** The learner says how the line really reads; the answer of the server is what stays */
  const correct = useCallback(async (lineId: string, text: string) => {
    const done = await correctLine(canvasRef.current, lineId, text);
    setState((s) => {
      const line = s.lines[lineId];
      return line ? { ...s, lines: { ...s.lines, [lineId]: { ...line, text: done.text, crossedOut: false } } } : s;
    });
  }, []);

  // the document's own teacher layer, under what came live
  const marks = useMemo(() => {
    const out: Record<string, MarkKind> = {};
    for (const m of doc?.teacher?.marks ?? []) out[m.line_id] = m.kind;
    for (const [id, kind] of Object.entries(state.marks)) {
      if (kind) out[id] = kind;
      else delete out[id];
    }
    return out;
  }, [doc, state.marks]);

  const hints = useMemo(() => {
    const seen = new Set<string>();
    const out: LayerHint[] = [];
    const own = (doc?.teacher?.hints ?? []).map((h) => ({ key: hintKey(h.line_id ?? null, h.step, h.text), lineId: h.line_id ?? null, step: h.step, text: h.text }));
    for (const h of [...own, ...state.hints]) {
      if (seen.has(h.key)) continue;
      seen.add(h.key);
      out.push(h);
    }
    return out;
  }, [doc, state.hints]);

  const blocks = useMemo<LayerBlock[]>(
    () =>
      (doc?.blocks ?? [])
        .filter((b) => b.kind === "task")
        .map((b) => ({ id: b.block_id, kind: b.kind, x: b.layout?.x ?? 0, y: b.layout?.y ?? 0, w: b.layout?.width ?? 640, h: b.layout?.height ?? 360 })),
    [doc],
  );

  return { lines: state.lines, marks, hints, blocks, handle, correct };
}
