import type { Pt } from "@/lib/canvas-board";

export type SyncState = "saved" | "saving" | "offline";

type StrokeOut = { stroke_id: string; points: [number, number, number, number][] };
type Batch = { type: "stroke_batch"; batch_no: number; strokes: StrokeOut[]; erasures: { stroke_ids: string[]; at_ms: number }[] };

/** Batches go out every `stroke_batch_ms` of the backend (300) */
const BATCH_MS = 300;
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 15000;
/** The board's points carry no time: spaced like a 120 Hz pen, so a replay plays the stroke at the speed it was drawn */
const POINT_MS = 8;
const PRESSURE = 0.5;

/**
 * Where the WebSocket is. `NEXT_PUBLIC_QUACK_WS` (for example `wss://api.qvack.online`) when the API has its own host;
 * without it the socket goes through the same `/api` proxy as the requests.
 */
function socketUrl(canvasId: string): string {
  const base = process.env.NEXT_PUBLIC_QUACK_WS ?? `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api`;
  return `${base.replace(/\/$/, "")}/v1/canvases/${canvasId}/ws`;
}

/**
 * The stroke socket of a canvas (Т-§15.2). Strokes and erasures are collected into a batch every 300 ms; a batch is
 * kept until the server acknowledges it (`ack {batch_no}` comes after its chunk is written), and everything not acknowledged
 * is sent again when the socket reconnects. `batch_no` only has to be new per batch; it starts at the clock, so a page
 * opened later never repeats the number of an earlier one (the journal drops a repeated `batch_no`).
 */
export class CanvasSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private retry = RETRY_MIN_MS;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private batchNo = Date.now();
  private strokes: StrokeOut[] = [];
  private erasures: string[] = [];
  private unacked = new Map<number, Batch>();
  private state: SyncState = "saved";

  constructor(
    private readonly canvasId: string,
    private readonly onState: (s: SyncState) => void,
  ) {
    this.connect();
  }

  stroke(id: string, pts: Pt[]) {
    const t0 = Date.now();
    this.strokes.push({ stroke_id: id, points: pts.map(([x, y], i) => [x, y, t0 + i * POINT_MS, PRESSURE]) });
    this.schedule();
  }

  erase(ids: string[]) {
    this.erasures.push(...ids);
    this.schedule();
  }

  /** Sends what is collected now and leaves; the server writes what it has on close */
  close() {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.flush();
    this.ws?.close();
  }

  private schedule() {
    this.update();
    if (!this.timer) this.timer = setTimeout(() => this.flush(), BATCH_MS);
  }

  private flush() {
    this.timer = null;
    if (!this.strokes.length && !this.erasures.length) return;
    const batch: Batch = {
      type: "stroke_batch",
      batch_no: this.batchNo++,
      strokes: this.strokes,
      erasures: this.erasures.length ? [{ stroke_ids: this.erasures, at_ms: Date.now() }] : [],
    };
    this.strokes = [];
    this.erasures = [];
    this.unacked.set(batch.batch_no, batch);
    this.transmit(batch);
    this.update();
  }

  private transmit(batch: Batch) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(batch));
  }

  private update() {
    const pending = this.unacked.size > 0 || this.strokes.length > 0 || this.erasures.length > 0;
    const next: SyncState = !pending ? "saved" : this.ws?.readyState === WebSocket.OPEN ? "saving" : "offline";
    if (next !== this.state) {
      this.state = next;
      this.onState(next);
    }
  }

  private connect() {
    if (this.closed) return;
    const ws = new WebSocket(socketUrl(this.canvasId));
    this.ws = ws;
    ws.onopen = () => {
      this.retry = RETRY_MIN_MS;
      for (const b of this.unacked.values()) ws.send(JSON.stringify(b));
      this.update();
    };
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(String(e.data)) as { type?: string; batch_no?: number };
        if (m.type === "ack" && typeof m.batch_no === "number") {
          this.unacked.delete(m.batch_no);
          this.update();
        }
      } catch {}
    };
    ws.onclose = (e) => {
      if (this.ws === ws) this.ws = null;
      this.update();
      // 1008: no session or not this learner's canvas: another try will not change that
      if (this.closed || e.code === 1008) return;
      this.retryTimer = setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(RETRY_MAX_MS, this.retry * 2);
    };
    ws.onerror = () => ws.close();
  }
}
