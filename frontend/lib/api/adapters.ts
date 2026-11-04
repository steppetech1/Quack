import type { Domain, Hub, Session, TodayItem, Topic, TopicState } from "@/lib/mock/types";
import { type GraphHub, type GraphModel, type GraphTopic, type TodayRec } from "@/lib/graph/types";
import type { S } from "./client";

/** Backend → the shapes the UI was built on (lib/mock/types.ts). The UI itself does not change. */

/** displayState of the backend (Т-§22.2: 8 codes + `none`) → the four topic states of the design */
export function topicState(display: S["TopicDisplay"] | undefined): TopicState {
  switch (display?.display_state) {
    case "solved_independently":
      return "solid";
    case "confused_with":
    case "persistent_error":
    case "struggling":
      return "confused";
    case "inconsistent":
    case "in_progress":
      return "review";
    default:
      // not_tested, known_from_other_domain, none
      return "new";
  }
}

/** Ring of the topic: how far its levels have come (`display_level` 0..6), the backend has no mastery number */
const retentionOf = (display: S["TopicDisplay"] | undefined) => Math.min(1, Math.max(0, (display?.display_level ?? 0) / 6));

/** The graph already carries hubs on the backend (`GET /v1/domains/{id}/hubs`), but its nodes do not say which hub they belong to: all topics go into one chapter */
export const allTopicsHubId = (domainId: string) => `all-${domainId}`;

/** Chapters of the design = hubs of the backend (`GET /v1/domains/{id}/hubs`): their topic lists say which topic sits in which chapter */
export type ApiHub = S["HubGoal"];

const ROMAN: [number, string][] = [[10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]];
const roman = (n: number) => {
  let out = "";
  for (const [v, r] of ROMAN) while (n >= v) { out += r; n -= v; }
  return out;
};

/** topic_id → the first hub that lists it (a topic may stand in several hubs on the backend; the graph draws it in one chapter) */
function hubOfTopic(hubs: ApiHub[]): Map<string, string> {
  const m = new Map<string, string>();
  for (const h of hubs) if (h.kind !== "domain_all") for (const t of h.topics ?? []) if (!m.has(t.topic_id)) m.set(t.topic_id, h.hub_id);
  return m;
}

/** `from → to` in TopicRequires: `from` has to come before `to`. Without hubs all topics go into one chapter; with hubs a topic outside every hub is a free topic */
export function topicsFromGraph(domainId: string, graph: S["DomainGraphResponse"], allHubs: ApiHub[] = []): Topic[] {
  // `domain_all` is the hub over the whole subject, not a chapter
  const hubs = allHubs.filter((h) => h.kind !== "domain_all");
  const hubOf = hubOfTopic(hubs);
  const requires = new Map<string, string[]>();
  for (const e of graph.requires) requires.set(e.to_topic_id, [...(requires.get(e.to_topic_id) ?? []), e.from_topic_id]);
  // hidden and archived topics are the student's "not needed": they leave the graph
  const shown = graph.topics.filter((t) => t.status === "active" && t.visibility !== "hidden");
  const ids = new Set(shown.map((t) => t.topic_id));
  for (const [k, v] of requires) requires.set(k, v.filter((id) => ids.has(id)));
  return shown.map((t) => ({
    id: t.topic_id,
    hubId: hubs.length ? hubOf.get(t.topic_id) : allTopicsHubId(domainId),
    title: t.name,
    // the graph carries no descriptions: the card line is filled by the topic page
    line: "",
    simple: t.name,
    about: "",
    state: topicState(t.display),
    retention: retentionOf(t.display),
    requires: requires.get(t.topic_id) ?? [],
  }));
}

/** «С чего начнём?»: не больше стольких тем, у которых подошёл срок повторения */
const TODAY_MAX = 3;
const DAY_MS = 86_400_000;

export type TodayLabels = { reviewToday: string; reviewOverdue: (days: number) => string };

/** Темы, у которых срок повторения (`nearest_due_at_ms`, мс; 0 — не назначен) наступает до конца дня, самые давние первыми */
export function todayFromGraph(graph: S["DomainGraphResponse"], topics: Topic[], labels: TodayLabels, now = Date.now()): TodayItem[] {
  const endOfDay = new Date(now).setHours(24, 0, 0, 0);
  const shown = new Map(topics.map((t) => [t.id, t]));
  const due = (t: S["TopicNodeView"]) => t.nearest_due_at_ms ?? 0;
  return graph.topics
    .filter((t) => shown.has(t.topic_id) && due(t) > 0 && due(t) < endOfDay)
    .sort((a, b) => due(a) - due(b))
    .slice(0, TODAY_MAX)
    .map((t) => {
      const days = Math.floor((now - due(t)) / DAY_MS);
      return {
        id: t.topic_id,
        topicId: t.topic_id,
        title: shown.get(t.topic_id)!.title,
        reason: days >= 1 ? labels.reviewOverdue(days) : labels.reviewToday,
      };
    });
}

export type SessionLabels = { activeSession: string; modes: Record<string, string> };

/** Session history row → the sessions panel: topic, else the intent */
export function sessionFromApi(s: S["SessionView"], topics: Topic[], labels: SessionLabels): Session {
  const intent = s.intent ?? "free";
  const mode = labels.modes[intent] ?? intent;
  return {
    id: s.session_id,
    href: `/session/${s.session_id}`,
    date: s.started_at.slice(0, 10),
    title: topics.find((t) => t.id === s.topic_id)?.title ?? mode,
    topics: s.status === "active" ? [labels.activeSession] : [mode],
  };
}

export function domainFromApi(
  summary: S["DomainSummary"],
  graph: S["DomainGraphResponse"] | undefined,
  labels: { hub: string } & SessionLabels & TodayLabels,
  history: S["SessionView"][],
  apiHubs: ApiHub[] = [],
): Domain {
  const hubTitle = labels.hub;
  const topics = graph ? topicsFromGraph(summary.domain_id, graph, apiHubs) : [];
  const chapters = apiHubs.filter((h) => h.kind !== "domain_all");
  // chapters with no visible topic would be an empty node in the ring: leave them out
  const used = new Set(topics.map((t) => t.hubId));
  const hubs: Hub[] = chapters.length
    ? chapters
        .filter((h) => used.has(h.hub_id))
        .map((h, i) => ({
          id: h.hub_id,
          num: roman(i + 1),
          title: h.name,
          about: "",
          deadline: h.deadline_at_ms ? { label: h.name, date: new Date(h.deadline_at_ms).toISOString().slice(0, 10) } : undefined,
        }))
    : topics.length
      ? [{ id: allTopicsHubId(summary.domain_id), num: "I", title: hubTitle, about: "" }]
      : [];
  const sessions = history.filter((s) => s.domain_id === summary.domain_id).map((s) => sessionFromApi(s, topics, labels));
  return {
    id: summary.domain_id,
    title: summary.name,
    titleDat: summary.name,
    hubs,
    topics,
    outside: [],
    sessions,
    today: graph ? todayFromGraph(graph, topics, labels) : [],
    lagging: graph?.lag_degraded,
  };
}

/** Placeholder while the list is loading or empty: the shell always has some domain */
export const EMPTY_DOMAIN: Domain = { id: "", title: "", titleDat: "", hubs: [], topics: [], outside: [], sessions: [], today: [] };

const KNOWN_HUB_KINDS = new Set<string>(["material", "request", "deadline", "domain_all"]);
const STATUSES = new Set<string>(["proposed", "active", "hidden", "off_domain"]);
const VISIBILITIES = new Set<string>(["shown", "pale", "hidden"]);

export type TodayApiLabels = { paceWarning: (hubName: string, ready: number, notReady: number) => string };

/**
 * `GET …/today` → the "Today" strip of the layered graph. Real recommendations (skill-level `due`,
 * `consolidation`, `misconception`) replace the old client-side guess at `nearest_due_at_ms` (14.4);
 * a pace warning has no topic, only a ready-made sentence about its hub (14.6).
 */
export function todayFromApi(view: S["TodayView"] | undefined, hubNameOf: (hubId: string) => string | undefined, labels: TodayApiLabels): TodayRec[] {
  if (!view) return [];
  const ACTION: Record<string, TodayRec["action"]> = { due: "review", consolidation: "practice", misconception: "practice" };
  const items = (view.items ?? [])
    .filter((it) => it.topic_id)
    .sort((a, b) => b.score - a.score)
    .map((it) => ({ id: it.skill_id, kind: "item" as const, topicId: it.topic_id as string, action: ACTION[it.reasons[0] ?? "due"] ?? "review" }));
  // Pace warnings earn their slot first: "you won't make the deadline" outranks a review pick,
  // and a flood of due items must not push it out of the TODAY_MAX cards the strip shows.
  const warnings = (view.pace_warnings ?? [])
    .slice(0, TODAY_MAX)
    .map((w) => ({
      id: `pace-${w.hub_id}`,
      kind: "pace_warning" as const,
      hubId: w.hub_id,
      text: labels.paceWarning(hubNameOf(w.hub_id) ?? "", w.ready, w.not_ready),
    }));
  return [...warnings, ...items].slice(0, TODAY_MAX);
}

/**
 * `GET …/graph` + `GET …/hubs` + the session history → the model of the layered graph (lib/graph/types.ts).
 * That model was written to the contract of the backend, so this is a plain renaming: `requires {from, to}` means
 * `to` needs `from` first; display codes and `nearest_due_at_ms` go over as they are; `domain_all` stays in the list
 * (the graph does not draw it as a node).
 */
export function graphModelFromApi(
  summary: S["DomainSummary"],
  graph: S["DomainGraphResponse"],
  hubs: ApiHub[],
  history: S["SessionView"][],
  today: S["TodayView"] | undefined,
  labels: TodayApiLabels,
  now = Date.now(),
): GraphModel {
  const topics: GraphTopic[] = graph.topics.map((t) => ({
    id: t.topic_id,
    name: t.name,
    status: (STATUSES.has(t.status) ? t.status : "active") as GraphTopic["status"],
    visibility: (VISIBILITIES.has(t.visibility) ? t.visibility : "shown") as GraphTopic["visibility"],
    displayState: t.display?.display_state && t.display.display_state !== "none" ? t.display.display_state : undefined,
    nearestDueAtMs: t.nearest_due_at_ms ? t.nearest_due_at_ms : undefined,
  }));
  const hubNames = new Map(hubs.map((h) => [h.hub_id, h.name]));
  return {
    domainId: summary.domain_id,
    domainName: summary.name,
    topics,
    hubs: hubs.map((h) => ({
      id: h.hub_id,
      name: h.name,
      kind: (KNOWN_HUB_KINDS.has(h.kind) ? h.kind : "material") as GraphHub["kind"],
      deadlineAtMs: h.deadline_at_ms ?? undefined,
    })),
    includes: hubs.flatMap((h) => (h.topics ?? []).map((t) => ({ hubId: h.hub_id, topicId: t.topic_id }))),
    requires: graph.requires.map((e) => ({ topicId: e.to_topic_id, requiresId: e.from_topic_id })),
    otherDomain: [],
    recentSessions: history
      .filter((x) => x.domain_id === summary.domain_id && x.topic_id)
      .map((x) => ({ id: x.session_id, atMs: Date.parse(x.last_activity_at) || now, topicIds: [x.topic_id as string] })),
    today: todayFromApi(today, (id) => hubNames.get(id), labels),
    nowMs: now,
  };
}
