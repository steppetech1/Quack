import * as THREE from "three";
import { createDuckRig, type DuckPose } from "../hero-duck/duck-rig";
import { DUCK_RAMPS, GlyphSprites, pickGlyph } from "../hero-duck/glyphs";
import { easeOut, hash } from "./scene-utils";

/**
 * Утка для сцен ниже первого экрана: та же 3D-модель, что на первом, рисуется в ту же сетку глифов (точки, звёздочки,
 * рогатки, части утки в своих цветах, мягкие границы между ними, общее свечение). Живёт в своей рамке `box` внутри сцены,
 * собирается из точек и рассыпается по параметру `form` (0 — рассыпана, 1 — собрана).
 * Из 3D читается только рамка и асинхронно: без остановки видеокарты.
 */

const RS = 0.5; // разрешение 3D-рендера относительно CSS px
const DUCK_WORLD_W = 3.4; // ширина утки в мире: по ней считается масштаб

type DCell = { x: number; y: number; h: number; h2: number; dx: number; dy: number; delay: number; i: number };

export type StageLayout = {
  /** Рамка утки в координатах сцены, CSS px */
  box: { x: number; y: number; w: number; h: number };
  /** Ширина утки в CSS px (тело, без раскрытых крыльев) */
  duckPx: number;
  /** Шаг решётки частиц, CSS px */
  cell: number;
  dpr: number;
  /** Мировая точка в центре рамки */
  center: { x: number; y: number };
};

export class DuckStage {
  readonly pose: DuckPose = { x: 0, facing: 0, step: 0.25, walk: 0, stretch: 0.15, headYaw: 0, headPitch: 0, jaw: 0, flap: 0, time: 0 };
  /** Контейнер с холстами утки и её свечения; ставится в сцену на место `box` */
  readonly el: HTMLDivElement;
  ok = false;

  private rig = createDuckRig();
  private renderer: THREE.WebGLRenderer | null = null;
  private camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 60);
  private target = new THREE.WebGLRenderTarget(2, 2);
  private cvMain = document.createElement("canvas");
  private cvNear = document.createElement("canvas");
  private cvFar = document.createElement("canvas");
  private ctx: CanvasRenderingContext2D | null;
  private nearCtx: CanvasRenderingContext2D | null;
  private farCtx: CanvasRenderingContext2D | null;
  private pixels = new Uint8Array(16);
  private pixBack = new Uint8Array(16);
  private readPending = false;
  private readGen = 0;
  private cells: DCell[] = [];
  private sprites: GlyphSprites[] = [];
  private tw = 2;
  private th = 2;
  private L: StageLayout | null = null;
  private v3 = new THREE.Vector3();
  private nbr: number[] = [];
  private step = 1;

  constructor() {
    this.el = document.createElement("div");
    this.el.style.cssText = "position:absolute;pointer-events:none;";
    const style = (cv: HTMLCanvasElement, css: string) => {
      cv.style.cssText = `position:absolute;left:0;top:0;display:block;${css}`;
      this.el.appendChild(cv);
    };
    style(this.cvFar, "opacity:0.5;filter:blur(12px);");
    style(this.cvNear, "opacity:0.6;filter:blur(2px);");
    style(this.cvMain, "");
    this.ctx = this.cvMain.getContext("2d");
    this.nearCtx = this.cvNear.getContext("2d");
    this.farCtx = this.cvFar.getContext("2d");
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: "low-power" });
      this.renderer.setClearColor(0x000000, 0);
      this.ok = true;
    } catch {
      this.ok = false;
    }
  }

  layout(L: StageLayout) {
    if (!this.renderer || !this.ctx) return;
    this.L = L;
    const { box, duckPx, cell, dpr, center } = L;
    this.el.style.left = `${box.x}px`;
    this.el.style.top = `${box.y}px`;
    this.el.style.width = `${box.w}px`;
    this.el.style.height = `${box.h}px`;
    for (const cv of [this.cvMain, this.cvNear, this.cvFar]) {
      cv.width = Math.round(box.w * dpr);
      cv.height = Math.round(box.h * dpr);
      cv.style.width = `${box.w}px`;
      cv.style.height = `${box.h}px`;
    }
    this.tw = Math.max(2, Math.round(box.w * RS));
    this.th = Math.max(2, Math.round(box.h * RS));
    this.target.setSize(this.tw, this.th);
    this.renderer.setSize(this.tw, this.th, false);
    this.pixels = new Uint8Array(this.tw * this.th * 4);
    this.pixBack = new Uint8Array(this.tw * this.th * 4);
    this.readPending = false;
    this.readGen++;

    // кадр: мировая точка center в центре рамки, камера чуть сверху
    const u = DUCK_WORLD_W / duckPx;
    const vw = box.w * u;
    const vh = box.h * u;
    const c = this.camera;
    c.left = -vw / 2;
    c.right = vw / 2;
    c.top = vh / 2;
    c.bottom = -vh / 2;
    c.position.set(center.x, center.y + 10 * Math.tan(0.16), 10);
    c.lookAt(center.x, center.y, 0);
    c.updateProjectionMatrix();

    this.sprites = DUCK_RAMPS.map((r) => new GlyphSprites(cell * dpr, r));
    this.cells = [];
    const dy = cell * 0.866;
    let row = 0;
    for (let y = dy / 2; y < box.h; y += dy, row++) {
      for (let x = row % 2 ? cell : cell / 2; x < box.w; x += cell) {
        const a = hash(y, x) * Math.PI * 2;
        const d = (0.3 + hash(x + 7, y + 3)) * duckPx * 0.8;
        const px = Math.min(this.tw - 1, (x * RS) | 0);
        const drow = Math.min(this.th - 1, (y * RS) | 0);
        this.cells.push({
          x,
          y,
          h: hash(x, y),
          h2: hash(x + 31, y + 17),
          dx: Math.cos(a) * d,
          dy: Math.sin(a) * d * 0.6,
          delay: hash(x + 3, y + 11) * 0.45,
          i: ((this.th - 1 - drow) * this.tw + px) * 4,
        });
      }
    }
    this.step = Math.max(1, Math.round(cell * RS * 1.2));
    const rowB = this.tw * 4;
    const s = this.step;
    this.nbr = [-s * 4, s * 4, -s * rowB, s * rowB, (-s - s * this.tw) * 4, (s - s * this.tw) * 4, (-s + s * this.tw) * 4, (s + s * this.tw) * 4];
  }

  /** Экранная точка (в координатах сцены, CSS px) для мировой */
  private toScene(v: THREE.Vector3) {
    const L = this.L!;
    v.project(this.camera);
    return { x: L.box.x + ((v.x + 1) / 2) * L.box.w, y: L.box.y + ((1 - v.y) / 2) * L.box.h };
  }
  beakTip() {
    return this.toScene(this.rig.beakTip(this.v3));
  }
  headCenter() {
    return this.toScene(this.rig.headCenter(this.v3));
  }

  private partAt(q: number) {
    const px = this.pixels;
    const r = px[q];
    const g = px[q + 1];
    const b = px[q + 2];
    const m = Math.max(r, g, b);
    const R = r > m * 0.5;
    const G = g > m * 0.5;
    const B = b > m * 0.5;
    return R && G && B ? 0 : G && !R && !B ? 1 : R && G ? 2 : R && B ? 4 : R ? 3 : G && B ? 6 : 5;
  }

  /** Один кадр: поза → 3D → глифы. `dt === 0` — статичная отрисовка (читаем сразу). `reduced` — без мерцания */
  render(now: number, dt: number, form: number, reduced = false) {
    const L = this.L;
    const ctx = this.ctx;
    if (!L || !ctx || !this.renderer) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.cvMain.width, this.cvMain.height);
    if (form <= 0.005) {
      this.clearGlow();
      return;
    }
    this.rig.apply(this.pose);
    if (!this.readPending) {
      const r = this.renderer;
      r.setRenderTarget(this.target);
      r.clear();
      r.render(this.rig.scene, this.camera);
      r.setRenderTarget(null);
      if (dt === 0) {
        r.readRenderTargetPixels(this.target, 0, 0, this.tw, this.th, this.pixels);
      } else {
        this.readPending = true;
        const gen = this.readGen;
        const buf = this.pixBack;
        r.readRenderTargetPixelsAsync(this.target, 0, 0, this.tw, this.th, buf)
          .then(() => {
            if (gen !== this.readGen) return;
            this.pixBack = this.pixels;
            this.pixels = buf;
          })
          .catch(() => {})
          .finally(() => {
            if (gen === this.readGen) this.readPending = false;
          });
      }
    }

    const { dpr, duckPx } = L;
    const px = this.pixels;
    const nbr = this.nbr;
    ctx.scale(1, 1);
    for (const cell of this.cells) {
      const i = cell.i;
      if (px[i + 3] < 120) continue;
      const flick = reduced ? 0 : 0.05 * Math.sin(now * 1.3 + cell.h * 40) + (cell.h2 - 0.5) * 0.1;
      const m = Math.max(px[i], px[i + 1], px[i + 2]);
      const part = this.partAt(i);
      const set = this.sprites[part];
      // мягкий переход на границе частей: соседние точки другой части подмешивают свой цвет (до половины)
      let other = -1;
      let otherN = 0;
      for (const o of nbr) {
        const q = i + o;
        if (q < 0 || q >= px.length || px[q + 3] < 120) continue;
        const pq = this.partAt(q);
        if (pq === part) continue;
        if (other < 0 || pq === other) {
          other = pq;
          otherN++;
        }
      }
      const mix = other >= 0 ? Math.min(0.5, otherN / nbr.length) : 0;
      const g = pickGlyph(Math.pow(m / 255, 0.8) + flick, cell.h);
      if (!g) continue;
      // сборка: точка летит из своей случайной стороны в свою ячейку, середина утки раньше краёв
      const p = easeOut((form * 1.5 - cell.delay) / 0.9);
      if (p <= 0) continue;
      const spread = 1 - p;
      const x = (cell.x + cell.dx * spread * 2) * dpr;
      const y = (cell.y + (cell.dy - duckPx * 0.25 * (1 - form)) * spread) * dpr;
      const alpha = g.alpha * Math.min(1, p * 1.4);
      if (alpha <= 0.01) continue;
      const settled = p >= 0.92;
      ctx.globalAlpha = alpha;
      set.draw(ctx, settled ? g.shape : "dot", g.bucket, x, y, settled ? 1 : 0.6);
      if (mix > 0.04) {
        ctx.globalAlpha = alpha * mix;
        this.sprites[other].draw(ctx, settled ? g.shape : "dot", g.bucket, x, y, settled ? 1 : 0.6);
      }
    }
    ctx.globalAlpha = 1;

    // ореол: узкий — копия кадра (каждая точка светится своим цветом), широкий — силуэт одним тёплым цветом; размытие — CSS
    const near = this.nearCtx;
    const far = this.farCtx;
    if (near && far) {
      near.clearRect(0, 0, this.cvNear.width, this.cvNear.height);
      near.drawImage(this.cvMain, 0, 0);
      far.clearRect(0, 0, this.cvFar.width, this.cvFar.height);
      far.drawImage(this.cvMain, 0, 0);
      far.globalCompositeOperation = "source-in";
      far.fillStyle = "rgb(237,129,52)";
      far.fillRect(0, 0, this.cvFar.width, this.cvFar.height);
      far.globalCompositeOperation = "source-over";
    }
  }

  private clearGlow() {
    this.nearCtx?.clearRect(0, 0, this.cvNear.width, this.cvNear.height);
    this.farCtx?.clearRect(0, 0, this.cvFar.width, this.cvFar.height);
  }

  dispose() {
    this.rig.dispose();
    this.target.dispose();
    this.renderer?.dispose();
    this.el.remove();
  }
}
