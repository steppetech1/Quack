"use client";

import { MoreHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import type { Skill } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { PILL } from "./page-parts";
import { SkillsMigrate } from "./skills-migrate";
import { useMenu } from "./use-menu";
import { API_MODE } from "@/lib/api/client";
import { useNotice } from "./soon";
import { useSkills } from "./use-skills";
import { SkillDoc } from "./workspace/doc-skill";

/**
 * The learner's skills: the list, on/off, delete, and «Open» (the same read view the workspace pane of a session has).
 * Skills are not made here: the learner types `/skill-creator` in the teacher chat and the teacher drafts one with them.
 */
export function SkillsTab() {
  const tx = useT();
  const { skills, remove, migrate, toggle, canToggle } = useSkills();
  const notice = useNotice();
  const [opened, setOpened] = useState<string | null>(null);

  return (
    <div>
      <p className="max-w-[56ch] text-paper-dim">{tx.skills.intro}</p>
      <p className="mt-2 max-w-[56ch] text-body text-paper">
        {tx.skills.createHint[0]}
        <code className="num mx-1 text-small text-paper-dim">/skill-creator</code>
        {tx.skills.createHint[1]}
      </p>

      {API_MODE && <SkillsMigrate migrate={migrate} />}

      <ul className="mt-5 border-t border-seam-soft">
        {skills.map((s) => (
          <li key={s.id} className="border-b border-seam-soft">
            <SkillRow
              skill={s}
              isOpen={opened === s.id}
              onToggle={canToggle ? () => toggle(s.id) : undefined}
              onOpen={() => setOpened(opened === s.id ? null : s.id)}
              onDelete={() => void remove(s.id, tx.live.serverError).then((err) => err && notice(err))}
            />
            {opened === s.id && (
              <div className="mb-3 rounded-[2px] border border-seam bg-cloth-raised">
                <SkillDoc doc={{ id: s.id }} onGone={() => setOpened(null)} />
              </div>
            )}
          </li>
        ))}
        {skills.length === 0 && <li className="py-6 text-paper-dim">{tx.skills.empty}</li>}
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
  isOpen,
  onToggle,
  onOpen,
  onDelete,
}: {
  skill: Skill;
  isOpen: boolean;
  /** `undefined`: the backend has no on/off for a skill, no switch is shown */
  onToggle?: () => void;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const tx = useT();
  const nameId = useId();
  return (
    <div className="flex items-start gap-3 py-3.5">
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
        <RowMenu name={skill.name} isOpen={isOpen} onOpen={onOpen} onDelete={onDelete} />
      </div>
    </div>
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

function RowMenu({ name, isOpen, onOpen, onDelete }: { name: string; isOpen: boolean; onOpen: () => void; onDelete: () => void }) {
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
                onOpen();
              }}
            >
              {isOpen ? tx.skills.hide : tx.skills.open}
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
