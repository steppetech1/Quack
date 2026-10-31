"use client";

import { Search } from "lucide-react";
import { useId, useMemo, useRef, useState } from "react";
import type { Domain } from "@/lib/mock/types";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { StateDot } from "./topic-card";

const norm = (s: string) => s.toLowerCase().replace(/ё/g, "е");

export function TopicSearch({ domain, onPick }: { domain: Domain; onPick: (id: string) => void }) {
  const t = useT();
  const noChapter = t.graph.noChapter;
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);

  const hubNum = useMemo(() => new Map(domain.hubs.map((h) => [h.id, h.num])), [domain]);
  const results = useMemo(() => {
    const n = norm(q.trim());
    if (!n) return [];
    return domain.topics.filter((t) => norm(t.title).includes(n) || norm(t.line).includes(n)).slice(0, 6);
  }, [q, domain]);

  const pick = (id: string) => {
    onPick(id);
    setOpen(false);
    setQ("");
    input.current?.blur();
  };

  const showList = open && q.trim().length > 0;

  return (
    <div className="relative w-full sm:w-52 xl:w-64">
      <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-paper-dim" />
      <input
        ref={input}
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={showList && results[cursor] ? `${listId}-${results[cursor].id}` : undefined}
        aria-label={t.search.label}
        placeholder={t.search.label}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setCursor(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setCursor((c) => Math.min(c + 1, results.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setCursor((c) => Math.max(c - 1, 0));
          } else if (e.key === "Enter" && results[cursor]) {
            e.preventDefault();
            pick(results[cursor].id);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
        className="h-10 w-full rounded-control border border-seam bg-cloth-deep pr-3 pl-9 text-body text-paper transition-colors hover:border-rule focus-visible:border-ribbon/70 [&::-webkit-search-cancel-button]:hidden"
      />
      {showList && (
        <ul
          id={listId}
          role="listbox"
          aria-label={t.search.results}
          className="absolute top-12 right-0 z-30 w-full min-w-72 rounded-[16px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
        >
          {results.length === 0 ? (
            <li className="px-3 py-2.5 text-small text-paper-dim">{t.search.none}</li>
          ) : (
            results.map((t, i) => (
              <li
                key={t.id}
                id={`${listId}-${t.id}`}
                role="option"
                aria-selected={i === cursor}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(t.id)}
                onMouseEnter={() => setCursor(i)}
                className={cn(
                  "flex min-h-10 cursor-pointer items-center gap-2.5 rounded-option px-3 py-2",
                  i === cursor && "bg-cloth-hover",
                )}
              >
                <StateDot state={t.state} />
                <span className="truncate">{t.title}</span>
                <span className="num ml-auto shrink-0 text-small text-paper-dim">{t.hubId ? `§ ${hubNum.get(t.hubId)}` : noChapter}</span>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
