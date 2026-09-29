// Phase I5 Knowledge Adapter: bridges backend /knowledge API with frontend UI models.

import {
  backend,
  type BackendEvidenceListOut,
  type BackendKnowledgeOut,
  type BackendMisconceptionStateOut,
  type BackendRefreshIn,
  type BackendRefreshOut,
  type BackendSkillStateView,
} from "@/api/backend";
import {
  registerRemoteSkills,
  type ExamId,
  type Misconception,
  type Skill,
  type SkillState,
} from "./prepData";
import { type PrepModel } from "./prepModel";
import { toBackendExamId } from "./remotePrep";
import { REMOTE_PREP } from "./remoteSets";

const KNOWLEDGE_CACHE_PREFIX = "quack:prep:knowledge-cache:";
const memoryCache = new Map<ExamId, BackendKnowledgeOut>();
const inFlightRequests = new Map<ExamId, Promise<BackendKnowledgeOut | null>>();

export function toFrontendSkillState(level: string): SkillState {
  if (level === "solid" || level === "closed") return "solid";
  if (level === "shaky") return "shaky";
  if (level === "low_data" || level === "lowData") return "lowData";
  return "weak";
}

export function getCachedRemoteKnowledge(exam: ExamId): BackendKnowledgeOut | null {
  if (memoryCache.has(exam)) {
    return memoryCache.get(exam)!;
  }
  if (typeof window === "undefined") return null;
  try {
    const item = window.localStorage.getItem(`${KNOWLEDGE_CACHE_PREFIX}${exam}`);
    if (item) {
      const parsed = JSON.parse(item) as BackendKnowledgeOut;
      memoryCache.set(exam, parsed);
      return parsed;
    }
  } catch (err) {
    console.warn("Failed to read knowledge cache from localStorage:", err);
  }
  return null;
}

export function saveCachedRemoteKnowledge(exam: ExamId, data: BackendKnowledgeOut) {
  memoryCache.set(exam, data);
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(`${KNOWLEDGE_CACHE_PREFIX}${exam}`, JSON.stringify(data));
  } catch (err) {
    console.warn("Failed to write knowledge cache to localStorage:", err);
  }
}

export async function fetchRemoteKnowledge(
  exam: ExamId,
  forceRefresh = false
): Promise<BackendKnowledgeOut | null> {
  if (!REMOTE_PREP) return null;

  if (!forceRefresh) {
    const cached = getCachedRemoteKnowledge(exam);
    if (cached) {
      registerKnowledgeSkills(cached, exam);
      return cached;
    }
  }

  if (inFlightRequests.has(exam)) {
    return inFlightRequests.get(exam)!;
  }

  const backendExam = toBackendExamId(exam);
  const promise = (async () => {
    try {
      const data = await backend.knowledge.get(backendExam);
      registerKnowledgeSkills(data, exam);
      saveCachedRemoteKnowledge(exam, data);
      return data;
    } catch (err) {
      console.warn("Failed to fetch remote knowledge for", exam, err);
      return null;
    } finally {
      inFlightRequests.delete(exam);
    }
  })();

  inFlightRequests.set(exam, promise);
  return promise;
}

function registerKnowledgeSkills(data: BackendKnowledgeOut, exam: ExamId) {
  if (!data.skills || data.skills.length === 0) return;

  const adaptedSkills: Skill[] = data.skills.map((s: BackendSkillStateView) => {
    const skillMisconceptions = (data.misconceptions || [])
      .filter((m) => m.skill_ids && m.skill_ids.includes(s.skill_id))
      .map(
        (m): Misconception => ({
          id: m.misconception_id,
          text: m.visible_label || m.name,
          status: m.status as Misconception["status"],
          observations: m.occurrence_count,
        })
      );

    return {
      id: s.skill_id,
      name: s.name,
      area: s.area_id.replace(/^area\.[^.]+\./, "") || "Алгебра",
      exam,
      weight: s.weight,
      state: toFrontendSkillState(s.level),
      recall: s.p_recall,
      requires: [],
      root: s.is_root,
      misconceptions: skillMisconceptions,
      evidence: [],
    };
  });

  registerRemoteSkills(adaptedSkills);
}

export function applyRemoteKnowledgeToModel(
  model: PrepModel,
  data: BackendKnowledgeOut,
  exam: ExamId
): PrepModel {
  registerKnowledgeSkills(data, exam);

  const newStates = { ...model.states };
  const newRecall = { ...model.recall };
  const newMisconceptions = { ...model.misconceptions };

  for (const s of data.skills) {
    const remote = toFrontendSkillState(s.level);
    // «low_data» means the server has not seen enough yet: a verdict the student already earned here
    // (a finished mock) is kept instead of being wiped back to «не изучено»
    if (remote !== "lowData" || !newStates[s.skill_id] || newStates[s.skill_id] === "lowData") newStates[s.skill_id] = remote;
    newRecall[s.skill_id] = s.p_recall;

    const skillTraps = (data.misconceptions || [])
      .filter((m) => m.skill_ids && m.skill_ids.includes(s.skill_id))
      .map(
        (m): Misconception => ({
          id: m.misconception_id,
          text: m.visible_label || m.name,
          status: m.status as Misconception["status"],
          observations: m.occurrence_count,
        })
      );
    if (skillTraps.length > 0 || !newMisconceptions[s.skill_id]) {
      newMisconceptions[s.skill_id] = skillTraps;
    }
  }

  // The entrance test is a fact the server can prove: once any skill has left «low_data», the student
  // has been measured — here, on another device, or by the chat. The flag kept in the browser alone
  // would keep offering the test to someone who took it.
  const measured = data.skills.some((s) => s.level !== "low_data");

  return {
    ...model,
    diagnosticDone: model.diagnosticDone || measured,
    states: newStates,
    recall: newRecall,
    misconceptions: newMisconceptions,
  };
}

export async function disputeRemoteMisconception(
  misconceptionId: string,
  disputed: boolean,
  exam?: ExamId
): Promise<BackendMisconceptionStateOut | null> {
  try {
    const res = await backend.knowledge.dispute(misconceptionId, disputed);
    if (exam) {
      fetchRemoteKnowledge(exam, true).catch(() => {});
    }
    return res;
  } catch (err) {
    console.warn("Failed to dispute remote misconception:", misconceptionId, err);
    return null;
  }
}

export async function explainRemoteNode(nodeId: string): Promise<BackendEvidenceListOut | null> {
  try {
    return await backend.knowledge.explain(nodeId);
  } catch (err) {
    console.warn("Failed to explain remote node:", nodeId, err);
    return null;
  }
}

export async function refreshRemoteKnowledge(body: BackendRefreshIn): Promise<BackendRefreshOut | null> {
  try {
    return await backend.knowledge.refresh(body);
  } catch (err) {
    console.warn("Failed to refresh remote knowledge:", err);
    return null;
  }
}
