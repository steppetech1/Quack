"use client";

import { ArrowLeft, Maximize2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { getCanvas, openCanvas, readCanvasTitle, writeCanvasTitle, type CanvasView } from "@/lib/api/canvases";
import { ApiError } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { localIso } from "@/lib/format";
import { useFmt, useT } from "@/lib/i18n/provider";
import { useShell } from "../app-shell";
import { Board } from "../board/board";
import { useErrorText } from "./error-text";

const BACK_CLS = "inline-flex size-10 shrink-0 items-center justify-center rounded-pill text-paper-dim transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover hover:text-paper [&_svg]:size-[18px]";

function Status({ text, error }: { text: string; error?: boolean }) {
  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <p role={error ? "alert" : "status"} className={error ? "text-st-confused" : "text-paper-dim"}>
        {text}
      </p>
    </div>
  );
}

/** /canvas/new on the backend: a canvas lives in a session, so take the active one of this subject (or start one), open a canvas in it and go there */
export function NewCanvasLive() {
  const tx = useT();
  const router = useRouter();
  const errorText = useErrorText();
  const { domain } = useShell();
  const { active, setActive } = useData();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !domain.id) return;
    started.current = true;
    (async () => {
      let sessionId = active?.status === "active" && active.domain_id === domain.id ? active.session_id : null;
      if (!sessionId) {
        const s = await startSession({ domain_id: domain.id, intent: "free" });
        setActive(s);
        sessionId = s.session_id;
      }
      const c = await openCanvas(sessionId);
      router.replace(`/canvas/${c.canvas_id}`);
    })().catch((e) => setError(errorText(e)));
  }, [domain.id, active, setActive, router, errorText]);

  return <Status text={error ?? tx.live.canvasOpening} error={!!error} />;
}

/**
 * The board of a canvas of the backend, full screen. The title stays in this browser (the backend has no field for it yet).
 * `embedded`: the board stands in the workspace pane of a session instead of the whole page: a strip with the title and a link
 * to the full page replaces the corner with the way back, and the board's keys work only after a click on it.
 */
export function CanvasLive({ id, embedded = false }: { id: string; embedded?: boolean }) {
  const tx = useT();
  const { fullDate } = useFmt();
  const errorText = useErrorText();
  const [view, setView] = useState<CanvasView | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");

  useEffect(() => {
    let off = false;
    getCanvas(id)
      .then((v) => {
        if (off) return;
        setView(v);
        setTitle(readCanvasTitle(id));
      })
      .catch((e) => {
        if (off) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) setMissing(true);
        else setError(errorText(e));
      });
    return () => {
      off = true;
    };
  }, [id, errorText]);

  if (missing) return <Status text={tx.live.canvasMissing} error />;
  if (error) return <Status text={error} error />;
  if (!view) return <Status text={tx.live.canvasLoading} />;

  if (embedded) {
    return (
      <>
        <div className="flex shrink-0 items-center gap-1 border-b border-seam-soft pr-1 pl-4">
          <input
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              writeCanvasTitle(view.canvas_id, e.target.value.trim());
            }}
            aria-label={tx.canvases.nameLabel}
            placeholder={tx.canvases.untitled}
            maxLength={80}
            className="h-10 min-w-0 flex-1 bg-transparent font-display text-body uppercase tracking-[0.02em] text-paper outline-none placeholder:text-paper-dim focus-visible:outline-2 focus-visible:outline-ribbon"
          />
          <Link href={`/canvas/${view.canvas_id}`} aria-label={tx.workspace.canvasDoc.full} title={tx.workspace.canvasDoc.full} className={BACK_CLS}>
            <Maximize2 aria-hidden />
          </Link>
        </div>
        <Board canvasId={view.canvas_id} live={view.session_id ? { sessionId: view.session_id, doc: view.doc } : undefined} mode="edit" scoped />
      </>
    );
  }

  return (
    <Board
      canvasId={view.canvas_id}
      live={view.session_id ? { sessionId: view.session_id, doc: view.doc } : undefined}
      mode="edit"
      topLeft={
        <>
          <Link href="/?view=canvases" aria-label={tx.canvases.backToList} className={BACK_CLS}>
            <ArrowLeft aria-hidden />
          </Link>
          <div className="min-w-0 pr-3 pl-1">
            <h1 className="sr-only">{title || tx.canvases.untitled}</h1>
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                writeCanvasTitle(view.canvas_id, e.target.value.trim());
              }}
              aria-label={tx.canvases.nameLabel}
              placeholder={tx.canvases.untitled}
              maxLength={80}
              className="h-6 w-full min-w-0 bg-transparent font-display text-body uppercase tracking-[0.02em] text-paper outline-none placeholder:text-paper-dim focus-visible:outline-2 focus-visible:outline-ribbon"
            />
            <p className="caps mt-0.5 hidden truncate [--caps-size:0.625rem] text-paper-dim sm:block">{fullDate(localIso(Date.parse(view.updated_at)))}</p>
          </div>
        </>
      }
    />
  );
}
