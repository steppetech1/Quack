"use client";

import { BookOpen, FileText, MoreHorizontal, Presentation } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useFmt, useT } from "@/lib/i18n/provider";
import type { Material, Moment, Topic } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { Composer } from "./composer";
import { IconButton } from "./icon-button";
import { useSoon } from "./soon";
import { useMenu } from "./use-menu";
import { STATE_COLOR } from "./topic-card";

/** Кольцо памяти темы — тот же рисунок, что у точки в графе, но крупнее */
export function MemoryRing({ state, retention = 0, size = 40 }: { state: Topic["state"]; retention?: number; size?: number }) {
  const color = STATE_COLOR[state];
  const R = 16;
  const C = 2 * Math.PI * R;
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden className="shrink-0">
      <circle cx={20} cy={20} r={R} fill="var(--cloth)" stroke="var(--seam)" strokeWidth={2.5} strokeDasharray={state === "new" ? "3 5" : undefined} />
      {state !== "new" && (
        <circle
          cx={20}
          cy={20}
          r={R}
          fill="none"
          stroke={color}
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeDasharray={`${C * retention} ${C}`}
          transform="rotate(-90 20 20)"
        />
      )}
      {state === "new" ? (
        <circle cx={20} cy={20} r={5} fill="none" stroke={color} strokeWidth={1.8} />
      ) : (
        <circle cx={20} cy={20} r={6.5} fill={color} />
      )}
    </svg>
  );
}

/** Кольцо прогресса главы, как узел главы в графе */
export function HubRing({ progress, suggested, size = 56 }: { progress: number; suggested?: boolean; size?: number }) {
  const R = 22;
  const C = 2 * Math.PI * R;
  return (
    <svg width={size} height={size} viewBox="0 0 56 56" aria-hidden className="shrink-0">
      <circle cx={28} cy={28} r={R} fill="var(--cloth-raised)" stroke="var(--seam)" strokeWidth={3} strokeDasharray={suggested ? "4 6" : undefined} />
      {!suggested && progress > 0 && (
        <circle
          cx={28}
          cy={28}
          r={R}
          fill="none"
          stroke="var(--st-solid)"
          strokeWidth={3}
          strokeLinecap="round"
          strokeDasharray={`${C * progress} ${C}`}
          transform="rotate(-90 28 28)"
        />
      )}
    </svg>
  );
}

const CRUMB_LINK = "inline-flex min-h-10 items-center rounded-control px-2 transition-colors hover:bg-cloth-hover hover:text-paper";

/** Ссылка «← назад» над заголовком: везде на одном месте и в одном стиле */
export function BackLink({ href, children }: { href: string; children: React.ReactNode }) {
  const t = useT();
  return (
    <nav aria-label={t.page.path} className="-ml-2 flex flex-wrap items-center text-small text-paper-dim">
      <Link href={href} className={CRUMB_LINK}>
        ← {children}
      </Link>
    </nav>
  );
}

/** «← Матанализ / § IV Ряды» */
export function Crumbs({ domainTitle, hub, noChapter }: { domainTitle: string; hub?: { id: string; num: string; title: string }; noChapter?: string }) {
  const t = useT();
  return (
    <nav aria-label={t.page.path} className="-ml-2 flex flex-wrap items-center text-small text-paper-dim">
      <Link href="/" className={CRUMB_LINK}>
        ← {domainTitle}
      </Link>
      {hub && (
        <>
          <span aria-hidden>/</span>
          <Link href={`/hub/${hub.id}`} className={CRUMB_LINK}>
            <span className="num">§ {hub.num}</span>&nbsp;{hub.title}
          </Link>
        </>
      )}
      {!hub && noChapter && (
        <>
          <span aria-hidden>/</span>
          <span className="inline-flex min-h-10 items-center px-2">{noChapter}</span>
        </>
      )}
    </nav>
  );
}

/** Общий каркас страницы: прокручиваемая колонка по центру, одна ось для всех экранов */
export function PageShell({
  width = 720,
  className,
  children,
  ...rest
}: { width?: 720 | 960; className?: string; children: React.ReactNode } & Omit<React.ComponentProps<"div">, "className" | "children">) {
  return (
    <div {...rest} className={cn("min-h-0 flex-1 overflow-y-auto px-4 pt-2 pb-12 md:px-7 md:pt-4", className)}>
      <div className={cn("mx-auto w-full", width === 960 ? "max-w-[960px]" : "max-w-[720px]")}>{children}</div>
    </div>
  );
}

/** Раздел страницы: средник и мелкий моно-заголовок «§ Название» */
export function PageSection({
  id,
  title,
  count,
  action,
  children,
}: {
  id: string;
  title: string;
  count?: number;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-h`} id={id} className="mt-9 scroll-mt-4 border-t border-seam-soft pt-4">
      <div className="mb-3 flex min-h-10 items-center justify-between gap-3">
        <h2 id={`${id}-h`} className="num text-small tracking-wide text-paper-dim uppercase">
          § {title}
          {count !== undefined && <span className="ml-2 text-paper">{count}</span>}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export const TEXT_BTN =
  "inline-flex h-10 items-center rounded-control px-3 text-small text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper";

/** Действие: ссылка, если есть адрес сессии, иначе кнопка с «скоро» */
export function ActionButton({
  label,
  primary,
  href,
  soonText,
}: {
  label: string;
  primary?: boolean;
  href?: string;
  soonText: string;
}) {
  const soon = useSoon();
  const cls = cn(
    "inline-flex h-10 items-center justify-center rounded-control px-4 text-body font-semibold transition-colors",
    primary ? "bg-ribbon text-on-ribbon hover:bg-ribbon-hover" : "border border-seam text-paper hover:border-rule hover:bg-cloth-hover",
  );
  return href ? (
    <Link href={href} className={cls}>
      {label}
    </Link>
  ) : (
    <button type="button" onClick={() => soon(soonText)} className={cls}>
      {label}
    </button>
  );
}

/** Меню «⋯»: пункты пока ведут на «скоро» */
export function MoreMenu({ items }: { items: { label: string; soon: string }[] }) {
  const t = useT();
  const soon = useSoon();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const onMenuKey = useMenu(open, root, () => setOpen(false));

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: PointerEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <div ref={root} className="relative shrink-0">
      <IconButton label={t.page.more} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <MoreHorizontal />
      </IconButton>
      {open && (
        <ul role="menu" aria-label={t.page.actions} onKeyDown={onMenuKey} className="absolute top-full right-0 z-20 mt-1 w-64 rounded-[14px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]">
          {items.map((it) => (
            <li key={it.label} role="none">
              <button
                role="menuitem"
                type="button"
                onClick={() => {
                  setOpen(false);
                  soon(it.soon);
                }}
                className="flex min-h-10 w-full items-center rounded-option px-3 text-left text-body text-paper transition-colors hover:bg-cloth-hover"
              >
                {it.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const PILL = "inline-flex shrink-0 items-center rounded-full border border-seam px-2.5 py-0.5 text-small whitespace-nowrap text-paper-dim";

export function MaterialsList({ items }: { items: Material[] }) {
  const t = useT();
  const soon = useSoon();
  if (!items.length) return <p className="text-paper-dim">{t.page.noMaterials}</p>;
  const Icon = { pdf: FileText, slides: Presentation, book: BookOpen } as const;
  return (
    <ul>
      {items.map((m) => {
        const I = Icon[m.kind];
        return (
          <li key={m.id} className="border-b border-seam-soft last:border-0">
            <button
              type="button"
              onClick={() => soon(t.page.materialSoon(m.title))}
              className="flex min-h-11 w-full items-center gap-3 rounded-option px-1 py-2 text-left transition-colors hover:bg-cloth-hover"
            >
              <I aria-hidden className="size-[18px] shrink-0 stroke-[1.6] text-paper-dim" />
              <span className="min-w-0 flex-1 text-paper">{m.title}</span>
              <span className="num shrink-0 text-small text-paper-dim">{m.ref}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function MomentsList({ items, showTopic }: { items: (Moment & { href?: string; topicTitle?: string })[]; showTopic?: boolean }) {
  const t = useT();
  const { dayLabel } = useFmt();
  const soon = useSoon();
  if (!items.length) return <p className="text-paper-dim">{showTopic ? t.page.noMomentsHub : t.page.noMomentsTopic}</p>;
  return (
    <ul>
      {items.map((m) => {
        const body = (
          <>
            <span className="num w-14 shrink-0 text-small text-paper-dim">{dayLabel(m.date)}</span>
            <span className="min-w-0 flex-1 text-paper">
              {m.title}
              {showTopic && m.topicTitle && <span className="block text-small text-paper-dim">{m.topicTitle}</span>}
            </span>
            <span className={cn(PILL, m.kind === "mistake" && "border-st-confused/50 text-st-confused")}>{t.page.moment[m.kind]}</span>
          </>
        );
        const cls =
          "flex min-h-11 w-full items-center gap-3 rounded-option px-1 py-2 text-left transition-colors hover:bg-cloth-hover max-sm:flex-wrap";
        return (
          <li key={`${m.topicId}-${m.id}`} className="border-b border-seam-soft last:border-0">
            {m.href ? (
              <Link href={m.href} className={cls}>
                {body}
              </Link>
            ) : (
              <button type="button" onClick={() => soon(t.soon.sessionHistory(m.title))} className={cls}>
                {body}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Каркас страницы: прокручиваемая колонка + поле ввода с меткой фокуса */
export function PageFrame({ chipLabel, children }: { chipLabel: string; children: React.ReactNode }) {
  const t = useT();
  const { startSession } = useShell();
  const [chip, setChip] = useState(true);
  return (
    <>
      <PageShell>{children}</PageShell>
      <div className="px-4 pb-4 md:px-7 md:pb-5">
        <div className="mx-auto w-full max-w-[720px]">
          <Composer
            label={t.page.composerLabel(chipLabel)}
            onSend={startSession}
            primary={false}
            chip={chip ? { label: chipLabel, onRemove: () => setChip(false) } : undefined}
          />
        </div>
      </div>
    </>
  );
}
