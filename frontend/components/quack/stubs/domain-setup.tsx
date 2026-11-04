"use client";

import { useRouter } from "next/navigation";
import { useId } from "react";
import { API_MODE } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { NewDomainLive } from "../live/domain-setup-live";
import { BackLink, PageShell } from "../page-parts";
import { DomainSteps } from "./domain-steps";
import { NodeChains } from "./node-chain";
import { PILL_FIELD, PRIMARY_BTN } from "./stub-parts";

/** New subject on mocks: one input; submitting opens the app (the backend flow is NewDomainLive) */
function NewDomainMock() {
  const tx = useT();
  const t = tx.stubs.newDomain;
  const router = useRouter();
  const id = useId();
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <NodeChains />
      <PageShell width={960} className="relative z-10 flex flex-col [&>div]:flex [&>div]:flex-1 [&>div]:flex-col">
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-2 text-center">
        <DomainSteps step={1} />
        <h1 className="display-caps relative z-10 text-[clamp(2rem,4.5vw,3.25rem)] text-paper [text-shadow:0_0_10px_#141311,0_0_4px_#141311]">{t.title}</h1>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            router.push("/");
          }}
          className="flex w-full max-w-[420px] flex-col items-center gap-5"
        >
          <div className="flex w-full items-center gap-3 sm:gap-4 md:gap-6">
            <input
              id={`${id}-text`}
              aria-label={tx.live.domainAsk}
              required
              placeholder={tx.live.domainAskPh}
              className={cn(PILL_FIELD, "relative z-10 min-w-0 flex-1")}
            />
          </div>
          <button type="submit" className={cn(PRIMARY_BTN, "relative z-10")}>
            {tx.live.domainFind}
          </button>
        </form>
      </div>
    </PageShell>
    </div>
  );
}

export function NewDomainStub() {
  return API_MODE ? <NewDomainLive /> : <NewDomainMock />;
}
