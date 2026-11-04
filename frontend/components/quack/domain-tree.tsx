"use client";

import { ChevronRight, Plus } from "lucide-react";
import Link from "next/link";
import type { Domain } from "@/lib/mock/types";
import { scrollToId } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useShell } from "./app-shell";
import { StateDot } from "./topic-card";
import { useT } from "@/lib/i18n/provider";

const row =
  "flex min-h-10 w-full items-center gap-2 rounded-control px-3 text-left transition-colors hover:bg-cloth-hover";

/**
 * Домен → хаб → темы. Используется и в боковой панели (DomainSidebar), и инлайном
 * на домашней странице телефона вместо графа (см. domain-view.tsx).
 */
export function DomainTree({
  domains,
  activeId,
  onSelect,
  showNewSubject = true,
  openTopicId,
}: {
  domains: Domain[];
  activeId: string;
  onSelect: (id: string) => void;
  /** Ссылка «+ Новый предмет» внизу списка; скрыта в инлайн-варианте на мобильной главной */
  showNewSubject?: boolean;
  /** Тема, найденная поиском: раскрыть её хаб и подсветить строку (мобильная главная без графа) */
  openTopicId?: string | null;
}) {
  const t = useT();
  const { graphMode, openGraph } = useShell();
  const pathname = usePathname();
  const onGraph = pathname === "/";
  // Какой хаб сейчас развёрнут (видны его темы); один за раз
  const [openHub, setOpenHub] = useState<string | null>(null);
  // Какой домен сейчас развёрнут (видны его хабы); не привязано к activeId — повторный клик по
  // уже выбранному домену должен сворачивать его список, а не просто переизбирать тот же домен
  const [expandedDomain, setExpandedDomain] = useState<string | null>(activeId);

  // Домен сменили снаружи (например, ссылкой «← Calculus» со страницы темы) — разворачиваем его
  const [seenActiveId, setSeenActiveId] = useState(activeId);
  if (activeId !== seenActiveId) {
    setSeenActiveId(activeId);
    setExpandedDomain(activeId);
  }

  const toggleDomain = (id: string) => {
    if (id === activeId) {
      setExpandedDomain((cur) => (cur === id ? null : id));
    } else {
      onSelect(id);
      setExpandedDomain(id);
    }
  };

  // Поиск темы (без графа на телефоне) раскрывает её хаб, чтобы строку было видно.
  // Делаем это при рендере (не в эффекте), чтобы не ловить лишний цикл обновлений.
  const [seenTopicId, setSeenTopicId] = useState(openTopicId);
  if (openTopicId !== seenTopicId) {
    setSeenTopicId(openTopicId);
    if (openTopicId) {
      const active = domains.find((d) => d.id === activeId);
      const hubId = active?.topics.find((t) => t.id === openTopicId)?.hubId;
      if (hubId) setOpenHub(hubId);
    }
  }

  // На телефоне вместо графа список: прокручиваем к главе. Разворачивает хаб, чтобы увидеть его темы.
  const goHub = (hubId: string) => {
    setOpenHub((cur) => (cur === hubId ? null : hubId));
    openGraph({ kind: "hub", hubId });
    if (window.matchMedia("(max-width: 767px)").matches) {
      setTimeout(() => scrollToId(`hub-${hubId}`, 16), onGraph ? 320 : 700);
    }
  };
  const hubOn = (id: string) => onGraph && graphMode.kind === "hub" && graphMode.hubId === id;
  const sub = "pl-8 text-small hover:text-paper";
  const subOn = (on: boolean) => (on ? "bg-cloth-raised text-paper" : "text-paper-dim");
  const topicRow = "pl-14 text-small hover:text-paper";

  return (
    <ul className="flex flex-col gap-0.5">
      {domains.map((d) => {
        const active = d.id === activeId;
        const open = d.id === expandedDomain;
        return (
          <li key={d.id}>
            <button
              type="button"
              aria-current={active ? "page" : undefined}
              aria-expanded={open}
              onClick={() => toggleDomain(d.id)}
              className={cn(row, active ? "bg-cloth-raised font-medium text-paper" : "text-paper-dim")}
            >
              <ChevronRight
                aria-hidden
                className={cn("size-3.5 shrink-0 transition-transform duration-200", open && "rotate-90")}
              />
              <span className="truncate">{d.title}</span>
            </button>
            {open && (
              <ul className="mt-0.5 mb-2 flex flex-col">
                {d.hubs.map((h) => {
                  const expanded = openHub === h.id;
                  const topics = d.topics.filter((top) => top.hubId === h.id);
                  return (
                    <li key={h.id}>
                      <button
                        type="button"
                        onClick={() => goHub(h.id)}
                        aria-current={hubOn(h.id) ? "true" : undefined}
                        aria-expanded={expanded}
                        className={cn(row, sub, subOn(hubOn(h.id)))}
                      >
                        <ChevronRight
                          aria-hidden
                          className={cn("size-3 shrink-0 transition-transform duration-200", expanded && "rotate-90")}
                        />
                        <span className="num w-10 shrink-0 whitespace-nowrap">§ {h.num}</span>
                        <span className={cn("truncate", h.suggested && "italic")}>{h.title}</span>
                        {h.suggested && <span className="sr-only">{t.shell.suggestedSr}</span>}
                      </button>
                      {expanded && topics.length > 0 && (
                        <ul className="flex flex-col">
                          {topics.map((top) => (
                            <li key={top.id} id={`topic-${top.id}`} className="scroll-mt-4">
                              <Link
                                href={`/topic/${top.id}`}
                                className={cn(row, topicRow, top.id === openTopicId ? "bg-cloth-raised text-paper" : "text-paper-dim")}
                              >
                                <StateDot state={top.state} />
                                <span className="truncate">{top.title}</span>
                              </Link>
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  );
                })}
                {d.topics.filter((top) => !top.hubId).length > 0 && (
                  <li>
                    <p className={cn(row, sub, "text-paper-dim")} aria-hidden>
                      {t.graph.noChapter}
                    </p>
                    <ul className="flex flex-col">
                      {d.topics
                        .filter((top) => !top.hubId)
                        .map((top) => (
                          <li key={top.id}>
                            <Link href={`/topic/${top.id}`} className={cn(row, topicRow, "text-paper-dim")}>
                              <StateDot state={top.state} />
                              <span className="truncate">{top.title}</span>
                            </Link>
                          </li>
                        ))}
                    </ul>
                  </li>
                )}
              </ul>
            )}
          </li>
        );
      })}
      {showNewSubject && (
        <li>
          <Link
            href="/domains/new"
            className="caps mt-2 ml-3 inline-flex min-h-10 items-center gap-2 rounded-pill border border-seam px-4 text-paper-dim transition-colors hover:border-rule hover:bg-cloth-hover hover:text-paper"
          >
            <Plus aria-hidden className="size-3.5" />
            {t.shell.newSubject}
          </Link>
        </li>
      )}
    </ul>
  );
}
