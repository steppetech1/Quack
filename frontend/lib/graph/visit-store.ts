"use client";

import { changesBetween, loadLastChanges, loadSnapshot, saveLastChanges, saveSnapshot, snapshotOf, type Snapshot } from "./visit";
import type { GraphModel } from "./types";

/** The learner id is unknown on mocks; the key only has to be stable on this device */
export const LEARNER = "local";

export type VisitState = {
  /** The learner has come to the domain (the visit is registered); false only before that */
  entered: boolean;
  /** There was a snapshot from an earlier visit to compare with. Without it "New" has nothing to say */
  baseline: boolean;
  topics: ReadonlySet<string>;
  hubs: ReadonlySet<string>;
};

const EMPTY: VisitState = { entered: false, baseline: false, topics: new Set(), hubs: new Set() };

type Entry = {
  state: VisitState;
  last: Snapshot;
  leaving?: ReturnType<typeof setTimeout>;
  blinked?: boolean;
  /** `state` is the changes of an earlier visit shown again (nothing changed since); a fresh change replaces it */
  recalled?: boolean;
};
const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function subscribeVisit(cb: () => void) {
  listeners.add(cb);
  return () => void listeners.delete(cb);
}

export function getVisit(domainId: string): VisitState {
  return entries.get(domainId)?.state ?? EMPTY;
}

/** Called when the learner opens a domain: what changed since the snapshot taken when they last left */
export function enterDomain(domainId: string, model: GraphModel) {
  const known = entries.get(domainId);
  if (known?.leaving) {
    // Strict Mode or a quick re-open: the learner never left
    clearTimeout(known.leaving);
    known.leaving = undefined;
    return;
  }
  const last = snapshotOf(model);
  const base = loadSnapshot(LEARNER, domainId);
  let state: VisitState = base ? { entered: true, baseline: true, ...changesBetween(base, last) } : { entered: true, baseline: false, topics: new Set(), hubs: new Set() };
  let recalled = false;
  if (base && (state.topics.size || state.hubs.size)) saveLastChanges(LEARNER, domainId, state);
  else if (base) {
    // Nothing changed since the last visit: "New" shows what it showed last time
    const again = loadLastChanges(LEARNER, domainId);
    if (again) {
      state = { entered: true, baseline: true, topics: new Set(again.topics), hubs: new Set(again.hubs) };
      recalled = true;
    }
  }
  entries.set(domainId, { state, last, recalled });
  emit();
}

/** The graph changed while the learner looks at it: those nodes are new too. Returns what just changed */
export function updateDomain(domainId: string, model: GraphModel): { topics: Set<string>; hubs: Set<string> } {
  const e = entries.get(domainId);
  const cur = snapshotOf(model);
  if (!e) return { topics: new Set(), hubs: new Set() };
  const fresh = changesBetween(e.last, cur);
  e.last = cur;
  if (fresh.topics.size || fresh.hubs.size) {
    // changes of an earlier visit shown again give way to the fresh ones
    const keep = e.recalled ? { topics: new Set<string>(), hubs: new Set<string>() } : e.state;
    e.recalled = false;
    e.state = {
      entered: true,
      baseline: e.state.baseline,
      topics: new Set([...keep.topics, ...fresh.topics]),
      hubs: new Set([...keep.hubs, ...fresh.hubs]),
    };
    saveLastChanges(LEARNER, domainId, e.state);
    emit();
  }
  return fresh;
}

/** True once per visit: the graph may remount while the learner stays, and the "New" blink must not repeat */
export function takeBlink(domainId: string): boolean {
  const e = entries.get(domainId);
  if (!e || e.blinked) return false;
  e.blinked = true;
  return true;
}

/** Called when the learner leaves: what they saw becomes the baseline for the next visit */
export function leaveDomain(domainId: string) {
  const e = entries.get(domainId);
  if (!e) return;
  clearTimeout(e.leaving);
  e.leaving = setTimeout(() => {
    saveSnapshot(LEARNER, domainId, e.last);
    entries.delete(domainId);
    emit();
  }, 0);
}

/** The tab is closing: save everything we are showing */
export function saveAllVisits() {
  for (const [id, e] of entries) saveSnapshot(LEARNER, id, e.last);
}
