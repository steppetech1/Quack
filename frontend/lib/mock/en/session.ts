/**
 * Mock of the "Radius of convergence" session. Turn text is mini-markup:
 * paragraphs split by a blank line, **bold**, $formula$ and $$display formula$$.
 */

import type { SessionData } from "../types";

export const radiusSession: SessionData = {
  id: "radius",
  domainId: "matan",
  title: "Radius of convergence",
  goal: "Midterm on series, Nov 1",
  topics: [
    { title: "Power series", state: "review" },
    { title: "Radius of convergence", state: "new" },
    { title: "Ratio test", state: "confused" },
  ],
  turns: [
    {
      kind: "student",
      id: "t1",
      text: "Shall we keep going with power series? I stopped at the radius of convergence.",
    },
    {
      kind: "teacher",
      id: "t2",
      memory: { session: "Where the ratio test breaks", date: "2026-10-28" },
      thinking:
        "They use the ratio test confidently, but yesterday twice assumed the series diverges when q = 1. The radius comes from a similar limit, and the endpoints of the interval are exactly where the test goes silent. First I'll check whether they keep the radius apart from 1/R: I'll predict the trap, which is mixing them up.",
      text: `Let's go. First, the thing that tripped you up yesterday: at $q = 1$ the ratio test **says nothing**. The series can converge or diverge. That will come in handy today.

For a series $\\sum a_n (x - a)^n$ the radius of convergence comes from the Cauchy–Hadamard formula:

$$\\frac{1}{R} = \\limsup_{n\\to\\infty} \\sqrt[n]{|a_n|}$$

If the limit of the ratio of neighbouring coefficients exists, you can use that instead. It's the same ratio test, just applied to $a_n$:

$$\\frac{1}{R} = \\lim_{n\\to\\infty} \\left|\\frac{a_{n+1}}{a_n}\\right|$$

Inside the interval $|x - a| < R$ the series converges absolutely, outside it diverges. And the endpoints are exactly the "the test says nothing" case, so you check them separately.`,
    },
    {
      kind: "question",
      id: "q1",
      num: 1,
      prompt: "What is the radius of convergence of $\\displaystyle\\sum_{n=1}^{\\infty} \\frac{(x-2)^n}{n\\cdot 3^n}$?",
      options: [
        { id: "a", text: "$R = \\tfrac13$", plain: "R equals one third" },
        { id: "b", text: "$R = 3$", plain: "R equals three", correct: true },
        { id: "c", text: "$R = 1$, because of the $n$ in the denominator", plain: "R equals one, because of the n in the denominator" },
      ],
    },
  ],
  replies: {
    b: {
      text: `Right. $\\sqrt[n]{\\tfrac{1}{n\\cdot 3^n}} \\to \\tfrac13$ is $\\tfrac1R$, so $R = 3$ and the interval is $-1 < x < 5$. The factor $\\tfrac1n$ doesn't affect the radius: $\\sqrt[n]{n} \\to 1$.

Next, the endpoints. At $x = 5$ you get the harmonic series, at $x = -1$ an alternating one. What happens with each?`,
    },
    a: {
      text: `Close: $\\sqrt[n]{\\tfrac{1}{n\\cdot 3^n}} \\to \\tfrac13$, but that's $\\tfrac1R$, not the radius itself. So $R = 3$ and the interval is $-1 < x < 5$.

It's a common trap: the left side of the formula is $\\tfrac1R$. I'll remember it for you and bring it back in a couple of problems.`,
    },
    c: {
      text: `The factor $\\tfrac1n$ doesn't affect the radius: $\\sqrt[n]{n} \\to 1$. It's $3^n$ that decides: $\\sqrt[n]{\\tfrac{1}{3^n}} = \\tfrac13 = \\tfrac1R$, so $R = 3$ and the interval is $-1 < x < 5$.

Power factors like $n$ or $n^2$ never change the radius. Worth remembering for the midterm.`,
    },
    idk: {
      text: `No worries. Take the $n$-th root of the coefficient's absolute value: $\\sqrt[n]{\\tfrac{1}{n\\cdot 3^n}} = \\tfrac{1}{\\sqrt[n]{n}\\cdot 3} \\to \\tfrac13$. That's $\\tfrac1R$, so $R = 3$ and the interval is $-1 < x < 5$.

Let's do one more example like this before we go to the endpoints.`,
    },
  },
  thoughtLead: "You wrote: “{thought}”. ",
  plan: {
    done: [
      { id: "p1", label: "Ratio test at q = 1", anchor: "t2" },
      { id: "p1b", label: "One more example, as you asked", branch: true },
    ],
    current: { id: "p2", label: "Cauchy–Hadamard formula", anchor: "q1" },
    next: [
      { id: "p3", label: "Endpoints of the interval" },
      { id: "p4", label: "Problem from seminar no. 4" },
    ],
  },
  planAfter: {
    done: [
      { id: "p1", label: "Ratio test at q = 1", anchor: "t2" },
      { id: "p1b", label: "One more example, as you asked", branch: true },
      { id: "p2", label: "Cauchy–Hadamard formula", anchor: "q1" },
    ],
    current: { id: "p3", label: "Endpoints of the interval", anchor: "reply" },
    next: [{ id: "p4", label: "Problem from seminar no. 4" }],
  },
  progress: { label: "to the midterm", done: 2, total: 5 },
  progressAfter: { done: 3, total: 5 },
  agent: { task: "the duck is building a demo: interval of convergence" },
};
