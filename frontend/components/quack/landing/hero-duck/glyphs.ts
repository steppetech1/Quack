/**
 * Глифы первого экрана. Набор ровно как в референсе с лестницей: круглая точка, шестилучевая звёздочка
 * и «рогатка» Y (прямая и перевёрнутая). Ничего больше: ни букв, ни математических знаков.
 * Яркость ячейки выбирает знак и цвет: свет — крупные точки, полутон — звёздочки, тень — рогатки, глубокая тень — пусто.
 */

export type Shape = "dot" | "star" | "y" | "yInv";

export const BUCKETS = 10;
const SHAPE_INDEX: Record<Shape, number> = { dot: 0, star: 1, y: 2, yInv: 3 };

// янтарная шкала бренда: от тёплой тени до почти белого блика
export type Ramp = [number, [number, number, number]][];
const RAMP: Ramp = [
  [0, [70, 40, 20]],
  [0.3, [140, 76, 28]],
  [0.6, [255, 138, 42]],
  [0.82, [255, 190, 100]],
  [1, [255, 236, 200]],
];

// мягкий светло-синий для частиц, что расходятся от «Quack!» (как голубые искры у ракеты в референсе)
const BLUE: Ramp = [
  [0, [48, 70, 104]],
  [0.5, [120, 166, 222]],
  [0.8, [170, 206, 245]],
  [1, [226, 240, 255]],
];

// приглушённая тёплая шкала для растений вокруг утки: тот же янтарь, но тише и серее
const MUTED: Ramp = [
  [0, [46, 36, 28]],
  [0.5, [112, 88, 64]],
  [0.8, [160, 130, 96]],
  [1, [204, 178, 142]],
];

// Утка, палитра «Б. Кряква в янтаре»: тело янтарное, части приглушённо настоящие. Индекс — часть утки (см. hero-duck.tsx)
// Палитра утки «1. Пастельный коричневый» (по мотивам варианта В): общие для всех частей тень и блик склеивают
// утку в одно целое, цвет части живёт в средних тонах. Лапы почти в цвет растений
const SHADOW: [number, number, number] = [42, 28, 18];
const NEAR_SHADOW: [number, number, number] = [90, 64, 42];
const HILITE: [number, number, number] = [240, 214, 176];
const part = (mid: [number, number, number][], warmHi: [number, number, number]): Ramp => [
  [0, SHADOW],
  [0.16, NEAR_SHADOW],
  [0.34, mid[0]],
  [0.6, mid[1]],
  [0.85, warmHi],
  [1, HILITE],
];
export const DUCK_RAMPS: Ramp[] = [
  // 0 тело: ~70% янтаря и ~30% пастельного коричневого — утка заметно выделяется на фоне растений
  part([[182, 110, 47], [232, 154, 70]], [248, 181, 131]),
  // 1 голова: зелёная, чуть ярче — под янтарное тело
  part([[76, 166, 124], [126, 212, 170]], [212, 230, 192]),
  // 2 клюв: ярче жёлтый
  part([[206, 160, 60], [240, 194, 82]], [250, 222, 150]),
  // 3 грудь: янтарно-коричневая, чуть рыжее тела
  part([[170, 90, 40], [214, 130, 62]], [240, 176, 124]),
  // 4 лапы: спокойный оранжевый, как у кряквы — видны на фоне растений
  part([[214, 110, 40], [246, 146, 60]], [252, 192, 128]),
  // 5 крыло и спина: тёплый коричневый — под янтарное тело
  part([[120, 80, 48], [168, 112, 64]], [214, 164, 112]),
  // 6 ошейник: сливочный
  part([[170, 150, 126], [233, 217, 194]], [244, 228, 206]),
];

// Палитры растений: камыши приглушённые, цветы теплее и светлее
export const LAYER_RAMPS: Ramp[] = [
  [[0, [46, 36, 28]], [0.5, [112, 88, 64]], [0.8, [160, 130, 96]], [1, [204, 178, 142]]],
  [[0, [56, 42, 30]], [0.5, [156, 120, 84]], [0.8, [204, 166, 120]], [1, [236, 212, 176]]],
];

export type Palette = "amber" | "blue" | "muted" | Ramp;

export function rampColor(t: number, a = 1, palette: Palette = "amber"): string {
  const ramp = Array.isArray(palette) ? palette : palette === "blue" ? BLUE : palette === "muted" ? MUTED : RAMP;
  const x = Math.max(0, Math.min(1, t));
  let i = 0;
  while (i < ramp.length - 2 && x > ramp[i + 1][0]) i++;
  const [t0, c0] = ramp[i];
  const [t1, c1] = ramp[i + 1];
  const u = (x - t0) / (t1 - t0);
  const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * u));
  return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
}

/** Размер знака в долях шага решётки */
function glyphSize(shape: Shape, bucket: number): number {
  const t = bucket / (BUCKETS - 1);
  if (shape === "dot") return 0.22 + 0.26 * t;
  if (shape === "star") return 0.36 + 0.1 * t;
  return 0.36 + 0.08 * t;
}

function drawShape(ctx: CanvasRenderingContext2D, shape: Shape, r: number, lw: number) {
  ctx.lineCap = "round";
  ctx.lineWidth = lw;
  if (shape === "dot") {
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  const arms = shape === "star" ? 6 : 3;
  const start = shape === "yInv" ? Math.PI / 2 : -Math.PI / 2; // Y: ножка вниз, ⅄: ножка вверх
  ctx.beginPath();
  for (let i = 0; i < arms; i++) {
    const a = start + Math.PI + (i * Math.PI * 2) / arms;
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
  }
  ctx.stroke();
}

/** Набор заранее нарисованных спрайтов: по одному на знак и ступень яркости */
export class GlyphSprites {
  readonly cell: number; // шаг решётки в device px
  readonly box: number;
  private sprites: (HTMLCanvasElement | undefined)[] = []; // индекс: форма * BUCKETS + ступень (без строковых ключей: отрисовка идёт тысячи раз за кадр)

  /**
   * `proportional`: толщина линий — постоянная доля шага (как на весь экран 1920px: 1px при шаге ~6,8px),
   * без минимума в 1px, поэтому знаки выглядят одинаково на любом размере окна. Иначе — минимум 1px.
   */
  constructor(cellDevicePx: number, palette: Palette = "amber", proportional = false) {
    this.cell = cellDevicePx;
    this.box = Math.ceil(cellDevicePx * 1.2);
    const shapes: Shape[] = ["dot", "star", "y", "yInv"];
    for (const s of shapes) {
      for (let b = 0; b < BUCKETS; b++) {
        const c = document.createElement("canvas");
        c.width = c.height = this.box;
        const ctx = c.getContext("2d");
        if (!ctx) continue;
        const color = rampColor(b / (BUCKETS - 1), 1, palette);
        ctx.fillStyle = color;
        ctx.strokeStyle = color;
        ctx.translate(this.box / 2, this.box / 2);
        const r = glyphSize(s, b) * cellDevicePx;
        drawShape(ctx, s, r, proportional ? cellDevicePx * 0.147 : Math.max(1, cellDevicePx * 0.13));
        this.sprites[SHAPE_INDEX[s] * BUCKETS + b] = c;
      }
    }
  }

  draw(ctx: CanvasRenderingContext2D, shape: Shape, bucket: number, x: number, y: number, scale = 1) {
    const img = this.sprites[SHAPE_INDEX[shape] * BUCKETS + bucket];
    if (!img) return;
    const s = this.box * scale;
    ctx.drawImage(img, x - s / 2, y - s / 2, s, s);
  }
}

export type Glyph = { shape: Shape; bucket: number; alpha: number };

// Знаков всего несколько десятков (форма × ступень × прозрачность): раздаём готовые общие объекты,
// а не создаём новый на каждую ячейку каждого кадра (десятки тысяч за кадр) — меньше мусора для сборщика
const GLYPH_POOL: (Glyph | undefined)[] = [];
function glyph(shape: Shape, bucket: number, alpha: number): Glyph {
  const tier = alpha === 1 ? 0 : alpha === 0.95 ? 1 : alpha === 0.85 ? 2 : 3;
  const k = SHAPE_INDEX[shape] * (BUCKETS * 4) + bucket * 4 + tier;
  return (GLYPH_POOL[k] ??= { shape, bucket, alpha });
}

/** Знак и ступень по яркости ячейки `l` (0..1) и её постоянному случайному числу `h`. Возвращённый объект общий: не менять */
export function pickGlyph(l: number, h: number): Glyph | null {
  if (l < 0.07) return null; // зрачок, глубокая тень
  const bucket = Math.min(BUCKETS - 1, Math.max(0, Math.round(l * (BUCKETS - 1))));
  if (l > 0.8) return glyph(h < 0.85 ? "dot" : "star", bucket, 1);
  if (l > 0.6) return glyph(h < 0.5 ? "star" : "dot", bucket, 1);
  if (l > 0.42) return glyph(h < 0.35 ? "star" : h < 0.7 ? (h < 0.52 ? "y" : "yInv") : "dot", bucket, 0.95);
  if (l > 0.2) return glyph(h < 0.5 ? "y" : "yInv", bucket, 0.85);
  return glyph(h < 0.5 ? "yInv" : "dot", Math.max(1, bucket), 0.55);
}
