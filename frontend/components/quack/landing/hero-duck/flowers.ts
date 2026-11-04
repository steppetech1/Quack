// Цветы — ближний план вокруг утки (за ними, дальше, — камыши из reeds.ts). Приём как в блоке Prologue на
// sondaven.com: там видео роста, пропущенное через сетку штрихов; у нас — процедурный рост, нарисованный ЯРКОСТЬЮ
// (серый на прозрачном), который решётка глифов в hero-duck.tsx переводит в точки, звёздочки и рогатки.
// Виды разные: мак (широкие лепестки, тёмная сердцевина), ромашка (много узких лучей, светлый диск), тюльпан
// (чашечка сбоку) и колос-соцветие (мелкие цветки по верхушке, раскрываются снизу вверх). От стебля в стороны
// отходят ветки со своими бутонами — они распускаются позже главного цветка.
// Всё строится для левого края сцены; правый — зеркалом.

/**
 * extend — на сколько опустить корни (цветы и бутоны остаются на месте); reach — докуда по ширине доходит куст (доля);
 * crowd — сгущение к краю экрана (1 — равномерно, больше — гуще у края и реже к центру); count — сколько цветов
 */
export type FlowersOptions = { side: "left" | "right"; seed: number; extend?: number; reach?: number; crowd?: number; count?: number };

const TAU = Math.PI * 2;

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
const easeOut = (x: number) => 1 - Math.pow(1 - clamp01(x), 3);
const easeBack = (x: number) => {
  const t = clamp01(x);
  const c = 1.3;
  return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
};
const gray = (v: number) => {
  const c = Math.max(0, Math.min(255, v | 0));
  return `rgb(${c},${c},${c})`;
};

type Kind = "poppy" | "daisy" | "tulip" | "spike";
type Leaf = { at: number; side: 1 | -1; len: number; wid: number; ang: number; ph: number };
type Head = { kind: Kind; r: number; petals: number; rot: number; tilt: number };
type Branch = { at: number; side: 1 | -1; len: number; ang: number; head: Head; delay: number };
type Plant = {
  x: number;
  h: number;
  lean: number;
  curve: number;
  w: number;
  delay: number;
  span: number;
  leaves: Leaf[];
  head: Head;
  branches: Branch[];
  ph: number;
};

const KINDS: Kind[] = ["poppy", "daisy", "tulip", "spike"];

function makeHead(r: () => number, kind: Kind, size: number): Head {
  const petals = kind === "poppy" ? 4 + ((r() * 2) | 0) : kind === "daisy" ? 13 + ((r() * 6) | 0) : kind === "tulip" ? 3 : 11 + ((r() * 5) | 0);
  return { kind, r: size, petals, rot: r() * TAU, tilt: (r() - 0.5) * 0.5 };
}

function build(seed: number, W: number, H: number, reach: number, crowd: number, count: number): Plant[] {
  const r = mulberry32(seed);
  const zone = W * reach; // ближний план занимает край сцены до свободной полосы у утки
  const plants: Plant[] = [];
  const N = count; // цветов немного: между ними видны камыши дальнего плана
  for (let i = 0; i < N; i++) {
    // по зоне без пустых дыр; crowd > 1 сдвигает цветы к краю экрана — там гуще, к центру реже
    const slot = Math.pow((i + 0.15 + 0.7 * r()) / N, crowd);
    const x = -W * 0.02 + zone * slot;
    const edge = slot;
    const h = H * (0.36 + 0.34 * (1 - edge) * (0.7 + 0.3 * r()));
    const kind = KINDS[(i + ((r() * 2) | 0)) % KINDS.length];
    const size = H * (kind === "spike" ? 0.12 : kind === "daisy" ? 0.075 : 0.09) * (0.8 + 0.4 * r());
    const leaves: Leaf[] = [];
    const nl = 2 + ((r() * 2) | 0);
    for (let k = 0; k < nl; k++) {
      leaves.push({ at: 0.06 + (0.45 * (k + r() * 0.6)) / nl, side: k % 2 ? 1 : -1, len: H * (0.1 + 0.06 * r()), wid: H * (0.028 + 0.016 * r()), ang: 0.6 + 0.5 * r(), ph: r() * TAU });
    }
    // боковые ветки с бутонами: у маков и ромашек 1–2, у тюльпана и колоса реже
    const branches: Branch[] = [];
    const nb = kind === "poppy" || kind === "daisy" ? 1 + ((r() * 2) | 0) : r() < 0.35 ? 1 : 0;
    for (let k = 0; k < nb; k++) {
      const side: 1 | -1 = k % 2 ? -1 : 1;
      branches.push({
        at: 0.45 + 0.3 * r(),
        side,
        len: h * (0.16 + 0.12 * r()),
        ang: 0.5 + 0.35 * r(),
        head: makeHead(r, kind, size * (0.5 + 0.2 * r())),
        delay: 0.12 + 0.12 * r(),
      });
    }
    plants.push({
      x,
      h,
      lean: (r() - 0.3) * 0.16,
      curve: (r() - 0.5) * 0.1,
      w: Math.max(1.8, H * 0.009),
      delay: 0.08 + 0.3 * r(),
      span: 0.55 + 0.1 * r(),
      leaves,
      head: makeHead(r, kind, size),
      branches,
      ph: r() * TAU,
    });
  }
  return plants;
}

const cache = new Map<string, Plant[]>();
function get(seed: number, W: number, H: number, reach: number, crowd = 1, count = 6) {
  const key = `${seed}|${Math.round(W)}|${Math.round(H)}|${reach.toFixed(3)}|${crowd}|${count}`;
  let p = cache.get(key);
  if (!p) {
    if (cache.size > 8) cache.clear();
    p = build(seed, W, H, reach, crowd, count);
    cache.set(key, p);
  }
  return p;
}

let EXT = 0; // на сколько опущены корни в текущей отрисовке: стебель длиннее снизу, верхушка на месте
function stemAt(p: Plant, H: number, t: number, sway: number) {
  return {
    x: p.x + p.lean * p.h * t * t + p.curve * p.h * Math.sin(Math.PI * t) + sway * t * t,
    y: H + EXT - (p.h + EXT) * t,
  };
}

function drawLeaf(ctx: CanvasRenderingContext2D, x: number, y: number, lf: Leaf, open: number, base: number, sway: number) {
  if (open <= 0) return;
  const len = lf.len * open;
  const wid = lf.wid * (0.3 + 0.7 * open);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(Math.PI + lf.side * lf.ang * (0.3 + 0.7 * open) + sway * 0.3);
  ctx.fillStyle = gray(base * 0.7);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.quadraticCurveTo(wid, len * 0.45, 0, len);
  ctx.quadraticCurveTo(-wid, len * 0.45, 0, 0);
  ctx.fill();
  ctx.fillStyle = gray(base);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.quadraticCurveTo(-wid, len * 0.45, 0, len);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = gray(base * 1.35);
  ctx.lineWidth = Math.max(1, wid * 0.12);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(0, len * 0.9);
  ctx.stroke();
  ctx.restore();
}

function drawBud(ctx: CanvasRenderingContext2D, hd: Head, s: number, base: number) {
  const tall = hd.kind === "tulip" || hd.kind === "spike" ? 1.5 : 1;
  ctx.fillStyle = gray(base * 0.85);
  ctx.beginPath();
  ctx.ellipse(0, -s * 0.5 * tall, s * 0.5, s * tall, 0, 0, TAU);
  ctx.fill();
  ctx.fillStyle = gray(base * 1.3);
  ctx.beginPath();
  ctx.ellipse(-s * 0.15, -s * 0.7 * tall, s * 0.18, s * 0.6 * tall, 0, 0, TAU);
  ctx.fill();
}

function petal(ctx: CanvasRenderingContext2D, ax: number, ay: number, dist: number, w: number, l: number, lum: number) {
  ctx.save();
  ctx.translate(ax * dist, ay * dist);
  ctx.rotate(Math.atan2(ay, ax) + Math.PI / 2);
  ctx.fillStyle = gray(lum);
  ctx.beginPath();
  ctx.ellipse(0, 0, w, l, 0, 0, TAU);
  ctx.fill();
  ctx.strokeStyle = gray(lum * 1.3);
  ctx.lineWidth = Math.max(1, w * 0.12);
  ctx.beginPath();
  ctx.ellipse(0, 0, w, l, 0, Math.PI * 1.15, Math.PI * 1.85);
  ctx.stroke();
  ctx.restore();
}

/** Цветок: bud — набухание бутона, bloom — раскрытие */
function drawHead(ctx: CanvasRenderingContext2D, x: number, y: number, hd: Head, bud: number, bloom: number, base: number, time: number) {
  if (bud <= 0) return;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(hd.tilt + 0.03 * Math.sin(time * 0.8 + hd.rot));
  const R = hd.r;
  if (bloom <= 0.02) {
    drawBud(ctx, hd, R * 0.32 * easeOut(bud), base);
    ctx.restore();
    return;
  }
  const open = easeBack(bloom);
  const n = hd.petals;

  if (hd.kind === "poppy") {
    // мак: 4–5 широких перекрывающихся лепестков, тёмная сердцевина, кольцо тычинок
    for (let layer = 0; layer < 2; layer++) {
      const lr = R * (layer === 0 ? 1 : 0.7) * (0.3 + 0.7 * open);
      for (let k = 0; k < n; k++) {
        const a = hd.rot + (k / n) * TAU + layer * (Math.PI / n);
        const sp = 0.2 + 0.8 * open;
        const ax = Math.cos(a) * sp;
        const ay = Math.sin(a) * sp * 0.8 - (1 - open) * 0.7;
        petal(ctx, ax, ay, lr * 0.5, lr * 0.5, lr * 0.52, base * (layer ? 1.15 : 1) * (0.85 + 0.25 * Math.max(0, -Math.sin(a))));
      }
    }
    const cr = R * 0.2 * (0.4 + 0.6 * open);
    ctx.fillStyle = gray(base * 0.3);
    ctx.beginPath();
    ctx.arc(0, 0, cr, 0, TAU);
    ctx.fill();
    if (bloom > 0.5) {
      ctx.fillStyle = gray(base * 1.6);
      for (let k = 0; k < 10; k++) {
        const a = (k / 10) * TAU;
        ctx.beginPath();
        ctx.arc(Math.cos(a) * cr * 1.1, Math.sin(a) * cr * 1.1, Math.max(1, cr * 0.14), 0, TAU);
        ctx.fill();
      }
    }
  } else if (hd.kind === "daisy") {
    // ромашка: много узких лучей, раскрываются от «вверх» к плоскому кругу, светлый выпуклый диск
    for (let k = 0; k < n; k++) {
      const a = hd.rot + (k / n) * TAU;
      const sp = 0.15 + 0.85 * open;
      const ax = Math.cos(a) * sp;
      const ay = Math.sin(a) * sp * 0.75 - (1 - open) * 0.8;
      petal(ctx, ax, ay, R * 0.55, R * 0.09, R * 0.42 * (0.4 + 0.6 * open), base * 1.25);
    }
    const cr = R * 0.24;
    ctx.fillStyle = gray(base * 0.55);
    ctx.beginPath();
    ctx.arc(0, 0, cr * 1.15, 0, TAU);
    ctx.fill();
    ctx.fillStyle = gray(base * 1.45);
    ctx.beginPath();
    ctx.arc(-cr * 0.2, -cr * 0.2, cr * 0.75, 0, TAU);
    ctx.fill();
  } else if (hd.kind === "tulip") {
    // тюльпан: чашечка сбоку, три лепестка раскрываются в стороны
    const h = R * 1.15;
    const spread = 0.12 + 0.42 * open;
    const lums = [base * 0.85, base * 1.05, base * 1.25];
    const angs = [-spread, spread, 0];
    for (let k = 0; k < 3; k++) {
      ctx.save();
      ctx.rotate(angs[k]);
      ctx.fillStyle = gray(lums[k]);
      ctx.beginPath();
      ctx.ellipse(0, -h * 0.5, R * (k === 2 ? 0.42 : 0.36), h * 0.55, 0, 0, TAU);
      ctx.fill();
      ctx.strokeStyle = gray(lums[k] * 1.25);
      ctx.lineWidth = Math.max(1, R * 0.05);
      ctx.beginPath();
      ctx.moveTo(0, -h * 0.05);
      ctx.lineTo(0, -h * 0.95);
      ctx.stroke();
      ctx.restore();
    }
  } else {
    // колос: мелкие цветки по верхушке, распускаются снизу вверх
    const len = R * 2.2;
    for (let k = 0; k < n; k++) {
      const t = k / (n - 1);
      const f = easeOut((bloom - t * 0.55) / 0.45);
      if (f <= 0) {
        ctx.fillStyle = gray(base * 0.8);
        ctx.beginPath();
        ctx.ellipse(0, -len * t, R * 0.06, R * 0.1, 0, 0, TAU);
        ctx.fill();
        continue;
      }
      const side = k % 2 ? 1 : -1;
      const w = R * 0.28 * (1 - 0.55 * t) * f;
      const cx = side * w * 0.6;
      const cy = -len * t;
      ctx.fillStyle = gray(base * (1 + 0.3 * (1 - t)));
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(1, w), 0, TAU);
      ctx.fill();
      ctx.fillStyle = gray(base * 0.45);
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(0.6, w * 0.3), 0, TAU);
      ctx.fill();
    }
  }
  ctx.restore();
}

/** Рисует цветы ближнего плана по яркости. grow 0..1 — рост сцены; time — покачивание. */
export function drawFlowers(ctx: CanvasRenderingContext2D, W: number, H: number, grow: number, time: number, options: FlowersOptions) {
  if (grow <= 0 || W <= 0 || H <= 0) return;
  const plants = get(options.seed, W, H, options.reach ?? 0.36, options.crowd ?? 1, options.count ?? 6);
  EXT = options.extend ?? 0;
  ctx.save();
  if (options.side === "right") {
    ctx.translate(W, 0);
    ctx.scale(-1, 1);
  }
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const base = 170;
  for (const p of plants) {
    const g = clamp01((grow - p.delay) / p.span);
    if (g <= 0) continue;
    const sway = Math.sin(time * 0.9 + p.ph) * p.h * 0.012 * g;
    const stem = easeOut(g / 0.45);

    for (const lf of p.leaves) {
      if (stem < lf.at) continue;
      const at = stemAt(p, H, lf.at, sway);
      drawLeaf(ctx, at.x, at.y, lf, easeOut((g - 0.1 - lf.at * 0.35) / 0.3), base * 0.75, Math.sin(time * 1.1 + lf.ph) * 0.15);
    }

    // главный стебель
    ctx.strokeStyle = gray(base * 0.72);
    ctx.lineWidth = p.w;
    ctx.beginPath();
    for (let k = 0; k <= 14; k++) {
      const pt = stemAt(p, H, (stem * k) / 14, sway);
      if (k === 0) ctx.moveTo(pt.x, pt.y + 4);
      else ctx.lineTo(pt.x, pt.y);
    }
    ctx.stroke();

    // боковые ветки: отрастают, когда стебель их миновал, их бутоны раскрываются позже главного
    for (const b of p.branches) {
      if (stem < b.at) continue;
      const bg = clamp01((g - b.at * 0.45 - 0.05) / 0.5);
      const from = stemAt(p, H, b.at, sway);
      const a = -Math.PI / 2 + b.side * b.ang;
      const L = b.len * easeOut(bg / 0.5);
      const tx = from.x + Math.cos(a) * L + b.side * L * 0.15;
      const ty = from.y + Math.sin(a) * L;
      ctx.strokeStyle = gray(base * 0.68);
      ctx.lineWidth = p.w * 0.7;
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.quadraticCurveTo(from.x + Math.cos(a) * L * 0.3 + b.side * L * 0.25, from.y + Math.sin(a) * L * 0.6, tx, ty);
      ctx.stroke();
      const bud = easeOut((g - 0.5 - b.delay * 0.5) / 0.15);
      const bloom = clamp01((g - 0.62 - b.delay) / 0.35);
      drawHead(ctx, tx, ty, b.head, bud, bloom, base * 0.95, time);
    }

    // главный цветок
    if (stem > 0.97) {
      const top = stemAt(p, H, 1, sway);
      drawHead(ctx, top.x, top.y, p.head, easeOut((g - 0.38) / 0.15), clamp01((g - 0.5) / 0.4), base, time);
    }
  }
  ctx.restore();
}

type PadItem = { x: number; t: number; r: number; rot: number; notch: number; flower: boolean; ph: number; delay: number };
const padCache = new Map<string, PadItem[]>();

/**
 * Кувшинки на воде в перспективе: плоские листья-«блюдца», рассеянные по глубине. Ближние (внизу) крупнее и
 * круглее, дальние (выше, ближе к горизонту) мельче и сплющены. На некоторых — маленький цветок.
 * yNear/yFar — ближняя и дальняя линии воды; reach — докуда от края по ширине (доля W). Левый край; правый — зеркалом.
 */
export function drawPads(ctx: CanvasRenderingContext2D, W: number, yNear: number, yFar: number, reach: number, grow: number, time: number, options: { side: "left" | "right"; seed: number }) {
  if (grow <= 0) return;
  const key = `${options.seed}|${Math.round(W)}|${reach.toFixed(3)}`;
  let pads = padCache.get(key);
  if (!pads) {
    const r = mulberry32(options.seed);
    pads = [];
    for (let i = 0; i < 16; i++) {
      const t = Math.pow(r(), 0.8); // 0 — ближний, 1 — дальний
      pads.push({ x: W * reach * r(), t, r: 26 * (1 - 0.6 * t) * (0.75 + 0.5 * r()), rot: (r() - 0.5) * 0.4, notch: r() * TAU, flower: r() < 0.3, ph: r() * TAU, delay: 0.15 + 0.4 * r() });
    }
    pads.sort((a, b) => b.t - a.t); // дальние первыми
    if (padCache.size > 8) padCache.clear();
    padCache.set(key, pads);
  }
  ctx.save();
  if (options.side === "right") {
    ctx.translate(W, 0);
    ctx.scale(-1, 1);
  }
  for (const p of pads) {
    const g = easeOut((grow - p.delay) / 0.5);
    if (g <= 0) continue;
    const y = yNear + (yFar - yNear) * p.t + Math.sin(time * 0.9 + p.ph) * 0.6;
    const rx = p.r * g;
    const ry = rx * (0.34 - 0.14 * p.t); // чем дальше, тем площе
    const lum = 190 - 50 * p.t;
    ctx.save();
    ctx.translate(p.x, y);
    ctx.rotate(p.rot);
    ctx.fillStyle = gray(lum * 0.7);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.ellipse(0, 0, rx, ry, 0, p.notch + 0.25, p.notch + TAU - 0.25);
    ctx.closePath();
    ctx.fill();
    // светлая дальняя кромка листа
    ctx.strokeStyle = gray(lum * 1.25);
    ctx.lineWidth = Math.max(1, ry * 0.18);
    ctx.beginPath();
    ctx.ellipse(0, 0, rx * 0.96, ry * 0.92, 0, Math.PI + 0.3, TAU - 0.3);
    ctx.stroke();
    ctx.restore();
    if (p.flower && g > 0.6) {
      // маленький цветок кувшинки: острые лепестки звездой
      const fr = rx * 0.38 * easeOut((g - 0.6) / 0.4);
      ctx.save();
      ctx.translate(p.x + rx * 0.15, y - ry * 0.5);
      ctx.fillStyle = gray(lum * 1.45);
      for (let k = 0; k < 7; k++) {
        const a = -Math.PI / 2 + ((k - 3) / 3) * 1.2;
        ctx.save();
        ctx.rotate(a + Math.PI / 2);
        ctx.beginPath();
        ctx.ellipse(0, -fr * 0.5, fr * 0.22, fr * 0.55, 0, 0, TAU);
        ctx.fill();
        ctx.restore();
      }
      ctx.restore();
    }
  }
  ctx.restore();
}
