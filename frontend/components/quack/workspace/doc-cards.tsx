"use client";

import { BookOpen, Play } from "lucide-react";
import { useT } from "@/lib/i18n/provider";
import { useWorkspaceOptional } from "./workspace-context";

const CARD =
  "relative flex min-h-10 w-full items-center gap-3 rounded-[2px] border border-seam bg-cloth-raised px-4 py-3 text-left transition-colors duration-(--dur-s) ease-out hover:border-ribbon focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon";

/** A demo the duck made, under a turn: opens in the workspace pane. Nothing without a pane */
export function DemoCard({ id, title, mock }: { id: string; title: string; mock?: boolean }) {
  const tx = useT();
  const ws = useWorkspaceOptional();
  if (!ws) return null;
  return (
    <button type="button" aria-label={tx.workspace.openDemo(title)} onClick={() => ws.open({ kind: "demo", id, title, mock })} className={CARD}>
      <Play aria-hidden className="size-4 shrink-0 text-ribbon" />
      <span className="min-w-0 flex-1">
        <span className="caps block text-paper-dim">{tx.workspace.kind.demo}</span>
        <span className="block truncate text-paper">{title}</span>
      </span>
    </button>
  );
}

/** Notes of a topic under a turn (mock mode): a preview and a button that opens them in the workspace pane */
export function NoteRefCard({ id, title, preview }: { id: string; title: string; preview: string }) {
  const tx = useT();
  const ws = useWorkspaceOptional();
  if (!ws) return null;
  return (
    <button
      type="button"
      aria-label={`${tx.workspace.openNotes}: ${title}`}
      onClick={() => ws.open({ kind: "note", id, title, noteKind: "topic" })}
      className={CARD}
    >
      <BookOpen aria-hidden className="size-4 shrink-0 text-ribbon" />
      <span className="min-w-0 flex-1">
        <span className="caps block text-paper-dim">{tx.workspace.kind.note}</span>
        <span className="block truncate text-paper">{title}</span>
        <span className="mt-0.5 block line-clamp-2 text-small text-paper-dim">{preview}</span>
      </span>
    </button>
  );
}
