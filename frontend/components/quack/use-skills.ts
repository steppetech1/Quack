"use client";

import { useCallback, useEffect, useState } from "react";
import { useMocks } from "@/lib/i18n/provider";
import { SKILLS_KEY } from "@/lib/skills";
import type { Skill } from "@/lib/mock/types";

/** Skills: the locale mock seeds, with whatever is saved in localStorage on top (read in an effect, not on render) */
export function useSkills() {
  const seed = useMocks().skills;
  const [stored, setStored] = useState<Skill[] | null>(null);
  const skills = stored ?? seed;

  useEffect(() => {
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

  return { skills, update };
}
