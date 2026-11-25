"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiUrl, ApiError, get, idem, post, sse, type BodyOf, type S } from "@/lib/api/client";
import { allTopicsHubId } from "@/lib/api/adapters";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useSessionJobs } from "@/lib/api/use-session-jobs";
import { uploadVoice } from "@/lib/api/voice";
import type { VoiceClip } from "@/lib/audio/voice-recorder";
import { useT } from "@/lib/i18n/provider";
import { skillInvocation } from "@/lib/skills";
import { useSendQueue } from "@/lib/use-send-queue";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { Composer } from "../composer";
import { RichText } from "../rich-text";
import { PRIMARY_BTN, AREA } from "../stubs/stub-parts";
import { SentFiles, type FileRef } from "../attachments";
import { decodeFirstTurn, encodeFirstTurn, firstTurnKey } from "./domain-setup-live";
import { ChatTurnActions, type NoteHubTarget, type NoteTopicTarget, type ResolvedNoteTargets } from "./chat-actions";
import { useErrorText } from "./error-text";
import { jobRows } from "./jobs-plaque";
import { DemoPanel, type OpenDemo } from "./demo-panel";
import { CodePanel, type OpenCode } from "./code-panel";
import { elementCodeTask, type CodeTask } from "@/lib/api/code";
import { PlotCard, type PlotSpec } from "./plot-card";
import { SessionCorner, useSessionPlan } from "./plan-graph-live";

const MEASURE = "mx-auto w-full max-w-[34rem]";
const HEARTBEAT_MS = 30_000;
/** No data (not even a keepalive) on the turn's stream for this long: the request is cut and the learner may send again, so `busy` never hangs for good */
const BUSY_TIMEOUT_MS = 90_000;

type ActionBody = BodyOf<`/v1/sessions/${string}/actions`, "post">;
type ActionPayload = {
  [K in ActionBody as K["kind"]]: Omit<K, "idempotency_key">;
}[ActionBody["kind"]];

type Line = {
  id: string;
  role: "student" | "teacher";
  text: string;
  degraded?: string | null;
  interrupted?: boolean;
  /** a committed teacher turn or recorded student event: its `seq` */
  seq?: number;
  /** a learner message: the files that went with it */
  files?: FileRef[];
};
/** An interactive element of a turn (task, question…): comes as an `element` frame and is answered with an `element_response` action */
type Elem = {
  blockId: string;
  element: S["Element"];
  turnSeq: number | null;
  answered: boolean;
  /** what the learner answered (kept in the form, read only) and the verdict of it, if there was one */
  answer?: string;
  verdict?: Outcome;
};
type Outcome = "correct" | "incorrect" | "partial" | "unclear";

type FailedAction = {
  body: ActionPayload;
  recorded: boolean;
  studentLineId?: string;
  onVerdict?: (o: Outcome) => void;
};

/** Elements the learner answers with text; the rest of the kinds are shown as a card with their title */
const ANSWERABLE = new Set(["task", "short_answer", "free_answer", "choice", "prediction", "why_choice"]);

function parseSessionFeed(
  d: S["SessionDetail"],
  names: Map<string, string>,
  fallbackFileName: string,
): { past: Line[]; pastElems: Elem[] } {
  const past: Line[] = [];
  const pastElems: Elem[] = [];
  // what the learner answered inside the forms: `FeedEvent` carries deterministic `turn_seq`, `element_id` and `outcome`
  // (with fallback to payload / `answer_seq` for legacy events) so reload never guesses by order
  const answers = new Map<string, string>();
  const verdicts = new Map<string, Outcome>();
  const lastAnswer = new Map<string, string>();
  const answerAt = new Map<number, string>();
  const keyOf = (ev: S["FeedEvent"]) => {
    const tSeq = ev.turn_seq ?? (typeof ev.payload.turn_seq === "number" ? ev.payload.turn_seq : null);
    const elId = ev.element_id ?? (typeof ev.payload.element_id === "string" ? ev.payload.element_id : null);
    return tSeq !== null && elId ? `${tSeq}:${elId}` : null;
  };
  for (const ev of d.events) {
    const pl = ev.payload;
    if (ev.type === "element.responded") {
      const k = keyOf(ev);
      if (!k) continue;
      const said = typeof pl.response === "string" && pl.response ? pl.response : typeof pl.text === "string" ? pl.text : "";
      if (said) answers.set(k, said);
      if (ev.outcome) verdicts.set(k, ev.outcome);
      const elId = ev.element_id ?? (typeof pl.element_id === "string" ? pl.element_id : null);
      if (elId) lastAnswer.set(elId, k);
    } else if (ev.type === "answer.submitted") {
      const elId = ev.element_id ?? (typeof pl.element_id === "string" ? pl.element_id : null);
      const k = keyOf(ev) ?? (elId ? lastAnswer.get(elId) ?? null : null);
      if (k) answerAt.set(ev.seq, k);
    } else if (ev.type === "verdict.recorded") {
      const outcome = ev.outcome ?? (typeof pl.outcome === "string" ? (pl.outcome as Outcome) : null);
      if (!outcome) continue;
      const k = keyOf(ev) ?? (typeof pl.answer_seq === "number" ? answerAt.get(pl.answer_seq) ?? null : null);
      if (k) verdicts.set(k, outcome);
    }
  }
  for (const ev of d.events) {
    const text = typeof ev.payload.text === "string" ? ev.payload.text : "";
    // a spoken message: its words are the `voice.transcribed` event, the `message.sent` after it has none
    if ((ev.type === "message.sent" || ev.type === "voice.transcribed") && text) {
      const ids =
        ev.type === "message.sent" && Array.isArray(ev.payload.material_ids)
          ? (ev.payload.material_ids as unknown[]).filter((x): x is string => typeof x === "string")
          : [];
      past.push({
        id: `e-${ev.seq}`,
        role: "student",
        text,
        seq: ev.seq,
        files: ids.map((fid) => ({ id: fid, name: names.get(fid) ?? fallbackFileName })),
      });
    } else if (ev.type === "turn.committed") {
      const markup = ev.payload.markup as { elements?: unknown[] } | undefined;
      // a turn of one task has no text; its line only holds the place of the forms
      if (!text && !markup?.elements?.length) continue;
      past.push({ id: `e-${ev.seq}`, role: "teacher", text, degraded: (ev.payload.degraded_mode as string | null) ?? null, seq: ev.seq });
      // committed elements carry `request_id` (resolved from the live frame's `R…` label, ТЗ §12.3): the only
      // point a `visualization` element becomes renderable, so history is where it actually shows up
      for (const el of markup?.elements ?? []) {
        const e = el as { element_id?: string; kind?: string };
        if (!e.kind) continue;
        const k = `${ev.seq}:${e.element_id}`;
        const answer = answers.get(k);
        pastElems.push({ blockId: k, element: e as S["Element"], turnSeq: ev.seq, answered: answer !== undefined, answer, verdict: verdicts.get(k) });
      }
    }
  }
  return { past, pastElems };
}

async function fetchSessionFeed(sessionId: string, fallbackFileName: string) {
  const d = await get<S["SessionDetail"]>(`/v1/sessions/${sessionId}`);
  const names = new Map<string, string>();
  const withFiles = d.events.some((e) => e.type === "message.sent" && Array.isArray(e.payload.material_ids) && e.payload.material_ids.length > 0);
  if (withFiles) {
    try {
      const list = await get<S["MaterialList"]>(`/v1/domains/${d.domain_id}/materials`);
      for (const m of list.materials) if (m.title) names.set(m.material_id, m.title);
    } catch {}
  }
  const { past, pastElems } = parseSessionFeed(d, names, fallbackFileName);
  return { detail: d, past, pastElems };
}

function isRealHubId(hubId: string | null | undefined, domainId: string): hubId is string {
  return Boolean(hubId && hubId !== "all" && hubId !== allTopicsHubId(domainId));
}

function extractNoteTargets(
  d: S["SessionDetail"],
  domainTopics: { id: string; title: string; hubId?: string }[],
  hubs: S["HubGoal"][],
  fallbackTopicTitle: string,
  allTopicsTitle: string,
): ResolvedNoteTargets {
  const orderedIds: string[] = [];
  const eventTopicNames = new Map<string, string>();
  const addId = (tid: unknown) => {
    if (typeof tid === "string" && tid && !orderedIds.includes(tid)) orderedIds.push(tid);
  };

  if (d.topic_id) addId(d.topic_id);

  const byTitleLower = new Map<string, string>();
  for (const t of domainTopics) byTitleLower.set(t.title.trim().toLowerCase(), t.id);
  for (const h of hubs) {
    for (const t of h.topics ?? []) {
      if (t.name) byTitleLower.set(t.name.trim().toLowerCase(), t.topic_id);
    }
  }

  for (const ev of d.events) {
    const pl = ev.payload;
    if (ev.type === "topic.created" || ev.type === "topic.proposed") {
      if (typeof pl.topic_id === "string" && typeof pl.name === "string" && pl.name.trim()) {
        eventTopicNames.set(pl.topic_id, pl.name.trim());
      }
      addId(pl.topic_id);
    } else if (ev.type === "skill.placed" || ev.type === "skill.created") {
      addId(pl.topic_id);
    } else if (ev.type === "observation.extracted") {
      addId(pl.topic_id);
      if (Array.isArray(pl.elements)) {
        for (const el of pl.elements) {
          if (!el || typeof el !== "object") continue;
          const rec = el as Record<string, unknown>;
          addId(rec.topic_id);
          const ns = rec.new_skill as { topic_hint?: unknown } | null | undefined;
          if (typeof ns?.topic_hint === "string") {
            const matched = byTitleLower.get(ns.topic_hint.trim().toLowerCase());
            if (matched) addId(matched);
          }
        }
      }
    } else if (ev.type === "turn.committed") {
      addId(pl.topic_id);
      const markup = pl.markup as { plan_update?: { current_step?: { landmark?: { kind?: string; id?: string } | null }; next_steps?: { landmark?: { kind?: string; id?: string } | null }[] } } | undefined;
      const curLm = markup?.plan_update?.current_step?.landmark;
      if (curLm?.kind === "topic") addId(curLm.id);
      for (const ns of markup?.plan_update?.next_steps ?? []) {
        if (ns?.landmark?.kind === "topic") addId(ns.landmark.id);
      }
    } else {
      addId(pl.topic_id);
    }
  }

  const chapterHubs = hubs.filter((h) => h.kind !== "domain_all");
  const domainAllHub = hubs.find((h) => h.kind === "domain_all");

  const hubTopicName = new Map<string, string>();
  const topicChapterHub = new Map<string, string>();
  for (const h of chapterHubs) {
    for (const t of h.topics ?? []) {
      if (t.name) hubTopicName.set(t.topic_id, t.name);
      if (!topicChapterHub.has(t.topic_id) && isRealHubId(h.hub_id, d.domain_id)) {
        topicChapterHub.set(t.topic_id, h.hub_id);
      }
    }
  }
  for (const t of domainTopics) {
    if (!topicChapterHub.has(t.id) && isRealHubId(t.hubId, d.domain_id)) {
      topicChapterHub.set(t.id, t.hubId);
    }
  }

  const domainTopicById = new Map(domainTopics.map((t) => [t.id, t]));
  const topics: NoteTopicTarget[] = orderedIds.slice(0, 3).map((tid) => ({
    id: tid,
    title: domainTopicById.get(tid)?.title ?? hubTopicName.get(tid) ?? eventTopicNames.get(tid) ?? fallbackTopicTitle,
    hubId: topicChapterHub.get(tid) ?? null,
  }));

  let hub: NoteHubTarget | null = null;
  if (topics.length > 0) {
    const hubIds = topics.map((t) => t.hubId);
    if (hubIds.every((h): h is string => h !== null) && new Set(hubIds).size === 1) {
      const hid = hubIds[0];
      const found = chapterHubs.find((h) => h.hub_id === hid);
      hub = { id: hid, title: found?.name ?? allTopicsTitle };
    }
  }
  if (!hub) {
    if (domainAllHub) {
      hub = { id: domainAllHub.hub_id, title: domainAllHub.name || allTopicsTitle };
    } else if (isRealHubId(d.hub_id, d.domain_id)) {
      const found = hubs.find((h) => h.hub_id === d.hub_id);
      hub = { id: d.hub_id, title: found?.name ?? allTopicsTitle };
    } else if (chapterHubs[0]) {
      hub = { id: chapterHubs[0].hub_id, title: chapterHubs[0].name };
    }
  }

  return { topics, hub };
}

/** /session/new: create the session in the current subject, then open it */
export function NewSessionLive() {
  const tx = useT();
  const router = useRouter();
  const errorText = useErrorText();
  const { pending, pendingFiles, pendingTopic, domain } = useShell();
  const { setActive } = useData();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !domain.id) return;
    started.current = true;
    startSession({ domain_id: domain.id, topic_id: pendingTopic, intent: "free" })
      .then((s) => {
        // the typed message is the first action of the session, not a "goal": the session page sends it
        if (pending) {
          try {
            sessionStorage.setItem(firstTurnKey(s.session_id), encodeFirstTurn(pending, pendingFiles));
          } catch {}
        }
        setActive(s);
        router.replace(`/session/${s.session_id}`);
      })
      .catch((e) => setError(errorText(e)));
  }, [domain.id, pending, pendingFiles, pendingTopic, router, setActive, errorText]);

  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 pt-4 md:px-7 md:pt-6">
      <header className={MEASURE}>
        <h1 className="display-caps text-display text-paper">{tx.session.newTitle}</h1>
        <p role={error ? "alert" : "status"} className={cn("mt-1.5", error ? "text-st-confused" : "text-paper-dim")}>
          {error ?? tx.session.newThinking}
        </p>
      </header>
    </div>
  );
}

/** A real session: history, streaming teacher turns (`POST …/actions`), elements. There is no "end": the backend closes an idle session and any action or heartbeat opens it again */
export function SessionLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.live;
  const errorText = useErrorText();
  const { domains, setActive, appliedSeq } = useData();
  const { focusDomain } = useShell();
  const [detail, setDetail] = useState<S["SessionDetail"] | null>(null);
  const detailRef = useRef<S["SessionDetail"] | null>(null);
  const [hubs, setHubs] = useState<S["HubGoal"][]>([]);
  const [lines, setLines] = useState<Line[]>([]);
  const [elems, setElems] = useState<Elem[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  /** The streamed article and the committed line of the same turn share this id: React keeps the node, the text never blinks out at commit */
  const [streamId, setStreamId] = useState("");
  /** A `reset {guard}` cut the text: it stays on screen, dimmed, until the first token of the next attempt replaces it */
  const [refining, setRefining] = useState(false);
  /** Elements that came as live `element` frames: shown at once, answerable only after `turn_committed` */
  const [live, setLive] = useState<Elem[]>([]);
  const turnNo = useRef(0);
  /** `status: thinking` arrives after the action is recorded, before the first token */
  const [thinking, setThinking] = useState(false);
  const [verdict, setVerdict] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  /** the turn lock moves with the action itself; `busy` (state) catches up on the next render */
  const busyRef = useRef(false);
  const [unansweredSeq, setUnansweredSeq] = useState<number | null>(null);
  const [lastFailedAction, setLastFailedAction] = useState<FailedAction | null>(null);
  const [artifactTick, setArtifactTick] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const sentFirst = useRef(false);
  /** The session was closed by the idle timer when it was read: the first accepted action or heartbeat reopens it, then `detail` is read again */
  const closed = useRef(false);
  /** bumped by "Try again" when the history could not be read */
  const [historyTry, setHistoryTry] = useState(0);

  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const clearLiveError = useCallback(() => {
    if (detailRef.current) setError(null);
  }, []);

  /** A finished background job (a demo, say) leaves its result in the committed turn: re-read it, a visualization gets its `request_id` there */
  const refreshElements = useCallback(() => {
    get<S["SessionDetail"]>(`/v1/sessions/${id}`)
      .then((d) => {
        setDetail(d);
        setUnansweredSeq(d.unanswered_seq ?? null);
        const resolved = new Map<string, S["Element"]>();
        for (const ev of d.events) {
          if (ev.type !== "turn.committed") continue;
          const markup = ev.payload.markup as { elements?: unknown[] } | undefined;
          for (const el of markup?.elements ?? []) {
            const e = el as { element_id?: string; kind?: string; request_id?: string | null };
            if (e.kind === "visualization" && e.request_id && e.element_id) resolved.set(`${ev.seq}:${e.element_id}`, e as S["Element"]);
          }
        }
        if (!resolved.size) return;
        setElems((all) =>
          all.map((x) => {
            const e = x.element as { kind: string; element_id?: string; request_id?: string | null };
            const next = e.kind === "visualization" && !e.request_id && x.turnSeq !== null ? resolved.get(`${x.turnSeq}:${e.element_id}`) : undefined;
            return next ? { ...x, element: next } : x;
          }),
        );
      })
      .catch(() => {});
  }, [id]);
  /** The backend opened a closed session again (an action or a heartbeat): read its state anew */
  const reopened = useCallback(() => {
    if (!closed.current) return;
    closed.current = false;
    get<S["SessionDetail"]>(`/v1/sessions/${id}`)
      .then((d) => {
        setDetail(d);
        setUnansweredSeq(d.unanswered_seq ?? null);
        if (d.status === "active") setActive(d);
        else closed.current = true;
      })
      .catch(() => {
        closed.current = true;
      });
  }, [id, setActive]);
  /** The plan changes only when a teacher turn is committed: read it again when a turn ends, no polling */
  const { plan, stale: planStale, refresh: refreshPlan } = useSessionPlan(id, { onSuccess: clearLiveError });
  const bg = useSessionJobs(id, {
    onDone: (kinds) => {
      clearLiveError();
      refreshElements();
      // a demo made ahead is ready (or failed): the plan carries where each one stands
      if (kinds.includes("agent.viz")) refreshPlan();
      if (kinds.some((k) => k === "agent.artifact" || k === "artifact.topic_refresh" || k === "artifact.cheatsheet")) {
        setArtifactTick((n) => n + 1);
      }
    },
  });
  const refreshJobs = bg.refresh;
  const [demo, setDemo] = useState<OpenDemo | null>(null);
  /** The code editor of a task element (Т-§6.10), opened on the right as the demo panel */
  const [code, setCode] = useState<(OpenCode & { blockId: string }) | null>(null);
  // a demo goes into the queue a moment after the turn: the plan shows it as being made once the builder starts
  const building = bg.jobs.some((j) => j.kind === "agent.viz");
  useEffect(() => {
    if (building) refreshPlan();
  }, [building, refreshPlan]);

  // Clear red session error once jobs poll succeeds/updates
  useEffect(() => {
    if (!error || !detail) return;
    if (bg.jobs.length > 0 || bg.finished.length > 0) {
      const timer = setTimeout(() => setError(null), 0);
      return () => clearTimeout(timer);
    }
  }, [bg.jobs, bg.finished, error, detail]);

  const domainId = detail?.domain_id;
  useEffect(() => {
    if (!domainId) return;
    let off = false;
    get<S["HubGoal"][]>(`/v1/domains/${domainId}/hubs`)
      .then((h) => {
        if (!off) setHubs(h);
      })
      .catch(() => {});
    return () => {
      off = true;
    };
  }, [domainId, appliedSeq]);

  const activeTopicId = (() => {
    if (!detail) return null;
    if (detail.topic_id) return detail.topic_id;
    for (let i = detail.events.length - 1; i >= 0; i--) {
      const tid = detail.events[i]?.payload?.topic_id;
      if (typeof tid === "string" && tid) return tid;
    }
    return null;
  })();

  const scrollDown = () => requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));

  /** One action of the learner; the answer of the teacher streams back as frames (ТЗ Ф4 §4.7) */
  const act = useCallback(
    async (
      body: ActionPayload,
      studentText?: string,
      onVerdict?: (o: Outcome) => void,
      files?: FileRef[],
      opts?: { skipStudentEcho?: boolean; existingStudentLineId?: string },
    ): Promise<boolean> => {
      if (busy || busyRef.current) {
        setError(t.turnLocked);
        return false;
      }
      busyRef.current = true;
      setBusy(true);
      setError(null);
      setVerdict(null);
      setLastFailedAction(null);
      // a turn whose stream goes silent (no frame, no keepalive) is cut after `BUSY_TIMEOUT_MS`; every chunk restarts the clock
      const cut = new AbortController();
      let stalled = false;
      let watchdog: ReturnType<typeof setTimeout> | undefined;
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => {
          stalled = true;
          cut.abort();
        }, BUSY_TIMEOUT_MS);
      };
      arm();
      /** the verdict of an answer given inside an element goes into that form, not under the reply */
      const shown = onVerdict ?? setVerdict;
      const lineId = opts?.existingStudentLineId ?? `s-${Date.now()}`;
      const turnId = `t-${Date.now()}-${++turnNo.current}`;
      if (studentText && !opts?.skipStudentEcho) {
        setLines((l) => [...l, { id: lineId, role: "student", text: studentText, files }]);
      }
      setStreamId(turnId);
      setStreaming("");
      scrollDown();
      let reply = "";
      /** After `reset {guard}`: `reply` is the cut text, still shown; the next token starts the new attempt */
      let retry = false;
      let degraded: string | null = null;
      let committed: number | null = null;
      let accepted = false;
      let actionRecorded = false;
      const fresh: { block: string; element: S["Element"] }[] = [];
      const showLive = () => setLive(fresh.map((f) => ({ blockId: `live:${f.block}`, element: f.element, turnSeq: null, answered: false })));
      try {
        await sse(
          "POST",
          `/v1/sessions/${id}/actions`,
          // the body is one of the actions of the contract (message, intent, element_response, retry_turn…); the shape is checked by the backend
          { ...body, idempotency_key: idem() } as ActionBody,
          ({ event, data }) => {
            if (event === "action_accepted") {
              accepted = true;
            } else if (event === "action_recorded") {
              actionRecorded = true;
              reopened();
              const v = data.verdict as { outcome?: Outcome } | null | undefined;
              if (v?.outcome) shown(v.outcome);
            } else if (event === "verdict") shown(data.outcome as Outcome);
            else if (event === "status") setThinking(data.state === "thinking");
            else if (event === "token") {
              if (retry) {
                // the first token of the new attempt: the cut text (and anything shown with it) is replaced
                retry = false;
                reply = "";
                fresh.length = 0;
                setRefining(false);
                showLive();
              }
              reply += String(data.text ?? "");
              setStreaming(reply);
            } else if (event === "reset") {
              if (data.reason === "guard" && reply) {
                // a cut answer is harmless: keep it dimmed with «refining…» until the new attempt starts
                retry = true;
                setRefining(true);
              } else {
                // `spoiler` (or anything unknown): the text may give the answer away, drop it at once
                retry = false;
                reply = "";
                fresh.length = 0;
                setStreaming("");
                setRefining(false);
                showLive();
              }
            } else if (event === "element") {
              const block = String(data.block_id);
              if (!fresh.some((f) => f.block === block)) {
                fresh.push({ block, element: data.element as S["Element"] });
                showLive();
              }
            } else if (event === "turn_committed") {
              committed = typeof data.seq === "number" ? data.seq : null;
              degraded = (data.degraded_mode as string | null) ?? null;
            } else if (event === "error") throw new ApiError(500, String(data.code ?? "ERROR"), String(data.message ?? ""));
          },
          cut.signal,
          arm,
        );
        // the stream closed with no `turn_committed`: either 201 JSON (turn already answered/accepted) or cut stream
        if (committed === null) {
          if (accepted) {
            const synced = await fetchSessionFeed(id, tx.composer.attachFallbackName).catch(() => null);
            if (synced) {
              setDetail(synced.detail);
              setUnansweredSeq(synced.detail.unanswered_seq ?? null);
              setLines(synced.past);
              setElems(synced.pastElems);
            } else {
              setUnansweredSeq(null);
            }
            setLastFailedAction(null);
            return true;
          }
          if (reply) setLines((l) => [...l, { id: turnId, role: "teacher", text: reply, interrupted: true }]);
          else setError(t.interrupted);
          setLastFailedAction({ body, recorded: actionRecorded, studentLineId: studentText ? lineId : opts?.existingStudentLineId, onVerdict });
          return false;
        }
        // one batch with the `finally` below: the streamed article becomes the committed line (same key), the live cards become answerable
        // a turn with only a task has no text, but its line carries the `seq` the cards hang on
        if (reply || fresh.length) setLines((l) => [...l, { id: turnId, role: "teacher", text: reply, degraded, seq: committed ?? undefined }]);
        if (fresh.length) {
          // `seq:id` is unique over the session; `b1` of one turn is not `b1` of the next
          const seq = committed;
          const done = fresh.map((f) => ({ blockId: `${seq}:${(f.element as { element_id?: string }).element_id ?? f.block}`, element: f.element, turnSeq: seq, answered: false }));
          setElems((e) => [...e, ...done.filter((d) => !e.some((x) => x.blockId === d.blockId))]);
        }
        setUnansweredSeq(null);
        setLastFailedAction(null);
        return true;
      } catch (e) {
        setError(stalled ? t.busyTimeout : e instanceof ApiError && e.code === "TURN_LOCKED" ? t.turnLocked : errorText(e));
        setLastFailedAction({ body, recorded: actionRecorded, studentLineId: studentText ? lineId : opts?.existingStudentLineId, onVerdict });
        // what was already shown stays on screen, marked: this part is not committed and is gone after a reload
        if (reply) setLines((l) => [...l, { id: turnId, role: "teacher", text: reply, interrupted: true }]);
        return false;
      } finally {
        clearTimeout(watchdog);
        setStreaming(null);
        setRefining(false);
        setLive([]);
        setThinking(false);
        busyRef.current = false;
        setBusy(false);
        scrollDown();
        // a turn queues background agents (demo, tasks, checks) shortly after it is committed
        refreshJobs();
        refreshPlan();
        if (!accepted) refreshElements();
      }
    },
    [busy, id, t.turnLocked, t.interrupted, t.busyTimeout, tx.composer.attachFallbackName, errorText, reopened, refreshJobs, refreshPlan, refreshElements],
  );

  const send = useCallback(
    (text: string, files?: FileRef[]) =>
      text.trim()
        ? act(
            { kind: "message", text, skill_invocation: skillInvocation(text), ...(files?.length ? { material_ids: files.map((f) => f.id) } : {}) },
            text,
            undefined,
            files,
          )
        : undefined,
    [act],
  );

  const queue = useSendQueue<FileRef>({
    busy,
    onSend: (item) => {
      // a turn that began after the queue looked (the ref moves with the action, the state a render later): not sent, stays queued
      if (busyRef.current) return false;
      void send(item.text, item.files);
      return true;
    },
  });

  const handleComposerSend = useCallback(
    (text: string, files?: FileRef[]) => {
      if (!text.trim() && !files?.length) return;
      if (busy || queue.items.length > 0) {
        queue.enqueue(text, files);
        return;
      }
      void send(text, files);
    },
    [busy, queue, send],
  );

  /** A recorded clip → the recognized text goes to the teacher once: the message points at the recorded voice segment (`voice_seq`) and carries no text. Returns an error text, or null */
  const sendVoice = useCallback(
    async (clip: VoiceClip): Promise<string | null> => {
      try {
        const heard = await uploadVoice(id, clip);
        if (!heard.text.trim()) return tx.voiceSession.nothingHeard;
        if (heard.seq === null) return tx.voiceSession.nothingHeard;
        void act({ kind: "message", voice_seq: heard.seq }, heard.text);
        return null;
      } catch (e) {
        if (e instanceof ApiError) {
          if (e.status === 413) return tx.voiceSession.tooLong;
          if (e.status === 415) return tx.voiceSession.badFormat;
          if (e.status === 403) return tx.voiceSession.notAllowed;
        }
        return errorText(e);
      }
    },
    [id, act, errorText, tx.voiceSession],
  );

  const respond = async (el: Elem, response: string) => {
    if (!response.trim() || el.turnSeq === null) return;
    // the answer stays in its form (read only), no separate bubble: the reply of the teacher follows the form
    const patch = (p: Partial<Elem>) => setElems((all) => all.map((x) => (x.blockId === el.blockId ? { ...x, ...p } : x)));
    patch({ answered: true, answer: response, verdict: undefined });
    const ok = await act({ kind: "element_response", turn_seq: el.turnSeq, element_id: el.element.element_id, response }, undefined, (verdict) => patch({ verdict }));
    // a failed send (network, locked turn, 5xx) must not eat the task: it comes back so the answer can be sent again
    if (!ok) patch({ answered: false, answer: undefined, verdict: undefined });
  };

  // History: the feed of the session; text lives in `message.sent` / `voice.transcribed` (student) and `turn.committed` (teacher)
  useEffect(() => {
    let off = false;
    fetchSessionFeed(id, tx.composer.attachFallbackName)
      .then(({ detail: d, past, pastElems }) => {
        if (off) return;
        setDetail(d);
        setUnansweredSeq(d.unanswered_seq ?? null);
        closed.current = d.status !== "active";
        focusDomain(d.domain_id);
        // a session opened from a topic page becomes the active one in the sessions panel
        if (d.status === "active") setActive(d);
        setLines(past);
        // the forms come back where the teacher put them (right after the text of their turn), answered ones with the answer in them
        setElems(pastElems);
        scrollDown();
      })
      .catch((e) => {
        if (off) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) setMissing(true);
        else setError(errorText(e));
      });
    return () => {
      off = true;
    };
    // load once per session (and again on "Try again")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, historyTry]);

  // The message typed in the input box before the session existed; or, for a fresh learn/practice/review session, its intent
  useEffect(() => {
    if (!detail || sentFirst.current) return;
    sentFirst.current = true;
    let first: string | null = null;
    try {
      first = sessionStorage.getItem(firstTurnKey(id));
      sessionStorage.removeItem(firstTurnKey(id));
    } catch {}
    if (first) {
      const turn = decodeFirstTurn(first);
      void send(turn.text, turn.files);
    }
    // a fresh session already has `session.started` in its log: "fresh" means nobody has spoken yet
    else if (
      (detail.intent === "learn" || detail.intent === "practice" || detail.intent === "review") &&
      !detail.events.some((e) => e.type === "message.sent" || e.type === "turn.committed")
    ) {
      void act({ kind: "intent", intent: detail.intent, topic_id: detail.topic_id, hub_id: detail.hub_id });
    }
  }, [detail, id, send, act]);

  // Presence: the backend closes a session after a while without activity; a heartbeat keeps it and brings a closed one back
  useEffect(() => {
    const timer = setInterval(
      () =>
        void post(`/v1/sessions/${id}/heartbeat`)
          .then(() => {
            clearLiveError();
            reopened();
          })
          .catch(() => {}),
      HEARTBEAT_MS,
    );
    return () => clearInterval(timer);
  }, [id, reopened, clearLiveError]);

  const resolveTargets = useCallback(async (): Promise<ResolvedNoteTargets> => {
    const cur = detailRef.current;
    if (!cur) return { topics: [], hub: null };
    const [freshRes, hubsRes] = await Promise.allSettled([
      get<S["SessionDetail"]>(`/v1/sessions/${id}`),
      get<S["HubGoal"][]>(`/v1/domains/${cur.domain_id}/hubs`),
    ]);
    const freshDetail = freshRes.status === "fulfilled" ? freshRes.value : cur;
    const freshHubs = hubsRes.status === "fulfilled" ? hubsRes.value : hubs;
    if (freshRes.status === "fulfilled") setDetail(freshDetail);
    if (hubsRes.status === "fulfilled") setHubs(freshHubs);
    const domainTopics = domains.find((d) => d.id === freshDetail.domain_id)?.topics ?? [];
    return extractNoteTargets(freshDetail, domainTopics, freshHubs, tx.meta.topic, tx.live.allTopics);
  }, [id, hubs, domains, tx.meta.topic, tx.live.allTopics]);

  const retryAction = useCallback(
    async (studentLineId?: string) => {
      if (lastFailedAction && !lastFailedAction.recorded && (!studentLineId || lastFailedAction.studentLineId === studentLineId)) {
        await act(lastFailedAction.body, undefined, lastFailedAction.onVerdict, undefined, {
          skipStudentEcho: true,
          existingStudentLineId: lastFailedAction.studentLineId,
        });
        return;
      }
      await act({ kind: "retry_turn" });
    },
    [act, lastFailedAction],
  );

  if (missing) {
    return (
      <div className={cn("px-4 pt-6", MEASURE)}>
        <p className="text-paper-dim">{t.sessionMissing}</p>
      </div>
    );
  }
  if (!detail) {
    return error ? (
      <div className={cn("px-4 pt-6", MEASURE)}>
        <p role="alert" className="text-st-confused">
          {error}
        </p>
        <button
          type="button"
          onClick={() => {
            setError(null);
            setHistoryTry((n) => n + 1);
          }}
          className={cn(PRIMARY_BTN, "mt-3")}
        >
          {t.retry}
        </button>
      </div>
    ) : (
      <div className="flex-1" aria-busy="true" />
    );
  }

  const intent = detail.intent ?? "free";
  const modeLabel = t.modes[intent as keyof typeof t.modes] ?? intent;
  const domain = domains.find((d) => d.id === detail.domain_id);
  const topic = domain?.topics.find((x) => x.id === detail.topic_id);
  const title = topic?.title ?? modeLabel ?? tx.session.newTitle;

  // Orphaned student message: either backend `unanswered_seq` is set, or client-side there is no committed teacher turn after the last student line
  let lastStudentIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].role === "student") {
      lastStudentIdx = i;
      break;
    }
  }
  const hasCommittedTeacherAfter =
    lastStudentIdx >= 0 && lines.slice(lastStudentIdx + 1).some((l) => l.role === "teacher" && !l.interrupted);
  const hasOrphanedStudent =
    !busy &&
    streaming === null &&
    lastStudentIdx >= 0 &&
    (!hasCommittedTeacherAfter || (unansweredSeq !== null && lines[lastStudentIdx].seq === unansweredSeq));
  const hasStandaloneUnanswered =
    !busy && streaming === null && unansweredSeq !== null && !hasOrphanedStudent;

  const hasBgCheck = bg.jobs.some((j) => j.kind === "check.symbolic" || j.kind === "check.model" || j.kind === "task.check");
  const hasBgViz = bg.jobs.some((j) => j.kind === "agent.viz");
  const teacherStatusKey: keyof typeof tx.session.teacherStatus | null = busy
    ? refining
      ? "checking"
      : streaming
        ? "writing"
        : thinking
          ? "thinking"
          : "reading"
    : hasBgCheck
      ? "checking"
      : hasBgViz
        ? "makingDemo"
        : null;

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-8 md:px-7 md:pt-6">
        <header className={cn("min-w-0", MEASURE)}>
          <h1 className="line-clamp-2 display-caps text-display text-paper">{title}</h1>
          <p className="mt-1.5 text-paper-dim">
            {domain?.title}
            {domain && " · "}
            {modeLabel}
          </p>
        </header>

        <div className={cn("mt-6 flex flex-col gap-7", MEASURE)}>
          {lines.length === 0 && streaming === null && <p className="text-paper-dim">{t.sessionEmpty}</p>}
          {/* one keyed list: the streamed article of a turn and its committed line share a key, so React keeps the node and the text never blinks out */}
          {[
            ...lines.flatMap((l, idx) => {
              if (l.role === "student") {
                const showOrphanRetry = hasOrphanedStudent && idx === lastStudentIdx;
                return [
                  <StudentLine
                    key={l.id}
                    text={l.text}
                    files={l.files}
                    onRetry={showOrphanRetry ? () => void retryAction(l.id) : undefined}
                    retryLabel={t.retry}
                  />,
                ];
              }
              let studentPrompt: string | null = null;
              for (let j = idx - 1; j >= 0; j--) {
                if (lines[j].role === "student") {
                  studentPrompt = lines[j].text;
                  break;
                }
              }
              return [
                // a turn of one task has no text: only its forms are drawn
                l.text || l.degraded || l.interrupted ? (
                  <article key={l.id} aria-label={tx.session.teacherTurn} className="text-paper [overflow-wrap:anywhere]">
                    <RichText text={l.text} />
                    {l.degraded && <p className="mt-2 text-small text-paper-dim">{t.degraded}</p>}
                    {l.interrupted && <p className="mt-2 text-small text-paper-dim">{t.interrupted}</p>}
                    {!l.interrupted && l.text && (
                      <ChatTurnActions
                        ctx={{
                          sessionId: id,
                          domainId: detail.domain_id,
                          topicId: activeTopicId,
                          turnId: l.id,
                          turnSeq: l.seq,
                          turnText: l.text,
                          studentPrompt,
                          resolveTargets,
                          onJobsRefresh: refreshJobs,
                          artifactTick,
                        }}
                      />
                    )}
                  </article>
                ) : null,
                // the forms of a turn stay right after its text, in the order of the session, answered or not
                ...(l.seq === undefined
                  ? []
                  : elems
                      .filter((x) => x.turnSeq === l.seq)
                      .map((el) => (
                        <ElementCard
                          key={el.blockId}
                          el={el}
                          disabled={busy}
                          sessionId={id}
                          onRespond={(r) => void respond(el, r)}
                          onOpenCode={(task) => setCode({ task, title: tx.code.taskLabel, blockId: el.blockId })}
                        />
                      ))),
              ];
            }),
            streaming !== null && (
              <article
                key={streamId}
                aria-label={tx.session.teacherTurn}
                aria-busy="true"
                className="text-paper [overflow-wrap:anywhere]"
              >
                {streaming ? (
                  <RichText text={streaming} className={cn("transition-opacity duration-(--dur-s) ease-(--ease-out)", refining && "opacity-60")} />
                ) : (
                  <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
                    {thinking ? tx.session.teacherThinking : tx.session.teacherReading}
                    <Dots />
                  </p>
                )}
                {refining && streaming && (
                  <p role="status" className="mt-2 text-small text-paper-dim">
                    {tx.session.teacherRefining}
                  </p>
                )}
              </article>
            ),
            // live cards of the turn in progress: already there, but not answerable until the turn is committed
            ...(streaming !== null ? live.map((el) => <ElementCard key={el.blockId} el={el} disabled onRespond={() => {}} />) : []),
          ]}
          {verdict && (verdict === "correct" || verdict === "incorrect") && (
            <p role="status" className={cn("text-body", verdict === "correct" ? "text-st-solid" : "text-st-confused")}>
              {verdict === "correct" ? tx.stubs.practice.correct : tx.live.notQuite}
            </p>
          )}
          {hasStandaloneUnanswered && !error && (
            <div>
              <button
                type="button"
                disabled={busy}
                onClick={() => void retryAction()}
                className="caps rounded-pill border border-seam px-3.5 py-1.5 text-small text-paper transition-colors hover:border-ribbon hover:text-ribbon disabled:opacity-50"
              >
                {t.retry}
              </button>
            </div>
          )}
          {error && (
            <div role="alert" className="flex flex-wrap items-center gap-3 text-small text-st-confused">
              <p className="min-w-0 flex-1">{error}</p>
              <button
                type="button"
                disabled={busy}
                onClick={() => void retryAction()}
                className="caps shrink-0 rounded-pill border border-st-confused/60 px-3 py-1 text-st-confused transition-colors hover:border-st-confused hover:bg-cloth-hover disabled:opacity-50"
              >
                {t.retry}
              </button>
            </div>
          )}
        </div>
      </div>

      <SessionCorner plan={plan} stale={planStale} jobs={jobRows(bg.jobs, bg.finished)} topicId={detail.topic_id} onOpenDemo={setDemo} />
      {demo && <DemoPanel demo={demo} onClose={() => setDemo(null)} />}
      {code && (
        <CodePanel
          key={code.task.task_id}
          open={code}
          sessionId={id}
          onClose={() => setCode(null)}
          onVerdict={(outcome) => {
            const blockId = code.blockId;
            setElems((all) => all.map((x) => (x.blockId === blockId ? { ...x, answered: true, verdict: outcome } : x)));
          }}
        />
      )}

      <div className="px-4 pb-4 md:px-7 md:pb-5">
        {teacherStatusKey && (
          <p role="status" className="mb-1.5 flex items-center gap-2 pr-12 text-small text-paper-dim">
            <span aria-hidden className="size-1.5 rounded-pill bg-ribbon motion-safe:animate-pulse" />
            <span>{tx.session.teacherStatus[teacherStatusKey]}</span>
          </p>
        )}
        {queue.items.length > 0 && (
          <ul aria-label={tx.session.queueLabel} className="mb-2 flex flex-wrap gap-1.5 pr-12">
            {queue.items.map((item) => (
              <li
                key={item.id}
                className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-pill border border-seam bg-cloth-raised px-3 py-1 text-small text-paper"
              >
                <span className="min-w-0 truncate">{item.text}</span>
                {item.files?.length ? (
                  <span className="shrink-0 text-paper-dim">{tx.session.queueFiles(item.files.length)}</span>
                ) : null}
                <button
                  type="button"
                  aria-label={tx.session.queueRemove}
                  onClick={() => queue.remove(item.id)}
                  className="-mr-1 inline-flex size-5 shrink-0 items-center justify-center rounded-pill text-paper-dim hover:bg-cloth-hover hover:text-paper"
                >
                  <X aria-hidden className="size-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <Composer
          label={tx.session.composerLabel}
          placeholder={tx.session.replyPlaceholder}
          submitText={tx.session.send}
          onSend={handleComposerSend}
          sessionId={id}
          voice={{ onClip: sendVoice, disabled: busy }}
          duck
        />
      </div>
    </div>
  );
}

/** A task or a question of the teacher: its prompt and a box for the answer. Other element kinds show only what they carry */
function ElementCard({
  el,
  disabled,
  onRespond,
  sessionId,
  onOpenCode,
}: {
  el: Elem;
  disabled: boolean;
  onRespond: (text: string) => void;
  sessionId?: string;
  onOpenCode?: (task: CodeTask) => void;
}) {
  const tx = useT();
  const [text, setText] = useState("");
  const e = el.element as { kind: string; prompt?: string; request?: string | null; request_id?: string | null; plot?: unknown };
  /** A live card (its turn is not committed yet): the same box as the committed one, so nothing moves at commit, but it cannot be answered */
  const inactive = el.turnSeq === null;
  /** Т-§6.10: a task element may stand for a code task; elements carry no task id, so the backend is asked once */
  const [codeTask, setCodeTask] = useState<CodeTask | null>(null);
  const turnSeq = el.turnSeq;
  const elementId = (el.element as { element_id?: string }).element_id;
  useEffect(() => {
    if ((e.kind !== "task" && e.kind !== "code_task") || turnSeq === null || !sessionId || !elementId || !onOpenCode) return;
    let alive = true;
    elementCodeTask(sessionId, turnSeq, elementId)
      .then((found) => alive && setCodeTask(found))
      .catch(() => {});
    return () => {
      alive = false;
    };
    // onOpenCode is a fresh closure every render; asking once per element is enough
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [e.kind, turnSeq, sessionId, elementId]);
  const answerable = ANSWERABLE.has(e.kind) && !codeTask;
  const viz = e.kind === "visualization" ? e : null;
  return (
    <section aria-label={tx.live.taskFromTeacher} aria-busy={inactive || undefined} className="rounded-[2px] border border-seam p-4">
      <p className="caps text-paper-dim">
        {e.kind === "task" ? tx.live.taskFromTeacher : e.kind === "code_task" ? tx.code.taskLabel : e.kind.replace(/_/g, " ")}
      </p>
      {e.prompt && (
        <div className="mt-2 text-paper">
          <RichText text={e.prompt} />
        </div>
      )}
      {/* a code task element carries no text of its own: the statement comes with the task */}
      {e.kind === "code_task" && codeTask?.condition && (
        <div className="mt-2 text-paper">
          <RichText text={codeTask.condition} />
        </div>
      )}
      {e.kind === "code_task" && !codeTask && !inactive && <p className="mt-2 text-small text-paper-dim">{tx.code.loading}</p>}
      {viz &&
        // `request_id` is resolved only once the turn is committed (ТЗ §12.3): a live `element` frame still
        // carries the raw `request` label and has nothing fetchable yet (Д-B-178 serves by `request_id`)
        (viz.request_id ? (
          <iframe
            src={apiUrl(`/v1/viz/${viz.request_id}`)}
            sandbox="allow-scripts"
            title={tx.live.taskFromTeacher}
            className="mt-2 h-80 w-full rounded-[2px] border border-seam-soft bg-paper"
          />
        ) : (
          viz.plot ? null : <p className="mt-2 text-small text-paper-dim">{tx.live.vizPreparing}</p>
        ))}
      {viz?.plot ? <PlotCard plot={viz.plot as PlotSpec} /> : null}
      {codeTask && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => onOpenCode?.(codeTask)} className={PRIMARY_BTN}>
            {tx.code.openEditor}
          </button>
          {(el.verdict === "correct" || el.verdict === "partial" || el.verdict === "incorrect") && (
            <p role="status" className={cn("text-body", el.verdict === "correct" ? "text-st-solid" : "text-st-confused")}>
              {tx.code.outcome[el.verdict]}
            </p>
          )}
        </div>
      )}
      {answerable && (
        <form
          className="mt-3"
          onSubmit={(ev) => {
            ev.preventDefault();
            // the box keeps the text: if the send fails, the card comes back unanswered with the words still in it
            onRespond(text);
          }}
        >
          <label className="sr-only" htmlFor={`el-${el.blockId}`}>
            {tx.live.elementAnswer}
          </label>
          <textarea
            id={`el-${el.blockId}`}
            value={el.answered ? (el.answer ?? text) : text}
            onChange={(ev) => setText(ev.target.value)}
            placeholder={tx.live.answerPlaceholder}
            disabled={inactive}
            readOnly={el.answered}
            className={cn(AREA, "min-h-16", inactive && "opacity-60")}
          />
          {/* answered: the answer stays in the form, read only, nothing to send; the verdict (if any) is under it */}
          {!el.answered && (
            <button type="submit" disabled={inactive || disabled || !text.trim()} className={cn(PRIMARY_BTN, "mt-3")}>
              {tx.live.elementSend}
            </button>
          )}
          {el.answered && (el.verdict === "correct" || el.verdict === "incorrect") && (
            <p role="status" className={cn("mt-3 text-body", el.verdict === "correct" ? "text-st-solid" : "text-st-confused")}>
              {el.verdict === "correct" ? tx.stubs.practice.correct : tx.live.notQuite}
            </p>
          )}
        </form>
      )}
    </section>
  );
}

function StudentLine({
  text,
  files,
  onRetry,
  retryLabel,
}: {
  text: string;
  files?: FileRef[];
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const tx = useT();
  return (
    <div className="flex flex-col items-end">
      <p
        role="group"
        aria-label={tx.session.you}
        className="max-w-[85%] min-w-0 rounded-[1rem] border border-seam bg-cloth-hover px-4 py-2.5 whitespace-pre-wrap text-paper [overflow-wrap:anywhere]"
      >
        {text}
      </p>
      {files?.length ? <SentFiles files={files} /> : null}
      {onRetry && retryLabel && (
        <button
          type="button"
          onClick={onRetry}
          className="caps mt-1.5 rounded-pill border border-seam px-3 py-1 text-small text-paper-dim transition-colors hover:border-ribbon hover:text-ribbon"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

function Dots() {
  return (
    <span aria-hidden className="inline-flex gap-0.5">
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1 animate-pulse rounded-pill bg-paper-dim" style={{ animationDelay: `${i * 180}ms` }} />
      ))}
    </span>
  );
}
