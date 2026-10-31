"use client";

import katex from "katex";
import { useLayoutEffect, useRef } from "react";
import { INK_VAR, smoothPath, strokeBox, type BoardItem } from "@/lib/canvas-board";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

const tex = (src: string) => katex.renderToString(src, { throwOnError: false, output: "htmlAndMathml" });

/** Поле для правки текста или формулы: Enter и потеря фокуса сохраняют */
function EditBox({ initial, formula, onDone }: { initial: string; formula: boolean; onDone: (v: string) => void }) {
  const tx = useT();
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  useLayoutEffect(() => {
    ref.current?.focus({ preventScroll: true });
    ref.current?.select();
  }, []);
  const finish = (v: string) => {
    if (done.current) return;
    done.current = true;
    onDone(v);
  };
  return (
    <textarea
      ref={ref}
      defaultValue={initial}
      rows={1}
      aria-label={formula ? tx.board.items.formulaAria : tx.board.items.textAria}
      placeholder={formula ? tx.board.items.formulaPlaceholder : tx.board.items.textPlaceholder}
      spellCheck={false}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          finish(e.currentTarget.value);
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(e.currentTarget.value);
        }
        e.stopPropagation();
      }}
      onBlur={(e) => finish(e.currentTarget.value)}
      className={cn(
        "field-sizing-content min-w-[12ch] resize-none rounded-[10px] border border-ribbon/70 bg-cloth px-2 py-1 text-paper outline-none placeholder:text-paper-dim",
        formula ? "num text-[18px]" : "text-[20px] leading-tight",
      )}
    />
  );
}

const HANDLES = ["-left-1 -top-1", "-right-1 -top-1", "-bottom-1 -left-1", "-right-1 -bottom-1"];

/** Элемент студента на доске. Размеры рамки выделения не зависят от зума: --inv = 1/k задаёт слой мира */
export function ItemView({
  item,
  selected,
  editing,
  onSelect,
  onEdit,
}: {
  item: BoardItem;
  selected: boolean;
  editing: boolean;
  onSelect: (id: string) => void;
  onEdit: (id: string, v: string | null) => void;
}) {
  const tx = useT();
  const label =
    item.kind === "stroke"
      ? item.marker
        ? tx.board.items.marker
        : tx.board.items.pen
      : item.kind === "text"
        ? tx.board.items.text(item.text)
        : item.kind === "formula"
          ? tx.board.items.formula(item.tex)
          : tx.board.items.image;

  const sel = selected && !editing;
  const common = {
    "data-item": item.id,
    role: "group" as const,
    "aria-label": label,
    tabIndex: editing ? -1 : 0,
    onFocus: () => onSelect(item.id),
  };
  const frame = cn(
    "absolute outline-none focus-visible:outline-0",
    sel && "outline-[calc(1.5px*var(--inv))] outline-ribbon [outline-offset:calc(3px*var(--inv))] outline-solid",
  );
  const handles = sel
    ? HANDLES.map((h) => (
        <span
          key={h}
          aria-hidden
          className={cn("absolute rounded-[2px] border border-ribbon bg-cloth", h)}
          style={{ width: "calc(8px*var(--inv))", height: "calc(8px*var(--inv))", borderWidth: "calc(1.5px*var(--inv))", margin: "calc(-1px*var(--inv))" }}
        />
      ))
    : null;

  if (item.kind === "stroke") {
    const b = strokeBox(item.pts, item.width / 2 + 3);
    const d = smoothPath(item.pts);
    return (
      <div {...common} className={cn(frame, "pointer-events-none")} style={{ left: b.x, top: b.y, width: b.w, height: b.h }}>
        <svg aria-hidden width={b.w} height={b.h} viewBox={`${b.x} ${b.y} ${b.w} ${b.h}`} className="absolute inset-0 overflow-visible">
          <path d={d} fill="none" stroke="transparent" strokeWidth={Math.max(item.width, 16)} strokeLinecap="round" pointerEvents="stroke" />
          <path d={d} fill="none" stroke={INK_VAR[item.color]} strokeOpacity={item.marker ? 0.35 : 1} strokeWidth={item.width} strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {handles}
      </div>
    );
  }
  if (item.kind === "image") {
    return (
      <div {...common} className={cn(frame, "cursor-move")} style={{ left: item.x, top: item.y, width: item.w, height: item.h }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={item.src} alt="" draggable={false} className="size-full rounded-[10px] object-cover" />
        {handles}
      </div>
    );
  }
  const body =
    item.kind === "text" ? (
      <p className="px-2 py-1 text-[20px] leading-tight whitespace-pre text-paper">{item.text}</p>
    ) : (
      <div className="px-2 py-1 text-[22px] text-paper" dangerouslySetInnerHTML={{ __html: tex(item.tex) }} />
    );
  return (
    <div {...common} className={cn(frame, !editing && "cursor-move")} style={{ left: item.x, top: item.y }}>
      {editing ? (
        <EditBox initial={item.kind === "text" ? item.text : item.tex} formula={item.kind === "formula"} onDone={(v) => onEdit(item.id, v)} />
      ) : (
        body
      )}
      {handles}
    </div>
  );
}
