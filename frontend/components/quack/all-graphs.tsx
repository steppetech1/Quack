"use client";

import { useMemo } from "react";
import { useData } from "@/lib/api/data";
import { layoutGraph, loadPositions } from "@/lib/graph/layout";
import { LEARNER } from "@/lib/graph/visit-store";
import { useGraphModel } from "@/lib/graph/use-graph-model";
import { onGraph, type GraphModel } from "@/lib/graph/types";
import { layoutDomain } from "@/lib/graph-layout";
import { useLocale, useT } from "@/lib/i18n/provider";
import type { Domain, TopicState } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { stateOf } from "./graph/layered-graph";
import { NumText } from "./num-text";

const STATE_FILL: Record<TopicState, string> = {
  solid: "fill-st-solid",
  review: "fill-st-review",
  confused: "fill-st-confused",
  new: "fill-st-new",
};

type Shape = {
  box: string;
  r: number;
  dots: { id: string; x: number; y: number; state: TopicState }[];
  rings: { id: string; x: number; y: number }[];
  lines: { id: string; d: string; dashed?: boolean; faint?: boolean }[];
};

/** The same picture the domain view draws, taken from the graph model (mocks) or, without one, from the subject's own topics */
function shapeOf(model: GraphModel | undefined, domain: Domain): Shape | null {
  let dots: Shape["dots"] = [];
  let rings: Shape["rings"] = [];
  let lines: Shape["lines"] = [];
  if (model) {
    const layout = layoutGraph(model, loadPositions(LEARNER, model.domainId));
    const byId = new Map(model.topics.map((t) => [t.id, t]));
    dots = layout.topics.map((n) => ({ id: n.id, x: n.x, y: n.y, state: stateOf(byId.get(n.id)!, model.nowMs) }));
    rings = layout.hubs.map((n) => ({ id: n.id, x: n.x, y: n.y }));
    for (const e of model.requires) {
      const a = layout.nodes.get(e.requiresId);
      const b = layout.nodes.get(e.topicId);
      if (a && b) lines.push({ id: `${e.requiresId}>${e.topicId}`, d: `M${a.x} ${a.y}L${b.x} ${b.y}` });
    }
    // a hub reaches the topics it includes: faint, so the chains stay the picture
    for (const l of model.includes) {
      const a = layout.nodes.get(`h:${l.hubId}`);
      const b = layout.nodes.get(l.topicId);
      if (a && b) lines.push({ id: `h:${l.hubId}>${l.topicId}`, d: `M${a.x} ${a.y}L${b.x} ${b.y}`, faint: true });
    }
  } else {
    const layout = layoutDomain(domain);
    dots = layout.topics.map((t) => ({ id: t.id, x: t.x, y: t.y, state: t.state }));
    rings = layout.hubs.map((h) => ({ id: h.id, x: h.x, y: h.y }));
    lines = layout.edges.map((e) => ({ id: e.id, d: e.d, dashed: e.kind === "related", faint: e.kind === "cross" || e.kind === "related" }));
  }
  const pts = [...dots, ...rings];
  if (!pts.length) return null;
  const x0 = Math.min(...pts.map((p) => p.x));
  const x1 = Math.max(...pts.map((p) => p.x));
  const y0 = Math.min(...pts.map((p) => p.y));
  const y1 = Math.max(...pts.map((p) => p.y));
  // dot sizes follow the extent of the graph, so a small subject and a big one read the same
  const r = Math.max(4, Math.max(x1 - x0, y1 - y0, 1) / 80);
  const pad = r * 6;
  return { box: `${x0 - pad} ${y0 - pad} ${x1 - x0 + pad * 2} ${y1 - y0 + pad * 2}`, r, dots, rings, lines };
}

/** Whole graph of one subject as a still picture: no labels, no interaction */
function MiniGraph({ shape, label }: { shape: Shape; label: string }) {
  const { box, r, dots, rings, lines } = shape;
  return (
    <svg viewBox={box} role="img" aria-label={label} className="size-full text-paper-dim" preserveAspectRatio="xMidYMid meet">
      <g fill="none" stroke="currentColor">
        {lines.map((l) => (
          <path key={l.id} d={l.d} strokeWidth={1} vectorEffect="non-scaling-stroke" strokeDasharray={l.dashed ? "1 4" : undefined} opacity={l.faint ? 0.22 : 0.4} />
        ))}
      </g>
      {dots.map((d) => (
        <circle key={d.id} cx={d.x} cy={d.y} r={r} className={STATE_FILL[d.state]} opacity={0.9} />
      ))}
      {rings.map((h) => (
        <circle key={h.id} cx={h.x} cy={h.y} r={r * 2.1} fill="none" stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" className="text-paper" />
      ))}
    </svg>
  );
}

function Tile({ domain }: { domain: Domain }) {
  const t = useT();
  const { locale } = useLocale();
  const { selectDomain } = useShell();
  const model = useGraphModel(locale, domain.id);
  const shape = useMemo(() => shapeOf(model, domain), [model, domain]);
  const hubs = model ? model.hubs.filter((h) => h.kind !== "domain_all").length : domain.hubs.length;
  const topics = model ? model.topics.filter(onGraph).length : domain.topics.length;
  const ready = domain.readiness;
  return (
    <button
      type="button"
      aria-label={t.allGraphs.open(domain.title)}
      onClick={() => selectDomain(domain.id)}
      className={cn(
        "group flex h-full w-full flex-col gap-3 rounded-control border border-seam bg-cloth-deep p-4 text-left",
        "transition-colors duration-(--dur-s) ease-(--ease-out) hover:border-rule hover:bg-cloth-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon",
      )}
    >
      <span className="flex items-baseline justify-between gap-3">
        <span className="display-caps truncate text-[1.25rem] text-paper">{domain.title}</span>
        {ready !== undefined && (
          <span className="caps shrink-0 whitespace-nowrap text-paper-dim">
            {t.allGraphs.ready} <span className="num text-paper">{ready}%</span>
          </span>
        )}
      </span>
      <span className="relative block aspect-[4/3] w-full">
        {shape ? <MiniGraph shape={shape} label={t.allGraphs.graphSr(domain.title)} /> : <span className="caps absolute inset-0 grid place-items-center text-paper-dim">{t.allGraphs.empty}</span>}
      </span>
      {shape && (
        <span className="caps text-paper-dim">
          <NumText text={`${t.allGraphs.hubs(hubs)} · ${t.allGraphs.topics(topics)}`} />
        </span>
      )}
    </button>
  );
}

/** First screen: the graphs of all subjects side by side. A click opens the subject and centres its graph */
export function AllGraphs() {
  const t = useT();
  const { domains } = useData();
  const list = domains.filter((d) => d.id);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 pt-4 pb-6 md:px-7 md:pt-5">
      <header className="flex flex-col gap-1">
        <h1 className="display-caps text-[clamp(1.75rem,2.6vw,2.25rem)] text-paper">{t.allGraphs.title}</h1>
        <p className="caps text-paper-dim">{t.allGraphs.lead}</p>
      </header>

      <ul className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
        {list.map((d) => (
          <li key={d.id}>
            <Tile domain={d} />
          </li>
        ))}
      </ul>
    </div>
  );
}
