"use client";

// Обзор — the screen behind the Quack! button and the first thing a student sees.
// The saved programs are its input; the work is split across GitHub-style tabs so the screen
// says where to look: the main numbers and activity, the merged exams, the calendar and the programs.

import { FirstHint } from "@/components/hints/FirstHint";
import { useEffect, useMemo, useState } from "react";
import type { Profile } from "../choice/assistant";
import { Icon } from "../choice/Icon";
import { programById } from "../choice/programs";
import { readiness, type PrepTab } from "../prep/prepModel";
import { loadPrepModel } from "../prep/prepStore";
import { REMOTE_PREP } from "../prep/remoteFlag";
import type { AdviceAction, Signal } from "../quack/contract";
import { useQuack } from "../quack/source";
import { forecastScore, markableId } from "../quack/standing";
import {
  chosenTestDates,
  dismissAdvice,
  markMilestone,
  pickTestDate,
  resolveConflict,
  useChosenTargets,
  useChosenTestDates,
  useDismissedAdvice,
  useResolvedConflicts,
  useDoneMilestones,
} from "../prep/milestoneMarks";
import { ActivityGrid } from "./ActivityGrid";
import { CalendarTab } from "./CalendarTab";
import { ChancesCard } from "./ChancesCard";
import { ChangesFeed } from "./ChangesFeed";
import {
  activityByDay,
  calendarEvents,
  DASH_TABS,
  removalEffects,
  unionExams,
  watchList,
  type DashTab,
} from "./dashboardRules";
import { ExamsTab } from "./ExamsTab";
import { ProgramsTab } from "./ProgramsTab";
import styles from "./dashboard.module.css";
import { store } from "../account/store";

const WATCH_KEY = "quack-dashboard-watch";

type Props = {
  /** The open category; the left column switches it too */
  tab: DashTab;
  onTab: (tab: DashTab) => void;
  saved: string[];
  profile: Profile;
  /** Chat history, used for the activity grid */
  chatDays: number[];
  onUnsave: (id: string) => void;
  onOpenChoice: () => void;
  onOpenPrep: (tab: PrepTab) => void;
};

export function Dashboard({ tab, onTab, saved, profile, chatDays, onUnsave, onOpenChoice, onOpenPrep }: Props) {
  const [watched, setWatched] = useState<string[]>([]);
  const { state: quack, decide } = useQuack();
  const doneMilestones = useDoneMilestones();
  const testDates = useChosenTestDates();
  const targets = useChosenTargets();
  const dismissed = useDismissedAdvice();
  const resolved = useResolvedConflicts();

  /** Taking a piece of advice or a way out of a conflict: the plan changes as if the student did it by hand */
  const act = (action: AdviceAction) => {
    if (action.kind === "pick-date") pickTestDate(action.exam, action.key);
    else onOpenPrep("overview");
  };

  // Fresh signals turn into history a moment after Quack opens; for this visit they still read as new
  const [visitNew, setVisitNew] = useState<Set<string>>(() => new Set());
  const keyOf = (s: Signal) => `${s.id}@${s.at}`;
  const freshKey = quack.fresh.map(keyOf).join("|");
  useEffect(() => {
    if (freshKey) setVisitNew((seen) => new Set([...seen, ...freshKey.split("|")]));
  }, [freshKey]);

  useEffect(() => setWatched(store.get<string[]>(WATCH_KEY) ?? []), []);

  const keepWatching = (id: string) => {
    const next = watched.includes(id) ? watched : [...watched, id];
    setWatched(next);
    store.set(WATCH_KEY, next);
  };

  // Preparation is a separate screen with its own storage; here we only read it. At `remote` that
  // storage holds no domain any more, so readiness and activity come from the standing instead.
  const prep = useMemo(() => loadPrepModel(), []);

  const programs = saved.map(programById).filter(Boolean);
  const exams = unionExams(programs);
  const events = calendarEvents(programs, exams, testDates);
  // The calendar also lists the other sittings, so a date can be picked right there
  const calendarDays = calendarEvents(programs, exams, testDates, true);
  const predicted = forecastScore(REMOTE_PREP ? (quack.standing?.readiness ?? 0) : readiness(prep));
  const localActivity = useMemo(
    () => activityByDay(Object.values(prep.evidence).flat(), chatDays),
    [prep, chatDays]
  );
  // The server counts activity across devices; the browser only knows what happened in it
  const activity = useMemo(
    () =>
      quack.activity
        ? quack.activity.map((d) => ({ date: new Date(`${d.day}T00:00:00`), count: d.count, level: d.level, parts: d.parts }))
        : localActivity,
    [quack.activity, localActivity]
  );

  if (!programs.length) {
    return (
      <div className={styles.dashboard}>
        <div className={styles.empty}>
          <h2>Здесь соберётся твой план</h2>
          <p>
            Сохрани программы в «Выборе» — и обзор сам сведёт их требования в один список экзаменов, проверит даты на конфликты и
            наполнит «Подготовку». Нажимать «сформировать план» не нужно.
          </p>
          <button type="button" className={styles.primary} onClick={onOpenChoice}>
            Перейти к выбору
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.dashboard}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Обзор</h2>
          <p className={styles.muted}>Всё, что следует из твоих сохранённых программ</p>
        </div>
        <button type="button" className={styles.secondary} onClick={onOpenChoice}>
          <Icon name="graduation-cap" size={16} /> Добавить программы
        </button>
      </header>

      <FirstHint id="dashboard" title="Зачем «Обзор»">
        Quack следит за сохранёнными программами: какие экзамены сдавать, какие сроки близко и как меняются шансы. Когда что-то
        меняется, кнопка Quack! наверху начинает светиться. Заходи сюда, чтобы узнать, что случилось.
      </FirstHint>

      <nav className={styles.tabs} role="tablist" aria-label="Разделы обзора">
        {DASH_TABS.map((t) => (
          <button key={t.tab} type="button" role="tab" aria-selected={tab === t.tab} onClick={() => onTab(t.tab)}>
            <Icon name={t.icon} size={16} />
            {t.label}
            {t.tab === "exams" && <span className={styles.counter}>{exams.length}</span>}
            {t.tab === "calendar" && <span className={styles.counter}>{events.length}</span>}
            {t.tab === "programs" && <span className={styles.counter}>{programs.length}</span>}
          </button>
        ))}
      </nav>

      <div key={tab} className={styles.tabBody}>
        {tab === "overview" && (
          <div className={styles.grid}>
            {quack.standing && (
              <ChancesCard
                standing={quack.standing}
                fresh={quack.fresh}
                onOpenPrep={() => onOpenPrep("overview")}
                onOpenCalendar={() => onTab("calendar")}
                onOpenPrograms={() => onTab("programs")}
                onMark={(id) => markMilestone(id, true)}
                decisions={{ dismissed, onAct: act, onDismiss: dismissAdvice }}
              />
            )}
            <ChangesFeed
              fresh={quack.fresh}
              history={quack.history}
              isNew={(s) => visitNew.has(keyOf(s))}
              onTarget={(target) => (target === "prep" ? onOpenPrep("overview") : target === "calendar" ? onTab("calendar") : onTab("programs"))}
              done={doneMilestones}
              onMark={(id, value) => markMilestone(id, value)}
              onConflict={(id, option) => {
                // A new date settles the conflict by itself; any other way out is kept as the student's decision
                if (option.action?.kind === "pick-date") {
                  const { exam } = option.action;
                  const before = chosenTestDates()[exam] ?? null;
                  act(option.action);
                  return () => pickTestDate(exam, before);
                }
                resolveConflict(id, option.label);
                return () => resolveConflict(id, null);
              }}
              resolved={resolved}
              onUnresolve={(id) => resolveConflict(id, null)}
              onDecide={decide ? (id, decision) => (decision === "accept" ? decide.accept(id) : decide.decline(id)) : undefined}
            />
            <ActivityGrid days={activity} />
          </div>
        )}

        {tab === "exams" && (
          <>
            <FirstHint id="dashboard-exams" title="Что во «Экзаменах»">
              Требования всех сохранённых программ сведены в один список: какой экзамен сдавать, на какой балл и каким программам
              он нужен. Один экзамен часто закрывает сразу несколько программ.
            </FirstHint>
            <ExamsTab exams={exams} programCount={programs.length} targets={targets} />
          </>
        )}
        {tab === "calendar" && (
          <>
            <FirstHint id="dashboard-calendar-v3" title="Что в «Календаре»">
              Даты экзаменов и дедлайны подачи твоих программ на одной сетке. Бледные тесты — другие даты сдачи: «Сдаю в эту
              дату» перестроит план под неё. Зарегистрировался или подал документы — нажми «Отметить», и прогноз с напоминаниями
              пересчитаются.
            </FirstHint>
            <CalendarTab
              events={calendarDays}
              marks={{ idOf: markableId, done: doneMilestones, onToggle: (id) => markMilestone(id) }}
              onPickDate={pickTestDate}
            />
          </>
        )}
        {tab === "programs" && (
          <FirstHint id="dashboard-programs" title="Что в «Программах»">
            Сохранённые программы и то, что изменится, если убрать одну из них: какие экзамены и сроки уйдут. Прогноз из подготовки
            показывает, хватает ли балла.
          </FirstHint>
        )}
        {tab === "programs" && (
          <ProgramsTab
            effects={removalEffects(programs, exams)}
            watch={watchList(programs, profile)}
            profile={profile}
            predicted={predicted}
            watched={watched}
            onWatch={keepWatching}
            onUnsave={onUnsave}
          />
        )}
      </div>
    </div>
  );
}
