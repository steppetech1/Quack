"use client";

import { FileText, Plus, X } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState } from "react";
import { MAX_CONTEXT_FILES } from "@/lib/api/domain-uploads";
import { MATERIAL_ACCEPT } from "@/lib/api/materials";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { PRIMARY_BTN, SECONDARY_BTN } from "../stubs/stub-parts";

/** What the learner may write about a subject (`CreateDomainRequest.about`) */
export const ABOUT_MAX = 500;

export type NewDomainValues = { name: string; about: string; files: File[] };

/** The same name for the soft hint: case, spaces around and inside do not matter */
const same = (s: string) => s.normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, "");

/** The star of a required field. A real button, so the tooltip opens on focus and by keyboard, not on hover only */
function RequiredStar() {
  const t = useT().live;
  const tip = useId();
  return (
    <span className="group relative -mx-2.5 inline-flex">
      <button
        type="button"
        aria-label={t.domainRequiredAria}
        aria-describedby={tip}
        onClick={(e) => e.currentTarget.focus()}
        className="-my-3 inline-flex size-10 items-center justify-center rounded-pill text-ribbon focus-visible:outline-2 focus-visible:outline-ribbon"
      >
        <span aria-hidden className="text-body leading-none">
          *
        </span>
      </button>
      <span
        id={tip}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-0.5 -translate-x-1/2 rounded-[2px] border border-seam bg-cloth-raised px-2 py-1 text-small whitespace-nowrap text-paper opacity-0 transition-opacity duration-(--dur-s) group-focus-within:opacity-100 group-hover:opacity-100 motion-reduce:transition-none"
      >
        {t.domainRequired}
      </span>
    </span>
  );
}

/**
 * New subject, one card: it starts as a single field (the name) and opens downward when the learner clicks or tabs into it:
 * about (optional, stored with the subject), context files (up to `MAX_CONTEXT_FILES`), «Create». «Reset» at the bottom
 * brings the screen back to the empty, closed card. A name the learner already has only gets a soft line with a link, nothing
 * is blocked. What «Create» does is the parent's business (`onCreate`): it makes the subject and opens its page.
 */
export function DomainCreateCard({
  existing,
  onCreate,
  busy,
  error,
  onClearError,
}: {
  existing: { id: string; title: string }[];
  onCreate: (v: NewDomainValues) => void;
  busy: boolean;
  error: string | null;
  onClearError: () => void;
}) {
  const t = useT().live;
  const id = useId();
  const picker = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [tooMany, setTooMany] = useState(false);

  const dirty = name !== "" || about !== "" || files.length > 0;
  const twin = name.trim() ? existing.find((d) => same(d.title) === same(name)) : undefined;
  const ready = name.trim().length > 0 && !busy;

  const addFiles = (picked: File[]) => {
    const fresh = picked.filter((f) => !files.some((g) => g.name === f.name && g.size === f.size && g.lastModified === f.lastModified));
    const room = Math.max(0, MAX_CONTEXT_FILES - files.length);
    setTooMany(fresh.length > room);
    if (room > 0 && fresh.length) setFiles((all) => [...all, ...fresh.slice(0, room)]);
  };

  const reset = () => {
    setName("");
    setAbout("");
    setFiles([]);
    setTooMany(false);
    setOpen(false);
    onClearError();
    if (picker.current) picker.current.value = "";
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) onCreate({ name: name.trim(), about: about.trim(), files });
      }}
      className="relative z-10 w-full max-w-[520px] text-left"
    >
      <div className={cn("rounded-[6px] border bg-cloth transition-colors duration-(--dur-s) ease-out", open ? "border-rule" : "border-seam hover:border-rule", "focus-within:border-ribbon")}>
        <div className="px-4 pt-3 pb-3">
          <div className="flex items-center">
            <label htmlFor={`${id}-name`} className="caps text-paper">
              {t.domainCardName}
            </label>
            <RequiredStar />
          </div>
          <input
            id={`${id}-name`}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              onClearError();
            }}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            required
            aria-required="true"
            maxLength={200}
            autoComplete="off"
            placeholder={t.domainCardNamePh}
            className="mt-1 h-11 w-full bg-transparent text-body text-paper placeholder:text-paper-dim/70 focus-visible:outline-none"
          />
          {twin && (
            <p role="status" className="text-small text-paper-dim">
              {t.domainDupPre(twin.title)}{" "}
              <Link href={`/?domain=${twin.id}`} className="inline-flex min-h-10 items-center text-ribbon underline-offset-2 hover:underline">
                {t.domainDupOpen}
              </Link>
            </p>
          )}
        </div>

        <div
          inert={!open}
          className={cn("grid transition-[grid-template-rows] duration-(--dur-m) ease-(--ease-out) motion-reduce:transition-none", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="flex flex-col gap-4 border-t border-seam-soft px-4 pt-4 pb-3">
              <div>
                <label htmlFor={`${id}-about`} className="caps flex items-baseline gap-2 text-paper">
                  {t.domainAbout}
                  <span className="text-small font-normal text-paper-dim normal-case">{t.domainAboutOptional}</span>
                </label>
                <textarea
                  id={`${id}-about`}
                  value={about}
                  onChange={(e) => setAbout(e.target.value)}
                  maxLength={ABOUT_MAX}
                  rows={3}
                  placeholder={t.domainAboutPh}
                  className="mt-1.5 min-h-24 w-full resize-y rounded-control border border-seam bg-cloth-deep px-3 py-2 text-body text-paper transition-colors duration-(--dur-s) ease-out placeholder:text-paper-dim/70 hover:border-rule focus-visible:border-ribbon"
                />
                <p aria-hidden className={cn("num mt-1 text-right text-small", about.length > ABOUT_MAX - 50 ? "text-paper" : "text-paper-dim")}>
                  {about.length} / {ABOUT_MAX}
                </p>
              </div>

              <div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="caps text-paper">{t.domainContext}</span>
                  <input
                    ref={picker}
                    type="file"
                    multiple
                    accept={MATERIAL_ACCEPT}
                    tabIndex={-1}
                    aria-hidden
                    className="sr-only"
                    onChange={(e) => {
                      addFiles([...(e.target.files ?? [])]);
                      e.target.value = "";
                    }}
                  />
                  <button type="button" onClick={() => picker.current?.click()} className={cn(SECONDARY_BTN, "gap-1.5 px-4")}>
                    <Plus aria-hidden className="size-4" />
                    {t.domainContextAdd}
                  </button>
                  <span className="num text-small text-paper-dim">
                    {files.length} / {MAX_CONTEXT_FILES}
                  </span>
                </div>
                <p className="mt-1.5 text-small text-paper-dim">{t.domainContextHint}</p>
                {tooMany && (
                  <p role="status" className="mt-1.5 text-small text-st-confused">
                    {t.domainFilesMax(MAX_CONTEXT_FILES)}
                  </p>
                )}
                {files.length > 0 && (
                  <ul aria-label={t.domainFilesLabel} className="mt-2 flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
                    {files.map((f, i) => (
                      <li key={`${f.name}-${f.size}-${f.lastModified}-${i}`} className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-pill border border-seam bg-cloth-deep py-1 pr-1 pl-2.5 text-small text-paper-dim">
                        <FileText aria-hidden className="size-3.5 shrink-0" />
                        <span className="min-w-0 truncate text-paper">{f.name}</span>
                        <button
                          type="button"
                          aria-label={t.domainFilesRemove(f.name)}
                          onClick={() => {
                            setFiles((all) => all.filter((_, j) => j !== i));
                            setTooMany(false);
                          }}
                          className="-my-2.5 inline-flex size-10 shrink-0 items-center justify-center rounded-pill hover:bg-cloth-hover hover:text-paper focus-visible:outline-2 focus-visible:outline-ribbon"
                        >
                          <X aria-hidden className="size-3.5" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {error && (
                <p role="alert" className="text-small text-st-confused">
                  {error}
                </p>
              )}
              <div className="flex justify-end">
                <button type="submit" disabled={!ready} className={PRIMARY_BTN}>
                  {busy ? t.domainCreating : t.domainCreateBtn}
                </button>
              </div>
            </div>

            {dirty && (
              <div className="flex min-h-10 items-center border-t border-seam-soft px-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={reset}
                  className="caps inline-flex h-10 items-center rounded-pill px-3 text-small text-st-confused transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover disabled:opacity-50"
                >
                  {t.domainReset}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
