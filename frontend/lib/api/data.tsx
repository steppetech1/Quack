"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMocks, useT } from "@/lib/i18n/provider";
import type { Domain } from "@/lib/mock/types";
import { TermsGate } from "@/components/quack/live/terms-gate";
import type { GraphModel } from "@/lib/graph/types";
import { type ApiHub, domainFromApi, graphModelFromApi } from "./adapters";
import { API_MODE, ApiError, get, idem, patch, post, sse, type S } from "./client";

/** Projection updates come in bursts (one turn → several events): re-read once they settle */
const LIVE_SETTLE_MS = 600;
/** Reconnect delays after the stream drops, the last one repeats */
const LIVE_RETRY_MS = [1_000, 3_000, 10_000, 30_000];
/** How often a graph answered with `lag_degraded` is re-read */
const LAG_RETRY_MS = 5_000;

type Data = {
  /** false until the first load (API mode only) */
  ready: boolean;
  error: string | null;
  domains: Domain[];
  /** Model of the layered graph per subject (API mode; mocks have their own store) */
  models: Record<string, GraphModel>;
  me: S["Me"] | null;
  active: S["SessionView"] | null;
  /**
   * How far the backend projection (graph, topic states) has caught up, from `/v1/learner/stream`.
   * Pages that show projected data re-fetch when it grows; 0 on mocks and before the stream connects.
   */
  appliedSeq: number;
  /** Re-read the subject list and the active session */
  reload: () => Promise<void>;
  /** Load (or re-load) the topic graph of a subject */
  loadGraph: (domainId: string) => Promise<void>;
  setActive: (s: S["SessionView"] | null) => void;
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
      models: {},
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
  const [consentBusy, setConsentBusy] = useState(false);
  const [graphs, setGraphs] = useState<Record<string, S["DomainGraphResponse"]>>({});
  const [hubs, setHubs] = useState<Record<string, ApiHub[]>>({});
  const [todays, setTodays] = useState<Record<string, S["TodayView"]>>({});
  const [me, setMe] = useState<S["Me"] | null>(null);
  const [active, setActiveState] = useState<S["SessionView"] | null>(null);
  const [history, setHistory] = useState<S["SessionView"][]>([]);
  const [appliedSeq, setAppliedSeq] = useState(0);
  /** Graphs already on screen: the live stream refreshes these */
  const loaded = useRef(new Set<string>());

  const loadHistory = useCallback(async () => {
    const res = await get<S["SessionListResponse"]>("/v1/sessions");
    setHistory(res.sessions);
  }, []);

  // A started or finished session changes the history too
  const setActive = useCallback(
    (s: S["SessionView"] | null) => {
      setActiveState(s);
      void loadHistory().catch(() => {});
    },
    [loadHistory],
  );

  const loadGraph = useCallback(async (domainId: string) => {
    try {
      // chapters and "today" ride along with the graph; a failed "today" just leaves the strip empty
      const [g, h, today] = await Promise.all([
        get<S["DomainGraphResponse"]>(`/v1/domains/${domainId}/graph`),
        get<ApiHub[]>(`/v1/domains/${domainId}/hubs`).catch(() => [] as ApiHub[]),
        get<S["TodayView"]>(`/v1/domains/${domainId}/today`).catch(() => undefined),
      ]);
      loaded.current.add(domainId);
      setGraphs((m) => ({ ...m, [domainId]: g }));
      setHubs((m) => ({ ...m, [domainId]: h }));
      if (today) setTodays((m) => ({ ...m, [domainId]: today }));
    } catch (e) {
      // a subject without sessions or materials has no graph yet
      if (!(e instanceof ApiError && e.status === 404)) throw e;
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      const [meRes, list, act, hist] = await Promise.all([
        get<S["Me"]>("/v1/me"),
        get<S["DomainListResponse"]>("/v1/domains"),
        get<S["SessionView"] | null>("/v1/sessions/active"),
        get<S["SessionListResponse"]>("/v1/sessions"),
      ]);
      setMe(meRes);
      setSummaries(list.domains);
      setActiveState(act);
      setHistory(hist.sessions);
      setError(null);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(e instanceof ApiError ? t.live.serverError : t.live.serverDown);
    } finally {
      setReady(true);
    }
  }, [t.live.serverDown, t.live.serverError]);

  useEffect(() => {
    // first load; a missing cookie answers 401 and the client sends the user to the login screen
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  // Live updates: the stream says only "the projection moved", the data comes with ordinary GETs
  useEffect(() => {
    if (!ready || !me) return;
    const stop = new AbortController();
    let settle: ReturnType<typeof setTimeout> | undefined;
    let seen = -1;
    const refresh = () => {
      void get<S["DomainListResponse"]>("/v1/domains")
        .then((list) => setSummaries(list.domains))
        .catch(() => {});
      for (const id of loaded.current) void loadGraph(id).catch(() => {});
      void loadHistory().catch(() => {});
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
  }, [ready, me, loadGraph, loadHistory]);

  // A lagging graph is re-read until the backend catches up, even without a new `projection_advanced`
  useEffect(() => {
    const lagging = Object.entries(graphs).filter(([, g]) => g.lag_degraded).map(([id]) => id);
    if (!lagging.length) return;
    const timer = setTimeout(() => {
      for (const id of lagging) void loadGraph(id).catch(() => {});
    }, LAG_RETRY_MS);
    return () => clearTimeout(timer);
  }, [graphs, loadGraph]);

  // No subjects yet: the first screen is creating one
  useEffect(() => {
    if (ready && !error && summaries.length === 0 && pathname === "/") router.replace("/domains/new");
  }, [ready, error, summaries.length, pathname, router]);

  const labels = useMemo(
    () => ({
      hub: t.live.allTopics,
      activeSession: t.live.activeSession,
      modes: t.live.modes,
      reviewToday: t.live.reviewToday,
      reviewOverdue: t.live.reviewOverdue,
      paceWarning: t.today.paceWarning,
    }),
    [t],
  );
  const domains = useMemo(() => summaries.map((s) => domainFromApi(s, graphs[s.domain_id], labels, history, hubs[s.domain_id])), [summaries, graphs, hubs, labels, history]);

  const models = useMemo(() => {
    const out: Record<string, GraphModel> = {};
    for (const s of summaries) if (graphs[s.domain_id]) out[s.domain_id] = graphModelFromApi(s, graphs[s.domain_id], hubs[s.domain_id] ?? [], history, todays[s.domain_id], labels);
    return out;
  }, [summaries, graphs, hubs, history, todays, labels]);

  const savePreferences = useCallback(async (prefs: Record<string, unknown>) => {
    // one key per request: `preference.set {key, value}`; only plain values go through
    for (const [key, value] of Object.entries(prefs)) {
      if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") continue;
      await patch("/v1/learner/preferences", { key, value, idempotency_key: idem() });
      setMe((m) => (m ? { ...m, settings: { ...m.settings, [key]: value } } : m));
    }
  }, []);

  const logout = useCallback(() => {
    // the cookie is HttpOnly: only the backend can drop it
    void post("/auth/logout")
      .catch(() => {})
      .finally(() => router.replace("/login"));
  }, [router]);

  // Without the current terms accepted a session would start with CONSENT_REQUIRED: ask once, here
  const termsVersion = me?.terms_version ?? null;
  const needsTerms = !!me && !me.terms_accepted && !!termsVersion;
  const acceptTerms = useCallback(async () => {
    if (!termsVersion) return;
    setConsentBusy(true);
    try {
      await post("/v1/consents", { kind: "terms", version: termsVersion, idempotency_key: idem() });
      setMe((m) => (m ? { ...m, terms_accepted: true } : m));
    } catch (e) {
      setError(e instanceof ApiError ? t.live.serverError : t.live.serverDown);
    } finally {
      setConsentBusy(false);
    }
  }, [termsVersion, t.live.serverDown, t.live.serverError]);

  const value = useMemo<Data>(
    () => ({ ready, error, domains, models, me, active, appliedSeq, reload, loadGraph, setActive, savePreferences, logout }),
    [ready, error, domains, models, me, active, appliedSeq, reload, loadGraph, setActive, savePreferences, logout],
  );
  // the gate lies over the app (the app stays mounted: the router needs its segments, and the data loads behind it)
  return (
    <DataContext.Provider value={value}>
      {children}
      {needsTerms && <TermsGate busy={consentBusy} onAccept={() => void acceptTerms()} />}
    </DataContext.Provider>
  );
}

export function DataProvider({ children }: { children: React.ReactNode }) {
  return API_MODE ? <ApiData>{children}</ApiData> : <MockData>{children}</MockData>;
}
