import { useSyncExternalStore } from "react";

/**
 * The demos of a session, as the session knows them (the plan's demos, the visualizations of its turns): the session
 * publishes them here, the «Visualizations» document of the workspace pane reads them. One list per session, in memory.
 */
export type SessionDemo = {
  requestId: string;
  title: string;
  status: "ready" | "preparing";
  /** mock mode: the page is drawn from the app itself */
  mock?: boolean;
};

const lists = new Map<string, { key: string; list: SessionDemo[] }>();
const listeners = new Set<() => void>();
const EMPTY: SessionDemo[] = [];

const subscribe = (f: () => void) => {
  listeners.add(f);
  return () => void listeners.delete(f);
};

/** Replaces the list of a session; nothing is announced when it is the same list */
export function publishDemos(sessionId: string, list: SessionDemo[]) {
  const key = JSON.stringify(list);
  if (lists.get(sessionId)?.key === key) return;
  lists.set(sessionId, { key, list });
  listeners.forEach((f) => f());
}

export function useSessionDemos(sessionId: string): SessionDemo[] {
  return useSyncExternalStore(
    subscribe,
    () => lists.get(sessionId)?.list ?? EMPTY,
    () => EMPTY,
  );
}
