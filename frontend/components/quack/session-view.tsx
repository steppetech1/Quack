"use client";

import { ChevronRight, History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useFmt, useMocks, useT } from "@/lib/i18n/provider";
import type { SessionData, Turn } from "@/lib/mock/types";
import { scrollToId } from "@/lib/scroll";
import { SKILL_CREATOR, skillInvocation } from "@/lib/skills";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { Composer } from "./composer";
import { Duck } from "./duck";
import { IntervalDemo } from "./interval-demo";
import { PlanGraph } from "./plan-graph";
import { QuestionBlock, type Answer } from "./question-block";
import { RichText } from "./rich-text";
import { SkillProposalCard, type Proposal } from "./skill-proposal";
import { useSoon } from "./soon";
import { SentFiles } from "./attachments";
import { SessionTools } from "./live/session-tools";
import { DemoCard, NoteRefCard } from "./workspace/doc-cards";
import { publishDemos } from "./workspace/session-demos";
import { StudentText } from "./workspace/student-text";
import { StateDot } from "./topic-card";

/** Колонка текста — мера страницы; справа на широком экране — поля с планом */
const MEASURE = "mx-auto w-full max-w-[34rem]";

export function SessionView({ id }: { id: string }) {
  const { radiusSession } = useMocks();
  return id === "new" ? <NewSession /> : <RadiusSession id={id} data={radiusSession} />;
}

/** The mock of the built-in `/skill-creator` talk: what the learner and the teacher said, in order */
type Talk = { id: string; role: "student" | "teacher"; text: string; proposal?: Proposal };

function RadiusSession({ id, data }: { id: string; data: SessionData }) {
  const tx = useT();
  const { domain } = useShell();
  const soon = useSoon();
  const mock = tx.skillCreatorMock;
  const hit = (words: string, text: string) => new RegExp(words, "i").test(text);
  const [talk, setTalk] = useState<Talk[]>([]);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [typing, setTyping] = useState(false);
  const stage = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  // the demos of the turns, for the «Visualizations» document of the pane
  useEffect(() => {
    publishDemos(
      id,
      data.turns.flatMap((t) => (t.kind === "teacher" && t.demo ? [{ requestId: t.demo.id, title: t.demo.title, status: "ready" as const, mock: true }] : [])),
    );
  }, [id, data.turns]);

  // Without the backend `/skill-creator` is a script: one question, then a proposal; «shorter», «cards» change it, «thanks» ends it
  const send = (text: string) => {
    const call = skillInvocation(text);
    if (call !== SKILL_CREATOR && !(creatorOpen && !call)) return soon(tx.composer.soonReply);
    const n = talk.length;
    setTalk((all) => [...all, { id: `m${n}`, role: "student", text }]);
    setTyping(true);
    const reply = (t: string, p?: Omit<Proposal, "element_id">): Talk => ({ id: `m${n + 1}`, role: "teacher", text: t, proposal: p && { ...p, element_id: `p${n + 1}` } });
    const binding = (): Proposal["artifact_type"] => (hit(mock.cardsRe, text) ? "cards" : hit(mock.cheatRe, text) ? "cheatsheet" : "notes");
    let next: Talk;
    if (call === SKILL_CREATOR && stage.current === 0) {
      stage.current = 1;
      next = reply(mock.ask);
    } else if (stage.current === 1) {
      stage.current = 2;
      next = reply(mock.first, { name: mock.name, text: mock.text, artifact_type: binding() });
    } else if (hit(mock.byeRe, text)) {
      stage.current = 0;
      setCreatorOpen(false);
      next = reply(mock.bye);
    } else {
      const short = hit(mock.shorterRe, text);
      const cards = hit(mock.cardsRe, text);
      next = reply(short ? mock.shorter : cards ? mock.cards : mock.again, { name: mock.name, text: short ? mock.textShort : mock.text, artifact_type: cards ? "cards" : "notes" });
    }
    if (stage.current > 0) setCreatorOpen(true);
    timer.current = setTimeout(() => {
      setTyping(false);
      setTalk((all) => [...all, next]);
      requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));
    }, 800);
    requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));
  };
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [replied, setReplied] = useState(false);
  const [planOpen, setPlanOpen] = useState(true);
  const [chipOpen, setChipOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const header = useRef<HTMLElement>(null);
  const [headerGone, setHeaderGone] = useState(false);
  /** The whole chat area takes files dragged from the OS (the input box attaches them) */
  const [chatEl, setChatEl] = useState<HTMLDivElement | null>(null);

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
    <div ref={setChatEl} className="@container relative flex min-h-0 flex-1 flex-col">
      {/* Колонтитул: название сессии и фоновые агенты, не прокручивается */}
      <div className="flex min-h-12 shrink-0 items-center gap-3 border-b border-seam-soft px-4 md:px-7">
        <p
          aria-hidden={!headerGone}
          className={cn(
            "display-caps min-w-0 truncate text-paper transition-opacity duration-(--dur-s) ease-out",
            headerGone ? "opacity-100" : "opacity-0",
          )}
        >
          {data.title}
        </p>
        {!replied && (
          <p
            role="status"
            className="ml-auto flex shrink-0 items-center gap-2 rounded-pill caps border border-seam-soft px-3 py-1 text-paper-dim"
          >
            <Duck className="h-3.5 w-4" />
            <span className="max-sm:hidden">{data.agent.task}</span>
            <span className="sm:hidden">{tx.session.duckPreparing}</span>
            <Dots />
          </p>
        )}
        {/* the tool buttons are drawn in the top bar of the session (a portal into the app shell) */}
        <SessionTools sessionId={id} domainId={domain.id} code={null} />
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-8 md:px-7 md:pt-6 @[52rem]:pr-[17rem]">
        <header ref={header} className={MEASURE}>
          <h1 className="display-caps text-display text-paper">{data.title}</h1>
          <p className="mt-2 text-paper-dim">{tx.session.goal(data.goal)}</p>
          <ul className="mt-3 flex flex-wrap gap-1.5" aria-label={tx.session.topicsLabel}>
            {data.topics.map((t) => (
              <li key={t.title}>
                <button
                  type="button"
                  onClick={() => soon(tx.session.topicPageSoon(t.title))}
                  className="caps flex h-10 items-center gap-2 rounded-pill border border-seam px-4 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon"
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

          {talk.map((m) =>
            m.role === "student" ? (
              <StudentLine key={m.id} id={m.id} text={m.text} />
            ) : (
              <article key={m.id} id={m.id} aria-label={tx.session.teacherTurn} className="text-paper">
                <RichText text={m.text} />
                {m.proposal && (
                  <div className="mt-4">
                    <SkillProposalCard turnSeq={1000 + Number(m.id.slice(1))} proposal={m.proposal} />
                  </div>
                )}
              </article>
            ),
          )}
          {typing && (
            <p role="status" className="flex items-center gap-2 text-small text-paper-dim">
              {tx.session.teacherThinking}
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
          onSend={send}
          creator
          dropZone={chatEl}
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
    return <StudentLine id={turn.id} text={turn.text} files={turn.files} />;
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
          className="caps mb-3 flex min-h-10 items-center gap-2 rounded-pill border border-seam-soft px-4 text-paper-dim transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-paper"
        >
          <History aria-hidden className="size-3.5" />
          {tx.session.fromMemory}: «{turn.memory.session}», <span className="num">{dayLabel(turn.memory.date)}</span>
        </button>
      )}
      <RichText text={turn.text} />
      {(turn.demo || turn.note) && (
        <div className="mt-4 flex flex-col gap-2">
          {turn.demo && <DemoCard id={turn.demo.id} title={turn.demo.title} mock />}
          {turn.note && <NoteRefCard id={turn.note.id} title={turn.note.title} preview={turn.note.preview} />}
        </div>
      )}
      {turn.thinking && (
        <details className="group mt-4">
          <summary className="caps flex min-h-10 w-fit cursor-pointer list-none items-center gap-1.5 rounded-control text-paper-dim transition-colors duration-(--dur-s) ease-out hover:text-paper [&::-webkit-details-marker]:hidden">
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
          <h1 className="display-caps line-clamp-2 text-display text-paper">{tx.session.newTitle}</h1>
          <p className="mt-2 text-paper-dim">{tx.session.newMeta(domain.title)}</p>
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

/** Реплика студента — контурный пузырь справа у края колонки; файлы сообщения — под ним */
function StudentLine({ id, text, files }: { id?: string; text: string; files?: { id: string; name: string }[] }) {
  const tx = useT();
  return (
    <div id={id} className="flex scroll-mt-16 flex-col items-end">
      <p
        role="group"
        aria-label={tx.session.you}
        className="max-w-[85%] min-w-0 rounded-[1rem] border border-seam bg-cloth-hover px-4 py-2.5 whitespace-pre-wrap text-paper [overflow-wrap:anywhere]"
      >
        <StudentText text={text} />
      </p>
      {files?.length ? <SentFiles files={files} /> : null}
    </div>
  );
}

/** Три точки «работает»; при reduced-motion просто стоят */
function Dots() {
  return (
    <span aria-hidden className="inline-flex gap-0.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1 animate-pulse rounded-pill bg-paper-dim"
          style={{ animationDelay: `${i * 180}ms` }}
        />
      ))}
    </span>
  );
}
