"use client";

import { useRouter } from "next/navigation";
import { useId } from "react";
import { API_MODE } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { NewDomainLive } from "../live/domain-setup-live";
import { BackLink, PageShell } from "../page-parts";
import { NodeChain } from "./node-chain";
import { PILL_FIELD, PRIMARY_BTN } from "./stub-parts";

/** New subject on mocks: one input; submitting opens the app (the backend flow is NewDomainLive) */
function NewDomainMock() {
  const tx = useT();
  const t = tx.stubs.newDomain;
  const router = useRouter();
  const id = useId();
  return (
    <PageShell width={960}>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <div className="flex min-h-[65vh] flex-col items-center justify-center gap-8 px-2 text-center">
        <h1 className="display-caps text-[clamp(2rem,4.5vw,3.25rem)] text-paper">{t.title}</h1>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            router.push("/");
          }}
          className="flex w-full max-w-2xl flex-col items-center gap-5"
        >
          <div className="flex w-full items-center gap-3 sm:gap-4 md:gap-6">
            <NodeChain side="left" className="w-12 shrink-0 sm:w-20 md:w-28 lg:w-40 xl:w-[210px]" />
            <input
              id={`${id}-text`}
              aria-label={tx.live.domainAsk}
              required
              placeholder={tx.live.domainAskPh}
              className={cn(PILL_FIELD, "min-w-0 flex-1")}
            />
            <NodeChain side="right" className="w-12 shrink-0 sm:w-20 md:w-28 lg:w-40 xl:w-[210px]" />
          </div>
          <button type="submit" className={PRIMARY_BTN}>
            {tx.live.domainFind}
          </button>
        </form>
      </div>
    </PageShell>
  );
}

export function NewDomainStub() {
  return API_MODE ? <NewDomainLive /> : <NewDomainMock />;
}
