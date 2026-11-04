"use client";

import { useEffect, useMemo, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import type { Canvas } from "@/lib/mock/types";
import { API_MODE } from "./client";
import { listDomainCanvases, readCanvasTitle, type CanvasView } from "./canvases";

/** A backend canvas in the shape the list draws (blocks are empty: strokes live in the journal, not in the doc) */
function toListItem(c: CanvasView, domainId: string, untitled: string): Canvas {
  return {
    id: c.canvas_id,
    domainId,
    title: readCanvasTitle(c.canvas_id) || untitled,
    date: c.updated_at.slice(0, 10),
    topics: [],
    sessionId: c.session_id ?? undefined,
    solved: "no",
    sketch: "ratio",
    shape: "square",
    blocks: [],
  };
}

/** The canvases of a subject from the backend; an empty list on mocks (they have their own store) */
export function useApiCanvases(domainId: string): Canvas[] {
  const untitled = useT().canvases.untitled;
  const [loaded, setLoaded] = useState<{ domainId: string; items: CanvasView[] } | null>(null);

  useEffect(() => {
    if (!API_MODE || !domainId) return;
    let off = false;
    listDomainCanvases(domainId)
      .then((r) => !off && setLoaded({ domainId, items: r.canvases }))
      .catch(() => !off && setLoaded({ domainId, items: [] }));
    return () => {
      off = true;
    };
  }, [domainId]);

  return useMemo(
    () => (loaded && loaded.domainId === domainId ? loaded.items.map((c) => toListItem(c, domainId, untitled)) : []),
    [loaded, domainId, untitled],
  );
}
