import { dictionaries } from "./i18n/dict";
import type { Locale } from "./i18n/locale";
import { API_MODE } from "./api/client";
import { MOCK_TODAY } from "./mock";

export { plural, plural2 } from "./i18n/plural";

const DAY = 86_400_000;

const MONTHS: Record<Locale, string[]> = {
  ru: ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
};
const MONTHS_FULL: Record<Locale, string[]> = {
  ru: [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
  ],
  en: [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ],
};

const parse = (iso: string) => new Date(`${iso}T00:00:00`);

/** Calendar day (`YYYY-MM-DD`) of a moment in the learner's own time zone, not UTC: a deadline set at 23:59 stays on its day */
export function localIso(at: number | Date) {
  const d = new Date(at);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** «Today» of the interface: the real day on the backend, the fixed demo day on mocks (their dates are written against it) */
export const todayIso = () => (API_MODE ? localIso(Date.now()) : MOCK_TODAY);

export function daysFromToday(iso: string) {
  return Math.round((parse(iso).getTime() - parse(todayIso()).getTime()) / DAY);
}

/** «today», «tomorrow», «in 3 days»; a date that has passed: «yesterday», «3 days ago» */
export function inDays(iso: string, locale: Locale) {
  const d = daysFromToday(iso);
  const t = dictionaries[locale].time;
  if (d === 0) return t.today;
  if (d === 1) return t.tomorrow;
  if (d === -1) return t.yesterday;
  if (d < 0) return t.daysAgo(-d);
  return t.inDays(d);
}

/** «today», «yesterday», «12 Oct» / «12 окт» */
export function dayLabel(iso: string, locale: Locale) {
  const d = daysFromToday(iso);
  const t = dictionaries[locale].time;
  if (d === 0) return t.today;
  if (d === -1) return t.yesterday;
  const date = parse(iso);
  return locale === "en" ? `${date.getDate()} ${MONTHS.en[date.getMonth()]}` : `${date.getDate()} ${MONTHS.ru[date.getMonth()]}`;
}

/** «1 November» / «1 ноября» */
export function fullDate(iso: string, locale: Locale) {
  const date = parse(iso);
  return `${date.getDate()} ${MONTHS_FULL[locale][date.getMonth()]}`;
}
