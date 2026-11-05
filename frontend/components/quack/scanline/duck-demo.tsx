"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Button } from "@/components/ui/button";
import { createDuck, type Duck } from "./duck-3d";
import type { Scanline, ScanlineOptions } from "./scanline";
import { ScanlineCanvas } from "./scanline-canvas";

/** Демо 3D-утки: штриховка и «сырой» рендер для отладки света и анимации (служебная страница, без i18n) */

type Clock = { speed: number; paused: boolean };

/** Время утки с паузой и скоростью: копим фазу, а не умножаем t (иначе при смене скорости поза прыгает) */
function useDuckClock(clock: Clock) {
  const state = useRef({ last: 0, phase: 0 });
  const clockRef = useRef(clock);
  useEffect(() => {
    clockRef.current = clock;
  }, [clock]);
  // колбэк стабилен: цикл рендера не пересоздаётся при правке контролов
  return useCallback((t: number) => {
    const st = state.current;
    const dt = Math.max(0, t - st.last);
    st.last = t;
    if (!clockRef.current.paused) st.phase += dt * clockRef.current.speed;
    return st.phase;
  }, []);
}

const STROKE_OPTIONS: Partial<ScanlineOptions> = { step: 3, glitch: 0.3 };

function StrokeView({ clock }: { clock: Clock }) {
  const duckRef = useRef<Duck | null>(null);
  const time = useDuckClock(clock);

  useEffect(
    () => () => {
      duckRef.current?.dispose();
      duckRef.current = null;
    },
    [],
  );

  const onFrame = useCallback(
    (s: Scanline, t: number) => {
      const duck = (duckRef.current ??= createDuck()); // лениво: эффект canvas срабатывает раньше родителя
      const { width, height } = s.getSize();
      duck.update(time(t), width / height);
      s.renderScene(duck.scene, duck.camera, t);
    },
    [time],
  );

  return <ScanlineCanvas className="block size-full" options={STROKE_OPTIONS} onFrame={onFrame} />;
}

/** Та же сцена обычным рендером, на чёрном */
function RawView({ clock }: { clock: Clock }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const time = useDuckClock(clock);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    } catch {
      return;
    }
    renderer.setClearColor(0x000000, 1);
    const duck = createDuck();
    let raf = 0;
    const t0 = performance.now();

    const fit = () => {
      const r = canvas.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(r.width, r.height, false);
    };
    const ro = new ResizeObserver(fit);
    ro.observe(canvas);
    fit();

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const r = canvas.getBoundingClientRect();
      duck.update(time((performance.now() - t0) / 1000), r.width / Math.max(1, r.height));
      renderer.render(duck.scene, duck.camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      duck.dispose();
      renderer.dispose();
    };
  }, [time]);

  return <canvas ref={canvasRef} className="block size-full" aria-hidden />;
}

export function DuckDemo() {
  const [raw, setRaw] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [paused, setPaused] = useState(false);
  // объект часов меняется только при правке контролов
  const clock = useMemo<Clock>(() => ({ speed, paused }), [speed, paused]);

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col gap-4 overflow-auto p-5">
      <header>
        <h1 className="display-caps text-2xl text-paper">Duck</h1>
        <p className="mt-1 text-small text-paper-dim">3D duck from primitives, walking in place. Strokes or raw render.</p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => setRaw((v) => !v)} aria-pressed={raw}>
          {raw ? "Show strokes" : "Show raw render"}
        </Button>
        <Button variant="outline" size="sm" onClick={() => setPaused((v) => !v)} aria-pressed={paused}>
          {paused ? "Play" : "Pause"}
        </Button>
        <label className="flex items-center gap-2 text-small text-paper">
          <span>speed</span>
          <input
            type="range"
            min={0.1}
            max={2}
            step={0.1}
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
          />
          <span className="num w-8 text-paper-dim">{speed.toFixed(1)}</span>
        </label>
      </div>

      <div className="min-h-[360px] flex-1 bg-black">
        {raw ? <RawView clock={clock} /> : <StrokeView clock={clock} />}
      </div>
    </div>
  );
}
