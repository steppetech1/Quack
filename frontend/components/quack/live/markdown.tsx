"use client";

import { Play } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useT } from "@/lib/i18n/provider";
import { MarkdownBlocks } from "../rich-text";
import { DemoPanel, type OpenDemo } from "./demo-panel";

const VIZ_LINK_RE = /\[([^\]]+)\]\((?:\/api)?\/v1\/viz\/([0-9a-f-]+)\)/gi;
const VIZ_STANDALONE_LINE_RE = /^\s*(?:[-*+]\s+)?\[([^\]]+)\]\((?:\/api)?\/v1\/viz\/([0-9a-f-]+)\)\s*$/i;
const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})/;

function demoTitle(label: string): string {
  const cleaned = label.replace(/^(?:демо|demo)\s*:\s*/i, "").trim();
  return cleaned || label.trim();
}

function DemoLinkCards({ demos, onOpenDemo }: { demos: OpenDemo[]; onOpenDemo?: (demo: OpenDemo) => void }) {
  const tx = useT();
  if (!demos.length) return null;
  return (
    <div className="my-2 flex flex-col gap-2">
      {demos.map((d) => (
        <button
          key={d.requestId}
          type="button"
          onClick={() => onOpenDemo?.(d)}
          aria-label={tx.plan.demoOpen(d.title)}
          className="group flex min-h-11 w-full items-center gap-3 rounded-[2px] border border-seam bg-cloth-raised px-3.5 py-2.5 text-left transition-colors duration-(--dur-s) ease-(--ease-out) hover:border-ribbon focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon"
        >
          <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-pill border border-ribbon/50 bg-ribbon-soft text-ribbon">
            <Play aria-hidden className="size-3.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="caps block text-[0.6875rem] text-paper-dim">{tx.live.demoBlockLabel}</span>
            <span className="block truncate text-body font-medium text-paper group-hover:text-ribbon">{d.title}</span>
          </span>
          <span className="caps shrink-0 text-small text-ribbon">{tx.live.openDemoBtn}</span>
        </button>
      ))}
    </div>
  );
}

/** Small Markdown for artifact content from the backend: headings, lists, paragraphs, tables, code, $math$, **bold** and interactive demo blocks */
export function Markdown({
  text,
  className,
  onOpenDemo,
}: {
  text: string;
  className?: string;
  onOpenDemo?: (demo: OpenDemo) => void;
}) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const nodes: ReactNode[] = [];
  let mdBuf: string[] = [];
  let demoBuf: OpenDemo[] = [];
  let fence: { char: string; len: number } | null = null;
  let seq = 0;

  const flushDemos = () => {
    if (!demoBuf.length) return;
    nodes.push(<DemoLinkCards key={`d-${seq++}`} demos={demoBuf} onOpenDemo={onOpenDemo} />);
    demoBuf = [];
  };

  const flushMd = () => {
    if (!mdBuf.length) return;
    const chunk = mdBuf.join("\n");
    mdBuf = [];
    const inlineDemos: OpenDemo[] = [];
    const cleaned = onOpenDemo
      ? chunk.replace(VIZ_LINK_RE, (_m, label: string, requestId: string) => {
          const title = demoTitle(label);
          if (!inlineDemos.some((d) => d.requestId === requestId)) {
            inlineDemos.push({ requestId, title });
          }
          return title;
        })
      : chunk;
    if (cleaned.trim()) {
      nodes.push(<MarkdownBlocks key={`m-${seq++}`} text={cleaned} />);
    }
    if (inlineDemos.length) {
      nodes.push(<DemoLinkCards key={`id-${seq++}`} demos={inlineDemos} onOpenDemo={onOpenDemo} />);
    }
  };

  for (const line of lines) {
    const fm = FENCE_RE.exec(line);
    if (!fence && fm) {
      fence = { char: fm[1][0], len: fm[1].length };
      flushDemos();
      mdBuf.push(line);
      continue;
    }
    if (fence) {
      mdBuf.push(line);
      const t = line.trim();
      if (t.length >= fence.len && [...t].every((c) => c === fence!.char)) {
        fence = null;
      }
      continue;
    }

    const dm = onOpenDemo ? VIZ_STANDALONE_LINE_RE.exec(line) : null;
    if (dm) {
      flushMd();
      const requestId = dm[2];
      const title = demoTitle(dm[1]);
      if (!demoBuf.some((d) => d.requestId === requestId)) {
        demoBuf.push({ requestId, title });
      }
      continue;
    }

    if (demoBuf.length && !line.trim()) {
      continue;
    }

    flushDemos();
    mdBuf.push(line);
  }

  flushMd();
  flushDemos();

  return <div className={className ?? "space-y-2 text-body leading-relaxed text-paper"}>{nodes}</div>;
}

/** Markdown for artifact windows: opens `/v1/viz/{request_id}` links in an inline `DemoPanel` without leaving the page */
export function ArtifactMarkdown({ text, className }: { text: string; className?: string }) {
  const [demo, setDemo] = useState<OpenDemo | null>(null);

  useEffect(() => {
    if (!demo) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        setDemo(null);
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [demo]);

  return (
    <>
      <Markdown text={text} className={className} onOpenDemo={setDemo} />
      {demo && <DemoPanel demo={demo} onClose={() => setDemo(null)} />}
    </>
  );
}

/** Markdown → one line of plain text for card previews: no #, |, list markers, ** or raw demo URLs */
export function plainText(md: string, max = 280): string {
  const parts: string[] = [];
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line || /^\|?\s*:?-{3,}/.test(line)) continue;
    const withoutViz = line.replace(VIZ_LINK_RE, "$1");
    const text = withoutViz.startsWith("|")
      ? withoutViz.split("|").map((c) => c.trim()).filter(Boolean).join(" · ")
      : withoutViz.replace(/^(#{1,6}|[-*+]|\d+\.)\s+/, "");
    parts.push(text.replace(/\*\*|__|`/g, ""));
  }
  const out = parts.join(" ");
  return out.length > max ? `${out.slice(0, max - 1)}…` : out;
}
