"use client";

import { Check, ListTree, Loader2, Maximize2, Minimize2, Minus, Play } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { get, type S } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import type { OpenDemo } from "./demo-panel";
import { JobsLines, type JobRow } from "./jobs-plaque";
import { cn } from "@/lib/utils";

export type PlanStepStatus = "done" | "current" | "upcoming";
export type PlanStep = S["PlanStep"] & { status?: PlanStepStatus };
export type SessionPlanView = Omit<S["SessionPlanView"], "current_step" | "next_steps"> & {
  current_step: PlanStep | null;
  next_steps: PlanStep[];
  done_steps?: PlanStep[];
};
export type PlanDemo = SessionPlanView["demos"][number];

const PLAN_RETRY_MS = 5_000;

/**
 * The plan of a session: read once on open and again after each finished teacher turn (`refresh()`), no polling.
 * On error, keeps the previous `plan`, marks it `stale`, and retries after a short pause.
 */
export function useSessionPlan(sessionId: string, opts?: { onSuccess?: () => void }) {
  const [plan, setPlan] = useState<SessionPlanView | null>(null);
  const [stale, setStale] = useState(false);
  const request = useRef(0);
  const alive = useRef(true);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onSuccessRef = useRef(opts?.onSuccess);
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    onSuccessRef.current = opts?.onSuccess;
  });

  const refresh = useCallback(() => {
    clearTimeout(retryTimer.current);
    const mine = ++request.current;
    get<SessionPlanView>(`/v1/sessions/${sessionId}/plan`)
      .then((p) => {
        if (!alive.current || mine !== request.current) return;
        setPlan(p);
        setStale(false);
        onSuccessRef.current?.();
      })
      .catch(() => {
        if (!alive.current || mine !== request.current) return;
        setStale(true);
        retryTimer.current = setTimeout(() => {
          if (alive.current) refreshRef.current();
        }, PLAN_RETRY_MS);
      });
  }, [sessionId]);

  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    alive.current = true;
    refresh();
    return () => {
      alive.current = false;
      clearTimeout(retryTimer.current);
    };
  }, [refresh]);

  return { plan, stale, refresh };
}

/** Is there anything to draw: a goal, a landmark or a step */
export function hasPlan(plan: SessionPlanView | null): plan is SessionPlanView {
  return (
    !!plan &&
    (!!plan.goal ||
      plan.landmarks.length > 0 ||
      (plan.done_steps?.length ?? 0) > 0 ||
      !!plan.current_step ||
      plan.next_steps.length > 0)
  );
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
  stale = false,
  jobs,
  topicId,
  onOpenDemo,
  className,
}: {
  plan: SessionPlanView | null;
  stale?: boolean;
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
          <PlanWindow plan={plan} stale={stale} full={fullOn} fullRef={fullBtn} onFull={() => setFull((f) => !f)} onFold={() => setOpen(false)} onOpenDemo={onOpenDemo} />
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
            {stale && <span className="text-st-confused">{p.stale}</span>}
          </button>
        ))}
      {!fullOn && jobs.length > 0 && (
        <JobsLines rows={jobs} topicId={topicId} compact={!wide} className="pointer-events-auto w-full shrink-0 rounded-[2px] bg-cloth/90 px-1" />
      )}
    </div>
  );
}

type NodeKind = "done" | "current" | "upcoming" | "step-done" | "step-now" | "step-next";
type GraphNode = { key: string; kind: NodeKind; label: string; dashed?: boolean; srAdded?: string; stepId?: string };

/** Points (landmarks) in a chain; under each landmark its steps, smaller and paler. Only steps: they form the chain themselves */
function graphNodes(plan: SessionPlanView, addedLabel: string): GraphNode[] {
  const activeIds = new Set<string>([
    ...(plan.current_step ? [plan.current_step.id] : []),
    ...plan.next_steps.map((s) => s.id),
  ]);
  const doneSteps = (plan.done_steps ?? []).filter((s) => !activeIds.has(s.id));

  if (!plan.landmarks.length) {
    const out: GraphNode[] = doneSteps.map((s) => ({ key: `done:${s.id}`, kind: "done", label: s.text, stepId: s.id }));
    if (plan.current_step) {
      out.push({ key: `cur:${plan.current_step.id}`, kind: "current", label: plan.current_step.text, stepId: plan.current_step.id });
    }
    for (const s of plan.next_steps) {
      out.push({ key: `next:${s.id}`, kind: "upcoming", label: s.text, stepId: s.id });
    }
    return out;
  }

  const landmarkIds = new Set(plan.landmarks.map((l) => l.id));
  const doneByLandmark = new Map<string | null, PlanStep[]>();
  for (const s of doneSteps) {
    const bucketKey = s.landmark_id && landmarkIds.has(s.landmark_id) ? s.landmark_id : null;
    const list = doneByLandmark.get(bucketKey);
    if (list) list.push(s);
    else doneByLandmark.set(bucketKey, [s]);
  }
  const orphanDone = doneByLandmark.get(null) ?? [];
  const hasCurrentLandmark = plan.landmarks.some((l) => l.status === "current");

  const out: GraphNode[] = [];
  plan.landmarks.forEach((l, idx) => {
    out.push({
      key: `${l.kind}:${l.id}`,
      kind: l.status,
      label: l.title,
      dashed: l.added_by_teacher,
      srAdded: l.added_by_teacher ? addedLabel : undefined,
    });
    for (const s of doneByLandmark.get(l.id) ?? []) {
      out.push({ key: `done:${s.id}`, kind: "step-done", label: s.text, stepId: s.id });
    }
    if (l.status === "current" || (!hasCurrentLandmark && idx === plan.landmarks.length - 1)) {
      for (const s of orphanDone) {
        out.push({ key: `done:${s.id}`, kind: "step-done", label: s.text, stepId: s.id });
      }
    }
    if (l.status === "current") {
      if (plan.current_step) {
        out.push({ key: `cur:${plan.current_step.id}`, kind: "step-now", label: plan.current_step.text, stepId: plan.current_step.id });
      }
      for (const s of plan.next_steps) {
        out.push({ key: `next:${s.id}`, kind: "step-next", label: s.text, stepId: s.id });
      }
    }
  });
  return out;
}

function PlanWindow({
  plan,
  stale,
  full,
  fullRef,
  onFull,
  onFold,
  onOpenDemo,
}: {
  plan: SessionPlanView;
  stale: boolean;
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
  const open = (d: PlanDemo) => onOpenDemo?.({ requestId: d.request_id, title: d.title });
  const hasCurNode = nodes.some((n) => n.kind === "current");

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
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="caps text-paper">{p.heading}</span>
            {plan.progress && <span className="caps text-paper-dim">{p.liveProgress(plan.progress.done, plan.progress.total)}</span>}
            {stale && <span role="status" className="caps text-st-confused">{p.stale}</span>}
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
            <Node
              key={n.key}
              node={n}
              big={full}
              isCurrentTarget={n.kind === "current" || (!hasCurNode && n.kind === "step-now")}
              demo={n.stepId ? demos.find((d) => d.step_id === n.stepId) : undefined}
              onOpen={open}
            />
          ))}
        </ol>
      </div>
    </section>
  );
}

function Node({
  node,
  big,
  isCurrentTarget,
  demo,
  onOpen,
}: {
  node: GraphNode;
  big: boolean;
  isCurrentTarget: boolean;
  demo?: PlanDemo;
  onOpen: (d: PlanDemo) => void;
}) {
  const tx = useT();
  const p = tx.plan;
  const step = node.kind === "step-done" || node.kind === "step-now" || node.kind === "step-next";
  const done = node.kind === "done" || node.kind === "step-done";
  const dim = node.kind === "upcoming" || node.kind === "step-next";
  return (
    <li className={cn("relative flex", step && (big ? "ml-9" : "ml-6"))}>
      {step && <span aria-hidden className={cn("absolute top-1/2 h-px bg-seam", big ? "-left-5 w-5" : "-left-2.5 w-2.5")} />}
      <div
        data-cur={isCurrentTarget ? "" : undefined}
        className={cn(
          "relative flex min-w-0 flex-1 items-start gap-2 rounded-[2px] border px-2.5",
          step ? (big ? "py-2 text-small" : "py-1 text-[0.8125rem] leading-snug") : big ? "py-3 text-body" : "py-1.5 text-small",
          done && "border-seam bg-cloth-deep text-paper",
          node.kind === "current" && "border-ribbon bg-ribbon-soft font-medium text-paper",
          node.kind === "step-now" && "border-seam bg-cloth-deep text-paper",
          dim && "border-seam-soft bg-cloth text-paper-dim",
          node.dashed && node.kind !== "current" && "border-dashed",
        )}
      >
        {done && <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-paper-dim" />}
        <span className={cn("min-w-0 [overflow-wrap:anywhere]", !big && "line-clamp-2")}>
          {step && (
            <span className="sr-only">
              {node.kind === "step-done" ? p.doneStep : node.kind === "step-now" ? p.currentStep : p.nextStep}:{" "}
            </span>
          )}
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
