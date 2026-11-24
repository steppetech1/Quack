"use client";

import { Check } from "lucide-react";
import { useId, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { SKILL_NAME_MAX, SKILL_TEXT_MAX, checkSkillDraft } from "@/lib/skills";
import { draftKey, useDraft, type Draft } from "@/lib/skill-drafts";
import type { Skill, SkillBinding } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useSkills } from "./use-skills";
import { useWorkspaceOptional } from "./workspace/workspace-context";

const FIELD =
  "block w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper transition-colors duration-(--dur-s) ease-out focus-visible:border-ribbon";
const TYPES: SkillBinding[] = ["notes", "cards", "cheat"];

/** The element `skill_proposal` of a teacher turn, as the backend sends it (`cheatsheet` is `cheat` in the UI) */
export type Proposal = { element_id: string; name: string; text: string; artifact_type: "notes" | "cards" | "cheatsheet" | null };

export const proposalDraft = (p: Proposal): Draft => ({
  name: p.name,
  text: p.text,
  binding: p.artifact_type === "cheatsheet" ? "cheat" : p.artifact_type,
});

/**
 * «Skill proposal» in the chat: the teacher drafted a skill, the learner edits it here and saves it (`POST /v1/learner-skills`
 * from the browser: the model never writes the skill store). The same draft can be opened beside the chat
 * (`SkillDraftDoc`), both edit the one draft of `lib/skill-drafts.ts`.
 */
export function SkillProposalCard({ turnSeq, proposal }: { turnSeq: number | null; proposal: Proposal }) {
  const key = draftKey(turnSeq ?? "live", proposal.element_id);
  return <SkillDraftForm draftId={key} base={proposalDraft(proposal)} live={turnSeq === null} />;
}

export function SkillDraftForm({ draftId, base, live = false, inPane = false }: { draftId: string; base: Draft; live?: boolean; inPane?: boolean }) {
  const tx = useT();
  const p = tx.skillProposal;
  const uid = useId();
  const ws = useWorkspaceOptional();
  const { skills, save } = useSkills();
  const [draft, setDraft] = useDraft(draftId, base);
  const [errors, setErrors] = useState<{ name?: string; text?: string; type?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(inPane);
  const nameRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  // saved: by this draft just now, or (after a reload) a skill that has the proposed name and text
  const saved = skills.find((s) => s.name === draft.savedName) ?? skills.find((s) => s.name === base.name && s.text === base.text);
  const inactive = live || busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (inactive || saved) return;
    const { errors: found, name } = checkSkillDraft(
      draft,
      skills.map((s) => s.name),
      {
        nameError: p.nameError(SKILL_NAME_MAX),
        nameTaken: p.nameTaken,
        reserved: p.reserved,
        textError: p.textError,
        textTooLong: p.textTooLong,
      },
    );
    // one skill per artifact type: the backend says so too, the card says it before asking
    const owner = draft.binding ? skills.find((s) => s.bindings.includes(draft.binding!)) : undefined;
    const errs: typeof errors = { ...found, ...(owner ? { type: p.typeTaken(tx.skills.binding[draft.binding!], owner.name) } : {}) };
    setErrors(errs);
    if (errs.name) return nameRef.current?.focus();
    if (errs.text) return textRef.current?.focus();
    if (errs.type) return;
    setBusy(true);
    const skill: Skill = { id: `sk-${Date.now().toString(36)}`, name: name!, text: draft.text.trim(), bindings: draft.binding ? [draft.binding] : [], command: name!, enabled: true };
    const err = await save(skill, tx.live.serverError);
    setBusy(false);
    if (!err) {
      setDraft({ savedName: name! });
      return;
    }
    // the backend explains conflicts itself: its words go next to the field they are about
    if (/name \/|built-in/i.test(err)) setErrors({ name: err });
    else if (/already has the skill/i.test(err)) setErrors({ type: err });
    else if (/instruction|characters/i.test(err)) setErrors({ text: err });
    else setErrors({ form: err });
  };

  const err = "mt-1.5 text-small text-st-confused";
  const edit = (patch: Partial<Draft>) => {
    setDraft(patch);
    setErrors({});
  };

  return (
    <form
      onSubmit={(e) => void submit(e)}
      noValidate
      aria-label={p.title}
      aria-busy={live || undefined}
      className={cn("rounded-[2px] border border-seam p-4", inPane && "border-0 p-0")}
    >
      {!inPane && <p className="caps text-paper-dim">{p.title}</p>}

      <div className={cn(!inPane && "mt-3")}>
        <label htmlFor={`${uid}-name`} className="caps mb-1.5 block text-paper-dim">
          {p.nameLabel}
        </label>
        <div className="flex items-center gap-2">
          <span aria-hidden className="num text-paper-dim">
            /
          </span>
          <input
            id={`${uid}-name`}
            ref={nameRef}
            value={draft.name}
            maxLength={SKILL_NAME_MAX + 1}
            disabled={inactive || !!saved}
            onChange={(e) => edit({ name: e.target.value })}
            aria-invalid={!!errors.name}
            aria-describedby={errors.name ? `${uid}-name-err` : undefined}
            autoCapitalize="none"
            spellCheck={false}
            className={cn(FIELD, "num h-11 disabled:opacity-70", errors.name && "border-st-confused")}
          />
        </div>
        {errors.name && (
          <p id={`${uid}-name-err`} role="alert" className={err}>
            {errors.name}
          </p>
        )}
      </div>

      <fieldset className="mt-4" disabled={inactive || !!saved}>
        <legend className="caps mb-1.5 text-paper-dim">{p.bindLabel}</legend>
        <div className="flex flex-wrap gap-2">
          {[...TYPES, null].map((b) => {
            const on = draft.binding === b;
            return (
              <button
                key={b ?? "none"}
                type="button"
                aria-pressed={on}
                onClick={() => edit({ binding: b })}
                className={cn(
                  "caps h-10 rounded-pill border px-5 transition-colors duration-(--dur-s) ease-out disabled:opacity-60",
                  on ? "border-ribbon bg-ribbon-soft text-paper" : "border-seam text-paper-dim enabled:hover:border-ribbon enabled:hover:text-paper",
                )}
              >
                {b ? tx.skills.binding[b] : p.bindNone}
              </button>
            );
          })}
        </div>
        {errors.type && (
          <p role="alert" className={err}>
            {errors.type}
          </p>
        )}
      </fieldset>

      <div className="mt-4">
        <div className="mb-1.5 flex items-baseline justify-between gap-3">
          <label htmlFor={`${uid}-text`} className="caps text-paper-dim">
            {p.textLabel}
          </label>
          <span className={cn("num text-small", draft.text.length > SKILL_TEXT_MAX ? "text-st-confused" : "text-paper-dim")}>
            {draft.text.length}/{SKILL_TEXT_MAX}
          </span>
        </div>
        <textarea
          id={`${uid}-text`}
          ref={textRef}
          rows={open ? 10 : 3}
          value={draft.text}
          disabled={inactive || !!saved}
          onChange={(e) => edit({ text: e.target.value })}
          aria-invalid={!!errors.text}
          aria-describedby={errors.text ? `${uid}-text-err` : undefined}
          className={cn(FIELD, "resize-y py-2.5 disabled:opacity-70", errors.text && "border-st-confused")}
        />
        {errors.text && (
          <p id={`${uid}-text-err`} role="alert" className={err}>
            {errors.text}
          </p>
        )}
        {!inPane && (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="caps -ml-3 mt-1 min-h-10 rounded-pill px-3 text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper"
          >
            {open ? p.collapse : p.expand}
          </button>
        )}
      </div>

      {errors.form && (
        <p role="alert" className={cn(err, "mt-3")}>
          {errors.form}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {saved ? (
          <>
            <p role="status" className="caps inline-flex h-10 items-center gap-2 text-st-solid">
              <Check aria-hidden className="size-4" />
              {p.saved}
            </p>
            {ws && (
              <button
                type="button"
                onClick={() => ws.open({ kind: "skill", id: saved.id, title: `/${saved.command ?? saved.name}` })}
                className="num min-h-10 text-ribbon underline decoration-ribbon/40 underline-offset-2 hover:decoration-ribbon"
              >
                {p.openSaved(saved.command ?? saved.name)}
              </button>
            )}
          </>
        ) : (
          <>
            <button
              type="submit"
              disabled={inactive}
              className="caps h-10 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover disabled:cursor-default disabled:opacity-60"
            >
              {busy ? p.saving : p.save}
            </button>
            {!inPane && ws && (
              <button
                type="button"
                disabled={live}
                onClick={() => ws.open({ kind: "skill_draft", id: draftId, title: p.draftTitle(draft.name), base })}
                className="caps h-10 rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon disabled:opacity-50"
              >
                {p.openSide}
              </button>
            )}
          </>
        )}
      </div>
    </form>
  );
}
