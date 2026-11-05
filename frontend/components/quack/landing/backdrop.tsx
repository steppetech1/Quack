"use client";

import { useEffect, useRef, type RefObject } from "react";
import { createGlyphField } from "./glyph-field";

const ASSEMBLE_MS = 2600;

/**
 * Фон лендинга на всю страницу: поле из символов с уткой, графами и нодами. Закреплён на экране, поэтому графы и ноды
 * остаются за любой секцией; утка и тёплое пятно под ней рассыпаются, когда первый экран уезжает вверх.
 */
export function Backdrop({ scrollRef }: { scrollRef: RefObject<HTMLElement | null> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const glowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const glow = glowRef.current;
    const root = scrollRef.current;
    if (!canvas || !glow || !root) return;

    let field: ReturnType<typeof createGlyphField>;
    try {
      field = createGlyphField(canvas);
    } catch {
      return; // нет WebGL — остаётся тёмный фон
    }

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;
    let raf = 0;
    const pointer = { x: 0, y: 0, active: false };
    const t0 = performance.now();

    const draw = () => {
      const sec = reduced ? 1 : (performance.now() - t0) / 1000;
      const p = reduced ? 1 : Math.min(1, Math.max(0, (performance.now() - t0 - 200) / ASSEMBLE_MS));
      const intro = Math.pow(1 - p, 3); // easeOutCubic: быстро собирается, долго «доезжает»
      const scroll = root.scrollTop / Math.max(1, root.clientHeight);
      const away = 1; // утку теперь рисует первый экран (HeroDuck), здесь остаются только графы и ноды
      glow.style.opacity = "0";
      // на первом экране фон не спорит с уткой: графы проявляются, когда он уезжает вверх
      canvas.style.opacity = String(Math.min(1, Math.max(0, (scroll - 0.25) / 0.5)));
      field.render(sec, {
        pointerX: pointer.x,
        pointerY: pointer.y,
        pointerActive: pointer.active && !reduced,
        disperse: intro,
        away,
        scroll,
      });
    };
    const tick = () => {
      raf = requestAnimationFrame(tick);
      draw();
    };
    const sync = () => {
      const run = !document.hidden && !reduced;
      if (run && !raf) raf = requestAnimationFrame(tick);
      if (!run && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    };

    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      field.resize(r.width, r.height, window.devicePixelRatio);
      if (!raf) draw();
    });
    ro.observe(canvas);

    const onMove = (e: PointerEvent) => {
      pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
      pointer.y = 1 - (e.clientY / window.innerHeight) * 2;
      pointer.active = true;
    };
    const onLeave = () => {
      pointer.active = false;
    };
    const onVis = () => sync();
    const onScroll = () => {
      if (!raf) draw(); // без анимации (reduced motion) кадр обновляется только от прокрутки
    };
    const onMotion = () => {
      reduced = mq.matches;
      sync();
      if (reduced) draw();
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVis);
    root.addEventListener("scroll", onScroll, { passive: true });
    mq.addEventListener("change", onMotion);

    const r = canvas.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1) field.resize(r.width, r.height, window.devicePixelRatio);
    draw();
    sync();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      root.removeEventListener("scroll", onScroll);
      mq.removeEventListener("change", onMotion);
      field.dispose();
    };
  }, [scrollRef]);

  return (
    <>
      {/* тёплое пятно света под уткой */}
      <div
        ref={glowRef}
        aria-hidden
        className="pointer-events-none fixed inset-0 -z-10 bg-[radial-gradient(70%_55%_at_50%_88%,#2a1407_0%,#120a05_55%,#080504_100%)]"
      />
      <canvas ref={canvasRef} className="pointer-events-none fixed inset-0 -z-10 block size-full" aria-hidden />
    </>
  );
}
