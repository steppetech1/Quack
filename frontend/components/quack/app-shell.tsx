"use client";

import { PanelLeft, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useRef, useState } from "react";
import type { FileRef } from "./attachments";
import type { GraphMode } from "@/lib/graph/types";
import { EMPTY_DOMAIN } from "@/lib/api/adapters";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useT } from "@/lib/i18n/provider";
import { MOCK_TODAY } from "@/lib/mock";
import type { Domain, Session } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { DomainSidebar } from "./domain-sidebar";
import { IconButton } from "./icon-button";
import { ResizeHandle, useResizable } from "./resize-handle";
import { BackLink } from "./page-parts";
import { SoonProvider } from "./soon";

type Shell = {
  domain: Domain;
  selectDomain: (id: string) => void;
  /** Новая сессия из поля ввода: сообщение студента, пока бэкенда нет */
  pending: string | null;
  /** Files attached to the message that starts the session */
  pendingFiles: FileRef[];
  /** Тема, к которой привязана новая сессия (поле ввода на странице темы) */
  pendingTopic: string | null;
  startSession: (text: string, topicId?: string | null, files?: FileRef[]) => void;
  /** Режим графа домена: обзор глав, одна глава или все темы */
  graphMode: GraphMode;
  setGraphMode: (m: GraphMode) => void;
  /** Показать граф в режиме: закрывает шторку и уводит на «/», если нужно */
  openGraph: (m: GraphMode) => void;
  closeDrawer: () => void;
  /** Сессии текущего домена; только что начатая стоит первой */
  sessions: Session[];
  /** Сделать домен текущим без перехода (страница темы или сессии реального домена) */
  focusDomain: (id: string) => void;
  /** Клик по названию домена в левой панели: граф встаёт по центру экрана. Граф сбрасывает флаг, когда выровнялся */
  centerPending: boolean;
  consumeCenter: () => void;
};

const ShellContext = createContext<Shell | null>(null);

export function useShell() {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error("useShell outside AppShell");
  return ctx;
}

/** Сессии пока есть только в матанализе */
const SESSION_DOMAIN = "matan";
const SIDEBAR_KEY = "quack.sidebar-collapsed";

/** Левую панель можно тянуть за правый край: от 200 px до 480 px, но чтобы центру осталось не меньше 640 px */
const SIDEBAR_MIN_PX = 200;
const sidebarMax = (panel: HTMLElement) => Math.min(480, (panel.parentElement?.clientWidth ?? 1280) - 640);

/** Правый край верхней полосы сессии: сюда сессия кладёт кнопки инструментов */
export const SESSION_TOOLS_SLOT = "session-tools-slot";

/** Вид домена (граф или канвасы) живёт здесь; «/» — экран «Все графы» */
export const DOMAIN_PATH = "/domain";

/** «/domain?domain=» (ссылка из просмотра канваса) выбирает домен и убирает параметр из адреса */
function DomainFromQuery({ domains, onDomain }: { domains: Domain[]; onDomain: (id: string) => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const search = useSearchParams();
  const param = search.get("domain");
  const view = search.get("view");
  useEffect(() => {
    if (pathname !== DOMAIN_PATH || !param) return;
    const id = domains.find((d) => d.id === param)?.id;
    if (id) onDomain(id);
    router.replace(view === "canvases" ? `${DOMAIN_PATH}?view=canvases` : DOMAIN_PATH, { scroll: false });
  }, [pathname, param, view, domains, onDomain, router]);
  return null;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useT();
  const data = useData();
  const domains = data.domains.length ? data.domains : [EMPTY_DOMAIN];
  const router = useRouter();
  const pathname = usePathname();
  const inSession = /^\/(session|topic|hub|canvas)\//.test(pathname);
  const inSessionPage = pathname.startsWith("/session/");
  // Доска канваса занимает весь экран: ни панели доменов, ни индикаторов графа
  const fullscreen = pathname.startsWith("/canvas/");
  // Живой режим: пока данные грузятся (или «/» без предметов — сейчас уведёт на создание) — пустой центр; ошибка — плашка
  const busy = API_MODE && (!data.ready || (!data.error && data.domains.length === 0 && pathname === "/"));
  const failed = API_MODE && !busy && !!data.error;

  const [domainId, setDomainId] = useState(domains[0].id);
  const [pending, setPending] = useState<string | null>(null);
  const [pendingTopic, setPendingTopic] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<FileRef[]>([]);
  const [graphMode, setGraphMode] = useState<GraphMode>({ kind: "overview" });
  const [drawer, setDrawer] = useState(false);
  const [centerPending, setCenterPending] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const sidebar = useResizable({ id: "sidebar", min: SIDEBAR_MIN_PX, max: sidebarMax, edge: "right" });
  // localStorage is only readable after mount
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCollapsed(localStorage.getItem(SIDEBAR_KEY) === "1");
  }, []);
  const toggleSidebar = () => {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "0" : "1");
    setCollapsed(!collapsed);
  };
  const trigger = useRef<HTMLElement | null>(null);
  const closeBtn = useRef<HTMLButtonElement | null>(null);
  const openDrawer = (el: HTMLElement) => {
    trigger.current = el;
    setDrawer(true);
  };

  // Моки: сессии есть только в матанализе. Бэкенд: домен выбирает сама страница сессии или темы
  const wanted = inSession && !API_MODE ? SESSION_DOMAIN : domainId;
  const domain = domains.find((d) => d.id === wanted) ?? domains[0];
  const activeId = domain.id;
  const { loadGraph } = data;

  // Граф домена грузится, когда домен становится текущим
  useEffect(() => {
    if (API_MODE && activeId) void loadGraph(activeId).catch(() => {});
  }, [activeId, loadGraph]);

  // Шторка — модальное окно: фокус на «Закрыть», Esc закрывает, при закрытии фокус возвращается на кнопку-триггер
  useEffect(() => {
    if (!drawer) return;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", onKey);
    const opener = trigger.current;
    return () => {
      window.removeEventListener("keydown", onKey);
      requestAnimationFrame(() => opener?.isConnected && opener.focus());
    };
  }, [drawer]);

  const trapTab = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Tab") return;
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input, [tabindex]:not([tabindex='-1'])")].filter(
      (el) => el.offsetParent !== null,
    );
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const selectDomain = (id: string) => {
    setDomainId(id);
    setGraphMode({ kind: "overview" });
    setDrawer(false);
    setCenterPending(true);
    if (pathname !== DOMAIN_PATH) router.push(DOMAIN_PATH);
  };

  const openGraph = (m: GraphMode) => {
    setGraphMode(m);
    setDrawer(false);
    if (pathname !== DOMAIN_PATH || window.location.search.includes("view=canvases")) router.push(DOMAIN_PATH);
  };

  const startSession = (text: string, topicId: string | null = null, files: FileRef[] = []) => {
    setPending(text);
    setPendingTopic(topicId);
    setPendingFiles(files);
    if (!API_MODE) setDomainId(SESSION_DOMAIN);
    router.push("/session/new");
  };

  // Только что начатая сессия встаёт первой в списке
  const sessions: Session[] =
    // С бэкендом новая сессия появляется в списке сама, как только создана
    pending && !API_MODE && domain.id === SESSION_DOMAIN
      ? [{ id: "new", href: "/session/new", date: MOCK_TODAY, title: pending, topics: [] }, ...domain.sessions]
      : domain.sessions;

  return (
    <SoonProvider>
      <Suspense fallback={null}>
        <DomainFromQuery domains={domains} onDomain={setDomainId} />
      </Suspense>
      <ShellContext.Provider value={{ domain, selectDomain, pending, pendingFiles, pendingTopic, startSession, graphMode, setGraphMode, openGraph, closeDrawer: () => setDrawer(false), sessions, focusDomain: setDomainId, centerPending, consumeCenter: () => setCenterPending(false) }}>
        <a
          href="#main"
          onClick={(e) => {
            e.preventDefault();
            document.getElementById("main")?.focus();
          }}
          className="caps fixed top-2 left-2 z-[60] -translate-y-16 rounded-pill border border-seam bg-cloth-raised px-4 py-2.5 text-paper focus:translate-y-0"
        >
          {t.shell.skip}
        </a>
        <div inert={drawer} className="flex h-dvh">
          {!fullscreen && (
            <aside
              aria-label={t.shell.subjects}
              className={cn("relative hidden shrink-0 border-r border-seam-soft md:block", collapsed ? "w-15" : sidebar.width === null && "w-62")}
              // a width the learner dragged to (never more than 45% of the window, so the centre keeps its room)
              style={!collapsed && sidebar.width !== null ? { width: `min(${sidebar.width}px, 45vw)` } : undefined}
            >
              {!collapsed && <ResizeHandle r={sidebar} />}
              <DomainSidebar domains={data.domains} activeId={activeId} onSelect={selectDomain} collapsed={collapsed} onToggle={toggleSidebar} />
            </aside>
          )}

          <main id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col outline-none">
            {!fullscreen && <div className="flex items-center gap-1 border-b border-seam-soft px-2 py-1.5 md:hidden">
              <IconButton label={t.shell.subjects} onClick={(e) => openDrawer(e.currentTarget)}>
                <PanelLeft />
              </IconButton>
              <p className="display-caps text-[1.125rem] text-paper">Qvack!</p>
            </div>}
            {/* Сессия: путь назад в граф домена; граф открывается по «/» */}
            {inSessionPage && (
              <div className="flex shrink-0 items-center justify-between gap-2 border-b border-seam-soft px-4 md:px-7">
                <BackLink href={DOMAIN_PATH}>{t.shell.backToGraph}</BackLink>
                {/* the session puts its tool buttons here (live/session-tools.tsx): the bar is outside the session's workspace, so they come through a portal */}
                <div id={SESSION_TOOLS_SLOT} className="-mr-2 flex items-center" />
              </div>
            )}
            {busy ? (
              <div className="flex-1" aria-busy="true" />
            ) : failed ? (
              <div role="alert" className="m-auto max-w-md px-6 text-center">
                <p className="text-body text-paper">{data.error}</p>
                <button
                  type="button"
                  onClick={() => void data.reload()}
                  className="caps mt-4 inline-flex h-10 items-center rounded-pill border border-seam px-5 text-paper transition-colors hover:border-rule hover:bg-cloth-hover"
                >
                  {t.live.retry}
                </button>
              </div>
            ) : null}
            {/* Страницу рендерим всегда (и на сервере — иначе Next не может проверить instant-навигацию),
                пока данных нет — просто прячем; обёртка постоянная, страница не пересоздаётся */}
            <div className={busy || failed ? "hidden" : "contents"}>{children}</div>
          </main>

        </div>

        {/* Телефон: левая панель (предметы, темы, сессии) — шторка */}
        <div
          aria-hidden
          onClick={() => setDrawer(false)}
          className={cn(
            "fixed inset-0 z-40 bg-cloth-deep/70 transition-opacity duration-300 md:hidden",
            drawer ? "opacity-100" : "pointer-events-none opacity-0",
          )}
        />
        {!fullscreen && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t.shell.subjects}
            inert={!drawer}
            onKeyDown={trapTab}
            className={cn(
              "fixed inset-y-0 left-0 z-50 w-[min(20rem,86vw)] border-r border-seam bg-cloth-deep transition-transform duration-300 ease-out md:hidden",
              drawer ? "translate-x-0" : "-translate-x-full",
            )}
          >
            <IconButton ref={drawer ? closeBtn : undefined} label={t.shell.close} className="absolute top-2 right-2 z-10" onClick={() => setDrawer(false)}>
              <X />
            </IconButton>
            <DomainSidebar domains={data.domains} activeId={activeId} onSelect={selectDomain} />
          </div>
        )}
      </ShellContext.Provider>
    </SoonProvider>
  );
}
