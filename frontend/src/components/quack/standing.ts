// The rules layer (L4) as the browser runs it until the backend takes over: from the sources of
// truth — profile, saved programs, preparation — to one Standing. Pure functions, no React, no storage.

import type { Profile } from "../choice/assistant";
import { budgetOf, evaluate, LEVEL_LABEL, programById, type Level, type Program } from "../choice/programs";
import {
  calendarEvents,
  EXAMS,
  hardConflicts,
  unionExams,
  type CalendarEvent,
  type Conflict,
  type UnionExam,
} from "../dashboard/dashboardRules";
import {
  dateKey,
  daysBetween,
  examMilestoneId,
  forecastSeries,
  formatDate,
  plannedTest,
  registrationBy,
  allSets,
  setById,
  TODAY,
  type DatedExam,
  type ExamId,
  type StudySet,
} from "../prep/prepData";
import { proposedSet, readiness, type PrepModel } from "../prep/prepModel";
import type { AdviceAction, ChanceFact, ExamPace, PaceLevel, ProgramChance, Standing, StandingAlert } from "./contract";

export type QuackInputs = { profile: Profile; saved: string[]; prep: PrepModel };

const iso = (d: Date) => d.toISOString();
const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Each point of readiness moves the forecast by half a day (see forecastSeries). */
const DAYS_PER_POINT = 0.5;

export const PACE_VERDICT: Record<PaceLevel, string> = {
  0: "Не успеваешь",
  1: "Нужно ускориться",
  2: "Успеваешь",
  3: "С запасом",
};

/** Readiness in «Подготовке» read as a SAT Math forecast score. */
export const forecastScore = (percent: number) => Math.round((400 + percent * 4) / 10) * 10;

/**
 * The route falls behind when its first open set is past its deadline. The forecast moves by the same
 * number of days — preparation never blocks or scolds, it recounts (product-logic §4.3).
 */
export function routeDelay(prep: PrepModel, today = TODAY): { set: StudySet; days: number } | null {
  const open = allSets().find((s) => !prep.doneSets.includes(s.id));
  if (!open) return null;
  const days = daysBetween(open.deadline, today);
  return days > 0 ? { set: open, days } : null;
}

/**
 * What closing the set in work is worth, in days of forecast: the readiness it adds, plus the delay
 * of the route it takes away when it was the set holding the route back.
 */
function closingGain(prep: PrepModel, today: Date, exam: ExamId): { set: StudySet; days: number } | null {
  const set = prep.currentSet ? setById(prep.currentSet) : proposedSet(prep);
  if (!set || set.exam !== exam) return null;
  const states = { ...prep.states, ...Object.fromEntries(set.skills.map((id) => [id, "solid" as const])) };
  const closed = { ...prep, states, doneSets: [...prep.doneSets, set.id], currentSet: null };
  const fromSkills = (readiness(closed, exam) - readiness(prep, exam)) * DAYS_PER_POINT;
  const fromRoute = (routeDelay(prep, today)?.days ?? 0) - (routeDelay(closed, today)?.days ?? 0);
  return { set, days: Math.round(fromSkills + fromRoute) };
}

/* ---------- Exams: ready by the test? ---------- */

type Ctx = { today: Date; prep: PrepModel; forecast: Date; delay: ReturnType<typeof routeDelay>; conflicts: Conflict[] };

const targetOf = (u: UnionExam) => (u.exam.id === "ielts" ? u.target.toFixed(1) : String(u.target));

/** An exam the knowledge model covers (SAT Math, ЕНТ): forecast against the test date. */
function modelPace(u: UnionExam, { today, prep, forecast, delay }: Ctx): ExamPace {
  const base = { id: u.exam.id, name: u.exam.name, target: targetOf(u) };
  // The sitting the student picked, or the nearest one while they have not
  const exam = u.exam.id as DatedExam;
  const planned = plannedTest(exam, prep.testDates);
  if (!planned) return { ...base, level: 0, verdict: PACE_VERDICT[0], summary: "Ближайших дат теста в календаре нет", advice: [] };

  const following = u.exam.dates.find((d) => d > planned);
  const registration = registrationBy(planned);
  const dated = { ...base, testDate: iso(planned), forecast: iso(forecast) };
  const registered = examMilestoneId(exam, "reg", planned);

  // Slept through the registration: this test date is gone, whatever the readiness
  if (registration < today && !prep.milestonesDone.includes(registered)) {
    return {
      ...dated,
      level: 0,
      verdict: PACE_VERDICT[0],
      summary: `К тесту ${formatDate(planned)} уже не успеть — регистрация закрылась ${formatDate(registration)}`,
      advice: [
        following
          ? `Следующая дата — ${formatDate(following)}, регистрация до ${formatDate(registrationBy(following))}. Выбрать её можно в календаре или в «Требованиях»`
          : "Других дат в этом году нет — нужен слот на следующий год",
        `Если ты уже зарегистрировался на ${formatDate(planned)} — нажми кнопку ниже или напиши об этом в чате, и прогноз вернётся`,
      ],
      adviceActions: [following ? moveTo(exam, following) : null, null],
      mark: { milestone: registered, label: `Уже зарегистрировался на ${formatDate(planned)}` },
    };
  }

  const margin = daysBetween(forecast, planned);
  const level: PaceLevel = margin < 0 ? 0 : margin < 7 ? 1 : margin < 21 ? 2 : 3;
  const advice: string[] = [];
  const adviceActions: (AdviceAction | null)[] = [];
  const say = (text: string, action: AdviceAction | null = null) => {
    advice.push(text);
    adviceActions.push(action);
  };
  const toPrep: AdviceAction = { kind: "open-prep", label: "Открыть сет" };
  if (level <= 1) {
    if (margin < 0) {
      say(`Нужно нагнать ${-margin} дн., чтобы прогноз встал на ${formatDate(planned)}`);
    }
    const gain = closingGain(prep, today, u.exam.id as ExamId);
    if (gain && gain.days > 0) {
      const when = gain.set.deadline >= today ? `до ${formatDate(gain.set.deadline)}` : "как можно скорее";
      say(`Закрой сет ${gain.set.number} «${gain.set.title}» ${when} — прогноз станет раньше на ≈${gain.days} дн.`, toPrep);
    }
    if (margin < 0 && following) say(`Или перенеси тест на ${formatDate(following)} — запас будет ${daysBetween(forecast, following)} дн.`, moveTo(exam, following));
    if (delay) say(`Сет ${delay.set.number} должен был закрыться ${formatDate(delay.set.deadline)} — прогноз сдвинулся на ${delay.days} дн.`, toPrep);
  }

  const summary =
    margin < 0
      ? `Готовность к ${formatDate(forecast)}, а тест ${formatDate(planned)} — не хватает ${-margin} дн.`
      : margin < 7
        ? `Готовность к ${formatDate(forecast)}, тест ${formatDate(planned)} — запас всего ${margin} дн.`
        : `Готовность к ${formatDate(forecast)}, тест ${formatDate(planned)} — запас ${margin} дн.`;
  return { ...dated, level, verdict: PACE_VERDICT[level], summary, advice, adviceActions };
}

/** Taking the advice to sit another date picks it, as the calendar and «Требования» do */
const moveTo = (exam: DatedExam, test: Date): AdviceAction => ({
  kind: "pick-date",
  exam,
  key: dateKey(test),
  label: `Перенести на ${formatDate(test)}`,
});

/* ---------- Programs: chances as words, plus the facts behind them ---------- */

export const LEVEL_RANK: Record<Level, number> = { realistic: 2, try: 1, unlikely: 0 };

/** How much a fact moves the ordering index; only the direction of a change is ever shown. */
function weight(f: ChanceFact): number {
  if (f.margin === null) return f.key === "budget" ? 20 : 0;
  if (f.key === "sat") return clamp(f.margin / 5, -30, 30);
  if (f.key === "sat-forecast") return clamp(f.margin / 2.5, -30, 30);
  if (f.key === "ielts") return clamp(f.margin * 20, -30, 30);
  return clamp(f.margin / 100, -20, 20);
}

export function chanceOf(program: Program, profile: Profile, predictedMath: number): ProgramChance {
  // The level comes from the same scoring the rest of the app uses, so every screen agrees
  const { level } = evaluate(program, profile);
  const facts: ChanceFact[] = [];

  const ieltsGiven = profile.ielts && /^\d/.test(profile.ielts) ? Number(profile.ielts) : undefined;
  const ielts = ieltsGiven ?? 6.0;
  facts.push({
    key: "ielts",
    label: "IELTS",
    have: ieltsGiven ? ielts.toFixed(1) : "6.0 (допущение)",
    need: program.ieltsMin.toFixed(1),
    ok: ielts >= program.ieltsMin,
    margin: Math.round((ielts - program.ieltsMin) * 10) / 10,
  });

  if (program.satMin) {
    const sat = profile.sat && /^\d+$/.test(profile.sat) ? Number(profile.sat) : undefined;
    if (sat !== undefined) {
      facts.push({ key: "sat", label: "SAT", have: String(sat), need: String(program.satMin), ok: sat >= program.satMin, margin: sat - program.satMin });
    } else {
      // No score yet: preparation's forecast stands in for it — a failed topic lowers it, a closed set raises it
      const need = Math.round(program.satMin / 2 / 10) * 10;
      facts.push({
        key: "sat-forecast",
        label: "SAT Math по прогнозу",
        have: String(predictedMath),
        need: String(need),
        ok: predictedMath >= need,
        margin: predictedMath - need,
      });
    }
  }

  const budget = budgetOf(profile);
  if (budget !== undefined) {
    facts.push({
      key: "budget",
      label: "Бюджет",
      have: budget === Infinity ? "не ограничен" : `до ${budget}$`,
      need: `€${program.costEur}`,
      ok: program.costEur <= budget,
      margin: budget === Infinity ? null : budget - program.costEur,
    });
  }

  const index = Math.round(LEVEL_RANK[level] * 100 + facts.reduce((sum, f) => sum + weight(f), 0));
  return { id: program.id, university: program.university, level, levelLabel: LEVEL_LABEL[level], index, facts };
}

/* ---------- Dates: coming up, slept through, in conflict ---------- */

/** Dates the student can tick off (in the calendar, the Quack feed or the chat); only those can be missed. */
export const markableId = (event: CalendarEvent): string | undefined => event.milestone;

function alertsFor(events: CalendarEvent[], conflicts: Conflict[], programs: Program[], { today, prep, delay }: Ctx): StandingAlert[] {
  // A conflict the student settled by choosing a way out is theirs to keep: it is not raised again
  const list: StandingAlert[] = conflicts
    .filter((c) => !prep.resolvedConflicts[c.id])
    .map((c) => ({
      id: `conflict-${c.id}`,
      level: "urgent",
      kind: "conflict",
      title: "Конфликт в датах",
      detail: c.text,
      conflict: { id: c.id, options: c.options },
    }));

  for (const e of events) {
    const done = markableId(e);
    if (done && prep.milestonesDone.includes(done)) continue;
    const left = daysBetween(today, e.date);
    if (left < 0) {
      if (done && left >= -45) {
        list.push({
          id: `missed-${done}`,
          level: "urgent",
          kind: "missed",
          title: `Пропущено: ${lowerFirst(e.title)}`,
          detail: `Срок был ${formatDate(e.date)}, а отметки нет. Если всё сделано — нажми «Уже сделал» или отметь в календаре`,
          milestone: done,
        });
      }
    } else if (left <= 7) {
      // The id keeps its bucket, not the day count: the button glows again only when it turns urgent
      list.push({
        id: `soon-${left <= 2 ? 2 : 7}-${e.title}`,
        level: left <= 2 ? "urgent" : "notice",
        kind: "deadline",
        milestone: done,
        title: `${e.title} — ${left === 0 ? "сегодня" : `через ${left} дн.`}`,
        detail: formatDate(e.date),
      });
    }
  }

  if (delay) {
    list.push({
      id: `late-${delay.set.id}`,
      level: "notice",
      kind: "late-set",
      title: `Сет ${delay.set.number} ещё открыт`,
      detail: `Срок был ${formatDate(delay.set.deadline)} — прогноз сдвинулся на ${delay.days} дн.`,
    });
  }
  return list;
}

/* ---------- The whole standing ---------- */

export function computeStanding({ profile, saved, prep }: QuackInputs, today = TODAY): Standing {
  const programs = saved.map(programById).filter(Boolean);
  const exams = unionExams(programs);
  const events = calendarEvents(programs, exams, prep.testDates);
  const conflicts = hardConflicts(programs, exams, prep.testDates);

  const now = readiness(prep);
  const delay = routeDelay(prep, today);
  // Each planned exam has its own forecast from its own skills
  const ctxFor = (exam: ExamId): Ctx => ({
    today,
    prep,
    forecast: forecastSeries(readiness(prep, exam), prep.extraDays + (delay?.set.exam === exam ? delay.days : 0), exam).forecast,
    delay: delay?.set.exam === exam ? delay : null,
    conflicts,
  });

  // The plan includes exams asked for by the student's saved programs
  const entPrograms = programs.filter((p) => p.entMin || p.country === "Казахстан");
  const ent: UnionExam = exams.find((u) => u.exam.id === "ent") ?? {
    exam: EXAMS.ent,
    target: 40,
    targetOwner: programs[0],
    demands: [],
  };
  const planned = [
    ...exams.filter((u) => u.exam.id === "sat"),
    ...(entPrograms.length || exams.some((u) => u.exam.id === "ent") ? [ent] : []),
  ];
  const paces = planned.map((u) => modelPace(u, ctxFor(u.exam.id as ExamId)));
  // The worst exam speaks for all; on a tie the one with a forecast says more
  const worst = [...paces].sort((a, b) => a.level - b.level || Number(!a.forecast) - Number(!b.forecast))[0];
  const next = events.find((e) => e.date >= today);

  return {
    asOf: iso(today),
    readiness: now,
    skills: { ...prep.states },
    pace: worst
      ? { level: worst.level, verdict: worst.verdict, summary: `${worst.name}: ${lowerFirst(worst.summary)}`, advice: worst.advice, exam: worst.id, mark: worst.mark, adviceActions: worst.adviceActions }
      : null,
    exams: paces,
    programs: programs.map((p) => chanceOf(p, profile, forecastScore(now))),
    alerts: alertsFor(events, conflicts, programs, { ...ctxFor("sat"), delay }),
    next: next ? { title: next.title, date: iso(next.date), daysLeft: daysBetween(today, next.date) } : undefined,
  };
}
