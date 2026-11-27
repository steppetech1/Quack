/**
 * Движок «ASCII»: 2D-источник (canvas) → поле моно-символов на прямоугольной решётке, как на лендинге и у modal.com.
 * Яркость ячейки задаёт символ из ряда «. : - = + * # % @» и цвет: приглушённый тёплый серый, янтарь только на светах.
 * Там, где источник — тонкая линия или кромка, символ идёт вдоль неё: | / \ -.
 * Интерфейс как у штриховки (renderSource / resize / getSize / dispose), поэтому слой растений меняет движок одной строкой.
 */

export type AsciiField = {
  /** Рисует источник символами; источник растягивается на весь canvas */
  renderSource(source: HTMLCanvasElement, timeSec: number): void;
  resize(widthCss: number, heightCss: number, dpr?: number): void;
  dispose(): void;
  /** Текущий размер в CSS px */
  getSize(): { width: number; height: number };
};

const RAMP = ".:-=+*#%@";
// '-' уже есть в ряду яркости; линейные символы идут после него
const CHARS = `${RAMP}|/\\`;
const LINE_V = CHARS.indexOf("|");
const LINE_SLASH = CHARS.indexOf("/");
const LINE_BACK = CHARS.indexOf("\\");
const LINE_H = CHARS.indexOf("-");

/** Ступеней цвета в атласе: от тёмного тёплого серого через песочный к янтарю */
const STEPS = 8;
const COLOR_DARK = [74, 70, 62];
const COLOR_SAND = [141, 135, 123];
const COLOR_AMBER = [237, 185, 102];

const MIN_LUM = 0.1; // ниже этой яркости символа нет
const MIN_LINE_LUM = 0.24; // тусклые кромки линией не рисуем: из них получается сплошной частокол «|»
const BOOST = 1.6; // тонкие стебли усредняются в ячейке и тускнеют: поднимаем яркость
const MIN_EDGE = 0.03; // меньше этой энергии кромки линия не рисуется, остаётся ряд яркости
const MIN_COHERENCE = 0.7; // насколько кромка должна быть «одной линией», а не пятном
const TWINKLE = 0.035; // доля ячеек, у которых символ в этот момент соседний по яркости

const clamp = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x);
const smooth = (a: number, b: number, x: number) => {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};

function hash(x: number, y: number, k: number): number {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(k | 0, 1274126177);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function stepColor(s: number): string {
  const t = s / (STEPS - 1);
  const [a, b, k] = t < 0.55 ? [COLOR_DARK, COLOR_SAND, t / 0.55] : [COLOR_SAND, COLOR_AMBER, (t - 0.55) / 0.45];
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(",")})`;
}

export function createAsciiField(canvas: HTMLCanvasElement): AsciiField {
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  const lumCanvas = document.createElement("canvas");
  const lumCtx = lumCanvas.getContext("2d", { willReadFrequently: true });
  if (!lumCtx) throw new Error("no 2d context");
  const atlas = document.createElement("canvas");

  let width = canvas.clientWidth || 300;
  let height = canvas.clientHeight || 150;
  let dpr = 1;
  let cw = 7; // ячейка, CSS px
  let ch = 13;
  let cols = 1;
  let rows = 1;
  let aw = 1; // ячейка атласа, device px
  let ah = 1;
  let lum = new Float32Array(1);
  let gx = new Float32Array(1);
  let gy = new Float32Array(1);
  let disposed = false;
  let last: { source: HTMLCanvasElement; t: number } | null = null;

  const family = () => getComputedStyle(canvas).fontFamily || "ui-monospace, monospace";

  const buildAtlas = () => {
    atlas.width = aw * CHARS.length;
    atlas.height = ah * STEPS;
    const a = atlas.getContext("2d");
    if (!a) return;
    a.clearRect(0, 0, atlas.width, atlas.height);
    a.font = `500 ${Math.round(ch * 0.9 * dpr)}px ${family()}`;
    a.textAlign = "center";
    a.textBaseline = "middle";
    for (let s = 0; s < STEPS; s++) {
      a.fillStyle = stepColor(s);
      for (let c = 0; c < CHARS.length; c++) a.fillText(CHARS[c], c * aw + aw / 2, s * ah + ah / 2 + ah * 0.04);
    }
  };

  const resize = (w: number, h: number, ratio?: number) => {
    if (disposed) return;
    width = Math.max(1, w);
    height = Math.max(1, h);
    dpr = Math.min(ratio ?? (typeof window !== "undefined" ? window.devicePixelRatio : 1), 2);
    const fs = clamp(Math.round(height / 64), 10, 14);
    cw = fs * 0.6;
    ch = Math.round(fs * 1.12);
    cols = Math.ceil(width / cw);
    rows = Math.ceil(height / ch);
    canvas.width = Math.ceil(width * dpr);
    canvas.height = Math.ceil(height * dpr);
    lumCanvas.width = cols;
    lumCanvas.height = rows;
    aw = Math.ceil(cw * dpr);
    ah = Math.ceil(ch * dpr);
    lum = new Float32Array(cols * rows);
    gx = new Float32Array(cols * rows);
    gy = new Float32Array(cols * rows);
    buildAtlas();
    // шрифт мог ещё не загрузиться: когда загрузится, пересобираем атлас и перерисовываем последний кадр
    const font = `500 ${Math.round(ch * 0.9 * dpr)}px ${family()}`;
    if (!document.fonts.check(font)) {
      void document.fonts.load(font).then(() => {
        if (disposed) return;
        buildAtlas();
        if (last) render(last.source, last.t);
      });
    }
  };

  const render = (source: HTMLCanvasElement, t: number) => {
    if (disposed || source.width < 2 || source.height < 2) return;
    last = { source, t };

    // яркость по ячейкам: уменьшение источника до решётки усредняет площадь ячейки
    lumCtx.imageSmoothingEnabled = true;
    lumCtx.imageSmoothingQuality = "high";
    lumCtx.drawImage(source, 0, 0, source.width, source.height, 0, 0, cols, rows);
    const data = lumCtx.getImageData(0, 0, cols, rows).data;
    for (let i = 0; i < cols * rows; i++) lum[i] = Math.min(1, (data[i * 4] / 255) * BOOST);

    // градиент в пикселях, не в ячейках: ячейка уже, чем выше, поэтому по y поправка cw/ch
    const asp = cw / ch;
    for (let y = 0; y < rows; y++) {
      const y0 = Math.max(0, y - 1) * cols;
      const y1 = Math.min(rows - 1, y + 1) * cols;
      for (let x = 0; x < cols; x++) {
        const i = y * cols + x;
        gx[i] = (lum[y * cols + Math.min(cols - 1, x + 1)] - lum[y * cols + Math.max(0, x - 1)]) * 0.5;
        gy[i] = (lum[y1 + x] - lum[y0 + x]) * 0.5 * asp;
      }
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const slice = Math.floor(t * 2.5); // «мерцание» меняется несколько раз в секунду, а не каждый кадр
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const i = y * cols + x;
        const L = lum[i];
        if (L < MIN_LUM) continue;

        // структурный тензор по соседям 3×3: направление кромки, даже если линия в одну ячейку шириной
        let jxx = 0;
        let jyy = 0;
        let jxy = 0;
        for (let dy = -1; dy <= 1; dy++) {
          const yy = y + dy;
          if (yy < 0 || yy >= rows) continue;
          for (let dx = -1; dx <= 1; dx++) {
            const xx = x + dx;
            if (xx < 0 || xx >= cols) continue;
            const j = yy * cols + xx;
            jxx += gx[j] * gx[j];
            jyy += gy[j] * gy[j];
            jxy += gx[j] * gy[j];
          }
        }

        let c = -1;
        let level = L;
        const energy = jxx + jyy;
        if (energy > MIN_EDGE && L >= MIN_LINE_LUM && L < 0.92) {
          const dxx = jxx - jyy;
          const dxy = 2 * jxy;
          if (Math.hypot(dxx, dxy) / energy > MIN_COHERENCE) {
            // направление градиента → направление линии, в [0, π)
            const a = (((0.5 * Math.atan2(dxy, dxx) + Math.PI / 2) % Math.PI) + Math.PI) % Math.PI;
            const deg = (a * 180) / Math.PI;
            c = deg < 22.5 || deg >= 157.5 ? LINE_H : deg < 67.5 ? LINE_BACK : deg < 112.5 ? LINE_V : LINE_SLASH;
            level = Math.max(L, 0.3);
          }
        }
        if (c < 0) {
          let r = Math.min(RAMP.length - 1, Math.floor(Math.pow(L, 0.85) * RAMP.length));
          if (hash(x, y, slice) < TWINKLE) r = clamp(r + (hash(y, x, slice) < 0.5 ? -1 : 1), 0, RAMP.length - 1);
          c = r;
        }

        const step = Math.round(smooth(0.1, 0.9, level) * (STEPS - 1));
        ctx.drawImage(atlas, c * aw, step * ah, aw, ah, Math.round(x * cw * dpr), Math.round(y * ch * dpr), aw, ah);
      }
    }
  };

  resize(width, height);

  return {
    renderSource: (source, t) => render(source, t),
    resize,
    getSize: () => ({ width, height }),
    dispose() {
      if (disposed) return;
      disposed = true;
      last = null;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    },
  };
}
