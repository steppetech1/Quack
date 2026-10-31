import type { CheatSheet, Material, TopicDetails } from "../types";

const mat = (id: string, title: string, ref: string, kind: Material["kind"] = "pdf"): Material => ({ id, title, ref, kind });

const LECTURE7 = mat("m-l7", "Lecture 7. Convergence tests.pdf", "slides 12–15", "slides");
const SEM8 = mat("m-s8", "Seminar 8, problems", "problems 3–9");
const DEMIDOVICH = mat("m-dem", "Demidovich, §2", "no. 2.1–2.40", "book");

export const topicDetails: Record<string, TopicDetails> = {
  dalembert: {
    summary:
      "You look at how many times bigger the next term is than the previous one. If the limit is below one, the series converges; if it's above, it diverges. Exactly one, and the test stays silent.",
    notes: [
      {
        kind: "thm",
        text: "Let $a_n > 0$ and suppose $q = \\lim_{n\\to\\infty} \\dfrac{a_{n+1}}{a_n}$ exists. If $q < 1$, the series $\\sum a_n$ converges; if $q > 1$, it diverges.",
        src: "lecture 7, slide 12",
      },
      {
        kind: "text",
        text: "When $q = 1$ the test tells you nothing: the series $\\sum \\dfrac{1}{n}$ and $\\sum \\dfrac{1}{n^2}$ both give $q = 1$, but the first diverges and the second converges.",
        src: "lecture 7, slide 13",
      },
    ],
    when: [
      "the term has a factorial $n!$ or an exponential part $a^n$",
      "the terms are a product of growing factors, and the ratio of neighbors cancels down",
      "the term has a power like $n^k$ with no exponential: the ratio test gives $q = 1$ here, use comparison",
    ],
    example: {
      title: "The series $\\sum \\dfrac{n!}{n^n}$",
      text: "Compute the ratio:\n\n$$\\frac{a_{n+1}}{a_n} = \\frac{(n+1)!}{(n+1)^{n+1}} \\cdot \\frac{n^n}{n!} = \\left(\\frac{n}{n+1}\\right)^n = \\frac{1}{\\left(1 + \\frac1n\\right)^n} \\to \\frac1e$$\n\nThe limit is $q = 1/e < 1$, so the series converges.",
    },
    traps: [
      {
        text: "For $\\sum \\dfrac{1}{n^2}$ I got $q = 1$ and wrote \"diverges\". In fact, at $q = 1$ there's no answer: the series converges, the ratio test just can't see it.",
        date: "2026-10-28",
      },
      { text: "Forgot to check that all $a_n > 0$ before dividing.", date: "2026-10-21" },
    ],
    cards: [
      { id: "c1", kind: "definition", front: "Ratio test: the statement", back: "$a_n>0$, $q=\\lim \\dfrac{a_{n+1}}{a_n}$. If $q<1$ the series converges, if $q>1$ it diverges, if $q=1$ the question is open." },
      { id: "c2", kind: "sign", front: "The term has $n!$ and $3^n$. Which tool?", back: "The ratio test: factorials and exponential factors cancel in the ratio $a_{n+1}/a_n$." },
      { id: "c3", kind: "distinction", front: "$q=1$ in the ratio test vs. $q=1$ in a geometric series: what's the difference?", back: "In a geometric series $q$ is the common ratio, and at $q=1$ the terms don't tend to zero, so it diverges. In the ratio test $q$ is a limit, and at $q=1$ there's no conclusion." },
      { id: "c4", kind: "myth", front: "\"At $q=1$ the series diverges.\" True?", back: "No. $\\sum 1/n^2$ gives $q=1$ and converges. At $q=1$ you need another test: comparison, integral." },
    ],
    canvases: [
      { id: "k1", title: "Ratio for n!/nⁿ", date: "2026-10-28", sketch: "ratio" },
      { id: "k2", title: "Where q = 1 breaks", date: "2026-10-28", sketch: "plot" },
    ],
    materials: [LECTURE7, SEM8, DEMIDOVICH],
    moments: [
      { id: "m1", date: "2026-10-19", sessionId: "s6", title: "Lecture 7: comparison tests", kind: "first", topicId: "dalembert" },
      { id: "m2", date: "2026-10-26", title: "Practice: series with factorials", kind: "practice", topicId: "dalembert" },
      { id: "m3", date: "2026-10-28", sessionId: "s2", title: "Where the ratio test breaks", kind: "mistake", topicId: "dalembert" },
    ],
  },

  radius: {
    summary:
      "A number R such that a power series converges for |x − a| < R and diverges for |x − a| > R. The endpoints of the interval are checked separately.",
    notes: [
      {
        kind: "thm",
        text: "The Cauchy–Hadamard formula:\n\n$$R = \\frac{1}{\\limsup_{n\\to\\infty} \\sqrt[n]{|a_n|}}$$",
        src: "lecture 9, slide 4",
      },
      {
        kind: "text",
        text: "If the limit exists, it's often easier to compute $R = \\lim \\left|\\dfrac{a_n}{a_{n+1}}\\right|$.",
        src: "lecture 9, slide 6",
      },
    ],
    when: ["it's a series in powers of $(x-a)^n$ and you're asked for the region of convergence", "you need to know where a Taylor series makes sense at all"],
    example: {
      title: "The series $\\sum \\dfrac{x^n}{n}$",
      text: "$a_n = \\dfrac1n$, $R = \\lim \\dfrac{n+1}{n} = 1$. The interval is $(-1, 1)$.\n\nAt $x = 1$ we get the harmonic series, which diverges. At $x = -1$ we get the alternating harmonic series, which converges. The region of convergence is $[-1, 1)$.",
    },
    traps: [],
    cards: [],
    canvases: [],
    materials: [mat("m-l9", "Lecture 9. Power series.pdf", "slides 3–8", "slides"), DEMIDOVICH],
    moments: [{ id: "m1", date: "2026-10-29", sessionId: "s1", title: "Radius of convergence", kind: "first", topicId: "radius" }],
  },

  "num-series": {
    summary: "A series is the limit of its partial sums. It converges if that limit exists and is finite.",
    notes: [
      { kind: "def", text: "The series $\\sum_{n=1}^{\\infty} a_n$ converges if the sequence of partial sums $S_N = \\sum_{n=1}^{N} a_n$ converges. Its limit is the sum of the series.", src: "lecture 5, slide 3" },
      { kind: "thm", text: "The geometric series: $\\sum_{n=0}^{\\infty} q^n = \\dfrac{1}{1-q}$ for $|q| < 1$, and it diverges for $|q| \\ge 1$.", src: "lecture 5, slide 7" },
    ],
    when: ["you need to figure out whether an infinite sum has a finite value", "the series can be collapsed: the partial sums telescope"],
    example: {
      title: "The series $\\sum \\dfrac{1}{n(n+1)}$",
      text: "Split $\\dfrac{1}{n(n+1)} = \\dfrac1n - \\dfrac1{n+1}$. The partial sum is $S_N = 1 - \\dfrac{1}{N+1} \\to 1$. The series converges and its sum is 1.",
    },
    traps: [],
    cards: [
      { id: "c1", kind: "definition", front: "What does \"the series converges\" mean?", back: "The sequence of partial sums $S_N$ has a finite limit." },
      { id: "c2", kind: "sign", front: "The term is a difference of neighboring fractions. What do you do?", back: "Write out the partial sum: the terms cancel (the telescoping trick)." },
    ],
    canvases: [{ id: "k1", title: "Telescoping sum", date: "2026-10-12", sketch: "estimate" }],
    materials: [mat("m-l5", "Lecture 5. Numerical series.pdf", "slides 1–10", "slides"), DEMIDOVICH],
    moments: [
      { id: "m1", date: "2026-10-12", title: "Series: what \"converges\" means", kind: "first", topicId: "num-series" },
      { id: "m2", date: "2026-10-19", sessionId: "s6", title: "Lecture 7: comparison tests", kind: "repeat", topicId: "num-series" },
    ],
  },

  comparison: {
    summary: "You compare your series with a known one: if yours is no bigger than a convergent one, it converges too, and if it's no smaller than a divergent one, it diverges.",
    notes: [
      { kind: "thm", text: "Let $0 \\le a_n \\le b_n$. If $\\sum b_n$ converges, then $\\sum a_n$ converges too. If $\\sum a_n$ diverges, then $\\sum b_n$ diverges too.", src: "lecture 7, slide 3" },
      { kind: "thm", text: "The limit form: if $\\lim \\dfrac{a_n}{b_n} = c \\in (0, \\infty)$, the series converge or diverge together.", src: "lecture 7, slide 6" },
    ],
    when: ["the term looks like $\\dfrac{1}{n^p}$ for large $n$", "there's a sine, arctangent or logarithm you can bound"],
    example: {
      title: "The series $\\sum \\dfrac{1}{n^2 + 1}$",
      text: "Since $\\dfrac{1}{n^2+1} < \\dfrac{1}{n^2}$ and $\\sum \\dfrac{1}{n^2}$ converges, our series converges.",
    },
    traps: [{ text: "Compared with the harmonic series in the wrong direction: an upper bound by a divergent series proves nothing.", date: "2026-10-19" }],
    cards: [
      { id: "c1", kind: "sign", front: "The term is $\\dfrac{\\sin n}{n^2}$. What do you do?", back: "Bound the absolute value: $\\left|\\dfrac{\\sin n}{n^2}\\right| \\le \\dfrac1{n^2}$ and cite the convergence of $\\sum 1/n^2$." },
      { id: "c2", kind: "distinction", front: "The benchmark series $\\sum 1/n^p$: when does it converge?", back: "It converges for $p > 1$ and diverges for $p \\le 1$." },
      { id: "c3", kind: "myth", front: "\"$a_n \\le b_n$ and $\\sum b_n$ diverges, so $\\sum a_n$ diverges.\" True?", back: "No. The smaller series can converge. Comparison works in two directions: from above by a convergent series or from below by a divergent one." },
    ],
    canvases: [{ id: "k1", title: "Estimate of 1/(n²+1)", date: "2026-10-19", sketch: "estimate" }],
    materials: [LECTURE7, SEM8],
    moments: [
      { id: "m1", date: "2026-10-19", sessionId: "s6", title: "Lecture 7: comparison tests", kind: "first", topicId: "comparison" },
      { id: "m2", date: "2026-10-22", title: "Practice: estimates with sine", kind: "practice", topicId: "comparison" },
    ],
  },
};

export const SERIES_CHEAT: CheatSheet = {
  formulas: [
    { name: "Ratio test", tex: "q = \\lim \\dfrac{a_{n+1}}{a_n}" },
    { name: "Root test", tex: "q = \\lim \\sqrt[n]{a_n}" },
    { name: "Cauchy–Hadamard", tex: "R = \\dfrac{1}{\\limsup \\sqrt[n]{|a_n|}}" },
    { name: "Geometric series", tex: "\\sum_{n=0}^{\\infty} q^n = \\dfrac{1}{1-q},\\ |q|<1" },
  ],
  methods: [
    { sign: "the term has $n!$ or $a^n$", how: "ratio test" },
    { sign: "the term is an $n$-th power: $(\\dots)^n$", how: "root test" },
    { sign: "the term looks like $1/n^p$", how: "comparison (limit form)" },
    { sign: "the signs alternate", how: "Leibniz test, then absolute convergence" },
    { sign: "a series in powers of $x$", how: "radius first, then the endpoints separately" },
  ],
  traps: [
    "At $q = 1$ the ratio and root tests give no conclusion: use another test",
    "The divergence test condition $a_n \\to 0$ is not sufficient: $\\sum 1/n$ diverges",
    "The endpoints of the interval of convergence are checked separately",
  ],
};

export const cheatSheets: Record<string, CheatSheet> = { series: SERIES_CHEAT };
