"use client";

import { MoreHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { skillName, slugCommand } from "@/lib/skills";
import type { Skill, SkillBinding } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { PILL } from "./page-parts";
import { SkillChat } from "./skill-chat";
import { useMenu } from "./use-menu";
import { API_MODE } from "@/lib/api/client";
import { useNotice } from "./soon";
import { useSkills } from "./use-skills";

export function SkillsTab() {
  const tx = useT();
  const { skills, save: saveSkill, remove, toggle, canToggle } = useSkills();
  const notice = useNotice();
  // "chat" — помощник; { skill } — редактор (skill: null — пустой, новый)
  const [editing, setEditing] = useState<"chat" | { skill: Skill | null } | null>(null);
  const createBtn = useRef<HTMLButtonElement>(null);
  const manualBtn = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const returnFocus = useRef(false);

  // После закрытия фокус возвращается на кнопку, которая открыла чат или редактор
  useEffect(() => {
    if (!editing && returnFocus.current) {
      returnFocus.current = false;
      const el = opener.current;
      (el && el.isConnected ? el : createBtn.current)?.focus();
    }
  }, [editing]);

  const close = () => {
    returnFocus.current = true;
    setEditing(null);
  };

  // on the backend a save can fail (name or artifact type taken, bad name): say why and keep the form open
  const save = async (s: Skill) => {
    const err = await saveSkill(s, tx.live.serverError);
    if (err) return notice(err);
    close();
  };

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <p className="max-w-[46ch] text-paper-dim">
          {tx.skills.intro}
        </p>
        {!editing && (
          <div className="flex flex-wrap gap-2">
            <button
              ref={createBtn}
              type="button"
              onClick={() => {
                opener.current = createBtn.current;
                setEditing("chat");
              }}
              className="caps h-10 shrink-0 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover"
            >
              {tx.skills.create}
            </button>
            <button
              ref={manualBtn}
              type="button"
              onClick={() => {
                opener.current = manualBtn.current;
                setEditing({ skill: null });
              }}
              className="caps h-10 shrink-0 rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon"
            >
              {tx.skills.writeSelf}
            </button>
          </div>
        )}
      </div>

      {editing === "chat" && <SkillChat onSave={save} onManual={(skill) => setEditing({ skill })} onClose={close} />}
      {editing && editing !== "chat" && (
        <SkillEditor
          key={editing.skill?.id ?? "new"}
          initial={editing.skill}
          isNew={!editing.skill || !skills.some((x) => x.id === editing.skill!.id)}
          onSave={save}
          onCancel={close}
        />
      )}

      <ul className="mt-5 border-t border-seam-soft">
        {skills.map((s) => (
          <SkillRow
            key={s.id}
            skill={s}
            onToggle={canToggle ? () => toggle(s.id) : undefined}
            onEdit={(from) => {
              opener.current = from;
              setEditing({ skill: s });
            }}
            onDelete={() => void remove(s.id, tx.live.serverError).then((err) => err && notice(err))}
          />
        ))}
        {skills.length === 0 && (
          <li className="py-6 text-paper-dim">{tx.skills.empty}</li>
        )}
      </ul>

      <div className="mt-6 max-w-[62ch] space-y-2 text-small text-paper-dim">
        <p>{tx.skills.note1}</p>
        <p>{tx.skills.note2}</p>
      </div>
    </div>
  );
}

function SkillRow({
  skill,
  onToggle,
  onEdit,
  onDelete,
}: {
  skill: Skill;
  /** `undefined`: the backend has no on/off for a skill, no switch is shown */
  onToggle?: () => void;
  onEdit: (from: HTMLElement | null) => void;
  onDelete: () => void;
}) {
  const tx = useT();
  const nameId = useId();
  return (
    <li className="flex items-start gap-3 border-b border-seam-soft py-3.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <h3 id={nameId} className={cn("display-caps text-body", skill.enabled ? "text-paper" : "text-paper-dim")}>
            {skill.name}
          </h3>
          {skill.command && <code className="num text-small text-paper-dim">/{skill.command}</code>}
          {skill.command && <span className={PILL}>{tx.skills.callInChat}</span>}
          {skill.bindings.map((b) => (
            <span key={b} className={PILL}>
              {tx.skills.bound(tx.skills.binding[b])}
            </span>
          ))}
          {!skill.enabled && <span className={PILL}>{tx.skills.off}</span>}
        </div>
        <p className="mt-1 line-clamp-2 text-paper-dim">{skill.text}</p>
      </div>
      <div className="-mt-1 flex shrink-0 items-center">
        {onToggle && <Switch checked={skill.enabled} onChange={onToggle} labelledBy={nameId} />}
        <RowMenu name={skill.name} onEdit={onEdit} onDelete={onDelete} />
      </div>
    </li>
  );
}

function Switch({ checked, onChange, labelledBy }: { checked: boolean; onChange: () => void; labelledBy: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      onClick={onChange}
      className="inline-flex size-10 items-center justify-center rounded-pill"
    >
      <span
        aria-hidden
        className={cn("relative h-[22px] w-10 rounded-pill border transition-colors duration-(--dur-s) ease-out", checked ? "border-ribbon bg-ribbon" : "border-seam bg-transparent")}
      >
        <span
          className={cn(
            "absolute top-[2px] size-4 rounded-pill transition-all duration-(--dur-s) ease-out",
            checked ? "left-[20px] bg-on-ribbon" : "left-[2px] bg-paper-dim",
          )}
        />
      </span>
    </button>
  );
}

function RowMenu({ name, onEdit, onDelete }: { name: string; onEdit: (from: HTMLElement | null) => void; onDelete: () => void }) {
  const tx = useT();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLDivElement>(null);
  const onMenuKey = useMenu(open, root, () => setOpen(false));

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: PointerEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const item = "caps flex min-h-10 w-full items-center rounded-option px-3 text-left transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover";
  return (
    <div ref={root} className="relative">
      <div ref={trigger} className="contents">
      <IconButton label={tx.skills.more(name)} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="rounded-pill">
        <MoreHorizontal />
      </IconButton>
      </div>
      {open && (
        <ul
          role="menu"
          aria-label={tx.skills.actions(name)}
          onKeyDown={onMenuKey}
          className="absolute top-full right-0 z-20 mt-1 w-44 rounded-[2px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
        >
          <li role="none">
            <button
              role="menuitem"
              type="button"
              className={cn(item, "text-paper")}
              onClick={() => {
                setOpen(false);
                onEdit(trigger.current?.querySelector("button") ?? null);
              }}
            >
              {tx.skills.edit}
            </button>
          </li>
          <li role="none">
            <button
              role="menuitem"
              type="button"
              className={cn(item, "text-st-confused")}
              onClick={() => {
                setOpen(false);
                onDelete();
              }}
            >
              {tx.skills.delete}
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

const FIELD =
  "block w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper transition-colors duration-(--dur-s) ease-out focus-visible:border-ribbon";

function SkillEditor({ initial, isNew, onSave, onCancel }: { initial: Skill | null; isNew: boolean; onSave: (s: Skill) => void | Promise<void>; onCancel: () => void }) {
  const tx = useT();
  const uid = useId();
  const [name, setName] = useState(initial?.name ?? "");
  const [text, setText] = useState(initial?.text ?? "");
  const [bindings, setBindings] = useState<SkillBinding[]>(initial?.bindings ?? []);
  // on the backend every skill is called by its name
  const [callable, setCallable] = useState(API_MODE || (initial ? !!initial.command : false));
  const [errors, setErrors] = useState<{ name?: string; text?: string }>({});
  const nameRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  // Esc закрывает редактор, где бы ни стоял фокус
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  useEffect(() => {
    nameRef.current?.focus();
    nameRef.current?.closest("form")?.scrollIntoView({ block: "nearest" });
  }, []);

  const slug = slugCommand(name);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const errs: typeof errors = {};
    // on the backend the name is the `/command`: it must have letters or digits in it
    if (!name.trim() || (API_MODE && !skillName(name))) errs.name = tx.skills.nameError;
    if (!text.trim()) errs.text = tx.skills.textError;
    setErrors(errs);
    if (errs.name) return nameRef.current?.focus();
    if (errs.text) return textRef.current?.focus();
    onSave({
      id: initial?.id ?? `sk-${Date.now().toString(36)}`,
      name: name.trim(),
      text: text.trim(),
      bindings,
      command: callable && slug ? (API_MODE ? skillName(name) : slug) : undefined,
      enabled: initial?.enabled ?? true,
    });
  };

  const toggle = (b: SkillBinding) => setBindings((cur) => (cur.includes(b) ? cur.filter((x) => x !== b) : [...cur, b]));
  const err = "mt-1.5 text-small text-st-confused";

  return (
    <form
      onSubmit={submit}
      noValidate
      aria-label={isNew ? tx.skills.newTitle : tx.skills.editEditTitle}
      className="mt-5 rounded-[2px] border border-seam bg-cloth-raised p-4 md:p-5"
    >
      <h3 className="display-caps mb-4 text-paper">{isNew ? tx.skills.newTitle : tx.skills.editEditTitle}</h3>
      <div>
        <label htmlFor={`${uid}-name`} className="caps mb-1.5 block text-paper-dim">
          {tx.skills.name}
        </label>
        <input
          id={`${uid}-name`}
          ref={nameRef}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={!!errors.name}
          aria-describedby={errors.name ? `${uid}-name-err` : undefined}
          placeholder={tx.skills.namePlaceholder}
          className={cn(FIELD, "h-11", errors.name && "border-st-confused")}
        />
        {errors.name && (
          <p id={`${uid}-name-err`} className={err}>
            {errors.name}
          </p>
        )}
      </div>

      <div className="mt-4">
        <label htmlFor={`${uid}-text`} className="caps mb-1.5 block text-paper-dim">
          {tx.skills.textLabel}
        </label>
        <textarea
          id={`${uid}-text`}
          ref={textRef}
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={!!errors.text}
          aria-describedby={errors.text ? `${uid}-text-err` : undefined}
          placeholder={tx.skills.textPlaceholder}
          className={cn(FIELD, "resize-y py-2.5", errors.text && "border-st-confused")}
        />
        {errors.text && (
          <p id={`${uid}-text-err`} className={err}>
            {errors.text}
          </p>
        )}
      </div>

      <fieldset className="mt-4">
        <legend className="caps mb-1.5 text-paper-dim">{tx.skills.bindTo}</legend>
        <div className="flex flex-wrap gap-2">
          {(["notes", "cards", "cheat"] as SkillBinding[]).map((b) => {
            const on = bindings.includes(b);
            return (
              <button
                key={b}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(b)}
                className={cn(
                  "caps h-10 rounded-pill border px-5 transition-colors duration-(--dur-s) ease-out",
                  on ? "border-ribbon bg-ribbon-soft text-paper" : "border-seam text-paper-dim hover:border-ribbon hover:text-paper",
                )}
              >
                {tx.skills.binding[b]}
              </button>
            );
          })}
        </div>
      </fieldset>

      {API_MODE ? (
        <p className="mt-3 text-body text-paper">
          {tx.skills.callable}
          <code className="num ml-2 text-small text-paper-dim">{slug ? `/${skillName(name)}` : tx.skills.commandHint}</code>
        </p>
      ) : (
      <label className="mt-3 flex min-h-10 cursor-pointer items-center gap-3 text-body text-paper">
        <input
          type="checkbox"
          checked={callable}
          onChange={(e) => setCallable(e.target.checked)}
          className="size-5 shrink-0 cursor-pointer accent-ribbon"
        />
        <span>
          {tx.skills.callable}
          {callable && (
            <code className="num ml-2 text-small text-paper-dim">{slug ? `/${slug}` : tx.skills.commandHint}</code>
          )}
        </span>
      </label>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        <button
          type="submit"
          className="caps h-10 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover"
        >
          {tx.skills.save}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="caps h-10 rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon"
        >
          {tx.skills.cancel}
        </button>
      </div>
    </form>
  );
}
