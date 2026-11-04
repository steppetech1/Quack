"use client";

import { EASE_OUT } from "@/lib/motion";
import { cn } from "@/lib/utils";

type Props = {
  open: boolean;
  onClick: () => void;
  labelOpen: string;
  labelClose: string;
  className?: string;
};

const LINE_TRANSITION = `transform 0.6s ${EASE_OUT}`;
const LABEL_TRANSITION = `opacity 0.3s ${EASE_OUT}, transform 0.3s ${EASE_OUT}`;

/** Кнопка меню: две линии → крестик, подпись меняется с короткой анимацией */
export function MenuToggle({ open, onClick, labelOpen, labelClose, className }: Props) {
  const label = open ? labelClose : labelOpen;

  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      aria-label={label}
      className={cn(
        "inline-flex min-h-10 items-center gap-2.5 text-paper-dim transition-colors hover:text-paper",
        className,
      )}
    >
      <span className="relative block size-6 shrink-0" aria-hidden="true">
        <span
          className="absolute inset-x-0 top-1/2 -mt-px h-0.5 rounded-full bg-current"
          style={{
            transition: LINE_TRANSITION,
            transform: open ? "translateY(0) rotate(45deg)" : "translateY(-5px) rotate(0deg)",
          }}
        />
        <span
          className="absolute inset-x-0 top-1/2 -mt-px h-0.5 rounded-full bg-current"
          style={{
            transition: LINE_TRANSITION,
            transform: open ? "translateY(0) rotate(-45deg)" : "translateY(5px) rotate(0deg)",
          }}
        />
      </span>

      {/* две подписи в одной ячейке грида: ширина по самой длинной, без прыжков */}
      <span
        className="relative grid text-[11px] leading-none tracking-[0.08em] uppercase"
        aria-hidden="true"
      >
        <span
          className="col-start-1 row-start-1"
          style={{
            transition: LABEL_TRANSITION,
            opacity: open ? 0 : 1,
            transform: open ? "translateY(-6px)" : "translateY(0)",
          }}
        >
          {labelOpen}
        </span>
        <span
          className="col-start-1 row-start-1"
          style={{
            transition: LABEL_TRANSITION,
            opacity: open ? 1 : 0,
            transform: open ? "translateY(0)" : "translateY(6px)",
          }}
        >
          {labelClose}
        </span>
      </span>
    </button>
  );
}
