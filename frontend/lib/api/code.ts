import { ApiError, get, idem, post, type S } from "./client";

/** A code task as the editor gets it: the language, the function, the starter code and the open tests. Hidden tests: their count only */
export type CodeTask = S["CodeTaskView"];
export type CodeTestView = S["CodeTestView"];
export type CustomRunView = S["CustomRunView"];
export type RunResult = S["RunResponse"];
export type SubmitResult = S["SubmitResponse"];

/** The code task behind an element of a teacher turn, or null when the element is not one (404) */
export async function elementCodeTask(sessionId: string, turnSeq: number, elementId: string): Promise<CodeTask | null> {
  try {
    return await get<CodeTask>(
      `/v1/sessions/${sessionId}/turns/${turnSeq}/elements/${encodeURIComponent(elementId)}/code`,
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** The open tests; with `args` (Python) the function on the learner's own arguments, nothing compared. Writes nothing */
export function runCode(taskId: string, code: string, args?: unknown[]): Promise<RunResult> {
  return post(`/v1/tasks/${taskId}/code/run`, { code, args: args ?? null });
}

/** Every test; the answer and its verdict are written. Hidden tests come back as number and status only */
export function submitCode(sessionId: string, taskId: string, code: string): Promise<SubmitResult> {
  return post(`/v1/sessions/${sessionId}/tasks/${taskId}/code/submit`, { code, idempotency_key: idem() });
}
