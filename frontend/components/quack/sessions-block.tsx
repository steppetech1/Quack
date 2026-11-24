"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState, useSyncExternalStore } from "react";
import { useFmt, useT } from "@/lib/i18n/provider";
import type { Session } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useSoon } from "./soon";

/** How many of the latest sessions show before «show all» */
const LATEST = 5;
const KEY = (domainId: string) => `quack.sessions.open.${domainId}`;

// Open or closed, remembered per domain. The server and the first client render say "closed", so the markup matches.
const listeners = new Set<() => void>();
const readOpen = (domainId: string) => {
  try {
    return localStorage.getItem(KEY(domainId));
  } catch {
    return null;
  }
};
const writeOpen = (domainId: string, open: boolean) => {
  try {
    localStorage.setItem(KEY(domainId), open ? "1" : "0");
  } catch {}
  listeners.forEach((l) => l());
};
const subscribe = (cb: () => void) => {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
};

const rowCls =
  "flex min-h-10 w-full items-center gap-2 rounded-control px-3 text-left text-small transition-colors hover:bg-cloth-hover hover:text-paper";

/** Amber mark: the learner's last message has no answer yet */
function AttentionDot({ className }: { className?: string }) {
  return <span aria-hidden className={cn("size-2 shrink-0 rounded-full bg-ribbon", className)} />;
}

/**
 * Sessions of the open domain, a collapsible block under its chapters in the left panel: the latest few, «show all» for
 * the rest. Opens itself when a session of this domain is open (and that row is lit); the choice is remembered per domain.
 * A dot marks a session whose last message is unanswered, also on the folded header.
 */
export function SessionsBlock({
  domainId,
  sessions,
  activeHref,
  onNavigate,
}: {
  domainId: string;
  sessions: Session[];
  activeHref?: string;
  onNavigate?: () => void;
}) {
  const t = useT();
  const { dayLabel } = useFmt();
  const soon = useSoon();
  const uid = useId();
  const [showAll, setShowAll] = useState(false);
  const open = useSyncExternalStore(
    subscribe,
    () => readOpen(domainId) === "1",
    () => false,
  );

  const activeIdx = sessions.findIndex((s) => !!s.href && s.href === activeHref);
  const activeId = activeIdx >= 0 ? sessions[activeIdx].id : null;

  // A session of this domain was opened: open the block once; the learner can still fold it afterwards
  useEffect(() => {
    if (activeId) writeOpen(domainId, true);
  }, [domainId, activeId]);

  // The open session always shows, even when it is older than the latest few
  const shown = showAll
    ? sessions
    : activeIdx >= LATEST
      ? [...sessions.slice(0, LATEST), sessions[activeIdx]]
      : sessions.slice(0, LATEST);
  const attention = sessions.some((s) => s.unanswered);

  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${uid}-list`}
        onClick={() => writeOpen(domainId, !open)}
        className={cn(rowCls, "pl-8 text-paper-dim")}
      >
        <ChevronRight aria-hidden className={cn("size-3 shrink-0 transition-transform duration-200", open && "rotate-90")} />
        <span className="truncate">{t.shell.sessions}</span>
        <span className="num">{sessions.length}</span>
        {!open && attention && (
          <>
            <AttentionDot className="ml-auto" />
            <span className="sr-only">{t.shell.unansweredSr}</span>
          </>
        )}
      </button>
      {open && (
        <div id={`${uid}-list`}>
          {sessions.length === 0 ? (
            <p className="py-1 pr-3 pl-14 text-small text-paper-dim">{t.shell.noSessions}</p>
          ) : (
            <ul className="flex flex-col">
              {shown.map((s) => {
                const active = s.id === activeId;
                const cls = cn(rowCls, "items-start py-2 pl-8", active ? "bg-cloth-raised text-paper" : "text-paper-dim");
                const body = (
                  <>
                    <span aria-hidden className="flex h-[1.35em] w-2.5 shrink-0 items-center">
                      {s.unanswered && <AttentionDot />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span title={s.title} className="line-clamp-2 break-words">
                        {s.title}
                      </span>
                      <span className="caps block text-paper-dim">{dayLabel(s.date)}</span>
                    </span>
                    {s.unanswered && <span className="sr-only">{t.shell.unansweredSr}</span>}
                  </>
                );
                return (
                  <li key={s.id}>
                    {s.href ? (
                      <Link href={s.href} aria-current={active ? "page" : undefined} onClick={onNavigate} className={cls}>
                        {body}
                      </Link>
                    ) : (
                      <button type="button" onClick={() => soon(t.soon.sessionHistory(s.title))} className={cls}>
                        {body}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {sessions.length > LATEST && (
            <button
              type="button"
              aria-expanded={showAll}
              onClick={() => setShowAll((v) => !v)}
              className={cn(rowCls, "pl-14 text-paper-dim")}
            >
              {showAll ? t.shell.showFewerSessions : t.shell.showAllSessions(sessions.length)}
            </button>
          )}
        </div>
      )}
    </li>
  );
}
