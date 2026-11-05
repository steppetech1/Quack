"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, get, idem, patch, post, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useT } from "@/lib/i18n/provider";
import { useNotice } from "../soon";
import { useErrorText } from "./error-text";

/** A hub of the backend has a uuid; "all" and the synthetic `all-<domain>` chapter of a subject without chapters do not */
const REAL_HUB = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
    get<S["MaterialList"]>(`/v1/domains/${domainId}/materials`)
      .then((r) => !off && setMaterials(r.materials))
      .catch(() => {});
    return () => {
      off = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [real, hubId, domainId, appliedSeq]);

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

  return { real, cheat, materials, busy, buildCheat, prep, setDeadline };
}
