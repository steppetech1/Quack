"use client";

import { useEffect, useState } from "react";
import { get, idem, post, type S } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { useErrorText } from "./live/error-text";

/** Polls for about four and a half minutes (2 s, then 5 s), then hands the wait back to the learner */
const FAST_POLLS = 15;
const MAX_POLLS = 60;

type Phase =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "polling"; id: string }
  | { kind: "slow"; id: string }
  | { kind: "done"; status: S["ExportStatus"] }
  | { kind: "error"; text: string };

/**
 * «Download everything»: asks the backend for the archive of the account (`POST /v1/learner/export`), polls
 * `GET /v1/learner/exports/{id}` until it is ready, and offers the signed link. A repeat while an archive is
 * on its way or still valid gives the same one, so closing the page and coming back loses nothing.
 */
export function DataExport({ buttonClass }: { buttonClass: string }) {
  const tx = useT().dataExport;
  const label = useT().settings.exportBtn;
  const errorText = useErrorText();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const start = () => {
    setPhase({ kind: "starting" });
    post("/v1/learner/export", { idempotency_key: idem() }).then(
      (r) => setPhase({ kind: "polling", id: r.export_id }),
      (e) => setPhase({ kind: "error", text: errorText(e) }),
    );
  };

  useEffect(() => {
    if (phase.kind !== "polling") return;
    const id = phase.id;
    let dead = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    const tick = async () => {
      try {
        const status = await get<S["ExportStatus"]>(`/v1/learner/exports/${id}`);
        if (dead) return;
        if (status.status !== "pending") {
          setPhase({ kind: "done", status });
          return;
        }
        polls += 1;
        if (polls >= MAX_POLLS) {
          setPhase({ kind: "slow", id });
          return;
        }
        timer = setTimeout(tick, polls < FAST_POLLS ? 2000 : 5000);
      } catch (e) {
        if (!dead) setPhase({ kind: "error", text: errorText(e) });
      }
    };
    void tick();
    return () => {
      dead = true;
      clearTimeout(timer);
    };
  }, [phase, errorText]);

  const busy = phase.kind === "starting" || phase.kind === "polling";
  const done = phase.kind === "done" ? phase.status : null;

  let note: string | null = null;
  if (phase.kind === "starting") note = tx.starting;
  else if (phase.kind === "polling") note = tx.preparing;
  else if (phase.kind === "slow") note = tx.slow;
  else if (phase.kind === "error") note = phase.text;
  else if (done?.status === "ready") note = tx.ready;
  else if (done?.status === "expired") note = tx.expired;
  else if (done?.status === "failed") note = tx.failed;

  return (
    <div className="flex flex-wrap items-center gap-3">
      {done?.status === "ready" && done.download_url ? (
        <a href={done.download_url} download rel="noopener noreferrer" className={buttonClass}>
          {tx.download}
        </a>
      ) : phase.kind === "slow" ? (
        <button type="button" onClick={() => setPhase({ kind: "polling", id: phase.id })} className={buttonClass}>
          {tx.retry}
        </button>
      ) : (
        <button type="button" disabled={busy} onClick={start} className={`${buttonClass} disabled:cursor-not-allowed disabled:opacity-60`}>
          {phase.kind === "done" || phase.kind === "error" ? tx.again : label}
        </button>
      )}
      <p aria-live="polite" className="text-small text-paper-dim">
        {note}
      </p>
    </div>
  );
}
