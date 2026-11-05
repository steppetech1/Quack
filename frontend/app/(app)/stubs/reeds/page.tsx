"use client";

import { useEffect, useRef, useState } from "react";
import { drawReeds } from "@/components/quack/scanline/reeds";

// Демо камышей: обе стороны в обычном 2D-канвасе на чёрном
const GROW_MS = 1600;

export default function ReedsStubPage() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const growRef = useRef(1);
  const animRef = useRef<{ start: number } | null>(null);
  const [grow, setGrow] = useState(1);
  const [ms, setMs] = useState(0);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let w = 0;
    let h = 0;
    const resize = () => {
      const r = wrap.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, Math.round(r.width));
      h = Math.max(1, Math.round(r.height));
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    let raf = 0;
    let acc = 0;
    let frames = 0;
    let lastReport = performance.now();
    const loop = (now: number) => {
      // анимация «replay»: ease-out за ~1.6s
      const a = animRef.current;
      if (a) {
        const k = Math.min(1, (now - a.start) / GROW_MS);
        growRef.current = 1 - (1 - k) ** 3;
        setGrow(growRef.current);
        if (k >= 1) animRef.current = null;
      }
      const t0 = performance.now();
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, w, h);
      drawReeds(ctx, w, h, growRef.current, now / 1000, { side: "left", seed: 7 });
      drawReeds(ctx, w, h, growRef.current, now / 1000, { side: "right", seed: 19 });
      acc += performance.now() - t0;
      frames++;
      if (now - lastReport > 700) {
        setMs(acc / frames);
        acc = 0;
        frames = 0;
        lastReport = now;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  const replay = () => {
    growRef.current = 0;
    animRef.current = { start: performance.now() };
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <div ref={wrapRef} className="relative min-h-[360px] flex-1 overflow-hidden bg-black">
        <canvas ref={canvasRef} className="absolute inset-0 size-full" />
      </div>
      <div className="flex items-center gap-4 text-sm text-paper">
        <label className="flex flex-1 items-center gap-3">
          <span className="w-12 font-mono tabular-nums">{grow.toFixed(2)}</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.005}
            value={grow}
            aria-label="grow"
            className="flex-1"
            onChange={(e) => {
              animRef.current = null;
              growRef.current = Number(e.target.value);
              setGrow(growRef.current);
            }}
          />
        </label>
        <button
          type="button"
          onClick={replay}
          className="h-10 rounded-full border border-paper-dim px-5 text-paper hover:bg-white/10"
        >
          replay
        </button>
        <span className="w-24 text-right font-mono text-xs text-paper-dim">{ms.toFixed(2)} ms</span>
      </div>
    </div>
  );
}
