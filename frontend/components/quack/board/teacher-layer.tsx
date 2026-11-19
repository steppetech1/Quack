"use client";

import { CircleAlert, CircleQuestionMark, MessageSquareText } from "lucide-react";
import katex from "katex";
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import type { LayerBlock, LayerHint, MarkKind, TeacherLine } from "./use-teacher-layer";

const tex = (src: string) => katex.renderToString(src, { throwOnError: false, output: "htmlAndMathml" });

/** How a line was read, as small as it can be and still be read: LaTeX through KaTeX, plain text as it is */
function Reading({ line }: { line: TeacherLine }) {
  if (line.kind === "text") return <span className={cn(line.crossedOut && "line-through")}>{line.text}</span>;
  return <span className={cn(line.crossedOut && "line-through")} dangerouslySetInnerHTML={{ __html: tex(line.text ?? "") }} />;
}

/** The fix of a misread line: the text it really says, with a preview as it will be shown */
function Fix({ line, onSave, onClose }: { line: TeacherLine; onSave: (text: string) => Promise<void>; onClose: () => void }) {
  const tx = useT().canvasTeacher;
  const field = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState(line.text ?? "");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    field.current?.focus({ preventScroll: true });
    field.current?.select();
  }, []);

  const save = async () => {
    const text = value.trim();
    if (!text || busy) return;
    if (text === line.text) return onClose();
    setBusy(true);
    setFailed(false);
    try {
      await onSave(text);
      onClose();
    } catch {
      setFailed(true);
      setBusy(false);
    }
  };

  return (
    <div
      data-nopan
      role="group"
      aria-label={tx.readAs}
      className="pointer-events-auto absolute top-full left-0 z-30 mt-1 w-[320px] rounded-[2px] border border-seam bg-cloth-raised p-3.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") onClose();
      }}
    >
      <p className="caps text-paper-dim">{tx.readAsLabel}</p>
      <div className="mt-1 min-h-8 overflow-x-auto py-1 text-body text-paper" dangerouslySetInnerHTML={{ __html: value.trim() ? tex(value) : "" }} />
      <textarea
        ref={field}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void save();
          }
        }}
        rows={2}
        spellCheck={false}
        aria-label={tx.fixAria}
        placeholder={tx.fixPlaceholder}
        className="num mt-2 w-full resize-none rounded-[2px] border border-seam bg-cloth px-2 py-1.5 text-small text-paper outline-none placeholder:text-paper-dim focus-visible:border-ribbon/70"
      />
      {failed && (
        <p role="alert" className="mt-1 text-small text-st-confused">
          {tx.fixFailed}
        </p>
      )}
      <div className="mt-2 -mb-1.5 -ml-3 flex items-center gap-1">
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy || !value.trim()}
          className="caps inline-flex h-10 items-center rounded-pill bg-ribbon px-4 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ribbon disabled:opacity-50"
        >
          {busy ? tx.saving : tx.save}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="caps inline-flex h-10 items-center rounded-pill px-4 text-paper transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover focus-visible:outline-2 focus-visible:outline-ribbon"
        >
          {tx.cancel}
        </button>
      </div>
    </div>
  );
}

function MarkNote({ kind }: { kind: MarkKind }) {
  const tx = useT().canvasTeacher;
  const error = kind === "error";
  const Icon = error ? CircleAlert : CircleQuestionMark;
  return (
    <p className={cn("flex w-fit items-center gap-1.5 rounded-pill border px-2.5 py-1 text-small", error ? "border-st-confused/50 text-st-confused" : "border-seam text-paper-dim")}>
      <Icon aria-hidden className="size-4 shrink-0" />
      <span className="sr-only">{tx.teacher}</span>
      {error ? tx.markError : tx.markQuestion}
    </p>
  );
}

function HintNote({ text }: { text: string }) {
  const tx = useT().canvasTeacher;
  return (
    <p className="flex items-start gap-2 rounded-[2px] border border-seam bg-cloth px-2.5 py-2 text-small text-paper-dim">
      <MessageSquareText aria-hidden className="mt-px size-4 shrink-0" />
      <span>
        <span className="sr-only">{tx.hint}</span>
        {text}
      </span>
    </p>
  );
}

/**
 * The teacher layer of a backend canvas, over the learner's ink in board coordinates: how each line was read
 * (subtle, to the right of it; a tap opens the fix), the teacher's marks and hints for the line, and the task block.
 * The layer never takes pointer events away from the board except on its own buttons, so writing the next line
 * under or beside a note still draws.
 */
export function TeacherLayer({
  lines,
  marks,
  hints,
  blocks,
  onCorrect,
}: {
  /** only the lines whose strokes are still on the board */
  lines: TeacherLine[];
  marks: Record<string, MarkKind>;
  hints: LayerHint[];
  blocks: LayerBlock[];
  onCorrect: (lineId: string, text: string) => Promise<void>;
}) {
  const tx = useT().canvasTeacher;
  const [fixing, setFixing] = useState<string | null>(null);

  // a hint without a line (or of a line that is gone) goes beside the lowest line
  const lowest = useMemo(() => lines.reduce<TeacherLine | null>((a, l) => (!a || l.box[3] > a.box[3] ? l : a), null), [lines]);
  const hintsOf = useMemo(() => {
    const by = new Map<string, LayerHint[]>();
    for (const h of hints) {
      const at = lines.find((l) => l.id === h.lineId) ?? lowest;
      if (!at) continue;
      by.set(at.id, [...(by.get(at.id) ?? []), h]);
    }
    return by;
  }, [hints, lines, lowest]);

  return (
    <>
      {blocks.map((b) => (
        <div key={b.id} aria-hidden className="pointer-events-none absolute rounded-[2px] border border-dashed border-seam" style={{ left: b.x, top: b.y, width: b.w, height: b.h }}>
          <span className="caps absolute bottom-full left-1 mb-2 whitespace-nowrap text-paper-dim">{tx.task}</span>
        </div>
      ))}
      {lines.map((line) => {
        const mark = marks[line.id];
        const own = hintsOf.get(line.id) ?? [];
        if (!line.text && !mark && !own.length) return null;
        return (
          <div
            key={line.id}
            className="pointer-events-none absolute flex w-[300px] flex-col items-start gap-1.5"
            style={{ left: line.box[2] + 16, top: line.box[1], transform: "scale(var(--inv))", transformOrigin: "top left", zIndex: fixing === line.id ? 30 : undefined }}
          >
            {line.text && (
              <div className="relative">
                <button
                  type="button"
                  data-nopan
                  aria-expanded={fixing === line.id}
                  aria-label={tx.lineAria(line.text)}
                  onClick={() => setFixing(fixing === line.id ? null : line.id)}
                  className={cn(
                    "pointer-events-auto -mx-2 inline-flex min-h-10 max-w-[300px] items-center overflow-hidden rounded-option px-2 text-left text-[13px] text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper focus-visible:outline-2 focus-visible:outline-ribbon",
                    fixing === line.id && "bg-cloth-hover text-paper",
                  )}
                >
                  <Reading line={line} />
                </button>
                {fixing === line.id && <Fix line={line} onSave={(t) => onCorrect(line.id, t)} onClose={() => setFixing(null)} />}
              </div>
            )}
            {mark && <MarkNote kind={mark} />}
            {own.map((h) => (
              <HintNote key={h.key} text={h.text} />
            ))}
          </div>
        );
      })}
    </>
  );
}
