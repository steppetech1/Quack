"use client";

import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

/** Shared bits of the stub screens: screens the backend already serves but that are not wired yet */

export const FIELD =
  "h-10 w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper transition-colors duration-(--dur-s) ease-out placeholder:text-paper-dim hover:border-rule focus-visible:border-ribbon";
export const AREA =
  "min-h-24 w-full rounded-control border border-seam bg-cloth px-3 py-2 text-body text-paper transition-colors duration-(--dur-s) ease-out placeholder:text-paper-dim hover:border-rule focus-visible:border-ribbon";
/** Длинная пилюля ввода на экране «Новый предмет»: плейсхолдер тусклый и ненавязчивый до ввода */
export const PILL_FIELD =
  "h-14 w-full rounded-pill border border-seam bg-cloth px-6 text-body text-paper transition-colors duration-(--dur-s) ease-out placeholder:text-paper-dim/70 hover:border-rule focus-visible:border-ribbon";
export const PRIMARY_BTN =
  "caps inline-flex h-10 shrink-0 items-center justify-center rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover disabled:opacity-50";
export const SECONDARY_BTN =
  "caps inline-flex h-10 shrink-0 items-center justify-center rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon disabled:opacity-50";
export const DANGER_BTN =
  "caps inline-flex h-10 items-center rounded-pill px-4 text-st-confused transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover";

/** "Stub" marker with the backend endpoints the screen will call once wired */
export function StubNote({ endpoints, className }: { endpoints: string[]; className?: string }) {
  const t = useT();
  return (
    <aside className={cn("rounded-[2px] border border-dashed border-seam px-4 py-3 text-small text-paper-dim", className)}>
      <p>
        <span className="caps mr-2 inline-flex items-center rounded-pill border border-seam px-2.5 py-0.5 text-paper">{t.stubs.tag}</span>
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
      <label htmlFor={id} className="caps mb-1.5 block text-paper">
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
      <h1 className="display-caps text-[28px] text-paper md:text-[32px]">{title}</h1>
      {body && <p className="mt-2 max-w-[60ch] text-body text-paper-dim">{body}</p>}
    </header>
  );
}
