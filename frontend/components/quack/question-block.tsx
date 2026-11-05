"use client";

import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import type { Option } from "@/lib/mock/types";
import { scrollToId } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { Inline } from "./rich-text";

export type Answer = { option: string; confidence: number | null; thought: string };

/**
 * Вопрос-предсказание: варианты, «Не знаю», уверенность (по умолчанию не выбрана)
 * и свёрнутое «Объясни утке». После отправки блок фиксирует ответ.
 */
export function QuestionBlock({
  id,
  num,
  prompt,
  options,
  answer,
  onAnswer,
}: {
  id: string;
  num: number;
  prompt: string;
  options: Option[];
  answer: Answer | null;
  onAnswer: (a: Answer) => void;
}) {
  const tx = useT();
  const uid = useId();
  const [option, setOption] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [thoughtOpen, setThoughtOpen] = useState(false);
  const [thought, setThought] = useState("");

  const locked = answer !== null;
  const chosen = answer?.option ?? option;
  const all: Option[] = [...options, { id: "idk", text: tx.question.idk }];

  // «Ответить» не должна уходить за край, когда блок вырастает
  const keepInView = () => requestAnimationFrame(() => scrollToId(id, 16, undefined, "bottom"));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!option || locked) return;
    onAnswer({ option, confidence, thought: thought.trim() });
  };

  return (
    <form
      id={id}
      onSubmit={submit}
      aria-labelledby={`${uid}-q`}
      className="scroll-mt-6 rounded-[2px] border border-seam bg-cloth-raised p-4 sm:p-5"
    >
      <p id={`${uid}-q`} className="flex gap-3 text-paper">
        <span className="caps shrink-0 pt-1 text-paper-dim">{tx.question.num(num)}</span>
        <span>
          <Inline text={prompt} />
        </span>
      </p>

      <fieldset className="mt-4" disabled={locked}>
        <legend className="sr-only">{tx.question.options}</legend>
        <div className="flex flex-col gap-2">
          {all.map((o) => {
            const isChosen = chosen === o.id;
            const idk = o.id === "idk";
            const verdict = locked && (("correct" in o && o.correct) ? "right" : isChosen ? "wrong" : null);
            return (
              <label
                key={o.id}
                className={cn(
                  "relative flex min-h-11 cursor-pointer items-center gap-3 rounded-control border px-3.5 py-2.5 transition-colors duration-(--dur-s) ease-(--ease-out) has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-ribbon",
                  idk ? "border-dashed" : "",
                  isChosen ? "border-ribbon bg-cloth-hover" : locked ? "border-seam-soft" : "border-seam hover:border-ribbon/60",
                  verdict === "right" && "border-st-solid",
                  verdict === "wrong" && !idk && "border-st-confused",
                  locked && "cursor-default",
                )}
              >
                <input
                  type="radio"
                  name={`${uid}-opt`}
                  value={o.id}
                  aria-label={"plain" in o ? o.plain : undefined}
                  checked={isChosen}
                  onChange={() => {
                    setOption(o.id);
                    keepInView();
                  }}
                  className="peer absolute inset-0 m-0 size-full cursor-pointer opacity-0 disabled:cursor-default"
                />
                <span
                  aria-hidden
                  className="flex size-4 shrink-0 items-center justify-center rounded-full border border-rule peer-checked:border-paper after:size-2 after:rounded-full after:bg-paper after:opacity-0 peer-checked:after:opacity-100"
                />
                <span className={cn(idk || (locked && !isChosen && !verdict) ? "text-paper-dim" : "text-paper")}>
                  <Inline text={o.text} />
                </span>
                {verdict === "right" && <span className="caps ml-auto [--caps-size:0.625rem] text-st-solid">{tx.question.right}</span>}
                {verdict === "wrong" && !idk && <span className="caps ml-auto [--caps-size:0.625rem] text-st-confused">{tx.question.yours}</span>}
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="mt-4" disabled={locked}>
        <legend className="caps mb-2 text-paper-dim">{tx.question.howSure}</legend>
        <div className="flex flex-wrap gap-1.5">
          {tx.question.confidence.map((c, i) => {
            const on = (answer?.confidence ?? confidence) === i;
            return (
              <button
                key={c}
                type="button"
                aria-pressed={on}
                onClick={() => setConfidence(on ? null : i)}
                className={cn(
                  "caps h-10 flex-1 basis-[calc(50%-0.375rem)] rounded-pill border px-3 [--caps-size:0.625rem] whitespace-nowrap transition-colors duration-(--dur-s) ease-(--ease-out) disabled:cursor-default sm:basis-0",
                  on
                    ? "border-ribbon bg-cloth-hover text-paper"
                    : locked
                      ? "border-seam-soft text-paper-dim"
                      : "border-seam text-paper-dim hover:border-ribbon/60 hover:text-paper",
                )}
              >
                {c}
              </button>
            );
          })}
        </div>
      </fieldset>

      {!locked ? (
        <div className="mt-4">
          <button
            type="button"
            aria-expanded={thoughtOpen}
            aria-controls={`${uid}-thought`}
            onClick={() => {
              setThoughtOpen((v) => !v);
              keepInView();
            }}
            className="caps flex min-h-10 items-center gap-2 rounded-pill pr-2 text-paper-dim transition-colors duration-(--dur-s) ease-(--ease-out) hover:text-paper"
          >
            {tx.question.explain}
            <ChevronDown aria-hidden className={cn("size-3.5 transition-transform", thoughtOpen && "rotate-180")} />
          </button>
          {thoughtOpen && (
            <textarea
              id={`${uid}-thought`}
              aria-label={tx.question.thoughtLabel}
              rows={3}
              value={thought}
              onChange={(e) => setThought(e.target.value)}
              placeholder={tx.question.thoughtPlaceholder}
              className="mt-1 block w-full resize-none rounded-control border border-seam bg-cloth-deep px-3 py-2.5 text-body text-paper outline-none focus-visible:border-ribbon"
            />
          )}
        </div>
      ) : (
        answer.thought && (
          <p className="mt-4 flex gap-2 text-small text-paper-dim">
            <span>«{answer.thought}»</span>
          </p>
        )
      )}

      {!locked && (
        <div className="mt-4 flex items-center justify-end gap-3">
          {!option && <span className="caps text-paper-dim">{tx.question.pick}</span>}
          <button
            type="submit"
            disabled={!option}
            className="caps h-10 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-ribbon-hover disabled:bg-cloth-hover disabled:text-paper-dim"
          >
            {tx.question.submit}
          </button>
        </div>
      )}
    </form>
  );
}
