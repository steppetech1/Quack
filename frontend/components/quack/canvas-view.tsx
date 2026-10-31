"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import { MOCK_TODAY } from "@/lib/mock";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { Board } from "./board/board";

const clean = (s: string) => s.replace(/­/g, "");
const BACK_CLS = "inline-flex size-10 shrink-0 items-center justify-center rounded-control text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper [&_svg]:size-[18px]";
const LINK_CLS = "relative text-paper-dim underline decoration-seam underline-offset-4 transition-colors after:absolute after:-inset-y-3 after:inset-x-0 hover:text-paper hover:decoration-paper-dim";

/** Доска существующего канваса: на весь экран, без боковых панелей */
export function CanvasView({ id }: { id: string }) {
  const tx = useT();
  const { fullDate } = useFmt();
  const { getCanvas } = useMocks();
  const { domain } = useShell();
  const canvas = getCanvas(id);
  const blockParam = useSearchParams().get("block");
  if (!canvas) return null;

  return (
    <Board
      canvas={canvas}
      canvasId={canvas.id}
      mode="edit"
      focusBlock={blockParam}
      topLeft={
        <>
          <Link href={`/?view=canvases&domain=${canvas.domainId}`} aria-label={tx.canvases.backToList} className={BACK_CLS}>
            <ArrowLeft aria-hidden />
          </Link>
          <div className="min-w-0 pr-3 pl-1">
            <h1 className="truncate font-serif text-body leading-tight font-bold text-paper">{canvas.title}</h1>
            <p className="num mt-0.5 hidden items-center gap-x-1.5 truncate text-[12px] whitespace-nowrap text-paper-dim sm:flex">
              <span>{fullDate(canvas.date)}</span>
              <span aria-hidden>·</span>
              <span className="flex gap-x-1">
                {canvas.topics.map((tid, i) => {
                  const t = domain.topics.find((x) => x.id === tid);
                  return (
                    <span key={tid}>
                      <Link href={`/topic/${tid}`} className={cn(LINK_CLS)}>
                        {t ? clean(t.title) : tid}
                      </Link>
                      {i < canvas.topics.length - 1 && <span aria-hidden>,</span>}
                    </span>
                  );
                })}
              </span>
              <span aria-hidden>·</span>
              <span className={canvas.solved === "no" ? undefined : "text-st-solid"}>{tx.solved[canvas.solved]}</span>
            </p>
          </div>
        </>
      }
    />
  );
}

/** Пустая доска: название правится на месте */
export function NewCanvasView() {
  const tx = useT();
  const { fullDate } = useFmt();
  const [title, setTitle] = useState("");
  return (
    <Board
      canvasId="new"
      mode="edit"
      topLeft={
        <>
          <Link href="/?view=canvases" aria-label={tx.canvases.backToList} className={BACK_CLS}>
            <ArrowLeft aria-hidden />
          </Link>
          <div className="min-w-0 pr-3 pl-1">
            <h1 className="sr-only">{tx.canvases.newCanvas}</h1>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label={tx.canvases.nameLabel}
              placeholder={tx.canvases.untitled}
              maxLength={80}
              className="h-6 w-full min-w-0 bg-transparent font-serif text-body font-bold text-paper outline-none placeholder:text-paper-dim focus-visible:outline-2 focus-visible:outline-ribbon"
            />
            <p className="num mt-0.5 hidden truncate text-[12px] text-paper-dim sm:block">
              {tx.canvases.draft(fullDate(MOCK_TODAY))}
            </p>
          </div>
        </>
      }
    />
  );
}
