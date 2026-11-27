"use client";

import { LayoutGrid, PanelLeft, Plus } from "lucide-react";
import Link from "next/link";
import type { Domain } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { usePathname } from "next/navigation";
import { useShell } from "./app-shell";
import { DomainTree } from "./domain-tree";
import { IconButton } from "./icon-button";
import { ProfileMenu } from "./profile-menu";
import { useT } from "@/lib/i18n/provider";

const row =
  "flex min-h-10 w-full items-center gap-2 rounded-control px-3 text-left transition-colors hover:bg-cloth-hover";
const icon =
  "flex size-10 shrink-0 items-center justify-center rounded-control transition-colors hover:bg-cloth-hover hover:text-paper";

export function DomainSidebar({
  domains,
  activeId,
  onSelect,
  collapsed = false,
  onToggle,
}: {
  domains: Domain[];
  activeId: string;
  onSelect: (id: string) => void;
  /** Узкая полоса: от панели остаются только иконки */
  collapsed?: boolean;
  /** Есть только на компьютере; шторка на телефоне не сворачивается */
  onToggle?: () => void;
}) {
  const t = useT();
  const { closeDrawer } = useShell();
  const pathname = usePathname();
  const onOverview = pathname === "/";

  return (
    <nav aria-label={t.shell.subjects} className={cn("flex h-full flex-col bg-cloth-deep pt-4 pb-3", collapsed ? "items-center px-2" : "px-3")}>
      <div className={cn("flex min-h-10 items-center pb-1", !collapsed && "justify-between pl-3")}>
        {!collapsed && <p className="display-caps text-[1.375rem] text-paper">Qvack!</p>}
        {onToggle && (
          <IconButton label={collapsed ? t.shell.showSidebar : t.shell.hideSidebar} aria-expanded={!collapsed} onClick={onToggle}>
            <PanelLeft />
          </IconButton>
        )}
      </div>

      {/* первый экран: графы всех предметов сразу */}
      <Link
        href="/"
        onClick={closeDrawer}
        aria-current={onOverview ? "page" : undefined}
        aria-label={collapsed ? t.shell.allGraphs : undefined}
        title={collapsed ? t.shell.allGraphs : undefined}
        className={cn(collapsed ? icon : row, "mt-3", onOverview ? "bg-cloth-raised font-medium text-paper" : "text-paper-dim")}
      >
        <LayoutGrid aria-hidden className={cn("shrink-0", collapsed ? "size-[18px] stroke-[1.6]" : "size-3.5")} />
        {!collapsed && <span className="truncate">{t.shell.allGraphs}</span>}
      </Link>

      {collapsed ? (
        // предметы — первая буква названия; полное название в подсказке
        <ul aria-label={t.shell.subjects} className="mt-3 flex min-h-0 flex-col gap-1 overflow-y-auto border-t border-seam-soft pt-3">
          {domains.map((d) => {
            const on = !onOverview && d.id === activeId;
            return (
              <li key={d.id}>
                <button
                  type="button"
                  aria-label={d.title}
                  title={d.title}
                  aria-current={on ? "true" : undefined}
                  onClick={() => onSelect(d.id)}
                  className={cn(icon, "display-caps text-[1.125rem]", on ? "bg-cloth-raised text-paper" : "text-paper-dim")}
                >
                  {d.title.charAt(0)}
                </button>
              </li>
            );
          })}
          <li>
            <Link href="/domains/new" aria-label={t.shell.newSubject} title={t.shell.newSubject} className={cn(icon, "text-paper-dim")}>
              <Plus aria-hidden className="size-[18px] stroke-[1.6]" />
            </Link>
          </li>
        </ul>
      ) : (
        <>
          <p className="caps flex items-center justify-between px-3 pt-4 pb-2 text-paper-dim">
            <span>{t.shell.subjects}</span>
            <span className="num">{domains.length}</span>
          </p>

          {/* предметы → главы → темы и сессии предмета; на «Всех графах» ни один предмет не выбран */}
          <div className="min-h-0 overflow-y-auto">
            <DomainTree domains={domains} activeId={onOverview ? "" : activeId} onSelect={onSelect} />
          </div>
        </>
      )}

      <ProfileMenu active={pathname.startsWith("/settings")} collapsed={collapsed} />
    </nav>
  );
}
