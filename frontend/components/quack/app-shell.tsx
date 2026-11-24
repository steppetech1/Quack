"use client";

import { PanelLeft, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useRef, useState } from "react";
import type { FileRef } from "./attachments";
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
import { IndicatorPills } from "./graph/indicator-pills";
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
  // Панель предметов слева: сворачивается только на десктопе (≥768px); на телефоне тоггла нет, всегда развёрнута
  const [domainsCollapsed, setDomainsCollapsed] = useState(false);
  const [graphMode, setGraphMode] = useState<GraphMode>({ kind: "overview" });
  const [drawer, setDrawer] = useState(false);
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
    if (pathname !== "/") router.push("/");
  };

  const openGraph = (m: GraphMode) => {
    setGraphMode(m);
    setDrawer(false);
    if (pathname !== "/" || window.location.search.includes("view=canvases")) router.push("/");
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
      ? [{ id: "new", href: "/session/new", date: todayIso(), title: pending, topics: [] }, ...domain.sessions]
      : domain.sessions;

  return (
    <SoonProvider>
      <Suspense fallback={null}>
        <DomainFromQuery domains={domains} onDomain={setDomainId} />
      </Suspense>
      <ShellContext.Provider value={{ domain, selectDomain, pending, pendingFiles, pendingTopic, startSession, graphMode, setGraphMode, openGraph, closeDrawer: () => setDrawer(false), sessions, focusDomain: setDomainId }}>
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
                <IconButton label={t.shell.subjects} onClick={(e) => openDrawer(e.currentTarget)}>
                  <PanelLeft />
                </IconButton>
              )}
              <p className="display-caps text-[1.125rem] text-paper">Quack!</p>
            </div>}
            {/* Сессия: путь назад в граф домена; граф открывается по «/» */}
            {inSessionPage && (
              <div className="shrink-0 border-b border-seam-soft px-4 md:px-7">
                <BackLink href="/">{t.shell.backToGraph}</BackLink>
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

          {/* Индикаторы графа стоят у правого края центра: нулевая ширина, сами «выезжают» влево */}
          {!inSettings && !fullscreen && (
            <div className="relative hidden w-0 shrink-0 md:block">
              <IndicatorPills />
            </div>
          )}
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
            <DomainSidebar domains={domains} activeId={activeId} onSelect={selectDomain} />
          </div>
        )}
      </ShellContext.Provider>
    </SoonProvider>
  );
}
