import type { Domain, Topic } from "../types";
import { topicTexts } from "./topic-texts";

/** Deterministic "how well it sticks in memory" from id and state */
const retentionOf = (id: string, state: Topic["state"]): number => {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 1000;
  const r = h / 1000;
  if (state === "solid") return 0.85 + 0.15 * r;
  if (state === "review") return 0.35 + 0.2 * r;
  if (state === "confused") return 0.2 + 0.2 * r;
  return 0;
};

const t = (
  hubId: string | undefined,
  id: string,
  title: string,
  line: string,
  state: Topic["state"],
  requires: string[] = [],
  misconception?: string,
  related?: string[],
): Topic => ({
  id,
  hubId,
  title,
  line,
  ...topicTexts[id],
  state,
  requires,
  misconception,
  related,
  retention: retentionOf(id, state),
});

const matan: Domain = {
  id: "matan",
  title: "Calculus",
  titleDat: "calculus",
  readiness: 62,
  hubs: [
    { id: "base", num: "0", title: "Foundations", about: "This is the language the whole course is written in: sets, functions, inequalities and induction. Without it, the other chapters are hard going.", lastStudied: "2026-09-18" },
    { id: "limits", num: "I", title: "Limits", about: "A limit answers the question of what something is approaching, and it's the most important idea in the whole course. Here you learn to compute limits and to compare how fast quantities go to zero.", lastStudied: "2026-10-02" },
    { id: "cont", num: "II", title: "Continuity", about: "A continuous function is one with no gaps or jumps, and it comes with useful guarantees like the existence of a root. This is also where you sort out what discontinuities look like.", lastStudied: "2026-10-21" },
    { id: "deriv", num: "III", title: "Derivatives", about: "The derivative shows how fast a function changes, and it's what you use to analyze graphs and compute limits. Taylor's formula lives here too, approximating functions with polynomials.", lastStudied: "2026-10-26" },
    {
      id: "series",
      num: "IV",
      title: "Series",
      about: "A series is an infinite sum, and the big question is whether it adds up to a finite number. Here you learn the convergence tests and how to work with power series.",
      deadline: { label: "Midterm on series", date: "2026-11-01" },
      lastStudied: "2026-10-29",
    },
    { id: "integrals", num: "V", title: "Integrals", about: "The integral is the reverse of the derivative and a way to compute areas. Here are the main techniques: substitution, integration by parts and partial fractions.", suggested: true },
  ],
  topics: [
    t("base", "sets", "Sets and logic", "Quantifiers, set operations, negating statements", "solid"),
    t("base", "functions", "Functions and graphs", "Domain, composition, inverse function", "solid", ["sets"]),
    t("base", "ineq", "Inequalities and absolute value", "Estimates, the triangle inequality, working with |x|", "solid", ["sets"]),
    t("base", "induction", "Induction", "Proving statements for all natural n", "review", ["sets"]),
    t("base", "seq", "Sequences", "Boundedness, monotonicity, subsequences", "solid", ["functions", "ineq"]),

    t("limits", "lim-seq", "Limit of a sequence", "The ε–N definition, uniqueness of the limit", "solid", ["seq"]),
    t("limits", "lim-fn", "Limit of a function", "Cauchy and Heine definitions and why they're equivalent", "solid", ["lim-seq", "functions"]),
    t("limits", "one-sided", "One-sided limits", "Limit from the left and right when the ordinary one doesn't exist", "solid", ["lim-fn"]),
    t("limits", "remarkable", "Standard limits", "sin x / x and (1 + 1/n)ⁿ, and everything that follows from them", "review", ["lim-fn"]),
    t("limits", "infinitesimal", "Infinitesimals", "Comparing them, order of smallness, little-o notation", "solid", ["lim-fn"], undefined, ["taylor"]),
    t("limits", "equiv", "Equivalent infinitesimals", "Swapping in equivalents in products and quotients", "review", ["remarkable", "infinitesimal"], undefined, ["taylor"]),
    t(
      "limits",
      "indet",
      "Indeterminate forms",
      "0/0, ∞/∞, 1^∞ and how to resolve them",
      "confused",
      ["equiv"],
      "You think 1^∞ is always 1",
    ),

    t("cont", "continuity", "Continuity", "Three equivalent definitions and the arithmetic of continuous functions", "solid", ["lim-fn"]),
    t("cont", "discont", "Discontinuities", "Removable, jump, and discontinuities of the second kind", "review", ["continuity", "one-sided"], undefined, ["asymptotes"]),
    t("cont", "weierstrass", "Weierstrass theorems", "A function continuous on a closed interval is bounded and attains its extrema", "solid", ["continuity"], undefined, ["extrema"]),
    t("cont", "bolzano", "Intermediate value theorem", "Intermediate values and finding roots", "new", ["continuity"]),
    t("cont", "uniform", "Uniform continuity", "When δ doesn't depend on the point; the Heine–Cantor theorem", "new", ["weierstrass"]),

    t("deriv", "deriv-def", "Definition of the derivative", "Limit of the difference quotient and the tangent line", "solid", ["continuity"]),
    t("deriv", "rules", "Differentiation rules", "Sum, product, quotient and the table of derivatives", "solid", ["deriv-def"]),
    t("deriv", "chain", "Chain rule", "Derivative of a composition and of an inverse function", "solid", ["rules"]),
    t("deriv", "extrema", "Monotonicity and extrema", "Sign of the derivative and the necessary condition for an extremum", "solid", ["chain"]),
    t(
      "deriv",
      "lhopital",
      "L'Hôpital's rule",
      "Limit of a ratio via the ratio of derivatives",
      "confused",
      ["chain", "indet"],
      "You apply it without checking that it's actually 0/0 or ∞/∞",
      ["equiv"],
    ),
    t("deriv", "taylor", "Taylor's formula", "Approximation by a polynomial and the Peano remainder", "review", ["chain"]),
    t("deriv", "convex", "Convexity and inflection points", "The second derivative and the shape of the graph", "new", ["extrema"]),
    t("deriv", "asymptotes", "Asymptotes", "Vertical, horizontal and slant", "new", ["convex"]),

    t("series", "num-series", "Numerical series", "Partial sums and convergence of a series", "solid", ["lim-seq"]),
    t("series", "necessary", "Divergence test", "If a series converges, its terms tend to zero", "solid", ["num-series"]),
    t("series", "comparison", "Comparison tests", "Bounding a series by a known one: geometric, harmonic", "review", ["necessary", "equiv"]),
    t(
      "series",
      "dalembert",
      "Ratio test",
      "Convergence from the limit of the ratio of consecutive terms",
      "confused",
      ["comparison"],
      "You think the series diverges when q = 1",
      ["lim-seq"],
    ),
    t("series", "cauchy-root", "Root test", "Convergence from the limit of the n-th root of the term", "new", ["comparison"], undefined, ["remarkable"]),
    t("series", "alternating", "Alternating series", "Leibniz test, absolute and conditional convergence", "new", ["necessary"], undefined, ["induction"]),
    t("series", "power", "Power series", "Series in powers of (x − a) and the region of convergence", "review", ["dalembert", "taylor"]),
    t("series", "radius", "Radius of convergence", "The Cauchy–Hadamard formula and checking the endpoints", "new", ["power", "cauchy-root"]),

    t("integrals", "antideriv", "Antiderivatives", "The indefinite integral and the table", "new", ["rules"]),
    t("integrals", "substitution", "Substitution", "Changing variables and absorbing into the differential", "new", ["antideriv", "chain"]),
    t("integrals", "by-parts", "Integration by parts", "∫u dv = uv − ∫v du and when to use it", "new", ["antideriv"], undefined, ["taylor"]),
    t("integrals", "rational", "Partial fractions", "Decomposing rational functions and integrating the pieces", "new", ["substitution"]),

    // Topics without a chapter
    t(undefined, "hyperbolic", "Hyperbolic functions", "sinh, cosh and tanh: the exponential cousins of sin and cos", "solid", [], undefined, ["remarkable", "rules"]),
    t(undefined, "newton", "Newton's method", "Finding a root by sliding down tangent lines", "review", ["deriv-def"], undefined, ["bolzano"]),
    t(undefined, "stirling", "Stirling's formula", "How fast n! grows, in one neat approximation", "new", ["seq"], undefined, ["equiv", "num-series"]),
    t(undefined, "parametric", "Parametric curves", "Curves given as x(t), y(t) and their tangents", "new", ["functions", "chain"]),
  ],
  outside: [
    { id: "complex", title: "Complex numbers", fromDomain: "Linear algebra", linkedTo: "power" },
  ],
  sessions: [
    { id: "s1", href: "/session/radius", date: "2026-10-29", title: "Radius of convergence", topics: ["Power series", "Radius of convergence"] },
    { id: "s2", date: "2026-10-28", title: "Where the ratio test breaks", topics: ["Ratio test"] },
    { id: "s3", date: "2026-10-26", title: "L'Hôpital without the magic", topics: ["L'Hôpital's rule", "Indeterminate forms"] },
    { id: "s4", date: "2026-10-24", title: "Taylor up to o(x³)", topics: ["Taylor's formula"] },
    { id: "s5", date: "2026-10-21", title: "Discontinuities of the second kind", topics: ["Discontinuities"] },
    { id: "s6", date: "2026-10-19", title: "Lecture 7: comparison tests", topics: ["Comparison tests", "Numerical series"] },
    { id: "s7", date: "2026-10-14", title: "Why 1^∞ isn't 1", topics: ["Indeterminate forms", "Standard limits"] },
    { id: "s8", date: "2026-10-09", title: "ε–δ, explained simply", topics: ["Limit of a function"] },
  ],
  today: [
    { id: "d1", href: "/session/radius", title: "Continue power series", reason: "we stopped at the radius of convergence", minutes: 25 },
    { id: "d2", title: "Midterm prep", reason: "3 topics in \"Series\" not studied yet", minutes: 40 },
    { id: "d3", title: "Review standard limits", reason: "it's been 9 days, time for a refresher", minutes: 10 },
  ],
};

const empty = (id: string, title: string, titleDat: string): Domain => ({
  id,
  title,
  titleDat,
  hubs: [],
  topics: [],
  outside: [],
  sessions: [],
  today: [],
});

export const domains: Domain[] = [
  matan,
  empty("linal", "Linear algebra", "linear algebra"),
  empty("prob", "Probability", "probability"),
  empty("db", "Databases", "databases"),
];
