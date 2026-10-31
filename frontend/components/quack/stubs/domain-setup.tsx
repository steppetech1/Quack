"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { useLocale, useMocks, useT } from "@/lib/i18n/provider";
import { getStubData } from "@/lib/mock/stubs";
import { cn } from "@/lib/utils";
import { API_MODE } from "@/lib/api/client";
import { DiagnosticLive, NewDomainLive, StructureLive } from "../live/domain-setup-live";
import { BackLink, PageShell, PageSection } from "../page-parts";
import { Inline } from "../rich-text";
import { StateDot } from "../topic-card";
import { AREA, DANGER_BTN, FIELD, Field, PRIMARY_BTN, SECONDARY_BTN, StubHeader, StubNote } from "./stub-parts";

const clean = (s: string) => s.replace(/­/g, "");

/** New subject: name, goals, background, focus. Then the duck proposes topics. */
function NewDomainMock() {
  const tx = useT();
  const t = tx.stubs.newDomain;
  const router = useRouter();
  const id = useId();
  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={t.body} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          // Stub: the proposed structure is shown for an existing mock subject
          router.push("/domains/matan/structure");
        }}
      >
        <Field id={`${id}-name`} label={t.name}>
          <input id={`${id}-name`} required placeholder={t.namePh} className={FIELD} />
        </Field>
        <Field id={`${id}-goals`} label={t.goals}>
          <textarea id={`${id}-goals`} placeholder={t.goalsPh} className={AREA} />
        </Field>
        <Field id={`${id}-bg`} label={t.background}>
          <textarea id={`${id}-bg`} placeholder={t.backgroundPh} className={AREA} />
        </Field>
        <Field id={`${id}-focus`} label={t.focus} help={t.focusHelp}>
          <input id={`${id}-focus`} placeholder={t.focusPh} className={FIELD} />
        </Field>
        <button type="submit" className={cn(PRIMARY_BTN, "mt-6")}>
          {t.submit}
        </button>
      </form>
      <StubNote className="mt-10" endpoints={["POST /v1/domains", "POST /v1/domains/{id}/structure/propose"]} />
    </PageShell>
  );
}

/** Proposed structure: skip or add topics, confirm, archive the subject */
function StructureMock({ domainId }: { domainId: string }) {
  const tx = useT();
  const t = tx.stubs.structure;
  const { domains } = useMocks();
  const id = useId();
  const domain = domains.find((d) => d.id === domainId);
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<string[]>([]);
  const [draft, setDraft] = useState("");

  if (!domain) {
    return (
      <PageShell>
        <BackLink href="/">{tx.shell.subjects}</BackLink>
        <p className="mt-6 text-body text-paper-dim">{t.missing}</p>
      </PageShell>
    );
  }

  const titleOf = (topicId: string) => clean(domain.topics.find((x) => x.id === topicId)?.title ?? topicId);
  const toggle = (topicId: string) =>
    setSkipped((s) => {
      const n = new Set(s);
      if (n.has(topicId)) n.delete(topicId);
      else n.add(topicId);
      return n;
    });

  return (
    <PageShell>
      <BackLink href={`/?domain=${domain.id}`}>{domain.title}</BackLink>
      <StubHeader title={t.title(domain.title)} body={t.body} />

      <PageSection id="structure-topics" title={t.topics} count={domain.topics.length + added.length - skipped.size}>
        <ul className="border-t border-seam-soft">
          {domain.topics.map((topic) => {
            const off = skipped.has(topic.id);
            return (
              <li key={topic.id} className="flex min-h-12 items-center gap-3 border-b border-seam-soft py-2">
                <StateDot state={topic.state} />
                <div className={cn("min-w-0 flex-1", off && "opacity-50")}>
                  <p className={cn("text-body text-paper", off && "line-through")}>{clean(topic.title)}</p>
                  {topic.requires.length > 0 && <p className="truncate text-small text-paper-dim">{t.requires(topic.requires.map(titleOf).join(", "))}</p>}
                </div>
                <button type="button" onClick={() => toggle(topic.id)} className="inline-flex h-10 items-center rounded-control px-3 text-small text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper">
                  {off ? t.restore : t.skip}
                </button>
              </li>
            );
          })}
          {added.map((title, i) => (
            <li key={`${title}-${i}`} className="flex min-h-12 items-center gap-3 border-b border-seam-soft py-2">
              <StateDot state="new" />
              <p className="flex-1 text-body text-paper">{title}</p>
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

      <div className="mt-8 flex flex-wrap gap-2">
        <Link href={`/domains/${domain.id}/diagnostic`} className={PRIMARY_BTN}>
          {t.confirm}
        </Link>
        <Link href={`/domains/${domain.id}/diagnostic`} className={SECONDARY_BTN}>
          {t.diagnostic}
        </Link>
      </div>

      <div className="mt-10 border-t border-seam-soft pt-4">
        <button type="button" className={cn(DANGER_BTN, "-ml-3")}>
          {t.archive}
        </button>
        <p className="text-small text-paper-dim">{t.archiveHelp}</p>
      </div>

      <StubNote
        className="mt-10"
        endpoints={[
          "POST /v1/domains/{id}/structure/confirm",
          "PATCH /v1/domains/{id}/structure",
          "PATCH /v1/domains/{id}/topics/{topic_id}/status",
          "POST /v1/domains/{id}/archive",
        ]}
      />
    </PageShell>
  );
}

/** Diagnostic: a few choice questions, then the graph gets priors */
function DiagnosticMock({ domainId }: { domainId: string }) {
  const tx = useT();
  const t = tx.stubs.diagnostic;
  const { locale } = useLocale();
  const { questions } = getStubData(locale);
  const [i, setI] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [answered, setAnswered] = useState(false);
  const [done, setDone] = useState(false);
  const q = questions[i];
  const last = i === questions.length - 1;

  return (
    <PageShell>
      <BackLink href={`/?domain=${domainId}`}>{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={t.body} />

      {done ? (
        <div className="mt-8">
          <p className="text-body text-paper">{t.done}</p>
          <Link href={`/?domain=${domainId}`} className={cn(PRIMARY_BTN, "mt-4")}>
            {t.toGraph}
          </Link>
        </div>
      ) : (
        <section className="mt-8 rounded-[16px] border border-seam p-5" aria-labelledby="diag-q">
          <p className="num text-small text-paper-dim">{t.progress(i + 1, questions.length)}</p>
          <p id="diag-q" className="mt-2 text-body text-paper">
            <Inline text={q.prompt} />
          </p>
          <div role="radiogroup" aria-label={t.options} className="mt-4 grid gap-2">
            {q.options.map((o, k) => {
              const right = answered && k === q.correct;
              const wrong = answered && picked === k && k !== q.correct;
              return (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={picked === k}
                  disabled={answered}
                  onClick={() => setPicked(k)}
                  className={cn(
                    "flex min-h-11 items-center rounded-option border px-3 text-left text-body text-paper transition-colors",
                    picked === k ? "border-ribbon" : "border-seam hover:bg-cloth-hover",
                    right && "border-st-solid",
                    wrong && "border-st-confused",
                  )}
                >
                  <Inline text={o} />
                </button>
              );
            })}
          </div>
          {answered && <p className="mt-3 text-small text-paper-dim">{picked === q.correct ? t.correct : t.wrong}</p>}
          <div className="mt-5 flex flex-wrap gap-2">
            {!answered ? (
              <>
                <button type="button" disabled={picked === null} onClick={() => setAnswered(true)} className={PRIMARY_BTN}>
                  {t.answer}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setPicked(null);
                    setAnswered(true);
                  }}
                  className={SECONDARY_BTN}
                >
                  {t.idk}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => {
                  if (last) setDone(true);
                  else {
                    setI(i + 1);
                    setPicked(null);
                    setAnswered(false);
                  }
                }}
                className={PRIMARY_BTN}
              >
                {last ? t.finish : t.next}
              </button>
            )}
          </div>
        </section>
      )}

      <StubNote
        className="mt-10"
        endpoints={["POST /v1/domains/{id}/diagnostic/start", "POST /v1/domains/{id}/diagnostic/answer", "POST /v1/domains/{id}/diagnostic/finish"]}
      />
    </PageShell>
  );
}

// With the backend the same screens are live (components/quack/live); on mocks they stay stubs
export function NewDomainStub() {
  return API_MODE ? <NewDomainLive /> : <NewDomainMock />;
}

export function StructureStub({ domainId }: { domainId: string }) {
  return API_MODE ? <StructureLive domainId={domainId} /> : <StructureMock domainId={domainId} />;
}

export function DiagnosticStub({ domainId }: { domainId: string }) {
  return API_MODE ? <DiagnosticLive domainId={domainId} /> : <DiagnosticMock domainId={domainId} />;
}
