"use client";

// The report on a finished set (phase 4 §14.3). The numbers are the server's and are ready at once;
// the words about them are generated, so they can still be on their way. The student sees the facts
// immediately either way — a report that is only a spinner is worse than a report of numbers.

import { useEffect, useState } from "react";
import { backend } from "@/api/backend";
import { ApiError, isUuid } from "@/api/client";
import type { components } from "@/api/schema";
import { REMOTE_PREP } from "./remoteSets";
import { GIVE_UP_MS, retryDelay } from "./remoteTexts";
import styles from "./prep.module.css";

type Summary = components["schemas"]["SetSummaryOut"];

const plural = (n: number, one: string, few: string, many: string) => {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};

/** The facts, in the order a student would ask about them */
function factsOf(summary: Summary): string[] {
  const s = summary.stats;
  const facts: string[] = [];
  if (s.tasks_answered) {
    facts.push(`${s.tasks_correct} из ${s.tasks_answered} ${plural(s.tasks_answered, "задачи", "задач", "задач")} верно`);
  }
  if (s.skills_total) facts.push(`тем закрыто: ${s.skills_closed} из ${s.skills_total}`);
  if (s.mocks_completed) {
    facts.push(`${s.mocks_completed} ${plural(s.mocks_completed, "мок", "мока", "моков")}`);
  }
  if (s.days_vs_deadline > 0) facts.push(`закрыт на ${s.days_vs_deadline} дн. раньше срока`);
  else if (s.days_vs_deadline < 0) facts.push(`закрыт на ${-s.days_vs_deadline} дн. позже срока`);
  if (s.ready_by_shift_days) {
    const days = Math.abs(s.ready_by_shift_days);
    facts.push(s.ready_by_shift_days < 0 ? `прогноз сдвинулся раньше на ${days} дн.` : `прогноз сдвинулся позже на ${days} дн.`);
  }
  return facts;
}

export function SetReport({ setId }: { setId?: string }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [waitedTooLong, setWaitedTooLong] = useState(false);

  useEffect(() => {
    if (!REMOTE_PREP || !setId || !isUuid(setId)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const since = Date.now();

    const poll = async (attempt: number) => {
      try {
        const next = await backend.sets.summary(setId);
        if (cancelled) return;
        setSummary(next);
        if (next.status !== "generating") return;
        if (Date.now() - since > GIVE_UP_MS) {
          setWaitedTooLong(true);
          return;
        }
        timer = setTimeout(() => poll(attempt + 1), retryDelay(attempt));
      } catch (err) {
        // 404 — the server has nothing to report on this set, and that is an answer, not a failure
        if (!(err instanceof ApiError && err.status === 404)) {
          console.warn("Failed to read the set report:", setId, err);
        }
      }
    };
    void poll(0);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [setId]);

  if (!summary) return null;
  const facts = factsOf(summary);

  return (
    <section className={styles.theory} aria-label="Отчёт по сету">
      <header className={styles.theoryHead}>
        <h3>Отчёт по сету</h3>
        <span className={styles.muted}>
          {summary.text
            ? "по итогам работы"
            : waitedTooLong
              ? "готовим — загляни позже"
              : "готовим отчёт…"}
        </span>
      </header>

      {facts.length > 0 && (
        <div className={styles.theoryBody}>
          <p>{facts.join(" · ")}</p>
        </div>
      )}

      {/* The words come later than the numbers, and never instead of them */}
      {summary.text && (
        <div className={styles.theoryBody}>
          {summary.text.split("\n").filter(Boolean).map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      {summary.status === "failed" && !summary.text && (
        <p className={styles.muted}>Текст отчёта не получился — цифры выше всё равно верные.</p>
      )}
    </section>
  );
}
