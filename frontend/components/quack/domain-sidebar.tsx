"use client";

import { ChevronRight, LayoutGrid, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import type { Domain } from "@/lib/mock/types";
import { scrollToId } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { usePathname } from "next/navigation";
import { DOMAIN_PATH, useShell } from "./app-shell";
import { IconButton } from "./icon-button";
import { ProfileMenu } from "./profile-menu";
import { useT } from "@/lib/i18n/provider";

const row =
  "flex min-h-10 w-full items-center gap-2 rounded-control px-3 text-left transition-colors hover:bg-cloth-hover";

export function DomainSidebar({
  domains,
  activeId,
  onSelect,
  collapsed,
  onToggle,
}: {
  domains: Domain[];
  activeId: string;
  onSelect: (id: string) => void;
  /** Узкая полоса вместо панели (только десктопная колонка, на мобильной шторке не используется) */
  collapsed?: boolean;
  onToggle?: () => void;
}) {
  const t = useT();
  const { graphMode, openGraph, closeDrawer } = useShell();
  const pathname = usePathname();
  const onGraph = pathname === DOMAIN_PATH;
  const onOverview = pathname === "/";

  // На телефоне вместо графа список: прокручиваем к главе
  const goHub = (hubId: string) => {
    openGraph({ kind: "hub", hubId });
    if (window.matchMedia("(max-width: 767px)").matches) {
      setTimeout(() => scrollToId(`hub-${hubId}`, 16), onGraph ? 320 : 700);
    }
  };
  const hubOn = (id: string) => onGraph && graphMode.kind === "hub" && graphMode.hubId === id;
  const sub = "pl-8 text-small hover:text-paper";
  const subOn = (on: boolean) => (on ? "bg-cloth-raised text-paper" : "text-paper-dim");

  if (collapsed) {
    return (
      <div className="flex h-full flex-col items-center gap-3 bg-cloth-deep pt-3">
        <IconButton label={t.shell.expandSubjects} onClick={onToggle}>
          <PanelLeftOpen />
        </IconButton>
        <p className="caps text-paper-dim [writing-mode:vertical-rl]">{t.shell.subjectsCount(domains.length)}</p>
      </div>
    );
  }

  return (
    <nav aria-label={t.shell.subjects} className="flex h-full flex-col bg-cloth-deep px-3 pt-4 pb-3">
      {/* шапка: название антиквой капсом, справа — свернуть (десктоп) */}
      <div className="flex min-h-10 items-center justify-between gap-2 px-3 pb-1">
        <p className="display-caps flex-1 text-[1.375rem] text-paper">Quack!</p>
        {onToggle && (
          <IconButton label={t.shell.collapseSubjects} onClick={onToggle}>
            <PanelLeftClose />
          </IconButton>
        )}
      </div>

      {/* первый экран: графы всех предметов сразу */}
      <Link
        href="/"
        onClick={closeDrawer}
        aria-current={onOverview ? "page" : undefined}
        className={cn(row, "mt-3", onOverview ? "bg-cloth-raised font-medium text-paper" : "text-paper-dim")}
      >
        <LayoutGrid aria-hidden className="size-3.5 shrink-0" />
        <span className="truncate">{t.shell.allGraphs}</span>
      </Link>

      <p className="caps flex items-center justify-between px-3 pt-4 pb-2 text-paper-dim">
        <span>{t.shell.subjects}</span>
        <span className="num">{domains.length}</span>
      </p>

      <ul className="flex flex-col gap-0.5 overflow-y-auto">
        {domains.map((d) => {
          const on = d.id === activeId && !onOverview;
          return (
            <li key={d.id}>
              <button
                type="button"
                aria-current={on ? "page" : undefined}
                aria-expanded={on}
                onClick={() => onSelect(d.id)}
                className={cn(row, on ? "bg-cloth-raised font-medium text-paper" : "text-paper-dim")}
              >
                <ChevronRight
                  aria-hidden
                  className={cn("size-3.5 shrink-0 transition-transform duration-200", on && "rotate-90")}
                />
                <span className="truncate">{d.title}</span>
              </button>
              {on && (
                <ul className="mt-0.5 mb-2 flex flex-col">
                  {d.hubs.map((h) => (
                    <li key={h.id}>
                      <button
                        type="button"
                        onClick={() => goHub(h.id)}
                        aria-current={hubOn(h.id) ? "true" : undefined}
                        className={cn(row, sub, subOn(hubOn(h.id)))}
                      >
                        <span className="num w-12 shrink-0 whitespace-nowrap">§ {h.num}</span>
                        <span className={cn("truncate", h.suggested && "italic")}>{h.title}</span>
                        {h.suggested && <span className="sr-only">{t.shell.suggestedSr}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      <ProfileMenu active={pathname.startsWith("/settings")} />
    </nav>
  );
}
