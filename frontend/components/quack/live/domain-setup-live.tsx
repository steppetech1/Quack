"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { idem, post, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { BackLink, PageShell } from "../page-parts";
import { NodeChains } from "../stubs/node-chain";
import { DomainSteps } from "../stubs/domain-steps";
import { PILL_FIELD, PRIMARY_BTN, SECONDARY_BTN } from "../stubs/stub-parts";
import { useErrorText } from "./error-text";

/** The first message of a session started from an input box waits here while the page switches to the real id */
export const firstTurnKey = (id: string) => `quack.firstTurn.${id}`;

type Suggestion = S["DomainSuggestion"];

/** The message typed into the default chat (no subjects yet) waits here while the page switches to the subject check */
export const NEW_DOMAIN_TEXT_KEY = "quack.newDomain.text";

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

  const classify = async (message = text) => {
    setBusy(true);
    setError(null);
    try {
      const s = await post("/v1/domains/classify", { text: message.trim() });
      setSuggestion(s);
      setName(s.name ?? "");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  // A message from the default chat: check it at once, once per visit
  const handedOver = useRef(false);
  useEffect(() => {
    if (handedOver.current) return;
    handedOver.current = true;
    let first: string | null = null;
    try {
      first = sessionStorage.getItem(NEW_DOMAIN_TEXT_KEY);
      sessionStorage.removeItem(NEW_DOMAIN_TEXT_KEY);
    } catch {}
    if (!first?.trim()) return;
    setText(first);
    void classify(first);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={t.title} body={t.body} />
      {!suggestion ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void classify();
          }}
        >
          <Field id={`${id}-text`} label={live.domainAsk}>
            <textarea id={`${id}-text`} value={text} onChange={(e) => setText(e.target.value)} required placeholder={live.domainAskPh} className={AREA} />
          </Field>
          {error && (
            <p role="alert" className="mt-4 text-small text-st-confused">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy || !text.trim()} className={cn(PRIMARY_BTN, "mt-6")}>
            {busy ? live.domainChecking : live.domainFind}
          </button>
        </form>
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
              {heading}
            </h1>
            {suggestion && (
              <p className="caps relative z-10 max-w-[420px] truncate text-paper-dim [text-shadow:0_0_10px_#141311,0_0_4px_#141311]" title={text.trim()}>
                {live.domainYouWrote}: “{text.trim()}”
              </p>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
              className="flex w-full max-w-[420px] flex-col items-center gap-5"
            >
              {!suggestion ? (
                <input
                  id={`${id}-text`}
                  aria-label={live.domainAsk}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  required
                  placeholder={live.domainAskPh}
                  className={cn(PILL_FIELD, "relative z-10")}
                />
              ) : suggestion.decision === "existing" ? (
                <div className={cn(PILL_FIELD, "relative z-10 flex items-center justify-center truncate text-paper")}>{existing?.title ?? suggestion.name}</div>
              ) : (
                <input
                  id={`${id}-name`}
                  aria-label={live.domainName}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  autoFocus
                  placeholder={live.domainName}
                  className={cn(PILL_FIELD, "relative z-10")}
                />
              )}
              {error && (
                <p role="alert" className="relative z-10 text-small text-st-confused">
                  {error}
                </p>
              )}
              <div className="relative z-10 flex flex-wrap items-center justify-center gap-2">
                <button
                  type="submit"
                  disabled={busy || (!suggestion ? !text.trim() : unclearOrNew && !name.trim())}
                  className={PRIMARY_BTN}
                >
                  {!suggestion
                    ? busy
                      ? live.domainChecking
                      : live.domainFind
                    : busy
                      ? live.domainStarting
                      : suggestion.decision === "existing"
                        ? live.domainOpen
                        : live.domainCreate}
                </button>
                {suggestion?.decision === "existing" && (
                  <button type="button" disabled={busy} onClick={() => setSuggestion({ decision: "unclear", name: "" })} className={SECONDARY_BTN}>
                    {live.domainOther}
                  </button>
                )}
              </div>
            </form>
          </div>
        </section>
      )}
    </PageShell>
  );
}
