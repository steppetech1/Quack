import type { Locale } from "@/lib/i18n/locale";
import type { MockData } from "./data";
import { en } from "./en";
import { ru } from "./ru-data";
import type { Domain, Moment, MomentKind, Topic, TopicDetails } from "./types";

export type { MockData } from "./data";

/** The mock "today": all relative dates are counted from it. Not locale-dependent. */
export const MOCK_TODAY = "2026-10-29";

const clean = (s: string) => s.replace(/­/g, "");

/** Moments without hand-written markup: first explanation, then practice */
function derivedMoments(domain: Domain, topic: Topic): Moment[] {
  const mine = domain.sessions
    .filter((s) => s.topics.some((x) => clean(x) === clean(topic.title)))
    .sort((a, b) => a.date.localeCompare(b.date));
  return mine.map((s, i) => {
    const kind: MomentKind = i === 0 ? "first" : topic.state === "confused" && i === mine.length - 1 ? "mistake" : "practice";
    return { id: `${topic.id}-${s.id}`, date: s.date, sessionId: s.id, title: s.title, kind, topicId: topic.id };
  });
}

export type Mocks = MockData & {
  canvasesOf: (domainId: string) => MockData["canvases"];
  getCanvas: (id: string) => MockData["canvases"][number] | undefined;
  getTopicDetails: (domain: Domain, topic: Topic) => TopicDetails;
};

const cache = new Map<Locale, Mocks>();

/** The single seam for mock data. Locale modules plug in here; missing pieces fall back to Russian. */
export function getMocks(locale: Locale): Mocks {
  const hit = cache.get(locale);
  if (hit) return hit;
  const data: MockData = locale === "en" ? { ...ru, ...en } : ru;
  const mocks: Mocks = {
    ...data,
    canvasesOf: (domainId) => data.canvases.filter((c) => c.domainId === domainId).sort((a, b) => b.date.localeCompare(a.date)),
    getCanvas: (id) => data.canvases.find((c) => c.id === id),
    getTopicDetails: (domain, topic) =>
      data.topicDetails[topic.id] ?? { notes: [], when: [], traps: [], cards: [], canvases: [], materials: [], moments: derivedMoments(domain, topic) },
  };
  cache.set(locale, mocks);
  return mocks;
}
