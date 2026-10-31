"use client";

import { ChevronRight, History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import type { SessionData, Turn } from "@/lib/mock/types";
import { scrollToId } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { Composer } from "./composer";
import { Duck } from "./duck";
import { IntervalDemo } from "./interval-demo";
import { PlanGraph } from "./plan-graph";
import { QuestionBlock, type Answer } from "./question-block";
import { RichText } from "./rich-text";
import { useSoon } from "./soon";
import { StateDot } from "./topic-card";

/** Колонка текста — мера страницы; справа на широком экране — поля с планом */
const MEASURE = "mx-auto w-full max-w-[34rem]";

export function SessionView({ id }: { id: string }) {
  const { radiusSession } = useMocks();
  return id === "new" ? <NewSession /> : <RadiusSession data={radiusSession} />;
}

function RadiusSession({ data }: { data: SessionData }) {
  const tx = useT();
  const soon = useSoon();
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [replied, setReplied] = useState(false);
  const [planOpen, setPlanOpen] = useState(true);
  const [chipOpen, setChipOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const header = useRef<HTMLElement>(null);
  const [headerGone, setHeaderGone] = useState(false);

  // Название сессии переезжает в колонтитул, когда шапка уходит из кадра
  useEffect(() => {
    const el = header.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setHeaderGone(!e.isIntersecting), {
      root: scroller.current,
      threshold: 0,
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // Заготовленная реакция учителя приходит после короткой паузы
  useEffect(() => {
    if (!answer) return;
    const t = setTimeout(() => setReplied(true), 900);
    return () => clearTimeout(t);
  }, [answer]);

  useEffect(() => {
    if (replied) scrollToId("reply", 16);
  }, [replied]);

  // Вход в сессию: сразу к открытому вопросу, главное действие — в кадре
  useEffect(() => {
    const t = requestAnimationFrame(() => scrollToId("q1", 16, "instant", "end"));
    return () => cancelAnimationFrame(t);
  }, []);

  const jump = (anchor: string) => {
    scrollToId(anchor, 16);
    setChipOpen(false);
  };

  const plan = replied ? data.planAfter : data.plan;
  const progress = { ...data.progress, ...(replied ? data.progressAfter : {}) };
  const questionOpen = !answer;

  const planProps = { plan, progress, onJump: jump };

  return (
    <div className="@container relative flex min-h-0 flex-1 flex-col">
      {/* Колонтитул: название сессии и фоновые агенты, не прокручивается */}
      <div className="flex min-h-12 shrink-0 items-center gap-3 border-b border-seam-soft px-4 md:px-7">
        <p
          aria-hidden={!headerGone}
          className={cn(
            "min-w-0 truncate font-serif font-bold text-paper transition-opacity duration-200",
            headerGone ? "opacity-100" : "opacity-0",
          )}
        >
          {data.title}
        </p>
        {!replied && (
          <p
            role="status"
            className="ml-auto flex shrink-0 items-center gap-2 rounded-full border border-seam-soft px-3 py-1 text-small text-paper-dim"
          >
            <Duck className="h-3.5 w-4" />
            <span className="max-sm:hidden">{data.agent.task}</span>
            <span className="sm:hidden">{tx.session.duckPreparing}</span>
            <Dots />
          </p>
        )}
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-8 md:px-7 md:pt-6 @[52rem]:pr-[17rem]">
        <header ref={header} className={MEASURE}>
          <h1 className="font-serif text-display font-bold tracking-[-0.01em] text-paper">{data.title}</h1>
          <p className="mt-1.5 text-paper-dim">{tx.session.goal(data.goal)}</p>
          <ul className="mt-3 flex flex-wrap gap-1.5" aria-label={tx.session.topicsLabel}>
            {data.topics.map((t) => (
              <li key={t.title}>
                <button
                  type="button"
                  onClick={() => soon(tx.session.topicPageSoon(t.title))}
                  className="flex h-10 items-center gap-2 rounded-full border border-seam px-3 text-small text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
                >
                  <StateDot state={t.state} className="size-2" />
                  {t.title}
                </button>
              </li>
            ))}
          </ul>
        </header>


        <div className={cn("mt-6 flex flex-col gap-7", MEASURE)}>
          {data.turns.map((turn) => (
            <TurnView
              key={turn.id}
              turn={turn}
              answer={answer}
              onAnswer={setAnswer}
              onMemory={(s) => soon(tx.soon.sessionHistory(s))}
            />
          ))}

          {answer && !replied && (
            <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
              {tx.session.teacherReading}
              <Dots />
            </p>
          )}

          {replied && answer && (
            <article id="reply" aria-label={tx.session.teacherReply} className="scroll-mt-6 text-paper">
              {answer.thought && (
                <p className="mb-3 text-paper">
                  {tx.session.youWrite(answer.thought)}
                </p>
              )}
              <RichText text={data.replies[answer.option].text} />
              <div className="mt-5 animate-in duration-500 ease-out slide-in-from-bottom-3">
                <p className="mb-2 flex items-center gap-2 text-small text-paper-dim">
                  <Duck className="h-3.5 w-4" />
                  {tx.session.duckDrew}
                </p>
                <IntervalDemo />
              </div>
            </article>
          )}
        </div>
      </div>

      <div className="relative px-4 pb-4 md:px-7 md:pb-5">
        {/* Широкий экран: план на полях справа, над полем ввода */}
        <PlanGraph
          {...planProps}
          open={planOpen}
          onToggle={() => setPlanOpen((v) => !v)}
          className="absolute right-4 bottom-full mb-3 hidden w-54 md:right-7 @[52rem]:block"
        />
        {/* Узкий экран: план — строка прогресса над полем ввода */}
        <PlanGraph
          {...planProps}
          open={chipOpen}
          onToggle={() => setChipOpen((v) => !v)}
          className="mb-2 @[52rem]:hidden"
        />
        <Composer
          label={tx.session.composerLabel}
          placeholder={questionOpen ? tx.session.askPlaceholder : tx.session.replyPlaceholder}
          submitText={tx.session.send}
          primary={!questionOpen}
          compact={questionOpen}
        />
      </div>
    </div>
  );
}

function TurnView({
  turn,
  answer,
  onAnswer,
  onMemory,
}: {
  turn: Turn;
  answer: Answer | null;
  onAnswer: (a: Answer) => void;
  onMemory: (session: string) => void;
}) {
  const tx = useT();
  const { dayLabel } = useFmt();
  if (turn.kind === "student") {
    return (
      <StudentLine id={turn.id} text={turn.text} />
    );
  }
  if (turn.kind === "question") {
    return <QuestionBlock id={turn.id} num={turn.num} prompt={turn.prompt} options={turn.options} answer={answer} onAnswer={onAnswer} />;
  }
  return (
    <article id={turn.id} aria-label={tx.session.teacherTurn} className="scroll-mt-6 text-paper">
      {turn.memory && (
        <button
          type="button"
          onClick={() => onMemory(turn.memory!.session)}
          className="mb-3 flex min-h-10 items-center gap-2 rounded-full border border-seam-soft px-3 text-small text-paper-dim transition-colors hover:border-rule hover:text-paper"
        >
          <History aria-hidden className="size-3.5" />
          {tx.session.fromMemory}: «{turn.memory.session}», <span className="num">{dayLabel(turn.memory.date)}</span>
        </button>
      )}
      <RichText text={turn.text} />
      {turn.thinking && (
        <details className="group mt-4">
          <summary className="flex min-h-10 w-fit cursor-pointer list-none items-center gap-1.5 rounded-control text-small text-paper-dim transition-colors hover:text-paper [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden className="size-3.5 transition-transform group-open:rotate-90" />
            {tx.session.thinking}
          </summary>
          <p className="mt-1 border-l border-seam pl-4 text-small text-paper-dim">{turn.thinking}</p>
        </details>
      )}
    </article>
  );
}

function NewSession() {
  const tx = useT();
  const { pending, domain } = useShell();
  const text = pending ?? tx.session.newDefaultMessage;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-8 md:px-7 md:pt-6">
        <header className={MEASURE}>
          <h1 className="line-clamp-2 font-serif text-display font-bold tracking-[-0.01em] text-paper">{tx.session.newTitle}</h1>
          <p className="mt-1.5 text-paper-dim">{tx.session.newMeta(domain.title)}</p>
        </header>
        <div className={cn("mt-6 flex flex-col gap-7", MEASURE)}>
          <StudentLine text={text} />
          <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
            {tx.session.newThinking}
            <Dots />
          </p>
          <p className="text-small text-paper-dim">{tx.session.planLater}</p>
        </div>
      </div>
      <div className="px-4 pb-4 md:px-7 md:pb-5">
        <Composer label={tx.session.composerLabel} placeholder={tx.session.newPlaceholder} submitText={tx.session.send} />
      </div>
    </div>
  );
}

/** Реплика студента — строка на развороте: метка «Ты» на полях и тонкие швы */
function StudentLine({ id, text }: { id?: string; text: string }) {
  const tx = useT();
  return (
    <p id={id} className="flex scroll-mt-16 gap-3 border-y border-seam-soft py-3 whitespace-pre-wrap text-paper">
      <span className="num shrink-0 pt-0.5 text-small text-paper-dim">{tx.session.you}</span>
      <span>{text}</span>
    </p>
  );
}

/** Три точки «работает»; при reduced-motion просто стоят */
function Dots() {
  return (
    <span aria-hidden className="inline-flex gap-0.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1 animate-pulse rounded-full bg-paper-dim"
          style={{ animationDelay: `${i * 180}ms` }}
        />
      ))}
    </span>
  );
}
