"use client";

import { useT } from "@/lib/i18n/provider";
import { Duck } from "../duck";
import { PRIMARY_BTN } from "../stubs/stub-parts";

/** One screen before the first session: accept the current terms (`POST /v1/consents {kind: "terms"}`), otherwise a session start answers CONSENT_REQUIRED */
export function TermsGate({ busy, onAccept }: { busy: boolean; onAccept: () => void }) {
  const t = useT().live;
  return (
    <main className="fixed inset-0 z-50 flex overflow-y-auto bg-cloth px-4 py-10">
      <div className="m-auto w-full max-w-[400px]">
        <Duck className="w-32" striped />
        <h1 className="display-caps mt-4 text-display text-paper">{t.termsTitle}</h1>
        <p className="mt-2 text-body text-paper-dim">{t.termsBody}</p>
        <button type="button" disabled={busy} onClick={onAccept} className={`${PRIMARY_BTN} mt-6 w-full`}>
          {t.termsAccept}
        </button>
      </div>
    </main>
  );
}
