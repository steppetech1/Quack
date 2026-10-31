"use client";

import { PanelRightClose, PanelRightOpen } from "lucide-react";
import Link from "next/link";
import { useFmt, useT } from "@/lib/i18n/provider";
import type { Session } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { IconButton } from "./icon-button";
import { useSoon } from "./soon";

export function SessionsPanel({
  sessions,
  activeHref,
  collapsed,
  onToggle,
  onNavigate,
}: {
  sessions: Session[];
  activeHref?: string;
  collapsed?: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
}) {
  const t = useT();
  const { dayLabel } = useFmt();
  const soon = useSoon();

  if (collapsed) {
    return (
      <div className="flex h-full flex-col items-center gap-3 bg-cloth-deep pt-3">
        <IconButton label={t.shell.expandSessions} onClick={onToggle}>
          <PanelRightOpen />
        </IconButton>
        <p className="num text-small text-paper-dim [writing-mode:vertical-rl]">
          {t.shell.sessionsCount(sessions.length)}
        </p>
      </div>
    );
  }

  return (
    <section aria-labelledby="sessions-h" className="flex h-full flex-col bg-cloth-deep">
      <div className="flex items-center justify-between py-3 pr-2 pl-5">
        <h2 id="sessions-h" className="font-semibold text-paper">
          {t.shell.sessions}
        </h2>
        {onToggle && (
          <IconButton label={t.shell.collapseSessions} onClick={onToggle}>
            <PanelRightClose />
          </IconButton>
        )}
      </div>
      {sessions.length === 0 ? (
        <p className="px-5 text-small text-paper-dim">{t.shell.noSessions}</p>
      ) : (
        <ul className="flex flex-col gap-0.5 overflow-y-auto px-2 pb-3">
          {sessions.map((s) => {
            const active = !!s.href && s.href === activeHref;
            const cls = cn(
              "block w-full rounded-control px-3 py-2.5 text-left transition-colors hover:bg-cloth-hover",
              active && "bg-cloth-raised",
            );
            const body = (
              <>
                <span className="num block text-small text-paper-dim">{dayLabel(s.date)}</span>
                <span className="mt-0.5 line-clamp-2 block text-paper">{s.title}</span>
                {s.topics.length > 0 && (
                  <span className="mt-0.5 line-clamp-2 block text-small text-paper-dim">{s.topics.join(" · ")}</span>
                )}
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
    </section>
  );
}
