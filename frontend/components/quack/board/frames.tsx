"use client";

import { CircleQuestionMark, MessageSquareText } from "lucide-react";
import katex from "katex";
import { memo, useEffect, useRef } from "react";
import { LINES_TOP, ROW_H, type Frame } from "@/lib/canvas-board";
import type { CanvasBlock, HandLine, TeacherMark } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { Hand, hashSeed } from "../handwriting";
import { RichText } from "../rich-text";
import { ScrollRegion } from "../scroll-region";
import { useT } from "@/lib/i18n/provider";
import { useSoon } from "../soon";

/** Строка почерка на рамке: нажатие открывает «Прочитано как…» */
function HandRow({ line, open, flagged, ring, onToggle }: { line: HandLine; open: boolean; flagged: boolean; ring: boolean; onToggle: () => void }) {
  const tx = useT();
  const soon = useSoon();
  const lineBtn = useRef<HTMLButtonElement>(null);
  const fixBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const btn = lineBtn.current;
    fixBtn.current?.focus({ preventScroll: true });
    return () => {
      if (!document.activeElement || document.activeElement === document.body) btn?.focus({ preventScroll: true });
    };
  }, [open]);

  return (
    <div className="relative w-fit max-w-[560px] shrink-0">
      <button
        ref={lineBtn}
        type="button"
        data-hand
        aria-expanded={open}
        aria-label={tx.board.frames.lineAria(line.read)}
        onClick={onToggle}
        className={cn(
          "-mx-2 flex min-h-10 max-w-[calc(100%+1rem)] items-center rounded-option px-2 py-0.5 text-left transition-[background-color,box-shadow] duration-700 hover:bg-cloth-hover focus-visible:outline-2 focus-visible:outline-ribbon",
          open && "bg-cloth-hover",
          ring && "ring-2 ring-ribbon",
        )}
      >
        <span className="block text-[28px] leading-[1.25] text-paper/90">
          <Hand text={line.hand} seed={hashSeed(line.id + line.read)} className={cn(flagged && "underline decoration-st-confused/80 decoration-1 underline-offset-[5px]")} />
        </span>
      </button>
      {open && (
        <div
          data-hand
          data-nopan
          role="group"
          aria-label={tx.board.frames.readAs}
          className="absolute top-full left-0 z-30 mt-1 w-[320px] rounded-[14px] border border-seam bg-cloth-raised p-3.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
        >
          <span aria-hidden className="absolute -top-1.5 left-6 size-3 rotate-45 border-t border-l border-seam bg-cloth-raised" />
          <p className="text-small text-paper-dim">{tx.board.frames.readAsLabel}</p>
          <ScrollRegion className="mt-1 py-1 text-body text-paper" html={katex.renderToString(line.tex, { throwOnError: false, output: "htmlAndMathml" })} />
          <button
            ref={fixBtn}
            type="button"
            onClick={() => soon(tx.board.frames.fixSoon)}
            className="-mb-1.5 -ml-3 mt-1 inline-flex h-10 items-center rounded-control px-3 text-small text-paper transition-colors hover:bg-cloth-hover"
          >
            {tx.board.frames.fix}
          </button>
        </div>
      )}
    </div>
  );
}

/** Пометка учителя справа от строки: вопрос — розовым, подсказка — маленьким «комментарием» */
function Mark({ m }: { m: TeacherMark }) {
  const tx = useT();
  const error = m.kind === "error";
  return (
    <>
      <span aria-hidden className={cn("mx-2 mt-[24px] h-px min-w-6 flex-1", error ? "bg-st-confused/55" : "bg-rule/60")} />
      <div className="mt-[10px] w-[260px] shrink-0">
        {error ? (
          <p className="flex items-start gap-2 text-small text-st-confused">
            <CircleQuestionMark aria-hidden className="mt-px size-4 shrink-0" />
            <span>
              <span className="sr-only">{tx.board.frames.teacherLayer}</span>
              {m.text}
            </span>
          </p>
        ) : (
          <p className="flex items-start gap-2 rounded-[12px] border border-seam bg-cloth px-2.5 py-2 text-small text-paper-dim">
            <MessageSquareText aria-hidden className="mt-px size-4 shrink-0" />
            <span>
              <span className="sr-only">{tx.board.frames.teacherLayer}</span>
              {m.text}
            </span>
          </p>
        )}
      </div>
    </>
  );
}

function FrameBody({
  block,
  teacher,
  openLine,
  ringLine,
  setOpenLine,
}: {
  block: CanvasBlock;
  teacher: boolean;
  openLine: string | null;
  ringLine: string | null;
  setOpenLine: (k: string | null) => void;
}) {
  const tx = useT();
  if (block.kind === "hint") {
    return <RichText text={block.text} className="absolute inset-0 p-6 text-[16px] text-paper-dim" />;
  }
  if (block.kind === "card") {
    return (
      <ul className="absolute inset-0 flex flex-col divide-y divide-seam-soft p-6 pt-4">
        {block.formulas.map((f) => (
          <li key={f.name} className="flex-1 py-2">
            <p className="text-small text-paper-dim">{f.name}</p>
            <RichText text={`$$${f.tex}$$`} className="[&_.katex-display]:my-1 [&_.katex-display]:text-left" />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <>
      <RichText text={block.condition} className="absolute inset-x-7 top-6 text-[18px]" />
      <p className="num absolute left-7 text-small tracking-wide text-paper-dim uppercase" style={{ top: LINES_TOP - 28 }}>
        {tx.board.frames.solution}
      </p>
      {block.lines.map((line, i) => {
        const key = `${block.id}:${line.id}`;
        const marks = teacher ? block.marks.filter((m) => m.afterLine === line.id) : [];
        return (
          <div
            key={line.id}
            id={`line-${block.id}-${line.id}`}
            className={cn("absolute inset-x-7 flex items-start", openLine === key && "z-30")}
            style={{ top: LINES_TOP + i * ROW_H, height: ROW_H }}
          >
            <HandRow line={line} flagged={marks.some((m) => m.kind === "error")} open={openLine === key} ring={ringLine === key} onToggle={() => setOpenLine(openLine === key ? null : key)} />
            {marks.map((m) => (
              <Mark key={m.id} m={m} />
            ))}
          </div>
        );
      })}
    </>
  );
}

/** Рамки блоков канваса. Не перерисовываются, пока не изменились слой учителя, поповер или подсветка */
export const Frames = memo(function Frames({
  frames,
  teacher,
  openLine,
  ringKey,
  setOpenLine,
  headingLevel,
}: {
  frames: Frame[];
  teacher: boolean;
  openLine: string | null;
  /** «frame:<id>» или «line:<block>:<line>» */
  ringKey: string | null;
  setOpenLine: (k: string | null) => void;
  headingLevel: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <>
      {frames.map((f) => (
        <section
          key={f.block.id}
          id={`block-${f.block.id}`}
          aria-labelledby={`block-${f.block.id}-h`}
          className={cn(
            "absolute rounded-[14px] border border-seam bg-cloth-raised/60 transition-shadow duration-700",
            ringKey === `frame:${f.block.id}` ? "shadow-[0_0_0_3px_var(--ribbon)]" : "shadow-[0_0_0_3px_transparent]",
          )}
          style={{ left: f.x, top: f.y, width: f.w, height: f.h }}
        >
          <Heading id={`block-${f.block.id}-h`} className="num absolute bottom-full left-1 mb-2.5 text-small tracking-wide whitespace-nowrap text-paper-dim uppercase">
            {f.block.title}
          </Heading>
          <FrameBody
            block={f.block}
            teacher={teacher}
            openLine={openLine}
            ringLine={ringKey?.startsWith("line:") ? ringKey.slice(5) : null}
            setOpenLine={setOpenLine}
          />
        </section>
      ))}
    </>
  );
});
