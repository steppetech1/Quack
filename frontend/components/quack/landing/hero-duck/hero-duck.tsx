"use client";

import { useEffect, useRef, type RefObject } from "react";
import * as THREE from "three";
import { createDuckRig, GROUND, type DuckPose } from "./duck-rig";
import { BUCKETS, DUCK_RAMPS, GlyphSprites, LAYER_RAMPS, pickGlyph, type Shape } from "./glyphs";
import { drawShore } from "./lake";
import { drawFlowers, drawPads } from "./flowers";
import { drawReeds } from "./reeds";
import { NodeField } from "./nodes";
import { storyStore } from "../scenes/story";

/**
 * Утка первого экрана. 3D-модель (duck-rig) рендерится в маленькую текстуру, плотная гекс-решётка читает из неё
 * яркость и рисует глифы (точка, звёздочка, рогатка) со свечением. При заходе утка собирается из разлетевшихся точек,
 * через полсекунды вытягивается на лапах, поворачивает голову влево и говорит «Quack!» — надпись облаком точек
 * (густо в середине букв, реже к краям), от неё расходятся мягкие голубые частицы, в конце слово распадается. Дальше утка живёт сама: осматривается рывками головы,
 * тянется на лапах и машет крыльями. Не ходит: стоит на месте. Клик по утке — ещё одно «кря».
 * Когда первый экран уезжает вверх, утка рассыпается на точки.
 */

const FACING = -1.25; // корпус почти анфас к зрителю, чуть вправо; зеркальная поза — -π - FACING
const RIGHT = -0.6; // взгляд вправо на 3/4 к зрителю
const LEFT = -Math.PI + 0.6; // взгляд влево на 3/4 к зрителю
const ASSEMBLE = 1.1;
const FIRST_LOOK = 0.5;
const CELLS_PER_DUCK = 59;
// «Quack!» — на сетке мельче, чем утка и растения: иначе в штрих буквы влезает одна частица и слово не читается
const TEXT_CELL = 0.45;
const RS = 0.5; // разрешение 3D-рендера и холста растений относительно CSS px
const GROW_START = 0.1; // с: растения начинают расти почти сразу
const GROW_TIME = 2.4; // с: от ростка до раскрытого цветка (очень ускоренный рост)
// Сцену рисуем в постоянных единицах (высота REF_H) и масштабируем под сцену: на любом экране она выглядит
// так же, как в окне на пол-экрана (там высота сцены ~240px), а не «редеет» на широком мониторе
const REF_H = 240;
// планы островков от дальнего к ближнему; размер точек у всех одинаковый, глубину дают размер растений,
// тусклость и параллакс. fade — дальние планы у центра становятся прозрачными: у утки чистое пространство
const LAYER_STYLE = [
  { alpha: 0.8, dim: 0.72, ramp: 0, fade: true }, // 0 дальние камыши и цветы у горизонта
  { alpha: 0.9, dim: 0.95, ramp: 0, fade: true }, // 1 кувшинки на воде в перспективе
  { alpha: 0.72, dim: 1, ramp: 0, fade: false }, // 2 камыши и рогоз
  { alpha: 1, dim: 1, ramp: 1, fade: false }, // 3 цветы (верхний и нижний ряд)
];
const DUCK_WORLD_W = 3.4; // примерная ширина утки в мире: по ней считаем масштаб

// j — индекс пикселя ячейки в холстах растений, i — в вырезке 3D-рендера утки (−1, если ячейка вне утки)
// axis и zone — затухание у центра и в овале вокруг утки; считаются один раз на раскладку, а не на каждую ячейку каждого кадра
type Cell = { x: number; y: number; h: number; h2: number; dx: number; dy: number; delay: number; j: number; i: number; axis: number; zone: number };
// яркость точки растения по значению пикселя слоя: заранее, без Math.pow в горячем цикле
const PLANT_L = Float32Array.from({ length: 256 }, (_, v) => Math.pow(v / 255, 0.85) * 0.92);
type Spark = { x: number; y: number; vx: number; vy: number; age: number; life: number; shape: Shape; bucket: number; scale: number };
/** Точка облака «Quack!»: m — глубина внутри штриха (1 — середина буквы, 0 — край) */
type TextPt = { ox: number; oy: number; m: number; sx: number; sy: number; delay: number; vx: number; vy: number; blue: boolean; shape: Shape; bucket: number };
type Quack = { t: number; x: number; y: number; dir: 1 | -1 };

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const easeOut = (x: number) => 1 - Math.pow(1 - clamp01(x), 3);
const smoothstep = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
const approach = (v: number, target: number, rate: number, dt: number) => v + (target - v) * (1 - Math.exp(-rate * dt));
const rand = (a: number, b: number) => a + Math.random() * (b - a);
function hash(x: number, y: number) {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

export function HeroDuck({ scrollRef, label }: { scrollRef: RefObject<HTMLElement | null>; label: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const backRef = useRef<HTMLCanvasElement>(null);
  const backBRef = useRef<HTMLCanvasElement>(null); // вторая пара холстов растений: рисуем в скрытый, потом меняем местами
  const frontBRef = useRef<HTMLCanvasElement>(null);
  // свечение утки: её же размытые копии под ней — каждая точка светится своим цветом, а не общим оранжевым
  const glowNearRef = useRef<HTMLCanvasElement>(null);
  const glowFarRef = useRef<HTMLCanvasElement>(null);
  const frontRef = useRef<HTMLCanvasElement>(null);
  // узлы графа над уткой: свой холст, выше полосы утки
  const nodesRef = useRef<HTMLCanvasElement>(null);
  const hitRef = useRef<HTMLButtonElement>(null);
  const quackRef = useRef<() => void>(() => {});

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    const hit = hitRef.current;
    if (!wrap || !canvas || !hit) return;
    const ctx = canvas.getContext("2d");
    const back0 = backRef.current;
    const back1 = backBRef.current;
    const front0 = frontRef.current;
    const front1 = frontBRef.current;
    const bctx0 = back0?.getContext("2d");
    const bctx1 = back1?.getContext("2d");
    const fctx0 = front0?.getContext("2d");
    const fctx1 = front1?.getContext("2d");
    const glows = [glowNearRef.current, glowFarRef.current].filter((g): g is HTMLCanvasElement => !!g);
    const glowCtx = glows.map((g) => g.getContext("2d"));
    const farCtx = glowFarRef.current?.getContext("2d") ?? null;
    const nodesCv = nodesRef.current;
    const nctx = nodesCv?.getContext("2d");
    if (!ctx || !back0 || !back1 || !front0 || !front1 || !bctx0 || !bctx1 || !fctx0 || !fctx1 || !nodesCv || !nctx) return;
    // видимая пара и рабочая (в неё рисуем очередной кадр растений); готовый кадр показываем сменой видимости, без копирования
    let back = back0;
    let wback = back1;
    let front = front0;
    let wfront = front1;
    let bctx = bctx0;
    let wbctx = bctx1;
    let fctx = fctx0;
    let wfctx = fctx1;
    const field = new NodeField();
    let nodesTop = 0; // насколько холст узлов поднимается над полосой утки, CSS px
    let ptrPx: { x: number; y: number } | null = null; // курсор в координатах сцены

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: "low-power" });
    } catch {
      return; // без WebGL утки нет, остаётся заголовок
    }
    renderer.setClearColor(0x000000, 0);
    const rig = createDuckRig();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 60);
    const target = new THREE.WebGLRenderTarget(2, 2);
    let pixels = new Uint8Array(4 * 4);

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;

    // --- размеры и решётка ---
    let dpr = 1;
    let W = 0;
    let H = 0;
    let duckPx = 0;
    let Ht = 0; // полная высота сцены: от верха до низа первого экрана (растения дотянуты до края)
    let ph = 2; // строк в холстах растений
    let u = 0.01; // мировых единиц на CSS px
    let tw = 2;
    let th = 2;
    let cellCss = 5;
    let sprites: GlyphSprites | null = null;
    let blueSprites: GlyphSprites | null = null;
    let textSprites: GlyphSprites | null = null;
    let textBlue: GlyphSprites | null = null;
    let layerSprites: GlyphSprites[] = [];
    let duckSprites: GlyphSprites[] = [];
    // каждый план сцены рисуется яркостью в свой 2D-холст того же разрешения, что и 3D-рендер утки
    const layers = [0, 1, 2, 3].map(() => {
      const cv = document.createElement("canvas");
      return { cv, ctx: cv.getContext("2d", { willReadFrequently: true }), px: null as Uint8ClampedArray | null };
    });
    let cells: Cell[] = [];
    let duckCells: Cell[] = []; // только ячейки в области утки: они пересчитываются каждый кадр
    let duckX0 = 0; // вырезка 3D-рендера по ширине (в пикселях текстуры)
    let duckTW = 2;
    let duckOff = 0; // левый край холста утки внутри сцены, CSS px
    let lastPlant = -1;
    let stage = 0; // этап поэтапного обновления растений
    let cycle = 0; // номер цикла поэтапного обновления растений
    let zoneReady = false; // axis/zone ячеек посчитаны под текущую раскладку
    // холсты растений — в своём, меньшем разрешении: точки берут по одному пикселю на ячейку, больше не нужно
    let PS = 0.5;
    let ptw = 2;
    // чтение 3D-рендера утки асинхронное (без остановки видеокарты): рисуем по последнему готовому кадру
    let pixBack = new Uint8Array(4 * 4);
    let readPending = false;
    let readGen = 0;
    let textPts: TextPt[] = [];
    let textHalf = 40;
    let fontPx = 30;

    const buildText = () => {
      // «Quack!» как облако точек, как ракета в референсе: в середине штриха густо и ярко, к краям реже и тусклее,
      // немного точек выходит за край — мягкий ореол. Плотность — размытая маска текста.
      fontPx = Math.round(Math.max(28, Math.min(54, duckPx * 0.17)));
      const family = getComputedStyle(document.body).getPropertyValue("--font-jetbrains").trim() || "ui-monospace, monospace";
      const c = document.createElement("canvas");
      const tctx = c.getContext("2d", { willReadFrequently: true });
      if (!tctx) return;
      const font = `800 ${fontPx}px ${family}`;
      tctx.font = font;
      const pad = Math.ceil(fontPx * 0.25);
      c.width = Math.ceil(tctx.measureText("Quack!").width) + pad * 2;
      c.height = Math.ceil(fontPx * 1.2) + pad * 2;
      tctx.font = font;
      tctx.fillStyle = "#fff";
      tctx.textBaseline = "middle";
      tctx.fillText("Quack!", pad, c.height / 2);
      const w = c.width;
      const h = c.height;
      const raw = tctx.getImageData(0, 0, w, h).data;
      let a = new Float32Array(w * h);
      for (let i = 0; i < w * h; i++) a[i] = raw[i * 4 + 3] / 255;
      // box blur по осям, два прохода: получаем глубину внутри штриха
      const rb = Math.max(2, Math.round(fontPx * 0.07));
      const blur = (src: Float32Array, horiz: boolean) => {
        const out = new Float32Array(w * h);
        for (let y = 0; y < h; y++) {
          for (let x = 0; x < w; x++) {
            let sum = 0;
            let n = 0;
            for (let k = -rb; k <= rb; k++) {
              const xx = horiz ? x + k : x;
              const yy = horiz ? y : y + k;
              if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
              sum += src[yy * w + xx];
              n++;
            }
            out[y * w + x] = sum / n;
          }
        }
        return out;
      };
      for (let pass = 0; pass < 2; pass++) a = blur(blur(a, true), false);
      let peak = 0;
      for (let i = 0; i < a.length; i++) peak = Math.max(peak, a[i]);
      const pts: TextPt[] = [];
      // облако всё так же случайное, но точки стоят у ячеек той же шестиугольной сетки, что утка и растения
      // (с небольшим сдвигом, не больше одной на ячейку), знаки те же, что в сцене, но сетка мельче (TEXT_CELL)
      const gs = cellCss * TEXT_CELL;
      const gdy = gs * 0.866;
      let grow = 0;
      for (let gy = gdy / 2; gy < h; gy += gdy, grow++) {
        for (let gx = grow % 2 ? gs : gs / 2; gx < w; gx += gs) {
        const x = Math.min(w - 1, Math.max(0, gx + (Math.random() - 0.5) * gs * 0.4));
        const y = Math.min(h - 1, Math.max(0, gy + (Math.random() - 0.5) * gs * 0.4));
        const m = a[(y | 0) * w + (x | 0)] / (peak || 1);
        if (Math.random() > Math.pow(m, 1.8)) continue;
        const glyph = pickGlyph(0.3 + 0.7 * m + (Math.random() - 0.5) * 0.1, Math.random());
        if (!glyph) continue;
        const ang = Math.random() * Math.PI * 2;
        const r = (6 + 30 * (1 - m)) * Math.random();
        pts.push({
          ox: x - w / 2,
          oy: y - h / 2,
          m,
          sx: Math.cos(ang) * r,
          sy: Math.sin(ang) * r,
          delay: 0.2 * (1 - m) * Math.random() + 0.04 * Math.random(), // середина букв проявляется первой
          vx: 0,
          vy: 0,
          blue: Math.random() < 0.45,
          shape: glyph.shape,
          bucket: glyph.bucket,
        });
        }
      }
      textPts = pts;
      textHalf = w / 2 - pad;
    };

    const layout = () => {
      const r = wrap.getBoundingClientRect();
      if (r.width < 1) return false;
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = r.width;
      // утка и вся сцена растут с шириной окна без потолка: пропорции сцены одни и те же на любом мониторе
      duckPx = Math.min(W * 0.5, Math.max(170, window.innerWidth * 0.21));
      H = Math.round(duckPx * 1.2);
      u = DUCK_WORLD_W / duckPx;

      // утка занимает верхние H пикселей, ниже до края первого экрана — луг и цветы
      const sec = wrap.closest("section")?.getBoundingClientRect();
      // сцена уходит чуть ниже первого экрана — нижний ряд цветов не обрезается краем
      Ht = Math.max(H, sec ? Math.round(sec.bottom - r.top + H * 0.3) : H);

      // холст утки — только полоса вокруг неё (с запасом на «Quack!»): CSS-свечение считается по меньшей площади
      const DW = Math.min(W, Math.round(duckPx * 3.4));
      duckOff = Math.round((W - DW) / 2);
      canvas.width = Math.round(DW * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = `${DW}px`;
      canvas.style.height = `${H}px`;
      canvas.style.left = `${duckOff}px`;
      for (const g of glows) {
        g.width = canvas.width;
        g.height = canvas.height;
        g.style.width = canvas.style.width;
        g.style.height = canvas.style.height;
        g.style.left = canvas.style.left;
      }
      nodesTop = Math.round(duckPx * 1.15);
      nodesCv.width = Math.round(DW * dpr);
      nodesCv.height = Math.round((nodesTop + H * 0.8) * dpr);
      nodesCv.style.width = `${DW}px`;
      nodesCv.style.height = `${nodesTop + H * 0.8}px`;
      nodesCv.style.left = `${duckOff}px`;
      nodesCv.style.top = `${-nodesTop}px`;
      for (const cv of [back0, back1, front0, front1]) {
        cv.width = Math.round(W * dpr);
        cv.height = Math.round(Ht * dpr);
        cv.style.height = `${Ht}px`;
      }
      zoneReady = false;
      stage = 0;
      wrap.style.height = `${H}px`;
      hit.style.width = `${duckPx * 0.75}px`;
      hit.style.height = `${H * 0.8}px`;

      tw = Math.max(2, Math.round(W * RS));
      th = Math.max(2, Math.round(H * RS));
      target.setSize(tw, th);
      // холст растений — всегда ~2,2 пикселя на шаг решётки (как на весь экран): иначе в маленьком окне
      // штрихи считываются с другой детализацией и меняется доля точек, звёздочек и рогаток
      PS = Math.max(0.2, Math.min(1, 2.2 / (duckPx / CELLS_PER_DUCK)));
      ptw = Math.max(2, Math.round(W * PS));
      ph = Math.max(2, Math.round(Ht * PS));
      for (const l of layers) {
        l.cv.width = ptw;
        l.cv.height = ph;
      }
      // из 3D-рендера читаем только вырезку вокруг утки, а не всю ширину сцены
      duckX0 = Math.max(0, Math.floor((W / 2 - duckPx * 0.85) * RS));
      duckTW = Math.max(2, Math.min(tw - duckX0, Math.ceil(duckPx * 1.7 * RS)));
      pixels = new Uint8Array(duckTW * th * 4);
      pixBack = new Uint8Array(duckTW * th * 4);
      readPending = false;
      readGen++;
      renderer.setSize(tw, th, false);

      // кадр: земля у нижнего края, камера чуть сверху
      const vw = W * u;
      const vh = H * u;
      camera.left = -vw / 2;
      camera.right = vw / 2;
      camera.top = vh / 2;
      camera.bottom = -vh / 2;
      const cy = GROUND - 0.18 + vh / 2;
      camera.position.set(0, cy + 10 * Math.tan(0.16), 10);
      camera.lookAt(0, cy, 0);
      camera.updateProjectionMatrix();

      // шаг решётки — постоянная доля утки (~59 ячеек на её ширину, как в окне на пол-экрана): на любом экране
      // утка состоит из того же числа точек того же относительного размера, меняется только масштаб
      cellCss = duckPx / CELLS_PER_DUCK;
      sprites = new GlyphSprites(cellCss * dpr);
      blueSprites = new GlyphSprites(cellCss * dpr, "blue");
      textSprites = new GlyphSprites(cellCss * TEXT_CELL * dpr, "amber", true);
      textBlue = new GlyphSprites(cellCss * TEXT_CELL * dpr, "blue", true);
      // знаки растений — с толщиной линий в постоянной доле шага, чтобы не менялись с размером окна
      layerSprites = LAYER_RAMPS.map((r) => new GlyphSprites(cellCss * dpr, r, true));
      duckSprites = DUCK_RAMPS.map((r) => new GlyphSprites(cellCss * dpr, r));
      cells = [];
      const dy = cellCss * 0.866;
      let row = 0;
      for (let y = dy / 2; y < Ht; y += dy, row++) {
        for (let x = row % 2 ? cellCss : cellCss / 2; x < W; x += cellCss) {
          const a = hash(y, x) * Math.PI * 2;
          const d = (0.3 + hash(x + 7, y + 3)) * duckPx * 0.8;
          const px = Math.min(tw - 1, (x * RS) | 0);
          const drow = Math.min(th - 1, (y * RS) | 0);
          const dpx = px - duckX0;
          const inDuck = y < H && dpx >= 0 && dpx < duckTW;
          const i = inDuck ? ((th - 1 - drow) * duckTW + dpx) * 4 : -1;
          const ppx = Math.min(ptw - 1, (x * PS) | 0);
          const prow = Math.min(ph - 1, (y * PS) | 0);
          cells.push({ x, y, h: hash(x, y), h2: hash(x + 31, y + 17), dx: Math.cos(a) * d, dy: Math.sin(a) * d * 0.6, delay: hash(x + 3, y + 11) * 0.45, j: (prow * ptw + ppx) * 4, i, axis: 0, zone: 0 });
        }
      }
      duckCells = cells.filter((cl) => cl.i >= 0);
      lastPlant = -1;
      buildText();
      field.layout(duckPx, cellCss, dpr);
      return true;
    };

    // --- поза и поведение ---
    const ptr = { x: 0, y: 0, tx: 0, ty: 0 }; // курсор, -1..1 от центра окна
    const pose: DuckPose = { x: 0, facing: FACING, step: 0.25, walk: 0, stretch: 0, headYaw: 0, headPitch: 0, jaw: 0, flap: 0, time: 0 };
    const goal = { facing: FACING, headYaw: 0, headPitch: 0, stretch: 0.15, headRate: 9 };
    let flapUntil = 0;
    let nextTurn = FIRST_LOOK + 2.0 + rand(4, 7); // раз в 5–9 с утка переминается и разворачивается зеркально
    let turnUntil = 0;
    let quack: Quack | null = null;
    let quackT = -9;
    let introDone = false;
    let quacked = false;
    let plan: { until: number; next: () => void } | null = null;
    let nextPlay = FIRST_LOOK + 2.0 + rand(4, 6); // раз в 6–10 с утка играет с узлами
    let shakeFrom = 0;
    let shakeUntil = 0;
    const sparks: Spark[] = [];
    const v3 = new THREE.Vector3();
    const t0 = performance.now();
    let last = t0;
    const clock = () => (performance.now() - t0) / 1000;

    const toScreen = (v: THREE.Vector3) => {
      v.project(camera);
      return { x: ((v.x + 1) / 2) * W, y: ((1 - v.y) / 2) * H };
    };
    /** Мировой поворот, который смотрит в сторону экрана `side` на 3/4 к зрителю */
    const lookWorld = (side: 1 | -1, spread = 0) => (side > 0 ? RIGHT : LEFT) + spread;
    const setLook = (worldYaw: number) => {
      goal.headYaw = Math.max(-2.3, Math.min(2.3, worldYaw - goal.facing));
    };

    const say = (now: number) => {
      const tip = toScreen(rig.beakTip(v3));
      const dir: 1 | -1 = Math.cos(pose.facing + pose.headYaw) >= 0 ? 1 : -1;
      quack = { t: now, x: tip.x, y: tip.y, dir };
      quackT = now;
      for (const p of textPts) {
        // при распаде точки уходят от центра слова
        const len = Math.hypot(p.ox, p.oy * 2) || 1;
        const sp = rand(20, 90);
        p.vx = (p.ox / len) * sp + rand(-15, 15);
        p.vy = ((p.oy * 2) / len) * sp * 0.6 + rand(-15, 10);
      }
    };

    // поведение после вступления: короткие «дела», как у настоящей утки
    // игры с узлами: клюнуть ближний — отлетит; потрясти головой — вздрогнут; поводить головой — задевает и отодвигает
    const play = () => {
      const now = clock();
      nextPlay = now + rand(6, 10);
      const side: 1 | -1 = Math.random() < 0.5 ? 1 : -1;
      const r = Math.random();
      if (r < 0.4) {
        // клевок: повернуться к узлу, чуть вытянуться и резко ткнуть клювом вверх-вперёд
        goal.headRate = 9;
        setLook(lookWorld(side, 0.15 * side));
        goal.headPitch = 0.12;
        goal.stretch = 0.7;
        plan = {
          until: now + 0.65,
          next: () => {
            goal.headPitch = 0.6;
            goal.stretch = 1;
            plan = {
              until: clock() + 0.16,
              next: () => {
                const tip = toScreen(rig.beakTip(v3));
                field.peck(tip.x, tip.y);
                goal.headPitch = 0;
                goal.stretch = 0.15;
                plan = { until: clock() + 1.1, next: nextAction };
              },
            };
          },
        };
        return;
      }
      if (r < 0.7) {
        // встряхнуть головой: узлы рядом вздрогнут и отойдут
        goal.headRate = 9;
        goal.headYaw = 0;
        goal.headPitch = 0.08;
        goal.stretch = 0.6;
        plan = {
          until: now + 0.4,
          next: () => {
            shakeFrom = clock();
            shakeUntil = shakeFrom + 0.75;
            field.shake(shakeFrom, 1.0);
            plan = {
              until: shakeUntil + 0.2,
              next: () => {
                goal.stretch = 0.15;
                plan = { until: clock() + 1, next: nextAction };
              },
            };
          },
        };
        return;
      }
      // поводить головой из стороны в сторону, задевая узлы
      field.sweep(now, 2.4);
      goal.headRate = 5;
      goal.stretch = 0.9;
      goal.headPitch = 0.2;
      setLook(lookWorld(side, 0.2 * side));
      plan = {
        until: now + 1.1,
        next: () => {
          setLook(lookWorld(side > 0 ? -1 : 1, -0.2 * side));
          plan = {
            until: clock() + 1.2,
            next: () => {
              goal.stretch = 0.15;
              goal.headPitch = 0;
              plan = { until: clock() + 0.8, next: nextAction };
            },
          };
        },
      };
    };

    const nextAction = () => {
      if (clock() > nextPlay && pose.stretch < 0.6) {
        play();
        return;
      }
      const r = Math.random();
      const side: 1 | -1 = Math.cos(goal.facing) >= 0 ? 1 : -1;
      goal.stretch = 0.15;
      if (r < 0.3) {
        // постоять спокойно, голова прямо
        goal.headRate = 7;
        goal.headYaw = 0;
        goal.headPitch = 0;
        plan = { until: clock() + rand(2, 3.5), next: nextAction };
        return;
      }
      if (r < 0.85) {
        // поворот головы (иногда два подряд) и пауза
        const n = Math.random() < 0.35 ? 2 : 1;
        let k = 0;
        const look = () => {
          k++;
          goal.headRate = 9;
          setLook(lookWorld(Math.random() < 0.5 ? 1 : -1, rand(-0.35, 0.35)));
          goal.headPitch = rand(-0.08, 0.15);
          plan = { until: clock() + (k < n ? rand(0.9, 1.4) : rand(1.6, 3)), next: () => (k < n ? look() : nextAction()) };
        };
        look();
        return;
      }
      // вытянуться на лапах и взмахнуть крыльями, иногда сказать «кря»
      goal.stretch = 1;
      goal.headPitch = 0.15;
      setLook(lookWorld(side, rand(-0.3, 0.3)));
      flapUntil = clock() + 0.8;
      plan = {
        until: clock() + 1.4,
        next: () => {
          if (Math.random() < 0.4) say(clock());
          goal.stretch = 0.15;
          plan = { until: clock() + 0.9, next: nextAction };
        },
      };
    };

    quackRef.current = () => {
      if (reduced) return;
      const now = clock();
      if (now - quackT < 0.6) return;
      goal.stretch = 0.8;
      say(now);
      if (introDone) plan = { until: now + 1.2, next: nextAction };
    };

    const update = (now: number, dt: number) => {
      if (!introDone) {
        if (now > FIRST_LOOK) {
          goal.stretch = 1;
          goal.headRate = 10;
          setLook(LEFT + 0.25); // голова влево, к зрителю
          if (!quacked && now > FIRST_LOOK + 0.45) {
            quacked = true;
            say(now);
          }
          if (now > FIRST_LOOK + 2.0) {
            introDone = true;
            goal.stretch = 0.15;
            goal.headYaw = 0;
            plan = { until: now + 1, next: nextAction };
          }
        }
      } else {
        if (now > nextTurn && pose.stretch < 0.5) {
          // разворот на месте: та же поза, только зеркально (через сторону зрителя), лапы перебирают
          goal.facing = -Math.PI - goal.facing;
          goal.headYaw = 0;
          goal.headRate = 8;
          turnUntil = now + 1.1;
          nextTurn = now + rand(5, 9);
          if (plan) plan.until = Math.max(plan.until, turnUntil + 0.2);
        }
        if (plan && now > plan.until) {
          const p = plan;
          plan = null;
          p.next();
        }
      }

      pose.facing = approach(pose.facing, goal.facing, 3.2, dt);
      // на развороте лапы только переступают на месте: маленький шаг почти без выноса вперёд
      pose.walk = approach(pose.walk, now < turnUntil ? 0.2 : 0, 7, dt);
      if (pose.walk > 0.02) pose.step = (pose.step + dt / 0.36) % 1;
      pose.headYaw = approach(pose.headYaw, goal.headYaw, goal.headRate, dt);
      if (now < shakeUntil) {
        // встряхивание: быстрые повороты головы туда-сюда, затухают к концу
        const k = (now - shakeFrom) / (shakeUntil - shakeFrom);
        pose.headYaw = goal.headYaw + Math.sin(k * Math.PI * 7) * 0.55 * Math.sin(k * Math.PI);
      }
      pose.headPitch = approach(pose.headPitch, goal.headPitch, 8, dt);
      pose.stretch = approach(pose.stretch, goal.stretch, 4.5, dt);
      pose.flap = approach(pose.flap, now < flapUntil ? 1 : 0, 10, dt);
      const q = now - quackT;
      pose.jaw = q < 0.45 ? Math.sin((q / 0.45) * Math.PI) : 0;
      pose.time = now;
    };

    let awayCleared = false;
    const draw = (now: number, dt: number) => {
      if (!sprites) return;
      const scroller = scrollRef.current;
      // закреплённый режим: собранность первого экрана ведёт контроллер сцен; иначе — по прокрутке
      const away = scroller?.dataset.pinned === "true" ? 1 - storyStore.forms[0] : scroller ? smoothstep(0.04, 0.55, scroller.scrollTop / Math.max(1, scroller.clientHeight)) : 0;
      // сцена полностью рассыпалась и погасла: один раз стираем холсты и больше ничего не считаем и не рисуем
      if (away > 0.995) {
        if (!awayCleared) {
          awayCleared = true;
          for (const [cv, cx] of [
            [canvas, ctx],
            [back, bctx],
            [front, fctx],
            [wback, wbctx],
            [wfront, wfctx],
            [nodesCv, nctx],
          ] as const) {
            cx.setTransform(1, 0, 0, 1, 0, 0);
            cx.clearRect(0, 0, cv.width, cv.height);
          }
          for (const g of glowCtx) g?.clearRect(0, 0, canvas.width, canvas.height);
          lastPlant = -1; // при возвращении растения нарисуются заново за один кадр
          hit.style.pointerEvents = "none";
        }
        return;
      }
      awayCleared = false;
      hit.style.pointerEvents = away > 0.3 ? "none" : "";
      if (!reduced) update(now, dt);
      rig.apply(pose);

      if (!readPending) {
        renderer.setRenderTarget(target);
        renderer.clear();
        renderer.render(rig.scene, camera);
        renderer.setRenderTarget(null);
        if (dt === 0) {
          // статичная отрисовка (reduced motion, ресайз): читаем сразу
          renderer.readRenderTargetPixels(target, duckX0, 0, duckTW, th, pixels);
        } else {
          readPending = true;
          const gen = readGen;
          const buf = pixBack;
          renderer
            .readRenderTargetPixelsAsync(target, duckX0, 0, duckTW, th, buf)
            .then(() => {
              if (gen !== readGen) return; // раскладка сменилась, буфер устарел
              pixBack = pixels;
              pixels = buf;
            })
            .catch(() => {})
            .finally(() => {
              if (gen === readGen) readPending = false;
            });
        }
      }

      // где утка на экране: туда же ставим невидимую кнопку
      const c = toScreen(v3.set(pose.x, 0.2, 0));
      hit.style.left = `${c.x - duckPx * 0.375}px`;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(1, 0, 0, 1, -duckOff * dpr, 0); // дальше рисуем в координатах сцены
      // растения: пока растут — каждый кадр, потом поэтапно (см. ниже)
      const growing = !reduced && now - GROW_START < GROW_TIME + 0.1;
      const plantDue = dt === 0 || growing || lastPlant < 0;
      // сцена вокруг утки: камыши позади, цветы впереди; вырастает и расцветает при заходе
      const grow = reduced ? 1 : clamp01((now - GROW_START) / GROW_TIME);
      const sc = scroller ? scroller.scrollTop / Math.max(1, scroller.clientHeight) : 0;
      const k = H / REF_H; // CSS px на единицу сцены
      const sw = W / k; // ширина сцены в единицах
      const ox0 = c.x - W / 2;
      ptr.x = approach(ptr.x, ptr.tx, 3, dt);
      ptr.y = approach(ptr.y, ptr.ty, 3, dt);
      // параллакс: за курсором и при прокрутке ближний план смещается сильнее, дальний — слабее и в другую сторону
      const par = [
        { x: -ptr.x * duckPx * 0.07, y: -ptr.y * duckPx * 0.02 - sc * H * 0.03 },
        { x: -ptr.x * duckPx * 0.04, y: -ptr.y * duckPx * 0.012 - sc * H * 0.08 },
        { x: -ptr.x * duckPx * 0.015, y: -ptr.y * duckPx * 0.005 - sc * H * 0.12 },
        { x: ptr.x * duckPx * 0.15, y: ptr.y * duckPx * 0.04 - sc * H * 0.35 },
      ];
      const footY = toScreen(v3.set(pose.x, GROUND, 0)).y / k;
      // свободная полоса по центру: ≈2,9 видимой ширины утки (утка анфас ≈ 0,6 duckPx); растений столько же, они плотнее
      const gapHalf = (duckPx * 0.6 * 2.875) / 2 / k;
      const drawLayer = (li: number) => {
        const L = layers[li];
        L.px = null;
        const lc = L.ctx;
        if (!lc || grow <= 0) return;
        lc.setTransform(1, 0, 0, 1, 0, 0);
        lc.clearRect(0, 0, ptw, ph);
        lc.setTransform(PS * k, 0, 0, PS * k, (ox0 + par[li].x) * PS, par[li].y * PS);
        if (li === 0) {
          // дальний план островков: мелкие камыши и цветы у горизонта, тянутся к центру (там их гасит fade)
          const horizon = REF_H * 0.9;
          lc.save();
          lc.translate(0, horizon - REF_H * 0.5);
          lc.scale(0.5, 0.5);
          drawReeds(lc, sw / 0.5, REF_H, grow, now, { side: "left", seed: 61, reach: 0.47, lilies: false });
          drawReeds(lc, sw / 0.5, REF_H, grow, now, { side: "right", seed: 67, reach: 0.47, lilies: false });
          lc.restore();
          lc.save();
          lc.translate(0, horizon + REF_H * 0.04 - REF_H * 0.62);
          lc.scale(0.62, 0.62);
          drawFlowers(lc, sw / 0.62, REF_H, grow, now, { side: "left", seed: 71, reach: 0.45 });
          drawFlowers(lc, sw / 0.62, REF_H, grow, now, { side: "right", seed: 73, reach: 0.45 });
          lc.restore();
        } else if (li === 1) {
          // кувшинки: рассеяны по глубине воды между ближним краем островка и горизонтом
          drawPads(lc, sw, REF_H * 1.12, REF_H * 0.9, 0.44, grow, now, { side: "left", seed: 81 });
          drawPads(lc, sw, REF_H * 1.12, REF_H * 0.9, 0.44, grow, now, { side: "right", seed: 83 });
        } else if (li === 2) {
          // камыши и рогоз — за цветами; корни опущены до низа экрана, верхушки на прежнем месте
          const RT = Ht / k;
          const reachR = (sw / 2 - gapHalf) / sw;
          drawReeds(lc, sw, REF_H, grow, now, { side: "left", seed: 7, extend: RT - REF_H, reach: reachR, lilies: false });
          drawReeds(lc, sw, REF_H, grow, now, { side: "right", seed: 23, extend: RT - REF_H, reach: reachR, lilies: false });
          drawShore(lc, sw, footY, grow, now, duckPx / k); // берег под лапами утки
        } else {
          // ближний план: цветы на прежнем месте, их стебли продлены вниз до края экрана
          const RT = Ht / k;
          const s = 1.25;
          const top = REF_H - REF_H * s + REF_H * 0.14; // где стоит куст (как было)
          const bw = (sw * 1.1) / s;
          const ext = Math.max(0, (RT - top) / s - REF_H); // сколько добавить стеблю, чтобы корни дошли до низа
          const reachF = (sw / 2 - gapHalf + sw * 0.05) / (bw * s);
          lc.save();
          lc.translate(-sw * 0.05, top);
          lc.scale(s, s);
          // ближний план чуть гуще к краям экрана и реже к центру
          drawFlowers(lc, bw, REF_H, grow, now, { side: "left", seed: 11, extend: ext, reach: reachF, crowd: 1.15 });
          drawFlowers(lc, bw, REF_H, grow, now, { side: "right", seed: 29, extend: ext, reach: reachF, crowd: 1.15 });
          lc.restore();
          // нижний ряд: такие же цветы того же размера, другая расстановка. Стоит всегда на одном расстоянии под
          // верхним (чуть заходит на него) — композиция одна на любом экране, к низу тянутся только стебли.
          // Ниже — ближе к центру: свободной полосы у нижнего ряда ≈1,5 утки вместо 2,5
          const lowTop = top + REF_H * 0.5 * s;
          const extLow = Math.max(0, (RT - lowTop) / s - REF_H);
          const gapLow = (duckPx * 0.6 * 1.725) / 2 / k;
          const reachLow = (sw / 2 - gapLow + sw * 0.05) / (bw * s);
          lc.save();
          lc.translate(-sw * 0.05, lowTop);
          lc.scale(s, s);
          // нижний ряд — к нижним углам гуще всего: на цветок больше и сильнее сдвиг к краю
          drawFlowers(lc, bw, REF_H, grow, now, { side: "left", seed: 41, extend: extLow, reach: reachLow, crowd: 1.35, count: 7 });
          drawFlowers(lc, bw, REF_H, grow, now, { side: "right", seed: 43, extend: extLow, reach: reachLow, crowd: 1.35, count: 7 });
          lc.restore();
        }
        L.px = lc.getImageData(0, 0, ptw, ph).data;
      };

      const duckVis = duckPx * 0.6; // видимая ширина утки анфас
      if (!zoneReady) {
        zoneReady = true;
        const zy = c.y - duckPx * 0.2;
        const zh = Math.max(Ht, window.innerHeight) * 0.55;
        for (const cl of cells) {
          cl.axis = smoothstep(duckVis * 0.6, duckVis * 1.6, Math.abs(cl.x - c.x));
          const ex = (cl.x - c.x) / (duckPx * 1.4);
          const ey = (cl.y - zy) / zh;
          cl.zone = smoothstep(0.82, 1.1, Math.sqrt(ex * ex + ey * ey));
        }
      }
      let tgtB = bctx;
      let tgtF = fctx;
      const plantCell = (li: number, cell: Cell, j: number, flick: number) => {
        const data = layers[li].px;
        if (!data || data[j + 3] < 90) return false;
        const st = LAYER_STYLE[li];
        // дальние планы у центра прозрачнеют: от ~0,6 до ~1,6 ширины утки от оси полностью исчезают к утке
        const axis = st.fade ? cell.axis : 1;
        // овал вокруг утки (от заголовка до низа экрана, ≈1,4 утки в каждую сторону): внутри все планы тусклее
        // и прозрачнее, за его краем яркость быстро нарастает. Глубина сохраняется: внутри передний план
        // остаётся заметнее камышей, а камыши — заметнее дальнего плана
        const zone = cell.zone;
        const floor = li === 3 ? 0.42 : li === 2 ? 0.3 : 0.22;
        const inZone = floor + (1 - floor) * zone;
        const fade = axis * inZone;
        if (fade <= 0.02) return false;
        const marked = data[j + 1] - data[j] > 60; // рябь у берега (метка в зелёном канале)
        if (marked && cell.h > (data[j] / 255) * 1.5) return true;
        const pl = PLANT_L[data[j]] * st.dim * (0.7 + 0.3 * inZone) + flick;
        const pg = pickGlyph(pl, cell.h);
        if (!pg) return true;
        const set = marked && blueSprites ? blueSprites : layerSprites[st.ramp];
        if (!set) return true;
        const spread = away * away;
        const pc = li === 3 ? tgtF : tgtB; // цветы над уткой, всё остальное под ней
        const a = pg.alpha * st.alpha * fade * (1 - away);
        if (a < 0.06) return true; // почти невидимые точки не рисуем
        pc.globalAlpha = a;
        set.draw(pc, pg.shape, pg.bucket, ((cell.x + cell.dx * spread * 2) * dpr) | 0, ((cell.y + (cell.dy - duckPx * 0.3) * spread) * dpr) | 0);
        return true;
      };
      // растения: по глубине — цветы → камыши → кувшинки → дальний план (утка на своём холсте между ними)
      const plantPass = (from: number, to: number) => {
        for (let n = from; n < to; n++) {
          const cell = cells[n];
          const flick = reduced ? 0 : 0.05 * Math.sin(now * 1.3 + cell.h * 40) + (cell.h2 - 0.5) * 0.1;
          if (plantCell(3, cell, cell.j, flick)) continue;
          if (!plantCell(2, cell, cell.j, flick) && !plantCell(1, cell, cell.j, flick)) plantCell(0, cell, cell.j, flick);
        }
      };
      if (plantDue) {
        // пока растут (и при статичной отрисовке) — всё за один кадр прямо в видимые холсты
        lastPlant = now;
        stage = 0;
        for (let li = 0; li < 4; li++) drawLayer(li);
        bctx.clearRect(0, 0, back.width, back.height);
        fctx.clearRect(0, 0, front.width, front.height);
        plantPass(0, cells.length);
      } else {
        // выросшие растения: обновление растянуто на 4 кадра (~15 раз в секунду), чтобы не было тяжёлого кадра.
        // 0 и 1 — планы, 2 и 3 — половины расстановки точек в рабочие холсты; в конце подменяем видимую картинку
        const half = cells.length >> 1;
        // векторные слои перерисовываются по очереди: дальние планы в чётных циклах, ближние в нечётных (качание медленное,
        // этого не видно), а расстановка точек — каждый цикл; так тяжёлый этап не сваливается в один и тот же кадр
        if (stage === 0) {
          if ((cycle & 1) === 0) {
            drawLayer(0);
            drawLayer(1);
          }
        } else if (stage === 1) {
          if ((cycle & 1) === 1) {
            drawLayer(2);
            drawLayer(3);
          }
        } else {
          tgtB = wbctx;
          tgtF = wfctx;
          if (stage === 2) {
            wbctx.clearRect(0, 0, wback.width, wback.height);
            wfctx.clearRect(0, 0, wfront.width, wfront.height);
            plantPass(0, half);
          } else {
            plantPass(half, cells.length);
            // кадр готов в рабочих холстах: показываем их, а прежние видимые становятся рабочими (без копирования картинки)
            wback.style.visibility = "visible";
            wfront.style.visibility = "visible";
            back.style.visibility = "hidden";
            front.style.visibility = "hidden";
            [back, wback] = [wback, back];
            [front, wfront] = [wfront, front];
            [bctx, wbctx] = [wbctx, bctx];
            [fctx, wfctx] = [wfctx, fctx];
            lastPlant = now;
          }
        }
        stage = (stage + 1) % 4;
        if (stage === 0) cycle++;
      }

      const step = Math.max(1, Math.round(cellCss * RS * 1.2));
      const rowB = duckTW * 4;
      const nbr = [-step * 4, step * 4, -step * rowB, step * rowB, (-step - step * duckTW) * 4, (step - step * duckTW) * 4, (-step + step * duckTW) * 4, (step + step * duckTW) * 4];
      const partAt = (q: number) => {
        const r = pixels[q];
        const gq = pixels[q + 1];
        const bq = pixels[q + 2];
        const mq = Math.max(r, gq, bq);
        const R = r > mq * 0.5;
        const G = gq > mq * 0.5;
        const B = bq > mq * 0.5;
        return R && G && B ? 0 : G && !R && !B ? 1 : R && G ? 2 : R && B ? 4 : R ? 3 : G && B ? 6 : 5;
      };
      // утка: каждый кадр, только по ячейкам в её области; часть утки — по набору ярких каналов служебного цвета
      for (const cell of duckCells) {
        const i = cell.i;
        if (pixels[i + 3] < 120) continue;
        const flick = reduced ? 0 : 0.05 * Math.sin(now * 1.3 + cell.h * 40) + (cell.h2 - 0.5) * 0.1;
        const m = Math.max(pixels[i], pixels[i + 1], pixels[i + 2]);
        const part = partAt(i);
        const set = duckSprites[part] ?? sprites;
        // мягкий переход на границе частей: соседние точки другой части подмешивают свой цвет (до половины)
        let other = -1;
        let otherN = 0;
        for (const o of nbr) {
          const q = i + o;
          if (q < 0 || q >= pixels.length || pixels[q + 3] < 120) continue;
          const pq = partAt(q);
          if (pq === part) continue;
          if (other < 0 || pq === other) {
            other = pq;
            otherN++;
          }
        }
        const mix = other >= 0 ? Math.min(0.5, otherN / nbr.length) : 0;
        const l = Math.pow(m / 255, 0.8) + flick;
        const g = pickGlyph(l, cell.h);
        if (!g) continue;
        const p = reduced ? 1 : easeOut((now - cell.delay) / (ASSEMBLE - 0.45));
        const spread = Math.max(1 - p, away * away);
        const x = (cell.x + cell.dx * spread * (1 + away)) * dpr;
        const y = (cell.y + (cell.dy - duckPx * 0.3 * away) * spread) * dpr;
        const alpha = g.alpha * Math.min(1, p * 1.4) * (1 - away);
        if (alpha <= 0.01) continue;
        ctx.globalAlpha = alpha;
        set.draw(ctx, p < 0.92 ? "dot" : g.shape, g.bucket, x, y, p < 0.92 ? 0.6 : 1);
        if (mix > 0.04) {
          // поверх — та же точка в цвете соседней части с долей mix: получается смесь цветов
          ctx.globalAlpha = alpha * mix;
          (duckSprites[other] ?? sprites).draw(ctx, p < 0.92 ? "dot" : g.shape, g.bucket, x, y, p < 0.92 ? 0.6 : 1);
        }
      }

      // «Quack!» облаком точек рядом с клювом: точки сгущаются из дымки (середина букв первой), слово чуть отплывает
      // от утки, всё время от него расходятся мягкие голубые частицы, в конце слово распадается
      if (quack && blueSprites) {
        const t = now - quack.t;
        const HOLD = 1.3;
        const END = 2.3;
        if (t > END) quack = null;
        else {
          const drift = easeOut(t / 1.8);
          const cx = quack.x + quack.dir * (textHalf + 12 + duckPx * 0.08 * drift);
          const cy = quack.y - fontPx * 0.75 - 8 * drift;
          const tilt = quack.dir * -0.08;
          const cs = Math.cos(tilt);
          const sn = Math.sin(tilt);
          const place = (p: TextPt) => ({ x: cx + p.ox * cs - p.oy * sn, y: cy + p.ox * sn + p.oy * cs });

          // голубые частицы рождаются на самой надписи и расходятся от неё
          if (t > 0.08 && t < HOLD + 0.25 && textPts.length) {
            const n = Math.round(dt * 150 + Math.random());
            for (let i = 0; i < n; i++) {
              const p = textPts[(Math.random() * textPts.length) | 0];
              const at = place(p);
              const len = Math.hypot(p.ox, p.oy * 2) || 1;
              const sp = rand(14, 55);
              const glyph = pickGlyph(rand(0.45, 0.9), Math.random());
              if (!glyph) continue;
              sparks.push({
                x: at.x,
                y: at.y,
                vx: (p.ox / len) * sp + rand(-10, 10),
                vy: ((p.oy * 2) / len) * sp * 0.7 + rand(-14, 6),
                age: 0,
                life: rand(0.8, 1.6),
                shape: glyph.shape,
                bucket: glyph.bucket,
                scale: 1,
              });
            }
          }

          for (const p of textPts) {
            const f = easeOut((t - p.delay) / 0.42);
            if (f <= 0) continue;
            const at = place(p);
            let x = at.x + p.sx * (1 - f);
            let y = at.y + p.sy * (1 - f);
            let alpha = (0.35 + 0.65 * p.m) * Math.min(1, f * 1.6);
            let blue = false;
            if (t > HOLD) {
              const k = t - HOLD;
              x += p.vx * k;
              y += p.vy * k;
              alpha *= 1 - k / (END - HOLD);
              blue = p.blue && k > 0.12;
            }
            const twinkle = 0.85 + 0.15 * Math.sin(now * 8 + p.ox * 0.7 + p.oy);
            ctx.globalAlpha = clamp01(alpha * twinkle) * (1 - away);
            (blue ? textBlue : textSprites)?.draw(ctx, p.shape, p.bucket, x * dpr, y * dpr);
          }
        }
      }

      // голубые частицы
      if (blueSprites) {
        for (let i = sparks.length - 1; i >= 0; i--) {
          const s = sparks[i];
          s.age += dt;
          if (s.age > s.life) {
            sparks.splice(i, 1);
            continue;
          }
          s.vx *= Math.exp(-1.1 * dt);
          s.vy *= Math.exp(-1.1 * dt);
          s.x += s.vx * dt;
          s.y += s.vy * dt;
          const life = s.age / s.life;
          ctx.globalAlpha = Math.min(1, life * 6) * (1 - life) * 0.85 * (1 - away);
          textBlue?.draw(ctx, s.shape, s.bucket, s.x * dpr, s.y * dpr, s.scale);
        }
      }
      ctx.globalAlpha = 1;

      // узлы графа над уткой: физика (курсор, голова, клевки) и отрисовка на своём холсте
      {
        const head = toScreen(rig.headCenter(v3));
        const anchor = { x: c.x, y: head.y + duckPx * 0.05 };
        field.step(now, reduced ? 0 : dt, anchor, head, ptrPx);
        nctx.setTransform(1, 0, 0, 1, 0, 0);
        nctx.clearRect(0, 0, nodesCv.width, nodesCv.height);
        field.draw(nctx, dpr, duckOff, -nodesTop, now, away, reduced);
      }
      // ореол утки — гибрид: узкий (2px) — копия кадра, каждая точка светится своим цветом; широкий (9px) — тот же
      // силуэт, залитый одним общим тёплым цветом, он склеивает утку в одно целое. Размытие и прозрачность — в CSS
      for (const gc of glowCtx) {
        if (!gc) continue;
        gc.clearRect(0, 0, canvas.width, canvas.height);
        gc.drawImage(canvas, 0, 0);
        if (gc === farCtx) {
          gc.globalCompositeOperation = "source-in";
          gc.fillStyle = "rgb(237,129,52)"; // общий ореол — тёплый янтарный, утка светится на фоне растений
          gc.fillRect(0, 0, canvas.width, canvas.height);
          gc.globalCompositeOperation = "source-over";
        }
      }
    };

    let raf = 0;
    // Слабое устройство: кадр сцены (JS, без работы видеокарты) дольше ~11 мс — не успевает за 60 к/с вместе с остальным.
    // Тогда рисуем через кадр (30 к/с; время шага `dt` настоящее, скорость движения не меняется), а вернёмся к полной
    // частоте, когда кадр станет короче ~8 мс. Первые кадры (раскладка, спрайты) в среднее не идут
    let cost = 0;
    let warm = 0;
    let half = false;
    let skip = false;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (half) {
        skip = !skip;
        if (skip) return;
      }
      const t = performance.now();
      const dt = Math.min(0.05, (t - last) / 1000);
      last = t;
      draw((t - t0) / 1000, dt);
      if (++warm > 30) {
        cost = cost * 0.9 + (performance.now() - t) * 0.1;
        half = half ? cost > 8 : cost > 11;
      }
    };
    let visible = true;
    const sync = () => {
      const run = visible && !document.hidden && !reduced;
      if (run && !raf) {
        last = performance.now();
        raf = requestAnimationFrame(tick);
      }
      if (!run && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    };

    const ro = new ResizeObserver(() => {
      if (layout() && !raf) draw(9, 0);
    });
    ro.observe(wrap);
    const onResize = () => {
      if (layout() && !raf) draw(9, 0);
    };
    window.addEventListener("resize", onResize); // высота окна меняет, докуда тянется луг
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      sync();
    });
    io.observe(wrap);
    const onVis = () => sync();
    const onMotion = () => {
      reduced = mq.matches;
      sync();
      if (reduced) draw(9, 0);
    };
    const onScroll = () => {
      if (!raf) draw(9, 0);
    };
    const onPointer = (e: PointerEvent) => {
      if (reduced) return;
      ptr.tx = (e.clientX / window.innerWidth) * 2 - 1;
      ptr.ty = (e.clientY / window.innerHeight) * 2 - 1;
      const wr = wrap.getBoundingClientRect();
      ptrPx = { x: e.clientX - wr.left, y: e.clientY - wr.top };
    };
    window.addEventListener("pointermove", onPointer, { passive: true });
    const onLeave = () => (ptrPx = null);
    document.documentElement.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVis);
    mq.addEventListener("change", onMotion);
    const scroller = scrollRef.current;
    scroller?.addEventListener("scroll", onScroll, { passive: true });

    if (reduced) pose.stretch = 0.3;
    if (layout()) draw(reduced ? 9 : 0, 0);
    sync();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("resize", onResize);
      io.disconnect();
      window.removeEventListener("pointermove", onPointer);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMotion);
      scroller?.removeEventListener("scroll", onScroll);
      rig.dispose();
      target.dispose();
      renderer.dispose();
    };
  }, [scrollRef]);

  return (
    <div ref={wrapRef} className="relative w-full">
      <canvas
        ref={backRef}
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 block w-full [filter:drop-shadow(0_0_2px_rgba(255,232,200,0.38))_drop-shadow(0_0_9px_rgba(255,200,150,0.18))]"
      />
      <canvas
        ref={backBRef}
        aria-hidden
        className="pointer-events-none invisible absolute inset-x-0 top-0 block w-full [filter:drop-shadow(0_0_2px_rgba(255,232,200,0.38))_drop-shadow(0_0_9px_rgba(255,200,150,0.18))]"
      />
      <canvas ref={glowFarRef} aria-hidden className="pointer-events-none absolute top-0 block opacity-50 [filter:blur(12px)]" />
      <canvas ref={glowNearRef} aria-hidden className="pointer-events-none absolute top-0 block opacity-60 [filter:blur(2px)]" />
      <canvas ref={canvasRef} aria-hidden className="pointer-events-none absolute top-0 block" />
      <canvas ref={nodesRef} aria-hidden className="pointer-events-none absolute block" />
      <canvas
        ref={frontRef}
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 block w-full [filter:drop-shadow(0_0_2px_rgba(255,232,200,0.38))_drop-shadow(0_0_9px_rgba(255,200,150,0.18))]"
      />
      <canvas
        ref={frontBRef}
        aria-hidden
        className="pointer-events-none invisible absolute inset-x-0 top-0 block w-full [filter:drop-shadow(0_0_2px_rgba(255,232,200,0.38))_drop-shadow(0_0_9px_rgba(255,200,150,0.18))]"
      />
      {/* утку можно тыкнуть: она ещё раз скажет «Quack!»; кнопка ездит за уткой */}
      <button
        ref={hitRef}
        type="button"
        aria-label={label}
        onClick={() => quackRef.current()}
        className="absolute bottom-0 left-1/2 cursor-pointer rounded-pill outline-none focus-visible:ring-2 focus-visible:ring-[#ff8a2a]/60"
      />
    </div>
  );
}
