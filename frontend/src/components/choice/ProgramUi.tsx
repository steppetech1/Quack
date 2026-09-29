"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { backend } from "@/api/backend";
import { ApiError } from "@/api/client";
import type { Profile } from "./assistant";
import { Icon } from "./Icon";
import { LEVEL_LABEL, evaluate, formatEur, programById, type Level } from "./programs";
import styles from "./layout.module.css";

export type ProgramActions = {
  saved: string[];
  compare: string[];
  onToggleSave: (id: string) => void;
  onToggleCompare: (id: string) => void;
  onOpen: (id: string) => void;
  /** A flagged program leaves the matching: the screen reloads the catalogue */
  onFlagged?: (id: string) => void;
};

/**
 * «Данные неверны» on an automatically extracted record. The backend hides it from matching; a
 * hand-verified record cannot be flagged and answers 409, so the button is only shown for the
 * extracted ones and says so when the server refuses anyway.
 */
function ProgramFlag({ id, onFlagged }: { id: string; onFlagged?: (id: string) => void }) {
  const [state, setState] = useState<"idle" | "asking" | "done" | "refused">("idle");
  const [reason, setReason] = useState("");

  if (state === "done") return <p className={styles.factorNote}>Спасибо — программа убрана из подборки.</p>;
  if (state === "refused") return <p className={styles.factorNote}>Эта запись выверена вручную, её не помечают.</p>;

  if (state === "idle") {
    return (
      <button type="button" className={styles.pillButton} onClick={() => setState("asking")}>
        Данные неверны
      </button>
    );
  }

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (!reason.trim()) return;
        try {
          await backend.programs.flag(id, reason.trim());
          setState("done");
          onFlagged?.(id);
        } catch (err: unknown) {
          setState(err instanceof ApiError && err.status === 409 ? "refused" : "idle");
        }
      }}
    >
      <input
        autoFocus
        aria-label="Что не так"
        placeholder="Что именно неверно?"
        value={reason}
        maxLength={300}
        onChange={(e) => setReason(e.target.value)}
      />{" "}
      <button type="submit" className={styles.pillButton} disabled={!reason.trim()}>
        Отправить
      </button>
    </form>
  );
}

export function LevelBadge({ level }: { level: Level }) {
  return <span className={`${styles.level} ${styles[level]}`}>{LEVEL_LABEL[level]}</span>;
}

export function LevelDot({ level }: { level: Level }) {
  return <span className={`${styles.levelDot} ${styles[level]}`} title={LEVEL_LABEL[level]} />;
}

/**
 * A mouse wheel only goes up and down, so over the cards it turns them sideways instead. Once the row
 * reaches its first or last card the wheel is let through, and the chat scrolls on as usual.
 */
function useSidewaysWheel(ref: RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const row = ref.current;
    if (!row) return;
    const onWheel = (e: WheelEvent) => {
      // A trackpad already swipes sideways, and Ctrl + wheel is the browser's zoom
      if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
      const max = row.scrollWidth - row.clientWidth;
      if (max <= 0) return;
      const atEnd = e.deltaY > 0 ? row.scrollLeft >= max - 1 : row.scrollLeft <= 0;
      if (atEnd) return;
      e.preventDefault();
      // Lines (Firefox) and pages come in other units than pixels
      const step = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? row.clientWidth : 1;
      row.scrollBy({ left: e.deltaY * step });
    };
    row.addEventListener("wheel", onWheel, { passive: false });
    return () => row.removeEventListener("wheel", onWheel);
  }, [ref]);
}

/**
 * Horizontal row of program cards posted by the assistant into the chat. It scrolls sideways:
 * a swipe on touch screens, the arrows, a trackpad or the mouse wheel on desktop. Arrows and edge fades show only
 * where there is more to see.
 */
export function ProgramCards({ ids, profile, actions }: { ids: string[]; profile: Profile; actions: ProgramActions }) {
  const rowRef = useRef<HTMLDivElement>(null);
  useSidewaysWheel(rowRef);
  const [edges, setEdges] = useState({ start: true, end: true });

  const measure = useCallback(() => {
    const row = rowRef.current;
    if (!row) return;
    const start = row.scrollLeft <= 4;
    const end = row.scrollLeft + row.clientWidth >= row.scrollWidth - 4;
    setEdges((e) => (e.start === start && e.end === end ? e : { start, end }));
  }, []);

  useEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(row);
    return () => observer.disconnect();
  }, [measure, ids.length]);

  /** One press moves by most of a screenful, leaving the last card partly in view for context. */
  const page = (dir: 1 | -1) => {
    const row = rowRef.current;
    row?.scrollBy({ left: dir * row.clientWidth * 0.8, behavior: "smooth" });
  };

  return (
    <div className={styles.cardsRail} data-start={edges.start} data-end={edges.end}>
      <button
        type="button"
        className={styles.cardsArrow}
        data-dir="prev"
        aria-label="Предыдущие программы"
        hidden={edges.start}
        onClick={() => page(-1)}
      >
        <Icon name="chevron-right" size={18} />
      </button>
      <button
        type="button"
        className={styles.cardsArrow}
        data-dir="next"
        aria-label="Следующие программы"
        hidden={edges.end}
        onClick={() => page(1)}
      >
        <Icon name="chevron-right" size={18} />
      </button>
      <div ref={rowRef} className={styles.cards} onScroll={measure}>
        {ids.map((id, i) => {
          const program = programById(id);
          const evaluation = evaluate(program, profile);
          const saved = actions.saved.includes(id);
          const comparing = actions.compare.includes(id);

          return (
            <article key={id} className={styles.card} style={{ animationDelay: `${i * 90}ms` }}>
              <div className={styles.cardTop}>
                <LevelBadge level={evaluation.level} />
                {/* Откуда запись: выдуманная демо-программа и автоматически извлечённая — не одно и то же */}
                {program.isDemo !== false && <span className={styles.demo}>демо</span>}
                {program.extractedAuto && (
                  <span className={styles.demo} title="Данные сняты со страницы вуза автоматически и не выверены вручную">
                    извлечено автоматически
                  </span>
                )}
              </div>
              <h3 className={styles.cardUni}>{program.university}</h3>
              <p className={styles.cardProgram}>{program.program}</p>
              <p className={styles.cardMeta}>
                {program.city}, {program.country} · {formatEur(program.costEur)}
              </p>
              {/* Почему так: слова бэка о реалистичности; пока их нет — прямо об этом */}
              {evaluation.realismText ? (
                <p className={styles.cardFit}>{evaluation.realismText}</p>
              ) : evaluation.realismTextStatus === "generating" ? (
                <p className={styles.cardFit}>Готовим объяснение…</p>
              ) : null}
              <p className={styles.cardFit}>
                {evaluation.softPending && <>Уточняем соответствие… </>}
                {evaluation.fits.length > 0 && (
                  <>
                    <span className={styles.fitLabel}>Подходит тебе:</span> {evaluation.fits.join(", ")}
                  </>
                )}
                {evaluation.misfits.length > 0 && (
                  <>
                    {evaluation.fits.length > 0 && <br />}
                    <span className={styles.fitLabel}>Но:</span> {evaluation.misfits.join(", ")}
                  </>
                )}
              </p>
              <div className={styles.cardActions}>
                <button
                  type="button"
                  className={`${styles.pillButton} ${styles.saveButton}`}
                  aria-pressed={saved}
                  aria-label={saved ? "Убрать из избранного" : "В избранное"}
                  onClick={() => actions.onToggleSave(id)}
                >
                  <Icon name="star" size={16} />
                </button>
                <button
                  type="button"
                  className={styles.pillButton}
                  aria-pressed={comparing}
                  onClick={() => actions.onToggleCompare(id)}
                >
                  <Icon name="git-compare" size={16} />
                  {comparing ? "В сравнении" : "Сравнить"}
                </button>
                <button type="button" className={`${styles.pillButton} ${styles.cardMore}`} onClick={() => actions.onOpen(id)}>
                  Подробнее
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

/** Slide-in panel with every factor behind the realism level. */
export function ProgramDrawer({
  id,
  profile,
  actions,
  onClose,
}: {
  id: string | null;
  profile: Profile;
  actions: ProgramActions;
  onClose: () => void;
}) {
  const program = id ? programById(id) : null;
  const evaluation = program ? evaluate(program, profile) : null;

  return (
    <div className={`${styles.drawerLayer} ${program ? styles.drawerOpen : ""}`} aria-hidden={!program}>
      <div className={styles.backdrop} onClick={onClose} />
      <aside className={styles.drawer} role="dialog" aria-label={program?.university}>
        {program && evaluation && (
          <>
            <div className={styles.drawerHead}>
              <LevelBadge level={evaluation.level} />
              <button type="button" className={styles.iconButton} aria-label="Закрыть" onClick={onClose}>
                <Icon name="x" />
              </button>
            </div>
            <div className={styles.drawerBody} key={program.id}>
              <h2 className={styles.drawerUni}>{program.university}</h2>
              <p className={styles.drawerProgram}>{program.program}</p>
              <p className={styles.drawerMeta}>
                {program.city}, {program.country} · {program.duration} · подача до {program.deadline} ·{" "}
                <span className={styles.demo}>демо-данные</span>
              </p>

              <h3 className={styles.sectionTitle}>Реалистичность — из чего складывается</h3>
              <ul className={styles.factors}>
                {evaluation.factors.map((f, i) => (
                  <li key={`${i}-${f.label}`} className={styles.factor} style={{ animationDelay: `${i * 60}ms` }}>
                    <span
                      className={`${styles.factorMark} ${
                        f.status === "ok" ? styles.factorOk : f.status === "below" ? styles.factorBelow : styles.factorUnknown
                      }`}
                    />
                    <span className={styles.factorLabel}>
                      {f.label}: {f.value}
                    </span>
                    <span className={styles.factorNote}>{f.note}</span>
                  </li>
                ))}
              </ul>

              <h3 className={styles.sectionTitle}>Подходит тебе</h3>
              {evaluation.fits.length || evaluation.misfits.length ? (
                <ul className={styles.fitList}>
                  {evaluation.fits.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                  {evaluation.misfits.map((f) => (
                    <li key={f}>но: {f}</li>
                  ))}
                </ul>
              ) : (
                <p className={styles.factorNote}>Расскажи в чате, что тебе важно, — и я объясню, чем программа подходит.</p>
              )}

              <h3 className={styles.sectionTitle}>Среда</h3>
              <p className={styles.factorNote}>
                {program.remote ? "" : `Наука: ${program.research}. Обмен: ${program.exchange}. `}Язык: {program.language}.
              </p>

              {/* Откуда запись и что делать, если она врёт. Выверенный вручную «пол» пометить
                  нельзя — бэк отвечает 409, поэтому кнопки там и нет. */}
              {program.sourceUrl && (
                <>
                  <h3 className={styles.sectionTitle}>Источник</h3>
                  <p className={styles.factorNote}>
                    <a href={program.sourceUrl} target="_blank" rel="noreferrer noopener">
                      Страница вуза
                    </a>
                    {program.checkedAt && <> · проверено {program.checkedAt}</>}
                  </p>
                  {program.extractedAuto && <ProgramFlag id={program.id} onFlagged={actions.onFlagged} />}
                </>
              )}
            </div>
            <div className={styles.drawerActions}>
              <button
                type="button"
                className={`${styles.pillButton} ${styles.saveButton}`}
                aria-pressed={actions.saved.includes(program.id)}
                onClick={() => actions.onToggleSave(program.id)}
              >
                <Icon name="star" size={16} />
                {actions.saved.includes(program.id) ? "В избранном" : "В избранное"}
              </button>
              <button
                type="button"
                className={styles.pillButton}
                aria-pressed={actions.compare.includes(program.id)}
                onClick={() => actions.onToggleCompare(program.id)}
              >
                <Icon name="git-compare" size={16} />
                {actions.compare.includes(program.id) ? "В сравнении" : "Сравнить"}
              </button>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
