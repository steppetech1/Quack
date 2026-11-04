"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useGraphModel } from "@/lib/graph/use-graph-model";
import type { TodayRec } from "@/lib/graph/types";
import { useFmt, useLocale, useMocks, useT } from "@/lib/i18n/provider";
import { scrollToId } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { CanvasesList, CanvasSearch } from "./canvases-page";
import { Composer } from "./composer";
import { DomainGraph, type GraphHandle } from "./domain-graph";
import { LayeredGraph } from "./graph/layered-graph";
import { NumText } from "./num-text";
import { EmptyDomain } from "./empty-domain";
import { TodayCards } from "./today-cards";
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
      className="flex h-11 w-full gap-0.5 rounded-pill border border-seam bg-cloth-deep p-0.5 sm:w-auto"
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
              "caps inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-pill border px-4 whitespace-nowrap transition-colors duration-(--dur-s) ease-(--ease-out) focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ribbon sm:flex-none",
              on ? "border-ribbon/70 bg-cloth-raised text-paper" : "border-transparent text-paper-dim hover:text-paper",
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
  const { locale } = useLocale();
  const { domain, startSession, graphMode, setGraphMode, centerPending, consumeCenter } = useShell();
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

  const clearFocus = useCallback(() => setGraphMode({ kind: "overview" }), [setGraphMode]);
  const deadlineHub = domain.hubs.find((h) => h.deadline);
  // The layered graph runs on the slice model. With the API on, the old graph stays until the adapter exists
  const model = useGraphModel(locale, domain.id);
  const hasGraph = model ? model.topics.length > 0 : domain.topics.length > 0;
  const canvasCount = canvasesOf(domain.id).length;

  // "Today": from the model on mocks; with the API on, from what the backend gave the older mapping.
  // Only the fields that exist are carried over.
  const recs = useMemo<TodayRec[]>(
    () =>
      model
        ? model.today
        : domain.today.map((it) => ({ id: it.id, kind: "item" as const, topicId: it.topicId, name: it.title, action: it.topicId ? ("review" as const) : undefined, minutes: it.minutes, href: it.href })),
    [model, domain.today],
  );
  const nameOf = (r: TodayRec) => r.name ?? (r.topicId ? model?.topics.find((t) => t.id === r.topicId)?.name : r.hubId ? model?.hubs.find((h) => h.id === r.hubId)?.name : undefined);
  // Hovering a card lights its topic (or hub) on the graph
  const [hot, setHot] = useState<TodayRec | null>(null);
  const highlight = useMemo(() => (hot ? { topicIds: hot.topicId ? [hot.topicId] : [], hubIds: hot.hubId ? [hot.hubId] : [] } : undefined), [hot]);

  // Nothing to centre (canvases, no graph, phone list): drop the request so it does not hit a later graph
  useEffect(() => {
    if (centerPending && (view !== "graph" || !hasGraph || !window.matchMedia("(min-width: 768px)").matches)) consumeCenter();
  }, [centerPending, view, hasGraph, consumeCenter]);

  const [seenDomain, setSeenDomain] = useState(domain.id);
  if (seenDomain !== domain.id) {
    setSeenDomain(domain.id);
    setQuery("");
  }

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pt-4 pb-3 md:gap-3 md:overflow-hidden md:px-7 md:pt-5">
        {/* On sm and up: title, view toggle and search share the first row, the deadline line gets the whole second one and never wraps */}
        <header className="grid gap-x-6 gap-y-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center sm:gap-y-2">
          <h1 title={domain.title} className="display-caps min-w-0 truncate text-[clamp(1.75rem,2.6vw,2.25rem)] text-paper sm:col-start-1 sm:row-start-1">
            {domain.title}
          </h1>
          <div className="min-w-0 sm:col-span-3 sm:row-start-2">
            {deadlineHub?.deadline ? (
              <p className="caps text-paper-dim">
                <span className="whitespace-nowrap">{deadlineHub.deadline.label}</span> ·{" "}
                <span className="whitespace-nowrap text-ind-deadline-text">
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
              <p className="caps text-paper-dim">{tx.domain.noDeadlines}</p>
            )}
            {domain.lagging && (
              <p role="status" className="mt-1 text-small text-paper-dim">
                {tx.live.graphLagging}
              </p>
            )}
          </div>
          <div className="sm:col-start-2 sm:row-start-1">
            <ViewToggle view={view} onChange={setView} count={canvasCount} panelId={panelId} />
          </div>
          {view === "graph" && hasGraph && (
            <div className="sm:col-start-3 sm:row-start-1">
              <TopicSearch key={domain.id} domain={domain} onPick={findTopic} />
            </div>
          )}
          {view === "canvases" && canvasCount > 0 && (
            <div className="sm:col-start-3 sm:row-start-1">
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
                {model ? (
                  <LayeredGraph
                    key={domain.id}
                    ref={graph}
                    model={model}
                    domain={domain}
                    highlight={highlight}
                    focusHubId={graphMode.kind === "hub" ? graphMode.hubId : null}
                    onClearFocus={clearFocus}
                    center={centerPending}
                    onCentered={consumeCenter}
                  />
                ) : (
                  <DomainGraph key={domain.id} ref={graph} domain={domain} mode={graphMode} onModeChange={setGraphMode} center={centerPending} onCentered={consumeCenter} />
                )}
              </div>
              <TodayCards recs={recs} nameOf={nameOf} onHover={setHot} className="md:hidden" idPrefix="m" />
              <div className="md:hidden">
                <TopicList domain={domain} openId={openTopic} />
              </div>
              <TodayCards recs={recs} nameOf={nameOf} onHover={setHot} className="hidden md:grid" />
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
