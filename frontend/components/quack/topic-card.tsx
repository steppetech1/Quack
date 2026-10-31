"use client";

import Link from "next/link";
import { useT } from "@/lib/i18n/provider";
import type { Topic } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
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
  const actions = [t.topicCard.study, t.topicCard.practice, t.topicCard.review, t.topicCard.history];
  return (
    <>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="font-semibold">
          <Link href={`/topic/${topic.id}`} className="text-paper underline decoration-seam decoration-1 underline-offset-4 hover:decoration-rule">
            {topic.title}
          </Link>
        </h3>
        {hubLabel && <span className="num shrink-0 text-small text-paper-dim">{hubLabel}</span>}
      </div>
      <p className="mt-1 text-small text-paper-dim">{topic.simple}</p>
      <p className="mt-3 flex items-center gap-2 text-small text-paper">
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
              className="inline-flex h-10 items-center justify-center rounded-control border border-seam px-3 text-small text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
            >
              {a}
            </Link>
          ) : (
          <button
            key={a}
            type="button"
            onClick={() => soon(t.soon.topicAction(a, topic.title))}
            className="h-10 rounded-control border border-seam px-3 text-small text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
          >
            {a}
          </button>
          ),
        )}
      </div>
    </>
  );
}
