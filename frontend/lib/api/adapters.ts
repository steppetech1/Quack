import type { Domain, Session, TodayItem, Topic, TopicState } from "@/lib/mock/types";
import type { S } from "./client";

/** Backend → the shapes the UI was built on (lib/mock/types.ts). The UI itself does not change. */

/** displayState of the backend (Т-§22.2) → the four topic states of the design */
export function topicState(display: S["TopicNodeView"]["display_state"]): TopicState {
  switch (display) {
    case "solid":
      return "solid";
    case "rusty":
    case "in_progress":
      return "review";
    default:
      return "new";
  }
}

/** The backend has no chapters yet: all topics of a subject go into one chapter, so the graph layout works */
export const allTopicsHubId = (domainId: string) => `all-${domainId}`;

/** `from → to` in PrereqEdge: `from` has to come before `to` */
export function topicsFromGraph(domainId: string, graph: S["DomainGraphResponse"]): Topic[] {
  const requires = new Map<string, string[]>();
  for (const e of graph.prereqs) requires.set(e.to_topic_id, [...(requires.get(e.to_topic_id) ?? []), e.from_topic_id]);
  // skipped and archived topics are the student's "not needed": they leave the graph
  const shown = graph.topics.filter((t) => t.status === "active");
  const ids = new Set(shown.map((t) => t.topic_id));
  for (const [k, v] of requires) requires.set(k, v.filter((id) => ids.has(id)));
  return shown
    .map((t) => ({
      id: t.topic_id,
      hubId: allTopicsHubId(domainId),
      title: t.name,
      line: t.description ?? "",
      simple: t.description || t.name,
      about: t.description ?? "",
      state: topicState(t.display_state),
      retention: t.mastery,
      requires: requires.get(t.topic_id) ?? [],
    }));
}

/** «С чего начнём?»: не больше стольких тем, у которых подошёл срок повторения */
const TODAY_MAX = 3;
const DAY_MS = 86_400_000;

export type TodayLabels = { reviewToday: string; reviewOverdue: (days: number) => string };

/** Темы, у которых срок повторения (`nearest_review_at`, мс; 0 — не назначен) наступает до конца дня, самые давние первыми */
export function todayFromGraph(graph: S["DomainGraphResponse"], topics: Topic[], labels: TodayLabels, now = Date.now()): TodayItem[] {
  const endOfDay = new Date(now).setHours(24, 0, 0, 0);
  const shown = new Map(topics.map((t) => [t.id, t]));
  return graph.topics
    .filter((t) => shown.has(t.topic_id) && t.nearest_review_at > 0 && t.nearest_review_at < endOfDay)
    .sort((a, b) => a.nearest_review_at - b.nearest_review_at)
    .slice(0, TODAY_MAX)
    .map((t) => {
      const days = Math.floor((now - t.nearest_review_at) / DAY_MS);
      return {
        id: t.topic_id,
        topicId: t.topic_id,
        title: shown.get(t.topic_id)!.title,
        reason: days >= 1 ? labels.reviewOverdue(days) : labels.reviewToday,
      };
    });
}

export type SessionLabels = { activeSession: string; modes: Record<string, string> };

/** Session history row → the sessions panel: topic, else what the student asked, else the mode */
export function sessionFromApi(s: S["SessionListItem"], topics: Topic[], labels: SessionLabels): Session {
  const mode = labels.modes[s.mode] ?? s.mode;
  return {
    id: s.session_id,
    href: `/session/${s.session_id}`,
    date: s.started_at.slice(0, 10),
    title: topics.find((t) => t.id === s.topic_id)?.title ?? s.goal ?? mode,
    topics: s.status === "active" ? [labels.activeSession] : [mode],
  };
}

export function domainFromApi(
  summary: S["DomainSummary"],
  graph: S["DomainGraphResponse"] | undefined,
  labels: { hub: string } & SessionLabels & TodayLabels,
  history: S["SessionListItem"][],
): Domain {
  const hubTitle = labels.hub;
  const topics = graph ? topicsFromGraph(summary.domain_id, graph) : [];
  const sessions = history.filter((s) => s.domain_id === summary.domain_id).map((s) => sessionFromApi(s, topics, labels));
  return {
    id: summary.domain_id,
    title: summary.name,
    titleDat: summary.name,
    status: summary.status,
    hubs: topics.length ? [{ id: allTopicsHubId(summary.domain_id), num: "I", title: hubTitle, about: "" }] : [],
    topics,
    outside: [],
    sessions,
    today: graph ? todayFromGraph(graph, topics, labels) : [],
    lagging: graph?.lag_degraded,
  };
}

/** Placeholder while the list is loading or empty: the shell always has some domain */
export const EMPTY_DOMAIN: Domain = { id: "", title: "", titleDat: "", hubs: [], topics: [], outside: [], sessions: [], today: [] };
