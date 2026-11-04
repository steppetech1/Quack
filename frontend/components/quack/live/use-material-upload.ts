"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useData } from "@/lib/api/data";
import { listMaterials, MATERIAL_PARSED, uploadMaterial } from "@/lib/api/materials";
import { useErrorText } from "./error-text";

export type MaterialPhase = "idle" | "uploading" | "parsing" | "done" | "slow" | "failed";

const POLL_MS = 4000;
/** Reading a long PDF takes a while; after this the line says it is slow, and the graph still updates from the stream */
const GIVE_UP_MS = 5 * 60_000;

/**
 * Attach a file to a subject: upload, then wait for the backend to read it (the status of the material),
 * then re-read the graph. The graph also refreshes by itself when the projection advances.
 */
export function useMaterialUpload(domainId: string) {
  const { loadGraph, reload } = useData();
  const errorText = useErrorText();
  const [phase, setPhase] = useState<MaterialPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const run = useRef(0); // a newer upload cancels the wait of the older one

  useEffect(
    () => () => {
      run.current += 1;
    },
    [],
  );

  const attach = useCallback(
    async (file: File) => {
      const mine = ++run.current;
      setError(null);
      setPhase("uploading");
      try {
        const m = await uploadMaterial(domainId, file);
        if (run.current !== mine) return;
        setPhase("parsing");
        const started = Date.now();
        for (;;) {
          await new Promise((r) => setTimeout(r, POLL_MS));
          if (run.current !== mine) return;
          const { materials } = await listMaterials(domainId);
          if (materials.find((x) => x.material_id === m.material_id)?.status === MATERIAL_PARSED) break;
          if (Date.now() - started > GIVE_UP_MS) {
            setPhase("slow");
            return;
          }
        }
        await Promise.all([loadGraph(domainId), reload()]);
        if (run.current === mine) setPhase("done");
      } catch (e) {
        if (run.current !== mine) return;
        setError(errorText(e));
        setPhase("failed");
      }
    },
    [domainId, loadGraph, reload, errorText],
  );

  return { phase, error, attach };
}
