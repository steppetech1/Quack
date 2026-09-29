// The backend catalog as the screens know it. `programs.ts` keeps a registry of Program objects and
// of the backend's realism verdicts; this file fills them from /matching and translates the profile
// the student built in the chat into the backend's questionnaire (profile paths).
//
// The backend has no climate, city size, research or exchange data, and no IELTS requirement, so
// those fields stay empty and the screens hide them (Program.remote). Prices are converted to euros
// at rough fixed rates for display only: the catalog is demo data.

import { backend, type BackendMatch, type BackendMatching, type BackendProgram } from "@/api/backend";
import { PRIORITY_KEYS, type PriorityKey, type Profile } from "./assistant";
import { catalog, formatEur, remoteEvaluations, type Evaluation, type Factor, type Level, type Program } from "./programs";

export const REMOTE = process.env.NEXT_PUBLIC_DATA_SOURCE === "remote";

const EUR_PER: Record<string, number> = { EUR: 1, USD: 0.92, KZT: 0.0017, SGD: 0.68, JPY: 0.006 };

const COUNTRY: Record<string, string> = {
  KZ: "Казахстан",
  DE: "Германия",
  NL: "Нидерланды",
  PL: "Польша",
  FR: "Франция",
  US: "США",
  SG: "Сингапур",
  JP: "Япония",
};
const CITY: Record<string, string> = {
  Almaty: "Алматы",
  Astana: "Астана",
  Shymkent: "Шымкент",
  Karaganda: "Караганда",
  Berlin: "Берлин",
  Munich: "Мюнхен",
  Hamburg: "Гамбург",
  Amsterdam: "Амстердам",
  Delft: "Делфт",
  Warsaw: "Варшава",
  Krakow: "Краков",
  Paris: "Париж",
  Lyon: "Лион",
  Boston: "Бостон",
  Seattle: "Сиэтл",
  Singapore: "Сингапур",
  Tokyo: "Токио",
};
const DIRECTION: Record<string, string> = { Mathematics: "Математика", Engineering: "Инженерия", Economics: "Экономика" };
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

/**
 * The one place the two realism scales meet. Typed over the backend's own union, so a new verdict
 * there fails the build here instead of quietly becoming `undefined` on a card (ТЗ §7.1).
 */
export const REALISM_LEVEL: Record<BackendMatch["realism"], Level> = {
  possible: "realistic",
  try: "try",
  impossible: "unlikely",
};
const STATUS: Record<string, Factor["status"]> = { below: "below", in_range: "ok", above: "ok", unknown: "unknown" };
const EXAM_NAME: Record<string, string> = { SAT_MATH: "SAT (математика)", ENT_MATH: "ЕНТ (математика)" };

/** "2027-05-15" -> "15 мая", the form the deadline parser in prepData understands */
const dayMonth = (iso: string) => {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
};

const years = (months: number | null | undefined) => {
  if (!months) return "—";
  const y = Math.round(months / 12);
  return `${y} ${y === 1 ? "год" : y < 5 ? "года" : "лет"}`;
};

/** Hundreds for real sums, tens for the tiny ones (a fictional 5000 yen is €30, not €0) */
const roundCost = (eur: number) => (eur < 1000 ? Math.round(eur / 10) * 10 : Math.round(eur / 100) * 100);

function toProgram(b: BackendProgram): Program {
  const exam = (id: string) => b.requirements.find((r) => r.type === "exam_score" && r.exam_id === id)?.threshold ?? undefined;
  const sat = exam("SAT_MATH");
  const deadline = b.deadlines.filter((d) => d.kind === "application").sort((x, y) => x.date.localeCompare(y.date))[0];
  return {
    id: b.id,
    university: b.university,
    program: DIRECTION[b.direction] ?? b.direction,
    city: CITY[b.city] ?? b.city,
    country: COUNTRY[b.country] ?? b.country,
    region: "europe",
    language: b.language,
    englishTaught: b.language === "English",
    duration: years(b.duration_months),
    costEur: roundCost((b.tuition_per_year ?? 0) * (EUR_PER[b.currency] ?? 1)),
    ieltsMin: 0,
    // Dashboard rules take the math score as satMin / 2 (the frontend thinks in the 1600 total)
    satMin: sat ? sat * 2 : undefined,
    entMin: exam("ENT_MATH"),
    deadline: deadline ? dayMonth(deadline.date) : "—",
    warm: false,
    megacity: false,
    research: "средняя",
    exchange: "",
    remote: true,
    isDemo: b.is_demo,
    extractedAuto: b.extracted_auto,
    sourceUrl: b.source_url,
    checkedAt: b.checked_at,
  };
}

function factorLabel(id: string) {
  if (id.startsWith("exam_score:")) return EXAM_NAME[id.slice(11)] ?? id.slice(11);
  if (id === "budget") return "Стоимость";
  if (id === "document") return "Документы";
  if (id === "country") return "Страна";
  if (id === "city") return "Город";
  if (id === "direction") return "Направление";
  if (id === "language" || id === "language_pref") return "Язык обучения";
  if (id === "excluded") return "Исключено";
  if (id === "required") return "Обязательное условие";
  if (id.startsWith("deadline")) return "Дедлайн";
  return id;
}

function toEvaluation(m: BackendMatch, program: Program): Evaluation {
  const threshold = (examId: string) => m.program.requirements.find((r) => r.exam_id === examId)?.threshold ?? "?";
  const factors: Factor[] = m.factors.map((f) => ({
    label: factorLabel(f.id),
    value: f.id === "budget" ? formatEur(program.costEur) : f.id.startsWith("exam_score:") ? `от ${threshold(f.id.slice(11))}` : "",
    status: STATUS[f.status] ?? "unknown",
    note: f.text,
  }));
  return {
    level: REALISM_LEVEL[m.realism],
    factors,
    fits: m.fits_text ? [m.fits_text] : [],
    misfits: [],
    score: m.score,
    realismText: m.realism_text ?? null,
    realismTextStatus: m.realism_text_status,
    softPending: m.soft_pending,
  };
}

/** Puts a matching answer into the registry; returns the program ids in the backend's order. */
export function ingest(matching: BackendMatching): string[] {
  for (const m of matching.items) {
    const program = toProgram(m.program);
    catalog.set(program.id, program);
    remoteEvaluations.set(program.id, toEvaluation(m, program));
  }
  return matching.items.map((m) => m.program.id);
}

/**
 * The backend ranks by score and treats every factor equally, so §3.3's "weights from the student's
 * priorities" has to happen here. Each priority key gets a 0..1 fit for a program; keys earlier in
 * the student's ranking count for more. Ranking and research/mobility have no signal from the
 * backend today (no university-rating or environment data in `Program`), so they stay neutral (0.5)
 * until that data exists — this does not fabricate a factor the backend cannot back with a source.
 */
function priorityFit(key: PriorityKey, id: string, maxCost: number): number {
  const ev = remoteEvaluations.get(id);
  const misses = (label: string) => (ev?.factors ?? []).some((f) => f.label === label && f.status === "below");
  switch (key) {
    case "location":
      return misses("Страна") || misses("Город") ? 0 : 1;
    case "program":
      return misses("Направление") ? 0 : 1;
    case "cost": {
      const cost = catalog.get(id)?.costEur;
      if (cost === undefined || !maxCost) return 0.5;
      return 1 - cost / maxCost;
    }
    case "realism": {
      const level = ev?.level;
      return level === "realistic" ? 1 : level === "try" ? 0.5 : level === "unlikely" ? 0 : 0.5;
    }
    default:
      return 0.5; // ranking / research / mobility: без источника от бэка
  }
}

export function prioritize(ids: string[], priorities?: PriorityKey[]): string[] {
  const order = priorities?.length === PRIORITY_KEYS.length ? priorities : PRIORITY_KEYS;
  const weightOf = (key: PriorityKey) => order.length - order.indexOf(key);
  const maxCost = Math.max(0, ...ids.map((id) => catalog.get(id)?.costEur ?? 0));
  return ids
    .map((id, i) => ({
      id,
      i,
      score: PRIORITY_KEYS.reduce((sum, key) => sum + weightOf(key) * priorityFit(key, id, maxCost), 0),
    }))
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .map((x) => x.id);
}

/** Loads the whole catalog with the student's realism verdicts; order is the backend's ranking. */
export async function loadCatalog(): Promise<{ ids: string[]; emptyReason: string | null }> {
  const matching = await backend.matching.get(50);
  return { ids: ingest(matching), emptyReason: matching.empty_reason ?? null };
}

/** Ids of the programs the student saved; their data lands in the registry too. */
export async function loadSaved(): Promise<string[]> {
  const page = await backend.saved.list();
  for (const item of page.items) {
    if (!catalog.get(item.program.id)?.remote) catalog.set(item.program.id, toProgram(item.program));
  }
  return page.items.map((i) => i.program_id);
}

/* ---------- Chat profile -> backend questionnaire ---------- */

const REGION_COUNTRIES: Record<string, string[]> = {
  "Южная, Западно-Южная Европа": ["DE", "NL", "PL", "FR"],
  Европа: ["DE", "NL", "PL", "FR"],
  США: ["US"],
  Казахстан: ["KZ"],
  Азия: ["SG", "JP"],
  Канада: ["CA"],
};
const DIRECTION_OUT: Record<string, string> = {
  Бизнес: "Economics",
  Инженерия: "Engineering",
  Медицина: "Medicine",
  "Дизайн и искусство": "Design",
};

const num = (s: string | undefined) => (s && /^\d+(?:\.\d+)?$/.test(s) ? Number(s) : undefined);

/** Dotted questionnaire path -> value, for everything the profile states in a form the backend understands. */
export function profileOps(p: Profile): [string, unknown][] {
  const ops: [string, unknown][] = [];
  const add = (path: string, value: unknown) => {
    if (value !== undefined && value !== "") ops.push([path, value]);
  };

  const grade = p.grade?.match(/\d+/)?.[0];
  add("level.grade", grade ? Number(grade) : undefined);
  add("direction.field", p.direction ? (DIRECTION_OUT[p.direction] ?? p.direction) : undefined);
  add("preferences.countries", p.location ? REGION_COUNTRIES[p.location] : undefined);

  const budget = p.budget?.match(/до\s*(\d+)/);
  if (budget) {
    add("preferences.budget_per_year", Number(budget[1]));
    add("preferences.currency", "EUR");
  }
  const grant = p.grant?.toLowerCase();
  if (grant) add("preferences.grant_need", grant.startsWith("только") ? "only_grant" : grant === "не нужен" ? "not_needed" : "preferred");
  else if (p.budget === "нужен грант") add("preferences.grant_need", "only_grant");

  const sat = num(p.sat);
  add("academics.sat_score", sat === undefined ? undefined : sat > 800 ? Math.round(sat / 2 / 10) * 10 : sat);
  add("academics.ielts_score", num(p.ielts));
  const ent = num(p.ent);
  add("academics.ent_trial_score", ent !== undefined && ent <= 50 ? ent : undefined);

  add("constraints.required", p.requiredNote ? p.requiredNote.split(",").map((s) => s.trim()).filter(Boolean) : undefined);
  add("constraints.excluded", p.excludedNote ? p.excludedNote.split(",").map((s) => s.trim()).filter(Boolean) : undefined);

  if (p.priorities.length === PRIORITY_KEYS.length && !PRIORITY_KEYS.every((k, i) => p.priorities[i] === k)) {
    add("priorities.ranking", p.priorities);
  }

  const hoursMatch = p.paceHours?.match(/(\d+(?:\.\d+)?)/);
  add("pace.hours_per_week", hoursMatch ? Number(hoursMatch[1]) : undefined);
  const DEPTH_IN: Record<string, string> = { коротко: "short", обычная: "normal", глубоко: "deep" };
  add("pace.explanation_depth", p.paceDepth ? DEPTH_IN[p.paceDepth] : undefined);
  const HINT_IN: Record<string, string> = { минимум: "minimal", обычный: "normal", щедро: "generous" };
  add("pace.hint_level", p.paceHint ? HINT_IN[p.paceHint] : undefined);

  return ops;
}

let sent = new Map<string, string>();

/** Sends only what changed since the last call; returns whether anything was sent. */
export async function syncProfile(p: Profile): Promise<boolean> {
  const changed = profileOps(p).filter(([path, value]) => sent.get(path) !== JSON.stringify(value));
  for (const [path, value] of changed) {
    await backend.profile.patch(path, value);
    sent.set(path, JSON.stringify(value));
  }
  return changed.length > 0;
}

const FIELD_PATHS: Record<string, string[]> = {
  grade: ["level.grade"],
  direction: ["direction.field"],
  location: ["preferences.countries"],
  budget: ["preferences.budget_per_year", "preferences.currency"],
  grant: ["preferences.grant_need"],
  sat: ["academics.sat_score"],
  ielts: ["academics.ielts_score"],
  ent: ["academics.ent_trial_score"],
  requiredNote: ["constraints.required"],
  excludedNote: ["constraints.excluded"],
  priorities: ["priorities.ranking"],
  paceHours: ["pace.hours_per_week"],
  paceDepth: ["pace.explanation_depth"],
  paceHint: ["pace.hint_level"],
};

/**
 * Sends only the given slots. Used for edits in the profile panel: the panel's slots are coarser than
 * the questionnaire (a region instead of countries), so a whole-profile sync would overwrite the agent's data.
 */
export async function syncFields(p: Profile, keys: string[]): Promise<boolean> {
  const paths = new Set(keys.flatMap((k) => FIELD_PATHS[k] ?? []));
  const changed = profileOps(p).filter(([path, value]) => paths.has(path) && sent.get(path) !== JSON.stringify(value));
  for (const [path, value] of changed) {
    await backend.profile.patch(path, value);
    sent.set(path, JSON.stringify(value));
  }
  return changed.length > 0;
}

/** What the server already has, so the next panel edit sends only what it changes */
export const primeSynced = (p: Profile) => {
  sent = new Map(profileOps(p).map(([path, value]) => [path, JSON.stringify(value)]));
};

/** After a reset or sign-out the next student starts from a clean slate */
export const forgetSynced = () => {
  sent = new Map();
};

/**
 * «Начать заново»: the questionnaire goes on the server too, not only in this browser.
 *
 * There is no endpoint that empties a profile, so each filled slot is cleared with the same PATCH
 * that fills it — a null value the server validates as "no answer". Only slots that actually hold
 * something are sent: an empty profile costs no requests, and the agent does not get a burst of
 * `profile.updated` events about fields nobody ever set.
 */
export async function clearProfileOnBackend(): Promise<void> {
  const profile = await backend.profile.get();
  const q = profile.questionnaire as unknown as Record<string, Record<string, { value: unknown } | undefined>>;
  const filled = Object.values(FIELD_PATHS)
    .flat()
    .filter((path) => {
      const [section, leaf] = path.split(".");
      const value = q?.[section]?.[leaf]?.value;
      return value !== null && value !== undefined;
    });
  for (const path of filled) {
    await backend.profile.patch(path, null);
  }
  forgetSynced();
}

export type CompareView = {
  /** Program ids in the order the backend answered, which is the order asked */
  ids: string[];
  rows: { param: string; values: string[]; differs: boolean; relevant: boolean }[];
  /** Parameters where every program says the same: shown folded away */
  collapsedSame: string[];
  conclusion: string | null;
  conclusionStatus: "ready" | "generating" | "stale" | "failed";
};

/**
 * The comparison as the backend makes it (§3.4). The table is arithmetic and is ready at once; the
 * takeaway under it is generated, so it can still be on its way — the table does not wait for it.
 */
export async function loadCompare(ids: string[]): Promise<CompareView | null> {
  if (!REMOTE || ids.length < 2) return null;
  try {
    const out = await backend.matching.compare(ids);
    const order = out.program_ids?.length ? out.program_ids : ids;
    return {
      ids: order,
      rows: (out.rows ?? []).map((r) => ({
        param: r.param,
        values: order.map((id) => r.values?.[id] ?? "—"),
        differs: r.differs,
        relevant: r.relevant_to_student,
      })),
      collapsedSame: out.collapsed_same ?? [],
      conclusion: out.conclusion ?? null,
      conclusionStatus: out.conclusion_status ?? "generating",
    };
  } catch (err) {
    console.warn("Failed to read the comparison:", ids, err);
    return null;
  }
}

export type SearchOutcome =
  | { status: "done"; found: string[] }
  | { status: "unavailable" }
  | { status: "timeout" };

/** 2s, 4, 8, 16, then every 30 — a search is a background job, not a request */
const SEARCH_RETRY_MS = [2_000, 4_000, 8_000, 16_000, 30_000];
/** Past this the student is told, rather than left watching */
const SEARCH_GIVE_UP_MS = 90_000;

/**
 * Looks for programs nobody has put in the catalogue yet (§1.1). Nothing is searched inside the
 * request: the POST answers 202 with an id, and the job's status is read until it settles. The id is
 * a hash of the query, so the same question from two students is one search and one budget spend.
 */
export async function searchPrograms(query: string): Promise<SearchOutcome> {
  if (!REMOTE) return { status: "unavailable" };
  const started = Date.now();
  let searchId: string;
  try {
    searchId = (await backend.programs.search(query)).search_id;
  } catch (err) {
    console.warn("Failed to start the program search:", query, err);
    return { status: "unavailable" };
  }

  for (let attempt = 0; Date.now() - started < SEARCH_GIVE_UP_MS; attempt++) {
    await new Promise((done) => setTimeout(done, SEARCH_RETRY_MS[Math.min(attempt, SEARCH_RETRY_MS.length - 1)]));
    let status;
    try {
      status = await backend.programs.searchStatus(searchId);
    } catch (err) {
      console.warn("Failed to read the search status:", searchId, err);
      return { status: "unavailable" };
    }
    if (status.status === "done") return { status: "done", found: status.found ?? [] };
    if (status.status === "unavailable") return { status: "unavailable" };
  }
  return { status: "timeout" };
}
