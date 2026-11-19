"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, get, idem, patch, post, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useT } from "@/lib/i18n/provider";
import { useNotice } from "../soon";
import { useErrorText } from "./error-text";

/** A hub of the backend has a uuid; "all" and the synthetic `all-<domain>` chapter of a subject without chapters do not */
const REAL_HUB = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const GRADE_DISMISSED_KEY = "quack.gradeDismissed.v1";

/**
 * What the chapter page takes from the backend: its cheat sheet, the materials of the subject, and the three
 * actions that were "soon": build the cheat sheet, deadline prep mode, change the deadline.
 */
export function useHubApi(hubId: string, domainId: string, enabled: boolean) {
  const t = useT().live;
  const notice = useNotice();
  const errorText = useErrorText();
  const { appliedSeq, loadGraph } = useData();
  const real = enabled && REAL_HUB.test(hubId);
  const [cheat, setCheat] = useState<S["CheatsheetResponse"] | null>(null);
  const [materials, setMaterials] = useState<S["MaterialView"][]>([]);
  const [busy, setBusy] = useState(false);
  /** The deadline has passed and no mark is entered yet: `grade_hub_ids` of `GET /v1/domains/{id}/today` (WP17.5) */
  const [gradeDue, setGradeDue] = useState(false);
  /** Chapters whose offer was dismissed with "Not now": the backend has no event for that, so it is kept in this browser */
  const [dismissed, setDismissed] = useState<string[]>([]);

  const loadCheat = useCallback(
    () =>
      get<S["CheatsheetResponse"]>(`/v1/hubs/${hubId}/cheatsheet`)
        .then(setCheat)
        .catch((e) => e instanceof ApiError && (e.status === 404 || e.status === 422) && setCheat(null)),
    [hubId],
  );

  // once per chapter, and again when the backend projection moves (a cheat sheet that was built appears)
  useEffect(() => {
    if (!real) return;
    let off = false;
    void loadCheat();
    get<S["TodayView"]>(`/v1/domains/${domainId}/today`)
      .then((r) => !off && setGradeDue((r.grade_hub_ids ?? []).includes(hubId)))
      .catch(() => {});
    get<S["MaterialList"]>(`/v1/domains/${domainId}/materials`)
      .then((r) => !off && setMaterials(r.materials))
      .catch(() => {});
    return () => {
      off = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [real, hubId, domainId, appliedSeq]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(GRADE_DISMISSED_KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (raw) setDismissed(JSON.parse(raw) as string[]);
    } catch {}
  }, []);

  const run = useCallback(
    async (job: () => Promise<unknown>, done: string) => {
      setBusy(true);
      try {
        await job();
        notice(done);
      } catch (e) {
        notice(errorText(e));
      } finally {
        setBusy(false);
      }
    },
    [notice, errorText],
  );

  const buildCheat = useCallback(
    () =>
      run(async () => {
        await post(`/v1/hubs/${hubId}/cheatsheet`, { idempotency_key: idem() });
        await loadCheat(); // it may already be there; the stream brings it later otherwise
      }, t.cheatQueued),
    [run, hubId, loadCheat, t.cheatQueued],
  );
  const prep = useCallback(() => run(() => post(`/v1/hubs/${hubId}/prep-mode`, { enabled: true }), t.prepOn), [run, hubId, t.prepOn]);
  /** `null` takes the deadline away */
  const setDeadline = useCallback(
    (ms: number | null) =>
      run(async () => {
        await patch(`/v1/hubs/${hubId}`, { deadline_at_ms: ms });
        await loadGraph(domainId);
      }, ms === null ? t.deadlineRemoved : t.deadlineSaved),
    [run, hubId, domainId, loadGraph, t.deadlineRemoved, t.deadlineSaved],
  );

  /** `POST /v1/hubs/{id}/grade {grade, scale}`: the hub stays, the mark is an event (the backend has no way to read it back) */
  const grade = useCallback(
    (mark: string, scale: string) =>
      run(async () => {
        await post(`/v1/hubs/${hubId}/grade`, { grade: mark, scale });
        setGradeDue(false);
      }, t.gradeSaved),
    [run, hubId, t.gradeSaved],
  );
  const dismissGrade = useCallback(() => {
    setDismissed((cur) => {
      const next = cur.includes(hubId) ? cur : [...cur, hubId];
      try {
        localStorage.setItem(GRADE_DISMISSED_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
  }, [hubId]);

  return { real, cheat, materials, busy, buildCheat, prep, setDeadline, gradeOffer: gradeDue && !dismissed.includes(hubId), grade, dismissGrade };
}
