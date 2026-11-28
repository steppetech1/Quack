"use client";

import { Loader2, Play } from "lucide-react";
import { useT } from "@/lib/i18n/provider";
import { DocScroll } from "./doc-scroll";
import { useSessionDemos } from "./session-demos";
import { useWorkspace, type WorkspaceDoc } from "./workspace-context";

const ROW =
  "flex min-h-10 w-full items-center gap-3 rounded-[2px] border border-seam bg-cloth-raised px-4 py-3 text-left transition-colors duration-(--dur-s) ease-out hover:border-ribbon focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon disabled:opacity-60 disabled:hover:border-seam";

/** The demos of this session in a list: a click opens one as its own tab (the existing demo document) */
export function DemosDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "demos" }> }) {
  const tx = useT();
  const t = tx.workspace.demosDoc;
  const ws = useWorkspace();
  const demos = useSessionDemos(doc.id);

  return (
    <DocScroll>
      <p className="text-paper-dim">{t.intro}</p>
      {demos.length === 0 ? (
        <p className="mt-4 text-paper-dim">{t.empty}</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {demos.map((d) => (
            <li key={d.requestId}>
              <button
                type="button"
                disabled={d.status !== "ready"}
                aria-label={d.status === "ready" ? t.open(d.title) : `${d.title}: ${t.preparing}`}
                onClick={() => ws.open({ kind: "demo", id: d.requestId, title: d.title, mock: d.mock })}
                className={ROW}
              >
                {d.status === "ready" ? (
                  <Play aria-hidden className="size-4 shrink-0 text-ribbon" />
                ) : (
                  <Loader2 aria-hidden className="size-4 shrink-0 animate-spin text-paper-dim motion-reduce:animate-none" />
                )}
                <span className="min-w-0 flex-1 truncate text-paper">{d.title}</span>
                {d.status !== "ready" && <span className="caps shrink-0 text-paper-dim">{t.preparing}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </DocScroll>
  );
}
