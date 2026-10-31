import type { ChatMsg, ChatPart } from "../types";

const T = (id: string, ...parts: ChatPart[]): ChatMsg => ({ id, role: "teacher", parts });
const S = (id: string, ...parts: ChatPart[]): ChatMsg => ({ id, role: "student", parts });
const line = (block: string, id: string, label: string) => ({ ref: `line:${block}:${id}`, label });

/** Teacher conversation thread for each canvas */
export const canvasChat: Record<string, ChatMsg[]> = {
  "radius-series": [
    T("1", "You got the radius right: R = 3. Now the endpoints of the interval."),
    S("2", "Why does everything diverge at x = 5?"),
    T("3", "Plug in x = 5: you get the harmonic series, ", line("task", "l4", "your fourth line"), ". It diverges."),
    T("4", "At x = −1 the series alternates, and ", line("task", "l5", "your fifth line"), " is correct."),
  ],
  "dalembert-factorial": [
    T("1", "You simplified the ratio of neighbouring terms cleanly."),
    S("2", "Where did 1/e come from?"),
    T("3", "It's a famous limit: ", line("task", "l2", "the second line"), " reduces to (1 + 1/n)⁻ⁿ, and that tends to 1/e."),
  ],
  "dalembert-q1": [
    T("1", "Two series, both with q = 1. What did you conclude?"),
    S("2", "That both diverge."),
    T("3", "Hold on: in ", line("task", "l3", "the last line"), " the conclusion doesn't follow from q = 1."),
    T("4", "Compare: 1/n² converges, 1/n diverges. So what does the test tell you at q = 1?"),
  ],
  "lhopital-exp": [
    T("1", "You spotted the 0/0 form correctly, so L'Hôpital fits here."),
    T("2", "But in ", line("task", "l2", "your second line"), " a sign got lost: after differentiating, the numerator has +1."),
    S("3", "Isn't the derivative of −x equal to −1?"),
    T("4", "It is. Redo it, and the second round of L'Hôpital comes out clean too."),
  ],
  "taylor-exp": [
    T("1", "The expansion is right. Let's talk about the error estimate."),
    S("2", "Is the x³/6 term enough for the accuracy?"),
    T("3", "The remainder is of order x⁴/24, which at x = 0.1 is about 4·10⁻⁶. Your ", line("task", "l3", "third line"), " agrees with that."),
  ],
  "integral-sin2": [
    T("1", "The power reduction is in ", line("task", "l1", "your first line"), ", which is where you start."),
    S("2", "Is checking by differentiating really required?"),
    T("3", "Until it becomes a habit, yes: it catches lost coefficients. Your ", line("task", "l3", "third line"), " does exactly that."),
  ],
  "estimate-n2": [
    T("1", "The upper bound works, since 1/n² converges."),
    S("2", "Do I need to say the terms are positive?"),
    T("3", "Yes, the comparison test works for non-negative terms. Add that to ", line("task", "l1", "the first line"), "."),
  ],
  "limit-e": [
    T("1", "The logarithm turns the exponent into a factor. Good move."),
    S("2", "Why is ln(1 + 1/n) ~ 1/n?"),
    T("3", "Because ln(1 + t) ~ t as t → 0. You already use it in ", line("task", "l2", "the second line"), "."),
  ],
  telescoping: [
    T("1", "Splitting into a difference of fractions in ", line("task", "l1", "the first line"), " is correct."),
    S("2", "So only the first and last terms are left?"),
    T("3", "Yes: everything between them cancels, and the partial sum is 1 − 1/(N+1)."),
  ],
};

/** Stub reply to a student message: cycled through until there is a backend */
export const canvasReplies: Record<string, ChatPart[][]> = {
  "lhopital-exp": [
    ["Looking… Check the derivative of the numerator again: the sign on x."],
    ["Now ", line("task", "l3", "the third line"), " looks reasonable. What limit does it give?"],
  ],
  "radius-series": [["Looking… Don't forget to check the endpoints separately: the formula says nothing about them."]],
  "dalembert-factorial": [["Looking… Take a look at ", line("task", "l2", "the second line"), ": a famous limit lives there."]],
  "dalembert-q1": [["Looking… If q = 1 can mean both convergence and divergence, what does the test actually decide here?"]],
};
export const genericReply: ChatPart[] = ["Looking… Let's go step by step: what do you want to check in this line?"];
