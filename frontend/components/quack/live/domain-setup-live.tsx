"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, idem, post, patch, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { BackLink, PageShell, PageSection } from "../page-parts";
import { Inline } from "../rich-text";
import { StateDot } from "../topic-card";
import { AREA, DANGER_BTN, FIELD, Field, PRIMARY_BTN, SECONDARY_BTN, StubHeader } from "../stubs/stub-parts";

type Proposal = S["ProposeStructureResponse"];

/** There is no GET for a proposed structure, so the last proposal lives in the tab (sessionStorage) */
const proposalKey = (domainId: string) => `quack.proposal.${domainId}`;
function saveProposal(p: Proposal) {
  try {
    sessionStorage.setItem(proposalKey(p.domain_id), JSON.stringify(p));
  } catch {}
}
function readProposal(domainId: string): Proposal | null {
  try {
    const raw = sessionStorage.getItem(proposalKey(domainId));
    return raw ? (JSON.parse(raw) as Proposal) : null;
  } catch {
    return null;
  }
}

/** Human message for a failed request */
export function useErrorText() {
  const t = useT().live;
  return (e: unknown) => {
    if (e instanceof ApiError) {
      if (e.code === "LLM_DEGRADED" || e.code === "LLM_UNAVAILABLE" || e.status === 503) return t.llmDown;
      if (e.code === "RATE_LIMITED") return t.rateLimited;
      if (e.status < 500) return e.message;
    }
    return t.serverDown;
  };
}

function ErrorLine({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p role="alert" className="mt-4 text-small text-st-confused">
      {text}
    </p>
  );
}

/** Goals / background / focus → the duck proposes topics */
function ProposeForm({ domainId, name, onDone }: { domainId?: string; name?: boolean; onDone: (p: Proposal) => void }) {
  const tx = useT();
  const t = tx.stubs.newDomain;
  const id = useId();
  const errorText = useErrorText();
  const { reload } = useData();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (form: FormData) => {
    setBusy(true);
    setError(null);
    try {
      let target = domainId;
      if (!target) {
        const created = await post("/v1/domains", { name: String(form.get("name")).trim(), idempotency_key: idem() });
        target = created.domain_id;
        // the structure screen looks the subject up in the list
        await reload();
      }
      const focus = String(form.get("focus") ?? "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const proposal = await post(`/v1/domains/${target}/structure/propose`, {
        goals: String(form.get("goals") ?? ""),
        background: String(form.get("background") ?? ""),
        focus_areas: focus,
        idempotency_key: idem(),
      });
      saveProposal(proposal);
      onDone(proposal);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void submit(new FormData(e.currentTarget));
      }}
    >
      {name && (
        <Field id={`${id}-name`} label={t.name}>
          <input id={`${id}-name`} name="name" required placeholder={t.namePh} className={FIELD} />
        </Field>
      )}
      <Field id={`${id}-goals`} label={t.goals}>
        <textarea id={`${id}-goals`} name="goals" placeholder={t.goalsPh} className={AREA} />
      </Field>
      <Field id={`${id}-bg`} label={t.background}>
        <textarea id={`${id}-bg`} name="background" placeholder={t.backgroundPh} className={AREA} />
      </Field>
      <Field id={`${id}-focus`} label={t.focus} help={t.focusHelp}>
        <input id={`${id}-focus`} name="focus" required placeholder={t.focusPh} className={FIELD} />
      </Field>
      <ErrorLine text={error} />
      <button type="submit" disabled={busy} className={cn(PRIMARY_BTN, "mt-6")}>
        {busy ? tx.live.proposing : t.submit}
      </button>
    </form>
  );
}

export function NewDomainLive() {
  const tx = useT();
  const t = tx.stubs.newDomain;
  const router = useRouter();
  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={t.body} />
      <ProposeForm name onDone={(p) => router.push(`/domains/${p.domain_id}/structure`)} />
    </PageShell>
  );
}

/** Proposed topics: skip, add, confirm; or archive the subject */
export function StructureLive({ domainId }: { domainId: string }) {
  const tx = useT();
  const t = tx.stubs.structure;
  const router = useRouter();
  const id = useId();
  const errorText = useErrorText();
  const { domains, reload, loadGraph } = useData();
  const { focusDomain } = useShell();
  const domain = domains.find((d) => d.id === domainId);
  // read once on mount: the proposal is only in this tab's storage
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [checked, setChecked] = useState(false);
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    focusDomain(domainId);
    // sessionStorage is only readable after mount
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setProposal(readProposal(domainId));
    setChecked(true);
  }, [domainId, focusDomain]);

  if (!domain) {
    return (
      <PageShell>
        <BackLink href="/">{tx.shell.subjects}</BackLink>
        <p className="mt-6 text-body text-paper-dim">{t.missing}</p>
      </PageShell>
    );
  }

  const archive = async () => {
    setBusy(true);
    try {
      await post(`/v1/domains/${domainId}/archive`, { idempotency_key: idem() });
      await reload();
      router.push("/");
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const archiveBlock = (
    <div className="mt-10 border-t border-seam-soft pt-4">
      <button type="button" disabled={busy} onClick={() => void archive()} className={cn(DANGER_BTN, "-ml-3")}>
        {t.archive}
      </button>
      <p className="text-small text-paper-dim">{t.archiveHelp}</p>
    </div>
  );

  // Already set up: topics are edited on the graph; here only archiving is left
  if (domain.status === "active" && !proposal) {
    return (
      <PageShell>
        <BackLink href="/">{domain.title}</BackLink>
        <StubHeader title={t.title(domain.title)} body={tx.live.alreadyActive} />
        <ErrorLine text={error} />
        {archiveBlock}
      </PageShell>
    );
  }

  if (!checked) return <PageShell aria-busy="true">{null}</PageShell>;

  // Proposal lost (another tab, reload): ask for a new one
  if (!proposal) {
    return (
      <PageShell>
        <BackLink href="/">{domain.title}</BackLink>
        <StubHeader title={tx.stubs.newDomain.title} body={tx.stubs.newDomain.body} />
        <ProposeForm domainId={domainId} onDone={setProposal} />
        {archiveBlock}
      </PageShell>
    );
  }

  const nameOf = (topicId: string) => proposal.topics.find((x) => x.topic_id === topicId)?.name ?? "";
  const requires = (topicId: string) => proposal.prereqs.filter((e) => e.to_topic_id === topicId).map((e) => nameOf(e.from_topic_id));
  const skills = (topicId: string) => proposal.skills.filter((s) => s.topic_id === topicId).length;
  const kept = proposal.topics.length - skipped.size + added.length;

  const toggle = (topicId: string) =>
    setSkipped((s) => {
      const n = new Set(s);
      if (n.has(topicId)) n.delete(topicId);
      else n.add(topicId);
      return n;
    });

  const confirm = async (then: "diagnostic" | "graph") => {
    setBusy(true);
    setError(null);
    try {
      await post(`/v1/domains/${domainId}/structure/confirm`, { structure_version: proposal.structure_version, idempotency_key: idem() });
      if (added.length) {
        await patch(`/v1/domains/${domainId}/structure`, {
          added_topics: added.map((name) => ({ topic_id: crypto.randomUUID(), name })),
          idempotency_key: idem(),
        });
      }
      for (const topicId of skipped) {
        await patch(`/v1/domains/${domainId}/topics/${topicId}/status`, { status: "skipped", idempotency_key: idem() });
      }
      try {
        sessionStorage.removeItem(proposalKey(domainId));
      } catch {}
      await reload();
      await loadGraph(domainId).catch(() => {});
      router.push(then === "diagnostic" ? `/domains/${domainId}/diagnostic` : "/");
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <PageShell>
      <BackLink href="/">{domain.title}</BackLink>
      <StubHeader title={t.title(domain.title)} body={proposal.degraded ? `${t.body} ${tx.live.proposalDegraded}` : t.body} />

      <PageSection id="structure-topics" title={t.topics} count={kept}>
        <ul className="border-t border-seam-soft">
          {proposal.topics.map((topic) => {
            const off = skipped.has(topic.topic_id);
            const req = requires(topic.topic_id);
            return (
              <li key={topic.topic_id} className="flex min-h-12 items-center gap-3 border-b border-seam-soft py-2">
                <StateDot state="new" />
                <div className={cn("min-w-0 flex-1", off && "opacity-50")}>
                  <p className={cn("text-body text-paper", off && "line-through")}>{topic.name}</p>
                  {topic.description && <p className="text-small text-paper-dim">{topic.description}</p>}
                  <p className="text-small text-paper-dim">
                    {tx.live.skills(skills(topic.topic_id))}
                    {req.length > 0 && ` · ${t.requires(req.join(", "))}`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => toggle(topic.topic_id)}
                  className="inline-flex h-10 items-center rounded-control px-3 text-small text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper"
                >
                  {off ? t.restore : t.skip}
                </button>
              </li>
            );
          })}
          {added.map((title, i) => (
            <li key={`${title}-${i}`} className="flex min-h-12 items-center gap-3 border-b border-seam-soft py-2">
              <StateDot state="new" />
              <p className="flex-1 text-body text-paper">{title}</p>
              <button
                type="button"
                onClick={() => setAdded((a) => a.filter((_, k) => k !== i))}
                className="inline-flex h-10 items-center rounded-control px-3 text-small text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper"
              >
                {t.skip}
              </button>
            </li>
          ))}
        </ul>
        <form
          className="mt-4 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.trim()) return;
            setAdded((a) => [...a, draft.trim()]);
            setDraft("");
          }}
        >
          <label htmlFor={`${id}-add`} className="sr-only">
            {t.addLabel}
          </label>
          <input id={`${id}-add`} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t.addPh} className={FIELD} />
          <button type="submit" className={SECONDARY_BTN}>
            {t.add}
          </button>
        </form>
      </PageSection>

      <ErrorLine text={error} />
      <div className="mt-8 flex flex-wrap gap-2">
        <button type="button" disabled={busy || kept === 0} onClick={() => void confirm("diagnostic")} className={PRIMARY_BTN}>
          {t.confirm}
        </button>
        <button type="button" disabled={busy || kept === 0} onClick={() => void confirm("graph")} className={SECONDARY_BTN}>
          {tx.live.confirmNoCheck}
        </button>
      </div>
      {archiveBlock}
    </PageShell>
  );
}

/** Diagnostic: questions one by one, then the graph gets priors */
export function DiagnosticLive({ domainId }: { domainId: string }) {
  const tx = useT();
  const t = tx.stubs.diagnostic;
  const errorText = useErrorText();
  const { loadGraph } = useData();
  const { focusDomain } = useShell();
  const id = useId();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [q, setQ] = useState<S["DiagnosticQuestion"] | null>(null);
  const [n, setN] = useState(1);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<S["AnswerDiagnosticResponse"] | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    focusDomain(domainId);
    // one diagnostic session per visit, also when dev mode runs effects twice
    if (started.current) return;
    started.current = true;
    post(`/v1/domains/${domainId}/diagnostic/start`, { idempotency_key: idem() })
      .then((r) => {
        setSessionId(r.session_id);
        setQ(r.next_question);
      })
      .catch((e) => setError(errorText(e)))
      .finally(() => setBusy(false));
    // start once per subject
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domainId]);

  const send = async (value: string) => {
    if (!sessionId || !q) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post(`/v1/domains/${domainId}/diagnostic/answer`, {
        session_id: sessionId,
        task_id: q.task_id,
        answer: value,
        idempotency_key: idem(),
      });
      setResult(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const finish = async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      await post(`/v1/domains/${domainId}/diagnostic/finish`, { session_id: sessionId, idempotency_key: idem() });
      await loadGraph(domainId).catch(() => {});
      setDone(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const next = () => {
    if (!result) return;
    if (result.should_finish || !result.next_question) {
      void finish();
      return;
    }
    setQ(result.next_question);
    setN(n + 1);
    setAnswer("");
    setResult(null);
  };

  const last = !!result && (result.should_finish || !result.next_question);

  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={t.body} />

      {done ? (
        <div className="mt-8">
          <p className="text-body text-paper">{t.done}</p>
          <Link href="/" className={cn(PRIMARY_BTN, "mt-4")}>
            {t.toGraph}
          </Link>
        </div>
      ) : q ? (
        <section className="mt-8 rounded-[16px] border border-seam p-5" aria-labelledby="diag-q" aria-busy={busy}>
          <p className="num text-small text-paper-dim">{tx.live.questionNum(n)}</p>
          <p id="diag-q" className="mt-2 text-body text-paper">
            <Inline text={q.prompt_text} />
          </p>
          {q.kind === "choice" && q.options?.length ? (
            <div role="radiogroup" aria-label={t.options} className="mt-4 grid gap-2">
              {q.options.map((o) => (
                <button
                  key={o}
                  type="button"
                  role="radio"
                  aria-checked={answer === o}
                  disabled={!!result}
                  onClick={() => setAnswer(o)}
                  className={cn(
                    "flex min-h-11 items-center rounded-option border px-3 text-left text-body text-paper transition-colors",
                    answer === o ? "border-ribbon" : "border-seam hover:bg-cloth-hover",
                    result && answer === o && (result.is_correct ? "border-st-solid" : "border-st-confused"),
                  )}
                >
                  <Inline text={o} />
                </button>
              ))}
            </div>
          ) : (
            <div className="mt-4">
              <label htmlFor={`${id}-a`} className="sr-only">
                {tx.stubs.practice.answerLabel}
              </label>
              <textarea id={`${id}-a`} value={answer} disabled={!!result} onChange={(e) => setAnswer(e.target.value)} className={AREA} />
            </div>
          )}
          {result && (
            <p role="status" className={cn("mt-3 text-small", result.is_correct ? "text-st-solid" : "text-paper-dim")}>
              {result.is_correct ? t.correct : tx.live.notQuite}
            </p>
          )}
          <ErrorLine text={error} />
          <div className="mt-5 flex flex-wrap gap-2">
            {!result ? (
              <>
                <button type="button" disabled={busy || !answer.trim()} onClick={() => void send(answer)} className={PRIMARY_BTN}>
                  {t.answer}
                </button>
                <button type="button" disabled={busy} onClick={() => void send("")} className={SECONDARY_BTN}>
                  {t.idk}
                </button>
              </>
            ) : (
              <button type="button" disabled={busy} onClick={next} className={PRIMARY_BTN}>
                {last ? t.finish : t.next}
              </button>
            )}
          </div>
        </section>
      ) : (
        <>
          <ErrorLine text={error} />
          {!busy && (
            <Link href="/" className={cn(SECONDARY_BTN, "mt-6")}>
              {t.toGraph}
            </Link>
          )}
        </>
      )}
    </PageShell>
  );
}
