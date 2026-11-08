import type { Pt } from "@/lib/canvas-board";

export type SyncState = "saved" | "saving" | "offline";

type StrokeOut = { stroke_id: string; points: [number, number, number, number][] };
type Batch = { type: "stroke_batch"; batch_no: number; strokes: StrokeOut[]; erasures: { stroke_ids: string[]; at_ms: number }[] };
type LineReady = {
  type: "line_ready";
  line_id: string;
  stroke_ids: string[];
  bbox: [number, number, number, number];
  started_at: number;
  ended_at: number;
};

/**
 * What the board hears from a canvas socket besides the acks: the line it just sent for reading and the teacher's
 * server messages `line_recognized`, `mark` (`kind: null` takes the mark off) and `hint` (Т-§15.2, WP21.2-21.5)
 */
export type CanvasEvent =
  | { type: "line_sent"; line_id: string; stroke_ids: string[]; bbox: [number, number, number, number] }
  | {
      type: "line_recognized";
      line_id: string;
      recognized: boolean;
      kind?: "latex" | "text";
      text?: string;
      sympy?: string | null;
      confidence?: number;
      crossed_out?: boolean;
    }
  | { type: "mark"; line_id: string; kind: "error" | "question" | null }
  | { type: "hint"; line_id: string | null; step: number; text: string };

/** Batches go out every `stroke_batch_ms` of the backend (300) */
const BATCH_MS = 300;
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 15000;
/** The board's points carry no time: spaced like a 120 Hz pen, so a replay plays the stroke at the speed it was drawn */
const POINT_MS = 8;
const PRESSURE = 0.5;
/** A line is "done" after this long without a new stroke (backend default of `line_pause_ms`, Т-§15.2) */
const LINE_PAUSE_MS = 1200;

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
  private opened = false;
  private retry = RETRY_MIN_MS;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private batchNo = Date.now();
  private strokes: StrokeOut[] = [];
  private erasures: string[] = [];
  private unacked = new Map<number, Batch>();
  private state: SyncState = "saved";
  private line: {
    lineId: string;
    strokeIds: string[];
    minX: number;
    minY: number;
    maxX: number;
    maxY: number;
    startedAt: number;
    endedAt: number;
  } | null = null;
  private lineTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly canvasId: string,
    private readonly onState: (s: SyncState) => void,
    private readonly onEvent: (e: CanvasEvent) => void = () => {},
    /** the socket is open again after a drop (not the first open): what the server said while it was down is lost to the board */
    private readonly onReopen: () => void = () => {},
  ) {
    this.connect();
  }

  /**
   * Raw ink only acks that a stroke is stored; the teacher reads recognized lines
   * (`canvas.line_committed`), which only exist once the server has grouped the strokes
   * of a line and run recognition on it — triggered by `line_ready` (Т-§15.3). A line is
   * one or more strokes with no gap over `LINE_PAUSE_MS` between them.
   */
  stroke(id: string, pts: Pt[]) {
    const t0 = Date.now();
    this.strokes.push({ stroke_id: id, points: pts.map(([x, y], i) => [x, y, t0 + i * POINT_MS, PRESSURE]) });
    this.schedule();

    const endedAt = t0 + (pts.length - 1) * POINT_MS;
    const xs = pts.map(([x]) => x);
    const ys = pts.map(([, y]) => y);
    if (!this.line) {
      this.line = {
        lineId: `L-${t0}-${Math.random().toString(36).slice(2, 8)}`,
        strokeIds: [id],
        minX: Math.min(...xs),
        minY: Math.min(...ys),
        maxX: Math.max(...xs),
        maxY: Math.max(...ys),
        startedAt: t0,
        endedAt,
      };
    } else {
      this.line.strokeIds.push(id);
      this.line.minX = Math.min(this.line.minX, ...xs);
      this.line.minY = Math.min(this.line.minY, ...ys);
      this.line.maxX = Math.max(this.line.maxX, ...xs);
      this.line.maxY = Math.max(this.line.maxY, ...ys);
      this.line.endedAt = endedAt;
    }
    if (this.lineTimer) clearTimeout(this.lineTimer);
    this.lineTimer = setTimeout(() => this.flushLine(), LINE_PAUSE_MS);
  }

  erase(ids: string[]) {
    this.erasures.push(...ids);
    this.schedule();
    if (this.line) this.line.strokeIds = this.line.strokeIds.filter((id) => !ids.includes(id));
  }

  /** Sends what is collected now and leaves; the server writes what it has on close */
  close() {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.lineTimer) clearTimeout(this.lineTimer);
    this.flushLine();
    this.flush();
    this.ws?.close();
  }

  private flushLine() {
    this.lineTimer = null;
    const line = this.line;
    this.line = null;
    if (!line || !line.strokeIds.length) return;
    const msg: LineReady = {
      type: "line_ready",
      line_id: line.lineId,
      stroke_ids: line.strokeIds,
      bbox: [line.minX, line.minY, line.maxX, line.maxY],
      started_at: line.startedAt,
      ended_at: line.endedAt,
    };
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(msg));
    this.onEvent({ type: "line_sent", line_id: msg.line_id, stroke_ids: msg.stroke_ids, bbox: msg.bbox });
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
      const again = this.opened;
      this.opened = true;
      if (again) this.onReopen();
    };
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(String(e.data)) as { type?: string; batch_no?: number; line_id?: unknown; step?: unknown; text?: unknown };
        if (m.type === "ack" && typeof m.batch_no === "number") {
          this.unacked.delete(m.batch_no);
          this.update();
        } else if ((m.type === "line_recognized" || m.type === "mark") && typeof m.line_id === "string") {
          this.onEvent(m as CanvasEvent);
        } else if (m.type === "hint" && typeof m.text === "string") {
          this.onEvent({ type: "hint", line_id: typeof m.line_id === "string" ? m.line_id : null, step: typeof m.step === "number" ? m.step : 1, text: m.text });
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
