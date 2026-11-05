"use client";

import { INDICATORS, type IndicatorId, type Selection } from "./indicators";

/**
 * The indicators live outside the graph (the pills stand by the sessions panel), so the two talk through this store:
 * the graph publishes what each indicator would light, the pills publish what the learner switched on.
 */
export type IndicatorState = {
  /** A graph is on screen; the pills show only then */
  mounted: boolean;
  domainId: string;
  selections: Record<IndicatorId, Selection>;
  active: ReadonlySet<IndicatorId>;
  /** The indicator is on only as a blink on entering the graph: the camera stays where it is, and it goes off by itself */
  flashing: boolean;
};

const none = (): Record<IndicatorId, Selection> => ({ new: { topics: [], hubs: [] }, relevant: { topics: [], hubs: [] }, deadline: { topics: [], hubs: [] }, weak: { topics: [], hubs: [] }, review: { topics: [], hubs: [] } });

let state: IndicatorState = { mounted: false, domainId: "", selections: none(), active: new Set(), flashing: false };
let flashTimer: ReturnType<typeof setTimeout> | undefined;
/** How long the blink lasts, ms */
const FLASH_MS = 800;
const listeners = new Set<() => void>();
const set = (next: IndicatorState) => {
  state = next;
  listeners.forEach((l) => l());
};

export const subscribeIndicators = (cb: () => void) => {
  listeners.add(cb);
  return () => void listeners.delete(cb);
};
export const getIndicators = () => state;
export const getIndicatorsServer = () => state;

const empty = (s: Selection) => s.topics.length === 0 && s.hubs.length === 0;

/** The graph tells what each indicator lights now. An indicator that ran out of nodes switches itself off */
export function publishIndicators(domainId: string, selections: Record<IndicatorId, Selection>) {
  const keep = state.domainId === domainId ? [...state.active].filter((id) => !empty(selections[id])) : [];
  // Same members: keep the same Set, so the graph does not take a data update for a click and move the camera
  const same = state.domainId === domainId && keep.length === state.active.size;
  const active = same ? state.active : new Set(keep);
  set({ mounted: true, domainId, selections, active, flashing: state.flashing && active.size > 0 });
}

export function unmountIndicators(domainId: string) {
  if (state.domainId === domainId) {
    clearTimeout(flashTimer);
    set({ mounted: false, domainId: "", selections: none(), active: new Set(), flashing: false });
  }
}

/** A blink of one indicator when the learner comes to the graph: "something changed since your last visit". Does nothing if the learner already switched something on */
export function flashIndicator(id: IndicatorId) {
  if (state.active.size || empty(state.selections[id])) return;
  clearTimeout(flashTimer);
  set({ ...state, active: new Set([id]), flashing: true });
  flashTimer = setTimeout(() => {
    if (state.flashing) set({ ...state, active: new Set(), flashing: false });
  }, FLASH_MS);
}

export function toggleIndicator(id: IndicatorId) {
  if (empty(state.selections[id]) && !state.active.has(id)) return;
  // A click on the indicator that is blinking keeps it on: the blink turns into the learner's own choice (and the camera follows, as for any click)
  if (state.flashing && state.active.has(id)) {
    clearTimeout(flashTimer);
    set({ ...state, flashing: false, active: new Set(state.active) });
    return;
  }
  const next = new Set(state.active);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  // Keep the fixed order, so rings are always stacked the same way. A click takes over from a blink
  clearTimeout(flashTimer);
  set({ ...state, flashing: false, active: new Set(INDICATORS.filter((i) => next.has(i))) });
}

export function clearIndicators() {
  clearTimeout(flashTimer);
  if (state.active.size) set({ ...state, active: new Set(), flashing: false });
}
