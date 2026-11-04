"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { idem, post, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { BackLink, PageShell } from "../page-parts";
import { NodeChain } from "../stubs/node-chain";
import { FIELD, Field, PILL_FIELD, PRIMARY_BTN, SECONDARY_BTN } from "../stubs/stub-parts";
import { useErrorText } from "./error-text";

/** The first message of a session started from an input box waits here while the page switches to the real id */
export const firstTurnKey = (id: string) => `quack.firstTurn.${id}`;

type Suggestion = S["DomainSuggestion"];

/**
 * New subject: only an input, like a normal chat. The first message is classified (`POST /v1/domains/classify`):
 * an existing subject, a new one with a name, or unclear. One tap confirms before the first turn (Т-§22.9),
 * then the subject is created if needed (`POST /v1/domains {name}`) and the message goes in as the first message of a session.
 */
export function NewDomainLive() {
  const tx = useT();
  const t = tx.stubs.newDomain;
  const live = tx.live;
  const router = useRouter();
  const id = useId();
  const errorText = useErrorText();
  const { domains, reload, setActive } = useData();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [name, setName] = useState("");

  const classify = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await post("/v1/domains/classify", { text: text.trim() });
      setSuggestion(s);
      setName(s.name ?? "");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  /** Opens the session in `domainId` and leaves the typed message as its first turn */
  const begin = async (domainId: string) => {
    const s = await startSession({ domain_id: domainId, intent: "free" });
    try {
      sessionStorage.setItem(firstTurnKey(s.session_id), text.trim());
    } catch {}
    setActive(s);
    router.push(`/session/${s.session_id}`);
  };

  const confirm = async () => {
    if (!suggestion) return;
    setBusy(true);
    setError(null);
    try {
      if (suggestion.decision === "existing" && suggestion.domain_id) {
        await begin(suggestion.domain_id);
      } else {
        const created = await post("/v1/domains", { name: name.trim(), idempotency_key: idem() });
        await reload();
        await begin(created.domain_id);
      }
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const existing = suggestion?.decision === "existing" ? domains.find((d) => d.id === suggestion.domain_id) : undefined;

  return (
    <PageShell width={960}>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      {!suggestion ? (
        <div className="flex min-h-[65vh] flex-col items-center justify-center gap-8 px-2 text-center">
          <h1 className="display-caps text-[clamp(2rem,4.5vw,3.25rem)] text-paper">{t.title}</h1>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) void classify();
            }}
            className="flex w-full max-w-2xl flex-col items-center gap-5"
          >
            <div className="flex w-full items-center gap-3 sm:gap-4 md:gap-6">
              <NodeChain side="left" className="w-12 shrink-0 sm:w-20 md:w-28 lg:w-40 xl:w-[210px]" />
              <input
                id={`${id}-text`}
                aria-label={live.domainAsk}
                value={text}
                onChange={(e) => setText(e.target.value)}
                required
                placeholder={live.domainAskPh}
                className={cn(PILL_FIELD, "min-w-0 flex-1")}
              />
              <NodeChain side="right" className="w-12 shrink-0 sm:w-20 md:w-28 lg:w-40 xl:w-[210px]" />
            </div>
            {error && (
              <p role="alert" className="text-small text-st-confused">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy || !text.trim()} className={PRIMARY_BTN}>
              {busy ? live.domainChecking : live.domainFind}
            </button>
          </form>
        </div>
      ) : (
        <section aria-live="polite" className="mt-6">
          <p className="text-body text-paper">
            {suggestion.decision === "existing"
              ? live.domainExisting(existing?.title ?? suggestion.name ?? "")
              : suggestion.decision === "new"
                ? live.domainNew(name)
                : live.domainUnclear}
          </p>
          {suggestion.decision !== "existing" && (
            <Field id={`${id}-name`} label={live.domainName}>
              <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} className={FIELD} />
            </Field>
          )}
          {error && (
            <p role="alert" className="mt-4 text-small text-st-confused">
              {error}
            </p>
          )}
          <div className="mt-6 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || (suggestion.decision !== "existing" && !name.trim())}
              onClick={() => void confirm()}
              className={PRIMARY_BTN}
            >
              {suggestion.decision === "existing" ? live.domainOpen : live.domainCreate}
            </button>
            {suggestion.decision === "existing" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => setSuggestion({ decision: "unclear", name: "" })}
                className={SECONDARY_BTN}
              >
                {live.domainOther}
              </button>
            )}
          </div>
        </section>
      )}
    </PageShell>
  );
}
