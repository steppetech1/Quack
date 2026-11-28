"use client";

import { Slash } from "lucide-react";
import { useT } from "@/lib/i18n/provider";
import { skillName } from "@/lib/skills";
import { useSkills } from "../use-skills";
import { DocScroll } from "./doc-scroll";
import { useWorkspace } from "./workspace-context";

const ROW =
  "flex min-h-10 w-full items-center gap-3 rounded-[2px] border border-seam bg-cloth-raised px-4 py-3 text-left transition-colors duration-(--dur-s) ease-out hover:border-ribbon focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon";

/** The learner's skills in a list: a click opens one as its own tab (the skill document, which can edit and delete it) */
export function SkillsDoc() {
  const tx = useT();
  const t = tx.workspace.skillsDoc;
  const ws = useWorkspace();
  const { skills, loaded } = useSkills();

  return (
    <DocScroll>
      <p className="text-paper-dim">{t.intro}</p>
      {!loaded ? (
        <p role="status" className="mt-4 text-paper-dim">
          {t.loading}
        </p>
      ) : skills.length === 0 ? (
        <p className="mt-4 text-paper-dim">{t.empty}</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {skills.map((s) => {
            const command = s.command ?? skillName(s.name);
            return (
              <li key={s.id}>
                <button
                  type="button"
                  aria-label={tx.workspace.openSkill(command)}
                  onClick={() => ws.open({ kind: "skill", id: s.id, title: `/${command}` })}
                  className={ROW}
                >
                  <Slash aria-hidden className="size-4 shrink-0 text-ribbon" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-paper">/{command}</span>
                    {s.text && <span className="mt-0.5 block line-clamp-2 text-small text-paper-dim">{s.text}</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </DocScroll>
  );
}
