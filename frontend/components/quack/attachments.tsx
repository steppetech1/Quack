"use client";

import { FileText, Loader2, Paperclip, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { get, type S } from "@/lib/api/client";
import { MATERIAL_POLL_LIMIT, materialReadable, materialState } from "@/lib/api/materials";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useMaterialUpload } from "./live/use-material-upload";
import { useWorkspaceOptional } from "./workspace/workspace-context";

/** How many files one message carries (the teacher reads the last few of a session, `material_attached_files`) */
export const MAX_ATTACHED = 3;
/** The status of a file that was just uploaded is asked again this often: its text is stored within seconds */
const READ_POLL_MS = 2000;

export type AttachmentState = "uploading" | "reading" | "ready" | "failed";
export type Attachment = { key: string; name: string; materialId: string | null; state: AttachmentState };
/** What a sent message remembers of its files */
export type FileRef = { id: string; name: string };

/**
 * The files the learner is about to send with a message. Each goes to the subject at once (`POST /v1/domains/{id}/materials`),
 * then its status is asked until the text of the file is stored (`read`, or `parsed` when the graph work is done too): only then
 * the file is `ready` and the message may carry it, so the teacher never gets a file it cannot read yet. A file that cannot be
 * read is marked and left out of the message. The poll stops by itself after `MATERIAL_POLL_LIMIT` tries.
 */
export function useAttachments(domainId: string, sessionId?: string) {
  const { send } = useMaterialUpload(domainId, sessionId, { quiet: true });
  const [items, setItems] = useState<Attachment[]>([]);
  const alive = useRef(true);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    alive.current = true;
    const pending = timers.current;
    return () => {
      alive.current = false;
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);

  const patch = useCallback((key: string, change: Partial<Attachment>) => {
    if (alive.current) setItems((all) => all.map((a) => (a.key === key ? { ...a, ...change } : a)));
  }, []);

  // the poll calls itself through a ref: a callback cannot name itself while it is being defined
  const again = useRef<(key: string, materialId: string, tries: number) => void>(() => {});
  const watch = useCallback(
    (key: string, materialId: string, tries = 0) => {
      get<S["MaterialDetailView"]>(`/v1/materials/${materialId}`)
        .then((d) => {
          const status = d.material.status;
          if (materialReadable(status)) return patch(key, { state: "ready" });
          if (materialState(status) === "failed") return patch(key, { state: "failed" });
          if (tries + 1 >= MATERIAL_POLL_LIMIT) return patch(key, { state: "failed" });
          timers.current.set(key, setTimeout(() => again.current(key, materialId, tries + 1), READ_POLL_MS));
        })
        .catch(() => patch(key, { state: "failed" }));
    },
    [patch],
  );
  useEffect(() => {
    again.current = watch;
  }, [watch]);

  const add = useCallback(
    async (files: File[]) => {
      const room = Math.max(0, MAX_ATTACHED - items.length);
      for (const file of files.slice(0, room)) {
        const key = `a-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        setItems((all) => [...all, { key, name: file.name, materialId: null, state: "uploading" }]);
        const made = await send(file);
        if (!made) {
          if (alive.current) setItems((all) => all.filter((a) => a.key !== key)); // the toast said why; no dead chip stays
          continue;
        }
        patch(key, { materialId: made.material_id, state: "reading" });
        watch(key, made.material_id);
      }
    },
    [items.length, send, patch, watch],
  );

  const remove = useCallback((key: string) => {
    const timer = timers.current.get(key);
    if (timer) clearTimeout(timer);
    timers.current.delete(key);
    setItems((all) => all.filter((a) => a.key !== key));
  }, []);

  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current.clear();
    setItems([]);
  }, []);

  const busy = items.some((a) => a.state === "uploading" || a.state === "reading");
  const ready = items.filter((a): a is Attachment & { materialId: string } => a.state === "ready" && a.materialId !== null);
  return { items, add, remove, clear, busy, ready, full: items.length >= MAX_ATTACHED };
}

const CHIP =
  "inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-pill border border-seam bg-cloth px-2.5 py-1 text-small text-paper-dim";

/** The files of the message being written: name, state, a cross to take the file out */
export function AttachmentChips({ items, onRemove }: { items: Attachment[]; onRemove: (key: string) => void }) {
  const tx = useT();
  const t = tx.composer;
  if (!items.length) return null;
  return (
    <ul aria-label={t.attachedFiles} className="mb-1.5 flex flex-wrap gap-1.5 px-1 pt-1">
      {items.map((a) => (
        <li key={a.key} className={cn(CHIP, a.state === "failed" && "border-st-confused/60 text-st-confused")}>
          {a.state === "uploading" || a.state === "reading" ? (
            <Loader2 aria-hidden className="size-3.5 shrink-0 animate-spin motion-reduce:animate-none" />
          ) : (
            <FileText aria-hidden className="size-3.5 shrink-0" />
          )}
          <span className="min-w-0 truncate text-paper">{a.name}</span>
          <span role="status" className="shrink-0">
            {a.state === "uploading" ? t.attachUploading : a.state === "reading" ? t.attachReading : a.state === "ready" ? t.attachReady : t.attachFailed}
          </span>
          <button
            type="button"
            aria-label={t.attachRemove(a.name)}
            onClick={() => onRemove(a.key)}
            className="-mr-1 inline-flex size-6 shrink-0 items-center justify-center rounded-pill hover:bg-cloth-hover hover:text-paper focus-visible:outline-2 focus-visible:outline-ribbon"
          >
            <X aria-hidden className="size-3.5" />
          </button>
        </li>
      ))}
    </ul>
  );
}

/** The files a sent message carried, under its bubble. Inside a session a chip opens the file in the workspace pane */
export function SentFiles({ files }: { files: FileRef[] }) {
  const tx = useT();
  const ws = useWorkspaceOptional();
  if (!files.length) return null;
  return (
    <ul aria-label={tx.composer.attachedFiles} className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {files.map((f) => (
        <li key={f.id} className={cn(CHIP, ws && "relative p-0 hover:border-ribbon")}>
          {ws ? (
            <button
              type="button"
              aria-label={tx.workspace.openMaterial(f.name)}
              onClick={() => ws.open({ kind: "material", id: f.id, title: f.name })}
              className="inline-flex min-w-0 items-center gap-1.5 rounded-pill px-2.5 py-1 after:absolute after:-inset-y-1.5 after:inset-x-0 focus-visible:outline-2 focus-visible:outline-ribbon"
            >
              <Paperclip aria-hidden className="size-3.5 shrink-0" />
              <span className="min-w-0 truncate text-paper">{f.name}</span>
            </button>
          ) : (
            <>
              <Paperclip aria-hidden className="size-3.5 shrink-0" />
              <span className="min-w-0 truncate text-paper">{f.name}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
