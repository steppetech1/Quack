"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useData } from "./data";
import { ApiError, get, type S } from "./client";

/**
 * Background agents of a session ("the plaque", Т-§13.8): `GET /v1/sessions/{id}/jobs` returns the visible jobs that are
 * `queued` or `running`, plus those that ended in the last few minutes as `succeeded` or `failed`. "Finished" here means
 * "was active, now is `succeeded`/`failed` or gone from the list" (an older backend only drops it: that reads as done).
 *
 * Refresh: `jobs_changed` of `/v1/learner/stream` (through `DataProvider`), a reconnect of that stream, and polling while
 * something is queued or running. After a turn, `refresh()` also polls a few times even when the list is empty: the
 * dispatcher enqueues jobs after the turn is committed, and without Redis there is no signal at all.
 */

/** Poll interval while at least one job is queued or running */
const POLL_MS = 4_000;
/** Interval and count of the polls after `refresh()` while the list is still empty */
const BURST_MS = 2_500;
const BURST_COUNT = 5;
/** How long a finished job stays on the plaque */
const FINISHED_MS = 12_000;

export type SessionJob = S["SessionJob"];
export type FinishedJob = { key: string; kind: string; failed: boolean };

const isActive = (j: SessionJob) => j.status === "queued" || j.status === "running";

export function useSessionJobs(sessionId: string, opts: { onDone?: (kinds: string[]) => void } = {}) {
  const { subscribeJobs } = useData();
  const [jobs, setJobs] = useState<SessionJob[]>([]);
  const [finished, setFinished] = useState<FinishedJob[]>([]);
  const onDone = useRef(opts.onDone);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const burst = useRef(0);
  const known = useRef(new Map<string, string>());
  const request = useRef(0);
  const alive = useRef(true);
  const again = useRef<() => void>(() => {});

  useEffect(() => {
    onDone.current = opts.onDone;
  });

  const load = useCallback(async () => {
    clearTimeout(timer.current);
    const mine = ++request.current;
    let next: SessionJob[] = [];
    let stop = false;
    try {
      next = (await get<S["SessionJobsResponse"]>(`/v1/sessions/${sessionId}/jobs`)).jobs;
    } catch (e) {
      // a session that isn't there has no jobs, and asking again won't change that
      if (e instanceof ApiError && (e.status === 404 || e.status === 422)) stop = true;
      else next = [...known.current.entries()].map(([job_key, kind]) => ({ job_key, kind, status: "running", created_at: "" }));
    }
    // an older answer that came back late must not undo a newer one
    if (!alive.current || mine !== request.current) return;

    const now = new Map(next.map((j) => [j.job_key, j.kind] as const));
    const gone: FinishedJob[] = [];
    for (const [key, kind] of known.current) if (!now.has(key)) gone.push({ key, kind, failed: false });
    for (const j of next) if (!isActive(j) && known.current.has(j.job_key)) gone.push({ key: j.job_key, kind: j.kind, failed: j.status !== "succeeded" });
    known.current = new Map(next.filter(isActive).map((j) => [j.job_key, j.kind] as const));
    setJobs(next.filter(isActive));
    if (gone.length) {
      setFinished((f) => [...f.filter((x) => !gone.some((g) => g.key === x.key)), ...gone]);
      for (const g of gone) {
        setTimeout(() => alive.current && setFinished((f) => f.filter((x) => x.key !== g.key)), FINISHED_MS);
      }
      onDone.current?.(gone.map((g) => g.kind));
    }

    if (stop) return;
    const wait = known.current.size ? POLL_MS : burst.current > 0 ? BURST_MS : 0;
    if (!known.current.size && burst.current > 0) burst.current -= 1;
    if (wait) timer.current = setTimeout(() => again.current(), wait);
  }, [sessionId]);

  useEffect(() => {
    again.current = () => void load();
  }, [load]);

  // First read on open: after a page reload this is how running jobs show up again
  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
    };
  }, [load]);

  useEffect(
    () =>
      subscribeJobs((sid) => {
        if (sid === null || sid === sessionId) void load();
      }),
    [subscribeJobs, sessionId, load],
  );

  /** Call after a turn or an action: new background jobs usually appear a moment later */
  const refresh = useCallback(() => {
    burst.current = BURST_COUNT;
    void load();
  }, [load]);

  return { jobs, finished, refresh };
}
