// The rest of the standing, for the parts `GET /quack` does not speak for.
//
// Quack answers about pace, the feed and activity. Readiness, the chances of each saved program, the
// dates coming up and the conflicts between them live in `GET /overview` and `GET /matching`. This
// module joins the three into one Standing, the shape every dashboard screen already reads
// (contract.ts), so F3.3 changes no screen.
//
// Pure functions: the payloads come in, a Standing comes out. Fetching is remoteSource's job.

import type { BackendMatching, BackendOverview } from "@/api/backend";
import { REALISM_LEVEL } from "../choice/catalog";
import { LEVEL_LABEL } from "../choice/programs";
import type { ChanceFact, ConflictOption, ProgramChance, StandingAlert } from "./contract";


const DAY_MS = 86_400_000;

/** Whole days from `from` to the ISO date `to`; negative when it has passed */
export function daysUntil(to: string, today: Date): number {
  return Math.round((Date.parse(to) - Date.parse(today.toISOString().slice(0, 10))) / DAY_MS);
}

/**
 * Readiness across the exams the student is preparing for. The backend reports it per exam as a
 * share; the screens show one number in percent, and the worst exam is the honest one to show —
 * being ready for one exam and not the other is not "half ready".
 */
export function readinessOf(overview: BackendOverview): number {
  const progress = overview.progress ?? [];
  if (!progress.length) return 0;
  const worst = Math.min(...progress.map((p) => p.readiness));
  return Math.round(worst * 100);
}

/**
 * The student's saved programs as the chances card reads them. `/matching` answers about the whole
 * catalogue, and this card is about the plan — «всё, что следует из твоих сохранённых программ» —
 * so anything not saved is left out. The level and the order are the server's: realism is its call
 * (product-logic §1), and the hard factors become the tooltip behind the word.
 */
export function chancesOf(matching: BackendMatching, savedIds: string[]): ProgramChance[] {
  const saved = new Set(savedIds);
  return (matching.items ?? [])
    .filter((m) => saved.has(m.program.id))
    .map((m) => {
      const level = REALISM_LEVEL[m.realism] ?? "try";
    const facts: ChanceFact[] = (m.factors ?? [])
      .filter((f) => f.kind === "hard")
      .map((f) => ({
        key: "factor",
        label: f.text,
        have: "",
        need: "",
        // `unknown` is not a failure: it means the student has not told us yet
        ok: f.status === "unknown" ? null : f.status !== "below",
        margin: null,
      }));
    return {
      id: m.program.id,
      university: m.program.university,
      level,
      levelLabel: LEVEL_LABEL[level],
      // Only the direction of a change is ever shown, so the server's score is enough to order by
      index: Math.round(m.score * 100),
      facts,
    };
  });
}

/** A conflict's ways out arrive as plain sentences; the feed renders each as a button */
const optionsOf = (options: string[] | undefined): ConflictOption[] =>
  (options ?? []).map((label) => ({ label }));

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Dates the student can still act on, and the ones they have already slept through */
export function alertsOf(overview: BackendOverview, today: Date): StandingAlert[] {
  const list: StandingAlert[] = (overview.conflicts ?? []).map((c, i) => ({
    id: `conflict-${c.kind}-${(c.milestone_keys ?? []).join("-") || i}`,
    level: "urgent",
    kind: "conflict",
    title: "Конфликт в датах",
    detail: c.text,
    conflict: { id: (c.milestone_keys ?? []).join("|") || `${c.kind}-${i}`, options: optionsOf(c.options) },
  }));

  for (const m of overview.milestones ?? []) {
    if (m.done) continue;
    const left = daysUntil(m.date, today);
    if (left < 0) {
      // Older than six weeks is history, not something to act on
      if (left >= -45) {
        list.push({
          id: `missed-${m.key}`,
          level: "urgent",
          kind: "missed",
          title: `Пропущено: ${lowerFirst(m.title)}`,
          detail: `Срок был ${m.date}, а отметки нет. Если всё сделано — нажми «Уже сделал»`,
          milestone: m.key,
        });
      }
    } else if (left <= 7) {
      // The id keeps its bucket, not the day count: the button glows again only when it turns urgent
      list.push({
        id: `soon-${left <= 2 ? 2 : 7}-${m.key}`,
        level: left <= 2 ? "urgent" : "notice",
        kind: "deadline",
        milestone: m.key,
        title: `${m.title} — ${left === 0 ? "сегодня" : `через ${left} дн.`}`,
        detail: m.date,
      });
    }
  }
  return list;
}

/** The nearest date still ahead, for the line under the chances card */
export function nextDateOf(overview: BackendOverview, today: Date) {
  const ahead = (overview.milestones ?? [])
    .filter((m) => !m.done && daysUntil(m.date, today) >= 0)
    .sort((a, b) => a.date.localeCompare(b.date))[0];
  if (!ahead) return undefined;
  return { title: ahead.title, date: ahead.date, daysLeft: daysUntil(ahead.date, today) };
}
