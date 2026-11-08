"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { topicState } from "@/lib/api/adapters";
import { answerCard, studyCardsFromApi } from "@/lib/api/cards";
import { ApiError, del, get, idem, patch, post, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { type Intent, startSession } from "@/lib/api/sessions";
import { useT } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";
import { useShell } from "../app-shell";
import { ArtifactHost, ExpandableCard, type WindowSpec } from "../artifact-window";
import { DeckCard, DeckViewer, type DeckAnswers, type DeckManage } from "../artifact-content";
import { Crumbs, MemoryRing, PageFrame, PageSection, PILL, TEXT_BTN } from "../page-parts";
import { useNotice } from "../soon";
import { useErrorText } from "./error-text";
import { Markdown, plainText } from "./markdown";

type Block = { block_id: string; kind: string; content: string };
type Loaded = {
  topic: S["TopicView"];
  /** notes of the topic as blocks (`GET /v1/topics/{id}/notes`) */
  notes: S["ArtifactResponse"] | null;
  cards: S["CardsResponse"] | null;
  /** the cheat sheet belongs to the hub of the topic, not to the topic (`GET /v1/hubs/{id}/cheatsheet`) */
  hubId: string | null;
  cheat: S["CheatsheetResponse"] | null;
};

/** 404 on an optional part of the page means "nothing yet", not an error */
const orNone = <T,>(p: Promise<T>) =>
  p.catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });
const ACTION_BTN =
  "caps inline-flex h-10 items-center justify-center rounded-pill px-6 transition-colors duration-(--dur-s) ease-out disabled:opacity-50";

/** Topic page on backend data: state, skills, mix-ups, notes and the cheat sheet */
export function TopicLive({ id }: { id: string }) {
  const tx = useT();
  const t = tx.live;
  const router = useRouter();
  const errorText = useErrorText();
  const notice = useNotice();
  const { domains, appliedSeq, active, setActive, loadGraph } = useData();
  const { focusDomain } = useShell();
  const [data, setData] = useState<Loaded | null>(null);
  /** The page for this topic is on screen: a failed live re-fetch keeps it instead of showing an error */
  const shown = useRef<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // moment of the render: skills past their review time get the "rusty" pill
  const [now] = useState(() => Date.now());

  useEffect(() => {
    let off = false;
    (async () => {
      try {
        const topic = await get<S["TopicView"]>(`/v1/topics/${id}`);
        const hubs = await get<S["HubGoal"][]>(`/v1/domains/${topic.domain_id}/hubs`).catch(() => [] as S["HubGoal"][]);
        const has = (h: S["HubGoal"]) => h.topics?.some((x) => x.topic_id === id);
        // a chapter first; `domain_all` (every topic of the subject) only when the topic is in no chapter
        const hubId = (hubs.find((h) => h.kind !== "domain_all" && has(h)) ?? hubs.find(has))?.hub_id ?? null;
        const [notes, cards, cheat] = await Promise.all([
          orNone(get<S["ArtifactResponse"]>(`/v1/topics/${id}/notes`)),
          orNone(get<S["CardsResponse"]>(`/v1/topics/${id}/cards`)),
          hubId ? orNone(get<S["CheatsheetResponse"]>(`/v1/hubs/${hubId}/cheatsheet`)) : Promise.resolve(null),
        ]);
        if (off) return;
        if (shown.current !== id) focusDomain(topic.domain_id);
        shown.current = id;
        setData({ topic, notes, cards, hubId, cheat });
      } catch (e) {
        if (off || shown.current === id) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) setMissing(true);
        else setError(errorText(e));
      }
    })();
    return () => {
      off = true;
    };
    // once per topic, and again when the backend projection moves (states, skills, new notes)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, appliedSeq]);

  // the active session as the deck answers see it at the moment of sending, not at the render that built the deck
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  const domain = domains.find((d) => d.id === data?.topic.domain_id);
  const domainTitle = domain?.title ?? "";

  if (missing || error) {
    return (
      <PageFrame chipLabel={tx.page.chipSubject(domainTitle)}>
        <Crumbs domainTitle={domainTitle || tx.shell.subjects} />
        <p role={error ? "alert" : undefined} className="mt-6 text-paper-dim">
          {error ?? tx.topic.missing}
        </p>
      </PageFrame>
    );
  }
  if (!data) return <div className="flex-1" aria-busy="true" />;

  const { topic } = data;
  const skills = topic.skills ?? [];
  const state = topicState(topic.display);
  const level = Math.min(1, Math.max(0, (topic.display.display_level ?? 0) / 6));
  const begin = async (intent: Intent) => {
    setBusy(true);
    try {
      const s = await startSession({ domain_id: topic.domain_id, topic_id: topic.topic_id, intent });
      setActive(s);
      router.push(`/session/${s.session_id}`);
    } catch (e) {
      // an action that failed must not take the page away: `error` is for the load of the page itself
      notice(errorText(e));
      setBusy(false);
    }
  };

  const buildCheat = async () => {
    if (!data.hubId) return;
    setBusy(true);
    try {
      await post(`/v1/hubs/${data.hubId}/cheatsheet`, { idempotency_key: idem() });
      notice(t.cheatQueued);
    } catch (e) {
      notice(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  /** "I know this" and "not needed": one POST, the graph is re-read by the live stream or right away */
  const mark = async (what: "known" | "hide") => {
    setBusy(true);
    try {
      await post(`/v1/topics/${topic.topic_id}/${what}`);
      notice(what === "known" ? t.knownDone : t.hiddenDone);
      if (what === "hide") {
        await loadGraph(topic.domain_id).catch(() => {});
        router.push("/");
      }
    } catch (e) {
      notice(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  /** Edit (`PATCH`) and delete (`DELETE`) answer with the whole deck: an edited card gets a new id, so the deck is replaced, not patched */
  const manage: DeckManage = {
    edit: async (cardId, prompt) => {
      try {
        const cards = await patch(`/v1/cards/${cardId}`, { prompt, idempotency_key: idem() });
        setData((d) => (d ? { ...d, cards } : d));
        return null;
      } catch (e) {
        return errorText(e);
      }
    },
    remove: async (cardId) => {
      try {
        const cards = await del(`/v1/cards/${cardId}`, { idempotency_key: idem() });
        setData((d) => (d ? { ...d, cards } : d));
        return null;
      } catch (e) {
        return errorText(e);
      }
    },
  };

  /**
   * A rated card is the `answer` action of a session (`source: "card"`), and the backend takes it only in an active one: the
   * active session of this subject, else a "review" session on this topic (it closes the active session of another subject).
   * A failure comes back as text: the deck keeps its ratings and offers to try again.
   */
  const answers: DeckAnswers = {
    submit: async (cardId, remembered, text) => {
      try {
        let cur = activeRef.current;
        if (!cur || cur.status !== "active" || cur.domain_id !== topic.domain_id) {
          cur = await startSession({ domain_id: topic.domain_id, topic_id: topic.topic_id, intent: "review" });
          // the ref first: the next card of the queue may go before the next render
          activeRef.current = cur;
          setActive(cur);
        }
        await answerCard(cur.session_id, { id: cardId, remembered, text });
        return null;
      } catch (e) {
        return e instanceof ApiError && e.code === "TURN_LOCKED" ? t.turnLocked : errorText(e);
      }
    },
  };

  // the documents of the page: notes (blocks), cards (prompts), the cheat sheet of the hub
  type Doc = { key: string; title: string; kind: "note" | "cheatsheet"; text: string };
  const joined = (blocks: Block[]) => blocks.map((b) => b.content).join("\n\n");
  const docs: Doc[] = [];
  if (data.cheat && data.cheat.blocks.length) docs.push({ key: data.cheat.artifact_id, title: tx.stubs.library.kinds.cheatsheet, kind: "cheatsheet", text: joined(data.cheat.blocks as Block[]) });
  if (data.notes && data.notes.blocks.length) docs.push({ key: data.notes.artifact_id, title: tx.stubs.note.title, kind: "note", text: joined(data.notes.blocks as Block[]) });
  // the cards are a deck: the stack on the page, the list and the review in its window (the same as on mocks)
  const deck = data.cards ? studyCardsFromApi(data.cards) : [];

  const windows: Record<string, WindowSpec> = {};
  for (const d of docs) {
    windows[`a-${d.key}`] = {
      title: d.title,
      meta: d.kind === "cheatsheet" ? tx.stubs.library.kinds.cheatsheet : tx.stubs.library.kinds.note,
      actions:
        d.kind === "note" ? (
          <Link href={`/notes/${topic.topic_id}`} className={TEXT_BTN}>
            {tx.topic.edit}
          </Link>
        ) : undefined,
      body: <Markdown text={d.text} />,
    };
  }
  // a deck that was emptied by deleting its cards keeps its window (it may be open), with a line saying so
  if (data.cards) {
    windows.cards = {
      title: tx.topic.cards,
      meta: tx.topic.cardsMeta(deck.length, topic.name),
      // the key makes the viewer start afresh when the deck changes (ratings are kept per card)
      body: <DeckViewer key={deck.map((c) => c.id).join()} cards={deck} manage={manage} answers={answers} />,
    };
  }

  return (
    <ArtifactHost windows={windows}>
      <PageFrame chipLabel={tx.page.chipTopic(topic.name)} topicId={topic.topic_id}>
        <Crumbs domainTitle={domainTitle} />

        <header className="mt-2">
          <h1 className="display-caps text-display text-paper">{topic.name}</h1>
        </header>

        <div className="mt-4 flex items-start gap-4">
          <MemoryRing state={state} retention={level} size={40} />
          <div className="min-w-0">
            <p className="display-caps text-paper">{tx.states[state].toLowerCase()}</p>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
          <button
            type="button"
            disabled={busy}
            onClick={() => void begin("learn")}
            className={cn(ACTION_BTN, state === "new" ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-ribbon hover:text-ribbon")}
          >
            {tx.topicCard.study}
          </button>
          <Link
            href={`/practice/${topic.topic_id}`}
            className={cn(ACTION_BTN, state !== "new" && state !== "review" ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-ribbon hover:text-ribbon")}
          >
            {tx.topicCard.practice}
          </Link>
          <button
            type="button"
            disabled={busy}
            onClick={() => void begin("review")}
            className={cn(ACTION_BTN, state === "review" ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-ribbon hover:text-ribbon")}
          >
            {tx.topicCard.review}
          </button>
        </div>

        <div className="mt-2 flex flex-wrap gap-x-2">
          <button type="button" disabled={busy} onClick={() => void mark("known")} className={cn(TEXT_BTN, "-ml-3")}>
            {t.iKnowThis}
          </button>
          <button type="button" disabled={busy} onClick={() => void mark("hide")} className={TEXT_BTN}>
            {t.hideTopic}
          </button>
        </div>

        <PageSection id="topic-skills" title={t.skillsTitle} count={skills.length}>
          {skills.length === 0 ? (
            <p className="text-paper-dim">{t.noSkills}</p>
          ) : (
            <ul className="border-t border-seam-soft">
              {skills.map((s) => (
                <li key={s.skill_id} className="flex min-h-12 items-center gap-3 border-b border-seam-soft py-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-body text-paper">{s.name}</p>
                  </div>
                  {(s.ret_due_at_ms ?? 0) > 0 && (s.ret_due_at_ms ?? 0) < now && <span className={PILL}>{t.rusty}</span>}
                </li>
              ))}
            </ul>
          )}
        </PageSection>

        <PageSection id="topic-artifacts" title={tx.topic.artifacts}>
          {docs.length === 0 && deck.length === 0 ? (
            // the learner has studied this topic (state isn't "new"), so empty means the background
            // job hasn't finished yet, not "nothing happened" — the live stream re-fetches on its own
            <p role="status" className="text-paper-dim">
              {state === "new" ? tx.topic.noNotes : tx.topic.notesGenerating}
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {docs.map((d) => (
                <ExpandableCard key={d.key} artifact={`a-${d.key}`}>
                  <span className="display-caps block px-4 pt-3 text-paper">{d.title}</span>
                  <span aria-hidden className="mt-1 block h-[72px] overflow-hidden px-4 text-small leading-relaxed text-paper-dim [mask-image:linear-gradient(to_bottom,#000_55%,transparent)]">
                    {plainText(d.text)}
                  </span>
                </ExpandableCard>
              ))}
              {deck.length > 0 && <DeckCard cards={deck} />}
            </div>
          )}
          {data.hubId && (
            <button type="button" disabled={busy} onClick={() => void buildCheat()} className={cn(TEXT_BTN, "mt-3 -ml-3")}>
              {data.cheat ? t.refreshCheat : t.buildCheat}
            </button>
          )}
        </PageSection>
      </PageFrame>
    </ArtifactHost>
  );
}
