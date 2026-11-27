"use client";

import { API_MODE } from "@/lib/api/client";
import { CanvasView } from "../canvas-view";
import { CanvasLive } from "../live/canvas-live";
import type { WorkspaceDoc } from "./workspace-context";

/**
 * A canvas of the session inside the pane (`id` is the canvas id; the button of the session finds or opens it before the tab is made,
 * so a tab that is opened again never makes a canvas of its own). The same board as the full page, with a strip for the title and a
 * link to the full page instead of the corner with the way back.
 */
export function CanvasDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "canvas" }> }) {
  return API_MODE ? <CanvasLive id={doc.id} embedded /> : <CanvasView id={doc.id} embedded />;
}
