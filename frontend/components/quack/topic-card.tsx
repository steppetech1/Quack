"use client";

import Link from "next/link";
import { API_MODE } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import type { Topic } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { useTopicBlurb } from "@/lib/api/blurb";
import { useStartSession } from "./live/use-start-session";
import { useSoon } from "./soon";

export const STATE_COLOR: Record<Topic["state"], string> = {
  solid: "var(--st-solid)",
  review: "var(--st-review)",
  confused: "var(--st-confused)",
  new: "var(--st-new)",
};

export function StateDot({ state, className }: { state: Topic["state"]; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block size-2.5 shrink-0 rounded-full", className)}
      style={
        state === "new"
          ? { boxShadow: `inset 0 0 0 1.5px ${STATE_COLOR.new}` }
          : { background: STATE_COLOR[state] }
      }
    />
  );
}

/** Содержимое карточки темы: одна строка, состояние, заблуждение, четыре действия */
export function TopicCardBody({ topic, hubLabel }: { topic: Topic; hubLabel?: string }) {
  const t = useT();
  const soon = useSoon();
  const { domain } = useShell();
  const { start, busy } = useStartSession();
  // with the backend a topic has no description: the card shows the start of its notes, when there are any
  const blurb = useTopicBlurb(topic.id);
  const actions = [t.topicCard.study, t.topicCard.practice, t.topicCard.review, t.topicCard.history];
  // На бэкенде «Изучать» и «Повторять» открывают сессию по теме; «Историю» пока некуда вести
  const mode = (a: string) => (!API_MODE ? null : a === t.topicCard.study ? "learn" : a === t.topicCard.review ? "review" : null);
  return (
    <>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="display-caps text-[1.0625rem]">
          <Link href={`/topic/${topic.id}`} className="text-paper underline decoration-seam decoration-1 underline-offset-4 hover:decoration-rule">
            {topic.title}
          </Link>
        </h3>
        {hubLabel && <span className="caps shrink-0 text-paper-dim">{hubLabel}</span>}
      </div>
      <p className="mt-1 text-small text-paper-dim">{blurb ?? topic.simple}</p>
      <p className="caps mt-3 flex items-center gap-2 text-paper">
        <StateDot state={topic.state} />
        {t.states[topic.state]}
      </p>
      {topic.misconception && (
        <p className="mt-1.5 text-small text-st-confused">{topic.misconception}</p>
      )}
      <div className="mt-3.5 grid grid-cols-2 gap-1.5 sm:flex sm:flex-wrap">
        {actions.map((a) =>
          a === t.topicCard.practice ? (
            <Link
              key={a}
              href={`/practice/${topic.id}`}
              className="caps inline-flex h-10 items-center justify-center rounded-pill border border-seam px-3 text-paper transition-colors duration-[var(--dur-s)] ease-out hover:border-rule hover:bg-cloth-hover"
            >
              {a}
            </Link>
          ) : (
          <button
            key={a}
            type="button"
            disabled={busy}
            onClick={() => {
              const m = mode(a);
              if (m) void start({ domainId: domain.id, topicId: topic.id, intent: m });
              else soon(t.soon.topicAction(a, topic.title));
            }}
            className="caps h-10 rounded-pill border border-seam px-3 text-paper transition-colors duration-[var(--dur-s)] ease-out hover:border-rule hover:bg-cloth-hover disabled:opacity-50"
          >
            {a}
          </button>
          ),
        )}
      </div>
    </>
  );
}
