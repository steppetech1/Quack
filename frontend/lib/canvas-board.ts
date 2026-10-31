import type { Canvas, CanvasBlock } from "@/lib/mock/types";

// ── Элементы, которые студент добавляет на доску ──────────────────────

export type InkColor = "paper" | "review" | "confused" | "solid";
export const INK_VAR: Record<InkColor, string> = {
  paper: "var(--paper)",
  review: "var(--st-review)",
  confused: "var(--st-confused)",
  solid: "var(--st-solid)",
};

export type Pt = [number, number];

export type BoardItem =
  | { id: string; kind: "stroke"; pts: Pt[]; color: InkColor; width: number; marker?: boolean }
  | { id: string; kind: "text"; x: number; y: number; text: string }
  | { id: string; kind: "formula"; x: number; y: number; tex: string }
  | { id: string; kind: "image"; x: number; y: number; w: number; h: number; src: string };

export type Box = { x: number; y: number; w: number; h: number };

export const uid = () => Math.random().toString(36).slice(2, 9);

export function strokeBox(pts: Pt[], pad: number): Box {
  let x0 = Infinity,
    y0 = Infinity,
    x1 = -Infinity,
    y1 = -Infinity;
  for (const [x, y] of pts) {
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x);
    y1 = Math.max(y1, y);
  }
  return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + pad * 2, h: y1 - y0 + pad * 2 };
}

/** Грубый габарит элемента — для «вписать» и перелёта камеры */
export function itemBox(it: BoardItem): Box {
  if (it.kind === "stroke") return strokeBox(it.pts, it.width / 2 + 2);
  if (it.kind === "text") return { x: it.x, y: it.y, w: Math.max(40, it.text.length * 11), h: 30 };
  if (it.kind === "formula") return { x: it.x, y: it.y, w: Math.max(60, it.tex.length * 12), h: 44 };
  return { x: it.x, y: it.y, w: it.w, h: it.h };
}

/** Путь со сглаживанием: квадратичные кривые через середины отрезков */
export function smoothPath(pts: Pt[]): string {
  if (pts.length === 0) return "";
  const [p0] = pts;
  if (pts.length === 1) return `M${p0[0]} ${p0[1]}l0.01 0`;
  if (pts.length === 2) return `M${p0[0]} ${p0[1]}L${pts[1][0]} ${pts[1][1]}`;
  let d = `M${p0[0]} ${p0[1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [x, y] = pts[i];
    const [nx, ny] = pts[i + 1];
    d += `Q${x} ${y} ${(x + nx) / 2} ${(y + ny) / 2}`;
  }
  const last = pts[pts.length - 1];
  return d + `L${last[0]} ${last[1]}`;
}

export function distToSeg(p: Pt, a: Pt, b: Pt) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l = dx * dx + dy * dy;
  const t = l === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

export function strokeHit(pts: Pt[], p: Pt, r: number) {
  if (pts.length === 1) return Math.hypot(pts[0][0] - p[0], pts[0][1] - p[1]) <= r;
  for (let i = 0; i < pts.length - 1; i++) if (distToSeg(p, pts[i], pts[i + 1]) <= r) return true;
  return false;
}

export function moveItem(it: BoardItem, dx: number, dy: number): BoardItem {
  if (it.kind === "stroke") return { ...it, pts: it.pts.map(([x, y]) => [x + dx, y + dy] as Pt) };
  return { ...it, x: it.x + dx, y: it.y + dy };
}

// ── Раскладка моков: каждый блок канваса — рамка на доске ────────────

export const TASK_W = 900;
export const SIDE_W = 440;
export const ROW_H = 76;
export const LINES_TOP = 152;
const GAP_X = 100;
const GAP_Y = 64;

export type Frame = { block: CanvasBlock; x: number; y: number; w: number; h: number };

function frameHeight(b: CanvasBlock): number {
  if (b.kind === "task") return LINES_TOP + b.lines.length * ROW_H + 24;
  if (b.kind === "hint") return 56 + (Math.ceil(b.text.length / 40) + 1) * 26;
  return 56 + b.formulas.length * 92;
}

export function layoutCanvas(canvas: Canvas): Frame[] {
  const frames: Frame[] = [];
  let sideY = 0;
  let sideX = 0;
  canvas.blocks.forEach((b, i) => {
    const h = frameHeight(b);
    if (i === 0) {
      const w = b.kind === "task" ? TASK_W : SIDE_W;
      frames.push({ block: b, x: 0, y: 0, w, h });
      sideX = w + GAP_X;
    } else {
      frames.push({ block: b, x: sideX, y: sideY, w: SIDE_W, h });
      sideY += h + GAP_Y;
    }
  });
  return frames;
}

/** Прямоугольник строки решения в мировых координатах */
export function lineBox(f: Frame, lineIndex: number): Box {
  return { x: f.x, y: f.y + LINES_TOP + lineIndex * ROW_H, w: f.w, h: ROW_H };
}

export function unionBox(boxes: Box[]): Box | null {
  if (!boxes.length) return null;
  const x0 = Math.min(...boxes.map((b) => b.x));
  const y0 = Math.min(...boxes.map((b) => b.y));
  const x1 = Math.max(...boxes.map((b) => b.x + b.w));
  const y1 = Math.max(...boxes.map((b) => b.y + b.h));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export const K_MIN = 0.1;
export const K_MAX = 4;
