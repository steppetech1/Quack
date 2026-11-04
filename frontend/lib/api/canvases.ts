import { get, idem, post, sse, type S } from "./client";

export type CanvasView = S["CanvasView"];

/** One canvas of the learner: the doc (blocks, learner and teacher layers), its session, the version */
export const getCanvas = (id: string) => get<CanvasView>(`/v1/canvases/${id}`);

/** The canvases of a subject, newest first */
export const listDomainCanvases = (domainId: string) => get<S["CanvasList"]>(`/v1/domains/${domainId}/canvases`);

/** A canvas lives in a session (Т-§15): opens a free one, or one of a task */
export const openCanvas = (sessionId: string, taskId: string | null = null) => post(`/v1/sessions/${sessionId}/canvases`, { task_id: taskId });

/**
 * A message to the teacher about the canvas: the same `message` action of the session, with the canvas
 * as a reference (Т-§15.6). The answer streams as the frames of a normal turn.
 */
export function askTeacher(sessionId: string, canvasId: string, text: string, onEvent: (e: { event: string; data: Record<string, unknown> }) => void, signal?: AbortSignal) {
  return sse("POST", `/v1/sessions/${sessionId}/actions`, { kind: "message", text, canvas_refs: [{ canvas_id: canvasId }], idempotency_key: idem() }, onEvent, signal);
}

/*
 * The title is not in the backend's CanvasView yet: it stays in this browser, by canvas id.
 * Without it (another device, cleared site data) the canvas shows as untitled.
 */
const titleKey = (id: string) => `quack-canvas-title:${id}`;

export function readCanvasTitle(id: string): string {
  try {
    return localStorage.getItem(titleKey(id)) ?? "";
  } catch {
    return "";
  }
}

export function writeCanvasTitle(id: string, title: string) {
  try {
    if (title) localStorage.setItem(titleKey(id), title);
    else localStorage.removeItem(titleKey(id));
  } catch {}
}
