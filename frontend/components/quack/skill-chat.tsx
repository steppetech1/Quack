"use client";

import { ArrowUp, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useLocale, useT } from "@/lib/i18n/provider";
import {
  buildDraft,
  detectBindings,
  detectCallable,
  draftCommand,
  hasContext,
  refineDraft,
  type SkillDraft,
} from "@/lib/mock/skill-draft";
import type { Skill } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { PILL } from "./page-parts";

type Msg =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "bot"; text: string; pills?: string[]; draft?: SkillDraft };

const SUGGEST = "rounded-full border border-seam px-3.5 py-2 text-left text-body text-paper transition-colors hover:border-rule hover:bg-cloth-hover disabled:opacity-40";

const toSkill = (d: SkillDraft): Skill => ({
  id: `sk-${Date.now().toString(36)}`,
  name: d.name,
  text: d.text,
  bindings: d.bindings,
  command: draftCommand(d),
  enabled: true,
});

export function SkillChat({
  onSave,
  onManual,
  onClose,
}: {
  onSave: (s: Skill) => void;
  onManual: (s: Skill | null) => void;
  onClose: () => void;
}) {
  const tx = useT();
  const { locale } = useLocale();
  const GREETING = useMemo<Msg>(() => ({ id: 0, role: "bot", text: tx.skillChat.greeting, pills: tx.skillChat.greetingPills }), [tx]);
  const ASK: Msg = { id: 0, role: "bot", text: tx.skillChat.ask, pills: tx.skillChat.askPills };
  const uid = useId();
  const [msgs, setMsgs] = useState<Msg[]>([GREETING]);
  const [typing, setTyping] = useState(false);
  const [value, setValue] = useState("");
  const pending = useRef<string | null>(null); // первое сообщение, пока ждём ответ «где применять»
  const nextId = useRef(1);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    input.current?.focus();
    root.current?.scrollIntoView({ block: "nearest" });
    return () => clearTimeout(timer.current);
  }, []);

  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, typing]);

  const lastDraft = [...msgs].reverse().find((m) => m.role === "bot" && m.draft) as Msg | undefined;
  const lastId = msgs[msgs.length - 1].id;

  const reply = (text: string, prev: Msg[]): Msg => {
    const id = nextId.current++;
    const draftMsg = (draft: SkillDraft, line: string): Msg => ({ id, role: "bot", text: line, draft });
    const prevBot = [...prev].reverse().find((m) => m.role === "bot" && m.draft);
    const prevDraft = prevBot && prevBot.role === "bot" ? prevBot.draft : undefined;

    if (prevDraft) return draftMsg(refineDraft(prevDraft, text, locale), tx.skillChat.revised);

    if (pending.current !== null) {
      const first = pending.current;
      pending.current = null;
      const ans = detectBindings(text, locale).length || detectCallable(text, locale) ? text : "";
      return draftMsg(buildDraft(first, locale, ans), tx.skillChat.draft);
    }

    if (!hasContext(text, locale)) {
      pending.current = text;
      return { ...ASK, id };
    }
    return draftMsg(buildDraft(text, locale), tx.skillChat.draft);
  };

  const send = (raw: string) => {
    const text = raw.trim();
    if (!text || typing) return;
    const userMsg: Msg = { id: nextId.current++, role: "user", text };
    const next = [...msgs, userMsg];
    setMsgs(next);
    setValue("");
    setTyping(true);
    if (input.current) input.current.style.height = "auto";
    timer.current = setTimeout(() => {
      const r = reply(text, next);
      setMsgs((cur) => [...cur, r]);
      setTyping(false);
    }, 700);
  };

  const grow = () => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  };

  return (
    <section
      ref={root}
      aria-label={tx.skillChat.title}
      className="mt-5 rounded-[16px] border border-seam bg-cloth-raised p-4 md:p-5"
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-semibold text-paper">{tx.skillChat.title}</h3>
        <div className="-mr-2 flex items-center gap-1">
          <button
            type="button"
            onClick={() => onManual(lastDraft && lastDraft.role === "bot" && lastDraft.draft ? toSkill(lastDraft.draft) : null)}
            className="min-h-10 rounded-control px-3 text-small text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper"
          >
            {tx.skills.writeSelf}
          </button>
          <IconButton label={tx.shell.close} onClick={onClose}>
            <X />
          </IconButton>
        </div>
      </div>

      <div
        ref={log}
        role="log"
        aria-live="polite"
        aria-label={tx.skillChat.log}
        className="mt-2 max-h-[26rem] space-y-4 overflow-y-auto pr-1"
      >
        {msgs.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <p className="max-w-[85%] rounded-[14px] bg-cloth-hover px-3.5 py-2 text-body break-words text-paper">{m.text}</p>
            </div>
          ) : (
            <div key={m.id} className="max-w-[92%] space-y-3">
              <p className="text-body text-paper">{m.text}</p>
              {m.draft && (
                <DraftCard
                  draft={m.draft}
                  active={m === lastDraft}
                  onSave={() => onSave(toSkill(m.draft!))}
                  onManual={() => onManual(toSkill(m.draft!))}
                />
              )}
              {m.pills && m.id === lastId && !typing && (
                <div className="flex flex-wrap gap-2">
                  {m.pills.map((p) => (
                    <button key={p} type="button" className={SUGGEST} onClick={() => send(p)}>
                      {p}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
        {typing && (
          <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
            {tx.skillChat.typing}
            <span aria-hidden className="inline-flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="size-1 animate-pulse rounded-full bg-paper-dim"
                  style={{ animationDelay: `${i * 180}ms` }}
                />
              ))}
            </span>
          </p>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(value);
        }}
        className="mt-4 flex items-end gap-2 rounded-[14px] border border-seam bg-cloth p-1.5 has-[textarea:focus-visible]:border-rule"
      >
        <label htmlFor={`${uid}-in`} className="sr-only">
          {tx.skillChat.inputLabel}
        </label>
        <textarea
          id={`${uid}-in`}
          ref={input}
          rows={1}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            grow();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send(value);
            }
          }}
          placeholder={tx.skillChat.placeholder}
          className="block min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2.5 py-2 text-body text-paper outline-none focus-visible:outline-none"
        />
        <IconButton
          label={tx.skillChat.send}
          type="submit"
          disabled={!value.trim() || typing}
          className="border border-seam disabled:cursor-default disabled:opacity-40"
        >
          <ArrowUp />
        </IconButton>
      </form>
    </section>
  );
}

function DraftCard({
  draft,
  active,
  onSave,
  onManual,
}: {
  draft: SkillDraft;
  active: boolean;
  onSave: () => void;
  onManual: () => void;
}) {
  const tx = useT();
  const cmd = draftCommand(draft);
  return (
    <>
      <div className={cn("rounded-[14px] border border-seam bg-cloth p-3.5", !active && "opacity-60")}>
        <p className="font-semibold break-words text-paper">{draft.name}</p>
        <p className="mt-1 text-body break-words text-paper-dim">{draft.text}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {draft.bindings.map((b) => (
            <span key={b} className={PILL}>
              {tx.skills.bound(tx.skills.binding[b])}
            </span>
          ))}
          {cmd && <code className="num text-small break-all text-paper-dim">/{cmd}</code>}
          {cmd && <span className={PILL}>{tx.skills.callInChat}</span>}
        </div>
      </div>
      {active && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onSave}
            className="h-10 rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover"
          >
            {tx.skillChat.saveSkill}
          </button>
          <button
            type="button"
            onClick={onManual}
            className="h-10 rounded-control border border-seam px-4 text-body text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
          >
            {tx.skillChat.manual}
          </button>
        </div>
      )}
    </>
  );
}
