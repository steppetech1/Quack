"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { API_MODE, get, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { getUploads, getUploadsServer, subscribeUploads } from "@/lib/api/domain-uploads";
import { MATERIAL_POLL_LIMIT, MATERIAL_POLL_MS, type MaterialView, materialState } from "@/lib/api/materials";
import { useT } from "@/lib/i18n/provider";
import type { JobRow } from "./jobs-plaque";

/** How long a finished line stays: a failure longer, so it is seen */
const DONE_MS = 12_000;
const FAILED_MS = 30_000;

type Finished = { id: string; name: string; failed: boolean };

/**
 * The agents that grow the graph of a subject, one line per file. A line is born when a file starts uploading (the queue of
 * `lib/api/domain-uploads`, files chosen on the «new subject» card) or when the backend has a material of the subject that
 * is not done: `uploaded` is «reading», `read` is «building the graph», `parsed` is done (`GET /v1/domains/{id}/materials`,
 * there is no separate jobs endpoint for a subject). Finished files stay a few seconds, then go.
 *
 * Polling and graph re-reads run only while something is in progress: the materials list every `MATERIAL_POLL_MS`, and the graph
 * of the subject next to it (the learner stream already says «the projection moved», this covers a stream that is down).
 * On mocks there is no backend: the lines come from the queue alone (development handle).
 */
export function useDomainJobs(domainId: string): { rows: JobRow[]; active: boolean } {
  const t = useT().live;
  const { loadGraph } = useData();
  const uploads = useSyncExternalStore(subscribeUploads, getUploads, getUploadsServer);
  const [listed, setListed] = useState<{ domainId: string; items: MaterialView[] }>({ domainId: "", items: [] });
  const [finished, setFinished] = useState<Finished[]>([]);
  const seen = useRef(new Map<string, string>());
  const doneUploads = useRef(new Set<string>());
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const mine = useMemo(() => uploads.filter((u) => u.domainId === domainId), [uploads, domainId]);
  const materials = useMemo(() => {
    const byId = new Map<string, MaterialView>();
    for (const u of mine) if (u.material) byId.set(u.material.material_id, u.material);
    // the backend's answer is newer than what the upload returned
    if (listed.domainId === domainId) for (const m of listed.items) byId.set(m.material_id, m);
    return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [mine, listed, domainId]);

  const waiting = mine.filter((u) => u.state === "queued" || u.state === "uploading");
  const processing = materials.filter((m) => materialState(m.status) === "processing");
  const active = waiting.length > 0 || processing.length > 0;
  const sent = mine.filter((u) => u.state === "sent").length;

  // A line that was active and is not any more: done or failed, for a while; the graph is read once more
  useEffect(() => {
    const out: Finished[] = [];
    for (const m of materials) {
      const st = materialState(m.status);
      const name = m.title?.trim() || mine.find((u) => u.material?.material_id === m.material_id)?.name || "";
      if (st === "processing") seen.current.set(m.material_id, name);
      else if (seen.current.has(m.material_id)) {
        const before = seen.current.get(m.material_id) ?? "";
        seen.current.delete(m.material_id);
        out.push({ id: m.material_id, name: name || before, failed: st === "failed" });
      }
    }
    for (const u of mine) {
      if (u.state === "failed" && !doneUploads.current.has(u.id)) {
        doneUploads.current.add(u.id);
        out.push({ id: u.id, name: u.name, failed: true });
      }
    }
    if (!out.length) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFinished((f) => [...f.filter((x) => !out.some((o) => o.id === x.id)), ...out]);
    for (const o of out) setTimeout(() => alive.current && setFinished((f) => f.filter((x) => x.id !== o.id)), o.failed ? FAILED_MS : DONE_MS);
    if (out.some((o) => !o.failed) && domainId) void loadGraph(domainId).catch(() => {});
  }, [materials, mine, domainId, loadGraph]);

  // Ask the backend only while something is going on (and once on arrival, to find what is already going)
  useEffect(() => {
    if (!API_MODE || !domainId) return;
    let off = false;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const r = await get<S["MaterialList"]>(`/v1/domains/${domainId}/materials`);
        if (!off) setListed({ domainId, items: r.materials });
        const going = r.materials.some((m) => materialState(m.status) === "processing");
        if (going || active) void loadGraph(domainId).catch(() => {});
        if (off || !(going || active)) return;
      } catch {
        if (off || !active) return;
      }
      if (++tries <= MATERIAL_POLL_LIMIT) timer = setTimeout(() => void tick(), MATERIAL_POLL_MS);
    };
    void tick();
    return () => {
      off = true;
      clearTimeout(timer);
    };
  }, [domainId, active, sent, loadGraph]);

  const rows = useMemo<JobRow[]>(() => {
    const name = (n: string) => n || t.domainJobMaterial;
    const out: JobRow[] = [];
    for (const u of waiting) out.push({ id: u.id, kind: "material.parse", state: u.state === "queued" ? "queued" : "running", count: 1, label: t.domainJobUploading(name(u.name)) });
    for (const m of processing) {
      const n = name(m.title?.trim() || mine.find((u) => u.material?.material_id === m.material_id)?.name || "");
      out.push({ id: m.material_id, kind: "material.parse", state: "running", count: 1, label: m.status.toLowerCase() === "read" ? t.domainJobBuilding(n) : t.domainJobReading(n) });
    }
    for (const f of finished) out.push({ id: f.id, kind: "material.parse", state: f.failed ? "failed" : "done", count: 1, label: f.failed ? t.domainJobFailed(name(f.name)) : t.domainJobDone(name(f.name)) });
    return out;
  }, [waiting, processing, finished, mine, t]);

  return { rows, active };
}
