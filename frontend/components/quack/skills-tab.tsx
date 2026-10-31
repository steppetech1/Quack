"use client";

import { MoreHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { slugCommand } from "@/lib/skills";
import type { Skill, SkillBinding } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { PILL } from "./page-parts";
import { SkillChat } from "./skill-chat";
import { useMenu } from "./use-menu";
import { useSkills } from "./use-skills";

export function SkillsTab() {
  const tx = useT();
  const { skills, update } = useSkills();
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

  const save = (s: Skill) => {
    update((prev) => (prev.some((x) => x.id === s.id) ? prev.map((x) => (x.id === s.id ? s : x)) : [...prev, s]));
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
              className="h-10 shrink-0 rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover"
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
              className="h-10 shrink-0 rounded-control border border-seam px-4 text-body text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
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
            onToggle={() => update((prev) => prev.map((x) => (x.id === s.id ? { ...x, enabled: !x.enabled } : x)))}
            onEdit={(from) => {
              opener.current = from;
              setEditing({ skill: s });
            }}
            onDelete={() => update((prev) => prev.filter((x) => x.id !== s.id))}
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
  onToggle: () => void;
  onEdit: (from: HTMLElement | null) => void;
  onDelete: () => void;
}) {
  const tx = useT();
  const nameId = useId();
  return (
    <li className="flex items-start gap-3 border-b border-seam-soft py-3.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <h3 id={nameId} className={cn("font-semibold", skill.enabled ? "text-paper" : "text-paper-dim")}>
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
        <Switch checked={skill.enabled} onChange={onToggle} labelledBy={nameId} />
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
      className="inline-flex size-10 items-center justify-center rounded-control"
    >
      <span
        aria-hidden
        className={cn("relative h-[22px] w-10 rounded-full border transition-colors", checked ? "border-paper/85 bg-paper/85" : "border-rule bg-seam")}
      >
        <span
          className={cn(
            "absolute top-[2px] size-4 rounded-full transition-all",
            checked ? "left-[20px] bg-cloth" : "left-[2px] bg-paper-dim",
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

  const item = "flex min-h-10 w-full items-center rounded-option px-3 text-left text-body transition-colors hover:bg-cloth-hover";
  return (
    <div ref={root} className="relative">
      <div ref={trigger} className="contents">
      <IconButton label={tx.skills.more(name)} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <MoreHorizontal />
      </IconButton>
      </div>
      {open && (
        <ul
          role="menu"
          aria-label={tx.skills.actions(name)}
          onKeyDown={onMenuKey}
          className="absolute top-full right-0 z-20 mt-1 w-44 rounded-[14px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
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
  "block w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper focus-visible:border-rule";

function SkillEditor({ initial, isNew, onSave, onCancel }: { initial: Skill | null; isNew: boolean; onSave: (s: Skill) => void; onCancel: () => void }) {
  const tx = useT();
  const uid = useId();
  const [name, setName] = useState(initial?.name ?? "");
  const [text, setText] = useState(initial?.text ?? "");
  const [bindings, setBindings] = useState<SkillBinding[]>(initial?.bindings ?? []);
  const [callable, setCallable] = useState(initial ? !!initial.command : false);
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
    if (!name.trim()) errs.name = tx.skills.nameError;
    if (!text.trim()) errs.text = tx.skills.textError;
    setErrors(errs);
    if (errs.name) return nameRef.current?.focus();
    if (errs.text) return textRef.current?.focus();
    onSave({
      id: initial?.id ?? `sk-${Date.now().toString(36)}`,
      name: name.trim(),
      text: text.trim(),
      bindings,
      command: callable && slug ? slug : undefined,
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
      className="mt-5 rounded-[16px] border border-seam bg-cloth-raised p-4 md:p-5"
    >
      <h3 className="mb-4 font-semibold text-paper">{isNew ? tx.skills.newTitle : tx.skills.editEditTitle}</h3>
      <div>
        <label htmlFor={`${uid}-name`} className="mb-1.5 block text-small text-paper-dim">
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
        <label htmlFor={`${uid}-text`} className="mb-1.5 block text-small text-paper-dim">
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
        <legend className="mb-1.5 text-small text-paper-dim">{tx.skills.bindTo}</legend>
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
                  "h-10 rounded-full border px-4 text-body transition-colors",
                  on ? "border-paper bg-cloth-hover text-paper" : "border-seam text-paper-dim hover:border-rule hover:text-paper",
                )}
              >
                {tx.skills.binding[b]}
              </button>
            );
          })}
        </div>
      </fieldset>

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

      <div className="mt-5 flex flex-wrap gap-2">
        <button
          type="submit"
          className="h-10 rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover"
        >
          {tx.skills.save}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="h-10 rounded-control border border-seam px-4 text-body text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
        >
          {tx.skills.cancel}
        </button>
      </div>
    </form>
  );
}
