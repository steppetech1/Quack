"use client";

import type { Draft } from "@/lib/skill-drafts";
import { createContext, useCallback, useContext, useMemo, useState, useSyncExternalStore } from "react";

/**
 * The workspace pane: documents open beside the chat of a session. This module is the whole contract for the rest of
 * the app: `useWorkspace().open({ kind, id, title, … })` opens a document (or activates its tab when it is already open).
 * The pane itself is `workspace-pane.tsx`; what it draws for each kind is `doc-*.tsx`.
 */

export type DocKind = "demo" | "material" | "skill" | "note" | "skill_draft";

type Base = {
  /** The id of the thing itself (the demo's request id, a material id, a skill id, a topic or hub id): unique within its kind */
  id: string;
  /** What the tab is called; a skill's tab shows its current name when it can */
  title: string;
};

export type DocInput =
  | (Base & { kind: "demo"; /** mock mode: the page is drawn from the app itself, there is no backend to ask */ mock?: boolean })
  | (Base & { kind: "material" })
  | (Base & { kind: "skill" })
  /** a skill the teacher proposed in the chat, opened beside it: `id` is the key of the draft (`lib/skill-drafts.ts`), `base` the proposal */
  | (Base & { kind: "skill_draft"; base: Draft })
  | (Base & { kind: "note"; noteKind: "topic" | "hub"; /** the full page of the same notes */ href?: string });

export type WorkspaceDoc = DocInput & { key: string };

/** The tab id of a document: `activeId`, `close(…)` and `activate(…)` speak in it */
export const docKey = (kind: DocKind, id: string) => `${kind}:${id}`;

/** How the pane sits in the session: beside the chat, as a sheet over it, or as a layer over the whole page (phone) */
export type PaneMode = "side" | "sheet" | "full";

type Workspace = {
  docs: WorkspaceDoc[];
  /** the tab id of the document shown, `null` when nothing is open */
  activeId: string | null;
  open: (doc: DocInput) => void;
  close: (key: string) => void;
  closeAll: () => void;
  activate: (key: string) => void;
  /** the pane over the whole screen */
  expanded: boolean;
  setExpanded: (v: boolean) => void;
  /** a sheet or a phone layer put away with the back button: the tabs stay, the chat is free */
  hidden: boolean;
  setHidden: (v: boolean) => void;
  mode: PaneMode;
  setMode: (m: PaneMode) => void;
  /** the pane is on screen right now */
  visible: boolean;
};

const Ctx = createContext<Workspace | null>(null);

/** For code that must work outside a session too (shared components): `null` there */
export function useWorkspaceOptional() {
  return useContext(Ctx);
}

export function useWorkspace() {
  const ws = useContext(Ctx);
  if (!ws) throw new Error("useWorkspace outside WorkspaceProvider");
  return ws;
}

// ── what is remembered: sessionStorage by session id, with a copy in memory when the storage is not there ──────────────

type Saved = { docs: DocInput[]; active: string | null };
const KINDS = new Set<string>(["demo", "material", "skill", "note", "skill_draft"]);
const storageKey = (sessionId: string) => `quack.workspace.${sessionId}`;
const memory = new Map<string, string>();
const listeners = new Set<() => void>();
const subscribe = (f: () => void) => {
  listeners.add(f);
  return () => void listeners.delete(f);
};

function readRaw(sessionId: string): string | null {
  const key = storageKey(sessionId);
  if (memory.has(key)) return memory.get(key) ?? null;
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeRaw(sessionId: string, value: string) {
  const key = storageKey(sessionId);
  memory.set(key, value);
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {}
  listeners.forEach((f) => f());
}

/** What was saved, checked: anything that is not a well-formed document is dropped */
function parse(raw: string | null): Saved {
  const empty: Saved = { docs: [], active: null };
  if (!raw) return empty;
  try {
    const v = JSON.parse(raw) as { docs?: unknown; active?: unknown };
    if (!v || !Array.isArray(v.docs)) return empty;
    const docs: DocInput[] = [];
    for (const d of v.docs) {
      const x = d as Partial<DocInput> & { noteKind?: unknown; href?: unknown; mock?: unknown };
      if (!x || typeof x.kind !== "string" || !KINDS.has(x.kind) || typeof x.id !== "string" || !x.id || typeof x.title !== "string") continue;
      if (docs.some((o) => docKey(o.kind, o.id) === docKey(x.kind as DocKind, x.id as string))) continue;
      if (x.kind === "note") {
        if (x.noteKind !== "topic" && x.noteKind !== "hub") continue;
        docs.push({ kind: "note", id: x.id, title: x.title, noteKind: x.noteKind, href: typeof x.href === "string" ? x.href : undefined });
      } else if (x.kind === "skill_draft") {
        const b = (x as { base?: Partial<Draft> }).base;
        if (!b || typeof b.name !== "string" || typeof b.text !== "string") continue;
        const binding = b.binding === "notes" || b.binding === "cards" || b.binding === "cheat" ? b.binding : null;
        docs.push({ kind: "skill_draft", id: x.id, title: x.title, base: { name: b.name, text: b.text, binding } });
      } else if (x.kind === "demo") {
        docs.push({ kind: "demo", id: x.id, title: x.title, mock: x.mock === true ? true : undefined });
      } else {
        docs.push({ kind: x.kind as "material" | "skill", id: x.id, title: x.title });
      }
    }
    const active = typeof v.active === "string" && docs.some((d) => docKey(d.kind, d.id) === v.active) ? v.active : (docs[0] ? docKey(docs[0].kind, docs[0].id) : null);
    return { docs, active: docs.length ? active : null };
  } catch {
    return empty;
  }
}

const withKey = (d: DocInput): WorkspaceDoc => ({ ...d, key: docKey(d.kind, d.id) });

export function WorkspaceProvider({ sessionId, children }: { sessionId: string; children: React.ReactNode }) {
  // the server has nothing: the saved tabs come in right after hydration, so the first render is the same on both sides
  const raw = useSyncExternalStore(subscribe, () => readRaw(sessionId), () => null);
  const saved = useMemo(() => parse(raw), [raw]);
  const docs = useMemo(() => saved.docs.map(withKey), [saved]);
  const [expanded, setExpandedState] = useState(false);
  // tabs restored after a reload do not cover the chat of a narrow screen: they wait behind the «Documents» button until one is opened
  const [hidden, setHidden] = useState(true);
  const [mode, setMode] = useState<PaneMode>("side");

  const save = useCallback((next: Saved) => writeRaw(sessionId, next.docs.length ? JSON.stringify(next) : ""), [sessionId]);
  const current = useCallback(() => parse(readRaw(sessionId)), [sessionId]);

  const open = useCallback(
    (doc: DocInput) => {
      const cur = current();
      const key = docKey(doc.kind, doc.id);
      const exists = cur.docs.some((d) => docKey(d.kind, d.id) === key);
      // an open document is only brought forward (its tab keeps the title it has)
      save({ docs: exists ? cur.docs : [...cur.docs, doc], active: key });
      setHidden(false);
    },
    [current, save],
  );

  const close = useCallback(
    (key: string) => {
      const cur = current();
      const at = cur.docs.findIndex((d) => docKey(d.kind, d.id) === key);
      if (at < 0) return;
      const left = cur.docs.filter((_, i) => i !== at);
      const next = left[at] ?? left[at - 1] ?? null;
      save({ docs: left, active: cur.active === key ? (next ? docKey(next.kind, next.id) : null) : cur.active });
      if (!left.length) setExpandedState(false);
    },
    [current, save],
  );

  const closeAll = useCallback(() => {
    save({ docs: [], active: null });
    setExpandedState(false);
  }, [save]);

  const activate = useCallback(
    (key: string) => {
      const cur = current();
      if (cur.docs.some((d) => docKey(d.kind, d.id) === key)) save({ docs: cur.docs, active: key });
    },
    [current, save],
  );

  const setExpanded = useCallback((v: boolean) => setExpandedState(v), []);
  const visible = docs.length > 0 && (mode === "side" || !hidden);

  const value = useMemo<Workspace>(
    () => ({ docs, activeId: saved.active, open, close, closeAll, activate, expanded: expanded && docs.length > 0, setExpanded, hidden, setHidden, mode, setMode, visible }),
    [docs, saved.active, open, close, closeAll, activate, expanded, setExpanded, hidden, mode, visible],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
