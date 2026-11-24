"use client";

import { useSyncExternalStore } from "react";

/**
 * Skill drafts the teacher proposed in the chat (`skill_proposal` elements). The card in the chat and the same draft
 * opened in the workspace pane edit ONE draft: this small store keeps what the learner changed (name, binding, text) and
 * where the save ended, keyed by `draftKey(turnSeq, elementId)`. It lives in memory: after a reload the card starts from
 * the proposal in the journal again, and «saved» is read from the learner's skills.
 */
export type Draft = {
  name: string;
  text: string;
  /** `notes` | `cards` | `cheat` — the UI's own names (`cheatsheet` on the backend) or `null` */
  binding: "notes" | "cards" | "cheat" | null;
  /** the name the skill was saved under (the learner may have changed it on the card) */
  savedName?: string;
};

export const draftKey = (turnSeq: number | string, elementId: string) => `${turnSeq}:${elementId}`;

const drafts = new Map<string, Draft>();
const subs = new Set<() => void>();
const subscribe = (f: () => void) => {
  subs.add(f);
  return () => void subs.delete(f);
};

export const readDraft = (key: string): Draft | undefined => drafts.get(key);

export function patchDraft(key: string, base: Draft, patch: Partial<Draft>) {
  drafts.set(key, { ...(drafts.get(key) ?? base), ...patch });
  subs.forEach((f) => f());
}

/** The draft being edited: the learner's changes over the proposal */
export function useDraft(key: string, base: Draft): [Draft, (patch: Partial<Draft>) => void] {
  const edited = useSyncExternalStore(subscribe, () => drafts.get(key), () => undefined);
  return [edited ?? base, (patch) => patchDraft(key, base, patch)];
}
