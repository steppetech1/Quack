"use client";

import { useEffect, useRef, useState } from "react";
import { CanvasSocket, type CanvasEvent, type SyncState } from "@/lib/api/canvas-socket";
import { loadStrokes } from "@/lib/api/stroke-replay";
import type { BoardItem, Pt } from "@/lib/canvas-board";

type Stroke = Extract<BoardItem, { kind: "stroke" }>;

/** What the journal knows about one board stroke: the id it has there, and a signature of its points */
type Known = { streamId: string; rev: number; sig: string };

/** Length, ends and the sum of coordinates: a cheap way to tell a moved stroke from an untouched one */
function signature(pts: Pt[]): string {
  let sx = 0;
  let sy = 0;
  for (const [x, y] of pts) {
    sx += x;
    sy += y;
  }
  const a = pts[0] ?? [0, 0];
  const b = pts[pts.length - 1] ?? [0, 0];
  return `${pts.length}:${a[0]},${a[1]}:${b[0]},${b[1]}:${sx.toFixed(2)},${sy.toFixed(2)}`;
}

/** The journal wants a new id for a stroke every time it is put back or moved: `id`, `id~1`, `id~2`… */
const streamId = (itemId: string, rev: number) => (rev === 0 ? itemId : `${itemId}~${rev}`);
export function parseStreamId(id: string): { itemId: string; rev: number } {
  const i = id.lastIndexOf("~");
  const rev = i < 0 ? NaN : Number(id.slice(i + 1));
  return Number.isInteger(rev) ? { itemId: id.slice(0, i), rev } : { itemId: id, rev: 0 };
}

/**
 * Saves the strokes of a backend canvas and brings them back (Т-§15.1, Т-§15.2).
 *
 * The board keeps its own copy of the items in the browser (with the ink, which the journal does not hold);
 * this keeps the journal equal to it: a stroke that is new, moved or put back goes out as a new stroke, a stroke
 * that is gone goes out as an erasure. On the first load the strokes of the journal that the browser lacks (another device,
 * cleared site data) are added to the board with the default ink, and what the browser has and the journal lacks
 * (the page was closed before the acknowledgement) is sent again.
 */
export function useCanvasSync({
  enabled,
  canvasId,
  items,
  loaded,
  restore,
  onEvent,
  onReopen,
}: {
  enabled: boolean;
  canvasId: string;
  items: BoardItem[];
  /** the items of the browser are read */
  loaded: boolean;
  /** adds items the board lacks, without an undo step */
  restore: (add: Stroke[]) => void;
  /** what the socket hears besides acks: lines sent for reading, recognitions, teacher marks and hints */
  onEvent?: (e: CanvasEvent) => void;
  /** the socket came back after a drop: the board re-reads what the teacher said meanwhile */
  onReopen?: () => void;
}): SyncState {
  const [state, setState] = useState<SyncState>("saved");
  const [ready, setReady] = useState(false);
  const socket = useRef<CanvasSocket | null>(null);
  const known = useRef(new Map<string, Known>());
  const lastRev = useRef(new Map<string, number>());
  const restoreRef = useRef(restore);
  const eventRef = useRef(onEvent);
  const reopenRef = useRef(onReopen);
  useEffect(() => {
    restoreRef.current = restore;
    eventRef.current = onEvent;
    reopenRef.current = onReopen;
  });

  useEffect(() => {
    if (!enabled) return;
    const s = new CanvasSocket(canvasId, setState, (e) => eventRef.current?.(e), () => reopenRef.current?.());
    socket.current = s;
    return () => {
      s.close();
      socket.current = null;
    };
  }, [enabled, canvasId]);

  // what the journal holds, once the browser's items are in
  useEffect(() => {
    if (!enabled || !loaded) return;
    let off = false;
    loadStrokes(canvasId)
      .then(
        (m) => m,
        () => new Map<string, Pt[]>(), // no replay: send what the browser has; the journal tolerates a repeated stroke id
      )
      .then((server) => {
        if (off) return;
        // the last revision of each stroke of the journal is the one on the board
        const latest = new Map<string, { rev: number; pts: Pt[]; id: string }>();
        for (const [id, pts] of server) {
          const { itemId, rev } = parseStreamId(id);
          lastRev.current.set(itemId, Math.max(lastRev.current.get(itemId) ?? 0, rev));
          const cur = latest.get(itemId);
          if (!cur || rev > cur.rev) latest.set(itemId, { rev, pts, id });
        }
        for (const [itemId, v] of latest) known.current.set(itemId, { streamId: v.id, rev: v.rev, sig: signature(v.pts) });
        const add: Stroke[] = [...latest].map(([itemId, v]) => ({ id: itemId, kind: "stroke", pts: v.pts, color: "paper", width: 2 }));
        restoreRef.current(add);
        setReady(true);
      });
    return () => {
      off = true;
    };
  }, [enabled, loaded, canvasId]);

  // the board against the journal
  useEffect(() => {
    const s = socket.current;
    if (!enabled || !ready || !s) return;
    const now = new Set<string>();
    for (const it of items) {
      if (it.kind !== "stroke") continue;
      now.add(it.id);
      const sig = signature(it.pts);
      const k = known.current.get(it.id);
      if (k?.sig === sig) continue;
      if (k) s.erase([k.streamId]); // moved: the old one goes, the new one is a new stroke
      const rev = k || lastRev.current.has(it.id) ? (lastRev.current.get(it.id) ?? 0) + 1 : 0;
      lastRev.current.set(it.id, rev);
      known.current.set(it.id, { streamId: streamId(it.id, rev), rev, sig });
      s.stroke(streamId(it.id, rev), it.pts);
    }
    for (const [itemId, k] of known.current) {
      if (now.has(itemId)) continue;
      s.erase([k.streamId]);
      known.current.delete(itemId);
    }
  }, [items, ready, enabled]);

  return state;
}
