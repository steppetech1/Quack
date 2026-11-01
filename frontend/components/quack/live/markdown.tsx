import { Inline } from "../rich-text";

/** Small Markdown for artifact content from the backend: headings, lists, paragraphs, $math$ and **bold** */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let table: string[][] = [];
  const flushTable = () => {
    if (!table.length) return;
    const [head, ...rows] = table;
    blocks.push(
      <div key={`t-${blocks.length}`} className="overflow-x-auto">
        <table className="w-full text-left text-small">
          <thead>
            <tr className="border-b border-seam text-paper-dim">
              {head.map((c, i) => (
                <th key={i} className="py-1.5 pr-4 font-medium">
                  <Inline text={c} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-seam-soft">
                {r.map((c, j) => (
                  <td key={j} className="num py-1.5 pr-4">
                    <Inline text={c} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
    table = [];
  };
  const flush = () => {
    if (!list.length) return;
    const items = list;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="ml-5 list-disc space-y-1">
        {items.map((it, i) => (
          <li key={i}>
            <Inline text={it} />
          </li>
        ))}
      </ul>,
    );
    list = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush();
      // the |---|---| row only separates the header
      if (!/^\s*\|[\s:|-]+\|\s*$/.test(line)) table.push(line.trim().slice(1, -1).split("|").map((c) => c.trim()));
      continue;
    }
    flushTable();
    const item = /^\s*[-*]\s+(.*)$/.exec(line);
    if (item) {
      list.push(item[1]);
      continue;
    }
    flush();
    if (!line.trim()) continue;
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push(
        <h3 key={blocks.length} className="mt-4 font-semibold text-paper first:mt-0">
          <Inline text={h[2]} />
        </h3>,
      );
    } else {
      blocks.push(
        <p key={blocks.length}>
          <Inline text={line} />
        </p>,
      );
    }
  }
  flush();
  flushTable();
  return <div className={className ?? "space-y-2 text-body leading-relaxed text-paper"}>{blocks}</div>;
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
