"use client";

import { Code2, Network, PenTool, Shapes, Slash } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { API_MODE } from "@/lib/api/client";
import { listDomainCanvases, openCanvas, readCanvasTitle } from "@/lib/api/canvases";
import { useData } from "@/lib/api/data";
import { useMocks, useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { SESSION_TOOLS_SLOT } from "../app-shell";
import { IconButton } from "../icon-button";
import { useNotice } from "../soon";
import { useWorkspaceOptional, type DocKind } from "../workspace/workspace-context";

/**
 * The row of tools in the top right corner of a session: the graph of the subject, the demos, the code editor, the canvas and
 * the learner's skills. A press opens the tool beside the chat (the workspace pane; a tool that is open is only brought forward).
 * The code editor is the session's own column, so it comes through `code`: `null` when the session has no code task yet.
 */
export function SessionTools({
  sessionId,
  domainId,
  code,
  codeOpen = false,
  className,
}: {
  sessionId: string;
  domainId: string | undefined;
  code: (() => void) | null;
  codeOpen?: boolean;
  className?: string;
}) {
  const tx = useT();
  const t = tx.workspace.tools;
  const ws = useWorkspaceOptional();
  const notice = useNotice();
  const { domains } = useData();
  const { canvasesOf } = useMocks();
  const [opening, setOpening] = useState(false);
  // the top bar of the session (outside the workspace, in the app shell) has a slot at its right end: the buttons are drawn there
  const slot = useSyncExternalStore(
    () => () => {},
    () => document.getElementById(SESSION_TOOLS_SLOT),
    () => null,
  );
  const domain = domains.find((d) => d.id === domainId);
  const activeKind: DocKind | undefined = ws?.visible ? ws.docs.find((d) => d.key === ws.activeId)?.kind : undefined;

  const openCanvasDoc = async () => {
    // one request at a time: a second press while the canvas is being found does not make a second canvas
    if (!ws || opening) return;
    setOpening(true);
    try {
      let id: string | undefined;
      if (API_MODE) {
        // a canvas lives in a session: this session's newest one, or the first one of it
        const mine = domainId ? (await listDomainCanvases(domainId)).canvases.find((c) => c.session_id === sessionId) : undefined;
        id = mine?.canvas_id ?? (await openCanvas(sessionId) as { canvas_id: string }).canvas_id;
      } else {
        id = canvasesOf(domainId ?? "")[0]?.id;
      }
      if (!id) return;
      ws.open({ kind: "canvas", id, title: (API_MODE && readCanvasTitle(id)) || t.canvasTitle });
    } catch {
      notice(tx.workspace.canvasDoc.failed);
    } finally {
      setOpening(false);
    }
  };

  const items: { key: string; label: string; icon: React.ReactNode; on: boolean; disabled?: boolean; busy?: boolean; onPress: () => void }[] = [
    {
      key: "graph",
      label: t.graph,
      icon: <Network aria-hidden />,
      on: activeKind === "graph",
      disabled: !ws || !domainId,
      onPress: () => domainId && ws?.open({ kind: "graph", id: domainId, title: t.graphTitle(domain?.title ?? tx.meta.topic) }),
    },
    {
      key: "demos",
      label: t.demos,
      icon: <Shapes aria-hidden />,
      on: activeKind === "demos",
      disabled: !ws,
      onPress: () => ws?.open({ kind: "demos", id: sessionId, title: t.demosTitle }),
    },
    {
      key: "code",
      label: code ? t.code : t.codeNone,
      icon: <Code2 aria-hidden />,
      on: codeOpen,
      disabled: !code,
      onPress: () => code?.(),
    },
    {
      key: "canvas",
      label: t.canvas,
      icon: <PenTool aria-hidden />,
      on: activeKind === "canvas",
      disabled: !ws || (!API_MODE && !canvasesOf(domainId ?? "").length),
      busy: opening,
      onPress: () => void openCanvasDoc(),
    },
    {
      key: "skills",
      label: t.skills,
      icon: <Slash aria-hidden />,
      on: activeKind === "skills",
      disabled: !ws,
      onPress: () => ws?.open({ kind: "skills", id: sessionId, title: t.skillsTitle }),
    },
  ];

  if (!slot) return null;
  return createPortal(
    <div role="toolbar" aria-label={t.group} className={cn("flex shrink-0 items-center justify-end gap-0.5", className)}>
      {items.map((it) => (
        <IconButton
          key={it.key}
          label={it.label}
          // a tool that cannot be used keeps its place and its explanation (aria-disabled stays reachable by keyboard, `disabled` would not)
          aria-disabled={it.disabled || undefined}
          aria-pressed={it.disabled ? undefined : it.on}
          aria-busy={it.busy || undefined}
          onClick={() => !it.disabled && !it.busy && it.onPress()}
          className={cn(
            "focus-visible:outline-2 focus-visible:outline-ribbon",
            it.disabled ? "cursor-not-allowed opacity-40 hover:bg-transparent hover:text-paper-dim" : "hover:text-ribbon",
            it.on && !it.disabled && "text-ribbon",
            it.busy && "animate-pulse motion-reduce:animate-none",
          )}
        >
          {it.icon}
        </IconButton>
      ))}
    </div>,
    slot,
  );
}
