"use client";

// "Choice" page (front-end only). The chat is the main surface; programs arrive in it as cards
// after the summary is confirmed. The left column holds the Выбор / Подготовка switch, the
// programs (picks, favourites, comparison) and chat history. Both side panels can be dragged
// to resize or collapsed; the workspace is kept in the student's store (account/store.ts).

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  CONFIRM_REPLY,
  EMPTY_PROFILE,
  editField,
  extract,
  fieldValue,
  placeholderFor,
  planReply,
  readiness,
  sanitizeProfile,
  type FieldKey,
  type Profile,
} from "./assistant";
import { Dashboard } from "../dashboard/Dashboard";
import type { DashTab } from "../dashboard/dashboardRules";
import { PrepView } from "../prep/PrepView";
import { prefetchRemoteOverview } from "../prep/remotePrep";
import { useQuack } from "../quack/source";
import { morph } from "@/components/transition/morph";
import { FirstHint, HelpDialog } from "@/components/hints/FirstHint";
import { store } from "@/components/account/store";
import type { PrepSub, PrepTab } from "../prep/prepModel";
import { ChatMessage, type ChatMsg } from "./ChatMessage";
import { CompareView } from "./CompareView";
import { CustomScrollbar } from "./CustomScrollbar";
import { milestoneIntent } from "./milestoneIntent";
import { chosenTestDates, doneMilestones, markMilestone, pickTestDate, skipEntranceTest } from "../prep/milestoneMarks";
import { readPrepUi } from "../prep/prepStore";
import { EXAMS, formatDate, plannedTest, registrationBy } from "../prep/prepData";
import { ProfilePanel } from "./ProfilePanel";
import { ProgramCards, ProgramDrawer, type ProgramActions } from "./ProgramUi";
import { catalog, recommend } from "./programs";
import {
  REMOTE,
  clearProfileOnBackend,
  forgetSynced,
  loadCatalog,
  loadSaved,
  prioritize,
  primeSynced,
  searchPrograms,
  syncFields,
} from "./catalog";
import { loadHistory, loadProfile, sendSelectionMessage, type HistoryItem } from "./remoteChat";
import { ApiError } from "@/api/client";
import { backend } from "@/api/backend";
import { ResizeHandle } from "./ResizeHandle";
import { Sidebar, type ChatSummary, type Mode, type SidebarTab } from "./Sidebar";
import { Icon } from "./Icon";
import { Topbar } from "./Topbar";
import styles from "./choice.module.css";
import layout from "./layout.module.css";

const GREETINGS = [
  ["Привет!", "Куда целишься после школы?"],
  ["IT, Бизнес, Медицина,", "Еще не определился?"],
];

// Side panel geometry, px
const RAIL_W = 72;
const LEFT_DEFAULT = 302;
const LEFT_MIN = 240;
const LEFT_MAX = 440;
const LEFT_SNAP = 160; // released narrower than this -> collapse to the icon rail
const RIGHT_DEFAULT = 620;
const RIGHT_MIN = 380;
const RIGHT_MAX = 760;
const RIGHT_SNAP = 300; // released narrower than this -> hide
const MAX_COMPARE = 4;
const CHAT_MIN = 460; // below this the profile panel turns into an overlay
const PHONE_MAX = 760;
const LAYOUT_KEY = "quack-choice-layout";
const WORKSPACE_KEY = "quack-choice-workspace";
// With the backend there is one conversation per student, kept on the server
const SERVER_CHAT = "server";
const INTRO_PLACEHOLDER = "Люблю бананы и хочу в IT...";

type LeftPanel = { width: number; collapsed: boolean; lastWidth: number; userSet: boolean };
type RightPanel = { width: number; hidden: boolean; lastWidth: number };
type ChatItem = ChatMsg & { programs?: string[] };
type Session = ChatSummary & { messages: ChatItem[] };
type Workspace = {
  profile: Profile;
  confirmed: boolean;
  picks: string[];
  saved: string[];
  compare: string[];
  sessions: Session[];
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

/** What the assistant says when the questions are skipped: what it knows and what it has to guess. */
function skipReply(profile: Profile) {
  const guesses: string[] = [];
  if (!profile.direction) guesses.push("направление любое");
  if (!profile.location) guesses.push("страна любая");
  if (!profile.sat && !profile.ielts && !profile.ent) guesses.push("IELTS около 6.0");
  if (!profile.budget && !profile.grant) guesses.push("грант желательно");
  if (!guesses.length) return "Хорошо, вот программы по тому, что ты уже рассказал.";
  return `Хорошо, без вопросов. Где не знаю, предполагаю: ${guesses.join(", ")}. Поправить можно в профиле или просто напиши мне, и подборка обновится.`;
}

export function ChoiceApp({ onRestart }: { onRestart: () => void }) {
  const [stage, setStage] = useState<"intro" | "chat">("intro");
  const [mode, setMode] = useState<Mode>("dashboard");
  const [prepTab, setPrepTab] = useState<PrepTab>("overview");
  const [prepSub, setPrepSub] = useState<PrepSub>("now");
  const [dashTab, setDashTab] = useState<DashTab>("overview");
  const [diagDone, setDiagDone] = useState<boolean>(() => {
    return Boolean(readPrepUi().diagnosticDone);
  });
  const [launchDiag, setLaunchDiag] = useState(false);
  // The source's functions are stable, so effects can depend on them
  const { state: quackState, report: reportToQuack, markSeen: markQuackSeen, reset: resetQuack } = useQuack();
  const [profile, setProfile] = useState<Profile>(EMPTY_PROFILE);
  const [confirmed, setConfirmed] = useState(false);
  const [versions, setVersions] = useState<Partial<Record<FieldKey, number>>>({});
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [placeholder, setPlaceholder] = useState(INTRO_PLACEHOLDER);
  const [sentTick, setSentTick] = useState(0);
  const [greeting, setGreeting] = useState({ current: 0, leaving: -1 });

  // Programs
  const [picks, setPicks] = useState<string[]>([]);
  const [saved, setSaved] = useState<string[]>([]);
  // The catalog registry is filled outside React (catalog.ts); bumping this re-reads it
  const [catalogVersion, bumpCatalog] = useState(0);
  const [compare, setCompare] = useState<string[]>([]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [view, setView] = useState<"chat" | "compare">("chat");
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("picks");
  const [toast, setToast] = useState<string | null>(null);

  // Chat history
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const activeChatRef = useRef<string | null>(null);

  // Layout
  const [left, setLeft] = useState<LeftPanel>({ width: LEFT_DEFAULT, collapsed: false, lastWidth: LEFT_DEFAULT, userSet: false });
  const [right, setRight] = useState<RightPanel>({ width: RIGHT_DEFAULT, hidden: false, lastWidth: RIGHT_DEFAULT });
  const [resizing, setResizing] = useState(false);
  const [mobileProgramsOpen, setMobileProgramsOpen] = useState(false);
  const [profileOverlayOpen, setProfileOverlayOpen] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(1920);
  const dragStart = useRef(0);
  const dragWidth = useRef(0);

  // Mirrors of state that the async assistant flow reads without waiting for a render
  const profileRef = useRef(profile);
  // Whether the current chat already has a summary; each chat gets its own
  const summarySentRef = useRef(false);
  const busyRef = useRef(false);
  const idRef = useRef(0);
  const mounted = useRef(true);
  /** Последний вопрос — чтобы «повторить» после обрыва отправило именно его */
  const lastAskedRef = useRef<string>("");
  // Effects, not refs: writing must wait until the loaded state has actually been applied,
  // otherwise React's double mount in development saves the empty state over the stored one
  const [workspaceLoaded, setWorkspaceLoaded] = useState(false);
  const [layoutLoaded, setLayoutLoaded] = useState(false);

  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    mounted.current = true;
    inputRef.current?.focus();
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const update = () => setViewportWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  /* ---------- Workspace persistence (profile, programs, chats) ---------- */

  useEffect(() => {
    let alive = true;
    (async () => {
    // Stored cards may be backend programs: the registry has to know them before they render
    let serverProfile: Profile | null = null;
    let history: HistoryItem[] = [];
    if (REMOTE) {
      await loadCatalog().catch(() => undefined);
      serverProfile = await loadProfile().catch(() => null);
      history = await loadHistory().catch(() => []);
    }
    if (!alive) return;
    try {
      const stored = store.get<Workspace>(WORKSPACE_KEY);
      if (stored) {
        profileRef.current = sanitizeProfile(stored.profile);
        setProfile(profileRef.current);
        setConfirmed(Boolean(stored.confirmed));
        setPicks(stored.picks ?? []);
        setSaved(stored.saved ?? []);
        setCompare(stored.compare ?? []);
        setSessions(stored.sessions ?? []);
        idRef.current = Math.max(0, ...(stored.sessions ?? []).flatMap((c: Session) => c.messages.map((m) => m.id)));
      }
    } catch {}
    if (REMOTE) {
      // The server is the source of truth for the profile and the conversation
      if (serverProfile) {
        profileRef.current = serverProfile;
        setProfile(serverProfile);
        primeSynced(serverProfile);
        setPlaceholder(placeholderFor(serverProfile));
      }
      const messages: ChatItem[] = history.map((m, i) => ({ id: i + 1, role: m.role, text: m.text, confirm: "none", editable: false }));
      idRef.current = messages.length;
      setSessions(
        messages.length ? [{ id: SERVER_CHAT, title: "Подбор программ", updatedAt: history[history.length - 1].at, messages }] : []
      );
    }
    setWorkspaceLoaded(true);
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!workspaceLoaded) return;
    // The store gathers a burst (a reply typing out) into one write.
    // With a backend the questionnaire, the saved list and the conversation are read from it on
    // every load and overwrite whatever was here, so keeping a copy is duplication, not a cache
    // (ТЗ §5.6). What stays is the screen's own: which programs are shown and compared.
    store.set(
      WORKSPACE_KEY,
      REMOTE
        ? ({ profile: EMPTY_PROFILE, confirmed, picks, saved: [], compare, sessions: [] } satisfies Workspace)
        : ({ profile, confirmed, picks, saved, compare, sessions } satisfies Workspace)
    );
  }, [workspaceLoaded, profile, confirmed, picks, saved, compare, sessions]);

  /* ---------- Backend: catalog, saved programs, profile ---------- */

  useEffect(() => {
    if (!REMOTE || !workspaceLoaded) return;
    let alive = true;
    (async () => {
      try {
        await loadCatalog();
        const ids = await loadSaved();
        if (!alive) return;
        setSaved(ids);
        prefetchRemoteOverview();
        // Cards picked before the backend was on refer to the demo set
        setPicks((list) => list.filter((id) => catalog.get(id)?.remote));
        setCompare((list) => list.filter((id) => catalog.get(id)?.remote));
        bumpCatalog((n) => n + 1);
      } catch {
        // Offline: the demo set stays, this visit just is not synced
      }
    })();
    return () => {
      alive = false;
    };
  }, [workspaceLoaded]);

  // Keep the active chat's stored copy in sync with what is on screen
  useEffect(() => {
    const id = activeChatRef.current;
    if (!id) return;
    const settled = messages.filter((m) => !m.typing);
    setSessions((list) =>
      list.map((c) =>
        c.id === id
          ? { ...c, messages: settled, updatedAt: settled.length !== c.messages.length ? Date.now() : c.updatedAt }
          : c
      )
    );
  }, [messages]);

  /* ---------- Layout persistence ---------- */

  useEffect(() => {
    const stored = store.get<{ left?: LeftPanel; right?: RightPanel }>(LAYOUT_KEY);
    if (stored?.left) setLeft({ ...stored.left, collapsed: false });
    if (stored?.right) setRight(stored.right);
    setLayoutLoaded(true);
  }, []);

  useEffect(() => {
    // Widths are saved when a drag ends, never on every pixel of it
    if (!layoutLoaded || resizing) return;
    store.set(LAYOUT_KEY, { left, right });
  }, [layoutLoaded, left, right, resizing]);

  useEffect(() => {
    if (!profileOverlayOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setProfileOverlayOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [profileOverlayOpen]);

  /* ---------- Greeting rotation ---------- */

  useEffect(() => {
    if (stage !== "intro") return;
    const timer = setInterval(
      () => setGreeting((g) => ({ current: (g.current + 1) % GREETINGS.length, leaving: g.current })),
      3600
    );
    return () => clearInterval(timer);
  }, [stage]);

  /* ---------- Messages ---------- */

  useEffect(() => {
    const el = messagesRef.current;
    el?.scrollTo({ top: el.scrollHeight, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [messages]);

  // Leaving the comparison once fewer than two programs remain in it
  useEffect(() => {
    if (compare.length < 2) setView("chat");
  }, [compare.length]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 2600);
    return () => clearTimeout(timer);
  }, [toast]);

  const setBusyState = (value: boolean) => {
    busyRef.current = value;
    setBusy(value);
  };

  const updateMsg = useCallback((id: number, patch: Partial<ChatItem> | ((m: ChatItem) => Partial<ChatItem>)) => {
    setMessages((list) => list.map((m) => (m.id === id ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m)));
  }, []);

  // Let "leaving" buttons finish their exit animation, then drop them
  const sweepLeaving = () =>
    setTimeout(() => setMessages((list) => list.map((m) => (m.confirm === "leaving" ? { ...m, confirm: "none" } : m))), 350);

  const addUserMessage = (text: string) =>
    setMessages((list) => [...list, { id: ++idRef.current, role: "user", text, confirm: "none", editable: false }]);

  async function assistantSay(text: string, summary = false, extra?: Pick<ChatItem, "milestone" | "testDate" | "offer">) {
    const id = ++idRef.current;
    const reduced = prefersReducedMotion();
    setMessages((list) => [...list, { id, role: "assistant", text: "", typing: true, confirm: "none", editable: false, ...extra }]);

    await sleep(reduced ? 100 : 700 + Math.min(900, text.length * 12));
    if (!mounted.current) return;

    if (reduced) {
      updateMsg(id, { typing: false, text });
    } else {
      // Quick typewriter, ~70 frames regardless of length
      const step = Math.max(1, Math.ceil(text.length / 70));
      for (let i = step; i < text.length + step; i += step) {
        updateMsg(id, { typing: false, text: text.slice(0, i) });
        await sleep(15);
        if (!mounted.current) return;
      }
    }

    if (summary) {
      // Only the latest summary can be confirmed
      setMessages((list) =>
        list.map((m) =>
          m.id === id ? { ...m, confirm: "shown", editable: true } : m.confirm === "shown" && !m.editing ? { ...m, confirm: "leaving" } : m
        )
      );
      sweepLeaving();
    }
  }

  function applyToProfile(text: string) {
    const before = profileRef.current;
    const { profile: next, changed } = extract(before, text);
    profileRef.current = next;
    setProfile(next);

    const updated = changed.filter((key) => fieldValue(before, key));
    if (updated.length) {
      setVersions((v) => Object.fromEntries([...Object.entries(v), ...updated.map((k) => [k, (v[k] ?? 0) + 1])]));
    }
    setPlaceholder(placeholderFor(next));
    return changed;
  }

  async function respond(text: string, fromEdit = false) {
    const changed = applyToProfile(text);
    if (fromEdit) setConfirmed(false);

    const reply = planReply(profileRef.current, changed, { summarySent: summarySentRef.current, fromEdit });
    if (reply.summary) summarySentRef.current = true;
    await assistantSay(reply.text, reply.summary);
  }

  /* ---------- Programs ---------- */

  const openSidebarTab = (tab: SidebarTab) => {
    setSidebarTab(tab);
    setLeft((l) => (l.collapsed ? { ...l, collapsed: false, width: l.lastWidth, userSet: true } : l));
    openMobilePrograms();
  };

  function toggleSave(id: string) {
    const has = saved.includes(id);
    const offer = !has && !saved.length && !diagDone && stage === "chat" && !busyRef.current;
    if (!has && !saved.length && !offer) setToast("Сохранено. Под неё уже собирается «Подготовка»");
    setSaved((list) => (has ? list.filter((x) => x !== id) : list.includes(id) ? list : [...list, id]));
    if (REMOTE && catalog.get(id)?.remote) {
      (has ? backend.saved.remove(id) : backend.saved.add(id))
        .then(() => {
          prefetchRemoteOverview();
        })
        .catch(() => {
          setSaved((list) => (has ? [...list, id] : list.filter((x) => x !== id)));
          setToast("Не получилось сохранить. Попробуй ещё раз");
        });
    }
    if (offer) void offerTest();
  }

  const programActions: ProgramActions = {
    saved,
    compare,
    onToggleSave: (id) => toggleSave(id),
    onToggleCompare: (id) =>
      setCompare((list) => {
        if (list.includes(id)) return list.filter((x) => x !== id);
        if (list.length >= MAX_COMPARE) {
          setToast(`Можно сравнить до ${MAX_COMPARE} программ — убери одну из сравнения`);
          return list;
        }
        return [...list, id];
      }),
    onOpen: (id) => {
      setMobileProgramsOpen(false);
      setDetailId(id);
    },
    // Помеченная программа уходит из подборки на сервере — перечитываем каталог, а не прячем её тут
    onFlagged: (id) => {
      setDetailId(null);
      setPicks((list) => list.filter((pick) => pick !== id));
      setCompare((list) => list.filter((pick) => pick !== id));
      setToast("Спасибо — программа убрана из подборки");
      if (REMOTE) loadCatalog().then(() => bumpCatalog((n) => n + 1)).catch(() => undefined);
    },
  };

  const openCompare = () => {
    if (compare.length < 2) return;
    setDetailId(null);
    setMobileProgramsOpen(false);
    setMode("choice");
    setView("compare");
  };

  /**
   * «Зарегистрировался на SAT», «подал в Болонью»: the chat ticks the milestone like the calendar and the Quack
   * feed do. A plan is not ticked, only asked about; a date no saved program has is named, not invented.
   */
  async function handleMilestone(text: string) {
    const chosen = chosenTestDates();
    const intent = milestoneIntent(text, saved, chosen);
    if (!intent) return false;
    if (intent.kind === "date") {
      const name = EXAMS[intent.exam].name;
      const prevTest = plannedTest(intent.exam, chosen);
      const same = prevTest?.getTime() === intent.test.getTime();
      if (!same) pickTestDate(intent.exam, intent.key);
      const already = intent.mark && doneMilestones().includes(intent.mark.id);
      if (intent.mark && !already) markMilestone(intent.mark.id, true);
      const reg = formatDate(registrationBy(intent.test));
      const reply = same
        ? intent.mark
          ? already
            ? `«${intent.mark.title}» уже отмечено.`
            : "Дата та же, отметил."
          : `${name} и так ${formatDate(intent.test)}. Регистрация до ${reg}.`
        : intent.mark
          ? `Перестроил план под ${formatDate(intent.test)} и отметил. Прогноз и напоминания в Quack пересчитаны.`
          : `Перестроил план под ${formatDate(intent.test)}: регистрация до ${reg} — отмечу, когда зарегистрируешься. Прогноз в Quack пересчитан.`;
      await assistantSay(reply, false, {
        testDate: same
          ? undefined
          : {
              exam: intent.exam,
              label: `${name} — ${formatDate(intent.test)}`,
              prev: chosen[intent.exam] ?? null,
              prevLabel: prevTest ? formatDate(prevTest) : "ближайшую",
            },
        milestone: intent.mark && !already ? intent.mark : undefined,
      });
      return true;
    }
    if (intent.kind === "badDate") {
      await assistantSay(`В эту дату ${EXAMS[intent.exam].name} не проходит. Ближайшие даты: ${intent.options.join(", ")}. Какую выбираешь?`);
      return true;
    }
    if (intent.kind === "planned") {
      await assistantSay(`Отмечу ${intent.what}, когда сделаешь. Как только будет готово, напиши, например, «зарегистрировался», или отметь в Календаре.`);
    } else if (intent.kind === "unknown") {
      await assistantSay(`Не нашёл ${intent.what} среди дат твоих сохранённых программ. Сохрани программу, которой это нужно, и дата появится в Календаре.`);
    } else if (doneMilestones().includes(intent.id)) {
      await assistantSay(`«${intent.title}» уже отмечено как сделанное.`);
    } else {
      markMilestone(intent.id, true);
      await assistantSay("Отметил. Прогноз и напоминания в Quack уже пересчитаны.", false, {
        milestone: { id: intent.id, title: intent.title },
      });
    }
    return true;
  }

  function undoMilestone(messageId: number) {
    const mark = messages.find((m) => m.id === messageId)?.milestone;
    if (!mark || mark.undone) return;
    markMilestone(mark.id, false);
    updateMsg(messageId, { milestone: { ...mark, undone: true } });
  }

  /** Optional, never in the way: taking it opens it in «Подготовке», putting it off starts from the profile */
  async function offerTest() {
    setBusyState(true);
    await assistantSay(
      "Сохранено — «Подготовка» уже собирается под эту программу. Хочешь пройти входной замер? 8 вопросов, пара минут: найдём, с какого уровня строить сеты. Можно и позже — тогда первый сет соберу по твоему профилю.",
      false,
      { offer: { kind: "diagnostic" } }
    );
    if (mounted.current) setBusyState(false);
  }

  async function answerOffer(messageId: number, answer: "start" | "later") {
    if (busyRef.current) return;
    updateMsg(messageId, { offer: { kind: "diagnostic", answered: answer } });
    if (answer === "start") {
      morph(() => {
        setMode("prep");
        setPrepTab("overview");
        setPrepSub("now");
        setLaunchDiag(true);
      });
      return;
    }
    skipEntranceTest();
    setDiagDone(true);
    setBusyState(true);
    await assistantSay("Хорошо. Первый сет собрал по твоему профилю — он уже в «Подготовке → Сейчас». Замер можно пройти там в любой момент.");
    if (mounted.current) setBusyState(false);
  }
  function undoTestDate(messageId: number) {
    const pick = messages.find((m) => m.id === messageId)?.testDate;
    if (!pick || pick.undone) return;
    pickTestDate(pick.exam, pick.prev);
    updateMsg(messageId, { testDate: { ...pick, undone: true } });
  }

  // Chat shortcuts once programs exist: "сравни…", "избранное…"
  async function handleIntent(text: string) {
    if (await handleMilestone(text)) return true;
    if (!picks.length) return false;
    const t = text.toLowerCase();
    if (/сравн/.test(t)) {
      if (compare.length >= 2) {
        openCompare();
        await assistantSay("Открыл сравнение — вернуться в чат можно стрелкой слева сверху.");
      } else {
        await assistantSay("Отметь «Сравнить» на двух–четырёх карточках, и я поставлю их рядом.");
      }
      return true;
    }
    if (/избранн|сохран/.test(t)) {
      openSidebarTab("saved");
      await assistantSay(saved.length ? "Открыл избранное в панели слева." : "Пока в избранном пусто — нажми ☆ на карточке.");
      return true;
    }
    return false;
  }

  /* ---------- Composer ---------- */

  async function openChat(text: string) {
    if (!activeChatRef.current) {
      const id = REMOTE ? SERVER_CHAT : `chat-${Date.now()}`;
      const title = text.length > 42 ? `${text.slice(0, 40)}…` : text;
      activeChatRef.current = id;
      setActiveChatId(id);
      setSessions((list) =>
        list.some((c) => c.id === id) ? list : [{ id, title: REMOTE ? "Подбор программ" : title, updatedAt: Date.now(), messages: [] }, ...list]
      );
    }

    if (stage === "intro") {
      setStage("chat");
      // The programs panel shrinks to its icon rail while chatting, unless the user arranged it
      setLeft((l) => (l.userSet ? l : { ...l, collapsed: true }));
      await sleep(prefersReducedMotion() ? 0 : 450);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busyRef.current) return;

    setInput("");
    setSentTick((t) => t + 1);
    setBusyState(true);

    await openChat(text);
    addUserMessage(text);
    if (REMOTE) await remoteRespond(text);
    else if (!(await handleIntent(text))) await respond(text);
    if (!mounted.current) return;
    setBusyState(false);
    inputRef.current?.focus();
  }

  /** «Повторить» под оборванным ответом: тот же вопрос, обрубок убираем */
  async function retryTruncated(id: number) {
    const text = lastAskedRef.current;
    if (!text || busyRef.current) return;
    setMessages((list) => list.filter((m) => m.id !== id));
    setBusyState(true);
    await remoteRespond(text);
    if (!mounted.current) return;
    setBusyState(false);
  }

  /** One turn with the backend agent: the answer streams in, cards and comparison arrive as tool results */
  async function remoteRespond(text: string) {
    const id = ++idRef.current;
    setMessages((list) => [...list, { id, role: "assistant", text: "", typing: true, confirm: "none", editable: false }]);
    let answer = "";
    const cards: string[][] = [];
    let compareIds: string[] = [];
    const changed = { profile: false, saved: false };
    let failed: string | null = null;
    let truncated = false;

    try {
      const turn = await sendSelectionMessage(text, {
        onText: (delta) => {
          answer += delta;
          if (mounted.current) updateMsg(id, { typing: false, text: answer });
        },
        onPrograms: (ids) => cards.push(ids),
        onCompare: (ids) => (compareIds = ids),
        onChanged: (what) => {
          changed.profile ||= Boolean(what.profile);
          changed.saved ||= Boolean(what.saved);
        },
      });
      // Стрим закончился без кадра `done` — связь оборвалась на полуслове.
      // Обрыв не бросает исключение: тело просто кончается.
      truncated = !turn.complete;
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      // Связь оборвалась посреди ответа: иногда тело просто кончается (ловится
      // выше по отсутствию `done`), иногда fetch бросает. Оба случая — не отказ
      // сервера, а обрыв, и лечатся одним и тем же: повторить вопрос.
      truncated = err instanceof ApiError && err.status === 0;
      failed =
        code === "llm_unavailable"
          ? "Ассистент сейчас недоступен. Программы и профиль слева по-прежнему можно смотреть — попробуй написать позже."
          : code === "conflict"
            ? "Я ещё отвечаю на предыдущее сообщение. Подожди секунду и напиши снова."
            : code === "too_many_requests"
              ? "Слишком много сообщений подряд. Подожди минуту."
              : code === "tool_loop_limit"
                ? "Не удалось завершить подборку (превышен лимит шагов). Попробуй повторить запрос."
                : code === "postcheck_failed"
                  ? "Ответ отозван проверкой фактов. Попробуй переформулировать вопрос."
                  : err instanceof ApiError && err.message
                    ? err.message
                    : "Не получилось получить ответ. Попробуй ещё раз.";
    }
    if (!mounted.current) return;

    // The backend withdrew the text but the picks themselves are real: show them without the scare
    if (failed && cards.length && failed.startsWith("Ответ отозван")) {
      failed = null;
      answer = "Вот что подобрал по твоему профилю — подробности в карточках.";
      updateMsg(id, { typing: false, text: answer });
    }

    if (truncated) {
      // Часть ответа уже на экране: её оставляем, но говорим, что это не весь
      // ответ, и даём повторить тот же вопрос (ТЗ §7.3 п.7).
      lastAskedRef.current = text;
      updateMsg(id, {
        typing: false,
        text: answer || "Связь пропала, ответ не дошёл.",
        truncated: true,
      });
    } else if (failed) {
      updateMsg(id, {
        typing: false,
        text: answer ? `${answer}\n\n⚠️ ${failed}` : failed,
      });
    } else if (!answer) {
      setMessages((list) => list.filter((m) => m.id !== id));
    }

    // Whatever the agent changed on the server is read back
    try {
      if (changed.profile || cards.length) {
        const [profileNow] = await Promise.all([changed.profile ? loadProfile() : null, loadCatalog()]);
        if (profileNow) {
          profileRef.current = profileNow;
          setProfile(profileNow);
          primeSynced(profileNow);
          setPlaceholder(placeholderFor(profileNow));
        }
        bumpCatalog((n) => n + 1);
      }
      if (changed.saved) setSaved(await loadSaved());
    } catch {
      // The next turn reads it again
    }
    if (!mounted.current) return;

    for (const ids of cards) {
      const known = prioritize(ids.filter((pid) => catalog.get(pid)?.remote), profileRef.current.priorities);
      if (!known.length) continue;
      setPicks(known);
      setSidebarTab("picks");
      setMessages((list) => [...list, { id: ++idRef.current, role: "assistant", text: "", confirm: "none", editable: false, programs: known }]);
    }
    const toCompare = compareIds.filter((pid) => catalog.get(pid)?.remote).slice(0, MAX_COMPARE);
    if (toCompare.length >= 2) {
      setCompare(toCompare);
      setDetailId(null);
      setMobileProgramsOpen(false);
      setMode("choice");
      setView("compare");
    }
  }

  /* ---------- Summary: confirm / edit ---------- */

  async function onConfirm(id: number) {
    if (busyRef.current) return;
    updateMsg(id, { confirm: "leaving" });
    sweepLeaving();
    setConfirmed(true);
    setBusyState(true);
    await assistantSay(CONFIRM_REPLY);
    if (!mounted.current) return;

    await showPicks();
    if (!mounted.current) return;
    setBusyState(false);
  }

  async function showPicks() {
    let ids: string[];
    if (REMOTE) {
      try {
        const loaded = await loadCatalog();
        bumpCatalog((n) => n + 1);
        ids = prioritize(loaded.ids, profileRef.current.priorities).slice(0, 5);
        if (!ids.length) {
          // Каталог пуст не потому, что программ нет, а потому что их туда ещё не клали:
          // просим поиск по тому, что ученик уже сказал, и возвращаемся с результатом.
          const p = profileRef.current;
          const query = [p.direction, p.location].filter(Boolean).join(" ").trim();
          if (query) {
            await assistantSay("Поищу программы по твоему запросу — это займёт около минуты.");
            const found = await searchPrograms(query);
            if (found.status === "done" && found.found.length) {
              const again = await loadCatalog();
              bumpCatalog((n) => n + 1);
              ids = prioritize(again.ids, p.priorities).slice(0, 5);
            } else {
              await assistantSay(
                found.status === "unavailable"
                  ? "Поиск сейчас недоступен — показываю то, что уже есть в каталоге."
                  : "Поиск ещё идёт — загляни чуть позже, программы появятся сами."
              );
            }
          }
          if (!ids.length) {
            await assistantSay(loaded.emptyReason ?? "Пока не нашёл подходящих программ. Расскажи, что для тебя важно, — и я поищу ещё.");
            return;
          }
        }
      } catch {
        // §6.3 «поиск недоступен»: подбор продолжает работать на кэше и полу проверенных программ,
        // но об этом стоит сказать явно, а не молча подсунуть демо-подборку.
        ids = recommend(profileRef.current);
        await assistantSay("Не удалось обновить список с сервера — показаны только проверенные программы из кэша.");
      }
    } else {
      ids = recommend(profileRef.current);
    }
    setPicks(ids);
    setSidebarTab("picks");
    setMessages((list) => [
      ...list,
      { id: ++idRef.current, role: "assistant", text: "", confirm: "none", editable: false, programs: ids },
    ]);
    setPlaceholder("Сравни программы или спроси что угодно...");
  }

  /**
   * The questions are optional: programs right away from what the student has said, with the gaps
   * filled by plain guesses that are named out loud and can be corrected later.
   */
  async function skipQuestions() {
    if (busyRef.current) return;
    setBusyState(true);
    const ask = "Покажи программы по тому, что уже знаешь";
    await openChat(ask);
    addUserMessage(ask);

    if (REMOTE) {
      await remoteRespond(ask);
      if (!mounted.current) return;
      setBusyState(false);
      inputRef.current?.focus();
      return;
    }

    await assistantSay(skipReply(profileRef.current));
    if (!mounted.current) return;
    await showPicks();
    if (!mounted.current) return;
    setBusyState(false);
    inputRef.current?.focus();
  }

  function onEditStart(id: number) {
    if (busyRef.current) return;
    updateMsg(id, (m) => ({ editing: true, confirmBeforeEdit: m.confirm, confirm: "shown" }));
  }

  function onEditCancel(id: number) {
    updateMsg(id, (m) => ({ editing: false, confirm: m.confirmBeforeEdit ?? "none" }));
  }

  async function onEditSave(id: number, original: string, edited: string) {
    // As in the design: the edited summary keeps only the pencil
    updateMsg(id, { editing: false, confirm: "leaving" });
    sweepLeaving();

    const text = edited.trim();
    if (!text || text === original) return;

    const correction = text.replace(/^.*?резюме понимания:\s*/i, "");
    addUserMessage(correction);
    setBusyState(true);
    await respond(correction, true);
    if (!mounted.current) return;
    setBusyState(false);
  }

  /* ---------- Chat history ---------- */

  function showChat(id: string | null, list: ChatItem[]) {
    activeChatRef.current = id;
    setActiveChatId(id);
    summarySentRef.current = list.some((m) => m.role === "assistant" && m.editable);
    setMessages(list);
    setStage(list.length ? "chat" : "intro");
    setGreeting({ current: 0, leaving: -1 });
    setView("chat");
    setDetailId(null);
    setMode("choice");
    setMobileProgramsOpen(false);
    setInput("");
    const p = profileRef.current;
    setPlaceholder(p.direction ? placeholderFor(p) : INTRO_PLACEHOLDER);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  const selectChat = (id: string) => {
    const chat = sessions.find((c) => c.id === id);
    if (chat && !busyRef.current) showChat(id, chat.messages);
  };

  const deleteChat = (id: string) => {
    if (busyRef.current) return;
    setSessions((list) => list.filter((c) => c.id !== id));
    if (id === activeChatRef.current) showChat(null, []);
  };

  const changeMode = (next: Mode) => {
    if (next === "prep") {
      prefetchRemoteOverview();
    }
    morph(() => {
      setMode(next);
      setView("chat");
      setDetailId(null);
    });
  };

  // Everything of this student goes (chats, programs, preparation, the map), the account stays
  const restart = async () => {
    await store.reset();
    // The questionnaire and the saved programs live on the server, so «начать заново» empties them
    // there as well — otherwise the student starts over and the old answers come straight back.
    if (REMOTE) {
      await Promise.all(saved.map((id) => backend.saved.remove(id).catch(() => undefined)));
      await clearProfileOnBackend().catch(() => undefined);
    }
    forgetSynced();
    resetQuack();
    onRestart();
  };

  /* ---------- What Quack wants to tell the student ---------- */

  // Profile and saved programs are sources of truth: every change is recomputed at once,
  // and whatever moved since the last visit makes the Quack! button glow
  // With the backend, realism arrives with the catalog: a new verdict is recomputed too (catalogVersion)
  useEffect(() => {
    if (workspaceLoaded) reportToQuack({ profile, saved });
  }, [reportToQuack, workspaceLoaded, profile, saved, catalogVersion]);

  // Answers in preparation move the backend's forecast and with it the realism of saved programs:
  // opening Quack or coming back from preparation asks for the fresh verdicts
  useEffect(() => {
    if (!REMOTE || !workspaceLoaded || mode === "prep") return;
    loadCatalog()
      .then(() => bumpCatalog((n) => n + 1))
      .catch(() => undefined);
  }, [workspaceLoaded, mode]);

  // Seen a moment after Quack opens, so the glow does not vanish before it is noticed
  const freshKey = quackState.fresh.map((s) => `${s.id}@${s.at}`).join("|");
  useEffect(() => {
    if (mode !== "dashboard" || !freshKey) return;
    const timer = setTimeout(markQuackSeen, 1500);
    return () => clearTimeout(timer);
  }, [markQuackSeen, mode, freshKey]);

  const alert = { level: quackState.glow, reasons: quackState.fresh.map((s) => s.title) };

  /* ---------- Student profile ---------- */

  // On phones and cramped windows the profile and the left column open over the chat: never both at once
  const openMobilePrograms = () => {
    setProfileOverlayOpen(false);
    setMobileProgramsOpen(true);
  };

  const toggleProfileOverlay = () =>
    setProfileOverlayOpen((open) => {
      if (!open) setMobileProgramsOpen(false);
      return !open;
    });

  function onEditField(key: FieldKey, value: string) {
    const next = editField(profileRef.current, key, value);
    profileRef.current = next;
    setProfile(next);
    setVersions((v) => ({ ...v, [key]: (v[key] ?? 0) + 1 }));
    setPlaceholder(placeholderFor(next));
    if (REMOTE) {
      syncFields(next, [key])
        .then(async (sent) => {
          if (!sent) return;
          await loadCatalog();
          bumpCatalog((n) => n + 1);
        })
        .catch(() => setToast("Не получилось сохранить изменение. Попробуй ещё раз"));
    }
  }

  /* ---------- Panel resizing ---------- */

  const phone = viewportWidth <= PHONE_MAX;
  const leftWidth = left.collapsed ? RAIL_W : left.width;
  // Beside the chat the profile is a column of its own. Everywhere else — Quack, preparation, the
  // greeting, a cramped window, a phone — it slides over the page from the button in the left column
  const roomForRight = viewportWidth - leftWidth - CHAT_MIN;
  const docked = stage === "chat" && mode === "choice" && !phone && roomForRight >= RIGHT_MIN;
  const rightWidth = docked && !right.hidden ? Math.min(right.width, roomForRight) : 0;
  const profileVisible = docked ? rightWidth > 0 : profileOverlayOpen;

  useEffect(() => {
    if (docked) setProfileOverlayOpen(false);
  }, [docked]);

  const settleLeft = (width: number) =>
    setLeft((l) =>
      width < LEFT_SNAP
        ? { ...l, collapsed: true, width: l.lastWidth, userSet: true }
        : { width: Math.max(width, LEFT_MIN), lastWidth: Math.max(width, LEFT_MIN), collapsed: false, userSet: true }
    );

  const settleRight = (width: number) =>
    setRight((r) => (width < RIGHT_SNAP ? { ...r, hidden: true, width: r.lastWidth } : { width: Math.max(width, RIGHT_MIN), lastWidth: Math.max(width, RIGHT_MIN), hidden: false }));

  const toggleLeft = () =>
    setLeft((l) => (l.collapsed ? { ...l, collapsed: false, width: l.lastWidth, userSet: true } : { ...l, collapsed: true, userSet: true }));

  const toggleRight = () => setRight((r) => ({ ...r, hidden: !r.hidden, width: r.lastWidth }));

  const readinessValue = readiness(profile, confirmed);
  const canSkip = !picks.length && !busy && !messages.some((m) => m.confirm === "shown");
  const toggleProfile = docked ? toggleRight : toggleProfileOverlay;

  const gridStyle = { "--left": `${leftWidth}px`, "--right": `${rightWidth}px` } as CSSProperties;
  const sidebarCollapsed = leftWidth < LEFT_SNAP && !mobileProgramsOpen;

  return (
    <div className={styles.page}>
      <main
        className={`${styles.app} ${resizing ? styles.isResizing : ""}`}
        data-stage={stage}
        data-mode={mode}
        data-view={view}
        style={gridStyle}
      >
        <Topbar
          mode={mode}
          onMode={changeMode}
          onOpenMenu={openMobilePrograms}
          alert={alert}
        />

        <aside
          className={`${layout.sidebar} ${mobileProgramsOpen ? layout.sidebarMobileOpen : ""}`}
          aria-label="Навигация"
        >
          <Sidebar
            collapsed={sidebarCollapsed}
            mode={mode}
            tab={sidebarTab}
            picks={picks}
            profile={profile}
            actions={programActions}
            chats={[...sessions].sort((a, b) => b.updatedAt - a.updatedAt)}
            activeChatId={activeChatId}
            onSelectChat={selectChat}
            onDeleteChat={deleteChat}
            onTab={setSidebarTab}
            onToggle={mobileProgramsOpen ? () => setMobileProgramsOpen(false) : toggleLeft}
            onOpenCompare={openCompare}
            onRestart={restart}
            profileToggle={{ open: profileVisible, readiness: readinessValue, onToggle: toggleProfile }}
            prepLocked={!diagDone}
            prepTab={prepTab}
            prepSub={prepSub}
            onPrepTab={(tab, sub) =>
              morph(() => {
                setMode("prep");
                if (!diagDone) {
                  // «Требования» are open before the entrance test: the test date is picked there
                  const open = tab === "overview" && (!sub || sub === "now" || sub === "requirements");
                  setPrepTab("overview");
                  setPrepSub(open && sub ? sub : "now");
                  if (!open) setLaunchDiag(true);
                } else {
                  setPrepTab(tab);
                  if (sub) setPrepSub(sub);
                }
                setMobileProgramsOpen(false);
              })
            }
            dashTab={dashTab}
            onDashTab={(tab) =>
              morph(() => {
                setMode("dashboard");
                setDashTab(tab);
                setMobileProgramsOpen(false);
              })
            }
          />
          <ResizeHandle
            side="left"
            label="Ширина левой панели"
            value={leftWidth}
            min={RAIL_W}
            max={LEFT_MAX}
            onDragStart={() => {
              dragStart.current = dragWidth.current = leftWidth;
              setResizing(true);
              setLeft((l) => ({ ...l, collapsed: false, width: leftWidth }));
            }}
            onDrag={(dx) => {
              dragWidth.current = clamp(dragStart.current + dx, RAIL_W, LEFT_MAX);
              setLeft((l) => ({ ...l, width: dragWidth.current }));
            }}
            onDragEnd={() => {
              setResizing(false);
              settleLeft(dragWidth.current);
            }}
            onNudge={(dx) => settleLeft(clamp(leftWidth + dx, RAIL_W, LEFT_MAX))}
          />
        </aside>
        {mobileProgramsOpen && <div className={layout.mobileBackdrop} onClick={() => setMobileProgramsOpen(false)} />}
        {!docked && profileVisible && <div className={styles.profileBackdrop} onClick={() => setProfileOverlayOpen(false)} />}

        <section className={styles.chat}>
          {/* What the screen on display is for: opened by the duck in the left column */}
          <HelpDialog />
          <div className={styles.chatBody}>
            <div className={`${styles.view} ${styles.viewChoice}`}>
              <div className={styles.greeting} aria-live="polite">
                {GREETINGS.map(([first, second], i) => (
                  <p
                    key={first}
                    className={[
                      styles.greetingText,
                      i === greeting.current && styles.isVisible,
                      i === greeting.leaving && styles.isLeaving,
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    aria-hidden={i !== greeting.current}
                  >
                    {first}
                    <br />
                    {second}
                  </p>
                ))}
              </div>

              <div className={styles.messagesWrap}>
                <div className={styles.messages} ref={messagesRef} aria-live="polite">
                  {messages.map((msg) =>
                    msg.programs ? (
                      <div key={msg.id} className={`${styles.msg} ${layout.programsBlock}`} style={{ maxWidth: "none" }}>
                        <ProgramCards ids={msg.programs} profile={profile} actions={programActions} />
                      </div>
                    ) : (
                      <ChatMessage
                        key={msg.id}
                        msg={msg}
                        onConfirm={onConfirm}
                        onEditStart={onEditStart}
                        onEditCancel={onEditCancel}
                        onEditSave={onEditSave}
                        onUndoMilestone={undoMilestone}
                        onUndoTestDate={undoTestDate}
                        onOffer={diagDone ? undefined : answerOffer}
                        onRetry={retryTruncated}
                      />
                    )
                  )}
                </div>
                <CustomScrollbar target={messagesRef} className={styles.scrollbarChat} />
              </div>

              {view === "compare" && (
                <CompareView
                  ids={compare}
                  profile={profile}
                  onBack={() => setView("chat")}
                  onRemove={programActions.onToggleCompare}
                  onOpen={programActions.onOpen}
                />
              )}

              <ProgramDrawer id={detailId} profile={profile} actions={programActions} onClose={() => setDetailId(null)} />
            </div>

            <div className={`${styles.view} ${styles.viewDashboard}`} aria-hidden={mode !== "dashboard"}>
              {mode === "dashboard" && (
                <Dashboard
                  tab={dashTab}
                  onTab={(t) => morph(() => setDashTab(t))}
                  saved={saved}
                  profile={profile}
                  chatDays={sessions.map((c) => c.updatedAt)}
                  onUnsave={(id) => saved.includes(id) && toggleSave(id)}
                  onOpenChoice={() => changeMode("choice")}
                  onOpenPrep={(tab) => {
                    setPrepTab(tab);
                    changeMode("prep");
                  }}
                />
              )}
            </div>

            <div className={`${styles.view} ${styles.viewPrep}`} aria-hidden={mode !== "prep"}>
              {mode === "prep" && (
                <PrepView
                  tab={prepTab}
                  onTab={setPrepTab}
                  sub={prepSub}
                  onSub={setPrepSub}
                  saved={saved}
                  onGoToChoice={() => changeMode("choice")}
                  externalOpenDiagnostic={launchDiag}
                  onCloseExternalDiagnostic={() => setLaunchDiag(false)}
                  onDiagnosticStatusChange={(done) => setDiagDone(done)}
                />
              )}
            </div>

            {toast && (
              <div className={layout.toast} role="status">
                {toast}
              </div>
            )}
          </div>

          {mode === "choice" && view === "chat" && (
            <div className={styles.composerNote}>
              {canSkip ? (
                <button type="button" className={styles.skipButton} onClick={skipQuestions}>
                  Пропустить вопросы и показать программы <Icon name="chevron-right" size={14} />
                </button>
              ) : picks.length > 0 ? (
                <FirstHint id="choice-picks" title="Что дальше">
                  Нажимай ☆ на программах, которые нравятся. Из избранного Quack соберёт подготовку к экзаменам и будет следить за
                  сроками в «Обзоре».
                </FirstHint>
              ) : null}
            </div>
          )}

          <form className={styles.composer} autoComplete="off" onSubmit={onSubmit}>
            <input
              ref={inputRef}
              className={styles.composerInput}
              name="message"
              type="text"
              value={input}
              placeholder={placeholder}
              aria-label="Сообщение"
              onChange={(e) => setInput(e.target.value)}
            />
            <button
              key={sentTick}
              className={`${styles.composerSend} ${sentTick ? styles.isSent : ""}`}
              type="submit"
              aria-label="Отправить"
              disabled={!input.trim() || busy}
            >
              <img src="/assets/send-arrow.svg" alt="" />
            </button>
          </form>
        </section>

        <aside
          className={`${styles.profile} ${!docked && profileOverlayOpen ? styles.profileOverlay : ""}`}
          aria-label="Профиль студента"
          aria-hidden={!profileVisible}
        >
          {docked && (
            <ResizeHandle
              side="right"
              label="Ширина профиля студента"
              value={rightWidth}
              min={0}
              max={RIGHT_MAX}
              onDragStart={() => {
                dragStart.current = dragWidth.current = rightWidth;
                setResizing(true);
                setRight((r) => ({ ...r, hidden: false, width: rightWidth }));
              }}
              onDrag={(dx) => {
                dragWidth.current = clamp(dragStart.current - dx, 0, RIGHT_MAX);
                setRight((r) => ({ ...r, width: dragWidth.current }));
              }}
              onDragEnd={() => {
                setResizing(false);
                settleRight(dragWidth.current);
              }}
              onNudge={(dx) => settleRight(clamp(rightWidth - dx, 0, RIGHT_MAX))}
            />
          )}
          <ProfilePanel
            profile={profile}
            readiness={readinessValue}
            versions={versions}
            onEdit={onEditField}
            onHide={() => (docked ? setRight((r) => ({ ...r, hidden: true, width: r.lastWidth })) : setProfileOverlayOpen(false))}
          />
        </aside>
      </main>
    </div>
  );
}
