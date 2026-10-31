import type { Skill } from "../types";

export const skills: Skill[] = [
  {
    id: "sk1",
    name: "Proofs step by step",
    text: "Walk through proofs step by step: the idea first, then the calculations, and at the end where people usually slip.",
    bindings: [],
    command: "proofs-step-by-step",
    enabled: true,
  },
  {
    id: "sk2",
    name: "Notes like a problem book",
    text: "Write notes dry, like a problem book: definition, example, typical trap. No intros and no retelling the lecture.",
    bindings: ["notes"],
    enabled: true,
  },
  {
    id: "sk3",
    name: "Shorter flashcards",
    text: "Question in one line, answer in two at most. If it doesn't fit, split it into two cards.",
    bindings: ["cards"],
    enabled: true,
  },
  {
    id: "sk4",
    name: "One-page cheat sheet",
    text: "Only formulas and conditions of use, small print, two columns. No proofs, just a link to the notes instead.",
    bindings: ["cheat"],
    enabled: false,
  },
];
