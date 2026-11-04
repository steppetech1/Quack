"use client";

import { useEffect, useSyncExternalStore } from "react";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import type { Locale } from "@/lib/i18n/locale";
import { getGraphModel, installGraphDevHandle, subscribeGraph } from "./mock";
import type { GraphModel } from "./types";

/** The graph of a domain, live: on mocks it follows edits; with the API on it comes from the backend graph and hubs (`undefined` until the graph is there) */
export function useGraphModel(locale: Locale, domainId: string): GraphModel | undefined {
  const { models } = useData();
  useEffect(installGraphDevHandle, []);
  const get = () => (API_MODE ? undefined : getGraphModel(locale, domainId));
  const mock = useSyncExternalStore(subscribeGraph, get, get);
  return API_MODE ? models[domainId] : mock;
}
