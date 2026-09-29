// Phase I5 Diagnostic Adapter: bridges backend /diagnostic endpoints with frontend DiagnosticMock.

import {
  backend,
  type BackendAnswerIn,
  type BackendDiagnosticOut,
  type BackendDiagnosticResult,
  type BackendTaskInstanceOut,
} from "@/api/backend";
import { skillById, type ExamId, type SkillState } from "./prepData";
import { type DiagnosticQuestion, type DiagnosticResultSummary } from "./diagnosticData";
import { toBackendExamId } from "./remotePrep";
import { REMOTE_PREP } from "./remoteSets";
import { isUuid } from "@/api/client";

export async function fetchActiveOrStartDiagnostic(
  exam: ExamId,
  nTasks = 8
): Promise<BackendDiagnosticOut | null> {
  if (!REMOTE_PREP) return null;

  const backendExam = toBackendExamId(exam);
  try {
    // Check if an active diagnostic run is already in progress
    const active = await backend.diagnostic.active(backendExam);
    if (active && active.status === "active") {
      return active;
    }
  } catch (err: unknown) {
    // 404 is expected when no active diagnostic run exists
    const status = (err as { status?: number })?.status;
    if (status !== 404) {
      console.warn("Error checking active diagnostic run:", err);
    }
  }

  // Start a new diagnostic run
  try {
    return await backend.diagnostic.start({
      exam_id: backendExam,
      n_tasks: nTasks,
    });
  } catch (err: unknown) {
    const status = (err as { status?: number })?.status;
    if (status === 409) {
      // Run was created concurrently; fetch it
      try {
        return await backend.diagnostic.active(backendExam);
      } catch (inner) {
        console.warn("Failed to fetch active run after 409 conflict:", inner);
      }
    }
    console.warn("Failed to start diagnostic run on backend:", err);
    return null;
  }
}

export async function submitDiagnosticAnswer(
  runId: string,
  instanceId: string,
  answerKey: string,
  timeSpentSec: number
): Promise<BackendDiagnosticOut | null> {
  if (!REMOTE_PREP || !isUuid(runId) || !isUuid(instanceId)) return null;

  try {
    const body: BackendAnswerIn = {
      instance_id: instanceId,
      answer: answerKey,
      time_spent_sec: Math.max(1, timeSpentSec),
      mode: "diagnostic",
      after_guideline: false,
      hint_level_before: 0,
    };
    return await backend.diagnostic.answer(runId, body);
  } catch (err) {
    console.warn("Failed to submit diagnostic answer:", runId, instanceId, err);
    return null;
  }
}

export async function finishDiagnosticRun(
  runId: string
): Promise<BackendDiagnosticResult | null> {
  if (!REMOTE_PREP || !isUuid(runId)) return null;

  try {
    return await backend.diagnostic.finish(runId);
  } catch (err) {
    console.warn("Failed to finish diagnostic run:", runId, err);
    return null;
  }
}

export function adaptBackendTaskToDiagnosticQuestion(
  task: BackendTaskInstanceOut
): DiagnosticQuestion {
  const skill = skillById(task.skill_id);
  return {
    id: task.id,
    skillId: task.skill_id,
    skillName: skill.name,
    area: skill.area || "Алгебра",
    question: task.stem_rendered,
    options: (task.options || []).map((opt) => ({
      label: opt.text,
      key: opt.key,
    })),
    explanation: `Тема: ${skill.name}. Ответ отправлен на сервер для калибровки точности модели знаний.`,
  };
}

/**
 * The counts come from the run the server closed, never from React state: a reload or a second
 * device mid-diagnostic leaves the component with a fraction of the answers, and the screen would
 * then report a score about questions the server graded differently.
 */
export function adaptDiagnosticResult(
  result: BackendDiagnosticResult,
  answeredOnServer: number
): DiagnosticResultSummary {
  const statesUpdate: Record<string, SkillState> = {};
  for (const id of result.firm) {
    statesUpdate[id] = "solid";
  }
  for (const id of result.shaky) {
    statesUpdate[id] = "weak";
  }

  const solidSkills = result.firm.map((id) => skillById(id).name);
  const attentionSkills = result.shaky.map((id) => skillById(id).name);
  const trapsCaught = [
    ...result.suspected.map((s) => `Ловушка: ${s}`),
    ...result.roots.map((r) => `Корень ошибок: ${skillById(r.root_skill_id).name}`),
  ];

  return {
    // «Верно» — это навыки, которые замер признал твёрдыми; всего — сколько ответов он засчитал
    score: result.firm.length,
    total: Math.max(answeredOnServer, result.firm.length + result.shaky.length, 1),
    solidSkills,
    attentionSkills,
    trapsCaught,
    statesUpdate,
    words: result.words,
  };
}
