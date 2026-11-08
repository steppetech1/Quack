/**
 * Domain graph as the hackathon slice delivers it (tech doc 3.4.2, 3.4.3, 4.7, 4.8, 22.2).
 * Mocks and the real API both produce a `GraphModel`; the view sees nothing else.
 * Only fields that exist in the slice are here. New stages add fields only when the slice has them.
 */

export type TopicStatus = "proposed" | "active" | "hidden" | "off_domain";
export type TopicVisibility = "shown" | "pale" | "hidden";

/** The backend writes a code, the frontend writes the words (22.2) */
export type DisplayCode =
  | "confused_with"
  | "persistent_error"
  | "struggling"
  | "inconsistent"
  | "solved_independently"
  | "known_from_other_domain"
  | "in_progress"
  | "not_tested";

export type GraphTopic = {
  id: string;
  name: string;
  status: TopicStatus;
  visibility: TopicVisibility;
  /** Proposed and hidden topics have none (8.6) */
  displayState?: DisplayCode;
  /** Nearest review time of the topic's skills, ms. "Time to review" is when it is in the past */
  nearestDueAtMs?: number;
};

/** `domain_all` is the hub over the whole domain: it is not drawn as a node (4.8). Suggested hubs are not in the slice (22.10) */
export type HubKind = "material" | "request" | "deadline" | "domain_all";

export type GraphHub = {
  id: string;
  name: string;
  kind: HubKind;
  deadlineAtMs?: number;
};

/** `(Hub)-[:INCLUDES]->(Topic)` */
export type HubLink = { hubId: string; topicId: string };

/** `(Topic)-[:REQUIRES]->(Topic)`: `topicId` needs `requiresId` first. Cycles are possible (4.6) */
export type RequiresEdge = { topicId: string; requiresId: string };

/**
 * A topic asked about in this domain that belongs to a subject the learner has its own domain for (4.7).
 * It lives in that domain; here it is read as a selection, with the name of its domain. There is no copy.
 */
export type OtherDomainTopic = { topic: GraphTopic; domainId: string; domainName: string };

/**
 * "Today" recommendation (14.4). The tech doc fixes no fields, so everything except `id` and `kind` is optional:
 * the view shows only what the backend sent and invents nothing.
 */
export type TodayRec = {
  id: string;
  /** `pace_warning` is a card with no action on a topic (14.6) */
  kind: "item" | "pace_warning";
  topicId?: string;
  hubId?: string;
  /** Name of the target, when the id alone is not enough to find it in the model */
  name?: string;
  /** One fixed set of words, so a card always means the same thing */
  action?: "learn" | "practice" | "review";
  minutes?: number;
  /** Ready-made text of a pace warning */
  text?: string;
  /** A session that already exists */
  href?: string;
};

/** Source for "Relevant": topics worked on in a session (the same data as the sessions panel) */
export type RecentSession = { id: string; atMs: number; topicIds: string[] };

export type GraphModel = {
  domainId: string;
  domainName: string;
  topics: GraphTopic[];
  hubs: GraphHub[];
  includes: HubLink[];
  requires: RequiresEdge[];
  /** Topics from other subjects shown here in the left column. Topics with status `off_domain` are in `topics` */
  otherDomain: OtherDomainTopic[];
  recentSessions: RecentSession[];
  today: TodayRec[];
  /** `minutes` of `GET …/today`: about how long all of today's recommendations take (the backend only; absent when 0) */
  todayMinutes?: number;
  /** Mock "now"; the real API uses `Date.now()` */
  nowMs: number;
};

/** Shown on the graph: not hidden, not off-domain (off-domain topics live in the left column) */
export const onGraph = (t: GraphTopic) => t.visibility !== "hidden" && t.status !== "off_domain";
