"use client";

import { useEffect, useRef } from "react";
import { basicSetup } from "codemirror";
import { EditorView, keymap } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { indentWithTab } from "@codemirror/commands";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { python } from "@codemirror/lang-python";
import { sql, SQLite } from "@codemirror/lang-sql";

/** Colours of the app's own tokens, so the editor reads as part of the panel, not a pasted widget */
const theme = EditorView.theme(
  {
    "&": { backgroundColor: "var(--cloth-deep)", color: "var(--paper)", fontSize: "0.875rem", height: "100%" },
    ".cm-scroller": { fontFamily: "var(--font-mono), ui-monospace, monospace", lineHeight: "1.55" },
    ".cm-content": { caretColor: "var(--accent)", padding: "8px 0" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
    "&.cm-focused": { outline: "none" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "color-mix(in oklab, var(--accent) 28%, transparent)",
    },
    ".cm-gutters": { backgroundColor: "var(--cloth-deep)", color: "var(--paper-dim)", border: "none" },
    ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "color-mix(in oklab, var(--paper) 5%, transparent)" },
  },
  { dark: true },
);

/** Calm colours readable on the warm graphite (the default style of basicSetup is made for a light page) */
const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.operatorKeyword], color: "#d9a66b" },
  { tag: [t.function(t.variableName), t.function(t.definition(t.variableName))], color: "#e9dcc4", fontWeight: "600" },
  { tag: [t.definition(t.variableName), t.variableName, t.propertyName], color: "var(--paper)" },
  { tag: [t.string, t.special(t.string)], color: "#a9c99a" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "#c7a6d8" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--paper-dim)", fontStyle: "italic" },
  { tag: [t.typeName, t.className], color: "#9fc3d6" },
  { tag: [t.operator, t.punctuation, t.bracket], color: "var(--paper-dim)" },
]);

/**
 * CodeMirror 6 with Python or SQLite. Uncontrolled: `initial` seeds the document once per `docKey`, every edit goes
 * out through `onChange`. Tab indents (Esc then Tab leaves the editor, as CodeMirror documents for keyboard users).
 */
export function CodeEditor({
  language,
  initial,
  docKey,
  onChange,
  label,
}: {
  language: "python" | "sql";
  initial: string;
  docKey: string;
  onChange: (code: string) => void;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const change = useRef(onChange);
  useEffect(() => {
    change.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (!host.current) return;
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: initial,
        extensions: [
          basicSetup,
          keymap.of([indentWithTab]),
          language === "python" ? python() : sql({ dialect: SQLite }),
          theme,
          syntaxHighlighting(highlight),
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) change.current(u.state.doc.toString());
          }),
        ],
      }),
    });
    return () => view.destroy();
    // the document is seeded once per task; later `initial` changes must not wipe what the learner typed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey, language]);

  return <div ref={host} className="h-full min-h-48 overflow-hidden rounded-[6px] border border-seam-soft" />;
}
