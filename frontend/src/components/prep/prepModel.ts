// State of the "Подготовка" section and the rules that change it. Pure functions, no React:
// a self-check answer is evidence, evidence moves skill state, a set is passed when all its skills are solid.

import {
  allSets,
  allSkills,
  setById,
  skillById,
  TODAY,
  type Evidence,
  type ExamId,
  type Misconception,
  type SetStatus,
  type SkillState,
  type StudySet,
  type Task,
  type TestDates,
  type DatedExam,
} from "./prepData";

import type { IconName } from "../choice/Icon";
import { REMOTE_PREP as REMOTE } from "./remoteFlag";

export type PrepTab = "overview" | "sets";

export const PREP_TABS: { tab: PrepTab; label: string; icon: IconName }[] = [
  { tab: "overview", label: "Обзор", icon: "layout-dashboard" },
  { tab: "sets", label: "Сеты", icon: "route" },
];

/**
 * Every tab holds several separate things, so each one is split further: the tab says which part of
 * preparation you are in, the sub-tab says what you are looking at. The left column shows both.
 */
export type PrepSub = "now" | "requirements" | "route" | "map";

export const PREP_SUBS: Record<PrepTab, { sub: PrepSub; label: string; icon: IconName; hint: string }[]> = {
  overview: [
    { sub: "now", label: "Сейчас", icon: "target", hint: "активный сет и его темы" },
    { sub: "requirements", label: "Требования", icon: "gauge", hint: "цели экзаменов и прогноз" },
  ],
  sets: [
    { sub: "route", label: "Маршрут", icon: "route", hint: "выбранный сет и советы ассистента" },
    { sub: "map", label: "Карта навыков", icon: "network", hint: "сеты и их темы" },
  ],
};

/** Keeps a sub-tab that belongs to another tab from leaking in; falls back to the tab's first one. */
export const subFor = (tab: PrepTab, sub: PrepSub): PrepSub =>
  PREP_SUBS[tab].some((s) => s.sub === sub) ? sub : PREP_SUBS[tab][0].sub;

export type PrepModel = {
  states: Record<string, SkillState>;
  recall: Record<string, number>;
  misconceptions: Record<string, Misconception[]>;
  evidence: Record<string, Evidence[]>;
  doneSets: string[];
  /** null while the next set is only proposed */
  currentSet: string | null;
  /** Sets put aside for another one, the latest first; their topics keep their progress */
  postponed?: string[];
  /** Days the forecast moved because of manual changes */
  extraDays: number;
  milestonesDone: string[];
  /** The test date the student picked per exam; the nearest one while nothing is picked */
  testDates?: TestDates;
  /** A target the student set by hand per exam; the highest bar of the saved programs while absent */
  targets?: Partial<Record<DatedExam, number>>;
  /** Skill states when the set in work was taken: the set report says what grew since */
  startStates?: Record<string, SkillState>;
  resolvedConflicts: Record<string, string>;
  /** Show the section on demo programs when nothing is saved */
  demo: boolean;
  /** Last set whose report hasn't been dismissed */
  reportFor: string | null;
  /** What the student asked the assistant to make for a topic: notes and flashcards */
  materials: Record<string, Material[]>;
  /** Whether the mandatory 8-question mock test for new users was completed */
  diagnosticDone?: boolean;
  /** Whether the diagnostic test was skipped with default baseline applied */
  diagnosticSkipped?: boolean;
};

/**
 * A material exists only because the student asked for it in the chat: it is generated for them, then
 * kept in the topic's «Материалы» to look through. Nothing is stored in advance, so it is not a course.
 */
export type Material =
  | { id: string; kind: "notes"; title: string; createdAt: Date; markdown: string }
  | { id: string; kind: "cards"; title: string; createdAt: Date; cards: { front: string; back: string }[] };

/** How many right answers of a topic's mock prove it; fewer leave it shaky or weak */
export const MOCK_SOLID = 5;

export function initialModel(): PrepModel {
  // At `remote` there is nothing to start from: the skills, their states and their evidence come from
  // `GET /knowledge`. Starting from the demo map would show another student's knowledge as this one's.
  const skills = allSkills();
  return {
    states: Object.fromEntries(skills.map((s) => [s.id, s.state])),
    recall: Object.fromEntries(skills.map((s) => [s.id, s.recall])),
    misconceptions: Object.fromEntries(skills.map((s) => [s.id, s.misconceptions])),
    evidence: Object.fromEntries(skills.map((s) => [s.id, s.evidence])),
    doneSets: REMOTE ? [] : ["s1", "e1"],
    currentSet: REMOTE ? null : "s2",
    extraDays: 0,
    milestonesDone: [],
    resolvedConflicts: {},
    demo: false,
    reportFor: REMOTE ? null : "s1",
    materials: {},
    diagnosticDone: false,
    diagnosticSkipped: false,
  };
}

/** Stored state keeps dates as strings (JSON); bring them back. */
export function reviveModel(raw: unknown): PrepModel | null {
  if (!raw || typeof raw !== "object") return null;
  const fresh = initialModel();
  const saved = raw as Partial<PrepModel>;
  // Per-skill records are merged, not replaced: a model saved before a skill (or a whole exam) was
  // added still gets that skill's starting state
  const model: PrepModel = {
    ...fresh,
    ...saved,
    states: { ...fresh.states, ...saved.states },
    recall: { ...fresh.recall, ...saved.recall },
    misconceptions: { ...fresh.misconceptions, ...saved.misconceptions },
    evidence: { ...fresh.evidence, ...saved.evidence },
  };
  model.evidence = Object.fromEntries(
    Object.entries(model.evidence).map(([id, list]) => [id, list.map((e) => ({ ...e, date: new Date(e.date) }))])
  );
  model.materials = Object.fromEntries(
    Object.entries(saved.materials ?? {}).map(([id, list]) => [id, list.map((m) => ({ ...m, createdAt: new Date(m.createdAt) }))])
  );
  return model;
}

export function setStatus(model: PrepModel, set: StudySet): SetStatus | "proposed" {
  if (model.doneSets.includes(set.id)) return "done";
  if (model.currentSet === set.id) return "current";
  if (!model.currentSet && proposedSet(model)?.id === set.id) return "proposed";
  return set.status === "review" ? "review" : "upcoming";
}

/**
 * Why a set is worth taking now, from what the model knows about the student: roots of errors,
 * confirmed traps, skills that do not hold. A set leaning on skills that are not there yet goes lower.
 */
export type Recommendation = { set: StudySet; score: number; reasons: string[] };

const GAP: Record<SkillState, number> = { weak: 1, lowData: 0.6, shaky: 0.5, solid: 0 };

export function rankSets(model: PrepModel, exam: ExamId): Recommendation[] {
  return allSets()
    .filter((s) => s.exam === exam && !model.doneSets.includes(s.id))
    .map((set) => {
      const reasons: string[] = [];
      let score = 0;
      for (const id of set.skills) {
        const skill = skillById(id);
        const state = model.states[id] ?? "weak";
        score += GAP[state] * (skill.weight ?? 1);
        if (state === "solid") continue;
        if (skill.root) {
          score += 30;
          reasons.push(`корень ошибок: ${skill.name}`);
        }
        const traps = (model.misconceptions[id] ?? []).filter((m) => m.status === "confirmed");
        if (traps.length) {
          score += 25;
          reasons.push(`подтверждённая ловушка: ${skill.name}`);
        } else if (state === "weak") {
          reasons.push(`не держится: ${skill.name}`);
        }
      }
      // Prerequisites outside the set that do not hold yet: the set would be built on sand
      const missing = [
        ...new Set(
          set.skills.flatMap((id) => skillById(id).requires).filter((id) => !set.skills.includes(id) && model.states[id] !== "solid")
        ),
      ];
      score -= missing.length * 12;
      if (missing.length) reasons.push(`сначала нужно: ${missing.map((id) => skillById(id).name).join(", ")}`);
      // Review comes last by design: no new skills in it
      if (set.status === "review") score -= 40;
      if (!reasons.length) {
        const open = set.skills.filter((id) => model.states[id] !== "solid").length;
        reasons.push(open ? `осталось доказать тем: ${open} из ${set.skills.length}` : "всё уже держится — повторение");
      }
      return { set, score, reasons };
    })
    .sort((a, b) => b.score - a.score);
}

/** At most this many sets are proposed at once, everywhere: «Маршрут», the map, and the backend's plan */
export const MAX_PROPOSED = 3;

/** The assistant's proposals for an exam: the strongest sets that are not in work and not put aside */
export function proposals(model: PrepModel, exam: ExamId): Recommendation[] {
  const aside = model.postponed ?? [];
  return rankSets(model, exam)
    .filter((r) => r.set.id !== model.currentSet && !aside.includes(r.set.id))
    .slice(0, MAX_PROPOSED);
}

/** The next set the system recommends: the first one in the route that isn't passed. */
export function proposedSet(model: PrepModel): StudySet | undefined {
  return allSets().find((s) => !model.doneSets.includes(s.id) && s.id !== model.currentSet);
}

export const closed = (model: PrepModel, set: StudySet) =>
  set.progress ? set.progress.topics_closed : set.skills.filter((id) => model.states[id] === "solid").length;

/** Readiness for one exam: weighted share of its solid skills, shaky counts half. */
export function readiness(model: PrepModel, exam: ExamId = "sat"): number {
  const skills = allSkills().filter((s) => s.exam === exam);
  const total = skills.reduce((sum, s) => sum + s.weight, 0);
  if (total === 0) return 0;
  const got = skills.reduce(
    (sum, s) => sum + s.weight * (model.states[s.id] === "solid" ? 1 : model.states[s.id] === "shaky" ? 0.5 : 0),
    0
  );
  return Math.round((got / total) * 100);
}

/** How the set's topics stood when it was taken: its report compares with this */
const snapshot = (model: PrepModel, id: string) =>
  Object.fromEntries(setById(id).skills.map((s) => [s, model.states[s]])) as Record<string, SkillState>;

export function acceptSet(model: PrepModel, id: string): PrepModel {
  return {
    ...model,
    currentSet: id,
    postponed: (model.postponed ?? []).filter((s) => s !== id),
    reportFor: null,
    startStates: snapshot(model, id),
  };
}

/**
 * The entrance test is optional (product-logic §4.2): without it the first set is built from what is known
 * of the student — for now the starting estimates of the demo model, later the profile on the backend.
 */
export function skipTest(model: PrepModel): PrepModel {
  const next = { ...model, diagnosticDone: true, diagnosticSkipped: true };
  if (next.currentSet) return next;
  if (REMOTE) return next;
  const top = rankSets(next, "sat")[0]?.set;
  return top ? acceptSet(next, top.id) : next;
}

/** What the passed set did, from the model alone: the numbers of the report, its words are the backend's */
export type SetReport = {
  set: StudySet;
  closed: string[];
  /** Topics that moved up since the set was taken */
  stronger: { id: string; from: SkillState; to: SkillState }[];
  /** Traps of the set's topics now «исправлено, следим» */
  fixed: { skill: string; text: string }[];
};

export function setReport(model: PrepModel): SetReport | null {
  if (!model.reportFor || model.currentSet) return null;
  const set = setById(model.reportFor);
  const rank: Record<SkillState, number> = { lowData: 0, weak: 0, shaky: 1, solid: 2 };
  const start = model.startStates ?? {};
  return {
    set,
    closed: set.skills.filter((id) => model.states[id] === "solid"),
    stronger: set.skills
      .filter((id) => start[id] && rank[model.states[id]] > rank[start[id]])
      .map((id) => ({ id, from: start[id], to: model.states[id] })),
    fixed: set.skills.flatMap((id) =>
      (model.misconceptions[id] ?? []).filter((m) => m.status === "resolved").map((m) => ({ skill: id, text: m.text }))
    ),
  };
}

/**
 * The student takes any set they like: no order is imposed, so no penalty for leaving one.
 * The set that was in work is put aside, not lost.
 */
export function makeCurrent(model: PrepModel, id: string): PrepModel {
  const doneSets = model.doneSets.filter((s) => s !== id);
  const prev = model.currentSet;
  const kept = (model.postponed ?? []).filter((s) => s !== id && s !== prev);
  const postponed = prev && prev !== id && !doneSets.includes(prev) ? [prev, ...kept] : kept;
  return { ...model, currentSet: id, doneSets, postponed, reportFor: null, startStates: snapshot(model, id) };
}

const UP: Record<SkillState, SkillState> = { weak: "shaky", lowData: "shaky", shaky: "solid", solid: "solid" };

export type AnswerResult = {
  model: PrepModel;
  correct: boolean;
  trap?: string;
  from: SkillState;
  to: SkillState;
  setPassed?: StudySet;
};

/**
 * A task answer becomes evidence; trap answers feed the misconception. A single task raises the state a
 * step; inside a mock the state waits for the whole mock (settleMock), so two lucky answers prove nothing.
 */
export function answerTask(
  model: PrepModel,
  skillId: string,
  task: Task,
  optionIndex: number,
  inMock = false
): AnswerResult {
  const option = task.options[optionIndex];
  const from = model.states[skillId];
  const to = inMock ? from : option.correct ? UP[from] : from === "solid" ? "shaky" : from;

  const evidence: Evidence = {
    source: inMock ? "мок" : "проверка",
    text: `${task.text.slice(0, 60)}… — ответ ${option.label}${option.correct ? ", верно" : option.trap ? `, ловушка: ${option.trap.toLowerCase()}` : ", неверно"}`,
    date: TODAY,
  };

  let misconceptions = model.misconceptions[skillId] ?? [];
  if (!option.correct && option.trap) {
    const existing = misconceptions.find((m) => m.text.toLowerCase().includes(option.trap!.toLowerCase().slice(0, 12)));
    if (existing) {
      misconceptions = misconceptions.map((m) =>
        m === existing ? { ...m, observations: m.observations + 1, status: m.observations + 1 >= 2 ? "confirmed" : "suspected" } : m
      );
    } else {
      misconceptions = [
        ...misconceptions,
        { id: `${skillId}-${task.id}`, text: option.trap, status: "suspected", observations: 1 },
      ];
    }
  } else if (option.correct) {
    // Getting past the trap once the skill is solid counts as "исправлено, следим"
    misconceptions = misconceptions.map((m) =>
      m.status === "confirmed" && to === "solid" && task.options.some((o) => o.trap) ? { ...m, status: "resolved" } : m
    );
  }

  const now = model.recall[skillId] ?? 0.5;
  const recall = option.correct ? Math.min(0.95, now + 0.2) : Math.max(0.2, now - 0.1);

  const next: PrepModel = {
    ...model,
    states: { ...model.states, [skillId]: to },
    recall: { ...model.recall, [skillId]: recall },
    misconceptions: { ...model.misconceptions, [skillId]: misconceptions },
    evidence: { ...model.evidence, [skillId]: [evidence, ...(model.evidence[skillId] ?? [])] },
  };

  const passed = passSets(next, skillId);
  return { model: passed.model, correct: Boolean(option.correct), trap: option.trap, from, to, setPassed: passed.set };
}

/** Any open set with this topic may be complete now — not only the current one */
function passSets(model: PrepModel, skillId: string): { model: PrepModel; set?: StudySet } {
  let next = model;
  let setPassed: StudySet | undefined;
  for (const set of allSets().filter((s) => s.skills.includes(skillId) && !next.doneSets.includes(s.id))) {
    if (closed(next, set) === set.skills.length) {
      setPassed = set;
      next = {
        ...next,
        doneSets: [...next.doneSets, set.id],
        currentSet: next.currentSet === set.id ? null : next.currentSet,
        reportFor: set.id,
      };
    }
  }
  return { model: next, set: setPassed };
}

/**
 * A finished mock decides the topic's state: MOCK_SOLID right answers or more — solid, a bit less —
 * shaky, fewer — weak. A solid topic also lets confirmed traps go to «исправлено, следим».
 */
export function settleMock(
  model: PrepModel,
  skillId: string,
  correct: number,
  total: number
): { model: PrepModel; from: SkillState; to: SkillState; setPassed?: StudySet } {
  const from = model.states[skillId];
  const need = Math.min(MOCK_SOLID, total);
  const to: SkillState = correct >= need ? "solid" : correct >= Math.ceil(need / 2) + 1 ? "shaky" : "weak";
  const known = model.misconceptions[skillId] ?? [];
  const misconceptions =
    to === "solid" ? known.map((m) => (m.status === "confirmed" ? { ...m, status: "resolved" as const } : m)) : known;
  const next: PrepModel = {
    ...model,
    states: { ...model.states, [skillId]: to },
    misconceptions: { ...model.misconceptions, [skillId]: misconceptions },
  };
  const passed = passSets(next, skillId);
  return { model: passed.model, from, to, setPassed: passed.set };
}

/** A freshly generated material goes to the top of the topic's «Материалы» */
export function addMaterial(model: PrepModel, skillId: string, material: Material): PrepModel {
  return { ...model, materials: { ...model.materials, [skillId]: [material, ...(model.materials[skillId] ?? [])] } };
}

export function removeMaterial(model: PrepModel, skillId: string, id: string): PrepModel {
  return { ...model, materials: { ...model.materials, [skillId]: (model.materials[skillId] ?? []).filter((m) => m.id !== id) } };
}

/**
 * The student picks the sitting they sit (product-logic §4.1: test date candidates, the student decides).
 * A registration ticked for another date stays with that date: the new one starts unticked.
 */
export function chooseTestDate(model: PrepModel, exam: DatedExam, key: string | null): PrepModel {
  const testDates = { ...model.testDates };
  if (key) testDates[exam] = key;
  else delete testDates[exam];
  return { ...model, testDates };
}

/** "Не согласен": the misconception leaves sets and chat context until new evidence. */
export function disputeMisconception(model: PrepModel, skillId: string, id: string): PrepModel {
  return {
    ...model,
    misconceptions: {
      ...model.misconceptions,
      [skillId]: (model.misconceptions[skillId] ?? []).map((m) => (m.id === id ? { ...m, status: "disputed" } : m)),
    },
  };
}

export const MISCONCEPTION_LABEL = (m: Misconception) =>
  m.status === "confirmed"
    ? `подтверждено, ${m.observations} набл.`
    : m.status === "suspected"
      ? `подозрение, ${m.observations} из 2`
      : m.status === "resolved"
        ? "исправлено, следим"
        : "оспорено — не учитываем";
