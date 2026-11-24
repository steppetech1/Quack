"use client";

import { Loader2, Mic, Paperclip, PenLine, Square, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import { SKILL_CREATOR } from "@/lib/skills";
import { API_MODE } from "@/lib/api/client";
import { MATERIAL_ACCEPT } from "@/lib/api/materials";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { IconButton } from "./icon-button";
import { PixelDuck } from "./pixel-duck";
import { AttachmentChips, useAttachments, type FileRef } from "./attachments";
import { useSoon } from "./soon";
import { useSkills } from "./use-skills";
import { useVoiceInput, type VoiceProps } from "./use-voice-input";

/** Слово с «/» перед кареткой: в начале поля или после пробела */
const SLASH = /(^|\s)\/([^\s/]*)$/;

type Props = {
  /** Подпись для скринридера: что произойдёт при отправке */
  label: string;
  /** `files` is set when the message carries attached files (they are read already: the box waits for that) */
  onSend?: (text: string, files?: FileRef[]) => void;
  /** The session the files belong to (their reading gets the priority of a live session) */
  sessionId?: string;
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
  /** Пиксельная утка сидит на верхней кромке поля (в сессии) */
  duck?: boolean;
  /** The chat of a session: the menu `/` always offers the built-in `/skill-creator` (it is not a learner skill) */
  creator?: boolean;
};

export function Composer({
  label,
  onSend,
  sessionId,
  placeholder,
  submitText,
  primary = true,
  compact = false,
  chip,
  voice,
  duck = false,
  creator = false,
}: Props) {
  const t = useT();
  placeholder ??= t.composer.placeholder;
  submitText ??= t.composer.submit;
  const soon = useSoon();
  const { domain } = useShell();
  // with the backend, the clip attaches a lecture, a presentation or a photo to the message (it goes into the current
  // subject and is read in the background); on mocks it stays a "soon"
  const files = useAttachments(domain.id, sessionId);
  const fileRef = useRef<HTMLInputElement>(null);
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
  // the built-in command comes first; a learner skill of the same name (an old one) never shadows it
  const builtin = creator && match && SKILL_CREATOR.includes(query) ? [{ id: `builtin-${SKILL_CREATOR}`, command: SKILL_CREATOR, label: t.composer.skillCreatorHint }] : [];
  const found = match
    ? [
        ...builtin,
        ...skills
          .filter((s) => s.enabled && s.command && s.command !== SKILL_CREATOR && (s.command.includes(query) || s.name.toLowerCase().includes(query)))
          .map((s) => ({ id: s.id, command: s.command!, label: `${s.name}. ${s.text}` })),
      ]
    : [];
  const open = !!match && !dismissed;
  const hasFiles = files.ready.length > 0;
  // a message needs words or a file that is read; while a file is being read the box waits (the teacher must not get a file it cannot see yet)
  const empty = (!value.trim() && !hasFiles) || files.busy;
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

  const choose = (i: number) => (i < found.length ? pick(found[i].command) : router.push("/settings?tab=skills"));

  const grow = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  };

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (files.busy) return;
    const text = value.trim() || (hasFiles ? t.composer.attachedDefault : "");
    if (!text) {
      ref.current?.focus();
      return;
    }
    if (onSend) {
      onSend(text, hasFiles ? files.ready.map((a) => ({ id: a.materialId, name: a.name })) : undefined);
      setValue("");
      files.clear();
      if (ref.current) ref.current.style.height = "auto";
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
                pick(s.command);
              }}
              className={cn("cursor-pointer rounded-option px-3 py-2", cur === i && "bg-cloth-hover")}
            >
              <span className="num text-body text-paper">/{s.command}</span>
              <span className="mt-0.5 line-clamp-1 block text-small text-paper-dim">{s.label}</span>
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
      <AttachmentChips items={files.items} onRemove={files.remove} />
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
        {API_MODE && (
          <input
            ref={fileRef}
            type="file"
            accept={MATERIAL_ACCEPT}
            multiple
            tabIndex={-1}
            aria-hidden
            className="hidden"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (picked.length) void files.add(picked);
            }}
          />
        )}
        <IconButton
          label={t.composer.attach}
          disabled={files.full}
          onClick={() => (API_MODE && domain.id ? fileRef.current?.click() : soon(t.composer.soonAttach))}
        >
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
      {duck && <PixelDuck mode="perch" />}
    </form>
  );
}
