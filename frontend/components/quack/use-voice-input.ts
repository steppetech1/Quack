"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useT } from "@/lib/i18n/provider";
import { MAX_VOICE_S, VoiceError, VoiceRecorder, voiceSupported, type VoiceClip } from "@/lib/audio/voice-recorder";

export type VoicePhase = "idle" | "starting" | "recording" | "sending";

/** What the composer needs for voice: `onClip` takes the WAV and returns an error text, or null when it went through */
export type VoiceProps = {
  onClip: (clip: VoiceClip) => Promise<string | null>;
  disabled?: boolean;
};

/** Toggle recording (click to start, click to stop), send the clip, keep the state for the button and the status line */
export function useVoiceInput(onClip: VoiceProps["onClip"] | undefined) {
  const t = useT().voiceSession;
  // false on the server and on hydration, the real answer afterwards
  const supported = useSyncExternalStore(
    () => () => {},
    voiceSupported,
    () => false,
  );
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<VoiceRecorder | null>(null);
  const onClipRef = useRef(onClip);
  useEffect(() => {
    onClipRef.current = onClip;
  });

  // seconds counter; at the limit the recording stops by itself and is sent
  const recording = phase === "recording";
  useEffect(() => {
    if (!recording) return;
    const begun = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - begun) / 1000)), 250);
    return () => clearInterval(timer);
  }, [recording]);

  // leaving the page with the microphone open must release it
  useEffect(
    () => () => {
      rec.current?.cancel();
      rec.current = null;
    },
    [],
  );

  const errorText = useCallback(
    (e: unknown) => {
      if (e instanceof VoiceError) {
        switch (e.code) {
          case "denied":
            return t.denied;
          case "no-device":
            return t.noDevice;
          case "busy":
            return t.busy;
          case "too-short":
            return t.tooShort;
          case "unsupported":
            return t.unsupported;
          case "decode":
            return t.decode;
          default:
            return t.failed;
        }
      }
      return t.failed;
    },
    [t],
  );

  const start = useCallback(async () => {
    setError(null);
    setSeconds(0);
    setPhase("starting");
    try {
      rec.current = await VoiceRecorder.start();
      setPhase("recording");
    } catch (e) {
      setError(errorText(e));
      setPhase("idle");
    }
  }, [errorText]);

  const stop = useCallback(async () => {
    const r = rec.current;
    if (!r) return;
    rec.current = null;
    setPhase("sending");
    try {
      const clip = await r.stop();
      setError(await (onClipRef.current?.(clip) ?? Promise.resolve(null)));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPhase("idle");
    }
  }, [errorText]);

  const cancel = useCallback(() => {
    rec.current?.cancel();
    rec.current = null;
    setPhase("idle");
  }, []);

  useEffect(() => {
    if (recording && seconds >= MAX_VOICE_S) void stop();
  }, [recording, seconds, stop]);

  return { supported: supported && !!onClip, phase, seconds, error, start, stop, cancel };
}
