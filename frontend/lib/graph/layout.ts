import { onGraph, type GraphHub, type GraphModel } from "./types";

/** Step between neighbours in a row, world units */
export const GAP_X = 110;
/** Step between rows inside a block (and the unit of every vertical rule), world units */
export const GAP_Y = 110;
/** A chapter block is at most this many topics wide */
const BLOCK_MAX_COLS = 4;
/** Empty space between two blocks of the snake (on top of the half-steps of their edge slots): chapters read as separate pieces */
const BLOCK_GAP_X = GAP_X * 1.3;
/** A block with a hub is at least this wide, so the name of the hub has room above it */
const HUB_MIN_W = GAP_X * 2;
/** A hub stands this far above the highest topic of its block */
const HUB_RISE = GAP_Y * 0.9;
/** Rows reserved above a chapter block for its hub (nothing else stands there) */
const HUB_BAND_ROWS = 1;
/** Two hubs are no closer than this */
const HUB_GAP_X = GAP_X * 1.35;
/**
 * The snake of blocks turns round when a row would be wider than this, world units. It is the width of the row that holds
 * all the hubs of Calculus today. Going back the row stops at the same edge on the left.
 */
const MAX_ROW_WIDTH = 2200;
/** Every next block of the snake starts at least this many rows above the base of the previous one: a staircase, never a flat row */
const SNAKE_RISE = 1.25;
/** A block taller than this many rows is at least `ZIGZAG_COLS` wide, so a long chain can wind up instead of standing as a column */
const ZIGZAG_FROM_ROWS = 4;
const ZIGZAG_COLS = 3;
/** Passes of the "mean of the neighbours" ordering inside a block */
const SWEEPS = 4;
/** Topics with no hub and no link: a block about as wide as high, `round(sqrt(LONE_ASPECT * N))` in a row, at most `LONE_MAX_COLS` */
const LONE_ASPECT = 1.2;
const LONE_MAX_COLS = 9;
/** A collection stands this far outside the occupied area, beside its topics */
const COLLECTION_MARGIN = GAP_X * 1.5;
/** Nodes this close in y (above and below) count as the occupied area at the height of a collection */
const COLLECTION_BAND = GAP_Y * 1.5;
/** When the place beside the topics is taken, the next collection moves this far along the edge */
const COLLECTION_STEP_Y = GAP_Y * 0.8;
/** Nodes closer than this (in x, at the same height) overlap, and the later one is lifted */
const OVERLAP_X = GAP_X * 0.9;
const REPAIR_PASSES = 40;

export type LayoutNode = {
  /** Topic id, or `h:<id>` for a hub */
  key: string;
  id: string;
  kind: "topic" | "hub";
  x: number;
  /** Up is negative: the foundation is at y = 0 */
  y: number;
  /** Row from the foundation, rounded */
  layer: number;
};

/** A compact piece of the snake: a chapter, a group of linked topics with no hub, or the block of lone topics */
export type LayoutBlock = {
  key: string;
  kind: "chapter" | "free" | "lone";
  /** Hub id of a chapter */
  hubId?: string;
  topicIds: string[];
  /** Horizontal extent the block reserves (the hub is centred over it) */
  x0: number;
  x1: number;
  /** y of the lowest row of the block (up is negative). Along the snake the base always rises */
  baseY: number;
};

export type Layout = {
  nodes: Map<string, LayoutNode>;
  topics: LayoutNode[];
  hubs: LayoutNode[];
  /** Always empty now: topics with no link are lone nodes in `topics` (kept for readers of the old column) */
  unlinked: string[];
  bounds: { x0: number; y0: number; x1: number; y1: number };
  /** In snake order: the lone block first, then chapters and groups */
  blocks: LayoutBlock[];
  /** Hubs with no topic of their own: they stand beside their topics and reach them with dashed lines */
  collections: Set<string>;
  /** `requires` edges broken to make the graph acyclic (the weakest of each cycle). Only these may point down */
  droppedEdges: { topicId: string; requiresId: string }[];
};

/**
 * Saved `[x, y]` per node key. It is a hint, not a pin: it breaks ties in the order of nodes and is the point
 * a node glides from to its new place. The layout itself is computed from the data alone.
 */
export type StoredPositions = Record<string, [number, number]>;

export const hubKey = (id: string) => `h:${id}`;

type Edge = { a: number; b: number; c: number };

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

const visibleTopics = (model: GraphModel) => model.topics.filter(onGraph).sort((a, b) => cmp(a.id, b.id));

/**
 * The hubs that can be chapters, earlier first. "Earlier" is the order the hubs appeared in the domain: until the backend
 * sends `created_seq`, the order of the list stands for it (the adapter keeps the backend order); then the hub id.
 * `domain_all` is not a chapter.
 */
function hubsInOrder(model: GraphModel): GraphHub[] {
  return model.hubs
    .map((h, i) => ({ h, seq: i }))
    .filter(({ h }) => h.kind !== "domain_all")
    .sort((a, b) => a.seq - b.seq || cmp(a.h.id, b.h.id))
    .map(({ h }) => h);
}

/** Topics that have at least one "needed before" link, and those links. The rest are lone */
export function chainOf(model: GraphModel) {
  // Sorted by id: the order the backend lists topics and links in is not guaranteed, and the layout must not depend on it
  const visible = visibleTopics(model);
  const ids = new Set(visible.map((t) => t.id));
  const edges = model.requires
    .filter((e) => ids.has(e.topicId) && ids.has(e.requiresId) && e.topicId !== e.requiresId)
    .sort((a, b) => cmp(a.topicId, b.topicId) || cmp(a.requiresId, b.requiresId));
  const linked = new Set(edges.flatMap((e) => [e.topicId, e.requiresId]));
  return { topics: visible.filter((t) => linked.has(t.id)), edges, unlinked: visible.filter((t) => !linked.has(t.id)).map((t) => t.id) };
}

/** A cycle of the alive edges as a list of edges, or null. Deterministic: nodes and their edges are visited in index order */
function findCycle(n: number, out: Edge[][]): Edge[] | null {
  const state = new Array<number>(n).fill(0); // 0 new, 1 on the path, 2 done
  const path: Edge[] = [];
  const res: { cycle: Edge[] | null } = { cycle: null };
  const visit = (v: number): boolean => {
    state[v] = 1;
    for (const e of out[v]) {
      if (state[e.b] === 1) {
        res.cycle = [...path.slice(path.findIndex((x) => x.a === e.b)), e];
        return true;
      }
      if (state[e.b] === 0) {
        path.push(e);
        if (visit(e.b)) return true;
        path.pop();
      }
    }
    state[v] = 2;
    return false;
  };
  for (let v = 0; v < n; v++) if (state[v] === 0 && visit(v)) return res.cycle;
  return null;
}

/**
 * Cycles of `requires` cannot all point up. Take a cycle, drop its weakest edge (lowest confidence, a missing one counts
 * as 1; a tie goes to the smaller topic id, then the smaller prerequisite id), and repeat until nothing cycles.
 * Topic indices follow the id order, so comparing indices compares ids.
 */
function breakCycles(n: number, edges: Edge[]): { kept: Edge[]; dropped: Edge[] } {
  let alive = edges;
  const dropped: Edge[] = [];
  for (;;) {
    const out: Edge[][] = Array.from({ length: n }, () => []);
    for (const e of alive) out[e.a].push(e);
    const cycle = findCycle(n, out);
    if (!cycle) return { kept: alive, dropped };
    let weak = cycle[0];
    for (const e of cycle) if (e.c < weak.c || (e.c === weak.c && (e.a < weak.a || (e.a === weak.a && e.b < weak.b)))) weak = e;
    alive = alive.filter((e) => e !== weak);
    dropped.push(weak);
  }
}

/** Prerequisites first */
function topoOrder(n: number, preq: number[][], dep: number[][]): number[] {
  const left = preq.map((p) => p.length);
  const queue: number[] = [];
  for (let v = 0; v < n; v++) if (!left[v]) queue.push(v);
  for (let i = 0; i < queue.length; i++) for (const d of dep[queue[i]]) if (--left[d] === 0) queue.push(d);
  return queue;
}

/** Left to right, no closer than one slot, inside 0..cols-1 */
function pack(aims: number[], cols: number): number[] {
  const p = [...aims];
  for (let i = 0; i < p.length; i++) p[i] = Math.max(p[i], i ? p[i - 1] + 1 : 0);
  for (let i = p.length - 1; i >= 0; i--) p[i] = Math.min(p[i], i < p.length - 1 ? p[i + 1] - 1 : cols - 1);
  return p;
}

type Block = {
  kind: "chapter" | "free" | "lone";
  /** Index in the ordered hubs for a chapter, -1 for the others */
  hub: number;
  topics: number[];
  /** Original order: the order of the hubs; a group with no hub goes by the chapters it is linked to (else after them, by first topic) */
  orig: number;
  /** Topics of each row of the block, bottom up */
  rows: number[][];
  cols: number;
  w: number;
  x0: number;
};

/**
 * Domain graph, bottom up, no physics: the same data gives the same picture.
 * Every `requires` edge (after cycles are broken) points strictly up. A chapter is a compact block; the blocks run
 * along a snake that rises like a staircase. See `graph-review/layout-spec.md`.
 */
export function layoutGraph(model: GraphModel, stored?: StoredPositions): Layout {
  const visible = visibleTopics(model);
  const n = visible.length;
  const idx = new Map(visible.map((t, i) => [t.id, i]));
  /** Place of a topic in the previous picture: only breaks ties */
  const prior = (v: number) => stored?.[visible[v].id]?.[0] ?? Infinity;
  const priorCmp = (a: number, b: number) => (prior(a) === prior(b) ? 0 : prior(a) < prior(b) ? -1 : 1) || a - b;

  // ---- edges: unique, cycles broken, so every kept edge can point strictly up
  const uniq = new Map<number, Edge>();
  for (const e of model.requires) {
    const a = idx.get(e.topicId);
    const b = idx.get(e.requiresId);
    if (a === undefined || b === undefined || a === b) continue;
    const c = e.confidence ?? 1;
    const old = uniq.get(a * n + b);
    if (!old || c > old.c) uniq.set(a * n + b, { a, b, c });
  }
  const all = [...uniq.values()].sort((x, y) => x.a - y.a || x.b - y.b);
  const { kept, dropped } = breakCycles(n, all);
  const preq: number[][] = Array.from({ length: n }, () => []);
  const dep: number[][] = Array.from({ length: n }, () => []);
  const nbAll: number[][] = Array.from({ length: n }, () => []);
  for (const e of kept) {
    preq[e.a].push(e.b);
    dep[e.b].push(e.a);
  }
  for (const e of all) {
    nbAll[e.a].push(e.b);
    nbAll[e.b].push(e.a);
  }
  const topo = topoOrder(n, preq, dep);

  // ---- home: a topic stands in the hub whose members it is linked to most (a tie: the earlier hub)
  const hubList = hubsInOrder(model);
  const hubIdx = new Map(hubList.map((h, i) => [h.id, i]));
  const members: Set<number>[] = hubList.map(() => new Set<number>());
  const hubsOf: number[][] = Array.from({ length: n }, () => []);
  for (const l of model.includes) {
    const h = hubIdx.get(l.hubId);
    const t = idx.get(l.topicId);
    if (h === undefined || t === undefined || members[h].has(t)) continue;
    members[h].add(t);
    hubsOf[t].push(h);
  }
  const home = new Array<number>(n).fill(-1);
  for (let t = 0; t < n; t++) {
    let bestScore = -1;
    for (const h of [...hubsOf[t]].sort((p, q) => p - q)) {
      const s = nbAll[t].filter((w) => members[h].has(w)).length;
      if (s > bestScore) {
        home[t] = h;
        bestScore = s;
      }
    }
  }

  // ---- blocks: chapters (hubs with home topics), groups of linked topics with no hub, the block of lone topics
  const mk = (kind: Block["kind"], hub: number, topics: number[], orig: number): Block => ({ kind, hub, topics, orig, rows: [], cols: 1, w: 0, x0: 0 });
  const blocks: Block[] = [];
  hubList.forEach((_, h) => {
    const ts = [...Array(n).keys()].filter((t) => home[t] === h);
    if (ts.length) blocks.push(mk("chapter", h, ts, h));
  });
  const free = [...Array(n).keys()].filter((t) => home[t] < 0);
  const group = free.map((_, i) => i);
  const slotOf = new Map(free.map((t, i) => [t, i]));
  const root = (i: number): number => (group[i] === i ? i : (group[i] = root(group[i])));
  for (const e of all) {
    const a = slotOf.get(e.a);
    const b = slotOf.get(e.b);
    if (a !== undefined && b !== undefined) group[Math.max(root(a), root(b))] = Math.min(root(a), root(b));
  }
  const groups = new Map<number, number[]>();
  const lone: number[] = [];
  free.forEach((t, i) => {
    if (!nbAll[t].length) lone.push(t);
    else groups.set(root(i), [...(groups.get(root(i)) ?? []), t]);
  });
  [...groups.values()].forEach((ts, i) => blocks.push(mk("free", -1, ts, hubList.length + i)));
  const blockOf = new Array<number>(n).fill(-1);
  blocks.forEach((b, i) => b.topics.forEach((t) => (blockOf[t] = i)));
  // A group with no hub stands where its links put it, like a chapter: right after the last chapter it needs, or, if it only
  // is needed, right before the first chapter that needs it (the order the hubs came in settles the rest)
  for (const b of blocks) {
    if (b.kind !== "free") continue;
    const needs = b.topics.flatMap((v) => preq[v]).map((p) => blockOf[p]).filter((o) => o >= 0 && blocks[o].kind === "chapter").map((o) => blocks[o].orig);
    const needed = b.topics.flatMap((v) => dep[v]).map((d) => blockOf[d]).filter((o) => o >= 0 && blocks[o].kind === "chapter").map((o) => blocks[o].orig);
    if (needs.length) b.orig = Math.max(...needs) + 0.5;
    else if (needed.length) b.orig = Math.min(...needed) - 0.5;
  }
  let loneBlock: Block | null = null;
  if (lone.length) {
    // Known topics keep their order (their old row, then x); new ones follow
    const oldY = (t: number) => stored?.[visible[t].id]?.[1];
    lone.sort((p, q) => {
      const yp = oldY(p);
      const yq = oldY(q);
      return yp !== undefined && yq !== undefined && yp !== yq ? yq - yp : priorCmp(p, q);
    });
    loneBlock = mk("lone", -1, lone, -1);
  }

  // Rows inside a block: a topic stands above everything it needs in the block, at most BLOCK_MAX_COLS to a row.
  // Topics with the longest chain above them go first (highest level first), the rest fill the room that is left
  const height = new Array<number>(n).fill(0);
  for (const v of [...topo].reverse()) for (const d of dep[v]) height[v] = Math.max(height[v], height[d] + 1);
  for (const b of blocks) {
    const inB = new Set(b.topics);
    const todo = [...b.topics].sort((p, q) => height[q] - height[p] || p - q);
    const rowOf = new Map<number, number>();
    for (let r = 0; todo.length; r++) {
      let ready = todo.filter((v) => preq[v].every((p) => !inB.has(p) || (rowOf.has(p) && rowOf.get(p)! < r))).slice(0, BLOCK_MAX_COLS);
      if (!ready.length) ready = [todo[0]];
      for (const v of ready) {
        rowOf.set(v, r);
        todo.splice(todo.indexOf(v), 1);
      }
      b.rows.push(ready);
    }
    const widest = Math.max(...b.rows.map((r) => r.length));
    b.cols = Math.min(BLOCK_MAX_COLS, Math.max(widest, b.rows.length >= ZIGZAG_FROM_ROWS ? ZIGZAG_COLS : 1));
  }
  if (loneBlock) {
    const cols = lone.length < 3 ? lone.length : Math.max(2, Math.min(LONE_MAX_COLS, Math.round(Math.sqrt(lone.length * LONE_ASPECT))));
    for (let i = 0; i < lone.length; i += cols) loneBlock.rows.push(lone.slice(i, i + cols));
    loneBlock.cols = cols;
  }
  for (const b of [...blocks, ...(loneBlock ? [loneBlock] : [])]) b.w = Math.max(b.cols * GAP_X, b.kind === "chapter" ? HUB_MIN_W : 0);

  // ---- order of the blocks along the snake: A before B when B needs A more often than A needs B; otherwise the earlier hub first
  const B = blocks.length;
  const need = new Map<number, number>();
  for (const e of kept) {
    const d = blockOf[e.a];
    const p = blockOf[e.b];
    if (d >= 0 && p >= 0 && d !== p) need.set(p * B + d, (need.get(p * B + d) ?? 0) + 1);
  }
  const succ: number[][] = blocks.map(() => []);
  const indeg = new Array<number>(B).fill(0);
  for (const [key, c] of [...need].sort((x, y) => x[0] - y[0])) {
    const p = Math.floor(key / B);
    const d = key % B;
    if (c > (need.get(d * B + p) ?? 0)) {
      succ[p].push(d);
      indeg[d]++;
    }
  }
  const order: number[] = [];
  const left = new Set(blocks.map((_, i) => i));
  while (left.size) {
    let pick = -1;
    for (const i of left) if (pick < 0 || indeg[i] < indeg[pick] || (indeg[i] === indeg[pick] && blocks[i].orig < blocks[pick].orig)) pick = i;
    left.delete(pick);
    order.push(pick);
    for (const d of succ[pick]) indeg[d]--;
  }
  const seq: Block[] = [...(loneBlock ? [loneBlock] : []), ...order.map((i) => blocks[i])];

  // ---- snake, horizontally: left to right, turn at the width limit, right to left, and so on
  let dir: 1 | -1 = 1;
  let front = 0;
  let lastLeft = 0;
  let lastRight = 0;
  seq.forEach((b, i) => {
    if (i > 0 && (dir === 1 ? front + b.w > MAX_ROW_WIDTH : front - b.w < 0)) {
      dir = dir === 1 ? -1 : 1;
      front = dir === 1 ? lastLeft : lastRight;
    }
    if (dir === 1) {
      b.x0 = front;
      front += b.w + BLOCK_GAP_X;
    } else {
      b.x0 = front - b.w;
      front = b.x0 - BLOCK_GAP_X;
    }
    lastLeft = b.x0;
    lastRight = b.x0 + b.w;
  });

  // ---- x inside a block: slots, ordered by the mean of the neighbours; a chain winds from side to side
  const relRow = new Array<number>(n).fill(0);
  const pos = new Array<number>(n).fill(0); // in slots, 0..cols-1
  const centerOf = (b: Block) => b.x0 + b.w / 2;
  for (const b of seq) {
    b.rows.forEach((row, r) => row.forEach((v) => (relRow[v] = r)));
    if (b.kind === "lone") {
      b.rows.forEach((row) => row.forEach((v, i) => (pos[v] = i)));
      continue;
    }
    const mid = (b.cols - 1) / 2;
    const inB = new Set(b.topics);
    const preIn = (v: number) => preq[v].filter((p) => inB.has(p));
    const depIn = (v: number) => dep[v].filter((d) => inB.has(d));
    // links to other blocks pull a topic to the side where those blocks lie
    const crossOf = new Map(
      b.topics.map((v) => {
        const out: number[] = [];
        for (const w of [...preq[v], ...dep[v]]) {
          const o = blockOf[w];
          if (o >= 0 && blocks[o] !== b) out.push(clamp((centerOf(blocks[o]) - centerOf(b)) / GAP_X + mid, 0, b.cols - 1));
        }
        return [v, out] as const;
      }),
    );
    b.rows.forEach((row) => {
      row.sort((p, q) => p - q);
      row.forEach((v, i) => (pos[v] = mid + (i - (row.length - 1) / 2)));
    });
    const chainDir = new Map<number, number>();
    // Only the last pass lets the previous picture settle a tie; the passes before it (and so the picture) never depend on it
    const pass = (up: boolean, last = false) => {
      const rs = b.rows.map((_, r) => r);
      if (!up) rs.reverse();
      for (const r of rs) {
        const row = b.rows[r];
        const aim = new Map<number, number>();
        for (const v of row) {
          const pre = preIn(v);
          if (up && pre.length === 1 && depIn(pre[0]).length === 1) {
            // a link of a chain: one step to the side, bouncing off the walls of the block
            const p = pre[0];
            let d = chainDir.get(p) ?? (pos[p] > mid ? -1 : 1);
            if (pos[p] + d < -1e-9 || pos[p] + d > b.cols - 1 + 1e-9) d = -d;
            chainDir.set(v, d);
            aim.set(v, clamp(pos[p] + d, 0, b.cols - 1));
            continue;
          }
          const near = [...(up ? pre : depIn(v)).map((w) => pos[w]), ...crossOf.get(v)!];
          aim.set(v, near.length ? mean(near) : pos[v]);
        }
        row.sort((p, q) => (Math.abs(aim.get(p)! - aim.get(q)!) > 1e-9 ? aim.get(p)! - aim.get(q)! : last ? priorCmp(p, q) : p - q));
        const placed = pack(row.map((v) => aim.get(v)!), b.cols);
        row.forEach((v, i) => (pos[v] = placed[i]));
      }
    };
    for (let s = 0; s < SWEEPS; s++) {
      pass(true);
      pass(false);
    }
    pass(true, true);
  }

  // ---- y: every block stands as low as the rules allow. Rows count from the foundation and may be fractional
  const rowAbs = new Array<number>(n).fill(NaN);
  const rects: { x0: number; x1: number; bot: number; top: number }[] = [];
  let prevBase = -SNAKE_RISE;
  for (const b of seq) {
    const span = b.rows.length - 1 + (b.kind === "chapter" ? HUB_BAND_ROWS : 0);
    // above everything its topics need, and above the base of the previous block of the snake
    let lo = Math.max(0, prevBase + SNAKE_RISE);
    for (const v of b.topics) for (const p of preq[v]) if (blockOf[p] !== blockOf[v] && !Number.isNaN(rowAbs[p])) lo = Math.max(lo, rowAbs[p] + 1 - relRow[v]);
    // and clear of the blocks already standing under it
    const clear = (o: number) => !rects.some((r) => r.x0 < b.x0 + b.w - 1e-9 && b.x0 < r.x1 - 1e-9 && o < r.top + 1 - 1e-9 && o + span + 1 > r.bot + 1e-9);
    const cands = [lo, ...rects.map((r) => r.top + 1).filter((c) => c > lo)].sort((p, q) => p - q);
    const o = cands.find(clear) ?? cands[cands.length - 1];
    for (const v of b.topics) rowAbs[v] = o + relRow[v];
    rects.push({ x0: b.x0, x1: b.x0 + b.w, bot: o, top: o + span });
    prevBase = o;
  }
  const px = new Array<number>(n).fill(0);
  for (const b of seq) for (const v of b.topics) px[v] = b.x0 + (b.w - b.cols * GAP_X) / 2 + (pos[v] + 0.5) * GAP_X;

  // ---- repair: if the order of the blocks could not keep every edge up (chapters that need each other), lift the topics
  // that stand too low, and then whoever they now cover
  for (let pass = 0; pass < REPAIR_PASSES; pass++) {
    let changed = false;
    for (const v of topo) {
      for (const p of preq[v]) {
        if (rowAbs[v] < rowAbs[p] + 1 - 1e-9) {
          rowAbs[v] = rowAbs[p] + 1;
          changed = true;
        }
      }
    }
    const byRow = [...Array(n).keys()].sort((p, q) => rowAbs[p] - rowAbs[q] || px[p] - px[q] || p - q);
    for (let i = 0; i < byRow.length; i++) {
      for (let j = i + 1; j < byRow.length && rowAbs[byRow[j]] - rowAbs[byRow[i]] < 0.5; j++) {
        const a = byRow[i];
        const c = byRow[j];
        if (Math.abs(px[a] - px[c]) < OVERLAP_X) {
          rowAbs[Math.max(a, c)] += 1;
          changed = true;
        }
      }
    }
    if (!changed) break;
  }

  // ---- nodes
  const nodes = new Map<string, LayoutNode>();
  const topicNodes: LayoutNode[] = visible.map((t, v) => {
    const node: LayoutNode = { key: t.id, id: t.id, kind: "topic", x: px[v], y: -rowAbs[v] * GAP_Y, layer: Math.round(rowAbs[v]) };
    nodes.set(node.key, node);
    return node;
  });
  const hubNodes: LayoutNode[] = [];
  const placeHub = (id: string, x: number, y: number) => {
    const node: LayoutNode = { key: hubKey(id), id, kind: "hub", x, y, layer: Math.round(-y / GAP_Y) };
    nodes.set(node.key, node);
    hubNodes.push(node);
  };
  // Hubs of chapters: centred over their block, above its highest topic
  for (const b of seq) {
    if (b.kind !== "chapter") continue;
    const xs = b.topics.map((v) => px[v]);
    const x = (Math.min(...xs) + Math.max(...xs)) / 2;
    let y = Math.min(...b.topics.map((v) => -rowAbs[v] * GAP_Y)) - HUB_RISE;
    // Only after a repair can something stand there: then the hub goes up until it is free
    const taken = (yy: number) =>
      topicNodes.some((q) => Math.abs(q.x - x) < OVERLAP_X && Math.abs(q.y - yy) < GAP_Y * 0.5) || hubNodes.some((o) => Math.abs(o.x - x) < HUB_GAP_X && Math.abs(o.y - yy) < GAP_Y * 0.7);
    for (let k = 0; k < 50 && taken(y); k++) y -= GAP_Y * 0.5;
    placeHub(hubList[b.hub].id, x, y);
  }
  // Collections: beside their topics, at the middle of them, outside the occupied area on the side where more of them are
  const collections = new Set<string>();
  const gx0 = topicNodes.length ? Math.min(...topicNodes.map((q) => q.x)) : 0;
  const gx1 = topicNodes.length ? Math.max(...topicNodes.map((q) => q.x)) : 0;
  const solid = [...topicNodes, ...hubNodes];
  const nothing: string[] = [];
  hubList.forEach((h, hi) => {
    if (blocks.some((b) => b.kind === "chapter" && b.hub === hi)) return;
    collections.add(h.id);
    const mine = [...members[hi]].map((v) => nodes.get(visible[v].id)!);
    if (!mine.length) {
      nothing.push(h.id);
      return;
    }
    const wantY = mean(mine.map((q) => q.y));
    const left = mine.filter((q) => q.x < (gx0 + gx1) / 2).length > mine.length / 2;
    const at = (y: number) => {
      const band = solid.filter((q) => Math.abs(q.y - y) < COLLECTION_BAND);
      const edge = band.length ? (left ? Math.min(...band.map((q) => q.x)) : Math.max(...band.map((q) => q.x))) : left ? gx0 : gx1;
      return left ? edge - COLLECTION_MARGIN : edge + COLLECTION_MARGIN;
    };
    // alternately below and above the middle until the place is free
    for (let k = 0; k < 60; k++) {
      const y = wantY + (k % 2 ? -1 : 1) * Math.ceil(k / 2) * COLLECTION_STEP_Y;
      const x = at(y);
      if (hubNodes.some((o) => Math.abs(o.x - x) < HUB_GAP_X && Math.abs(o.y - y) < GAP_Y * 0.7)) continue;
      placeHub(h.id, x, y);
      return;
    }
    placeHub(h.id, at(wantY), wantY);
  });
  // A hub with no topic on the graph: in a row above everything
  if (nothing.length) {
    const top = Math.min(0, ...[...nodes.values()].map((q) => q.y)) - GAP_Y;
    nothing.forEach((id, i) => placeHub(id, (gx0 + gx1) / 2 + (i - (nothing.length - 1) / 2) * HUB_GAP_X, top));
  }

  const every = [...topicNodes, ...hubNodes];
  const bounds = every.length
    ? { x0: Math.min(...every.map((p) => p.x)), x1: Math.max(...every.map((p) => p.x)), y0: Math.min(...every.map((p) => p.y)), y1: Math.max(...every.map((p) => p.y)) }
    : { x0: 0, x1: 0, y0: 0, y1: 0 };

  const outBlocks: LayoutBlock[] = seq.map((b) => ({
    key: b.kind === "chapter" ? `b:${hubList[b.hub].id}` : b.kind === "lone" ? "b:lone" : `b:free:${visible[b.topics[0]].id}`,
    kind: b.kind,
    hubId: b.kind === "chapter" ? hubList[b.hub].id : undefined,
    topicIds: b.topics.map((v) => visible[v].id),
    x0: b.x0,
    x1: b.x0 + b.w,
    baseY: -Math.min(...b.topics.map((v) => rowAbs[v])) * GAP_Y,
  }));
  return {
    nodes,
    topics: topicNodes,
    hubs: hubNodes,
    unlinked: [],
    bounds,
    blocks: outBlocks,
    collections,
    droppedEdges: dropped.map((e) => ({ topicId: visible[e.a].id, requiresId: visible[e.b].id })),
  };
}

const KEY = (learner: string, domainId: string) => `quack.graph.${learner}.${domainId}.pos`;
const STORAGE_VERSION = 11;

export function loadPositions(learner: string, domainId: string): StoredPositions | undefined {
  try {
    const raw = localStorage.getItem(KEY(learner, domainId));
    if (!raw) return undefined;
    const v = JSON.parse(raw) as { v?: number; pos?: StoredPositions };
    return v.v === STORAGE_VERSION && v.pos && typeof v.pos === "object" ? v.pos : undefined;
  } catch {
    return undefined;
  }
}

export function savePositions(learner: string, domainId: string, layout: Layout) {
  const pos: StoredPositions = {};
  for (const n of layout.nodes.values()) pos[n.key] = [Math.round(n.x * 10) / 10, Math.round(n.y * 10) / 10];
  try {
    localStorage.setItem(KEY(learner, domainId), JSON.stringify({ v: STORAGE_VERSION, pos }));
  } catch {
    /* no storage: the layout is recomputed next time */
  }
}
