// The topic's theory and worked explanations, generated per student on the backend (phase 4 §3.5).
//
//   GET  /texts/{set}/{skill}?kind=guideline|explanation  → the text and its status
//   POST /texts/{set}/{skill}/opened  {kind}              → 204, feeds `after_guideline` and activity
//   POST /texts/{set}/{skill}/regenerate?kind=            → 202, after a failed row
//
// The GET writes nothing and generates nothing: it reports what the cache holds and queues the job
// for what is missing. So `generating` is answered by asking again, with a growing pause — never by
// a spinner that turns forever.

import { backend } from "@/api/backend";
import { ApiError, isUuid } from "@/api/client";
import type { components } from "@/api/schema";
import { REMOTE_PREP } from "./remoteSets";

export type TextKind = "guideline" | "explanation";
export type RemoteText = components["schemas"]["GeneratedTextOut"];
export type TextStatus = RemoteText["status"];

/** Ask again after 2s, then 4, 8, 16, and settle at 30 — the job is a background one */
export const RETRY_MS = [2_000, 4_000, 8_000, 16_000, 30_000] as const;
export const retryDelay = (attempt: number) => RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)];

/** Waiting on a job forever is not waiting, it is a broken screen. After this the student is told. */
export const GIVE_UP_MS = 30_000;

/** How the student hears the backend's `mark` */
export const MARK_WORD: Record<string, string> = {
  generated: "сгенерировано",
  saved_version: "сохранённая версия",
};

export async function fetchText(setId: string, skillId: string, kind: TextKind): Promise<RemoteText | null> {
  if (!REMOTE_PREP || !isUuid(setId)) return null;
  try {
    return await backend.texts.get(setId, skillId, kind);
  } catch (err) {
    // 404 is an ordinary answer: the set or the topic is not the server's to talk about
    if (!(err instanceof ApiError && err.status === 404)) {
      console.warn("Failed to read the topic text:", setId, skillId, kind, err);
    }
    return null;
  }
}

/**
 * The student opened the theory. This is not bookkeeping: `after_guideline` on the next answer and
 * the activity calendar both read this event, so it is sent even when the text came from the cache.
 */
export async function markTextOpened(setId: string, skillId: string, kind: TextKind): Promise<void> {
  if (!REMOTE_PREP || !isUuid(setId)) return;
  try {
    await backend.texts.opened(setId, skillId, kind);
  } catch (err) {
    console.warn("Failed to record the opened text:", setId, skillId, kind, err);
  }
}

export async function regenerateText(setId: string, skillId: string, kind: TextKind): Promise<boolean> {
  if (!REMOTE_PREP || !isUuid(setId)) return false;
  try {
    await backend.texts.regenerate(setId, skillId, kind);
    return true;
  } catch (err) {
    console.warn("Failed to ask for a new text:", setId, skillId, kind, err);
    return false;
  }
}
