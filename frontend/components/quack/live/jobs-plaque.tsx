"use client";

import { AlertCircle, Check } from "lucide-react";
import Link from "next/link";
import type { FinishedJob, SessionJob } from "@/lib/api/use-session-jobs";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { Duck } from "../duck";

/** Kinds whose result lives on the topic page, not in the session feed */
const TOPIC_RESULT = new Set(["agent.artifact", "agent.topic_split", "agent.task_generation", "agent.solution_space"]);

type Row = { id: string; kind: string; state: "queued" | "running" | "done" | "failed"; count: number };

function rows(jobs: SessionJob[], finished: FinishedJob[]): Row[] {
  const out = new Map<string, Row>();
  const add = (kind: string, state: Row["state"]) => {
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
 * The plaque of background agents of a session: «утка готовит демо» while a job runs, then «готово: демо» for a few
 * seconds. Nothing is rendered when there is nothing to say.
 */
export function JobsPlaque({ jobs, finished, topicId, className }: { jobs: SessionJob[]; finished: FinishedJob[]; topicId?: string | null; className?: string }) {
  const tx = useT();
  const s = tx.session;
  const list = rows(jobs, finished);
  if (!list.length) return null;
  return (
    <ul role="status" aria-label={s.jobsLabel} className={cn("flex flex-wrap gap-2", className)}>
      {list.map((r) => {
        const noun = s.jobNoun[r.kind] ?? s.jobNounGeneric;
        const text =
          r.state === "running"
            ? (s.jobRunning[r.kind] ?? s.jobGeneric)
            : r.state === "queued"
              ? s.jobQueued(noun)
              : r.state === "done"
                ? s.jobDone(noun)
                : s.jobFailed(noun);
        const active = r.state === "queued" || r.state === "running";
        return (
          <li
            key={r.id}
            className={cn(
              "caps flex min-h-8 items-center gap-2 rounded-pill border border-seam-soft px-3 py-1 text-paper-dim",
              r.state === "failed" && "text-st-confused",
            )}
          >
            {r.state === "done" ? <Check aria-hidden className="size-3.5" /> : r.state === "failed" ? <AlertCircle aria-hidden className="size-3.5" /> : <Duck className="h-3.5 w-4" />}
            <span>
              {text}
              {r.count > 1 && ` ×${r.count}`}
            </span>
            {r.state === "running" && <Dots />}
            {!active && r.state === "done" && topicId && TOPIC_RESULT.has(r.kind) && (
              <Link href={`/topic/${topicId}`} className="-my-1 inline-flex min-h-10 items-center text-ribbon underline-offset-2 hover:underline">
                {s.jobOpenTopic}
              </Link>
            )}
          </li>
        );
      })}
    </ul>
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
