"use client";

import { LayoutGrid } from "lucide-react";
import Link from "next/link";
import type { Domain } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { usePathname } from "next/navigation";
import { useShell } from "./app-shell";
import { DomainTree } from "./domain-tree";
import { ProfileMenu } from "./profile-menu";
import { useT } from "@/lib/i18n/provider";

const row =
  "flex min-h-10 w-full items-center gap-2 rounded-control px-3 text-left transition-colors hover:bg-cloth-hover";

export function DomainSidebar({
  domains,
  activeId,
  onSelect,
}: {
  domains: Domain[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  const t = useT();
  const { closeDrawer } = useShell();
  const pathname = usePathname();
  const onOverview = pathname === "/";

  return (
    <nav aria-label={t.shell.subjects} className="flex h-full flex-col bg-cloth-deep px-3 pt-4 pb-3">
      <div className="flex min-h-10 items-center px-3 pb-1">
        <p className="display-caps text-[1.375rem] text-paper">Qvack!</p>
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

      {/* предметы → главы → темы и сессии предмета; на «Всех графах» ни один предмет не выбран */}
      <div className="min-h-0 overflow-y-auto">
        <DomainTree domains={domains} activeId={onOverview ? "" : activeId} onSelect={onSelect} />
      </div>

      <ProfileMenu active={pathname.startsWith("/settings")} />
    </nav>
  );
}
