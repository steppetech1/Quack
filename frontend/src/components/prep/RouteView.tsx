"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../choice/Icon";
import { allSets, daysBetween, EXAMS, formatShort, setById, skillById, STATE_LABEL, TODAY, type ExamId, type StudySet } from "./prepData";
import { closed, MAX_PROPOSED, proposals, type PrepModel } from "./prepModel";
import type { RemoteSetsData } from "./remoteSets";
import { StateGlyph } from "./SkillGraph";
import styles from "./prep.module.css";

type Props = {
  exam: ExamId;
  switcher: ReactNode;
  model: PrepModel;
  /** A set asked for from elsewhere (the map, «Важно сейчас»): its card is open and in view */
  focus: string | null;
  /** To the active set's work in «Сейчас» */
  onGoNow: () => void;
  /** Makes a set the active one; the one in work is put aside */
  onTake: (setId: string) => void;
  onOpenDiagnostic?: () => void;
  remoteData?: RemoteSetsData | null;
  loading?: boolean;
};

type Kind = "advised" | "postponed" | "done";

const trapsWord = (n: number) => (n === 1 ? "ловушка" : n < 5 ? "ловушки" : "ловушек");

const trapsOf = (model: PrepModel, set: StudySet) =>
  set.skills.reduce(
    (n, id) => n + (model.misconceptions[id]?.filter((m) => m.status === "confirmed").length ?? 0),
    0
  );

/**
 * §4.3 — Маршрут: which set is chosen and why, then what the assistant advises instead. The work on
 * the chosen set itself happens in «Сейчас»; here the student only picks it. A set swapped out goes
 * to «Отложенные» and comes back with one press, its topics keep their progress.
 */
export function RouteView({
  exam,
  switcher,
  model,
  focus,
  onGoNow,
  onTake,
  onOpenDiagnostic,
  remoteData,
  loading,
}: Props) {
  const sets = remoteData
    ? [
        ...(remoteData.current ? [remoteData.current] : []),
        ...remoteData.upcoming,
        ...remoteData.done,
      ]
    : allSets().filter((s) => s.exam === exam);

  const current = remoteData
    ? remoteData.current ?? (model.currentSet ? setById(model.currentSet) : null)
    : model.currentSet ? setById(model.currentSet) : null;

  const postponedIds = model.postponed ?? [];
  const advised = remoteData
    ? remoteData.upcoming.map((s) => ({
        set: s,
        reasons: s.why ? [s.why] : [s.title],
      }))
    : proposals(model, exam);

  const postponed = postponedIds
    .map(setById)
    .filter((s) => s && s.exam === exam && !model.doneSets.includes(s.id) && s.id !== current?.id);

  const done = remoteData
    ? remoteData.done
    : sets.filter((s) => model.doneSets.includes(s.id) && s.id !== model.currentSet);

  return (
    <div className={styles.setList}>
      <header className={styles.setListHead}>
        <div>
          <h3>Маршрут подготовки</h3>
          <p className={styles.muted}>
            В работе ровно один сет · пройдено {done.length} из {sets.length} по {EXAMS[exam].name}
            {onOpenDiagnostic && model.diagnosticDone && (
              <>
                {" "}·{" "}
                <button type="button" className={styles.inlineLink} onClick={onOpenDiagnostic}>
                  пересдать входной замер
                </button>
              </>
            )}
          </p>
        </div>
      </header>

      {current ? (
        <CurrentSet set={current} model={model} onGoNow={onGoNow} />
      ) : (
        <section className={styles.routeCurrent} data-empty>
          <p className={styles.routeCurrentLabel}>Сейчас в работе</p>
          <p className={styles.muted}>Актуального сета нет — выбери один из советов ниже.</p>
        </section>
      )}

      <div className={styles.routeSectionHead}>
        <div>
          <h4>Советы ассистента</h4>
          <p className={styles.muted}>
            Не больше {MAX_PROPOSED} за раз, по твоим ошибкам и ловушкам — первым то, что даст больше всего. Не нравится текущий сет — сделай актуальным другой.
          </p>
        </div>
        {switcher}
      </div>

      {advised.length ? (
        <div className={styles.routeGrid} role="list" aria-label="Советы ассистента">
          {advised.map((r, i) => (
            <RouteCard
              key={r.set.id}
              set={r.set}
              model={model}
              kind="advised"
              rank={i + 1}
              reasons={r.reasons}
              focused={focus === r.set.id}
              onTake={onTake}
            />
          ))}
        </div>
      ) : (
        <p className={styles.muted}>По {EXAMS[exam].name} больше нечего советовать: остальные сеты пройдены или отложены.</p>
      )}

      {postponed.length > 0 && (
        <>
          <div className={styles.routeSectionHead}>
            <div>
              <h4>Отложенные</h4>
              <p className={styles.muted}>Сеты, которые ты сменил на другие. Прогресс по их темам сохранён.</p>
            </div>
          </div>
          <div className={styles.routeGrid} role="list" aria-label="Отложенные сеты">
            {postponed.map((set) => (
              <RouteCard key={set.id} set={set} model={model} kind="postponed" focused={focus === set.id} onTake={onTake} />
            ))}
          </div>
        </>
      )}

      {done.length > 0 && (
        <details className={styles.routeDone}>
          <summary>Пройденные · {done.length}</summary>
          <div className={styles.routeGrid} role="list" aria-label="Пройденные сеты">
            {done.map((set) => (
              <RouteCard key={set.id} set={set} model={model} kind="done" focused={focus === set.id} onTake={onTake} />
            ))}
          </div>
        </details>
      )}

      <div className={styles.routeLegend}>
        <span><i data-state="solid" /> {STATE_LABEL.solid}</span>
        <span><i data-state="shaky" /> {STATE_LABEL.shaky}</span>
        <span><i data-state="weak" /> {STATE_LABEL.weak}</span>
        <span><i data-state="lowData" /> {STATE_LABEL.lowData}</span>
      </div>
    </div>
  );
}

/** The chosen set: what it is, how far along, when it is due, and the way to its work */
function CurrentSet({ set, model, onGoNow }: { set: StudySet; model: PrepModel; onGoNow: () => void }) {
  const next = set.skills.find((id) => model.states[id] !== "solid");
  const traps = trapsOf(model, set);
  const left = daysBetween(TODAY, set.deadline);

  return (
    <section className={styles.routeCurrent} aria-label="Сейчас в работе">
      <div className={styles.routeCurrentMain}>
        <p className={styles.routeCurrentLabel}>
          Сейчас в работе · {EXAMS[set.exam].name}
        </p>
        <h4 className={styles.routeCurrentTitle}>
          Сет {set.number} · {set.title}
        </h4>
        <p className={styles.routeCardArea}>{set.area}</p>

        <div className={styles.routeCardBar} aria-label={`Закрыто ${closed(model, set)} из ${set.skills.length} тем`}>
          {set.skills.map((id) => (
            <i key={id} data-state={model.states[id]} title={`${skillById(id).name} — ${STATE_LABEL[model.states[id]].toLowerCase()}`} />
          ))}
        </div>

        <dl className={styles.routeCurrentFacts}>
          <div>
            <dt>Закрыто</dt>
            <dd>
              {closed(model, set)} из {set.skills.length} тем
            </dd>
          </div>
          <div>
            <dt>Дедлайн</dt>
            <dd className={left < 0 ? styles.warn : undefined}>
              {formatShort(set.deadline)} · {left >= 0 ? `осталось ${left} дн.` : `просрочен на ${-left} дн.`}
            </dd>
          </div>
          {next && (
            <div>
              <dt>Следующая тема</dt>
              <dd className={styles.routeCurrentNext}>
                <StateGlyph state={model.states[next]} size={13} />
                {skillById(next).name}
              </dd>
            </div>
          )}
          {traps > 0 && (
            <div>
              <dt>Ловушки</dt>
              <dd className={styles.routeCardTraps}>
                {traps} {trapsWord(traps)}
              </dd>
            </div>
          )}
        </dl>
      </div>

      <button type="button" className={styles.primary} onClick={onGoNow}>
        Заниматься <Icon name="chevron-right" size={16} />
      </button>
    </section>
  );
}

type CardProps = {
  set: StudySet;
  model: PrepModel;
  kind: Kind;
  /** Place among the assistant's advice, 1 is the strongest */
  rank?: number;
  reasons?: string[];
  focused: boolean;
  onTake: (setId: string) => void;
};

const KIND_LABEL: Record<Kind, string> = { advised: "совет", postponed: "отложен", done: "пройден" };
const TAKE_LABEL: Record<Kind, string> = { advised: "Сделать актуальным", postponed: "Вернуть в работу", done: "Повторить" };

function RouteCard({ set, model, kind, rank, reasons, focused, onTake }: CardProps) {
  const [open, setOpen] = useState(focused);
  const ref = useRef<HTMLElement>(null);
  const traps = kind === "done" ? 0 : trapsOf(model, set);

  // Asked for from the map or «Важно сейчас»: open and brought into view
  useEffect(() => {
    if (!focused) return;
    setOpen(true);
    ref.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focused]);

  return (
    <article ref={ref} className={styles.routeCard} data-kind={kind} data-focus={focused || undefined} role="listitem">
      <div className={styles.routeCardTop}>
        <span className={styles.routeCardNumber}>Сет {String(set.number).padStart(2, "0")}</span>
        <span className={styles.routeCardStatus}>
          {kind === "advised" && rank === 1 ? "совет ассистента" : KIND_LABEL[kind]}
        </span>
      </div>

      <h4 className={styles.routeCardTitle}>{set.title}</h4>
      <p className={styles.routeCardArea}>{set.area}</p>

      {reasons && reasons.length > 0 && (
        <ul className={styles.routeReasons}>
          {reasons.slice(0, open ? reasons.length : 2).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      {open && (
        <ul className={styles.routeTopics} aria-label="Темы сета">
          {set.skills.map((id) => (
            <li key={id}>
              <StateGlyph state={model.states[id]} size={13} />
              <span>{skillById(id).name}</span>
              <span className={styles.muted}>{STATE_LABEL[model.states[id]].toLowerCase()}</span>
            </li>
          ))}
        </ul>
      )}

      <div className={styles.routeCardBar} aria-label={`Закрыто ${closed(model, set)} из ${set.skills.length} тем`}>
        {set.skills.map((id) => (
          <i key={id} data-state={model.states[id]} title={`${skillById(id).name} — ${STATE_LABEL[model.states[id]].toLowerCase()}`} />
        ))}
      </div>

      <div className={styles.routeCardMeta}>
        <span>
          {closed(model, set)}/{set.skills.length} закрыто · до {formatShort(set.deadline)}
        </span>
        {traps > 0 && (
          <span className={styles.routeCardTraps}>
            {traps} {trapsWord(traps)}
          </span>
        )}
      </div>

      <div className={styles.routeCardActions}>
        <button type="button" className={kind === "done" ? styles.routeCardSelect : styles.routeCardPrimary} onClick={() => onTake(set.id)}>
          <Icon name="check" size={13} />
          {TAKE_LABEL[kind]}
        </button>
        <button type="button" className={styles.routeCardGhost} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Свернуть" : "Подробнее"}
        </button>
      </div>
    </article>
  );
}
