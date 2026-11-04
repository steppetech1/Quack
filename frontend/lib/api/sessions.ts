import { idem, post, type S } from "./client";

export type Intent = S["SessionIntent"];

/** Starting a session on the backend (it closes the previous active one). No `mode` and no `goal`: the first message is a normal action */
export function startSession(body: { domain_id: string; intent: Intent; topic_id?: string | null; hub_id?: string | null }) {
  return post("/v1/sessions", { ...body, idempotency_key: idem() });
}
