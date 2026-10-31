import type { Locale } from "@/lib/i18n/locale";
import { slugCommand } from "@/lib/skills";
import type { SkillBinding } from "./types";

export type SkillDraft = {
  name: string;
  text: string;
  bindings: SkillBinding[];
  callable: boolean;
};

const ALL: SkillBinding[] = ["notes", "cards", "cheat"];

type Template = { re: RegExp; name: string; text: string; bindings: SkillBinding[]; callable: boolean };

/** Everything language-specific the fake "skill assistant" needs: keywords, templates, canned phrases */
type Pack = {
  notes: RegExp;
  cards: RegExp;
  cheat: RegExp;
  callable: RegExp;
  /** "in notes", "in cards" ... used in the fallback skill text */
  prep: Record<SkillBinding, string>;
  chatPrep: string;
  templates: Template[];
  fallbackName: string;
  fallbackText: (clean: string, where: string) => string;
  // refine
  named: RegExp;
  noCommand: RegExp;
  addCommand: RegExp;
  anyCommand: RegExp;
  shorter: RegExp;
  longer: RegExp;
  exampleMarker: string;
  longerAddon: string;
  extra: (t: string) => string;
};

const ru: Pack = {
  notes: /конспект/,
  cards: /карточ/,
  cheat: /шпаргалк|cheat|шпор/,
  callable: /в чате|команд|\//,
  prep: { notes: "конспектах", cards: "карточках", cheat: "cheat sheet" },
  chatPrep: "ответах в чате",
  templates: [
    {
      re: /длинн|покороч|слишком.*(много|мелк)/i,
      name: "Короткие карточки",
      text: "Вопрос в одну строку, ответ не длиннее двух. Если не влезает, режь на две карточки.",
      bindings: ["cards"],
      callable: false,
    },
    {
      re: /без воды|задачник|сухо|воды/i,
      name: "Конспект без воды",
      text: "Пиши сухо, как в задачнике: определение, пример, типовая ловушка. Без вступлений и пересказа лекции.",
      bindings: ["notes"],
      callable: false,
    },
    {
      re: /по шагам|доказательств|пошагов/i,
      name: "Разбор по шагам",
      text: "Разбирай доказательства по шагам: сначала идея, потом выкладки, в конце — где обычно ошибаются.",
      bindings: [],
      callable: true,
    },
    {
      re: /пример/i,
      name: "Больше примеров",
      text: "После каждого определения давай один короткий числовой пример и один контрпример, где условие не выполняется.",
      bindings: ["notes"],
      callable: false,
    },
    {
      re: /формул/i,
      name: "Формулы на первом месте",
      text: "Начинай с формулы, потом одной строкой — когда она работает. Объяснения словами только после неё.",
      bindings: ["cheat"],
      callable: false,
    },
  ],
  fallbackName: "Мой скилл",
  fallbackText: (clean, where) => `${clean}. Держи это правило во всех ${where}.`,
  named: /назов[иь]\s+(?:его\s+|скилл\s+)?(.+)/i,
  noCommand: /без\s*\/|убери команду|не нужна команда|без команды/,
  addCommand: /добавь команду|вызыва|в чате|команд/,
  anyCommand: /команд|\//,
  shorter: /короче|покороче|сократи/,
  longer: /длиннее|подробнее|развернуто|развёрнуто/,
  exampleMarker: "короткий пример",
  longerAddon: " Если тема сложная, добавь короткий пример.",
  extra: (t) => ` Ещё: ${t}.`,
};

const en: Pack = {
  notes: /notes?\b/,
  cards: /cards?\b|flashcards?/,
  cheat: /cheat|crib/,
  callable: /in (the )?chat|command|\//,
  prep: { notes: "notes", cards: "cards", cheat: "cheat sheets" },
  chatPrep: "chat answers",
  templates: [
    {
      re: /\blong\b|\blonger\b|shorter|too (much|small|wordy)/i,
      name: "Short cards",
      text: "Question in one line, answer no longer than two. If it doesn't fit, split it into two cards.",
      bindings: ["cards"],
      callable: false,
    },
    {
      re: /no fluff|fluff|problem book|textbook|\bdry\b|concise/i,
      name: "Notes without fluff",
      text: "Write dry, like a problem book: definition, example, typical trap. No intros, no retelling the lecture.",
      bindings: ["notes"],
      callable: false,
    },
    {
      re: /step.by.step|proofs?\b|walk me through/i,
      name: "Step-by-step breakdown",
      text: "Walk through proofs step by step: the idea first, then the calculations, and at the end where people usually slip.",
      bindings: [],
      callable: true,
    },
    {
      re: /example/i,
      name: "More examples",
      text: "After every definition, give one short numeric example and one counterexample where the condition fails.",
      bindings: ["notes"],
      callable: false,
    },
    {
      re: /formula/i,
      name: "Formulas first",
      text: "Start with the formula, then one line on when it works. Words come only after it.",
      bindings: ["cheat"],
      callable: false,
    },
  ],
  fallbackName: "My skill",
  fallbackText: (clean, where) => `${clean}. Keep this rule in all ${where}.`,
  named: /(?:name|call)\s+(?:it|the skill)\s+(.+)/i,
  noCommand: /no command|remove (the )?command|without (a )?command|don'?t need (a )?command/,
  addCommand: /add (a )?command|callable|in (the )?chat|command/,
  anyCommand: /command|\//,
  shorter: /shorter|shorten|trim|cut it/,
  longer: /longer|more detail|elaborate|expand/,
  exampleMarker: "short example",
  longerAddon: " If the topic is hard, add a short example.",
  extra: (t) => ` Also: ${t}.`,
};

const PACKS: Record<Locale, Pack> = { ru, en };

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export function detectBindings(t: string, locale: Locale): SkillBinding[] {
  const p = PACKS[locale];
  const s = t.toLowerCase();
  const out: SkillBinding[] = [];
  if (p.notes.test(s)) out.push("notes");
  if (p.cards.test(s)) out.push("cards");
  if (p.cheat.test(s)) out.push("cheat");
  return out;
}

export function detectCallable(t: string, locale: Locale): boolean {
  return PACKS[locale].callable.test(t.toLowerCase());
}

function fallbackName(t: string, p: Pack) {
  const words = t.replace(/[^\p{L}\p{N}\s-]+/gu, " ").split(/\s+/).filter(Boolean).slice(0, 4);
  return cap(words.join(" ").toLowerCase()) || p.fallbackName;
}

function fallbackText(t: string, bindings: SkillBinding[], p: Pack) {
  const clean = cap(t.trim().replace(/[.!?…\s]+$/u, ""));
  const where = bindings.length ? bindings.map((b) => p.prep[b]).join(", ") : p.chatPrep;
  return p.fallbackText(clean, where);
}

/** Is there anything in the text that tells where the skill applies */
export function hasContext(t: string, locale: Locale): boolean {
  return detectBindings(t, locale).length > 0 || detectCallable(t, locale) || PACKS[locale].templates.some((x) => x.re.test(t));
}

/** Draft from the first message. `extra` is the answer to the clarifying question */
export function buildDraft(t: string, locale: Locale, extra = ""): SkillDraft {
  const p = PACKS[locale];
  const all = `${t} ${extra}`;
  const tpl = p.templates.find((x) => x.re.test(t));
  let bindings = detectBindings(all, locale);
  let callable = detectCallable(all, locale);
  if (!bindings.length && !callable && tpl) {
    bindings = tpl.bindings;
    callable = tpl.callable;
  }
  if (!bindings.length && !callable) bindings = ALL;
  return {
    name: tpl?.name ?? fallbackName(t, p),
    text: tpl?.text ?? fallbackText(t, bindings, p),
    bindings,
    callable,
  };
}

/** Edit the draft with the next message */
export function refineDraft(d: SkillDraft, t: string, locale: Locale): SkillDraft {
  const p = PACKS[locale];
  const s = t.toLowerCase();
  let { name, text, callable } = d;
  let bindings = d.bindings;

  const named = p.named.exec(t);
  if (named) name = cap(named[1].replace(/[«»"“”.!]+/g, "").trim()) || name;

  const added = detectBindings(t, locale).filter((b) => !bindings.includes(b));
  if (added.length) bindings = [...bindings, ...added];

  if (p.noCommand.test(s)) callable = false;
  else if (p.addCommand.test(s)) callable = true;

  if (p.shorter.test(s)) {
    text = text.split(/(?<=[.!?])\s+/)[0];
  } else if (p.longer.test(s)) {
    if (!text.includes(p.exampleMarker)) text += p.longerAddon;
  } else if (!named && !added.length && !p.anyCommand.test(s)) {
    text += p.extra(t.trim().replace(/[.!?…\s]+$/u, ""));
  }
  return { name, text, bindings, callable };
}

export function draftCommand(d: SkillDraft): string | undefined {
  return d.callable ? slugCommand(d.name) || undefined : undefined;
}
