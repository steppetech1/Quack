"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import { scrollToId } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { CanvasesList, CanvasSearch } from "./canvases-page";
import { Composer } from "./composer";
import { DomainGraph, type GraphHandle } from "./domain-graph";
import { NumText } from "./num-text";
import { EmptyDomain } from "./empty-domain";
import { TodayList } from "./today-list";
import { TopicList } from "./topic-list";
import { TopicSearch } from "./topic-search";

type View = "graph" | "canvases";

function ViewToggle({ view, onChange, count, panelId }: { view: View; onChange: (v: View) => void; count: number; panelId: string }) {
  const tx = useT();
  const TABS: { id: View; label: string }[] = [
    { id: "graph", label: tx.domain.graph },
    { id: "canvases", label: tx.domain.canvases },
  ];
  const refs = useRef<Record<View, HTMLButtonElement | null>>({ graph: null, canvases: null });
  const onKey = (e: React.KeyboardEvent) => {
    const i = TABS.findIndex((t) => t.id === view);
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i + TABS.length - 1) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onChange(TABS[next].id);
    refs.current[TABS[next].id]?.focus();
  };
  return (
    <div
      role="tablist"
      aria-label={tx.domain.viewLabel}
      onKeyDown={onKey}
      className="flex h-11 w-full gap-0.5 rounded-control border border-seam bg-cloth-deep p-0.5 sm:w-auto"
    >
      {TABS.map((t) => {
        const on = view === t.id;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            type="button"
            role="tab"
            id={`view-tab-${t.id}`}
            aria-selected={on}
            aria-controls={panelId}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={cn(
              "inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-[9px] px-4 text-body whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ribbon sm:flex-none",
              on ? "bg-cloth-raised font-medium text-paper" : "text-paper-dim hover:text-paper",
            )}
          >
            {t.label}
            {t.id === "canvases" && count > 0 && <span className="num text-small">{count}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function DomainView() {
  const tx = useT();
  const { fullDate, inDays } = useFmt();
  const { canvasesOf } = useMocks();
  const { domain, startSession, graphMode, setGraphMode } = useShell();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const view: View = params.get("view") === "canvases" ? "canvases" : "graph";
  const panelId = useId();
  const [query, setQuery] = useState("");
  const [openTopic, setOpenTopic] = useState<string | null>(null);
  const graph = useRef<GraphHandle>(null);

  useEffect(() => {
    const title = view === "canvases" ? tx.meta.canvasesTitle(domain.title) : tx.meta.title(domain.title);
    // Next возвращает заголовок из metadata после навигации — держим свой, пока виден этот вид
    const apply = () => {
      document.querySelectorAll("title").forEach((el) => {
        if (el.textContent !== title) el.textContent = title;
      });
    };
    apply();
    const mo = new MutationObserver(apply);
    mo.observe(document.head, { childList: true, characterData: true, subtree: true });
    return () => mo.disconnect();
  }, [view, domain.title, tx]);

  const setView = (v: View) => {
    if (v === view) return;
    setQuery("");
    router.push(v === "canvases" ? `${pathname}?view=canvases` : pathname, { scroll: false });
  };

  const findTopic = (id: string) => {
    if (window.matchMedia("(min-width: 768px)").matches) {
      graph.current?.focusTopic(id);
    } else {
      setOpenTopic(id);
      requestAnimationFrame(() => scrollToId(`topic-${id}`, 120));
    }
  };

  const deadlineHub = domain.hubs.find((h) => h.deadline);
  const hasGraph = domain.topics.length > 0;
  const canvasCount = canvasesOf(domain.id).length;

  const [seenDomain, setSeenDomain] = useState(domain.id);
  if (seenDomain !== domain.id) {
    setSeenDomain(domain.id);
    setQuery("");
  }

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pt-4 pb-3 md:overflow-hidden md:px-7 md:pt-6">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:gap-6">
          <div className="min-w-0">
            <h1 className="font-serif text-display font-bold tracking-[-0.01em] text-paper">{domain.title}</h1>
            {deadlineHub?.deadline ? (
              <p className="mt-1.5 text-paper-dim">
                <span className="whitespace-nowrap">{deadlineHub.deadline.label}</span> ·{" "}
                <span className="whitespace-nowrap text-ribbon">
                  <NumText text={`${fullDate(deadlineHub.deadline.date)}, ${inDays(deadlineHub.deadline.date)}`} />
                </span>
                {domain.readiness !== undefined && (
                  <>
                    {" "}·{" "}
                    <span className="whitespace-nowrap">
                      {tx.domain.ready} <span className="num text-paper">{domain.readiness}%</span>
                    </span>
                  </>
                )}
              </p>
            ) : (
              <p className="mt-1.5 text-paper-dim">{tx.domain.noDeadlines}</p>
            )}
          </div>
          <div className="sm:ml-auto sm:shrink-0">
            <ViewToggle view={view} onChange={setView} count={canvasCount} panelId={panelId} />
          </div>
          {view === "graph" && hasGraph && (
            <div className="shrink-0">
              <TopicSearch key={domain.id} domain={domain} onPick={findTopic} />
            </div>
          )}
          {view === "canvases" && canvasCount > 0 && (
            <div className="shrink-0">
              <CanvasSearch value={query} onChange={setQuery} />
            </div>
          )}
        </header>

        <div
          role="tabpanel"
          id={panelId}
          aria-labelledby={`view-tab-${view}`}
          className={cn("flex flex-col gap-4 md:min-h-0 md:flex-1", view === "canvases" && "md:overflow-y-auto")}
        >
          {view === "canvases" ? (
            <CanvasesList query={query} onResetQuery={() => setQuery("")} />
          ) : hasGraph ? (
            <>
              <div className="hidden min-h-0 flex-1 md:flex">
                <DomainGraph key={domain.id} ref={graph} domain={domain} mode={graphMode} onModeChange={setGraphMode} />
              </div>
              <div className="md:hidden">
                <TodayList items={domain.today} idPrefix="m" />
              </div>
              <div className="md:hidden">
                <TopicList domain={domain} openId={openTopic} />
              </div>
              <div className="hidden md:block">
                <TodayList items={domain.today} />
              </div>
            </>
          ) : (
            <EmptyDomain titleDat={domain.titleDat} setupHref={domain.status === "onboarding" ? `/domains/${domain.id}/structure` : undefined} />
          )}
        </div>
      </div>

      <div className="px-4 pb-4 md:px-7 md:pb-5">
        <Composer label={tx.domain.composerLabel(domain.title)} onSend={startSession} />
      </div>
    </>
  );
}
