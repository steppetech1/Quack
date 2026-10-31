"use client";

import { Check, ChevronDown, PenLine, Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import type { Canvas } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { CanvasThumb } from "./sketch";

const clean = (s: string) => s.replace(/­/g, "");
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const FILTER_PILL =
  "inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full border px-4 text-small whitespace-nowrap transition-colors";

/** Подсветка совпадения запроса в строке */
function Hl({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRe(q)})`, "gi"));
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-[4px] bg-ribbon/25 px-0.5 text-paper">
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function firstTaskBlock(c: Canvas) {
  return c.blocks.find((b) => b.kind === "task") ?? c.blocks[0];
}

function TopicMenu({
  topics,
  value,
  onChange,
}: {
  topics: { id: string; title: string }[];
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  const tx = useT();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const listId = useId();
  const current = topics.find((t) => t.id === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={box} className="relative shrink-0">
      <button
        type="button"
        aria-pressed={!!current}
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          FILTER_PILL,
          current ? "border-paper-dim bg-cloth-hover text-paper" : "border-seam text-paper-dim hover:border-rule hover:text-paper",
        )}
      >
        <span className="max-w-44 truncate">{current ? current.title : tx.canvases.topicAny}</span>
        <ChevronDown aria-hidden className={cn("size-4 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul
          id={listId}
          className="absolute top-12 left-0 z-30 max-h-72 w-72 overflow-y-auto rounded-[16px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
        >
          {[{ id: null, title: tx.canvases.anyTopic }, ...topics].map((t) => (
            <li key={t.id ?? "all"}>
              <button
                type="button"
                aria-pressed={t.id === value}
                onClick={() => {
                  onChange(t.id);
                  setOpen(false);
                }}
                className="flex min-h-10 w-full items-center gap-2 rounded-option px-3 py-2 text-left transition-colors hover:bg-cloth-hover"
              >
                <span className="flex-1">{t.title}</span>
                {t.id === value && <Check aria-hidden className="size-4 text-paper-dim" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Поле поиска по канвасам: живёт в шапке вида домена, запрос держит родитель */
export function CanvasSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const tx = useT();
  return (
    <div className="relative w-full sm:w-52 xl:w-64">
      <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-paper-dim" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={tx.canvases.search}
        placeholder={tx.canvases.search}
        className="h-10 w-full rounded-control border border-seam bg-cloth-deep pr-10 pl-9 text-body text-paper transition-colors hover:border-rule focus-visible:border-ribbon/70 [&::-webkit-search-cancel-button]:hidden"
      />
      {value && (
        <button
          type="button"
          aria-label={tx.canvases.clearSearch}
          onClick={() => onChange("")}
          className="absolute top-0 right-0 inline-flex size-10 items-center justify-center rounded-control text-paper-dim hover:text-paper"
        >
          <X aria-hidden className="size-4" />
        </button>
      )}
    </div>
  );
}

export function CanvasesList({ query, onResetQuery }: { query: string; onResetQuery: () => void }) {
  const tx = useT();
  const { dayLabel } = useFmt();
  const { canvasesOf } = useMocks();
  const { domain } = useShell();
  const [topic, setTopic] = useState<string | null>(null);
  const [solved, setSolved] = useState<"all" | "done" | "no">("all");

  const all = useMemo(() => canvasesOf(domain.id), [domain.id, canvasesOf]);
  const topicName = useMemo(() => new Map(domain.topics.map((t) => [t.id, clean(t.title)])), [domain.topics]);
  const topicOptions = useMemo(() => {
    const ids = new Set(all.flatMap((c) => c.topics));
    return domain.topics.filter((t) => ids.has(t.id)).map((t) => ({ id: t.id, title: clean(t.title) }));
  }, [all, domain.topics]);

  // Смена домена сбрасывает фильтры: темы другого домена здесь не нужны
  const [seenDomain, setSeenDomain] = useState(domain.id);
  if (seenDomain !== domain.id) {
    setSeenDomain(domain.id);
    setTopic(null);
    setSolved("all");
  }

  const q = query.trim().toLowerCase();
  const results = useMemo(() => {
    return all
      .filter((c) => (topic ? c.topics.includes(topic) : true))
      .filter((c) => (solved === "all" ? true : solved === "done" ? c.solved !== "no" : c.solved === "no"))
      .map((c) => {
        const lines = c.blocks.flatMap((b) => (b.kind === "task" ? b.lines.map((l) => ({ ...l, blockId: b.id })) : []));
        const line = q ? lines.find((l) => l.read.toLowerCase().includes(q)) : undefined;
        const inTitle = c.title.toLowerCase().includes(q);
        const inTopics = c.topics.some((id) => topicName.get(id)?.toLowerCase().includes(q));
        return { c, line, match: !q || inTitle || inTopics || !!line };
      })
      .filter((r) => r.match);
  }, [all, topic, solved, q, topicName]);

  const groups = useMemo(() => {
    const m = new Map<string, typeof results>();
    for (const r of results) m.set(r.c.date, [...(m.get(r.c.date) ?? []), r]);
    return [...m.entries()];
  }, [results]);

  const filtered = !!q || !!topic || solved !== "all";

  const newBtn = (
    <Link
      href="/canvas/new"
      className="inline-flex h-10 shrink-0 items-center justify-center gap-1.5 rounded-control border border-seam px-4 text-body text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
    >
      <Plus aria-hidden className="size-[18px]" />
      {tx.canvases.newCanvas}
    </Link>
  );

  return (
    <div>
      {all.length === 0 ? (
        <div className="max-w-[44ch]">
          <p className="text-paper-dim">
            {tx.canvases.emptyBefore}{" "}
            <PenLine aria-label={tx.canvases.pencil} className="inline size-4 -translate-y-px align-middle text-paper" /> {tx.canvases.emptyAfter}
          </p>
          <div className="mt-4">{newBtn}</div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <TopicMenu topics={topicOptions} value={topic} onChange={setTopic} />
            <div role="group" aria-label={tx.canvases.solvedGroup} className="flex gap-2 overflow-x-auto">
              {(
                [
                  ["all", tx.canvases.all],
                  ["done", tx.canvases.done],
                  ["no", tx.canvases.notDone],
                ] as const
              ).map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={solved === v}
                  onClick={() => setSolved(v)}
                  className={cn(
                    FILTER_PILL,
                    solved === v ? "border-paper-dim bg-cloth-hover text-paper" : "border-seam text-paper-dim hover:border-rule hover:text-paper",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="max-sm:w-full sm:ml-auto [&>button]:max-sm:w-full">{newBtn}</div>
          </div>

          <>
            {results.length === 0 ? (
              <div className="mt-10 max-w-[44ch]" role="status">
                <p className="text-paper">
                  {q ? tx.canvases.noMatch(query.trim()) : tx.canvases.noMatchFilters}
                </p>
                <p className="mt-1 text-paper-dim">{tx.canvases.tryAgain}</p>
                {filtered && (
                  <button
                    type="button"
                    onClick={() => {
                      onResetQuery();
                      setTopic(null);
                      setSolved("all");
                    }}
                    className="mt-3 inline-flex h-10 items-center rounded-control border border-seam px-3 text-small text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
                  >
                    {tx.canvases.reset}
                  </button>
                )}
              </div>
            ) : (
              <div className="mt-7 flex flex-col gap-9 pb-2">
                {groups.map(([date, items]) => (
                  <section key={date} aria-labelledby={`g-${date}`}>
                    <h2 id={`g-${date}`} className="num mb-4 flex items-center gap-3 text-small tracking-wide text-paper-dim uppercase">
                      {dayLabel(date)}
                      <span aria-hidden className="h-px flex-1 bg-seam-soft" />
                    </h2>
                    <ul className="columns-2 gap-4 sm:columns-[190px]">
                      {items.map(({ c, line }) => {
                        const blockId = line ? line.blockId : firstTaskBlock(c).id;
                        return (
                          <li key={c.id} className="mb-5 break-inside-avoid">
                            <Link
                              href={`/canvas/${c.id}?block=${blockId}`}
                              className="group block rounded-[14px] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ribbon"
                            >
                              <CanvasThumb canvas={c} />
                              <span className="mt-1.5 block">
                                <span className="line-clamp-2 block text-small leading-snug text-paper">
                                  <Hl text={c.title} q={q} />
                                </span>
                                <span className="mt-0.5 line-clamp-2 block text-small text-paper-dim">
                                  {c.topics.map((id) => topicName.get(id) ?? id).join(" · ")}
                                </span>
                                <span className={cn("block text-small", c.solved === "no" ? "text-paper-dim" : "text-st-solid")}>
                                  {tx.solved[c.solved]}
                                </span>
                                {line && (
                                  <span className="num mt-1 line-clamp-2 block text-small break-words text-paper-dim">
                                    <Hl text={line.read} q={q} />
                                  </span>
                                )}
                              </span>
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </>
        </>
      )}
    </div>
  );
}
