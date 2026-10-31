"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { dayLabel, fullDate, inDays } from "@/lib/format";
import { getMocks, type Mocks } from "@/lib/mock";
import { dictionaries, type Dict } from "./dict";
import { LOCALE_COOKIE, type Locale } from "./locale";

type Ctx = { locale: Locale; setLocale: (l: Locale) => void };

const LocaleContext = createContext<Ctx>({ locale: "en", setLocale: () => {} });

export function LocaleProvider({ initial, children }: { initial: Locale; children: ReactNode }) {
  const router = useRouter();
  const [locale, setLocaleState] = useState<Locale>(initial);

  // The server is the source of truth: after router.refresh() `initial` changes
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setLocaleState(initial);
  }

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback(
    (l: Locale) => {
      document.cookie = `${LOCALE_COOKIE}=${l}; path=/; max-age=31536000; samesite=lax`;
      setLocaleState(l);
      router.refresh();
    },
    [router],
  );

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export const useLocale = () => useContext(LocaleContext);

/** Interface dictionary for the current locale */
export function useT(): Dict {
  return dictionaries[useLocale().locale];
}

/** Locale-dependent mock content */
export function useMocks(): Mocks {
  return getMocks(useLocale().locale);
}

/** Locale-bound date helpers */
export function useFmt() {
  const { locale } = useLocale();
  return useMemo(
    () => ({
      dayLabel: (iso: string) => dayLabel(iso, locale),
      fullDate: (iso: string) => fullDate(iso, locale),
      inDays: (iso: string) => inDays(iso, locale),
    }),
    [locale],
  );
}
