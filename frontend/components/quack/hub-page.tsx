"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { API_MODE } from "@/lib/api/client";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import type { Material, Moment, Topic } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { ArtifactHost, type WindowSpec } from "./artifact-window";
import { CheatCard, CheatSheetContent } from "./artifact-content";
import { Duck } from "./duck";
import { ActionButton, Crumbs, HubRing, MaterialsList, MemoryRing, MomentsList, MoreMenu, PageFrame, PageSection, TEXT_BTN } from "./page-parts";
import { NumText } from "./num-text";
import { useStartSession } from "./live/use-start-session";
import { useSoon } from "./soon";

const clean = (s: string) => s.replace(/­/g, "");

const PRIMARY_BTN = "inline-flex h-10 items-center justify-center rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover";

export function HubPage({ id }: { id: string }) {
  const tx = useT();
  const { fullDate, inDays } = useFmt();
  const { getTopicDetails, cheatSheets } = useMocks();
  const { domain } = useShell();
  const soon = useSoon();
  const { start, busy } = useStartSession();
  const hub = domain.hubs.find((h) => h.id === id);

  const data = useMemo(() => {
    if (!hub) return null;
    const topics = domain.topics.filter((t) => t.hubId === hub.id);
    // порядок изучения: по глубине «нужно до» внутри главы
    const byId = new Map(topics.map((t) => [t.id, t]));
    const depth = new Map<string, number>();
    const depthOf = (t: Topic): number => {
      const known = depth.get(t.id);
      if (known !== undefined) return known;
      const ps = t.requires.map((r) => byId.get(r)).filter((p): p is Topic => !!p);
      const d = ps.length ? Math.max(...ps.map(depthOf)) + 1 : 0;
      depth.set(t.id, d);
      return d;
    };
    const ordered = topics.map((t, i) => ({ t, d: depthOf(t), i })).sort((a, b) => a.d - b.d || a.i - b.i).map((x) => x.t);

    const materials = new Map<string, Material>();
    const moments = new Map<string, Moment & { topicTitles: string[] }>();
    for (const t of ordered) {
      const det = getTopicDetails(domain, t);
      det.materials.forEach((m) => materials.set(m.id, m));
      det.moments.forEach((m) => {
        const key = m.sessionId ?? m.id;
        const have = moments.get(key);
        if (have) have.topicTitles.push(clean(t.title));
        else moments.set(key, { ...m, topicTitles: [clean(t.title)] });
      });
    }
    const timeline = [...moments.values()]
      .sort((a, b) => b.date.localeCompare(a.date))
      .map((m) => ({ ...m, href: m.sessionId ? domain.sessions.find((s) => s.id === m.sessionId)?.href : undefined, topicTitle: m.topicTitles.join(", ") }));
    return { ordered, materials: [...materials.values()], timeline };
  }, [domain, hub, getTopicDetails]);

  if (!hub || !data) {
    return (
      <PageFrame chipLabel={tx.page.chipSubject(domain.title)}>
        <Crumbs domainTitle={domain.title} />
        <p className="mt-6 text-paper-dim">{tx.hub.missing}</p>
      </PageFrame>
    );
  }

  const title = clean(hub.title);
  const { ordered } = data;
  const started = ordered.filter((t) => t.state !== "new").length;
  const progress = ordered.length ? started / ordered.length : 0;
  const cheat = cheatSheets[hub.id];
  const deadline = hub.deadline;
  const suggested = hub.suggested;
  // ровно одна жёлтая кнопка: подготовка к дедлайну, иначе «Вперемешку»; в предложенной главе — «Начать главу»
  const primaryAction = deadline ? null : suggested ? null : "mixed";

  const cheatTitle = deadline ? tx.hub.cheatDeadline : tx.hub.cheat;
  const windows: Record<string, WindowSpec> = {};
  if (cheat) {
    windows.cheatsheet = {
      title: cheatTitle,
      meta: tx.hub.cheatMeta,
      actions: (
        <>
          <button
            type="button"
            onClick={() => soon(tx.hub.pdfSoon)}
            className="h-10 rounded-control border border-seam px-3 text-small text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
          >
            PDF
          </button>
          <button type="button" onClick={() => soon(tx.hub.selfBuildSoon)} className={TEXT_BTN}>
            {tx.hub.selfBuild}
          </button>
        </>
      ),
      body: <CheatSheetContent cheat={cheat} />,
    };
  }

  return (
    <ArtifactHost windows={windows}>
    <PageFrame chipLabel={tx.page.chipHub(hub.num, title)}>
      <Crumbs domainTitle={domain.title} />

      {suggested && (
        <div className="mt-2 rounded-[14px] border border-seam bg-cloth-raised p-4">
          <p className="font-semibold text-paper">{tx.hub.suggestedTitle}</p>
          <p className="mt-0.5 text-paper-dim">{tx.hub.suggestedBody}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => soon(tx.hub.startChapterSoon(title))} className={PRIMARY_BTN}>
              {tx.hub.startChapter}
            </button>
            <button type="button" onClick={() => soon(tx.hub.hideSoon)} className={TEXT_BTN}>
              {tx.hub.hideSuggestion}
            </button>
          </div>
        </div>
      )}

      <header className="mt-2 flex items-start gap-4">
        <HubRing progress={progress} suggested={suggested} size={56} />
        <div className="min-w-0 flex-1">
          <p className="num text-small text-paper-dim">§ {hub.num}</p>
          <h1 className="font-serif text-display font-bold tracking-[-0.01em] text-paper">{title}</h1>
          <p className="num mt-1 text-small text-paper-dim">
            {tx.hub.topicsCount(started, ordered.length)}
          </p>
        </div>
        <MoreMenu
          items={[
            { label: tx.hub.editTopics, soon: tx.hub.editTopicsSoon(title) },
            { label: tx.hub.editDeadline, soon: tx.hub.editDeadlineSoon },
          ]}
        />
      </header>

      <p className="mt-4 max-w-[62ch] text-body leading-relaxed text-paper">{hub.about}</p>

      {deadline && (
        <div className="mt-5 border-t border-seam-soft pt-4">
          <div className="flex items-start gap-3">
            <Duck className="mt-1 h-5 w-6" />
            <div className="min-w-0">
              <p className="text-paper">
                {deadline.label.split(" ")[0]} · <span className="whitespace-nowrap"><NumText text={fullDate(deadline.date)} /></span> ·{" "}
                <span className="whitespace-nowrap"><NumText text={inDays(deadline.date)} /></span>
              </p>
              {domain.readiness !== undefined && (
                <p className="mt-0.5 text-paper-dim">
                  {tx.hub.ready} <span className="num text-paper">{domain.readiness}%</span>
                </p>
              )}
            </div>
          </div>
          <button type="button" onClick={() => soon(tx.hub.prepSoon)} className={cn(PRIMARY_BTN, "mt-3 max-sm:w-full")}>
            {tx.hub.prep}
          </button>
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
        {(
          [
            ["all", tx.hub.studyAll],
            ["practice", tx.hub.practice],
            ["review", tx.hub.review],
            ["mixed", tx.hub.mixed],
          ] as const
        ).map(([k, a]) => {
          // Бэкенд: «Изучать всё» — с первой неизученной темы, «Повторение» — с темы, которую пора повторить,
          // «Вперемешку» — сессия по главе, тему выбирает бэкенд, «Практика» — экран задач по первой несданной теме
          const next = ordered.find((t) => t.state !== "solid") ?? ordered[0];
          const due = ordered.find((t) => t.state === "review" || t.state === "confused") ?? next;
          if (!API_MODE || !next) return <ActionButton key={k} label={a} primary={k === primaryAction} soonText={tx.hub.actionSoon(a, title)} />;
          if (k === "practice") return <ActionButton key={k} label={a} href={`/practice/${next.id}`} soonText="" />;
          const run = () =>
            void start(
              k === "all"
                ? { domainId: domain.id, topicId: next.id, mode: "learn" }
                : k === "review"
                  ? { domainId: domain.id, topicId: due.id, mode: "review" }
                  : { domainId: domain.id, mode: "learn" },
            );
          return <ActionButton key={k} label={a} primary={k === primaryAction} soonText="" onClick={run} disabled={busy} />;
        })}
      </div>

      <PageSection id="hub-topics" title={tx.hub.topics} count={ordered.length}>
        <ol>
          {ordered.map((t) => (
            <li key={t.id} className="border-b border-seam-soft last:border-0">
              <Link href={`/topic/${t.id}`} className="flex min-h-14 items-center gap-3 rounded-option px-1 py-2 transition-colors hover:bg-cloth-hover">
                <MemoryRing state={t.state} retention={t.retention} size={24} />
                <span className="min-w-0 flex-1">
                  <span className="block text-paper">{clean(t.title)}</span>
                  {t.misconception && <span className="block truncate text-small text-st-confused">{t.misconception}</span>}
                </span>
                <span className={cn("shrink-0 text-small", t.state === "confused" ? "text-st-confused" : "text-paper-dim")}>
                  {tx.states[t.state].toLowerCase()}
                </span>
                <ChevronRight aria-hidden className="size-4 shrink-0 text-paper-dim" />
              </Link>
            </li>
          ))}
        </ol>
      </PageSection>

      <PageSection id="hub-cheat" title={tx.hub.cheat}>
        {cheat ? (
          <CheatCard cheat={cheat} title={cheatTitle} />
        ) : (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <p className="text-paper-dim">{tx.hub.cheatLater}</p>
            <button type="button" onClick={() => soon(tx.hub.buildSoon)} className={cn(TEXT_BTN, "-ml-3 sm:ml-0")}>
              {tx.hub.buildNow}
            </button>
          </div>
        )}
      </PageSection>

      <PageSection id="hub-materials" title={tx.hub.materials}>
        <MaterialsList items={data.materials} />
      </PageSection>

      <PageSection id="hub-sessions" title={tx.hub.inSessions}>
        <MomentsList items={data.timeline} showTopic />
      </PageSection>
    </PageFrame>
    </ArtifactHost>
  );
}
