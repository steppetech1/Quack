import { useEffect, useState } from "react";
import { Icon } from "../choice/Icon";
import type { Program } from "../choice/programs";
import { useQuack } from "../quack/source";
import { routeDelay } from "../quack/standing";
import type { BackendConflict } from "@/api/backend";
import {
  REMOTE_PREP,
  fetchRemoteOverview,
  getCachedRemoteOverview,
  markRemoteMilestone,
  prefetchRemoteOverview,
  type RemotePrepOverview,
} from "./remotePrep";
import {
  allSkills,
  daysBetween,
  formatDate,
  formatShort,
  forecastSeries,
  milestones,
  requirements,
  SETS,
  knownSet,
  setById,
  skillById,
  SKILLS,
  STATE_LABEL,
  TODAY,
  EXAM_IDS,
  EXAMS,
  dateKey,
  examMilestoneId,
  plannedTest,
  TARGET_RANGE,
  type DatedExam,
  type ExamId,
  type ExamOutlook,
  type ExamRequirement,
  type Milestone,
  type StudySet,
} from "./prepData";
import { chooseTestDate, disputeMisconception, proposedSet, readiness, setReport, type PrepModel, type PrepSub, type PrepTab, type SetReport } from "./prepModel";
import { disputeRemoteMisconception } from "./remoteKnowledge";
import { fetchRemoteSets, getCachedRemoteSets, type RemoteSetsData } from "./remoteSets";
import { StateGlyph } from "./SkillGraph";
import { SetDetail } from "./SetDetail";
import { DiagnosticMock } from "./DiagnosticMock";
import type { DiagnosticResultSummary } from "./diagnosticData";
import { PixelDuck } from "../duck/PixelDuck";
import styles from "./prep.module.css";

type Props = {
  model: PrepModel;
  programs: Program[];
  sub: PrepSub;
  /** Jump to another tab, optionally straight to one of its sub-tabs */
  onGo: (tab: PrepTab, sub?: PrepSub, exam?: ExamId) => void;
  /** Opens a set: the active one in «Сейчас», any other in «Маршрут» */
  onOpenSet: (setId: string, topic?: string) => void;
  onAccept: (setId: string) => void;
  onModel: (model: PrepModel) => void;
  onToast: (text: string) => void;
  /** A topic of the active set asked for from elsewhere; `n` changes on every ask so it opens again */
  focus: { topic?: string; n: number } | null;
  diagnostic: {
    /** The test is on screen (first visit after the start button, or a retake) */
    open: boolean;
    onStart: () => void;
    onClose?: () => void;
    onComplete: (summary: DiagnosticResultSummary) => void;
    onSkip: () => void;
  };
};

/**
 * §4.1 — the section's overview in two sub-tabs. «Сейчас» is the one way in: the entrance test on
 * the first visit, then the active set itself — its graph of topics, the chat and the mocks.
 */
export function Overview({ model, programs, sub, onGo, onOpenSet, onAccept, onModel, onToast, focus, diagnostic }: Props) {
  const [remoteData, setRemoteData] = useState<RemotePrepOverview | null>(() => {
    if (typeof window === "undefined" || !REMOTE_PREP) return null;
    return getCachedRemoteOverview();
  });
  const [loading, setLoading] = useState(() => REMOTE_PREP && !getCachedRemoteOverview());

  useEffect(() => {
    if (!REMOTE_PREP) return;
    let cancelled = false;
    fetchRemoteOverview(programs)
      .then((data) => {
        if (!cancelled) {
          setRemoteData(data);
          setLoading(false);
          if (data.doneMilestoneKeys.length) {
            onModel({
              ...model,
              milestonesDone: Array.from(new Set([...model.milestonesDone, ...data.doneMilestoneKeys])),
            });
          }
        }
      })
      .catch((err) => {
        if (!cancelled) setLoading(false);
        console.warn("Failed to fetch remote overview, using local fallback:", err);
      });
    return () => {
      cancelled = true;
    };
  }, [programs.length]);

  // Each exam with a knowledge model has its own readiness, history and forecast
  const series = Object.fromEntries(
    EXAM_IDS.map((id) => {
      const now = readiness(model, id);
      return [id, { readiness: now, ...forecastSeries(now, model.extraDays, id) }];
    })
  ) as Record<ExamId, { readiness: number } & ReturnType<typeof forecastSeries>>;

  const outlook: ExamOutlook = remoteData?.outlook ?? {
    sat: { readiness: series.sat.readiness, forecast: series.sat.forecast },
    ent: { readiness: series.ent.readiness, forecast: series.ent.forecast },
  };
  const exams = remoteData
    ? remoteData.requirements
    : REMOTE_PREP
    ? []
    : requirements(programs, outlook, model.testDates, model.targets);
  const list = remoteData
    ? remoteData.milestones
    : REMOTE_PREP
    ? []
    : milestones(programs, model.testDates);
  const conflicts = remoteData?.conflicts ?? [];

  const handleToggleMilestone = async (milestone: Milestone, done: boolean) => {
    const key = milestone.key || milestone.id;
    const updatedDone = done
      ? Array.from(new Set([...model.milestonesDone, key]))
      : model.milestonesDone.filter((k) => k !== key);
    onModel({ ...model, milestonesDone: updatedDone });

    if (REMOTE_PREP && milestone.key) {
      try {
        await markRemoteMilestone(milestone.key, done);
        onToast(done ? `Отмечено: «${milestone.title}»` : `Снята отметка: «${milestone.title}»`);
      } catch (err) {
        console.error("Failed to mark milestone:", err);
        onModel({ ...model, milestonesDone: model.milestonesDone });
        onToast("Не удалось сохранить статус вехи");
      }
    } else {
      onToast(done ? `Отмечено: «${milestone.title}»` : `Снята отметка: «${milestone.title}»`);
    }
  };

  if (sub === "requirements")
    return (
      <Requirements
        exams={exams}
        loading={loading && !remoteData}
        milestoneList={list}
        done={model.milestonesDone}
        onPick={(exam, key) => {
          onModel(chooseTestDate(model, exam, key));
          onToast(`Тест ${EXAMS[exam].name} — ${formatDate(new Date(`${key}T00:00`))}. Вехи и прогноз пересчитаны`);
        }}
        onTarget={(exam, value) => {
          const targets = { ...model.targets };
          if (value === null) delete targets[exam];
          else targets[exam] = value;
          onModel({ ...model, targets });
          onToast(value === null ? `Цель ${EXAMS[exam].name} — снова по программам` : `Цель ${EXAMS[exam].name} — ${value}`);
        }}
      >
        <MilestonesSection
          milestones={list}
          doneKeys={model.milestonesDone}
          onToggle={handleToggleMilestone}
        />
        <Important
          model={model}
          milestoneList={list}
          conflicts={conflicts}
          onGo={onGo}
          onOpenSet={onOpenSet}
          onModel={onModel}
          onToast={onToast}
        />
      </Requirements>
    );
  return (
    <Now
      model={model}
      exams={exams}
      forecasts={{ sat: outlook.sat.forecast, ent: outlook.ent.forecast }}
      onGo={onGo}
      onAccept={onAccept}
      onModel={onModel}
      onToast={onToast}
      focus={focus}
      diagnostic={diagnostic}
    />
  );
}

/* ---------- Сейчас: the entrance test, then the active set right here ---------- */

const DUCK_TEMPO = ["fast", "fast", "steady", "chill"] as const;

type Pace = { id: string; name: string; level: 0 | 1 | 2 | 3; verdict: string; summary: string };

function Now({
  model,
  exams,
  forecasts,
  onGo,
  onAccept,
  onModel,
  onToast,
  focus,
  diagnostic,
}: {
  model: PrepModel;
  exams: ExamRequirement[];
  forecasts: Record<ExamId, Date>;
  onGo: (tab: PrepTab, sub?: PrepSub, exam?: ExamId) => void;
  onAccept: (setId: string) => void;
  onModel: (model: PrepModel) => void;
  onToast: (text: string) => void;
  focus: Props["focus"];
  diagnostic: Props["diagnostic"];
}) {
  const { state } = useQuack();
  const targetExam: ExamId = (exams.find((e) => e.id === "ent" || e.id === "sat")?.id as ExamId) ?? "sat";
  const [remoteSets, setRemoteSets] = useState<RemoteSetsData | null>(() => {
    if (!REMOTE_PREP) return null;
    return getCachedRemoteSets(targetExam);
  });

  useEffect(() => {
    if (!REMOTE_PREP) return;
    let active = true;
    fetchRemoteSets(targetExam).then((data) => {
      if (active) setRemoteSets(data);
    }).catch(() => {});
    return () => {
      active = false;
    };
  }, [targetExam]);

  const current = REMOTE_PREP
    ? (model.currentSet ? knownSet(model.currentSet) : null) ?? remoteSets?.current ?? null
    : model.currentSet ? setById(model.currentSet) : null;
  const isDiagPending = !model.diagnosticDone;

  // Quack's verdict per exam — strictly filtered to exams that are actually required
  const paces: Pace[] = exams.map((exam) => {
    const standingPace = state.standing?.exams.find((se) => se.id === exam.id);
    if (standingPace) {
      return {
        id: standingPace.id,
        name: standingPace.name,
        level: standingPace.level,
        verdict: standingPace.verdict,
        summary: standingPace.summary,
      };
    }
    const examId = exam.id as ExamId;
    const test = exam.testDate ?? plannedTest(examId, model.testDates) ?? EXAMS[examId]?.test ?? TODAY;
    const forecastDate = forecasts[examId] ?? EXAMS[examId]?.test ?? TODAY;
    const onTime = forecastDate <= test;
    return {
      id: exam.id,
      name: exam.name,
      level: onTime ? 3 : 0,
      verdict: onTime ? "Успеваешь" : "Не успеваешь",
      summary: `прогноз на ${formatDate(forecastDate)}, тест ${formatDate(test)}`,
    };
  });

  // The test itself, in place: its result builds the route and the first set opens on the same spot
  if (diagnostic.open)
    return (
      <DiagnosticMock
        exam={targetExam}
        onComplete={diagnostic.onComplete}
        onClose={diagnostic.onClose}
        onSkip={isDiagPending ? diagnostic.onSkip : undefined}
      />
    );

  // The active set is the screen: its topics on their timeline, a topic opens the chat and the mock
  if (!isDiagPending && current)
    return (
      <div className={styles.nowWork}>
        <div className={styles.nowStrip}>
          {paces.length > 0 && (
            <ul className={styles.nowStripPaces} aria-label="Темп по экзаменам">
              {paces.map((p) => (
                <li key={p.id} data-pace={p.level} title={p.summary}>
                  <PixelDuck tempo={DUCK_TEMPO[p.level]} className={styles.nowStripDuck} asleep={p.level === 0} />
                  <span className={styles.nowPaceName}>{p.name}</span>
                  <strong>{p.verdict}</strong>
                </li>
              ))}
            </ul>
          )}
          <span className={styles.nowStripLinks}>
            {/* Skipped at the start: the test is still there, never in the way */}
            {model.diagnosticSkipped && (
              <button type="button" className={styles.link} onClick={diagnostic.onStart}>
                Пройти замер
              </button>
            )}
            <button type="button" className={styles.link} onClick={() => onGo("sets", "route")}>
              Сменить сет →
            </button>
          </span>
        </div>
        <SetDetail
          key={`${current.id}-${focus?.n ?? 0}`}
          model={model}
          set={current}
          topic={focus?.topic}
          onMakeCurrent={onAccept}
          onModel={onModel}
          onToast={onToast}
        />
      </div>
    );

  const proposed = isDiagPending
    ? null
    : REMOTE_PREP
      ? (remoteSets?.upcoming?.[0] ?? null)
      : (proposedSet(model) ?? null);
  const topicId = proposed?.skills.find((id) => model.states[id] !== "solid") ?? proposed?.skills[0];
  const topic = topicId ? skillById(topicId) : null;
  const report = isDiagPending ? null : setReport(model);
  // Nothing to study can mean two different things, and they must not share a sentence: either the
  // route is behind the student, or the plan came back with no topics in it at all.
  const passedSomething = REMOTE_PREP ? Boolean(remoteSets?.done?.length) : model.doneSets.length > 0;
  const nothingToStudy = passedSomething
    ? "Все сеты пройдены — осталось закрепление и тест"
    : "Тем пока нет: в плане только закрепление перед тестом";

  return (
    <div className={styles.nowScreen}>
      <section className={styles.nowCenter} aria-label="Сейчас">
        {report && <SetReportCard report={report} pace={paces.find((p) => p.id === report.set.exam)} onChoose={() => onGo("sets", "route")} />}
        <PixelDuck tempo="steady" className={styles.nowDuck} waving />
        {isDiagPending ? (
          <>
            <p className={styles.eyebrow}>Необязательно</p>
            <h2 className={styles.nowSet}>Входной замер · 8 вопросов</h2>
            <p className={styles.nowTopic}>
              <Icon name="sparkles" size={14} />
              Найдём, с какого уровня строить сеты. Можно и позже — тогда первый сет соберём по твоему профилю
            </p>
          </>
        ) : proposed && topic ? (
          <>
            <p className={styles.eyebrow}>Ассистент предлагает начать с</p>
            <h2 className={styles.nowSet}>
              Сет {proposed.number} · {proposed.title}
            </h2>
            <p className={styles.nowTopic}>
              <StateGlyph state={model.states[topic.id]} size={14} />
              {topic.name}
            </p>
          </>
        ) : (
          <h2 className={styles.nowSet}>{nothingToStudy}</h2>
        )}

        {paces.length > 0 && (
          <ul className={styles.nowPaces}>
            {paces.map((p) => (
              <li key={p.id} data-pace={p.level}>
                <PixelDuck tempo={DUCK_TEMPO[p.level]} className={styles.nowPaceDuck} asleep={p.level === 0} />
                <span className={styles.nowPaceName}>{p.name}</span>
                <strong>{p.verdict}</strong>
              </li>
            ))}
          </ul>
        )}

        {isDiagPending ? (
          <div className={styles.nowDiagActionBlock}>
            <button
              type="button"
              className={styles.nowButton}
              aria-label="Пройти замер"
              title="Пройти замер"
              onClick={diagnostic.onStart}
            >
              <Icon name="play" size={26} />
            </button>
            <span className={styles.nowActionHint}>Пройти замер — это пара минут</span>
            <button
              type="button"
              className={styles.nowSkipBtn}
              onClick={diagnostic.onSkip}
              title="Первый сет соберётся по профилю, замер останется доступен"
            >
              Позже — начать с сета по профилю
            </button>
          </div>
        ) : (
          proposed && (
            <button type="button" className={styles.nowButton} aria-label="Начать" title="Начать" onClick={() => onAccept(proposed.id)}>
              <Icon name="play" size={26} />
            </button>
          )
        )}
      </section>
    </div>
  );
}

/**
 * §4.1 — the report on a passed set: short and encouraging, what was closed, what grew, which traps are
 * fixed and whether the tests are still in reach. The words come from the model's data only; the backend
 * will write them. Below it the next set is offered as before — take it or pick another in «Маршрут».
 */
function SetReportCard({ report, pace, onChoose }: { report: SetReport; pace?: Pace; onChoose: () => void }) {
  const names = (ids: string[]) => ids.map((id) => skillById(id).name).join(", ");
  return (
    <section className={styles.setReport} aria-label={`Итог сета ${report.set.number}`}>
      <p className={styles.eyebrow}>
        <Icon name="circle-check" size={14} /> Сет {report.set.number} пройден · {report.set.title}
      </p>
      <ul className={styles.setReportList}>
        {report.closed.length > 0 && (
          <li>
            <b>Закрыто:</b> {names(report.closed)}
          </li>
        )}
        {report.stronger.length > 0 && (
          <li>
            <b>Стало увереннее:</b>{" "}
            {report.stronger.map((s) => `${skillById(s.id).name} (${STATE_LABEL[s.from]} → ${STATE_LABEL[s.to]})`).join(", ")}
          </li>
        )}
        {report.fixed.length > 0 && (
          <li>
            <b>Исправлено:</b> {report.fixed.map((f) => f.text.charAt(0).toLowerCase() + f.text.slice(1)).join("; ")}
          </li>
        )}
        {pace && (
          <li data-pace={pace.level}>
            <b>К тесту:</b> {pace.verdict.toLowerCase()} — {pace.summary.charAt(0).toLowerCase() + pace.summary.slice(1)}
          </li>
        )}
      </ul>
      <button type="button" className={styles.link} onClick={onChoose}>
        Выбрать другой сет →
      </button>
    </section>
  );
}

/** The few things worth knowing before anything else: the root of the errors, confirmed traps, what is late, the next date. */
function Important({
  model,
  milestoneList,
  conflicts,
  onGo,
  onOpenSet,
  onModel,
  onToast,
}: {
  model: PrepModel;
  milestoneList: Milestone[];
  conflicts?: BackendConflict[];
  onGo: (tab: PrepTab, sub?: PrepSub, exam?: ExamId) => void;
  onOpenSet: (setId: string, topic?: string) => void;
  onModel?: (model: PrepModel) => void;
  onToast?: (text: string) => void;
}) {
  const getSetsFromCache = (examId: ExamId): StudySet[] => {
    const data = getCachedRemoteSets(examId);
    if (!data) return [];
    return [
      ...(data.current ? [data.current] : []),
      ...data.upcoming,
      ...data.done,
    ];
  };
  const remoteSets = [
    ...getSetsFromCache("sat"),
    ...getSetsFromCache("ent"),
  ];
  // With the backend behind us the plan is the backend's alone: a demo set among the real ones would
  // send the student to a topic the server has never heard of.
  const allAvailableSets = REMOTE_PREP ? remoteSets : [...SETS, ...remoteSets];
  const setWith = (skillId: string) =>
    allAvailableSets.find((s) => s.id === model.currentSet && s.skills.includes(skillId)) ??
    allAvailableSets.find((s) => s.skills.includes(skillId) && !model.doneSets.includes(s.id)) ??
    allAvailableSets.find((s) => s.skills.includes(skillId));
  const items: {
    key: string;
    tone: "root" | "trap" | "late" | "date";
    title: string;
    text: string;
    go: () => void;
    action: string;
    onDispute?: () => void;
  }[] = [];

  if (conflicts && conflicts.length) {
    for (const c of conflicts) {
      items.push({
        key: `conflict-${c.kind}-${c.milestone_keys.join("-")}`,
        tone: "late",
        title: c.text,
        text: c.options.join(" · "),
        go: () => onGo("overview", "requirements"),
        action: "Сроки",
      });
    }
  }

  const skills = allSkills();
  for (const root of skills.filter((s) => s.root && model.states[s.id] !== "solid")) {
    const above = skills.filter((s) => s.requires.includes(root.id)).map((s) => s.name.toLowerCase());
    items.push({
      key: `root-${root.id}`,
      tone: "root",
      title: `Корень: ${root.name}`,
      text: above.length ? `из-за него ошибки в теме «${above.join("», «")}»` : "из-за него ошибки выше по карте",
      go: () => onGo("sets", "map", root.exam),
      action: "На карте",
    });
  }

  for (const skill of skills) {
    const traps = (model.misconceptions[skill.id] ?? []).filter((m) => m.status === "confirmed");
    for (const trap of traps) {
      items.push({
        key: `trap-${skill.id}-${trap.id}`,
        tone: "trap",
        title: `Ловушка: ${skill.name}`,
        text: trap.text,
        go: () => {
          const set = setWith(skill.id);
          if (set) onOpenSet(set.id, skill.id);
        },
        action: "Проверить",
        onDispute: async () => {
          if (onModel) {
            onModel(disputeMisconception(model, skill.id, trap.id));
          }
          if (REMOTE_PREP) {
            await disputeRemoteMisconception(trap.id, true, skill.exam);
          }
          onToast?.("Ловушка оспорена");
        },
      });
    }
  }

  const delay = routeDelay(model);
  if (delay) {
    items.push({
      key: "late",
      tone: "late",
      title: `Сет ${delay.set.number} ещё открыт`,
      text: `срок был ${formatDate(delay.set.deadline)} — прогноз сдвинулся на ${delay.days} дн.`,
      go: () => onOpenSet(delay.set.id),
      action: "Продолжить",
    });
  }

  const next = milestoneList.find((m) => m.date >= TODAY && !model.milestonesDone.includes(m.key || m.id));
  if (next) {
    const left = daysBetween(TODAY, next.date);
    items.push({
      key: `date-${next.key || next.id}`,
      tone: "date",
      title: next.title,
      text: left === 0 ? "сегодня" : `через ${left} дн. · ${formatDate(next.date)}`,
      go: () => onGo("overview", "requirements"),
      action: "Требования",
    });
  }

  return (
    <section className={`${styles.canvas} ${styles.full}`} aria-label="Важно сейчас">
      <header className={styles.canvasHead}>
        <h3>Важно сейчас</h3>
      </header>
      {items.length === 0 ? (
        <p className={styles.muted}>Корней ошибок и подтверждённых ловушек нет, сроки в порядке.</p>
      ) : (
        <ul className={styles.important}>
          {items.map((item) => (
            <li key={item.key} data-tone={item.tone}>
              <div>
                <strong>{item.title}</strong>
                <span className={styles.muted}>{item.text}</span>
              </div>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                {item.onDispute && (
                  <button
                    type="button"
                    className={styles.link}
                    style={{ opacity: 0.7 }}
                    onClick={item.onDispute}
                    title="Оспорить вывод ассистента о наличии этой ловушки"
                  >
                    Не согласен
                  </button>
                )}
                <button type="button" className={styles.link} onClick={item.go}>
                  {item.action} →
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Milestones and deadlines interactive checklist */
function MilestonesSection({
  milestones,
  doneKeys,
  onToggle,
}: {
  milestones: Milestone[];
  doneKeys: string[];
  onToggle: (m: Milestone, done: boolean) => void;
}) {
  if (!milestones.length) return null;

  return (
    <section className={`${styles.canvas} ${styles.full}`} aria-label="Майлстоуны и дедлайны">
      <header className={styles.canvasHead}>
        <h3>Майлстоуны и дедлайны</h3>
        <span className={styles.muted}>
          {milestones.filter((m) => doneKeys.includes(m.key || m.id) || m.done).length} из {milestones.length}
        </span>
      </header>
      <ul className={styles.important}>
        {milestones.map((m) => {
          const key = m.key || m.id;
          const isDone = doneKeys.includes(key) || Boolean(m.done);
          return (
            <li key={key} data-tone={isDone ? "root" : "date"}>
              <div>
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    checked={isDone}
                    onChange={(e) => onToggle(m, e.target.checked)}
                  />
                  <strong style={{ textDecoration: isDone ? "line-through" : "none", opacity: isDone ? 0.7 : 1 }}>
                    {m.title}
                  </strong>
                </label>
                <span className={styles.muted}>
                  {formatDate(m.date)} {m.detail ? `· ${m.detail}` : ""} {m.source ? `· ${m.source}` : ""}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ---------- Требования: the exams the saved programs ask for ---------- */

/**
 * The target is the programs' highest bar until the student sets their own (§4.1). A target under the bar is
 * allowed — the student decides — but it says which program it leaves out of reach.
 */
function TargetEditor({
  exam,
  requirement,
  onTarget,
}: {
  exam: DatedExam;
  requirement: ReturnType<typeof requirements>[number];
  onTarget: (exam: DatedExam, value: number | null) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const range = TARGET_RANGE[exam];
  const bar = requirement.programTarget;
  const current = Number(requirement.target);

  const save = () => {
    if (draft === null) return;
    const n = Math.round(Number(draft.replace(",", ".")) / range.step) * range.step;
    setDraft(null);
    if (!Number.isFinite(n) || draft.trim() === "") return;
    const value = Math.min(range.max, Math.max(range.min, n));
    if (value !== current) onTarget(exam, value === bar ? null : value);
  };

  const below = bar !== undefined && current < bar;
  const owner = requirement.programs.find((p) => p.satMin && Math.round(p.satMin / 2 / 10) * 10 === bar);

  return (
    <div className={styles.examTarget}>
      {draft !== null ? (
        <input
          autoFocus
          className={styles.targetInput}
          inputMode="numeric"
          aria-label={`Цель ${requirement.name}`}
          value={draft}
          onFocus={(e) => e.target.select()}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") setDraft(null);
          }}
        />
      ) : (
        <button type="button" className={styles.targetValue} title="Изменить цель" onClick={() => setDraft(String(current))}>
          <span className={styles.examTargetValue}>{requirement.target}</span>
          <Icon name="pencil" size={14} />
        </button>
      )}
      <span className={styles.examTargetNote}>
        {requirement.custom ? (
          <>
            твоя цель · по программам {bar} ·{" "}
            <button type="button" className={styles.inlineLink} onClick={() => onTarget(exam, null)}>
              вернуть {bar}
            </button>
            {below && (
              <span className={styles.warn}>
                {" "}
                · ниже планки{owner ? ` ${owner.university}` : " программ"} — она может стать недостижимой
              </span>
            )}
          </>
        ) : (
          <>цель · {requirement.targetNote}</>
        )}
      </span>
    </div>
  );
}

/** What each exam asks for and whether the student makes it, in words: no readiness charts or percentages */
function Requirements({
  exams,
  loading,
  milestoneList,
  done,
  onPick,
  onTarget,
  children,
}: {
  exams: ExamRequirement[];
  loading?: boolean;
  milestoneList: Milestone[];
  done: string[];
  /** The student picks the sitting: its `dateKey` */
  onPick: (exam: DatedExam, key: string) => void;
  /** The student sets the target; null goes back to the programs' bar */
  onTarget: (exam: DatedExam, value: number | null) => void;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.canvasGrid}>
      {loading && (
        <section className={`${styles.canvas} ${styles.full}`} aria-label="Загрузка">
          <header className={styles.canvasHead}>
            <h3>Требования</h3>
          </header>
          <p className={styles.muted}>Загружаем актуальные требования к экзаменам...</p>
        </section>
      )}

      {!loading && exams.length === 0 && (
        <section className={`${styles.canvas} ${styles.full}`} aria-label="Экзамены не требуются">
          <header className={styles.canvasHead}>
            <h3>Стандартизированные экзамены не требуются</h3>
          </header>
          <p className={styles.muted}>
            Для твоих сохранённых программ сдача стандартизированных экзаменов (SAT / ЕНТ) не требуется. План подготовки строится вокруг дедлайнов подачи документов.
          </p>
        </section>
      )}

      {exams.map((exam) => {
        const margin = exam.testDate && exam.forecast ? daysBetween(exam.forecast, exam.testDate) : 0;
        const isDemo = !exam.programs.length || exam.programs.some((p: Program) => p.id.startsWith("demo-"));
        const dated = exam.id === "ielts" ? null : (exam.id as DatedExam);
        const registration = dated && exam.testDate ? milestoneList.find((m) => m.id === examMilestoneId(dated, "reg", exam.testDate!)) : undefined;
        return (
          <section key={exam.id} className={styles.canvas} aria-label={exam.name}>
            <header className={styles.canvasHead}>
              <h3>{exam.name}</h3>
              {isDemo && <span className={styles.demoTag}>демо</span>}
            </header>
            {dated ? (
              <TargetEditor exam={dated} requirement={exam} onTarget={onTarget} />
            ) : (
              <div className={styles.examTarget}>
                <span className={styles.examTargetValue}>{exam.target}</span>
                <span className={styles.examTargetNote}>цель · {exam.targetNote}</span>
              </div>
            )}
            <dl className={styles.facts}>
              <div>
                <dt>Тест</dt>
                {dated && exam.testCandidates.length > 1 ? (
                  // The student decides the date; each sitting says what it leaves by the forecast
                  <dd className={styles.testDates} role="radiogroup" aria-label={`Дата теста ${exam.name}`}>
                    {exam.testCandidates.map((d) => {
                      const chosen = exam.testDate?.getTime() === d.getTime();
                      const room = exam.forecast ? daysBetween(exam.forecast, d) : null;
                      return (
                        <button
                          key={d.getTime()}
                          type="button"
                          role="radio"
                          aria-checked={chosen}
                          className={styles.testDate}
                          data-late={room !== null && room < 0 ? "" : undefined}
                          onClick={() => !chosen && onPick(dated, dateKey(d))}
                        >
                          <b>{formatShort(d)}</b>
                          {room !== null && <span>{room >= 0 ? `запас ${room} дн.` : `не хватает ${-room} дн.`}</span>}
                        </button>
                      );
                    })}
                  </dd>
                ) : (
                  <dd>{exam.testDate ? formatDate(exam.testDate) : "—"}</dd>
                )}
              </div>
              {registration && (
                <div>
                  <dt>Регистрация</dt>
                  <dd>
                    до {formatDate(registration.date)}
                    {done.includes(registration.id) ? (
                      <span className={styles.ok}> · ✓ отмечена</span>
                    ) : (
                      <span className={styles.muted} title="Отметить можно в календаре Quack или написать в чате: «зарегистрировался на SAT»">
                        {" "}
                        · ещё не отмечена
                      </span>
                    )}
                  </dd>
                </div>
              )}
              <div>
                <dt>Нужен для</dt>
                {exam.programs.length ? (
                  <dd className={styles.chips}>
                    {exam.programs.map((p: Program) => (
                      <span key={p.id}>{p.university}</span>
                    ))}
                  </dd>
                ) : (
                  <dd>гранта в Казахстане — по нему твой план</dd>
                )}
              </div>
            </dl>
            {exam.hasModel ? (
              <div className={styles.readiness}>
                <p className={margin >= 0 ? styles.ok : styles.warn}>
                  Прогноз {formatDate(exam.forecast!)} —{" "}
                  {margin >= 0 ? `успеваешь, запас ${margin} дн.` : `на ${-margin} дн. позже теста, стоит добавить часов`}
                </p>
              </div>
            ) : (
              <p className={styles.muted}>Без модели знаний: подготовка идёт окнами до дат экзамена, прогноза нет.</p>
            )}
          </section>
        );
      })}

      {/* «Важно сейчас» below the exam targets: «Сейчас» stays a single calm screen */}
      {children}
    </div>
  );
}
