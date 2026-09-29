"use client";

import { useState } from "react";
import { Icon } from "../choice/Icon";
import type { ConflictOption, Signal } from "../quack/contract";
import styles from "./dashboard.module.css";

type Target = NonNullable<Signal["target"]>;

type Props = {
  fresh: Signal[];
  history: Signal[];
  /** New for this visit: fresh now, or fresh when the student opened Quack */
  isNew: (signal: Signal) => boolean;
  onTarget: (target: Target) => void;
  /** Ticked milestones: a date signal about one of them offers «Уже сделал» until it is ticked */
  done: string[];
  onMark: (milestone: string, done: boolean) => void;
  /** A way out of a date conflict taken; returns how to take it back */
  onConflict: (conflictId: string, option: ConflictOption) => () => void;
  /** Conflicts settled on earlier visits: id → the way out chosen, and taking it back */
  resolved: Record<string, string>;
  onUnresolve: (conflictId: string) => void;
  /**
   * Deciding a backend recommendation (phase 4). Accepting changes the plan on the server; declining
   * keeps it out of the feed. Absent while the browser computes the feed itself — then there is
   * nothing to post and the signal is shown as words only.
   */
  onDecide?: (id: string, decision: "accept" | "decline") => Promise<void>;
};

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

const TONE_ICON = { up: "trending-up", down: "trending-down", info: "info" } as const;
const TARGET_LABEL: Record<Target, string> = {
  prep: "Открыть подготовку",
  calendar: "Открыть календарь",
  programs: "Открыть программы",
  profile: "Открыть профиль",
};
const SHOWN = 8;

function ago(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

/** Why the Quack! button glowed: every change since the last visit, then the ones already seen. */
export function ChangesFeed({ fresh, history, isNew, onTarget, done, onMark, onConflict, resolved, onUnresolve, onDecide }: Props) {
  const key = (s: Signal) => `${s.id}@${s.at}`;
  // A signal ticked on this visit goes out of the standing at once; it stays here, ticked, so it can be undone
  const [ticked, setTicked] = useState<Signal[]>([]);
  // A conflict settled on this visit: which way out, and how to take it back
  const [settled, setSettled] = useState<Record<string, { label: string; undo: () => void }>>({});
  // A recommendation decided on this visit: the buttons go at once, the next read confirms it
  const [decided, setDecided] = useState<Record<string, "accept" | "decline" | "failed">>({});

  const decide = (id: string, decision: "accept" | "decline") => {
    setDecided((all) => ({ ...all, [id]: decision }));
    onDecide?.(id, decision).catch(() => setDecided((all) => ({ ...all, [id]: "failed" })));
  };

  const settle = (s: Signal, option: ConflictOption) => {
    const undo = onConflict(s.conflict!.id, option);
    setTicked((list) => (list.some((t) => key(t) === key(s)) ? list : [s, ...list]));
    setSettled((all) => ({ ...all, [s.id]: { label: option.label, undo } }));
  };

  const unsettle = (s: Signal) => {
    settled[s.id]?.undo();
    setTicked((list) => list.filter((t) => t.id !== s.id));
    setSettled(({ [s.id]: _gone, ...rest }) => rest);
  };
  // A signal that went and came back (a tick taken back) is shown once, as it stands now
  const items = [...ticked, ...fresh, ...history].filter((s, i, all) => all.findIndex((x) => x.id === s.id) === i).slice(0, SHOWN);

  const mark = (s: Signal, value: boolean) => {
    // Taken back: the standing raises the signal again by itself, so the kept copy goes
    if (value) setTicked((list) => (list.some((t) => key(t) === key(s)) ? list : [s, ...list]));
    else setTicked((list) => list.filter((t) => t.milestone !== s.milestone));
    onMark(s.milestone!, value);
  };
  const newCount = items.filter(isNew).length;

  return (
    <section className={styles.card} aria-label="Что изменилось">
      <header className={styles.cardHead}>
        <h3>Что изменилось</h3>
        {newCount > 0 && <span className={styles.counter}>{newCount} нов.</span>}
      </header>

      {items.length === 0 ? (
        <p className={styles.muted}>
          Пока тихо. Как только что-то пересчитается — профиль, избранное или подготовка, — это появится здесь, а кнопка Quack!
          засветится.
        </p>
      ) : (
        <ol className={styles.feed}>
          {items.map((s) => (
            <li
              key={key(s)}
              className={styles.feedItem}
              data-level={s.level}
              data-tone={s.tone}
              data-new={isNew(s)}
            >
              <span className={styles.feedIcon}>
                <Icon name={TONE_ICON[s.tone]} size={16} />
              </span>
              <div className={styles.feedBody}>
                <p className={styles.feedTitle}>{s.title}</p>
                {s.detail && <p className={styles.feedDetail}>{s.detail}</p>}
                <p className={styles.feedMeta}>{[s.cause, ago(s.at)].filter(Boolean).join(" · ")}</p>
                {s.milestone && done.includes(s.milestone) && (
                  <p className={styles.markDone}>
                    <Icon name="check" size={14} /> Отмечено как сделанное ·{" "}
                    <button type="button" className={styles.inlineLink} onClick={() => mark(s, false)}>
                      вернуть
                    </button>
                  </p>
                )}
                {/* A date conflict: its ways out, one press each — or the one taken, with a way back */}
                {s.conflict &&
                  (settled[s.id] || resolved[s.conflict.id] ? (
                    <p className={styles.markDone}>
                      <Icon name="check" size={14} /> Решено: {lowerFirst(settled[s.id]?.label ?? resolved[s.conflict.id])} ·{" "}
                      <button
                        type="button"
                        className={styles.inlineLink}
                        onClick={() => (settled[s.id] ? unsettle(s) : onUnresolve(s.conflict!.id))}
                      >
                        вернуть
                      </button>
                    </p>
                  ) : (
                    <div className={styles.feedOptions}>
                      {s.conflict.options.map((o) => (
                        <button key={o.label} type="button" className={styles.markButton} onClick={() => settle(s, o)}>
                          {o.label}
                        </button>
                      ))}
                    </div>
                  ))}
                {/* A backend recommendation: taking it changes the plan server-side (product-logic §3.6) */}
                {s.recommendation &&
                  onDecide &&
                  (decided[s.id] === "accept" || s.recommendation.status === "accepted" ? (
                    <p className={styles.markDone}>
                      <Icon name="check" size={14} /> Принято
                    </p>
                  ) : decided[s.id] === "decline" || s.recommendation.status === "declined" ? (
                    <p className={styles.markDone}>Отложено</p>
                  ) : (
                    <div className={styles.feedOptions}>
                      <button type="button" className={styles.markButton} onClick={() => decide(s.id, "accept")}>
                        {s.recommendation.actionText}
                      </button>
                      <button type="button" className={styles.markButton} onClick={() => decide(s.id, "decline")}>
                        Не сейчас
                      </button>
                      {decided[s.id] === "failed" && <span className={styles.feedMeta}>Не получилось — попробуй ещё раз</span>}
                    </div>
                  ))}
              </div>
              <div className={styles.feedActions}>
                {s.milestone && !done.includes(s.milestone) && (
                  <button type="button" className={styles.markButton} onClick={() => mark(s, true)}>
                    <Icon name="check" size={14} /> Уже сделал
                  </button>
                )}
                {s.target && s.target !== "profile" && (
                  <button
                    type="button"
                    className={styles.feedGo}
                    aria-label={TARGET_LABEL[s.target]}
                    title={TARGET_LABEL[s.target]}
                    onClick={() => onTarget(s.target!)}
                  >
                    <Icon name="chevron-right" size={16} />
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
