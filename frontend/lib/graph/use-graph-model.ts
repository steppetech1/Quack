"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { applyRemovedModel } from "@/lib/api/removed";
import type { Locale } from "@/lib/i18n/locale";
import { getGraphModel, installGraphDevHandle, subscribeGraph } from "./mock";
import type { GraphModel } from "./types";

/** The graph of a domain, live: on mocks it follows edits; with the API on it comes from the backend graph and hubs (`undefined` until the graph is there) */
export function useGraphModel(locale: Locale, domainId: string): GraphModel | undefined {
  const { models, removed } = useData();
  useEffect(installGraphDevHandle, []);
  const get = () => (API_MODE ? undefined : getGraphModel(locale, domainId));
  const mock = useSyncExternalStore(subscribeGraph, get, get);
  // what the learner deleted from the left panel is gone from the mock graph too
  const cut = useMemo(() => (mock ? applyRemovedModel(mock, removed) : mock), [mock, removed]);
  return API_MODE ? models[domainId] : cut;
}
