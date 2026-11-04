import { BUCKETS, GlyphSprites, type Ramp } from "../hero-duck/glyphs";
import { PALETTE, css, ramp } from "../hero-duck/nodes";
import { clamp01, rand } from "./scene-utils";

/** Общие кирпичики сцен: узел-облако точек, провисающая цепочка, молния из частиц */

/* ------------------------------------------------------------------ узлы */

export type Dot = { dx: number; dy: number; b: number; h: number; a: number };

/** Спрайты облаков узлов для всех цветов палитры «ночной пруд» */
export function nodeSprites(step: number, dpr: number): GlyphSprites[] {
  return PALETTE.map((c) => new GlyphSprites(step * dpr, ramp(c), true));
}

/** Облако случайных мелких точек радиуса R: к центру очень плотно (читается цельной точкой), к краю редеет */
export function makeDots(R: number, step: number): Dot[] {
  const count = Math.round(Math.max(24, Math.min(170, ((R * R) / (step * step)) * 2.4)));
  const dots: Dot[] = [];
  for (let i = 0; i < count; i++) {
    const d = Math.pow(Math.random(), 2.2);
    const ang = Math.random() * Math.PI * 2;
    dots.push({
      dx: Math.cos(ang) * d * R,
      dy: Math.sin(ang) * d * R,
      b: Math.min(BUCKETS - 1, Math.round((0.92 - 0.4 * d) * (BUCKETS - 1))),
      h: Math.random(),
      a: 1 - 0.75 * d,
    });
  }
  return dots;
}

// мягкое свечение узла — один готовый спрайт на цвет (радиальный градиент не создаётся заново на каждый узел каждого кадра)
const GLOW: (HTMLCanvasElement | undefined)[] = [];
function glowSprite(color: number): HTMLCanvasElement {
  let cv = GLOW[color];
  if (!cv) {
    cv = document.createElement("canvas");
    cv.width = cv.height = 64;
    const g = cv.getContext("2d");
    if (g) {
      const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      grad.addColorStop(0, css(PALETTE[color], 1));
      grad.addColorStop(1, css(PALETTE[color], 0));
      g.fillStyle = grad;
      g.fillRect(0, 0, 64, 64);
    }
    GLOW[color] = cv;
  }
  return cv;
}

export type NodeDraw = {
  x: number;
  y: number;
  R: number;
  color: number;
  /** общая яркость узла */
  alpha: number;
  /** 0..1: наведён — ярче и крупнее */
  hot: number;
  /** сборка/распад: 1 — собран, 0 — рассыпан; scatter — куда уходят точки при распаде */
  form: number;
  scatter: { x: number; y: number };
};

/** Рисует узел: тихое свечение своим цветом и облако точек. Координаты — CSS px сцены, холст масштабирован `dpr` */
export function drawNode(ctx: CanvasRenderingContext2D, sprites: GlyphSprites[], dots: Dot[], n: NodeDraw, now: number, dpr: number, reduced: boolean, scatterDist: number) {
  const c = PALETTE[n.color];
  const spread = 1 - n.form;
  const cx = (n.x + n.scatter.x * spread * scatterDist) * dpr;
  const cy = (n.y + n.scatter.y * spread * scatterDist) * dpr;
  const al = n.alpha * clamp01(n.form * 1.6);
  if (al <= 0.01) return;
  const k = 1 + 0.35 * n.hot;
  const glowR = n.R * (2.2 + 1.1 * n.hot) * dpr;
  ctx.globalAlpha = (0.14 + 0.2 * n.hot) * al;
  ctx.drawImage(glowSprite(n.color), cx - glowR, cy - glowR, glowR * 2, glowR * 2);
  const spr = sprites[n.color];
  for (const d of dots) {
    const fl = reduced ? 1 : 0.82 + 0.18 * Math.sin(now * 1.3 + d.h * 40);
    ctx.globalAlpha = Math.min(1, al * fl * d.a * (1 + 0.5 * n.hot));
    const s = (1 + spread * 3) * k;
    spr.draw(ctx, "dot", Math.min(BUCKETS - 1, d.b + (n.hot > 0.5 ? 1 : 0)), cx + d.dx * s * dpr, cy + d.dy * s * dpr);
  }
  ctx.globalAlpha = 1;
}

/** Тонкая плавная связь между двумя точками; `sag` — провисание контрольной точки (px) */
export function drawLink(ctx: CanvasRenderingContext2D, dpr: number, ax: number, ay: number, bx: number, by: number, qx: number, qy: number, ca: number, cb: number, alpha: number) {
  if (alpha <= 0.005) return;
  const g = ctx.createLinearGradient(ax * dpr, ay * dpr, bx * dpr, by * dpr);
  g.addColorStop(0, css(PALETTE[ca], 1));
  g.addColorStop(1, css(PALETTE[cb], 1));
  ctx.strokeStyle = g;
  ctx.lineCap = "round";
  for (const [w, al] of [
    [4, 0.05],
    [1, 0.34],
  ] as const) {
    ctx.globalAlpha = al * alpha;
    ctx.lineWidth = w * dpr;
    ctx.beginPath();
    ctx.moveTo(ax * dpr, ay * dpr);
    ctx.quadraticCurveTo(qx * dpr, qy * dpr, bx * dpr, by * dpr);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

/* ---------------------------------------------------------------- цепочка */

type P = { x: number; y: number; px: number; py: number };

/** Цепочка на верёвочной физике (Верле): нулевая точка — якорь (клюв), остальные тянутся за ним и провисают */
export class Chain {
  pts: P[] = [];
  constructor(
    n: number,
    private rest: number,
  ) {
    for (let i = 0; i <= n; i++) this.pts.push({ x: 0, y: 0, px: 0, py: 0 });
  }
  setRest(r: number) {
    this.rest = r;
  }
  reset(ax: number, ay: number) {
    this.pts.forEach((p, i) => {
      p.x = p.px = ax + i * this.rest * 0.8;
      p.y = p.py = ay + i * this.rest * 0.6;
    });
  }
  /** gx, gy — ускорение (px/с²): тяжесть вниз и «ветер» от полёта назад */
  step(dt: number, ax: number, ay: number, gx: number, gy: number, drag = 0.985) {
    const sub = dt > 0.02 ? 2 : 1;
    const h = dt / sub;
    for (let s = 0; s < sub; s++) {
      const a = this.pts[0];
      a.x = a.px = ax;
      a.y = a.py = ay;
      for (let i = 1; i < this.pts.length; i++) {
        const p = this.pts[i];
        const vx = (p.x - p.px) * drag;
        const vy = (p.y - p.py) * drag;
        p.px = p.x;
        p.py = p.y;
        p.x += vx + gx * h * h;
        p.y += vy + gy * h * h;
      }
      for (let it = 0; it < 6; it++) {
        for (let i = 1; i < this.pts.length; i++) {
          const a0 = this.pts[i - 1];
          const b0 = this.pts[i];
          const dx = b0.x - a0.x;
          const dy = b0.y - a0.y;
          const d = Math.hypot(dx, dy) || 1;
          const diff = (d - this.rest) / d;
          if (i === 1) {
            b0.x -= dx * diff;
            b0.y -= dy * diff;
          } else {
            a0.x += dx * diff * 0.5;
            a0.y += dy * diff * 0.5;
            b0.x -= dx * diff * 0.5;
            b0.y -= dy * diff * 0.5;
          }
        }
      }
    }
  }
}

/* ----------------------------------------------------------------- молния */

const ICE: Ramp = [
  [0, [70, 88, 120]],
  [0.5, [150, 180, 226]],
  [0.85, [214, 232, 252]],
  [1, [248, 252, 255]],
];

type Bolt = { pts: { x: number; y: number; w: number; brk?: boolean }[]; t0: number; life: number };

/** Молния сверху вниз из частиц сцены: изредка бьёт рядом с уткой, короткая вспышка и затухание */
export class Lightning {
  private bolts: Bolt[] = [];
  private next = 2.5;
  private sprites: GlyphSprites | null = null;
  private cell = 5;
  flash = 0;

  layout(cell: number, dpr: number) {
    this.cell = cell;
    this.sprites = new GlyphSprites(cell * 0.85 * dpr, ICE, true);
  }

  /** Ломаная от (x0, y0) к (x1, y1) серединным смещением; ветка отходит в сторону */
  private path(x0: number, y0: number, x1: number, y1: number, amp: number): { x: number; y: number }[] {
    let pts = [
      { x: x0, y: y0 },
      { x: x1, y: y1 },
    ];
    for (let lvl = 0; lvl < 6; lvl++) {
      const out: { x: number; y: number }[] = [];
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i];
        const b = pts[i + 1];
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        const nx = -(b.y - a.y);
        const ny = b.x - a.x;
        const len = Math.hypot(nx, ny) || 1;
        const o = rand(-1, 1) * amp * Math.pow(0.55, lvl);
        out.push(a, { x: mx + (nx / len) * o, y: my + (ny / len) * o });
      }
      out.push(pts[pts.length - 1]);
      pts = out;
    }
    return pts;
  }

  strike(now: number, x: number, top: number, bottom: number) {
    const main = this.path(x, top, x + rand(-60, 60), bottom, (bottom - top) * 0.22);
    const pts: Bolt["pts"] = main.map((p) => ({ ...p, w: 1 }));
    // одна-две короткие ветки
    for (let b = 0; b < 2; b++) {
      const i = Math.floor(main.length * rand(0.3, 0.7));
      const o = main[i];
      const side = Math.random() < 0.5 ? -1 : 1;
      const br = this.path(o.x, o.y, o.x + side * rand(40, 120), o.y + rand(60, 160), 40);
      br.forEach((p, k) => pts.push({ ...p, w: 0.6, brk: k === 0 })); // brk: новая ломаная, не соединять с предыдущей точкой
    }
    this.bolts.push({ pts, t0: now, life: 0.7 });
  }

  update(now: number, spawn: () => { x: number; top: number; bottom: number } | null) {
    if (now > this.next) {
      const s = spawn();
      if (s) this.strike(now, s.x, s.top, s.bottom);
      this.next = now + rand(4, 8);
    }
    this.bolts = this.bolts.filter((b) => now - b.t0 < b.life);
  }

  draw(ctx: CanvasRenderingContext2D, now: number, dpr: number, form: number, W: number, H: number) {
    this.flash = 0;
    if (!this.sprites) return;
    for (const b of this.bolts) {
      const age = (now - b.t0) / b.life;
      // два вспыха подряд и затухание, как у настоящей молнии
      const k = age < 0.12 ? 1 : age < 0.22 ? 0.25 : age < 0.34 ? 0.9 : Math.max(0, 0.6 * (1 - (age - 0.34) / 0.66));
      this.flash = Math.max(this.flash, k);
      const spacing = this.cell * 0.8;
      for (let i = 0; i < b.pts.length - 1; i++) {
        const a = b.pts[i];
        const c = b.pts[i + 1];
        const seg = Math.hypot(c.x - a.x, c.y - a.y);
        if (c.brk) continue; // начало ветки не соединяется с концом предыдущей линии
        for (let d = 0; d < seg; d += spacing) {
          const t = d / seg;
          const x = a.x + (c.x - a.x) * t + rand(-0.8, 0.8);
          const y = a.y + (c.y - a.y) * t + rand(-0.8, 0.8);
          ctx.globalAlpha = Math.min(1, k * a.w * form * (0.7 + 0.3 * Math.random()));
          this.sprites.draw(ctx, Math.random() < 0.18 ? "star" : "dot", Math.min(BUCKETS - 1, 6 + Math.round(3 * k * a.w)), x * dpr, y * dpr);
        }
      }
    }
    ctx.globalAlpha = 1;
    if (this.flash > 0.01) {
      ctx.fillStyle = `rgba(150,185,235,${0.045 * this.flash * form})`;
      ctx.fillRect(0, 0, W * dpr, H * dpr);
    }
  }
}
