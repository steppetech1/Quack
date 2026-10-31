import type { Locale } from "@/lib/i18n/locale";

/** Content for the stub screens (screens the backend already serves but the app had none for).
 *  Will be replaced by real API data when the screens are wired. */

export type StubQuestion = { id: string; prompt: string; options: string[]; correct: number };
export type StubTask = { id: string; prompt: string; answer: string; hints: string[] };
export type StubArtifact = { id: string; kind: "note" | "cheatsheet" | "digest"; title: string; snippet: string; topicId?: string; date: string };
export type StubSummary = { title: string; turns: number; minutes: number; tasks: number; topicId: string };

export type StubData = {
  questions: StubQuestion[];
  tasks: StubTask[];
  artifacts: StubArtifact[];
  summary: StubSummary;
};

const ru: StubData = {
  questions: [
    { id: "q1", prompt: "Чему равен $\\lim_{x \\to 0} \\frac{\\sin x}{x}$?", options: ["$0$", "$1$", "$\\infty$", "Не существует"], correct: 1 },
    { id: "q2", prompt: "Ряд $\\sum \\frac{1}{n}$…", options: ["сходится", "расходится", "сходится условно"], correct: 1 },
    { id: "q3", prompt: "Если $\\lim \\left|\\frac{a_{n+1}}{a_n}\\right| = 1$, признак Даламбера…", options: ["говорит, что ряд сходится", "говорит, что ряд расходится", "ничего не говорит"], correct: 2 },
  ],
  tasks: [
    { id: "t1", prompt: "Найди радиус сходимости ряда $\\sum_{n=1}^{\\infty} \\frac{x^n}{3^n}$.", answer: "3", hints: ["Попробуй признак Коши: возьми корень $n$-й степени из модуля члена.", "$\\sqrt[n]{|x|^n / 3^n} = |x| / 3$. Когда это меньше единицы?"] },
    { id: "t2", prompt: "Найди радиус сходимости ряда $\\sum_{n=1}^{\\infty} n \\, x^n$.", answer: "1", hints: ["Отношение соседних членов: $\\frac{(n+1)}{n}|x|$.", "Предел $\\frac{n+1}{n}$ равен единице."] },
  ],
  artifacts: [
    { id: "a1", kind: "cheatsheet", title: "Радиус сходимости", snippet: "Коши — Адамар: $1/R = \\limsup \\sqrt[n]{|a_n|}$. Концы проверять отдельно.", topicId: "radius", date: "2026-10-29" },
    { id: "a2", kind: "note", title: "Где ломается Даламбер", snippet: "Если предел отношения равен 1, признак молчит. Беру Коши или сравнение.", topicId: "dalembert", date: "2026-10-28" },
    { id: "a3", kind: "digest", title: "Сессия: Лопиталь без фокусов", snippet: "Разобрали, когда Лопиталь применим. Ошибка: применял к $\\frac{1}{0}$.", topicId: "lhopital", date: "2026-10-26" },
    { id: "a4", kind: "note", title: "Тейлор до o(x³)", snippet: "$\\sin x = x - \\frac{x^3}{6} + o(x^3)$, $\\cos x = 1 - \\frac{x^2}{2} + o(x^3)$.", topicId: "taylor", date: "2026-10-24" },
  ],
  summary: { title: "Радиус сходимости", turns: 14, minutes: 27, tasks: 3, topicId: "radius" },
};

const en: StubData = {
  questions: [
    { id: "q1", prompt: "What is $\\lim_{x \\to 0} \\frac{\\sin x}{x}$?", options: ["$0$", "$1$", "$\\infty$", "It doesn't exist"], correct: 1 },
    { id: "q2", prompt: "The series $\\sum \\frac{1}{n}$…", options: ["converges", "diverges", "converges conditionally"], correct: 1 },
    { id: "q3", prompt: "If $\\lim \\left|\\frac{a_{n+1}}{a_n}\\right| = 1$, the ratio test…", options: ["says the series converges", "says the series diverges", "says nothing"], correct: 2 },
  ],
  tasks: [
    { id: "t1", prompt: "Find the radius of convergence of $\\sum_{n=1}^{\\infty} \\frac{x^n}{3^n}$.", answer: "3", hints: ["Try the root test: take the $n$-th root of the absolute value of the term.", "$\\sqrt[n]{|x|^n / 3^n} = |x| / 3$. When is that less than one?"] },
    { id: "t2", prompt: "Find the radius of convergence of $\\sum_{n=1}^{\\infty} n \\, x^n$.", answer: "1", hints: ["Ratio of neighbouring terms: $\\frac{(n+1)}{n}|x|$.", "The limit of $\\frac{n+1}{n}$ is one."] },
  ],
  artifacts: [
    { id: "a1", kind: "cheatsheet", title: "Radius of convergence", snippet: "Cauchy–Hadamard: $1/R = \\limsup \\sqrt[n]{|a_n|}$. Check the endpoints separately.", topicId: "radius", date: "2026-10-29" },
    { id: "a2", kind: "note", title: "Where the ratio test breaks", snippet: "If the ratio limit is 1, the test says nothing. Switch to the root test or comparison.", topicId: "dalembert", date: "2026-10-28" },
    { id: "a3", kind: "digest", title: "Session: L'Hôpital without tricks", snippet: "When L'Hôpital applies. Mistake: used it on $\\frac{1}{0}$.", topicId: "lhopital", date: "2026-10-26" },
    { id: "a4", kind: "note", title: "Taylor up to o(x³)", snippet: "$\\sin x = x - \\frac{x^3}{6} + o(x^3)$, $\\cos x = 1 - \\frac{x^2}{2} + o(x^3)$.", topicId: "taylor", date: "2026-10-24" },
  ],
  summary: { title: "Radius of convergence", turns: 14, minutes: 27, tasks: 3, topicId: "radius" },
};

export const getStubData = (locale: Locale): StubData => (locale === "en" ? en : ru);
