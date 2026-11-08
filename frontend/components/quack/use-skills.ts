"use client";

import { useCallback, useEffect, useSyncExternalStore, useState } from "react";
import { API_MODE, ApiError, del, get, idem, patch, post, type S } from "@/lib/api/client";
import { useMocks } from "@/lib/i18n/provider";
import { skillFromApi, skillName, skillToApi } from "@/lib/skills";
import { SKILLS_KEY } from "@/lib/skills";
import type { Skill } from "@/lib/mock/types";

/**
 * Skills of the learner.
 *
 * - Mocks: the locale seeds with whatever is saved in localStorage on top (read in an effect, not on render).
 * - Backend (`NEXT_PUBLIC_QUACK_API=1`): `GET/POST/PATCH/DELETE /v1/learner-skills`, so the teacher and the
 *   artifact writer see them. One list for the whole app, shared by the settings tab and the `/` menu of the composer.
 *   The backend has no "off" switch and no separate name: a skill is called in chat by its name (`/name`), so
 *   `command` is the name and `enabled` is always true.
 */

let cache: Skill[] | null = null;
let inflight: Promise<void> | null = null;
const subs = new Set<() => void>();
const setCache = (next: Skill[]) => {
  cache = next;
  subs.forEach((f) => f());
};
const subscribe = (f: () => void) => {
  subs.add(f);
  return () => void subs.delete(f);
};

/** Read the list once for the app; later calls reuse it until a change re-reads it */
function load(force = false): Promise<void> {
  if (!force && (cache || inflight)) return inflight ?? Promise.resolve();
  inflight = get<S["LearnerSkillsResponse"]>("/v1/learner-skills")
    .then((r) => setCache(r.skills.map(skillFromApi)))
    .catch(() => {
      // a failed read leaves the list as it was (empty on the first load)
      if (!cache) setCache([]);
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Text of a failed save for the form: the backend explains conflicts (name or artifact type taken) and bad names itself */
const failText = (e: unknown, fallback: string) => (e instanceof ApiError && e.status < 500 && e.message ? e.message : fallback);

export function useSkills() {
  const seed = useMocks().skills;
  const [stored, setStored] = useState<Skill[] | null>(null);
  const remote = useSyncExternalStore(subscribe, () => cache, () => null);

  useEffect(() => {
    if (API_MODE) {
      void load();
      return;
    }
    const read = () => {
      try {
        const raw = localStorage.getItem(SKILLS_KEY);
        if (raw) setStored(JSON.parse(raw) as Skill[]);
      } catch {}
    };
    read();
    const onStorage = (e: StorageEvent) => e.key === SKILLS_KEY && read();
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const skills = API_MODE ? (remote ?? []) : (stored ?? seed);

  const update = useCallback(
    (fn: (prev: Skill[]) => Skill[]) => {
      setStored((cur) => {
        const next = fn(cur ?? seed);
        try {
          localStorage.setItem(SKILLS_KEY, JSON.stringify(next));
        } catch {}
        return next;
      });
    },
    [seed],
  );

  /** Create or change a skill. Resolves with the text of the error, or `null` when it is saved */
  const save = useCallback(
    async (s: Skill, fallback: string): Promise<string | null> => {
      if (!API_MODE) {
        update((prev) => (prev.some((x) => x.id === s.id) ? prev.map((x) => (x.id === s.id ? s : x)) : [...prev, s]));
        return null;
      }
      try {
        const body = skillToApi(s);
        if ((cache ?? []).some((x) => x.id === s.id)) await patch(`/v1/learner-skills/${s.id}`, body);
        else await post("/v1/learner-skills", { ...body, idempotency_key: idem() });
        await load(true);
        return null;
      } catch (e) {
        return failText(e, fallback);
      }
    },
    [update],
  );

  /** Delete a skill with all its versions. Resolves with the text of the error, or `null` */
  const remove = useCallback(
    async (id: string, fallback: string): Promise<string | null> => {
      if (!API_MODE) {
        update((prev) => prev.filter((x) => x.id !== id));
        return null;
      }
      try {
        const left = await del(`/v1/learner-skills/${id}`);
        setCache((left as S["LearnerSkillsResponse"]).skills.map(skillFromApi));
        return null;
      } catch (e) {
        return failText(e, fallback);
      }
    },
    [update],
  );

  /**
   * One-time move of skills kept in this browser to the account. A name the account already has is skipped (a duplicate);
   * an artifact type that already has a skill is dropped from the moved one (the backend allows one skill per type),
   * the skill stays callable by its name. `failed` are the ones the backend refused: they stay in the browser.
   */
  const migrate = useCallback(async (items: Skill[]): Promise<{ moved: number; skipped: number; unbound: number; failed: Skill[] }> => {
    await load(true);
    const names = new Set((cache ?? []).map((x) => x.name));
    const types = new Set((cache ?? []).flatMap((x) => x.bindings));
    const out = { moved: 0, skipped: 0, unbound: 0, failed: [] as Skill[] };
    for (const s of items) {
      const name = skillName(s.name);
      if (names.has(name)) {
        out.skipped++;
        continue;
      }
      const free = s.bindings.filter((b) => !types.has(b));
      try {
        await post("/v1/learner-skills", { ...skillToApi({ ...s, bindings: free }), idempotency_key: idem() });
        names.add(name);
        free.forEach((b) => types.add(b));
        out.unbound += s.bindings.length - free.length;
        out.moved++;
      } catch {
        out.failed.push(s);
      }
    }
    await load(true);
    return out;
  }, []);

  const toggle = useCallback((id: string) => update((prev) => prev.map((x) => (x.id === id ? { ...x, enabled: !x.enabled } : x))), [update]);

  return { skills, update, save, remove, migrate, toggle, /** the backend keeps no on/off for a skill */ canToggle: !API_MODE, nameOf: skillName };
}
