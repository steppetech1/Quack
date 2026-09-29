"use client";

// The set's own capstone check (§4.4 «мок по всему сету», §5.4 kind "mock_set"): once every topic of
// the set holds, this is the one node past the deadline on the set's graph. The backend assembles the
// questions from `ExamFormat` and the student's info (templates + profile) the same way a topic mock
// is assembled — this screen only renders them in the shared task format.

import { useEffect, useRef, useState } from "react";
import { Icon } from "../choice/Icon";
import { skillById, type StudySet, type Task } from "./prepData";
import { checksFor } from "./topicContent";
import { adaptBackendTask, answerRemoteMock, finishRemoteMock, startRemoteSetMock } from "./remoteTasks";
import { REMOTE_PREP } from "./remoteSets";
import { isUuid } from "@/api/client";
import styles from "./prep.module.css";

type Props = {
  set: StudySet;
  onToast: (text: string) => void;
  onBack: () => void;
};

/** Demo fallback when there is no backend run: a slice across the set's own topics, not one skill. */
function localPool(set: StudySet): Task[] {
  return set.skills.flatMap((id) => checksFor(id)).slice(0, 10);
}

const LEVEL_LABEL: Record<string, string> = {
  low_data: "мало данных",
  weak: "слабо",
  shaky: "шатко",
  solid: "держится",
  closed: "закрыт",
};

export function FinalMockTest({ set, onToast, onBack }: Props) {
  const [tasks, setTasks] = useState<Task[]>(() => localPool(set));
  const [runId, setRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(REMOTE_PREP);
  const [answering, setAnswering] = useState(false);
  const [shownAt, setShownAt] = useState(0);
  const [picks, setPicks] = useState<number[]>([]);
  const [result, setResult] = useState<{ raw: number; max: number; scaled?: string | null; perSkill: { name: string; level: string }[] } | null>(
    null
  );
  const questionStartTimeRef = useRef(Date.now());

  useEffect(() => {
    let active = true;
    if (REMOTE_PREP && set.rawId && isUuid(set.rawId)) {
      startRemoteSetMock(set.exam, set.rawId).then((run) => {
        if (!active) return;
        setLoading(false);
        if (run && run.tasks.length > 0) {
          setRunId(run.run_id);
          setTasks(run.tasks.map(adaptBackendTask));
        } else {
          setTasks(localPool(set));
        }
      });
    } else {
      setLoading(false);
    }
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [set.id]);

  const task = tasks[shownAt];
  const pick = picks[shownAt];
  const finished = shownAt >= tasks.length;
  const correct = picks.filter((p, i) => tasks[i]?.options[p]?.correct).length;

  if (loading) return <FinalMockShell set={set} onBack={onBack}><p className={styles.muted}>Собираем финальный мок по темам сета…</p></FinalMockShell>;
  if (!tasks.length) {
    return (
      <FinalMockShell set={set} onBack={onBack}>
        <p className={styles.muted}>Вопросы для финального мока пока не готовы — попробуй чуть позже.</p>
      </FinalMockShell>
    );
  }

  // A backend-issued question has no `correct` flags client-side (unlike the local demo pool) and
  // /mocks/{run_id}/answer reports no per-question grade either — it is a proper timed check, the
  // score only comes back whole at `finish`. So a remote pick just moves on; only the local fallback
  // pool (whose tasks already carry the right answer) shows an immediate right/wrong.
  const answer = async (i: number) => {
    if (pick !== undefined || answering) return;
    if (task.instanceId && runId) {
      setAnswering(true);
      const elapsed = Math.max(1, Math.round((Date.now() - questionStartTimeRef.current) / 1000));
      const key = task.options[i]?.key ?? task.options[i]?.label;
      await answerRemoteMock(runId, task.instanceId, key, elapsed);
      setAnswering(false);
    }
    setPicks((p) => [...p, i]);
  };

  const next = async () => {
    const last = shownAt + 1 >= tasks.length;
    setShownAt((n) => n + 1);
    questionStartTimeRef.current = Date.now();
    if (!last) return;

    if (runId) {
      const finishResult = await finishRemoteMock(runId);
      if (finishResult) {
        setResult({
          raw: finishResult.raw_score,
          max: finishResult.max_raw,
          scaled: finishResult.scale_note,
          perSkill: finishResult.per_skill.map((s) => ({ name: s.name, level: LEVEL_LABEL[s.level] ?? s.level })),
        });
        onToast(`Финальный мок сета ${set.number} пройден: ${finishResult.raw_score} из ${finishResult.max_raw}`);
        return;
      }
    }
    onToast(`Финальный мок сета ${set.number} пройден: ${correct} из ${tasks.length}`);
  };

  return (
    <FinalMockShell set={set} onBack={onBack}>
      {finished ? (
        <div className={styles.mockResult}>
          <p className={styles.mockScore}>
            <strong>{result?.raw ?? correct}</strong> из {result?.max ?? tasks.length}
            {result?.scaled ? ` · ${result.scaled}` : ""}
          </p>
          {result ? (
            <ul className={styles.mockReview}>
              {result.perSkill.map((s) => (
                <li key={s.name}>
                  <Icon name="circle-check" size={14} />
                  <span>
                    {s.name}: {s.level}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <ul className={styles.mockReview}>
              {tasks.map((t, i) => {
                const ok = t.options[picks[i]]?.correct;
                return (
                  <li key={t.id} data-correct={ok}>
                    <Icon name={ok ? "check" : "x"} size={14} />
                    <span>{t.text}</span>
                  </li>
                );
              })}
            </ul>
          )}
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={onBack}>
              К графу сета
            </button>
          </div>
        </div>
      ) : (
        <div className={styles.task}>
          <p className={styles.eyebrow}>
            Финальный мок · вопрос {shownAt + 1} из {tasks.length}
          </p>
          <p className={styles.taskText}>{task.text}</p>
          <div className={styles.options} role="group" aria-label="Варианты ответа">
            {task.options.map((o, i) => (
              <button
                key={o.label}
                type="button"
                className={styles.option}
                data-result={pick !== undefined && !task.instanceId ? (o.correct ? "correct" : pick === i ? "wrong" : undefined) : undefined}
                disabled={pick !== undefined || answering}
                onClick={() => answer(i)}
              >
                <span className={styles.optionLetter}>{"ABCD"[i]}</span>
                {o.label}
              </button>
            ))}
          </div>
          {pick !== undefined && (
            <div className={styles.feedback} data-correct={task.instanceId ? undefined : Boolean(task.options[pick]?.correct)}>
              <strong>{task.instanceId ? "Ответ принят" : task.options[pick]?.correct ? "Верно" : "Неверно"}</strong>
              <div className={styles.actions}>
                <button type="button" className={styles.primary} onClick={next}>
                  {shownAt + 1 < tasks.length ? "Дальше" : "Итог"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </FinalMockShell>
  );
}

function FinalMockShell({ set, onBack, children }: { set: StudySet; onBack: () => void; children: React.ReactNode }) {
  return (
    <div className={styles.setDetail}>
      <header className={styles.setBar}>
        <button type="button" className={styles.backLink} onClick={onBack} aria-label="К графу сета" title="К графу сета">
          <Icon name="arrow-left" size={18} />
        </button>
        <div className={styles.setBarTitle}>
          <h2>Финальный мок · Сет {set.number}</h2>
          <span className={styles.muted}>
            {set.title} · {set.skills.map((id) => skillById(id).name).join(", ")}
          </span>
        </div>
      </header>
      <div className={styles.setStage}>
        <section className={`${styles.canvas} ${styles.mockTest}`} aria-label="Финальный мок сета">
          {children}
        </section>
      </div>
    </div>
  );
}
