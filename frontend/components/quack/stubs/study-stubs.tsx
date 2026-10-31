"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { useFmt, useLocale, useMocks, useT } from "@/lib/i18n/provider";
import { getStubData } from "@/lib/mock/stubs";
import { cn } from "@/lib/utils";
import { API_MODE } from "@/lib/api/client";
import { LibraryLive, NoteLive, PracticeLive, SessionSummaryLive } from "../live/study-live";
import { BackLink, PageShell, PageSection, PILL } from "../page-parts";
import { Inline } from "../rich-text";
import { AREA, DANGER_BTN, FIELD, Field, PRIMARY_BTN, SECONDARY_BTN, StubHeader, StubNote } from "./stub-parts";

const clean = (s: string) => s.replace(/­/g, "");

function useTopic(topicId: string | undefined) {
  const { domains } = useMocks();
  for (const d of domains) {
    const topic = d.topics.find((x) => x.id === topicId);
    if (topic) return { domain: d, topic };
  }
  return undefined;
}

/** Practice: one task at a time, hints on request, answer check */
function PracticeMock({ topicId }: { topicId: string }) {
  const tx = useT();
  const t = tx.stubs.practice;
  const { locale } = useLocale();
  const { tasks } = getStubData(locale);
  const found = useTopic(topicId);
  const id = useId();
  const [i, setI] = useState(0);
  const [hints, setHints] = useState(0);
  const [answer, setAnswer] = useState("");
  const [verdict, setVerdict] = useState<"ok" | "no" | null>(null);
  const task = tasks[i % tasks.length];

  if (!found) {
    return (
      <PageShell>
        <BackLink href="/">{tx.shell.subjects}</BackLink>
        <p className="mt-6 text-body text-paper-dim">{t.missing}</p>
      </PageShell>
    );
  }

  const own = Math.max(10, 100 - hints * 30);
  const next = () => {
    setI(i + 1);
    setHints(0);
    setAnswer("");
    setVerdict(null);
  };

  return (
    <PageShell>
      <BackLink href={`/topic/${found.topic.id}`}>{clean(found.topic.title)}</BackLink>
      <StubHeader title={t.title(clean(found.topic.title))} />

      <section aria-labelledby="task-h" className="mt-8 rounded-[16px] border border-seam p-5">
        <h2 id="task-h" className="num text-small text-paper-dim">
          {t.task(i + 1)}
        </h2>
        <p className="mt-2 text-body text-paper">
          <Inline text={task.prompt} />
        </p>

        {hints > 0 && (
          <ol className="mt-4 space-y-2">
            {task.hints.slice(0, hints).map((h, k) => (
              <li key={k} className="rounded-option bg-cloth-raised px-3 py-2 text-small text-paper">
                <span className="text-paper-dim">{t.hintNum(k + 1)}: </span>
                <Inline text={h} />
              </li>
            ))}
          </ol>
        )}

        <form
          className="mt-5"
          onSubmit={(e) => {
            e.preventDefault();
            const norm = answer.replace(/\s|r=|R=/g, "");
            setVerdict(norm === task.answer ? "ok" : "no");
          }}
        >
          <label htmlFor={`${id}-a`} className="mb-1.5 block text-body font-medium text-paper">
            {t.answerLabel}
          </label>
          <input id={`${id}-a`} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={t.answerPh} className={FIELD} />
          {verdict && (
            <p className={cn("mt-3 text-body", verdict === "ok" ? "text-st-solid" : "text-st-confused")} role="status">
              {verdict === "ok" ? `${t.correct}. ${t.own(own)}` : t.wrong}
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {verdict === "ok" ? (
              <button type="button" onClick={next} className={PRIMARY_BTN}>
                {t.next}
              </button>
            ) : (
              <button type="submit" disabled={!answer.trim()} className={PRIMARY_BTN}>
                {t.check}
              </button>
            )}
            {verdict !== "ok" && hints < task.hints.length && (
              <button type="button" onClick={() => setHints(hints + 1)} className={SECONDARY_BTN}>
                {t.hint}
              </button>
            )}
          </div>
          <p className="mt-3 text-small text-paper-dim">{t.hintCost}</p>
        </form>
      </section>

      <StubNote
        className="mt-10"
        endpoints={[
          "POST /v1/sessions (mode: practice)",
          "POST /v1/sessions/{id}/tasks/next",
          "POST /v1/sessions/{id}/tasks/{task_id}/hint",
          "POST /v1/sessions/{id}/tasks/{task_id}/submit",
        ]}
      />
    </PageShell>
  );
}

/** Session summary after "Finish session" */
function SessionSummaryMock() {
  const tx = useT();
  const t = tx.stubs.summary;
  const { locale } = useLocale();
  const { summary } = getStubData(locale);
  const stats: [string, number][] = [
    [t.turns, summary.turns],
    [t.minutes, summary.minutes],
    [t.tasks, summary.tasks],
  ];
  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={t.body(summary.title)} />
      <dl className="mt-8 grid grid-cols-3 gap-3">
        {stats.map(([label, value]) => (
          <div key={label} className="rounded-[14px] border border-seam p-4">
            <dt className="text-small text-paper-dim">{label}</dt>
            <dd className="num mt-1 text-[28px] leading-none text-paper">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-6 text-body text-paper-dim">{t.cheat}</p>
      <div className="mt-6 flex flex-wrap gap-2">
        <Link href={`/topic/${summary.topicId}`} className={PRIMARY_BTN}>
          {tx.page.chipTopic(summary.title)}
        </Link>
        <Link href="/" className={SECONDARY_BTN}>
          {t.back}
        </Link>
      </div>
      <StubNote className="mt-10" endpoints={["POST /v1/sessions/{id}/finish", "GET /v1/sessions/{id}/events (cheatsheet_ready)"]} />
    </PageShell>
  );
}

/** All notes, cheat sheets and session digests + search + export */
function LibraryMock() {
  const tx = useT();
  const t = tx.stubs.library;
  const fmt = useFmt();
  const { locale } = useLocale();
  const { artifacts } = getStubData(locale);
  const id = useId();
  const [q, setQ] = useState("");
  const query = q.trim().toLowerCase();
  const shown = query ? artifacts.filter((a) => `${a.title} ${a.snippet}`.toLowerCase().includes(query)) : artifacts;

  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <StubHeader title={t.title} body={t.body} />
        <Link href="/notes/new" className={PRIMARY_BTN}>
          {t.newNote}
        </Link>
      </div>

      <div className="mt-6">
        <label htmlFor={`${id}-q`} className="sr-only">
          {t.search}
        </label>
        <input id={`${id}-q`} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.searchPh} aria-label={t.search} className={FIELD} />
        {query && <p className="mt-2 text-small text-paper-dim">{shown.length ? t.results(shown.length) : t.none}</p>}
      </div>

      <ul className="mt-4 border-t border-seam-soft">
        {shown.map((a) => (
          <li key={a.id} className="border-b border-seam-soft">
            <Link href={a.kind === "note" ? `/notes/${a.id}` : `/topic/${a.topicId}`} className="block rounded-option px-1 py-3 transition-colors hover:bg-cloth-hover">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-body text-paper">{a.title}</span>
                <span className={PILL}>{t.kinds[a.kind]}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-small text-paper-dim">
                <Inline text={a.snippet} />
              </p>
              <p className="num mt-1 text-small text-paper-dim">{fmt.dayLabel(a.date)}</p>
            </Link>
          </li>
        ))}
      </ul>

      <PageSection id="library-export" title={t.export}>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={SECONDARY_BTN}>
            {t.exportMd}
          </button>
          <button type="button" className={SECONDARY_BTN}>
            {t.exportJson}
          </button>
        </div>
      </PageSection>

      <StubNote
        className="mt-10"
        endpoints={["GET /v1/artifacts", "GET /v1/artifacts/search?q=", "GET /v1/domains/{id}/export?format=md|json"]}
      />
    </PageShell>
  );
}

/** Create / edit / delete a personal note */
function NoteMock({ noteId }: { noteId: string }) {
  const tx = useT();
  const t = tx.stubs.note;
  const router = useRouter();
  const { locale } = useLocale();
  const { domains } = useMocks();
  const id = useId();
  const isNew = noteId === "new";
  const note = getStubData(locale).artifacts.find((a) => a.id === noteId);
  const topics = domains.flatMap((d) => d.topics.map((x) => ({ id: x.id, title: `${d.title} · ${clean(x.title)}` })));

  return (
    <PageShell>
      <BackLink href="/library">{t.back}</BackLink>
      <StubHeader title={isNew ? t.newTitle : (note?.title ?? t.title)} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          router.push("/library");
        }}
      >
        <Field id={`${id}-t`} label={t.titleLabel}>
          <input id={`${id}-t`} required defaultValue={note?.title} placeholder={t.titlePh} className={FIELD} />
        </Field>
        <Field id={`${id}-topic`} label={t.topic}>
          <select id={`${id}-topic`} defaultValue={note?.topicId ?? ""} className={FIELD}>
            <option value="">{t.noTopic}</option>
            {topics.map((x) => (
              <option key={x.id} value={x.id}>
                {x.title}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-b`} label={t.body}>
          <textarea id={`${id}-b`} defaultValue={note?.snippet} placeholder={t.bodyPh} className={cn(AREA, "min-h-48")} />
        </Field>
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <button type="submit" className={PRIMARY_BTN}>
            {t.save}
          </button>
          {!isNew && (
            <button type="button" onClick={() => router.push("/library")} className={DANGER_BTN}>
              {t.delete}
            </button>
          )}
        </div>
      </form>
      <StubNote
        className="mt-10"
        endpoints={["POST /v1/artifacts/notes", "PATCH /v1/artifacts/notes/{id}", "DELETE /v1/artifacts/notes/{id}"]}
      />
    </PageShell>
  );
}

// With the backend the same screens are live (components/quack/live); on mocks they stay stubs
export function PracticeStub({ topicId }: { topicId: string }) {
  return API_MODE ? <PracticeLive topicId={topicId} /> : <PracticeMock topicId={topicId} />;
}

export function SessionSummaryStub({ id }: { id: string }) {
  return API_MODE ? <SessionSummaryLive id={id} /> : <SessionSummaryMock />;
}

export function LibraryStub() {
  return API_MODE ? <LibraryLive /> : <LibraryMock />;
}

export function NoteStub({ noteId }: { noteId: string }) {
  return API_MODE ? <NoteLive noteId={noteId} /> : <NoteMock noteId={noteId} />;
}
