// The Quack source that runs in the browser until the backend exists. It keeps the latest sources of
// truth, recomputes the standing on every report, and lets the planner compare it with the standing
// the student saw last. The baseline, the history and when each signal was first noticed are kept in
// the student's store, so a reload or a new sign-in keeps the glow exactly as it was.

import { FIELDS, fieldValue } from "../choice/assistant";
import { programById } from "../choice/programs";
import { EXAMS, formatDate, milestones, plannedTest, setById, skillById, STATE_LABEL, type TestDates } from "../prep/prepData";
import { initialModel, type PrepModel } from "../prep/prepModel";
import { loadPrepModel } from "../prep/prepStore";
import { EMPTY_STATE, glowOf, type QuackState, type Signal, type Standing } from "./contract";
import { firstBaseline, plan } from "./planner";
import type { QuackSource } from "./source";
import { computeStanding, type QuackInputs } from "./standing";
import { store } from "../account/store";

const KEYS = {
  baseline: "quack-baseline",
  history: "quack-history",
  known: "quack-known",
};
/** Device-level: a cause written right before a reload (the demo clock), picked up by the first recompute */
const PENDING_CAUSE_KEY = "quack-pending-cause";
const HISTORY_MAX = 12;

type Known = Record<string, { at: string; cause?: string }>;

const read = <T,>(key: string, fallback: T): T => store.get<T>(key) ?? fallback;
const write = (key: string, value: unknown) => store.set(key, value);

function takePendingCause(): string | undefined {
  try {
    const raw = localStorage.getItem(PENDING_CAUSE_KEY);
    localStorage.removeItem(PENDING_CAUSE_KEY);
    return raw ? (JSON.parse(raw) as string) : undefined;
  } catch {
    return undefined;
  }
}

/* ---------- What the student did, in words ---------- */

function describePrep(a: PrepModel, b: PrepModel, saved: string[]): string | undefined {
  const moved = Object.keys(b.states).filter((id) => a.states[id] !== b.states[id]);
  if (moved.length) {
    const id = moved[0];
    const more = moved.length > 1 ? ` и ещё ${moved.length - 1}` : "";
    return `задачи: ${skillById(id).name} — ${STATE_LABEL[a.states[id]]} → ${STATE_LABEL[b.states[id]]}${more}`;
  }
  const passed = b.doneSets.find((id) => !a.doneSets.includes(id));
  if (passed) return `сет ${setById(passed).number} пройден`;
  if (b.currentSet && a.currentSet !== b.currentSet) return `новый текущий сет: ${setById(b.currentSet).title}`;
  const programs = saved.map(programById).filter(Boolean);
  const moves = (["sat", "ent"] as const).filter((exam) => a.testDates?.[exam] !== b.testDates?.[exam]);
  if (moves.length) {
    const exam = moves[0];
    const when = (dates?: TestDates) => {
      const test = plannedTest(exam, dates);
      return test ? formatDate(test) : "—";
    };
    return `дата ${EXAMS[exam].name}: ${when(a.testDates)} → ${when(b.testDates)}`;
  }
  const marked = b.milestonesDone.find((id) => !a.milestonesDone.includes(id));
  const unmarked = a.milestonesDone.find((id) => !b.milestonesDone.includes(id));
  const toggled = marked ?? unmarked;
  if (toggled) {
    const title =
      [...milestones(programs, b.testDates), ...milestones(programs, a.testDates)].find((m) => m.id === toggled)?.title ?? toggled;
    return `веха «${title}» ${marked ? "отмечена" : "снята"}`;
  }
  if (Object.keys(b.resolvedConflicts).length > Object.keys(a.resolvedConflicts).length) return "решён конфликт вех";
  return undefined;
}

function describeChange(a: QuackInputs, b: QuackInputs): string | undefined {
  const parts: string[] = [];
  if (a.profile !== b.profile) {
    const changed = FIELDS.filter(([key]) => (fieldValue(a.profile, key) ?? "") !== (fieldValue(b.profile, key) ?? ""));
    if (changed.length) {
      parts.push(
        `правка профиля: ${changed
          .slice(0, 2)
          .map(([key, label]) => `${label} ${fieldValue(a.profile, key) ?? "—"} → ${fieldValue(b.profile, key) ?? "—"}`)
          .join(", ")}`
      );
    }
  }
  const added = b.saved.filter((id) => !a.saved.includes(id));
  const removed = a.saved.filter((id) => !b.saved.includes(id));
  if (added.length || removed.length) {
    parts.push(
      `избранное: ${[...added.map((id) => `+ ${programById(id).university}`), ...removed.map((id) => `− ${programById(id).university}`)].join(", ")}`
    );
  }
  if (a.prep !== b.prep) {
    const prep = describePrep(a.prep, b.prep, b.saved);
    if (prep) parts.push(prep);
  }
  return parts.length ? parts.join("; ") : undefined;
}

/* ---------- The source ---------- */

export function localSource(): QuackSource {
  // Storage is read lazily, on the first report: the module is also evaluated on the server
  let loaded = false;
  let prep: PrepModel = initialModel();
  let inputs: QuackInputs | null = null;
  let baseline: Standing | null = null;
  let history: Signal[] = [];
  let known: Known = {};
  let current: Standing | null = null;
  let state: QuackState = EMPTY_STATE;
  const listeners = new Set<() => void>();

  const load = () => {
    if (loaded) return;
    loaded = true;
    prep = loadPrepModel();
    baseline = read<Standing | null>(KEYS.baseline, null);
    history = read<Signal[]>(KEYS.history, []);
    known = read<Known>(KEYS.known, {});
  };

  const emit = () => listeners.forEach((listener) => listener());

  function recompute(cause?: string) {
    if (!inputs) return;
    current = computeStanding(inputs);
    if (!baseline) {
      baseline = firstBaseline(current);
      write(KEYS.baseline, baseline);
    } else if (!baseline.skills) {
      // A baseline saved before topics were tracked: start tracking them from here
      baseline = { ...baseline, skills: current.skills };
      write(KEYS.baseline, baseline);
    }

    // A signal keeps the time and cause of the moment it first appeared, however often it is recomputed
    const now = new Date().toISOString();
    const nextKnown: Known = {};
    const fresh: Signal[] = plan(baseline, current).map((draft) => {
      const first = known[draft.id] ?? { at: now, cause };
      nextKnown[draft.id] = first;
      return { ...draft, at: first.at, cause: first.cause };
    });
    known = nextKnown;
    write(KEYS.known, known);

    state = { standing: current, fresh, history, glow: glowOf(fresh), status: "local" };
    emit();
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    getSnapshot: () => state,

    report(change) {
      load();
      if (change.prep) prep = change.prep;
      const before = inputs;
      if (!before) {
        // Nothing is compared until the workspace (profile and saved programs) has been loaded
        if (!change.profile || !change.saved) return;
        inputs = { profile: change.profile, saved: change.saved, prep };
        recompute(takePendingCause());
        return;
      }
      inputs = { profile: change.profile ?? before.profile, saved: change.saved ?? before.saved, prep };
      recompute(describeChange(before, inputs));
    },

    markSeen() {
      if (!current || !state.fresh.length) return;
      const shown = new Set(state.fresh.map((f) => `${f.id}@${f.at}`));
      history = [...state.fresh, ...history.filter((h) => !shown.has(`${h.id}@${h.at}`))].slice(0, HISTORY_MAX);
      baseline = current;
      known = {};
      write(KEYS.history, history);
      write(KEYS.baseline, baseline);
      recompute();
    },

    reset() {
      Object.values(KEYS).forEach((key) => store.set(key, null));
      baseline = null;
      history = [];
      known = {};
      inputs = null;
      current = null;
      state = EMPTY_STATE;
      emit();
    },
  };
}
