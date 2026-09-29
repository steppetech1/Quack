// Everything the client remembers for the signed-in student: chats and profile, saved programs,
// preparation, what Quack has shown, panel widths, where the skill map was left. One place, keyed by
// student, so signing out and back in brings back exactly what was there.
//
// Reads are synchronous, from a cache filled once when the student signs in (AuthGate waits for it).
// Writes land in the cache at once and reach the backend shortly after, coalesced per key: a typing
// reply or a burst of edits is one write, not one per frame. Anything still pending goes out when the
// tab is hidden or closed. Continuous gestures (panning, dragging, resizing) call `set` only when they
// end — the screens keep the in-between state to themselves.

import { ApiError, api } from "@/api/client";
import { REMOTE_PREP } from "../prep/remoteFlag";
import type { StateBackend, User } from "./contract";

const REMOTE = process.env.NEXT_PUBLIC_DATA_SOURCE === "remote";
/** Quack recomputed on the server: then the local source's baseline and history have nothing to hold */
const REMOTE_QUACK = (process.env.NEXT_PUBLIC_QUACK_SOURCE ?? process.env.NEXT_PUBLIC_DATA_SOURCE) === "remote";
/** Local storage is cheap; the network gets a longer pause to gather more into one request */
const FLUSH_MS = REMOTE ? 1200 : 250;

/* ---------- What is allowed to live here ---------- */

/**
 * `/state` is a bridge for what belongs to this browser, not a second database (ТЗ §5.6). Every key
 * is listed here with the reason it is allowed to stay; anything else is dropped rather than quietly
 * synced, so a domain slice cannot creep back in without someone editing this list.
 */
const ALLOWED: Record<string, string> = {
  // UI-настройки — целиком наши
  "quack-choice-layout": "ширины и свёрнутость панелей «Выбора»",
  "quack-hints-seen": "какие подсказки ученик уже видел",
  "quack-dashboard-watch": "что ученик добавил в «Слежу» на дашборде",
  "quack-advice-dismissed": "какие советы ученик убрал с глаз",

  // Экранные выборы: что открыто и что сравнивается — это не домен
  "quack-choice-workspace": "выбранные к показу и сравнению программы, состояние анкеты-интро",

  // «Подготовка» (prep/prepStore.ts). При remote домен приходит с сервера и здесь не хранится:
  // остаются выбор ученика, которому бэк ещё не даёт ручки, и материалы, сгенерированные в браузере
  "quack-prep-ui": "отметки вех, выбранные даты и цели, решения по конфликтам, пройден ли замер",
  "quack-prep-materials": "конспекты и карточки, сделанные ассистентом по просьбе ученика (ТЗ §5.7)",

  // Ключи демо-режима: при remote их писать нечему — источник тех же данных на сервере.
  // Перечислены отдельно, потому что список разрешённого зависит от того, кто считает.
  ...(REMOTE_PREP ? {} : { "quack-prep": "модель подготовки целиком — источник правды, пока нет бэкенда" }),
  ...(REMOTE_QUACK
    ? {}
    : {
        "quack-baseline": "база сравнения локального Quack",
        "quack-history": "лента локального Quack",
        "quack-known": "когда локальный Quack впервые заметил сигнал",
      }),
};

const refused = new Set<string>();

function allowed(key: string): boolean {
  if (key in ALLOWED) return true;
  if (!refused.has(key)) {
    refused.add(key);
    console.warn(
      `store: ключ «${key}» не в списке разрешённых (components/account/store.ts). ` +
        "Доменные данные живут в своих ручках; если ключ правда про UI — добавь его в список с обоснованием."
    );
  }
  return false;
}

/* ---------- Backends ---------- */

const prefix = (userId: string) => `quack:u:${userId}:`;

/** Keys from before accounts existed. The first account signed in on this browser takes them over */
const LEGACY = /^(quack-|lupidrupi\.)/;
/** Keys that belong to the device, not to a student */
const DEVICE = new Set(["quack-accounts", "quack-session", "quack-demo-shift", "quack-pending-cause"]);

const localBackend: StateBackend = {
  async load(userId) {
    const out: Record<string, unknown> = {};
    const p = prefix(userId);
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i)!;
        if (!key.startsWith(p)) continue;
        try {
          out[key.slice(p.length)] = JSON.parse(localStorage.getItem(key)!);
        } catch {}
      }
      if (!Object.keys(out).length) adoptLegacy(userId, out);
    } catch {}
    return out;
  },
  async save(userId, entries) {
    this.saveOnExit(userId, entries);
  },
  saveOnExit(userId, entries) {
    const p = prefix(userId);
    for (const [key, value] of Object.entries(entries)) {
      try {
        if (value === null) localStorage.removeItem(p + key);
        else localStorage.setItem(p + key, JSON.stringify(value));
      } catch {
        // Full or private mode: this visit still works, it just is not remembered
      }
    }
  },
  async clear(userId) {
    const p = prefix(userId);
    try {
      const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)!);
      keys.filter((k) => k.startsWith(p)).forEach((k) => localStorage.removeItem(k));
    } catch {}
  },
};

/** Moves what this browser kept before sign-in existed into the first account, once */
function adoptLegacy(userId: string, into: Record<string, unknown>) {
  const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)!);
  for (const key of keys) {
    if (!LEGACY.test(key) || DEVICE.has(key)) continue;
    try {
      const value = JSON.parse(localStorage.getItem(key)!);
      into[key] = value;
      localStorage.setItem(prefix(userId) + key, JSON.stringify(value));
    } catch {}
    localStorage.removeItem(key);
  }
}

/** `PATCH /state` refuses more than 200 keys at a time (backend/app/api/state.py) */
const MAX_KEYS_PER_PATCH = 200;

function batches(entries: Record<string, unknown>) {
  const keys = Object.keys(entries);
  if (keys.length <= MAX_KEYS_PER_PATCH) return [entries];
  const out: Record<string, unknown>[] = [];
  for (let i = 0; i < keys.length; i += MAX_KEYS_PER_PATCH) {
    out.push(Object.fromEntries(keys.slice(i, i + MAX_KEYS_PER_PATCH).map((k) => [k, entries[k]])));
  }
  return out;
}

const remoteBackend: StateBackend = {
  async load() {
    try {
      return await api.get<Record<string, unknown>>("/state");
    } catch (e) {
      // No row yet for this student: an empty slate, not a failure
      if (e instanceof ApiError && e.status === 404) return {};
      throw e;
    }
  },
  async save(_userId, entries) {
    for (const batch of batches(entries)) await api.patch("/state", batch);
  },
  saveOnExit(_userId, entries) {
    // keepalive lets the request finish after the page is gone; a redirect would not, so no 401 hop
    for (const batch of batches(entries)) {
      void api.patch("/state", batch, { keepalive: true, noAuthRedirect: true }).catch(() => undefined);
    }
  },
  async clear() {
    await api.delete("/state");
  },
};

const backend = REMOTE ? remoteBackend : localBackend;

/* ---------- The cache the screens read ---------- */

let userId: string | null = null;
let cache: Record<string, unknown> = {};
let pending: Record<string, unknown> = {};
let timer: ReturnType<typeof setTimeout> | null = null;

function takePending() {
  const batch = pending;
  pending = {};
  if (timer) clearTimeout(timer);
  timer = null;
  return Object.keys(batch).length ? batch : null;
}

function flush() {
  const batch = takePending();
  if (batch && userId) backend.save(userId, batch).catch(() => {
    // Back into the queue for the next attempt, unless a newer value came meanwhile
    pending = { ...batch, ...pending };
  });
}

function flushOnExit() {
  const batch = takePending();
  if (batch && userId) backend.saveOnExit(userId, batch);
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", flushOnExit);
  document.addEventListener("visibilitychange", () => document.visibilityState === "hidden" && flushOnExit());
}

export const store = {
  /** Loads the student's state; everything else in the app waits for this */
  async open(user: User) {
    if (userId === user.id) return;
    flushOnExit();
    cache = await backend.load(user.id);
    userId = user.id;
  },

  /** Drops the cache on sign-out, after sending what is still pending */
  close() {
    flushOnExit();
    userId = null;
    cache = {};
  },

  get<T>(key: string): T | null {
    return key in cache ? (cache[key] as T) : null;
  },

  /** null removes the key */
  set(key: string, value: unknown) {
    const removing = value === null || value === undefined;
    // Dropping a key is always allowed: that is how a key that left the list is cleaned up
    if (!removing && !allowed(key)) return;
    if (value === null || value === undefined) delete cache[key];
    else cache[key] = value;
    pending[key] = value ?? null;
    if (!timer) timer = setTimeout(flush, FLUSH_MS);
  },

  /** "Начать заново": the student's data goes, the account stays */
  async reset() {
    takePending();
    cache = {};
    if (userId) await backend.clear(userId);
  },
};
