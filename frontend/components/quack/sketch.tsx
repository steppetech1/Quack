import type { Canvas, CanvasSketch } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { Hand, hashSeed } from "./handwriting";

/** «Почерк»: ломаная из коротких волн, одинаковая при каждом рендере */
function scribble(x: number, y: number, w: number, seed: number, amp = 3.2) {
  let d = `M${x} ${y}`;
  const step = 5;
  for (let cx = x, i = 0; cx < x + w; cx += step, i++) {
    const up = ((i * 7 + seed * 5) % 5) - 2;
    const sign = i % 2 === 0 ? -1 : 1;
    d += ` q${step / 2} ${sign * (amp + up * 0.6)} ${step} ${((i + seed) % 3) - 1 === 0 ? 0.4 : -0.3}`;
  }
  return d;
}

/** Эскиз канваса: набросок формулы от руки, без настоящего содержимого */
export function Sketch({ kind, className }: { kind: CanvasSketch; className?: string }) {
  return (
    <svg viewBox="0 0 168 112" aria-hidden className={className} fill="none" stroke="var(--duck)" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" opacity={0.85}>
      {kind === "ratio" && (
        <>
          <path d={scribble(16, 28, 54, 1)} />
          <path d="M13 41 Q44 38 76 41" />
          <path d={scribble(16, 56, 54, 3)} />
          <path d="M84 44 L106 44 M99 38 L106 44 L99 50" />
          <path d={scribble(114, 36, 14, 2, 2.4)} />
          <path d="M113 43 L131 43" />
          <path d={scribble(114, 54, 14, 4, 2.4)} />
          <path d={scribble(16, 84, 70, 5)} />
          <path d="M96 84 L104 80 L96 76" />
          <path d={scribble(112, 84, 28, 2, 2.4)} />
        </>
      )}
      {kind === "plot" && (
        <>
          <path d="M20 94 L20 14 M20 94 L152 94" />
          <path d="M20 40 Q60 44 86 58 Q112 70 150 72" />
          <path d="M20 66 L152 66" strokeDasharray="3 5" />
          <circle cx={36} cy={42} r={1.8} fill="var(--duck)" />
          <circle cx={62} cy={47} r={1.8} fill="var(--duck)" />
          <circle cx={92} cy={61} r={1.8} fill="var(--duck)" />
          <circle cx={128} cy={70} r={1.8} fill="var(--duck)" />
          <path d={scribble(28, 22, 22, 3, 2.4)} />
        </>
      )}
      {kind === "estimate" && (
        <>
          <path d={scribble(14, 26, 44, 2)} />
          <path d="M66 22 L76 26 L66 30" />
          <path d={scribble(84, 26, 44, 4)} />
          <path d={scribble(14, 52, 44, 1)} />
          <path d="M66 48 L76 52 L66 56" />
          <path d={scribble(84, 52, 30, 3)} />
          <path d={scribble(14, 80, 60, 5)} />
          <path d="M14 94 Q84 90 152 94" />
        </>
      )}
    </svg>
  );
}

/** Миниатюра канваса: первые строки настоящего решения, обрезанные по краю листа */
export function CanvasThumb({ canvas, className }: { canvas: Canvas; className?: string }) {
  const task = canvas.blocks.find((b) => b.kind === "task");
  if (!task || task.kind !== "task") return null;
  return (
    <div
      aria-hidden
      className={cn(
        "relative max-h-[156px] min-h-[84px] overflow-hidden rounded-[14px] bg-cloth-raised px-3 pt-2.5 pb-2 transition-colors group-hover:bg-cloth-hover",
        className,
      )}
    >
      <p className="line-clamp-2 text-[10px] leading-[1.25] text-paper-dim">{task.brief}</p>
      <div className="mt-1.5 flex flex-col">
        {task.lines.slice(0, 5).map((l) => (
          <p key={l.id} className="truncate text-[14.5px] leading-[18px] text-paper/85">
            <Hand text={l.hand} seed={hashSeed(canvas.id + l.id)} tilt={0.5} />
          </p>
        ))}
      </div>
    </div>
  );
}
