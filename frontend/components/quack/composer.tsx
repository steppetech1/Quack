"use client";

import { Loader2, Mic, Paperclip, PenLine, Square, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { useSoon } from "./soon";
import { useSkills } from "./use-skills";
import { useVoiceInput, type VoiceProps } from "./use-voice-input";

/** Слово с «/» перед кареткой: в начале поля или после пробела */
const SLASH = /(^|\s)\/([^\s/]*)$/;

type Props = {
  /** Подпись для скринридера: что произойдёт при отправке */
  label: string;
  onSend?: (text: string) => void;
  placeholder?: string;
  submitText?: string;
  /** Главное действие экрана — бежевая заливка. Одна такая кнопка на экран */
  primary?: boolean;
  /** На телефоне — одна строка без инструментов (пока открыт вопрос) */
  compact?: boolean;
  /** Фокус новой сессии: «тема: …» со снимаемой меткой */
  chip?: { label: string; onRemove: () => void };
  /** Голос в сессии (только режим API): запись с микрофона → WAV → `onClip`. Без пропса — кнопка-заглушка «скоро» */
  voice?: VoiceProps;
};

export function Composer({
  label,
  onSend,
  placeholder,
  submitText,
  primary = true,
  compact = false,
  chip,
  voice,
}: Props) {
  const t = useT();
  placeholder ??= t.composer.placeholder;
  submitText ??= t.composer.submit;
  const soon = useSoon();
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const router = useRouter();
  const listId = useId();
  const { skills } = useSkills();
  const [caret, setCaret] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [active, setActive] = useState(0);
  const mic = useVoiceInput(voice?.onClip);
  const micBusy = mic.phase === "starting" || mic.phase === "sending";
  const mm = (n: number) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;

  const match = SLASH.exec(value.slice(0, caret));
  const query = match?.[2].toLowerCase() ?? "";
  const found = match
    ? skills.filter((s) => s.enabled && s.command && (s.command.includes(query) || s.name.toLowerCase().includes(query)))
    : [];
  const open = !!match && !dismissed;
  const empty = !value.trim();
  const total = found.length + 1; // последняя строка — «Все скиллы →»
  const cur = Math.min(active, total - 1);

  const pick = (command: string) => {
    if (!match) return;
    const start = caret - match[2].length - 1;
    const ins = `/${command} `;
    const next = value.slice(0, start) + ins + value.slice(caret);
    setValue(next);
    setDismissed(true);
    const pos = start + ins.length;
    requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.setSelectionRange(pos, pos);
      setCaret(pos);
      grow();
    });
  };

  const choose = (i: number) => (i < found.length ? pick(found[i].command!) : router.push("/settings?tab=skills"));

  const grow = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  };

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = value.trim();
    if (!text) {
      ref.current?.focus();
      return;
    }
    if (onSend) {
      onSend(text);
      setValue("");
    } else {
      soon(t.composer.soonReply);
    }
  };

  return (
    <form
      onSubmit={submit}
      className="relative rounded-[2px] border border-seam bg-cloth-deep p-2 transition-colors duration-[var(--dur-s)] ease-out has-[textarea:focus-visible]:border-ribbon/70 has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-ribbon"
    >
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={t.composer.skills}
          className="absolute right-0 bottom-full left-0 z-30 mb-2 max-h-72 overflow-y-auto rounded-[2px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-16px_rgb(0_0_0/0.6)]"
        >
          {found.map((s, i) => (
            <li
              key={s.id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={cur === i}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s.command!);
              }}
              className={cn("cursor-pointer rounded-option px-3 py-2", cur === i && "bg-cloth-hover")}
            >
              <span className="num text-body text-paper">/{s.command}</span>
              <span className="mt-0.5 line-clamp-1 block text-small text-paper-dim">{s.name}. {s.text}</span>
            </li>
          ))}
          {found.length === 0 && <li role="presentation" className="px-3 py-2 text-small text-paper-dim">{t.composer.noCommands}</li>}
          <li
            id={`${listId}-${found.length}`}
            role="option"
            aria-selected={cur === found.length}
            onMouseEnter={() => setActive(found.length)}
            onMouseDown={(e) => {
              e.preventDefault();
              choose(found.length);
            }}
            className={cn(
              "flex min-h-10 cursor-pointer items-center rounded-option px-3 text-body text-paper-dim",
              cur === found.length && "bg-cloth-hover text-paper",
            )}
          >
            {t.composer.allSkills}
          </li>
        </ul>
      )}
      <label htmlFor="composer" className="sr-only">
        {label}
      </label>
      {chip && (
        <div className="flex px-1.5 pt-1">
          <span className="caps inline-flex h-8 items-center rounded-pill border border-seam pl-3 text-paper-dim">
            <span className="truncate">{chip.label}</span>
            <button
              type="button"
              aria-label={t.composer.removeChip(chip.label)}
              onClick={chip.onRemove}
              className="group -my-1 -mr-1 inline-flex size-10 items-center justify-center rounded-pill"
            >
              <span className="inline-flex size-6 items-center justify-center rounded-pill group-hover:bg-cloth-hover group-hover:text-paper">
                <X aria-hidden className="size-3.5" />
              </span>
            </button>
          </span>
        </div>
      )}
      <div className={cn(compact && "max-md:flex max-md:items-center max-md:gap-1")}>
      <textarea
        id="composer"
        ref={ref}
        rows={1}
        value={value}
        {...(open
          ? {
              role: "combobox",
              "aria-expanded": true,
              "aria-haspopup": "listbox",
              "aria-autocomplete": "list",
              "aria-controls": listId,
              "aria-activedescendant": `${listId}-${cur}`,
            }
          : {})}
        onChange={(e) => {
          setValue(e.target.value);
          setCaret(e.target.selectionStart);
          setDismissed(false);
          setActive(0);
          grow();
        }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onBlur={() => setDismissed(true)}
        onKeyDown={(e) => {
          if (open) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              setActive((cur + (e.key === "ArrowDown" ? 1 : total - 1)) % total);
              return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
              e.preventDefault();
              choose(cur);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setDismissed(true);
              return;
            }
          }
          if (e.key === "Enter" && !e.shiftKey) submit(e);
        }}
        placeholder={placeholder}
        className={cn(
          "block min-h-10 w-full resize-none bg-transparent px-2.5 pt-2 pb-1 text-body text-paper outline-none focus-visible:outline-none",
          compact && "max-md:min-w-0 max-md:flex-1 max-md:py-2",
        )}
      />
      {compact && (
        <button
          type="submit"
          disabled={empty}
          className="caps h-10 shrink-0 rounded-pill border border-seam px-4 text-paper transition-colors duration-[var(--dur-s)] ease-out disabled:cursor-default disabled:opacity-40 md:hidden"
        >
          {submitText}
        </button>
      )}
      </div>
      {voice && mic.supported && (mic.phase !== "idle" || mic.error) && (
        <p
          role={mic.error && mic.phase === "idle" ? "alert" : "status"}
          className={cn("flex items-center gap-2 px-2.5 pb-1 text-small", mic.error && mic.phase === "idle" ? "text-st-confused" : "text-paper-dim")}
        >
          {mic.phase === "recording" && (
            <>
              <span aria-hidden className="size-2 rounded-pill bg-st-confused motion-safe:animate-pulse" />
              <span>{t.voiceSession.recording}</span>
              <span className="num">{mm(mic.seconds)}</span>
            </>
          )}
          {mic.phase === "starting" && t.voiceSession.starting}
          {mic.phase === "sending" && t.voiceSession.sending}
          {mic.phase === "idle" && mic.error}
        </p>
      )}
      <div className={cn("flex items-center gap-1", compact && "max-md:hidden")}>
        <IconButton label={t.composer.attach} onClick={() => soon(t.composer.soonAttach)}>
          <Paperclip />
        </IconButton>
        {!voice && (
          <IconButton label={t.composer.dictate} onClick={() => soon(t.composer.soonVoice)}>
            <Mic />
          </IconButton>
        )}
        {voice && mic.supported && (
          <>
            <IconButton
              label={mic.phase === "recording" ? t.voiceSession.stop : t.voiceSession.record}
              aria-pressed={mic.phase === "recording"}
              aria-busy={micBusy}
              disabled={micBusy || (mic.phase === "idle" && voice.disabled)}
              onClick={() => void (mic.phase === "recording" ? mic.stop() : mic.start())}
              className={cn(
                "disabled:cursor-default disabled:opacity-50",
                mic.phase === "recording" && "bg-cloth-hover text-st-confused",
              )}
            >
              {mic.phase === "recording" ? <Square className="fill-current" /> : micBusy ? <Loader2 className="motion-safe:animate-spin" /> : <Mic />}
            </IconButton>
            {mic.phase === "recording" && (
              <IconButton label={t.voiceSession.cancel} onClick={mic.cancel}>
                <X />
              </IconButton>
            )}
          </>
        )}
        <IconButton label={t.composer.openCanvas} onClick={() => router.push("/canvas/new")}>
          <PenLine />
        </IconButton>
        <button
          type="submit"
          disabled={empty}
          className={cn(
            "caps ml-auto h-10 rounded-pill px-6 transition-colors duration-[var(--dur-s)] ease-out disabled:cursor-default disabled:opacity-40",
            primary
              ? "bg-ribbon text-on-ribbon enabled:hover:bg-ribbon-hover"
              : "border border-seam text-paper enabled:hover:border-rule enabled:hover:bg-cloth-hover",
          )}
        >
          {submitText}
        </button>
      </div>
    </form>
  );
}
