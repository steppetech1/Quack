import { HighlightStyle } from "@codemirror/language";
import { highlightTree, tagHighlighter, tags as t, type Tag } from "@lezer/highlight";
import { python } from "@codemirror/lang-python";
import { sql, SQLite } from "@codemirror/lang-sql";
import type { Parser } from "@lezer/common";

/**
 * One palette for the code editor and the code blocks in the chat. Colours live in app/globals.css as `--code-*`
 * variables (calm, ≥4.5:1 on the graphite, the amber accent is not used). Each token kind = one CSS class `code-<kind>`.
 */
const KINDS: { kind: string; tags: Tag[]; css: Record<string, string> }[] = [
  { kind: "kw", tags: [t.keyword, t.controlKeyword, t.definitionKeyword, t.operatorKeyword, t.moduleKeyword], css: { color: "var(--code-keyword)" } },
  { kind: "fn", tags: [t.function(t.variableName), t.function(t.definition(t.variableName)), t.function(t.propertyName), t.macroName], css: { color: "var(--code-function)" } },
  { kind: "cls", tags: [t.typeName, t.className, t.namespace], css: { color: "var(--code-class)" } },
  { kind: "def", tags: [t.definition(t.variableName), t.definition(t.propertyName)], css: { color: "var(--code-definition)" } },
  { kind: "builtin", tags: [t.standard(t.variableName), t.standard(t.name), t.self], css: { color: "var(--code-builtin)" } },
  { kind: "str", tags: [t.string, t.special(t.string), t.regexp, t.escape], css: { color: "var(--code-string)" } },
  { kind: "num", tags: [t.number, t.bool, t.null, t.atom], css: { color: "var(--code-number)" } },
  { kind: "com", tags: [t.comment, t.lineComment, t.blockComment, t.docComment], css: { color: "var(--code-comment)", fontStyle: "italic" } },
  { kind: "prop", tags: [t.propertyName, t.attributeName], css: { color: "var(--code-property)" } },
  { kind: "op", tags: [t.operator, t.punctuation, t.bracket, t.separator], css: { color: "var(--code-operator)" } },
  { kind: "var", tags: [t.variableName], css: { color: "var(--code-text)" } },
];

/** For the CodeMirror editor */
export const codeHighlightStyle = HighlightStyle.define(KINDS.flatMap((k) => k.tags.map((tag) => ({ tag, ...k.css }))));

const highlighter = tagHighlighter(KINDS.flatMap((k) => k.tags.map((tag) => ({ tag, class: `code-${k.kind}` }))));

export type CodeLang = "python" | "sql";

const ALIASES: Record<string, CodeLang> = {
  python: "python",
  py: "python",
  python3: "python",
  py3: "python",
  sql: "sql",
  sqlite: "sql",
  pgsql: "sql",
  postgres: "sql",
  postgresql: "sql",
  mysql: "sql",
};

/** Language of a fence info string (`python`, `py title="x"`, `{.sql}`); undefined = show as plain text */
export function langFromInfo(info: string): { lang?: CodeLang; label: string } {
  const word = info.trim().replace(/^\{?\.?/, "").split(/[\s{},]/)[0] ?? "";
  return { lang: ALIASES[word.toLowerCase()], label: word };
}

const parsers: Partial<Record<CodeLang, Parser>> = {};
function parserFor(lang: CodeLang): Parser {
  return (parsers[lang] ??= lang === "python" ? python().language.parser : sql({ dialect: SQLite }).language.parser);
}

export type CodeToken = { text: string; cls?: string };

const MAX_CHARS = 30000;
const cache = new Map<string, CodeToken[]>();

/** Static highlighting without an editor instance. Memoised by language+text; tolerates unfinished code (streaming) */
export function highlightCodeTokens(code: string, lang: CodeLang): CodeToken[] {
  if (code.length > MAX_CHARS) return [{ text: code }];
  const key = `${lang}\u0000${code}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const out: CodeToken[] = [];
  try {
    let pos = 0;
    highlightTree(parserFor(lang).parse(code), highlighter, (from, to, cls) => {
      if (from > pos) out.push({ text: code.slice(pos, from) });
      out.push({ text: code.slice(from, to), cls });
      pos = to;
    });
    if (pos < code.length) out.push({ text: code.slice(pos) });
  } catch {
    return [{ text: code }];
  }
  cache.set(key, out);
  if (cache.size > 120) cache.delete(cache.keys().next().value as string);
  return out;
}
