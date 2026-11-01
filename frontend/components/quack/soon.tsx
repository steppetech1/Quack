"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";

const SoonContext = createContext<(what: string, plain?: boolean) => void>(() => {});

/** Для действий, которые ведут на ещё не сделанные экраны */
export function useSoon() {
  return useContext(SoonContext);
}

/** Тот же тост без «скоро»: короткое сообщение, например об ошибке */
export function useNotice() {
  const show = useContext(SoonContext);
  return useCallback((text: string) => show(text, true), [show]);
}

export function SoonProvider({ children }: { children: React.ReactNode }) {
  const t = useT();
  const [msg, setMsg] = useState<{ text: string; plain?: boolean; n: number } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const show = useCallback((what: string, plain?: boolean) => {
    clearTimeout(timer.current);
    setMsg((m) => ({ text: what, plain, n: (m?.n ?? 0) + 1 }));
    timer.current = setTimeout(() => setMsg(null), 2600);
  }, []);

  return (
    <SoonContext.Provider value={show}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-28 z-[90] flex justify-center px-4">
        {msg && (
          <p
            key={msg.n}
            className="animate-toast rounded-xl border border-seam bg-cloth-raised px-4 py-2.5 text-small text-paper shadow-[0_8px_24px_-8px_rgb(0_0_0/0.6)]"
          >
            {!msg.plain && <span className="text-paper-dim">{t.soon.prefix}</span>}
            {msg.text}
          </p>
        )}
      </div>
    </SoonContext.Provider>
  );
}
