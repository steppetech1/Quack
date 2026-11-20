"use client";

import { Check, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, idem, post, type S } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useErrorText } from "./error-text";
import { plainText } from "./markdown";

export type LiveT = ReturnType<typeof useT>["live"];

export type NoteTopicTarget = {
  id: string;
  title: string;
  hubId: string | null;
};

export type NoteHubTarget = {
  id: string;
  title: string;
};

export type ResolvedNoteTargets = {
  topics: NoteTopicTarget[];
  hub: NoteHubTarget | null;
};

export type TurnActionContext = {
  sessionId: string;
  domainId: string;
  topicId: string | null;
  turnId: string;
  turnSeq?: number;
  turnText: string;
  studentPrompt: string | null;
  /** Latest touched topics (up to 3) + target hub for overall cheat sheet */
  resolveTargets: () => Promise<ResolvedNoteTargets>;
  /** Notify session job tracker that a background artifact job was queued */
  onJobsRefresh?: () => void;
  /** Bumped when background artifact jobs finish or projection advances */
  artifactTick?: number;
};

export type NoteCard = {
  key: string;
  id: string;
  kind: "topic" | "hub";
  title: string;
  href: string;
  status: "preparing" | "ready" | "failed";
  prevVersion: number | null;
  preview: string | null;
  error?: string;
};

const POLL_MS = 3_000;
const POLL_LIMIT = 40;

const orNone = <T,>(p: Promise<T>) =>
  p.catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });

const PROMPT_NOTES_RE =
  /(конспект|резюм|саммари|шпаргалк|сводк|выжимк|основн\S*\s+мысл|кратк\S*\s+итог|\bnotes\b|\bsummary\b|\bsummarize\b|\bcheat\s*sheet\b|\brecap\b|\boutline\b)/i;
const OVERALL_NOTES_RE =
  /(по\s+всем\s+темам|общий(?:\s+(?:конспект|итог|обзор))?|общая\s+шпаргалка|сводн\S*\s+конспект|по\s+всему\s+предмету|\ball\s+topics\b|\boverall\b|\bgeneral\s+(?:notes|summary)\b|\bcheat\s*sheet\b)/i;
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

  const out = paras
    .join("\n\n")
    .trim()
    .replace(/(?:^|\s+)[^.!?\n]*(?:кнопк\S*\s+ниже|button\s+below)[^.!?\n]*[.!]?\s*$/i, "")
    .trim();
  return out || raw;
}

function blocksPreview(blocks: { [key: string]: unknown }[]): string | null {
  const raw = blocks
    .map((b) => (typeof b.content === "string" ? b.content : ""))
    .filter(Boolean)
    .join("\n\n");
  const text = plainText(raw, 180).trim();
  return text || null;
}

async function postWithOptionalSession(path: string, sessionId: string) {
  try {
    await post(path as never, { idempotency_key: idem(), session_id: sessionId } as never);
  } catch (e) {
    // Fallback if backend endpoint doesn't yet accept `session_id`
    if (e instanceof ApiError && e.status === 422) {
      await post(path as never, { idempotency_key: idem() } as never);
      return;
    }
    throw e;
  }
}

/** Renders turn-level notes and overall cheat-sheet actions for a committed teacher message. */
export function ChatTurnActions({ ctx }: { ctx: TurnActionContext }) {
  const t = useT().live;
  const errorText = useErrorText();
  const [busyKind, setBusyKind] = useState<"topic" | "hub" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cards, setCards] = useState<NoteCard[]>([]);
  const alive = useRef(true);
  const pollTries = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const hasPreparing = cards.some((c) => c.status === "preparing");

  const checkPreparing = useCallback(
    async (forceReadyOnExisting: boolean) => {
      const preparing = cards.filter((c) => c.status === "preparing");
      if (!preparing.length) return;
      const updates = await Promise.all(
        preparing.map(async (card) => {
          try {
            if (card.kind === "topic") {
              const doc = await orNone(get<S["ArtifactResponse"]>(`/v1/topics/${card.id}/notes`));
              if (doc && doc.blocks.length > 0 && (card.prevVersion === null || doc.version > card.prevVersion || forceReadyOnExisting)) {
                return { key: card.key, status: "ready" as const, preview: blocksPreview(doc.blocks) };
              }
            } else {
              const doc = await orNone(get<S["CheatsheetResponse"]>(`/v1/hubs/${card.id}/cheatsheet`));
              if (doc && doc.blocks.length > 0 && (card.prevVersion === null || doc.version > card.prevVersion || forceReadyOnExisting)) {
                return { key: card.key, status: "ready" as const, preview: blocksPreview(doc.blocks) };
              }
            }
          } catch {}
          return null;
        }),
      );
      if (!alive.current) return;
      const byKey = new Map(updates.filter((u): u is NonNullable<typeof u> => u !== null).map((u) => [u.key, u]));
      if (!byKey.size) return;
      setCards((prev) =>
        prev.map((c) => {
          const u = byKey.get(c.key);
          return u ? { ...c, status: u.status, preview: u.preview } : c;
        }),
      );
    },
    [cards],
  );

  useEffect(() => {
    if (!hasPreparing) {
      pollTries.current = 0;
      return;
    }
    const timer = setInterval(() => {
      if (++pollTries.current > POLL_LIMIT) {
        clearInterval(timer);
        return;
      }
      void checkPreparing(false);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [hasPreparing, checkPreparing]);

  // Immediate refresh when session jobs finish or projection advances
  const prevTick = useRef(ctx.artifactTick ?? 0);
  useEffect(() => {
    const tick = ctx.artifactTick ?? 0;
    if (tick === prevTick.current) return;
    prevTick.current = tick;
    if (hasPreparing) {
      const timer = setTimeout(() => void checkPreparing(true), 0);
      return () => clearTimeout(timer);
    }
  }, [ctx.artifactTick, hasPreparing, checkPreparing]);

  if (!ctx.turnText.trim()) return null;

  const prefersOverall = Boolean(ctx.studentPrompt && OVERALL_NOTES_RE.test(ctx.studentPrompt));
  const promptWantsNotes = Boolean(ctx.studentPrompt && PROMPT_NOTES_RE.test(ctx.studentPrompt));

  const runTopics = async () => {
    setBusyKind("topic");
    setError(null);
    try {
      const { topics } = await ctx.resolveTargets();
      if (!topics.length) {
        if (alive.current) setError(t.notesNoTopics);
        return;
      }
      const initialCards: NoteCard[] = await Promise.all(
        topics.map(async (topic) => {
          const existing = await orNone(get<S["ArtifactResponse"]>(`/v1/topics/${topic.id}/notes`)).catch(() => null);
          return {
            key: `topic:${topic.id}`,
            id: topic.id,
            kind: "topic",
            title: topic.title,
            href: `/topic/${topic.id}`,
            status: "preparing",
            prevVersion: existing?.version ?? null,
            preview: existing ? blocksPreview(existing.blocks) : null,
          };
        }),
      );
      if (!alive.current) return;
      setCards((prev) => [
        ...prev.filter((c) => !initialCards.some((n) => n.key === c.key)),
        ...initialCards,
      ]);

      await Promise.all(
        initialCards.map(async (card) => {
          try {
            await postWithOptionalSession(`/v1/topics/${card.id}/notes`, ctx.sessionId);
          } catch (e) {
            if (!alive.current) return;
            const msg = errorText(e);
            setCards((prev) => prev.map((c) => (c.key === card.key ? { ...c, status: "failed", error: msg } : c)));
          }
        }),
      );
      ctx.onJobsRefresh?.();
    } catch (e) {
      if (alive.current) setError(errorText(e));
    } finally {
      if (alive.current) setBusyKind(null);
    }
  };

  const runOverall = async () => {
    setBusyKind("hub");
    setError(null);
    try {
      const { hub } = await ctx.resolveTargets();
      if (!hub) {
        if (alive.current) setError(t.notesNoTopics);
        return;
      }
      const existing = await orNone(get<S["CheatsheetResponse"]>(`/v1/hubs/${hub.id}/cheatsheet`)).catch(() => null);
      const card: NoteCard = {
        key: `hub:${hub.id}`,
        id: hub.id,
        kind: "hub",
        title: hub.title,
        href: `/hub/${hub.id}`,
        status: "preparing",
        prevVersion: existing?.version ?? null,
        preview: existing ? blocksPreview(existing.blocks) : null,
      };
      if (!alive.current) return;
      setCards((prev) => [...prev.filter((c) => c.key !== card.key), card]);

      try {
        await postWithOptionalSession(`/v1/hubs/${hub.id}/cheatsheet`, ctx.sessionId);
        ctx.onJobsRefresh?.();
      } catch (e) {
        if (!alive.current) return;
        const msg = errorText(e);
        setCards((prev) => prev.map((c) => (c.key === card.key ? { ...c, status: "failed", error: msg } : c)));
      }
    } catch (e) {
      if (alive.current) setError(errorText(e));
    } finally {
      if (alive.current) setBusyKind(null);
    }
  };

  const topicBtn = (
    <button
      key="topic"
      type="button"
      disabled={busyKind !== null}
      onClick={() => void runTopics()}
      className={cn(
        "rounded-pill border px-3.5 py-1.5 caps text-small transition-colors disabled:opacity-50",
        promptWantsNotes && !prefersOverall
          ? "border-ribbon text-ribbon hover:bg-ribbon-soft"
          : "border-seam text-paper hover:border-ribbon hover:text-ribbon",
      )}
    >
      {busyKind === "topic" ? t.savingTopicNotes : t.makeNotes}
    </button>
  );

  const hubBtn = (
    <button
      key="hub"
      type="button"
      disabled={busyKind !== null}
      onClick={() => void runOverall()}
      className={cn(
        "rounded-pill border px-3.5 py-1.5 caps text-small transition-colors disabled:opacity-50",
        prefersOverall
          ? "border-ribbon text-ribbon hover:bg-ribbon-soft"
          : "border-seam text-paper hover:border-ribbon hover:text-ribbon",
      )}
    >
      {busyKind === "hub" ? t.savingTopicNotes : t.makeOverallCheat}
    </button>
  );

  return (
    <div className="mt-3 flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        {prefersOverall ? [hubBtn, topicBtn] : [topicBtn, hubBtn]}
      </div>
      {error && (
        <p role="alert" className="text-small text-st-confused">
          {error}
        </p>
      )}
      {cards.length > 0 && <NoteCardsGrid cards={cards} />}
    </div>
  );
}

/** Renders topic/hub notes cards in `ExpandableCard` style with live preparing/ready status. */
export function NoteCardsGrid({ cards }: { cards: NoteCard[] }) {
  const t = useT().live;
  if (!cards.length) return null;
  return (
    <div className={cn("grid gap-2", cards.length > 1 && "sm:grid-cols-2")}>
      {cards.map((card) => (
        <Link
          key={card.key}
          href={card.href}
          className="block rounded-[2px] border border-seam bg-cloth-raised p-4 text-left transition-[transform,border-color] duration-(--dur-s) ease-(--ease-out) hover:-translate-y-px hover:border-ribbon focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon motion-reduce:transition-none motion-reduce:hover:translate-y-0"
        >
          <span className="display-caps block text-paper">{card.title}</span>
          <span className="mt-1 flex items-center gap-1.5 text-small text-paper-dim">
            {card.status === "preparing" && <Loader2 aria-hidden className="size-3.5 shrink-0 animate-spin motion-reduce:animate-none" />}
            {card.status === "ready" && <Check aria-hidden className="size-3.5 shrink-0 text-st-solid" />}
            <span>
              {card.status === "preparing"
                ? t.notesCardPreparing
                : card.status === "ready"
                  ? t.notesCardReady
                  : t.notesCardFailed}
            </span>
          </span>
          {card.preview && (
            <span className="mt-1.5 block line-clamp-2 text-small leading-relaxed text-paper-dim">
              {card.preview}
            </span>
          )}
          {card.error && (
            <span role="alert" className="mt-1.5 block text-small text-st-confused">
              {card.error}
            </span>
          )}
          <span className="caps mt-2 inline-block text-small text-ribbon">
            {card.kind === "hub" ? t.openHubCheat : t.openTopicNotes}
          </span>
        </Link>
      ))}
    </div>
  );
}
