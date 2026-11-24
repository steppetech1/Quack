"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { SKILL_TEXT_MAX, skillName } from "@/lib/skills";
import { cn } from "@/lib/utils";
import { PILL } from "../page-parts";
import { useSkills } from "../use-skills";
import { DocScroll } from "./doc-scroll";
import { useWorkspace, type WorkspaceDoc } from "./workspace-context";

const BTN = "caps h-10 rounded-pill border border-seam px-5 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon disabled:opacity-50";
const DANGER = "caps h-10 rounded-pill border border-seam px-5 text-st-confused transition-colors duration-(--dur-s) ease-out hover:border-st-confused disabled:opacity-50";
const MAIN = "caps h-10 rounded-pill bg-ribbon px-5 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover disabled:opacity-50";

/**
 * A skill of the learner: the command, what it is bound to and the instruction. The text can be changed and the skill
 * deleted (the same calls as in the settings; nothing creates a skill here).
 */
export function SkillDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "skill" }> }) {
  const tx = useT();
  const s = tx.workspace.skill;
  const ws = useWorkspace();
  const { skills, loaded, save, remove } = useSkills();
  const skill = skills.find((x) => x.id === doc.id);
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const uid = useId();

  useEffect(() => {
    if (mode === "edit") area.current?.focus();
  }, [mode]);

  if (!skill) {
    return (
      <DocScroll>
        <p role={loaded ? "alert" : "status"} className="text-paper-dim">
          {loaded ? s.gone : s.loading}
        </p>
      </DocScroll>
    );
  }

  const command = skill.command ?? skillName(skill.name);
  const startEdit = () => {
    setDraft(skill.text);
    setError(null);
    setMode("edit");
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return setError(tx.skills.textError);
    setBusy(true);
    const err = await save({ ...skill, text: draft.trim() }, tx.live.serverError);
    setBusy(false);
    if (err) return setError(err);
    setMode("view");
  };
  const del = async () => {
    setBusy(true);
    const err = await remove(skill.id, tx.live.serverError);
    setBusy(false);
    if (err) return setError(err);
    ws.close(`skill:${skill.id}`);
  };

  return (
    <DocScroll>
      <p className="caps text-paper-dim">{s.command}</p>
      <h2 className="num mt-1 text-[1.5rem] leading-tight text-paper [overflow-wrap:anywhere]">/{command}</h2>
      {skill.name !== command && <p className="mt-1 text-paper-dim">{skill.name}</p>}

      <p className="caps mt-6 text-paper-dim">{skill.bindings.length ? s.bindings : s.noBindings}</p>
      {skill.bindings.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {skill.bindings.map((b) => (
            <li key={b} className={PILL}>
              {tx.skills.binding[b]}
            </li>
          ))}
        </ul>
      )}

      {mode === "edit" ? (
        <form onSubmit={submit} className="mt-6" noValidate>
          <label htmlFor={`${uid}-text`} className="caps mb-1.5 block text-paper-dim">
            {s.text}
          </label>
          <textarea
            id={`${uid}-text`}
            ref={area}
            rows={9}
            maxLength={SKILL_TEXT_MAX}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-invalid={!!error}
            className={cn(
              "block w-full resize-y rounded-control border border-seam bg-cloth px-3 py-2.5 text-body text-paper transition-colors duration-(--dur-s) ease-out focus-visible:border-ribbon",
              error && "border-st-confused",
            )}
          />
          {error && (
            <p role="alert" className="mt-1.5 text-small text-st-confused">
              {error}
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="submit" disabled={busy} className={MAIN}>
              {busy ? s.saving : tx.skills.save}
            </button>
            <button type="button" disabled={busy} onClick={() => setMode("view")} className={BTN}>
              {tx.skills.cancel}
            </button>
          </div>
        </form>
      ) : (
        <>
          <p className="caps mt-6 text-paper-dim">{s.text}</p>
          <p className="mt-2 whitespace-pre-wrap text-paper [overflow-wrap:anywhere]">{skill.text}</p>

          {mode === "delete" ? (
            <div role="alertdialog" aria-label={s.deleteAsk(command)} className="mt-6 rounded-control border border-seam p-4">
              <p className="text-paper">{s.deleteAsk(command)}</p>
              {error && (
                <p role="alert" className="mt-2 text-small text-st-confused">
                  {error}
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" disabled={busy} onClick={() => void del()} className={DANGER}>
                  {s.deleteYes}
                </button>
                <button type="button" disabled={busy} onClick={() => setMode("view")} className={BTN} autoFocus>
                  {tx.skills.cancel}
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-6 flex flex-wrap gap-2">
              <button type="button" onClick={startEdit} className={BTN}>
                {s.editText}
              </button>
              <button
                type="button"
                onClick={() => {
                  setError(null);
                  setMode("delete");
                }}
                className={DANGER}
              >
                {tx.skills.delete}
              </button>
            </div>
          )}
        </>
      )}
    </DocScroll>
  );
}
