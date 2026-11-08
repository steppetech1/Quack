"use client";

import { useEffect, useState } from "react";
import { useT, useMocks } from "@/lib/i18n/provider";
import { readLocalSkills, writeLocalSkills } from "@/lib/skills";
import type { Skill } from "@/lib/mock/types";
import { useNotice } from "./soon";

/**
 * One-time move of the skills that were saved in this browser to the account (API mode only).
 * Nothing is sent until the learner confirms; after it the local copy is cleared, and only what the
 * backend refused stays there. The unchanged sample skills of the mocks are not the learner's own and are not offered.
 */
export function SkillsMigrate({ migrate }: { migrate: (items: Skill[]) => Promise<{ moved: number; skipped: number; unbound: number; failed: Skill[] }> }) {
  const tx = useT();
  const m = tx.skillMigrate;
  const seed = useMocks().skills;
  const notice = useNotice();
  const [local, setLocal] = useState<Skill[]>([]);
  const [busy, setBusy] = useState(false);
  const [later, setLater] = useState(false);

  // read in an effect: localStorage does not exist on the server render
  useEffect(() => {
    const read = () => setLocal(readLocalSkills().filter((s) => !seed.some((x) => x.id === s.id && x.name === s.name && x.text === s.text)));
    read();
  }, [seed]);

  if (!local.length || later) return null;

  const move = async () => {
    setBusy(true);
    const r = await migrate(local);
    setBusy(false);
    writeLocalSkills(r.failed);
    setLocal(r.failed);
    notice(r.failed.length ? m.partial(r.moved, r.failed.length) : m.done(r.moved, r.skipped, r.unbound));
  };

  return (
    <section aria-label={m.title} className="mt-5 rounded-[2px] border border-seam bg-cloth-raised p-4 md:p-5">
      <h3 className="display-caps text-paper">{m.title}</h3>
      <p className="mt-1 max-w-[62ch] text-paper-dim">{m.text(local.length)}</p>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {local.map((s) => (
          <li key={s.id} className="num text-small text-paper-dim">
            {s.name}
          </li>
        ))}
      </ul>
      <p className="mt-2 max-w-[62ch] text-small text-paper-dim">{m.rules}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void move()}
          className="caps h-10 rounded-pill bg-ribbon px-6 text-on-ribbon transition-colors duration-(--dur-s) ease-out hover:bg-ribbon-hover disabled:cursor-default disabled:opacity-60"
        >
          {busy ? m.moving : m.move}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => setLater(true)}
          className="caps h-10 rounded-pill border border-seam px-6 text-paper transition-colors duration-(--dur-s) ease-out hover:border-ribbon hover:text-ribbon disabled:opacity-60"
        >
          {m.later}
        </button>
      </div>
    </section>
  );
}
