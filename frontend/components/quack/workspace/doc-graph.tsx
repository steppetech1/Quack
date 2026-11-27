"use client";

import { useEffect } from "react";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useGraphModel } from "@/lib/graph/use-graph-model";
import { useLocale, useT } from "@/lib/i18n/provider";
import { LayeredGraph, type GraphTarget } from "../graph/layered-graph";
import { useWorkspace, type WorkspaceDoc } from "./workspace-context";

/**
 * The graph of the session's subject, beside the chat: the same graph as on the subject page (zoom, drag, hover cards), in a
 * pane-sized frame. A click on a topic or a chapter opens its notes (or the cheat sheet) as another tab, it does not leave the session.
 */
export function GraphDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "graph" }> }) {
  const tx = useT();
  const { locale } = useLocale();
  const ws = useWorkspace();
  const { domains, loadGraph } = useData();
  const model = useGraphModel(locale, doc.id);
  const domain = domains.find((d) => d.id === doc.id);

  // the subject of the session is the current one, so its graph is usually there already; when it is not, ask for it
  useEffect(() => {
    if (API_MODE && !model) void loadGraph(doc.id).catch(() => {});
  }, [doc.id, model, loadGraph]);

  if (!model || !domain) {
    return (
      <p role="status" className="p-5 text-paper-dim">
        {tx.workspace.graphDoc.loading}
      </p>
    );
  }
  if (model.topics.length === 0) return <p className="p-5 text-paper-dim">{tx.workspace.graphDoc.empty}</p>;

  const open = ({ kind, id }: GraphTarget) => {
    const name = kind === "hub" ? model.hubs.find((h) => h.id === id)?.name : model.topics.find((t) => t.id === id)?.name;
    ws.open({ kind: "note", id, title: name ?? tx.meta.topic, noteKind: kind, href: kind === "hub" ? `/hub/${id}` : `/topic/${id}` });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col px-2 pb-2">
      <LayeredGraph key={doc.id} model={model} domain={domain} compact onOpen={open} />
    </div>
  );
}
