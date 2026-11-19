import type { CardKind, StudyCard } from "@/lib/mock/types";
import type { S } from "./client";

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
