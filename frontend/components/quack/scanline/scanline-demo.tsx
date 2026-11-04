"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { PageShell } from "@/components/quack/page-parts";
import { DEFAULT_SCANLINE_OPTIONS, type Scanline, type ScanlineOptions } from "./scanline";
import { ScanlineCanvas } from "./scanline-canvas";

/** Демо движка штриховки: 3D-фигура и 2D-canvas через один и тот же эффект (служебная страница, без i18n) */

/** Сцена: узел-«бублик» с сильным боковым светом, чтобы была светотень */
function makeScene() {
  const scene = new THREE.Scene();
  const geometry = new THREE.TorusKnotGeometry(1, 0.34, 220, 36);
  const material = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.45, metalness: 0.1 });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);
  scene.add(new THREE.AmbientLight(0xffffff, 0.12));
  const key = new THREE.DirectionalLight(0xffffff, 3.2);
  key.position.set(-4, 2.5, 2);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xffffff, 0.8);
  rim.position.set(4, -1, -2);
  scene.add(rim);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
  camera.position.set(0, 0, 6);
  return { scene, camera, mesh, geometry, material };
}

const RANGES: { key: keyof Omit<ScanlineOptions, "color">; label: string; min: number; max: number; step: number }[] = [
  { key: "step", label: "step (px)", min: 2, max: 8, step: 1 },
  { key: "lineWidth", label: "lineWidth", min: 0.1, max: 1, step: 0.05 },
  { key: "threshold", label: "threshold", min: 0, max: 0.6, step: 0.01 },
  { key: "rowSize", label: "rowSize (px)", min: 1, max: 8, step: 1 },
  { key: "glitch", label: "glitch", min: 0, max: 1, step: 0.05 },
];

export function ScanlineDemo() {
  const [options, setOptions] = useState<ScanlineOptions>(DEFAULT_SCANLINE_OPTIONS);
  const threeRef = useRef<ReturnType<typeof makeScene> | null>(null);
  const blob = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    return () => {
      threeRef.current?.geometry.dispose();
      threeRef.current?.material.dispose();
      threeRef.current = null;
    };
  }, []);

  // 2D-источник: светлое пятно, плывущее по чёрному
  useEffect(() => {
    const c = document.createElement("canvas");
    c.width = 480;
    c.height = 360;
    blob.current = c;
    return () => {
      blob.current = null;
    };
  }, []);

  const draw3d = useCallback(
    (s: Scanline, t: number) => {
      // сцена создаётся лениво: эффект дочернего canvas срабатывает раньше родительского
      const three = (threeRef.current ??= makeScene());
      const { width, height } = s.getSize();
      three.camera.aspect = width / height;
      three.camera.updateProjectionMatrix();
      three.mesh.rotation.set(t * 0.25, t * 0.4, 0);
      s.renderScene(three.scene, three.camera, t);
    },
    [],
  );

  const draw2d = useCallback((s: Scanline, t: number) => {
    const c = blob.current;
    const ctx = c?.getContext("2d");
    if (!c || !ctx) return;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, c.width, c.height);
    const x = c.width * (0.5 + 0.28 * Math.sin(t * 0.8));
    const y = c.height * (0.5 + 0.25 * Math.sin(t * 1.3 + 1));
    const g = ctx.createRadialGradient(x, y, 4, x, y, 120);
    g.addColorStop(0, "#fff");
    g.addColorStop(0.5, "rgba(255,255,255,0.45)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, c.width, c.height);
    s.renderSource(c, t);
  }, []);

  return (
    <PageShell width={960}>
      <h1 className="mt-4 text-xl font-semibold text-paper">Scanline</h1>
      <p className="mt-1 text-small text-paper-dim">3D scene and 2D canvas through the same stroke shader.</p>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <figure className="rounded-card bg-cloth-deep p-2">
          <ScanlineCanvas className="block aspect-[4/3] w-full" options={options} onFrame={draw3d} />
          <figcaption className="px-1 pt-2 text-small text-paper-dim">renderScene — TorusKnot</figcaption>
        </figure>
        <figure className="rounded-card bg-cloth-deep p-2">
          <ScanlineCanvas className="block aspect-[4/3] w-full" options={options} onFrame={draw2d} />
          <figcaption className="px-1 pt-2 text-small text-paper-dim">renderSource — 2D canvas</figcaption>
        </figure>
      </div>

      <div className="mt-6 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {RANGES.map((r) => (
          <label key={r.key} className="flex items-center gap-3 text-small text-paper">
            <span className="w-28 shrink-0">{r.label}</span>
            <input
              type="range"
              className="min-w-0 flex-1"
              min={r.min}
              max={r.max}
              step={r.step}
              value={options[r.key]}
              onChange={(e) => setOptions((o) => ({ ...o, [r.key]: Number(e.target.value) }))}
            />
            <span className="num w-10 text-right text-paper-dim">{options[r.key]}</span>
          </label>
        ))}
        <label className="flex items-center gap-3 text-small text-paper">
          <span className="w-28 shrink-0">color</span>
          <input type="color" value={options.color} onChange={(e) => setOptions((o) => ({ ...o, color: e.target.value }))} />
          <span className="num text-paper-dim">{options.color}</span>
        </label>
      </div>
    </PageShell>
  );
}
