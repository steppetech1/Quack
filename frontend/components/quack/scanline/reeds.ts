// Камыши, рогоз и кувшинки для меню. Рисуем ЯРКОСТЬЮ: фон чёрный (его заливает вызывающий),
// растения — оттенки серого с объёмом. Движок штриховки превращает яркость в полосы,
// цвет добавляет он сам.
//
// Геометрия считается один раз на (seed, ширина, высота) и кэшируется; в кадре
// только пересчёт качания и заливки (без аллокаций, кроме путей канваса).
// Всё строится для левого края; правый — зеркалом через transform.

export type ReedsOptions = { side: "left" | "right"; seed: number };

const TAU = Math.PI * 2;
const NB = 24; // узлов в таблице изгиба стебля
const M_STEM = 16; // сегментов в стебле при отрисовке
const M_LEAF = 9; // сегментов в листе
const M_TOP = 8; // сегментов в початке / метёлке

// ---------- мелочи ----------

// Маленький детерминированный ГПСЧ (mulberry32)
function mulberry32(seed: number): () => number {
  let a = (seed | 0) ^ 0x5bd1e995;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const easeOut = (x: number) => 1 - (1 - x) * (1 - x) * (1 - x);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// Таблица готовых строк серого — чтобы не склеивать строки каждый кадр
const GRAY: string[] = [];
for (let i = 0; i < 256; i++) GRAY.push(`rgb(${i},${i},${i})`);
const gray = (v: number) => GRAY[v < 0 ? 0 : v > 255 ? 255 : v | 0];

// ---------- типы геометрии ----------

type Leaf = {
  ta: number; // где на стебле (0..1)
  side: number; // +1 внутрь кадра, -1 наружу
  ang: number; // начальный разворот от стебля
  bend: number; // изгиб к кончику (провисание)
  len: number;
  wid: number;
  ph: number;
};

type Stalk = {
  kind: 0 | 1 | 2; // 0 рогоз, 1 камыш с метёлкой, 2 кувшинка
  depth: 0 | 1; // 0 дальний (темнее), 1 ближний
  len: number;
  w0: number;
  w1: number;
  delay: number;
  swayAmp: number;
  swayW: number;
  swayPh: number;
  bx: Float32Array; // база изгиба (NB+1 точек)
  by: Float32Array;
  leaves: Leaf[];
  topT: number; // где начинается початок / метёлка (по t)
  topLen: number; // длина початка, px
  topW: number; // толщина початка
  droop: number; // наклон метёлки
  petalLen: number; // длина лепестка кувшинки
  wave: number; // текущее качание (пересчитывается в кадре)
};

type Grass = {
  x: number;
  y: number;
  len: number;
  wid: number;
  ang: number;
  bend: number;
  side: number;
  delay: number;
  ph: number;
};

type Pad = {
  x: number;
  y: number;
  rx: number;
  ry: number;
  rot: number;
  a0: number; // направление выреза
  nw: number; // полуширина выреза, рад
  delay: number;
  ph: number;
};

type Geometry = {
  u: number; // масштаб толщин
  stalks: Stalk[]; // рогоз и камыш, сначала дальние
  grass: Grass[];
  lilies: Stalk[];
  pads: Pad[];
};

// ---------- построение геометрии ----------

function makeStalk(
  rnd: () => number,
  kind: 0 | 1 | 2,
  depth: 0 | 1,
  ex: number,
  len: number,
  lean: number,
  w0: number,
  w1: number,
  delay: number,
  H: number,
  u: number,
): Stalk {
  const bx = new Float32Array(NB + 1);
  const by = new Float32Array(NB + 1);
  const wob = (rnd() - 0.5) * 2;
  const wph = rnd() * TAU;
  const y0 = H + 6 * u; // корень чуть ниже кадра
  for (let k = 0; k <= NB; k++) {
    const t = k / NB;
    bx[k] = ex + lean * len * t * t + wob * len * 0.014 * Math.sin(t * 3.2 + wph) * t;
    by[k] = y0 - len * t;
  }
  return {
    kind,
    depth,
    len,
    w0,
    w1,
    delay,
    swayAmp: len * (0.022 + 0.016 * rnd()),
    swayW: 0.7 + 0.55 * rnd(),
    swayPh: rnd() * TAU,
    bx,
    by,
    leaves: [],
    topT: 0.8,
    topLen: 0,
    topW: 0,
    droop: 0,
    petalLen: 0,
    wave: 0,
  };
}

function build(seed: number, W: number, H: number): Geometry {
  const rnd = mulberry32(seed);
  for (let i = 0; i < 4; i++) rnd();
  const u = Math.min(1.5, Math.max(0.45, H / 900));
  const cw = W * 0.3; // ширина кластера
  const stalks: Stalk[] = [];

  // Рогоз и камыш
  const N = 13;
  for (let i = 0; i < N; i++) {
    let ex: number;
    let edge: number;
    let hf: number;
    if (i < 3) {
      // три самых высоких — гарантированно у края
      ex = W * (0.07 + 0.055 * i) + (rnd() - 0.5) * W * 0.015;
      edge = ex / cw;
      hf = [0.88, 0.8, 0.74][i];
    } else {
      edge = Math.pow(rnd(), 1.4);
      ex = W * 0.025 + cw * 0.9 * edge;
      hf = (0.9 - 0.45 * Math.pow(edge, 0.8)) * (0.82 + 0.18 * rnd());
      if (hf < 0.3) hf = 0.3 + 0.1 * rnd();
    }
    const kind: 0 | 1 = i === 0 ? 0 : i === 1 ? 1 : rnd() < 0.5 ? 0 : 1;
    const depth: 0 | 1 = i < 3 ? 1 : rnd() < 0.45 ? 0 : 1;
    const len = hf * H + 6 * u;
    let lean = -0.02 + 0.1 * rnd() * (0.4 + edge);
    // верхушка не должна уходить дальше ~34% ширины
    const maxLean = (W * 0.34 - ex) / len;
    if (lean > maxLean) lean = Math.max(-0.03, maxLean);
    const cat = kind === 0;
    const w0 = (cat ? 8 + 3.5 * rnd() : 6.5 + 3 * rnd()) * u;
    const delay = 0.03 + 0.32 * rnd() + 0.08 * Math.min(1, edge);
    const s = makeStalk(rnd, kind, depth, ex, len, lean, w0, w0 * 0.42, delay, H, u);

    if (cat) {
      // початок: яркий цилиндр под верхушкой, над ним тонкий шип
      s.topLen = Math.min(len * 0.22, (80 + 40 * rnd()) * u);
      s.topW = (20 + 6 * rnd()) * u;
      s.topT = 1 - s.topLen / len - (0.05 + 0.03 * rnd());
      // прикорневые мечевидные листья
      const nb = 2 + ((rnd() * 2) | 0);
      for (let k = 0; k < nb; k++) {
        s.leaves.push({
          ta: 0.02 + 0.1 * rnd(),
          side: rnd() < 0.62 ? 1 : -1,
          ang: 0.08 + 0.22 * rnd(),
          bend: 0.4 + 0.7 * rnd(),
          len: len * (0.38 + 0.3 * rnd()),
          wid: (6 + 3.5 * rnd()) * u,
          ph: rnd() * TAU,
        });
      }
      const ns = 1 + ((rnd() * 2) | 0);
      for (let k = 0; k < ns; k++) {
        s.leaves.push({
          ta: 0.2 + 0.35 * rnd(),
          side: rnd() < 0.6 ? 1 : -1,
          ang: 0.4 + 0.3 * rnd(),
          bend: 0.6 + 0.6 * rnd(),
          len: len * (0.18 + 0.12 * rnd()),
          wid: (5 + 2.5 * rnd()) * u,
          ph: rnd() * TAU,
        });
      }
    } else {
      // камыш: метёлка на верхушке и широкие поникающие листья по стеблю
      s.topLen = Math.min(len * 0.22, (84 + 40 * rnd()) * u);
      s.topW = (20 + 8 * rnd()) * u;
      s.topT = 1 - s.topLen / len;
      s.droop = (rnd() - 0.3) * 0.45;
      const nl = 3 + ((rnd() * 2) | 0);
      for (let k = 0; k < nl; k++) {
        s.leaves.push({
          ta: 0.06 + (0.55 * k) / nl + 0.05 * rnd(),
          side: k % 2 === 0 ? (rnd() < 0.6 ? 1 : -1) : rnd() < 0.5 ? -1 : 1,
          ang: 0.5 + 0.4 * rnd(),
          bend: 0.5 + 0.7 * rnd(),
          len: len * (0.2 + 0.14 * rnd()),
          wid: (8 + 4 * rnd()) * u,
          ph: rnd() * TAU,
        });
      }
    }
    stalks.push(s);
  }
  stalks.sort((a, b) => a.depth - b.depth);

  // Травинки
  const grass: Grass[] = [];
  for (let i = 0; i < 16; i++) {
    const r = rnd();
    grass.push({
      x: W * 0.015 + cw * 1.05 * Math.pow(r, 1.2),
      y: H + 4 * u,
      len: H * (0.1 + 0.28 * rnd()) * (1.1 - 0.4 * r),
      wid: (3.5 + 2.5 * rnd()) * u,
      ang: 0.05 + 0.3 * rnd(),
      bend: 0.3 + 0.9 * rnd(),
      side: rnd() < 0.55 ? 1 : -1,
      delay: 0.4 * rnd(),
      ph: rnd() * TAU,
    });
  }

  // Кувшинки: цветы на коротких стеблях
  const lilies: Stalk[] = [];
  const NL = 3;
  for (let i = 0; i < NL; i++) {
    const ex = W * (0.05 + (0.23 * (i + 0.2 + 0.6 * rnd())) / NL);
    const len = H * (0.11 + 0.13 * rnd()) + 6 * u;
    const s = makeStalk(rnd, 2, 1, ex, len, (rnd() - 0.4) * 0.14, 4.5 * u, 3 * u, 0.28 + 0.22 * rnd(), H, u);
    s.petalLen = (50 + 20 * rnd()) * u;
    lilies.push(s);
  }

  // Листья-«блюдца» у самого низа
  const pads: Pad[] = [];
  for (let i = 0; i < 6; i++) {
    const rx = Math.min(90 * u, cw * (0.12 + 0.1 * rnd()));
    pads.push({
      x: W * (0.03 + 0.27 * ((i + rnd() * 0.9) / 6)),
      y: H * (0.935 + 0.05 * rnd()),
      rx,
      ry: rx * (0.34 + 0.08 * rnd()),
      rot: (rnd() - 0.5) * 0.25,
      a0: Math.PI * (0.15 + 0.7 * rnd()),
      nw: 0.1 + 0.07 * rnd(),
      delay: 0.12 + 0.38 * rnd(),
      ph: rnd() * TAU,
    });
  }
  pads.sort((a, b) => a.y - b.y);

  return { u, stalks, grass, lilies, pads };
}

// ---------- кэши ----------

const geoCache = new Map<string, Geometry>();

function getGeometry(seed: number, W: number, H: number): Geometry {
  const key = `${seed}|${Math.round(W)}|${Math.round(H)}`;
  let g = geoCache.get(key);
  if (!g) {
    if (geoCache.size >= 8) geoCache.delete(geoCache.keys().next().value as string);
    g = build(seed, W, H);
    geoCache.set(key, g);
  }
  return g;
}

// Градиенты по высоте экрана (общие для всех стеблей и листьев) + радиальный для листа кувшинки
type Grads = { w: number; h: number; band: CanvasGradient[]; pad: CanvasGradient };
const gradCache = new WeakMap<CanvasRenderingContext2D, Grads>();

function vGrad(ctx: CanvasRenderingContext2D, H: number, a: number, b: number, c: number): CanvasGradient {
  const g = ctx.createLinearGradient(0, H, 0, 0);
  g.addColorStop(0, gray(a));
  g.addColorStop(0.5, gray(b));
  g.addColorStop(1, gray(c));
  return g;
}

function getGrads(ctx: CanvasRenderingContext2D, W: number, H: number): Grads {
  let g = gradCache.get(ctx);
  if (g && g.w === W && g.h === H) return g;
  // порядок: [глубина*3 + полоса], полосы: тень, тело, блик
  const band = [
    vGrad(ctx, H, 14, 34, 52), // дальний: тень
    vGrad(ctx, H, 36, 92, 128), // тело
    vGrad(ctx, H, 58, 128, 170), // блик
    vGrad(ctx, H, 26, 60, 84), // ближний: тень
    vGrad(ctx, H, 74, 160, 205), // тело
    vGrad(ctx, H, 120, 215, 245), // блик
  ];
  const pad = ctx.createRadialGradient(-0.25, -0.3, 0.02, 0, 0, 1.05);
  pad.addColorStop(0, gray(236));
  pad.addColorStop(0.45, gray(165));
  pad.addColorStop(0.85, gray(92));
  pad.addColorStop(1, gray(56));
  g = { w: W, h: H, band, pad };
  gradCache.set(ctx, g);
  return g;
}

// ---------- примитивы ----------

// Общие буферы для полос (стебель, лист, початок)
const px = new Float32Array(32);
const py = new Float32Array(32);
const wd = new Float32Array(32);
const nx = new Float32Array(32);
const ny = new Float32Array(32);
// Выход pointAt
let ox = 0;
let oy = 0;

// Точка на стебле с качанием: изгиб растёт с высотой
function pointAt(s: Stalk, t: number): void {
  const tt = t < 0 ? 0 : t > 1 ? 1 : t;
  const f = tt * NB;
  let i = f | 0;
  if (i >= NB) i = NB - 1;
  const a = f - i;
  ox = s.bx[i] + (s.bx[i + 1] - s.bx[i]) * a + s.wave * tt * tt;
  oy = s.by[i] + (s.by[i + 1] - s.by[i]) * a;
}

function prepNormals(n: number): void {
  for (let j = 0; j < n; j++) {
    const a = j > 0 ? j - 1 : 0;
    const b = j < n - 1 ? j + 1 : n - 1;
    const tx = px[b] - px[a];
    const ty = py[b] - py[a];
    const l = Math.hypot(tx, ty) || 1;
    nx[j] = -ty / l;
    ny[j] = tx / l;
  }
}

// Полоса между долями ширины s0..s1 (-1..1 поперёк)
function strip(ctx: CanvasRenderingContext2D, n: number, s0: number, s1: number): void {
  ctx.beginPath();
  for (let j = 0; j < n; j++) {
    const h = wd[j] * 0.5 * s0;
    if (j === 0) ctx.moveTo(px[j] + nx[j] * h, py[j] + ny[j] * h);
    else ctx.lineTo(px[j] + nx[j] * h, py[j] + ny[j] * h);
  }
  for (let j = n - 1; j >= 0; j--) {
    const h = wd[j] * 0.5 * s1;
    ctx.lineTo(px[j] + nx[j] * h, py[j] + ny[j] * h);
  }
  ctx.closePath();
}

// Три слоя объёма: тень на всю ширину, тело со светлой стороны, узкий блик
function fillVolume(
  ctx: CanvasRenderingContext2D,
  n: number,
  lit: number, // с какой стороны свет: -1 или +1 (по s)
  sh: string | CanvasGradient,
  bo: string | CanvasGradient,
  hi: string | CanvasGradient,
): void {
  prepNormals(n);
  ctx.fillStyle = sh;
  strip(ctx, n, -1, 1);
  ctx.fill();
  ctx.fillStyle = bo;
  strip(ctx, n, lit, -0.2 * lit);
  ctx.fill();
  ctx.fillStyle = hi;
  strip(ctx, n, 0.8 * lit, 0.2 * lit);
  ctx.fill();
}

// Лист/травинка: изгибается от базы, раскрывается по lp (0..1)
function drawBlade(
  ctx: CanvasRenderingContext2D,
  bx: number,
  by: number,
  th0: number,
  side: number,
  ang: number,
  bend: number,
  len: number,
  wid: number,
  lp: number,
  sway: number,
  g: Grads,
  depth: number,
): void {
  const L = len * lp;
  if (L < 1.5) return;
  const angE = ang * lp;
  const bendE = bend * lp;
  const dl = L / M_LEAF;
  let x = bx;
  let y = by;
  px[0] = x;
  py[0] = y;
  wd[0] = wid * 0.55 * lp;
  for (let j = 1; j <= M_LEAF; j++) {
    const uu = (j - 0.5) / M_LEAF;
    const th = th0 + side * (angE + bendE * uu) + sway * uu;
    x += Math.sin(th) * dl;
    y -= Math.cos(th) * dl;
    px[j] = x;
    py[j] = y;
    const q = j / M_LEAF;
    wd[j] = wid * lp * Math.pow(1 - q, 0.75) * Math.min(1, 0.55 + q * 4);
  }
  const b = depth * 3;
  fillVolume(ctx, M_LEAF + 1, -side, g.band[b], g.band[b + 1], g.band[b + 2]);
}

// ---------- растения ----------

function drawStalk(ctx: CanvasRenderingContext2D, s: Stalk, gAll: number, time: number, g: Grads): void {
  const p = clamp01((gAll - s.delay) / (1 - s.delay));
  if (p <= 0) return;
  const sg = easeOut(clamp01(p / 0.7));
  if (sg < 0.004) return;
  s.wave = s.swayAmp * (Math.sin(time * s.swayW + s.swayPh) + 0.35 * Math.sin(time * s.swayW * 2.17 + s.swayPh * 1.9));
  const b = s.depth * 3;

  // листья — позади стебля, раскрываются после того как стебель их миновал
  for (let k = 0; k < s.leaves.length; k++) {
    const lf = s.leaves[k];
    if (sg <= lf.ta) continue;
    const lp = easeOut(clamp01((sg - lf.ta) / 0.3));
    pointAt(s, lf.ta);
    const x0 = ox;
    const y0 = oy;
    pointAt(s, lf.ta + 0.03);
    const th0 = Math.atan2(ox - x0, y0 - oy);
    const sway = 0.07 * Math.sin(time * s.swayW * 1.3 + lf.ph) * (0.5 + lf.ta);
    drawBlade(ctx, x0, y0, th0, lf.side, lf.ang, lf.bend, lf.len, lf.wid, lp, sway, g, s.depth);
  }

  // стебель
  const n = M_STEM + 1;
  for (let j = 0; j < n; j++) {
    const t = (sg * j) / M_STEM;
    pointAt(s, t);
    px[j] = ox;
    py[j] = oy;
    wd[j] = lerp(s.w0, s.w1, t);
  }
  fillVolume(ctx, n, -1, g.band[b], g.band[b + 1], g.band[b + 2]);

  // верхушка появляется последней
  if (s.kind === 2) return;
  const hp = easeOut(clamp01((p - 0.62) / 0.38));
  if (hp <= 0) return;
  if (s.kind === 0) drawCattail(ctx, s, hp);
  else drawPlume(ctx, s, hp);
}

// Початок рогоза: яркий цилиндр с тенью и бликом
function drawCattail(ctx: CanvasRenderingContext2D, s: Stalk, hp: number): void {
  const dt = (s.topLen / s.len) * hp;
  const n = M_TOP + 1;
  const e = 0.16;
  for (let j = 0; j < n; j++) {
    const uu = j / M_TOP;
    pointAt(s, s.topT + dt * uu);
    px[j] = ox;
    py[j] = oy;
    let prof = 1;
    if (uu < e) prof = Math.sqrt(1 - ((e - uu) / e) ** 2);
    else if (uu > 1 - e) prof = Math.sqrt(1 - ((uu - (1 - e)) / e) ** 2);
    wd[j] = s.topW * (0.3 + 0.7 * hp) * (0.15 + 0.85 * prof) * (0.94 + 0.06 * Math.sin(uu * Math.PI));
  }
  fillVolume(ctx, n, -1, gray(58), gray(158), gray(240));
}

// Метёлка камыша: вытянутая капля с пушистыми штрихами
function drawPlume(ctx: CanvasRenderingContext2D, s: Stalk, hp: number): void {
  const dt = (s.topLen / s.len) * hp;
  const n = M_TOP + 1;
  const dr = s.droop * s.topLen * hp;
  for (let j = 0; j < n; j++) {
    const uu = j / M_TOP;
    pointAt(s, s.topT + dt * uu);
    px[j] = ox + dr * uu * uu;
    py[j] = oy + Math.abs(dr) * 0.25 * uu * uu;
    wd[j] = s.topW * (0.3 + 0.7 * hp) * Math.sin(Math.PI * Math.pow(uu, 0.65)) * 0.9 + 1;
  }
  fillVolume(ctx, n, -1, gray(66), gray(172), gray(244));
  // пушистые штрихи по бокам
  ctx.strokeStyle = gray(208);
  ctx.lineWidth = Math.max(1.5, s.topW * 0.1);
  ctx.lineCap = "round";
  ctx.beginPath();
  for (let k = 1; k < n - 1; k++) {
    const dir = k % 2 === 0 ? -1 : 1;
    const w = wd[k] * 0.5;
    ctx.moveTo(px[k] + dir * w * 0.6, py[k]);
    ctx.lineTo(px[k] + dir * (w + s.topW * 0.22 * hp), py[k] - s.topLen * 0.05 * hp);
  }
  ctx.stroke();
}

function drawGrass(ctx: CanvasRenderingContext2D, gr: Grass, gAll: number, time: number, g: Grads): void {
  const p = clamp01((gAll - gr.delay) / (1 - gr.delay));
  if (p <= 0) return;
  const lp = easeOut(clamp01(p / 0.8));
  const sway = 0.1 * Math.sin(time * 0.9 + gr.ph);
  drawBlade(ctx, gr.x, gr.y, 0, gr.side, gr.ang, gr.bend, gr.len, gr.wid, lp, sway, g, 1);
}

// Лист кувшинки: эллипс с радиальным градиентом, вырезом, прожилками и ободком
function drawPad(ctx: CanvasRenderingContext2D, pd: Pad, gAll: number, time: number, g: Grads, u: number): void {
  const p = clamp01((gAll - pd.delay) / (1 - pd.delay));
  if (p <= 0) return;
  const pp = easeOut(clamp01(p / 0.7));
  if (pp < 0.02) return;
  ctx.save();
  ctx.translate(pd.x, pd.y + 1.4 * u * Math.sin(time * 0.9 + pd.ph));
  ctx.rotate(pd.rot + 0.02 * Math.sin(time * 0.7 + pd.ph));
  ctx.scale(pd.rx * pp, pd.ry * pp);
  // круги на воде под листом
  ctx.strokeStyle = gray(52);
  ctx.lineWidth = 0.05;
  ctx.beginPath();
  ctx.ellipse(0, 0.18, 1.16, 1.16, 0, 0, TAU);
  ctx.stroke();
  // сам лист с вырезом
  ctx.fillStyle = g.pad;
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.arc(0, 0, 1, pd.a0 + pd.nw, pd.a0 + TAU - pd.nw);
  ctx.closePath();
  ctx.fill();
  // прожилки
  ctx.strokeStyle = gray(64);
  ctx.lineWidth = 0.028;
  ctx.beginPath();
  for (let k = 0; k < 9; k++) {
    const a = pd.a0 + pd.nw + 0.12 + ((TAU - 2 * pd.nw - 0.24) * k) / 8;
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(a) * 0.94, Math.sin(a) * 0.94);
  }
  ctx.stroke();
  // светлый ободок на дальней кромке
  ctx.strokeStyle = gray(240);
  ctx.lineWidth = 0.06;
  ctx.beginPath();
  ctx.arc(0, 0, 0.97, Math.PI + 0.25, TAU - 0.25);
  ctx.stroke();
  ctx.restore();
}

// Один лепесток в локальных координатах (остриём вверх): тень, светлая половина, блик
function petal(
  ctx: CanvasRenderingContext2D,
  a: number,
  len: number,
  wid: number,
  cd: number,
  cm: number,
  cb: number,
): void {
  ctx.save();
  ctx.rotate(a);
  ctx.fillStyle = gray(cd);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.bezierCurveTo(-wid * 1.35, -len * 0.25, -wid * 0.9, -len * 0.72, 0, -len);
  ctx.bezierCurveTo(wid * 0.9, -len * 0.72, wid * 1.35, -len * 0.25, 0, 0);
  ctx.fill();
  // светлая (левая) половина
  ctx.fillStyle = gray(cm);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.bezierCurveTo(-wid * 1.35, -len * 0.25, -wid * 0.9, -len * 0.72, 0, -len);
  ctx.closePath();
  ctx.fill();
  // яркая сердцевина: та же половина, уже и короче
  ctx.scale(0.5, 0.88);
  ctx.translate(-wid * 0.15, -len * 0.04);
  ctx.fillStyle = gray(cb);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.bezierCurveTo(-wid * 1.35, -len * 0.25, -wid * 0.9, -len * 0.72, 0, -len);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

// Цветок кувшинки: три слоя остроконечных лепестков, раскрывается по fo
function drawFlower(ctx: CanvasRenderingContext2D, s: Stalk, fo: number, u: number): void {
  pointAt(s, 1);
  const x = ox;
  const y = oy;
  pointAt(s, 0.95);
  const tilt = Math.atan2(x - ox, oy - y);
  const L = s.petalLen;
  const open = 0.15 + 0.85 * fo;
  const lenK = 0.3 + 0.7 * fo;
  const wK = L * 0.3 * (0.6 + 0.4 * fo);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(tilt);
  // внешний слой
  const a1 = [-1.3, 1.3, -0.85, 0.85, -0.42, 0.42];
  for (let i = 0; i < a1.length; i++) petal(ctx, a1[i] * open, L * lenK, wK, 62, 135, 196);
  // средний слой
  const a2 = [-0.98, 0.98, -0.5, 0.5, 0];
  for (let i = 0; i < a2.length; i++) petal(ctx, a2[i] * open, L * 0.82 * lenK, wK * 0.9, 96, 178, 232);
  // тычинки
  if (fo > 0.3) {
    ctx.strokeStyle = gray(250);
    ctx.lineWidth = Math.max(1.5, 1.8 * u);
    ctx.lineCap = "round";
    ctx.beginPath();
    for (let k = -3; k <= 3; k++) {
      ctx.moveTo(0, 0);
      ctx.lineTo(k * L * 0.03 * fo, -L * 0.32 * fo);
    }
    ctx.stroke();
  }
  // внутренний слой
  const a3 = [-0.55, 0.55, -0.2, 0.2];
  for (let i = 0; i < a3.length; i++) petal(ctx, a3[i] * open, L * 0.6 * lenK, wK * 0.8, 130, 214, 255);
  ctx.restore();
}

// ---------- публичная функция ----------

/**
 * Рисует растения в 2D-контекст по яркости (серый на чёрном).
 * Фон вызывающий заливает сам. grow: 0..1 — насколько выросли; timeSec — для покачивания.
 * width/height — в единицах контекста (CSS-пиксели, если ctx уже масштабирован под dpr).
 */
export function drawReeds(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  grow: number,
  timeSec: number,
  options: ReedsOptions,
): void {
  if (!(grow > 0) || !(width > 0) || !(height > 0)) return;
  const gAll = grow > 1 ? 1 : grow;
  const geo = getGeometry(options.seed, width, height);
  const g = getGrads(ctx, width, height);

  ctx.save();
  if (options.side === "right") {
    ctx.translate(width, 0);
    ctx.scale(-1, 1);
  }
  ctx.lineJoin = "round";

  for (let i = 0; i < geo.stalks.length; i++) drawStalk(ctx, geo.stalks[i], gAll, timeSec, g);
  for (let i = 0; i < geo.grass.length; i++) drawGrass(ctx, geo.grass[i], gAll, timeSec, g);
  // стебли кувшинок, потом листья на воде, потом цветы поверх
  for (let i = 0; i < geo.lilies.length; i++) drawStalk(ctx, geo.lilies[i], gAll, timeSec, g);
  for (let i = 0; i < geo.pads.length; i++) drawPad(ctx, geo.pads[i], gAll, timeSec, g, geo.u);
  for (let i = 0; i < geo.lilies.length; i++) {
    const s = geo.lilies[i];
    const p = clamp01((gAll - s.delay) / (1 - s.delay));
    const fo = easeOut(clamp01((p - 0.6) / 0.4));
    if (fo > 0) drawFlower(ctx, s, fo, geo.u);
  }
  ctx.restore();
}
