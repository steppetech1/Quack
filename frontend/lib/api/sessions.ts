import { idem, post, type S } from "./client";

/** Starting a session on the backend (it closes the previous active one) */
export function startSession(body: { domain_id: string; topic_id?: string | null; mode?: "learn" | "practice" | "review"; goal?: string | null }) {
  return post<S["SessionStarted"]>("/v1/sessions", { ...body, idempotency_key: idem() });
}

/** Session results for the summary screen: finish returns them once, the summary page reads them here */
const summaryKey = (id: string) => `quack.summary.${id}`;

export function saveSummary(id: string, data: S["FinishResponse"]) {
  try {
    sessionStorage.setItem(summaryKey(id), JSON.stringify(data));
  } catch {}
}

export function readSummary(id: string): S["FinishResponse"] | null {
  try {
    const raw = sessionStorage.getItem(summaryKey(id));
    return raw ? (JSON.parse(raw) as S["FinishResponse"]) : null;
  } catch {
    return null;
  }
}
