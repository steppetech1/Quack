"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
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

const CARD_TITLE = "font-serif text-[18px] leading-tight font-bold text-paper";
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
        <span className="num text-small text-paper-dim">{notesMeta(details, t, dayLabel)}</span>
      </span>
      <span aria-hidden className={cn("mt-3 block h-[116px] overflow-hidden px-5 text-[15px] leading-relaxed text-paper", MASK_FADE)}>
        {details.summary && <span className="block">{details.summary}</span>}
        {first && (
          <span className="mt-2 block text-paper-dim">
            <Inline text={first.text.split("\n")[0]} />
          </span>
        )}
      </span>
      <span className="flex min-h-9 items-center px-5 pb-3 text-small">
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
          <p className="num text-small tracking-wide text-paper-dim uppercase">{t.artifacts.gist}</p>
          <p className="mt-1 mb-6 text-[18px] leading-snug">{details.summary}</p>
        </>
      )}
      <div className="flex flex-col gap-4">
        {details.notes.map((n, i) => (
          <div key={i}>
            {n.kind !== "text" && <p className="text-small font-semibold text-paper-dim">{t.artifacts.noteLabel[n.kind]}</p>}
            <RichText text={n.text} />
            {n.src && <p className="num mt-1 text-small text-paper-dim">{n.src}</p>}
          </div>
        ))}
      </div>

      {details.when.length > 0 && (
        <div className="mt-6">
          <h3 className="text-body font-semibold">{t.artifacts.whenToUse}</h3>
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
          <h3 className="text-body font-semibold">{t.artifacts.workedExample}</h3>
          <p className="mt-1.5">
            <Inline text={details.example.title} />
          </p>
          <RichText text={details.example.text} className="mt-2" />
        </div>
      )}

      {details.traps.length > 0 && (
        <aside aria-labelledby="traps-h" className="mt-7 rounded-[14px] bg-cloth-raised p-4 text-body">
          <div className="flex items-center justify-between gap-3">
            <h3 id="traps-h" className="font-semibold text-paper">
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
        <span className="num text-small text-paper-dim">{t.artifacts.cheatPages}</span>
      </span>
      <span aria-hidden className={cn("mt-3 block h-[132px] overflow-hidden px-5 pb-4 text-paper", MASK_FADE)}>
        <span className="num block text-small tracking-wide text-paper-dim uppercase">{t.artifacts.formulas}</span>
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
      <h3 className="num text-small tracking-wide text-paper-dim uppercase">{t.artifacts.formulas}</h3>
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

      <h3 className="num mt-7 text-small tracking-wide text-paper-dim uppercase">{t.artifacts.methods}</h3>
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

      <h3 className="num mt-7 text-small tracking-wide text-paper-dim uppercase">{t.artifacts.commonTraps}</h3>
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
          <span aria-hidden className="absolute inset-0 translate-x-3 translate-y-3 rounded-[16px] border border-seam-soft bg-cloth-deep" />
          <span aria-hidden className="absolute inset-0 translate-x-1.5 translate-y-1.5 rounded-[16px] border border-seam-soft bg-cloth-hover" />
        </>
      }
    >
      <span className="block px-4 pt-4 pb-4">
        <span className={cn(CARD_TITLE, "flex items-baseline gap-2")}>
          {t.artifacts.cards}
          <span className="num text-small font-normal text-paper-dim">· {cards.length}</span>
        </span>
        <span className="mt-2.5 flex flex-wrap gap-1.5">
          {kinds.map(({ k, n }) => (
            <span key={k} className={cn(PILL, "px-2 py-px text-[12px]")}>
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
export function DeckViewer({ cards }: { cards: StudyCard[] }) {
  const t = useT();
  const [i, setI] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [rated, setRated] = useState<(number | null)[]>(() => cards.map(() => null));
  const [done, setDone] = useState(false);
  const last = cards.length - 1;

  const step = (to: number) => {
    if (to < 0 || to > last) return;
    setI(to);
    setRevealed(false);
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

  useEffect(() => {
    if (done) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "ArrowRight") step(i + 1);
      else if (e.key === "ArrowLeft") step(i - 1);
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
        <p className="font-serif text-[26px] leading-tight font-bold text-paper">
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
          className="mt-6 inline-flex h-11 items-center rounded-control bg-ribbon px-5 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover"
        >
          {t.artifacts.again}
        </button>
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

      <div className="mt-3 min-h-[280px] rounded-[18px] border border-seam bg-cloth-raised p-6 md:p-8">
        <p className="text-[22px] leading-snug text-paper">
          <Inline text={card.front} />
        </p>
        {revealed && (
          <div className="mt-6 border-t border-seam-soft pt-5 text-[17px] leading-relaxed text-paper animate-in fade-in slide-in-from-bottom-1 duration-200 motion-reduce:animate-none">
            <RichText text={card.back} />
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
                autoFocus={r === 0}
                onClick={() => rate(r)}
                className="inline-flex h-10 items-center rounded-full border border-seam px-4 text-small text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
              >
                {label}
              </button>
            ))}
          </div>
        ) : (
          <button
            type="button"
            autoFocus
            onClick={() => setRevealed(true)}
            className="inline-flex h-11 flex-1 items-center justify-center rounded-control bg-ribbon px-5 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover sm:flex-none"
          >
            {t.artifacts.reveal}
          </button>
        )}
        <IconButton label={t.artifacts.next} disabled={i === last} onClick={() => step(i + 1)} className="ml-auto disabled:opacity-40">
          <ChevronRight />
        </IconButton>
      </div>
      <p className="mt-3 text-small text-paper-dim max-sm:hidden">{t.artifacts.arrowsHint}</p>
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
