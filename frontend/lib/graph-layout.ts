import type { Domain, Topic } from "./mock/types";

const PAD = 12;

/** Ширина подписи главы на экране, px: название и под ним строка дедлайна */
export const hubLabelPx = (hub: Domain["hubs"][number]) => {
  const title = hub.title.replace(/­/g, "").length * 8.8;
  const dl = hub.deadline ? 22 + 6 * 9 + 10 * 9 : 0; // утка + «колл. 1 ноября»
  return 22 + Math.max(title, dl);
};

/** Режим графа: обзор глав или одна глава крупно */
export type GraphMode = { kind: "overview" } | { kind: "hub"; hubId: string };

export type PlacedTopic = Topic & {
  x: number;
  y: number;
  /** −1 у свободной темы (без главы) */
  hubIndex: number;
  /** направление «наружу» от центра кольца (рад); у свободной — от центра */
  angle: number;
  retention: number;
  /** Откуда идут прямые связи: глава (корень цепочки) или темы-предшественники */
  links: { x: number; y: number }[];
};
export type PlacedOutside = {
  id: string;
  title: string;
  fromDomain: string;
  x: number;
  y: number;
  linkedTo: string;
  angle: number;
};
export type PlacedHub = {
  id: string;
  index: number;
  x: number;
  y: number;
  /** радиус веера тем от нода */
  r: number;
  count: number;
  /** Подпись внутри кольца: 1 — справа от нода, −1 — слева */
  labelSide: 1 | -1;
  /** Подпись под нодом (true) или над ним (false) */
  labelBelow: boolean;
  /** доля тем «решаешь сам» и «пора повторить», 0–1 */
  progress: number;
};
export type HubLink = { id: string; d: string; suggested: boolean };

/**
 * hub — главный нод → корень цепочки; prereq — «нужно до» внутри главы;
 * cross — «нужно до» между главами или со свободной темой; related — «похоже по смыслу»
 */
export type EdgeKind = "hub" | "prereq" | "cross" | "related";
export type GraphEdge = {
  id: string;
  kind: EdgeKind;
  /** id концов: у hub-ребра первый — id главы */
  a: string;
  b: string;
  /** индексы глав концов, −1 у свободной темы */
  ha: number;
  hb: number;
  /** путь: прямая для hub/prereq, хорда через внутренность кольца для cross/related */
  d: string;
};

export type GraphLayout = {
  hubs: PlacedHub[];
  topics: PlacedTopic[];
  outside: PlacedOutside[];
  /** Связи глав «строится на»: дуги по кольцу */
  links: HubLink[];
  edges: GraphEdge[];
  /** Кольцо: центр и радиус */
  ring: { cx: number; cy: number; R: number };
  width: number;
  height: number;
};

type Pt = { x: number; y: number };
type Rect = { x0: number; x1: number; y0: number; y1: number };

const hashStr = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
};

/** Масштаб, при котором считаем место под подписи глав (они постоянного экранного размера) */
const LABEL_K = 0.55;
/** Шаг между темами вдоль кольца и между слоями веера, мир */
const S = 66;
const STEP = 46;
const L0 = 66;
/** Угловой размах кольца: от 6:40 по часовой до 5:20, снизу пустой сектор 40° */
const START = (200 * Math.PI) / 180;
const SPAN = (320 * Math.PI) / 180;
const DEG = Math.PI / 180;

/** Темы главы по слоям глубины «нужно до»; порядок в слое — по барицентрам, чтобы связи не пересекались */
function orderedLayers(list: { t: Topic; d: number }[]): Topic[][] {
  if (!list.length) return [];
  const ids = new Set(list.map(({ t }) => t.id));
  const inHub = (t: Topic) => t.requires.filter((id) => ids.has(id));
  const dm = Math.max(...list.map((m) => m.d));
  const layers: Topic[][] = Array.from({ length: dm + 1 }, () => []);
  list.forEach(({ t, d }) => layers[d].push(t));
  const kidsOf = new Map<string, string[]>();
  list.forEach(({ t }) => inHub(t).forEach((id) => kidsOf.set(id, [...(kidsOf.get(id) ?? []), t.id])));
  const frac = new Map<string, number>();
  const reindex = () => layers.forEach((l) => l.forEach((t, i) => frac.set(t.id, (i + 0.5) / l.length)));
  reindex();
  const sortLayer = (l: Topic[], nb: (t: Topic) => string[]) => {
    const keyed = l.map((t, i) => {
      const n = nb(t);
      return { t, i, k: n.length ? n.reduce((sum, id) => sum + (frac.get(id) ?? 0), 0) / n.length : frac.get(t.id)! };
    });
    keyed.sort((p, q) => p.k - q.k || p.i - q.i);
    keyed.forEach((e, i) => (l[i] = e.t));
    reindex();
  };
  for (let pass = 0; pass < 3; pass++) {
    for (let d = 1; d <= dm; d++) sortLayer(layers[d], inHub);
    for (let d = dm - 1; d >= 0; d--) sortLayer(layers[d], (t) => kidsOf.get(t.id) ?? []);
  }
  return layers;
}

/**
 * Кольцо: главные ноды на большой окружности по часовой стрелке (хаб 0 внизу слева, внизу пустой сектор),
 * темы каждой главы веером наружу, свободные темы внутри кольца. Радиус подбирается так, чтобы
 * веера соседей не перекрывались, а подписи (внутри кольца) ни с чем не пересекались.
 * Только + − × ÷, sqrt, sin и cos от округлённых аргументов; координаты округляются в конце.
 */
export function layoutDomain(domain: Domain): GraphLayout {
  const n = domain.hubs.length;
  const byId = new Map(domain.topics.map((t) => [t.id, t]));
  const hubIndex = new Map(domain.hubs.map((h, i) => [h.id, i]));
  const hubOf = (t: Topic) => (t.hubId !== undefined ? (hubIndex.get(t.hubId) ?? -1) : -1);

  // ── глубина «нужно до» внутри главы ──
  const depth = new Map<string, number>();
  const depthOf = (t: Topic): number => {
    const known = depth.get(t.id);
    if (known !== undefined) return known;
    const hi = hubOf(t);
    const inHub = hi < 0 ? [] : t.requires.map((id) => byId.get(id)).filter((p): p is Topic => !!p && hubOf(p) === hi);
    const d = inHub.length ? Math.max(...inHub.map(depthOf)) + 1 : 0;
    depth.set(t.id, d);
    return d;
  };
  const lists = domain.hubs.map((_, hi) =>
    domain.topics
      .filter((t) => hubOf(t) === hi)
      .map((t, order) => ({ t, d: depthOf(t), order }))
      .sort((a, b) => a.d - b.d || a.order - b.order),
  );
  const freeTopics = domain.topics.filter((t) => hubOf(t) < 0);
  const layersOf = lists.map((l) => orderedLayers(l));

  // ── рёбра ──
  type E = { id: string; kind: EdgeKind; a: string; b: string; ha: number; hb: number };
  const edges: E[] = [];
  const seen = new Set<string>();
  const pairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);
  const hiOfId = (id: string) => {
    const t = byId.get(id);
    return t ? hubOf(t) : -1;
  };
  domain.topics.forEach((t) => {
    const hb = hubOf(t);
    let hasParentInHub = false;
    t.requires.forEach((pid) => {
      const p = byId.get(pid);
      if (!p || pid === t.id) return;
      const key = pairKey(pid, t.id);
      if (seen.has(key)) return;
      seen.add(key);
      const ha = hubOf(p);
      if (ha >= 0 && ha === hb) {
        hasParentInHub = true;
        edges.push({ id: `${pid}>${t.id}`, kind: "prereq", a: pid, b: t.id, ha, hb });
      } else edges.push({ id: `${pid}>${t.id}`, kind: "cross", a: pid, b: t.id, ha, hb });
    });
    if (hb >= 0 && !hasParentInHub) edges.push({ id: `h:${domain.hubs[hb].id}>${t.id}`, kind: "hub", a: domain.hubs[hb].id, b: t.id, ha: hb, hb });
  });
  domain.topics.forEach((t) =>
    (t.related ?? []).forEach((rid) => {
      if (!byId.has(rid) || rid === t.id) return;
      const key = pairKey(rid, t.id);
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ id: `${t.id}~${rid}`, kind: "related", a: t.id, b: rid, ha: hubOf(t), hb: hiOfId(rid) });
    }),
  );

  // ── размеры подписей: мир при LABEL_K ──
  const labelW = domain.hubs.map((hub) => (hubLabelPx(hub) + 10) / LABEL_K);
  const labelH = domain.hubs.map((hub) => (hub.deadline ? 44 : 26) / LABEL_K);

  // угловой сектор главы 36–62° по числу тем; если все вместе не влезают — сжимаем поровну
  const PADA = 4 * DEG;
  const alpha0 = lists.map((l) => Math.min(66, Math.max(44, 28 + 4.2 * l.length)) * DEG);
  const alphaSum = alpha0.reduce((sum, v) => sum + v, 0);
  const sc = Math.min(1, (SPAN - PADA * Math.max(0, n - 1)) / Math.max(1e-6, alphaSum));
  const alpha = alpha0.map((v) => v * sc);

  /** Раскладка при радиусе R: сектора, ноды, темы-веера, подписи. null — не помещается */
  type Arr = {
    R: number;
    phi: number[];
    hx: number[];
    hy: number[];
    pos: Map<string, Pt>;
    side: (1 | -1)[];
    below: boolean[];
    rects: Rect[];
  };
  const arrange = (R: number): Arr | null => {
    // веер должен помещаться: самый плотный слой на своём радиусе
    for (let hi = 0; hi < n; hi++) {
      for (let d = 0; d < layersOf[hi].length; d++) {
        if ((layersOf[hi][d].length * S) / (R + L0 + d * STEP) > alpha[hi]) return null;
      }
    }
    const pad = PADA;
    const total = alpha.reduce((sum, v) => sum + v, 0) + pad * Math.max(0, n - 1);
    if (total > SPAN + 1e-9) return null;
    const slack = n > 1 ? (SPAN - total) / (n - 1) : 0;
    const phi: number[] = [];
    let cur = START;
    alpha.forEach((a) => {
      phi.push(cur + a / 2);
      cur += a + pad + slack;
    });
    const polar = (r: number, f: number): Pt => ({ x: r * Math.sin(f), y: -r * Math.cos(f) });
    const hx = phi.map((f) => polar(R, f).x), hy = phi.map((f) => polar(R, f).y);
    const pos = new Map<string, Pt>();
    layersOf.forEach((layers, hi) => {
      const U = new Map<string, number>();
      const ids = new Set(lists[hi].map(({ t }) => t.id));
      layers.forEach((layer, d) => {
        const r = R + L0 + d * STEP;
        const gap = S / r;
        const lo = phi[hi] - alpha[hi] / 2 + gap / 2, hi2 = phi[hi] + alpha[hi] / 2 - gap / 2;
        const m = layer.length;
        // каждый слой раскидан по всему сектору; ребёнок тянется к родителям, одиночка чуть гнётся (зигзаг ±10°)
        const u = layer.map((t, k) => {
          const even = m === 1 ? phi[hi] : lo + (k / (m - 1)) * (hi2 - lo);
          if (d === 0) return even;
          const ps = t.requires.filter((id) => ids.has(id));
          const pm = ps.reduce((sum, id) => sum + (U.get(id) ?? phi[hi]), 0) / Math.max(1, ps.length);
          if (m === 1) return pm + (d % 2 ? 1 : -1) * 10 * DEG;
          return 0.75 * even + 0.25 * pm;
        });
        u[0] = Math.max(u[0], lo);
        for (let k = 1; k < m; k++) u[k] = Math.max(u[k], u[k - 1] + gap);
        if (u[m - 1] > hi2) {
          u[m - 1] = hi2;
          for (let k = m - 2; k >= 0; k--) u[k] = Math.min(u[k], u[k + 1] - gap);
        }
        layer.forEach((t, k) => {
          U.set(t.id, u[k]);
          pos.set(t.id, polar(r, u[k]));
        });
      });
    });
    // подписи внутри кольца: к центру по горизонтали, у верхних нодов под нодом, у нижних над ним
    const side: (1 | -1)[] = [];
    const below: boolean[] = [];
    const rects: Rect[] = [];
    const rectFor = (i: number, s: 1 | -1, b: boolean): Rect => ({
      x0: s > 0 ? hx[i] + 20 : hx[i] - 20 - labelW[i],
      x1: s > 0 ? hx[i] + 20 + labelW[i] : hx[i] - 20,
      y0: b ? hy[i] + 2 : hy[i] - 2 - labelH[i],
      y1: b ? hy[i] + 2 + labelH[i] : hy[i] - 2,
    });
    const hits = (r: Rect, self: number) => {
      for (let j = 0; j < n; j++) {
        if (j === self) continue;
        if (hx[j] > r.x0 - 30 && hx[j] < r.x1 + 30 && hy[j] > r.y0 - 26 && hy[j] < r.y1 + 26) return true;
        const o = rects[j];
        if (o && r.x0 < o.x1 + 14 && r.x1 > o.x0 - 14 && r.y0 < o.y1 + 8 && r.y1 > o.y0 - 8) return true;
      }
      for (const p of pos.values()) {
        if (p.x > r.x0 - 14 && p.x < r.x1 + 14 && p.y > r.y0 - 16 && p.y < r.y1 + 16) return true;
      }
      return false;
    };
    for (let i = 0; i < n; i++) {
      const s0: 1 | -1 = hx[i] < 0 ? 1 : -1;
      const b0 = hy[i] < 0;
      const tries: [1 | -1, boolean][] = [[s0, b0], [s0, !b0], [-s0 as 1 | -1, b0], [-s0 as 1 | -1, !b0]];
      const ok = tries.find(([s, b]) => !hits(rectFor(i, s, b), i));
      if (!ok) return null;
      side[i] = ok[0];
      below[i] = ok[1];
      rects[i] = rectFor(i, ok[0], ok[1]);
    }
    return { R, phi, hx, hy, pos, side, below, rects };
  };
  let arr: Arr | null = null;
  for (let R = 200; R < 2400 && !arr; R += 12) arr = arrange(R);
  if (!arr) arr = arrange(2400)!;
  const { R, hx, hy, pos, side, below, rects } = arr;

  // ── свободные темы: внутри кольца, у центра связанных тем ──
  const nbrs = new Map<string, string[]>();
  edges.forEach((e) => {
    if (e.kind === "hub") return;
    nbrs.set(e.a, [...(nbrs.get(e.a) ?? []), e.b]);
    nbrs.set(e.b, [...(nbrs.get(e.b) ?? []), e.a]);
  });
  const free = freeTopics.map((t, k) => {
    const linked = (nbrs.get(t.id) ?? []).map((id) => pos.get(id)).filter((p): p is Pt => !!p);
    const ax = linked.length ? linked.reduce((s, p) => s + p.x, 0) / linked.length : 0;
    const ay = linked.length ? linked.reduce((s, p) => s + p.y, 0) / linked.length : 0;
    // небольшой детерминированный разнос, чтобы точки не лежали друг на друге
    return { t, x: ax * 0.45 + ((k % 3) - 1) * 8, y: ay * 0.45 + ((k % 2) - 0.5) * 8, ix: ax * 0.45, iy: ay * 0.45 };
  });
  const LIM = R * 0.55, LIM0 = R * 0.25;
  for (let it = 0; it < 160; it++) {
    for (let i = 0; i < free.length; i++) {
      for (let j = i + 1; j < free.length; j++) {
        const dx = free[j].x - free[i].x, dy = free[j].y - free[i].y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d >= 66 || d < 1e-6) continue;
        const k = ((66 - d) / d) * 0.5;
        free[i].x -= dx * k;
        free[i].y -= dy * k;
        free[j].x += dx * k;
        free[j].y += dy * k;
      }
    }
    free.forEach((f) => {
      f.x += (f.ix - f.x) * 0.03;
      f.y += (f.iy - f.y) * 0.03;
      for (let h = 0; h < n; h++) {
        const dx = f.x - hx[h], dy = f.y - hy[h];
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < 90 && d > 1e-6) {
          f.x = hx[h] + (dx / d) * 90;
          f.y = hy[h] + (dy / d) * 90;
        }
        const r = rects[h];
        const e0 = r.x0 - 16, e1 = r.x1 + 16, f0 = r.y0 - 16, f1 = r.y1 + 16;
        if (f.x > e0 && f.x < e1 && f.y > f0 && f.y < f1) {
          const dl = f.x - e0, dr = e1 - f.x, du = f.y - f0, dd = f1 - f.y;
          const m = Math.min(dl, dr, du, dd);
          if (m === dl) f.x = e0;
          else if (m === dr) f.x = e1;
          else if (m === du) f.y = f0;
          else f.y = f1;
        }
      }
      const d = Math.sqrt(f.x * f.x + f.y * f.y);
      if (d > LIM) {
        f.x = (f.x / d) * LIM;
        f.y = (f.y / d) * LIM;
      } else if (d < LIM0) {
        const ux = d > 1e-6 ? f.x / d : 1, uy = d > 1e-6 ? f.y / d : 0;
        f.x = ux * LIM0;
        f.y = uy * LIM0;
      }
    });
  }
  free.forEach((f) => pos.set(f.t.id, { x: f.x, y: f.y }));

  // ── сдвиг: центр кольца в начало, затем рамка с полями ──
  const hubRects = rects;
  const frame = () => {
    const f = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    const grow = (ax: number, ay: number, bx: number, by: number) => {
      f.x0 = Math.min(f.x0, ax);
      f.y0 = Math.min(f.y0, ay);
      f.x1 = Math.max(f.x1, bx);
      f.y1 = Math.max(f.y1, by);
    };
    for (let i = 0; i < n; i++) {
      grow(hx[i] - 20, hy[i] - 20, hx[i] + 20, hy[i] + 20);
      grow(hubRects[i].x0, hubRects[i].y0, hubRects[i].x1, hubRects[i].y1);
    }
    pos.forEach((p) => grow(p.x - 14, p.y - 14, p.x + 14, p.y + 14));
    return f;
  };
  const f0 = frame();
  const sx0 = PAD - f0.x0, sy0 = PAD - f0.y0;
  const cx = sx0, cy = sy0; // центр кольца после сдвига

  // ── дуги цепочки главных нодов: по кольцу, без last→0 ──
  const chain: { id: string; ai: number; bi: number; suggested: boolean }[] = [];
  const linkSamples: Pt[] = [];
  const linkAll: Pt[] = [];
  for (let ai = 0; ai + 1 < n; ai++) {
    const bi = ai + 1;
    chain.push({ id: `${domain.hubs[ai].id}>${domain.hubs[bi].id}`, ai, bi, suggested: !!(domain.hubs[ai].suggested || domain.hubs[bi].suggested) });
    const fa = arr.phi[ai], fb = arr.phi[bi];
    for (let k = 1; k < 24; k++) {
      const f = fa + ((fb - fa) * k) / 24;
      const q = { x: R * Math.sin(f) + sx0, y: -R * Math.cos(f) + sy0 };
      linkAll.push(q);
      linkSamples.push(q);
    }
  }

  // ── сборка ──
  const hubs: PlacedHub[] = [];
  const topics: PlacedTopic[] = [];
  const placedBy = new Map<string, PlacedTopic>();
  const sh = (p: Pt): Pt => ({ x: p.x + sx0, y: p.y + sy0 });
  domain.hubs.forEach((hub, hi) => {
    const mine = lists[hi];
    const solidish = mine.filter(({ t }) => t.state === "solid" || t.state === "review").length;
    let r = 60;
    mine.forEach(({ t }) => {
      const p = pos.get(t.id)!;
      r = Math.max(r, Math.sqrt((p.x - hx[hi]) ** 2 + (p.y - hy[hi]) ** 2));
    });
    const h = sh({ x: hx[hi], y: hy[hi] });
    hubs.push({
      id: hub.id,
      index: hi,
      x: h.x,
      y: h.y,
      r,
      count: mine.length,
      labelSide: side[hi],
      labelBelow: below[hi],
      progress: mine.length ? solidish / mine.length : 0,
    });
  });
  const parentsOf = (t: Topic, hi: number) =>
    t.requires.filter((id) => {
      const p = byId.get(id);
      return !!p && id !== t.id && hubOf(p) === hi;
    });
  [...lists.flatMap((l) => l.map((m) => m.t)), ...freeTopics].forEach((t) => {
    const hi = hubOf(t);
    const p = sh(pos.get(t.id)!);
    const ps = hi >= 0 ? parentsOf(t, hi) : [];
    const links = hi < 0 ? [] : ps.length ? ps.map((id) => sh(pos.get(id)!)) : [{ x: hubs[hi].x, y: hubs[hi].y }];
    const placed: PlacedTopic = {
      ...t,
      hubIndex: hi,
      angle: Math.atan2(p.y - cy, p.x - cx),
      retention: t.retention ?? 0.5,
      x: p.x,
      y: p.y,
      links,
    };
    placedBy.set(t.id, placed);
    topics.push(placed);
  });

  // ── тема вне домена: снаружи, рядом со связанной, куда есть место ──
  const labelRects: Rect[] = rects.map((r) => ({ x0: r.x0 + sx0, x1: r.x1 + sx0, y0: r.y0 + sy0, y1: r.y1 + sy0 }));
  const W = f0.x1 + sx0 + PAD;
  const G = 80;
  const grid = new Map<number, { x: number; y: number; pad: number; id?: string }[]>();
  const gkey = (ix: number, iy: number) => ix * 4096 + iy;
  const addObs = (x: number, y: number, pad: number, id?: string) => {
    const k = gkey(Math.floor(x / G), Math.floor(y / G));
    const cell = grid.get(k);
    if (cell) cell.push({ x, y, pad, id });
    else grid.set(k, [{ x, y, pad, id }]);
  };
  topics.forEach((t) => addObs(t.x, t.y, 0, t.id));
  hubs.forEach((h) => addObs(h.x, h.y, 0));
  linkSamples.forEach((q) => addObs(q.x, q.y, 6));
  const outside: PlacedOutside[] = domain.outside.map((o) => {
    const linked = placedBy.get(o.linkedTo);
    if (!linked) return { ...o, angle: Math.PI / 2, x: hubs[0]?.x ?? 0, y: hubs[0]?.y ?? 0 };
    let best = { score: -Infinity, a: linked.angle, rr: 58 };
    [58, 96, 140, 190, 240].forEach((rr, ri) =>
      [0, 0.5, -0.5, 1, -1, 1.5, -1.5, 2, -2, 2.5, -2.5, Math.PI].forEach((da, ci0) => {
        const ci = ci0 + ri * 3;
        const a = linked.angle + da;
        const ca = Math.cos(a), sa = Math.sin(a);
        const right = ca >= -0.2;
        const ox = linked.x + ca * rr, oy = linked.y + sa * rr;
        let clear = Infinity;
        for (let s = 0; s <= 400; s += 20) {
          const px = ox + (right ? s : -s), py = oy;
          if (px > W + 400 || px < -400) clear = Math.min(clear, -20);
          const near = (qx: number, qy: number, pad: number) => {
            const dx = px - qx, dy = py - qy;
            if (dx >= 80 - pad || dx <= pad - 80 || dy >= 80 - pad || dy <= pad - 80) return;
            clear = Math.min(clear, Math.sqrt(dx * dx + dy * dy) + pad);
          };
          const gx = Math.floor(px / G), gy = Math.floor(py / G);
          for (let ix = gx - 1; ix <= gx + 1; ix++) {
            for (let iy = gy - 1; iy <= gy + 1; iy++) {
              grid.get(gkey(ix, iy))?.forEach((q) => {
                if (q.id === undefined || q.id !== linked.id) near(q.x, q.y, q.pad);
              });
            }
          }
          labelRects.forEach((r) => {
            const ddx = Math.max(r.x0 - px, 0, px - r.x1), ddy = Math.max(r.y0 - 12 - py, 0, py - r.y1 - 12);
            if (ddx < 80 && ddy < 80) clear = Math.min(clear, Math.sqrt(ddx * ddx + ddy * ddy));
          });
        }
        const score = Math.min(clear, 80) - ci * 0.1 - (ca < -0.2 ? 6 : 0);
        if (score > best.score) best = { score, a, rr };
      }),
    );
    return {
      ...o,
      angle: best.a,
      x: linked.x + Math.cos(best.a) * best.rr,
      y: linked.y + Math.sin(best.a) * best.rr,
    };
  });

  // ── рамка по содержимому ──
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const grow = (ax: number, ay: number, bx: number, by: number) => {
    x0 = Math.min(x0, ax); y0 = Math.min(y0, ay); x1 = Math.max(x1, bx); y1 = Math.max(y1, by);
  };
  hubs.forEach((h) => grow(h.x - 20, h.y - 20, h.x + 20, h.y + 20));
  labelRects.forEach((r) => grow(r.x0, r.y0, r.x1, r.y1));
  topics.forEach((t) => grow(t.x - 14, t.y - 14, t.x + 14, t.y + 14));
  outside.forEach((o) => {
    const right = Math.cos(o.angle) >= -0.2;
    grow(right ? o.x - 10 : o.x - 300, o.y - 14, right ? o.x + 300 : o.x + 10, o.y + 14);
  });
  linkAll.forEach((q) => grow(q.x - 4, q.y - 4, q.x + 4, q.y + 4));
  const dx = PAD - x0;
  const dy = PAD - y0;
  const q = (v: number) => Math.round(v * 100) / 100;
  hubs.forEach((h) => { h.x = q(h.x + dx); h.y = q(h.y + dy); h.r = q(h.r); });
  topics.forEach((t) => {
    t.x = q(t.x + dx);
    t.y = q(t.y + dy);
    t.links = t.links.map((l) => ({ x: q(l.x + dx), y: q(l.y + dy) }));
    t.angle = q(t.angle);
  });
  outside.forEach((o) => { o.x = q(o.x + dx); o.y = q(o.y + dy); o.angle = q(o.angle); });
  const ring = { cx: q(cx + dx), cy: q(cy + dy), R: q(R) };

  // пути по округлённым координатам
  const links: HubLink[] = chain.map(({ id, ai, bi, suggested }) => {
    const A = hubs[ai], B = hubs[bi];
    return { id, suggested, d: `M${A.x},${A.y} A${ring.R},${ring.R} 0 0 1 ${B.x},${B.y}` };
  });
  const pointOf = (e: E, end: "a" | "b"): Pt => {
    const id = e[end];
    if (e.kind === "hub" && end === "a") return hubs[hubIndex.get(id)!];
    return placedBy.get(id)!;
  };
  const outEdges: GraphEdge[] = edges.map((e) => {
    const A = pointOf(e, "a"), B = pointOf(e, "b");
    let d = `M${A.x},${A.y} L${B.x},${B.y}`;
    if (e.kind === "cross" || e.kind === "related") {
      const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
      let c: Pt;
      if (e.ha >= 0 && e.hb >= 0 && e.ha !== e.hb) {
        // как в chord-диаграмме: контрольная точка стянута к центру кольца
        c = { x: mx + (ring.cx - mx) * 0.45, y: my + (ring.cy - my) * 0.45 };
      } else {
        // внутри главы и со свободными темами — почти прямая
        const len = Math.sqrt((B.x - A.x) ** 2 + (B.y - A.y) ** 2) || 1;
        const sign = hashStr(e.id) % 2 ? 1 : -1;
        c = { x: mx - ((B.y - A.y) / len) * len * 0.08 * sign, y: my + ((B.x - A.x) / len) * len * 0.08 * sign };
      }
      d = `M${A.x},${A.y} Q${q(c.x)},${q(c.y)} ${B.x},${B.y}`;
    }
    return { ...e, d };
  });

  return {
    hubs,
    topics,
    outside,
    links,
    edges: outEdges,
    ring,
    width: x1 - x0 + PAD * 2,
    height: y1 - y0 + PAD * 2,
  };
}
