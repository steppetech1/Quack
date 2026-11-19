import { decode } from "@msgpack/msgpack";
import { decompress } from "fzstd";
import type { Pt } from "@/lib/canvas-board";
import { get, type S } from "./client";

/*
 * Replay of a canvas (Т-§15.7): the signed links of the stroke chunks, oldest first. A chunk is zstd over
 * MessagePack `{batches: [{batch_no, strokes: [{stroke_id, points}], erasures: [{stroke_ids, at_ms}]}]}`; the first
 * point of a stroke is absolute `[x, y, t_ms, pressure]`, the next ones are deltas `[dx, dy, dt, pressure]`.
 * Strokes are raw material and the journal may hold the same batch twice (a resend after a restart of the API):
 * a stroke is taken by its id once, an erasure only takes a stroke away.
 */

type RawBatch = { batch_no: number; strokes: { stroke_id: string; points: number[][] }[]; erasures: { stroke_ids: string[]; at_ms: number }[] };

/** Absolute points of a stroke; only x and y are kept (the board has no time or pressure) */
export function absolutePoints(raw: number[][]): Pt[] {
  const out: Pt[] = [];
  for (const [x, y] of raw) {
    const last = out[out.length - 1];
    out.push(last ? [last[0] + x, last[1] + y] : [x, y]);
  }
  return out;
}

/** The strokes left after the batches of one chunk, in the order they were drawn */
export function applyChunk(into: Map<string, Pt[]>, data: Uint8Array) {
  const { batches } = decode(decompress(data)) as { batches: RawBatch[] };
  for (const b of batches) {
    for (const s of b.strokes) if (!into.has(s.stroke_id)) into.set(s.stroke_id, absolutePoints(s.points));
    for (const e of b.erasures) for (const id of e.stroke_ids) into.delete(id);
  }
}

/** Every stroke of the canvas that is still on it; one chunk that fails to load or decode is skipped, not the whole board */
export async function loadStrokes(canvasId: string): Promise<Map<string, Pt[]>> {
  const replay = await get<S["ReplayView"]>(`/v1/canvases/${canvasId}/replay`);
  const strokes = new Map<string, Pt[]>();
  // an erasure in a later chunk must see the stroke of an earlier one: fetch in parallel, apply in order
  const parts = await Promise.all(
    replay.chunks.map(async (c) => {
      try {
        const res = await fetch(c.url, { credentials: "omit" });
        return res.ok ? new Uint8Array(await res.arrayBuffer()) : null;
      } catch {
        return null;
      }
    }),
  );
  for (const part of parts) {
    if (!part) continue;
    try {
      applyChunk(strokes, part);
    } catch {}
  }
  return strokes;
}
