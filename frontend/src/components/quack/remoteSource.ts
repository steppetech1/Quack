// The Quack source that reads the backend's phase-4 API instead of recomputing in the browser.
//
//   GET  /quack                    → pace, feed, activity, and whether a new batch is waiting
//   POST /quack/seen               → opening the screen; pending items become shown, the glow goes out
//   POST /quack/{id}/accept        → take the recommendation; the rules change the plan server-side
//   POST /quack/{id}/decline       → keep it out of the feed
//
// There is no push channel: `/sse` serves the chat and nothing else, and there is no `/events` router
// at all — the backend writes its own events from the domain endpoints. So the state is re-read when
// it can plausibly have changed: on the first subscriber, when the tab comes back, once a minute, and
// right after anything this device did to a source of truth (report()).
//
// What /quack cannot answer for — readiness, the chances of each saved program, the dates coming up —
// comes from /overview and /matching in the same read, and remoteStanding.ts joins the three into the
// one Standing the dashboard screens already read.

import { backend } from "@/api/backend";
import { EMPTY_STATE, glowOf, type QuackState, type Signal, type Standing } from "./contract";
import { toActivityDays, toQuackView, type QuackView } from "./remoteAdapter";
import { alertsOf, chancesOf, nextDateOf, readinessOf } from "./remoteStanding";
import type { QuackSource } from "./source";

/** A quiet tab is still worth a look now and then: the server recomputes on its own schedule */
const POLL_MS = 60_000;
/** Requests coalesce: a burst of reports is one read, not one per report */
const DEBOUNCE_MS = 400;
const HISTORY_MAX = 12;

type Sides = {
  overview: Awaited<ReturnType<typeof backend.overview.get>> | null;
  matching: Awaited<ReturnType<typeof backend.matching.get>> | null;
  savedIds: string[];
};

/** The whole standing: pace and the feed from /quack, everything else from /overview and /matching */
function standingOf(view: QuackView, sides: Sides, today: Date): Standing {
  const { overview, matching, savedIds } = sides;
  return {
    asOf: view.asOf,
    readiness: overview ? readinessOf(overview) : 0,
    alerts: overview ? alertsOf(overview, today) : [],
    next: overview ? nextDateOf(overview, today) : undefined,
    programs: matching ? chancesOf(matching, savedIds) : [],
    pace: view.pace
      ? {
          level: view.pace.level,
          verdict: view.pace.verdict,
          summary: view.pace.summary,
          advice: view.pace.advice,
          exam: view.pace.id,
          adviceActions: view.pace.adviceActions,
        }
      : null,
    exams: view.exams,
  };
}

export function remoteSource(): QuackSource {
  let state: QuackState = { ...EMPTY_STATE, status: "connecting" };
  let history: Signal[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  let poll: ReturnType<typeof setInterval> | null = null;
  let inFlight: Promise<void> | null = null;
  let sides: Sides = { overview: null, matching: null, savedIds: [] };
  let pendingSides = false;
  const listeners = new Set<() => void>();

  const emit = () => listeners.forEach((listener) => listener());

  const set = (next: QuackState) => {
    state = next;
    emit();
  };

  /**
   * `sides` only moves when the student does — the profile, the saved list, a ticked milestone — and
   * every one of those already calls report(). The minute timer therefore re-reads /quack alone,
   * which is the one the server recomputes on its own schedule.
   */
  async function load(withSides: boolean): Promise<void> {
    try {
      // Each side is allowed to fail on its own: a missing /matching should not cost the pace card.
      const [quack, overview, matching, saved] = await Promise.all([
        backend.quack.get(),
        withSides ? backend.overview.get().catch(() => null) : Promise.resolve(sides.overview),
        withSides ? backend.matching.get().catch(() => null) : Promise.resolve(sides.matching),
        withSides ? backend.saved.list().catch(() => null) : Promise.resolve(null),
      ]);
      const view = toQuackView(quack);
      // Decided items leave the feed; what the student has already been shown becomes the history
      const seen = view.open.filter((s) => !view.fresh.some((f) => f.id === s.id));
      history = [...seen, ...history.filter((h) => !view.open.some((o) => o.id === h.id))].slice(0, HISTORY_MAX);
      sides = {
        overview,
        matching,
        savedIds: withSides ? (saved?.items ?? []).map((row) => row.program.id) : sides.savedIds,
      };
      const standing = standingOf(view, sides, new Date());
      set({
        standing,
        activity: toActivityDays(quack.activity),
        fresh: view.fresh,
        history,
        glow: glowOf(view.fresh),
        status: "live",
      });
    } catch {
      // Nothing is thrown away: the last known state stays on screen, marked as out of date
      set({ ...state, status: "offline" });
    }
  }

  /** One read at a time, and at most one per DEBOUNCE_MS however many callers ask */
  function refresh(withSides = true) {
    pendingSides = pendingSides || withSides;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      const full = pendingSides;
      pendingSides = false;
      const run = () => load(full);
      inFlight = (inFlight ?? Promise.resolve()).then(run, run);
    }, DEBOUNCE_MS);
  }

  const onFocus = () => document.visibilityState === "visible" && refresh();

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) {
        refresh();
        poll = setInterval(() => refresh(false), POLL_MS);
        document.addEventListener("visibilitychange", onFocus);
      }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          if (poll) clearInterval(poll);
          poll = null;
          document.removeEventListener("visibilitychange", onFocus);
        }
      };
    },

    getSnapshot: () => state,

    /**
     * A source of truth changed on this device. The change itself has already gone to its own endpoint
     * (profile, saved, sets, tasks), which is what writes the event — this only asks for the result.
     */
    report() {
      refresh();
    },

    markSeen() {
      const pending = state.fresh.map((s) => s.id);
      if (!pending.length) return;
      // Optimistic: the glow goes out now, the server catches up and the next read confirms it
      history = [...state.fresh, ...history.filter((h) => !pending.includes(h.id))].slice(0, HISTORY_MAX);
      set({ ...state, fresh: [], history, glow: null });
      backend.quack.seen(pending).then(
        () => refresh(),
        () => refresh()
      );
    },

    accept(id) {
      return backend.quack.accept(id).then(
        () => {
          refresh();
        },
        (e) => {
          refresh();
          throw e;
        },
      );
    },

    decline(id, reason) {
      return backend.quack.decline(id, reason).then(
        () => {
          refresh();
        },
        (e) => {
          refresh();
          throw e;
        },
      );
    },

    reset() {
      history = [];
      set({ ...EMPTY_STATE, status: "connecting" });
      refresh();
    },
  };
}
