// Phase I5 Step 6 Tutor Chat Adapter: connects TopicWorkspace chat to backend
// /chat/prep/messages (SSE), /chat/prep/observe, and /chat/prep/observations.

import { api, ApiError, isUuid } from "@/api/client";
import {
  backend,
  type BackendMessageOut,
  type BackendObservationsDiffOut,
  type BackendObserveRequestedOut,
} from "@/api/backend";
import { postSSE, type StreamEvent } from "@/api/stream";
import { REMOTE_PREP } from "./remoteSets";

export interface PrepTurnHandlers {
  onText: (delta: string) => void;
  onDone?: (done: {
    event_id: number;
    mode: string | null;
    gave_task_instance_id: string | null;
    hint_level: number | null;
    referenced_skill_ids: string[];
  }) => void;
  onError?: (err: { code?: string; message?: string }) => void;
}

/**
 * Loads recent messages of a prep chat (either topic-specific or set-level).
 */
export async function loadPrepMessages(
  setId: string,
  topicSkillId?: string | null,
  limit = 50
): Promise<BackendMessageOut[] | null> {
  if (!REMOTE_PREP || !isUuid(setId)) return null;
  try {
    return await backend.chat.messages("prep", {
      set_id: setId,
      topic_skill_id: topicSkillId ?? null,
      limit,
    });
  } catch (err) {
    console.warn("Failed to load prep chat messages:", err);
    return null;
  }
}

/**
 * Sends one message to the tutor agent via POST /chat/prep/messages (SSE).
 */
export async function sendPrepMessage(
  params: { text: string; setId: string; topicSkillId?: string | null },
  handlers: PrepTurnHandlers,
  signal?: AbortSignal
): Promise<void> {
  if (!isUuid(params.setId)) {
    throw new ApiError(400, "invalid_id", "Invalid set ID for prep chat");
  }
  const failure: { code?: string; message?: string } = {};

  await postSSE(
    "/chat/prep/messages",
    {
      text: params.text,
      set_id: params.setId,
      topic_skill_id: params.topicSkillId ?? null,
    },
    (event: StreamEvent) => {
      if (event.type === "text_delta") {
        handlers.onText(event.text);
      } else if (event.type === "done") {
        handlers.onDone?.({
          event_id: event.event_id,
          mode: event.mode,
          gave_task_instance_id: event.gave_task_instance_id,
          hint_level: event.hint_level,
          referenced_skill_ids: event.referenced_skill_ids,
        });
      } else if (event.type === "error") {
        failure.code = event.code;
        failure.message = event.message;
        handlers.onError?.({ code: event.code, message: event.message });
      }
    },
    signal
  );

  if (failure.message) {
    throw new ApiError(500, failure.code ?? "internal", failure.message);
  }
}

/**
 * Requests the observer to analyze unprocessed messages in this chat.
 */
export async function requestChatObservation(
  setId: string,
  topicSkillId?: string | null
): Promise<BackendObserveRequestedOut | null> {
  if (!REMOTE_PREP || !isUuid(setId)) return null;
  try {
    return await backend.chat.observe({
      set_id: setId,
      topic_skill_id: topicSkillId ?? null,
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      throw err;
    }
    console.warn("Failed to request chat observation:", err);
    return null;
  }
}

/**
 * Polls the observer result for this chat until done, failed, or max attempts reached.
 */
export async function pollObservationsDiff(
  setId: string,
  sinceEventId: number,
  topicSkillId?: string | null,
  maxAttempts = 10,
  intervalMs = 1500
): Promise<BackendObservationsDiffOut | null> {
  if (!REMOTE_PREP || !isUuid(setId)) return null;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const diff = await backend.chat.observations({
        set_id: setId,
        since_event_id: sinceEventId,
        topic_skill_id: topicSkillId ?? null,
      });

      if (diff.status === "done" || diff.status === "failed") {
        return diff;
      }
    } catch (err) {
      console.warn("Failed to fetch observations diff:", err);
    }

    await new Promise((r) => setTimeout(r, intervalMs));
  }

  return null;
}

/**
 * Fetches the current global knowledge version for cache invalidation.
 */
export async function fetchKnowledgeVersion(): Promise<number> {
  if (!REMOTE_PREP) return 0;
  try {
    const res = await backend.prep.version();
    return res.version;
  } catch (err) {
    console.warn("Failed to fetch knowledge version:", err);
    return 0;
  }
}
