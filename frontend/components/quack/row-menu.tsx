"use client";

import { Ellipsis, LoaderCircle, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useErrorText } from "./live/error-text";
import { useMenu } from "./use-menu";

/** The copies of deleted data live this long in the backups (`copies_days` of the deletion routes) */
export const COPIES_DAYS = 15;

export type RowAction = {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Red: the item is erased, not just moved away */
  destructive?: boolean;
  /** The question the inline confirmation asks; the request starts only after «yes» */
  confirm: { text: string; yes: string };
  run: () => Promise<void>;
};

const ITEM =
  "caps flex min-h-10 w-full items-center gap-2.5 rounded-[2px] px-3 text-left text-paper transition-colors duration-[var(--dur-s)] ease-out [--caps-size:0.75rem] hover:bg-cloth-hover";
const PILL =
  "caps inline-flex h-10 items-center justify-center gap-2 rounded-pill border px-5 transition-colors duration-(--dur-s) ease-out disabled:opacity-60";

/**
 * A row of the left panel with a «⋯» menu: the button shows on hover and on keyboard focus (always on touch and
 * narrow screens), the right click opens the same menu. An action asks inline first; while the request runs the button
 * spins, a failure is written under the question. `children` is the row itself (a link or a button).
 */
export function RowMenu({ label, actions, children }: { label: string; actions: RowAction[]; children: React.ReactNode }) {
  const t = useT();
  const d = t.shell.del;
  const errorText = useErrorText();
  const uid = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<RowAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onMenuKey = useMenu(open, root, () => setOpen(false));
  const bare = actions.length === 0;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open]);

  useEffect(() => {
    if (asking) cancel.current?.focus();
  }, [asking]);

  const stopAsking = () => {
    setAsking(null);
    setError(null);
    trigger.current?.focus();
  };

  const confirm = async () => {
    if (!asking || busy) return;
    setBusy(true);
    setError(null);
    try {
      await asking.run();
      // the row goes away with the data; if it stays (the backend is still catching up), the question closes
      setAsking(null);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 503 ? d.unavailable : errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const ask = (action: RowAction) => {
    setOpen(false);
    setError(null);
    setAsking(action);
  };

  if (bare) return <>{children}</>;

  return (
    <div
      ref={root}
      className="group/row relative [&>:first-child]:pr-10"
      onContextMenu={(e) => {
        if (asking || (e.target as HTMLElement).closest("[role=menu]")) return;
        e.preventDefault();
        setOpen(true);
      }}
    >
      {children}
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${uid}-menu` : undefined}
        aria-label={d.menuFor(label)}
        title={d.menuFor(label)}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "absolute top-1 right-1 inline-flex size-8 items-center justify-center rounded-pill text-paper-dim transition-[opacity,colors] duration-[var(--dur-s)] ease-out hover:bg-cloth-raised hover:text-paper max-md:opacity-100 pointer-coarse:opacity-100 [&_svg]:size-4",
          open || asking ? "opacity-100" : "opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 focus-visible:opacity-100",
        )}
      >
        <Ellipsis aria-hidden />
      </button>
      {open && (
        <ul
          id={`${uid}-menu`}
          role="menu"
          aria-label={d.menuFor(label)}
          onKeyDown={onMenuKey}
          className="absolute top-9 right-1 z-30 w-max max-w-[min(18rem,calc(100vw-2rem))] min-w-44 rounded-[2px] border border-seam bg-cloth-raised p-1 shadow-[0_12px_32px_-16px_rgb(0_0_0/0.6)]"
        >
          {actions.map((a) => (
            <li key={a.id} role="none">
              <button role="menuitem" type="button" onClick={() => ask(a)} className={cn(ITEM, a.destructive && "text-st-confused")}>
                <a.icon aria-hidden className="size-[18px] shrink-0 stroke-[1.6]" />
                {a.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      {asking && (
        <div
          role="group"
          aria-label={d.confirmLabel}
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            e.preventDefault();
            e.stopPropagation();
            if (!busy) stopAsking();
          }}
          className="mx-3 my-1 rounded-[2px] border border-seam bg-cloth-raised p-3 text-small text-paper"
        >
          <p>{asking.confirm.text}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              aria-busy={busy}
              onClick={() => void confirm()}
              className={cn(PILL, asking.destructive ? "border-st-confused text-st-confused hover:bg-cloth-hover" : "border-seam text-paper hover:border-rule hover:bg-cloth-hover")}
            >
              {busy && <LoaderCircle aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />}
              {busy ? d.working : asking.confirm.yes}
            </button>
            <button ref={cancel} type="button" disabled={busy} onClick={stopAsking} className={cn(PILL, "border-seam text-paper-dim hover:border-rule hover:bg-cloth-hover hover:text-paper")}>
              {d.cancel}
            </button>
          </div>
          {error && (
            <p role="alert" className="mt-2 text-small text-st-confused">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
