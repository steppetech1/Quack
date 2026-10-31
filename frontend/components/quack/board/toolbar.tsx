"use client";

import { Eraser, Hand, Highlighter, ImagePlus, Lasso, MousePointer2, PenTool, Redo2, Sigma, Type, Undo2 } from "lucide-react";
import { INK_VAR, type InkColor } from "@/lib/canvas-board";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

export type Tool = "select" | "hand" | "pen" | "marker" | "eraser" | "text" | "formula";

const BTN =
  "group relative inline-flex size-11 shrink-0 items-center justify-center rounded-[11px] border border-transparent text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-paper-dim [&>svg]:size-[19px] [&>svg]:stroke-[1.6]";
const ON = "border-paper-dim/60 bg-cloth-hover text-paper";

function Tip({ label, keys }: { label: string; keys?: string }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-full z-30 ml-2.5 hidden items-center gap-2 rounded-[10px] border border-seam bg-cloth-deep px-2.5 py-1.5 text-small whitespace-nowrap text-paper opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 md:flex"
    >
      {label}
      {keys && <kbd className="num rounded-md border border-seam px-1.5 text-[12px] text-paper-dim">{keys}</kbd>}
    </span>
  );
}

type ToolId = Tool | "lasso" | "image";
type ToolDef = { id: ToolId; keys: string; icon: React.ComponentType };
const TOOLS: ToolDef[] = [
  { id: "select", keys: "V", icon: MousePointer2 },
  { id: "hand", keys: "H", icon: Hand },
  { id: "pen", keys: "P", icon: PenTool },
  { id: "marker", keys: "M", icon: Highlighter },
  { id: "eraser", keys: "E", icon: Eraser },
  { id: "text", keys: "T", icon: Type },
  { id: "formula", keys: "F", icon: Sigma },
  { id: "image", keys: "I", icon: ImagePlus },
  { id: "lasso", keys: "", icon: Lasso },
];

export function Toolbar({
  tool,
  setTool,
  ink,
  setInk,
  onImage,
  onLasso,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
}: {
  tool: Tool;
  setTool: (t: Tool) => void;
  ink: InkColor;
  setInk: (c: InkColor) => void;
  onImage: () => void;
  onLasso: () => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}) {
  const tx = useT();
  return (
    <div
      role="toolbar"
      aria-label={tx.board.toolbar}
      aria-orientation="vertical"
      data-ui
      className={cn(
        "z-20 flex gap-1 rounded-[14px] border border-seam bg-cloth-raised p-1 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]",
        "max-md:max-w-full max-md:self-start max-md:overflow-x-auto",
        "md:absolute md:top-1/2 md:left-3 md:-translate-y-1/2 md:flex-col md:overflow-visible",
      )}
    >
      {TOOLS.map(({ id, keys, icon: Icon }) => {
        const label = tx.board.tools[id];
        const active = id !== "image" && id !== "lasso" && tool === id;
        const press = () => (id === "image" ? onImage() : id === "lasso" ? onLasso() : setTool(id));
        return (
          <div key={id} className="relative flex md:block">
            {id === "lasso" && <span aria-hidden className="mx-0.5 my-1 hidden h-px bg-seam md:hidden" />}
            <button
              type="button"
              aria-label={keys ? `${label} (${keys})` : label}
              aria-keyshortcuts={keys || undefined}
              aria-pressed={id === "image" || id === "lasso" ? undefined : active}
              onClick={press}
              className={cn(BTN, active && ON)}
            >
              <Icon />
              <Tip label={label} keys={keys} />
            </button>
            {id === "pen" && (tool === "pen" || tool === "marker") && (
              <div
                role="group"
                aria-label={tx.board.penColor}
                className="flex gap-0.5 max-md:ml-1 max-md:items-center md:absolute md:top-0 md:left-full md:ml-3 md:rounded-[14px] md:border md:border-seam md:bg-cloth-raised md:p-1 md:shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]"
              >
                {(Object.keys(INK_VAR) as InkColor[]).map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-label={tx.board.color(tx.board.ink[c].toLowerCase())}
                    aria-pressed={ink === c}
                    onClick={() => setInk(c)}
                    className={cn("inline-flex size-10 items-center justify-center rounded-[11px] border border-transparent hover:bg-cloth-hover", ink === c && "border-paper-dim/60 bg-cloth-hover")}
                  >
                    <span aria-hidden className="size-4.5 rounded-full border border-black/30" style={{ background: INK_VAR[c] }} />
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
      <span aria-hidden className="mx-1 my-1 h-px w-auto bg-seam max-md:h-auto max-md:w-px max-md:self-stretch" />
      <button type="button" aria-label={tx.board.undoLabel} aria-keyshortcuts="Control+Z" disabled={!canUndo} onClick={onUndo} className={BTN}>
        <Undo2 />
        <Tip label={tx.board.undo} keys="Ctrl+Z" />
      </button>
      <button type="button" aria-label={tx.board.redoLabel} aria-keyshortcuts="Control+Shift+Z" disabled={!canRedo} onClick={onRedo} className={BTN}>
        <Redo2 />
        <Tip label={tx.board.redo} keys="Ctrl+Shift+Z" />
      </button>
    </div>
  );
}
