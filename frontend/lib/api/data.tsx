"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useMocks, useT } from "@/lib/i18n/provider";
import type { Domain } from "@/lib/mock/types";
import { domainFromApi } from "./adapters";
import { API_MODE, ApiError, get, getToken, setToken, type S } from "./client";

type Data = {
  /** false until the first load (API mode only) */
  ready: boolean;
  error: string | null;
  domains: Domain[];
  me: S["LearnerMeResponse"] | null;
  active: S["ActiveSession"] | null;
  /** Re-read the subject list and the active session */
  reload: () => Promise<void>;
  /** Load (or re-load) the topic graph of a subject */
  loadGraph: (domainId: string) => Promise<void>;
  setActive: (s: S["ActiveSession"] | null) => void;
  logout: () => void;
};

const DataContext = createContext<Data | null>(null);

export function useData(): Data {
  const ctx = useContext(DataContext);
  if (!ctx) throw new Error("useData outside DataProvider");
  return ctx;
}

/** Mocks: everything is there at once */
function MockData({ children }: { children: React.ReactNode }) {
  const { domains } = useMocks();
  const value = useMemo<Data>(
    () => ({
      ready: true,
      error: null,
      domains,
      me: null,
      active: null,
      reload: async () => {},
      loadGraph: async () => {},
      setActive: () => {},
      logout: () => {},
    }),
    [domains],
  );
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

/** Backend: subjects, graphs and the active session, with a login gate */
function ApiData({ children }: { children: React.ReactNode }) {
  const t = useT();
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<S["DomainSummary"][]>([]);
  const [graphs, setGraphs] = useState<Record<string, S["DomainGraphResponse"]>>({});
  const [me, setMe] = useState<S["LearnerMeResponse"] | null>(null);
  const [active, setActive] = useState<S["ActiveSession"] | null>(null);

  const loadGraph = useCallback(async (domainId: string) => {
    try {
      const g = await get<S["DomainGraphResponse"]>(`/v1/domains/${domainId}/graph`);
      setGraphs((m) => ({ ...m, [domainId]: g }));
    } catch (e) {
      // a subject still in onboarding has no graph yet
      if (!(e instanceof ApiError && e.status === 404)) throw e;
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      const [meRes, list, act] = await Promise.all([
        get<S["LearnerMeResponse"]>("/v1/auth/me"),
        get<S["DomainListResponse"]>("/v1/domains"),
        get<S["ActiveSession"] | null>("/v1/sessions/active"),
      ]);
      setMe(meRes);
      setSummaries(list.domains.filter((d) => d.status !== "archived"));
      setActive(act);
      setError(null);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(t.live.serverDown);
    } finally {
      setReady(true);
    }
  }, [t.live.serverDown]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    // first load of the session data; setState happens after the await
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload, router]);

  // No subjects yet: the first screen is creating one
  useEffect(() => {
    if (ready && !error && summaries.length === 0 && pathname === "/") router.replace("/domains/new");
  }, [ready, error, summaries.length, pathname, router]);

  const labels = useMemo(() => ({ hub: t.live.allTopics, activeSession: t.live.activeSession }), [t]);
  const domains = useMemo(() => summaries.map((s) => domainFromApi(s, graphs[s.domain_id], labels, active)), [summaries, graphs, labels, active]);

  const logout = useCallback(() => {
    setToken(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<Data>(
    () => ({ ready, error, domains, me, active, reload, loadGraph, setActive, logout }),
    [ready, error, domains, me, active, reload, loadGraph, logout],
  );
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function DataProvider({ children }: { children: React.ReactNode }) {
  return API_MODE ? <ApiData>{children}</ApiData> : <MockData>{children}</MockData>;
}
