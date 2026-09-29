// Phase I5 Sets Adapter: bridges backend /sets routes with frontend UI models.

import {
  backend,
  type BackendSetOut,
  type BackendSetsByExam,
  type BackendTopicOut,
} from "@/api/backend";
import {
  registerRemoteSets,
  registerRemoteSkills,
  setById,
  TODAY,
  type ExamId,
  type Skill,
  type StudySet,
} from "./prepData";
import { isUuid } from "@/api/client";
import { parseIsoDate, toBackendExamId } from "./remotePrep";

import { REMOTE_PREP } from "./remoteFlag";

export { REMOTE_PREP };

export type RemoteSetsData = {
  current: StudySet | null;
  upcoming: StudySet[];
  done: StudySet[];
  raw?: BackendSetsByExam;
};

const SETS_CACHE_PREFIX = "quack:prep:sets-cache:";
const memoryCache = new Map<ExamId, RemoteSetsData>();
const inFlightRequests = new Map<ExamId, Promise<RemoteSetsData>>();

export function adaptBackendSet(raw: BackendSetOut, exam: ExamId): StudySet {
  // Register skills from topics into the dynamic registry so skillById resolves them
  if (raw.topics && raw.topics.length > 0) {
    const adaptedSkills: Skill[] = raw.topics.map((t: BackendTopicOut) => ({
      id: t.skill_id,
      name: t.name,
      area: raw.area_ids?.[0] ?? "Алгебра",
      exam,
      weight: 5,
      state: t.level === "solid" || t.level === "closed" ? "solid" : t.level === "shaky" ? "shaky" : "weak",
      recall: t.level === "solid" ? 0.8 : t.level === "shaky" ? 0.6 : 0.4,
      requires: [],
      root: t.is_root,
      misconceptions: (t.misconception_labels || []).map((lbl) => ({
        id: lbl,
        text: lbl,
        status: "suspected" as const,
        observations: 1,
      })),
      evidence: [],
    }));
    registerRemoteSkills(adaptedSkills);
  }

  let title = raw.reason;
  if (raw.kind === "consolidation") {
    title = "Закрепление перед тестом";
  } else if (raw.kind === "review") {
    title = "Повторение навыков";
  } else if (raw.topics && raw.topics.length > 0) {
    title = raw.topics[0].name;
    if (raw.topics.length > 1) {
      title += ` и ещё ${raw.topics.length - 1}`;
    }
  }

  const studySet: StudySet = {
    id: raw.id,
    rawId: raw.id,
    exam,
    number: raw.position + 1,
    title: title || `Сет ${raw.position + 1}`,
    area: raw.area_ids?.[0] || "Алгебра",
    skills: (raw.topics || []).map((t) => t.skill_id),
    start: raw.opened_at ? parseIsoDate(raw.opened_at) : TODAY,
    deadline: parseIsoDate(raw.deadline),
    status:
      raw.status === "current"
        ? "current"
        : raw.status === "done"
        ? "done"
        : raw.kind === "review"
        ? "review"
        : "upcoming",
    why: raw.reason,
    kind: raw.kind,
    topics: raw.topics,
    progress: raw.progress,
  };

  return studySet;
}

function reviveSetsData(raw: unknown, exam: ExamId): RemoteSetsData | null {
  if (!raw || typeof raw !== "object") return null;
  try {
    const data = raw as { current: unknown; upcoming: unknown[]; done: unknown[] };
    const current = data.current ? reviveSet(data.current, exam) : null;
    const upcoming = Array.isArray(data.upcoming) ? data.upcoming.map((s) => reviveSet(s, exam)) : [];
    const done = Array.isArray(data.done) ? data.done.map((s) => reviveSet(s, exam)) : [];

    const all = [...(current ? [current] : []), ...upcoming, ...done];
    registerRemoteSets(all);

    return { current, upcoming, done };
  } catch (err) {
    console.warn("Failed to revive cached sets data:", err);
    return null;
  }
}

function reviveSet(raw: unknown, exam: ExamId): StudySet {
  const s = raw as StudySet;
  return {
    ...s,
    exam,
    start: s.start ? new Date(s.start) : TODAY,
    deadline: s.deadline ? new Date(s.deadline) : TODAY,
  };
}

export function getCachedRemoteSets(exam: ExamId): RemoteSetsData | null {
  if (memoryCache.has(exam)) {
    return memoryCache.get(exam)!;
  }
  if (typeof window === "undefined") return null;
  try {
    const item = window.localStorage.getItem(`${SETS_CACHE_PREFIX}${exam}`);
    if (item) {
      const parsed = JSON.parse(item);
      const revived = reviveSetsData(parsed, exam);
      if (revived) {
        memoryCache.set(exam, revived);
        return revived;
      }
    }
  } catch (e) {
    console.warn("Failed to read cached sets from localStorage", e);
  }
  return null;
}

export function saveCachedRemoteSets(exam: ExamId, data: RemoteSetsData) {
  memoryCache.set(exam, data);
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(`${SETS_CACHE_PREFIX}${exam}`, JSON.stringify(data));
  } catch (e) {
    console.warn("Failed to write sets to localStorage", e);
  }
}

/**
 * The server's plan for one exam against the model the screen holds. Inside that plan the server
 * decides: which set is in work and which are passed. Ids outside it — the other exam's plan — are
 * left where they are, so reading one exam does not forget the other.
 */
export function applyRemoteSetsToModel<M extends { currentSet: string | null; doneSets: string[] }>(
  model: M,
  data: RemoteSetsData
): M {
  const plan = new Set(
    [...(data.current ? [data.current] : []), ...data.upcoming, ...data.done].map((s) => s.id)
  );
  const done = data.done.map((s) => s.id);
  const elsewhere = model.currentSet && !plan.has(model.currentSet) ? model.currentSet : null;
  return {
    ...model,
    currentSet: elsewhere ?? data.current?.id ?? null,
    doneSets: [...model.doneSets.filter((id) => !plan.has(id)), ...done],
  };
}

export async function fetchRemoteSets(exam: ExamId, forceRefresh = false): Promise<RemoteSetsData> {
  if (!forceRefresh) {
    const cached = getCachedRemoteSets(exam);
    if (cached) return cached;
  }

  if (inFlightRequests.has(exam)) {
    return inFlightRequests.get(exam)!;
  }

  const backendExam = toBackendExamId(exam);
  const promise = (async () => {
    try {
      const res = await backend.sets.list(backendExam);
      const current = res.current ? adaptBackendSet(res.current, exam) : null;
      const upcoming = (res.upcoming || []).map((s) => adaptBackendSet(s, exam));
      const done = (res.done || []).map((s) => adaptBackendSet(s, exam));

      const all = [...(current ? [current] : []), ...upcoming, ...done];
      registerRemoteSets(all);

      const result: RemoteSetsData = {
        current,
        upcoming,
        done,
        raw: res,
      };

      saveCachedRemoteSets(exam, result);
      return result;
    } finally {
      inFlightRequests.delete(exam);
    }
  })();

  inFlightRequests.set(exam, promise);
  return promise;
}

/** Always asks the server: the plan is rebuilt there after every answer, so a cached copy goes stale */
export function prefetchRemoteSets(exam: ExamId) {
  fetchRemoteSets(exam, true).catch((err) => {
    console.warn("Failed to prefetch remote sets for", exam, err);
  });
}

export async function switchRemoteSet(setId: string, exam: ExamId): Promise<RemoteSetsData> {
  if (!isUuid(setId)) {
    console.warn("switchRemoteSet called with non-UUID set id:", setId);
    return getCachedRemoteSets(exam) ?? { current: null, upcoming: [], done: [] };
  }
  const res = await backend.sets.switch(setId);
  const current = res.current ? adaptBackendSet(res.current, exam) : null;
  const upcoming = (res.upcoming || []).map((s) => adaptBackendSet(s, exam));
  const done = (res.done || []).map((s) => adaptBackendSet(s, exam));

  const all = [...(current ? [current] : []), ...upcoming, ...done];
  registerRemoteSets(all);

  const result: RemoteSetsData = {
    current,
    upcoming,
    done,
    raw: res,
  };

  saveCachedRemoteSets(exam, result);
  return result;
}

export async function openRemoteSet(setId: string, exam: ExamId): Promise<StudySet> {
  if (!isUuid(setId)) {
    console.warn("openRemoteSet called with non-UUID set id:", setId);
    const cached = getCachedRemoteSets(exam);
    const candidate = cached?.upcoming?.[0] ?? cached?.current;
    if (candidate && isUuid(candidate.id)) {
      setId = candidate.id;
    } else {
      return setById(setId);
    }
  }
  const res = await backend.sets.open(setId);
  const adapted = adaptBackendSet(res, exam);
  registerRemoteSets([adapted]);
  // Refetch exam sets to update active state
  fetchRemoteSets(exam, true).catch(() => {});
  return adapted;
}

/**
 * The route right after the diagnostic. The server plans the sets but leaves them `upcoming`, so
 * without this the student finishes the test and the preparation screen still has no set in work —
 * and falls back to the local route, which is a different exam's. Already has one in work: nothing
 * to do, a retake must not move the student off the set they are on.
 */
export async function openFirstRemoteSet(exam: ExamId): Promise<RemoteSetsData> {
  const sets = await fetchRemoteSets(exam, true);
  if (sets.current || !sets.upcoming.length) return sets;
  const first = sets.upcoming[0];
  if (!first.rawId || !isUuid(first.rawId)) return sets;
  try {
    await backend.sets.open(first.rawId);
  } catch (err) {
    console.warn("Failed to open the first set after the diagnostic:", first.rawId, err);
    return sets;
  }
  return fetchRemoteSets(exam, true);
}
