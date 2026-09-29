"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { Icon } from "../choice/Icon";
import { daysBetween, EXAMS, formatDate, formatShort, SET_STATUS_LABEL, skillById, STATE_LABEL, TODAY, type StudySet } from "./prepData";
import { closed, setStatus, type PrepModel } from "./prepModel";
import { useVertical } from "./GraphCanvas";
import { NodeMark, StateGlyph } from "./SkillGraph";
import { TopicWorkspace } from "./TopicWorkspace";
import { FinalMockTest } from "./FinalMockTest";
import { SetReport } from "./SetReport";
import styles from "./prep.module.css";

type Props = {
  model: PrepModel;
  set: StudySet;
  /** A topic to open straight away, e.g. from «Важно сейчас» */
  topic?: string;
  /** Absent in «Сейчас»: there the set is the whole screen, nothing to go back to */
  onBack?: () => void;
  onMakeCurrent: (setId: string) => void;
  onModel: (model: PrepModel) => void;
  onToast: (text: string) => void;
};

const STATUS_TEXT = { ...SET_STATUS_LABEL, proposed: "предложен" };

/**
 * One set opened: the screen belongs to its topics, laid out as a graph on the set's own timeline, each
 * with its own deadline. A topic opens in place of the graph: a chat with the assistant and a mock test.
 */
export function SetDetail({ model, set, topic, onBack, onMakeCurrent, onModel, onToast }: Props) {
  const order = topicOrder(set);
  const plan = plannedDates(set, order);
  // A topic asked for from outside (e.g. a trap in «Важно сейчас») opens straight away
  const [open, setOpen] = useState<string | null>(topic && set.skills.includes(topic) ? topic : null);
  const [finalMockOpen, setFinalMockOpen] = useState(false);
  const next = order.find((id) => model.states[id] !== "solid");
  const allClosed = closed(model, set) === set.skills.length;

  const status = setStatus(model, set);
  const left = daysBetween(TODAY, set.deadline);
  const toStart = daysBetween(TODAY, set.start);
  const when =
    status === "done"
      ? `пройден · дедлайн был ${formatDate(set.deadline)}`
      : toStart > 0
        ? `старт через ${toStart} дн. · до ${formatDate(set.deadline)}`
        : left >= 0
          ? `до ${formatDate(set.deadline)} · осталось ${left} дн.`
          : `дедлайн был ${formatDate(set.deadline)} — прогноз уже пересчитан`;

  if (open) {
    return (
      <TopicWorkspace
        key={open}
        model={model}
        set={set}
        skillId={open}
        order={order}
        plannedBy={plan[open]}
        onBack={() => setOpen(null)}
        onTopic={setOpen}
        onModel={onModel}
        onToast={onToast}
      />
    );
  }

  if (finalMockOpen) {
    return <FinalMockTest set={set} onToast={onToast} onBack={() => setFinalMockOpen(false)} />;
  }

  return (
    <div className={styles.setDetail}>
      <header className={styles.setBar}>
        {onBack && (
          <button type="button" className={styles.backLink} onClick={onBack} aria-label="Все сеты" title="Все сеты">
            <Icon name="arrow-left" size={18} />
          </button>
        )}
        <div className={styles.setBarTitle}>
          <h2>
            Сет {set.number} · {set.title}
          </h2>
          <span className={styles.muted}>
            {EXAMS[set.exam].name} · {set.area}
          </span>
        </div>
        <div className={styles.setBarSide}>
          <span className={styles.setProgress} title="Сколько тем уже держится">
            {closed(model, set)} из {set.skills.length}
            <span className={styles.segments}>
              {order.map((id) => {
                const st = model.states[id] ?? "weak";
                return (
                  <span key={id} data-state={st} title={`${skillById(id).name}: ${STATE_LABEL[st]}`} />
                );
              })}
            </span>
          </span>
          <span className={left < 0 && status !== "done" ? styles.warn : styles.muted}>{when}</span>
          <span className={styles.statusPill} data-status={status}>
            {STATUS_TEXT[status]}
          </span>
          {status !== "current" && status !== "done" && (
            <button type="button" className={styles.secondary} onClick={() => onMakeCurrent(set.id)}>
              <Icon name="target" size={16} /> Взять в работу
            </button>
          )}
        </div>
      </header>

      {/* Сет пройден — первым делом отчёт о нём, а не снова его граф */}
      {status === "done" && <SetReport setId={set.rawId} />}

      <div className={styles.setStage}>
        <section className={`${styles.canvas} ${styles.setGraphCard}`} aria-label="Темы сета">
          <SetGraph
            model={model}
            set={set}
            order={order}
            plan={plan}
            next={next}
            onSelect={setOpen}
            allClosed={allClosed}
            onFinalMock={() => setFinalMockOpen(true)}
          />
        </section>
      </div>
    </div>
  );
}

/* ---------- Order and dates ---------- */

/** Topics in the order they build on each other inside the set; ties keep the set's own order */
function topicOrder(set: StudySet): string[] {
  if (set.topics && set.topics.length > 0) {
    return [...set.topics].sort((a, b) => a.position - b.position).map((t) => t.skill_id);
  }
  const depth = (id: string, seen: string[] = []): number => {
    const inside = skillById(id).requires.filter((r) => set.skills.includes(r) && !seen.includes(r));
    return inside.length ? 1 + Math.max(...inside.map((r) => depth(r, [...seen, id]))) : 0;
  };
  return [...set.skills].sort((a, b) => depth(a) - depth(b) || set.skills.indexOf(a) - set.skills.indexOf(b));
}

/** Each topic gets an even share of the set's window; its own deadline is the end of that share */
function plannedDates(set: StudySet, order: string[]): Record<string, Date> {
  const from = set.start.getTime();
  const span = set.deadline.getTime() - from;
  return Object.fromEntries(order.map((id, i) => [id, new Date(from + (span * (i + 1)) / order.length)]));
}

/** Where a topic's own deadline stands for the student today */
type Due = "done" | "late" | "now" | "later";

function dueOf(state: string, from: Date, by: Date): Due {
  if (state === "solid") return "done";
  if (by < TODAY) return "late";
  return from <= TODAY ? "now" : "later";
}

const DUE_TEXT: Record<Due, (by: Date) => string> = {
  done: () => "держится",
  late: (by) => `просрочена на ${daysBetween(by, TODAY)} дн.`,
  now: (by) => `осталось ${daysBetween(TODAY, by)} дн.`,
  later: (by) => `через ${daysBetween(TODAY, by)} дн.`,
};

/* ---------- The graph on the set's timeline ---------- */

/** The circle matches the map; the timeline gives the topics their room */
const NODE = 64;
const NODE_W = 156;
/** Room left and right of the axis for the start and the deadline */
const PAD = 104;
/** Least room one topic takes along the axis; a long set scrolls sideways */
const STEP = 168;
const MIN_H = 380;
/** From the bottom: the axis, then the topic deadlines under it */
const AXIS_FROM_BOTTOM = 70;
const TOP = 60;
const GAP = 3;

/** Phones: time runs down the left edge and the topics stack under each other */
const V_AXIS = 30;
const V_TOP = 56;
const V_STEP = 160;

function SetGraph({
  model,
  set,
  order,
  plan,
  next,
  onSelect,
  allClosed,
  onFinalMock,
}: {
  model: PrepModel;
  set: StudySet;
  order: string[];
  plan: Record<string, Date>;
  /** The first topic that does not hold yet: where to start */
  next?: string;
  onSelect: (id: string) => void;
  /** Every topic of the set holds — the final mock unlocks (§4.4) */
  allClosed: boolean;
  onFinalMock: () => void;
}) {
  const vertical = useVertical();
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 900, h: 480 });

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // A set can start and end on the same day (a short set, or dates the server has not spread yet):
  // keep a one-day window so positions never divide by zero, and fall back to today for a bad date
  const valid = (d: Date) => (Number.isFinite(d?.getTime?.()) ? d.getTime() : TODAY.getTime());
  const from = valid(set.start);
  const to = Math.max(valid(set.deadline), from + 86_400_000);
  const n = order.length;
  const starts = order.map((_, i) => new Date(from + ((to - from) * i) / n));

  const width = vertical ? box.w : Math.max(box.w, n * STEP + PAD * 2);
  const height = vertical ? V_TOP + n * V_STEP + 60 : Math.max(MIN_H, box.h);
  const axisY = height - AXIS_FROM_BOTTOM;
  const along = (f: number) => (vertical ? V_TOP + f * (height - V_TOP - 40) : PAD + f * (width - PAD * 2));
  const frac = (d: Date) => Math.min(1, Math.max(0, (d.getTime() - from) / (to - from)));

  // Topics sit over the middle of their share; neighbours step up and down so the links read
  const band = axisY - TOP - NODE - 80;
  const mid = TOP + NODE / 2 + band / 2;
  const sway = n > 1 ? Math.min(90, band / 4) : 0;
  const pos: Record<string, { x: number; y: number }> = Object.fromEntries(
    order.map((id, i) => [
      id,
      vertical
        ? { x: Math.max(V_AXIS + 120 + NODE_W / 2, width - NODE_W / 2 - 6), y: along((i + 0.5) / n) }
        : { x: along((i + 0.5) / n), y: mid + (i % 2 ? sway : -sway) },
    ])
  );

  const todayAt = along(frac(TODAY));
  const left = daysBetween(TODAY, set.deadline);
  const todayLabel =
    TODAY < set.start
      ? `старт через ${daysBetween(TODAY, set.start)} дн.`
      : left >= 0
        ? `сегодня · до конца сета ${left} дн.`
        : `просрочен на ${-left} дн.`;
  const edges = order.flatMap((id) => skillById(id).requires.filter((r) => order.includes(r)).map((r) => ({ from: r, to: id })));

  return (
    <div className={styles.setGraphScroll} ref={boxRef}>
      <div className={styles.setGraph} style={{ width, height }} data-vertical={vertical || undefined}>
        <svg className={styles.graphEdges} width={width} height={height} aria-hidden="true">
          {/* The part of the window already behind */}
          {vertical ? (
            <rect x={0} y={along(0)} width={width} height={Math.max(0, todayAt - along(0))} className={styles.setPast} />
          ) : (
            <rect x={along(0)} y={0} width={Math.max(0, todayAt - along(0))} height={axisY} className={styles.setPast} />
          )}

          {/* The set's own deadline */}
          {vertical ? (
            <line x1={0} y1={along(1)} x2={width} y2={along(1)} className={styles.setDeadlineLine} />
          ) : (
            <line x1={along(1)} y1={14} x2={along(1)} y2={axisY + 10} className={styles.setDeadlineLine} />
          )}

          {/* §4.4: the final mock, right after the set's own deadline — a dashed line until it unlocks */}
          {vertical ? (
            <line x1={0} y1={along(1) + 26} x2={width} y2={along(1) + 26} className={styles.setFinalMockLine} />
          ) : (
            <line x1={along(1) + 26} y1={14} x2={along(1) + 26} y2={axisY + 10} className={styles.setFinalMockLine} />
          )}

          {/* Every topic's share of the window, ending on its own deadline */}
          {order.map((id, i) => {
            const a = along(i / n) + (i ? GAP : 0);
            const b = along((i + 1) / n) - (i < n - 1 ? GAP : 0);
            const end = along((i + 1) / n);
            const due = dueOf(model.states[id], starts[i], plan[id]);
            return (
              <g key={`seg-${id}`} data-due={due} className={styles.setSeg}>
                {vertical ? <line x1={V_AXIS} y1={a} x2={V_AXIS} y2={b} /> : <line x1={a} y1={axisY} x2={b} y2={axisY} />}
                <circle cx={vertical ? V_AXIS : end} cy={vertical ? end : axisY} r={4.5} />
              </g>
            );
          })}

          {/* Where the student is in time */}
          {vertical ? (
            <line x1={0} y1={todayAt} x2={width} y2={todayAt} className={styles.routeNow} />
          ) : (
            <line x1={todayAt} y1={34} x2={todayAt} y2={axisY + 10} className={styles.routeNow} />
          )}

          {/* A topic resting on another one inside the set */}
          {edges.map(({ from: a, to: b }) => {
            const p = pos[a];
            const q = pos[b];
            const d = vertical
              ? `M${p.x},${p.y + NODE / 2 + 56} C${p.x},${p.y + 110} ${q.x},${q.y - 80} ${q.x},${q.y - NODE / 2 - 6}`
              : `M${p.x + NODE / 2 + 8},${p.y} C${p.x + 110},${p.y} ${q.x - 110},${q.y} ${q.x - NODE / 2 - 10},${q.y}`;
            return <path key={`${a}-${b}`} d={d} className={styles.edge} />;
          })}

          {/* Each topic tied down to its share of the axis */}
          {!vertical &&
            order.map((id) => (
              <line
                key={`drop-${id}`}
                x1={pos[id].x}
                y1={pos[id].y + NODE / 2 + 62}
                x2={pos[id].x}
                y2={axisY - 6}
                className={styles.routeDrop}
              />
            ))}
        </svg>

        {/* Start and the set's deadline, at the two ends of the axis */}
        <span
          className={styles.setEnd}
          style={vertical ? { left: V_AXIS + 12, top: along(0) - 40 } : { left: along(0) - 14, top: axisY - 17, translate: "-100% 0" }}
        >
          старт
          <b>{formatShort(set.start)}</b>
        </span>
        <span
          className={`${styles.setEnd} ${styles.setEndDeadline}`}
          style={vertical ? { left: V_AXIS + 12, top: along(1) + 6 } : { left: along(1) + 14, top: axisY - 17 }}
        >
          дедлайн сета
          <b>{formatShort(set.deadline)}</b>
        </span>
        <button
          type="button"
          className={styles.setFinalMock}
          style={
            vertical
              ? { left: V_AXIS + 12, top: along(1) + 32 }
              : { left: along(1) + 26, top: axisY / 2 - 14, translate: "-50% 0" }
          }
          disabled={!allClosed}
          title={allClosed ? "Финальный мок: проверить сет целиком" : "Откроется, когда все темы сета будут держаться"}
          onClick={onFinalMock}
        >
          <Icon name="flag" size={14} /> Final mock
        </button>
        <span
          className={`${styles.routeMark} ${styles.setTodayMark}`}
          style={vertical ? { left: V_AXIS - 6, top: todayAt - 24, translate: "none" } : { left: todayAt, top: 8 }}
        >
          {todayLabel}
        </span>

        {/* Each topic's own deadline, under its share of the axis */}
        {order.map((id, i) => {
          const due = dueOf(model.states[id], starts[i], plan[id]);
          return (
            <span
              key={`due-${id}`}
              className={styles.setDue}
              data-due={due}
              style={
                vertical
                  ? { left: V_AXIS + 14, top: along(i / n) + 12 }
                  : { left: along((i + 0.5) / n), top: axisY + 12, translate: "-50% 0" }
              }
            >
              <b>до {formatShort(plan[id])}</b>
              {DUE_TEXT[due](plan[id])}
            </span>
          );
        })}

        {order.map((id, i) => {
          const skill = skillById(id);
          const state = model.states[id] ?? "weak";
          const at = pos[id];
          const due = dueOf(state, starts[i], plan[id]);
          const trap = model.misconceptions[id]?.some((m) => m.status === "confirmed" || m.status === "suspected") ?? false;
          return (
            <button
              key={id}
              type="button"
              className={[
                styles.mapNode,
                styles.setNode,
                skill.root && styles.mapNodeRoot,
                due === "now" && styles.setNodeNow,
              ]
                .filter(Boolean)
                .join(" ")}
              data-state={state}
              style={{ left: at.x - NODE_W / 2, top: at.y - NODE / 2, width: NODE_W }}
              aria-label={`Открыть тему ${i + 1}. ${skill.name}: ${STATE_LABEL[state]}, до ${formatShort(plan[id])}`}
              onClick={() => onSelect(id)}
            >
              <span className={styles.mapShapeBox}>
                <NodeMark state={state} recall={model.recall[id] ?? 0.4} />
              </span>
              <span className={styles.mapChip}>
                <StateGlyph state={state} size={10} />
                <span className={styles.mapChipName}>{skill.name}</span>
              </span>
              <span className={styles.mapFlags}>
                {due === "late" && <span className={styles.behindTag}>отстаёт</span>}
                {due === "now" && <span className={styles.nowTag}>сейчас</span>}
                {id === next && due !== "now" && <span className={styles.nowTag}>начни здесь</span>}
                {skill.root && <span className={styles.rootTag}>корень</span>}
                {trap && <span className={styles.trapTag}>ловушка</span>}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
