"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, del, get, idem, put, qs, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { BackLink, PageShell, PILL } from "../page-parts";
import { AREA, DANGER_BTN, FIELD, PRIMARY_BTN, SECONDARY_BTN, StubHeader } from "../stubs/stub-parts";
import { useErrorText } from "./error-text";
import { Markdown } from "./markdown";

function ErrorLine({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p role="alert" className="mt-4 text-small text-st-confused">
      {text}
    </p>
  );
}

/**
 * Practice: a practice session on the topic. Tasks are elements of the teacher's turn now, so this screen only
 * starts the session (`intent: practice`) and opens it: the session page sends the intent and the first task streams in.
 */
export function PracticeLive({ topicId }: { topicId: string }) {
  const tx = useT();
  const t = tx.stubs.practice;
  const router = useRouter();
  const errorText = useErrorText();
  const { focusDomain } = useShell();
  const { setActive } = useData();
  const [name, setName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const topic = await get<S["TopicView"]>(`/v1/topics/${topicId}`);
        setName(topic.name);
        focusDomain(topic.domain_id);
        const s = await startSession({ domain_id: topic.domain_id, topic_id: topicId, intent: "practice" });
        setActive(s);
        router.replace(`/session/${s.session_id}`);
      } catch (e) {
        setError(errorText(e));
      }
    })();
    // start once per topic
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topicId]);

  return (
    <PageShell>
      <BackLink href={`/topic/${topicId}`}>{name ?? tx.meta.topic}</BackLink>
      <StubHeader title={t.title(name ?? "…")} />
      {error ? <ErrorLine text={error} /> : <p role="status" className="mt-8 text-paper-dim">{tx.live.preparingTask}</p>}
    </PageShell>
  );
}

/** After "Finish session": the summary of the backend (`GET …/summary`): tasks, answers, skills that moved */
export function SessionSummaryLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.stubs.summary;
  const errorText = useErrorText();
  const [summary, setSummary] = useState<S["SessionSummary"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let off = false;
    get<S["SessionSummary"]>(`/v1/sessions/${id}/summary`)
      .then((s) => !off && setSummary(s))
      .catch((e) => !off && setError(errorText(e)));
    return () => {
      off = true;
    };
    // load once per session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const rows: [string, number][] = [
    [tx.live.summaryTasks, summary?.tasks.length ?? 0],
    [tx.live.summaryAnswers, summary?.tasks.reduce((n, x) => n + x.answers, 0) ?? 0],
    [tx.live.summarySkills, summary?.skills.filter((x) => x.changed).length ?? 0],
  ];
  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} />
      {summary && (
        <dl className="mt-8 grid grid-cols-3 gap-3 sm:max-w-md">
          {rows.map(([label, value]) => (
            <div key={label} className="rounded-[2px] border border-seam p-4">
              <dt className="caps text-paper-dim">{label}</dt>
              <dd className="num mt-1 text-[28px] leading-none text-paper">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <ErrorLine text={error} />
      <p className="mt-6 text-body text-paper-dim">{t.cheat}</p>
      <div className="mt-6 flex flex-wrap gap-2">
        <Link href={`/session/${id}`} className={SECONDARY_BTN}>
          {tx.live.openTranscript}
        </Link>
        <Link href="/" className={PRIMARY_BTN}>
          {t.back}
        </Link>
      </div>
    </PageShell>
  );
}

/** Topics of the current subject (notes, cards and cheat sheets hang on them) and search across the learner's log (`GET /v1/search`) */
export function LibraryLive() {
  const tx = useT();
  const t = tx.stubs.library;
  const errorText = useErrorText();
  const { domain } = useShell();
  const id = useId();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<S["SearchHits"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = async (query: string) => {
    if (query.trim().length < 2) {
      setHits(null);
      return;
    }
    try {
      setHits(await get<S["SearchHits"]>(`/v1/search${qs({ q: query.trim(), domain_id: domain.id })}`));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <PageShell>
      <BackLink href="/">{domain.title || tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={domain.title} />

      <form
        className="mt-6"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void search(q);
        }}
      >
        <label htmlFor={`${id}-q`} className="sr-only">
          {t.search}
        </label>
        <input
          id={`${id}-q`}
          type="search"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            if (!e.target.value) setHits(null);
          }}
          placeholder={t.searchPh}
          className={FIELD}
        />
        {hits && <p className="mt-2 text-small text-paper-dim">{hits.hits.length ? t.results(hits.hits.length) : t.none}</p>}
      </form>

      <ErrorLine text={error} />

      {hits ? (
        <ul className="mt-4 border-t border-seam-soft">
          {hits.hits.map((h) => (
            <li key={h.seq} className="border-b border-seam-soft">
              <Link href={h.session_id ? `/session/${h.session_id}` : "#"} className="block rounded-option px-1 py-3 transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 line-clamp-2 text-body text-paper">{h.snippet}</span>
                  <span className={PILL}>{tx.live.inSession}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      ) : domain.topics.length === 0 ? (
        <p className="mt-6 text-paper-dim">{tx.live.libraryEmpty}</p>
      ) : (
        <ul className="mt-4 border-t border-seam-soft">
          {domain.topics.map((x) => (
            <li key={x.id} className="border-b border-seam-soft">
              <Link href={`/topic/${x.id}`} className="block rounded-option px-1 py-3 transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover">
                <span className="text-body text-paper">{x.title}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PageShell>
  );
}

type NoteBlock = { block_id: string; kind: string; content: string; owner?: string };

/** The notes of a topic as blocks: edit a block (`PUT …/blocks/{id}`) or delete it. The system writes them after sessions; the route id is the topic id */
export function NoteLive({ noteId }: { noteId: string }) {
  const tx = useT();
  const t = tx.stubs.note;
  const errorText = useErrorText();
  const [topic, setTopic] = useState<S["TopicView"] | null>(null);
  const [doc, setDoc] = useState<S["ArtifactResponse"] | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (noteId === "new") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoaded(true);
      return;
    }
    let off = false;
    (async () => {
      try {
        const [tp, notes] = await Promise.all([
          get<S["TopicView"]>(`/v1/topics/${noteId}`),
          get<S["ArtifactResponse"]>(`/v1/topics/${noteId}/notes`).catch((e) => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          }),
        ]);
        if (off) return;
        setTopic(tp);
        setDoc(notes);
      } catch (e) {
        if (!off && !(e instanceof ApiError && (e.status === 404 || e.status === 422))) setError(errorText(e));
      } finally {
        if (!off) setLoaded(true);
      }
    })();
    return () => {
      off = true;
    };
    // load once per topic
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId]);

  const blocks = (doc?.blocks ?? []) as NoteBlock[];

  const save = async (block: NoteBlock) => {
    if (!doc) return;
    setBusy(true);
    setError(null);
    try {
      setDoc(await put(`/v1/artifacts/${doc.artifact_id}/blocks/${block.block_id}`, { content: draft, idempotency_key: idem() }));
      setEditing(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (block: NoteBlock) => {
    if (!doc) return;
    setBusy(true);
    setError(null);
    try {
      setDoc(await del(`/v1/artifacts/${doc.artifact_id}/blocks/${block.block_id}`, { idempotency_key: idem() }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!loaded) return <PageShell aria-busy="true">{error && <ErrorLine text={error} />}</PageShell>;
  if (!topic) {
    return (
      <PageShell>
        <BackLink href="/library">{t.back}</BackLink>
        <p className="mt-6 text-paper-dim">{tx.live.noteMissing}</p>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <BackLink href={`/topic/${noteId}`}>{topic.name}</BackLink>
      <StubHeader title={t.title} body={topic.name} />
      <ErrorLine text={error} />
      {blocks.length === 0 && <p className="mt-6 text-paper-dim">{tx.live.notesEmpty}</p>}
      <div className="mt-6 flex flex-col gap-6">
        {blocks.map((b) => (
          <section key={b.block_id} className="border-b border-seam-soft pb-5">
            {editing === b.block_id ? (
              <>
                <label htmlFor={`b-${b.block_id}`} className="sr-only">
                  {t.body}
                </label>
                <textarea id={`b-${b.block_id}`} value={draft} onChange={(e) => setDraft(e.target.value)} className={cn(AREA, "min-h-32")} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" disabled={busy || !draft.trim()} onClick={() => void save(b)} className={PRIMARY_BTN}>
                    {t.save}
                  </button>
                  <button type="button" disabled={busy} onClick={() => setEditing(null)} className={SECONDARY_BTN}>
                    {tx.live.notesEdit}
                  </button>
                </div>
              </>
            ) : (
              <>
                <Markdown text={b.content} className="space-y-2 text-body text-paper" />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditing(b.block_id);
                      setDraft(b.content);
                    }}
                    className={SECONDARY_BTN}
                  >
                    {tx.live.notesEdit}
                  </button>
                  <button type="button" disabled={busy} onClick={() => void remove(b)} className={DANGER_BTN}>
                    {t.delete}
                  </button>
                </div>
              </>
            )}
          </section>
        ))}
      </div>
    </PageShell>
  );
}
