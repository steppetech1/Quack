"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { Domain } from "@/lib/mock/types";
import { usePathname } from "next/navigation";
import { DomainTree } from "./domain-tree";
import { IconButton } from "./icon-button";
import { ProfileMenu } from "./profile-menu";
import { useT } from "@/lib/i18n/provider";

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
  const pathname = usePathname();

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

      <p className="caps flex items-center justify-between px-3 pt-5 pb-2 text-paper-dim">
        <span>{t.shell.subjects}</span>
        <span className="num">{domains.length}</span>
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <DomainTree domains={domains} activeId={activeId} onSelect={onSelect} />
      </div>

      <ProfileMenu active={pathname.startsWith("/settings")} />
    </nav>
  );
}
