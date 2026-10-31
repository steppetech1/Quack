"use client";

import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/** Shared bits of the stub screens: screens the backend already serves but that are not wired yet */

export const FIELD =
  "h-10 w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper placeholder:text-paper-dim hover:border-rule";
export const AREA =
  "min-h-24 w-full rounded-control border border-seam bg-cloth px-3 py-2 text-body text-paper placeholder:text-paper-dim hover:border-rule";
export const PRIMARY_BTN =
  "inline-flex h-10 shrink-0 items-center justify-center rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover disabled:opacity-50";
export const SECONDARY_BTN =
  "inline-flex h-10 shrink-0 items-center justify-center rounded-control border border-seam px-4 text-body font-semibold text-paper transition-colors hover:border-rule hover:bg-cloth-hover disabled:opacity-50";
export const DANGER_BTN =
  "inline-flex h-10 items-center rounded-control px-3 text-body font-semibold text-st-confused transition-colors hover:bg-cloth-hover";

/** "Stub" marker with the backend endpoints the screen will call once wired */
export function StubNote({ endpoints, className }: { endpoints: string[]; className?: string }) {
  const t = useT();
  return (
    <aside className={cn("rounded-[14px] border border-dashed border-seam px-4 py-3 text-small text-paper-dim", className)}>
      <p>
        <span className="mr-2 inline-flex items-center rounded-full border border-seam px-2 py-0.5 text-paper">{t.stubs.tag}</span>
        {t.stubs.pending}
      </p>
      <ul className="num mt-2 space-y-0.5">
        {endpoints.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
    </aside>
  );
}

/** Label + control + optional help, stacked */
export function Field({ id, label, help, children }: { id: string; label: string; help?: string; children: React.ReactNode }) {
  return (
    <div className="mt-5">
      <label htmlFor={id} className="mb-1.5 block text-body font-medium text-paper">
        {label}
      </label>
      {children}
      {help && <p className="mt-1.5 text-small text-paper-dim">{help}</p>}
    </div>
  );
}

/** Page title + short lead, same rhythm as the topic and chapter pages */
export function StubHeader({ title, body }: { title: string; body?: string }) {
  return (
    <header className="mt-2">
      <h1 className="font-serif text-[28px] leading-tight font-bold text-paper md:text-[32px]">{title}</h1>
      {body && <p className="mt-2 max-w-[60ch] text-body text-paper-dim">{body}</p>}
    </header>
  );
}
