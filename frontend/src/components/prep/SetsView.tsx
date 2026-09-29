"use client";

import { PixelDuck } from "../duck/PixelDuck";
import { useLayoutEffect, useRef, useState, useEffect } from "react";
import { Icon } from "../choice/Icon";
import { EXAM_IDS, EXAMS, formatShort, setById, skillById, STATE_LABEL, type ExamId, type StudySet } from "./prepData";
import { closed, proposals, type PrepModel, type PrepSub } from "./prepModel";
import {
  REMOTE_PREP,
  fetchRemoteSets,
  getCachedRemoteSets,
  type RemoteSetsData,
} from "./remoteSets";
import { RouteView } from "./RouteView";
import { useVertical } from "./GraphCanvas";
import { StateGlyph } from "./SkillGraph";
import styles from "./prep.module.css";

type Props = {
  model: PrepModel;
  sub: PrepSub;
  /** Which exam the sets and the map show */
  exam: ExamId;
  onExam: (exam: ExamId) => void;
  /** Takes a set into work, replacing the one in work */
  onMakeCurrent: (setId: string) => void | Promise<void>;
  /** A set asked for from elsewhere: its card in «Маршрут» is shown open */
  focus: string | null;
  /** The active set opens in «Сейчас», any other in «Маршрут» */
  onOpenSet: (setId: string, topic?: string) => void;
  /** To the active set's work in «Сейчас» */
  onGoNow: () => void;
  onOpenDiagnostic?: () => void;
};

/**
 * §4.3 — sets. «Маршрут» holds the set in work and the assistant's advice; «Карта навыков» draws the
 * same thing as one line: the last passed sets lead to the one in work, and it leads to what is proposed.
 */
export function SetsView({ model, sub, exam, onExam, onMakeCurrent, focus, onOpenSet, onGoNow, onOpenDiagnostic }: Props) {
  const [remoteData, setRemoteData] = useState<RemoteSetsData | null>(() =>
    REMOTE_PREP ? getCachedRemoteSets(exam) : null
  );
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!REMOTE_PREP) return;
    const cached = getCachedRemoteSets(exam);
    if (cached) setRemoteData(cached);

    let active = true;
    setLoading(true);
    // The cached plan shows at once; the server's rebuilt one replaces it
    fetchRemoteSets(exam, true)
      .then((data) => {
        if (active) {
          setRemoteData(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch remote sets for", exam, err);
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [exam]);

  const handleMakeCurrent = async (setId: string) => {
    // The parent does the switch and, when the server refuses it, the re-read; here only the local copy follows
    await onMakeCurrent(setId);
    if (REMOTE_PREP) {
      const fresh = getCachedRemoteSets(exam);
      if (fresh) setRemoteData(fresh);
    }
  };

  const switcher = <ExamSwitch exam={exam} onExam={onExam} />;

  if (sub === "map") {
    return (
      <div className={styles.canvasGrid}>
        <section className={`${styles.canvas} ${styles.full} ${styles.setMapCard}`} aria-label="Карта навыков">
          <header className={styles.canvasHead}>
            <h3>Карта навыков · {EXAMS[exam].name}</h3>
            {switcher}
          </header>
          <SetMap
            key={exam}
            exam={exam}
            model={model}
            remoteData={remoteData}
            onOpen={onOpenSet}
            onTake={handleMakeCurrent}
          />
        </section>
      </div>
    );
  }

  // Nothing to study yet while the plan is being built: the duck from the page transitions waits with you
  const planEmpty =
    REMOTE_PREP && (!remoteData || ![remoteData.current, ...remoteData.upcoming].some((s) => s && s.skills.length));
  if (planEmpty && (loading || !remoteData)) {
    return (
      <div className={styles.setsLoading} role="status" aria-live="polite">
        {switcher}
        <PixelDuck tempo="fast" className={styles.setsLoadingDuck} />
        <p className={styles.muted}>Собираем сеты под твои ответы…</p>
      </div>
    );
  }

  return (
    <RouteView
      exam={exam}
      switcher={switcher}
      model={model}
      focus={focus}
      onGoNow={onGoNow}
      onTake={handleMakeCurrent}
      onOpenDiagnostic={onOpenDiagnostic}
      remoteData={remoteData}
      loading={loading}
    />
  );
}

/** SAT Math or ЕНТ: each has its own sets and map. */
function ExamSwitch({ exam, onExam }: { exam: ExamId; onExam: (exam: ExamId) => void }) {
  return (
    <div className={styles.segmented} role="tablist" aria-label="Экзамен">
      {EXAM_IDS.map((id) => (
        <button key={id} type="button" role="tab" aria-selected={exam === id} onClick={() => onExam(id)}>
          {EXAMS[id].name}
        </button>
      ))}
    </div>
  );
}

/* ---------- Карта навыков: passed → in work → proposed, everything said on the cards ---------- */

/** Passed sets shown before the one in work; the older ones open with «⋯» */
const HISTORY_SHOWN = 3;

type MapKind = "done" | "current" | "advised";

const KIND_TEXT: Record<MapKind, string> = { done: "пройден", current: "в работе", advised: "совет" };

function SetMap({
  exam,
  model,
  remoteData,
  onOpen,
  onTake,
}: {
  exam: ExamId;
  model: PrepModel;
  remoteData?: RemoteSetsData | null;
  onOpen: (setId: string, topic?: string) => void;
  onTake: (setId: string) => void;
}) {
  const vertical = useVertical();
  const [history, setHistory] = useState(false);

  const inExam = (id: string) => {
    const s = setById(id);
    return s && s.exam === exam ? s : undefined;
  };
  // Passed in the order they were passed, the latest last — right before the one in work
  const done = remoteData
    ? remoteData.done
    : model.doneSets.filter((id) => id !== model.currentSet).flatMap((id) => inExam(id) ?? []);
  const shownDone = history ? done : done.slice(-HISTORY_SHOWN);
  const hidden = done.length - shownDone.length;
  const current = remoteData?.current ?? (model.currentSet ? inExam(model.currentSet) ?? null : null);
  const advised = remoteData
    ? remoteData.upcoming.map((s) => ({ set: s, reasons: s.why ? [s.why] : [] }))
    : proposals(model, exam);

  const columns: { kind: MapKind; sets: StudySet[] }[] = [
    ...shownDone.map((s) => ({ kind: "done" as const, sets: [s] })),
    ...(current ? [{ kind: "current" as const, sets: [current] }] : []),
    ...(advised.length ? [{ kind: "advised" as const, sets: advised.map((r) => r.set) }] : []),
  ];
  // Each column leads to every card of the next one
  const edges = columns.slice(1).flatMap((col, i) => columns[i].sets.flatMap((a) => col.sets.map((b) => ({ from: a.id, to: b.id }))));
  const layout = columns.map((c) => c.sets.map((s) => s.id).join(",")).join("|");

  // Links are drawn between the cards where they actually are
  const boxRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef<Record<string, HTMLElement | null>>({});
  const [boxes, setBoxes] = useState<Record<string, { x: number; y: number; w: number; h: number }>>({});
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const measure = () => {
      const next: typeof boxes = {};
      for (const [id, el] of Object.entries(cardRefs.current)) {
        if (el) next[id] = { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight };
      }
      setBoxes(next);
      const all = Object.values(next);
      setSize({ w: Math.max(0, ...all.map((b) => b.x + b.w)), h: Math.max(0, ...all.map((b) => b.y + b.h)) });
    };
    measure();
    // Cards grow and shrink on their own (fonts, topic names), the box may not
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    for (const el of Object.values(cardRefs.current)) if (el) observer.observe(el);
    return () => observer.disconnect();
  }, [vertical, layout]);

  // The line ends with the set in work and the proposals: that end is in view; the opened history — its start
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    if (vertical) box.scrollTop = history ? 0 : box.scrollHeight;
    else box.scrollLeft = history ? 0 : box.scrollWidth;
  }, [vertical, layout, history]);

  const path = (from: string, to: string) => {
    const a = boxes[from];
    const b = boxes[to];
    if (!a || !b) return "";
    if (vertical) {
      const [x1, y1, x2, y2] = [a.x + a.w / 2, a.y + a.h, b.x + b.w / 2, b.y];
      const r = Math.max(24, (y2 - y1) / 2);
      return `M${x1},${y1} C${x1},${y1 + r} ${x2},${y2 - r} ${x2},${y2}`;
    }
    const [x1, y1, x2, y2] = [a.x + a.w, a.y + a.h / 2, b.x, b.y + b.h / 2];
    const r = Math.max(30, (x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + r},${y1} ${x2 - r},${y2} ${x2},${y2}`;
  };

  if (!columns.length) return <p className={styles.muted}>По {EXAMS[exam].name} пока нет ни пройденных, ни предложенных сетов.</p>;

  return (
    <div className={styles.setMapBody}>
      <div className={styles.setMapScroll} ref={boxRef}>
        <svg className={styles.graphEdges} width={size.w} height={size.h} aria-hidden="true">
          {edges.map((e) => (
            <path
              key={`${e.from}-${e.to}`}
              d={path(e.from, e.to)}
              className={styles.edge}
              data-active={e.from === current?.id || e.to === current?.id ? "" : undefined}
            />
          ))}
        </svg>

        {done.length > HISTORY_SHOWN && (
          <button
            type="button"
            className={styles.setMapMore}
            aria-expanded={history}
            title={history ? "Свернуть историю" : `Показать всю историю · ещё ${hidden}`}
            aria-label={history ? "Свернуть историю" : `Показать ещё ${hidden} пройденных сетов`}
            onClick={() => setHistory((h) => !h)}
          >
            {history ? <Icon name="arrow-left" size={16} /> : "⋯"}
          </button>
        )}

        {columns.map((column) => (
          <div key={column.sets.map((s) => s.id).join(",")} className={styles.setMapColumn} data-kind={column.kind}>
            {column.sets.map((s, i) => (
              <MapCard
                key={s.id}
                refEl={(el) => {
                  cardRefs.current[s.id] = el;
                }}
                set={s}
                model={model}
                kind={column.kind}
                rank={column.kind === "advised" ? i + 1 : undefined}
                reason={column.kind === "advised" ? advised[i].reasons[0] : undefined}
                onOpen={onOpen}
                onTake={onTake}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * One set, read without a legend: what it is, where it stands, each topic with its state in words,
 * roots and traps named on the topic, and the one thing to do with the set.
 */
function MapCard({
  refEl,
  set,
  model,
  kind,
  rank,
  reason,
  onOpen,
  onTake,
}: {
  refEl: (el: HTMLElement | null) => void;
  set: StudySet;
  model: PrepModel;
  kind: MapKind;
  rank?: number;
  reason?: string;
  onOpen: (setId: string, topic?: string) => void;
  onTake: (setId: string) => void;
}) {
  return (
    <article ref={refEl} className={styles.setMapNode} data-kind={kind} aria-label={`Сет ${set.number} · ${set.title}, ${KIND_TEXT[kind]}`}>
      <header className={styles.setMapNodeHead}>
        <span>
          Сет {set.number} · {set.area}
        </span>
        <span className={styles.setMapKind} data-kind={kind}>
          {kind === "advised" ? `совет ${rank}` : KIND_TEXT[kind]}
        </span>
      </header>

      <h4 className={styles.setMapNodeTitle}>{set.title}</h4>

      <p className={styles.setMapNodeMeta}>
        закрыто {closed(model, set)} из {set.skills.length} · {kind === "done" ? "дедлайн был" : "до"} {formatShort(set.deadline)}
      </p>

      {reason && <p className={styles.setMapReason}>{reason}</p>}

      <ul className={styles.setMapTopics}>
        {set.skills.map((id) => {
          const skill = skillById(id);
          const state = model.states[id];
          const trap = (model.misconceptions[id] ?? []).some((m) => m.status === "confirmed");
          return (
            <li key={id}>
              <button type="button" onClick={() => onOpen(set.id, id)} title="Открыть тему">
                <StateGlyph state={state} size={12} />
                <span className={styles.setMapTopicName}>
                  {skill.name}
                  {skill.root && state !== "solid" && <b className={styles.rootTag}>корень ошибок</b>}
                  {trap && <b className={styles.trapTag}>ловушка</b>}
                </span>
                <span className={styles.setMapTopicState} data-state={state}>
                  {STATE_LABEL[state].toLowerCase()}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <footer className={styles.setMapNodeFoot}>
        {kind === "current" ? (
          <button type="button" className={styles.routeCardPrimary} onClick={() => onOpen(set.id)}>
            Заниматься <Icon name="chevron-right" size={13} />
          </button>
        ) : kind === "advised" ? (
          <button type="button" className={styles.routeCardPrimary} onClick={() => onTake(set.id)}>
            <Icon name="check" size={13} /> Сделать актуальным
          </button>
        ) : (
          <button type="button" className={styles.routeCardSelect} onClick={() => onTake(set.id)}>
            Повторить
          </button>
        )}
      </footer>
    </article>
  );
}
