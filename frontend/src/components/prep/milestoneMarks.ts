"use client";

// Ticking a milestone done — the one tick in the product (product-logic §4.1) — and picking the test date
// a registration belongs to. Both have several ways in with the same effect: the choice chat, the Quack
// calendar and feed, «Подготовка → Требования». Outside «Подготовки» they write the preparation state
// here and tell Quack at once, so the forecast and the signals follow.

import { useSyncExternalStore } from "react";
import { store } from "../account/store";
import { quackSource } from "../quack/source";
import type { DatedExam, TestDates } from "./prepData";
import { chooseTestDate, skipTest, type PrepModel } from "./prepModel";
import { markRemoteMilestone } from "./remotePrep";
import { rawPrepUi, readPrepUi, updatePrepModel } from "./prepStore";
import { REMOTE_PREP } from "./remoteFlag";
const NO_MARKS: string[] = [];
const NO_DATES: TestDates = {};
type Targets = NonNullable<PrepModel["targets"]>;
const NO_TARGETS: Targets = {};

const listeners = new Set<() => void>();
// Handed out as long as the stored state is the same object, so screens re-render only on a change
let lastRaw: unknown = undefined;
let marks: string[] = NO_MARKS;
let dates: TestDates = NO_DATES;
let targets: Targets = NO_TARGETS;
const NO_RESOLVED: Record<string, string> = {};
let resolved: Record<string, string> = NO_RESOLVED;

function refresh() {
  const raw = rawPrepUi();
  if (raw === lastRaw) return;
  lastRaw = raw;
  const ui = readPrepUi();
  marks = ui.milestonesDone ?? NO_MARKS;
  dates = ui.testDates ?? NO_DATES;
  targets = ui.targets ?? NO_TARGETS;
  resolved = ui.resolvedConflicts ?? NO_RESOLVED;
}

/** Conflicts the student settled with a way out, as stored now: id → the way chosen. */
export function resolvedConflicts(): Record<string, string> {
  refresh();
  return resolved;
}

export function useResolvedConflicts(): Record<string, string> {
  return useSyncExternalStore(subscribe, resolvedConflicts, () => NO_RESOLVED);
}

/** Targets the student set by hand in «Требованиях», as stored now. */
export function chosenTargets(): Targets {
  refresh();
  return targets;
}

/** The hand-set targets for a screen outside «Подготовки»: Quack shows the same numbers. */
export function useChosenTargets(): Targets {
  return useSyncExternalStore(subscribe, chosenTargets, () => NO_TARGETS);
}

/** The ticked milestone ids, as stored now. */
export function doneMilestones(): string[] {
  refresh();
  return marks;
}

/** The test date picked per exam, as stored now. */
export function chosenTestDates(): TestDates {
  refresh();
  return dates;
}

function update(change: (model: PrepModel) => PrepModel) {
  const next = updatePrepModel(change);
  quackSource().report({ prep: next });
  listeners.forEach((listener) => listener());
}

/** Sets a milestone done (or not); the default flips it. Returns whether it is done afterwards. */
export function markMilestone(id: string, done?: boolean): boolean {
  const has = doneMilestones().includes(id);
  const want = done ?? !has;
  if (want !== has) {
    update((model) => ({
      ...model,
      milestonesDone: want ? [...model.milestonesDone, id] : model.milestonesDone.filter((m) => m !== id),
    }));
    // A tick made outside «Подготовки» (the dashboard, the choice chat) is the same tick: it has to
    // reach `POST /overview/milestones/{key}`, or the next load reads the server and undoes it.
    if (REMOTE_PREP) {
      markRemoteMilestone(id, want).catch((err) => {
        console.warn(`Не удалось сохранить отметку вехи «${id}» на сервере:`, err);
      });
    }
  }
  return want;
}

/** Picks the sitting of an exam by its `dateKey`; null goes back to the nearest one. */
export function pickTestDate(exam: DatedExam, key: string | null) {
  if ((chosenTestDates()[exam] ?? null) === key) return;
  update((model) => chooseTestDate(model, exam, key));
}

/** «Позже» on the entrance test from outside «Подготовки»: the first set is built from what is known already. */
export function skipEntranceTest() {
  update(skipTest);
}

/** A way out of a date conflict the student chose; null takes the choice back and the conflict returns. */
export function resolveConflict(id: string, choice: string | null) {
  update((model) => {
    const resolvedConflicts = { ...model.resolvedConflicts };
    if (choice) resolvedConflicts[id] = choice;
    else delete resolvedConflicts[id];
    return { ...model, resolvedConflicts };
  });
}

/* ---------- «Не сейчас» on Quack's advice ---------- */

// Advice is kept by its words: the numbers in them change with the cause, and changed advice shows again
// (product-logic §3.6: a refusal is not repeated until its reason changes)
const DISMISSED_KEY = "quack-advice-dismissed";
const NO_ADVICE: string[] = [];
let dismissedRaw: unknown = undefined;
let dismissed: string[] = NO_ADVICE;

export function dismissedAdvice(): string[] {
  const raw = store.get<string[]>(DISMISSED_KEY);
  if (raw !== dismissedRaw) {
    dismissedRaw = raw;
    dismissed = raw ?? NO_ADVICE;
  }
  return dismissed;
}

/** «Не сейчас» (or back, with `false`) */
export function dismissAdvice(text: string, hide = true) {
  const list = dismissedAdvice().filter((t) => t !== text);
  store.set(DISMISSED_KEY, hide ? [...list, text].slice(-50) : list);
  listeners.forEach((listener) => listener());
}

export function useDismissedAdvice(): string[] {
  return useSyncExternalStore(subscribe, dismissedAdvice, () => NO_ADVICE);
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

/** The ticked milestones for a screen; it re-renders on every tick, wherever it was made. */
export function useDoneMilestones(): string[] {
  return useSyncExternalStore(subscribe, doneMilestones, () => NO_MARKS);
}

/** The picked test dates for a screen; it re-renders on every pick, wherever it was made. */
export function useChosenTestDates(): TestDates {
  return useSyncExternalStore(subscribe, chosenTestDates, () => NO_DATES);
}
