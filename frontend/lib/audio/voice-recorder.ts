/*
 * Microphone → WAV for `POST /v1/sessions/{id}/voice`.
 *
 * The backend takes only PCM WAV (it has no decoder for WebM / Ogg / MP4), so the browser records with
 * MediaRecorder (whatever codec it likes), decodes the result with the Web Audio API at 16 kHz and writes
 * a mono 16-bit WAV by hand. 16 kHz is one of the two rates the model takes; the server converts the rest.
 */

/** The model's limit is 600 s (`MAX_AUDIO_S`); a message to the teacher is far shorter, and 5 min of 16 kHz WAV is ~9.6 MB (limit 50 MB) */
export const MAX_VOICE_S = 300;
/** Shorter than this is a mis-click, not speech */
const MIN_VOICE_MS = 400;
const TARGET_RATE = 16000;

export type VoiceErrorCode = "unsupported" | "denied" | "no-device" | "busy" | "too-short" | "decode" | "failed";

export class VoiceError extends Error {
  constructor(readonly code: VoiceErrorCode) {
    super(code);
  }
}

export type VoiceClip = {
  wav: Blob;
  /** Device clock (epoch ms) at the start and at the end of the recording: the server's idempotency key and the link with the board */
  startedAtMs: number;
  endedAtMs: number;
};

type AudioContextCtor = typeof AudioContext;
const audioContextCtor = (): AudioContextCtor | undefined =>
  typeof window === "undefined" ? undefined : (window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext);

/** A microphone API exists (needs a secure context: https or localhost) */
export function voiceSupported(): boolean {
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined" && !!audioContextCtor();
}

function mapGetUserMediaError(e: unknown): VoiceError {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") return new VoiceError("denied");
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") return new VoiceError("no-device");
  if (name === "NotReadableError" || name === "AbortError") return new VoiceError("busy");
  return new VoiceError("failed");
}

export class VoiceRecorder {
  private chunks: Blob[] = [];
  private startedAtMs = Date.now();
  private done = false;

  private constructor(
    private readonly stream: MediaStream,
    private readonly rec: MediaRecorder,
  ) {
    rec.ondataavailable = (ev) => {
      if (ev.data.size) this.chunks.push(ev.data);
    };
  }

  /** Asks for the microphone (the permission prompt) and starts recording */
  static async start(): Promise<VoiceRecorder> {
    if (!voiceSupported()) throw new VoiceError("unsupported");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      throw mapGetUserMediaError(e);
    }
    try {
      const self = new VoiceRecorder(stream, new MediaRecorder(stream));
      self.startedAtMs = Date.now();
      self.rec.start();
      return self;
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      throw new VoiceError("failed");
    }
  }

  private release() {
    this.stream.getTracks().forEach((t) => t.stop());
  }

  /** Drops the recording: nothing is sent */
  cancel() {
    if (this.done) return;
    this.done = true;
    this.rec.ondataavailable = null;
    if (this.rec.state !== "inactive") this.rec.stop();
    this.release();
  }

  /** Ends the recording and returns it as a WAV */
  async stop(): Promise<VoiceClip> {
    if (this.done) throw new VoiceError("failed");
    this.done = true;
    const ended = new Promise<void>((resolve) => {
      this.rec.onstop = () => resolve();
      this.rec.onerror = () => resolve();
    });
    if (this.rec.state !== "inactive") this.rec.stop();
    await ended;
    this.release();
    const endedAtMs = Date.now();
    if (endedAtMs - this.startedAtMs < MIN_VOICE_MS || !this.chunks.length) throw new VoiceError("too-short");
    const wav = await toWav(new Blob(this.chunks, { type: this.rec.mimeType || "audio/webm" }));
    return { wav, startedAtMs: this.startedAtMs, endedAtMs };
  }
}

async function toWav(source: Blob): Promise<Blob> {
  const Ctx = audioContextCtor();
  if (!Ctx) throw new VoiceError("unsupported");
  let ctx: AudioContext;
  try {
    ctx = new Ctx({ sampleRate: TARGET_RATE });
  } catch {
    ctx = new Ctx(); // the browser can't pick the rate: the server resamples
  }
  try {
    const decoded = await ctx.decodeAudioData(await source.arrayBuffer());
    return encodeWav(mixToMono(decoded), decoded.sampleRate);
  } catch {
    throw new VoiceError("decode");
  } finally {
    void ctx.close().catch(() => {});
  }
}

function mixToMono(buf: AudioBuffer): Float32Array {
  const out = new Float32Array(buf.length);
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const ch = buf.getChannelData(c);
    for (let i = 0; i < out.length; i++) out[i] += ch[i] / buf.numberOfChannels;
  }
  return out;
}

/** 16-bit PCM mono WAV (44-byte header) */
export function encodeWav(samples: Float32Array, rate: number): Blob {
  const data = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (at: number, s: string) => [...s].forEach((ch, i) => data.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  data.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  data.setUint32(16, 16, true); // fmt chunk size
  data.setUint16(20, 1, true); // PCM
  data.setUint16(22, 1, true); // mono
  data.setUint32(24, rate, true);
  data.setUint32(28, rate * 2, true); // byte rate
  data.setUint16(32, 2, true); // block align
  data.setUint16(34, 16, true); // bits per sample
  text(36, "data");
  data.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    data.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([data], { type: "audio/wav" });
}
