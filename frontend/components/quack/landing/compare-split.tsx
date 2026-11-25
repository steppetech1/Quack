"use client";

import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { Reveal } from "./motion";
import { useLandingStage } from "./stage/landing-context";

function ChatFadingVisual() {
  const dots = [
    { x: 52, y: 112, r: 12, op: 0.88 },
    { x: 132, y: 84, r: 10, op: 0.55 },
    { x: 214, y: 64, r: 7.5, op: 0.28 },
    { x: 298, y: 56, r: 5, op: 0.1 },
  ];

  return (
    <svg viewBox="0 0 360 150" className="h-36 w-full max-w-[22rem] select-none sm:h-40" aria-hidden>
      <path
        d="M 52 112 C 110 92, 165 72, 232 62 C 276 56, 312 56, 336 56"
        fill="none"
        stroke="#ef8c7a"
        strokeOpacity="0.48"
        strokeWidth="1.75"
        strokeDasharray="6 5"
      />
      {dots.map((d, i) => (
        <g key={i} opacity={d.op}>
          <circle cx={d.x} cy={d.y} r={d.r} fill="#181311" stroke="#ef8c7a" strokeWidth="1.5" />
          <circle cx={d.x} cy={d.y} r={d.r * 0.42} fill="#ef8c7a" />
        </g>
      ))}
    </svg>
  );
}

function QuackConstellationVisual() {
  const nodes = [
    { x: 76, y: 92, r: 10, core: "#8fc79a" },
    { x: 152, y: 52, r: 12, core: "#edb966" },
    { x: 226, y: 104, r: 10, core: "#86a9c9" },
    { x: 288, y: 56, r: 11, core: "#edb966" },
    { x: 180, y: 122, r: 8.5, core: "#8fc79a" },
  ];

  const edges = [
    [0, 1],
    [1, 2],
    [2, 3],
    [1, 3],
    [0, 4],
    [4, 2],
  ];

  return (
    <svg viewBox="0 0 360 150" className="h-36 w-full max-w-[22rem] select-none sm:h-40" aria-hidden>
      <circle
        cx="188"
        cy="82"
        r="58"
        fill="none"
        stroke="#f1e4d6"
        strokeOpacity="0.12"
        strokeWidth="1.2"
        strokeDasharray="3 5"
      />
      {edges.map(([a, b], idx) => (
        <line
          key={idx}
          x1={nodes[a].x}
          y1={nodes[a].y}
          x2={nodes[b].x}
          y2={nodes[b].y}
          stroke="#edb966"
          strokeOpacity="0.38"
          strokeWidth="1.5"
        />
      ))}
      {nodes.map((n, i) => (
        <g key={i}>
          <circle cx={n.x} cy={n.y} r={n.r + 6} fill="none" stroke="#edb966" strokeOpacity="0.3" strokeWidth="1.2" />
          <circle cx={n.x} cy={n.y} r={n.r} fill="#16120e" stroke="#edb966" strokeOpacity="0.55" strokeWidth="1.2" />
          <circle cx={n.x} cy={n.y} r={n.r * 0.44} fill={n.core} />
        </g>
      ))}
    </svg>
  );
}

export function CompareSplit() {
  const c = useT().landing.compare;
  const { compareSide, setCompareSide } = useLandingStage();

  return (
    <section id="compare" className="relative scroll-mt-20 text-[#f3eadc]">
      <div className="mx-auto max-w-6xl px-5 py-20 sm:px-8 lg:py-28">
        <div className="max-w-2xl">
          <Reveal mode="rise">
            <span className="font-mono text-[0.75rem] tabular-nums text-[#edb966]">
              {c.index}
            </span>
          </Reveal>
          <Reveal mode="wipe" delayMs={80} className="mt-2">
            <h2 className="font-display text-[clamp(1.65rem,3.2vw,2.6rem)] font-bold leading-[1.08] tracking-[-0.025em] text-[#f3eadc]">
              {c.title}
            </h2>
          </Reveal>
        </div>

        <Reveal mode="rise" delayMs={100} className="mt-8">
          <div className="grid overflow-hidden rounded-[6px] border border-[#f1e4d6]/12 lg:grid-cols-2">
            {/* Left column: Regular chat */}
            <button
              type="button"
              onMouseEnter={() => setCompareSide("chat")}
              onFocus={() => setCompareSide("chat")}
              onClick={() => setCompareSide("chat")}
              className={cn(
                "flex cursor-pointer flex-col justify-between p-7 text-left outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#edb966] sm:p-10",
                compareSide === "chat" ? "bg-[#110e0c]" : "bg-[#0a0807]/92",
              )}
            >
              <h3 className="font-display text-[1.25rem] font-semibold tracking-[-0.02em] text-[#9c9283]">
                {c.chat.label}
              </h3>

              <div className="my-6 flex w-full items-center justify-center">
                <ChatFadingVisual />
              </div>

              <ul className="w-full divide-y divide-[#f1e4d6]/8 border-t border-[#f1e4d6]/8">
                {c.chat.points.map((point) => (
                  <li
                    key={point}
                    className="flex items-center gap-2.5 py-2.5 text-[0.875rem] text-[#8d8273]"
                  >
                    <span aria-hidden className="font-mono text-[#736e63]">—</span>
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </button>

            {/* Right column: Quack! with surgical 2px amber top hairline */}
            <button
              type="button"
              onMouseEnter={() => setCompareSide("quack")}
              onFocus={() => setCompareSide("quack")}
              onClick={() => setCompareSide("quack")}
              className={cn(
                "flex cursor-pointer flex-col justify-between border-t-2 border-t-[#edb966] p-7 text-left outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#edb966] sm:p-10 lg:border-l lg:border-l-[#f1e4d6]/12",
                compareSide === "quack" ? "bg-[#14100c]" : "bg-[#100d0a]/95",
              )}
            >
              <h3 className="font-display text-[1.25rem] font-semibold tracking-[-0.02em] text-[#f3eadc]">
                Quack<span className="text-[#edb966]">!</span>
              </h3>

              <div className="my-6 flex w-full items-center justify-center">
                <QuackConstellationVisual />
              </div>

              <ul className="w-full divide-y divide-[#f1e4d6]/10 border-t border-[#f1e4d6]/10">
                {c.quack.points.map((point) => (
                  <li
                    key={point}
                    className="flex items-center gap-2.5 py-2.5 text-[0.875rem] text-[#f3eadc]"
                  >
                    <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-[#edb966]" />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </button>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
