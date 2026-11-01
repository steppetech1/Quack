"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { del, get, idem, patch, post, qs, sse, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { readSummary, startSession } from "@/lib/api/sessions";
import { useFmt, useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { BackLink, PageShell, PageSection, PILL } from "../page-parts";
import { Inline, RichText } from "../rich-text";
import { AREA, DANGER_BTN, FIELD, Field, PRIMARY_BTN, SECONDARY_BTN, StubHeader } from "../stubs/stub-parts";
import { useErrorText } from "./domain-setup-live";
import { Markdown, plainText } from "./markdown";

function ErrorLine({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p role="alert" className="mt-4 text-small text-st-confused">
      {text}
    </p>
  );
}

/**
 * Practice: a practice session on the topic. Tasks hang on a turn, so the screen opens
 * the session with one short student turn and then asks for tasks, hints and checks.
 */
export function PracticeLive({ topicId }: { topicId: string }) {
  const tx = useT();
  const t = tx.stubs.practice;
  const errorText = useErrorText();
  const { focusDomain } = useShell();
  const id = useId();
  const [topic, setTopic] = useState<S["TopicNodeView"] | null>(null);
  const [ctx, setCtx] = useState<{ sessionId: string; turnId: string } | null>(null);
  const [task, setTask] = useState<{ task_id: string; prompt_text: string } | null>(null);
  const [n, setN] = useState(1);
  const [hints, setHints] = useState<S["HintResponse"][]>([]);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<S["SubmitResponse"] | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const nextTask = async (c: { sessionId: string; turnId: string }) => {
    const r = await post<S["NextTaskResponse"]>(`/v1/sessions/${c.sessionId}/tasks/next`, { topic_id: topicId, turn_id: c.turnId, idempotency_key: idem() });
    setTask({ task_id: r.task_id, prompt_text: r.prompt_text });
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const d = await get<S["TopicDetailResponse"]>(`/v1/topics/${topicId}`);
        setTopic(d.topic);
        focusDomain(d.topic.domain_id);
        const s = await startSession({ domain_id: d.topic.domain_id, topic_id: topicId, mode: "practice" });
        let turnId = "";
        let assigned: { task_id: string; prompt_text: string } | null = null;
        await sse("POST", `/v1/sessions/${s.session_id}/turns`, { text: tx.live.practiceKickoff, idempotency_key: idem() }, ({ event, data }) => {
          if (event === "turn_started") turnId = String(data.turn_id);
          if (event === "task_assigned") assigned = { task_id: String(data.task_id), prompt_text: String(data.prompt_text) };
        });
        const c = { sessionId: s.session_id, turnId };
        setCtx(c);
        if (assigned) setTask(assigned);
        else await nextTask(c);
      } catch (e) {
        setError(errorText(e));
      } finally {
        setBusy(false);
      }
    })();
    // start once per topic
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topicId]);

  const hint = async () => {
    if (!ctx || !task) return;
    setBusy(true);
    try {
      const h = await post<S["HintResponse"]>(`/v1/sessions/${ctx.sessionId}/tasks/${task.task_id}/hint`, { turn_id: ctx.turnId, idempotency_key: idem() });
      setHints((x) => [...x, h]);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const check = async () => {
    if (!ctx || !task) return;
    setBusy(true);
    try {
      const r = await post<S["SubmitResponse"]>(`/v1/sessions/${ctx.sessionId}/tasks/${task.task_id}/submit`, {
        answer,
        turn_id: ctx.turnId,
        idempotency_key: idem(),
      });
      setResult(r);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const next = async () => {
    if (!ctx) return;
    setBusy(true);
    setHints([]);
    setAnswer("");
    setResult(null);
    try {
      await nextTask(ctx);
      setN(n + 1);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell>
      <BackLink href={`/topic/${topicId}`}>{topic?.name ?? tx.meta.topic}</BackLink>
      <StubHeader title={t.title(topic?.name ?? "…")} />

      {task ? (
        <section aria-labelledby="task-h" aria-busy={busy} className="mt-8 rounded-[16px] border border-seam p-5">
          <h2 id="task-h" className="num text-small text-paper-dim">
            {t.task(n)}
          </h2>
          <div className="mt-2 text-body text-paper">
            <RichText text={task.prompt_text} />
          </div>

          {hints.length > 0 && (
            <ol className="mt-4 space-y-2">
              {hints.map((h) => (
                <li key={h.hint_level} className="rounded-option bg-cloth-raised px-3 py-2 text-small text-paper">
                  <span className="text-paper-dim">{t.hintNum(h.hint_level)}: </span>
                  <Inline text={h.hint_text} />
                </li>
              ))}
            </ol>
          )}

          <form
            className="mt-5"
            onSubmit={(e) => {
              e.preventDefault();
              void check();
            }}
          >
            <label htmlFor={`${id}-a`} className="mb-1.5 block text-body font-medium text-paper">
              {t.answerLabel}
            </label>
            <textarea id={`${id}-a`} value={answer} disabled={!!result} onChange={(e) => setAnswer(e.target.value)} className={cn(AREA, "min-h-16")} />
            {result && (
              <p role="status" className={cn("mt-3 text-body", result.is_correct ? "text-st-solid" : "text-st-confused")}>
                {result.is_correct ? `${t.correct}. ${t.own(Math.round(result.independence * 100))}` : tx.live.notQuite}
                {result.feedback && <span className="block text-small text-paper-dim">{result.feedback}</span>}
              </p>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              {result ? (
                <button type="button" disabled={busy} onClick={() => void next()} className={PRIMARY_BTN}>
                  {t.next}
                </button>
              ) : (
                <>
                  <button type="submit" disabled={busy || !answer.trim()} className={PRIMARY_BTN}>
                    {t.check}
                  </button>
                  <button type="button" disabled={busy} onClick={() => void hint()} className={SECONDARY_BTN}>
                    {t.hint}
                  </button>
                </>
              )}
            </div>
            <p className="mt-3 text-small text-paper-dim">{t.hintCost}</p>
          </form>
        </section>
      ) : (
        busy && !error && <p role="status" className="mt-8 text-paper-dim">{tx.live.preparingTask}</p>
      )}
      <ErrorLine text={error} />
    </PageShell>
  );
}

/** After "Finish session": the stats finish returned */
export function SessionSummaryLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.stubs.summary;
  const [summary, setSummary] = useState<S["FinishResponse"] | null>(null);

  useEffect(() => {
    // sessionStorage is only readable after mount
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSummary(readSummary(id));
  }, [id]);

  const stats = summary?.summary_stats as { turns_count?: number; duration_sec?: number } | undefined;
  const rows: [string, number][] = [
    [t.turns, stats?.turns_count ?? 0],
    [t.minutes, Math.round((stats?.duration_sec ?? 0) / 60)],
  ];
  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} />
      {summary && (
        <dl className="mt-8 grid grid-cols-2 gap-3 sm:max-w-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="rounded-[14px] border border-seam p-4">
              <dt className="text-small text-paper-dim">{label}</dt>
              <dd className="num mt-1 text-[28px] leading-none text-paper">{value}</dd>
            </div>
          ))}
        </dl>
      )}
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

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** All notes and cheat sheets of the current subject, search, export */
export function LibraryLive() {
  const tx = useT();
  const t = tx.stubs.library;
  const { dayLabel } = useFmt();
  const errorText = useErrorText();
  const { domain } = useShell();
  const id = useId();
  const [items, setItems] = useState<S["ArtifactView"][] | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<S["SearchResponse"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!domain.id) return;
    let off = false;
    get<S["ArtifactListResponse"]>(`/v1/artifacts${qs({ domain_id: domain.id })}`)
      .then((r) => !off && setItems(r.artifacts))
      .catch((e) => !off && setError(errorText(e)));
    return () => {
      off = true;
    };
    // reload when the subject changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain.id]);

  const search = async (query: string) => {
    if (query.trim().length < 2) {
      setHits(null);
      return;
    }
    try {
      setHits(await get<S["SearchResponse"]>(`/v1/artifacts/search${qs({ q: query.trim(), domain_id: domain.id })}`));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const exportAs = async (format: "md" | "json") => {
    try {
      const r = await get<S["ExportMdResponse"] | S["ExportJson"]>(`/v1/domains/${domain.id}/export${qs({ format })}`);
      if (format === "md" && "content_md" in r) download(`${domain.title}.md`, r.content_md, "text/markdown");
      else download(`${domain.title}.json`, JSON.stringify(r, null, 2), "application/json");
    } catch (e) {
      setError(errorText(e));
    }
  };

  const kind = (k: string) => (k in t.kinds ? t.kinds[k as keyof typeof t.kinds] : k);
  const hitIds = hits ? new Set(hits.artifact_hits.map((h) => h.artifact_id)) : null;
  const shown = (items ?? []).filter((a) => !hitIds || hitIds.has(a.artifact_id));
  const href = (a: { artifact_id: string; kind: string; topic_id: string | null }) =>
    a.kind === "note" ? `/notes/${a.artifact_id}` : a.topic_id ? `/topic/${a.topic_id}` : `/notes/${a.artifact_id}`;

  return (
    <PageShell>
      <BackLink href="/">{domain.title || tx.shell.subjects}</BackLink>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <StubHeader title={t.title} body={domain.title} />
        <Link href={`/notes/new?domain=${domain.id}`} className={PRIMARY_BTN}>
          {t.newNote}
        </Link>
      </div>

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
        {hits && <p className="mt-2 text-small text-paper-dim">{shown.length + hits.event_hits.length ? t.results(shown.length + hits.event_hits.length) : t.none}</p>}
      </form>

      <ErrorLine text={error} />

      {items && items.length === 0 && !hits && <p className="mt-6 text-paper-dim">{tx.live.libraryEmpty}</p>}
      <ul className="mt-4 border-t border-seam-soft">
        {shown.map((a) => (
          <li key={a.artifact_id} className="border-b border-seam-soft">
            <Link href={href(a)} className="block rounded-option px-1 py-3 transition-colors hover:bg-cloth-hover">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-body text-paper">{a.title}</span>
                <span className={PILL}>{kind(a.kind)}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-small text-paper-dim">{plainText(a.content_md, 200)}</p>
              <p className="num mt-1 text-small text-paper-dim">{dayLabel(a.updated_at.slice(0, 10))}</p>
            </Link>
          </li>
        ))}
        {hits?.event_hits.map((h) => (
          <li key={`e-${h.seq}`} className="border-b border-seam-soft">
            <Link href={h.session_id ? `/session/${h.session_id}` : "#"} className="block rounded-option px-1 py-3 transition-colors hover:bg-cloth-hover">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 line-clamp-2 text-body text-paper">{h.text}</span>
                <span className={PILL}>{tx.live.inSession}</span>
              </div>
              <p className="num mt-1 text-small text-paper-dim">{dayLabel(h.occurred_at.slice(0, 10))}</p>
            </Link>
          </li>
        ))}
      </ul>

      <PageSection id="library-export" title={t.export}>
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={!domain.id} onClick={() => void exportAs("md")} className={SECONDARY_BTN}>
            {t.exportMd}
          </button>
          <button type="button" disabled={!domain.id} onClick={() => void exportAs("json")} className={SECONDARY_BTN}>
            {t.exportJson}
          </button>
        </div>
      </PageSection>
    </PageShell>
  );
}

/** Create / edit / delete a note. There is no GET for one artifact, so it is found in the list */
export function NoteLive({ noteId }: { noteId: string }) {
  const tx = useT();
  const t = tx.stubs.note;
  const router = useRouter();
  const params = useSearchParams();
  const errorText = useErrorText();
  const { domain } = useShell();
  const { domains } = useData();
  const id = useId();
  const isNew = noteId === "new";
  const [note, setNote] = useState<S["ArtifactView"] | null>(null);
  const [loaded, setLoaded] = useState(isNew);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isNew) return;
    let off = false;
    get<S["ArtifactListResponse"]>(`/v1/artifacts${qs({ kind: "note" })}`)
      .then((r) => {
        if (off) return;
        setNote(r.artifacts.find((a) => a.artifact_id === noteId) ?? null);
        setLoaded(true);
      })
      .catch((e) => !off && setError(errorText(e)));
    return () => {
      off = true;
    };
    // load once per note
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId, isNew]);

  const domainId = note?.domain_id ?? params.get("domain") ?? domain.id;
  const topics = domains.find((d) => d.id === domainId)?.topics ?? [];
  const topicDefault = note?.topic_id ?? params.get("topic") ?? "";

  const save = async (form: FormData) => {
    setBusy(true);
    setError(null);
    const title = String(form.get("title") ?? "").trim();
    const text = String(form.get("text") ?? "");
    const topic = String(form.get("topic") ?? "");
    try {
      if (isNew) {
        await post("/v1/artifacts/notes", { domain_id: domainId, topic_id: topic || null, title, text, idempotency_key: idem() });
      } else {
        await patch(`/v1/artifacts/notes/${noteId}`, { title, text, idempotency_key: idem() });
      }
      router.push(topic ? `/topic/${topic}` : "/library");
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await del(`/v1/artifacts/notes/${noteId}`, { idempotency_key: idem() });
      router.push("/library");
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  if (!loaded) return <PageShell aria-busy="true">{error && <ErrorLine text={error} />}</PageShell>;
  if (!isNew && !note) {
    return (
      <PageShell>
        <BackLink href="/library">{t.back}</BackLink>
        <p className="mt-6 text-paper-dim">{tx.live.noteMissing}</p>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <BackLink href="/library">{t.back}</BackLink>
      <StubHeader title={isNew ? t.newTitle : (note?.title ?? t.title)} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save(new FormData(e.currentTarget));
        }}
      >
        <Field id={`${id}-t`} label={t.titleLabel}>
          <input id={`${id}-t`} name="title" required defaultValue={note?.title} placeholder={t.titlePh} className={FIELD} />
        </Field>
        <Field id={`${id}-topic`} label={t.topic}>
          <select id={`${id}-topic`} name="topic" disabled={!isNew} defaultValue={topicDefault} className={FIELD}>
            <option value="">{t.noTopic}</option>
            {topics.map((x) => (
              <option key={x.id} value={x.id}>
                {x.title}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-b`} label={t.body}>
          <textarea id={`${id}-b`} name="text" required defaultValue={note?.content_md} placeholder={t.bodyPh} className={cn(AREA, "min-h-48")} />
        </Field>
        {note && (
          <details className="mt-4">
            <summary className="cursor-pointer text-small text-paper-dim">{tx.live.preview}</summary>
            <Markdown text={note.content_md} className="mt-2 space-y-2 text-body text-paper" />
          </details>
        )}
        <ErrorLine text={error} />
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <button type="submit" disabled={busy} className={PRIMARY_BTN}>
            {t.save}
          </button>
          {!isNew && (
            <button type="button" disabled={busy} onClick={() => void remove()} className={DANGER_BTN}>
              {t.delete}
            </button>
          )}
        </div>
      </form>
    </PageShell>
  );
}
