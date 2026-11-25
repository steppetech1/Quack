/**
 * Узлы графа над уткой — фирменный мотив платформы. Ближние: цепочка из четырёх слева и из трёх справа от головы,
 * яркие, в цветах «ночного пруда» (фиолетовый, мятный, небесный — холоднее янтаря сцены). Дальние: 5–7 узлов
 * без связей в свободной полосе над уткой, чем дальше — тем меньше и тусклее.
 *
 * Узел — облако случайных мелких точек, к центру очень плотное: издали читается цельной точкой.
 * Связи — плавные векторные кривые; середина каждой — точка на пружине, курсор её отталкивает, и связь
 * мягко прогибается, как резинка; сами узлы курсор не трогает. Узлы на пружинах: голова утки их отодвигает, клевок отбрасывает,
 * встряхивание головой заставляет вздрогнуть. Всё возвращается на место с лёгким покачиванием.
 */

import { BUCKETS, GlyphSprites, type Ramp } from "./glyphs";

export type RGB = [number, number, number];
// «ночной пруд», сильно приглушённый: оттенки те же, насыщенность и яркость ниже, ближе к тёплому графиту сцены
const VIOLET: RGB = [146, 134, 184]; // #9286b8
const MINT: RGB = [126, 168, 148]; // #7ea894
const SKY: RGB = [122, 150, 180]; // #7a96b4
export const PALETTE = [VIOLET, MINT, SKY];

export const ramp = (c: RGB): Ramp => [
  [0, [Math.round(c[0] * 0.25), Math.round(c[1] * 0.25), Math.round(c[2] * 0.28)]],
  [0.55, [Math.round(c[0] * 0.7), Math.round(c[1] * 0.7), Math.round(c[2] * 0.72)]],
  [0.8, c],
  [1, [Math.round(c[0] + (255 - c[0]) * 0.55), Math.round(c[1] + (255 - c[1]) * 0.55), Math.round(c[2] + (255 - c[2]) * 0.55)]],
];
export const css = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

type Node = {
  // дом — смещение от якоря (над головой утки) в долях ширины утки
  hx: number;
  hy: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number; // радиус облака точек в долях ширины утки
  color: number;
  alpha: number;
  near: boolean;
  phase: number;
  dots: { dx: number; dy: number; b: number; h: number; a: number }[];
  scatter: { x: number; y: number };
};
type Link = { a: number; b: number; mx: number; my: number; vx: number; vy: number };

const rnd = (a: number, b: number) => a + Math.random() * (b - a);

/** Раскладка: `near` — [смещение x, смещение y, радиус, цвет] в долях ширины утки; `links` — пары индексов из `near`; `far` — дальние узлы без связей */
export type NodeLayout = { near: [number, number, number, number][]; links: [number, number][]; far?: [number, number][] };

const HERO_NEAR: NodeLayout["near"] = [
  [-0.38, 0.06, 0.046, 0],
  [-0.6, -0.14, 0.042, 1],
  [-0.42, -0.38, 0.043, 2],
  [-0.66, -0.56, 0.039, 0],
  [0.38, 0.0, 0.045, 2],
  [0.6, -0.24, 0.041, 0],
  [0.44, -0.48, 0.039, 1],
];
const HERO_LINKS: NodeLayout["links"] = [
  [0, 1],
  [1, 2],
  [2, 3],
  [4, 5],
  [5, 6],
];
const HERO_FAR: [number, number][] = [
  [-0.9, -0.72],
  [-0.14, -0.82],
  [0.3, -0.7],
  [0.92, -0.74],
  [-0.95, -0.2],
  [0.98, -0.08],
];

export class NodeField {
  private nodes: Node[] = [];
  private links: Link[] = [];
  private sprites: GlyphSprites[] = [];
  private duck = 200;
  private ready = false;
  private ax = 0;
  private ay = 0;
  private prevHead = { x: 0, y: 0 };
  private shakeUntil = 0;
  private sweepUntil = 0;

  /** Без аргумента — раскладка лендинга вокруг утки; с `layout` — своя (например, цепочки на странице нового предмета) */
  constructor(layout?: NodeLayout) {
    // ближние: четыре слева цепочкой, три справа; смещения от якоря — в ширинах утки
    const near = layout?.near ?? HERO_NEAR;
    for (const [hx, hy, r, color] of near) this.nodes.push(this.make(hx, hy, r, color, 1, true));
    this.links = (layout?.links ?? HERO_LINKS).map(([a, b]) => ({ a, b, mx: 0, my: 0, vx: 0, vy: 0 }));
    // дальние: 6 узлов в свободной полосе выше и по сторонам; чем дальше от утки — тем меньше и тусклее
    (layout?.far ?? HERO_FAR).forEach(([hx, hy], i) => {
      const d = Math.min(1, Math.hypot(hx, hy * 1.3) / 1.4);
      this.nodes.push(this.make(hx + rnd(-0.04, 0.04), hy + rnd(-0.04, 0.04), 0.032 - 0.013 * d, i % 3, 0.5 - 0.28 * d, false));
    });
  }

  private make(hx: number, hy: number, r: number, color: number, alpha: number, near: boolean): Node {
    return { hx, hy, x: 0, y: 0, vx: 0, vy: 0, r, color, alpha, near, phase: rnd(0, Math.PI * 2), dots: [], scatter: { x: rnd(-1, 1), y: rnd(-1, 0.2) } };
  }

  /** Пересобрать под размер сцены: `cell` — шаг частиц сцены в CSS px; `maxDots` — потолок точек в облаке (крупным узлам нужно больше) */
  layout(duckPx: number, cell: number, dpr: number, maxDots = 140) {
    this.duck = duckPx;
    // узел — облако случайных точек, как «Quack!»: частицы того же мелкого размера, к центру очень плотно,
    // поэтому издали он читается цельной точкой, а к краю редеет и тает
    const step = cell * 0.45;
    this.sprites = PALETTE.map((c) => new GlyphSprites(step * dpr, ramp(c), true));
    for (const n of this.nodes) {
      n.dots = [];
      const R = n.r * duckPx;
      const count = Math.round(Math.max(18, Math.min(maxDots, ((R * R) / (step * step)) * 2.2)));
      for (let i = 0; i < count; i++) {
        const t = Math.pow(Math.random(), 2.2); // сильный сдвиг к центру
        const ang = Math.random() * Math.PI * 2;
        const d = t;
        n.dots.push({
          dx: Math.cos(ang) * d * R,
          dy: Math.sin(ang) * d * R,
          b: Math.min(BUCKETS - 1, Math.round((0.92 - 0.4 * d) * (BUCKETS - 1))),
          h: Math.random(),
          a: 1 - 0.75 * d,
        });
      }
    }
    this.ready = false;
  }

  /** Утка трясёт головой: ближние узлы слегка вздрагивают и отходят, потом возвращаются */
  shake(now: number, dur: number) {
    this.shakeUntil = now + dur;
  }
  /** Утка поводит головой: на это время голова «задевает» узлы на большем радиусе */
  sweep(now: number, dur: number) {
    this.sweepUntil = now + dur;
  }
  /** Клевок из точки клюва: узлы рядом отлетают */
  peck(bx: number, by: number) {
    const R = this.duck * 0.55;
    for (const n of this.nodes) {
      const dx = n.x - bx;
      const dy = n.y - by;
      const d = Math.hypot(dx, dy) || 1;
      if (d > R) continue;
      const k = (1 - d / R) * this.duck * 3.2;
      n.vx += (dx / d) * k;
      n.vy += (dy / d) * k - this.duck * 0.3;
    }
  }
  /** Ближний узел на стороне `side` (для выбора, куда клюнуть) */
  target(side: 1 | -1) {
    let best: Node | null = null;
    for (const n of this.nodes) if (n.near && Math.sign(n.hx) === side && (!best || Math.abs(n.hy) < Math.abs(best.hy))) best = n;
    return best ? { x: best.x, y: best.y } : null;
  }

  /**
   * Шаг физики. Координаты — CSS px сцены. `anchor` — над головой утки, `head` — центр головы,
   * `ptr` — курсор в координатах сцены (или null).
   */
  step(now: number, dt: number, anchor: { x: number; y: number }, head: { x: number; y: number }, ptr: { x: number; y: number } | null) {
    const D = this.duck;
    // якорь плавно следует за уткой: узлы не скачут вместе с каждым движением головы
    if (!this.ready) {
      this.ax = anchor.x;
      this.ay = anchor.y;
      this.prevHead = { ...head };
    } else {
      this.ax += (anchor.x - this.ax) * (1 - Math.exp(-0.8 * dt));
      this.ay += (anchor.y - this.ay) * (1 - Math.exp(-0.8 * dt));
    }
    const hvx = dt > 0 ? (head.x - this.prevHead.x) / dt : 0;
    const hvy = dt > 0 ? (head.y - this.prevHead.y) / dt : 0;
    this.prevHead = { ...head };
    const shaking = now < this.shakeUntil;
    const headR = D * (now < this.sweepUntil ? 0.42 : 0.17);

    for (const n of this.nodes) {
      // дом: покачивание на месте, как будто узел парит
      const bob = n.near ? 0.03 : 0.02;
      const tx = this.ax + n.hx * D + Math.sin(now * 0.6 + n.phase) * D * bob;
      const ty = this.ay + n.hy * D + Math.cos(now * 0.5 + n.phase * 1.3) * D * bob;
      if (!this.ready) {
        n.x = tx;
        n.y = ty;
      }
      if (dt <= 0) continue;
      // пружина к дому: мягкая, с лёгким недодемпфированием — возвращается с покачиванием
      const K = n.near ? 14 : 9;
      const C = 2 * Math.sqrt(K) * 0.55;
      let fx = (tx - n.x) * K - n.vx * C;
      let fy = (ty - n.y) * K - n.vy * C;
      // курсор узлы не трогает: на них можно спокойно навестись, прогибаются только связи
      // голова задевает и отодвигает; быстрее движется — сильнее толкает
      {
        const dx = n.x - head.x;
        const dy = n.y - head.y;
        const d = Math.hypot(dx, dy) || 1;
        const R = headR + n.r * D;
        if (d < R) {
          const k = (1 - d / R) * (D * 30 + Math.hypot(hvx, hvy) * 6);
          fx += (dx / d) * k;
          fy += (dy / d) * k;
        }
        // встряхивание: ближние узлы слегка отталкиваются от головы и подрагивают, без кружения
        if (shaking && d < D * 0.9) {
          const w = (1 - d / (D * 0.9)) * D * 5;
          fx += (dx / d) * w + Math.sin(now * 31 + n.phase * 5) * w * 0.6;
          fy += (dy / d) * w + Math.cos(now * 27 + n.phase * 3) * w * 0.6;
        }
      }
      n.vx += fx * dt;
      n.vy += fy * dt;
      n.x += n.vx * dt;
      n.y += n.vy * dt;
    }

    // середины связей — тоже на пружинах: тянутся к середине отрезка, курсор их отталкивает
    for (const l of this.links) {
      const a = this.nodes[l.a];
      const b = this.nodes[l.b];
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      // лёгкий провис, как у нити
      const sagX = -(b.y - a.y) * 0.12;
      const sagY = (b.x - a.x) * 0.12 + Math.hypot(b.x - a.x, b.y - a.y) * 0.08;
      const tx = cx + sagX * 0.3;
      const ty = cy + sagY * 0.3;
      if (!this.ready) {
        l.mx = tx;
        l.my = ty;
      }
      if (dt <= 0) continue;
      const K = 14;
      const C = 2 * Math.sqrt(K) * 0.7;
      let fx = (tx - l.mx) * K - l.vx * C;
      let fy = (ty - l.my) * K - l.vy * C;
      if (ptr) {
        const dx = l.mx - ptr.x;
        const dy = l.my - ptr.y;
        const d = Math.hypot(dx, dy) || 1;
        const R = D * 0.28;
        if (d < R) {
          const k = Math.pow(1 - d / R, 2) * D * 14;
          fx += (dx / d) * k;
          fy += (dy / d) * k;
        }
      }
      l.vx += fx * dt;
      l.vy += fy * dt;
      l.mx += l.vx * dt;
      l.my += l.vy * dt;
    }
    this.ready = true;
  }

  /**
   * Рисует в `ctx` (device px). `ox`, `oy` — сдвиг холста относительно сцены в CSS px;
   * `away` — первый экран уезжает: узлы рассыпаются вместе с уткой.
   */
  draw(ctx: CanvasRenderingContext2D, dpr: number, ox: number, oy: number, now: number, away: number, reduced: boolean) {
    if (!this.ready || away >= 1) return;
    const D = this.duck;
    const fade = 1 - away;
    const spread = away * away;
    const X = (x: number) => (x - ox) * dpr;
    const Y = (y: number) => (y - oy) * dpr;
    const pos = (n: Node) => ({ x: n.x + n.scatter.x * spread * D * 1.4, y: n.y + n.scatter.y * spread * D * 1.4 });

    ctx.lineCap = "round";
    // связи: мягкий широкий ореол + тонкая линия, цвет переходит от узла к узлу
    for (const l of this.links) {
      const a = this.nodes[l.a];
      const b = this.nodes[l.b];
      const pa = pos(a);
      const pb = pos(b);
      // кривая проходит через середину-пружину: контрольная точка квадратичной Безье
      const qx = 2 * l.mx - (pa.x + pb.x) / 2;
      const qy = 2 * l.my - (pa.y + pb.y) / 2;
      const g = ctx.createLinearGradient(X(pa.x), Y(pa.y), X(pb.x), Y(pb.y));
      g.addColorStop(0, css(PALETTE[a.color], 1));
      g.addColorStop(1, css(PALETTE[b.color], 1));
      ctx.strokeStyle = g;
      for (const [w, al] of [
        [4, 0.05],
        [1, 0.32],
      ]) {
        ctx.globalAlpha = al * fade * (1 - spread);
        ctx.lineWidth = w * dpr;
        ctx.beginPath();
        ctx.moveTo(X(pa.x), Y(pa.y));
        ctx.quadraticCurveTo(X(qx), Y(qy), X(pb.x), Y(pb.y));
        ctx.stroke();
      }
    }

    for (const n of this.nodes) {
      const p = pos(n);
      const c = PALETTE[n.color];
      const R = n.r * D;
      const x = X(p.x);
      const y = Y(p.y);
      const al = n.alpha * fade;
      // тихое свечение своим цветом
      const glow = ctx.createRadialGradient(x, y, 0, x, y, R * 2.2 * dpr);
      glow.addColorStop(0, css(c, 0.14 * al));
      glow.addColorStop(1, css(c, 0));
      ctx.globalAlpha = 1;
      ctx.fillStyle = glow;
      ctx.fillRect(x - R * 2.2 * dpr, y - R * 2.2 * dpr, R * 4.4 * dpr, R * 4.4 * dpr);
      // облако точек, мерцают как остальные частицы
      const spr = this.sprites[n.color];
      if (!spr) continue;
      for (const d of n.dots) {
        const fl = reduced ? 1 : 0.82 + 0.18 * Math.sin(now * 1.3 + d.h * 40);
        ctx.globalAlpha = al * fl * d.a;
        spr.draw(ctx, "dot", d.b, x + d.dx * dpr + d.dx * spread * 3 * dpr, y + d.dy * dpr + d.dy * spread * 3 * dpr);
      }
    }
    ctx.globalAlpha = 1;
  }
}
