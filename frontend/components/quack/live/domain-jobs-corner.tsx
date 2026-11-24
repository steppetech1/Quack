"use client";

import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";
import { type JobRow, JobsLines } from "./jobs-plaque";

const WIDE = "(min-width: 768px)";
const subscribeWide = (cb: () => void) => {
  const mq = window.matchMedia(WIDE);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
};

/**
 * The agents that grow the graph of this subject, bottom right of the subject page: the same plain lines as in a session
 * (`JobsLines`). `where="graph"`: in the bottom right corner of the graph area (put it in a `relative` box around the graph);
 * `where="input"`: right above the input row (a `relative` box that holds that row), for the narrow screen and for a subject
 * with no graph yet. Nothing is drawn when there is nothing to say. Narrow screen: one line, the most active file.
 */
export function DomainJobsCorner({ rows, where, className }: { rows: JobRow[]; where: "graph" | "input"; className?: string }) {
  const wide = useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE).matches, () => true);
  if (!rows.length) return null;
  return (
    <div
      className={cn(
        "pointer-events-none absolute z-10 w-72 max-w-[calc(100%-2rem)]",
        where === "graph" ? "right-3 bottom-3" : "right-4 bottom-full mb-1 md:right-7",
        className,
      )}
    >
      <JobsLines rows={rows} compact={!wide} className="pointer-events-auto w-full rounded-[2px] bg-cloth/90 px-1" />
    </div>
  );
}
