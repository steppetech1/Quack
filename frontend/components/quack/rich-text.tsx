import katex from "katex";
import { cn } from "@/lib/utils";
import { ScrollRegion } from "./scroll-region";

function tex(src: string, display: boolean) {
  return katex.renderToString(src, { displayMode: display, throwOnError: false, output: "htmlAndMathml" });
}

/** Строка: **жирный** и $формула$ */
export function Inline({ text }: { text: string }) {
  const parts = text.split(/(\$[^$]+\$|\*\*[^*]+\*\*)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("$") && p.endsWith("$") && p.length > 2) {
          // разметку KaTeX собираем сами из своих моков, пользовательский ввод сюда не попадает
          return <span key={i} dangerouslySetInnerHTML={{ __html: tex(p.slice(1, -1), false) }} />;
        }
        if (p.startsWith("**") && p.endsWith("**")) {
          return (
            <strong key={i} className="font-semibold text-paper">
              {p.slice(2, -2)}
            </strong>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

/** Абзацы через пустую строку; $$…$$ отдельной строкой — формула по центру */
export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks = text.trim().split(/\n\s*\n/);
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {blocks.map((b, i) => {
        const t = b.trim();
        if (t.startsWith("$$") && t.endsWith("$$")) {
          return (
            <ScrollRegion key={i} className="py-1 text-paper" html={tex(t.slice(2, -2), true)} />
          );
        }
        return (
          <p key={i}>
            <Inline text={t} />
          </p>
        );
      })}
    </div>
  );
}
