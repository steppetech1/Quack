"use client";

import { Paperclip } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/**
 * A polite line for screen readers after a drop («files attached: 2»). Put `region` somewhere in the target and call `say(text)`;
 * the text goes away by itself so the same message can be said again.
 */
export function useDropNote() {
  const [text, setText] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const say = useCallback((next: string) => {
    clearTimeout(timer.current);
    setText(next);
    timer.current = setTimeout(() => setText(""), 4000);
  }, []);
  const region = (
    <span role="status" className="sr-only">
      {text}
    </span>
  );
  return { say, region };
}

/**
 * Shown over a drop target while files are dragged onto it (`useFileDrop`): a thin amber dashed outline and a short line.
 * It lies over the target (`absolute`, the target must be `relative`) and takes no clicks, so nothing moves. For screen readers it is
 * hidden: the picker stays the way to attach, and `useDropNote` says what was attached. `container` puts it into another element
 * (the zone is an ancestor of the component that owns the drop), which must be `relative` too.
 */
export function DropOverlay({ active, container, className }: { active: boolean; container?: HTMLElement | null; className?: string }) {
  const t = useT().composer;
  if (!active) return null;
  const node = (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-0 z-40 bg-cloth/85 transition-opacity duration-(--dur-s) ease-(--ease-out) starting:opacity-0 motion-reduce:transition-none",
        className,
      )}
    >
      <div className="absolute inset-1.5 flex items-center justify-center gap-2 rounded-[6px] border border-dashed border-ribbon px-3 text-center">
        <Paperclip className="size-4 shrink-0 text-ribbon" />
        <span className="caps text-ribbon">{t.dropHere}</span>
      </div>
    </div>
  );
  return container ? createPortal(node, container) : node;
}
