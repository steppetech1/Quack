import { MarkdownBlocks } from "../rich-text";

/** Small Markdown for artifact content from the backend: headings, lists, paragraphs, tables, code, $math$ and **bold** */
export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className ?? "space-y-2 text-body leading-relaxed text-paper"}>
      <MarkdownBlocks text={text} />
    </div>
  );
}

/** Markdown → one line of plain text for card previews: no #, |, list markers or ** */
export function plainText(md: string, max = 280): string {
  const parts: string[] = [];
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line || /^\|?\s*:?-{3,}/.test(line)) continue;
    const text = line.startsWith("|")
      ? line.split("|").map((c) => c.trim()).filter(Boolean).join(" · ")
      : line.replace(/^(#{1,6}|[-*+]|\d+\.)\s+/, "");
    parts.push(text.replace(/\*\*|__|`/g, ""));
  }
  const out = parts.join(" ");
  return out.length > max ? `${out.slice(0, max - 1)}…` : out;
}
