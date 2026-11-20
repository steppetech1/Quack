"use client";

import { Check, ListTree, Loader2, Maximize2, Minimize2, Minus, Play } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { get, type S } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import type { OpenDemo } from "./demo-panel";
import { JobsLines, type JobRow } from "./jobs-plaque";
import { cn } from "@/lib/utils";

export type SessionPlanView = S["SessionPlanView"];
export type PlanDemo = SessionPlanView["demos"][number];

/**
 * The plan of a session: read once on open and again after each finished teacher turn (`refresh()`), no polling.
 * A 404 or any error just hides the graph (the endpoint may not exist yet): `plan` stays null.
 */
export function useSessionPlan(sessionId: string) {
  const [plan, setPlan] = useState<SessionPlanView | null>(null);
  const request = useRef(0);
  const alive = useRef(true);

  const refresh = useCallback(() => {
    const mine = ++request.current;
    get<SessionPlanView>(`/v1/sessions/${sessionId}/plan`)
      .then((p) => alive.current && mine === request.current && setPlan(p))
      .catch(() => alive.current && mine === request.current && setPlan(null));
  }, [sessionId]);

  useEffect(() => {
    alive.current = true;
    refresh();
    return () => {
      alive.current = false;
    };
  }, [refresh]);

  return { plan, refresh };
}

/** Is there anything to draw: a goal, a landmark or a step */
export function hasPlan(plan: SessionPlanView | null): plan is SessionPlanView {
  return !!plan && (!!plan.goal || plan.landmarks.length > 0 || !!plan.current_step || plan.next_steps.length > 0);
}

const STORE_KEY = "quack.planGraph.open";
/** From this width of the session area the window stands open in the free field right of the feed (feed column 34rem + the window) */
const WIDE_PX = 1100;
/** The pixel duck stands on the top edge of the composer row (36px tall): the stack stands that much plus a gap above that edge */
const DUCK_CLEARANCE = 44;

/**
 * The corner of a live session, bottom right: the window of the action graph and, right under it, plain lines of
 * background agents. Either may be missing (agents alone sit in the same place); with neither nothing is drawn.
 *
 * The window is small (about 15 x 16 rem): a header with the goal, «N of M» and two buttons (full screen / fold away),
 * under it the chain of point and step rectangles, scrolled to the current one. Folded away it is a small tab in the
 * same corner. The choice is remembered (localStorage); on a narrow area it starts folded.
 * The stack stands above the composer and the duck: its bottom is the height of the next sibling (the composer row) plus the
 * duck clearance. Read-only: no clicks on points or steps.
 */
export function SessionCorner({
  plan,
  jobs,
  topicId,
  onOpenDemo,
  className,
}: {
  plan: SessionPlanView | null;
  jobs: JobRow[];
  topicId?: string | null;
  /** The learner pressed the demo of a step: the session opens it on the right */
  onOpenDemo?: (demo: OpenDemo) => void;
  className?: string;
}) {
  const tx = useT();
  const p = tx.plan;
  const root = useRef<HTMLDivElement>(null);
  const fullBtn = useRef<HTMLButtonElement>(null);
  const [wide, setWide] = useState(false);
  const [bottom, setBottom] = useState(16);
  const [chosen, setChosen] = useState<boolean | null>(null);
  const [full, setFull] = useState(false);
  // the remembered choice only matters once `wide` is known (after mount), so reading it at init cannot mismatch the markup
  const [saved, setSaved] = useState<string | null>(() => {
    try {
      return typeof window === "undefined" ? null : localStorage.getItem(STORE_KEY);
    } catch {
      return null;
    }
  });
  const movedFocus = useRef(false);

  // the width of the session area and the height of the composer row below the stack
  useLayoutEffect(() => {
    const el = root.current;
    const parent = el?.parentElement;
    if (!el || !parent) return;
    let sibling: Element | null = null;
    const sync = () => {
      setWide(parent.clientWidth >= WIDE_PX);
      const next = el.nextElementSibling as HTMLElement | null;
      setBottom(next ? next.offsetHeight + DUCK_CLEARANCE : 16);
    };
    const ro = new ResizeObserver(sync);
    const bind = () => {
      const next = el.nextElementSibling;
      if (next === sibling) return;
      if (sibling) ro.unobserve(sibling);
      sibling = next;
      if (sibling) ro.observe(sibling);
    };
    ro.observe(parent);
    bind();
    sync();
    const mo = new MutationObserver(() => {
      bind();
      sync();
    });
    mo.observe(parent, { childList: true });
    return () => {
      ro.disconnect();
      mo.disconnect();
    };
  }, []);

  const showPlan = hasPlan(plan);
  // wide: open unless the learner folded it (remembered); narrow: folded until pressed
  const open = chosen ?? (wide && saved !== "0");
  const fullOn = full && showPlan && open;

  const setOpen = (v: boolean) => {
    setChosen(v);
    setSaved(v ? "1" : "0");
    try {
      localStorage.setItem(STORE_KEY, v ? "1" : "0");
    } catch {}
    if (!v) setFull(false);
  };

  // Esc brings the full-screen window back; focus follows the button that was pressed
  useEffect(() => {
    if (!fullOn) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFull(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullOn]);
  useEffect(() => {
    if (!movedFocus.current) {
      movedFocus.current = true;
      return;
    }
    fullBtn.current?.focus();
  }, [fullOn]);

  if (!showPlan && !jobs.length) return <div ref={root} hidden />;

  return (
    <div
      ref={root}
      style={fullOn ? undefined : { bottom }}
      className={cn(
        "pointer-events-none absolute z-10 flex flex-col items-end justify-end gap-2",
        fullOn ? "inset-2 z-40 md:inset-3" : "top-3 right-4 w-64 max-w-[calc(100%-2rem)] md:right-7",
        className,
      )}
    >
      {showPlan &&
        (open ? (
          <PlanWindow plan={plan} full={fullOn} fullRef={fullBtn} onFull={() => setFull((f) => !f)} onFold={() => setOpen(false)} onOpenDemo={onOpenDemo} />
        ) : (
          <button
            type="button"
            aria-expanded={false}
            aria-label={p.expand}
            onClick={() => setOpen(true)}
            className="pointer-events-auto caps inline-flex min-h-10 shrink-0 items-center gap-2 rounded-[2px] border border-seam bg-cloth-raised px-3 text-paper-dim hover:text-paper"
          >
            <ListTree aria-hidden className="size-4" />
            <span>{p.heading}</span>
            {plan.progress && <span>{p.liveProgress(plan.progress.done, plan.progress.total)}</span>}
          </button>
        ))}
      {!fullOn && jobs.length > 0 && (
        <JobsLines rows={jobs} topicId={topicId} compact={!wide} className="pointer-events-auto w-full shrink-0 rounded-[2px] bg-cloth/90 px-1" />
      )}
    </div>
  );
}

type NodeKind = "done" | "current" | "upcoming" | "step-now" | "step-next";
type GraphNode = { key: string; kind: NodeKind; label: string; dashed?: boolean; srAdded?: string; stepId?: string };

/** Points (landmarks) in a chain; under the current one its steps, smaller and paler. Only steps: they form the chain themselves */
function graphNodes(plan: SessionPlanView, addedLabel: string): GraphNode[] {
  const steps = [...(plan.current_step ? [plan.current_step] : []), ...plan.next_steps];
  if (!plan.landmarks.length) {
    return steps.map((s, i) => ({ key: s.id, kind: i === 0 ? "current" : "upcoming", label: s.text, stepId: s.id }));
  }
  const out: GraphNode[] = [];
  for (const l of plan.landmarks) {
    out.push({ key: `${l.kind}:${l.id}`, kind: l.status, label: l.title, dashed: l.added_by_teacher, srAdded: l.added_by_teacher ? addedLabel : undefined });
    if (l.status === "current") steps.forEach((s, i) => out.push({ key: s.id, kind: i === 0 ? "step-now" : "step-next", label: s.text, stepId: s.id }));
  }
  return out;
}

function PlanWindow({
  plan,
  full,
  fullRef,
  onFull,
  onFold,
  onOpenDemo,
}: {
  plan: SessionPlanView;
  full: boolean;
  fullRef: RefObject<HTMLButtonElement | null>;
  onFull: () => void;
  onFold: () => void;
  onOpenDemo?: (demo: OpenDemo) => void;
}) {
  const tx = useT();
  const p = tx.plan;
  const scroller = useRef<HTMLDivElement>(null);
  const nodes = graphNodes(plan, p.addedByTeacher);
  const stamp = `${plan.updated_at ?? ""}|${nodes.map((n) => `${n.key}:${n.kind}`).join(",")}`;
  const demos = plan.demos ?? [];
  const shown = new Set(nodes.map((n) => n.stepId));
  // demos of steps that are no longer on the chain stay reachable under it
  const others = demos.filter((d) => d.status === "ready" && !shown.has(d.step_id));
  const open = (d: PlanDemo) => onOpenDemo?.({ requestId: d.request_id, title: d.title });

  // bring the current point to the top of the window (3-4 nodes are in view by default)
  useEffect(() => {
    const box = scroller.current;
    const cur = box?.querySelector<HTMLElement>("[data-cur]");
    if (!box || !cur) return;
    if (full && box.scrollHeight <= box.clientHeight) {
      box.scrollTop = 0; // full screen: everything fits, show it all
      return;
    }
    const delta = cur.getBoundingClientRect().top - box.getBoundingClientRect().top;
    box.scrollTop += delta - (full ? box.clientHeight / 3 : 44);
  }, [stamp, full]);

  const IconFull = full ? Minimize2 : Maximize2;
  return (
    <section
      aria-label={p.title}
      className={cn(
        "pointer-events-auto flex min-h-0 shrink flex-col overflow-hidden rounded-[2px] border border-seam bg-cloth-raised",
        full ? "h-full w-full" : "h-[17rem] w-full",
      )}
    >
      <header className={cn("flex shrink-0 items-start border-b border-seam-soft", full && "px-2")}>
        <div className={cn("min-w-0 flex-1 py-2 pl-3", full && "pl-2 md:py-3")}>
          <div className="flex items-baseline gap-2">
            <span className="caps text-paper">{p.heading}</span>
            {plan.progress && <span className="caps text-paper-dim">{p.liveProgress(plan.progress.done, plan.progress.total)}</span>}
          </div>
          {plan.goal && <p className={cn("mt-0.5 text-paper", full ? "text-body" : "line-clamp-2 text-small")}>{plan.goal}</p>}
        </div>
        <button
          ref={fullRef}
          type="button"
          aria-label={full ? p.exitFullscreen : p.fullscreen}
          onClick={onFull}
          className="inline-flex size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper"
        >
          <IconFull aria-hidden className="size-4" />
        </button>
        <button
          type="button"
          aria-label={p.collapse}
          onClick={onFold}
          className="inline-flex size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper"
        >
          <Minus aria-hidden className="size-4" />
        </button>
      </header>
      <div ref={scroller} className={cn("min-h-0 flex-1 overflow-y-auto", full ? "px-4 py-6 md:px-8" : "p-3")}>
        <ol className={cn("relative mx-auto flex flex-col", full ? "max-w-xl gap-4" : "gap-3")}>
          <span aria-hidden className="absolute top-2 bottom-2 left-3.5 w-px bg-seam" />
          {nodes.map((n) => (
            <Node key={n.key} node={n} big={full} demo={demos.find((d) => d.step_id === n.stepId)} onOpen={open} />
          ))}
        </ol>
        {others.length > 0 && (
          <div className={cn("mx-auto mt-4 border-t border-seam-soft pt-2", full && "max-w-xl")}>
            <p className="caps text-paper-dim">{p.demosHeading}</p>
            <ul className="mt-1">
              {others.map((d) => (
                <li key={d.request_id}>
                  <button
                    type="button"
                    onClick={() => open(d)}
                    aria-label={p.demoOpen(d.title)}
                    className="flex min-h-10 w-full items-center gap-2 text-left text-small text-paper-dim hover:text-paper"
                  >
                    <Play aria-hidden className="size-3.5 shrink-0 text-ribbon" />
                    <span className="min-w-0 truncate">{d.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function Node({ node, big, demo, onOpen }: { node: GraphNode; big: boolean; demo?: PlanDemo; onOpen: (d: PlanDemo) => void }) {
  const tx = useT();
  const p = tx.plan;
  const step = node.kind === "step-now" || node.kind === "step-next";
  const dim = node.kind === "upcoming" || node.kind === "step-next";
  return (
    <li className={cn("relative flex", step && (big ? "ml-9" : "ml-6"))}>
      {step && <span aria-hidden className={cn("absolute top-1/2 h-px bg-seam", big ? "-left-5 w-5" : "-left-2.5 w-2.5")} />}
      <div
        data-cur={node.kind === "current" ? "" : undefined}
        className={cn(
          "relative flex min-w-0 flex-1 items-start gap-2 rounded-[2px] border px-2.5",
          step ? (big ? "py-2 text-small" : "py-1 text-[0.8125rem] leading-snug") : big ? "py-3 text-body" : "py-1.5 text-small",
          node.kind === "done" && "border-seam bg-cloth-deep text-paper",
          node.kind === "current" && "border-ribbon bg-ribbon-soft font-medium text-paper",
          node.kind === "step-now" && "border-seam bg-cloth-deep text-paper",
          dim && "border-seam-soft bg-cloth text-paper-dim",
          node.dashed && node.kind !== "current" && "border-dashed",
        )}
      >
        {node.kind === "done" && <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-paper-dim" />}
        <span className={cn("min-w-0 [overflow-wrap:anywhere]", !big && "line-clamp-2")}>
          {step && <span className="sr-only">{node.kind === "step-now" ? p.currentStep : p.nextStep}: </span>}
          {node.label}
          {node.kind === "current" && <span className="sr-only"> {p.current}</span>}
          {node.srAdded && <span className="sr-only"> ({node.srAdded})</span>}
        </span>
        {demo?.status === "ready" && (
          <button
            type="button"
            onClick={() => onOpen(demo)}
            aria-label={p.demoOpen(demo.title)}
            title={demo.title}
            className="-mr-1.5 ml-auto inline-flex size-7 shrink-0 items-center justify-center rounded-pill text-ribbon hover:bg-cloth-hover focus-visible:outline-2 focus-visible:outline-ribbon"
          >
            <Play aria-hidden className="size-3.5" />
          </button>
        )}
        {demo?.status === "preparing" && (
          <span role="img" aria-label={p.demoPreparing} title={p.demoPreparing} className="-mr-0.5 ml-auto inline-flex size-6 shrink-0 items-center justify-center text-paper-dim">
            <Loader2 aria-hidden className="size-3.5 animate-spin motion-reduce:animate-none" />
          </span>
        )}
      </div>
    </li>
  );
}
