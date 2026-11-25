"use client";

import { ArrowLeft, BookOpen, ChevronDown, FileText, Maximize2, Minimize2, Play, Slash, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { DemoDoc } from "./doc-demo";
import { MaterialDoc } from "./doc-material";
import { NoteDoc } from "./doc-note";
import { SkillDoc } from "./doc-skill";
import { SkillDraftDoc } from "./doc-skill-draft";
import { WorkspaceProvider, useWorkspace, type DocKind, type PaneMode, type WorkspaceDoc } from "./workspace-context";

/** From this width of the session area the pane stands beside the chat; narrower it is a sheet over it; on a phone, a layer over everything */
const SIDE_MIN_PX = 1100;
/** Below the `md` breakpoint (the left panel is a drawer there) the screen is a phone */
const PHONE_MAX_PX = 768;

const KIND_ICON: Record<DocKind, typeof Play> = { demo: Play, material: FileText, skill: Slash, note: BookOpen, skill_draft: Slash };

const modeFor = (width: number): PaneMode => (window.innerWidth < PHONE_MAX_PX ? "full" : width >= SIDE_MIN_PX ? "side" : "sheet");

/**
 * The session with its workspace: the chat and, beside it, the pane with the documents the learner opened (a demo, a file,
 * a skill, notes). The chat keeps its own column, so the floating plan window of the session stays inside it and never
 * reaches the pane. Documents open through `useWorkspace().open(…)` anywhere inside.
 */
export function SessionWorkspace({ sessionId, children }: { sessionId: string; children: React.ReactNode }) {
  return (
    <WorkspaceProvider sessionId={sessionId}>
      <Frame>{children}</Frame>
    </WorkspaceProvider>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  const ws = useWorkspace();
  const tx = useT();
  const box = useRef<HTMLDivElement>(null);
  const { setMode } = ws;

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setMode(modeFor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [setMode]);

  const away = ws.docs.length > 0 && ws.mode !== "side" && !ws.visible;
  return (
    <div ref={box} className="relative flex min-h-0 flex-1">
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {away && (
          <div className="flex shrink-0 justify-end px-4 pt-1 md:px-7">
            <button
              type="button"
              aria-label={tx.workspace.showDocs(ws.docs.length)}
              onClick={() => ws.setHidden(false)}
              className="caps inline-flex h-10 items-center gap-2 rounded-pill border border-seam px-4 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon"
            >
              {tx.workspace.docsCount(ws.docs.length)}
            </button>
          </div>
        )}
        {children}
      </div>
      {ws.visible && <Pane />}
    </div>
  );
}

function DocBody({ doc }: { doc: WorkspaceDoc }) {
  switch (doc.kind) {
    case "demo":
      return <DemoDoc doc={doc} />;
    case "material":
      return <MaterialDoc doc={doc} />;
    case "skill":
      return <SkillDoc doc={doc} />;
    case "skill_draft":
      return <SkillDraftDoc doc={doc} />;
    case "note":
      return <NoteDoc doc={doc} />;
  }
}

function Pane() {
  const tx = useT();
  const t = tx.workspace;
  const ws = useWorkspace();
  const [list, setList] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();

  const active = ws.docs.find((d) => d.key === ws.activeId) ?? ws.docs[0];
  const { expanded, setExpanded } = ws;

  // Esc: the expanded pane folds back first, then the list of documents closes (one handler, so one press does one thing)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (expanded) {
        e.preventDefault();
        setExpanded(false);
      } else if (list) {
        e.preventDefault();
        setList(false);
        trigger.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, list, setExpanded]);

  useEffect(() => {
    if (!list) return;
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setList(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [list]);

  if (!active) return null;
  const Icon = KIND_ICON[active.kind];
  const full = ws.mode === "full";
  const IconSize = expanded ? Minimize2 : Maximize2;
  const iconBtn = "inline-flex size-10 shrink-0 items-center justify-center rounded-pill text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper";

  return (
    <aside
      ref={root}
      aria-label={t.pane}
      className={cn(
        "flex min-h-0 flex-col bg-cloth-raised",
        expanded || full
          ? "fixed inset-0 z-50"
          : ws.mode === "sheet"
            ? "absolute inset-y-0 right-0 z-30 w-[min(34rem,92%)] border-l border-seam shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.6)]"
            : "relative w-[40%] max-w-[640px] min-w-[360px] shrink-0 border-l border-seam",
      )}
    >
      <header className="relative flex shrink-0 items-center gap-1 border-b border-seam-soft px-1.5">
        {ws.mode !== "side" && !expanded && (
          <button type="button" aria-label={t.back} onClick={() => ws.setHidden(true)} className={iconBtn}>
            <ArrowLeft aria-hidden className="size-4" />
          </button>
        )}
        <button
          ref={trigger}
          type="button"
          aria-label={t.switchDocs(active.title)}
          aria-expanded={list}
          aria-controls={listId}
          onClick={() => setList((v) => !v)}
          className="flex min-h-10 min-w-0 flex-1 items-center gap-2 rounded-control px-2.5 text-left text-paper transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover"
        >
          <Icon aria-hidden className="size-4 shrink-0 text-paper-dim" />
          <span className="min-w-0 truncate text-body">{active.title}</span>
          {ws.docs.length > 1 && <span className="caps shrink-0 text-paper-dim">{ws.docs.length}</span>}
          <ChevronDown aria-hidden className={cn("size-4 shrink-0 text-paper-dim transition-transform duration-(--dur-s) ease-out", list && "rotate-180")} />
        </button>
        {!full && (
          <button
            type="button"
            aria-label={expanded ? t.collapse : t.expand}
            onClick={() => setExpanded(!expanded)}
            className={iconBtn}
          >
            <IconSize aria-hidden className="size-4" />
          </button>
        )}
        <button type="button" aria-label={t.closeAll} onClick={ws.closeAll} className={iconBtn}>
          <X aria-hidden className="size-4" />
        </button>

        {list && (
          <ul
            id={listId}
            aria-label={t.docsCount(ws.docs.length)}
            className="absolute top-full right-1.5 left-1.5 z-10 mt-1 max-h-[60dvh] overflow-y-auto rounded-control border border-seam bg-cloth-raised p-1 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
          >
            {ws.docs.map((d) => {
              const DocIcon = KIND_ICON[d.kind];
              const on = d.key === active.key;
              return (
                <li key={d.key} className="flex items-center">
                  <button
                    type="button"
                    aria-current={on ? "true" : undefined}
                    onClick={() => {
                      ws.activate(d.key);
                      setList(false);
                      trigger.current?.focus();
                    }}
                    className={cn(
                      "flex min-h-10 min-w-0 flex-1 items-center gap-2 rounded-option px-2.5 text-left transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover",
                      on ? "text-ribbon" : "text-paper",
                    )}
                  >
                    <DocIcon aria-hidden className="size-4 shrink-0 text-paper-dim" />
                    <span className="min-w-0 flex-1 truncate">{d.title}</span>
                    <span className="caps shrink-0 text-paper-dim">{t.kind[d.kind]}</span>
                  </button>
                  <button
                    type="button"
                    aria-label={t.closeDoc(d.title)}
                    onClick={() => {
                      ws.close(d.key);
                      if (ws.docs.length <= 1) setList(false);
                    }}
                    className={iconBtn}
                  >
                    <X aria-hidden className="size-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        <DocBody key={active.key} doc={active} />
      </div>
    </aside>
  );
}
