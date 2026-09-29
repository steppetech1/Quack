"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import styles from "./choice.module.css";

export type ChatMsg = {
  id: number;
  role: "user" | "assistant";
  text: string;
  typing?: boolean;
  /** Confirm (✓) button state; "leaving" plays the exit animation */
  confirm: "none" | "shown" | "leaving";
  /** Whether the edit (✏) button is available — only on summaries */
  editable: boolean;
  editing?: boolean;
  confirmBeforeEdit?: ChatMsg["confirm"];
  /** A milestone this reply ticked done, with a way to take the tick back */
  milestone?: { id: string; title: string; undone?: boolean };
  /** The entrance test offered after the first save (product-logic §8.1): taken, put off, or not answered yet */
  offer?: { kind: "diagnostic"; answered?: "start" | "later" };
  /** A test date this reply picked, with the one it replaced to go back to */
  testDate?: { exam: "sat" | "ent"; label: string; prev: string | null; prevLabel: string; undone?: boolean };
  /** The stream stopped before the answer ended — the text above is half an answer, not a short one */
  truncated?: boolean;
};

type ChatMessageProps = {
  msg: ChatMsg;
  onConfirm: (id: number) => void;
  onEditStart: (id: number) => void;
  onEditCancel: (id: number) => void;
  onEditSave: (id: number, original: string, edited: string) => void;
  onUndoMilestone?: (id: number) => void;
  onUndoTestDate?: (id: number) => void;
  /** Absent once the test is done or put off elsewhere: the offer then has nothing left to ask */
  onOffer?: (id: number, answer: "start" | "later") => void;
  /** Ask the same question again after a cut-off answer */
  onRetry?: (id: number) => void;
};

export function ChatMessage({ msg, onConfirm, onEditStart, onEditCancel, onEditSave, onUndoMilestone, onUndoTestDate, onOffer, onRetry }: ChatMessageProps) {
  const editRef = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState(msg.text);

  // Entering edit mode: start from the current text with the caret at the end
  useEffect(() => {
    if (!msg.editing) return;
    setDraft(msg.text);
    requestAnimationFrame(() => {
      const el = editRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }, [msg.editing, msg.text]);

  // Grow with the text so the bubble expands instead of scrolling
  useLayoutEffect(() => {
    const el = editRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft, msg.editing]);

  const save = () => onEditSave(msg.id, msg.text, draft);
  const classes = [styles.msg, msg.role === "user" && styles.msgUser, msg.editing && styles.isEditing];

  return (
    <div className={classes.filter(Boolean).join(" ")}>
      <div className={styles.msgBubble}>
        {msg.typing ? (
          <div className={styles.typing} aria-label="Ассистент печатает">
            <span />
            <span />
            <span />
          </div>
        ) : msg.editing ? (
          <textarea
            ref={editRef}
            className={styles.editInput}
            value={draft}
            rows={1}
            aria-label="Правка резюме"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                save();
              } else if (e.key === "Escape") {
                onEditCancel(msg.id);
              }
            }}
          />
        ) : (
          <div className={styles.msgText}>{msg.text}</div>
        )}
        {msg.offer && !msg.typing && !msg.offer.answered && onOffer && (
          <div className={styles.msgOffer}>
            <button type="button" className={styles.msgOfferMain} onClick={() => onOffer(msg.id, "start")}>
              Пройти замер
            </button>
            <button type="button" className={styles.msgOfferLater} onClick={() => onOffer(msg.id, "later")}>
              Позже — начать с сета по профилю
            </button>
          </div>
        )}
        {msg.testDate && !msg.typing && (
          <p className={styles.msgMilestone} data-undone={msg.testDate.undone || undefined}>
            {msg.testDate.undone ? (
              <>Вернул прежнюю дату: {msg.testDate.prevLabel}</>
            ) : (
              <>
                ✓ Дата теста: {msg.testDate.label} ·{" "}
                <button type="button" className={styles.msgMilestoneUndo} onClick={() => onUndoTestDate?.(msg.id)}>
                  вернуть {msg.testDate.prevLabel}
                </button>
              </>
            )}
          </p>
        )}
        {/* Связь оборвалась на полуслове. Молча оставить обрубок — значит выдать
            половину ответа за целый: ученик поверит, что это всё. */}
        {msg.truncated && !msg.typing && (
          <p className={styles.msgMilestone}>
            Ответ оборвался — связь пропала.{" "}
            <button type="button" className={styles.msgMilestoneUndo} onClick={() => onRetry?.(msg.id)}>
              повторить
            </button>
          </p>
        )}
        {msg.milestone && !msg.typing && (
          <p className={styles.msgMilestone} data-undone={msg.milestone.undone || undefined}>
            {msg.milestone.undone ? (
              <>Отметка снята: {msg.milestone.title}</>
            ) : (
              <>
                ✓ {msg.milestone.title} ·{" "}
                <button type="button" className={styles.msgMilestoneUndo} onClick={() => onUndoMilestone?.(msg.id)}>
                  отменить
                </button>
              </>
            )}
          </p>
        )}
      </div>

      {(msg.confirm !== "none" || msg.editable) && (
        <div className={styles.msgActions}>
          {msg.confirm !== "none" && (
            <button
              type="button"
              className={`${styles.iconBtn} ${styles.iconBtnConfirm} ${msg.confirm === "leaving" ? styles.isLeaving : ""}`}
              aria-label={msg.editing ? "Сохранить правку" : "Всё верно"}
              onClick={() => (msg.editing ? save() : onConfirm(msg.id))}
            >
              <span className={`${styles.icon} ${styles.iconCheck}`} />
            </button>
          )}
          {msg.editable && (
            <button
              type="button"
              className={`${styles.iconBtn} ${styles.iconBtnEdit}`}
              aria-label={msg.editing ? "Отменить правку" : "Исправить"}
              onClick={() => (msg.editing ? onEditCancel(msg.id) : onEditStart(msg.id))}
            >
              <span className={`${styles.icon} ${styles.iconPencil}`} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
