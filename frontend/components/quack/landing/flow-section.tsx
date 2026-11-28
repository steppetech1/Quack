"use client";

import { ArrowRight, Check, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { Reveal } from "./motion";
import { useLandingStage, type FlowStep } from "./stage/landing-context";

const STEPS: FlowStep[] = [0, 1, 2];

const STATE_STYLE: Record<string, { color: string; pct: number; x: number; y: number }> = {
  solid: { color: "#8fc79a", pct: 0.92, x: 88, y: 120 },
  stuck: { color: "#ef8c7a", pct: 0.42, x: 184, y: 74 },
  fading: { color: "#86a9c9", pct: 0.65, x: 222, y: 158 },
  next: { color: "#8d877b", pct: 0, x: 332, y: 112 },
};

function DropVisual() {
  const v = useT().landing.flow.visuals.drop;
  const [merged, setMerged] = useState(true);

  const docs = [
    {
      label: v.fileLabels[0] ?? "PDF",
      sx: 58,
      sy: 54,
      srot: -11,
      mx: 40,
      my: 48,
      col: "#edb966",
    },
    {
      label: v.fileLabels[1] ?? "Photo",
      sx: 175,
      sy: 116,
      srot: 9,
      mx: 40,
      my: 108,
      col: "#86a9c9",
    },
    {
      label: v.fileLabels[2] ?? "Notes",
      sx: 82,
      sy: 162,
      srot: -6,
      mx: 40,
      my: 168,
      col: "#8fc79a",
    },
  ];

  const nodes = [
    { x: 236, y: 80, col: "#8fc79a", r: 9 },
    { x: 286, y: 136, col: "#ef8c7a", r: 10 },
    { x: 336, y: 74, col: "#edb966", r: 11 },
    { x: 368, y: 146, col: "#86a9c9", r: 8 },
  ];

  return (
    <div className="flex h-full flex-col justify-between gap-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-[#8d8273]">
          {docs.map((d) => d.label).join(" · ")}
        </span>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 font-mono text-[0.6875rem] transition-opacity duration-300",
            merged ? "text-st-solid opacity-100" : "text-[#8d8273] opacity-40",
          )}
        >
          <span aria-hidden className={cn("size-1.5 rounded-full", merged ? "bg-st-solid" : "bg-[#736e63]")} />
          {v.mappedBadge}
        </span>
      </div>

      <svg viewBox="0 0 420 220" className="my-auto h-52 w-full select-none sm:h-60" aria-hidden>
        {/* Subtle constellation edges when mapped */}
        <g
          className="transition-opacity duration-500 ease-(--ease-out)"
          style={{ opacity: merged ? 1 : 0.08 }}
        >
          <path d="M 108 58 C 168 58, 184 80, 236 80" fill="none" stroke="#edb966" strokeOpacity="0.45" strokeWidth="1.35" strokeDasharray="4 4" />
          <path d="M 108 118 C 175 118, 200 136, 286 136" fill="none" stroke="#86a9c9" strokeOpacity="0.45" strokeWidth="1.35" strokeDasharray="4 4" />
          <path d="M 108 176 C 175 176, 190 80, 236 80" fill="none" stroke="#8fc79a" strokeOpacity="0.45" strokeWidth="1.35" strokeDasharray="4 4" />
          <line x1="236" y1="80" x2="286" y2="136" stroke="#f1e4d6" strokeOpacity="0.32" strokeWidth="1.4" />
          <line x1="236" y1="80" x2="336" y2="74" stroke="#edb966" strokeOpacity="0.48" strokeWidth="1.5" />
          <line x1="286" y1="136" x2="336" y2="74" stroke="#ef8c7a" strokeOpacity="0.45" strokeWidth="1.4" />
          <line x1="286" y1="136" x2="368" y2="146" stroke="#86a9c9" strokeOpacity="0.42" strokeWidth="1.4" />
          <line x1="336" y1="74" x2="368" y2="146" stroke="#f1e4d6" strokeOpacity="0.26" strokeWidth="1.2" />
        </g>

        {/* Source material cards */}
        {docs.map((d) => {
          const tx = merged ? d.mx : d.sx;
          const ty = merged ? d.my : d.sy;
          const rot = merged ? 0 : d.srot;
          return (
            <g
              key={d.label}
              style={{
                transform: `translate(${tx}px, ${ty}px) rotate(${rot}deg)`,
                transition: "transform 550ms cubic-bezier(0.16, 1, 0.3, 1)",
              }}
            >
              <rect
                x="-12"
                y="-14"
                width="74"
                height="44"
                rx="5"
                fill="#161310"
                stroke={d.col}
                strokeOpacity={merged ? 0.65 : 0.38}
                strokeWidth="1.3"
              />
              <line x1="-2" y1="-2" x2="22" y2="-2" stroke={d.col} strokeOpacity="0.7" strokeWidth="2" strokeLinecap="round" />
              <text x="-2" y="17" className="fill-[#f3eadc] font-mono text-[10.5px] font-medium">
                {d.label}
              </text>
            </g>
          );
        })}

        {/* Extracted topic nodes */}
        {nodes.map((n, idx) => (
          <g
            key={idx}
            style={{
              transform: `translate(${merged ? n.x : 300 + (idx - 1.5) * 26}px, ${merged ? n.y : 110}px) scale(${merged ? 1 : 0.35})`,
              opacity: merged ? 1 : 0.14,
              transition: "transform 600ms cubic-bezier(0.16, 1, 0.3, 1), opacity 400ms ease",
            }}
          >
            <circle r={n.r + 9} fill={n.col} fillOpacity="0.12" />
            <circle r={n.r + 5} fill="none" stroke={n.col} strokeOpacity="0.42" strokeWidth="1.3" />
            <circle r={n.r} fill={n.col} />
          </g>
        ))}
      </svg>

      <div className="flex justify-end border-t border-[#f1e4d6]/10 pt-4">
        <button
          type="button"
          onClick={() => setMerged((m) => !m)}
          className={cn(
            "inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-pill px-5 font-mono text-[0.75rem] font-bold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
            merged
              ? "border border-[#f1e4d6]/18 bg-transparent text-[#e8e4dc] hover:border-[#edb966] hover:text-[#edb966]"
              : "bg-[#edb966] text-[#141008] hover:bg-[#f5cf8e]",
          )}
        >
          {merged ? <RotateCcw aria-hidden className="size-3.5" /> : <Sparkles aria-hidden className="size-3.5" />}
          <span>{merged ? v.actionReset : v.actionMerge}</span>
        </button>
      </div>
    </div>
  );
}

function MapVisual() {
  const m = useT().landing.flow.visuals.map;
  const [activeId, setActiveId] = useState<string>("stuck");
  const selected = m.states.find((s) => s.id === activeId) ?? m.states[1] ?? m.states[0];
  const activeMeta = STATE_STYLE[selected.id] ?? STATE_STYLE.stuck;

  const links: [string, string][] = [
    ["solid", "stuck"],
    ["solid", "fading"],
    ["stuck", "next"],
    ["fading", "next"],
  ];

  return (
    <div className="flex h-full flex-col justify-between gap-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-[#8d8273]">
          {m.hint}
        </span>
      </div>

      <svg viewBox="0 0 420 220" className="my-auto h-52 w-full select-none sm:h-60">
        {links.map(([aId, bId]) => {
          const a = STATE_STYLE[aId];
          const b = STATE_STYLE[bId];
          if (!a || !b) return null;
          const lit = aId === selected.id || bId === selected.id;
          return (
            <line
              key={`${aId}-${bId}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={lit ? activeMeta.color : "#736e63"}
              strokeOpacity={lit ? 0.65 : 0.28}
              strokeWidth={lit ? 1.75 : 1.25}
              strokeDasharray={aId === "next" || bId === "next" ? "4 5" : undefined}
            />
          );
        })}

        {m.states.map((st) => {
          const pos = STATE_STYLE[st.id] ?? STATE_STYLE.solid;
          const active = st.id === selected.id;
          const ringR = 22;
          const circ = 2 * Math.PI * ringR;
          const dash = pos.pct * circ;
          const isUntouched = st.id === "next";

          return (
            <g
              key={st.id}
              transform={`translate(${pos.x}, ${pos.y})`}
              role="button"
              tabIndex={0}
              aria-label={`${st.label}: ${st.note}`}
              aria-pressed={active}
              onClick={() => setActiveId(st.id)}
              onMouseEnter={() => setActiveId(st.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setActiveId(st.id);
                }
              }}
              className="cursor-pointer outline-none"
            >
              {/* >= 44px transparent hit area */}
              <circle r="28" fill="transparent" />

              {active && (
                <circle r="27" fill="none" stroke={pos.color} strokeOpacity="0.4" strokeWidth="1.5" />
              )}

              <circle r={ringR} fill="none" stroke="#f1e4d6" strokeOpacity="0.1" strokeWidth="1.8" />
              {pos.pct > 0 && (
                <circle
                  r={ringR}
                  fill="none"
                  stroke={pos.color}
                  strokeWidth="2.4"
                  strokeDasharray={`${dash} ${circ}`}
                  strokeLinecap="round"
                  transform="rotate(-90)"
                />
              )}

              <circle
                r={active ? 10 : 8}
                fill={isUntouched ? "#12100d" : pos.color}
                stroke={isUntouched ? pos.color : "none"}
                strokeWidth="1.8"
              />

              <text
                y="38"
                textAnchor="middle"
                className={cn(
                  "fill-current font-mono text-[11px]",
                  active ? "fill-[#f3eadc] font-bold" : "fill-[#9c9283]",
                )}
              >
                {st.label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Flat bottom status readout */}
      <div className="flex flex-wrap items-center gap-2.5 border-t border-[#f1e4d6]/10 pt-4">
        <span
          aria-hidden
          className="size-2.5 rounded-full"
          style={{
            backgroundColor: selected.id === "next" ? "transparent" : activeMeta.color,
            border: selected.id === "next" ? `1.5px solid ${activeMeta.color}` : "none",
          }}
        />
        <span className="font-mono text-[0.8125rem] font-semibold text-[#f3eadc]">
          {selected.label}
        </span>
        <span aria-hidden className="text-[#736e63]">·</span>
        <span className="text-[0.8125rem] text-[#9c9283]">{selected.note}</span>
      </div>
    </div>
  );
}

function CloseGapVisual() {
  const c = useT().landing.flow.visuals.close;
  const [fixed, setFixed] = useState(false);

  const ringColor = fixed ? "#8fc79a" : "#ef8c7a";
  const ringR = 38;
  const circ = 2 * Math.PI * ringR;
  const dash = fixed ? circ : circ * 0.36;

  return (
    <div className="flex h-full flex-col justify-between gap-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-[#8d8273]">
          {c.promptLabel}
        </span>
        {fixed && (
          <span className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] text-st-solid">
            <Check aria-hidden className="size-3 stroke-[2.5]" />
            {c.savedNote}
          </span>
        )}
      </div>

      <svg viewBox="0 0 420 220" className="my-auto h-52 w-full select-none sm:h-60" aria-hidden>
        {/* Prompt wave node on the left */}
        <circle cx="96" cy="110" r="24" fill="#18140e" stroke="#edb966" strokeOpacity="0.65" strokeWidth="1.5" />
        <circle cx="96" cy="110" r="7" fill="#edb966" />
        <path
          d="M 124 110 C 165 86, 185 134, 224 110"
          fill="none"
          stroke={fixed ? "#8fc79a" : "#edb966"}
          strokeWidth="1.75"
          strokeDasharray={fixed ? undefined : "5 5"}
        />

        {/* Target gap ring morphing smoothly from Coral Gap -> Sage Solid */}
        <g transform="translate(292, 110)">
          {fixed && (
            <circle r="56" fill="none" stroke="#8fc79a" strokeOpacity="0.22" strokeWidth="1.2" />
          )}
          <circle r={ringR} fill="#141210" stroke="#f1e4d6" strokeOpacity="0.1" strokeWidth="2.5" />
          <circle
            r={ringR}
            fill="none"
            stroke={ringColor}
            strokeWidth="3.2"
            strokeLinecap="round"
            strokeDasharray={`${dash.toFixed(1)} ${circ.toFixed(1)}`}
            transform="rotate(-90)"
            style={{ transition: "stroke-dasharray 550ms cubic-bezier(0.16, 1, 0.3, 1), stroke 350ms ease" }}
          />
          <circle
            r={fixed ? 15 : 10}
            fill={ringColor}
            style={{ transition: "r 350ms ease, fill 350ms ease" }}
          />
          <text
            y="60"
            textAnchor="middle"
            className="fill-[#f3eadc] font-mono text-[12px] font-semibold"
          >
            {fixed ? c.afterState : c.beforeState}
          </text>
        </g>
      </svg>

      <div className="flex items-center justify-end gap-2 border-t border-[#f1e4d6]/10 pt-4">
        <button
          type="button"
          onClick={() => setFixed((f) => !f)}
          className={cn(
            "inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-pill px-5 font-mono text-[0.75rem] font-bold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[#edb966]/70",
            fixed
              ? "border border-[#f1e4d6]/18 bg-transparent text-[#e8e4dc] hover:border-[#edb966] hover:text-[#edb966]"
              : "bg-[#edb966] text-[#141008] hover:bg-[#f5cf8e]",
          )}
        >
          {fixed ? <RotateCcw aria-hidden className="size-3.5" /> : <ArrowRight aria-hidden className="size-3.5" />}
          <span>{fixed ? c.actionReset : c.actionRecall}</span>
        </button>
      </div>
    </div>
  );
}

export function FlowSection() {
  const f = useT().landing.flow;
  const { flowStep, setFlowStep } = useLandingStage();

  return (
    <section id="flow" className="relative scroll-mt-20 text-[#f3eadc]">
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

        <Reveal mode="rise" delayMs={100} className="mt-8">
          <div className="overflow-hidden rounded-[6px] border border-[#f1e4d6]/12 bg-[#0b0908]/92">
            <div className="grid lg:grid-cols-12">
              {/* Left 4 cols: flat hairline-divided 3-step selector */}
              <div
                role="tablist"
                aria-label={f.title}
                className="flex flex-col divide-y divide-[#f1e4d6]/10 border-b border-[#f1e4d6]/10 lg:col-span-4 lg:border-r lg:border-b-0"
              >
                {STEPS.map((idx) => {
                  const step = f.steps[idx];
                  if (!step) return null;
                  const active = flowStep === idx;
                  return (
                    <button
                      key={step.id}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => setFlowStep(idx)}
                      className={cn(
                        "relative flex flex-1 cursor-pointer items-start gap-3.5 p-5 text-left transition-colors duration-200 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#edb966]/70 sm:p-6",
                        active ? "bg-[#13100d]" : "bg-transparent hover:bg-[#100d0b]",
                      )}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "absolute inset-y-0 left-0 w-[2px] transition-opacity duration-200",
                          active ? "bg-[#edb966] opacity-100" : "opacity-0",
                        )}
                      />
                      <span
                        className={cn(
                          "mt-0.5 font-mono text-[0.75rem] tabular-nums",
                          active ? "font-bold text-[#edb966]" : "text-[#736e63]",
                        )}
                      >
                        {step.num}
                      </span>
                      <div>
                        <p
                          className={cn(
                            "font-display text-[1.0625rem] font-semibold leading-snug",
                            active ? "text-[#f3eadc]" : "text-[#b3a695]",
                          )}
                        >
                          {step.title}
                        </p>
                        <p className="mt-1 text-[0.8125rem] leading-[1.45] text-[#8d8273]">
                          {step.caption}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>

              {/* Right 8 cols: interactive visual stage */}
              <div className="min-h-[320px] bg-[#0e0b09]/95 p-5 sm:min-h-[370px] sm:p-7 lg:col-span-8">
                {flowStep === 0 && <DropVisual />}
                {flowStep === 1 && <MapVisual />}
                {flowStep === 2 && <CloseGapVisual />}
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
