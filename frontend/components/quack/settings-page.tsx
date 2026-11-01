"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { API_MODE, del as apiDelete, idem, patch } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useLocale, useT } from "@/lib/i18n/provider";
import { LOCALES, LOCALE_NAME } from "@/lib/i18n/locale";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { PageShell, TEXT_BTN } from "./page-parts";
import { SkillsTab } from "./skills-tab";
import { useSoon } from "./soon";

const TAB_IDS = ["general", "domains", "skills", "usage", "language", "account"] as const;
type TabId = (typeof TAB_IDS)[number];

const KEY = "quack.settings";
type Prefs = { name: string; address: "ty" | "vy"; course: string; replyLang: "ui" | "materials" };
/** Settings: locale defaults, with whatever is saved on top: the backend profile, or localStorage on mocks */
function usePrefs() {
  const t = useT();
  const { me, reload } = useData();
  // On the backend the name comes from the profile; the mock name and course are for mocks only
  const defaults = useMemo<Prefs>(
    () =>
      API_MODE
        ? { name: me?.display_name ?? "", address: "ty", course: "", replyLang: "ui" }
        : { name: t.settings.defaultName, address: "ty", course: t.settings.defaultCourse, replyLang: "ui" },
    [t, me],
  );
  const [stored, setStored] = useState<Partial<Prefs> | null>(null);
  useEffect(() => {
    const read = () => {
      if (API_MODE) {
        if (me) setStored(me.preferences as Partial<Prefs>);
        return;
      }
      try {
        const raw = localStorage.getItem(KEY);
        if (raw) setStored(JSON.parse(raw) as Partial<Prefs>);
      } catch {}
    };
    read();
  }, [me]);
  const saved = useMemo(() => ({ ...defaults, ...stored }), [defaults, stored]);
  const save = useCallback((next: Prefs) => {
    setStored(next);
    if (API_MODE) {
      // the backend merges keys into the stored preferences; the profile is re-read so other pages see them
      void patch("/v1/learner/preferences", { preferences: next, idempotency_key: idem() }).then(reload, () => {});
      return;
    }
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {}
  }, [reload]);
  return [saved, save] as const;
}

const FIELD =
  "h-10 w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper placeholder:text-paper-dim hover:border-rule";
const SECONDARY =
  "inline-flex h-10 shrink-0 items-center justify-center rounded-control border border-seam px-4 text-body font-semibold text-paper transition-colors hover:border-rule hover:bg-cloth-hover";

export function SettingsPage() {
  const tx = useT();
  const TABS = TAB_IDS.map((id) => ({ id, label: tx.settings.tabs[id] }));
  const router = useRouter();
  const param = useSearchParams().get("tab");
  const tab: TabId = TABS.some((t) => t.id === param) ? (param as TabId) : "general";
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [prefs, savePrefs] = usePrefs();
  const [toast, setToast] = useState<{ n: number } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const saved = useCallback(
    (next: Prefs) => {
      savePrefs(next);
      clearTimeout(timer.current);
      setToast((t) => ({ n: (t?.n ?? 0) + 1 }));
      timer.current = setTimeout(() => setToast(null), 2200);
    },
    [savePrefs],
  );

  const go = (id: TabId) => router.replace(id === "general" ? "/settings" : `/settings?tab=${id}`, { scroll: false });

  const onKey = (e: React.KeyboardEvent, i: number) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    const to = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : step ? (i + step + TABS.length) % TABS.length : -1;
    if (to < 0) return;
    e.preventDefault();
    go(TABS[to].id);
    refs.current[TABS[to].id]?.focus();
  };

  const current = TABS.find((t) => t.id === tab)!;

  return (
    <PageShell width={960}>
      <h1 className="mt-2 font-serif text-display font-bold tracking-[-0.01em] text-paper">{tx.settings.title}</h1>

      <div className="mt-5 flex gap-8 max-md:flex-col max-md:gap-4">
        <div
          role="tablist"
          aria-label={tx.settings.tabsLabel}
          aria-orientation="vertical"
          className="flex w-52 shrink-0 flex-col gap-0.5 max-md:-mx-4 max-md:w-auto max-md:flex-row max-md:gap-1 max-md:overflow-x-auto max-md:px-4"
        >
          {TABS.map((t, i) => (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[t.id] = el;
              }}
              role="tab"
              type="button"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              tabIndex={tab === t.id ? 0 : -1}
              onClick={() => go(t.id)}
              onKeyDown={(e) => onKey(e, i)}
              className={cn(
                "min-h-10 shrink-0 rounded-option px-3 text-left text-body whitespace-nowrap transition-colors",
                tab === t.id ? "bg-cloth-raised text-paper" : "text-paper-dim hover:bg-cloth-hover hover:text-paper",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0} className="min-w-0 max-w-[640px] flex-1 outline-none">
          <h2 className="font-serif text-[1.375rem] font-bold text-paper">{current.label}</h2>
          {tab === "general" && <GeneralTab prefs={prefs} onSave={saved} />}
          {tab === "domains" && <DomainsTab />}
          {tab === "skills" && <div className="mt-4"><SkillsTab /></div>}
          {tab === "usage" && <UsageTab />}
          {tab === "language" && <LanguageTab prefs={prefs} onSave={saved} />}
          {tab === "account" && <AccountTab />}
        </div>
      </div>

      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-8 z-50 flex justify-center px-4">
        {toast && (
          <p
            key={toast.n}
            className="animate-toast rounded-xl border border-seam bg-cloth-raised px-4 py-2.5 text-small text-paper shadow-[0_8px_24px_-8px_rgb(0_0_0/0.6)]"
          >
            {tx.settings.saved}
          </p>
        )}
      </div>
    </PageShell>
  );
}

/** Раздел вкладки: линия сверху, подпись Golos 500, подсказка */
function Row({ label, help, children, id }: { label: string; help?: string; children: React.ReactNode; id?: string }) {
  return (
    <div className="border-t border-seam-soft py-5 first-of-type:mt-4">
      <p id={id} className="font-medium text-paper">
        {label}
      </p>
      {help && <p className="mt-0.5 text-small text-paper-dim">{help}</p>}
      <div className="mt-3">{children}</div>
    </div>
  );
}

function Radio<T extends string>({
  name,
  value,
  options,
  onChange,
  labelledBy,
}: {
  name: string;
  value: T;
  options: { id: T; label: string; hint?: string; disabled?: boolean; soon?: boolean }[];
  onChange: (v: T) => void;
  labelledBy: string;
}) {
  const tx = useT();
  return (
    <div role="radiogroup" aria-labelledby={labelledBy} className="flex flex-col gap-0.5">
      {options.map((o) => (
        <label
          key={o.id}
          className={cn(
            "flex min-h-10 cursor-pointer items-center gap-3 rounded-option px-3 text-body transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ribbon",
            value === o.id ? "bg-cloth-raised text-paper" : "text-paper-dim hover:bg-cloth-hover hover:text-paper",
            o.disabled && "cursor-not-allowed opacity-80",
          )}
        >
          <input
            type="radio"
            name={name}
            value={o.id}
            checked={value === o.id}
            disabled={o.disabled}
            onChange={() => onChange(o.id)}
            className="size-4 shrink-0 accent-[var(--paper)]"
          />
          <span className="flex-1">{o.label}</span>
          {o.hint && <span className="text-small text-paper-dim">{o.hint}</span>}
          {o.soon && <span className="num text-small text-paper-dim">{tx.settings.soonTag}</span>}
        </label>
      ))}
    </div>
  );
}

function GeneralTab({ prefs, onSave }: { prefs: Prefs; onSave: (p: Prefs) => void }) {
  const tx = useT();
  const soon = useSoon();
  const { me } = useData();
  const profileName = API_MODE ? (me?.display_name ?? me?.email.split("@")[0] ?? "") : tx.profile.name;
  const profileEmail = API_MODE ? (me?.email ?? "") : tx.profile.email;
  const [draft, setDraft] = useState(prefs);
  const [synced, setSynced] = useState(prefs);
  // Сохранённое подгрузилось из localStorage: обновляем черновик, пока он не тронут
  if (prefs !== synced) {
    if (draft === synced) setDraft(prefs);
    setSynced(prefs);
  }
  const dirty = JSON.stringify(draft) !== JSON.stringify(prefs);
  const uid = useId();
  const set = <K extends keyof Prefs>(k: K, v: Prefs[K]) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty) onSave(draft);
      }}
    >
      <div className="mt-4 mb-5 flex items-center gap-4">
        <div
          aria-hidden
          className="flex size-14 shrink-0 items-center justify-center rounded-full border border-seam bg-cloth-raised font-heading text-[1.5rem] text-paper"
        >
          {API_MODE ? profileName.charAt(0).toUpperCase() : tx.profile.initial}
        </div>
        <div className="min-w-0">
          <p className="text-body font-medium text-paper">{profileName}</p>
          <p className="truncate text-small text-paper-dim">{profileEmail}</p>
        </div>
      </div>

      <Row label={tx.settings.nameLabel} id={`${uid}-name`}>
        <input aria-labelledby={`${uid}-name`} value={draft.name} onChange={(e) => set("name", e.target.value)} className={FIELD} />
      </Row>
      <Row label={tx.settings.addressLabel} help={tx.settings.addressHelp} id={`${uid}-addr`}>
        <Radio
          name="address"
          labelledBy={`${uid}-addr`}
          value={draft.address}
          onChange={(v) => set("address", v)}
          options={[
            { id: "ty", label: tx.settings.casual },
            { id: "vy", label: tx.settings.formal },
          ]}
        />
      </Row>
      <Row label={tx.settings.courseLabel} help={tx.settings.courseHelp} id={`${uid}-course`}>
        <input aria-labelledby={`${uid}-course`} value={draft.course} onChange={(e) => set("course", e.target.value)} className={FIELD} />
      </Row>
      <Row label={tx.settings.themeLabel} id={`${uid}-theme`}>
        <div role="radiogroup" aria-labelledby={`${uid}-theme`} className="flex flex-col gap-0.5">
          <div className="flex min-h-10 items-center gap-3 rounded-option bg-cloth-raised px-3 text-body text-paper">
            <input type="radio" name="theme" checked readOnly className="size-4 accent-[var(--paper)]" aria-label={tx.settings.dark} />
            {tx.settings.dark}
          </div>
          <button
            type="button"
            role="radio"
            aria-checked="false"
            onClick={() => soon(tx.settings.lightSoon)}
            className="flex min-h-10 items-center gap-3 rounded-option px-3 text-left text-body text-paper-dim transition-colors hover:bg-cloth-hover hover:text-paper"
          >
            <span aria-hidden className="size-4 rounded-full border border-paper-dim" />
            {tx.settings.light}
          </button>
        </div>
      </Row>

      {dirty && (
        <div className="border-t border-seam-soft pt-5">
          <button
            type="submit"
            className="h-10 rounded-control bg-ribbon px-4 text-body font-semibold text-on-ribbon transition-colors hover:bg-ribbon-hover"
          >
            {tx.settings.save}
          </button>
        </div>
      )}
    </form>
  );
}

function DomainsTab() {
  const tx = useT();
  const { domains } = useData();
  const { selectDomain } = useShell();
  return (
    <div className="mt-4">
      <ul className="border-t border-seam-soft">
        {domains.map((d) => (
          <li key={d.id} className="border-b border-seam-soft">
            <button
              type="button"
              onClick={() => selectDomain(d.id)}
              className="flex min-h-12 w-full items-center gap-3 rounded-option px-1 py-2 text-left transition-colors hover:bg-cloth-hover"
            >
              <span className="min-w-0 flex-1 text-paper">
                {d.title}
              </span>
              <span className="num shrink-0 text-small text-paper-dim">
                {d.readiness !== undefined ? tx.settings.readiness(d.readiness) : tx.settings.topicsCount(d.topics.length)}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <Link href="/domains/new" className={cn(SECONDARY, "mt-5 gap-2")}>
        <Plus aria-hidden className="size-4" />
        {tx.shell.newSubject}
      </Link>
    </div>
  );
}

function Meter({ label, value, text, note }: { label: string; value: number; text: string; note?: string }) {
  const id = useId();
  return (
    <div className="py-4">
      <div className="flex items-baseline justify-between gap-4">
        <p id={id} className="text-body text-paper">
          {label}
        </p>
        <p className="num shrink-0 text-small text-paper">{text}</p>
      </div>
      <div
        role="progressbar"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        aria-valuetext={text}
        className="mt-2 h-2 overflow-hidden rounded-full bg-seam"
      >
        <div className="h-full rounded-full bg-paper-dim" style={{ width: `${value}%` }} />
      </div>
      {note && <p className="mt-1.5 text-small text-paper-dim">{note}</p>}
    </div>
  );
}

function UsageTab() {
  const tx = useT();
  const soon = useSoon();
  return (
    <div className="mt-4">
      <div className="flex items-center justify-between gap-4 pb-4">
        <p className="text-body text-paper">
          {tx.settings.plan} <span className="font-medium">{tx.profile.plan}</span>
        </p>
        <button type="button" onClick={() => soon(tx.soon.plans)} className={TEXT_BTN}>
          {tx.settings.upgrade}
        </button>
      </div>
      <div className="divide-y divide-seam-soft border-t border-seam-soft">
        <Meter label={tx.settings.meterMessages} value={62} text="62%" note={tx.settings.meterMessagesNote} />
        <Meter label={tx.settings.meterWeek} value={38} text="38%" note={tx.settings.meterWeekNote} />
        <Meter label={tx.settings.meterAgents} value={35} text={tx.settings.meterAgentsText} />
        <Meter label={tx.settings.meterStorage} value={24} text={tx.settings.meterStorageText} />
      </div>
      <p className="border-t border-seam-soft pt-4 text-small text-paper-dim">
        {tx.settings.usageFooter}
      </p>
    </div>
  );
}

function LanguageTab({ prefs, onSave }: { prefs: Prefs; onSave: (p: Prefs) => void }) {
  const tx = useT();
  const { locale, setLocale } = useLocale();
  const uid = useId();
  return (
    <div>
      <Row label={tx.settings.uiLanguage} id={`${uid}-ui`}>
        <Radio
          name="ui-lang"
          labelledBy={`${uid}-ui`}
          value={locale}
          onChange={setLocale}
          options={LOCALES.map((id) => ({ id, label: LOCALE_NAME[id] }))}
        />
      </Row>
      <Row label={tx.settings.replyLanguage} help={tx.settings.replyHelp} id={`${uid}-reply`}>
        <Radio
          name="reply-lang"
          labelledBy={`${uid}-reply`}
          value={prefs.replyLang}
          onChange={(v) => onSave({ ...prefs, replyLang: v })}
          options={[
            { id: "ui", label: tx.settings.replyUi },
            { id: "materials", label: tx.settings.replyMaterials },
          ]}
        />
      </Row>
    </div>
  );
}

function AccountTab() {
  const tx = useT();
  const { me, logout } = useData();
  const soon = useSoon();
  const [confirm, setConfirm] = useState(false);
  const del = useRef<HTMLButtonElement>(null);
  const yes = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);

  useEffect(() => {
    if (confirm) yes.current?.focus();
    else if (returnFocus.current) {
      returnFocus.current = false;
      del.current?.focus();
    }
  }, [confirm]);

  return (
    <div>
      <Row label={tx.settings.email} help={tx.settings.emailHelp}>
        <div className="flex flex-wrap items-center gap-3">
          <input readOnly aria-label={tx.settings.email} value={me?.email ?? tx.profile.email} className={cn(FIELD, "max-w-xs flex-1 text-paper-dim")} />
          <button type="button" onClick={() => soon(tx.settings.changeEmailSoon)} className={SECONDARY}>
            {tx.settings.changeEmail}
          </button>
        </div>
      </Row>
      <Row label={tx.settings.export} help={tx.settings.exportHelp}>
        {API_MODE ? (
          <Link href="/library" className={SECONDARY}>
            {tx.settings.exportBtn}
          </Link>
        ) : (
          <button type="button" onClick={() => soon(tx.settings.exportSoon)} className={SECONDARY}>
            {tx.settings.exportBtn}
          </button>
        )}
      </Row>
      <Row label={tx.settings.deleteLabel} help={tx.settings.deleteHelp}>
        {confirm ? (
          <div role="group" aria-label={tx.settings.deleteConfirmLabel} className="rounded-[14px] border border-seam p-4">
            <p className="text-body text-paper">{tx.settings.deleteConfirm}</p>
            <div className="mt-3 flex gap-2">
              <button
                ref={yes}
                type="button"
                onClick={() => {
                  setConfirm(false);
                  if (!API_MODE) {
                    soon(tx.settings.deleteSoon);
                    return;
                  }
                  // the backend tombstones the learner and purges the data later; the token stops working
                  void apiDelete("/v1/learner", { idempotency_key: idem() }).then(logout, () => soon(tx.settings.deleteSoon));
                }}
                className="h-10 rounded-control border border-st-confused px-4 text-body font-semibold text-st-confused transition-colors hover:bg-cloth-hover"
              >
                {tx.settings.deleteYes}
              </button>
              <button
                type="button"
                onClick={() => {
                  returnFocus.current = true;
                  setConfirm(false);
                }}
                className={SECONDARY}
              >
                {tx.settings.cancel}
              </button>
            </div>
          </div>
        ) : (
          <button
            ref={del}
            type="button"
            onClick={() => setConfirm(true)}
            className="inline-flex h-10 items-center rounded-control px-3 text-body font-semibold text-st-confused transition-colors hover:bg-cloth-hover"
          >
            {tx.settings.deleteLabel}
          </button>
        )}
      </Row>
    </div>
  );
}
