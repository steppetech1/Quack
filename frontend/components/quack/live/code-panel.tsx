"use client";

import { ChevronsLeft, ChevronsRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { runCode, submitCode, type CodeTask, type CodeTestView, type CustomRunView } from "@/lib/api/code";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { PRIMARY_BTN, SECONDARY_BTN } from "../stubs/stub-parts";
import { CodeEditor } from "./code-editor";
import { Markdown } from "./markdown";

export type OpenCode = { task: CodeTask; title: string };
type Outcome = "correct" | "incorrect" | "partial" | "unclear";

const draftKey = (taskId: string) => `quack:code-draft:${taskId}`;

function loadDraft(task: CodeTask): string {
  try {
    return localStorage.getItem(draftKey(task.task_id)) ?? task.starter_code ?? "";
  } catch {
    return task.starter_code ?? "";
  }
}

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
  return (
    <li className="rounded-[6px] border border-seam-soft p-3">
      <p className="flex items-baseline gap-2">
        <span className="caps text-paper-dim">{t.hidden ? tx.hiddenTest(t.number) : tx.openTest(t.number)}</span>
        <span className={cn("text-small", passed ? "text-st-solid" : "text-st-confused")}>{tx.status[t.status]}</span>
        {t.time_ms != null && !t.hidden && <span className="ml-auto font-mono text-small text-paper-dim">{t.time_ms} ms</span>}
      </p>
      {!t.hidden && language === "python" && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-small">
          <dt className="text-paper-dim">{tx.args}</dt>
          <dd className="break-all text-paper">{(t.args ?? []).map(show).join(", ")}</dd>
          <dt className="text-paper-dim">{tx.expected}</dt>
          <dd className="break-all text-paper">{show(t.expected)}</dd>
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
      {!t.hidden && t.error && (
        <p className="mt-2 break-words font-mono text-small text-st-confused">
          {t.error.type}: {t.error.message}
          {t.error.line != null && ` (${tx.line(t.error.line)})`}
        </p>
      )}
      {!t.hidden && t.stdout && <pre className="mt-2 max-h-32 overflow-auto rounded-[6px] bg-cloth-deep p-2 font-mono text-small text-paper-dim">{t.stdout}</pre>}
    </li>
  );
}

/**
 * The editor of a code task, on the right of the session (as the demo panel). The learner runs the open tests (nothing
 * is written), runs the function on their own arguments (Python), and submits: every test runs, the answer and its
 * verdict go to memory. Hidden tests come back as a number and a status only. The draft survives a reload.
 */
export function CodePanel({
  open,
  sessionId,
  onClose,
  onVerdict,
}: {
  open: OpenCode;
  sessionId: string;
  onClose: () => void;
  onVerdict?: (outcome: Outcome) => void;
}) {
  const t = useT();
  const tx = t.code;
  const task = open.task;
  const language = task.language;
  const [wide, setWide] = useState(true);
  const [code, setCode] = useState(() => loadDraft(task));
  const [tests, setTests] = useState<CodeTestView[] | null>(null);
  const [custom, setCustom] = useState<CustomRunView | null>(null);
  const [args, setArgs] = useState("");
  const [busy, setBusy] = useState<"run" | "custom" | "submit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const close = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target instanceof HTMLElement && e.target.closest(".cm-editor"))) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      before?.focus?.();
    };
  }, [onClose]);

  const edit = (next: string) => {
    setCode(next);
    try {
      localStorage.setItem(draftKey(task.task_id), next);
    } catch {
      /* private mode: the draft lives only in memory */
    }
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
    try {
      if (kind === "submit") {
        const r = await submitCode(sessionId, task.task_id, code);
        setTests(r.tests);
        setCustom(null);
        setOutcome(r.outcome);
        onVerdict?.(r.outcome);
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
      }
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(null);
    }
  };

  const Resize = wide ? ChevronsRight : ChevronsLeft;
  const fn = task.function ? `${task.function}(…)` : null;
  return (
    <aside
      role="dialog"
      aria-label={tx.panel}
      className={cn(
        "pointer-events-auto absolute inset-y-0 right-0 z-30 flex w-full flex-col border-l border-seam bg-cloth-raised shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.6)]",
        wide ? "md:w-[min(60rem,92%)]" : "md:w-[min(32rem,92%)]",
      )}
    >
      <header className="flex shrink-0 items-center gap-1 border-b border-seam-soft pl-4">
        <h2 className="caps min-w-0 flex-1 truncate py-3 text-paper">
          {open.title} · {language === "python" ? "Python 3.12" : "SQLite"}
        </h2>
        <button
          type="button"
          aria-label={wide ? tx.narrow : tx.wide}
          onClick={() => setWide((w) => !w)}
          className="hidden size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper md:inline-flex"
        >
          <Resize aria-hidden className="size-4" />
        </button>
        <button
          ref={close}
          type="button"
          aria-label={tx.close}
          onClick={onClose}
          className="inline-flex size-10 shrink-0 items-center justify-center text-paper-dim hover:text-paper"
        >
          <X aria-hidden className="size-4" />
        </button>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
        {/* a long statement scrolls on its own, so the editor stays in view */}
        {task.condition && (
          <div tabIndex={0} className="max-h-[35vh] shrink-0 overflow-y-auto">
            <Markdown text={task.condition} />
          </div>
        )}
        {fn && <p className="text-small text-paper-dim">{tx.writeFunction(fn)}</p>}
        {task.schema_sql && (
          <details className="rounded-[6px] border border-seam-soft">
            <summary className="cursor-pointer px-3 py-2 text-small text-paper-dim">{tx.schema}</summary>
            <pre className="overflow-x-auto px-3 pb-3 font-mono text-small text-paper">{task.schema_sql}</pre>
          </details>
        )}

        <div className="h-72 shrink-0">
          <CodeEditor language={language} initial={code} docKey={task.task_id} onChange={edit} label={tx.editor} />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void go("run")} disabled={!!busy || !code.trim()} className={SECONDARY_BTN}>
            {busy === "run" ? tx.running : tx.run}
          </button>
          <button type="button" onClick={() => void go("submit")} disabled={!!busy || !code.trim()} className={PRIMARY_BTN}>
            {busy === "submit" ? tx.checking : tx.submit}
          </button>
          <span className="text-small text-paper-dim">{tx.hiddenCount(task.hidden_tests)}</span>
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
