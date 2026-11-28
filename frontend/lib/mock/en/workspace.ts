import type { WorkspaceMock } from "../types";

const PDF = "application/pdf";
const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";

/** English mock content of the workspace pane: the files of the session, the demo and the notes */
export const workspace: WorkspaceMock = {
  demoLabels: {
    title: "Partial sums of the geometric series",
    sliderLabel: "x",
    readout: (x, sum, limit) => `x = ${x}: partial sum S₂₀ = ${sum}, limit = ${limit}`,
  },
  materials: {
    "mat-lecture7": {
      title: "Lecture 7 - Power series.pdf",
      mime: PDF,
      pages: [
        {
          page: 1,
          text: "Lecture 7. Power series\n\nA power series is a series of the form  Σ aₙ (x − a)ⁿ,  n = 0, 1, 2, …\nThe numbers aₙ are the coefficients, the point a is the centre. For every x the series is an ordinary number series, so the question is: for which x does it converge?",
        },
        {
          page: 2,
          text: "Abel's theorem. If the series converges at a point x₀, it converges absolutely at every x with |x − a| < |x₀ − a|.\n\nConsequence: the set where the series converges is an interval centred at a. Its half-length R is called the radius of convergence.",
        },
        {
          page: 3,
          text: "Cauchy–Hadamard formula\n\n1/R = limsup ⁿ√|aₙ|\n\nIf the limit of |aₙ₊₁ / aₙ| exists, you may use it instead: 1/R = lim |aₙ₊₁ / aₙ|. Here R = 0 means the series converges only at x = a, and R = ∞ means it converges everywhere.",
        },
        {
          page: 4,
          text: "Endpoints of the interval\n\nInside |x − a| < R the series converges absolutely, outside it diverges. At x = a ± R anything can happen: check both endpoints separately with the tests for number series (Leibniz, comparison, integral test).\n\nExample: Σ xⁿ / n has R = 1; it diverges at x = 1 (harmonic series) and converges at x = −1 (Leibniz).",
        },
      ],
    },
    "mat-slides": {
      title: "Series cheat slides.pptx",
      mime: PPTX,
      pages: [
        { page: 1, text: "Series: what to try first\n\n1. Does the general term go to zero? If not, it diverges.\n2. Is there a factorial or a power? Ratio test.\n3. Is there an n-th power? Root test." },
        { page: 2, text: "Ratio test\n\nq = lim |aₙ₊₁ / aₙ|\nq < 1: converges. q > 1: diverges. q = 1: the test says nothing." },
        { page: 3, text: "Typical slips\n\n- Mixing up R and 1/R.\n- Forgetting to check the endpoints.\n- Deciding that q = 1 means divergence." },
      ],
    },
  },
  note: {
    title: "Radius of convergence",
    text: `**Gist.** A power series converges on an interval around its centre, and the half-length of that interval is the radius $R$.

**How to find it.** Take $\\frac{1}{R} = \\limsup \\sqrt[n]{|a_n|}$, or the limit of $\\left|\\frac{a_{n+1}}{a_n}\\right|$ if it exists.

**Trap.** The endpoints $x = a \\pm R$ are not covered by the radius: check them separately.`,
  },
};
