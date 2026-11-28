"use client";

import { ChevronsLeft, ChevronsRight, Code2, Maximize2, Minimize2, PanelRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { codeTask, runCode, saveDraft, submitCode, type CodeTask, type CodeTestView, type CustomRunView } from "@/lib/api/code";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { ResizeHandle, useResizable } from "../resize-handle";
import { PRIMARY_BTN, SECONDARY_BTN } from "../stubs/stub-parts";
import { CodeEditor } from "./code-editor";
import { Markdown } from "./markdown";

export type OpenCode = { task: CodeTask; title: string; /** when it was asked for: asking again unfolds the panel */ at?: number };
type Outcome = "correct" | "incorrect" | "partial" | "unclear";

const draftKey = (taskId: string) => `quack:code-draft:${taskId}`;

/** The draft saved on the server wins (another device, a cleared browser); then this browser's; then the starter code */
function loadDraft(task: CodeTask): string {
  if (task.draft?.code) return task.draft.code;
  try {
    return localStorage.getItem(draftKey(task.task_id)) ?? task.starter_code ?? "";
  } catch {
    return task.starter_code ?? "";
  }
}

/** How often the editor asks whether the hidden tests are ready, and how long it waits after typing to save the draft */
const BUILD_POLL_MS = 2500;
const DRAFT_SAVE_MS = 1500;
/** «Все тесты пройдены! Отправляем на ревью через 4 сек»: time to take out debug prints before the code goes */
const AUTO_SEND_S = 4;

/** Tagged JSON of the runner as Python would print it: tuples, sets, dicts, inf */
function show(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return `[${value.map(show).join(", ")}]`;
  if (typeof value === "object") {
    const [tag, body] = Object.entries(value as Record<string, unknown>)[0] ?? [];
    if (tag === "$tuple" && Array.isArray(body)) return body.length === 1 ? `(${show(body[0])},)` : `(${body.map(show).join(", ")})`;
    if (tag === "$set" && Array.isArray(body)) return body.length ? `{${body.map(show).join(", ")}}` : "set()";
    if (tag === "$dict" && Array.isArray(body)) return `{${body.map((p) => `${show((p as unknown[])[0])}: ${show((p as unknown[])[1])}`).join(", ")}}`;
    if (tag === "$float") return String(body) === "nan" ? "nan" : String(body);
    if (tag === "$bytes") return `x'${String(body)}'`;
    if (tag === "raises") return `raises ${String(body)}`;
  }
  return JSON.stringify(value);
}

function Rows({ columns, rows }: { columns?: string[] | null; rows?: unknown[][] | null }) {
  if (!rows) return null;
  return (
    <div className="mt-1 max-h-48 overflow-auto rounded-[6px] border border-seam-soft">
      <table className="w-full font-mono text-small">
        {columns && (
          <thead className="text-paper-dim">
            <tr>{columns.map((c, i) => <th key={i} className="border-b border-seam-soft px-2 py-1 text-left font-normal">{c}</th>)}</tr>
          </thead>
        )}
        <tbody className="text-paper">
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j} className="px-2 py-0.5">{show(c)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TestRow({ t, language }: { t: CodeTestView; language: "python" | "sql" }) {
  const tx = useT().code;
  const passed = t.status === "passed";
  /** A hidden case of the sandbox shows its input and what the code gave, never the expected result; an old one, its status only */
  const shown = !t.hidden || !!t.name;
  return (
    <li className="rounded-[6px] border border-seam-soft p-3">
      <p className="flex items-baseline gap-2">
        <span className="caps text-paper-dim">
          {t.hidden ? (t.name ? tx.hiddenCase(t.name) : tx.hiddenTest(t.number)) : tx.openTest(t.number)}
        </span>
        <span className={cn("text-small", passed ? "text-st-solid" : "text-st-confused")}>{tx.status[t.status]}</span>
        {t.time_ms != null && (!t.hidden || t.name) && <span className="ml-auto font-mono text-small text-paper-dim">{t.time_ms} ms</span>}
      </p>
      {shown && language === "python" && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-small">
          <dt className="text-paper-dim">{tx.args}</dt>
          <dd className="break-all text-paper">{(t.args ?? []).map(show).join(", ")}</dd>
          {!t.hidden && (
            <>
              <dt className="text-paper-dim">{tx.expected}</dt>
              <dd className="break-all text-paper">{show(t.expected)}</dd>
            </>
          )}
          {t.status !== "error" && (
            <>
              <dt className="text-paper-dim">{tx.got}</dt>
              <dd className="break-all text-paper">{show(t.actual)}</dd>
            </>
          )}
        </dl>
      )}
      {!t.hidden && language === "sql" && (
        <>
          <p className="mt-2 text-small text-paper-dim">{tx.expected}</p>
          <Rows rows={t.expected_rows} />
          {t.actual_rows && (
            <>
              <p className="mt-2 text-small text-paper-dim">{tx.got}</p>
              <Rows columns={t.columns} rows={t.actual_rows} />
            </>
          )}
        </>
      )}
      {shown && t.error && (
        <p className="mt-2 break-words font-mono text-small text-st-confused">
          {t.error.type}: {t.error.message}
          {t.error.line != null && ` (${tx.line(t.error.line)})`}
        </p>
      )}
      {shown && t.stderr && t.status !== "passed" && !t.error && (
        <pre className="mt-2 max-h-32 overflow-auto rounded-[6px] bg-cloth-deep p-2 font-mono text-small text-st-confused">{t.stderr}</pre>
      )}
      {shown && t.stdout && <pre className="mt-2 max-h-32 overflow-auto rounded-[6px] bg-cloth-deep p-2 font-mono text-small text-paper-dim">{t.stdout}</pre>}
    </li>
  );
}

/** The editor can be dragged by its left edge from 360 px up to what leaves the chat 22rem (from `lg`, where it is a column) */
const CODE_MIN_PX = 360;
const CHAT_MIN_REM = 22;
const codeMax = (panel: HTMLElement) => (panel.parentElement?.clientWidth ?? 1200) - CHAT_MIN_REM * 16;

const ICON_BTN = "inline-flex size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper";

/**
 * The code sandbox, a column on the right of the session chat. «Запустить» is never blocked: it runs every test there is
 * now (the example, and the hidden cases of a full problem once they are built in the background; nothing is written).
 * «Отправить учителю» is always there, red tests or not: every test runs, the answer and its verdict go to memory, and
 * the teacher's review comes as the next turn. In a full problem whose tests all pass the code goes by itself after a
 * short countdown that can be cancelled. A hidden case shows its name, input and what the code gave, never the expected
 * result. The draft is kept on the server by task (and in this browser).
 */
export function CodePanel({
  open,
  sessionId,
  onClose,
  onVerdict,
  onReview,
}: {
  open: OpenCode;
  sessionId: string;
  onClose: () => void;
  onVerdict?: (outcome: Outcome) => void;
  /** The teacher's turn on the answer just sent (`retry_turn`); false — it could not start */
  onReview?: () => Promise<boolean>;
}) {
  const t = useT();
  const tx = t.code;
  const task = open.task;
  const language = task.language;
  const [wide, setWide] = useState(true);
  /** the width the learner dragged to; the «wider / narrower» button goes back to its two presets */
  const resize = useResizable({ id: "code-panel", min: CODE_MIN_PX, max: codeMax, edge: "left" });
  /** over the whole screen */
  const [full, setFull] = useState(false);
  /** from `lg`: folded into a narrow strip, the editor stays mounted behind it */
  const [folded, setFolded] = useState(false);
  // «Open the editor» again unfolds it
  const [seenAt, setSeenAt] = useState(open.at);
  if (open.at !== seenAt) {
    setSeenAt(open.at);
    setFolded(false);
  }
  const [code, setCode] = useState(() => loadDraft(task));
  const [tests, setTests] = useState<CodeTestView[] | null>(null);
  const [custom, setCustom] = useState<CustomRunView | null>(null);
  const [args, setArgs] = useState("");
  const [busy, setBusy] = useState<"run" | "custom" | "submit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [build, setBuild] = useState(task.build);
  const [hidden, setHidden] = useState(task.hidden_tests);
  /** Seconds left before the code goes for review by itself; null — no countdown */
  const [countdown, setCountdown] = useState<number | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // The hidden tests of a full problem are built in the background: ask again until they are ready (or given up on).
  // The panel lives as long as its task (`key` is the task id), so an answer for another task never lands here.
  useEffect(() => {
    if (build !== "pending") return;
    let alive = true;
    const timer = setInterval(() => {
      codeTask(task.task_id)
        .then((fresh) => {
          if (!alive || fresh.task_id !== task.task_id) return;
          setBuild(fresh.build);
          setHidden(fresh.hidden_tests);
        })
        .catch(() => {});
    }, BUILD_POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [build, task.task_id]);

  useEffect(() => () => clearTimeout(saveTimer.current), []);

  // Focus moves into the panel once, on opening. Not on every render of the session (it re-renders on its own while
  // the learner types): a focus that jumps to a button lets the next Space or Enter press it.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => before?.focus?.();
  }, []);

  /** Esc closes the panel only from the panel itself and not while typing: the chat beside it keeps its own Esc */
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Escape" || !(e.target instanceof HTMLElement) || e.target.closest(".cm-editor, input, textarea")) return;
    if (full) setFull(false);
    else onClose();
  };

  const edit = (next: string) => {
    setCode(next);
    setCountdown(null);
    try {
      localStorage.setItem(draftKey(task.task_id), next);
    } catch {
      /* private mode: the draft lives in memory and on the server */
    }
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => void saveDraft(task.task_id, next).catch(() => {}), DRAFT_SAVE_MS);
  };

  const explain = (e: unknown) => {
    if (e instanceof ApiError) {
      if (e.status === 429) return tx.busy;
      if (e.status === 503) return tx.unavailable;
      if (e.status === 422) return tx.tooLong;
    }
    return tx.failed;
  };

  const go = async (kind: "run" | "custom" | "submit") => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    setCountdown(null);
    try {
      if (kind === "submit") {
        const r = await submitCode(sessionId, task.task_id, code);
        setTests(r.tests);
        setCustom(null);
        setOutcome(r.outcome);
        onVerdict?.(r.outcome);
        // the answer is written; the review is the teacher's next turn, streamed into the chat
        // the line shows while the review streams into the chat, and goes once it is there (or could not start)
        if (onReview) {
          setReviewing(true);
          await onReview();
          setReviewing(false);
        }
      } else if (kind === "custom") {
        let parsed: unknown;
        try {
          parsed = JSON.parse(`[${args}]`);
        } catch {
          setError(tx.badArgs);
          return;
        }
        const r = await runCode(task.task_id, code, parsed as unknown[]);
        setCustom(r.custom ?? null);
      } else {
        const r = await runCode(task.task_id, code);
        setTests(r.tests);
        setCustom(null);
        setBuild(r.build ?? build);
        // a new run replaces what the last submit said: its outcome was about other code
        setOutcome(null);
        // a full problem whose hidden tests are built and all passed: the code goes for review after a short countdown
        const built = r.tests.some((x) => x.hidden);
        if (task.mode === "full_problem" && r.build === "ready" && built && r.tests.every((x) => x.status === "passed")) {
          setCountdown(AUTO_SEND_S);
        }
      }
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(null);
    }
  };

  // The countdown before the code goes for review by itself; every tick is a timeout, the last one sends
  useEffect(() => {
    if (countdown === null) return;
    const timer = setTimeout(() => {
      if (countdown <= 1) {
        setCountdown(null);
        void go("submit");
      } else setCountdown(countdown - 1);
    }, 1000);
    return () => clearTimeout(timer);
    // `go` is a fresh closure each render; the countdown alone drives this
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countdown]);

  const samplePassed = tests?.find((x) => !x.hidden)?.status === "passed";

  const Resize = wide ? ChevronsRight : ChevronsLeft;
  const Full = full ? Minimize2 : Maximize2;
  const fn = task.function ? `${task.function}(…)` : null;
  return (
    <aside
      ref={panel}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      aria-label={tx.panel}
      // From `lg` the editor is a column beside the chat and narrows it; below, there is no room for two: it covers the chat
      className={cn(
        "flex min-w-0 flex-col border-l border-seam bg-cloth-raised outline-none",
        full ? "fixed inset-0 z-50" : "absolute inset-0 z-30 lg:relative lg:z-auto lg:shrink-0",
        !full && (folded ? "lg:w-12 lg:items-center lg:py-1" : resize.width !== null ? "lg:w-(--code-w)" : wide ? "lg:w-[58%]" : "lg:w-[42%]"),
      )}
      style={!full && !folded && resize.width !== null ? ({ "--code-w": `min(${resize.width}px, calc(100% - ${CHAT_MIN_REM}rem))` } as React.CSSProperties) : undefined}
    >
      {!full && !folded && <ResizeHandle r={resize} className="hidden lg:block" />}
      {folded && (
        <>
          <button type="button" aria-label={tx.unfold} title={tx.unfold} onClick={() => setFolded(false)} className={cn(ICON_BTN, "hidden lg:inline-flex")}>
            <PanelRight aria-hidden className="size-4" />
          </button>
          <button type="button" aria-label={tx.panel} title={tx.panel} onClick={() => setFolded(false)} className={cn(ICON_BTN, "hidden lg:inline-flex")}>
            <Code2 aria-hidden className="size-4" />
          </button>
        </>
      )}
      <header className={cn("flex shrink-0 items-center gap-1 border-b border-seam-soft pl-4", folded && "lg:hidden", !full && "lg:pl-1")}>
        {!full && (
          <button
            type="button"
            aria-label={tx.fold}
            title={tx.fold}
            onClick={() => {
              // nothing is sent by itself while the countdown cannot be seen
              setCountdown(null);
              setFolded(true);
            }}
            className={cn(ICON_BTN, "hidden lg:inline-flex")}
          >
            <PanelRight aria-hidden className="size-4" />
          </button>
        )}
        <h2 className="caps min-w-0 flex-1 truncate py-3 text-paper">
          {open.title} · {language === "python" ? "Python 3.12" : "SQLite"}
        </h2>
        {!full && (
          <button type="button" aria-label={wide ? tx.narrow : tx.wide} onClick={() => {
              setWide((w) => !w);
              resize.reset();
            }} className={cn(ICON_BTN, "hidden lg:inline-flex")}>
            <Resize aria-hidden className="size-4" />
          </button>
        )}
        <button type="button" aria-label={full ? tx.exitFull : tx.full} title={full ? tx.exitFull : tx.full} onClick={() => setFull((v) => !v)} className={ICON_BTN}>
          <Full aria-hidden className="size-4" />
        </button>
        <button
          type="button"
          aria-label={tx.close}
          onClick={onClose}
          className="inline-flex size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper"
        >
          <X aria-hidden className="size-4" />
        </button>
      </header>

      <div className={cn("flex min-h-0 w-full flex-1 flex-col gap-4 overflow-y-auto p-4", folded && "lg:hidden")}>
        {/* beside the chat the statement is read there; where the editor covers the chat, it is shown here */}
        <div className={cn("flex shrink-0 flex-col gap-2", !full && "lg:hidden")}>
          {task.condition && (
            <div tabIndex={0} className="max-h-[35vh] overflow-y-auto">
              <Markdown text={task.condition} />
            </div>
          )}
          {fn && <p className="text-small text-paper-dim">{tx.writeFunction(fn)}</p>}
        </div>
        <p className="flex flex-wrap items-center gap-2 text-small text-paper-dim">
          <span className="caps rounded-pill border border-seam-soft px-2 py-0.5">{tx.mode[task.mode]}</span>
          {task.sql_preset && <span>{tx.database(task.sql_preset)}</span>}
        </p>
        {task.schema_sql && (
          <details className="rounded-[6px] border border-seam-soft">
            <summary className="cursor-pointer px-3 py-2 text-small text-paper-dim">{tx.schema}</summary>
            <pre className="overflow-x-auto px-3 pb-3 font-mono text-small text-paper">{task.schema_sql}</pre>
          </details>
        )}

        <div className="h-72 shrink-0 lg:h-[55dvh]">
          <CodeEditor language={language} initial={code} docKey={task.task_id} onChange={edit} label={tx.editor} />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void go("run")} disabled={!!busy || !code.trim()} className={SECONDARY_BTN}>
            {busy === "run" ? tx.running : tx.run}
          </button>
          <button type="button" onClick={() => void go("submit")} disabled={!!busy || !code.trim()} className={PRIMARY_BTN}>
            {busy === "submit" ? tx.sending : tx.sendToTeacher}
          </button>
          {build === "ready" && hidden > 0 && <span className="text-small text-paper-dim">{tx.hiddenReady(hidden)}</span>}
        </div>

        {/* the state of the background tests, and the countdown before the code goes by itself */}
        <div aria-live="polite" className="flex flex-col gap-2 empty:hidden">
          {build === "pending" && <p className="text-small text-paper-dim">{samplePassed ? tx.samplePassedPending : tx.pending}</p>}
          {countdown !== null && (
            <div className="flex flex-wrap items-center gap-3 rounded-[6px] border border-seam-soft p-3">
              <p className="text-body text-st-solid">{tx.autoSend(countdown)}</p>
              <button
                type="button"
                onClick={() => setCountdown(null)}
                className={SECONDARY_BTN}
              >
                {tx.cancel}
              </button>
            </div>
          )}
          {reviewing && <p className="text-small text-paper-dim">{tx.reviewing}</p>}
        </div>

        {language === "python" && (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void go("custom");
            }}
          >
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-small text-paper-dim">
              {tx.ownArgs}
              <input
                value={args}
                onChange={(e) => setArgs(e.target.value)}
                placeholder={tx.ownArgsHint}
                className="h-10 rounded-[6px] border border-seam-soft bg-cloth-deep px-3 font-mono text-small text-paper outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
            </label>
            <button type="submit" disabled={!!busy || !code.trim() || !args.trim()} className={SECONDARY_BTN}>
              {busy === "custom" ? tx.running : tx.runOwn}
            </button>
          </form>
        )}

        <div aria-live="polite" className="flex flex-col gap-3">
          {error && <p className="text-small text-st-confused">{error}</p>}
          {outcome && (
            <p className={cn("text-body", outcome === "correct" ? "text-st-solid" : "text-st-confused")}>{tx.outcome[outcome]}</p>
          )}
          {custom && (
            <div className="rounded-[6px] border border-seam-soft p-3 font-mono text-small">
              {/* nothing is compared here: a value that came back is a result, not a pass */}
              <p className="text-paper-dim">{custom.status === "passed" ? tx.result : tx.status[custom.status]}</p>
              {custom.status === "passed" && <p className="mt-1 break-all text-paper">{show(custom.value)}</p>}
              {custom.error && <p className="mt-1 text-st-confused">{custom.error.type}: {custom.error.message}</p>}
              {custom.stdout && <pre className="mt-2 max-h-32 overflow-auto text-paper-dim">{custom.stdout}</pre>}
            </div>
          )}
          {tests && (
            <ol className="flex flex-col gap-2">
              {tests.map((test) => <TestRow key={test.number} t={test} language={language} />)}
            </ol>
          )}
          {!tests && !custom && !error && <p className="text-small text-paper-dim">{tx.idle}</p>}
        </div>
      </div>
    </aside>
  );
}
