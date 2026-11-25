"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_MODE, get, type S } from "@/lib/api/client";
import { useMocks, useT } from "@/lib/i18n/provider";
import { Markdown } from "../live/markdown";
import { DocScroll } from "./doc-scroll";
import { useWorkspace, type WorkspaceDoc } from "./workspace-context";

type Loaded = { key: string; text: string | null };

/** The notes of a topic or the cheat sheet of a hub: the same text as on their pages (`/v1/topics/{id}/notes`, `/v1/hubs/{id}/cheatsheet`) */
export function NoteDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "note" }> }) {
  const tx = useT();
  const n = tx.workspace.note;
  const ws = useWorkspace();
  const mock = useMocks().workspace.note;
  const key = doc.key;
  const [got, setGot] = useState<Loaded | null>(null);

  useEffect(() => {
    if (!API_MODE) return;
    let off = false;
    const path = doc.noteKind === "hub" ? `/v1/hubs/${doc.id}/cheatsheet` : `/v1/topics/${doc.id}/notes`;
    get<S["ArtifactResponse"]>(path)
      .then((d) => {
        const text = d.blocks
          .map((b) => (typeof b.content === "string" ? b.content : ""))
          .filter(Boolean)
          .join("\n\n");
        if (!off) setGot({ key, text });
      })
      .catch(() => {
        if (!off) setGot({ key, text: null });
      });
    return () => {
      off = true;
    };
  }, [doc.id, doc.noteKind, key]);

  const text = API_MODE ? (got?.key === key ? got.text : undefined) : mock.text;
  return (
    <DocScroll>
      <p className="caps text-paper-dim">{doc.noteKind === "hub" ? n.hub : n.topic}</p>
      <h2 className="display-caps mt-1 text-paper [overflow-wrap:anywhere]">{doc.title}</h2>
      {doc.href && (
        <Link href={doc.href} className="relative mt-2 inline-block text-small text-ribbon hover:underline after:absolute after:-inset-y-3 after:inset-x-0">
          {n.openPage}
        </Link>
      )}
      <div className="mt-5">
        {text === undefined ? (
          <p role="status" className="text-paper-dim">
            {n.loading}
          </p>
        ) : text === null ? (
          <p role="alert" className="text-st-confused">
            {n.failed}
          </p>
        ) : text.trim() ? (
          <Markdown text={text} onOpenDemo={(d) => ws.open({ kind: "demo", id: d.requestId, title: d.title })} />
        ) : (
          <p className="text-paper-dim">{n.empty}</p>
        )}
      </div>
    </DocScroll>
  );
}
