"use client";

import { useEffect, useRef, useState } from "react";
import { isMaterialFile } from "@/lib/api/materials";

/** A drag that carries files from the OS (text, links and things dragged inside the page do not count) */
function carriesFiles(e: DragEvent): boolean {
  const dt = e.dataTransfer;
  if (!dt) return false;
  if (!Array.from(dt.types ?? []).includes("Files")) return false;
  // while dragging, the list of items shows only the kinds: when it is there and has no file, it is not a file drag
  const items = Array.from(dt.items ?? []);
  return items.length === 0 || items.some((i) => i.kind === "file");
}

/** The tab never opens a file that missed a target: while any drop target is mounted, the window refuses file drops */
let guards = 0;
const guardOver = (e: DragEvent) => {
  if (!carriesFiles(e)) return;
  // a target that took the drag has already called `preventDefault`: leave its effect alone
  if (!e.defaultPrevented && e.dataTransfer) e.dataTransfer.dropEffect = "none";
  e.preventDefault();
};
const guardDrop = (e: DragEvent) => {
  if (carriesFiles(e)) e.preventDefault();
};

function useWindowGuard() {
  useEffect(() => {
    if (guards++ === 0) {
      window.addEventListener("dragover", guardOver);
      window.addEventListener("drop", guardDrop);
    }
    return () => {
      if (--guards === 0) {
        window.removeEventListener("dragover", guardOver);
        window.removeEventListener("drop", guardDrop);
      }
    };
  }, []);
}

/** A drag that stopped sending `dragover` (cancelled somewhere we did not see) lets go of the overlay after this long */
const STALE_MS = 900;

type Options = {
  /** The element that takes the drop. It may be an ancestor of the component that owns the files (the whole chat, not only the input) */
  zone: HTMLElement | null;
  /**
   * Files that passed `accept` and how many did not. Called even when none passed, so the owner can say why.
   * The picker's own rules (limits, queues, messages) are the owner's business: this only brings the files.
   */
  onFiles: (files: File[], rejected: number) => void;
  /** `false`: no overlay, and a drop calls `onBlocked` (attaching is off right now) instead of `onFiles` */
  enabled?: boolean;
  onBlocked?: () => void;
  /** Default: what the material pickers offer (PDF, image, PowerPoint) */
  accept?: (file: File) => boolean;
};

/**
 * Drop files from the OS file manager on `zone`. `dragging` is true while such a drag is over the zone (nested elements do not
 * flicker it: enter and leave are counted). It ends on leave, on drop, on Escape and when the drag goes quiet. The overlay itself is
 * `DropOverlay`; the picker stays the accessible way to attach.
 */
export function useFileDrop({ zone, onFiles, enabled = true, onBlocked, accept = isMaterialFile }: Options) {
  const [dragging, setDragging] = useState(false);
  // the handlers are bound once and read the latest props from here
  const latest = useRef({ onFiles, enabled, onBlocked, accept });
  useEffect(() => {
    latest.current = { onFiles, enabled, onBlocked, accept };
  });
  useWindowGuard();

  useEffect(() => {
    const el = zone;
    if (!el) return;
    let depth = 0;
    let stale: ReturnType<typeof setTimeout> | undefined;
    const stop = () => {
      depth = 0;
      clearTimeout(stale);
      setDragging(false);
    };
    const onEnter = (e: DragEvent) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      depth++;
      if (latest.current.enabled) setDragging(true);
    };
    const onOver = (e: DragEvent) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      if (!latest.current.enabled) return;
      setDragging(true);
      clearTimeout(stale);
      stale = setTimeout(stop, STALE_MS);
    };
    const onLeave = (e: DragEvent) => {
      if (!carriesFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) stop();
    };
    const onDrop = (e: DragEvent) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      stop();
      const dropped = Array.from(e.dataTransfer?.files ?? []);
      const { enabled: on, onBlocked: blocked, onFiles: take, accept: ok } = latest.current;
      if (!on) return blocked?.();
      if (!dropped.length) return;
      const files = dropped.filter(ok);
      take(files, dropped.length - files.length);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && stop();

    el.addEventListener("dragenter", onEnter);
    el.addEventListener("dragover", onOver);
    el.addEventListener("dragleave", onLeave);
    el.addEventListener("drop", onDrop);
    window.addEventListener("keydown", onKey);
    window.addEventListener("dragend", stop);
    window.addEventListener("blur", stop);
    return () => {
      clearTimeout(stale);
      el.removeEventListener("dragenter", onEnter);
      el.removeEventListener("dragover", onOver);
      el.removeEventListener("dragleave", onLeave);
      el.removeEventListener("drop", onDrop);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("dragend", stop);
      window.removeEventListener("blur", stop);
    };
  }, [zone]);

  return { dragging };
}
