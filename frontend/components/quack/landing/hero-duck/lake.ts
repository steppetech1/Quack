// Берег под лапами утки. Рисуем ЯРКОСТЬЮ в тот же холст, что и камыши
// (reeds.ts), решётка глифов переводит её в точки. Метка цвета: всё, что должно стать голубым (вода, рябь, туман),
// рисуется с зелёным каналом 255 — решётка читает яркость из красного канала, а по зелёному понимает, что это вода.
// Координаты — сцены: ширина W, высота H, утка по центру, её лапы на высоте footY.

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
const smooth = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
const gray = (v: number, a = 1) => `rgba(${v | 0},${v | 0},${v | 0},${a})`;
const wet = (v: number, a = 1) => `rgba(${v | 0},255,${v | 0},${a})`; // «голубая» метка

type Dash = { x: number; row: number; len: number; lum: number; ph: number };
type Mist = { x: number; dy: number; rx: number; ry: number; speed: number; lum: number };
type Tuft = { x: number; blades: { ang: number; len: number; bend: number }[]; ph: number };
type Pebble = { x: number; rx: number; ry: number; lum: number };
type Hill = { x: number; h: number; w: number };
type Lake = { dashes: Dash[]; mist: Mist[]; tufts: Tuft[]; pebbles: Pebble[]; hills: Hill[]; reeds: { x: number; h: number }[] };

const cache = new Map<string, Lake>();

function build(W: number, H: number): Lake {
  const r = mulberry32(7);
  const dashes: Dash[] = [];
  for (let row = 0; row < 9; row++) {
    const n = 7 + row * 2;
    for (let i = 0; i < n; i++) {
      dashes.push({ x: r() * W, row, len: (6 + r() * 26) * (0.6 + row * 0.08), lum: 90 + r() * 90, ph: r() * TAU });
    }
  }
  const mist: Mist[] = [];
  for (let i = 0; i < 7; i++) {
    mist.push({ x: r() * W, dy: (r() - 0.6) * H * 0.07, rx: W * (0.14 + r() * 0.16), ry: H * (0.035 + r() * 0.03), speed: 4 + r() * 8, lum: 90 + r() * 50 });
  }
  const tufts: Tuft[] = [];
  for (let i = 0; i < 9; i++) {
    const blades = [];
    const nb = 3 + ((r() * 3) | 0);
    for (let k = 0; k < nb; k++) blades.push({ ang: (r() - 0.5) * 0.9, len: 5 + r() * 9, bend: (r() - 0.5) * 0.6 });
    tufts.push({ x: (r() - 0.5) * 2, blades, ph: r() * TAU });
  }
  const pebbles: Pebble[] = [];
  for (let i = 0; i < 8; i++) pebbles.push({ x: (r() - 0.5) * 2, rx: 1.5 + r() * 3, ry: 1 + r() * 1.6, lum: 100 + r() * 70 });
  const hills: Hill[] = [];
  for (let i = 0; i < 6; i++) hills.push({ x: r() * W, h: H * (0.02 + r() * 0.035), w: W * (0.12 + r() * 0.2) });
  const reeds: { x: number; h: number }[] = [];
  for (let i = 0; i < 26; i++) reeds.push({ x: r() * W, h: H * (0.015 + r() * 0.03) });
  return { dashes, mist, tufts, pebbles, hills, reeds };
}

function get(W: number, H: number): Lake {
  const key = `${Math.round(W)}|${Math.round(H)}`;
  let l = cache.get(key);
  if (!l) {
    if (cache.size > 6) cache.clear();
    l = build(W, H);
    cache.set(key, l);
  }
  return l;
}

/** Берег под лапами: тёмная кромка земли, камешки, пучки травы и рябь у кромки воды. Рисуется после камышей. */
export function drawShore(ctx: CanvasRenderingContext2D, W: number, footY: number, grow: number, time: number, duckW: number) {
  if (grow <= 0) return;
  const lk = get(W, Math.round(footY * 1.05));
  const cx = W / 2;
  const half = duckW * 0.5;
  const a = smooth(0, 0.5, grow);
  ctx.save();
  // земля
  const g = ctx.createRadialGradient(cx, footY + 2, 1, cx, footY + 2, half);
  g.addColorStop(0, gray(96, a));
  g.addColorStop(0.7, gray(70, 0.85 * a));
  g.addColorStop(1, gray(60, 0));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.ellipse(cx, footY + 3, half, 7, 0, 0, TAU);
  ctx.fill();
  // рябь у кромки
  ctx.lineWidth = 1.3;
  for (let k = 0; k < 3; k++) {
    const rr = half * (1.05 + k * 0.16) + Math.sin(time * 0.8 + k) * 2;
    ctx.strokeStyle = wet(120 - k * 20, 0.8 * a);
    ctx.beginPath();
    ctx.ellipse(cx, footY + 4, rr, 6 + k * 2.5, 0, 0.12 * Math.PI, 0.88 * Math.PI);
    ctx.stroke();
  }
  // камешки
  for (const p of lk.pebbles) {
    ctx.fillStyle = gray(p.lum, a);
    ctx.beginPath();
    ctx.ellipse(cx + p.x * half * 0.85, footY + 4 + Math.abs(p.x) * 1.5, p.rx, p.ry, 0, 0, TAU);
    ctx.fill();
  }
  // трава: пучки растут из земли вместе с камышами
  const gh = smooth(0.1, 0.8, grow);
  ctx.lineCap = "round";
  ctx.lineWidth = 1.4;
  for (const t of lk.tufts) {
    const x0 = cx + t.x * half * 0.95;
    for (const b of t.blades) {
      const sway = Math.sin(time * 1.1 + t.ph) * 0.12;
      const ang = b.ang + sway;
      const len = b.len * gh;
      ctx.strokeStyle = gray(150, a);
      ctx.beginPath();
      ctx.moveTo(x0, footY + 3);
      ctx.quadraticCurveTo(x0 + Math.sin(ang) * len * 0.5, footY + 3 - len * 0.6, x0 + Math.sin(ang + b.bend) * len, footY + 3 - Math.cos(ang + b.bend) * len);
      ctx.stroke();
    }
  }
  ctx.restore();
}
