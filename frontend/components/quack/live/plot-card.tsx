"use client";

import { useMemo, useState } from "react";
import { compileExpr, samplePlot } from "@/lib/plot-expr";
import { useT } from "@/lib/i18n/provider";
import { Inline } from "../rich-text";

export type PlotSpec = { function: { latex: string; sympy: string }; variable: string; x_min: number; x_max: number };

const W = 480;
const H = 270;
const PAD = { left: 44, right: 14, top: 14, bottom: 30 };

/** Round-looking tick values between lo and hi (about `count` of them) */
function ticks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / count;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

const fmt = (v: number) => (Math.abs(v) >= 1e4 || (v !== 0 && Math.abs(v) < 1e-3) ? v.toExponential(1) : String(Number(v.toPrecision(4))));

/** A plot of one function the teacher gave (`visualization.plot`): axes, grid, the curve, and a readout under the pointer */
export function PlotCard({ plot }: { plot: PlotSpec }) {
  const tx = useT();
  const [hover, setHover] = useState<number | null>(null);
  const model = useMemo(() => {
    const fn = compileExpr(plot.function.sympy, plot.variable);
    const series = fn ? samplePlot(fn, plot.x_min, plot.x_max) : null;
    return fn && series ? { fn, series } : null;
  }, [plot]);

  if (!model) return <p className="mt-2 text-small text-paper-dim">{tx.live.vizPlotUnsupported}</p>;
  const { fn, series } = model;
  const { yMin, yMax } = series;
  const sx = (x: number) => PAD.left + ((x - plot.x_min) / (plot.x_max - plot.x_min)) * (W - PAD.left - PAD.right);
  const sy = (y: number) => H - PAD.bottom - ((y - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);
  const xs = ticks(plot.x_min, plot.x_max, 6);
  const ys = ticks(yMin, yMax, 5);
  const axisX = yMin <= 0 && yMax >= 0 ? sy(0) : H - PAD.bottom;
  const axisY = plot.x_min <= 0 && plot.x_max >= 0 ? sx(0) : PAD.left;
  const hx = hover === null ? null : plot.x_min + hover * (plot.x_max - plot.x_min);
  const hy = hx === null ? null : fn(hx);

  return (
    <figure className="mt-2">
      <div className="text-paper-dim">
        <span className="caps mr-2">{plot.variable} →</span>
        <Inline text={`$${plot.function.latex}$`} />
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${plot.function.latex}, ${plot.variable} ${fmt(plot.x_min)}…${fmt(plot.x_max)}`}
        className="mt-1 h-auto w-full max-w-[34rem] touch-none select-none rounded-[2px] border border-seam-soft bg-cloth"
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          setHover(Math.min(1, Math.max(0, (px - PAD.left) / (W - PAD.left - PAD.right))));
        }}
        onPointerLeave={() => setHover(null)}
      >
        {xs.map((v) => (
          <g key={`x${v}`}>
            <line x1={sx(v)} x2={sx(v)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--seam-soft)" strokeWidth={1} />
            <text x={sx(v)} y={H - PAD.bottom + 16} textAnchor="middle" fontSize={10} fill="var(--paper-dim)" fontFamily="var(--font-mono, monospace)">
              {fmt(v)}
            </text>
          </g>
        ))}
        {ys.map((v) => (
          <g key={`y${v}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(v)} y2={sy(v)} stroke="var(--seam-soft)" strokeWidth={1} />
            <text x={PAD.left - 6} y={sy(v) + 3} textAnchor="end" fontSize={10} fill="var(--paper-dim)" fontFamily="var(--font-mono, monospace)">
              {fmt(v)}
            </text>
          </g>
        ))}
        <line x1={PAD.left} x2={W - PAD.right} y1={axisX} y2={axisX} stroke="var(--paper-dim)" strokeWidth={1.2} />
        <line x1={axisY} x2={axisY} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--paper-dim)" strokeWidth={1.2} />
        {series.segments.map((seg, i) => (
          <polyline
            key={i}
            points={seg.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ")}
            fill="none"
            stroke="var(--duck)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {hx !== null && hy !== null && Number.isFinite(hy) && hy >= yMin && hy <= yMax && (
          <g>
            <line x1={sx(hx)} x2={sx(hx)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--paper-dim)" strokeDasharray="3 4" />
            <circle cx={sx(hx)} cy={sy(hy)} r={4} fill="var(--duck)" />
            <text x={Math.min(sx(hx) + 8, W - 110)} y={Math.max(sy(hy) - 8, PAD.top + 10)} fontSize={11} fill="var(--paper)" fontFamily="var(--font-mono, monospace)">
              {`${plot.variable} = ${fmt(hx)},  ${fmt(hy)}`}
            </text>
          </g>
        )}
      </svg>
    </figure>
  );
}
