"use client";

import { useEffect, useRef } from "react";
import { createAsciiField, type AsciiField } from "./ascii";

type Props = {
  className?: string;
  /** Вызывается каждый кадр: тут вызывай f.renderSource(...) */
  onFrame: (f: AsciiField, timeSec: number) => void;
};

const FRAME_MS = 33; // символы меняются реже, чем качаются стебли: ~30 кадров в секунду хватает

/** Canvas с символами: сам создаёт движок, следит за размером и видимостью, крутит rAF (по образцу ScanlineCanvas) */
export function AsciiCanvas({ className, onFrame }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const onFrameRef = useRef(onFrame);

  // актуальный колбэк без пересоздания движка
  useEffect(() => {
    onFrameRef.current = onFrame;
  }, [onFrame]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let field: AsciiField;
    try {
      field = createAsciiField(canvas);
    } catch {
      return; // нет 2D-контекста — ничего не рисуем
    }

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;
    let visible = true;
    let raf = 0;
    let lastAt = 0;
    const t0 = performance.now();

    const draw = () => {
      const t = reduced ? 0 : (performance.now() - t0) / 1000;
      onFrameRef.current(field, t);
    };
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - lastAt < FRAME_MS) return;
      lastAt = now;
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

    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      field.resize(r.width, r.height, window.devicePixelRatio);
      draw(); // resize очищает буфер — сразу перерисовываем, чтобы не мигало
    });
    ro.observe(canvas);

    const io = new IntersectionObserver((entries) => {
      visible = entries[entries.length - 1]?.isIntersecting ?? true;
      sync();
    });
    io.observe(canvas);

    const onVis = () => sync();
    const onMotion = () => {
      reduced = mq.matches;
      sync();
      if (reduced) draw();
    };
    document.addEventListener("visibilitychange", onVis);
    mq.addEventListener("change", onMotion);

    const r = canvas.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1) field.resize(r.width, r.height, window.devicePixelRatio);
    draw();
    sync();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMotion);
      field.dispose();
    };
  }, []);

  return <canvas ref={canvasRef} className={className} aria-hidden />;
}
