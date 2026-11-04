import type { Locale } from "@/lib/i18n/locale";
import { getMocks, MOCK_TODAY } from "@/lib/mock";
import type { DisplayCode, GraphHub, GraphModel, GraphTopic, HubLink } from "./types";
import { getVisit } from "./visit-store";

const DAY = 86_400_000;
const clean = (s: string) => s.replace(/­/g, "");
export const MOCK_NOW_MS = Date.parse(MOCK_TODAY) + 12 * 3_600_000;

/** Stable 0..1 from an id, so mocks do not change between renders */
const hash01 = (id: string) => {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 9973;
  return h / 9973;
};

type Kind = "solid" | "review" | "proposed" | "hidden" | DisplayCode;

function topicOf(id: string, name: string, kind: Kind): GraphTopic {
  const r = hash01(id);
  if (kind === "proposed") return { id, name, status: "proposed", visibility: "pale" };
  if (kind === "hidden") return { id, name, status: "hidden", visibility: "hidden" };
  const code: DisplayCode = kind === "solid" || kind === "review" ? "solved_independently" : kind;
  // solid: review is ahead; everything else is overdue
  const due = kind === "solid" ? MOCK_NOW_MS + Math.round(3 + r * 15) * DAY : MOCK_NOW_MS - Math.round(1 + r * 8) * DAY;
  return { id, name, status: "active", visibility: "shown", displayState: code, nearestDueAtMs: due };
}

/** Calculus, built from the existing presentation mock */
function matanGraph(locale: Locale): GraphModel | undefined {
  const domain = getMocks(locale).domains.find((d) => d.id === "matan");
  if (!domain) return undefined;
  const topics = domain.topics.map((t) => topicOf(t.id, clean(t.title), t.state === "solid" ? "solid" : t.state === "review" ? "review" : t.state === "confused" ? "persistent_error" : "proposed"));
  const hubs: GraphHub[] = domain.hubs.map((h) => ({
    id: h.id,
    name: clean(h.title),
    kind: h.deadline ? "deadline" : "material",
    deadlineAtMs: h.deadline ? Date.parse(h.deadline.date) : undefined,
  }));
  hubs.push({ id: "all", name: domain.title, kind: "domain_all" });
  // Asked about in calculus, but there is no subject for it: it stays here with status off_domain
  topics.push({ id: "wave", name: locale === "en" ? "Wave–particle duality" : "Корпускулярно-волновой дуализм", status: "off_domain", visibility: "shown" });
  return {
    domainId: domain.id,
    domainName: domain.title,
    topics,
    hubs,
    includes: domain.topics.filter((t) => t.hubId).map((t) => ({ hubId: t.hubId!, topicId: t.id })),
    requires: domain.topics.flatMap((t) => t.requires.map((r) => ({ topicId: t.id, requiresId: r }))),
    otherDomain: domain.outside.map((o) => ({ topic: topicOf(o.id, o.title, "solid"), domainId: "linal", domainName: o.fromDomain })),
    recentSessions: domain.sessions.map((s) => ({
      id: s.id,
      atMs: Date.parse(s.date),
      topicIds: s.topics.map((name) => domain.topics.find((t) => clean(t.title) === clean(name))?.id).filter((x): x is string => !!x),
    })),
    today: [
      { id: "d1", kind: "item", topicId: "power", action: "learn", minutes: 25, href: "/session/radius" },
      { id: "d2", kind: "item", hubId: "series", action: "practice", minutes: 40 },
      { id: "d3", kind: "item", topicId: "remarkable", action: "review", minutes: 10 },
    ],
    nowMs: MOCK_NOW_MS,
  };
}

type Row = { id: string; en: string; ru: string; requires: string[]; hubs: string[]; kind: Kind };

/**
 * Linear algebra: the awkward cases of the slice. A cycle in "needed before" (span, independence),
 * topics in two hubs, a hidden topic, a hub with a deadline, a topic with no links at all.
 */
const ROWS: Row[] = [
  { id: "vec", en: "Vectors", ru: "Векторы", requires: [], hubs: ["vectors"], kind: "solid" },
  { id: "dot", en: "Dot product", ru: "Скалярное произведение", requires: ["vec"], hubs: ["vectors"], kind: "solid" },
  { id: "mat", en: "Matrices", ru: "Матрицы", requires: ["vec"], hubs: ["vectors"], kind: "solid" },
  { id: "matmul", en: "Matrix multiplication", ru: "Умножение матриц", requires: ["mat"], hubs: ["vectors"], kind: "review" },
  { id: "orth", en: "Orthogonality", ru: "Ортогональность", requires: ["dot"], hubs: ["vectors"], kind: "solid" },
  { id: "gauss", en: "Gaussian elimination", ru: "Метод Гаусса", requires: ["mat"], hubs: ["systems"], kind: "solid" },
  { id: "det", en: "Determinant", ru: "Определитель", requires: ["matmul"], hubs: ["systems", "colloq"], kind: "persistent_error" },
  { id: "inverse", en: "Inverse matrix", ru: "Обратная матрица", requires: ["det", "gauss"], hubs: ["systems", "colloq"], kind: "review" },
  { id: "rank", en: "Rank", ru: "Ранг", requires: ["gauss"], hubs: ["systems"], kind: "proposed" },
  { id: "span", en: "Span and basis", ru: "Линейная оболочка и базис", requires: ["vec", "indep"], hubs: ["spaces"], kind: "solid" },
  { id: "indep", en: "Linear independence", ru: "Линейная независимость", requires: ["span"], hubs: ["spaces"], kind: "inconsistent" },
  { id: "subspace", en: "Subspaces", ru: "Подпространства", requires: ["vec"], hubs: ["spaces"], kind: "solid" },
  { id: "dim", en: "Dimension", ru: "Размерность", requires: ["span", "indep"], hubs: ["spaces"], kind: "in_progress" },
  { id: "lintrans", en: "Linear maps", ru: "Линейные отображения", requires: ["mat", "subspace"], hubs: ["spaces", "colloq"], kind: "review" },
  { id: "eigval", en: "Eigenvalues", ru: "Собственные значения", requires: ["det", "lintrans"], hubs: ["colloq"], kind: "struggling" },
  { id: "eigvec", en: "Eigenvectors", ru: "Собственные векторы", requires: ["eigval"], hubs: ["colloq"], kind: "proposed" },
  { id: "diag", en: "Diagonalization", ru: "Диагонализация", requires: ["eigvec", "inverse"], hubs: ["colloq"], kind: "proposed" },
  { id: "jordan", en: "Jordan form", ru: "Жорданова форма", requires: ["diag"], hubs: ["colloq"], kind: "hidden" },
  { id: "cross", en: "Cross product", ru: "Векторное произведение", requires: [], hubs: ["vectors"], kind: "solid" },
  { id: "trace", en: "Trace", ru: "След матрицы", requires: [], hubs: [], kind: "proposed" },
];

const LINAL = {
  en: { name: "Linear algebra", vectors: "Vectors and matrices", systems: "Linear systems", spaces: "Vector spaces", colloq: "Colloquium" },
  ru: { name: "Линейная алгебра", vectors: "Векторы и матрицы", systems: "Линейные системы", spaces: "Векторные пространства", colloq: "Коллоквиум" },
} as const;

function linalGraph(locale: Locale): GraphModel {
  const l = LINAL[locale];
  const hubIds = ["vectors", "systems", "spaces", "colloq"] as const;
  const hubs: GraphHub[] = [
    { id: "vectors", name: l.vectors, kind: "material" },
    { id: "systems", name: l.systems, kind: "request" },
    { id: "spaces", name: l.spaces, kind: "material" },
    { id: "colloq", name: l.colloq, kind: "deadline", deadlineAtMs: MOCK_NOW_MS + 12 * DAY },
    { id: "all", name: l.name, kind: "domain_all" },
  ];
  const includes: HubLink[] = hubIds.flatMap((h) => ROWS.filter((r) => r.hubs.includes(h)).map((r) => ({ hubId: h, topicId: r.id })));
  return {
    domainId: "linal",
    domainName: l.name,
    topics: [
      ...ROWS.map((r) => topicOf(r.id, r[locale], r.kind)),
      { id: "quantum", name: locale === "en" ? "Quantum states" : "Квантовые состояния", status: "off_domain" as const, visibility: "shown" as const },
    ],
    hubs,
    includes,
    requires: ROWS.flatMap((r) => r.requires.map((q) => ({ topicId: r.id, requiresId: q }))),
    recentSessions: [
      { id: "l1", atMs: MOCK_NOW_MS - 1 * DAY, topicIds: ["det", "matmul"] },
      { id: "l2", atMs: MOCK_NOW_MS - 3 * DAY, topicIds: ["gauss", "inverse"] },
      { id: "l3", atMs: MOCK_NOW_MS - 6 * DAY, topicIds: ["span", "indep"] },
      { id: "l4", atMs: MOCK_NOW_MS - 30 * DAY, topicIds: ["vec", "dot"] },
    ],
    today: [
      { id: "t1", kind: "item", topicId: "matmul", action: "review", minutes: 10 },
      { id: "t2", kind: "item", hubId: "colloq", action: "practice", minutes: 30 },
      { id: "t3", kind: "pace_warning", text: locale === "en" ? "3 colloquium topics are behind the pace" : "3 темы к коллоквиуму не успеваются в нужном темпе" },
    ],
    otherDomain: [{ topic: topicOf("ev", locale === "en" ? "Expected value" : "Матожидание", "review"), domainId: "prob", domainName: locale === "en" ? "Probability" : "Теория вероятностей" }],
    nowMs: MOCK_NOW_MS,
  };
}

const cache = new Map<string, GraphModel | null>();
const listeners = new Set<() => void>();

/**
 * Graph of a domain in the slice's shape. Only on mocks: with the API on, an adapter produces the same type.
 * A domain with no topics gives `undefined`. The object stays the same until the graph changes.
 */
export function getGraphModel(locale: Locale, domainId: string): GraphModel | undefined {
  const key = `${locale}:${domainId}`;
  if (!cache.has(key)) cache.set(key, domainId === "matan" ? (matanGraph(locale) ?? null) : domainId === "linal" ? linalGraph(locale) : null);
  return cache.get(key) ?? undefined;
}

export function subscribeGraph(cb: () => void) {
  listeners.add(cb);
  return () => void listeners.delete(cb);
}

/** Stands in for a change arriving from the backend: replaces the model with an edited copy and tells the views */
function editGraph(domainId: string, edit: (m: GraphModel) => GraphModel) {
  for (const key of [...cache.keys()]) {
    const m = cache.get(key);
    if (m && m.domainId === domainId) cache.set(key, edit(m));
  }
  listeners.forEach((cb) => cb());
}

declare global {
  interface Window {
    /** Development only: pokes the mock graph to watch how the view reacts to changes */
    __quackGraph?: {
      link: (domainId: string, topicId: string, requiresId: string) => void;
      /** What counts as new in this domain right now */
      visit: (domainId: string) => { baseline: boolean; topics: string[]; hubs: string[] };
    };
  }
}

/** Development only: exposes `window.__quackGraph` so a change can be fed to the view by hand */
export function installGraphDevHandle() {
  if (typeof window === "undefined" || process.env.NODE_ENV === "production") return;
  window.__quackGraph = {
    link: (domainId, topicId, requiresId) => editGraph(domainId, (m) => ({ ...m, requires: [...m.requires, { topicId, requiresId }] })),
    visit: (domainId) => {
      const v = getVisit(domainId);
      return { baseline: v.baseline, topics: [...v.topics], hubs: [...v.hubs] };
    },
  };
}
