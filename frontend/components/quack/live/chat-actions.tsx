"use client";

import Link from "next/link";
import { useState } from "react";
import { idem, put, type S } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { useErrorText } from "./error-text";

export type LiveT = ReturnType<typeof useT>["live"];

export type TurnActionContext = {
  sessionId: string;
  domainId: string;
  topicId: string | null;
  turnId: string;
  turnSeq?: number;
  turnText: string;
  studentPrompt: string | null;
  hasTopicNotes: boolean;
  onTopicNotesSaved: (doc: S["ArtifactResponse"]) => void;
};

export type ChatActionDescriptor = {
  id: string;
  when: (ctx: TurnActionContext) => boolean;
  label: (ctx: TurnActionContext, t: LiveT) => string;
  busyLabel: (ctx: TurnActionContext, t: LiveT) => string;
  doneLabel: (ctx: TurnActionContext, t: LiveT, wasUpdate: boolean) => string;
  doneHref?: (ctx: TurnActionContext) => string | null;
  doneLinkLabel?: (ctx: TurnActionContext, t: LiveT) => string;
  run: (ctx: TurnActionContext) => Promise<void>;
};

const PROMPT_NOTES_RE =
  /(конспект|резюм|саммари|шпаргалк|сводк|выжимк|основн\S*\s+мысл|кратк\S*\s+итог|\bnotes\b|\bsummary\b|\bsummarize\b|\bcheat\s*sheet\b|\brecap\b|\boutline\b)/i;
const MENTIONS_NOTES_RE = /(конспект|topic notes|saved notes)/i;
const MENTIONS_ACTION_RE = /(уже есть|обновить|прикрепить|already exist|already saved|update)/i;
const MENTIONS_EXISTING_RE = /(уже есть|обновить|already exist|already saved|update)/i;
const HEADING_RE = /^#{1,4}\s+/gm;
const META_EDGE_RE =
  /(кнопк\S*\s+ниже|button\s+below|(?:можешь|можно)\s+(?:его\s+)?(?:обновить|прикрепить|сохранить)|you\s+can\s+(?:update|attach|save)|если\s+(?:хочешь|нужно)[^.!\n]*(?:обнови|прикрепи|сохрани)|в\s+этой\s+теме\s+(?:уже\s+)?(?:есть|сохран\S*)\s+конспект|notes\s+(?:already\s+exist|are\s+already\s+saved))/i;

/**
 * Trim and strip short leading/trailing conversational meta-sentences offering
 * to attach or update topic notes with the button below, so saved notes stay clean.
 */
export function cleanNotesMarkdown(text: string): string {
  const raw = text.replace(/\r\n/g, "\n").trim();
  if (!raw) return "";

  const paras = raw.split(/\n{2,}/);
  while (paras.length > 1 && paras[0].trim().length <= 260 && META_EDGE_RE.test(paras[0])) {
    paras.shift();
  }
  while (paras.length > 1 && paras[paras.length - 1].trim().length <= 260 && META_EDGE_RE.test(paras[paras.length - 1])) {
    paras.pop();
  }

  let out = paras.join("\n\n").trim();
  // Also strip a single trailing meta-sentence glued onto the last paragraph.
  out = out.replace(/(?:^|\s+)[^.!?\n]*(?:кнопк\S*\s+ниже|button\s+below)[^.!?\n]*[.!]?\s*$/i, "").trim();
  return out || raw;
}

const isNotesUpdate = (ctx: TurnActionContext) =>
  ctx.hasTopicNotes || (MENTIONS_NOTES_RE.test(ctx.turnText) && MENTIONS_EXISTING_RE.test(ctx.turnText));

export const topicNotesAction: ChatActionDescriptor = {
  id: "topic-notes",
  when(ctx) {
    if (!ctx.topicId || !ctx.turnText.trim()) return false;
    if (ctx.studentPrompt && PROMPT_NOTES_RE.test(ctx.studentPrompt)) return true;
    if (MENTIONS_NOTES_RE.test(ctx.turnText) && MENTIONS_ACTION_RE.test(ctx.turnText)) return true;
    return ctx.turnText.length >= 240 && (ctx.turnText.match(HEADING_RE)?.length ?? 0) >= 2;
  },
  label: (ctx, t) => (isNotesUpdate(ctx) ? t.updateTopicNotes : t.attachNotesToTopic),
  busyLabel: (_ctx, t) => t.savingTopicNotes,
  doneLabel: (_ctx, t, wasUpdate) => (wasUpdate ? t.notesUpdatedInTopic : t.notesAttachedToTopic),
  doneHref: (ctx) => (ctx.topicId ? `/topic/${ctx.topicId}` : null),
  doneLinkLabel: (_ctx, t) => t.openTopicNotes,
  async run(ctx) {
    if (!ctx.topicId) return;
    const res = await put(`/v1/topics/${ctx.topicId}/notes`, {
      content: cleanNotesMarkdown(ctx.turnText),
      idempotency_key: idem(),
    });
    ctx.onTopicNotesSaved(res);
  },
};

export const CHAT_ACTIONS: readonly ChatActionDescriptor[] = [topicNotesAction];

type ActionState = { status: "idle" | "busy" | "done"; wasUpdate: boolean; error: string | null };

/** Renders registered turn-level actions for a committed teacher message. */
export function ChatTurnActions({ ctx }: { ctx: TurnActionContext }) {
  const t = useT().live;
  const errorText = useErrorText();
  const [states, setStates] = useState<Record<string, ActionState>>({});

  const applicable = CHAT_ACTIONS.filter((a) => a.when(ctx));
  if (applicable.length === 0) return null;

  const invoke = async (action: ChatActionDescriptor) => {
    const wasUpdate = action.label(ctx, t) === t.updateTopicNotes;
    setStates((s) => ({ ...s, [action.id]: { status: "busy", wasUpdate, error: null } }));
    try {
      await action.run(ctx);
      setStates((s) => ({ ...s, [action.id]: { status: "done", wasUpdate, error: null } }));
    } catch (e) {
      setStates((s) => ({ ...s, [action.id]: { status: "idle", wasUpdate, error: errorText(e) } }));
    }
  };

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {applicable.map((action) => {
        const st = states[action.id] ?? { status: "idle", wasUpdate: false, error: null };
        const href = st.status === "done" ? action.doneHref?.(ctx) : null;
        return (
          <div key={action.id} className="flex flex-wrap items-center gap-2">
            {st.status === "done" ? (
              <span className="inline-flex flex-wrap items-center gap-2 text-small text-paper-dim">
                <span>{action.doneLabel(ctx, t, st.wasUpdate)}</span>
                {href && action.doneLinkLabel && (
                  <Link href={href} className="caps text-small text-ribbon hover:text-ribbon-hover">
                    {action.doneLinkLabel(ctx, t)}
                  </Link>
                )}
              </span>
            ) : (
              <button
                type="button"
                disabled={st.status === "busy"}
                onClick={() => void invoke(action)}
                className="rounded-pill border border-seam px-3.5 py-1.5 caps text-small text-paper transition-colors hover:border-ribbon hover:text-ribbon disabled:opacity-50"
              >
                {st.status === "busy" ? action.busyLabel(ctx, t) : action.label(ctx, t)}
              </button>
            )}
            {st.error && (
              <p role="alert" className="w-full text-small text-st-confused">
                {st.error}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
