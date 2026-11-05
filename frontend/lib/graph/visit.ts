import { chainOf } from "./layout";
import { onGraph, type GraphModel } from "./types";

/**
 * What the learner has seen of a domain, kept on this device (the backend has no "last visit").
 * Two limits follow: changes seen on another device look new again, and a changed note or context of a topic is not tracked.
 */
export type Snapshot = {
  topics: string[];
  hubs: string[];
  /** `topic>requires` */
  edges: string[];
  /** Topics that were in the left column */
  unlinked: string[];
};

export function snapshotOf(model: GraphModel): Snapshot {
  const { edges, unlinked } = chainOf(model);
  return {
    topics: model.topics.filter(onGraph).map((t) => t.id),
    hubs: model.hubs.filter((h) => h.kind !== "domain_all").map((h) => h.id),
    edges: edges.map((e) => `${e.topicId}>${e.requiresId}`),
    unlinked,
  };
}

export type Changes = { topics: Set<string>; hubs: Set<string> };

/** What is new in `cur` compared with `base`: new topics and hubs, topics with new links, topics that left the column */
export function changesBetween(base: Snapshot, cur: Snapshot): Changes {
  const had = {
    topics: new Set(base.topics),
    hubs: new Set(base.hubs),
    edges: new Set(base.edges),
    unlinked: new Set(base.unlinked),
  };
  const topics = new Set<string>();
  const hubs = new Set<string>();
  for (const t of cur.topics) if (!had.topics.has(t)) topics.add(t);
  for (const h of cur.hubs) if (!had.hubs.has(h)) hubs.add(h);
  for (const e of cur.edges) {
    if (had.edges.has(e)) continue;
    const [a, b] = e.split(">");
    if (had.topics.has(a)) topics.add(a);
    if (had.topics.has(b)) topics.add(b);
  }
  const now = new Set(cur.unlinked);
  for (const t of base.unlinked) if (cur.topics.includes(t) && !now.has(t)) topics.add(t);
  return { topics, hubs };
}

const snapKey = (learner: string, domainId: string) => `quack.graph.${learner}.${domainId}.snap`;

export function loadSnapshot(learner: string, domainId: string): Snapshot | undefined {
  try {
    const raw = localStorage.getItem(snapKey(learner, domainId));
    if (!raw) return undefined;
    const v = JSON.parse(raw) as { v?: number; snap?: Snapshot };
    return v.v === 1 && v.snap && Array.isArray(v.snap.topics) ? v.snap : undefined;
  } catch {
    return undefined;
  }
}

export function saveSnapshot(learner: string, domainId: string, snap: Snapshot) {
  try {
    localStorage.setItem(snapKey(learner, domainId), JSON.stringify({ v: 1, snap }));
  } catch {
    /* nothing to compare with next time: the indicator will stay off */
  }
}

const lastKey = (learner: string, domainId: string) => `quack.graph.${learner}.${domainId}.lastnew`;

/** The last non-empty set of changes: when the next visit finds nothing new, "New" shows this again */
export function loadLastChanges(learner: string, domainId: string): { topics: string[]; hubs: string[] } | undefined {
  try {
    const raw = localStorage.getItem(lastKey(learner, domainId));
    if (!raw) return undefined;
    const v = JSON.parse(raw) as { v?: number; topics?: string[]; hubs?: string[] };
    return v.v === 1 && Array.isArray(v.topics) && Array.isArray(v.hubs) ? { topics: v.topics, hubs: v.hubs } : undefined;
  } catch {
    return undefined;
  }
}

export function saveLastChanges(learner: string, domainId: string, c: { topics: Iterable<string>; hubs: Iterable<string> }) {
  try {
    localStorage.setItem(lastKey(learner, domainId), JSON.stringify({ v: 1, topics: [...c.topics], hubs: [...c.hubs] }));
  } catch {
    /* the next visit with no changes just will not blink */
  }
}

/** Where the camera stood. The anchor is the topic nearest to the centre: if it is gone, a neighbour takes its place */
export type SavedCamera = {
  k: number;
  /** World point at the centre of the graph area */
  cx: number;
  cy: number;
  anchor?: { id: string; x: number; y: number };
  near: { id: string; x: number; y: number }[];
};

const camKey = (learner: string, domainId: string) => `quack.graph.${learner}.${domainId}.cam`;

export function loadCamera(learner: string, domainId: string): SavedCamera | undefined {
  try {
    const raw = localStorage.getItem(camKey(learner, domainId));
    if (!raw) return undefined;
    const v = JSON.parse(raw) as { v?: number; cam?: SavedCamera };
    const c = v.cam;
    return v.v === 1 && c && Number.isFinite(c.k) && Number.isFinite(c.cx) && Number.isFinite(c.cy) ? { ...c, near: c.near ?? [] } : undefined;
  } catch {
    return undefined;
  }
}

export function saveCamera(learner: string, domainId: string, cam: SavedCamera) {
  try {
    localStorage.setItem(camKey(learner, domainId), JSON.stringify({ v: 1, cam }));
  } catch {
    /* the next visit starts from the whole graph */
  }
}

/** The world point to centre on: the anchor's current place plus the old offset, else a neighbour's, else the old point */
export function restoreCenter(cam: SavedCamera, place: (id: string) => { x: number; y: number } | undefined): { x: number; y: number } {
  for (const a of [cam.anchor, ...cam.near]) {
    if (!a) continue;
    const now = place(a.id);
    if (now) return { x: now.x + (cam.cx - a.x), y: now.y + (cam.cy - a.y) };
  }
  return { x: cam.cx, y: cam.cy };
}
