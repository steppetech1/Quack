"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl, ApiError, get, idem, post, sse, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useSessionJobs } from "@/lib/api/use-session-jobs";
import { useT } from "@/lib/i18n/provider";
import { skillInvocation } from "@/lib/skills";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { Composer } from "../composer";
import { RichText } from "../rich-text";
import { PRIMARY_BTN, AREA } from "../stubs/stub-parts";
import { firstTurnKey } from "./domain-setup-live";
import { useErrorText } from "./error-text";
import { JobsPlaque } from "./jobs-plaque";

const MEASURE = "mx-auto w-full max-w-[34rem]";
const HEARTBEAT_MS = 30_000;

type Line = { id: string; role: "student" | "teacher"; text: string; degraded?: string | null; interrupted?: boolean };
/** An interactive element of a turn (task, question…): comes as an `element` frame and is answered with an `element_response` action */
type Elem = { blockId: string; element: S["Element"]; turnSeq: number | null; answered: boolean };
type Outcome = "correct" | "incorrect" | "partial" | "unclear";

/** Elements the learner answers with text; the rest of the kinds are shown as a card with their title */
const ANSWERABLE = new Set(["task", "short_answer", "free_answer", "choice", "prediction", "why_choice"]);

/** /session/new: create the session in the current subject, then open it */
export function NewSessionLive() {
  const tx = useT();
  const router = useRouter();
  const errorText = useErrorText();
  const { pending, pendingTopic, domain } = useShell();
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
            sessionStorage.setItem(firstTurnKey(s.session_id), pending);
          } catch {}
        }
        setActive(s);
        router.replace(`/session/${s.session_id}`);
      })
      .catch((e) => setError(errorText(e)));
  }, [domain.id, pending, pendingTopic, router, setActive, errorText]);

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

/** A real session: history, streaming teacher turns (`POST …/actions`), elements, end */
export function SessionLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.live;
  const router = useRouter();
  const errorText = useErrorText();
  const { domains, setActive, reload } = useData();
  const { focusDomain } = useShell();
  const [detail, setDetail] = useState<S["SessionDetail"] | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [elems, setElems] = useState<Elem[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  /** `status: thinking` arrives after the action is recorded, before the first token */
  const [thinking, setThinking] = useState(false);
  const [verdict, setVerdict] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const sentFirst = useRef(false);

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
  const bg = useSessionJobs(id, { onDone: refreshElements });
  const refreshJobs = bg.refresh;

  const scrollDown = () => requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));

  /** One action of the learner; the answer of the teacher streams back as frames (ТЗ Ф4 §4.7) */
  const act = useCallback(
    async (body: Record<string, unknown>, studentText?: string) => {
      if (busy) return;
      setBusy(true);
      setError(null);
      setVerdict(null);
      const lineId = `s-${Date.now()}`;
      if (studentText) setLines((l) => [...l, { id: lineId, role: "student", text: studentText }]);
      setStreaming("");
      scrollDown();
      let reply = "";
      let degraded: string | null = null;
      let committed: number | null = null;
      const fresh: Elem[] = [];
      try {
        await sse(
          "POST",
          `/v1/sessions/${id}/actions`,
          // the body is one of the actions of the contract (message, intent, element_response…); the shape is checked by the backend
          { ...body, idempotency_key: idem() } as never,
          ({ event, data }) => {
            if (event === "action_recorded") {
              const v = data.verdict as { outcome?: Outcome } | null | undefined;
              if (v?.outcome) setVerdict(v.outcome);
            } else if (event === "verdict") setVerdict(data.outcome as Outcome);
            else if (event === "status") setThinking(data.state === "thinking");
            else if (event === "token") {
              reply += String(data.text ?? "");
              setStreaming(reply);
            } else if (event === "reset") {
              reply = "";
              setStreaming("");
            } else if (event === "element") fresh.push({ blockId: String(data.block_id), element: data.element as S["Element"], turnSeq: null, answered: false });
            else if (event === "turn_committed") {
              committed = typeof data.seq === "number" ? data.seq : null;
              degraded = (data.degraded_mode as string | null) ?? null;
            } else if (event === "error") throw new ApiError(500, String(data.code ?? "ERROR"), String(data.message ?? ""));
          },
        );
        if (reply) setLines((l) => [...l, { id: `t-${committed ?? Date.now()}`, role: "teacher", text: reply, degraded }]);
        if (fresh.length) setElems((e) => [...e.map((x) => ({ ...x })), ...fresh.map((x) => ({ ...x, turnSeq: committed }))]);
      } catch (e) {
        setError(e instanceof ApiError && e.code === "TURN_LOCKED" ? t.turnLocked : errorText(e));
        // what was already shown stays on screen, marked: this part is not committed and is gone after a reload
        if (reply) setLines((l) => [...l, { id: `t-${Date.now()}`, role: "teacher", text: reply, interrupted: true }]);
      } finally {
        setStreaming(null);
        setThinking(false);
        setBusy(false);
        scrollDown();
        // a turn queues background agents (demo, tasks, checks) shortly after it is committed
        refreshJobs();
      }
    },
    [busy, id, t.turnLocked, errorText, refreshJobs],
  );

  const send = useCallback((text: string) => (text.trim() ? act({ kind: "message", text, skill_invocation: skillInvocation(text) }, text) : undefined), [act]);

  const respond = (el: Elem, response: string) => {
    if (!response.trim() || el.turnSeq === null) return;
    setElems((all) => all.map((x) => (x.blockId === el.blockId ? { ...x, answered: true } : x)));
    return act({ kind: "element_response", turn_seq: el.turnSeq, element_id: el.element.element_id, response }, response);
  };

  // History: the feed of the session; text lives in `message.sent` (student) and `turn.committed` (teacher)
  useEffect(() => {
    let off = false;
    get<S["SessionDetail"]>(`/v1/sessions/${id}`)
      .then((d) => {
        if (off) return;
        setDetail(d);
        focusDomain(d.domain_id);
        // a session opened from a topic page becomes the active one in the sessions panel
        if (d.status === "active") setActive(d);
        const past: Line[] = [];
        const pastElems: Elem[] = [];
        for (const ev of d.events) {
          const text = typeof ev.payload.text === "string" ? ev.payload.text : "";
          if (ev.type === "message.sent" && text) past.push({ id: `e-${ev.seq}`, role: "student", text });
          else if (ev.type === "turn.committed" && text) {
            past.push({ id: `e-${ev.seq}`, role: "teacher", text, degraded: (ev.payload.degraded_mode as string | null) ?? null });
            // committed elements carry `request_id` (resolved from the live frame's `R…` label, ТЗ §12.3): the only
            // point a `visualization` element becomes renderable, so history is where it actually shows up
            const markup = ev.payload.markup as { elements?: unknown[] } | undefined;
            for (const el of markup?.elements ?? []) {
              const e = el as { element_id?: string; kind?: string };
              if (e.kind) pastElems.push({ blockId: `e-${ev.seq}-${e.element_id}`, element: e as S["Element"], turnSeq: ev.seq, answered: false });
            }
          }
        }
        setLines(past);
        if (pastElems.length) setElems((cur) => [...pastElems, ...cur]);
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
    // load once per session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // The message typed in the input box before the session existed; or, for a fresh learn/practice/review session, its intent
  useEffect(() => {
    if (!detail || sentFirst.current || detail.status !== "active") return;
    sentFirst.current = true;
    let first: string | null = null;
    try {
      first = sessionStorage.getItem(firstTurnKey(id));
      sessionStorage.removeItem(firstTurnKey(id));
    } catch {}
    if (first) void send(first);
    // a fresh session already has `session.started` in its log: "fresh" means nobody has spoken yet
    else if (detail.intent && detail.intent !== "free" && !detail.events.some((e) => e.type === "message.sent" || e.type === "turn.committed")) {
      void act({ kind: "intent", intent: detail.intent, topic_id: detail.topic_id, hub_id: detail.hub_id });
    }
  }, [detail, id, send, act]);

  // Presence: the backend closes sessions without a heartbeat
  const live = detail?.status === "active";
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => void post(`/v1/sessions/${id}/heartbeat`).catch(() => {}), HEARTBEAT_MS);
    return () => clearInterval(timer);
  }, [live, id]);

  const finish = async () => {
    setBusy(true);
    try {
      await post(`/v1/sessions/${id}/end`);
      setActive(null);
      void reload();
      router.push(`/session/${id}/summary`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  if (missing) {
    return (
      <div className={cn("px-4 pt-6", MEASURE)}>
        <p className="text-paper-dim">{t.sessionMissing}</p>
      </div>
    );
  }
  if (!detail) {
    return error ? (
      <p role="alert" className={cn("px-4 pt-6 text-st-confused", MEASURE)}>
        {error}
      </p>
    ) : (
      <div className="flex-1" aria-busy="true" />
    );
  }

  const intent = detail.intent ?? "free";
  const modeLabel = t.modes[intent as keyof typeof t.modes] ?? intent;
  const domain = domains.find((d) => d.id === detail.domain_id);
  const topic = domain?.topics.find((x) => x.id === detail.topic_id);
  const title = topic?.title ?? modeLabel ?? tx.session.newTitle;
  const open = elems.filter((e) => !e.answered);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-8 md:px-7 md:pt-6">
        <header className={cn("flex items-start justify-between gap-3", MEASURE)}>
          <div className="min-w-0">
            <h1 className="line-clamp-2 display-caps text-display text-paper">{title}</h1>
            <p className="mt-1.5 text-paper-dim">
              {domain?.title}
              {domain && " · "}
              {modeLabel}
              {!live && ` · ${t.sessionClosed}`}
            </p>
          </div>
          {live && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void finish()}
              className="caps inline-flex h-10 shrink-0 items-center rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon disabled:opacity-50"
            >
              {tx.stubs.summary.finish}
            </button>
          )}
        </header>

        <div className={cn("mt-6 flex flex-col gap-7", MEASURE)}>
          {lines.length === 0 && streaming === null && <p className="text-paper-dim">{t.sessionEmpty}</p>}
          {lines.map((l) =>
            l.role === "student" ? (
              <StudentLine key={l.id} text={l.text} />
            ) : (
              <article key={l.id} aria-label={tx.session.teacherTurn} className="text-paper">
                <RichText text={l.text} />
                {l.degraded && <p className="mt-2 text-small text-paper-dim">{t.degraded}</p>}
                {l.interrupted && <p className="mt-2 text-small text-paper-dim">{t.interrupted}</p>}
              </article>
            ),
          )}
          {streaming !== null && (
            <article aria-label={tx.session.teacherTurn} aria-busy="true" className="text-paper">
              {streaming ? (
                <RichText text={streaming} />
              ) : (
                <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
                  {thinking ? tx.session.teacherThinking : tx.session.teacherReading}
                  <Dots />
                </p>
              )}
            </article>
          )}
          {verdict && (verdict === "correct" || verdict === "incorrect") && (
            <p role="status" className={cn("text-body", verdict === "correct" ? "text-st-solid" : "text-st-confused")}>
              {verdict === "correct" ? tx.stubs.practice.correct : tx.live.notQuite}
            </p>
          )}
          {live &&
            streaming === null &&
            open.map((el) => <ElementCard key={el.blockId} el={el} disabled={busy} onRespond={(r) => void respond(el, r)} />)}
          {error && (
            <p role="alert" className="text-small text-st-confused">
              {error}
            </p>
          )}
        </div>
      </div>

      {(bg.jobs.length > 0 || bg.finished.length > 0) && (
        <div className="px-4 pb-2 md:px-7">
          <JobsPlaque jobs={bg.jobs} finished={bg.finished} topicId={detail.topic_id} className={MEASURE} />
        </div>
      )}

      {live && (
        <div className="px-4 pb-4 md:px-7 md:pb-5">
          <Composer
            label={tx.session.composerLabel}
            placeholder={tx.session.replyPlaceholder}
            submitText={tx.session.send}
            onSend={(text) => void send(text)}
          />
        </div>
      )}
    </div>
  );
}

/** A task or a question of the teacher: its prompt and a box for the answer. Other element kinds show only what they carry */
function ElementCard({ el, disabled, onRespond }: { el: Elem; disabled: boolean; onRespond: (text: string) => void }) {
  const tx = useT();
  const [text, setText] = useState("");
  const e = el.element as { kind: string; prompt?: string; request?: string | null; request_id?: string | null; plot?: unknown };
  const answerable = ANSWERABLE.has(e.kind) && el.turnSeq !== null;
  const viz = e.kind === "visualization" ? e : null;
  return (
    <section aria-label={tx.live.taskFromTeacher} className="rounded-[2px] border border-seam p-4">
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
          <p className="mt-2 text-small text-paper-dim">{viz.plot ? tx.live.vizPlotUnsupported : tx.live.vizPreparing}</p>
        ))}
      {answerable && (
        <form
          className="mt-3"
          onSubmit={(ev) => {
            ev.preventDefault();
            onRespond(text);
            setText("");
          }}
        >
          <label className="sr-only" htmlFor={`el-${el.blockId}`}>
            {tx.live.elementAnswer}
          </label>
          <textarea id={`el-${el.blockId}`} value={text} onChange={(ev) => setText(ev.target.value)} placeholder={tx.live.answerPlaceholder} className={cn(AREA, "min-h-16")} />
          <button type="submit" disabled={disabled || !text.trim()} className={cn(PRIMARY_BTN, "mt-3")}>
            {tx.live.elementSend}
          </button>
        </form>
      )}
    </section>
  );
}

function StudentLine({ text }: { text: string }) {
  const tx = useT();
  return (
    <p className="flex gap-3 border-y border-seam-soft py-3 whitespace-pre-wrap text-paper">
      <span className="caps shrink-0 pt-1 text-paper-dim">{tx.session.you}</span>
      <span>{text}</span>
    </p>
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
