import katex from "katex";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { ScrollRegion } from "./scroll-region";

const MAX_INLINE_DEPTH = 16;

function tex(src: string, display: boolean) {
  return katex.renderToString(src, {
    displayMode: display,
    throwOnError: false,
    trust: false,
    output: "htmlAndMathml",
  });
}

function matchCodeSpan(text: string, start: number): { content: string; end: number } | null {
  let tickLen = 0;
  while (text[start + tickLen] === "`") tickLen++;
  if (tickLen === 0) return null;

  let j = start + tickLen;
  while (j < text.length) {
    if (text[j] === "`") {
      let closeLen = 0;
      while (text[j + closeLen] === "`") closeLen++;
      if (closeLen === tickLen) {
        let content = text.slice(start + tickLen, j);
        if (content.length >= 2 && content.startsWith(" ") && content.endsWith(" ") && content.trim().length > 0) {
          content = content.slice(1, -1);
        }
        return { content, end: j + closeLen };
      }
      j += closeLen;
    } else {
      j++;
    }
  }
  return null;
}

function matchParenMath(text: string, start: number): { expr: string; end: number } | null {
  if (!text.startsWith("\\(", start)) return null;
  let j = start + 2;
  while (j < text.length) {
    if (text.startsWith("\\)", j)) {
      const expr = text.slice(start + 2, j);
      if (expr.trim().length > 0) return { expr, end: j + 2 };
      return null;
    }
    j++;
  }
  return null;
}

function matchInlineMath(text: string, start: number): { expr: string; end: number } | null {
  if (text[start] !== "$") return null;

  if (text[start + 1] === "$") {
    let j = start + 2;
    while (j < text.length) {
      if (text[j] === "\\" && j + 1 < text.length) {
        j += 2;
        continue;
      }
      if (text.startsWith("$$", j)) {
        const expr = text.slice(start + 2, j);
        if (expr.trim().length > 0) return { expr, end: j + 2 };
        return null;
      }
      j++;
    }
    return null;
  }

  if (start + 1 >= text.length || /\s/.test(text[start + 1])) return null;

  let j = start + 1;
  while (j < text.length) {
    const ch = text[j];
    if (ch === "\n") return null;
    if (ch === "\\" && j + 1 < text.length) {
      j += 2;
      continue;
    }
    if (ch === "$") {
      if (text[j + 1] === "$") return null;
      if (j > start + 1 && !/\s/.test(text[j - 1]) && !/\d/.test(text[j + 1] ?? "")) {
        return { expr: text.slice(start + 1, j), end: j + 1 };
      }
      return null;
    }
    j++;
  }
  return null;
}

function matchLink(text: string, start: number): { label: string; url: string; end: number } | null {
  if (text[start] !== "[") return null;
  let k = start + 1;
  while (k < text.length) {
    const ch = text[k];
    if (ch === "\n" || ch === "[") return null;
    if (ch === "\\" && k + 1 < text.length) {
      k += 2;
      continue;
    }
    if (ch === "`") {
      const cs = matchCodeSpan(text, k);
      if (cs) {
        k = cs.end;
        continue;
      }
    }
    if (ch === "$") {
      const mm = matchInlineMath(text, k);
      if (mm) {
        k = mm.end;
        continue;
      }
    }
    if (ch === "]") break;
    k++;
  }
  if (text[k] !== "]" || text[k + 1] !== "(") return null;

  const label = text.slice(start + 1, k);
  if (!label) return null;

  let u = k + 2;
  while (u < text.length && text[u] !== ")" && !/\s/.test(text[u])) {
    if (text[u] === "(") return null;
    u++;
  }
  if (text[u] !== ")") return null;

  const url = text.slice(k + 2, u);
  if (!/^(?:https?:\/\/|mailto:|\/|#)/i.test(url)) return null;
  return { label, url, end: u + 1 };
}

function findClosingDelim(text: string, start: number, delim: string, wordBoundary: boolean): number {
  let k = start;
  while (k < text.length) {
    if (text[k] === "\\") {
      const pm = matchParenMath(text, k);
      if (pm) {
        k = pm.end;
        continue;
      }
      if (k + 1 < text.length) {
        k += 2;
        continue;
      }
    }
    if (text[k] === "`") {
      const cs = matchCodeSpan(text, k);
      if (cs) {
        k = cs.end;
        continue;
      }
    }
    if (text[k] === "$") {
      const mm = matchInlineMath(text, k);
      if (mm) {
        k = mm.end;
        continue;
      }
    }
    if (delim === "*" && text.startsWith("**", k) && !text.startsWith("***", k)) {
      k += 2;
      continue;
    }
    if (text.startsWith(delim, k)) {
      if (k > start && !/\s/.test(text[k - 1])) {
        if (wordBoundary && /[\p{L}\p{N}]/u.test(text[k + delim.length] ?? "")) {
          k += delim.length;
          continue;
        }
        if (delim === "*" && text.startsWith("***", k) && !text.startsWith("****", k)) {
          return k + 2;
        }
        if (delim === "**" && text.startsWith("***", k) && !text.startsWith("****", k)) {
          // If an unclosed single * is inside **...*** (e.g. **bold *italic***), let the first * close italic
          const inner = text.slice(start, k);
          let singleStars = 0;
          for (let s = 0; s < inner.length; s++) {
            if (inner[s] === "\\" && s + 1 < inner.length) {
              s++;
              continue;
            }
            if (inner[s] === "`") {
              const cs = matchCodeSpan(inner, s);
              if (cs) {
                s = cs.end - 1;
                continue;
              }
            }
            if (inner[s] === "$") {
              const mm = matchInlineMath(inner, s);
              if (mm) {
                s = mm.end - 1;
                continue;
              }
            }
            if (inner[s] === "*") {
              if (inner[s + 1] === "*") s++;
              else singleStars++;
            }
          }
          if (singleStars % 2 === 1) return k + 1;
        }
        return k;
      }
    }
    k++;
  }
  return -1;
}

function pushPlainLines(out: ReactNode[], buf: string, keyBase: string) {
  if (!buf) return;
  const lines = buf.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = i < lines.length - 1 ? lines[i].replace(/ {2,}$/, "") : lines[i];
    if (line) out.push(<span key={`${keyBase}-t-${i}`}>{line}</span>);
    if (i < lines.length - 1) out.push(<br key={`${keyBase}-br-${i}`} />);
  }
}

function parseInlineNodes(text: string, depth: number): ReactNode[] {
  const out: ReactNode[] = [];
  let buf = "";
  let i = 0;
  let seq = 0;

  const flushBuf = () => {
    if (!buf) return;
    pushPlainLines(out, buf, `p-${depth}-${seq++}`);
    buf = "";
  };

  while (i < text.length) {
    const ch = text[i];

    // Inline LaTeX \(...\) and backslash escapes
    if (ch === "\\") {
      const pm = matchParenMath(text, i);
      if (pm) {
        flushBuf();
        out.push(<span key={`m-${depth}-${seq++}`} dangerouslySetInnerHTML={{ __html: tex(pm.expr, false) }} />);
        i = pm.end;
        continue;
      }
      if (i + 1 < text.length && /[\\`*$~_|[\](){}#+\-.!]/.test(text[i + 1])) {
        buf += text[i + 1];
        i += 2;
        continue;
      }
    }

    // Inline code span `...`
    if (ch === "`") {
      const cs = matchCodeSpan(text, i);
      if (cs) {
        flushBuf();
        out.push(
          <code
            key={`c-${depth}-${seq++}`}
            className="rounded-[2px] border border-seam-soft bg-cloth-deep px-1 py-0.5 font-mono text-[0.875em] text-paper"
          >
            {cs.content}
          </code>,
        );
        i = cs.end;
        continue;
      }
    }

    // Inline math $...$ (and inline $$...$$ fallback)
    if (ch === "$") {
      const mm = matchInlineMath(text, i);
      if (mm) {
        flushBuf();
        out.push(<span key={`m-${depth}-${seq++}`} dangerouslySetInnerHTML={{ __html: tex(mm.expr, false) }} />);
        i = mm.end;
        continue;
      }
      if (text[i + 1] === "$") {
        buf += "$$";
        i += 2;
        continue;
      }
    }

    // Emphasis / bold / strikethrough / links (guarded by depth)
    if (depth < MAX_INLINE_DEPTH) {
      if (text.startsWith("***", i) && i + 3 < text.length && !/\s/.test(text[i + 3])) {
        const close = findClosingDelim(text, i + 3, "***", false);
        if (close !== -1) {
          flushBuf();
          out.push(
            <strong key={`bi-${depth}-${seq++}`} className="font-semibold text-paper">
              <em className="italic">{parseInlineNodes(text.slice(i + 3, close), depth + 1)}</em>
            </strong>,
          );
          i = close + 3;
          continue;
        }
      }

      if (text.startsWith("**", i) && i + 2 < text.length && !/\s/.test(text[i + 2])) {
        const close = findClosingDelim(text, i + 2, "**", false);
        if (close !== -1) {
          flushBuf();
          out.push(
            <strong key={`b-${depth}-${seq++}`} className="font-semibold text-paper">
              {parseInlineNodes(text.slice(i + 2, close), depth + 1)}
            </strong>,
          );
          i = close + 2;
          continue;
        }
      }

      if (
        text.startsWith("__", i) &&
        i + 2 < text.length &&
        !/\s/.test(text[i + 2]) &&
        !/[\p{L}\p{N}]/u.test(text[i - 1] ?? "")
      ) {
        const close = findClosingDelim(text, i + 2, "__", true);
        if (close !== -1) {
          flushBuf();
          out.push(
            <strong key={`bu-${depth}-${seq++}`} className="font-semibold text-paper">
              {parseInlineNodes(text.slice(i + 2, close), depth + 1)}
            </strong>,
          );
          i = close + 2;
          continue;
        }
      }

      if (text.startsWith("~~", i) && i + 2 < text.length && !/\s/.test(text[i + 2])) {
        const close = findClosingDelim(text, i + 2, "~~", false);
        if (close !== -1) {
          flushBuf();
          out.push(
            <del key={`s-${depth}-${seq++}`} className="text-paper-dim line-through">
              {parseInlineNodes(text.slice(i + 2, close), depth + 1)}
            </del>,
          );
          i = close + 2;
          continue;
        }
      }

      if (ch === "*" && text[i + 1] !== "*" && i + 1 < text.length && !/\s/.test(text[i + 1])) {
        const close = findClosingDelim(text, i + 1, "*", false);
        if (close !== -1) {
          flushBuf();
          out.push(
            <em key={`i-${depth}-${seq++}`} className="italic">
              {parseInlineNodes(text.slice(i + 1, close), depth + 1)}
            </em>,
          );
          i = close + 1;
          continue;
        }
      }

      if (
        ch === "_" &&
        text[i + 1] !== "_" &&
        i + 1 < text.length &&
        !/\s/.test(text[i + 1]) &&
        !/[\p{L}\p{N}]/u.test(text[i - 1] ?? "")
      ) {
        const close = findClosingDelim(text, i + 1, "_", true);
        if (close !== -1) {
          flushBuf();
          out.push(
            <em key={`iu-${depth}-${seq++}`} className="italic">
              {parseInlineNodes(text.slice(i + 1, close), depth + 1)}
            </em>,
          );
          i = close + 1;
          continue;
        }
      }

      if (ch === "[") {
        const link = matchLink(text, i);
        if (link) {
          flushBuf();
          const external = /^https?:\/\//i.test(link.url);
          out.push(
            <a
              key={`a-${depth}-${seq++}`}
              href={link.url}
              target={external ? "_blank" : undefined}
              rel={external ? "noopener noreferrer" : undefined}
              className="text-paper underline decoration-paper-dim/60 decoration-1 underline-offset-4 transition-colors hover:decoration-paper"
            >
              {parseInlineNodes(link.label, depth + 1)}
            </a>,
          );
          i = link.end;
          continue;
        }
      }
    }

    buf += ch;
    i++;
  }

  flushBuf();
  return out;
}

/** Строка: **жирный**, *курсив*, `код`, ~~зачёркнутый~~, ссылки и $формула$ / \(формула\) */
export function Inline({ text }: { text: string }) {
  const normalized = text.replace(/\r\n?/g, "\n");
  return <>{parseInlineNodes(normalized, 0)}</>;
}

const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*)$/;
const QUOTE_RE = /^\s{0,3}>\s?(.*)$/;
const LIST_RE = /^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$/;
const HR_RE = /^\s{0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/;

function matchFenceOpen(line: string): { char: "`" | "~"; len: number } | null {
  const m = /^\s{0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  if (!m) return null;
  const marker = m[1];
  const info = m[2];
  if (marker[0] === "`" && info.includes("`")) return null;
  return { char: marker[0] as "`" | "~", len: marker.length };
}

function isFenceClose(line: string, fence: { char: "`" | "~"; len: number }): boolean {
  const trimmed = line.trim();
  if (trimmed.length < fence.len) return false;
  for (let i = 0; i < trimmed.length; i++) {
    if (trimmed[i] !== fence.char) return false;
  }
  return true;
}

function isTableSep(line: string): boolean {
  return line.includes("|") && /^\s*\|?[\s:-]*-[\s|:-]*$/.test(line);
}

function splitTableRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) {
    let slashes = 0;
    for (let k = s.length - 2; k >= 0 && s[k] === "\\"; k--) slashes++;
    if (slashes % 2 === 0) s = s.slice(0, -1);
  }

  const cells: string[] = [];
  let cur = "";
  let i = 0;
  while (i < s.length) {
    if (s[i] === "\\" && i + 1 < s.length) {
      if (s[i + 1] === "|") {
        cur += "|";
        i += 2;
        continue;
      }
      const pm = matchParenMath(s, i);
      if (pm) {
        cur += s.slice(i, pm.end);
        i = pm.end;
        continue;
      }
      cur += s[i] + s[i + 1];
      i += 2;
      continue;
    }
    if (s[i] === "`") {
      const cs = matchCodeSpan(s, i);
      if (cs) {
        cur += s.slice(i, cs.end);
        i = cs.end;
        continue;
      }
    }
    if (s[i] === "$") {
      const mm = matchInlineMath(s, i);
      if (mm) {
        cur += s.slice(i, mm.end);
        i = mm.end;
        continue;
      }
    }
    if (s[i] === "|") {
      cells.push(cur.trim());
      cur = "";
      i++;
      continue;
    }
    cur += s[i];
    i++;
  }
  cells.push(cur.trim());
  return cells;
}

function parseTableAligns(sepLine: string): ("left" | "center" | "right" | undefined)[] {
  return splitTableRow(sepLine).map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return undefined;
  });
}

function isStandaloneDisplayMathStart(trimmed: string): boolean {
  if (trimmed.startsWith("$$")) {
    const close = trimmed.indexOf("$$", 2);
    return close === -1 || close === trimmed.length - 2;
  }
  if (trimmed.startsWith("\\[")) {
    const close = trimmed.indexOf("\\]", 2);
    return close === -1 || close === trimmed.length - 2;
  }
  return false;
}

function isBlockStart(lines: string[], i: number): boolean {
  const line = lines[i];
  const trimmed = line.trim();
  if (!trimmed) return true;
  if (matchFenceOpen(line)) return true;
  if (isStandaloneDisplayMathStart(trimmed)) return true;
  if (HR_RE.test(line)) return true;
  if (HEADING_RE.test(line)) return true;
  if (QUOTE_RE.test(line)) return true;
  if (LIST_RE.test(line)) return true;
  if (i + 1 < lines.length && line.includes("|") && isTableSep(lines[i + 1])) return true;
  return false;
}

type ListItem = { text: string; children: ReactNode[] };

function parseList(
  lines: string[],
  start: number,
  keyPrefix: string,
): { node: ReactNode; next: number } {
  const first = LIST_RE.exec(lines[start])!;
  const baseIndent = first[1].replace(/\t/g, "  ").length;
  const ordered = first[3] !== undefined;
  const startNum = ordered ? Number(first[3]) : 1;
  const items: ListItem[] = [];
  let i = start;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      // Look ahead across blank lines to see if the same list continues
      let k = i + 1;
      while (k < lines.length && !lines[k].trim()) k++;
      if (k < lines.length) {
        const nextItem = LIST_RE.exec(lines[k]);
        if (nextItem) {
          const nextIndent = nextItem[1].replace(/\t/g, "  ").length;
          const nextOrdered = nextItem[3] !== undefined;
          if (nextIndent === baseIndent && nextOrdered === ordered) {
            i = k;
            continue;
          }
        }
      }
      break;
    }

    const m = LIST_RE.exec(line);
    if (m) {
      const indent = m[1].replace(/\t/g, "  ").length;
      const itemOrdered = m[3] !== undefined;
      if (indent < baseIndent) break;
      if (indent === baseIndent) {
        if (itemOrdered !== ordered) break;
        items.push({ text: m[4], children: [] });
        i++;
        continue;
      }
      if (items.length > 0) {
        const nested = parseList(lines, i, `${keyPrefix}-${items.length}`);
        items[items.length - 1].children.push(nested.node);
        i = nested.next;
        continue;
      }
      break;
    }

    if (isBlockStart(lines, i)) break;
    if (items.length > 0) {
      items[items.length - 1].text += `\n${line.trim()}`;
      i++;
    } else {
      break;
    }
  }

  const listBody = items.map((it, idx) => (
    <li key={idx}>
      <Inline text={it.text} />
      {it.children}
    </li>
  ));

  const node = ordered ? (
    <ol
      key={keyPrefix}
      start={startNum !== 1 ? startNum : undefined}
      className="ml-5 list-decimal space-y-1"
    >
      {listBody}
    </ol>
  ) : (
    <ul key={keyPrefix} className="ml-5 list-disc space-y-1">
      {listBody}
    </ul>
  );

  return { node, next: i };
}

function renderBlocks(text: string, keyPrefix = "b"): ReactNode[] {
  const normalized = text.replace(/\r\n?/g, "\n");
  if (!normalized.trim()) return [];

  const lines = normalized.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    // 1. Fenced code block (handles unclosed fence while streaming)
    const fence = matchFenceOpen(line);
    if (fence) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !isFenceClose(lines[i], fence)) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++;
      blocks.push(
        <pre
          key={`${keyPrefix}-pre-${blocks.length}`}
          className="overflow-x-auto rounded-[2px] border border-seam-soft bg-cloth-deep p-3 font-mono text-small text-paper"
        >
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // 2. Display math $$...$$ or \[...\] (handles multi-line & unclosed streaming math)
    if (trimmed.startsWith("$$") && isStandaloneDisplayMathStart(trimmed)) {
      let expr = "";
      const closeSameLine = trimmed.indexOf("$$", 2);
      if (closeSameLine !== -1) {
        expr = trimmed.slice(2, closeSameLine).trim();
        i++;
      } else {
        const mathLines: string[] = [];
        const rest = trimmed.slice(2);
        if (rest) mathLines.push(rest);
        i++;
        while (i < lines.length) {
          const cur = lines[i];
          const curTrim = cur.trim();
          const closePos = curTrim.indexOf("$$");
          if (closePos !== -1) {
            const before = curTrim.slice(0, closePos);
            if (before) mathLines.push(before);
            i++;
            break;
          }
          mathLines.push(cur);
          i++;
        }
        expr = mathLines.join("\n").trim();
      }
      if (expr) {
        blocks.push(
          <ScrollRegion key={`${keyPrefix}-math-${blocks.length}`} className="py-1 text-paper" html={tex(expr, true)} />,
        );
      }
      continue;
    }

    if (trimmed.startsWith("\\[") && isStandaloneDisplayMathStart(trimmed)) {
      let expr = "";
      const closeSameLine = trimmed.indexOf("\\]", 2);
      if (closeSameLine !== -1) {
        expr = trimmed.slice(2, closeSameLine).trim();
        i++;
      } else {
        const mathLines: string[] = [];
        const rest = trimmed.slice(2);
        if (rest) mathLines.push(rest);
        i++;
        while (i < lines.length) {
          const cur = lines[i];
          const curTrim = cur.trim();
          const closePos = curTrim.indexOf("\\]");
          if (closePos !== -1) {
            const before = curTrim.slice(0, closePos);
            if (before) mathLines.push(before);
            i++;
            break;
          }
          mathLines.push(cur);
          i++;
        }
        expr = mathLines.join("\n").trim();
      }
      if (expr) {
        blocks.push(
          <ScrollRegion key={`${keyPrefix}-math-${blocks.length}`} className="py-1 text-paper" html={tex(expr, true)} />,
        );
      }
      continue;
    }

    // 3. Horizontal rule
    if (HR_RE.test(line)) {
      blocks.push(<hr key={`${keyPrefix}-hr-${blocks.length}`} className="border-seam-soft" />);
      i++;
      continue;
    }

    // 4. Headings
    const h = HEADING_RE.exec(line);
    if (h) {
      const level = h[1].length;
      const headingText = h[2].replace(/\s+#+\s*$/, "");
      if (level <= 2) {
        blocks.push(
          <h2
            key={`${keyPrefix}-h-${blocks.length}`}
            className="mt-3 text-[1.0625rem] font-semibold leading-snug text-paper first:mt-0"
          >
            <Inline text={headingText} />
          </h2>,
        );
      } else if (level === 3) {
        blocks.push(
          <h3
            key={`${keyPrefix}-h-${blocks.length}`}
            className="mt-2 font-semibold leading-snug text-paper first:mt-0"
          >
            <Inline text={headingText} />
          </h3>,
        );
      } else {
        blocks.push(
          <h4
            key={`${keyPrefix}-h-${blocks.length}`}
            className="mt-2 text-small font-semibold leading-snug text-paper first:mt-0"
          >
            <Inline text={headingText} />
          </h4>,
        );
      }
      i++;
      continue;
    }

    // 5. Tables (header row + separator row + body rows)
    if (i + 1 < lines.length && line.includes("|") && isTableSep(lines[i + 1])) {
      const head = splitTableRow(line);
      const aligns = parseTableAligns(lines[i + 1]);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|") && !HEADING_RE.test(lines[i])) {
        if (matchFenceOpen(lines[i]) || QUOTE_RE.test(lines[i])) break;
        rows.push(splitTableRow(lines[i]));
        i++;
      }
      blocks.push(
        <div key={`${keyPrefix}-tbl-${blocks.length}`} className="overflow-x-auto">
          <table className="w-full text-left text-small">
            <thead>
              <tr className="border-b border-seam text-paper-dim">
                {head.map((c, ci) => (
                  <th
                    key={ci}
                    style={aligns[ci] ? { textAlign: aligns[ci] } : undefined}
                    className="py-1.5 pr-4 font-medium last:pr-0"
                  >
                    <Inline text={c} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-b border-seam-soft last:border-b-0">
                  {head.map((_, ci) => (
                    <td
                      key={ci}
                      style={aligns[ci] ? { textAlign: aligns[ci] } : undefined}
                      className="num py-1.5 pr-4 last:pr-0"
                    >
                      <Inline text={r[ci] ?? ""} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // 6. Blockquotes
    if (QUOTE_RE.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length) {
        const qm = QUOTE_RE.exec(lines[i]);
        if (qm) {
          quoteLines.push(qm[1]);
          i++;
        } else if (lines[i].trim() && !isBlockStart(lines, i)) {
          quoteLines.push(lines[i]);
          i++;
        } else {
          break;
        }
      }
      blocks.push(
        <blockquote
          key={`${keyPrefix}-q-${blocks.length}`}
          className="flex flex-col gap-2 border-l-2 border-seam pl-3 text-paper-dim"
        >
          {renderBlocks(quoteLines.join("\n"), `${keyPrefix}-q-${blocks.length}`)}
        </blockquote>,
      );
      continue;
    }

    // 7. Ordered & unordered lists
    if (LIST_RE.test(line)) {
      const parsed = parseList(lines, i, `${keyPrefix}-l-${blocks.length}`);
      blocks.push(parsed.node);
      i = parsed.next;
      continue;
    }

    // 8. Paragraph: group consecutive normal text lines without crossing block starts
    const paraLines: string[] = [line];
    i++;
    while (i < lines.length && !isBlockStart(lines, i)) {
      paraLines.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={`${keyPrefix}-p-${blocks.length}`}>
        <Inline text={paraLines.join("\n")} />
      </p>,
    );
  }

  return blocks;
}

/** Блочный Markdown + KaTeX (заголовки, списки, таблицы, код, цитаты, $$…$$ и \[…\]) */
export function MarkdownBlocks({ text }: { text: string }) {
  return <>{renderBlocks(text)}</>;
}

/** Markdown-разметка с формулами KaTeX (безопасна для частичного SSE-стриминга) */
export function RichText({ text, className }: { text: string; className?: string }) {
  return <div className={cn("flex flex-col gap-3", className)}>{renderBlocks(text)}</div>;
}
