"use client";

import { ChevronUp, SendHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { askTeacher } from "@/lib/api/canvases";
import { ApiError, get, type S } from "@/lib/api/client";
import { useMocks, useT } from "@/lib/i18n/provider";
import { skillInvocation } from "@/lib/skills";
import type { ChatMsg, ChatPart } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { Duck } from "../duck";
import { useErrorText } from "../live/error-text";
import { Inline } from "../rich-text";
import { useSoon } from "../soon";

const plain = (parts: ChatPart[]) => parts.map((p) => (typeof p === "string" ? p : p.label)).join("").replace(/\$/g, "");

function Parts({ parts, onJump }: { parts: ChatPart[]; onJump: (ref: string) => void }) {
  return (
    <>
      {parts.map((p, i) =>
        typeof p === "string" ? (
          <Inline key={i} text={p} />
        ) : (
          <button
            key={i}
            type="button"
            onClick={() => onJump(p.ref)}
            className="inline rounded-[2px] text-paper underline decoration-paper-dim/60 decoration-1 underline-offset-4 transition-colors hover:decoration-paper focus-visible:outline-2 focus-visible:outline-ribbon"
          >
            {p.label}
          </button>
        ),
      )}
    </>
  );
}

/** Разговор с учителем про канвас: свёрнутая строка внизу и панель с историей */
export function BoardChat({
  canvasId,
  sessionId,
  beforeAsk,
  onJump,
}: {
  canvasId: string;
  sessionId?: string;
  /** resolves when the drawing so far is on the server and read: the teacher sees only what is in the session log */
  beforeAsk?: () => Promise<void>;
  onJump: (ref: string) => void;
}) {
  const tx = useT();
  const errorText = useErrorText();
  const { canvasChat, canvasReplies, genericReply } = useMocks();
  const soon = useSoon();
  const live = !!sessionId; // a canvas of the backend: the answer is a real turn of the session
  const known = canvasId in canvasChat;
  const [msgs, setMsgs] = useState<ChatMsg[]>(canvasChat[canvasId] ?? []);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const thread = useRef<HTMLDivElement>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const replyN = useRef(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  // the conversation so far: the feed of the session (text is in `message.sent` and `turn.committed`)
  useEffect(() => {
    if (!sessionId) return;
    let off = false;
    get<S["SessionDetail"]>(`/v1/sessions/${sessionId}`)
      .then((d) => {
        if (off) return;
        const past: ChatMsg[] = [];
        for (const ev of d.events) {
          const text = typeof ev.payload.text === "string" ? ev.payload.text : "";
          if (ev.type === "message.sent" && text) past.push({ id: `e-${ev.seq}`, role: "student", parts: [text.replace(/\$/g, "")] });
          else if (ev.type === "turn.committed" && text) past.push({ id: `e-${ev.seq}`, role: "teacher", parts: [text] });
        }
        // what was typed while this loaded stays after the history
        setMsgs((m) => [...past, ...m]);
      })
      .catch(() => {});
    return () => {
      off = true;
    };
  }, [sessionId]);
  useEffect(() => () => abort.current?.abort(), []);

  const ask = async (v: string) => {
    if (!sessionId) return;
    const ctl = new AbortController();
    abort.current = ctl;
    const tid = `t${Date.now()}`;
    let reply = "";
    const show = () =>
      setMsgs((m) => {
        const rest = m.filter((x) => x.id !== tid);
        return reply ? [...rest, { id: tid, role: "teacher", parts: [reply] }] : rest;
      });
    setBusy(true);
    setError(null);
    try {
      await beforeAsk?.();
      if (ctl.signal.aborted) return;
      await askTeacher(
        sessionId,
        canvasId,
        v,
        ({ event, data }) => {
          if (event === "token") {
            reply += String(data.text ?? "");
            show();
          } else if (event === "reset") {
            reply = "";
            show();
          } else if (event === "error") throw new ApiError(500, String(data.code ?? "ERROR"), String(data.message ?? ""));
        },
        ctl.signal,
        skillInvocation(v),
      );
    } catch (e) {
      if (!ctl.signal.aborted) setError(e instanceof ApiError && e.code === "TURN_LOCKED" ? tx.live.turnLocked : errorText(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const ts = timers.current;
    return () => ts.forEach(clearTimeout);
  }, []);

  // Esc сворачивает чат, доска остаётся рабочей
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    const el = thread.current;
    if (open && el) el.scrollTop = el.scrollHeight;
  }, [open, msgs]);

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    const v = text.trim();
    if (!v || busy) return;
    const id = `u${Date.now()}`;
    setMsgs((m) => [...m, { id, role: "student", parts: [v.replace(/\$/g, "")] }]);
    setText("");
    setOpen(true);
    if (live) {
      void ask(v);
      return;
    }
    if (!known) {
      soon(tx.board.chat.newSoon);
      return;
    }
    timers.current.push(
      setTimeout(() => {
        const pool = canvasReplies[canvasId];
        const parts = pool ? pool[replyN.current++ % pool.length] : genericReply;
        setMsgs((m) => [...m, { id: `t${Date.now()}`, role: "teacher", parts }]);
      }, 600),
    );
  };

  const lastTeacher = [...msgs].reverse().find((m) => m.role === "teacher");

  return (
    <section
      aria-label={tx.board.chat.title}
      data-ui
      data-chat
      className="z-20 w-full overflow-hidden rounded-[2px] border border-seam bg-cloth-raised shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)] md:absolute md:bottom-3 md:left-1/2 md:w-[min(560px,calc(100%-9rem))] md:-translate-x-1/2"
    >
      {open ? (
        <>
          <div className="flex items-center gap-2 caps border-b border-seam-soft px-4 py-2 text-paper-dim">
            <Duck className="size-5" />
            {tx.board.chat.duck}
          </div>
          <div ref={thread} role="log" aria-label={tx.board.chat.log} className="flex max-h-[45vh] flex-col gap-2.5 overflow-y-auto px-4 py-3">
            {msgs.length === 0 && <p className="text-small text-paper-dim">{tx.board.chat.quiet}</p>}
            {msgs.map((m) => (
              <p
                key={m.id}
                className={cn(
                  "max-w-[88%] rounded-[2px] border px-3 py-2 text-body",
                  m.role === "teacher" ? "self-start border-transparent border-l-seam bg-cloth text-paper" : "self-end border-seam text-paper",
                )}
              >
                <span className="sr-only">{m.role === "teacher" ? tx.board.chat.teacher : tx.board.chat.you}</span>
                <Parts parts={m.parts} onJump={onJump} />
              </p>
            ))}
          </div>
        </>
      ) : (
        lastTeacher && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="block h-8 w-full truncate px-5 pt-2 text-left text-small text-paper-dim transition-colors duration-(--dur-s) ease-out hover:text-paper"
          >
            <span className="sr-only">{tx.board.chat.lastTeacher}</span>
            {plain(lastTeacher.parts)}
          </button>
        )
      )}
      {error && (
        <p role="alert" className="px-4 pt-2 text-small text-st-confused">
          {error}
        </p>
      )}
      <form onSubmit={send} className="flex items-center gap-1.5 p-2 pl-2.5">
        <button
          type="button"
          aria-label={open ? tx.board.chat.collapse : tx.board.chat.expand}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex size-10 shrink-0 items-center justify-center rounded-pill text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper"
        >
          <ChevronUp aria-hidden className={cn("size-5 transition-transform duration-(--dur-s) ease-out", open && "rotate-180")} />
        </button>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label={tx.board.chat.message}
          placeholder={tx.board.chat.placeholder}
          className="h-10 min-w-0 flex-1 rounded-control border border-seam-soft bg-cloth px-4 text-body text-paper outline-none focus-visible:outline-2 focus-visible:outline-ribbon"
        />
        <button
          type="submit"
          aria-label={tx.board.chat.send}
          disabled={!text.trim() || busy}
          className="inline-flex size-10 shrink-0 items-center justify-center rounded-pill bg-ribbon text-on-ribbon transition-colors duration-(--dur-s) ease-out enabled:hover:bg-ribbon-hover disabled:cursor-default disabled:bg-cloth-hover disabled:text-paper-dim"
        >
          <SendHorizontal aria-hidden className="size-[18px]" />
        </button>
      </form>
    </section>
  );
}
