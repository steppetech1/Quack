"use client";

// The topic's theory. The backend generates it per student and per set, so it is not a course page:
// it is the minimum needed to work on this topic now (product-logic §4.4).
//
// Four statuses, four honest things to say (ТЗ §6):
//   ready      — the text
//   generating — "готовим объяснение", asked for again with a growing pause
//   stale      — the text, marked as being refreshed
//   failed     — the local text, and a button to ask for a new one
// Without a backend, or when it has nothing, the local text stands on its own with no status at all.

import { useEffect, useRef, useState } from "react";
import { Icon } from "../choice/Icon";
import {
  fetchText,
  GIVE_UP_MS,
  MARK_WORD,
  markTextOpened,
  regenerateText,
  retryDelay,
  type RemoteText,
} from "./remoteTexts";
import { TOPICS } from "./topicContent";
import styles from "./prep.module.css";

type Props = {
  /** The backend's set id; without it there is nothing to ask the backend about */
  setId?: string;
  skillId: string;
};

/** The local text, as a few paragraphs — the shape the generated one arrives in */
function localText(skillId: string): string | null {
  const content = TOPICS[skillId];
  if (!content) return null;
  return [
    content.summary,
    ...content.points.map((p) => `— ${p}`),
    `Пример. ${content.example.q} → ${content.example.a}`,
    `Частая ошибка: ${content.trap.toLowerCase()}`,
  ].join("\n");
}

export function TopicTheory({ setId, skillId }: Props) {
  const [remote, setRemote] = useState<RemoteText | null>(null);
  const [waitedTooLong, setWaitedTooLong] = useState(false);
  const [asking, setAsking] = useState(false);
  /** `opened` goes once per topic, not once per poll */
  const openedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!setId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const since = Date.now();
    setRemote(null);
    setWaitedTooLong(false);

    const poll = async (attempt: number) => {
      const text = await fetchText(setId, skillId, "guideline");
      if (cancelled) return;
      setRemote(text);
      if (text && (text.status === "ready" || text.status === "stale")) {
        const key = `${setId}:${skillId}`;
        if (openedFor.current !== key) {
          openedFor.current = key;
          void markTextOpened(setId, skillId, "guideline");
        }
        return;
      }
      if (!text || text.status === "failed") return;
      if (Date.now() - since > GIVE_UP_MS) {
        setWaitedTooLong(true);
        return;
      }
      timer = setTimeout(() => poll(attempt + 1), retryDelay(attempt));
    };
    void poll(0);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [setId, skillId]);

  const askAgain = async () => {
    if (!setId) return;
    setAsking(true);
    const queued = await regenerateText(setId, skillId, "guideline");
    setAsking(false);
    if (queued) {
      setWaitedTooLong(false);
      setRemote((prev) => (prev ? { ...prev, status: "generating" } : prev));
    }
  };

  const fallback = localText(skillId);
  const status = remote?.status;
  const generated = (status === "ready" || status === "stale") && remote?.text ? remote.text : null;
  const body = generated ?? fallback;
  if (!body) return null;

  return (
    <section className={styles.theory} aria-label="Теория по теме">
      <header className={styles.theoryHead}>
        <h3>Теория</h3>
        <span className={styles.muted}>
          {status === "stale"
            ? "обновляем"
            : generated
              ? (remote?.mark && MARK_WORD[remote.mark]) || "сгенерировано"
              : "коротко о теме"}
        </span>
      </header>

      {status === "generating" && !generated ? (
        <p className={styles.muted}>
          {waitedTooLong ? "Готовим объяснение — загляни чуть позже." : "Готовим объяснение…"}
        </p>
      ) : (
        <div className={styles.theoryBody}>
          {body.split("\n").map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      {/* Generation failed, or it has been waiting too long: the local text stands in, and the
          student can ask for a real one rather than wait on something that already gave up. */}
      {setId && (status === "failed" || waitedTooLong) && (
        <button type="button" className={styles.theoryRetry} onClick={askAgain} disabled={asking}>
          <Icon name="sparkles" size={14} /> {asking ? "Просим…" : "Сгенерировать ещё раз"}
        </button>
      )}
    </section>
  );
}
