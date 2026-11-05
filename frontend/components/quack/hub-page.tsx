"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { API_MODE } from "@/lib/api/client";
import { localIso } from "@/lib/format";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import type { Material, Moment, Topic } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { ArtifactHost, type WindowSpec } from "./artifact-window";
import { CheatCard, CheatSheetContent } from "./artifact-content";
import { Duck } from "./duck";
import { ActionButton, Crumbs, HubRing, MaterialsList, MemoryRing, MomentsList, MoreMenu, PageFrame, PageSection, TEXT_BTN } from "./page-parts";
import { ExpandableCard } from "./artifact-window";
import { Markdown, plainText } from "./live/markdown";
import { useHubApi } from "./live/use-hub-api";
import { NumText } from "./num-text";
import { useStartSession } from "./live/use-start-session";
import { useSoon } from "./soon";

const clean = (s: string) => s.replace(/­/g, "");

const PRIMARY_BTN = "caps inline-flex h-10 items-center justify-center rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-ribbon-hover";

export function HubPage({ id }: { id: string }) {
  const tx = useT();
  const { fullDate, inDays } = useFmt();
  const { getTopicDetails, cheatSheets } = useMocks();
  const { domain } = useShell();
  const soon = useSoon();
  const { start, busy } = useStartSession();
  const api = useHubApi(id, domain.id, API_MODE);
  const [editingDeadline, setEditingDeadline] = useState(false);
  // "all" is the hub over the whole subject: not a node on the graph, reached from the pill next to it
  const hub = useMemo(() => (id === "all" ? { id, num: "·", title: domain.title, about: "" } : domain.hubs.find((h) => h.id === id)), [id, domain]);

  const data = useMemo(() => {
    if (!hub) return null;
    const topics = hub.id === "all" ? domain.topics : domain.topics.filter((t) => t.hubId === hub.id);
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
  const materialItems: Material[] = api.real
    ? api.materials.map((m) => ({ id: m.material_id, title: m.mime === "application/pdf" ? `PDF${m.pages_count ? ` · ${m.pages_count}` : ""}` : tx.hub.photoMaterial, ref: fullDate(localIso(Date.parse(m.created_at))), kind: "pdf" as const }))
    : data?.materials ?? [];

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
  const apiCheat = api.real && api.cheat && api.cheat.blocks.length ? api.cheat : null;
  const apiCheatText = apiCheat ? (apiCheat.blocks as { content: string }[]).map((b) => b.content).join("\n\n") : "";
  const deadline = hub.deadline;
  const suggested = hub.suggested;
  // ровно одна жёлтая кнопка: подготовка к дедлайну, иначе «Вперемешку»; в предложенной главе — «Начать главу»
  const primaryAction = deadline ? null : suggested ? null : "mixed";

  const cheatTitle = deadline ? tx.hub.cheatDeadline : tx.hub.cheat;
  const windows: Record<string, WindowSpec> = {};
  if (apiCheat) {
    windows.cheatsheet = { title: cheatTitle, meta: tx.hub.cheatMeta, body: <Markdown text={apiCheatText} /> };
  } else if (cheat) {
    windows.cheatsheet = {
      title: cheatTitle,
      meta: tx.hub.cheatMeta,
      actions: (
        <>
          <button
            type="button"
            onClick={() => soon(tx.hub.pdfSoon)}
            className="caps h-10 rounded-pill border border-ribbon/60 px-5 text-ribbon transition-colors duration-(--dur-s) ease-(--ease-out) hover:border-ribbon hover:bg-ribbon hover:text-on-ribbon"
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
        <div className="mt-2 rounded-[2px] border border-seam bg-cloth-raised p-4">
          <p className="display-caps text-[1.125rem] text-paper">{tx.hub.suggestedTitle}</p>
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
          <p className="caps text-paper-dim">§ {hub.num}</p>
          <h1 className="display-caps text-display text-paper">{title}</h1>
          <p className="num mt-1.5 text-small text-paper-dim">
            {tx.hub.topicsCount(started, ordered.length)}
          </p>
        </div>
        <MoreMenu
          items={[
            { label: tx.hub.editTopics, soon: tx.hub.editTopicsSoon(title) },
            { label: tx.hub.editDeadline, soon: tx.hub.editDeadlineSoon, onSelect: api.real ? () => setEditingDeadline(true) : undefined },
          ]}
        />
      </header>

      {editingDeadline && api.real && (
        <DeadlineEditor
          initial={deadline?.date}
          busy={api.busy}
          onSave={(ms) => void api.setDeadline(ms).then(() => setEditingDeadline(false))}
          onCancel={() => setEditingDeadline(false)}
        />
      )}

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
          <button type="button" disabled={api.busy} onClick={() => (api.real ? void api.prep() : soon(tx.hub.prepSoon))} className={cn(PRIMARY_BTN, "mt-3 max-sm:w-full")}>
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
                ? { domainId: domain.id, topicId: next.id, intent: "learn" }
                : k === "review"
                  ? { domainId: domain.id, topicId: due.id, intent: "review" }
                  : { domainId: domain.id, intent: "learn" },
            );
          return <ActionButton key={k} label={a} primary={k === primaryAction} soonText="" onClick={run} disabled={busy} />;
        })}
      </div>

      <PageSection id="hub-topics" title={tx.hub.topics} count={ordered.length}>
        <ol>
          {ordered.map((t) => (
            <li key={t.id} className="border-b border-seam-soft last:border-0">
              <Link href={`/topic/${t.id}`} className="flex min-h-14 items-center gap-3 rounded-option px-1 py-2 transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-cloth-hover">
                <MemoryRing state={t.state} retention={t.retention} size={24} />
                <span className="min-w-0 flex-1">
                  <span className="block text-paper">{clean(t.title)}</span>
                  {t.misconception && <span className="block truncate text-small text-st-confused">{t.misconception}</span>}
                </span>
                <span className={cn("caps shrink-0 [--caps-size:0.625rem]", t.state === "confused" ? "text-st-confused" : "text-paper-dim")}>
                  {tx.states[t.state]}
                </span>
                <ChevronRight aria-hidden className="size-4 shrink-0 text-paper-dim" />
              </Link>
            </li>
          ))}
        </ol>
      </PageSection>

      <PageSection id="hub-cheat" title={tx.hub.cheat}>
        {apiCheat ? (
          <ExpandableCard artifact="cheatsheet">
            <span className="display-caps block px-4 pt-3 text-paper">{cheatTitle}</span>
            <span aria-hidden className="mt-1 block h-[72px] overflow-hidden px-4 text-small leading-relaxed text-paper-dim [mask-image:linear-gradient(to_bottom,#000_55%,transparent)]">
              {plainText(apiCheatText)}
            </span>
          </ExpandableCard>
        ) : cheat ? (
          <CheatCard cheat={cheat} title={cheatTitle} />
        ) : (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <p className="text-paper-dim">{tx.hub.cheatLater}</p>
            <button type="button" disabled={api.busy} onClick={() => (api.real ? void api.buildCheat() : soon(tx.hub.buildSoon))} className={cn(TEXT_BTN, "-ml-3 sm:ml-0")}>
              {tx.hub.buildNow}
            </button>
          </div>
        )}
      </PageSection>

      <PageSection id="hub-materials" title={tx.hub.materials}>
        <MaterialsList items={materialItems} />
      </PageSection>

      <PageSection id="hub-sessions" title={tx.hub.inSessions}>
        <MomentsList items={data.timeline} showTopic />
      </PageSection>
    </PageFrame>
    </ArtifactHost>
  );
}

/** Date of the chapter's deadline (to the end of that day, local time), or none */
function DeadlineEditor({ initial, busy, onSave, onCancel }: { initial?: string; busy: boolean; onSave: (ms: number | null) => void; onCancel: () => void }) {
  const t = useT().live;
  const [date, setDate] = useState(initial ?? "");
  return (
    <form
      className="mt-4 flex flex-wrap items-end gap-3 border-t border-seam-soft pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (date) onSave(new Date(`${date}T23:59:59`).getTime());
      }}
    >
      <label className="flex flex-col gap-1.5">
        <span className="caps text-paper-dim">{t.deadlineLabel}</span>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="h-10 rounded-control border border-seam bg-cloth-deep px-3 text-body text-paper outline-none focus-visible:border-ribbon"
        />
      </label>
      <button type="submit" disabled={busy || !date} className={PRIMARY_BTN + " disabled:opacity-50"}>
        {t.deadlineSave}
      </button>
      {initial && (
        <button type="button" disabled={busy} onClick={() => onSave(null)} className={TEXT_BTN}>
          {t.deadlineRemove}
        </button>
      )}
      <button type="button" onClick={onCancel} className={TEXT_BTN}>
        {t.deadlineCancel}
      </button>
    </form>
  );
}
