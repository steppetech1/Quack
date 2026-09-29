// Typed calls for the domain endpoints. Types come from schema.d.ts (make types), so a backend
// change that breaks a caller fails the build instead of the screen.

import { api } from "./client";
import type { components } from "./schema";

type Schemas = components["schemas"];

export type BackendProgram = Schemas["Program"];
export type BackendMatch = Schemas["MatchOut"];
export type BackendMatching = Schemas["MatchingOut"];
export type BackendProfile = Schemas["Profile"];
export type BackendSaved = Schemas["SavedProgramWithProgram"];
export type BackendOverview = Schemas["OverviewOut"];
export type BackendMilestone = Schemas["MilestoneOut"];
export type BackendExamRequirement = Schemas["ExamRequirementOut"];
export type BackendExamProgress = Schemas["ExamProgress"];
export type BackendConflict = Schemas["ConflictOut"];
export type BackendKnowledgeVersion = Schemas["KnowledgeVersionOut"];
export type BackendSetsByExam = Schemas["SetsByExam"];
export type BackendSetOut = Schemas["SetOut"];
export type BackendTopicOut = Schemas["TopicOut"];
export type BackendSetProgress = Schemas["SetProgress"];
export type BackendTaskInstanceOut = Schemas["TaskInstanceOut"];
export type BackendOptionOut = Schemas["OptionOut"];
export type BackendTaskRequestIn = Schemas["TaskRequestIn"];
export type BackendAnswerIn = Schemas["AnswerIn"];
export type BackendAnswerResult = Schemas["AnswerResult"];
export type BackendTaskSkipIn = Schemas["TaskSkipIn"];
export type BackendKnowledgeOut = Schemas["KnowledgeOut"];
export type BackendSkillStateView = Schemas["SkillStateView"];
export type BackendMisconceptionStateOut = Schemas["MisconceptionStateOut"];
export type BackendRootCauseOut = Schemas["RootCauseOut"];
export type BackendEvidenceListOut = Schemas["EvidenceListOut"];
export type BackendEvidenceOut = Schemas["EvidenceOut"];
export type BackendRefreshIn = Schemas["RefreshIn"];
export type BackendRefreshOut = Schemas["RefreshOut"];
export type BackendDiagnosticOut = Schemas["DiagnosticOut"];
export type BackendDiagnosticResult = Schemas["DiagnosticResult"];
export type BackendDiagnosticStartIn = Schemas["DiagnosticStartIn"];
export type BackendDiagnosticState = Schemas["DiagnosticState"];
export type BackendMockOut = Schemas["MockOut"];
export type BackendMockResultOut = Schemas["MockResultOut"];
export type BackendMockStartIn = Schemas["MockStartIn"];
export type BackendChatMessageIn = Schemas["ChatMessageIn"];
export type BackendMessageOut = Schemas["MessageOut"];
export type BackendAssistantMarkup = Schemas["AssistantMarkup"];
export type BackendObserveRequestIn = Schemas["ObserveRequestIn"];
export type BackendObserveRequestedOut = Schemas["ObserveRequestedOut"];
export type BackendObservationsDiffOut = Schemas["ObservationsDiffOut"];
export type BackendObservationView = Schemas["ObservationView"];
export type BackendQuack = Schemas["QuackOut"];
export type BackendPace = Schemas["PaceOut"];
export type BackendExamPace = Schemas["ExamPaceOut"];
export type BackendPaceVariant = Schemas["PaceVariantOut"];
export type BackendActivity = Schemas["ActivityOut"];
export type BackendActivityDay = Schemas["ActivityDay"];
export type BackendRecommendation = Schemas["RecommendationOut"];
export type BackendRecommendationAction = Schemas["RecommendationAction"];
export type BackendForecast = Schemas["ForecastOut"];
/** `ExamId` is an inline literal in the schema, so it is read off a model that uses it */
export type BackendExamId = BackendExamPace["exam_id"];

export const backend = {
  profile: {
    get: () => api.get<BackendProfile>("/profile"),
    /** `path` is dotted, e.g. `academics.sat_score`; the server rejects unknown paths with 400 */
    patch: (path: string, value: unknown) =>
      api.patch<BackendProfile>("/profile", { path, value, by: "user" satisfies "user" }),
  },
  matching: {
    get: (limit = 50) => api.get<BackendMatching>(`/matching?limit=${limit}`),
    compare: (ids: string[]) => api.get<Schemas["CompareOut"]>(`/matching/compare?ids=${encodeURIComponent(ids.join(","))}`),
  },
  programs: {
    get: (programId: string) => api.get<BackendProgram>(`/programs/${encodeURIComponent(programId)}`),
    /** 202 and a search_id; nothing is searched inside the request (§1.1) */
    search: (query: string) => api.post<Schemas["SearchStartedOut"]>("/programs/search", { query }),
    searchStatus: (searchId: string) =>
      api.get<Schemas["SearchStatusOut"]>(`/programs/search/${encodeURIComponent(searchId)}`),
    /** «Данные неверны» hides an automatically extracted record; a verified one answers 409 */
    flag: (programId: string, reason: string) =>
      api.post<BackendProgram>(`/programs/${encodeURIComponent(programId)}/flag`, { reason }),
  },
  saved: {
    list: () => api.get<Schemas["Page_SavedProgramWithProgram_"]>("/saved"),
    add: (programId: string) => api.post<unknown>(`/saved/${encodeURIComponent(programId)}`),
    remove: (programId: string) => api.delete<void>(`/saved/${encodeURIComponent(programId)}`),
  },
  overview: {
    get: () => api.get<BackendOverview>("/overview"),
    markMilestone: (key: string, done: boolean) =>
      api.post<BackendMilestone>(`/overview/milestones/${encodeURIComponent(key)}`, { done }),
  },
  sets: {
    list: (examId: "SAT_MATH" | "ENT_MATH") =>
      api.get<BackendSetsByExam>(`/sets?exam_id=${examId}`),
    switch: (setId: string) =>
      api.post<BackendSetsByExam>("/sets/switch", { set_id: setId }),
    get: (setId: string) =>
      api.get<BackendSetOut>(`/sets/${encodeURIComponent(setId)}`),
    /** The report on a finished set: numbers now, the words about them when the job is done */
    summary: (setId: string) =>
      api.get<Schemas["SetSummaryOut"]>(`/sets/${encodeURIComponent(setId)}/summary`),
    open: (setId: string) =>
      api.post<BackendSetOut>(`/sets/${encodeURIComponent(setId)}/open`),
    patch: (setId: string, body: { skill_ids?: string[]; deadline?: string }) =>
      api.patch<BackendSetOut>(`/sets/${encodeURIComponent(setId)}`, body),
    topic: {
      open: (setId: string, skillId: string) =>
        api.post<BackendTopicOut>(`/sets/${encodeURIComponent(setId)}/topics/${encodeURIComponent(skillId)}/open`),
      complete: (setId: string, skillId: string) =>
        api.post<BackendSetOut>(`/sets/${encodeURIComponent(setId)}/topics/${encodeURIComponent(skillId)}/complete`),
    },
  },
  tasks: {
    issue: (body: BackendTaskRequestIn) =>
      api.post<BackendTaskInstanceOut>("/tasks", body),
    answer: (instanceId: string, body: Omit<BackendAnswerIn, "instance_id">) =>
      api.post<BackendAnswerResult>(`/tasks/${encodeURIComponent(instanceId)}/answer`, {
        instance_id: instanceId,
        ...body,
      }),
    skip: (instanceId: string, reason: "skipped" | "timed_out", timeSpentSec: number) =>
      api.post<void>(`/tasks/${encodeURIComponent(instanceId)}/skip`, {
        instance_id: instanceId,
        reason,
        time_spent_sec: timeSpentSec,
      }),
    solution: (instanceId: string) =>
      api.get<{ solution: string[] }>(`/tasks/${encodeURIComponent(instanceId)}/solution`),
  },
  knowledge: {
    get: (examId: "SAT_MATH" | "ENT_MATH") =>
      api.get<BackendKnowledgeOut>(`/knowledge?exam_id=${examId}`),
    explain: (nodeId: string) =>
      api.get<BackendEvidenceListOut>(`/knowledge/explain/${encodeURIComponent(nodeId)}`),
    dispute: (misconceptionId: string, disputed: boolean) =>
      api.post<BackendMisconceptionStateOut>(
        `/knowledge/misconceptions/${encodeURIComponent(misconceptionId)}/dispute`,
        { disputed }
      ),
    refresh: (body: BackendRefreshIn) =>
      api.post<BackendRefreshOut>("/knowledge/refresh", body),
  },
  diagnostic: {
    start: (body: BackendDiagnosticStartIn) =>
      api.post<BackendDiagnosticOut>("/diagnostic", body),
    active: (examId: "SAT_MATH" | "ENT_MATH") =>
      api.get<BackendDiagnosticOut>(`/diagnostic/active?exam_id=${examId}`),
    answer: (runId: string, body: BackendAnswerIn) =>
      api.post<BackendDiagnosticOut>(`/diagnostic/${encodeURIComponent(runId)}/answer`, body),
    finish: (runId: string) =>
      api.post<BackendDiagnosticResult>(`/diagnostic/${encodeURIComponent(runId)}/finish`),
  },
  mocks: {
    start: (body: BackendMockStartIn) =>
      api.post<BackendMockOut>("/mocks", body),
    get: (runId: string) =>
      api.get<BackendMockOut>(`/mocks/${encodeURIComponent(runId)}`),
    answer: (runId: string, body: BackendAnswerIn) =>
      api.post<BackendMockOut>(`/mocks/${encodeURIComponent(runId)}/answer`, body),
    finish: (runId: string) =>
      api.post<BackendMockResultOut>(`/mocks/${encodeURIComponent(runId)}/finish`),
  },
  prep: {
    version: () => api.get<BackendKnowledgeVersion>("/prep/knowledge/version"),
  },
  texts: {
    /** Never generates and never writes; it reports the cache and queues what is missing */
    get: (setId: string, skillId: string, kind: "guideline" | "explanation") =>
      api.get<Schemas["GeneratedTextOut"]>(
        `/texts/${encodeURIComponent(setId)}/${encodeURIComponent(skillId)}?kind=${kind}`
      ),
    opened: (setId: string, skillId: string, kind: "guideline" | "explanation") =>
      api.post<void>(`/texts/${encodeURIComponent(setId)}/${encodeURIComponent(skillId)}/opened`, { kind }),
    regenerate: (setId: string, skillId: string, kind: "guideline" | "explanation") =>
      api.post<{ status: string; job_id?: string }>(
        `/texts/${encodeURIComponent(setId)}/${encodeURIComponent(skillId)}/regenerate?kind=${kind}`
      ),
  },
  quack: {
    /** Feed, pace and activity in one read — what the Quack screen and the dashboard need */
    get: () => api.get<BackendQuack>("/quack"),
    pace: () => api.get<BackendPace>("/quack/pace"),
    activity: (days = 14) => api.get<BackendActivity>(`/quack/activity?days=${days}`),
    history: (limit = 50) =>
      api.get<Schemas["Page_RecommendationOut_"]>(`/quack/history?limit=${limit}`),
    /** Opening the screen: pending recommendations become shown and stop the button glowing */
    seen: (recommendationIds?: string[]) =>
      api.post<{ shown: number }>("/quack/seen", { recommendation_ids: recommendationIds ?? null }),
    accept: (id: string) =>
      api.post<BackendRecommendation>(`/quack/${encodeURIComponent(id)}/accept`),
    decline: (id: string, reason?: string) =>
      api.post<BackendRecommendation>(`/quack/${encodeURIComponent(id)}/decline`, { reason: reason ?? null }),
  },
  chat: {
    messages: (
      kind: "selection" | "prep",
      params?: { limit?: number; set_id?: string | null; topic_skill_id?: string | null }
    ) => {
      const query = new URLSearchParams();
      if (params?.limit) query.set("limit", String(params.limit));
      if (params?.set_id) query.set("set_id", params.set_id);
      if (params?.topic_skill_id) query.set("topic_skill_id", params.topic_skill_id);
      const qs = query.toString();
      return api.get<BackendMessageOut[]>(`/chat/${kind}/messages${qs ? `?${qs}` : ""}`);
    },
    observe: (body: BackendObserveRequestIn) =>
      api.post<BackendObserveRequestedOut>("/chat/prep/observe", body),
    observations: (params: {
      set_id: string;
      since_event_id?: number;
      topic_skill_id?: string | null;
    }) => {
      const query = new URLSearchParams();
      query.set("set_id", params.set_id);
      if (params.since_event_id !== undefined) {
        query.set("since_event_id", String(params.since_event_id));
      }
      if (params.topic_skill_id) {
        query.set("topic_skill_id", params.topic_skill_id);
      }
      return api.get<BackendObservationsDiffOut>(`/chat/prep/observations?${query.toString()}`);
    },
  },
};

