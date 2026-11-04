import { WEAK_CODES } from "./weak";
import type { GraphModel } from "./types";
import type { VisitState } from "./visit-store";

export type IndicatorId = "new" | "relevant" | "deadline" | "weak" | "review";

/** The set and the order never change: the learner gets used to where each one is */
export const INDICATORS: readonly IndicatorId[] = ["new", "relevant", "deadline", "weak", "review"];

/** "Relevant": topics of the latest sessions of the domain. These are the frontend's own settings */
export const RELEVANT_SESSIONS = 5;
export const RELEVANT_DAYS = 14;
/** "Review": only the most overdue topics are lit. A pile of overdue topics would be the debt the product refuses to show */
export const REVIEW_TOP = 5;

export type Selection = {
  topics: string[];
  hubs: string[];
  /** Why there is nothing to light, when there is nothing */
  off?: "firstVisit" | "none";
};

const DAY = 86_400_000;

export function computeIndicators(model: GraphModel, visit: VisitState): Record<IndicatorId, Selection> {
  const shown = new Set(model.topics.filter((t) => t.visibility !== "hidden").map((t) => t.id));
  const hubIds = new Set(model.hubs.filter((h) => h.kind !== "domain_all").map((h) => h.id));
  const only = (ids: Iterable<string>) => [...new Set(ids)].filter((id) => shown.has(id));
  const pick = (topics: string[], hubs: string[] = [], off: Selection["off"] = "none"): Selection => (topics.length || hubs.length ? { topics, hubs } : { topics, hubs, off });

  // New: what appeared or got new links since the last visit, and what changed while looking. With no earlier visit there is nothing to compare
  const fresh: Selection = visit.baseline
    ? pick(only(visit.topics), [...visit.hubs].filter((h) => hubIds.has(h)))
    : { topics: [], hubs: [], off: "firstVisit" };

  const since = model.nowMs - RELEVANT_DAYS * DAY;
  const recent = [...model.recentSessions]
    .filter((s) => s.atMs >= since)
    .sort((a, b) => b.atMs - a.atMs)
    .slice(0, RELEVANT_SESSIONS)
    .flatMap((s) => s.topicIds);

  const deadlineHubs = model.hubs.filter((h) => h.kind !== "domain_all" && h.deadlineAtMs !== undefined);
  const deadlineSet = new Set(deadlineHubs.map((h) => h.id));
  const inDeadline = model.includes.filter((l) => deadlineSet.has(l.hubId)).map((l) => l.topicId);

  const weak = model.topics.filter((t) => t.displayState && WEAK_CODES.includes(t.displayState)).map((t) => t.id);
  const overdue = model.topics
    .filter((t) => t.nearestDueAtMs !== undefined && t.nearestDueAtMs <= model.nowMs && t.visibility !== "hidden")
    .sort((a, b) => a.nearestDueAtMs! - b.nearestDueAtMs! || (a.id < b.id ? -1 : 1))
    .slice(0, REVIEW_TOP)
    .map((t) => t.id);

  return {
    new: fresh,
    relevant: pick(only(recent)),
    deadline: pick(only(inDeadline), deadlineHubs.map((h) => h.id)),
    weak: pick(only(weak)),
    review: pick(only(overdue)),
  };
}
