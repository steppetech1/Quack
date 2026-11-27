"use client";

import { Check, RotateCcw, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { Reveal } from "./motion";
import { useLandingStage } from "./stage/landing-context";

function FeatureCell({
  index,
  title,
  short,
  active,
  onActivate,
  className,
  children,
}: {
  index: number;
  title: string;
  short: string;
  active: boolean;
  onActivate: (idx: number) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      onMouseEnter={() => onActivate(index)}
      onFocusCapture={() => onActivate(index)}
      className={cn(
        "flex flex-col justify-between p-5 transition-colors duration-200 sm:p-6",
        active ? "bg-[#110e0b]" : "bg-[#0b0908]",
        className,
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <span
            className={cn(
              "font-mono text-[0.75rem] tabular-nums transition-colors",
              active ? "font-bold text-[#edb966]" : "text-[#736e63]",
            )}
          >
            {String(index + 1).padStart(2, "0")}
          </span>
          <h3 className="font-display text-[1.125rem] font-semibold leading-[1.2] tracking-[-0.02em] text-[#f3eadc]">
            {title}
          </h3>
        </div>
        <span className="text-[0.8125rem] text-[#8d8273]">{short}</span>
      </div>

      <div className="mt-4 flex-1 rounded-[4px] border border-[#f1e4d6]/8 bg-[#090706] p-3.5">
        {children}
      </div>
    </div>
  );
}

/**
 * Module 01 Visual: Slide / PDF Scanner -> Connected Node Lens
 */
function MaterialsMicroScene() {
  const m = useT().landing.features.visuals.materials;
  const [tabIdx, setTabIdx] = useState(1);

  const scanY = tabIdx === 0 ? 42 : tabIdx === 1 ? 82 : 122;
  const nodes = [
    { x: 256, y: 42, col: "#86a9c9", r: 7.5 },
    { x: 294, y: 82, col: "#edb966", r: 9.5 },
    { x: 262, y: 124, col: "#8fc79a", r: 8 },
  ];
  const activeNode = nodes[tabIdx] ?? nodes[1];

  return (
    <div className="flex h-full flex-col justify-between gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="tablist" className="flex items-center gap-4">
          {m.tabs.map((label, idx) => {
            const isCur = idx === tabIdx;
            return (
              <button
                key={label}
                type="button"
                role="tab"
                aria-selected={isCur}
                onMouseEnter={() => setTabIdx(idx)}
                onClick={() => setTabIdx(idx)}
                className={cn(
                  "min-h-7 cursor-pointer border-b pb-0.5 font-mono text-[0.6875rem] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
                  isCur
                    ? "border-[#edb966] font-semibold text-[#edb966]"
                    : "border-transparent text-[#736e63] hover:text-[#c7baa8]",
                )}
              >
                {label}
              </button>
            );
          })}
        </div>

        <span className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] text-[#edb966]">
          <span aria-hidden className="size-1.5 rounded-full bg-[#edb966] motion-safe:animate-pulse" />
          {m.linkedLabel}
        </span>
      </div>

      <svg viewBox="0 0 360 156" className="my-auto h-36 w-full select-none sm:h-40" aria-hidden>
        <rect x="18" y="16" width="128" height="124" rx="5" fill="#13100d" stroke="#f1e4d6" strokeOpacity="0.18" strokeWidth="1.2" />
        {[42, 82, 122].map((y, idx) => {
          const isCur = idx === tabIdx;
          return (
            <g
              key={y}
              onClick={() => setTabIdx(idx)}
              className="cursor-pointer"
            >
              <line
                x1="34"
                y1={y - 6}
                x2={idx === 1 ? "118" : "104"}
                y2={y - 6}
                stroke={isCur ? "#edb966" : "#736e63"}
                strokeOpacity={isCur ? 0.95 : 0.4}
                strokeWidth="2.4"
                strokeLinecap="round"
              />
              <line
                x1="34"
                y1={y + 5}
                x2="88"
                y2={y + 5}
                stroke={isCur ? "#f3eadc" : "#736e63"}
                strokeOpacity={isCur ? 0.65 : 0.25}
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </g>
          );
        })}

        <rect
          x="22"
          y={scanY - 16}
          width="120"
          height="30"
          rx="3"
          fill="#edb966"
          fillOpacity="0.11"
          stroke="#edb966"
          strokeOpacity="0.55"
          strokeWidth="1.1"
          style={{ transition: "y 420ms cubic-bezier(0.16, 1, 0.3, 1)" }}
        />

        <line x1={nodes[0].x} y1={nodes[0].y} x2={nodes[1].x} y2={nodes[1].y} stroke="#f1e4d6" strokeOpacity="0.22" strokeWidth="1.2" />
        <line x1={nodes[1].x} y1={nodes[1].y} x2={nodes[2].x} y2={nodes[2].y} stroke="#f1e4d6" strokeOpacity="0.22" strokeWidth="1.2" />
        <line x1={nodes[0].x} y1={nodes[0].y} x2={nodes[2].x} y2={nodes[2].y} stroke="#f1e4d6" strokeOpacity="0.14" strokeWidth="1" strokeDasharray="3 4" />

        <path
          d={`M 142 ${scanY} C 195 ${scanY}, 210 ${activeNode.y}, ${activeNode.x} ${activeNode.y}`}
          fill="none"
          stroke={activeNode.col}
          strokeWidth="1.8"
          strokeDasharray="4 4"
        />

        {nodes.map((n, idx) => {
          const isCur = idx === tabIdx;
          return (
            <g
              key={idx}
              transform={`translate(${n.x}, ${n.y})`}
              onClick={() => setTabIdx(idx)}
              className="cursor-pointer"
            >
              {isCur && (
                <>
                  <circle r={n.r + 14} fill={n.col} fillOpacity="0.14" />
                  <circle r={n.r + 8} fill="none" stroke={n.col} strokeOpacity="0.55" strokeWidth="1.4" />
                </>
              )}
              <circle r={isCur ? n.r : n.r * 0.78} fill={n.col} fillOpacity={isCur ? 1 : 0.45} />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/**
 * Module 02 Visual: Interactive Trap Radar & Shield
 */
function TrapRadarMicroScene() {
  const tr = useT().landing.features.visuals.traps;
  const [shielded, setShielded] = useState(true);

  return (
    <div className="flex h-full flex-col justify-between gap-3">
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 font-mono text-[0.6875rem] transition-colors",
            shielded ? "text-st-solid" : "text-st-confused",
          )}
        >
          <ShieldCheck aria-hidden className="size-3.5" />
          {shielded ? tr.shieldLabel : tr.catchLabel}
        </span>

        <button
          type="button"
          onClick={() => setShielded((s) => !s)}
          className="inline-flex min-h-7 cursor-pointer items-center gap-1.5 font-mono text-[0.6875rem] text-[#9c9283] transition-colors outline-none hover:text-[#edb966] focus-visible:ring-2 focus-visible:ring-[#edb966]/70"
        >
          <RotateCcw aria-hidden className="size-3" />
          <span>{tr.toggleLabel}</span>
        </button>
      </div>

      <svg
        viewBox="0 0 260 156"
        onClick={() => setShielded((s) => !s)}
        className="my-auto h-36 w-full cursor-pointer select-none sm:h-40"
        aria-hidden
      >
        <g transform="translate(130, 78)">
          <circle r="60" fill="none" stroke="#f1e4d6" strokeOpacity="0.12" strokeWidth="1" strokeDasharray="2 5" />
          <circle
            r="42"
            fill="none"
            stroke={shielded ? "#8fc79a" : "#ef8c7a"}
            strokeOpacity="0.38"
            strokeWidth="1.4"
          />

          <g className="landing-orbit-spin">
            <line x1="0" y1="0" x2="0" y2="-58" stroke="#edb966" strokeOpacity="0.32" strokeWidth="1.2" />
            <circle cx="0" cy="-42" r="3.5" fill="#edb966" />
            <circle cx="38" cy="18" r="2.8" fill="#8fc79a" />
          </g>

          <circle
            r="22"
            fill={shielded ? "#8fc79a" : "#ef8c7a"}
            fillOpacity="0.14"
          />
          <circle
            r="22"
            fill="none"
            stroke={shielded ? "#8fc79a" : "#ef8c7a"}
            strokeWidth="2"
            strokeDasharray={shielded ? "138 138" : "58 138"}
            strokeLinecap="round"
            transform="rotate(-90)"
            style={{ transition: "stroke-dasharray 500ms cubic-bezier(0.16, 1, 0.3, 1), stroke 300ms ease" }}
          />
          <circle r="8" fill={shielded ? "#8fc79a" : "#ef8c7a"} />
        </g>
      </svg>
    </div>
  );
}

/**
 * Module 03 Visual: Dot-grid Canvas Stroke & QR Photo Viewfinder -> Clean Node
 */
function CanvasPhotoMicroScene() {
  const cv = useT().landing.features.visuals.canvas;
  const [scanMode, setScanMode] = useState(false);

  return (
    <div className="flex h-full flex-col justify-between gap-3">
      <div className="flex items-center justify-between gap-2">
        <div role="tablist" className="flex items-center gap-4">
          {[
            { id: false, label: cv.modeDraw },
            { id: true, label: cv.modeScan },
          ].map((m) => {
            const active = scanMode === m.id;
            return (
              <button
                key={String(m.id)}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setScanMode(m.id)}
                className={cn(
                  "min-h-7 cursor-pointer border-b pb-0.5 font-mono text-[0.6875rem] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
                  active
                    ? "border-[#edb966] font-semibold text-[#edb966]"
                    : "border-transparent text-[#736e63] hover:text-[#c7baa8]",
                )}
              >
                {m.label}
              </button>
            );
          })}
        </div>

        <span className="font-mono text-[0.6875rem] text-st-solid">{cv.parsedLabel}</span>
      </div>

      <div
        style={{
          backgroundImage: "radial-gradient(rgba(241, 228, 214, 0.14) 1px, transparent 1px)",
          backgroundSize: "14px 14px",
        }}
        className="relative my-auto flex h-36 w-full items-center justify-center overflow-hidden rounded-[4px] border border-[#f1e4d6]/10 bg-[#0d0b09] sm:h-40"
      >
        <svg viewBox="0 0 260 130" className="size-full select-none" aria-hidden>
          <g
            stroke="#edb966"
            strokeWidth="1.6"
            fill="none"
            style={{ opacity: scanMode ? 0.85 : 0.2, transition: "opacity 300ms ease" }}
          >
            <path d="M 18 32 V 20 H 30" />
            <path d="M 122 20 H 134 V 32" />
            <path d="M 18 98 V 110 H 30" />
            <path d="M 122 110 H 134 V 98" />
          </g>

          <path
            key={String(scanMode)}
            d="M 28 84 Q 52 28, 76 66 T 124 44"
            fill="none"
            stroke="#f3eadc"
            strokeWidth="2.2"
            strokeLinecap="round"
            style={{ "--stroke-len": "160" } as React.CSSProperties}
            className="landing-stroke-anim"
          />

          <line x1="150" y1="16" x2="150" y2="114" stroke="#edb966" strokeOpacity="0.45" strokeWidth="1.2" strokeDasharray="3 4" />
          <path d="M 134 65 H 168" stroke="#edb966" strokeWidth="1.5" />

          <g transform="translate(206, 65)">
            <circle r="28" fill="#8fc79a" fillOpacity="0.12" />
            <circle r="22" fill="none" stroke="#8fc79a" strokeOpacity="0.6" strokeWidth="1.6" />
            <circle r="7.5" fill="#8fc79a" />
            <text y="37" textAnchor="middle" className="fill-[#edb966] font-mono text-[10px] font-bold">
              LaTeX ✓
            </text>
          </g>
        </svg>
      </div>
    </div>
  );
}

/**
 * Module 04 Visual: Interactive Deadline Runway & Priority Shaper
 */
function DeadlineRunwayMicroScene() {
  const dl = useT().landing.features.visuals.deadline;
  const [hIdx, setHIdx] = useState(1);

  const presets = [
    { bars: [78, 64, 52], curve: "M 28 112 C 110 104, 195 72, 316 42" },
    { bars: [92, 84, 46], curve: "M 28 112 C 95 74, 185 48, 316 32" },
    { bars: [100, 96, 88], curve: "M 28 112 C 75 42, 165 28, 316 24" },
  ];
  const cur = presets[hIdx] ?? presets[1];
  const barColors = ["#8fc79a", "#edb966", "#86a9c9"];

  return (
    <div className="flex h-full flex-col justify-between gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="tablist" className="flex items-center gap-4">
          {dl.horizons.map((label, idx) => {
            const active = idx === hIdx;
            return (
              <button
                key={label}
                type="button"
                role="tab"
                aria-selected={active}
                onMouseEnter={() => setHIdx(idx)}
                onClick={() => setHIdx(idx)}
                className={cn(
                  "min-h-7 cursor-pointer border-b pb-0.5 font-mono text-[0.6875rem] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
                  active
                    ? "border-[#edb966] font-semibold text-[#edb966]"
                    : "border-transparent text-[#736e63] hover:text-[#c7baa8]",
                )}
              >
                {label}
              </button>
            );
          })}
        </div>

        <span className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] text-[#edb966]">
          <Check aria-hidden className="size-3.5" />
          {dl.focusLabels[hIdx]}
        </span>
      </div>

      <svg viewBox="0 0 360 156" className="my-auto h-36 w-full select-none sm:h-40" aria-hidden>
        <line x1="28" y1="32" x2="332" y2="32" stroke="#f1e4d6" strokeOpacity="0.08" strokeWidth="1" />
        <line x1="28" y1="72" x2="332" y2="72" stroke="#f1e4d6" strokeOpacity="0.08" strokeWidth="1" />
        <line x1="28" y1="112" x2="332" y2="112" stroke="#f1e4d6" strokeOpacity="0.14" strokeWidth="1" />

        <path
          d={cur.curve}
          fill="none"
          stroke="#edb966"
          strokeWidth="2.2"
          strokeLinecap="round"
        />

        <circle cx="28" cy="112" r="4.5" fill="#736e63" />
        <circle cx="172" cy={hIdx === 0 ? 78 : hIdx === 1 ? 56 : 36} r="5.5" fill="#edb966" />
        <g transform={`translate(316, ${hIdx === 0 ? 42 : hIdx === 1 ? 32 : 24})`}>
          <circle r="13" fill="#8fc79a" fillOpacity="0.18" />
          <circle r="6.5" fill="#8fc79a" />
        </g>

        {cur.bars.map((w, i) => (
          <g key={i} transform={`translate(${28 + i * 104}, 130)`}>
            <rect width="88" height="6" rx="3" fill="#1b1814" />
            <rect
              width={Math.round((w / 100) * 88)}
              height="6"
              rx="3"
              fill={barColors[i]}
              style={{ transition: "width 450ms cubic-bezier(0.16, 1, 0.3, 1)" }}
            />
          </g>
        ))}
      </svg>
    </div>
  );
}

/**
 * Flat 1px hairline-divided 2x2 architectural showcase synced to the 3D WebGL stage.
 */
export function Features() {
  const f = useT().landing.features;
  const { hoveredFeature, setHoveredFeature } = useLandingStage();

  const activeIdx = hoveredFeature;

  return (
    <section id="tools" className="relative scroll-mt-20 text-[#f3eadc]">
      <div className="mx-auto max-w-6xl px-5 py-20 sm:px-8 lg:py-28">
        <div className="max-w-2xl">
          <Reveal mode="rise">
            <span className="font-mono text-[0.75rem] tabular-nums text-[#edb966]">
              {f.index}
            </span>
          </Reveal>
          <Reveal mode="wipe" delayMs={80} className="mt-2">
            <h2 className="font-display text-[clamp(1.65rem,3.2vw,2.6rem)] font-bold leading-[1.08] tracking-[-0.025em] text-[#f3eadc]">
              {f.title}
            </h2>
          </Reveal>
        </div>

        <Reveal mode="rise" delayMs={110} className="mt-8">
          <div
            onMouseLeave={() => setHoveredFeature(-1)}
            className="grid gap-px overflow-hidden rounded-[6px] border border-[#f1e4d6]/12 bg-[#f1e4d6]/10 lg:grid-cols-12"
          >
            <FeatureCell
              index={0}
              title={f.items[0].title}
              short={f.items[0].short}
              active={activeIdx === 0}
              onActivate={setHoveredFeature}
              className="lg:col-span-7"
            >
              <MaterialsMicroScene />
            </FeatureCell>

            <FeatureCell
              index={1}
              title={f.items[1].title}
              short={f.items[1].short}
              active={activeIdx === 1}
              onActivate={setHoveredFeature}
              className="lg:col-span-5"
            >
              <TrapRadarMicroScene />
            </FeatureCell>

            <FeatureCell
              index={2}
              title={f.items[2].title}
              short={f.items[2].short}
              active={activeIdx === 2}
              onActivate={setHoveredFeature}
              className="lg:col-span-5"
            >
              <CanvasPhotoMicroScene />
            </FeatureCell>

            <FeatureCell
              index={3}
              title={f.items[3].title}
              short={f.items[3].short}
              active={activeIdx === 3}
              onActivate={setHoveredFeature}
              className="lg:col-span-7"
            >
              <DeadlineRunwayMicroScene />
            </FeatureCell>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
