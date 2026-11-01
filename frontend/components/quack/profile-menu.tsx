"use client";

import { ArrowUpRight, Check, ChevronRight, Gauge, LifeBuoy, Languages, LogOut, Settings } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_MODE } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useLocale, useT } from "@/lib/i18n/provider";
import { LOCALES, LOCALE_NAME } from "@/lib/i18n/locale";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { useSoon } from "./soon";
import { useMenu } from "./use-menu";

const ITEM =
  "flex min-h-10 w-full items-center gap-2.5 rounded-option px-3 text-left text-body text-paper transition-colors hover:bg-cloth-hover";
const ICON = "size-[18px] shrink-0 stroke-[1.6] text-paper-dim";

/** Меню профиля внизу левой панели: открывается вверх, как в claude.ai */
export function ProfileMenu({ active }: { active: boolean }) {
  const router = useRouter();
  const t = useT();
  const { locale, setLocale } = useLocale();
  const soon = useSoon();
  const { closeDrawer } = useShell();
  const { me, logout } = useData();
  // С бэкендом — настоящий профиль, на моках — из словаря
  // На бэкенде, пока профиль не загрузился, мок-имя не показываем
  const name = me ? (me.display_name ?? me.email.split("@")[0]) : API_MODE ? "" : t.profile.name;
  const email = me ? me.email : API_MODE ? "" : t.profile.email;
  const initial = me ? name.charAt(0).toUpperCase() : API_MODE ? "" : t.profile.initial;
  const [open, setOpen] = useState(false);
  const [lang, setLang] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = () => {
    setOpen(false);
    setLang(false);
  };
  const onMenuKey = useMenu(open, root, close);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) {
        setOpen(false);
        setLang(false);
      }
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open]);

  const go = (href: string) => {
    close();
    closeDrawer();
    router.push(href);
  };
  const later = (what: string) => {
    close();
    soon(what);
  };

  return (
    <div ref={root} className="relative mt-auto border-t border-seam-soft pt-3">
      {open && (
        <div className="absolute inset-x-0 bottom-full z-30 mb-2 rounded-[14px] border border-seam bg-cloth-raised p-1.5 shadow-[0_12px_32px_-12px_rgb(0_0_0/0.7)]">
          <div className="px-3 pt-2 pb-2.5">
            <p className="text-body font-medium text-paper">{name}</p>
            <p className="truncate text-small text-paper-dim">{email}</p>
          </div>
          <ul role="menu" aria-label={t.profile.menu} onKeyDown={onMenuKey} className="border-t border-seam-soft pt-1.5">
            <li role="none">
              <button role="menuitem" type="button" onClick={() => go("/settings")} className={ITEM}>
                <Settings aria-hidden className={ICON} />
                {t.profile.settings}
              </button>
            </li>
            <li role="none">
              <button role="menuitem" type="button" onClick={() => go("/settings?tab=usage")} className={ITEM}>
                <Gauge aria-hidden className={ICON} />
                {t.profile.usage}
              </button>
            </li>
            <li role="none">
              <button role="menuitem" type="button" aria-expanded={lang} onClick={() => setLang((v) => !v)} className={ITEM}>
                <Languages aria-hidden className={ICON} />
                <span className="flex-1">{t.profile.language}</span>
                <ChevronRight aria-hidden className={cn("size-3.5 text-paper-dim transition-transform duration-200", lang && "rotate-90")} />
              </button>
              {lang && (
                <ul role="group" aria-label={t.profile.interfaceLanguage} className="mb-1">
                  {LOCALES.map((l) => (
                    <li key={l} role="none">
                      <button
                        role="menuitemradio"
                        aria-checked={locale === l}
                        lang={l}
                        type="button"
                        onClick={() => {
                          close();
                          if (l !== locale) setLocale(l);
                        }}
                        className={cn(ITEM, "pl-10")}
                      >
                        <span className="flex-1">{LOCALE_NAME[l]}</span>
                        {locale === l && <Check aria-hidden className="size-4 text-paper-dim" />}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
            <li role="none">
              <button role="menuitem" type="button" onClick={() => later(t.soon.help)} className={ITEM}>
                <LifeBuoy aria-hidden className={ICON} />
                {t.profile.help}
              </button>
            </li>
            <li role="separator" className="my-1.5 border-t border-seam-soft" />
            <li role="none">
              <button role="menuitem" type="button" onClick={() => later(t.soon.plans)} className={ITEM}>
                <ArrowUpRight aria-hidden className={ICON} />
                <span className="flex-1 whitespace-nowrap">{t.profile.upgrade}</span>
                <span className="num shrink-0 rounded-full border border-seam px-2 py-0.5 text-[0.75rem] text-paper-dim">{t.profile.plan}</span>
              </button>
            </li>
            <li role="none">
              <button role="menuitem" type="button" onClick={() => (me ? logout() : go("/login"))} className={ITEM}>
                <LogOut aria-hidden className={ICON} />
                {t.stubs.auth.logout}
              </button>
            </li>
          </ul>
        </div>
      )}
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex min-h-11 w-full items-center gap-2.5 rounded-control px-3 text-left text-paper transition-colors hover:bg-cloth-hover",
          (open || active) && "bg-cloth-raised",
        )}
      >
        <span aria-hidden className="flex size-7 shrink-0 items-center justify-center rounded-full bg-cloth-hover text-small text-paper-dim">
          {initial}
        </span>
        <span className="min-w-0 flex-1 truncate">{name}</span>
      </button>
    </div>
  );
}
