"use client";

import { ChevronUp, SendHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMocks, useT } from "@/lib/i18n/provider";
import type { ChatMsg, ChatPart } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { Duck } from "../duck";
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
            className="inline rounded-md text-paper underline decoration-paper-dim/60 decoration-1 underline-offset-4 transition-colors hover:decoration-paper focus-visible:outline-2 focus-visible:outline-ribbon"
          >
            {p.label}
          </button>
        ),
      )}
    </>
  );
}

/** Разговор с учителем про канвас: свёрнутая строка внизу и панель с историей */
export function BoardChat({ canvasId, onJump }: { canvasId: string; onJump: (ref: string) => void }) {
  const tx = useT();
  const { canvasChat, canvasReplies, genericReply } = useMocks();
  const soon = useSoon();
  const known = canvasId in canvasChat;
  const [msgs, setMsgs] = useState<ChatMsg[]>(canvasChat[canvasId] ?? []);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const thread = useRef<HTMLDivElement>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const replyN = useRef(0);

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
    if (!v) return;
    const id = `u${Date.now()}`;
    setMsgs((m) => [...m, { id, role: "student", parts: [v.replace(/\$/g, "")] }]);
    setText("");
    setOpen(true);
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
      className="z-20 w-full overflow-hidden rounded-[24px] border border-seam bg-cloth-raised shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)] md:absolute md:bottom-3 md:left-1/2 md:w-[min(560px,calc(100%-9rem))] md:-translate-x-1/2"
    >
      {open ? (
        <>
          <div className="flex items-center gap-2 border-b border-seam-soft px-4 py-2 text-small text-paper-dim">
            <Duck className="size-5" />
            {tx.board.chat.duck}
          </div>
          <div ref={thread} role="log" aria-label={tx.board.chat.log} className="flex max-h-[45vh] flex-col gap-2.5 overflow-y-auto px-4 py-3">
            {msgs.length === 0 && <p className="text-small text-paper-dim">{tx.board.chat.quiet}</p>}
            {msgs.map((m) => (
              <p
                key={m.id}
                className={cn(
                  "max-w-[88%] rounded-[14px] px-3 py-2 text-body",
                  m.role === "teacher" ? "self-start bg-cloth text-paper" : "self-end border border-seam text-paper",
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
            className="block h-8 w-full truncate px-5 pt-2 text-left text-small text-paper-dim hover:text-paper"
          >
            <span className="sr-only">{tx.board.chat.lastTeacher}</span>
            {plain(lastTeacher.parts)}
          </button>
        )
      )}
      <form onSubmit={send} className="flex items-center gap-1.5 p-2 pl-2.5">
        <button
          type="button"
          aria-label={open ? tx.board.chat.collapse : tx.board.chat.expand}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex size-10 shrink-0 items-center justify-center rounded-full text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper"
        >
          <ChevronUp aria-hidden className={cn("size-5 transition-transform", open && "rotate-180")} />
        </button>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label={tx.board.chat.message}
          placeholder={tx.board.chat.placeholder}
          className="h-10 min-w-0 flex-1 rounded-full bg-cloth px-4 text-body text-paper outline-none focus-visible:outline-2 focus-visible:outline-ribbon"
        />
        <button
          type="submit"
          aria-label={tx.board.chat.send}
          disabled={!text.trim()}
          className="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-ribbon text-on-ribbon transition-colors enabled:hover:bg-ribbon-hover disabled:cursor-default disabled:bg-cloth-hover disabled:text-paper-dim"
        >
          <SendHorizontal aria-hidden className="size-[18px]" />
        </button>
      </form>
    </section>
  );
}
