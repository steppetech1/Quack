"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMocks, useT } from "@/lib/i18n/provider";
import type { Domain } from "@/lib/mock/types";
import { domainFromApi } from "./adapters";
import { API_MODE, ApiError, get, getToken, idem, patch, setToken, sse, type S } from "./client";

/** Projection updates come in bursts (one turn → several events): re-read once they settle */
const LIVE_SETTLE_MS = 600;
/** Reconnect delays after the stream drops, the last one repeats */
const LIVE_RETRY_MS = [1_000, 3_000, 10_000, 30_000];

type Data = {
  /** false until the first load (API mode only) */
  ready: boolean;
  error: string | null;
  domains: Domain[];
  me: S["LearnerMeResponse"] | null;
  active: S["ActiveSession"] | null;
  /**
   * How far the backend projection (graph, topic states) has caught up, from `/v1/learner/stream`.
   * Pages that show projected data re-fetch when it grows; 0 on mocks and before the stream connects.
   */
  appliedSeq: number;
  /** Re-read the subject list and the active session */
  reload: () => Promise<void>;
  /** Load (or re-load) the topic graph of a subject */
  loadGraph: (domainId: string) => Promise<void>;
  setActive: (s: S["ActiveSession"] | null) => void;
  /** Merge keys into the learner's preferences on the backend (no-op on mocks) */
  savePreferences: (prefs: Record<string, unknown>) => Promise<void>;
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
      appliedSeq: 0,
      reload: async () => {},
      loadGraph: async () => {},
      setActive: () => {},
      savePreferences: async () => {},
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
  const [appliedSeq, setAppliedSeq] = useState(0);
  /** Graphs already on screen: the live stream refreshes these */
  const loaded = useRef(new Set<string>());

  const loadGraph = useCallback(async (domainId: string) => {
    try {
      const g = await get<S["DomainGraphResponse"]>(`/v1/domains/${domainId}/graph`);
      loaded.current.add(domainId);
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

  // Live updates: the stream says only "the projection moved", the data comes with ordinary GETs
  useEffect(() => {
    if (!ready || !getToken()) return;
    const stop = new AbortController();
    let settle: ReturnType<typeof setTimeout> | undefined;
    let seen = -1;
    const refresh = () => {
      void get<S["DomainListResponse"]>("/v1/domains")
        .then((list) => setSummaries(list.domains.filter((d) => d.status !== "archived")))
        .catch(() => {});
      for (const id of loaded.current) void loadGraph(id).catch(() => {});
    };
    const run = async () => {
      for (let attempt = 0; !stop.signal.aborted; attempt++) {
        try {
          await sse(
            "GET",
            "/v1/learner/stream",
            undefined,
            ({ event, data }) => {
              if (event !== "projection_advanced" || typeof data.applied_seq !== "number") return;
              attempt = 0;
              const seq = data.applied_seq;
              // the first event is the current position: nothing to re-read yet
              if (seen >= 0 && seq > seen) {
                clearTimeout(settle);
                settle = setTimeout(() => {
                  setAppliedSeq(seq);
                  refresh();
                }, LIVE_SETTLE_MS);
              } else if (seen < 0) setAppliedSeq(seq);
              seen = Math.max(seen, seq);
            },
            stop.signal,
          );
        } catch (e) {
          // 401 already sent the user to the login screen
          if (stop.signal.aborted || (e instanceof ApiError && e.status === 401)) return;
        }
        const wait = LIVE_RETRY_MS[Math.min(attempt, LIVE_RETRY_MS.length - 1)];
        await new Promise((r) => setTimeout(r, wait));
      }
    };
    void run();
    return () => {
      stop.abort();
      clearTimeout(settle);
    };
  }, [ready, loadGraph]);

  // No subjects yet: the first screen is creating one
  useEffect(() => {
    if (ready && !error && summaries.length === 0 && pathname === "/") router.replace("/domains/new");
  }, [ready, error, summaries.length, pathname, router]);

  const labels = useMemo(() => ({ hub: t.live.allTopics, activeSession: t.live.activeSession }), [t]);
  const domains = useMemo(() => summaries.map((s) => domainFromApi(s, graphs[s.domain_id], labels, active)), [summaries, graphs, labels, active]);

  const savePreferences = useCallback(async (prefs: Record<string, unknown>) => {
    // the backend merges the keys and returns the whole object
    const r = await patch("/v1/learner/preferences", { preferences: prefs, idempotency_key: idem() });
    setMe((m) => (m ? { ...m, preferences: r.preferences } : m));
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<Data>(
    () => ({ ready, error, domains, me, active, appliedSeq, reload, loadGraph, setActive, savePreferences, logout }),
    [ready, error, domains, me, active, appliedSeq, reload, loadGraph, savePreferences, logout],
  );
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function DataProvider({ children }: { children: React.ReactNode }) {
  return API_MODE ? <ApiData>{children}</ApiData> : <MockData>{children}</MockData>;
}
