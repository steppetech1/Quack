"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import type { Topic } from "@/lib/mock/types";
import { flash, scrollToId } from "@/lib/scroll";
import { useShell } from "./app-shell";
import { ArtifactHost, type WindowSpec } from "./artifact-window";
import { CanvasCard, DeckCard, DeckViewer, NotesCard, NotesContent, notesMeta } from "./artifact-content";
import { Board } from "./board/board";
import { ActionButton, Crumbs, MaterialsList, MemoryRing, MomentsList, MoreMenu, PageFrame, PageSection, TEXT_BTN } from "./page-parts";
import { Sketch } from "./sketch";
import { useSoon } from "./soon";

const clean = (s: string) => s.replace(/­/g, "");

const PRIMARY: Record<Topic["state"], "study" | "practice" | "review"> = {
  new: "study",
  confused: "practice",
  review: "review",
  solid: "practice",
};

export function TopicPage({ id }: { id: string }) {
  const tx = useT();
  const { dayLabel, fullDate } = useFmt();
  const { canvases, getTopicDetails } = useMocks();
  const { domain } = useShell();
  const soon = useSoon();
  const topic = domain.topics.find((t) => t.id === id);
  const hub = topic?.hubId ? domain.hubs.find((h) => h.id === topic.hubId) : undefined;
  const details = useMemo(() => (topic ? getTopicDetails(domain, topic) : null), [domain, topic, getTopicDetails]);

  if (!topic || (topic.hubId && !hub) || !details) {
    return (
      <PageFrame chipLabel={tx.page.chipSubject(domain.title)}>
        <Crumbs domainTitle={domain.title} />
        <p className="mt-6 text-paper-dim">{tx.topic.missing}</p>
      </PageFrame>
    );
  }

  const title = clean(topic.title);
  const hubTitle = hub ? clean(hub.title) : "";
  const isNew = topic.state === "new";
  const lastMoment = details.moments.at(-1);
  const sessionHref = domain.sessions.find((s) => s.href && s.topics.some((x) => clean(x) === title))?.href;
  const primary = tx.topicCard[PRIMARY[topic.state]];
  const moments = details.moments
    .map((m) => ({ ...m, href: m.sessionId ? domain.sessions.find((s) => s.id === m.sessionId)?.href : undefined }))
    .sort((a, b) => b.date.localeCompare(a.date));

  const actions = [tx.topicCard.study, tx.topicCard.practice, tx.topicCard.review, tx.topicCard.history];

  const windows: Record<string, WindowSpec> = {};
  if (details.notes.length > 0) {
    windows.notes = {
      title: tx.topic.notes,
      meta: notesMeta(details, tx, dayLabel),
      actions: (
        <button type="button" onClick={() => soon(tx.topic.editSoon)} className={TEXT_BTN}>
          {tx.topic.edit}
        </button>
      ),
      body: <NotesContent details={details} />,
    };
  }
  if (details.cards.length > 0) {
    windows.cards = {
      title: tx.topic.cards,
      meta: tx.topic.cardsMeta(details.cards.length, title),
      body: <DeckViewer cards={details.cards} />,
    };
  }
  for (const c of details.canvases) {
    const saved = canvases.find((x) => x.title === c.title);
    if (!saved) continue;
    windows[`canvas:${saved.id}`] = {
      title: c.title,
      meta: `${fullDate(saved.date)} · ${tx.solved[saved.solved]}`,
      actions: (
        <Link href={`/canvas/${saved.id}`} className={TEXT_BTN}>
          {tx.topic.openCanvas}
        </Link>
      ),
      body: (
        <div className="-mx-5 -my-6 flex h-[calc(100%+3rem)] min-h-[360px] md:-mx-8">
          <Board canvas={saved} canvasId={saved.id} mode="view" headingLevel={3} />
        </div>
      ),
    };
  }

  return (
    <ArtifactHost windows={windows}>
    <PageFrame chipLabel={tx.page.chipTopic(title)}>
      <Crumbs domainTitle={domain.title} hub={hub ? { id: hub.id, num: hub.num, title: hubTitle } : undefined} noChapter={hub ? undefined : tx.graph.noChapter} />

      <header className="mt-2 flex items-start justify-between gap-3">
        <h1 className="font-serif text-display font-bold tracking-[-0.01em] text-paper">{title}</h1>
        <MoreMenu
          items={[
            { label: tx.topic.rename, soon: tx.topic.renameSoon(title) },
            { label: tx.topic.merge, soon: tx.topic.mergeSoon },
            { label: tx.topic.move, soon: tx.topic.moveSoon },
            { label: tx.topic.remove, soon: tx.topic.removeSoon },
          ]}
        />
      </header>

      <div className="mt-4 flex items-start gap-4">
        <MemoryRing state={topic.state} retention={topic.retention} size={40} />
        <div className="min-w-0">
          <p className="font-semibold text-paper">{tx.states[topic.state].toLowerCase()}</p>
          <p className="mt-0.5 text-paper-dim">{topic.line}</p>
          {topic.misconception && <p className="mt-1.5 text-st-confused">{topic.misconception}</p>}
          <p className="num mt-2 text-small text-paper-dim">
            {isNew
              ? lastMoment
                ? tx.topic.startedOn(dayLabel(lastMoment.date))
                : tx.topic.notOpened
              : `${tx.topic.holds(Math.round((topic.retention ?? 0) * 100))}${lastMoment ? ` · ${tx.topic.lastTime(dayLabel(lastMoment.date))}` : ""}`}
          </p>
        </div>
      </div>

      <section aria-labelledby="topic-about-h" className="mt-6">
        <h2 id="topic-about-h" className="num text-small tracking-wide text-paper-dim uppercase">
          {tx.topic.about}
        </h2>
        <p className="mt-1.5 max-w-[62ch] text-body leading-relaxed text-paper">{topic.about}</p>
      </section>

      <div className="mt-5 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
        {actions.map((a) =>
          a === tx.topicCard.history ? (
            <button
              key={a}
              type="button"
              onClick={() => {
                scrollToId("topic-sessions", 16);
                flash("topic-sessions-h");
              }}
              className="inline-flex h-10 items-center justify-center rounded-control border border-seam px-4 text-body font-semibold text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
            >
              {a}
            </button>
          ) : (
            <ActionButton key={a} label={a} primary={a === primary} href={sessionHref} soonText={tx.soon.topicAction(a, title)} />
          ),
        )}
      </div>

      <PageSection id="topic-artifacts" title={tx.topic.artifacts}>
        {details.notes.length === 0 ? (
          <p className="text-paper-dim">{tx.topic.noNotes}</p>
        ) : (
          <NotesCard details={details} />
        )}

        {(!isNew || details.cards.length > 0) && (
          <div className="mt-4 flex flex-wrap items-start gap-x-4 gap-y-4">
            {details.cards.length === 0 ? (
              <p className="w-full text-paper-dim">{tx.topic.noCards}</p>
            ) : (
              <DeckCard cards={details.cards} />
            )}
            {details.canvases.length === 0 ? (
              <p className="w-full text-paper-dim">{tx.topic.noSolved}</p>
            ) : (
              details.canvases.map((c) => {
                const saved = canvases.find((x) => x.title === c.title);
                return saved ? (
                  <CanvasCard key={c.id} canvas={saved} caption={c.title} date={c.date} />
                ) : (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => soon(tx.topic.canvasSoon(c.title))}
                    className="group block min-h-10 w-full rounded-[16px] border border-seam bg-cloth-raised p-2.5 text-left transition-[transform,border-color] hover:-translate-y-px hover:border-rule sm:w-[196px]"
                  >
                    <Sketch kind={c.sketch} className="h-[112px] w-full rounded-[14px] bg-cloth transition-colors group-hover:bg-cloth-hover" />
                    <span className="mt-2 block line-clamp-2 px-0.5 text-small leading-snug text-paper">{c.title}</span>
                    <span className="num block px-0.5 pb-0.5 text-small text-paper-dim">{dayLabel(c.date)}</span>
                  </button>
                );
              })
            )}
          </div>
        )}
      </PageSection>

      <PageSection id="topic-materials" title={tx.topic.materials}>
        <MaterialsList items={details.materials} />
      </PageSection>

      <PageSection id="topic-sessions" title={tx.topic.inSessions}>
        <MomentsList items={moments} />
      </PageSection>
    </PageFrame>
    </ArtifactHost>
  );
}
