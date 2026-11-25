import type { GraphModel } from "@/lib/graph/types";
import type { Domain } from "@/lib/mock/types";

/**
 * What the learner deleted from the left panel (subjects, chapters, topics, sessions), by id. The backend applies a
 * deletion in the background (the graph is rebuilt by the projector), so a re-read right after can still bring the
 * item back: the ids are kept for the whole visit and cut out of everything the UI builds. On mocks this is the
 * whole deletion.
 */
export type Removed = ReadonlySet<string>;

export function applyRemoved(domains: Domain[], removed: Removed): Domain[] {
  if (!removed.size) return domains;
  return domains
    .filter((d) => !removed.has(d.id))
    .map((d) => ({
      ...d,
      hubs: d.hubs.filter((h) => !removed.has(h.id)),
      // topics of a deleted chapter stay, without a chapter
      topics: d.topics
        .filter((t) => !removed.has(t.id))
        .map((t) => ({ ...t, hubId: t.hubId && removed.has(t.hubId) ? undefined : t.hubId, requires: t.requires.filter((id) => !removed.has(id)) })),
      sessions: d.sessions.filter((s) => !removed.has(s.id)),
      today: d.today.filter((i) => !(i.topicId && removed.has(i.topicId))),
    }));
}

export function applyRemovedModel(model: GraphModel, removed: Removed): GraphModel {
  if (!removed.size) return model;
  return {
    ...model,
    topics: model.topics.filter((t) => !removed.has(t.id)),
    hubs: model.hubs.filter((h) => !removed.has(h.id)),
    includes: model.includes.filter((l) => !removed.has(l.hubId) && !removed.has(l.topicId)),
    requires: model.requires.filter((e) => !removed.has(e.topicId) && !removed.has(e.requiresId)),
    recentSessions: model.recentSessions.filter((s) => !removed.has(s.id)),
    today: model.today.filter((r) => !(r.topicId && removed.has(r.topicId)) && !(r.hubId && removed.has(r.hubId))),
  };
}
