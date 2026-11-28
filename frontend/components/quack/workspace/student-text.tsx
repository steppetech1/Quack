"use client";

import { useT } from "@/lib/i18n/provider";
import { skillName } from "@/lib/skills";
import { useSkills } from "../use-skills";
import { useWorkspaceOptional } from "./workspace-context";

const LEADING_COMMAND = /^(\s*)\/([\p{L}\p{N}_-]{1,40})(?=\s|$)/u;

/**
 * The words of a learner message. A leading `/name` that is one of the learner's skills is a button that opens the skill in
 * the workspace pane; anywhere without a pane (or without such a skill) the text is just text.
 */
export function StudentText({ text }: { text: string }) {
  const tx = useT();
  const ws = useWorkspaceOptional();
  const { skills } = useSkills();
  const m = ws ? LEADING_COMMAND.exec(text) : null;
  const skill = m ? skills.find((s) => (s.command ?? skillName(s.name)).toLowerCase() === m[2].toLowerCase()) : undefined;
  if (!ws || !m || !skill) return <>{text}</>;
  const name = skill.command ?? skillName(skill.name);
  return (
    <>
      {m[1]}
      <button
        type="button"
        aria-label={tx.workspace.openSkill(name)}
        onClick={() => ws.open({ kind: "skill", id: skill.id, title: `/${name}` })}
        className="num relative inline text-ribbon underline decoration-ribbon/40 underline-offset-2 hover:decoration-ribbon after:absolute after:-inset-y-2.5 after:inset-x-0 focus-visible:outline-2 focus-visible:outline-ribbon"
      >
        /{m[2]}
      </button>
      {text.slice(m[0].length)}
    </>
  );
}
