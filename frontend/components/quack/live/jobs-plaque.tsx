"use client";

import Link from "next/link";
import type { FinishedJob, SessionJob } from "@/lib/api/use-session-jobs";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/** Kinds whose result lives on the topic page, not in the session feed */
const TOPIC_RESULT = new Set(["agent.artifact", "agent.topic_split", "agent.task_generation", "agent.solution_space"]);

/** `label`: a ready line of text (a file of a subject: «Reading lecture.pdf»); without it the line is named by `kind` */
export type JobRow = { id: string; kind: string; state: "queued" | "running" | "done" | "failed"; count: number; label?: string };

export function jobRows(jobs: SessionJob[], finished: FinishedJob[]): JobRow[] {
  const out = new Map<string, JobRow>();
  const add = (kind: string, state: JobRow["state"]) => {
    const id = `${state}:${kind}`;
    const row = out.get(id);
    if (row) row.count += 1;
    else out.set(id, { id, kind, state, count: 1 });
  };
  for (const j of jobs) add(j.kind, j.status === "queued" ? "queued" : "running");
  for (const f of finished) add(f.kind, f.failed ? "failed" : "done");
  return [...out.values()];
}

/**
 * A duck's webbed foot, a solid silhouette: three toe tips on top with concave web arcs between them, narrowing
 * to a rounded heel (a shield). No inner detail. Colour comes from `currentColor`, tilted a little; a running job
 * paddles (`.paw-busy` in globals.css, only when motion is welcome).
 */
export function Paw({ className, busy }: { className?: string; busy?: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      fill="currentColor"
      className={cn("size-4 shrink-0", busy && "paw-busy", className)}
      style={{ transform: "rotate(-12deg)" }}
    >
      <path d="M8 14.9C7.1 14.9 6.3 14.1 5.7 12.8L2 6.6C1.4 5.6 2.2 4.2 3.3 4.4Q5 6.9 6.8 3.5C7.2 1.7 8.8 1.7 9.2 3.5Q11 6.9 12.7 4.4C13.8 4.2 14.6 5.6 14 6.6L10.3 12.8C9.7 14.1 8.9 14.9 8 14.9Z" />
    </svg>
  );
}

const MAX_ROWS = 3;

function activeFirst(rows: JobRow[]): JobRow[] {
  const rank = (r: JobRow) => (r.state === "running" ? 0 : r.state === "queued" ? 1 : r.state === "failed" ? 2 : 3);
  return [...rows].sort((a, b) => rank(a) - rank(b));
}

/**
 * Background agents of a session as plain lines of text, no card: a foot, what the duck is doing, a small status.
 * `compact` (narrow screen): one line, the most active job, «+N more» in place of the status when there are several.
 * Nothing is rendered when there is nothing to say.
 */
export function JobsLines({ rows, topicId, compact, className }: { rows: JobRow[]; topicId?: string | null; compact?: boolean; className?: string }) {
  const tx = useT();
  const s = tx.session;
  if (!rows.length) return null;
  const sorted = activeFirst(rows);
  const shown = compact ? sorted.slice(0, 1) : sorted.slice(0, MAX_ROWS);
  const more = sorted.length - shown.length;
  return (
    <section aria-label={s.jobsLabel} className={cn("text-small", className)}>
      <ul role="status" className="flex flex-col">
        {shown.map((r, i) => {
          const text = r.label ?? s.jobDoing[r.kind] ?? s.jobDoingGeneric;
          const tag = r.state === "queued" ? s.jobTagQueued : r.state === "done" ? s.jobTagDone : r.state === "failed" ? s.jobTagFailed : null;
          return (
            <li key={r.id} className={cn("flex min-h-6 items-center gap-1.5", r.state === "running" ? "text-paper" : "text-paper-dim", r.state === "failed" && "text-st-confused")}>
              <Paw busy={r.state === "running"} />
              <span className="min-w-0 flex-1 truncate">
                {text}
                {r.count > 1 && ` ×${r.count}`}
              </span>
              {r.state === "running" && <Dots />}
              {tag && <span className="caps shrink-0 text-[0.6875rem]">{tag}</span>}
              {compact && more > 0 && i === 0 && <span className="caps shrink-0 text-[0.6875rem] text-paper-dim">{s.jobsMore(more)}</span>}
              {r.state === "done" && topicId && TOPIC_RESULT.has(r.kind) && (
                <Link href={`/topic/${topicId}`} className="-my-2 inline-flex min-h-10 shrink-0 items-center text-ribbon underline-offset-2 hover:underline">
                  {s.jobOpenTopic}
                </Link>
              )}
            </li>
          );
        })}
        {!compact && more > 0 && (
          <li className="caps min-h-6 pl-[1.375rem] text-[0.6875rem] text-paper-dim">{s.jobsMore(more)}</li>
        )}
      </ul>
    </section>
  );
}

function Dots() {
  return (
    <span aria-hidden className="inline-flex gap-0.5 motion-reduce:hidden">
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1 animate-pulse rounded-pill bg-paper-dim" style={{ animationDelay: `${i * 180}ms` }} />
      ))}
    </span>
  );
}
