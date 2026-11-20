"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl, ApiError, get, idem, post, sse, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useSessionJobs } from "@/lib/api/use-session-jobs";
import { uploadVoice } from "@/lib/api/voice";
import type { VoiceClip } from "@/lib/audio/voice-recorder";
import { useT } from "@/lib/i18n/provider";
import { skillInvocation } from "@/lib/skills";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { Composer } from "../composer";
import { RichText } from "../rich-text";
import { PRIMARY_BTN, AREA } from "../stubs/stub-parts";
import { SentFiles, type FileRef } from "../attachments";
import { decodeFirstTurn, encodeFirstTurn, firstTurnKey } from "./domain-setup-live";
import { ChatTurnActions } from "./chat-actions";
import { useErrorText } from "./error-text";
import { jobRows } from "./jobs-plaque";
import { DemoPanel, type OpenDemo } from "./demo-panel";
import { PlotCard, type PlotSpec } from "./plot-card";
import { SessionCorner, useSessionPlan } from "./plan-graph-live";

const orNone = <T,>(p: Promise<T>) =>
  p.catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });

const MEASURE = "mx-auto w-full max-w-[34rem]";
const HEARTBEAT_MS = 30_000;
/** No data (not even a keepalive) on the turn's stream for this long: the request is cut and the learner may send again, so `busy` never hangs for good */
const BUSY_TIMEOUT_MS = 90_000;

type Line = {
  id: string;
  role: "student" | "teacher";
  text: string;
  degraded?: string | null;
  interrupted?: boolean;
  /** a committed teacher turn: its `seq`; the elements of the turn are drawn right after its text */
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

/** Elements the learner answers with text; the rest of the kinds are shown as a card with their title */
const ANSWERABLE = new Set(["task", "short_answer", "free_answer", "choice", "prediction", "why_choice"]);

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
  const [notesState, setNotesState] = useState<{ topicId: string; has: boolean } | null>(null);
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
  const scroller = useRef<HTMLDivElement>(null);
  const sentFirst = useRef(false);
  /** The session was closed by the idle timer when it was read: the first accepted action or heartbeat reopens it, then `detail` is read again */
  const closed = useRef(false);
  /** bumped by "Try again" when the history could not be read */
  const [historyTry, setHistoryTry] = useState(0);

  /** A finished background job (a demo, say) leaves its result in the committed turn: re-read it, a visualization gets its `request_id` there */
  const refreshElements = useCallback(() => {
    get<S["SessionDetail"]>(`/v1/sessions/${id}`)
      .then((d) => {
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
        if (d.status === "active") setActive(d);
        else closed.current = true;
      })
      .catch(() => {
        closed.current = true;
      });
  }, [id, setActive]);
  /** The plan changes only when a teacher turn is committed: read it again when a turn ends, no polling */
  const { plan, refresh: refreshPlan } = useSessionPlan(id);
  const bg = useSessionJobs(id, {
    onDone: (kinds) => {
      refreshElements();
      // a demo made ahead is ready (or failed): the plan carries where each one stands
      if (kinds.includes("agent.viz")) refreshPlan();
    },
  });
  const refreshJobs = bg.refresh;
  const [demo, setDemo] = useState<OpenDemo | null>(null);
  // a demo goes into the queue a moment after the turn: the plan shows it as being made once the builder starts
  const building = bg.jobs.some((j) => j.kind === "agent.viz");
  useEffect(() => {
    if (building) refreshPlan();
  }, [building, refreshPlan]);

  const activeTopicId = (() => {
    if (!detail) return null;
    if (detail.topic_id) return detail.topic_id;
    for (let i = detail.events.length - 1; i >= 0; i--) {
      const tid = detail.events[i]?.payload?.topic_id;
      if (typeof tid === "string" && tid) return tid;
    }
    return null;
  })();
  const hasTopicNotes = Boolean(activeTopicId) && notesState?.topicId === activeTopicId && notesState.has;

  useEffect(() => {
    if (!activeTopicId) return;
    let off = false;
    void orNone(get<S["ArtifactResponse"]>(`/v1/topics/${activeTopicId}/notes`))
      .then((notes) => {
        if (!off) setNotesState({ topicId: activeTopicId, has: (notes?.blocks.length ?? 0) > 0 });
      })
      .catch(() => {});
    return () => {
      off = true;
    };
  }, [activeTopicId, appliedSeq]);

  const scrollDown = () => requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));

  /** One action of the learner; the answer of the teacher streams back as frames (ТЗ Ф4 §4.7) */
  const act = useCallback(
    async (body: Record<string, unknown>, studentText?: string, onVerdict?: (o: Outcome) => void, files?: FileRef[]): Promise<boolean> => {
      if (busy) {
        setError(t.turnLocked);
        return false;
      }
      setBusy(true);
      setError(null);
      setVerdict(null);
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
      const lineId = `s-${Date.now()}`;
      const turnId = `t-${Date.now()}-${++turnNo.current}`;
      if (studentText) setLines((l) => [...l, { id: lineId, role: "student", text: studentText, files }]);
      setStreamId(turnId);
      setStreaming("");
      scrollDown();
      let reply = "";
      /** After `reset {guard}`: `reply` is the cut text, still shown; the next token starts the new attempt */
      let retry = false;
      let degraded: string | null = null;
      let committed: number | null = null;
      const fresh: { block: string; element: S["Element"] }[] = [];
      const showLive = () => setLive(fresh.map((f) => ({ blockId: `live:${f.block}`, element: f.element, turnSeq: null, answered: false })));
      try {
        await sse(
          "POST",
          `/v1/sessions/${id}/actions`,
          // the body is one of the actions of the contract (message, intent, element_response…); the shape is checked by the backend
          { ...body, idempotency_key: idem() } as never,
          ({ event, data }) => {
            if (event === "action_recorded") {
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
        // the stream closed with no `turn_committed` and no `error` frame (proxy cut, server restart): the turn is not saved
        if (committed === null) {
          if (reply) setLines((l) => [...l, { id: turnId, role: "teacher", text: reply, interrupted: true }]);
          else setError(t.interrupted);
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
        return true;
      } catch (e) {
        setError(stalled ? t.busyTimeout : e instanceof ApiError && e.code === "TURN_LOCKED" ? t.turnLocked : errorText(e));
        // what was already shown stays on screen, marked: this part is not committed and is gone after a reload
        if (reply) setLines((l) => [...l, { id: turnId, role: "teacher", text: reply, interrupted: true }]);
        return false;
      } finally {
        clearTimeout(watchdog);
        setStreaming(null);
        setRefining(false);
        setLive([]);
        setThinking(false);
        setBusy(false);
        scrollDown();
        // a turn queues background agents (demo, tasks, checks) shortly after it is committed
        refreshJobs();
        refreshPlan();
      }
    },
    [busy, id, t.turnLocked, t.interrupted, t.busyTimeout, errorText, reopened, refreshJobs, refreshPlan],
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
    get<S["SessionDetail"]>(`/v1/sessions/${id}`)
      .then(async (d) => {
        if (off) return;
        // the names of the files that went with messages (the log holds their ids)
        const names = new Map<string, string>();
        const withFiles = d.events.some((e) => e.type === "message.sent" && Array.isArray(e.payload.material_ids) && e.payload.material_ids.length > 0);
        if (withFiles) {
          try {
            const list = await get<S["MaterialList"]>(`/v1/domains/${d.domain_id}/materials`);
            for (const m of list.materials) if (m.title) names.set(m.material_id, m.title);
          } catch {}
          if (off) return;
        }
        setDetail(d);
        closed.current = d.status !== "active";
        focusDomain(d.domain_id);
        // a session opened from a topic page becomes the active one in the sessions panel
        if (d.status === "active") setActive(d);
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
            const ids = ev.type === "message.sent" && Array.isArray(ev.payload.material_ids) ? (ev.payload.material_ids as unknown[]).filter((x): x is string => typeof x === "string") : [];
            past.push({ id: `e-${ev.seq}`, role: "student", text, files: ids.map((id) => ({ id, name: names.get(id) ?? tx.composer.attachFallbackName })) });
          }
          else if (ev.type === "turn.committed") {
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
    else if (detail.intent && detail.intent !== "free" && !detail.events.some((e) => e.type === "message.sent" || e.type === "turn.committed")) {
      void act({ kind: "intent", intent: detail.intent, topic_id: detail.topic_id, hub_id: detail.hub_id });
    }
  }, [detail, id, send, act]);

  // Presence: the backend closes a session after a while without activity; a heartbeat keeps it and brings a closed one back
  useEffect(() => {
    const timer = setInterval(
      () =>
        void post(`/v1/sessions/${id}/heartbeat`)
          .then(reopened)
          .catch(() => {}),
      HEARTBEAT_MS,
    );
    return () => clearInterval(timer);
  }, [id, reopened]);

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
              if (l.role === "student") return [<StudentLine key={l.id} text={l.text} files={l.files} />];
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
                          hasTopicNotes,
                          onTopicNotesSaved: () => {
                            if (activeTopicId) setNotesState({ topicId: activeTopicId, has: true });
                          },
                        }}
                      />
                    )}
                  </article>
                ) : null,
                // the forms of a turn stay right after its text, in the order of the session, answered or not
                ...(l.seq === undefined
                  ? []
                  : elems.filter((x) => x.turnSeq === l.seq).map((el) => <ElementCard key={el.blockId} el={el} disabled={busy} onRespond={(r) => void respond(el, r)} />)),
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
          {error && (
            <p role="alert" className="text-small text-st-confused">
              {error}
            </p>
          )}
        </div>
      </div>

      <SessionCorner plan={plan} jobs={jobRows(bg.jobs, bg.finished)} topicId={detail.topic_id} onOpenDemo={setDemo} />
      {demo && <DemoPanel demo={demo} onClose={() => setDemo(null)} />}

      <div className="px-4 pb-4 md:px-7 md:pb-5">
        <Composer
          label={tx.session.composerLabel}
          placeholder={tx.session.replyPlaceholder}
          submitText={tx.session.send}
          onSend={(text, files) => void send(text, files)}
          sessionId={id}
          voice={{ onClip: sendVoice, disabled: busy }}
          duck
        />
      </div>
    </div>
  );
}

/** A task or a question of the teacher: its prompt and a box for the answer. Other element kinds show only what they carry */
function ElementCard({ el, disabled, onRespond }: { el: Elem; disabled: boolean; onRespond: (text: string) => void }) {
  const tx = useT();
  const [text, setText] = useState("");
  const e = el.element as { kind: string; prompt?: string; request?: string | null; request_id?: string | null; plot?: unknown };
  const answerable = ANSWERABLE.has(e.kind);
  /** A live card (its turn is not committed yet): the same box as the committed one, so nothing moves at commit, but it cannot be answered */
  const inactive = el.turnSeq === null;
  const viz = e.kind === "visualization" ? e : null;
  return (
    <section aria-label={tx.live.taskFromTeacher} aria-busy={inactive || undefined} className="rounded-[2px] border border-seam p-4">
      <p className="caps text-paper-dim">{e.kind === "task" ? tx.live.taskFromTeacher : e.kind.replace(/_/g, " ")}</p>
      {e.prompt && (
        <div className="mt-2 text-paper">
          <RichText text={e.prompt} />
        </div>
      )}
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

function StudentLine({ text, files }: { text: string; files?: FileRef[] }) {
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
