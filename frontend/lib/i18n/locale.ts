export type Locale = "en" | "ru";

export const LOCALES: Locale[] = ["en", "ru"];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_COOKIE = "quack.lang";

/** Each language is always shown in its own language */
export const LOCALE_NAME: Record<Locale, string> = { en: "English", ru: "Русский" };

export const isLocale = (v: unknown): v is Locale => v === "en" || v === "ru";

export const parseLocale = (v: string | undefined | null): Locale => (isLocale(v) ? v : DEFAULT_LOCALE);

/** BCP-47 tag for Intl */
export const INTL_TAG: Record<Locale, string> = { en: "en-GB", ru: "ru-RU" };
