"use client";

import { useEffect, useRef } from "react";
import { NodeField, type NodeLayout } from "../landing/hero-duck/nodes";

/**
 * Живые сети узлов по бокам поля ввода. Это тот же `NodeField`, что рисует узлы над уткой на стартовой
 * сцене лендинга (landing/hero-duck/nodes.ts): облака мерцающих точек, узлы парят на пружинах, связи мягко
 * прогибаются под курсором, цвета палитры «ночной пруд». Здесь только раскладка, размеры и цикл кадров.
 *
 * Размер сети фиксированный (в CSS px) и от экрана не зависит: на узком экране сеть не ужимается, а уходит
 * за край и обрезается, на широком — занимает место от поля до края.
 */

// индекс в PALETTE = [VIOLET, MINT, SKY]
type Spec = { x: number; y: number; r: number; color: number };
type Edge = [number, number];

// Область сети REGION_W × REGION_H px. Координаты узлов — в px этой области. Узлы крупные (радиус облака
// 40–125 px) и разбросаны от внешнего края области до внутреннего, у которого оставлено место полю.
// Пара узлов без связей разбросана среди остальных.
const REGION_W = 340;
const REGION_H = 640;
// координаты ниже нарисованы на сетке шириной 400 px; по x они сжимаются до REGION_W, радиусы узлов остаются как есть
const GRID_W = 400;

// левая: внутренняя (к полю) сторона — справа
const LEFT: Spec[] = [
  { x: 300, y: 60, r: 52, color: 1 },
  { x: 120, y: 40, r: 40, color: 0 },
  { x: 210, y: 135, r: 66, color: 2 },
  { x: 55, y: 175, r: 48, color: 0 },
  { x: 355, y: 215, r: 42, color: 1 }, // изолированный
  { x: 170, y: 305, r: 118, color: 2 }, // хаб
  { x: 40, y: 335, r: 56, color: 1 },
  { x: 335, y: 345, r: 50, color: 0 },
  { x: 90, y: 475, r: 64, color: 0 },
  { x: 255, y: 470, r: 80, color: 1 },
  { x: 365, y: 545, r: 44, color: 2 }, // изолированный
  { x: 150, y: 595, r: 52, color: 0 },
];
const LEFT_EDGES: Edge[] = [
  [0, 1], [0, 2], [1, 3], [2, 3], [2, 5], [3, 5],
  [5, 6], [5, 7], [5, 8], [5, 9], [6, 8], [8, 9], [8, 11], [9, 11], [7, 9],
];

// правая: внутренняя (к полю) сторона — слева; другой формы, не зеркало левой
const RIGHT: Spec[] = [
  { x: 100, y: 45, r: 44, color: 2 },
  { x: 265, y: 75, r: 60, color: 0 },
  { x: 355, y: 195, r: 48, color: 1 },
  { x: 150, y: 175, r: 76, color: 2 },
  { x: 40, y: 265, r: 42, color: 0 }, // изолированный
  { x: 235, y: 325, r: 124, color: 1 }, // хаб
  { x: 70, y: 405, r: 50, color: 2 },
  { x: 365, y: 410, r: 56, color: 1 },
  { x: 140, y: 485, r: 66, color: 0 },
  { x: 300, y: 505, r: 70, color: 2 },
  { x: 35, y: 585, r: 40, color: 1 }, // изолированный
  { x: 215, y: 600, r: 54, color: 0 },
];
const RIGHT_EDGES: Edge[] = [
  [0, 3], [1, 3], [1, 2], [2, 5], [3, 5],
  [5, 6], [5, 7], [5, 8], [5, 9], [8, 9], [6, 8], [7, 9], [8, 11], [9, 11],
];

// 1 единица раскладки NodeField = BASE px: радиусы и смещения в нём считаются в долях «ширины утки»
const BASE = 100;
// частицы: клетка 6 px (шаг точки ~2.7 px) и до 320 точек на облако — крупный узел остаётся плотным, а не рыхлым
const CELL = 6;
const MAX_DOTS = 320;

function toLayout(nodes: Spec[], edges: Edge[]): NodeLayout {
  const cx = REGION_W / 2;
  const cy = REGION_H / 2;
  return {
    near: nodes.map((n) => [((n.x * REGION_W) / GRID_W - cx) / BASE, (n.y - cy) / BASE, n.r / BASE, n.color]),
    links: edges,
    far: [],
  };
}

/** Одна сеть фиксированного размера; положение задаёт `style`/`className` снаружи (см. `NodeChains`) */
export function NodeChain({ side, className, style }: { side: "left" | "right"; className?: string; style?: React.CSSProperties }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const box = boxRef.current;
    const cv = canvasRef.current;
    const ctx = cv?.getContext("2d");
    if (!box || !cv || !ctx) return;

    const layout = toLayout(side === "left" ? LEFT : RIGHT, side === "left" ? LEFT_EDGES : RIGHT_EDGES);
    const field = new NodeField(layout);
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const FAR = { x: -1e5, y: -1e5 }; // «голова утки» здесь не нужна: уводим её за экран

    // Холст больше области на поля под свечение (оно крупнее узла), иначе его обрезает рамка холста.
    // anchor — центр области в координатах холста
    let anchor = { x: 0, y: 0 };
    let dpr = 1;
    let ptr: { x: number; y: number } | null = null;
    let raf = 0;
    let visible = true;
    let last = 0;
    let reduced = mq.matches;

    const frame = (now: number, dt: number) => {
      field.step(now, reduced ? 0 : dt, anchor, FAR, ptr);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);
      field.draw(ctx, dpr, 0, 0, now, 0, reduced);
    };

    dpr = Math.min(2, window.devicePixelRatio || 1);
    {
      const D = BASE;
      let padL = 0;
      let padR = 0;
      let padT = 0;
      let padB = 0;
      for (const [hx, hy, r] of layout.near) {
        const g = r * D * 2.2 + D * 0.08;
        padL = Math.max(padL, g - (REGION_W / 2 + hx * D));
        padR = Math.max(padR, g - (REGION_W / 2 - hx * D));
        padT = Math.max(padT, g - (REGION_H / 2 + hy * D));
        padB = Math.max(padB, g - (REGION_H / 2 - hy * D));
      }
      padL = Math.ceil(padL);
      padR = Math.ceil(padR);
      padT = Math.ceil(padT);
      padB = Math.ceil(padB);
      const cvW = REGION_W + padL + padR;
      const cvH = REGION_H + padT + padB;
      anchor = { x: padL + REGION_W / 2, y: padT + REGION_H / 2 };
      cv.width = Math.round(cvW * dpr);
      cv.height = Math.round(cvH * dpr);
      cv.style.width = `${cvW}px`;
      cv.style.height = `${cvH}px`;
      cv.style.left = `${-padL}px`;
      cv.style.top = `${-padT}px`;
      field.layout(D, CELL, dpr, MAX_DOTS);
      frame(performance.now() / 1000, 0); // сразу кадр, чтобы не мигало пустым холстом
    }

    const tick = (t: number) => {
      raf = 0;
      const now = t / 1000;
      const dt = last ? Math.min(0.05, now - last) : 0;
      last = now;
      frame(now, dt);
      if (visible && !reduced && !document.hidden) raf = requestAnimationFrame(tick);
    };
    const start = () => {
      if (!raf && visible && !reduced && !document.hidden) {
        last = 0;
        raf = requestAnimationFrame(tick);
      }
    };

    // курсор — в координатах холста; вне холста (с запасом на изгиб связей) связи ему не поддаются
    const onMove = (e: PointerEvent) => {
      const r = cv.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      const m = 40;
      ptr = x > -m && y > -m && x < r.width + m && y < r.height + m ? { x, y } : null;
    };
    const onLeave = () => {
      ptr = null;
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);

    // не крутим цикл, когда область вне экрана или вкладка скрыта
    const io = new IntersectionObserver(([e]) => {
      visible = !!e?.isIntersecting;
      start();
    });
    io.observe(box);
    const onVis = () => start();
    document.addEventListener("visibilitychange", onVis);
    const onMotion = () => {
      reduced = mq.matches;
      frame(performance.now() / 1000, 0);
      start();
    };
    mq.addEventListener("change", onMotion);

    start();
    return () => {
      if (raf) cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMotion);
    };
  }, [side]);

  return (
    <div aria-hidden className={className} style={style}>
      <div ref={boxRef} className="relative" style={{ width: REGION_W, height: REGION_H }}>
        <canvas ref={canvasRef} className="pointer-events-none absolute" />
      </div>
    </div>
  );
}

// Расстояние от центра страницы до внутренней границы сети: полширины поля (210 px) и зазор 12 px;
// на узком экране сети заходят глубже и частично лежат за полем (оно непрозрачное), чтобы с каждого края оставалось видно не меньше 150 px сети.
const INNER = "min(222px, 50% - 150px)";

/**
 * Слой с двумя сетями на всю область страницы: левая стоит слева от поля и уходит к краю экрана, правая — справа.
 * Родитель должен быть `relative`. Выступающее за край обрезается, клики слой не перехватывает.
 */
export function NodeChains() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <NodeChain side="left" className="absolute top-1/2 -translate-y-1/2" style={{ right: `calc(50% + ${INNER})` }} />
      <NodeChain side="right" className="absolute top-1/2 -translate-y-1/2" style={{ left: `calc(50% + ${INNER})` }} />
    </div>
  );
}
