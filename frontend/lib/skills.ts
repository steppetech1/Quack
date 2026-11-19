import type { Skill, SkillBinding } from "@/lib/mock/types";

/** localStorage key for user-made skills */
export const SKILLS_KEY = "quack.skills.v1";

/** kebab-case slug of a skill name: «Доказательство по шагам» becomes «доказательство-по-шагам» */
export function slugCommand(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
}

/** What the backend takes as a skill name: `/name` in chat, 1–40 letters, digits, `-`, `_` (`NAME_PATTERN` of the backend) */
export const skillName = (name: string): string => slugCommand(name).slice(0, 40).replace(/-+$/g, "");

/** Skill of the backend → the shape of the settings tab. The name is the command; the backend has no "off" switch */
export function skillFromApi(s: { skill_id: string; name: string; instruction: string; bindings: { artifact_types?: string[] } }): Skill {
  return {
    id: s.skill_id,
    name: s.name,
    text: s.instruction,
    bindings: (s.bindings.artifact_types ?? []).map((t) => (t === "cheatsheet" ? "cheat" : t)).filter((t): t is SkillBinding => t === "notes" || t === "cards" || t === "cheat"),
    command: s.name,
    enabled: true,
  };
}

/** Skill of the settings tab → body of `POST/PATCH /v1/learner-skills`; `cheat` is `cheatsheet` there */
export function skillToApi(s: Skill) {
  return {
    name: skillName(s.name),
    instruction: s.text,
    bindings: { artifact_types: s.bindings.map((b) => (b === "cheat" ? "cheatsheet" : b)) },
  };
}

/** `/name rest of the message` → the name of the skill the message calls, or `undefined` (an unknown name changes nothing on the backend) */
export function skillInvocation(text: string): string | undefined {
  const m = /^\/([\p{L}\p{N}_-]{1,40})(?:\s|$)/u.exec(text.trim());
  return m?.[1].toLowerCase();
}
