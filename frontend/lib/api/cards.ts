import type { CardKind, StudyCard } from "@/lib/mock/types";
import { ApiError, idem, sse, type S } from "./client";

/** Kinds of the backend (`CardKind`, Т-§17.5) → the kinds the deck was drawn for */
const KIND: Record<string, CardKind> = {
  definition: "definition",
  sign_to_method: "sign",
  distinction: "distinction",
  against_misconception: "myth",
};

/**
 * `GET /v1/topics/{id}/cards` → the cards of the deck. The backend card is `{card_id, kind, prompt, owner}`: the front is the
 * prompt. The task behind the card keeps its answer, and the API does not show it: the back is `answer` when the response
 * has one, else empty (the deck then says so and the learner rates how it went by themself, the «recall and reveal» card).
 */
export function studyCardsFromApi(res: S["CardsResponse"]): StudyCard[] {
  return res.cards.map((c) => ({
    id: String(c.card_id),
    kind: KIND[String(c.kind)] ?? "definition",
    front: String(c.prompt ?? ""),
    back: typeof c.answer === "string" ? c.answer : "",
    mine: c.owner === "learner",
  }));
}

/** Waits between tries while the last teacher turn still holds the session (ms) */
const TURN_RETRY_MS = [800, 1_600, 3_200];
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * One card answered, as the contract has it: the session action `answer` with `source: "card"` (a card is a task of the bank,
 * `card_id` is its `task_id`). A «recall and reveal» card has no check of its own: `self_assessment` is the learner's
 * «I remembered» yes / no, and the text they typed (if any) is the answer. The action starts a teacher turn that the deck has
 * no place for, so its frames are read to the end and dropped. `TURN_LOCKED` (the last turn is still going) is tried again
 * with the same key, so a repeat never lands twice.
 */
export async function answerCard(sessionId: string, card: { id: string; remembered: boolean; text?: string }): Promise<void> {
  const text = card.text?.trim() ?? "";
  const body = {
    kind: "answer" as const,
    task_id: card.id,
    source: "card" as const,
    self_assessment: card.remembered,
    answer: { kind: "free_text" as const, value: text },
    ...(text ? { text } : {}),
    idempotency_key: idem(),
  };
  for (let attempt = 0; ; attempt++) {
    try {
      await sse("POST", `/v1/sessions/${sessionId}/actions`, body, ({ event, data }) => {
        if (event === "error") throw new ApiError(500, String(data.code ?? "ERROR"), String(data.message ?? ""));
      });
      return;
    } catch (e) {
      if (!(e instanceof ApiError) || e.code !== "TURN_LOCKED" || attempt >= TURN_RETRY_MS.length) throw e;
      await wait(TURN_RETRY_MS[attempt]);
    }
  }
}
