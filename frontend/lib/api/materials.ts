import type { S } from "./client";

/** What the file picker offers: the backend takes a PDF, an image or a PowerPoint presentation, the type is checked by content (`upload_max_mb`, 50 by default) */
export const MATERIAL_ACCEPT =
  "application/pdf,image/*,.pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation";

export type MaterialState = "processing" | "ready" | "failed";

/**
 * `MaterialView.status` is a free string. The backend writes `uploaded` on upload and `parsed` when the parser is done;
 * a failure word is not fixed in the contract, so any `failed`/`error` is read as a failure and every other
 * unknown word as "still going" (the poll stops on its own after `MATERIAL_POLL_LIMIT`).
 */
export function materialState(status: string): MaterialState {
  const s = status.toLowerCase();
  if (s === "parsed" || s === "ready") return "ready";
  if (s === "failed" || s === "error" || s === "rejected") return "failed";
  return "processing";
}

/**
 * The text of the file is stored and the teacher can read it: `read` (the graph work on it may still be going, or may have
 * failed) or `parsed` (all done). The attachment of a message waits for exactly this, not for `parsed`.
 */
export function materialReadable(status: string): boolean {
  const s = status.toLowerCase();
  return s === "read" || s === "parsed" || s === "ready";
}

/** Poll period and number of tries while something is processing */
export const MATERIAL_POLL_MS = 4000;
export const MATERIAL_POLL_LIMIT = 90;

export type MaterialView = S["MaterialView"];
