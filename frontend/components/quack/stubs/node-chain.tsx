"use client";

import { useEffect, useRef } from "react";
import { drawLink, drawNode, makeDots, nodeSprites } from "../landing/scenes/effects";

/**
 * Декоративная цепочка узлов по бокам пилюли ввода. Рисуется буквально тем же кодом, что узлы на
 * стартовой сцене лендинга (`drawNode`/`drawLink`/`makeDots`/`nodeSprites` из landing/scenes/effects.ts,
 * палитра из landing/hero-duck/nodes.ts) — один статичный кадр на canvas, без цикла анимации.
 */

// индекс в PALETTE = [VIOLET, MINT, SKY]
type Spec = { x: number; y: number; r: number; color: number };
type Edge = [number, number];

// Разветвлённая сеть с крупным узлом-хабом в середине и несколькими ветвями от него;
// пара узлов без связей разбросана среди остальных. Правая раскладка — другой формы
// и других пропорций, не зеркало левой.
const LEFT: Spec[] = [
  { x: 64, y: 22, r: 38, color: 1 },
  { x: 22, y: 54, r: 27, color: 0 },
  { x: 106, y: 48, r: 34, color: 2 },
  { x: 48, y: 93, r: 50, color: 0 },
  { x: 118, y: 99, r: 24, color: 1 },
  { x: 80, y: 141, r: 92, color: 2 },
  { x: 19, y: 150, r: 35, color: 1 },
  { x: 141, y: 147, r: 30, color: 0 },
  { x: 58, y: 198, r: 46, color: 0 },
  { x: 112, y: 208, r: 54, color: 1 },
  { x: 26, y: 246, r: 28, color: 2 },
  { x: 80, y: 272, r: 40, color: 0 },
];
const LEFT_EDGES: Edge[] = [
  [0, 1], [0, 2], [1, 3], [2, 3], [3, 5], [2, 5],
  [5, 6], [5, 7], [5, 8], [5, 9], [6, 8], [8, 9], [8, 11], [9, 11], [7, 9],
];

const RIGHT: Spec[] = [
  { x: 96, y: 16, r: 30, color: 2 },
  { x: 138, y: 51, r: 40, color: 0 },
  { x: 48, y: 58, r: 26, color: 1 },
  { x: 90, y: 90, r: 54, color: 2 },
  { x: 22, y: 109, r: 30, color: 0 },
  { x: 74, y: 144, r: 96, color: 1 },
  { x: 134, y: 138, r: 38, color: 2 },
  { x: 13, y: 163, r: 28, color: 1 },
  { x: 106, y: 192, r: 48, color: 0 },
  { x: 45, y: 208, r: 44, color: 2 },
  { x: 141, y: 227, r: 32, color: 1 },
  { x: 80, y: 269, r: 42, color: 2 },
];
const RIGHT_EDGES: Edge[] = [
  [0, 1], [0, 3], [2, 3], [1, 3], [3, 5], [3, 6],
  [5, 6], [5, 8], [5, 9], [8, 9], [8, 10], [8, 11], [9, 11], [6, 10],
];

// Пропорции сцены (ширина/высота); сеть узлов масштабируется под фактическую ширину контейнера,
// так что цепочка остаётся видимой и резкой на любом экране, а не только там, где хватает места
// под фиксированный размер.
const ASPECT_W = 210;
const ASPECT_H = 400;

export function NodeChain({ side, className }: { side: "left" | "right"; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;

    const nodes = side === "left" ? LEFT : RIGHT;
    const edges = side === "left" ? LEFT_EDGES : RIGHT_EDGES;
    const pad = 30;
    const minX = Math.min(...nodes.map((n) => n.x - n.r)) - pad;
    const maxX = Math.max(...nodes.map((n) => n.x + n.r)) + pad;
    const minY = Math.min(...nodes.map((n) => n.y - n.r)) - pad;
    const maxY = Math.max(...nodes.map((n) => n.y + n.r)) + pad;
    const naturalW = maxX - minX;
    const naturalH = maxY - minY;

    const draw = (cssW: number) => {
      const cssH = (cssW * ASPECT_H) / ASPECT_W;
      const scale = Math.min(cssW / naturalW, cssH / naturalH);
      const ox = -minX * scale + (cssW - naturalW * scale) / 2;
      const oy = -minY * scale + (cssH - naturalH * scale) / 2;

      const dpr = Math.min(2, window.devicePixelRatio || 1);
      cv.width = Math.round(cssW * dpr);
      cv.height = Math.round(cssH * dpr);
      cv.style.width = `${cssW}px`;
      cv.style.height = `${cssH}px`;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);

      const cell = 5 * scale;
      const sprites = nodeSprites(cell * 0.45, dpr);
      const dots = nodes.map((n) => makeDots(n.r * scale, cell * 0.45));

      for (const [a, b] of edges) {
        const A = nodes[a];
        const B = nodes[b];
        const ax = A.x * scale + ox;
        const ay = A.y * scale + oy;
        const bx = B.x * scale + ox;
        const by = B.y * scale + oy;
        drawLink(ctx, dpr, ax, ay, bx, by, (ax + bx) / 2, (ay + by) / 2, A.color, B.color, 1);
      }
      nodes.forEach((n, i) => {
        drawNode(
          ctx,
          sprites,
          dots[i],
          { x: n.x * scale + ox, y: n.y * scale + oy, R: n.r * scale, color: n.color, alpha: 1, hot: 0, form: 1, scatter: { x: 0, y: 0 } },
          0,
          dpr,
          true,
          0,
        );
      });
    };

    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) draw(w);
    });
    ro.observe(cv);
    return () => ro.disconnect();
  }, [side]);

  return <canvas ref={canvasRef} aria-hidden className={className} style={{ aspectRatio: `${ASPECT_W} / ${ASPECT_H}` }} />;
}
