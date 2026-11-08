"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { getSystemTemplates, type SystemTemplate } from "@/lib/api/skills";
import { useT } from "@/lib/i18n/provider";
import { SKILL_NAME_MAX, SKILL_TEXT_MAX, validSkillName } from "@/lib/skills";
import type { Skill, SkillBinding } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { PILL } from "./page-parts";

const FIELD =
  "block w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper transition-colors duration-(--dur-s) ease-out focus-visible:border-ribbon";
const TYPES: SkillBinding[] = ["notes", "cards", "cheat"];

/**
 * The helper "Create a skill" on the backend: a short guided form that collects the three things a skill is
 * (the name = the `/command`, the artifact type, the instruction), checks them against the backend's contract
 * and shows what the backend answers. The system templates are offered as a starting point for the text.
 */
export function SkillGuide({
  existing,
  onSave,
  onManual,
  onClose,
}: {
  existing: Skill[];
  /** Resolves with the backend's error text, or `null` when the skill is saved */
  onSave: (s: Skill) => Promise<string | null>;
  onManual: () => void;
  onClose: () => void;
}) {
  const tx = useT();
  const g = tx.skillGuide;
  const uid = useId();
  const [name, setName] = useState("");
  const [type, setType] = useState<SkillBinding | null>(null);
  const [text, setText] = useState("");
  const [errors, setErrors] = useState<{ name?: string; text?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const root = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    nameRef.current?.focus();
    root.current?.scrollIntoView({ block: "nearest" });
  }, []);

  // the backend allows one skill per artifact type
  const takenBy = (b: SkillBinding) => existing.find((x) => x.bindings.includes(b));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const clean = validSkillName(name);
    const body = text.trim();
    const errs: typeof errors = {};
    if (!clean) errs.name = g.nameError;
    else if (existing.some((x) => x.name === clean)) errs.name = g.nameTaken(clean);
    if (!body) errs.text = g.textError;
    else if (body.length > SKILL_TEXT_MAX) errs.text = g.textTooLong(SKILL_TEXT_MAX);
    setErrors(errs);
    if (errs.name) return nameRef.current?.focus();
    if (errs.text) return textRef.current?.focus();
    setBusy(true);
    const err = await onSave({
      id: `sk-${Date.now().toString(36)}`,
      name: clean!,
      text: body,
      bindings: type ? [type] : [],
      command: clean!,
      enabled: true,
    });
    setBusy(false);
    // the backend explains conflicts (name or type taken) and bad input itself: keep the form open with its words
    if (err) setErrors({ form: err });
  };

  const err = "mt-1.5 text-small text-st-confused";

  return (
    <form
      ref={root}
      onSubmit={submit}
      noValidate
      aria-label={g.title}
      className="mt-5 rounded-[2px] border border-seam bg-cloth-raised p-4 md:p-5"
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="display-caps text-paper">{g.title}</h3>
        <div className="-mr-2 flex items-center gap-1">
          <button
            type="button"
            onClick={onManual}
            className="caps min-h-10 rounded-pill px-3 text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper"
          >
            {tx.skills.writeSelf}
          </button>
          <IconButton label={tx.shell.close} onClick={onClose} className="rounded-pill">
            <X />
          </IconButton>
        </div>
      </div>
      <p className="mt-1 max-w-[62ch] text-paper-dim">{g.intro}</p>

      <div className="mt-4">
        <label htmlFor={`${uid}-name`} className="caps mb-1.5 block text-paper-dim">
          {g.nameLabel}
        </label>
        <div className="flex items-center gap-2">
          <span aria-hidden className="num text-paper-dim">
            /
          </span>
          <input
            id={`${uid}-name`}
            ref={nameRef}
            value={name}
            maxLength={SKILL_NAME_MAX + 1}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={!!errors.name}
            aria-describedby={`${uid}-name-hint${errors.name ? ` ${uid}-name-err` : ""}`}
            placeholder={g.namePlaceholder}
            autoCapitalize="none"
            spellCheck={false}
            className={cn(FIELD, "num h-11", errors.name && "border-st-confused")}
          />
        </div>
        <p id={`${uid}-name-hint`} className="mt-1.5 text-small text-paper-dim">
          {g.nameHint(SKILL_NAME_MAX)}
        </p>
        {errors.name && (
          <p id={`${uid}-name-err`} className={err}>
            {errors.name}
          </p>
        )}
      </div>

      <fieldset className="mt-4">
        <legend className="caps mb-1.5 text-paper-dim">{g.typeLabel}</legend>
        <div className="flex flex-wrap gap-2">
          {TYPES.map((b) => {
            const owner = takenBy(b);
            const on = type === b;
            return (
              <button
                key={b}
                type="button"
                aria-pressed={on}
                disabled={!!owner}
                onClick={() => setType(on ? null : b)}
                className={cn(
                  "caps h-10 rounded-pill border px-5 transition-colors duration-(--dur-s) ease-out disabled:cursor-default disabled:opacity-40",
                  on ? "border-ribbon bg-ribbon-soft text-paper" : "border-seam text-paper-dim enabled:hover:border-ribbon enabled:hover:text-paper",
                )}
              >
                {tx.skills.binding[b]}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-small text-paper-dim">
          {type ? g.typeOne(tx.skills.binding[type]) : g.typeNone}
          {TYPES.some(takenBy) && ` ${g.typeTaken(TYPES.filter(takenBy).map((b) => `${tx.skills.binding[b]} → /${takenBy(b)!.name}`).join(", "))}`}
        </p>
      </fieldset>

      <div className="mt-4">
        <div className="mb-1.5 flex items-baseline justify-between gap-3">
          <label htmlFor={`${uid}-text`} className="caps text-paper-dim">
            {g.textLabel}
          </label>
          <span className={cn("num text-small", text.length > SKILL_TEXT_MAX ? "text-st-confused" : "text-paper-dim")}>
            {text.length}/{SKILL_TEXT_MAX}
          </span>
        </div>
        <textarea
          id={`${uid}-text`}
          ref={textRef}
          rows={5}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={!!errors.text}
          aria-describedby={errors.text ? `${uid}-text-err` : undefined}
          placeholder={g.textPlaceholder}
          className={cn(FIELD, "resize-y py-2.5", errors.text && "border-st-confused")}
        />
        {errors.text && (
          <p id={`${uid}-text-err`} className={err}>
            {errors.text}
          </p>
        )}
      </div>

      <Templates onUse={(t) => setText(t)} />

      {errors.form && (
        <p role="alert" className={cn(err, "mt-4")}>
          {errors.form}
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={busy}
          className="caps h-10 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover disabled:cursor-default disabled:opacity-60"
        >
          {busy ? g.creating : g.create}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="caps h-10 rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon"
        >
          {tx.skills.cancel}
        </button>
      </div>
    </form>
  );
}

/** `GET /v1/system-templates`: the prompts the system itself works by. Read only; a short one can be copied as a start */
function Templates({ onUse }: { onUse: (text: string) => void }) {
  const tx = useT();
  const g = tx.skillGuide;
  const [list, setList] = useState<SystemTemplate[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<string | null>(null);

  // loaded when the section is opened, not with the form
  useEffect(() => {
    if (!open || list || failed) return;
    let off = false;
    getSystemTemplates()
      .then((t) => !off && setList(t))
      .catch(() => !off && setFailed(true));
    return () => {
      off = true;
    };
  }, [open, list, failed]);

  const roleName = (role: string) => (g.roles as Record<string, string>)[role] ?? role;

  return (
    <div className="mt-4 border-t border-seam-soft pt-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="caps min-h-10 rounded-pill px-3 text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper -ml-3"
      >
        {open ? g.templatesHide : g.templatesShow}
      </button>
      {open && (
        <div className="mt-1">
          <p className="max-w-[62ch] text-small text-paper-dim">{g.templatesHint(SKILL_TEXT_MAX)}</p>
          {!list && !failed && (
            <p role="status" className="caps mt-2 text-paper-dim">
              {g.templatesLoading}
            </p>
          )}
          {failed && (
            <p role="alert" className="mt-2 text-small text-st-confused">
              {g.templatesError}
            </p>
          )}
          {list && (
            <ul className="mt-2 divide-y divide-seam-soft border-y border-seam-soft">
              {list.map((t) => {
                const fits = t.text.length <= SKILL_TEXT_MAX;
                const id = `${t.role}@${t.version}`;
                return (
                  <li key={id} className="py-2.5">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="display-caps text-body text-paper">{roleName(t.role)}</span>
                      <span className={PILL}>v{t.version}</span>
                      <span className="num text-small text-paper-dim">{g.chars(t.text.length)}</span>
                      <div className="ml-auto flex flex-wrap gap-1">
                        <button
                          type="button"
                          aria-expanded={shown === id}
                          onClick={() => setShown(shown === id ? null : id)}
                          className="caps min-h-10 rounded-pill px-3 text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper"
                        >
                          {shown === id ? g.templateClose : g.templateRead}
                        </button>
                        {fits && (
                          <button
                            type="button"
                            onClick={() => onUse(t.text)}
                            className="caps min-h-10 rounded-pill border border-seam px-4 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon"
                          >
                            {g.templateUse}
                          </button>
                        )}
                      </div>
                    </div>
                    {!fits && <p className="mt-1 text-small text-paper-dim">{g.templateTooLong}</p>}
                    {shown === id && (
                      <pre className="num mt-2 max-h-72 overflow-auto rounded-control border border-seam bg-cloth p-3 text-small whitespace-pre-wrap text-paper-dim">
                        {t.text}
                      </pre>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
