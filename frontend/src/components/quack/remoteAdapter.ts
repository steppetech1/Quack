// From what the backend's phase-4 Quack API answers (QuackOut) to what the screens read (QuackState).
// Pure functions, no network and no React, so the mapping is testable on recorded payloads.
//
// The shapes differ on purpose. The backend thinks in recommendations, forecasts and aggregates
// (backend/app/schemas/quack.py); the screens think in a standing, signals and a glow (contract.ts).
// Everything the two disagree about is settled here, never in a screen and never in the backend schema.

import type {
  BackendActivity,
  BackendExamId,
  BackendExamPace,
  BackendPaceVariant,
  BackendQuack,
  BackendRecommendation,
} from "@/api/backend";
import { EXAMS } from "../dashboard/dashboardRules";
import type { ActivityDay, AdviceAction, ExamPace, PaceLevel, Signal, SignalLevel } from "./contract";

/** Backend exam ids against the ones every frontend screen uses */
const EXAM_ID: Record<BackendExamId, "sat" | "ent"> = { SAT_MATH: "sat", ENT_MATH: "ent" };

export const examIdOf = (id: BackendExamId) => EXAM_ID[id];

export const PACE_VERDICT: Record<PaceLevel, string> = {
  0: "Не успеваешь",
  1: "Нужно ускориться",
  2: "Успеваешь",
  3: "С запасом",
};

/**
 * There is a difference between "you will not make it" and "we cannot tell yet", and the student is
 * owed the honest one. The backend says so itself — `ЕНТ математика: готовность не посчитать — часы
 * не указаны` — so a missing forecast keeps the worst level (nothing is promised) but not the verdict.
 */
export const PACE_UNKNOWN = "Пока не посчитать";

const knowable = (exam: BackendExamPace) =>
  Boolean(exam.forecast?.ready_by) || exam.on_track !== null;

export const paceVerdict = (exam: BackendExamPace, level: PaceLevel) =>
  knowable(exam) ? PACE_VERDICT[level] : PACE_UNKNOWN;

const DAY_MS = 86_400_000;

/** Whole days from `from` to `to`; both are ISO dates (2026-11-07) */
export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(to) - Date.parse(from)) / DAY_MS);
}

/**
 * The four levels the screens colour by, from the one thing the backend states outright (`on_track`)
 * and the margin between readiness and the test. No forecast means nothing to promise: level 0.
 */
export function paceLevel(exam: BackendExamPace): PaceLevel {
  const readyBy = exam.forecast?.ready_by ?? null;
  const testDate = exam.test_date ?? exam.forecast?.test_date ?? null;
  if (!readyBy || !testDate) return exam.on_track ? 2 : 0;
  const margin = daysBetween(readyBy, testDate);
  if (margin < 0) return 0;
  if (margin < 7) return 1;
  return margin < 21 ? 2 : 3;
}

/**
 * A pace variant is a real change of plan, so it comes with the action that makes it. Only two of the
 * four kinds have somewhere to go on the frontend today; the rest are words the student agrees with,
 * and accepting them goes through the recommendation the variant belongs to.
 */
export function variantAction(variant: BackendPaceVariant, examId: BackendExamId): AdviceAction | null {
  if (!variant.available) return null;
  if (variant.kind === "move_date") {
    const date = typeof variant.params?.test_date === "string" ? variant.params.test_date : null;
    // The advice line already spells out the date; the button only has to be pressable
    return date ? { kind: "pick-date", exam: EXAM_ID[examId], key: date.slice(0, 10), label: "Выбрать эту дату" } : null;
  }
  if (variant.kind === "more_hours") return { kind: "open-prep", label: "Открыть подготовку" };
  return null;
}

/**
 * `unavailable_reason` is a code for machines. The backend's own text already says why for every
 * code but one: "→ готов 19 ноября" does not say that date misses the test. An unknown code adds nothing.
 */
const BLOCKED_WORDS: Record<string, string> = { still_late: "всё равно позже теста" };

function blockedLine(v: BackendPaceVariant): string {
  const words = v.unavailable_reason ? BLOCKED_WORDS[v.unavailable_reason] : undefined;
  return words ? `${v.text} — ${words}` : v.text;
}

/** One exam as the pace card reads it */
export function toExamPace(exam: BackendExamPace): ExamPace {
  const id = EXAM_ID[exam.exam_id];
  const level = paceLevel(exam);
  const variants = exam.variants ?? [];
  const available = variants.filter((v) => v.available);
  // An unavailable variant still earns its line: the student is owed the reason it is not on offer
  const blocked = variants.filter((v) => !v.available);
  return {
    id,
    name: EXAMS[id].name,
    level,
    verdict: paceVerdict(exam, level),
    summary: exam.words,
    advice: [
      ...available.map((v) => v.text),
      ...blocked.map(blockedLine),
    ],
    adviceActions: [...available.map((v) => variantAction(v, exam.exam_id)), ...blocked.map(() => null)],
    testDate: exam.test_date ?? undefined,
    forecast: exam.forecast?.ready_by ?? undefined,
  };
}

/** Urgent recommendations glow red; the rest are a notice (contract.ts SignalLevel) */
const signalLevel = (rec: BackendRecommendation): SignalLevel =>
  rec.urgency === "urgent" || rec.urgency === "high" ? "urgent" : "notice";

/** A recommendation is good news only when it is about something new to look at */
const signalTone = (rec: BackendRecommendation): Signal["tone"] => {
  if (rec.kind === "program_new_fit") return "up";
  if (rec.kind === "saved_realism_shift" || rec.kind === "conflict" || rec.kind === "activity_pause") return "down";
  return "info";
};

/** Where the feed's arrow takes the student, by what the recommendation would change */
const signalTarget = (rec: BackendRecommendation): Signal["target"] => {
  switch (rec.action.kind) {
    case "set_open":
    case "set_edit":
      return "prep";
    case "program_remove":
      return "programs";
    case "milestone_open":
      return "calendar";
    case "profile_update":
    case "requirement_update":
      return "profile";
    default:
      return rec.kind === "diagnostic_suggested" ? "prep" : undefined;
  }
};

/** Feed kinds the screens already know; anything else reads as a change in preparation */
const SIGNAL_KIND: Partial<Record<BackendRecommendation["kind"], Signal["kind"]>> = {
  pace_variant: "pace",
  milestone_due: "deadline",
  conflict: "conflict",
  next_set: "skills",
  set_change: "skills",
  program_new_fit: "programs",
  saved_realism_shift: "chance",
  diagnostic_suggested: "skills",
  activity_pause: "pace",
};

/**
 * One recommendation as a feed signal. `recommendation` is what makes it actionable: the screens show
 * the action and a way to decline, and call back into the source, which posts accept/decline.
 */
export function toSignal(rec: BackendRecommendation): Signal {
  return {
    id: rec.id,
    level: signalLevel(rec),
    tone: signalTone(rec),
    kind: SIGNAL_KIND[rec.kind] ?? "skills",
    title: rec.title,
    detail: rec.reason,
    subject: rec.program_id ?? rec.exam_id ?? undefined,
    milestone: rec.milestone_key ?? undefined,
    at: rec.shown_at ?? rec.created_at,
    target: signalTarget(rec),
    recommendation: { id: rec.id, actionText: rec.action_text, status: rec.status },
  };
}

/** Activity as the calendar grid reads it: one entry per day of the window */
export type ActivityView = {
  days: { day: string; active: boolean; minutes: number }[];
  windowDays: number;
  activeDays: number;
  hoursActual: number | null;
  hoursDeclared: number | null;
  /** null while the aggregates have not been computed yet — the grid says it is still counting */
  computedAt: string | null;
  tz: string;
};

export function toActivity(activity: BackendActivity): ActivityView {
  return {
    days: (activity.days ?? []).map((d) => ({ day: d.day, active: d.active, minutes: d.active_minutes ?? 0 })),
    windowDays: activity.window_days,
    activeDays: activity.active_days,
    hoursActual: activity.hours_per_week_actual ?? null,
    hoursDeclared: activity.hours_per_week_declared ?? null,
    computedAt: activity.computed_at ?? null,
    tz: activity.tz,
  };
}

/** Everything the Quack screen takes from one `GET /quack` */
export type QuackView = {
  exams: ExamPace[];
  /** The worst exam speaks for all, as it does locally */
  pace: ExamPace | null;
  /** Not decided yet: these are the feed, in the server's order */
  open: Signal[];
  /** Not shown yet: these are what makes the button glow */
  fresh: Signal[];
  activity: ActivityView;
  asOf: string;
};

export function toQuackView(quack: BackendQuack): QuackView {
  const exams = (quack.pace.exams ?? []).map(toExamPace);
  // Order is the server's (`position`); pending items are the ones the student has not been shown
  const items = quack.items ?? [];
  const open = items.map(toSignal);
  return {
    exams,
    pace: [...exams].sort((a, b) => a.level - b.level || Number(!a.forecast) - Number(!b.forecast))[0] ?? null,
    open,
    fresh: items.filter((item) => item.status === "pending").map(toSignal),
    activity: toActivity(quack.activity),
    asOf: quack.pace.as_of,
  };
}

/**
 * The activity calendar for the grid. The backend counts what the student actually did that day —
 * tasks, mocks, chat — and the grid colours by how much, so the parts are worded here once.
 */
export function toActivityDays(activity: BackendActivity): ActivityDay[] {
  return (activity.days ?? []).map((d) => {
    const parts: string[] = [];
    if (d.tasks_answered) parts.push(`задач: ${d.tasks_answered}`);
    if (d.mocks_completed) parts.push(`моков: ${d.mocks_completed}`);
    if (d.chat_messages) parts.push(`сообщений: ${d.chat_messages}`);
    const count = (d.tasks_answered ?? 0) + (d.mocks_completed ?? 0) + (d.chat_messages ?? 0);
    // Same three buckets the local grid uses, so the two sources look alike
    const level: ActivityDay["level"] = count === 0 ? 0 : count <= 2 ? 1 : count <= 5 ? 2 : 3;
    return { day: d.day, count, level, parts };
  });
}
