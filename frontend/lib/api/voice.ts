import type { VoiceClip } from "@/lib/audio/voice-recorder";
import { upload, type S } from "./client";

/** `POST /v1/sessions/{id}/voice`: one WAV segment → the recognized text (`text` is "" for silence, the audio is not stored) */
export function uploadVoice(sessionId: string, clip: VoiceClip): Promise<S["VoiceView"]> {
  const form = new FormData();
  form.append("audio", clip.wav, "voice.wav");
  form.append("started_at_ms", String(clip.startedAtMs));
  form.append("ended_at_ms", String(clip.endedAtMs));
  return upload<S["VoiceView"]>(`/v1/sessions/${sessionId}/voice`, form);
}
