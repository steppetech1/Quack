"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { Duck } from "../duck";
import { createAuthField } from "./auth-field";

const ASSEMBLE_MS = 2600;

/**
 * Фон экрана входа: утка из глифов, графы по бокам и плавающие ноды. На весь экран, как на лендинге.
 * `blind` — ученик вводит пароль: утка закрывает глаза повязкой. Без WebGL остаётся плоская утка.
 */
export function AuthScene({ blind, className }: { blind: boolean; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const blindRef = useRef(blind);
  // Redraws the frame once when the loop is not running (reduced motion, hidden tab)
  const poke = useRef<() => void>(() => {});

  useEffect(() => {
    blindRef.current = blind;
    poke.current();
  }, [blind]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let field: ReturnType<typeof createAuthField>;
    try {
      field = createAuthField(canvas);
    } catch {
      canvas.dataset.flat = ""; // shows the flat duck below, no state needed
      return;
    }

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;
    let visible = true;
    let raf = 0;
    const pointer = { x: 0, y: 0, active: false };
    const t0 = performance.now();

    const draw = () => {
      const sec = reduced ? 1 : (performance.now() - t0) / 1000;
      const p = reduced ? 1 : Math.min(1, Math.max(0, (performance.now() - t0 - 200) / ASSEMBLE_MS));
      field.render(sec, {
        pointerX: pointer.x,
        pointerY: pointer.y,
        pointerActive: pointer.active && !reduced,
        disperse: Math.pow(1 - p, 3),
        blind: blindRef.current,
        instant: reduced,
      });
    };
    const tick = () => {
      raf = requestAnimationFrame(tick);
      draw();
    };
    const sync = () => {
      const run = visible && !document.hidden && !reduced;
      if (run && !raf) raf = requestAnimationFrame(tick);
      if (!run && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    };
    poke.current = () => {
      if (!raf) draw();
    };

    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      field.resize(r.width, r.height, window.devicePixelRatio);
      if (!raf) draw();
    });
    ro.observe(canvas);
    const io = new IntersectionObserver((entries) => {
      visible = entries[entries.length - 1]?.isIntersecting ?? true;
      sync();
    });
    io.observe(canvas);

    const onMove = (e: PointerEvent) => {
      pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
      pointer.y = 1 - (e.clientY / window.innerHeight) * 2;
      pointer.active = true;
    };
    const onLeave = () => {
      pointer.active = false;
    };
    const onVis = () => sync();
    const onMotion = () => {
      reduced = mq.matches;
      sync();
      if (reduced) draw();
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVis);
    mq.addEventListener("change", onMotion);

    const r = canvas.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1) field.resize(r.width, r.height, window.devicePixelRatio);
    draw();
    sync();

    return () => {
      poke.current = () => {};
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMotion);
      field.dispose();
    };
  }, []);

  return (
    <div aria-hidden className={cn("pointer-events-none", className)}>
      <canvas ref={canvasRef} className="peer absolute inset-0 block size-full" />
      <div className="absolute inset-0 hidden place-items-center peer-data-flat:grid">
        <Duck className="w-40 text-[#ff8a2a]" />
      </div>
    </div>
  );
}
