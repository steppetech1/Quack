"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { API_MODE, ApiError, del as apiDelete, get, type S } from "@/lib/api/client";
import { useData } from "@/lib/api/data";
import { useLocale, useT } from "@/lib/i18n/provider";
import { LOCALES, LOCALE_NAME } from "@/lib/i18n/locale";
import { cn } from "@/lib/utils";
import { useShell } from "./app-shell";
import { useErrorText } from "./live/error-text";
import { PageShell, TEXT_BTN } from "./page-parts";
import { SkillsTab } from "./skills-tab";
import { useNotice, useSoon } from "./soon";

const TAB_IDS = ["general", "domains", "skills", "usage", "language", "account"] as const;
type TabId = (typeof TAB_IDS)[number];

const KEY = "quack.settings";
type Prefs = { name: string; address: "ty" | "vy"; course: string; replyLang: "ui" | "materials" };

/** Only the keys this page owns, and only with values it understands: the backend object is free-form */
function pickPrefs(raw: unknown): Partial<Prefs> {
  if (!raw || typeof raw !== "object") return {};
  const r = raw as Record<string, unknown>;
  const out: Partial<Prefs> = {};
  if (typeof r.name === "string") out.name = r.name;
  if (r.address === "ty" || r.address === "vy") out.address = r.address;
  if (typeof r.course === "string") out.course = r.course;
  if (r.replyLang === "ui" || r.replyLang === "materials") out.replyLang = r.replyLang;
  return out;
}

/**
 * Settings: locale defaults with the saved values on top. With the backend they live in the learner's
 * preferences (`PATCH /v1/learner/preferences`), so they follow the account; on mocks — in localStorage.
 */
function usePrefs() {
  const t = useT();
  const { me, savePreferences } = useData();
  const defaults = useMemo<Prefs>(
    () => ({ name: t.settings.defaultName, address: "ty", course: t.settings.defaultCourse, replyLang: "ui" }),
    [t],
  );
  const [stored, setStored] = useState<Partial<Prefs> | null>(null);
  useEffect(() => {
    if (API_MODE) return;
    try {
      const raw = localStorage.getItem(KEY);
      // localStorage is only readable after mount
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (raw) setStored(pickPrefs(JSON.parse(raw)));
    } catch {}
  }, []);
  // the backend keeps the preferences next to the other settings of the learner (`preference.set {key, value}`)
  const remote = useMemo(() => pickPrefs(me?.settings), [me?.settings]);
  const saved = useMemo(() => ({ ...defaults, ...(API_MODE ? remote : stored) }), [defaults, remote, stored]);
  const save = useCallback(
    async (next: Prefs) => {
      if (API_MODE) return savePreferences(next);
      setStored(next);
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {}
    },
    [savePreferences],
  );
  return [saved, save] as const;
}

const FIELD =
  "h-10 w-full rounded-control border border-seam bg-cloth px-3 text-body text-paper transition-colors duration-(--dur-s) ease-out placeholder:text-paper-dim hover:border-rule focus-visible:border-ribbon";
const SECONDARY =
  "caps inline-flex h-10 shrink-0 items-center justify-center rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon";

export function SettingsPage() {
  const tx = useT();
  const TABS = TAB_IDS.map((id) => ({ id, label: tx.settings.tabs[id] }));
  const router = useRouter();
  const param = useSearchParams().get("tab");
  const tab: TabId = TABS.some((t) => t.id === param) ? (param as TabId) : "general";
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [prefs, savePrefs] = usePrefs();
  const [toast, setToast] = useState<{ n: number; failed: boolean } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const saved = useCallback(
    (next: Prefs) => {
      const show = (failed: boolean) => {
        clearTimeout(timer.current);
        setToast((t) => ({ n: (t?.n ?? 0) + 1, failed }));
        timer.current = setTimeout(() => setToast(null), failed ? 4000 : 2200);
      };
      savePrefs(next).then(
        () => show(false),
        () => show(true),
      );
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
      <h1 className="display-caps mt-2 text-display text-paper">{tx.settings.title}</h1>

      <div className="mt-5 flex gap-8 max-md:flex-col max-md:gap-4">
        <div
          role="tablist"
          aria-label={tx.settings.tabsLabel}
          aria-orientation="vertical"
          className="flex w-52 shrink-0 flex-col md:border-l md:border-seam-soft max-md:-mx-4 max-md:w-auto max-md:flex-row max-md:gap-1 max-md:overflow-x-auto max-md:px-4"
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
                "caps min-h-10 shrink-0 px-4 text-left whitespace-nowrap transition-colors duration-(--dur-s) ease-out md:border-l-2 max-md:border-b-2",
                tab === t.id ? "border-ribbon text-paper" : "border-transparent text-paper-dim hover:text-paper",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0} className="min-w-0 max-w-[640px] flex-1 outline-none">
          <h2 className="display-caps text-[1.375rem] text-paper">{current.label}</h2>
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
            className="animate-toast caps rounded-pill border border-seam bg-cloth-raised px-5 py-2.5 text-paper shadow-[0_8px_24px_-8px_rgb(0_0_0/0.6)]"
          >
            {toast.failed ? tx.settings.saveFailed : tx.settings.saved}
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
      <p id={id} className="caps text-paper">
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
            "flex min-h-10 cursor-pointer items-center gap-3 rounded-control border px-3 text-body transition-colors duration-(--dur-s) ease-out has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ribbon",
            value === o.id ? "border-seam text-paper" : "border-transparent text-paper-dim hover:border-seam-soft hover:text-paper",
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
            className="size-4 shrink-0 accent-[var(--ribbon)]"
          />
          <span className="flex-1">{o.label}</span>
          {o.hint && <span className="text-small text-paper-dim">{o.hint}</span>}
          {o.soon && <span className="caps text-paper-dim">{tx.settings.soonTag}</span>}
        </label>
      ))}
    </div>
  );
}

function GeneralTab({ prefs, onSave }: { prefs: Prefs; onSave: (p: Prefs) => void }) {
  const tx = useT();
  const soon = useSoon();
  const { me } = useData();
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
          className="flex size-14 shrink-0 items-center justify-center rounded-pill border border-seam font-display text-[1.5rem] text-paper"
        >
          {me ? prefs.name.trim().charAt(0).toUpperCase() || tx.profile.initial : tx.profile.initial}
        </div>
        <div className="min-w-0">
          <p className="display-caps text-body text-paper">{me ? prefs.name : tx.profile.name}</p>
          <p className="truncate text-small text-paper-dim">{me?.email ?? tx.profile.email}</p>
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
          <div className="flex min-h-10 items-center gap-3 rounded-control border border-seam px-3 text-body text-paper">
            <input type="radio" name="theme" checked readOnly className="size-4 accent-[var(--ribbon)]" aria-label={tx.settings.dark} />
            {tx.settings.dark}
          </div>
          <button
            type="button"
            role="radio"
            aria-checked="false"
            onClick={() => soon(tx.settings.lightSoon)}
            className="flex min-h-10 items-center gap-3 rounded-control border border-transparent px-3 text-left text-body text-paper-dim transition-colors duration-(--dur-s) ease-out hover:border-seam-soft hover:text-paper"
          >
            <span aria-hidden className="size-4 rounded-pill border border-paper-dim" />
            {tx.settings.light}
          </button>
        </div>
      </Row>

      {dirty && (
        <div className="border-t border-seam-soft pt-5">
          <button
            type="submit"
            className="caps h-10 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover"
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
              className="flex min-h-12 w-full items-center gap-3 rounded-option px-1 py-2 text-left transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover"
            >
              <span className="display-caps min-w-0 flex-1 text-paper">
                {d.title}
              </span>
              <span className="caps num shrink-0 text-paper-dim">
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
        <p className="caps num shrink-0 text-paper">{text}</p>
      </div>
      <div
        role="progressbar"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        aria-valuetext={text}
        className="mt-2 h-px overflow-hidden bg-seam"
      >
        <div className="h-full bg-ribbon" style={{ width: `${value}%` }} />
      </div>
      {note && <p className="mt-1.5 text-small text-paper-dim">{note}</p>}
    </div>
  );
}

/**
 * `GET /v1/llm/access`: the state of the shared money pool (`normal` / `tight` / `judges_only`) and what the learner can do about it.
 * The backend hides the remainder itself, so there is no meter, only a word. A failed read says so quietly and changes nothing else.
 */
function LlmAccessRow() {
  const tx = useT();
  const { savePreferences } = useData();
  const notice = useNotice();
  const [access, setAccess] = useState<S["LLMAccess"] | "unknown" | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let off = false;
    get<S["LLMAccess"]>("/v1/llm/access")
      .then((r) => !off && setAccess(r))
      .catch((e) => !off && setAccess(e instanceof ApiError && e.status === 401 ? null : "unknown"));
    return () => {
      off = true;
    };
  }, []);
  if (!access) return null;
  const s = tx.settings;
  const mode = access === "unknown" ? null : access.mode;
  const word = mode === "normal" ? s.accessNormal : mode === "tight" ? s.accessTight : mode === "judges_only" ? s.accessJudgesOnly : "";
  const note =
    access === "unknown"
      ? s.accessUnknown
      : access.judge && mode !== "normal"
        ? s.accessJudge
        : mode === "normal"
          ? s.accessNormalNote
          : mode === "tight"
            ? s.accessTightNote
            : s.accessJudgesOnlyNote;
  const becomeJudge = async () => {
    if (access === "unknown") return;
    setBusy(true);
    try {
      await savePreferences({ [access.preference_key]: true });
      setAccess({ ...access, judge: true, blocked: false });
      notice(s.accessJudgeSaved);
    } catch {
      notice(s.accessUnknown);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="border-t border-seam-soft py-4">
      <div className="flex items-baseline justify-between gap-4">
        <p className="text-body text-paper">{s.accessTitle}</p>
        {word && <p className="caps shrink-0 text-paper">{word}</p>}
      </div>
      <p role="status" className="mt-1.5 text-small text-paper-dim">
        {note}
      </p>
      {access !== "unknown" && access.blocked && (
        <button type="button" disabled={busy} onClick={() => void becomeJudge()} className={cn(SECONDARY, "mt-3 disabled:opacity-50")}>
          {s.accessJudgeButton}
        </button>
      )}
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
          {tx.settings.plan} <span className="caps">{tx.profile.plan}</span>
        </p>
        <button type="button" onClick={() => soon(tx.soon.plans)} className={TEXT_BTN}>
          {tx.settings.upgrade}
        </button>
      </div>
      {API_MODE && <LlmAccessRow />}
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
  const errorText = useErrorText();
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const notice = useNotice();
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
        {/* the backend can give an export by id (`GET /v1/learner/exports/{id}`) but has no call that starts one: until it does, no fake link */}
        <button type="button" onClick={() => notice(tx.exportNotice.unavailable)} className={SECONDARY}>
          {tx.settings.exportBtn}
        </button>
      </Row>
      <Row label={tx.settings.deleteLabel} help={tx.settings.deleteHelp}>
        {confirm ? (
          <div role="group" aria-label={tx.settings.deleteConfirmLabel} className="rounded-[2px] border border-seam p-4">
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
                  setDeleteError(null);
                  void apiDelete("/v1/learner").then(logout, (e) => setDeleteError(errorText(e)));
                }}
                className="caps h-10 rounded-pill border border-st-confused px-6 text-st-confused transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover"
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
            className="caps inline-flex h-10 items-center rounded-pill px-4 text-st-confused transition-colors duration-(--dur-s) ease-out hover:bg-cloth-hover"
          >
            {tx.settings.deleteLabel}
          </button>
        )}
        {deleteError && (
          <p role="alert" className="mt-2 text-small text-st-confused">
            {deleteError}
          </p>
        )}
      </Row>
    </div>
  );
}
