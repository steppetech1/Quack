"use client";

import { useRouter } from "next/navigation";
import { useId } from "react";
import { API_MODE } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { NewDomainLive } from "../live/domain-setup-live";
import { BackLink, PageShell } from "../page-parts";
import { FIELD, Field, PRIMARY_BTN, StubHeader } from "./stub-parts";

/** New subject on mocks: one input; submitting opens the app (the backend flow is NewDomainLive) */
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
          router.push("/");
        }}
      >
        <Field id={`${id}-text`} label={tx.live.domainAsk}>
          <input id={`${id}-text`} required placeholder={tx.live.domainAskPh} className={FIELD} />
        </Field>
        <button type="submit" className={cn(PRIMARY_BTN, "mt-6")}>
          {tx.live.domainFind}
        </button>
      </form>
    </PageShell>
  );
}

export function NewDomainStub() {
  return API_MODE ? <NewDomainLive /> : <NewDomainMock />;
}
