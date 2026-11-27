"use client";

import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { highlightCodeTokens, langFromInfo } from "@/lib/code-highlight";
import { useT } from "@/lib/i18n/provider";

/** Fenced code from a reply: highlighted like in the editor, language label and copy button in the corner */
export function CodeBlock({ code, info }: { code: string; info: string }) {
  const t = useT().codeBlock;
  const [copied, setCopied] = useState(false);
  const { lang, label } = langFromInfo(info);
  const tokens = useMemo(() => (lang ? highlightCodeTokens(code, lang) : null), [code, lang]);

  const copy = () => {
    try {
      void navigator.clipboard.writeText(code).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      });
    } catch {
      /* clipboard may be blocked */
    }
  };

  return (
    <div className="group/code relative">
      <div className="absolute right-1.5 top-1.5 z-10 flex items-center gap-1">
        {label && <span className="caps text-paper-dim">{label}</span>}
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? t.copied : t.copy}
          title={copied ? t.copied : t.copy}
          className="flex size-6 items-center justify-center rounded-[4px] text-paper-dim opacity-60 transition-opacity hover:bg-cloth-hover hover:text-paper hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-ribbon group-hover/code:opacity-100 pointer-coarse:size-10"
        >
          {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
        </button>
      </div>
      <pre className="overflow-x-auto rounded-[6px] border border-seam-soft bg-cloth-deep p-3 pr-20 font-mono text-small text-paper">
        <code>
          {tokens
            ? tokens.map((tk, i) => (tk.cls ? <span key={i} className={tk.cls}>{tk.text}</span> : tk.text))
            : code}
        </code>
      </pre>
    </div>
  );
}
