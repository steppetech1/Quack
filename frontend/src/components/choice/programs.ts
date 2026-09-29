// Demo program dataset and deterministic scoring (product-logic §3.3–3.5).
// All numbers are illustrative and flagged as "демо" in the UI.

import type { Profile } from "./assistant";

export type Level = "realistic" | "try" | "unlikely";

export const LEVEL_LABEL: Record<Level, string> = {
  realistic: "реалистично",
  try: "стоит попробовать",
  unlikely: "маловероятно",
};

export type Program = {
  id: string;
  university: string;
  program: string;
  city: string;
  country: string;
  region: "south-europe" | "europe";
  language: string;
  englishTaught: boolean;
  duration: string;
  costEur: number;
  ieltsMin: number;
  satMin?: number;
  deadline: string;
  /** Application round, where the university runs one (demo data) */
  round?: "single-choice-early" | "early-decision" | "regular";
  warm: boolean;
  megacity: boolean;
  research: "сильная" | "средняя";
  exchange: string;
  /** Came from the backend catalog: climate, city size, research and exchange are not known there */
  remote?: boolean;
  /** Minimum ENT math score, for programs that ask for it */
  entMin?: number;
  /** Demo data, said so on the card rather than passed off as checked */
  isDemo?: boolean;
  /** Pulled off the university's page by the extractor, not verified by hand */
  extractedAuto?: boolean;
  /** Where the record came from, and when it was last checked */
  sourceUrl?: string;
  checkedAt?: string;
};

export const PROGRAMS: Program[] = [
  {
    id: "valencia",
    university: "Universitat de València",
    program: "Computer Engineering",
    city: "Валенсия",
    country: "Испания",
    region: "south-europe",
    language: "испанский / английский",
    englishTaught: true,
    duration: "4 года",
    costEur: 1500,
    ieltsMin: 6.0,
    deadline: "30 июня",
    warm: true,
    megacity: false,
    research: "средняя",
    exchange: "Erasmus+, 60+ партнёров",
  },
  {
    id: "sapienza",
    university: "Sapienza University of Rome",
    program: "Applied Computer Science and AI",
    city: "Рим",
    country: "Италия",
    region: "south-europe",
    language: "английский",
    englishTaught: true,
    duration: "3 года",
    costEur: 2900,
    ieltsMin: 6.0,
    satMin: 1300,
    deadline: "30 апреля",
    warm: true,
    megacity: true,
    research: "сильная",
    exchange: "Erasmus+, 100+ партнёров",
  },
  {
    id: "bologna",
    university: "University of Bologna",
    program: "Computer Science and Engineering",
    city: "Болонья",
    country: "Италия",
    region: "south-europe",
    language: "английский",
    englishTaught: true,
    duration: "3 года",
    costEur: 2800,
    ieltsMin: 6.5,
    satMin: 1250,
    deadline: "16 декабря",
    round: "early-decision",
    warm: true,
    megacity: false,
    research: "сильная",
    exchange: "Erasmus+, 90+ партнёров",
  },
  {
    id: "porto",
    university: "University of Porto",
    program: "Informatics and Computing Engineering",
    city: "Порту",
    country: "Португалия",
    region: "south-europe",
    language: "португальский / английский",
    englishTaught: true,
    duration: "3 года",
    costEur: 3400,
    ieltsMin: 6.0,
    deadline: "20 мая",
    warm: true,
    megacity: false,
    research: "сильная",
    exchange: "Erasmus+, 50+ партнёров",
  },
  {
    id: "lisbon",
    university: "Instituto Superior Técnico",
    program: "Computer Science and Engineering",
    city: "Лиссабон",
    country: "Португалия",
    region: "south-europe",
    language: "португальский",
    englishTaught: false,
    duration: "3 года",
    costEur: 7000,
    ieltsMin: 6.5,
    deadline: "15 марта",
    warm: true,
    megacity: true,
    research: "сильная",
    exchange: "Erasmus+, 70+ партнёров",
  },
  {
    id: "eth",
    university: "ETH Zürich",
    program: "Computer Science",
    city: "Цюрих",
    country: "Швейцария",
    region: "europe",
    language: "немецкий",
    englishTaught: false,
    duration: "3 года",
    costEur: 1500,
    ieltsMin: 7.0,
    satMin: 1450,
    deadline: "15 декабря",
    round: "single-choice-early",
    warm: false,
    megacity: false,
    research: "сильная",
    exchange: "обмен с Сингапуром и США",
  },
];

/**
 * What the screens read. It starts as the demo set; with the backend on (catalog.ts) it is filled
 * from /matching, so every screen sees the same programs without knowing where they came from.
 */
export const catalog = new Map<string, Program>(PROGRAMS.map((p) => [p.id, p]));
/** The backend's verdict per program; wins over the local rules while it is present */
export const remoteEvaluations = new Map<string, Evaluation>();

/** Shown for an id the registry does not know yet (a stored card before the catalog has loaded) */
const placeholder = (id: string): Program => ({
  id,
  university: "Загрузка…",
  program: "",
  city: "",
  country: "",
  region: "europe",
  language: "",
  englishTaught: false,
  duration: "",
  costEur: 0,
  ieltsMin: 0,
  deadline: "31 декабря",
  warm: false,
  megacity: false,
  research: "средняя",
  exchange: "",
  remote: true,
});

export const programById = (id: string) => catalog.get(id) ?? placeholder(id);

export type FactorStatus = "ok" | "below" | "unknown";

export type Factor = {
  label: string;
  value: string;
  status: FactorStatus;
  note: string;
};

export type Evaluation = {
  level: Level;
  factors: Factor[];
  fits: string[];
  misfits: string[];
  score: number;
  /** Why the backend calls it realistic, in its own words; generated, so it has a status */
  realismText?: string | null;
  realismTextStatus?: "ready" | "generating" | "stale" | "failed";
  /** The soft match is computed in the background: true while it is not ready */
  softPending?: boolean;
};

const formatEur = (n: number) => `€${n.toLocaleString("ru-RU")}/год`;

/** The yearly budget from the profile; Infinity when it is not limited. */
export function budgetOf(profile: Profile): number | undefined {
  if (!profile.budget) return undefined;
  if (profile.budget.startsWith("не ограничен")) return Infinity;
  const n = profile.budget.match(/(\d[\d\s]*)/);
  return n ? Number(n[1].replace(/\s/g, "")) : undefined;
}

/** Hard factors decide the realism level; soft traits only explain "подходит тебе". */
export function evaluate(program: Program, profile: Profile): Evaluation {
  const remote = remoteEvaluations.get(program.id);
  if (remote) return remote;
  const factors: Factor[] = [];

  const ieltsGiven = profile.ielts && /^\d/.test(profile.ielts) ? Number(profile.ielts) : undefined;
  const ielts = ieltsGiven ?? 6.0;
  factors.push({
    label: "IELTS",
    value: `от ${program.ieltsMin.toFixed(1)}`,
    status: ielts >= program.ieltsMin ? "ok" : "below",
    note: ieltsGiven
      ? `у тебя ${ieltsGiven.toFixed(1)}`
      : `не сдавал — считаем как 6.0 (допущение)`,
  });

  if (program.satMin) {
    const sat = profile.sat && /^\d+$/.test(profile.sat) ? Number(profile.sat) : undefined;
    factors.push({
      label: "SAT",
      value: `от ${program.satMin}`,
      status: sat === undefined ? "unknown" : sat >= program.satMin ? "ok" : "below",
      note: sat === undefined ? "балл неизвестен" : `у тебя ${sat}`,
    });
  }

  const budget = budgetOf(profile);
  factors.push({
    label: "Стоимость",
    value: formatEur(program.costEur),
    status: budget === undefined ? "unknown" : program.costEur <= budget ? "ok" : "below",
    note:
      budget === undefined
        ? "бюджет не указан"
        : budget === Infinity
          ? "бюджет не ограничен"
          : program.costEur <= budget
            ? `в бюджете до ${budget}$`
            : `выше бюджета до ${budget}$`,
  });

  factors.push({
    label: "Язык обучения",
    value: program.language,
    status: program.englishTaught ? "ok" : "below",
    note: program.englishTaught ? "есть программа на английском" : "нужен язык страны",
  });

  const fails = factors.filter((f) => f.status === "below").length;
  const level: Level = fails === 0 ? "realistic" : fails === 1 ? "try" : "unlikely";

  const soft = profile.soft;
  const fits: string[] = [];
  const misfits: string[] = [];
  if (soft.includes("Любит теплые климаты")) (program.warm ? fits : misfits).push(program.warm ? "тёплый климат" : "прохладный климат");
  if (soft.includes("не любит мегаполисы") || soft.includes("предпочитает небольшие города")) {
    (program.megacity ? misfits : fits).push(program.megacity ? "большой город" : "не мегаполис");
  }
  if (soft.includes("хочет заниматься наукой") || profile.strong) {
    if (program.research === "сильная") fits.push("сильная исследовательская среда");
  }
  if (soft.includes("важен обмен")) fits.push(`обмен: ${program.exchange}`);

  const levelRank = { realistic: 2, try: 1, unlikely: 0 }[level];
  return { level, factors, fits, misfits, score: levelRank * 10 + fits.length - misfits.length };
}

/** Programs matching the profile's location, best first. */
export function recommend(profile: Profile): string[] {
  const pool = PROGRAMS.filter((p) =>
    profile.location?.startsWith("Южная") ? p.region === "south-europe" : true
  );
  return pool
    .map((p) => ({ id: p.id, score: evaluate(p, profile).score }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 5)
    .map((p) => p.id);
}

export type CompareRow = { label: string; values: string[]; differs: boolean; relevant: boolean };

export function compareRows(ids: string[], profile: Profile): CompareRow[] {
  const programs = ids.map(programById);
  const evals = programs.map((p) => evaluate(p, profile));
  const factor = (e: Evaluation, label: string) => e.factors.find((f) => f.label === label);

  const remote = programs.every((p) => p.remote);
  const rows: Omit<CompareRow, "differs">[] = [
    { label: "Реалистичность", values: evals.map((e) => LEVEL_LABEL[e.level]), relevant: true },
    { label: "Город", values: programs.map((p) => `${p.city}, ${p.country}`), relevant: false },
    {
      label: "Стоимость",
      values: programs.map((p, i) => `${formatEur(p.costEur)} · ${factor(evals[i], "Стоимость")!.note}`),
      relevant: true,
    },
    { label: "IELTS", values: programs.map((p) => (p.ieltsMin ? `от ${p.ieltsMin.toFixed(1)}` : "не требуется")), relevant: true },
    { label: "SAT", values: programs.map((p) => (p.satMin ? `от ${p.satMin}` : "не требуется")), relevant: true },
    { label: "ЕНТ", values: programs.map((p) => (p.entMin ? `от ${p.entMin}` : "не требуется")), relevant: true },
    { label: "Язык", values: programs.map((p) => p.language), relevant: true },
    { label: "Подача до", values: programs.map((p) => p.deadline), relevant: true },
    { label: "Климат", values: programs.map((p) => (p.warm ? "тёплый" : "прохладный")), relevant: true },
    { label: "Размер города", values: programs.map((p) => (p.megacity ? "мегаполис" : "небольшой")), relevant: true },
    { label: "Наука", values: programs.map((p) => p.research), relevant: false },
    { label: "Обмен", values: programs.map((p) => p.exchange), relevant: false },
    { label: "Длительность", values: programs.map((p) => p.duration), relevant: false },
  ];

  // The backend catalog has no climate, city size, research or exchange data: do not invent it
  const unknown = new Set(["Климат", "Размер города", "Наука", "Обмен"]);
  return rows
    .filter((r) => !(remote && unknown.has(r.label)))
    .filter((r) => !(r.label === "ЕНТ" && programs.every((p) => !p.entMin)))
    .map((r) => ({ ...r, differs: new Set(r.values).size > 1 }));
}

export function compareSummary(rows: CompareRow[]): { same: string[]; keyDifferences: string[] } {
  // Keep acronyms like IELTS / SAT in capitals
  const name = (label: string) => (label === label.toUpperCase() ? label : label.toLowerCase());
  return {
    same: rows.filter((r) => !r.differs).map((r) => name(r.label)),
    keyDifferences: rows.filter((r) => r.differs && r.relevant).map((r) => name(r.label)),
  };
}

export { formatEur };
