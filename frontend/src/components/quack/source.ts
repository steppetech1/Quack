"use client";

// Where the Quack state comes from. The screens call useQuack() and never know which source runs:
//   local  — the browser recomputes on every change (default, works without a backend);
//   remote — the backend recomputes and pushes the state over SSE.
// Switch with NEXT_PUBLIC_QUACK_SOURCE=remote and NEXT_PUBLIC_API_URL=<api base>.

import { useSyncExternalStore } from "react";
import { store } from "../account/store";
import { EMPTY_STATE, type QuackState } from "./contract";
import { localSource } from "./localSource";
import { remoteSource } from "./remoteSource";
import type { QuackInputs } from "./standing";

export interface QuackSource {
  subscribe(listener: () => void): () => void;
  getSnapshot(): QuackState;
  /** A source of truth changed on this device: pass the slices that changed. */
  report(change: Partial<QuackInputs>): void;
  /** The student opened Quack and saw the state: what is fresh becomes history, the glow goes out. */
  markSeen(): void;
  /** «Начать заново»: forget the baseline and the history. */
  reset(): void;
  /** Take a backend recommendation: the plan changes server-side. Absent for the local source. */
  accept?(id: string): Promise<void>;
  /** Turn one down, with the student's reason when they gave one. */
  decline?(id: string, reason?: string): Promise<void>;
}

let instance: QuackSource | null = null;

/**
 * What the browser computed back when it was the one computing. The server keeps its own baseline and
 * its own history, so these are not a fallback — they are last year's numbers waiting to be mistaken
 * for this year's. Dropped once, on the first read with the backend behind us.
 */
const LOCAL_ONLY_KEYS = ["quack-baseline", "quack-history", "quack-known"];

export function quackSource(): QuackSource {
  if (!instance) {
    // The domain flag wins over the general one, as it does in prep/remoteFlag.ts; without either the
    // browser recomputes, so the app still builds and runs with no backend at all.
    const flag = process.env.NEXT_PUBLIC_QUACK_SOURCE ?? process.env.NEXT_PUBLIC_DATA_SOURCE;
    if (flag === "remote") {
      for (const key of LOCAL_ONLY_KEYS) if (store.get(key) !== null) store.set(key, null);
      instance = remoteSource();
    } else {
      instance = localSource();
    }
  }
  return instance;
}

const serverSnapshot = () => EMPTY_STATE;

/** The Quack state for a screen, plus the two things a screen can tell it. */
export function useQuack() {
  const source = quackSource();
  const state = useSyncExternalStore(source.subscribe, source.getSnapshot, serverSnapshot);
  return {
    state,
    report: source.report,
    markSeen: source.markSeen,
    reset: source.reset,
    /** Only the remote source can decide a recommendation; local signals have nothing to post */
    decide: source.accept && source.decline ? { accept: source.accept, decline: source.decline } : null,
  };
}
