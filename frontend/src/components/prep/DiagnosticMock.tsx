"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "../choice/Icon";
import {
  DIAGNOSTIC_8_QUESTIONS,
  evaluateDiagnostic,
  type DiagnosticQuestion,
  type DiagnosticResultSummary,
} from "./diagnosticData";
import {
  adaptBackendTaskToDiagnosticQuestion,
  adaptDiagnosticResult,
  fetchActiveOrStartDiagnostic,
  finishDiagnosticRun,
  submitDiagnosticAnswer,
} from "./remoteDiagnostic";
import { REMOTE_PREP } from "./remoteSets";
import type { ExamId } from "./prepData";
import type { BackendDiagnosticOut, BackendTaskInstanceOut } from "@/api/backend";
import styles from "./prep.module.css";

type Props = {
  onComplete: (summary: DiagnosticResultSummary) => void;
  /** Without it the test cannot be left, only finished or skipped (the first visit) */
  onClose?: () => void;
  onSkip?: () => void;
  exam?: ExamId;
};

/** `selectedOption` for a typed answer: matches no option index, so nothing lights up */
const NUMERIC_ANSWER = -1;

type AnswerRecord = {
  optionIndex: number;
  correct: boolean;
  trap?: string;
};

/**
 * Обязательный входной мок-тест на 8 вопросов для новых пользователей.
 * В remote-режиме подключается к /diagnostic и динамически калибрует маршрут.
 * В local-режиме работает автономно на 8 базовых вопросах.
 */
export function DiagnosticMock({ onComplete, onClose, onSkip, exam = "sat" }: Props) {
  const [run, setRun] = useState<BackendDiagnosticOut | null>(null);
  const [questions, setQuestions] = useState<DiagnosticQuestion[]>(DIAGNOSTIC_8_QUESTIONS);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, AnswerRecord>>({});
  const [selectedOption, setSelectedOption] = useState<number | null>(null);
  const [isFinished, setIsFinished] = useState(false);
  const [remoteSummary, setRemoteSummary] = useState<DiagnosticResultSummary | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pendingNextTask, setPendingNextTask] = useState<BackendTaskInstanceOut | null>(null);
  /**
   * The server refused something. A question the server issued is the server's to grade, so when it
   * will not, the student hears that instead of a verdict the browser made up (ТЗ §6, X10).
   */
  const [remoteFailure, setRemoteFailure] = useState<string | null>(null);
  /** What the student typed for a numeric task, before they send it */
  const [typedAnswer, setTypedAnswer] = useState("");
  const startTimeRef = useRef<number>(Date.now());

  // Initialize remote diagnostic run if REMOTE_PREP is active
  useEffect(() => {
    if (!REMOTE_PREP) return;

    let cancelled = false;
    fetchActiveOrStartDiagnostic(exam)
      .then((activeRun) => {
        if (cancelled || !activeRun) return;
        setRun(activeRun);
        if (activeRun.next_task) {
          const firstQ = adaptBackendTaskToDiagnosticQuestion(activeRun.next_task);
          setQuestions([firstQ]);
          setCurrentIndex(0);
          startTimeRef.current = Date.now();
        }
      })
      .catch((err) => {
        console.warn("Failed to init remote diagnostic, falling back to local:", err);
      });

    return () => {
      cancelled = true;
    };
  }, [exam]);

  const total = run ? 8 : questions.length;
  const currentQ = questions[currentIndex] || DIAGNOSTIC_8_QUESTIONS[0];

  /**
   * The backend issues numeric tasks in a diagnostic too (`type: "numeric"`), and those come with no
   * options at all. They are answered by typing, and marked as answered with NUMERIC_ANSWER — a
   * sentinel that matches no option index, so nothing highlights and the feedback still opens.
   */
  const isNumeric = currentQ.options.length === 0;

  const handleSelect = async (optionIndex: number, typed?: string) => {
    if (selectedOption !== null || submitting) return;
    setRemoteFailure(null);
    setSelectedOption(optionIndex);

    if (REMOTE_PREP && run) {
      setSubmitting(true);
      const answerKey =
        typed ?? (currentQ.options[optionIndex]?.key || String.fromCharCode(65 + optionIndex));
      const timeSpentSec = Math.max(1, Math.round((Date.now() - startTimeRef.current) / 1000));
      const updatedRun = await submitDiagnosticAnswer(run.run_id, currentQ.id, answerKey, timeSpentSec);
      setSubmitting(false);

      if (updatedRun) {
        setRun(updatedRun);
        const isCorrect = Boolean(updatedRun.state.last_grade_correct);
        const trapText =
          updatedRun.state.trap_hits.length > (run.state.trap_hits?.length ?? 0)
            ? updatedRun.state.trap_hits[updatedRun.state.trap_hits.length - 1]
            : undefined;

        setAnswers((prev) => ({
          ...prev,
          [currentQ.id]: {
            optionIndex,
            correct: isCorrect,
            trap: trapText,
          },
        }));
        setPendingNextTask(updatedRun.next_task);
        return;
      }

      // The task came from the server; grading it here would be a different answer from the one that
      // counts, and nothing would be recorded. Say so and let the student try again.
      setSelectedOption(null);
      setRemoteFailure("Не удалось записать ответ — сервер не принял его. Попробуй ещё раз.");
      return;
    }

    // Local fallback evaluation
    const opt = currentQ.options[optionIndex];
    setAnswers((prev) => ({
      ...prev,
      [currentQ.id]: {
        optionIndex,
        correct: Boolean(opt?.correct),
        trap: opt?.trap,
      },
    }));
  };

  const handleNext = async () => {
    if (REMOTE_PREP && run) {
      if (pendingNextTask && currentIndex < total - 1) {
        const nextQ = adaptBackendTaskToDiagnosticQuestion(pendingNextTask);
        setQuestions((prev) => {
          if (prev.some((q) => q.id === nextQ.id)) return prev;
          return [...prev, nextQ];
        });
        setCurrentIndex((prev) => prev + 1);
        setSelectedOption(null);
        setTypedAnswer("");
        setPendingNextTask(null);
        startTimeRef.current = Date.now();
        return;
      }

      // Finish diagnostic on backend
      setSubmitting(true);
      const result = await finishDiagnosticRun(run.run_id);
      setSubmitting(false);

      if (!result) {
        // Without the server's result there is no result: a locally counted score would be about
        // questions the server never recorded an answer for.
        setRemoteFailure("Не удалось подвести итог замера — сервер не ответил. Попробуй ещё раз.");
        return;
      }
      setRemoteSummary(adaptDiagnosticResult(result, run.state.answered));
      setIsFinished(true);
      return;
    }

    // Local flow
    if (currentIndex < questions.length - 1) {
      setCurrentIndex((prev) => prev + 1);
      const nextAns = answers[questions[currentIndex + 1]?.id];
      setSelectedOption(nextAns ? nextAns.optionIndex : null);
    } else {
      setIsFinished(true);
    }
  };

  const handleRestart = () => {
    setCurrentIndex(0);
    setAnswers({});
    setSelectedOption(null);
    setIsFinished(false);
    setRemoteSummary(null);
    setPendingNextTask(null);
    startTimeRef.current = Date.now();
    if (REMOTE_PREP) {
      fetchActiveOrStartDiagnostic(exam).then((newRun) => {
        if (newRun && newRun.next_task) {
          setRun(newRun);
          setQuestions([adaptBackendTaskToDiagnosticQuestion(newRun.next_task)]);
        }
      });
    }
  };

  // Build summary for local or remote
  const localAnswersMap: Record<string, number> = {};
  for (const [id, rec] of Object.entries(answers)) {
    localAnswersMap[id] = rec.optionIndex;
  }
  const localSummary = evaluateDiagnostic(localAnswersMap);
  const summary: DiagnosticResultSummary = remoteSummary ?? {
    ...localSummary,
    score: Object.values(answers).filter((a) => a.correct).length,
    total,
  };
  const percent = Math.round((summary.score / Math.max(summary.total, 1)) * 100);

  return (
    <section className={styles.diagnosticInline} aria-labelledby="diag-title">
      <div className={styles.diagnosticModalBox}>
        {/* Шапка модального окна */}
        <header className={styles.diagnosticModalHead}>
          <div>
            <div className={styles.diagnosticBadgeGroup}>
              <span className={styles.diagnosticTag}>
                <Icon name="sparkles" size={12} /> Входной замер
              </span>
              <span className={styles.diagnosticSubtitle}>{total} вопросов · калибровка маршрута</span>
            </div>
            <h3 id="diag-title" className={styles.diagnosticTitle}>
              {isFinished ? "Итог входного замера" : `Вопрос ${currentIndex + 1} из ${total}`}
            </h3>
          </div>

          <div className={styles.diagHeadActions}>
            {onSkip && (
              <button
                type="button"
                className={styles.diagSkipBtn}
                onClick={onSkip}
                title="Скинуть тест и использовать базовые оценки"
              >
                Скинуть тест
              </button>
            )}
            {onClose && (
              <button
                type="button"
                className={styles.topicClose}
                onClick={onClose}
                aria-label="Закрыть тест"
                title="Закрыть замер"
              >
                <Icon name="x" size={16} />
              </button>
            )}
          </div>
        </header>

        {/* Сервер не принял ответ или итог: говорим об этом, а не рисуем выдуманный результат */}
        {remoteFailure && (
          <p className={styles.warn} role="status">
            {remoteFailure}
          </p>
        )}

        {/* Индикатор прогресса */}
        {!isFinished && (
          <div className={styles.diagnosticDots} aria-label="Прогресс по вопросам">
            {Array.from({ length: total }).map((_, idx) => {
              const q = questions[idx];
              const answered = q ? answers[q.id] : undefined;
              const isCurrent = idx === currentIndex;
              let dotState = "empty";
              if (answered !== undefined) {
                dotState = answered.correct ? "correct" : "wrong";
              } else if (isCurrent) {
                dotState = "current";
              }
              return (
                <span
                  key={idx}
                  className={styles.diagnosticDot}
                  data-state={dotState}
                  title={`Вопрос ${idx + 1}`}
                />
              );
            })}
          </div>
        )}

        {/* Экран прохождения вопросов */}
        {!isFinished ? (
          <div className={styles.diagnosticBody}>
            {/* Карточка текущего вопроса */}
            <div className={styles.diagnosticQuestionCard}>
              <div className={styles.diagnosticAreaMeta}>
                <span>{currentQ.area}</span>
                <span>·</span>
                <span className={styles.diagnosticSkillName}>{currentQ.skillName}</span>
              </div>

              <p className={styles.diagnosticQuestionText}>{currentQ.question}</p>

              {/* Варианты ответа */}
              {/* Задача без вариантов — числовая: ответ вводится, а не выбирается */}
              {isNumeric ? (
                <form
                  className={styles.diagnosticNumeric}
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (typedAnswer.trim()) handleSelect(NUMERIC_ANSWER, typedAnswer.trim());
                  }}
                >
                  <input
                    autoFocus
                    className={styles.diagnosticNumericInput}
                    inputMode="decimal"
                    aria-label="Ответ"
                    placeholder="Ответ"
                    value={typedAnswer}
                    disabled={selectedOption !== null || submitting}
                    onChange={(e) => setTypedAnswer(e.target.value)}
                  />
                  <button
                    type="submit"
                    className={styles.diagnosticNumericBtn}
                    disabled={!typedAnswer.trim() || selectedOption !== null || submitting}
                  >
                    Ответить
                  </button>
                </form>
              ) : null}

              <div className={styles.diagnosticOptionsGrid}>
                {currentQ.options.map((opt, oIdx) => {
                  const isPicked = selectedOption === oIdx;
                  let stateClass = "";
                  if (selectedOption !== null) {
                    const ansRec = answers[currentQ.id];
                    if (ansRec) {
                      if (ansRec.correct && isPicked) stateClass = styles.optCorrect;
                      else if (!ansRec.correct && isPicked) stateClass = styles.optWrong;
                    } else if (opt.correct) {
                      stateClass = styles.optCorrect;
                    } else if (isPicked) {
                      stateClass = styles.optWrong;
                    }
                  }

                  return (
                    <button
                      key={oIdx}
                      type="button"
                      disabled={selectedOption !== null || submitting}
                      className={`${styles.diagnosticOptionBtn} ${stateClass} ${isPicked ? styles.optSelected : ""}`}
                      onClick={() => handleSelect(oIdx)}
                    >
                      <span className={styles.diagnosticOptionIndex}>
                        {String.fromCharCode(65 + oIdx)}
                      </span>
                      <span className={styles.diagnosticOptionLabel}>{opt.label}</span>
                      {selectedOption !== null && answers[currentQ.id]?.correct && isPicked && (
                        <Icon name="check" size={15} className={styles.optCheckIcon} />
                      )}
                    </button>
                  );
                })}
              </div>

              {/* Пояснение и предупреждение о ловушке после ответа */}
              {selectedOption !== null && (
                <div className={styles.diagnosticFeedback}>
                  {answers[currentQ.id]?.trap && (
                    <div className={styles.diagnosticTrapAlert}>
                      <Icon name="triangle-alert" size={14} />
                      <span>Ловушка: {answers[currentQ.id].trap}</span>
                    </div>
                  )}

                  <div className={styles.diagnosticExplainBox}>
                    <strong>Разбор:</strong> {currentQ.explanation}
                  </div>
                </div>
              )}
            </div>

            {/* Футер вопроса: кнопка дальше */}
            <footer className={styles.diagnosticFoot}>
              {onSkip && (
                <button
                  type="button"
                  className={styles.diagSkipLink}
                  onClick={onSkip}
                  title="Скинуть тест и применить базовые оценки"
                >
                  Скинуть тест (взять базовые оценки)
                </button>
              )}
              <button
                type="button"
                disabled={selectedOption === null || submitting}
                className={styles.primary}
                onClick={handleNext}
              >
                {submitting ? (
                  "Сверяем..."
                ) : currentIndex < total - 1 ? (
                  <>
                    Следующий вопрос <Icon name="chevron-right" size={16} />
                  </>
                ) : (
                  <>
                    Завершить замер <Icon name="check" size={16} />
                  </>
                )}
              </button>
            </footer>
          </div>
        ) : (
          /* Экран результатов */
          <div className={styles.diagnosticResultBody}>
            <div className={styles.diagnosticScoreCard}>
              <div className={styles.diagnosticScoreNum}>
                <strong>{summary.score}</strong> / {summary.total}
              </div>
              <p className={styles.diagnosticPercent}>{percent}% верных ответов</p>

              <p className={styles.diagnosticVerdict}>
                {summary.words
                  ? summary.words
                  : percent >= 75
                  ? "Отличная база! Сильные стороны зафиксированы. Ассистент ускорит стартовые сеты и сфокусируется на продвинутых темах."
                  : percent >= 50
                  ? "Хороший старт! Выявлены ключевые темы и ловушки — ассистент скорректировал маршрут для закрытия слабых мест."
                  : "Диагностика выявила базовые пробелы. Стартовые сеты перестроены от фундамента, чтобы не допустить ошибок выше."}
              </p>
            </div>

            {/* Сводка по темам */}
            <div className={styles.diagnosticBreakdown}>
              {summary.solidSkills.length > 0 && (
                <div className={styles.breakdownSection}>
                  <p className={styles.breakdownTitleOk}>
                    <Icon name="check" size={14} /> Твёрдо усвоенные темы ({summary.solidSkills.length})
                  </p>
                  <ul className={styles.breakdownList}>
                    {summary.solidSkills.map((s, idx) => (
                      <li key={idx} className={styles.breakdownChipOk}>
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {summary.attentionSkills.length > 0 && (
                <div className={styles.breakdownSection}>
                  <p className={styles.breakdownTitleWarn}>
                    <Icon name="triangle-alert" size={14} /> Требуют укрепления в маршруте ({summary.attentionSkills.length})
                  </p>
                  <ul className={styles.breakdownList}>
                    {summary.attentionSkills.map((s, idx) => (
                      <li key={idx} className={styles.breakdownChipWarn}>
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {summary.trapsCaught.length > 0 && (
                <div className={styles.breakdownSection}>
                  <p className={styles.breakdownTitleTrap}>
                    Зафиксированные ловушки ({summary.trapsCaught.length})
                  </p>
                  <ul className={styles.trapsList}>
                    {summary.trapsCaught.map((t, idx) => (
                      <li key={idx}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Футер результатов */}
            <footer className={styles.diagnosticResultFoot}>
              <button
                type="button"
                className={styles.primary}
                onClick={() => onComplete(summary)}
              >
                Собрать маршрут и начать <Icon name="chevron-right" size={16} />
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={handleRestart}
              >
                Пройти заново
              </button>
            </footer>
          </div>
        )}
      </div>
    </section>
  );
}
