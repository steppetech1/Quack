import { onGraph, type GraphHub, type GraphModel } from "./types";

/** Step between neighbours in a row, world units */
export const GAP_X = 110;
/** Step between rows inside a block (and the unit of every vertical rule), world units */
export const GAP_Y = 110;
/** A chapter block is at most this many topics wide */
const BLOCK_MAX_COLS = 4;
/** Empty space between two blocks that follow each other along the snake */
const BLOCK_GAP_X = GAP_X * 0.6;
/** A block starts on a grid of this many slots along the lap: a chapter that grows by a topic seldom moves the next one */
const BLOCK_GRID_SLOTS = 2;
/** A block with a hub is at least this wide, so the name of the hub has room above it */
const HUB_MIN_W = GAP_X * 2;
/** A hub stands this far above the highest topic of its block */
const HUB_RISE = GAP_Y * 0.75;
/** Two hubs are no closer than this */
const HUB_GAP_X = GAP_X * 1.35;
/**
 * One lap of the snake is this wide, world units. The graph is one long rising path (chapter after chapter, a run of topics
 * a gentle diagonal); where it reaches this width it turns and goes back the other way, one lap higher.
 */
const MAX_ROW_WIDTH = 1900;
/** A block that fits a lap waits for this much overflow before it moves to the next lap: a chapter that grows a little does not move the turn */
const SNAKE_SLACK = GAP_X * 2;
/** A block that stood in this lap last time waits for more (needs the saved positions) */
const SNAKE_STAY_SLACK = GAP_X * 4;
/** Clear rows between a lap and the lap above it, so laps never touch */
const LAP_GAP = 1;
/** Rows a hub name takes above the hub ring */
const HUB_LABEL_ROWS = 0.4;
/** Every next block of the snake starts at least this many rows above the base of the previous one: a staircase, never a flat row */
const SNAKE_RISE = 1;
/** A step along a run (a topic with one prerequisite in its chapter, which has one dependent there): one slot sideways and this many rows up */
const CHAIN_RISE = 0.3;
/** A topic that does not fit moves up by this many rows */
const LIFT_ROWS = 1;
/** Two topics of a block are no closer than this (slots) when they are less than `PLACE_DY` rows apart */
const SLOT_CLEAR = 0.95;
const PLACE_DY = 0.6;
/** How far (slots) a topic looks for a free place beside where it wants to stand, before it moves up */
const SEARCH_SLOTS = BLOCK_MAX_COLS + 2;
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
const REPAIR_PASSES = 200;

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
  /** Lap of the snake the block starts in (lap 0 runs to the right, lap 1 to the left, and so on) */
  lap: number;
  /** The block is longer than a lap and turns inside itself */
  wrapped: boolean;
  /** Horizontal extent the block reserves in its lap (the hub stands over its middle) */
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
 * Saved `[x, y]` per node key, and `[x, lap]` per block key (`b:...`). It is a hint, not a pin: it breaks ties in the order of nodes and is the point
 * a node glides from to its new place. The layout itself is computed from the data alone.
 */
export type StoredPositions = Record<string, [number, number]>;

export const hubKey = (id: string) => `h:${id}`;

type Edge = { a: number; b: number; c: number };

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;

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

type Block = {
  kind: "chapter" | "free" | "lone";
  /** Index in the ordered hubs for a chapter, -1 for the others */
  hub: number;
  topics: number[];
  /** Original order: the order of the hubs; a group with no hub goes by the chapters it is linked to (else after them, by first topic) */
  orig: number;
  /** Extent of the block in slots (x) and rows (y), counted for a row that goes to the right */
  minX: number;
  maxX: number;
  h: number;
  w: number;
  x0: number;
  /** Lap of the snake the block starts in, and whether it is longer than a lap and turns inside */
  lap: number;
  wrapped: boolean;
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
  const mk = (kind: Block["kind"], hub: number, topics: number[], orig: number): Block => ({ kind, hub, topics, orig, minX: 0, maxX: 0, h: 0, w: 0, x0: 0, lap: 0, wrapped: false });
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

  // Inside a block a topic stands above everything it needs there. Where the block is a run (a topic with exactly one
  // prerequisite in the block, which has exactly one dependent in the block) the next topic is one slot sideways and only
  // CHAIN_RISE up: a gentle diagonal. Branching and merging points rise a whole row. Rows are real numbers from the foot of the
  // block, x is in slots; both are laid out for a snake row that goes right (a row that goes left is mirrored)
  const height = new Array<number>(n).fill(0);
  for (const v of [...topo].reverse()) for (const d of dep[v]) height[v] = Math.max(height[v], height[d] + 1);
  const rise = new Map<number, number>();
  for (const e of kept) {
    const same = blockOf[e.a] >= 0 && blockOf[e.a] === blockOf[e.b];
    const pc = same ? preq[e.a].filter((q) => blockOf[q] === blockOf[e.a]).length : preq[e.a].length;
    const dc = same ? dep[e.b].filter((q) => blockOf[q] === blockOf[e.b]).length : dep[e.b].length;
    rise.set(e.a * n + e.b, pc === 1 && dc === 1 ? CHAIN_RISE : 1);
  }
  /** How far above `p` the topic `d` that needs it stands at least */
  const riseOf = (d: number, p: number) => rise.get(d * n + p) ?? 1;
  const xl = new Array<number>(n).fill(0);
  const yl = new Array<number>(n).fill(0);
  for (const b of blocks) {
    const inB = new Set(b.topics);
    const level = new Map<number, number>();
    for (const v of topo) if (inB.has(v)) level.set(v, Math.max(0, ...preq[v].filter((q) => inB.has(q)).map((q) => level.get(q)! + 1)));
    const order = [...b.topics].sort((p, q) => level.get(p)! - level.get(q)! || height[q] - height[p] || p - q);
    const done: number[] = [];
    for (const v of order) {
      const pre = preq[v].filter((q) => inB.has(q));
      const link = pre.length === 1 && riseOf(v, pre[0]) === CHAIN_RISE;
      const y0 = Math.max(0, ...pre.map((q) => yl[q] + riseOf(v, q)));
      const aim = !pre.length ? 0 : link ? xl[pre[0]] + 1 : mean(pre.map((q) => xl[q]));
      let at: { x: number; y: number } | null = null;
      for (let y = y0; !at; y += LIFT_ROWS) {
        const near = done.filter((q) => Math.abs(yl[q] - y) < PLACE_DY);
        // a row of the block holds at most BLOCK_MAX_COLS topics (a run does not count: it climbs, it does not fill a row)
        if (!link && near.filter((q) => Math.abs(yl[q] - y) < 0.5).length >= BLOCK_MAX_COLS) continue;
        for (let k = 0; k <= SEARCH_SLOTS && !at; k++) {
          for (const off of k ? [k, -k] : [0]) {
            if (near.every((q) => Math.abs(xl[q] - (aim + off)) >= SLOT_CLEAR - 1e-9)) {
              at = { x: aim + off, y };
              break;
            }
          }
        }
      }
      xl[v] = at.x;
      yl[v] = at.y;
      done.push(v);
    }
    b.minX = Math.min(...b.topics.map((v) => xl[v]));
    b.maxX = Math.max(...b.topics.map((v) => xl[v]));
    b.h = Math.max(...b.topics.map((v) => yl[v]));
  }
  if (loneBlock) {
    const cols = lone.length < 3 ? lone.length : Math.max(2, Math.min(LONE_MAX_COLS, Math.round(Math.sqrt(lone.length * LONE_ASPECT))));
    lone.forEach((v, i) => {
      xl[v] = i % cols;
      yl[v] = Math.floor(i / cols);
    });
    loneBlock.minX = 0;
    loneBlock.maxX = cols - 1;
    loneBlock.h = Math.ceil(lone.length / cols) - 1;
  }
  for (const b of [...blocks, ...(loneBlock ? [loneBlock] : [])]) b.w = Math.max((b.maxX - b.minX + 1) * GAP_X, b.kind === "chapter" ? HUB_MIN_W : 0);

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

  // ---- the snake: blocks one after another along one rising path that is folded into laps of MAX_ROW_WIDTH.
  // Lap 0 runs to the right, lap 1 back to the left (mirrored), and so on. A block that fits a lap never turns inside:
  // it goes to the next lap when it would overflow. A block longer than a lap turns inside itself, at the topic
  const keyOf = (b: Block) => (b.kind === "chapter" ? `b:${hubList[b.hub].id}` : b.kind === "lone" ? "b:lone" : `b:free:${visible[b.topics[0]].id}`);
  const lapOf = new Array<number>(n).fill(0);
  const ux = new Array<number>(n).fill(0); // x along the lap before the mirror
  const grid = BLOCK_GRID_SLOTS * GAP_X;
  let lap = 0;
  let cur = 0;
  for (const b of seq) {
    const slots = b.maxX - b.minX + 1;
    b.w = Math.max(slots * GAP_X, b.kind === "chapter" ? HUB_MIN_W : 0);
    b.wrapped = b.w > MAX_ROW_WIDTH;
    if (!b.wrapped) {
      const slack = stored?.[keyOf(b)]?.[1] === lap ? SNAKE_STAY_SLACK : SNAKE_SLACK;
      if (cur > 0 && cur + b.w > MAX_ROW_WIDTH + slack) {
        lap++;
        cur = 0;
      }
      b.lap = lap;
      b.x0 = cur;
      for (const v of b.topics) {
        lapOf[v] = lap;
        ux[v] = cur + (b.w - slots * GAP_X) / 2 + (xl[v] - b.minX + 0.5) * GAP_X;
      }
      cur += b.w;
    } else {
      b.lap = lap;
      b.x0 = cur;
      for (const v of b.topics) {
        const g = cur + (xl[v] - b.minX + 0.5) * GAP_X;
        const k = Math.floor(g / MAX_ROW_WIDTH);
        lapOf[v] = lap + k;
        ux[v] = g - k * MAX_ROW_WIDTH;
      }
      const end = cur + b.w;
      lap += Math.floor(end / MAX_ROW_WIDTH);
      cur = end % MAX_ROW_WIDTH;
    }
    cur = Math.ceil((cur + BLOCK_GAP_X) / grid) * grid;
  }
  const mirror = (k: number, x: number) => (k % 2 ? MAX_ROW_WIDTH - x : x);
  const px = new Array<number>(n).fill(0);
  for (let v = 0; v < n; v++) px[v] = mirror(lapOf[v], ux[v]);

  // ---- y along the path: every block as low as the rules allow, and always above the base of the block before it.
  // Rows count from the foundation and are fractional
  const rowAbs = new Array<number>(n).fill(NaN);
  let prevBase = -SNAKE_RISE;
  for (const b of seq) {
    let lo = Math.max(0, prevBase + SNAKE_RISE);
    for (const v of b.topics) for (const p of preq[v]) if (blockOf[p] !== blockOf[v] && !Number.isNaN(rowAbs[p])) lo = Math.max(lo, rowAbs[p] + riseOf(v, p) - yl[v]);
    for (const v of b.topics) rowAbs[v] = lo + yl[v];
    prevBase = lo;
  }
  // ---- laps: lap k+1 stands above everything of the laps below it that is near it in x, with LAP_GAP clear rows between
  // (hubs count, with their names). The offset of a lap never falls below the one under it, so edges keep pointing up
  type Item = { lap: number; x: number; y: number; hub: boolean };
  const items: Item[] = [];
  for (let v = 0; v < n; v++) items.push({ lap: lapOf[v], x: px[v], y: rowAbs[v], hub: false });
  for (const b of seq) {
    if (b.kind !== "chapter") continue;
    const top = b.topics.reduce((best, v) => (rowAbs[v] > rowAbs[best] ? v : best), b.topics[0]);
    items.push({ lap: lapOf[top], x: px[top], y: rowAbs[top] + HUB_RISE / GAP_Y, hub: true });
  }
  const lapCount = n ? Math.max(...lapOf) + 1 : 0;
  const lapShift = new Array<number>(lapCount).fill(0);
  for (let k = 1; k < lapCount; k++) {
    let o = lapShift[k - 1];
    for (const v of items) {
      if (v.lap !== k) continue;
      for (const u of items) {
        if (u.lap >= k) continue;
        const clearX = u.hub || v.hub ? HUB_MIN_W / 2 + GAP_X / 2 : GAP_X * SLOT_CLEAR;
        if (Math.abs(u.x - v.x) < clearX) o = Math.max(o, u.y + lapShift[u.lap] + LAP_GAP + (u.hub ? HUB_LABEL_ROWS : 0) - v.y);
      }
    }
    lapShift[k] = o;
  }
  for (let v = 0; v < n; v++) rowAbs[v] += lapShift[lapOf[v]];

  // ---- repair: if the order of the blocks could not keep every edge up (chapters that need each other), lift the topics
  // that stand too low, and then whoever they now cover
  for (let pass = 0; pass < REPAIR_PASSES; pass++) {
    let changed = false;
    for (const v of topo) {
      for (const p of preq[v]) {
        if (rowAbs[v] < rowAbs[p] + riseOf(v, p) - 1e-9) {
          rowAbs[v] = rowAbs[p] + riseOf(v, p);
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
  // Whatever the passes left, an edge that points down is worse than two nodes that stand close: one last lift, no more moving aside
  for (const v of topo) for (const p of preq[v]) rowAbs[v] = Math.max(rowAbs[v], rowAbs[p] + riseOf(v, p));

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
  // Hubs of chapters: over the middle of their block, just above its highest topic
  for (const b of seq) {
    if (b.kind !== "chapter") continue;
    const xs = b.topics.map((v) => px[v]);
    // over the middle of the block, but no further from its highest topic than a quarter of the block's width
    const centre = (Math.min(...xs) + Math.max(...xs)) / 2;
    const top = b.topics.reduce((best, v) => (rowAbs[v] > rowAbs[best] ? v : best), b.topics[0]);
    const x = b.wrapped ? px[top] : clamp(px[top], centre - (Math.max(...xs) - Math.min(...xs)) / 4, centre + (Math.max(...xs) - Math.min(...xs)) / 4);
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
    key: keyOf(b),
    kind: b.kind,
    lap: b.lap,
    wrapped: b.wrapped,
    hubId: b.kind === "chapter" ? hubList[b.hub].id : undefined,
    topicIds: b.topics.map((v) => visible[v].id),
    x0: b.wrapped ? 0 : mirror(b.lap, b.x0 + (b.lap % 2 ? b.w : 0)),
    x1: b.wrapped ? MAX_ROW_WIDTH : mirror(b.lap, b.x0 + (b.lap % 2 ? 0 : b.w)),
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
const STORAGE_VERSION = 13;

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
  // a block remembers the row of the snake it stood in: that is what keeps the turn of the snake where it was
  for (const b of layout.blocks) pos[b.key] = [Math.round(b.x0 * 10) / 10, b.lap];
  try {
    localStorage.setItem(KEY(learner, domainId), JSON.stringify({ v: STORAGE_VERSION, pos }));
  } catch {
    /* no storage: the layout is recomputed next time */
  }
}
