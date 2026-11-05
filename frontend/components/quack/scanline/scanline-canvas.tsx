"use client";

import { useEffect, useRef } from "react";
import { createScanline, type Scanline, type ScanlineOptions } from "./scanline";

type Props = {
  className?: string;
  options?: Partial<ScanlineOptions>;
  /** Вызывается каждый кадр: тут вызывай s.renderScene(...) или s.renderSource(...) */
  onFrame: (s: Scanline, timeSec: number) => void;
};

/** Canvas со штриховкой: сам создаёт движок, следит за размером и видимостью, крутит rAF */
export function ScanlineCanvas({ className, options, onFrame }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<Scanline | null>(null);
  const onFrameRef = useRef(onFrame);
  const optionsRef = useRef(options);
  const redrawRef = useRef<() => void>(() => {});

  // актуальные колбэк и опции без пересоздания движка
  useEffect(() => {
    onFrameRef.current = onFrame;
  }, [onFrame]);

  useEffect(() => {
    optionsRef.current = options;
    engineRef.current?.setOptions(options ?? {});
    redrawRef.current(); // в режиме одного кадра перерисовать с новыми опциями
  }, [options]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let engine: Scanline;
    try {
      engine = createScanline(canvas, optionsRef.current);
    } catch {
      return; // нет WebGL — ничего не рисуем
    }
    engineRef.current = engine;

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;
    let visible = true;
    let raf = 0;
    const t0 = performance.now();

    const draw = () => {
      const t = reduced ? 0 : (performance.now() - t0) / 1000;
      onFrameRef.current(engine, t);
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
    redrawRef.current = () => {
      if (!raf) draw(); // цикл стоит — рисуем один кадр вручную
    };

    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      engine.resize(r.width, r.height, window.devicePixelRatio);
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
    if (r.width >= 1 && r.height >= 1) engine.resize(r.width, r.height, window.devicePixelRatio);
    draw();
    sync();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMotion);
      redrawRef.current = () => {};
      engineRef.current = null;
      engine.dispose();
    };
  }, []);

  return <canvas ref={canvasRef} className={className} aria-hidden />;
}
