// Phase I5 Tasks Adapter: bridges backend /tasks and /sets topic lifecycle with frontend UI.

import {
  backend,
  type BackendAnswerResult,
  type BackendMockOut,
  type BackendMockResultOut,
  type BackendSetOut,
  type BackendTaskInstanceOut,
  type BackendTopicOut,
} from "@/api/backend";
import { isUuid } from "@/api/client";
import { type ExamId, type Task } from "./prepData";
import { toBackendExamId } from "./remotePrep";
import { REMOTE_PREP } from "./remoteSets";

const tasksCache = new Map<string, Task[]>();
// A second mount while the first request is out (Strict Mode, a quick re-render) shares it instead of
// issuing its own tasks: every issued task is recorded on the server as given to the student.
const tasksInFlight = new Map<string, Promise<Task[] | null>>();

export function adaptBackendTask(instance: BackendTaskInstanceOut): Task {
  return {
    id: instance.id,
    instanceId: instance.id,
    text: instance.stem_rendered,
    figure: instance.figure_url,
    options: instance.options.map((opt) => ({
      label: opt.text,
      key: opt.key,
    })),
  };
}

export async function fetchRemoteTopicTasks(
  skillId: string,
  setId?: string | null,
  count = 3
): Promise<Task[] | null> {
  if (!REMOTE_PREP) return null;

  const validSetId = setId && isUuid(setId) ? setId : null;
  const cacheKey = `${skillId}:${validSetId ?? ""}`;
  if (tasksCache.has(cacheKey)) {
    return tasksCache.get(cacheKey)!;
  }
  const pending = tasksInFlight.get(cacheKey);
  if (pending) return pending;
  const request = issueTopicTasks(skillId, validSetId, cacheKey, count).finally(() => tasksInFlight.delete(cacheKey));
  tasksInFlight.set(cacheKey, request);
  return request;
}

async function issueTopicTasks(skillId: string, validSetId: string | null, cacheKey: string, count: number): Promise<Task[] | null> {
  try {
    const tasks: Task[] = [];
    // A topic with a single fixed template answers the same question every time; a mock of three
    // identical questions tests nothing, so repeats are dropped and a short mock is kept instead.
    const stems = new Set<string>();
    for (let attempt = 0; attempt < count * 2 && tasks.length < count; attempt++) {
      try {
        const instance = await backend.tasks.issue({
          skill_id: skillId,
          set_id: validSetId,
          mode: "topic",
          with_trap: null,
          exclude_seen: false,
        });
        if (stems.has(instance.stem_rendered)) continue;
        stems.add(instance.stem_rendered);
        tasks.push(adaptBackendTask(instance));
      } catch (err: unknown) {
        // If 404 on the first task, skill has no backend templates; fallback to local
        if (tasks.length === 0) {
          return null;
        }
        break;
      }
    }

    if (tasks.length > 0) {
      tasksCache.set(cacheKey, tasks);
      return tasks;
    }
    return null;
  } catch (err) {
    console.warn("Failed to fetch remote tasks for skill:", skillId, err);
    return null;
  }
}

export async function submitRemoteAnswer(
  instanceId: string,
  answerKey: string,
  timeSpentSec: number,
  mode = "topic"
): Promise<BackendAnswerResult> {
  if (!isUuid(instanceId)) {
    throw new Error(`Invalid instanceId for submitRemoteAnswer: ${instanceId}`);
  }
  return await backend.tasks.answer(instanceId, {
    answer: answerKey,
    time_spent_sec: Math.max(1, timeSpentSec),
    mode,
    after_guideline: false,
    hint_level_before: 0,
  });
}

export async function completeRemoteTopic(
  setId: string,
  skillId: string
): Promise<BackendSetOut | null> {
  if (!isUuid(setId)) return null;
  try {
    return await backend.sets.topic.complete(setId, skillId);
  } catch (err) {
    console.warn("Failed to complete remote topic:", setId, skillId, err);
    return null;
  }
}

export async function openRemoteTopic(
  setId: string,
  skillId: string
): Promise<BackendTopicOut | null> {
  if (!isUuid(setId)) return null;
  try {
    return await backend.sets.topic.open(setId, skillId);
  } catch (err) {
    console.warn("Failed to open remote topic:", setId, skillId, err);
    return null;
  }
}

export async function skipRemoteTask(
  instanceId: string,
  timeSpentSec: number,
  reason: "skipped" | "timed_out" = "skipped"
): Promise<void> {
  if (!isUuid(instanceId)) return;
  try {
    await backend.tasks.skip(instanceId, reason, Math.max(1, timeSpentSec));
  } catch (err) {
    console.warn("Failed to skip remote task:", instanceId, err);
  }
}

export function clearTasksCache(skillId?: string, setId?: string) {
  if (skillId) {
    tasksCache.delete(`${skillId}:${setId ?? ""}`);
  } else {
    tasksCache.clear();
  }
}

/* ---------- Final mock: the set's own mock, §4.4/§5.4 kind "mock_set" ---------- */

/** Starts (or resumes, if the backend returns the same run) the set's final mock. */
export async function startRemoteSetMock(exam: ExamId, setId: string): Promise<BackendMockOut | null> {
  if (!REMOTE_PREP || !isUuid(setId)) return null;
  try {
    return await backend.mocks.start({
      kind: "mock_set",
      exam_id: toBackendExamId(exam),
      set_id: setId,
      skill_id: null,
      misconception_id: null,
    });
  } catch (err) {
    console.warn("Failed to start the set's final mock:", setId, err);
    return null;
  }
}

export async function answerRemoteMock(
  runId: string,
  instanceId: string,
  answerKey: string,
  timeSpentSec: number
): Promise<BackendMockOut | null> {
  if (!isUuid(runId) || !isUuid(instanceId)) return null;
  try {
    return await backend.mocks.answer(runId, {
      instance_id: instanceId,
      answer: answerKey,
      time_spent_sec: Math.max(1, timeSpentSec),
      mode: "mock_set",
      after_guideline: false,
      hint_level_before: 0,
    });
  } catch (err) {
    console.warn("Failed to submit a final-mock answer:", runId, err);
    return null;
  }
}

export async function finishRemoteMock(runId: string): Promise<BackendMockResultOut | null> {
  if (!isUuid(runId)) return null;
  try {
    return await backend.mocks.finish(runId);
  } catch (err) {
    console.warn("Failed to finish the final mock:", runId, err);
    return null;
  }
}
