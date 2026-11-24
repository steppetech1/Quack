"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { idem, post } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { uploadInBackground } from "@/lib/api/domain-uploads";
import { useT } from "@/lib/i18n/provider";
import { BackLink, PageShell } from "../page-parts";
import { NodeChains } from "../stubs/node-chain";
import type { FileRef } from "../attachments";
import { DomainCreateCard, type NewDomainValues } from "./domain-create-card";
import { useErrorText } from "./error-text";

/** The first message of a session started from an input box waits here while the page switches to the real id */
export const firstTurnKey = (id: string) => `quack.firstTurn.${id}`;

/** What waits under `firstTurnKey`: the plain text, or JSON `{text, files}` when the message carries attached files */
export function encodeFirstTurn(text: string, files: FileRef[] = []): string {
  return files.length ? JSON.stringify({ text, files }) : text;
}

export function decodeFirstTurn(raw: string): { text: string; files: FileRef[] } {
  if (raw.startsWith("{")) {
    try {
      const v = JSON.parse(raw) as { text?: unknown; files?: unknown };
      if (typeof v.text === "string") {
        const files = Array.isArray(v.files) ? v.files.filter((f): f is FileRef => !!f && typeof (f as FileRef).id === "string" && typeof (f as FileRef).name === "string") : [];
        return { text: v.text, files };
      }
    } catch {}
  }
  return { text: raw, files: [] };
}

/**
 * The «new subject» screen: a heading and the card (`DomainCreateCard`). Shared by the backend flow and by mocks, which differ
 * only in what «Create» does.
 */
export function NewDomainScreen({ onCreate, busy, error, onClearError }: { onCreate: (v: NewDomainValues) => void; busy: boolean; error: string | null; onClearError: () => void }) {
  const tx = useT();
  const { domains } = useData();
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <NodeChains />
      <PageShell width={960} className="relative z-10 flex flex-col [&>div]:flex [&>div]:flex-1 [&>div]:flex-col">
        <BackLink href="/">{tx.shell.subjects}</BackLink>
        <div className="flex flex-1 flex-col items-center justify-center gap-6 px-2 py-6 text-center">
          <h1 className="display-caps relative z-10 text-balance text-[clamp(2rem,4.5vw,3.25rem)] text-paper [text-shadow:0_0_10px_#141311,0_0_4px_#141311]">{tx.stubs.newDomain.title}</h1>
          <DomainCreateCard existing={domains.map((d) => ({ id: d.id, title: d.title }))} onCreate={onCreate} busy={busy} error={error} onClearError={onClearError} />
        </div>
      </PageShell>
    </div>
  );
}

/**
 * New subject on the backend: `POST /v1/domains {name, about?, idempotency_key}`, then the page of the subject opens (its graph).
 * No session starts and nothing goes to `firstTurnKey`. The files of the card are uploaded into the new subject in the background
 * after it exists (`uploadInBackground`): the learner may leave, the page of the subject shows the agents at work.
 * The classifier (`POST /v1/domains/classify`) is not used here; it stays on the backend.
 */
export function NewDomainLive() {
  const router = useRouter();
  const errorText = useErrorText();
  const { reload } = useData();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // a retry of the same card sends the same key (the backend answers with the same subject); other words, another key
  const key = useRef({ sig: "", value: "" });

  const create = async ({ name, about, files }: NewDomainValues) => {
    setBusy(true);
    setError(null);
    const sig = `${name}\u0000${about}`;
    if (key.current.sig !== sig) key.current = { sig, value: idem() };
    try {
      const made = await post("/v1/domains", { name, ...(about ? { about } : {}), idempotency_key: key.current.value });
      await reload();
      uploadInBackground(made.domain_id, files);
      router.push(`/?domain=${made.domain_id}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return <NewDomainScreen onCreate={(v) => void create(v)} busy={busy} error={error} onClearError={() => setError(null)} />;
}
