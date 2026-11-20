"use client";

import { ChevronsLeft, ChevronsRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

export type OpenDemo = { requestId: string; title: string };

/**
 * An interactive demo made ahead for a step of the plan, opened on the right of the session. The page is the one
 * the backend serves at `/v1/viz/{id}`: it runs in an iframe with `sandbox="allow-scripts"` only (no same-origin
 * rights, no network: the policy of the page forbids it), so whatever it does stays inside the frame.
 *
 * A drawer over the right edge: about 28 rem wide, twice that when widened, the whole width on a narrow screen. Esc and the
 * cross close it; the focus goes to the close button on opening and back to where it was on closing.
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
      <iframe
        key={demo.requestId}
        src={apiUrl(`/v1/viz/${demo.requestId}`)}
        sandbox="allow-scripts"
        title={p.demoFrame(demo.title)}
        className="min-h-0 w-full flex-1 bg-cloth"
      />
    </aside>
  );
}
