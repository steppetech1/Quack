"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, idem, post, sse, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { saveSummary, startSession } from "@/lib/api/sessions";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { Composer } from "../composer";
import { RichText } from "../rich-text";
import { useErrorText } from "./domain-setup-live";

const MEASURE = "mx-auto w-full max-w-[34rem]";
const HEARTBEAT_MS = 30_000;

type Line = { id: string; role: "student" | "teacher"; text: string; degraded?: string | null };
type Task = { task_id: string; kind: string; prompt_text: string };
type Verdict = { task_id: string; is_correct: boolean; score: number; feedback: string };

/** The first message of a session started from an input box waits here while the page switches to the real id */
const firstTurnKey = (id: string) => `quack.firstTurn.${id}`;

/** /session/new: create the session in the current subject, then open it */
export function NewSessionLive() {
  const tx = useT();
  const router = useRouter();
  const errorText = useErrorText();
  const { pending, domain } = useShell();
  const { setActive } = useData();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !domain.id) return;
    started.current = true;
    startSession({ domain_id: domain.id, goal: pending })
      .then((s) => {
        if (pending) {
          try {
            sessionStorage.setItem(firstTurnKey(s.session_id), pending);
          } catch {}
        }
        setActive({
          session_id: s.session_id,
          domain_id: s.domain_id,
          topic_id: s.topic_id,
          mode: s.mode,
          status: s.status,
          started_at: new Date().toISOString(),
          last_activity_at: new Date().toISOString(),
          last_turn_index: -1,
          last_checkpoint_turn: -1,
        });
        router.replace(`/session/${s.session_id}`);
      })
      .catch((e) => setError(errorText(e)));
  }, [domain.id, pending, router, setActive, errorText]);

  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 pt-4 md:px-7 md:pt-6">
      <header className={MEASURE}>
        <h1 className="font-serif text-display font-bold tracking-[-0.01em] text-paper">{tx.session.newTitle}</h1>
        <p role={error ? "alert" : "status"} className={cn("mt-1.5", error ? "text-st-confused" : "text-paper-dim")}>
          {error ?? tx.session.newThinking}
        </p>
      </header>
    </div>
  );
}

/** A real session: history, streaming teacher turns, tasks, finish */
export function SessionLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.live;
  const router = useRouter();
  const errorText = useErrorText();
  const { domains, setActive, reload } = useData();
  const { focusDomain } = useShell();
  const [detail, setDetail] = useState<S["SessionDetail"] | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const sentFirst = useRef(false);

  const scrollDown = () => requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || busy) return;
      setBusy(true);
      setError(null);
      setVerdict(null);
      const studentId = `s-${Date.now()}`;
      setLines((l) => [...l, { id: studentId, role: "student", text }]);
      setStreaming("");
      scrollDown();
      let reply = "";
      let degraded: string | null = null;
      let turnId = studentId;
      try {
        await sse(
          "POST",
          `/v1/sessions/${id}/turns`,
          { text, related_task_id: task?.task_id ?? null, idempotency_key: idem() },
          ({ event, data }) => {
            if (event === "turn_started") turnId = String(data.turn_id);
            else if (event === "token") {
              reply += String(data.delta ?? "");
              setStreaming(reply);
            } else if (event === "task_evaluated") {
              setVerdict(data as unknown as Verdict);
              setTask(null);
            } else if (event === "task_assigned") setTask(data as unknown as Task);
            else if (event === "turn_completed") degraded = (data.degraded_mode as string | null) ?? null;
            else if (event === "error") throw new ApiError(500, String(data.code ?? "ERROR"), String(data.message ?? ""));
          },
        );
        setLines((l) => [...l, { id: `t-${turnId}`, role: "teacher", text: reply, degraded }]);
      } catch (e) {
        setError(e instanceof ApiError && e.code === "TURN_LOCKED" ? t.turnLocked : errorText(e));
        if (reply) setLines((l) => [...l, { id: `t-${turnId}`, role: "teacher", text: reply }]);
      } finally {
        setStreaming(null);
        setBusy(false);
        scrollDown();
      }
    },
    [busy, id, task, t.turnLocked, errorText],
  );

  // History
  useEffect(() => {
    let off = false;
    get<S["SessionDetail"]>(`/v1/sessions/${id}`)
      .then((d) => {
        if (off) return;
        setDetail(d);
        focusDomain(d.domain_id);
        // a session opened from a topic page becomes the active one in the sessions panel
        if (d.status === "active") setActive(d);
        setLines(d.turns.map((x) => ({ id: x.turn_id, role: x.role, text: x.text, degraded: x.degraded_mode })));
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

  // The message typed in the input box before the session existed
  useEffect(() => {
    if (!detail || sentFirst.current) return;
    sentFirst.current = true;
    let first: string | null = null;
    try {
      first = sessionStorage.getItem(firstTurnKey(id));
      sessionStorage.removeItem(firstTurnKey(id));
    } catch {}
    if (first && detail.status === "active") void send(first);
  }, [detail, id, send]);

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
      const res = await post<S["FinishResponse"]>(`/v1/sessions/${id}/finish`, { idempotency_key: idem() });
      saveSummary(id, res);
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

  const domain = domains.find((d) => d.id === detail.domain_id);
  const topic = domain?.topics.find((x) => x.id === detail.topic_id);
  const title = topic?.title ?? t.modes[detail.mode as keyof typeof t.modes] ?? tx.session.newTitle;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-8 md:px-7 md:pt-6">
        <header className={cn("flex items-start justify-between gap-3", MEASURE)}>
          <div className="min-w-0">
            <h1 className="line-clamp-2 font-serif text-display font-bold tracking-[-0.01em] text-paper">{title}</h1>
            <p className="mt-1.5 text-paper-dim">
              {domain?.title}
              {domain && " · "}
              {t.modes[detail.mode as keyof typeof t.modes] ?? detail.mode}
              {!live && ` · ${t.sessionClosed}`}
            </p>
          </div>
          {live && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void finish()}
              className="inline-flex h-10 shrink-0 items-center rounded-control border border-seam px-4 text-body font-semibold text-paper transition-colors hover:border-rule hover:bg-cloth-hover disabled:opacity-50"
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
              </article>
            ),
          )}
          {streaming !== null && (
            <article aria-label={tx.session.teacherTurn} aria-busy="true" className="text-paper">
              {streaming ? (
                <RichText text={streaming} />
              ) : (
                <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
                  {tx.session.teacherReading}
                  <Dots />
                </p>
              )}
            </article>
          )}
          {verdict && (
            <p role="status" className={cn("text-body", verdict.is_correct ? "text-st-solid" : "text-st-confused")}>
              {verdict.feedback || (verdict.is_correct ? tx.stubs.practice.correct : tx.live.notQuite)}
            </p>
          )}
          {task && (
            <section aria-label={t.taskFromTeacher} className="rounded-[16px] border border-seam p-4">
              <p className="num text-small text-paper-dim">{t.taskFromTeacher}</p>
              <div className="mt-2 text-paper">
                <RichText text={task.prompt_text} />
              </div>
              <p className="mt-2 text-small text-paper-dim">{t.answerBelow}</p>
            </section>
          )}
          {error && (
            <p role="alert" className="text-small text-st-confused">
              {error}
            </p>
          )}
        </div>
      </div>

      {live && (
        <div className="px-4 pb-4 md:px-7 md:pb-5">
          <Composer
            label={tx.session.composerLabel}
            placeholder={task ? t.answerPlaceholder : tx.session.replyPlaceholder}
            submitText={tx.session.send}
            onSend={(text) => void send(text)}
          />
        </div>
      )}
    </div>
  );
}

function StudentLine({ text }: { text: string }) {
  const tx = useT();
  return (
    <p className="flex gap-3 border-y border-seam-soft py-3 whitespace-pre-wrap text-paper">
      <span className="num shrink-0 pt-0.5 text-small text-paper-dim">{tx.session.you}</span>
      <span>{text}</span>
    </p>
  );
}

function Dots() {
  return (
    <span aria-hidden className="inline-flex gap-0.5">
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1 animate-pulse rounded-full bg-paper-dim" style={{ animationDelay: `${i * 180}ms` }} />
      ))}
    </span>
  );
}
