"use client";

import { ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { API_MODE, get, type S } from "@/lib/api/client";
import { MATERIAL_POLL_LIMIT, MATERIAL_POLL_MS, materialState, type MaterialState } from "@/lib/api/materials";
import { useMocks, useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { PILL } from "../page-parts";
import { DocScroll } from "./doc-scroll";
import type { WorkspaceDoc } from "./workspace-context";

type View = {
  title: string | null;
  mime: string;
  state: MaterialState;
  pagesCount: number | null;
  pages: { page: number; text: string }[];
  /** the original file (a link of the file storage), when the backend gave one */
  fileUrl: string | null;
};

type Loaded = { id: string; view: View | null; failed: boolean };

/**
 * The file as the backend stores it: `GET /v1/materials/{id}` carries the text of every page or slide in order. While the
 * file is still being read the request is repeated (as the composer does for a new file). Mock mode reads the files of
 * the mock session. The backend tells no size of the file, so none is shown.
 */
function useMaterial(id: string): { view: View | null; failed: boolean; loading: boolean } {
  const mock = useMocks().workspace.materials[id];
  const [got, setGot] = useState<Loaded | null>(null);

  useEffect(() => {
    if (!API_MODE) return;
    let off = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = (tries: number) => {
      get<S["MaterialDetailView"]>(`/v1/materials/${id}`)
        .then((d) => {
          if (off) return;
          const state = materialState(d.material.status);
          setGot({
            id,
            failed: false,
            view: {
              title: d.material.title,
              mime: d.material.mime,
              state,
              pagesCount: d.material.pages_count,
              pages: [...d.pages].sort((a, b) => a.page - b.page).map((p) => ({ page: p.page, text: p.text })),
              fileUrl: /^https?:\/\//.test(d.file_url) ? d.file_url : null,
            },
          });
          if (state === "processing" && tries + 1 < MATERIAL_POLL_LIMIT) timer = setTimeout(() => ask(tries + 1), MATERIAL_POLL_MS);
        })
        .catch(() => {
          if (!off) setGot((cur) => (cur?.id === id && cur.view ? cur : { id, view: null, failed: true }));
        });
    };
    ask(0);
    return () => {
      off = true;
      clearTimeout(timer);
    };
  }, [id]);

  if (!API_MODE) {
    return {
      loading: false,
      failed: !mock,
      view: mock ? { title: mock.title, mime: mock.mime, state: "ready", pagesCount: mock.pages.length, pages: mock.pages, fileUrl: null } : null,
    };
  }
  const mine = got?.id === id ? got : null;
  return { view: mine?.view ?? null, failed: mine?.failed ?? false, loading: !mine };
}

const isSlides = (mime: string) => /presentation|powerpoint|pptx/i.test(mime);

/** First paragraph of a page as a heading, when it is one short line without a full stop at the end */
function splitHeading(text: string): { heading: string | null; body: string } {
  const [first, ...rest] = text.trim().split(/\n\s*\n/);
  const line = first?.trim() ?? "";
  if (rest.length && line && !line.includes("\n") && line.length <= 70 && !/[.,;:!?]$/.test(line)) return { heading: line, body: rest.join("\n\n") };
  return { heading: null, body: text.trim() };
}

export function MaterialDoc({ doc }: { doc: Extract<WorkspaceDoc, { kind: "material" }> }) {
  const tx = useT();
  const m = tx.workspace.material;
  const { view, failed, loading } = useMaterial(doc.id);

  if (loading) return <DocScroll><p role="status" className="text-paper-dim">{m.loading}</p></DocScroll>;
  if (!view) return <DocScroll><p role="alert" className="text-st-confused">{failed ? m.loadFailed : m.noPages}</p></DocScroll>;

  const slides = isSlides(view.mime);
  const count = view.pagesCount ?? view.pages.length;
  const kind = view.mime.startsWith("image/") ? m.image : slides ? "PPTX" : /pdf/i.test(view.mime) ? "PDF" : tx.workspace.kind.material;
  return (
    <DocScroll>
      <h2 className="display-caps text-paper [overflow-wrap:anywhere]">{view.title ?? doc.title}</h2>
      <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-small text-paper-dim">
        <span className={PILL}>{kind}</span>
        {view.state === "ready" && count > 0 && <span>{slides ? m.slides(count) : m.pages(count)}</span>}
        <span className={cn(PILL, view.state === "failed" && "border-st-confused/60 text-st-confused")}>{m.status[view.state]}</span>
        {view.fileUrl && (
          <a
            href={view.fileUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="relative inline-flex items-center gap-1 text-ribbon hover:underline after:absolute after:-inset-y-3 after:inset-x-0"
          >
            {m.original}
            <ExternalLink aria-hidden className="size-3.5" />
          </a>
        )}
      </p>

      {view.state === "processing" && (
        <p role="status" className="mt-6 text-paper-dim">
          {m.processing}
        </p>
      )}
      {view.state === "failed" && <p className="mt-6 text-st-confused">{m.failed}</p>}
      {view.state === "ready" && view.pages.length === 0 && <p className="mt-6 text-paper-dim">{m.noPages}</p>}

      {view.pages.length > 0 && (
        <ol className="mt-6 flex flex-col gap-7">
          {view.pages.map((p) => {
            const { heading, body } = splitHeading(p.text);
            return (
              <li key={p.page} aria-label={slides ? m.slide(p.page) : m.page(p.page)}>
                <p aria-hidden className="caps text-paper-dim">
                  {slides ? m.slide(p.page) : m.page(p.page)}
                </p>
                {heading && <h3 className="display-caps mt-1.5 text-paper [overflow-wrap:anywhere]">{heading}</h3>}
                {body ? (
                  <p className={cn("whitespace-pre-wrap text-paper [overflow-wrap:anywhere]", heading ? "mt-2" : "mt-1.5")}>{body}</p>
                ) : !heading ? (
                  <p className="mt-1.5 text-paper-dim">{m.noText}</p>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </DocScroll>
  );
}
