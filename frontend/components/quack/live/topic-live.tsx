"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { topicState } from "@/lib/api/adapters";
import { ApiError, get, idem, post, qs, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useFmt, useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { ArtifactHost, ExpandableCard, type WindowSpec } from "../artifact-window";
import { Crumbs, MemoryRing, PageFrame, PageSection, PILL, TEXT_BTN } from "../page-parts";
import { useErrorText } from "./domain-setup-live";
import { Markdown } from "./markdown";

type Loaded = {
  detail: S["TopicDetailResponse"];
  artifacts: S["ArtifactView"][];
  cheat: S["ArtifactView"] | null;
};

const pct = (x: number) => Math.round(x * 100);
const ACTION_BTN =
  "inline-flex h-10 items-center justify-center rounded-control px-4 text-body font-semibold transition-colors disabled:opacity-50";

/** Topic page on backend data: state, skills, mix-ups, notes and the cheat sheet */
export function TopicLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.live;
  const router = useRouter();
  const { dayLabel } = useFmt();
  const errorText = useErrorText();
  const { domains } = useData();
  const { focusDomain } = useShell();
  const [data, setData] = useState<Loaded | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let off = false;
    (async () => {
      try {
        const detail = await get<S["TopicDetailResponse"]>(`/v1/topics/${id}`);
        const [list, cheat] = await Promise.all([
          get<S["ArtifactListResponse"]>(`/v1/artifacts${qs({ topic_id: id })}`),
          get<S["ArtifactView"] | null>(`/v1/topics/${id}/cheatsheet`).catch(() => null),
        ]);
        if (off) return;
        focusDomain(detail.topic.domain_id);
        setData({ detail, artifacts: list.artifacts.filter((a) => a.kind !== "cheatsheet"), cheat });
      } catch (e) {
        if (off) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) setMissing(true);
        else setError(errorText(e));
      }
    })();
    return () => {
      off = true;
    };
    // load once per topic
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const domain = domains.find((d) => d.id === data?.detail.topic.domain_id);
  const domainTitle = domain?.title ?? "";

  if (missing || error) {
    return (
      <PageFrame chipLabel={tx.page.chipSubject(domainTitle)}>
        <Crumbs domainTitle={domainTitle || tx.shell.subjects} />
        <p role={error ? "alert" : undefined} className="mt-6 text-paper-dim">
          {error ?? tx.topic.missing}
        </p>
      </PageFrame>
    );
  }
  if (!data) return <div className="flex-1" aria-busy="true" />;

  const { topic, skills, misconceptions } = data.detail;
  const state = topicState(topic.display_state);
  const activeMix = misconceptions.filter((m) => m.status !== "fixed" && m.status !== "archived");

  const begin = async (mode: "learn" | "review") => {
    setBusy(true);
    try {
      const s = await startSession({ domain_id: topic.domain_id, topic_id: topic.topic_id, mode });
      router.push(`/session/${s.session_id}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const refreshCheat = async () => {
    setBusy(true);
    try {
      await post(`/v1/topics/${id}/cheatsheet/refresh`, { domain_id: topic.domain_id });
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const windows: Record<string, WindowSpec> = {};
  const docs = [...(data.cheat ? [data.cheat] : []), ...data.artifacts];
  for (const a of docs) {
    windows[`a-${a.artifact_id}`] = {
      title: a.title,
      meta: `${a.kind === "cheatsheet" ? tx.stubs.library.kinds.cheatsheet : a.kind === "note" ? tx.stubs.library.kinds.note : a.kind} · ${dayLabel(a.updated_at.slice(0, 10))}`,
      actions:
        a.kind === "note" ? (
          <Link href={`/notes/${a.artifact_id}`} className={TEXT_BTN}>
            {tx.topic.edit}
          </Link>
        ) : undefined,
      body: <Markdown text={a.content_md} />,
    };
  }

  return (
    <ArtifactHost windows={windows}>
      <PageFrame chipLabel={tx.page.chipTopic(topic.name)}>
        <Crumbs domainTitle={domainTitle} />

        <header className="mt-2">
          <h1 className="font-serif text-display font-bold tracking-[-0.01em] text-paper">{topic.name}</h1>
        </header>

        <div className="mt-4 flex items-start gap-4">
          <MemoryRing state={state} retention={topic.mastery} size={40} />
          <div className="min-w-0">
            <p className="font-semibold text-paper">{tx.states[state].toLowerCase()}</p>
            {topic.description && <p className="mt-0.5 text-paper-dim">{topic.description}</p>}
            {activeMix[0] && <p className="mt-1.5 text-st-confused">{activeMix[0].name}</p>}
            <p className="num mt-2 text-small text-paper-dim">{t.masteryLine(pct(topic.mastery), pct(topic.coverage))}</p>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
          <button
            type="button"
            disabled={busy}
            onClick={() => void begin("learn")}
            className={cn(ACTION_BTN, state === "new" ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-rule hover:bg-cloth-hover")}
          >
            {tx.topicCard.study}
          </button>
          <Link
            href={`/practice/${topic.topic_id}`}
            className={cn(ACTION_BTN, state !== "new" && state !== "review" ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-rule hover:bg-cloth-hover")}
          >
            {tx.topicCard.practice}
          </Link>
          <button
            type="button"
            disabled={busy}
            onClick={() => void begin("review")}
            className={cn(ACTION_BTN, state === "review" ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-rule hover:bg-cloth-hover")}
          >
            {tx.topicCard.review}
          </button>
        </div>

        <PageSection id="topic-skills" title={t.skillsTitle} count={skills.length}>
          {skills.length === 0 ? (
            <p className="text-paper-dim">{t.noSkills}</p>
          ) : (
            <ul className="border-t border-seam-soft">
              {skills.map((s) => (
                <li key={s.skill_id} className="flex min-h-12 items-center gap-3 border-b border-seam-soft py-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-body text-paper">{s.name}</p>
                    {s.description && <p className="text-small text-paper-dim">{s.description}</p>}
                  </div>
                  {s.is_rusty && <span className={PILL}>{t.rusty}</span>}
                  <span className="num shrink-0 text-small text-paper-dim">{pct(s.effective_mastery)}%</span>
                </li>
              ))}
            </ul>
          )}
        </PageSection>

        {activeMix.length > 0 && (
          <PageSection id="topic-mix" title={t.mixups} count={activeMix.length}>
            <ul className="space-y-3">
              {activeMix.map((m) => (
                <li key={m.misconception_id}>
                  <p className="text-body text-st-confused">{m.name}</p>
                  {m.description && <p className="text-small text-paper-dim">{m.description}</p>}
                  <p className="num text-small text-paper-dim">{t.seenTimes(m.occurrences)}</p>
                </li>
              ))}
            </ul>
          </PageSection>
        )}

        <PageSection
          id="topic-artifacts"
          title={tx.topic.artifacts}
          action={
            <Link href={`/notes/new?topic=${topic.topic_id}&domain=${topic.domain_id}`} className={TEXT_BTN}>
              {tx.stubs.library.newNote}
            </Link>
          }
        >
          {docs.length === 0 ? (
            <p className="text-paper-dim">{tx.topic.noNotes}</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {docs.map((a) => (
                <ExpandableCard key={a.artifact_id} artifact={`a-${a.artifact_id}`}>
                  <span className="block px-4 pt-3 font-semibold text-paper">{a.title}</span>
                  <span aria-hidden className="mt-1 block h-[72px] overflow-hidden px-4 text-small leading-relaxed text-paper-dim [mask-image:linear-gradient(to_bottom,#000_55%,transparent)]">
                    {a.content_md.slice(0, 280)}
                  </span>
                  <span className="num block px-4 pb-3 text-small text-paper-dim">
                    {a.kind === "cheatsheet" ? tx.stubs.library.kinds.cheatsheet : tx.stubs.library.kinds.note} · {dayLabel(a.updated_at.slice(0, 10))}
                  </span>
                </ExpandableCard>
              ))}
            </div>
          )}
          <button type="button" disabled={busy} onClick={() => void refreshCheat()} className={cn(TEXT_BTN, "mt-3 -ml-3")}>
            {data.cheat ? t.refreshCheat : t.buildCheat}
          </button>
        </PageSection>
      </PageFrame>
    </ArtifactHost>
  );
}
