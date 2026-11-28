"use client";

import { ChevronsLeft, ChevronsRight, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

export type OpenDemo = { requestId: string; title: string };

/** A frame that has not said it is loaded after this long is offered a second try */
const LOAD_PATIENCE_MS = 15_000;

/**
 * An interactive demo made ahead for a step of the plan. The page is the one the backend serves at `/v1/viz/{id}`: it
 * runs in an iframe with `sandbox="allow-scripts"` only (no same-origin rights, no network: the policy of the page
 * forbids it), so whatever it does stays inside the frame. The frame fills all the room it is given (the page inside
 * fits its canvas to the device pixels of the frame), so its parent must be a column with `min-h-0` and a height.
 *
 * It lives in a tab of the workspace pane (`workspace/`). `srcDoc` is for mock mode: the page comes from the app.
 *
 * The state under the frame is only what the browser tells: «loaded» is the load event of the frame, which fires for an
 * error page of the server too, so a failure is only «nothing came in time» and the second try reloads the frame.
 */
export function DemoFrame({ demo, srcDoc }: { demo: OpenDemo; srcDoc?: string }) {
  const tx = useT();
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (state !== "loading") return;
    const timer = setTimeout(() => setState("failed"), LOAD_PATIENCE_MS);
    return () => clearTimeout(timer);
  }, [state, attempt]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <iframe
        key={`${demo.requestId}:${attempt}`}
        {...(srcDoc ? { srcDoc } : { src: apiUrl(`/v1/viz/${demo.requestId}`) })}
        sandbox="allow-scripts"
        title={tx.plan.demoFrame(demo.title)}
        onLoad={() => setState("ready")}
        className="min-h-0 w-full flex-1 bg-cloth"
      />
      {state !== "ready" && (
        <div role="status" className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-cloth px-6 text-center">
          {state === "loading" ? (
            <>
              <Loader2 aria-hidden className="size-5 animate-spin text-paper-dim motion-reduce:animate-none" />
              <p className="text-paper-dim">{tx.workspace.demoLoading}</p>
            </>
          ) : (
            <>
              <p className="text-paper">{tx.workspace.demoFailed}</p>
              <button
                type="button"
                onClick={() => {
                  setAttempt((n) => n + 1);
                  setState("loading");
                }}
                className="caps h-10 rounded-pill border border-seam px-5 text-paper transition-colors hover:border-ribbon hover:text-ribbon"
              >
                {tx.workspace.retry}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The same demo as a drawer over the right edge, for places that have no workspace pane (the notes and cheat sheet windows
 * of a topic or a hub): about 28 rem wide, twice that when widened, the whole width on a narrow screen. Esc and the cross
 * close it; the focus goes to the close button on opening and back to where it was on closing.
 */
export function DemoPanel({ demo, onClose }: { demo: OpenDemo; onClose: () => void }) {
  const tx = useT();
  const p = tx.plan;
  const [wide, setWide] = useState(false);
  const close = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      before?.focus?.();
    };
  }, [onClose]);

  const Resize = wide ? ChevronsRight : ChevronsLeft;
  return (
    <aside
      role="dialog"
      aria-label={p.demoPanel}
      className={cn(
        "pointer-events-auto absolute inset-y-0 right-0 z-30 flex w-full flex-col border-l border-seam bg-cloth-raised shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.6)]",
        wide ? "md:w-[min(60rem,92%)]" : "md:w-[min(28rem,92%)]",
      )}
    >
      <header className="flex shrink-0 items-center gap-1 border-b border-seam-soft pl-4">
        <h2 className="caps min-w-0 flex-1 truncate py-3 text-paper">{demo.title}</h2>
        <button
          type="button"
          aria-label={wide ? p.demoNarrow : p.demoWide}
          onClick={() => setWide((w) => !w)}
          className="hidden size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper md:inline-flex"
        >
          <Resize aria-hidden className="size-4" />
        </button>
        <button
          ref={close}
          type="button"
          aria-label={p.demoClose}
          onClick={onClose}
          className="inline-flex size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper"
        >
          <X aria-hidden className="size-4" />
        </button>
      </header>
      <DemoFrame demo={demo} />
    </aside>
  );
}
