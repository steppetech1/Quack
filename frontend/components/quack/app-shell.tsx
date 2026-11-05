"use client";

import { History, PanelLeft, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { GraphMode } from "@/lib/graph-layout";
import { EMPTY_DOMAIN } from "@/lib/api/adapters";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useT } from "@/lib/i18n/provider";
import { todayIso } from "@/lib/format";
import type { Domain, Session } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { DomainSidebar } from "./domain-sidebar";
import { IconButton } from "./icon-button";
import { MenuToggle } from "./menu-toggle";
import { SiteMenu } from "./site-menu";
import { IndicatorPills } from "./graph/indicator-pills";
import { SessionsPanel } from "./sessions-panel";
import { SoonProvider } from "./soon";

type Shell = {
  domain: Domain;
  selectDomain: (id: string) => void;
  /** Новая сессия из поля ввода: сообщение студента, пока бэкенда нет */
  pending: string | null;
  /** Тема, к которой привязана новая сессия (поле ввода на странице темы) */
  pendingTopic: string | null;
  startSession: (text: string, topicId?: string | null) => void;
  /** Режим графа домена: обзор глав, одна глава или все темы */
  graphMode: GraphMode;
  setGraphMode: (m: GraphMode) => void;
  /** Показать граф в режиме: закрывает шторку и уводит на «/», если нужно */
  openGraph: (m: GraphMode) => void;
  closeDrawer: () => void;
  /** Сделать домен текущим без перехода (страница темы или сессии реального домена) */
  focusDomain: (id: string) => void;
  /** Полноэкранное меню: открыть из кнопки-триггера (откуда берём место и куда вернуть фокус) или закрыть */
  menuOpen: boolean;
  openMenu: (trigger: HTMLElement | null) => void;
  closeMenu: () => void;
};

const ShellContext = createContext<Shell | null>(null);

export function useShell() {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error("useShell outside AppShell");
  return ctx;
}

/** Сессии пока есть только в матанализе */
const SESSION_DOMAIN = "matan";

/** «/?domain=» (ссылка из просмотра канваса) выбирает домен и убирает параметр из адреса */
function DomainFromQuery({ domains, onDomain }: { domains: Domain[]; onDomain: (id: string) => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const search = useSearchParams();
  const param = search.get("domain");
  const view = search.get("view");
  useEffect(() => {
    if (pathname !== "/" || !param) return;
    const id = domains.find((d) => d.id === param)?.id;
    if (id) onDomain(id);
    router.replace(view === "canvases" ? "/?view=canvases" : "/", { scroll: false });
  }, [pathname, param, view, domains, onDomain, router]);
  return null;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useT();
  const data = useData();
  const domains = data.domains.length ? data.domains : [EMPTY_DOMAIN];
  const router = useRouter();
  const pathname = usePathname();
  const inSettings = pathname.startsWith("/settings");
  const inSession = /^\/(session|topic|hub|canvas)\//.test(pathname);
  // Доска канваса занимает весь экран: ни панели доменов, ни панели сессий
  const fullscreen = pathname.startsWith("/canvas/");
  // Живой режим: пока данные грузятся (или «/» без предметов — сейчас уведёт на создание) — пустой центр; ошибка — плашка
  const busy = API_MODE && (!data.ready || (!data.error && data.domains.length === 0 && pathname === "/"));
  const failed = API_MODE && !busy && !!data.error;

  const [domainId, setDomainId] = useState(domains[0].id);
  const [pending, setPending] = useState<string | null>(null);
  const [pendingTopic, setPendingTopic] = useState<string | null>(null);
  // null — решает ширина экрана: на узком десктопе панель свёрнута
  const [sessionsPref, setSessionsPref] = useState<boolean | null>(null);
  // Панель предметов слева: сворачивается только на десктопе (≥768px); на телефоне тоггла нет, всегда развёрнута
  const [domainsCollapsed, setDomainsCollapsed] = useState(false);
  const [graphMode, setGraphMode] = useState<GraphMode>({ kind: "overview" });
  const [drawer, setDrawer] = useState<"domains" | "sessions" | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<{ left: number; top: number } | null>(null);
  const menuOpener = useRef<HTMLElement | null>(null);
  const barToggle = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const closeBtn = useRef<HTMLButtonElement | null>(null);
  const openDrawer = (side: "domains" | "sessions", el: HTMLElement) => {
    trigger.current = el;
    setDrawer(side);
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
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(null);
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

  const openMenu = useCallback((el: HTMLElement | null) => {
    menuOpener.current = el;
    const r = el?.getBoundingClientRect();
    setMenuAnchor(r ? { left: r.left, top: r.top } : null);
    setMenuOpen(true);
  }, []);
  const closeMenu = useCallback(() => setMenuOpen(false), []);

  // любой переход (в том числе «назад») закрывает меню
  const [menuPath, setMenuPath] = useState(pathname);
  if (menuPath !== pathname) {
    setMenuPath(pathname);
    setMenuOpen(false);
  }

  const selectDomain = (id: string) => {
    setDomainId(id);
    setGraphMode({ kind: "overview" });
    setDrawer(null);
    if (pathname !== "/") router.push("/");
  };

  const openGraph = (m: GraphMode) => {
    setGraphMode(m);
    setDrawer(null);
    if (pathname !== "/" || window.location.search.includes("view=canvases")) router.push("/");
  };

  const startSession = (text: string, topicId: string | null = null) => {
    setPending(text);
    setPendingTopic(topicId);
    if (!API_MODE) setDomainId(SESSION_DOMAIN);
    router.push("/session/new");
  };

  // Только что начатая сессия встаёт первой в списке
  const sessions: Session[] =
    // С бэкендом новая сессия появляется в списке сама, как только создана
    pending && !API_MODE && domain.id === SESSION_DOMAIN
      ? [{ id: "new", href: "/session/new", date: todayIso(), title: pending, topics: [] }, ...domain.sessions]
      : domain.sessions;

  const toggleSessions = () =>
    setSessionsPref((p) => !(p ?? window.matchMedia("(max-width: 1279px)").matches));

  const panel = (collapsed?: boolean, onToggle?: () => void) => (
    <SessionsPanel
      sessions={sessions}
      activeHref={pathname}
      collapsed={collapsed}
      onToggle={onToggle}
      onNavigate={() => setDrawer(null)}
    />
  );

  return (
    <SoonProvider>
      <Suspense fallback={null}>
        <DomainFromQuery domains={domains} onDomain={setDomainId} />
      </Suspense>
      <ShellContext.Provider value={{ domain, selectDomain, pending, pendingTopic, startSession, graphMode, setGraphMode, openGraph, closeDrawer: () => setDrawer(null), focusDomain: setDomainId, menuOpen, openMenu, closeMenu }}>
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
        <div inert={!!drawer || menuOpen} className="flex h-dvh">
          {!fullscreen && (
            <aside
              aria-label={t.shell.subjects}
              className={cn("hidden shrink-0 border-r border-seam-soft transition-[width] duration-300 ease-out md:block", domainsCollapsed ? "w-14" : "w-62")}
            >
              <DomainSidebar
                domains={domains}
                activeId={activeId}
                onSelect={selectDomain}
                collapsed={domainsCollapsed}
                onToggle={() => setDomainsCollapsed((v) => !v)}
              />
            </aside>
          )}

          <main id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col outline-none">
            {/* Домашняя страница на телефоне: сама панель — основной контент, кнопка «Предметы» здесь не нужна */}
            {!fullscreen && <div className="flex items-center gap-1 border-b border-seam-soft px-2 py-1.5 md:hidden">
              {pathname !== "/" && (
                <IconButton label={t.shell.subjects} onClick={(e) => openDrawer("domains", e.currentTarget)}>
                  <PanelLeft />
                </IconButton>
              )}
              <p className="display-caps text-[1.125rem] text-paper">Qvack!</p>
              {/* на телефоне кнопка меню в верхней полосе */}
              <div ref={barToggle} data-menu-anchor className="ml-auto">
                <MenuToggle
                  open={menuOpen}
                  onClick={() => (menuOpen ? closeMenu() : openMenu(barToggle.current?.querySelector("button") ?? null))}
                  labelOpen={t.menu.open}
                  labelClose={t.menu.close}
                />
              </div>
              {!inSettings && <IconButton label={t.shell.sessions} onClick={(e) => openDrawer("sessions", e.currentTarget)}>
                <History />
              </IconButton>}
            </div>}
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

          {!inSettings && !fullscreen && <aside
            aria-label={t.shell.sessions}
            className={cn(
              "relative hidden shrink-0 border-l border-seam-soft transition-[width] duration-300 ease-out md:block",
              sessionsPref === null ? "w-14 xl:w-73" : sessionsPref ? "w-14" : "w-73",
            )}
          >
            <IndicatorPills />
            {sessionsPref === null ? (
              <>
                <div className="h-full xl:hidden">{panel(true, toggleSessions)}</div>
                <div className="hidden h-full xl:block">{panel(false, toggleSessions)}</div>
              </>
            ) : (
              panel(sessionsPref, toggleSessions)
            )}
          </aside>}
        </div>

        {/* Телефон: панели — шторки */}
        <div
          aria-hidden
          onClick={() => setDrawer(null)}
          className={cn(
            "fixed inset-0 z-40 bg-cloth-deep/70 transition-opacity duration-300 md:hidden",
            drawer ? "opacity-100" : "pointer-events-none opacity-0",
          )}
        />
        {(fullscreen ? ([] as const) : inSettings ? (["domains"] as const) : (["domains", "sessions"] as const)).map((side) => {
          const open = drawer === side;
          return (
            <div
              key={side}
              role="dialog"
              aria-modal="true"
              aria-label={side === "domains" ? t.shell.subjects : t.shell.sessions}
              inert={!open}
              onKeyDown={trapTab}
              className={cn(
                "fixed inset-y-0 z-50 w-[min(20rem,86vw)] border-seam bg-cloth-deep transition-transform duration-300 ease-out md:hidden",
                side === "domains" ? "left-0 border-r" : "right-0 border-l",
                open ? "translate-x-0" : side === "domains" ? "-translate-x-full" : "translate-x-full",
              )}
            >
              <IconButton ref={open ? closeBtn : undefined} label={t.shell.close} className="absolute top-2 right-2 z-10" onClick={() => setDrawer(null)}>
                <X />
              </IconButton>
              {side === "domains" ? (
                <DomainSidebar domains={domains} activeId={activeId} onSelect={selectDomain} withMenu={false} />
              ) : (
                panel()
              )}
            </div>
          );
        })}

        <SiteMenu open={menuOpen && !fullscreen} onClose={closeMenu} anchor={menuAnchor} returnFocusRef={menuOpener} />
      </ShellContext.Provider>
    </SoonProvider>
  );
}
