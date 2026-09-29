"use client";

import { useEffect, useState } from "react";
import { FirstHint } from "@/components/hints/FirstHint";
import { loadCompare, REMOTE, type CompareView as RemoteCompare } from "./catalog";
import type { Profile } from "./assistant";
import { Icon } from "./Icon";
import { compareRows, compareSummary, programById } from "./programs";
import styles from "./layout.module.css";

type CompareViewProps = {
  ids: string[];
  profile: Profile;
  onBack: () => void;
  onRemove: (id: string) => void;
  onOpen: (id: string) => void;
};

/** Side-by-side comparison (§3.4): rows that differ are marked, then a short takeaway. */
export function CompareView({ ids, profile, onBack, onRemove, onOpen }: CompareViewProps) {
  // The server compares against the student's own profile and words the takeaway; the local rules
  // stay as the offline answer and while the first read is in flight.
  const [remote, setRemote] = useState<RemoteCompare | null>(null);
  const [foldedOpen, setFoldedOpen] = useState(false);
  useEffect(() => {
    if (!REMOTE) return;
    let cancelled = false;
    setRemote(null);
    loadCompare(ids).then((next) => {
      if (!cancelled) setRemote(next);
    });
    return () => {
      cancelled = true;
    };
  }, [ids]);

  const localRows = compareRows(ids, profile);
  const { same, keyDifferences } = compareSummary(localRows);
  // A remote answer for another set of ids (one was just removed) is stale until the refetch lands.
  const fresh = remote && remote.ids.length === ids.length && remote.ids.every((id, k) => id === ids[k]) ? remote : null;
  const rows = fresh
    ? fresh.rows.map((r) => ({ label: r.param, values: r.values, differs: r.differs, relevant: r.relevant }))
    : localRows;

  return (
    <section className={styles.compare} aria-label="Сравнение программ">
      <div className={styles.compareHead}>
        <button type="button" className={styles.iconButton} aria-label="Назад к чату" onClick={onBack}>
          <Icon name="arrow-left" />
        </button>
        <h2 className={styles.compareTitle}>Сравнение</h2>
      </div>

      <FirstHint id="choice-compare" title="Как сравнивать">
        Программы стоят рядом по одним и тем же строкам: реалистичность, стоимость, баллы, сроки подачи и город. Нажми на название, чтобы открыть
        программу целиком, а крестик уберёт её из сравнения.
      </FirstHint>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th />
              {ids.map((id) => {
                const p = programById(id);
                return (
                  <th key={id} scope="col">
                    <div className={styles.colHead}>
                      <button type="button" className={styles.rowMain} style={{ padding: 0 }} onClick={() => onOpen(id)}>
                        <span>
                          <span className={styles.colName}>{p.university}</span>
                          <span className={styles.colSub}>{p.program}</span>
                        </span>
                      </button>
                      <button type="button" className={styles.iconButton} aria-label="Убрать из сравнения" onClick={() => onRemove(id)}>
                        <Icon name="x" size={16} />
                      </button>
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.label} className={row.differs ? styles.differs : ""} style={{ animationDelay: `${i * 35}ms` }}>
                <th scope="row">{row.label}</th>
                {row.values.map((value, j) => (
                  <td key={ids[j]}>{value}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Одинаковое сворачивается: смотреть в таблицу стоит ради различий */}
      {fresh && fresh.collapsedSame.length > 0 && (
        <p className={styles.compareSummary}>
          <button type="button" className={styles.inlineLink} onClick={() => setFoldedOpen((v) => !v)}>
            Одинаково по {fresh.collapsedSame.length} параметрам
          </button>
          {foldedOpen && <> — {fresh.collapsedSame.join(", ")}.</>}
        </p>
      )}

      <p className={styles.compareSummary}>
        {fresh ? (
          fresh.conclusion ? (
            fresh.conclusion
          ) : fresh.conclusionStatus === "failed" ? (
            <>Вывод не получился — таблица выше всё равно верная.</>
          ) : (
            <>Готовим вывод…</>
          )
        ) : (
          <>
            {same.length > 0 && <>Одинаково по: {same.join(", ")}. </>}
            {keyDifferences.length > 0 ? (
              <>Для тебя разница — {keyDifferences.join(", ")}.</>
            ) : (
              <>Существенных для тебя различий нет.</>
            )}
          </>
        )}
      </p>
    </section>
  );
}
