/** localStorage key for user-made skills */
export const SKILLS_KEY = "quack.skills.v1";

/** kebab-case slug of a skill name: «Доказательство по шагам» becomes «доказательство-по-шагам» */
export function slugCommand(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
}
