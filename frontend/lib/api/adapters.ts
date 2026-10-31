import type { Domain, Session, Topic, TopicState } from "@/lib/mock/types";
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

export function domainFromApi(
  summary: S["DomainSummary"],
  graph: S["DomainGraphResponse"] | undefined,
  labels: { hub: string; activeSession: string },
  active: S["ActiveSession"] | null,
): Domain {
  const hubTitle = labels.hub;
  const topics = graph ? topicsFromGraph(summary.domain_id, graph) : [];
  // The backend lists only the active session (there is no session history endpoint yet)
  const sessions: Session[] =
    active && active.domain_id === summary.domain_id
      ? [
          {
            id: active.session_id,
            href: `/session/${active.session_id}`,
            date: active.started_at.slice(0, 10),
            title: topics.find((t) => t.id === active.topic_id)?.title ?? labels.activeSession,
            topics: [],
          },
        ]
      : [];
  return {
    id: summary.domain_id,
    title: summary.name,
    titleDat: summary.name,
    status: summary.status,
    hubs: topics.length ? [{ id: allTopicsHubId(summary.domain_id), num: "I", title: hubTitle, about: "" }] : [],
    topics,
    outside: [],
    sessions,
    today: [],
  };
}

/** Placeholder while the list is loading or empty: the shell always has some domain */
export const EMPTY_DOMAIN: Domain = { id: "", title: "", titleDat: "", hubs: [], topics: [], outside: [], sessions: [], today: [] };
