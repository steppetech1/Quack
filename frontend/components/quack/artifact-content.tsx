"use client";

import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, Space } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useFmt, useT } from "@/lib/i18n/provider";
import type { Dict } from "@/lib/i18n/dict";
import type { Canvas, CardKind, CheatSheet, StudyCard, TopicDetails } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { ExpandableCard } from "./artifact-window";
import { IconButton } from "./icon-button";
import { PILL, TEXT_BTN } from "./page-parts";
import { Inline, RichText } from "./rich-text";
import { CanvasThumb } from "./sketch";
import { useSoon } from "./soon";

const CARD_KINDS: CardKind[] = ["definition", "sign", "distinction", "myth"];

const CARD_TITLE = "display-caps text-[1.125rem] text-paper";
const MASK_FADE = "[mask-image:linear-gradient(to_bottom,#000_55%,transparent)]";

// ── Конспект ───────────────────────────────────────────────────────────

/** Сколько разделов в конспекте: суть, определения, когда применять, пример, ловушки */
function sectionCount(d: TopicDetails) {
  return [d.summary, d.notes.length, d.when.length, d.example, d.traps.length].filter(Boolean).length;
}

export function notesMeta(d: TopicDetails, t: Dict, dayLabel: (iso: string) => string) {
  const last = d.moments.at(-1);
  return `${last ? `${t.artifacts.updated(dayLabel(last.date))} · ` : ""}${t.artifacts.sections(sectionCount(d))}`;
}

/** Карточка-превью конспекта: начало «сути» и первое определение, низ растворяется */
export function NotesCard({ details }: { details: TopicDetails }) {
  const t = useT();
  const { dayLabel } = useFmt();
  const first = details.notes[0];
  return (
    <ExpandableCard artifact="notes" className="w-full">
      <span className="flex flex-wrap items-baseline justify-between gap-x-4 px-5 pt-4">
        <span className={CARD_TITLE}>{t.artifacts.notesTitle}</span>
        <span className="caps text-paper-dim">{notesMeta(details, t, dayLabel)}</span>
      </span>
      <span aria-hidden className={cn("mt-3 block h-[116px] overflow-hidden px-5 text-[15px] leading-relaxed text-paper", MASK_FADE)}>
        {details.summary && <span className="block">{details.summary}</span>}
        {first && (
          <span className="mt-2 block text-paper-dim">
            <Inline text={first.text.split("\n")[0]} />
          </span>
        )}
      </span>
      <span className="caps flex min-h-9 items-center px-5 pb-3 [--caps-size:0.625rem]">
        {details.traps.length > 0 ? (
          <span className="text-st-confused">{t.artifacts.yourTrapsCount(details.traps.length)}</span>
        ) : (
          <span className="text-paper-dim">{t.artifacts.openNotes}</span>
        )}
      </span>
    </ExpandableCard>
  );
}

/** Полный конспект: то, что раньше лежало прямо на странице */
export function NotesContent({ details }: { details: TopicDetails }) {
  const t = useT();
  const { dayLabel } = useFmt();
  const soon = useSoon();
  return (
    <div className="mx-auto max-w-[68ch] text-[16px] leading-relaxed text-paper">
      {details.summary && (
        <>
          <p className="caps text-paper-dim">{t.artifacts.gist}</p>
          <p className="mt-1 mb-6 text-[18px] leading-snug">{details.summary}</p>
        </>
      )}
      <div className="flex flex-col gap-4">
        {details.notes.map((n, i) => (
          <div key={i}>
            {n.kind !== "text" && <p className="caps mb-1 text-paper-dim">{t.artifacts.noteLabel[n.kind]}</p>}
            <RichText text={n.text} />
            {n.src && <p className="num mt-1 text-small text-paper-dim">{n.src}</p>}
          </div>
        ))}
      </div>

      {details.when.length > 0 && (
        <div className="mt-6">
          <h3 className="display-caps text-[1.25rem]">{t.artifacts.whenToUse}</h3>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 marker:text-rule">
            {details.when.map((w, i) => (
              <li key={i}>
                <Inline text={w} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {details.example && (
        <div className="mt-6">
          <h3 className="display-caps text-[1.25rem]">{t.artifacts.workedExample}</h3>
          <p className="mt-1.5">
            <Inline text={details.example.title} />
          </p>
          <RichText text={details.example.text} className="mt-2" />
        </div>
      )}

      {details.traps.length > 0 && (
        <aside aria-labelledby="traps-h" className="mt-7 rounded-[2px] border border-seam bg-cloth-raised p-4 text-body">
          <div className="flex items-center justify-between gap-3">
            <h3 id="traps-h" className="caps text-paper">
              {t.artifacts.yourTraps}
            </h3>
            <button type="button" onClick={() => soon(t.artifacts.editPersonalSoon)} className={cn(TEXT_BTN, "-my-1.5 -mr-2")}>
              {t.artifacts.edit}
            </button>
          </div>
          <ul className="mt-2 flex flex-col gap-3">
            {details.traps.map((trap, i) => (
              <li key={i}>
                <p className="num text-small text-paper-dim">{dayLabel(trap.date)}</p>
                <p className="mt-0.5 text-paper">
                  <Inline text={trap.text} />
                </p>
              </li>
            ))}
          </ul>
        </aside>
      )}
    </div>
  );
}

// ── Шпаргалка ──────────────────────────────────────────────────────────

export function CheatCard({ cheat, title }: { cheat: CheatSheet; title: string }) {
  const t = useT();
  return (
    <ExpandableCard artifact="cheatsheet" className="w-full">
      <span className="flex flex-wrap items-baseline justify-between gap-x-4 px-5 pt-4">
        <span className={CARD_TITLE}>{title}</span>
        <span className="caps text-paper-dim">{t.artifacts.cheatPages}</span>
      </span>
      <span aria-hidden className={cn("mt-3 block h-[132px] overflow-hidden px-5 pb-4 text-paper", MASK_FADE)}>
        <span className="caps block text-paper-dim">{t.artifacts.formulas}</span>
        <span className="mt-2 grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {cheat.formulas.slice(0, 4).map((f) => (
            <span key={f.name} className="block">
              <span className="block text-small text-paper-dim">{f.name}</span>
              <span className="block">
                <Inline text={`$${f.tex}$`} />
              </span>
            </span>
          ))}
        </span>
      </span>
    </ExpandableCard>
  );
}

export function CheatSheetContent({ cheat }: { cheat: CheatSheet }) {
  const t = useT();
  return (
    <div className="mx-auto max-w-[860px] text-[16px] text-paper">
      <h3 className="caps text-paper-dim">{t.artifacts.formulas}</h3>
      <dl className="mt-2 grid gap-x-8 gap-y-4 sm:grid-cols-2">
        {cheat.formulas.map((f) => (
          <div key={f.name}>
            <dt className="text-small text-paper-dim">{f.name}</dt>
            <dd className="text-paper">
              <Inline text={`$${f.tex}$`} />
            </dd>
          </div>
        ))}
      </dl>

      <h3 className="caps mt-7 text-paper-dim">{t.artifacts.methods}</h3>
      <ul className="mt-2">
        {cheat.methods.map((m, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-3 border-b border-seam-soft py-2 last:border-0">
            <span className="min-w-0 flex-1 text-paper-dim">
              <Inline text={m.sign} />
            </span>
            <span aria-hidden className="text-paper-dim">
              →
            </span>
            <span className="w-full text-paper sm:w-64">{m.how}</span>
          </li>
        ))}
      </ul>

      <h3 className="caps mt-7 text-paper-dim">{t.artifacts.commonTraps}</h3>
      <ul className="mt-2 list-disc space-y-1.5 pl-5 text-paper marker:text-rule">
        {cheat.traps.map((trap, i) => (
          <li key={i}>
            <Inline text={trap} />
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Карточки (колода) ──────────────────────────────────────────────────

/** Колода: бумажная стопка из трёх листов, на верхнем — первая карточка */
export function DeckCard({ cards }: { cards: StudyCard[] }) {
  const t = useT();
  const kinds = CARD_KINDS
    .map((k) => ({ k, n: cards.filter((c) => c.kind === k).length }))
    .filter((x) => x.n > 0);
  return (
    <ExpandableCard
      artifact="cards"
      className="mr-3 mb-3 w-full sm:w-[272px]"
      behind={
        <>
          <span aria-hidden className="absolute inset-0 translate-x-3 translate-y-3 rounded-[2px] border border-seam-soft bg-cloth-deep" />
          <span aria-hidden className="absolute inset-0 translate-x-1.5 translate-y-1.5 rounded-[2px] border border-seam-soft bg-cloth-hover" />
        </>
      }
    >
      <span className="block px-4 pt-4 pb-4">
        <span className={cn(CARD_TITLE, "flex items-baseline gap-2")}>
          {t.artifacts.cards}
          <span className="num text-small text-paper-dim">· {cards.length}</span>
        </span>
        <span className="mt-2.5 flex flex-wrap gap-1.5">
          {kinds.map(({ k, n }) => (
            <span key={k} className={cn(PILL, "px-2.5 py-px")}>
              {t.artifacts.cardKind[k]}
              {n > 1 && <span className="num ml-1 text-paper">{n}</span>}
            </span>
          ))}
        </span>
        <span aria-hidden className="mt-3 line-clamp-3 block text-[15px] leading-snug text-paper">
          <Inline text={cards[0].front} />
        </span>
      </span>
    </ExpandableCard>
  );
}

const PROGRESS = "num text-small text-paper-dim";

/** Просмотр колоды: одна карточка, ответ по кнопке, оценка «как вспомнилось» */
/** A key on the legend under the deck: the shape of a key, so it reads as "press this" */
function Keycap({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-6 items-center justify-center gap-1 rounded-[3px] border border-seam bg-cloth-raised font-mono text-[11px] leading-none text-paper shadow-[0_1px_0_0_var(--seam)]",
        wide ? "px-2" : "min-w-6 px-1.5",
      )}
    >
      {children}
    </kbd>
  );
}

function KeyHint({ keys, label }: { keys: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="inline-flex items-center gap-1">{keys}</span>
      <span>{label}</span>
    </span>
  );
}

const ARROWS = (
  <>
    <Keycap>
      <ArrowLeft className="size-3.5" aria-label="←" />
    </Keycap>
    <Keycap>
      <ArrowRight className="size-3.5" aria-label="→" />
    </Keycap>
  </>
);
const SPACE = (
  <Keycap wide>
    <Space className="size-3.5" aria-hidden />
    Space
  </Keycap>
);

/** Backend decks only: change the text of a card or delete it. Each resolves with the text of the error, or `null` when done */
export type DeckManage = { edit: (id: string, prompt: string) => Promise<string | null>; remove: (id: string) => Promise<string | null> };

export function DeckViewer({ cards, manage }: { cards: StudyCard[]; manage?: DeckManage }) {
  const t = useT();
  const [i, setI] = useState(0);
  const [started, setStarted] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [rated, setRated] = useState<(number | null)[]>(() => cards.map(() => null));
  const [done, setDone] = useState(false);
  /** The rating the arrows are on once the card is turned over ("got it" first: the usual answer) */
  const [sel, setSel] = useState(2);
  /** The card on the list that is being edited (its text) or asked about deleting; one at a time */
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [manageBusy, setManageBusy] = useState(false);
  const [manageError, setManageError] = useState<string | null>(null);
  const rateRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const revealRef = useRef<HTMLButtonElement>(null);
  const last = cards.length - 1;

  const step = (to: number) => {
    if (to < 0 || to > last) return;
    setI(to);
    setRevealed(false);
    setSel(2);
  };
  const rate = (r: number) => {
    setRated((prev) => prev.map((x, k) => (k === i ? r : x)));
    if (i === last) setDone(true);
    else step(i + 1);
  };
  const restart = () => {
    setRated(cards.map(() => null));
    setDone(false);
    setI(0);
    setRevealed(false);
  };

  // The focus follows the card: on the "Show answer" button, and once the card is turned over on the chosen rating,
  // so Space and Enter do what the button says without any key handling of their own
  useEffect(() => {
    if (done || !started) return;
    (revealed ? rateRefs.current[sel] : revealRef.current)?.focus();
  }, [started, done, revealed, sel, i]);

  // Keys. Before the turn: ← → flip through the cards, Space turns the card over. After it: ← → move over the ratings,
  // Space (the focused rating) confirms, 1–4 rate at once
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if (typing) return;
      // The list ("Start") and the result ("Go again"): Space or Enter press the one button, whatever has the focus
      if (!started || done) {
        if ((e.key === " " || e.key === "Enter") && !(e.target instanceof HTMLButtonElement)) {
          e.preventDefault();
          if (done) restart();
          else setStarted(true);
        }
        return;
      }
      if (revealed) {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          setSel((v) => Math.min(t.artifacts.ratings.length - 1, v + 1));
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          setSel((v) => Math.max(0, v - 1));
        } else if (/^[1-9]$/.test(e.key) && Number(e.key) <= t.artifacts.ratings.length) {
          e.preventDefault();
          rate(Number(e.key) - 1);
        }
        return;
      }
      if (e.key === "ArrowRight") step(i + 1);
      else if (e.key === "ArrowLeft") step(i - 1);
      else if ((e.key === " " || e.key === "Enter") && !(e.target instanceof HTMLButtonElement)) {
        // focus is nowhere useful: turn the card over; on a button the button does its own job
        e.preventDefault();
        setRevealed(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (done) {
    const good = rated.filter((r) => r !== null && r >= 2).length;
    const share = good / cards.length;
    const verdict = share >= 0.75 ? t.artifacts.verdictGood : share >= 0.5 ? t.artifacts.verdictMid : t.artifacts.verdictBad;
    const weak = cards.find((_, k) => rated[k] !== null && (rated[k] as number) <= 1);
    return (
      <div className="mx-auto max-w-[640px]">
        <p className="display-caps text-[1.625rem] text-paper">
          {t.artifacts.score(good, cards.length, verdict)}
        </p>
        {weak && (
          <p className="mt-2 text-[16px] text-paper-dim">
            {t.artifacts.weakPre}<Inline text={weak.front} />{t.artifacts.weakPost}
          </p>
        )}
        <ul className="mt-6">
          {cards.map((c, k) => (
            <li key={c.id} className="flex items-baseline gap-3 border-b border-seam-soft py-2.5 last:border-0">
              <span className="min-w-0 flex-1 text-paper">
                <Inline text={c.front} />
              </span>
              <span className={cn("num shrink-0 text-small", (rated[k] ?? 0) <= 1 ? "text-st-confused" : "text-paper-dim")}>{t.artifacts.ratings[rated[k] ?? 0]}</span>
            </li>
          ))}
        </ul>
        <button
          type="button"
          autoFocus
          onClick={restart}
          className="caps mt-6 inline-flex h-11 items-center rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-ribbon-hover"
        >
          {t.artifacts.again}
        </button>
      </div>
    );
  }

  const manageRun = async (job: () => Promise<string | null>) => {
    setManageBusy(true);
    setManageError(null);
    const err = await job();
    setManageBusy(false);
    if (err) setManageError(err);
    else {
      setEditing(null);
      setAsking(null);
    }
  };

  // First the list of the deck and "Start"
  if (!started) {
    return (
      <div className="mx-auto max-w-[640px]">
        <p className="text-paper-dim">{cards.length ? t.artifacts.deckIntro(cards.length) : t.artifacts.deckEmpty}</p>
        <ol className="mt-5 border-t border-seam-soft">
          {cards.map((c, k) => (
            <li key={c.id} className="border-b border-seam-soft py-3">
              {manage && editing?.id === c.id ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (editing.text.trim()) void manageRun(() => manage.edit(c.id, editing.text.trim()));
                  }}
                >
                  <label className="caps mb-1.5 block text-paper-dim" htmlFor={`card-${c.id}`}>
                    {t.artifacts.cardEditLabel}
                  </label>
                  <textarea
                    id={`card-${c.id}`}
                    autoFocus
                    rows={3}
                    value={editing.text}
                    onChange={(e) => setEditing({ id: c.id, text: e.target.value })}
                    className="block w-full rounded-control border border-seam bg-cloth-deep px-3 py-2 text-body text-paper outline-none focus-visible:border-ribbon"
                  />
                  <div className="mt-2 flex gap-2">
                    <button type="submit" disabled={manageBusy || !editing.text.trim()} className="caps h-10 rounded-pill bg-ribbon px-5 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover disabled:opacity-50">
                      {t.artifacts.cardSave}
                    </button>
                    <button type="button" onClick={() => setEditing(null)} className={TEXT_BTN}>
                      {t.artifacts.cardCancel}
                    </button>
                  </div>
                </form>
              ) : (
                <div className="flex items-baseline gap-3">
                  <span className="num w-6 shrink-0 text-small text-paper-dim">{k + 1}</span>
                  <span className="min-w-0 flex-1 text-paper">
                    <Inline text={c.front} />
                  </span>
                  {c.mine && <span className={PILL}>{t.artifacts.cardMine}</span>}
                  <span className={PILL}>{t.artifacts.cardKind[c.kind]}</span>
                </div>
              )}
              {manage && editing?.id !== c.id &&
                (asking === c.id ? (
                  <div className="mt-1 flex flex-wrap items-center gap-2 pl-9">
                    <span className="text-small text-paper-dim">{t.artifacts.cardDeleteAsk}</span>
                    <button type="button" disabled={manageBusy} onClick={() => void manageRun(() => manage.remove(c.id))} className={cn(TEXT_BTN, "text-st-confused")}>
                      {t.artifacts.cardDelete}
                    </button>
                    <button type="button" onClick={() => setAsking(null)} className={TEXT_BTN}>
                      {t.artifacts.cardCancel}
                    </button>
                  </div>
                ) : (
                  <div className="mt-1 flex gap-1 pl-9">
                    <button type="button" onClick={() => { setAsking(null); setManageError(null); setEditing({ id: c.id, text: c.front }); }} className={TEXT_BTN}>
                      {t.artifacts.cardEdit}
                    </button>
                    <button type="button" onClick={() => { setEditing(null); setManageError(null); setAsking(c.id); }} className={TEXT_BTN}>
                      {t.artifacts.cardDelete}
                    </button>
                  </div>
                ))}
            </li>
          ))}
        </ol>
        {manageError && (
          <p role="alert" className="mt-3 text-small text-st-confused">
            {manageError}
          </p>
        )}
        {cards.length > 0 && (<button
          type="button"
          autoFocus
          onClick={() => setStarted(true)}
          className="caps mt-6 inline-flex h-11 items-center rounded-pill bg-ribbon px-8 text-on-ribbon transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-ribbon-hover"
        >
          {t.artifacts.start}
        </button>)}
      </div>
    );
  }

  const card = cards[i];
  return (
    <div className="mx-auto max-w-[640px]">
      <div className="flex items-center justify-between gap-3">
        <p className={PROGRESS} aria-live="polite">
          <span className="text-paper">{i + 1}</span> / {cards.length}
        </p>
        <span className={PILL}>{t.artifacts.cardKind[card.kind]}</span>
      </div>

      <div className="mt-3 min-h-[280px] rounded-[2px] border border-seam bg-cloth-raised p-6 md:p-8">
        <p className="text-[22px] leading-snug text-paper">
          <Inline text={card.front} />
        </p>
        {revealed && (
          <div className="mt-6 border-t border-seam-soft pt-5 text-[17px] leading-relaxed text-paper animate-in fade-in slide-in-from-bottom-1 duration-200 motion-reduce:animate-none">
            <RichText text={card.back || t.artifacts.noAnswer} />
          </div>
        )}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <IconButton label={t.artifacts.prev} disabled={i === 0} onClick={() => step(i - 1)} className="disabled:opacity-40">
          <ChevronLeft />
        </IconButton>
        {revealed ? (
          <div role="group" aria-label={t.artifacts.howRemembered} className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            {t.artifacts.ratings.map((label, r) => (
              <button
                key={label}
                type="button"
                ref={(el) => {
                  rateRefs.current[r] = el;
                }}
                tabIndex={r === sel ? 0 : -1}
                onClick={() => rate(r)}
                className="caps inline-flex h-10 items-center rounded-pill border border-seam px-5 text-paper transition-colors duration-(--dur-s) ease-(--ease-out) hover:border-ribbon hover:bg-cloth-hover"
              >
                <span className="num mr-2 text-paper-dim">{r + 1}</span>
                {label}
              </button>
            ))}
          </div>
        ) : (
          <button
            type="button"
            ref={revealRef}
            onClick={() => setRevealed(true)}
            className="caps inline-flex h-11 flex-1 items-center justify-center rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-(--ease-out) hover:bg-ribbon-hover sm:flex-none"
          >
            {t.artifacts.reveal}
          </button>
        )}
        <IconButton label={t.artifacts.next} disabled={i === last} onClick={() => step(i + 1)} className="ml-auto disabled:opacity-40">
          <ChevronRight />
        </IconButton>
      </div>
      {/* What the keys do right now */}
      <p className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-small text-paper-dim max-sm:hidden" aria-label={t.artifacts.keysLabel}>
        {revealed ? (
          <>
            <KeyHint keys={ARROWS} label={t.artifacts.keysChoose} />
            <KeyHint keys={SPACE} label={t.artifacts.keysConfirm} />
            <KeyHint keys={<Keycap wide>1–{t.artifacts.ratings.length}</Keycap>} label={t.artifacts.keysRate} />
          </>
        ) : (
          <>
            <KeyHint keys={ARROWS} label={t.artifacts.keysFlip} />
            <KeyHint keys={SPACE} label={t.artifacts.keysTurn} />
          </>
        )}
      </p>
    </div>
  );
}

// ── Решённые задачи ────────────────────────────────────────────────────

/** Карточка решённой задачи: миниатюра почерка и подпись */
export function CanvasCard({ canvas, caption, date }: { canvas: Canvas; caption: string; date: string }) {
  const { dayLabel } = useFmt();
  return (
    <ExpandableCard artifact={`canvas:${canvas.id}`} className="w-full sm:w-[196px]" buttonClassName="group p-2.5">
      <CanvasThumb canvas={canvas} className="bg-cloth group-hover:bg-cloth-hover" />
      <span className="mt-2 block line-clamp-2 px-0.5 text-small leading-snug text-paper">{caption}</span>
      <span className="num block px-0.5 pb-0.5 text-small text-paper-dim">{dayLabel(date)}</span>
    </ExpandableCard>
  );
}
