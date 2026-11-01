"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMocks, useT } from "@/lib/i18n/provider";
import type { Domain } from "@/lib/mock/types";
import { domainFromApi } from "./adapters";
import { API_MODE, ApiError, get, getToken, setToken, sse, type S } from "./client";

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
  /** Grows when the backend has applied new events (graph, cheat sheets, mastery): pages re-read their data */
  revision: number;
};

/** Pause before re-reading after `projection_advanced`: a burst of events becomes one reload */
const REFRESH_DEBOUNCE_MS = 600;
const STREAM_RETRY_MS = [1_000, 3_000, 10_000, 30_000];
/** How often a graph answered with `lag_degraded` is re-read */
const LAG_RETRY_MS = 5_000;

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
      revision: 0,
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
  const [active, setActiveState] = useState<S["ActiveSession"] | null>(null);
  const [history, setHistory] = useState<S["SessionListItem"][]>([]);

  const loadHistory = useCallback(async () => {
    const res = await get<S["SessionListResponse"]>("/v1/sessions");
    setHistory(res.sessions);
  }, []);

  // A started or finished session changes the history too
  const setActive = useCallback(
    (s: S["ActiveSession"] | null) => {
      setActiveState(s);
      void loadHistory().catch(() => {});
    },
    [loadHistory],
  );

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
      const [meRes, list, act, hist] = await Promise.all([
        get<S["LearnerMeResponse"]>("/v1/auth/me"),
        get<S["DomainListResponse"]>("/v1/domains"),
        get<S["ActiveSession"] | null>("/v1/sessions/active"),
        get<S["SessionListResponse"]>("/v1/sessions"),
      ]);
      setMe(meRes);
      setSummaries(list.domains.filter((d) => d.status !== "archived"));
      setActiveState(act);
      setHistory(hist.sessions);
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

  // Live updates: the learner channel says "something was applied", the data come with ordinary GETs
  const [revision, setRevision] = useState(0);
  const graphsRef = useRef(graphs);
  useEffect(() => {
    graphsRef.current = graphs;
  }, [graphs]);
  useEffect(() => {
    if (!ready || error) return;
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        for (const id of Object.keys(graphsRef.current)) void loadGraph(id).catch(() => {});
        void loadHistory().catch(() => {});
        setRevision((r) => r + 1);
      }, REFRESH_DEBOUNCE_MS);
    };
    void (async () => {
      for (let attempt = 0; !ctrl.signal.aborted; attempt++) {
        try {
          await sse("GET", "/v1/learner/stream", undefined, ({ event }) => {
            attempt = 0;
            if (event === "projection_advanced") refresh();
          }, ctrl.signal);
        } catch (e) {
          if (ctrl.signal.aborted || (e instanceof ApiError && e.status === 401)) return;
        }
        await new Promise((r) => setTimeout(r, STREAM_RETRY_MS[Math.min(attempt, STREAM_RETRY_MS.length - 1)]));
      }
    })();
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [ready, error, loadGraph, loadHistory]);

  // A lagging graph is re-read until the backend catches up, even without a new `projection_advanced`
  useEffect(() => {
    const lagging = Object.entries(graphs).filter(([, g]) => g.lag_degraded).map(([id]) => id);
    if (!lagging.length) return;
    const timer = setTimeout(() => {
      for (const id of lagging) void loadGraph(id).catch(() => {});
    }, LAG_RETRY_MS);
    return () => clearTimeout(timer);
  }, [graphs, loadGraph]);

  const labels = useMemo(
    () => ({
      hub: t.live.allTopics,
      activeSession: t.live.activeSession,
      modes: t.live.modes,
      reviewToday: t.live.reviewToday,
      reviewOverdue: t.live.reviewOverdue,
    }),
    [t],
  );
  const domains = useMemo(() => summaries.map((s) => domainFromApi(s, graphs[s.domain_id], labels, history)), [summaries, graphs, labels, history]);

  const logout = useCallback(() => {
    setToken(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<Data>(
    () => ({ ready, error, domains, me, active, reload, loadGraph, setActive, logout, revision }),
    [ready, error, domains, me, active, reload, loadGraph, setActive, logout, revision],
  );
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function DataProvider({ children }: { children: React.ReactNode }) {
  return API_MODE ? <ApiData>{children}</ApiData> : <MockData>{children}</MockData>;
}
